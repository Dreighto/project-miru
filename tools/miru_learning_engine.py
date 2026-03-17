from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import sqlite3
import struct
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.miru_ai_onepiece import (
    clean_display_text,
    initialize_fallback_catalog_db,
    inspect_fallback_catalog_db,
    normalize_card_code,
)
from tools.miru_source_adapters import (
    NormalizedImageRecord,
    NormalizedSourceRecord,
    OfficialCardImageSourceAdapter,
    OfficialCardListSourceAdapter,
    SourceAdapterError,
)
from tools.miru_source_registry import (
    MiruSourceEntry,
    build_source_registry,
    get_approved_sources_config_status,
    get_source_entry,
    get_source_entry_or_none,
)
from tools.miru_learner_config import (
    get_learner_mode,
    is_publish_allowed,
    is_review_required_mode,
    is_dry_run,
    LEARNER_MODE_ACTIVE,
    LEARNER_MODE_REVIEW_REQUIRED,
    LEARNER_MODE_DRY_RUN,
)
from tools.miru_ethics_gates import can_publish
from tools.miru_project_sync import MiruProjectDbSync
from tools.miru_source_discovery import (
    DiscoveredSourceCandidate,
    discover_source_candidates,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_queue.db"
DEFAULT_STATUS_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_log.db"
DEFAULT_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
DEFAULT_PROJECT_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_KNOWLEDGE_CACHE_PATH = PROJECT_ROOT / "data" / "miru_ai_onepiece_knowledge.json"
DEFAULT_CATALOG_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_IMAGE_DEST_ROOT = PROJECT_ROOT / "data" / "miru_images"
DEFAULT_SLEEP_SECONDS = 2.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_SEED_BATCH_SIZE = 25
DEFAULT_MAX_PARALLEL_VALIDATIONS = 2

# Event types for compliance and Dev page visibility
EVENT_SOURCE_NOT_REGISTERED = "SOURCE_NOT_REGISTERED"
EVENT_API_REQUIRED_SOURCE_DETECTED = "API_REQUIRED_SOURCE_DETECTED"
EVENT_ACCESS_POLICY_UNCLEAR = "ACCESS_POLICY_UNCLEAR"
EVENT_PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
EVENT_PUBLISH_BLOCKED = "PUBLISH_BLOCKED"
EVENT_PUBLISH_SUCCESS = "PUBLISH_SUCCESS"


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def connect_sqlite(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


VARIANT_SAFE_RE = re.compile(r"[^a-z0-9_-]+")


def normalize_variant_key(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "base", "default", "standard"}:
        return ""
    text = text.replace(" ", "-")
    return VARIANT_SAFE_RE.sub("", text)


def build_image_filename(card_code: str, variant_key: str) -> str:
    canonical = normalize_card_code(card_code)
    base_code = canonical["canonical_code"] or card_code.strip().upper()
    normalized_variant = normalize_variant_key(variant_key)
    if normalized_variant:
        return f"{base_code}({normalized_variant}).png"
    return f"{base_code}.png"


def normalize_task_payload_for_signature(payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    ignored_keys = {"snapshot_path", "snapshot_url", "requested_at"}
    normalized: dict[str, Any] = {}
    for key in sorted(raw):
        if key in ignored_keys:
            continue
        value = raw[key]
        if isinstance(value, dict):
            normalized[key] = normalize_task_payload_for_signature(value)
        elif isinstance(value, list):
            normalized[key] = [
                normalize_task_payload_for_signature(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            normalized[key] = value
    return normalized


def build_task_signature(
    *,
    card_code: str = "",
    variant_key: str = "",
    task_type: str,
    source_id: str = "",
    task_payload: dict[str, Any] | None = None,
) -> str:
    normalized_payload = normalize_task_payload_for_signature(task_payload)
    return json.dumps(
        {
            "card_code": str(card_code or "").strip().upper(),
            "variant_key": normalize_variant_key(variant_key),
            "task_type": str(task_type or "").strip().lower(),
            "source_id": str(source_id or "").strip().lower(),
            "task_payload": normalized_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def read_png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return (0, 0)
    try:
        width, height = struct.unpack(">II", data[16:24])
    except struct.error:
        return (0, 0)
    return (int(width), int(height))


@dataclass(slots=True)
class LearningTask:
    id: int
    card_code: str
    variant_key: str
    task_type: str
    source_id: str
    priority: int
    status: str
    attempts: int
    last_error: str
    task_payload: dict[str, Any]
    created_at: str
    updated_at: str

    @property
    def label(self) -> str:
        source_suffix = f"@{self.source_id}" if self.source_id else ""
        if self.card_code:
            variant_suffix = f"({self.variant_key})" if self.variant_key else ""
            return f"{self.task_type}:{self.card_code}{variant_suffix}{source_suffix}"
        return f"{self.task_type}{source_suffix}"


class MiruLearningEngine:
    def __init__(
        self,
        *,
        queue_db_path: Path = DEFAULT_QUEUE_DB_PATH,
        status_db_path: Path = DEFAULT_STATUS_DB_PATH,
        dossier_db_path: Path = DEFAULT_DOSSIER_DB_PATH,
        project_db_path: Path = DEFAULT_PROJECT_DB_PATH,
        knowledge_cache_path: Path = DEFAULT_KNOWLEDGE_CACHE_PATH,
        catalog_db_path: Path = DEFAULT_CATALOG_DB_PATH,
        image_dest_root: Path = DEFAULT_IMAGE_DEST_ROOT,
        sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        seed_batch_size: int = DEFAULT_SEED_BATCH_SIZE,
        max_parallel_validations: int = DEFAULT_MAX_PARALLEL_VALIDATIONS,
        sync_batch_size: int = 3,
        sync_immediate: bool = True,
    ) -> None:
        self.queue_db_path = Path(queue_db_path)
        self.status_db_path = Path(status_db_path)
        self.dossier_db_path = Path(dossier_db_path)
        self.project_db_path = Path(project_db_path)
        self.knowledge_cache_path = Path(knowledge_cache_path)
        self.catalog_db_path = Path(catalog_db_path)
        self.image_dest_root = Path(image_dest_root)
        self.sleep_seconds = max(float(sleep_seconds), 0.1)
        self.max_attempts = max(int(max_attempts), 1)
        self.seed_batch_size = max(int(seed_batch_size), 1)
        self.max_parallel_validations = max(int(max_parallel_validations), 1)
        self._knowledge_cache: dict[str, Any] | None = None
        self.source_registry = build_source_registry()
        self.official_source_adapter = OfficialCardListSourceAdapter()
        self.official_image_adapter = OfficialCardImageSourceAdapter()
        self.project_sync = MiruProjectDbSync(
            project_db_path=self.project_db_path,
            batch_size=sync_batch_size,
            sync_immediate=sync_immediate,
            logger=self.append_log,
        )

    def maybe_send_learning_notification(self, *, force: bool = False) -> dict[str, Any] | None:
        # Reuse the same compact notification builder as the Dev preview/test route.
        from tools import miru_ai_server as notification_server

        app = notification_server.create_app()
        ctx = app.test_request_context("/api/dev-status")
        ctx.push()
        try:
            training_status = notification_server.build_training_status()
            learning_status = load_learning_engine_status(
                queue_db_path=self.queue_db_path,
                status_db_path=self.status_db_path,
                dossier_db_path=self.dossier_db_path,
                total_cards=int(training_status.get("total_cards") or 0),
            )
            previous_snapshot = notification_server.load_pushover_learning_snapshot()
            payload = notification_server.build_learning_notification_payload(
                training_status,
                learning_status,
                previous_snapshot=previous_snapshot,
            )
        finally:
            ctx.pop()

        previous_engine_state = (
            notification_server.describe_learning_notification_engine_state(previous_snapshot)
            if isinstance(previous_snapshot, dict)
            else ""
        )
        should_send = bool(force or payload.get("meaningful_gain"))
        if payload.get("engine_state") == "stuck":
            should_send = True
        elif not isinstance(previous_snapshot, dict):
            should_send = True
        elif payload.get("api_permission_required"):
            should_send = True
        elif payload.get("engine_state") in {"idle", "searching", "checking images"} and payload.get("engine_state") != previous_engine_state:
            should_send = True

        if not should_send:
            return None

        result = notification_server.send_pushover_notification(
            title=str(payload.get("title") or "Miru learning snapshot"),
            message=str(payload.get("message") or "Miru learning update."),
        )
        if bool(result.get("ok")):
            notification_server.save_pushover_learning_snapshot(
                dict(payload["snapshot"]),
                title=str(payload.get("title") or ""),
                message=str(payload.get("message") or ""),
            )
        return {
            "sent": bool(result.get("ok")),
            "title": str(payload.get("title") or ""),
            "message": str(payload.get("message") or ""),
            "engine_state": str(payload.get("engine_state") or ""),
            "result": result,
        }

    def ensure_datastores(self) -> None:
        for path in (self.queue_db_path, self.status_db_path, self.dossier_db_path, self.catalog_db_path, self.project_db_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.image_dest_root.mkdir(parents=True, exist_ok=True)

        catalog_status = inspect_fallback_catalog_db(self.catalog_db_path)
        if not catalog_status["usable"]:
            initialize_fallback_catalog_db(
                db_path=self.catalog_db_path,
                cache_path=self.knowledge_cache_path,
            )

        queue_schema = """
            CREATE TABLE IF NOT EXISTS learning_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL DEFAULT '',
                variant_key TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL,
                source_id TEXT NOT NULL DEFAULT '',
                task_signature TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                task_payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                claimed_at TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_learning_queue_status_priority
                ON learning_queue(status, priority DESC, created_at ASC);
            CREATE INDEX IF NOT EXISTS idx_learning_queue_card_task
                ON learning_queue(card_code, task_type, status);
        """

        status_schema = """
            CREATE TABLE IF NOT EXISTS engine_status (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                current_state TEXT NOT NULL DEFAULT 'idle',
                current_task_label TEXT NOT NULL DEFAULT '',
                current_card_code TEXT NOT NULL DEFAULT '',
                current_task_type TEXT NOT NULL DEFAULT '',
                current_source_id TEXT NOT NULL DEFAULT '',
                current_image_task TEXT NOT NULL DEFAULT '',
                last_completed_task TEXT NOT NULL DEFAULT '',
                last_completed_card TEXT NOT NULL DEFAULT '',
                last_heartbeat TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                processed_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                last_source_id TEXT NOT NULL DEFAULT '',
                last_source_reference TEXT NOT NULL DEFAULT '',
                last_source_update TEXT NOT NULL DEFAULT '',
                source_success_count INTEGER NOT NULL DEFAULT 0,
                source_error_count INTEGER NOT NULL DEFAULT 0,
                last_image_update TEXT NOT NULL DEFAULT '',
                image_success_count INTEGER NOT NULL DEFAULT 0,
                image_error_count INTEGER NOT NULL DEFAULT 0,
                max_parallel_validations INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS engine_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL DEFAULT 'info',
                event_type TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                card_code TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS discovered_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                host TEXT NOT NULL DEFAULT '',
                source_kind TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                confidence_score REAL NOT NULL DEFAULT 0.0,
                review_status TEXT NOT NULL DEFAULT 'pending_review',
                signals_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                detected_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(url)
            );
            CREATE INDEX IF NOT EXISTS idx_discovered_sources_review_status
                ON discovered_sources(review_status, updated_at DESC);
            CREATE TABLE IF NOT EXISTS learner_review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_learner_review_queue_created
                ON learner_review_queue(created_at DESC);
        """

        dossier_schema = """
            CREATE TABLE IF NOT EXISTS learning_dossiers (
                card_code TEXT PRIMARY KEY,
                card_name TEXT NOT NULL DEFAULT '',
                set_code TEXT NOT NULL DEFAULT '',
                rarity TEXT NOT NULL DEFAULT '',
                basic_facts_json TEXT NOT NULL DEFAULT '{}',
                source_summary TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                verification_state TEXT NOT NULL DEFAULT 'placeholder',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_learning_dossiers_set_code
                ON learning_dossiers(set_code);
            CREATE INDEX IF NOT EXISTS idx_learning_dossiers_updated_at
                ON learning_dossiers(updated_at);
            CREATE TABLE IF NOT EXISTS learning_dossier_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_reference TEXT NOT NULL DEFAULT '',
                field_payload_json TEXT NOT NULL DEFAULT '{}',
                verification_state TEXT NOT NULL DEFAULT 'source-fetched',
                fetched_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(card_code, source_id, source_reference)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_sources_card
                ON learning_dossier_sources(card_code);
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_sources_source
                ON learning_dossier_sources(source_id);
            CREATE TABLE IF NOT EXISTS learning_dossier_images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                variant_key TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL,
                local_path TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL,
                source_reference TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                verification_state TEXT NOT NULL DEFAULT 'provisional',
                image_hash TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                downloaded_at TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                UNIQUE(card_code, variant_key, source_id, filename)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_card
                ON learning_dossier_images(card_code);
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_source
                ON learning_dossier_images(source_id);
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_state
                ON learning_dossier_images(verification_state);
            CREATE TABLE IF NOT EXISTS learning_dossier_rulings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                ruling_key TEXT NOT NULL DEFAULT '',
                ruling_text TEXT NOT NULL DEFAULT '',
                source_summary TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL,
                UNIQUE(card_code, ruling_key)
            );
            CREATE TABLE IF NOT EXISTS learning_dossier_strategy_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                note_type TEXT NOT NULL DEFAULT '',
                note_text TEXT NOT NULL DEFAULT '',
                source_summary TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_dossier_deck_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                archetype_key TEXT NOT NULL DEFAULT '',
                usage_context TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                source_summary TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_dossier_variant_art (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                variant_key TEXT NOT NULL DEFAULT '',
                art_label TEXT NOT NULL DEFAULT '',
                image_reference TEXT NOT NULL DEFAULT '',
                source_summary TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(card_code, variant_key, art_label)
            );
            CREATE TABLE IF NOT EXISTS learning_dossier_market_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                signal_type TEXT NOT NULL DEFAULT '',
                signal_value REAL NOT NULL DEFAULT 0.0,
                source_summary TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_deck_archetypes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                archetype_key TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL DEFAULT '',
                color_profile TEXT NOT NULL DEFAULT '',
                summary_json TEXT NOT NULL DEFAULT '{}',
                verification_state TEXT NOT NULL DEFAULT 'placeholder',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_card_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                archetype_key TEXT NOT NULL DEFAULT '',
                usage_frequency REAL NOT NULL DEFAULT 0.0,
                sample_size INTEGER NOT NULL DEFAULT 0,
                source_summary TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_tournament_placements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL DEFAULT '',
                event_name TEXT NOT NULL DEFAULT '',
                placement INTEGER NOT NULL DEFAULT 0,
                archetype_key TEXT NOT NULL DEFAULT '',
                deck_label TEXT NOT NULL DEFAULT '',
                source_summary TEXT NOT NULL DEFAULT '',
                event_date TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
        """

        with closing(connect_sqlite(self.queue_db_path)) as conn:
            conn.executescript(queue_schema)
            self.ensure_column(conn, "learning_queue", "source_id TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_queue", "variant_key TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_queue", "task_signature TEXT NOT NULL DEFAULT ''")
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_queue_card_variant_task
                        ON learning_queue(card_code, variant_key, task_type, status)
                    """
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_queue_signature_status
                        ON learning_queue(task_signature, status)
                    """
                )
            except sqlite3.OperationalError:
                pass
        with closing(connect_sqlite(self.status_db_path)) as conn:
            conn.executescript(status_schema)
            self.ensure_column(conn, "engine_status", "current_source_id TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "engine_status", "current_image_task TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "engine_status", "last_source_id TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "engine_status", "last_source_reference TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "engine_status", "last_source_update TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "engine_status", "source_success_count INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "engine_status", "source_error_count INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "engine_status", "last_image_update TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "engine_status", "image_success_count INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "engine_status", "image_error_count INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "engine_status", "max_parallel_validations INTEGER NOT NULL DEFAULT 1")
            self.ensure_column(conn, "engine_status", "last_publish_at TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "engine_status", "last_blocked_at TEXT NOT NULL DEFAULT ''")
            conn.execute(
                """
                INSERT INTO engine_status (
                    singleton_id,
                    current_state,
                    last_heartbeat
                ) VALUES (1, 'idle', ?)
                ON CONFLICT(singleton_id) DO NOTHING
                """,
                (utc_timestamp(),),
            )
            conn.execute(
                "UPDATE engine_status SET max_parallel_validations = ? WHERE singleton_id = 1",
                (self.max_parallel_validations,),
            )
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.executescript(dossier_schema)
            self.ensure_column(conn, "learning_dossiers", "trivia TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def ensure_column(conn: sqlite3.Connection, table_name: str, column_definition: str) -> None:
        existing_columns = {
            row["name"] if isinstance(row, sqlite3.Row) else row[1]
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        column_name = column_definition.split()[0]
        if column_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    def load_knowledge_cache(self) -> dict[str, Any]:
        if self._knowledge_cache is None:
            payload = json.loads(self.knowledge_cache_path.read_text(encoding="utf-8"))
            self._knowledge_cache = payload if isinstance(payload, dict) else {}
        return self._knowledge_cache

    def append_log(
        self,
        *,
        level: str,
        event_type: str,
        message: str,
        card_code: str = "",
        task_type: str = "",
    ) -> None:
        with closing(connect_sqlite(self.status_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO engine_log (
                    level,
                    event_type,
                    message,
                    card_code,
                    task_type,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (level, event_type, message, card_code, task_type, utc_timestamp()),
            )

    def append_review_item(
        self,
        *,
        card_code: str = "",
        source_id: str = "",
        confidence: float = 0.0,
        reason: str = "",
    ) -> None:
        """Add an item to the learner review queue (e.g. when mode is REVIEW_REQUIRED)."""
        now = utc_timestamp()
        with closing(connect_sqlite(self.status_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learner_review_queue (card_code, source_id, confidence, reason, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(card_code or "").strip(), str(source_id or "").strip(), float(confidence), str(reason or "").strip(), now),
            )

    def store_discovered_source_candidate(self, candidate: DiscoveredSourceCandidate) -> bool:
        now = utc_timestamp()
        with closing(connect_sqlite(self.status_db_path)) as conn:
            existing = conn.execute(
                "SELECT review_status FROM discovered_sources WHERE url = ? LIMIT 1",
                (candidate.url,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO discovered_sources (
                    url,
                    host,
                    source_kind,
                    title,
                    notes,
                    confidence_score,
                    review_status,
                    signals_json,
                    metadata_json,
                    detected_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    host = excluded.host,
                    source_kind = excluded.source_kind,
                    title = excluded.title,
                    notes = excluded.notes,
                    confidence_score = excluded.confidence_score,
                    signals_json = excluded.signals_json,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    candidate.url,
                    candidate.host,
                    candidate.source_kind,
                    candidate.title,
                    candidate.notes,
                    float(candidate.confidence_score),
                    (existing["review_status"] if existing is not None else candidate.review_status),
                    json.dumps(list(candidate.signals), ensure_ascii=False, sort_keys=True),
                    json.dumps(candidate.metadata or {}, ensure_ascii=False, sort_keys=True),
                    candidate.detected_at or now,
                    now,
                ),
            )
        return existing is None

    def update_status(
        self,
        *,
        current_state: str | None = None,
        current_task_label: str | None = None,
        current_card_code: str | None = None,
        current_task_type: str | None = None,
        current_source_id: str | None = None,
        current_image_task: str | None = None,
        last_completed_task: str | None = None,
        last_completed_card: str | None = None,
        last_error: str | None = None,
        last_source_id: str | None = None,
        last_source_reference: str | None = None,
        last_source_update: str | None = None,
        last_image_update: str | None = None,
        last_publish_at: str | None = None,
        last_blocked_at: str | None = None,
        increment_processed: int = 0,
        increment_success: int = 0,
        increment_error: int = 0,
        increment_source_success: int = 0,
        increment_source_error: int = 0,
        increment_image_success: int = 0,
        increment_image_error: int = 0,
    ) -> None:
        now = utc_timestamp()
        assignments = ["last_heartbeat = ?"]
        params: list[Any] = [now]

        optional_values = {
            "current_state": current_state,
            "current_task_label": current_task_label,
            "current_card_code": current_card_code,
            "current_task_type": current_task_type,
            "current_source_id": current_source_id,
            "current_image_task": current_image_task,
            "last_completed_task": last_completed_task,
            "last_completed_card": last_completed_card,
            "last_error": last_error,
            "last_source_id": last_source_id,
            "last_source_reference": last_source_reference,
            "last_source_update": last_source_update,
            "last_image_update": last_image_update,
            "last_publish_at": last_publish_at,
            "last_blocked_at": last_blocked_at,
        }
        for column, value in optional_values.items():
            if value is not None:
                assignments.append(f"{column} = ?")
                params.append(value)

        if increment_processed:
            assignments.append("processed_count = processed_count + ?")
            params.append(int(increment_processed))
        if increment_success:
            assignments.append("success_count = success_count + ?")
            params.append(int(increment_success))
        if increment_error:
            assignments.append("error_count = error_count + ?")
            params.append(int(increment_error))
        if increment_source_success:
            assignments.append("source_success_count = source_success_count + ?")
            params.append(int(increment_source_success))
        if increment_source_error:
            assignments.append("source_error_count = source_error_count + ?")
            params.append(int(increment_source_error))
        if increment_image_success:
            assignments.append("image_success_count = image_success_count + ?")
            params.append(int(increment_image_success))
        if increment_image_error:
            assignments.append("image_error_count = image_error_count + ?")
            params.append(int(increment_image_error))

        params.append(1)
        with closing(connect_sqlite(self.status_db_path)) as conn:
            conn.execute(
                f"UPDATE engine_status SET {', '.join(assignments)} WHERE singleton_id = ?",
                params,
            )

    def enqueue_task(
        self,
        *,
        card_code: str = "",
        variant_key: str = "",
        task_type: str,
        source_id: str = "",
        priority: int = 0,
        task_payload: dict[str, Any] | None = None,
    ) -> bool:
        canonical_card_code = ""
        if card_code:
            canonical = normalize_card_code(card_code)
            canonical_card_code = canonical["canonical_code"] or card_code.strip().upper()

        payload = task_payload or {}
        normalized_variant = normalize_variant_key(variant_key or payload.get("variant_key") or "")
        task_signature = build_task_signature(
            card_code=canonical_card_code,
            variant_key=normalized_variant,
            task_type=task_type,
            source_id=source_id,
            task_payload=payload,
        )
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            duplicate = conn.execute(
                """
                SELECT 1
                FROM learning_queue
                WHERE task_signature = ?
                  AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (task_signature,),
            ).fetchone()
            if duplicate:
                return False

            now = utc_timestamp()
            conn.execute(
                """
                INSERT INTO learning_queue (
                    card_code,
                    variant_key,
                    task_type,
                    source_id,
                    task_signature,
                    priority,
                    status,
                    attempts,
                    last_error,
                    task_payload_json,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, '', ?, ?, ?)
                """,
                (
                    canonical_card_code,
                    normalized_variant,
                    task_type,
                    source_id.strip().lower(),
                    task_signature,
                    int(priority),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        return True

    def queue_counts(self) -> dict[str, int]:
        counts = {"queued": 0, "running": 0, "failed": 0, "completed": 0}
        if not self.queue_db_path.exists():
            return counts
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM learning_queue GROUP BY status"
            ).fetchall()
        for row in rows:
            counts[str(row["status"])] = int(row["total"])
        return counts

    def seed_missing_bootstrap_tasks(self, limit: int | None = None) -> int:
        batch_limit = max(int(limit or self.seed_batch_size), 1)
        inserted = 0
        with closing(connect_sqlite(self.catalog_db_path)) as conn:
            conn.execute("ATTACH DATABASE ? AS learning_dossiers", (str(self.dossier_db_path),))
            rows = conn.execute(
                """
                SELECT cards.canonical_code
                FROM cards
                LEFT JOIN learning_dossiers.learning_dossiers dossiers
                    ON dossiers.card_code = cards.canonical_code
                WHERE dossiers.card_code IS NULL
                ORDER BY cards.canonical_code ASC
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
        for row in rows:
            if self.enqueue_task(
                card_code=str(row["canonical_code"]),
                task_type="bootstrap_dossier",
                priority=50,
            ):
                inserted += 1
        if inserted:
            self.append_log(
                level="info",
                event_type="seed_queue",
                message=f"Queued {inserted} bootstrap_dossier task(s).",
            )
        return inserted

    def claim_next_task(self) -> LearningTask | None:
        tasks = self.claim_next_tasks(1)
        return tasks[0] if tasks else None

    def claim_next_tasks(self, limit: int) -> list[LearningTask]:
        batch_limit = max(int(limit), 1)
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT *
                FROM learning_queue
                WHERE status = 'queued'
                ORDER BY priority DESC, created_at ASC, id ASC
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
            if not rows:
                conn.execute("COMMIT")
                return []

            now = utc_timestamp()
            placeholders = ", ".join("?" for _ in rows)
            conn.execute(
                f"""
                UPDATE learning_queue
                SET status = 'running',
                    attempts = attempts + 1,
                    claimed_at = ?,
                    updated_at = ?,
                    last_error = ''
                WHERE id IN ({placeholders})
                """,
                (now, now, *[int(row["id"]) for row in rows]),
            )
            conn.execute("COMMIT")

        claimed: list[LearningTask] = []
        for row in rows:
            attempts = int(row["attempts"]) + 1
            claimed.append(
                LearningTask(
                    id=int(row["id"]),
                    card_code=str(row["card_code"] or ""),
                    variant_key=str(row["variant_key"] or ""),
                    task_type=str(row["task_type"]),
                    source_id=str(row["source_id"] or ""),
                    priority=int(row["priority"] or 0),
                    status="running",
                    attempts=attempts,
                    last_error="",
                    task_payload=json.loads(row["task_payload_json"] or "{}"),
                    created_at=str(row["created_at"] or ""),
                    updated_at=now,
                )
            )
        return claimed

    def complete_task(self, task: LearningTask, message: str, *, source_reference: str = "") -> None:
        now = utc_timestamp()
        is_image_task = task.task_type in {"fetch_card_image", "verify_card_image", "refresh_card_image"}
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            conn.execute(
                """
                UPDATE learning_queue
                SET status = 'completed',
                    updated_at = ?,
                    completed_at = ?,
                    last_error = ''
                WHERE id = ?
                """,
                (now, now, task.id),
            )
        self.update_status(
            current_state="sleeping",
            current_task_label="",
            current_card_code="",
            current_task_type="",
            current_source_id="",
            current_image_task="",
            last_completed_task=task.task_type,
            last_completed_card=task.card_code,
            last_error="",
            last_source_id=task.source_id if task.source_id else None,
            last_source_reference=source_reference if task.source_id else None,
            last_source_update=now if task.source_id else None,
            last_image_update=now if is_image_task else None,
            increment_processed=1,
            increment_success=1,
            increment_source_success=1 if task.source_id else 0,
            increment_image_success=1 if is_image_task else 0,
        )
        self.append_log(
            level="info",
            event_type="task_completed",
            message=message,
            card_code=task.card_code,
            task_type=task.task_type,
        )
        self.maybe_send_learning_notification()

    def fail_task(self, task: LearningTask, exc: Exception) -> None:
        error_message = f"{exc.__class__.__name__}: {exc}"
        retry_status = "failed" if task.attempts >= self.max_attempts else "queued"
        is_image_task = task.task_type in {"fetch_card_image", "verify_card_image", "refresh_card_image"}
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            conn.execute(
                """
                UPDATE learning_queue
                SET status = ?,
                    updated_at = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (retry_status, utc_timestamp(), error_message[:1000], task.id),
            )
        self.update_status(
            current_state="error" if retry_status == "failed" else "sleeping",
            current_task_label="",
            current_card_code="",
            current_task_type="",
            current_source_id="",
            current_image_task="",
            last_error=error_message[:1000],
            increment_processed=1,
            increment_error=1,
            increment_source_error=1 if task.source_id else 0,
            increment_image_error=1 if is_image_task else 0,
        )
        self.append_log(
            level="error",
            event_type="task_failed",
            message=error_message,
            card_code=task.card_code,
            task_type=task.task_type,
        )
        self.maybe_send_learning_notification()

    def catalog_card_row(self, card_code: str) -> dict[str, Any]:
        with closing(connect_sqlite(self.catalog_db_path)) as conn:
            row = conn.execute(
                """
                SELECT
                    c.canonical_code,
                    c.set_code,
                    c.card_number,
                    c.set_name,
                    c.card_name,
                    c.rarity,
                    c.color,
                    c.card_type,
                    c.cost,
                    c.power,
                    c.counter,
                    c.attribute,
                    c.traits,
                    c.life,
                    c.block_icon,
                    c.effect_text,
                    c.trigger_text,
                    COUNT(v.id) AS variant_count,
                    SUM(
                        CASE
                            WHEN trim(coalesce(v.image_path, '')) != '' OR trim(coalesce(v.image_url, '')) != ''
                            THEN 1
                            ELSE 0
                        END
                    ) AS image_variant_count
                FROM cards c
                LEFT JOIN card_variants v
                    ON v.card_id = c.id
                WHERE c.canonical_code = ?
                GROUP BY c.id
                """,
                (card_code,),
            ).fetchone()
        if row is None:
            return {}
        return {key: row[key] for key in row.keys()}

    def resolve_local_profile(self, card_code: str) -> dict[str, Any]:
        canonical = normalize_card_code(card_code)
        resolved_code = canonical["canonical_code"] or card_code.strip().upper()
        knowledge_entry = (self.load_knowledge_cache().get("cards") or {}).get(resolved_code, {})
        catalog_entry = self.catalog_card_row(resolved_code)

        if not knowledge_entry and not catalog_entry:
            raise LookupError(f"No local card profile found for {resolved_code}")

        def choose(field_name: str) -> Any:
            knowledge_value = knowledge_entry.get(field_name)
            if knowledge_value not in (None, "", []):
                return knowledge_value
            return catalog_entry.get(field_name)

        sources: list[str] = []
        if knowledge_entry:
            sources.append("knowledge-cache")
        if catalog_entry:
            sources.append("card-catalog")

        basic_facts = {
            "card_code": resolved_code,
            "card_name": clean_display_text(str(choose("card_name") or "")),
            "set_code": clean_display_text(str(choose("set_code") or "")),
            "set_name": clean_display_text(str(choose("set_name") or "")),
            "rarity": clean_display_text(str(choose("rarity") or "")),
            "color": clean_display_text(str(choose("color") or "")),
            "card_type": clean_display_text(str(choose("card_type") or "")),
            "cost": choose("cost"),
            "power": clean_display_text(str(choose("power") or "")),
            "counter": clean_display_text(str(choose("counter") or "")),
            "attribute": clean_display_text(str(choose("attribute") or "")),
            "traits": clean_display_text(str(choose("traits") or "")),
            "life": clean_display_text(str(choose("life") or "")),
            "block_icon": clean_display_text(str(choose("block_icon") or "")),
            "effect_text": clean_display_text(str(choose("effect_text") or "")),
            "trigger_text": clean_display_text(str(choose("trigger_text") or "")),
            "variant_count": int(catalog_entry.get("variant_count") or 0),
            "image_variant_count": int(catalog_entry.get("image_variant_count") or 0),
            "missing_fields": [
                field_name
                for field_name in (
                    "rarity",
                    "color",
                    "card_type",
                    "effect_text",
                    "trigger_text",
                    "attribute",
                    "traits",
                )
                if not clean_display_text(str(choose(field_name) or ""))
            ],
        }

        confidence = 0.55
        if knowledge_entry and catalog_entry:
            confidence = 0.75
        elif knowledge_entry:
            confidence = 0.65

        return {
            "card_code": resolved_code,
            "card_name": basic_facts["card_name"],
            "set_code": basic_facts["set_code"],
            "rarity": basic_facts["rarity"],
            "basic_facts": basic_facts,
            "source_summary": ", ".join(sources),
            "confidence": round(confidence, 2),
            "verification_state": "local-bootstrap",
        }

    def upsert_dossier(self, dossier: dict[str, Any]) -> None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_dossiers (
                    card_code,
                    card_name,
                    set_code,
                    rarity,
                    basic_facts_json,
                    source_summary,
                    confidence,
                    verification_state,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code) DO UPDATE SET
                    card_name = excluded.card_name,
                    set_code = excluded.set_code,
                    rarity = excluded.rarity,
                    basic_facts_json = excluded.basic_facts_json,
                    source_summary = excluded.source_summary,
                    confidence = excluded.confidence,
                    verification_state = excluded.verification_state,
                    updated_at = excluded.updated_at
                """,
                (
                    dossier["card_code"],
                    dossier.get("card_name", ""),
                    dossier.get("set_code", ""),
                    dossier.get("rarity", ""),
                    json.dumps(dossier.get("basic_facts", {}), ensure_ascii=False, sort_keys=True),
                    dossier.get("source_summary", ""),
                    float(dossier.get("confidence", 0.0)),
                    dossier.get("verification_state", "placeholder"),
                    utc_timestamp(),
                ),
            )

    def fetch_dossier(self, card_code: str) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM learning_dossiers WHERE card_code = ?",
                (card_code,),
            ).fetchone()
        if row is None:
            return None
        dossier = {key: row[key] for key in row.keys()}
        dossier["basic_facts"] = json.loads(dossier["basic_facts_json"] or "{}")
        return dossier

    def resolve_source_entry(self, source_id: str) -> MiruSourceEntry:
        return get_source_entry(source_id or "official-cardlist", self.source_registry)

    def fetch_official_source_records(
        self,
        *,
        source_id: str,
        card_code: str = "",
        set_code: str = "",
        task_payload: dict[str, Any] | None = None,
    ) -> list[NormalizedSourceRecord]:
        source_entry = self.resolve_source_entry(source_id)
        payload = dict(task_payload or {})
        snapshot_path = payload.get("snapshot_path") or ""
        snapshot_url = payload.get("snapshot_url") or ""
        return self.official_source_adapter.fetch_records(
            source_entry=source_entry,
            card_code=card_code,
            set_code=set_code,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )

    def store_source_record(
        self,
        record: NormalizedSourceRecord,
        *,
        verification_state: str,
    ) -> None:
        payload = record.to_dict()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_dossier_sources (
                    card_code,
                    source_id,
                    source_reference,
                    field_payload_json,
                    verification_state,
                    fetched_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, source_id, source_reference) DO UPDATE SET
                    field_payload_json = excluded.field_payload_json,
                    verification_state = excluded.verification_state,
                    fetched_at = excluded.fetched_at,
                    updated_at = excluded.updated_at
                """,
                (
                    record.card_code,
                    record.source_id,
                    record.source_reference,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    verification_state,
                    record.fetched_at,
                    utc_timestamp(),
                ),
            )

    def merge_source_record_into_dossier(
        self,
        record: NormalizedSourceRecord,
        *,
        verification_state: str,
    ) -> None:
        existing = self.fetch_dossier(record.card_code)
        merged_facts = dict((existing or {}).get("basic_facts") or {})
        merged_facts.update(
            {
                "card_code": record.card_code,
                "card_name": record.card_name,
                "set_code": record.set_code,
                "set_name": record.set_name,
                "rarity": record.rarity,
                "color": record.color,
                "card_type": record.card_type,
                "cost": record.cost,
                "power": record.power,
                "counter": record.counter,
                "attribute": record.attribute,
                "traits": record.traits,
                "life": record.life,
                "effect_text": record.effect_text,
                "trigger_text": record.trigger_text,
                "source_id": record.source_id,
                "source_url": record.source_url,
                "source_reference": record.source_reference,
                "source_fetched_at": record.fetched_at,
            }
        )
        merged = {
            "card_code": record.card_code,
            "card_name": record.card_name or (existing or {}).get("card_name", ""),
            "set_code": record.set_code or (existing or {}).get("set_code", ""),
            "rarity": record.rarity or (existing or {}).get("rarity", ""),
            "basic_facts": merged_facts,
            "source_summary": f"{record.source_id}: {record.source_reference}",
            "confidence": max(float((existing or {}).get("confidence") or 0.0), 0.9),
            "verification_state": verification_state,
        }
        self.upsert_dossier(merged)

    def resolve_image_source_entry(self, source_id: str) -> MiruSourceEntry:
        entry = get_source_entry(source_id or "official-card-images", self.source_registry)
        if "image" not in entry.source_type:
            raise ValueError(f"Source {entry.source_id} is not an image source.")
        return entry

    def fetch_image_source_records(
        self,
        *,
        source_id: str,
        card_code: str = "",
        set_code: str = "",
        variant_key: str = "",
        task_payload: dict[str, Any] | None = None,
    ) -> list[NormalizedImageRecord]:
        source_entry = self.resolve_image_source_entry(source_id)
        payload = dict(task_payload or {})
        snapshot_path = payload.get("snapshot_path") or ""
        snapshot_url = payload.get("snapshot_url") or ""
        return self.official_image_adapter.fetch_records(
            source_entry=source_entry,
            card_code=card_code,
            set_code=set_code,
            variant_key=variant_key,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )

    def resolve_image_path(self, image_path: str, *, snapshot_path: str = "") -> str:
        if not image_path:
            return ""
        candidate = Path(image_path)
        if not candidate.is_absolute() and snapshot_path:
            candidate = Path(snapshot_path).parent / candidate
        return str(candidate)

    def fetch_image_bytes(self, *, image_path: str = "", source_url: str = "") -> bytes:
        if image_path:
            return Path(image_path).read_bytes()
        if source_url:
            try:
                with urlopen(source_url, timeout=10.0) as response:
                    return response.read()
            except HTTPError as exc:
                raise SourceAdapterError(f"HTTPError while fetching image content: {exc.code}") from exc
            except URLError as exc:
                raise SourceAdapterError(f"URLError while fetching image content: {exc.reason}") from exc
        raise SourceAdapterError("No image_path or source_url available for image fetch.")

    def resolve_image_destination(self, filename: str) -> Path:
        destination = self.image_dest_root / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        return destination

    def store_image_record(
        self,
        record: NormalizedImageRecord,
        *,
        filename: str,
        local_path: str,
        image_hash: str,
        width: int,
        height: int,
        verification_state: str,
        downloaded_at: str,
        last_verified_at: str,
        last_error: str,
    ) -> None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_dossier_images (
                    card_code,
                    variant_key,
                    filename,
                    local_path,
                    source_id,
                    source_reference,
                    source_url,
                    verification_state,
                    image_hash,
                    width,
                    height,
                    downloaded_at,
                    last_verified_at,
                    last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, variant_key, source_id, filename) DO UPDATE SET
                    local_path = excluded.local_path,
                    source_reference = excluded.source_reference,
                    source_url = excluded.source_url,
                    verification_state = excluded.verification_state,
                    image_hash = excluded.image_hash,
                    width = excluded.width,
                    height = excluded.height,
                    downloaded_at = excluded.downloaded_at,
                    last_verified_at = excluded.last_verified_at,
                    last_error = excluded.last_error
                """,
                (
                    record.card_code,
                    normalize_variant_key(record.variant_key),
                    filename,
                    local_path,
                    record.source_id,
                    record.source_reference,
                    record.source_url,
                    verification_state,
                    image_hash,
                    int(width),
                    int(height),
                    downloaded_at,
                    last_verified_at,
                    last_error,
                ),
            )
            # Associate variant to card identity and store classification for retrieval (alt-art/library)
            try:
                from tools.miru_print_variant import classify_print_variant, set_variant_classification

                vkey = normalize_variant_key(record.variant_key)
                vtype = classify_print_variant(vkey, filename)
                set_variant_classification(record.card_code, vkey, vtype)
            except Exception:
                pass

    def fetch_image_registry_record(
        self,
        *,
        card_code: str,
        variant_key: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_dossier_images
                WHERE card_code = ?
                  AND variant_key = ?
                  AND source_id = ?
                ORDER BY downloaded_at DESC, id DESC
                LIMIT 1
                """,
                (card_code, normalize_variant_key(variant_key), source_id),
            ).fetchone()
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    def process_task(self, task: LearningTask) -> dict[str, Any]:
        handler = TASK_HANDLERS.get(task.task_type)
        if handler is None:
            raise KeyError(f"Unknown learning task type: {task.task_type}")

        current_image_task = task.label if task.task_type in {"fetch_card_image", "verify_card_image", "refresh_card_image"} else ""
        self.update_status(
            current_state="processing",
            current_task_label=task.label,
            current_card_code=task.card_code,
            current_task_type=task.task_type,
            current_source_id=task.source_id,
            current_image_task=current_image_task,
            last_error="",
        )
        self.append_log(
            level="info",
            event_type="task_started",
            message=f"Running {task.label}",
            card_code=task.card_code,
            task_type=task.task_type,
        )
        return handler(self, task)

    def process_one(self) -> dict[str, Any] | None:
        task = self.claim_next_task()
        if task is None:
            return None
        return self.process_claimed_task(task)

    def process_claimed_task(self, task: LearningTask) -> dict[str, Any]:
        # Allowed-source registry: only operate on registered sources
        effective_source = (task.source_id or "").strip().lower()
        if effective_source:
            entry = get_source_entry_or_none(effective_source, self.source_registry)
            if entry is None:
                self.append_log(
                    level="warn",
                    event_type=EVENT_SOURCE_NOT_REGISTERED,
                    message=f"Source not in registry; ignoring task: {task.label}",
                    card_code=task.card_code,
                    task_type=task.task_type,
                )
                self.fail_task(task, ValueError(f"Source not registered: {effective_source}"))
                return {"ok": False, "task": task.label, "error": EVENT_SOURCE_NOT_REGISTERED}
            if getattr(entry, "requires_api", False):
                self.append_log(
                    level="warn",
                    event_type=EVENT_API_REQUIRED_SOURCE_DETECTED,
                    message=f"Source requires API/permission; not attempting automated access: {entry.source_id}",
                    card_code=task.card_code,
                    task_type=task.task_type,
                )
                self.fail_task(task, ValueError(f"Source requires API permission: {entry.source_id}"))
                return {"ok": False, "task": task.label, "error": EVENT_API_REQUIRED_SOURCE_DETECTED}
        try:
            result = self.process_task(task)
        except Exception as exc:
            self.fail_task(task, exc)
            return {"ok": False, "task": task.label, "error": f"{exc.__class__.__name__}: {exc}"}

        message = str(result.get("message") or f"Completed {task.label}")
        self.complete_task(task, message, source_reference=str(result.get("source_reference") or ""))
        return {"ok": True, "task": task.label, **result}

    def process_parallel_batch(self, limit: int | None = None) -> list[dict[str, Any]]:
        worker_count = max(min(int(limit or self.max_parallel_validations), self.max_parallel_validations), 1)
        tasks = self.claim_next_tasks(worker_count)
        if not tasks:
            return []
        if len(tasks) == 1:
            return [self.process_claimed_task(tasks[0])]

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            futures = [executor.submit(self.process_claimed_task, task) for task in tasks]
            for future in as_completed(futures):
                results.append(future.result())
        return results

    def queue_validated_card_for_project_sync(
        self,
        record: NormalizedSourceRecord,
        *,
        task_type: str,
    ) -> dict[str, Any]:
        now = utc_timestamp()
        mode = get_learner_mode()
        if is_dry_run():
            self.append_log(
                level="info",
                event_type=EVENT_PUBLISH_BLOCKED,
                message=f"Dry run: would publish {record.card_code}; nothing published.",
                card_code=record.card_code,
                task_type=task_type,
            )
            self.update_status(last_blocked_at=now)
            return {"queued": False, "reason": "dry_run", "mode": mode}
        if is_review_required_mode():
            self.append_review_item(
                card_code=record.card_code,
                source_id=getattr(record, "source_id", "") or "official-cardlist",
                confidence=getattr(record, "confidence", 0.0) or 0.9,
                reason="review_required_mode",
            )
            self.append_log(
                level="info",
                event_type=EVENT_PUBLISH_BLOCKED,
                message=f"Review required: {record.card_code} added to review queue; nothing published.",
                card_code=record.card_code,
                task_type=task_type,
            )
            self.update_status(last_blocked_at=now)
            return {"queued": False, "reason": "review_required", "mode": mode, "added_to_review_queue": True}
        # Ethics gate: no publish without provenance and approved source
        source_id = getattr(record, "source_id", "") or ""
        has_provenance = bool(source_id or getattr(record, "source_reference", "") or getattr(record, "source_url", ""))
        entry = get_source_entry_or_none(source_id)
        source_approved = entry is not None and getattr(entry, "publish_allowed", True)
        allowed, gate_reason = can_publish(has_provenance, source_approved, card_code=record.card_code, source_id=source_id)
        if not allowed:
            self.append_log(
                level="info",
                event_type=EVENT_PUBLISH_BLOCKED,
                message=f"Ethics gate: {gate_reason}",
                card_code=record.card_code,
                task_type=task_type,
            )
            self.update_status(last_blocked_at=now)
            return {"queued": False, "reason": "ethics_gate", "mode": mode, "gate_reason": gate_reason}
        # ACTIVE: allow publish
        sync_result = self.project_sync.queue_validated_record(record, task_type=task_type)
        self.append_log(
            level="info",
            event_type=EVENT_PUBLISH_SUCCESS,
            message=f"Queued for project sync: {record.card_code}",
            card_code=record.card_code,
            task_type=task_type,
        )
        self.update_status(last_publish_at=now)
        return {**sync_result, "mode": mode}

    def flush_pending_project_sync(self, *, reason: str) -> dict[str, int]:
        return self.project_sync.flush_pending(reason=reason)

    def prime_work(
        self,
        *,
        card_code: str = "",
        variant_key: str = "",
        task_type: str = "",
        source_id: str = "",
        task_payload: dict[str, Any] | None = None,
    ) -> None:
        if task_type:
            self.enqueue_task(
                card_code=card_code,
                variant_key=variant_key,
                task_type=task_type,
                source_id=source_id,
                priority=100,
                task_payload=task_payload,
            )
        elif card_code:
            self.enqueue_task(
                card_code=card_code,
                task_type="bootstrap_dossier",
                priority=100,
            )

    def run_once(
        self,
        *,
        card_code: str = "",
        variant_key: str = "",
        task_type: str = "",
        source_id: str = "",
        task_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.ensure_datastores()
        self.prime_work(
            card_code=card_code,
            variant_key=variant_key,
            task_type=task_type,
            source_id=source_id,
            task_payload=task_payload,
        )
        try:
            if self.queue_counts()["queued"] == 0:
                self.seed_missing_bootstrap_tasks(limit=1)
            result = self.process_one()
            if result is None:
                self.update_status(current_state="idle", current_task_label="", current_card_code="", current_task_type="")
                return {"ok": True, "message": "No queued learning work was available."}
            return result
        finally:
            self.flush_pending_project_sync(reason="run-once")

    def run_continuous(
        self,
        *,
        card_code: str = "",
        variant_key: str = "",
        task_type: str = "",
        source_id: str = "",
        task_payload: dict[str, Any] | None = None,
    ) -> None:
        self.ensure_datastores()
        self.prime_work(
            card_code=card_code,
            variant_key=variant_key,
            task_type=task_type,
            source_id=source_id,
            task_payload=task_payload,
        )
        self.update_status(current_state="starting", current_task_label="", current_card_code="", current_task_type="", last_error="")
        self.append_log(level="info", event_type="engine_started", message="Continuous learning engine started.")
        try:
            while True:
                counts = self.queue_counts()
                if counts["queued"] < max(self.seed_batch_size // 4, 1):
                    self.seed_missing_bootstrap_tasks(limit=self.seed_batch_size)

                results = self.process_parallel_batch(self.max_parallel_validations)
                if not results:
                    self.update_status(
                        current_state="sleeping",
                        current_task_label="Waiting for queued work",
                        current_card_code="",
                        current_task_type="",
                    )
                    self.maybe_send_learning_notification()
                    time.sleep(self.sleep_seconds)
                    continue

                time.sleep(min(self.sleep_seconds, 0.5))
        except KeyboardInterrupt:
            self.append_log(level="info", event_type="engine_stopped", message="Continuous learning engine stopped by operator.")
            self.update_status(current_state="idle", current_task_label="", current_card_code="", current_task_type="")
        finally:
            self.flush_pending_project_sync(reason="engine-shutdown")


def handle_bootstrap_dossier(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("bootstrap_dossier requires a card_code")
    dossier = engine.resolve_local_profile(task.card_code)
    engine.upsert_dossier(dossier)
    return {
        "message": f"Bootstrapped dossier for {dossier['card_code']}",
        "card_code": dossier["card_code"],
        "task_type": task.task_type,
    }


def handle_sync_missing_fields(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("sync_missing_fields requires a card_code")
    local_profile = engine.resolve_local_profile(task.card_code)
    existing = engine.fetch_dossier(local_profile["card_code"])
    if existing is None:
        engine.upsert_dossier(local_profile)
        return {
            "message": f"Created dossier while syncing fields for {local_profile['card_code']}",
            "card_code": local_profile["card_code"],
            "task_type": task.task_type,
        }

    merged_facts = dict(existing.get("basic_facts") or {})
    updates = 0
    for field_name, value in local_profile["basic_facts"].items():
        if merged_facts.get(field_name) in (None, "", [], {}) and value not in (None, "", [], {}):
            merged_facts[field_name] = value
            updates += 1

    merged = {
        "card_code": local_profile["card_code"],
        "card_name": existing.get("card_name") or local_profile["card_name"],
        "set_code": existing.get("set_code") or local_profile["set_code"],
        "rarity": existing.get("rarity") or local_profile["rarity"],
        "basic_facts": merged_facts,
        "source_summary": existing.get("source_summary") or local_profile["source_summary"],
        "confidence": max(float(existing.get("confidence") or 0.0), float(local_profile["confidence"])),
        "verification_state": existing.get("verification_state") or local_profile["verification_state"],
    }
    engine.upsert_dossier(merged)
    return {
        "message": f"Synced {updates} missing field(s) for {local_profile['card_code']}",
        "card_code": local_profile["card_code"],
        "task_type": task.task_type,
        "updated_fields": updates,
    }


def handle_inspect_missing_image(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("inspect_missing_image requires a card_code")
    local_profile = engine.resolve_local_profile(task.card_code)
    existing = engine.fetch_dossier(local_profile["card_code"])
    merged_facts = dict((existing or {}).get("basic_facts") or {})
    merged_facts.update(local_profile["basic_facts"])
    merged_facts["has_local_image"] = bool(int(local_profile["basic_facts"].get("image_variant_count") or 0) > 0)
    merged = {
        "card_code": local_profile["card_code"],
        "card_name": (existing or {}).get("card_name") or local_profile["card_name"],
        "set_code": (existing or {}).get("set_code") or local_profile["set_code"],
        "rarity": (existing or {}).get("rarity") or local_profile["rarity"],
        "basic_facts": merged_facts,
        "source_summary": (existing or {}).get("source_summary") or local_profile["source_summary"],
        "confidence": max(float((existing or {}).get("confidence") or 0.0), float(local_profile["confidence"])),
        "verification_state": (existing or {}).get("verification_state") or local_profile["verification_state"],
    }
    engine.upsert_dossier(merged)
    image_count = int(local_profile["basic_facts"].get("image_variant_count") or 0)
    return {
        "message": f"Inspected image coverage for {local_profile['card_code']} ({image_count} variant image reference(s))",
        "card_code": local_profile["card_code"],
        "task_type": task.task_type,
        "image_variant_count": image_count,
    }


def handle_refresh_progress(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    snapshot = load_learning_engine_status(
        queue_db_path=engine.queue_db_path,
        status_db_path=engine.status_db_path,
        dossier_db_path=engine.dossier_db_path,
    )
    return {
        "message": (
            f"Refreshed progress snapshot: {snapshot['dossier_count']} dossiers, "
            f"{snapshot['queue_length']} queued task(s)."
        ),
        "task_type": task.task_type,
        "queue_length": snapshot["queue_length"],
        "dossier_count": snapshot["dossier_count"],
    }


def handle_fetch_official_source(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("fetch_official_source requires a card_code")
    source_id = task.source_id or "official-cardlist"
    records = engine.fetch_official_source_records(
        source_id=source_id,
        card_code=task.card_code,
        task_payload=task.task_payload,
    )
    if not records:
        raise LookupError(f"No source record found for {task.card_code} from {source_id}")
    record = records[0]
    engine.store_source_record(record, verification_state="source-fetched")
    return {
        "message": f"Fetched source-backed record for {record.card_code} from {source_id}",
        "card_code": record.card_code,
        "task_type": task.task_type,
        "source_id": source_id,
        "source_reference": record.source_reference,
    }


def handle_verify_official_fields(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("verify_official_fields requires a card_code")
    source_id = task.source_id or "official-cardlist"
    records = engine.fetch_official_source_records(
        source_id=source_id,
        card_code=task.card_code,
        task_payload=task.task_payload,
    )
    if not records:
        raise LookupError(f"No source record found for {task.card_code} from {source_id}")
    record = records[0]
    engine.store_source_record(record, verification_state="verified-source-fields")
    engine.merge_source_record_into_dossier(record, verification_state="source-backed")
    sync_result = engine.queue_validated_card_for_project_sync(record, task_type=task.task_type)
    return {
        "message": f"Verified source-backed fields for {record.card_code} from {source_id}",
        "card_code": record.card_code,
        "task_type": task.task_type,
        "source_id": source_id,
        "source_reference": record.source_reference,
        "project_sync": sync_result,
    }


def handle_refresh_from_source(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    source_id = task.source_id or "official-cardlist"
    set_code = str(task.task_payload.get("set_code") or "").strip().upper()
    target_card_code = task.card_code.strip().upper()
    records = engine.fetch_official_source_records(
        source_id=source_id,
        card_code=target_card_code,
        set_code=set_code,
        task_payload=task.task_payload,
    )
    if not records:
        raise LookupError(
            f"No source refresh records found for {target_card_code or set_code or 'requested scope'} from {source_id}"
        )

    queued = 0
    for record in records:
        if engine.enqueue_task(
            card_code=record.card_code,
            task_type="verify_official_fields",
            source_id=source_id,
            priority=max(task.priority - 1, 0),
            task_payload=task.task_payload,
        ):
            queued += 1

    scope_label = target_card_code or set_code or "source scope"
    return {
        "message": f"Queued {queued} verify_official_fields task(s) from {source_id} for {scope_label}",
        "task_type": task.task_type,
        "source_id": source_id,
        "queued_tasks": queued,
    }


def handle_discover_set_cards(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    source_id = task.source_id or "official-cardlist"
    set_code = str(task.task_payload.get("set_code") or "").strip().upper()
    if not set_code:
        raise ValueError("discover_set_cards requires task_payload['set_code']")
    records = engine.fetch_official_source_records(
        source_id=source_id,
        set_code=set_code,
        task_payload=task.task_payload,
    )
    if not records:
        raise LookupError(f"No source records found for set {set_code} from {source_id}")

    queued = 0
    for record in records:
        if engine.enqueue_task(
            card_code=record.card_code,
            task_type="verify_official_fields",
            source_id=source_id,
            priority=max(task.priority - 1, 0),
            task_payload={"set_code": set_code, **dict(task.task_payload or {})},
        ):
            queued += 1
    return {
        "message": f"Discovered {len(records)} card(s) for {set_code} and queued {queued} verification task(s)",
        "task_type": task.task_type,
        "source_id": source_id,
        "set_code": set_code,
        "cards_discovered": len(records),
        "queued_tasks": queued,
    }


def handle_discover_sources(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    raw_urls = task.task_payload.get("urls") or []
    if not isinstance(raw_urls, list) or not raw_urls:
        raise ValueError("discover_sources requires task_payload['urls'] as a non-empty list")
    rows = [
        {
            "url": str(item.get("url") or ""),
            "title": str(item.get("title") or ""),
            "notes": str(item.get("notes") or ""),
        }
        if isinstance(item, dict)
        else {"url": str(item or ""), "title": "", "notes": ""}
        for item in raw_urls
    ]
    detected_at = utc_timestamp()
    candidates = discover_source_candidates(rows, detected_at=detected_at)
    created = 0
    for candidate in candidates:
        if engine.store_discovered_source_candidate(candidate):
            created += 1
    return {
        "message": f"Stored {created} new source candidate(s) for review ({len(candidates)} matched heuristics)",
        "task_type": task.task_type,
        "discovered_candidates": len(candidates),
        "new_candidates": created,
    }


def handle_fetch_card_image(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("fetch_card_image requires a card_code")
    payload = dict(task.task_payload or {})
    source_id = task.source_id or "official-card-images"
    variant_key = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    records = engine.fetch_image_source_records(
        source_id=source_id,
        card_code=task.card_code,
        variant_key=variant_key,
        task_payload=payload,
    )
    if not records:
        raise LookupError(f"No image source record found for {task.card_code} from {source_id}")
    record = records[0]
    resolved_variant = normalize_variant_key(variant_key or record.variant_key or "")
    filename = build_image_filename(record.card_code, resolved_variant)
    snapshot_path = payload.get("snapshot_path") or ""
    resolved_path = engine.resolve_image_path(record.image_path, snapshot_path=snapshot_path)
    image_bytes = engine.fetch_image_bytes(image_path=resolved_path, source_url=record.source_url)
    destination = engine.resolve_image_destination(filename)
    destination.write_bytes(image_bytes)
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    width, height = read_png_dimensions(image_bytes)
    now = utc_timestamp()
    try:
        relative_path = str(destination.relative_to(engine.image_dest_root))
    except ValueError:
        relative_path = str(destination)
    engine.store_image_record(
        record,
        filename=filename,
        local_path=relative_path,
        image_hash=image_hash,
        width=width,
        height=height,
        verification_state="provisional",
        downloaded_at=now,
        last_verified_at="",
        last_error="",
    )
    return {
        "message": f"Fetched image for {record.card_code}{f'({resolved_variant})' if resolved_variant else ''}",
        "task_type": task.task_type,
        "card_code": record.card_code,
        "variant_key": resolved_variant,
        "filename": filename,
        "source_id": source_id,
        "source_reference": record.source_reference,
        "image_hash": image_hash,
    }


def handle_verify_card_image(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("verify_card_image requires a card_code")
    payload = dict(task.task_payload or {})
    source_id = task.source_id or "official-card-images"
    variant_key = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    existing = engine.fetch_image_registry_record(
        card_code=task.card_code,
        variant_key=variant_key,
        source_id=source_id,
    )
    if existing is None:
        raise LookupError(f"No stored image registry entry for {task.card_code} from {source_id}")
    local_path = str(existing.get("local_path") or "")
    path = Path(local_path)
    if not path.is_absolute():
        path = engine.image_dest_root / path
    if not path.is_file():
        raise FileNotFoundError(f"Local image file missing: {path}")
    image_bytes = path.read_bytes()
    image_hash = hashlib.sha256(image_bytes).hexdigest()
    width, height = read_png_dimensions(image_bytes)
    now = utc_timestamp()
    record = NormalizedImageRecord(
        card_code=task.card_code,
        variant_key=variant_key,
        source_id=source_id,
        source_url=str(existing.get("source_url") or ""),
        source_reference=str(existing.get("source_reference") or ""),
        image_path=str(path),
        fetched_at=now,
        width=width,
        height=height,
    )
    engine.store_image_record(
        record,
        filename=str(existing.get("filename") or build_image_filename(task.card_code, variant_key)),
        local_path=str(existing.get("local_path") or ""),
        image_hash=image_hash,
        width=width,
        height=height,
        verification_state="verified",
        downloaded_at=str(existing.get("downloaded_at") or now),
        last_verified_at=now,
        last_error="",
    )
    return {
        "message": f"Verified image for {task.card_code}{f'({variant_key})' if variant_key else ''}",
        "task_type": task.task_type,
        "card_code": task.card_code,
        "variant_key": variant_key,
        "filename": existing.get("filename") or "",
        "source_id": source_id,
        "source_reference": existing.get("source_reference") or "",
        "image_hash": image_hash,
    }


def handle_refresh_card_image(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    source_id = task.source_id or "official-card-images"
    target_card_code = task.card_code or payload.get("card_code") or ""
    target_set_code = payload.get("set_code") or ""
    variant_key = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    records = engine.fetch_image_source_records(
        source_id=source_id,
        card_code=target_card_code,
        set_code=target_set_code,
        variant_key=variant_key,
        task_payload=payload,
    )
    if not records:
        raise LookupError(
            f"No image refresh records found for {target_card_code or target_set_code or 'requested scope'} from {source_id}"
        )
    queued = 0
    for record in records:
        if engine.enqueue_task(
            card_code=record.card_code,
            variant_key=record.variant_key,
            task_type="fetch_card_image",
            source_id=source_id,
            priority=max(task.priority - 1, 0),
            task_payload=payload,
        ):
            queued += 1
    scope_label = target_card_code or target_set_code or "image scope"
    return {
        "message": f"Queued {queued} fetch_card_image task(s) from {source_id} for {scope_label}",
        "task_type": task.task_type,
        "source_id": source_id,
        "queued_tasks": queued,
    }


TASK_HANDLERS: dict[str, Callable[[MiruLearningEngine, LearningTask], dict[str, Any]]] = {
    "bootstrap_dossier": handle_bootstrap_dossier,
    "sync_missing_fields": handle_sync_missing_fields,
    "inspect_missing_image": handle_inspect_missing_image,
    "refresh_progress": handle_refresh_progress,
    "fetch_official_source": handle_fetch_official_source,
    "verify_official_fields": handle_verify_official_fields,
    "refresh_from_source": handle_refresh_from_source,
    "discover_set_cards": handle_discover_set_cards,
    "discover_sources": handle_discover_sources,
    "fetch_card_image": handle_fetch_card_image,
    "verify_card_image": handle_verify_card_image,
    "refresh_card_image": handle_refresh_card_image,
}


def load_learning_engine_status(
    *,
    queue_db_path: Path = DEFAULT_QUEUE_DB_PATH,
    status_db_path: Path = DEFAULT_STATUS_DB_PATH,
    dossier_db_path: Path = DEFAULT_DOSSIER_DB_PATH,
    total_cards: int | None = None,
) -> dict[str, Any]:
    snapshot = {
        "queue_db_path": str(queue_db_path),
        "status_db_path": str(status_db_path),
        "dossier_db_path": str(dossier_db_path),
        "queue_db_exists": Path(queue_db_path).is_file(),
        "status_db_exists": Path(status_db_path).is_file(),
        "dossier_db_exists": Path(dossier_db_path).is_file(),
        "current_state": "idle",
        "current_task_label": "",
        "current_card_code": "",
        "current_task_type": "",
        "current_source_id": "",
        "current_image_task": "",
        "last_completed_task": "",
        "last_completed_card": "",
        "last_heartbeat": "",
        "last_error": "",
        "processed_count": 0,
        "success_count": 0,
        "error_count": 0,
        "last_source_id": "",
        "last_source_reference": "",
        "last_source_update": "",
        "source_success_count": 0,
        "source_error_count": 0,
        "last_image_update": "",
        "image_success_count": 0,
        "image_error_count": 0,
        "max_parallel_validations": 1,
        "queue_length": 0,
        "running_count": 0,
        "failed_count": 0,
        "completed_count": 0,
        "dossier_count": 0,
        "images_tracked": 0,
        "images_verified": 0,
        "images_missing": 0,
        "queue_backlog": 0,
        "validated_card_count": 0,
        "cards_learned_per_hour": 0,
        "validation_success_rate": 0.0,
        "average_validation_seconds": 0.0,
        "discovery_candidate_count": 0,
        "discovery_pending_review_count": 0,
        "learner_mode": get_learner_mode(),
        "last_publish_at": "",
        "last_blocked_at": "",
        "review_queue_count": 0,
        "api_permission_events": [],
        "approved_sources_config_path": "",
        "approved_sources_loaded_count": 0,
        "approved_sources_config_errors": [],
    }

    queue_path = Path(queue_db_path)
    if queue_path.is_file():
        with closing(connect_sqlite(queue_path)) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM learning_queue GROUP BY status"
            ).fetchall()
            validation_counts = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_validations,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_validations,
                    SUM(
                        CASE
                            WHEN status = 'completed'
                             AND completed_at >= datetime('now', '-1 hour')
                            THEN 1
                            ELSE 0
                        END
                    ) AS recent_validations,
                    AVG(
                        CASE
                            WHEN status = 'completed'
                             AND trim(coalesce(claimed_at, '')) != ''
                             AND trim(coalesce(completed_at, '')) != ''
                            THEN (julianday(completed_at) - julianday(claimed_at)) * 86400.0
                            ELSE NULL
                        END
                    ) AS average_validation_seconds
                FROM learning_queue
                WHERE task_type = 'verify_official_fields'
                """
            ).fetchone()
        for row in rows:
            status = str(row["status"])
            total = int(row["total"])
            if status == "queued":
                snapshot["queue_length"] = total
            elif status == "running":
                snapshot["running_count"] = total
            elif status == "failed":
                snapshot["failed_count"] = total
            elif status == "completed":
                snapshot["completed_count"] = total
        snapshot["queue_backlog"] = int(snapshot["queue_length"])
        if validation_counts is not None:
            completed_validations = int(validation_counts["completed_validations"] or 0)
            failed_validations = int(validation_counts["failed_validations"] or 0)
            snapshot["validated_card_count"] = completed_validations
            snapshot["cards_learned_per_hour"] = int(validation_counts["recent_validations"] or 0)
            total_validation_outcomes = completed_validations + failed_validations
            snapshot["validation_success_rate"] = (
                round((completed_validations / total_validation_outcomes) * 100.0, 1)
                if total_validation_outcomes
                else 0.0
            )
            snapshot["average_validation_seconds"] = round(
                float(validation_counts["average_validation_seconds"] or 0.0),
                2,
            )

    status_path = Path(status_db_path)
    if status_path.is_file():
        with closing(connect_sqlite(status_path)) as conn:
            row = conn.execute("SELECT * FROM engine_status WHERE singleton_id = 1").fetchone()
            try:
                discovery_row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_candidates,
                        SUM(CASE WHEN review_status = 'pending_review' THEN 1 ELSE 0 END) AS pending_review
                    FROM discovered_sources
                    """
                ).fetchone()
                if discovery_row is not None:
                    snapshot["discovery_candidate_count"] = int(discovery_row["total_candidates"] or 0)
                    snapshot["discovery_pending_review_count"] = int(discovery_row["pending_review"] or 0)
            except sqlite3.OperationalError:
                snapshot["discovery_candidate_count"] = 0
                snapshot["discovery_pending_review_count"] = 0
            if row is not None:
                available_keys = set(row.keys())
                for key in (
                    "current_state",
                    "current_task_label",
                    "current_card_code",
                    "current_task_type",
                    "current_source_id",
                    "current_image_task",
                    "last_completed_task",
                    "last_completed_card",
                    "last_heartbeat",
                    "last_error",
                    "processed_count",
                    "success_count",
                    "error_count",
                    "last_source_id",
                    "last_source_reference",
                    "last_source_update",
                    "source_success_count",
                    "source_error_count",
                    "last_image_update",
                    "image_success_count",
                    "image_error_count",
                    "max_parallel_validations",
                    "last_publish_at",
                    "last_blocked_at",
                ):
                    if key in available_keys:
                        snapshot[key] = row[key]
            try:
                rq = conn.execute("SELECT COUNT(*) AS n FROM learner_review_queue").fetchone()
                if rq is not None:
                    snapshot["review_queue_count"] = int(rq["n"] or 0)
            except sqlite3.OperationalError:
                snapshot["review_queue_count"] = 0
            try:
                perm_rows = conn.execute(
                    """
                    SELECT event_type, message, card_code, task_type, created_at
                    FROM engine_log
                    WHERE event_type IN (?, ?, ?)
                    ORDER BY created_at DESC
                    LIMIT 20
                    """,
                    (EVENT_API_REQUIRED_SOURCE_DETECTED, EVENT_ACCESS_POLICY_UNCLEAR, EVENT_PERMISSION_REQUIRED),
                ).fetchall()
                snapshot["api_permission_events"] = [
                    {
                        "event_type": str(r["event_type"]),
                        "message": str(r["message"] or ""),
                        "card_code": str(r["card_code"] or ""),
                        "task_type": str(r["task_type"] or ""),
                        "created_at": str(r["created_at"] or ""),
                    }
                    for r in (perm_rows or [])
                ]
            except sqlite3.OperationalError:
                snapshot["api_permission_events"] = []

    dossier_path = Path(dossier_db_path)
    if dossier_path.is_file():
        with closing(connect_sqlite(dossier_path)) as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM learning_dossiers").fetchone()
            snapshot["dossier_count"] = int(row["total"] if row is not None else 0)
            for state, key in (("verified", "dossier_verified_count"), ("source-backed", "dossier_source_backed_count")):
                count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM learning_dossiers WHERE verification_state = ?",
                    (state,),
                ).fetchone()
                snapshot[key] = int(count_row[0] or 0) if count_row else 0
            try:
                image_row = conn.execute("SELECT COUNT(*) AS total FROM learning_dossier_images").fetchone()
                if image_row is not None:
                    snapshot["images_tracked"] = int(image_row["total"])
                verified_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM learning_dossier_images
                    WHERE verification_state = 'verified'
                    """
                ).fetchone()
                if verified_row is not None:
                    snapshot["images_verified"] = int(verified_row["total"])
            except sqlite3.OperationalError:
                snapshot["images_tracked"] = 0
                snapshot["images_verified"] = 0

    if total_cards is not None:
        snapshot["images_missing"] = max(int(total_cards) - int(snapshot["images_tracked"]), 0)

    try:
        approved_status = get_approved_sources_config_status()
        snapshot["approved_sources_config_path"] = str(approved_status.get("config_path", ""))
        snapshot["approved_sources_loaded_count"] = int(approved_status.get("loaded_count", 0))
        snapshot["approved_sources_config_errors"] = list(approved_status.get("errors", []))
    except Exception:  # fail visibly but do not break status load
        snapshot["approved_sources_config_errors"] = ["Failed to read approved-sources config status."]

    return snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Miru local learning engine")
    parser.add_argument("--mode", choices=("continuous", "once", "status"), default="once")
    parser.add_argument("--card", default="", help="Optional card code for the queued learning task.")
    parser.add_argument(
        "--task",
        default="",
        choices=sorted(TASK_HANDLERS),
        help="Optional learning task type to enqueue before running.",
    )
    parser.add_argument(
        "--source",
        default="",
        choices=sorted(build_source_registry()),
        help="Optional registry-defined source id for source-backed tasks.",
    )
    parser.add_argument("--variant", default="", help="Optional variant key for image tasks.")
    parser.add_argument("--queue-db", type=Path, default=DEFAULT_QUEUE_DB_PATH)
    parser.add_argument("--status-db", type=Path, default=DEFAULT_STATUS_DB_PATH)
    parser.add_argument("--dossier-db", type=Path, default=DEFAULT_DOSSIER_DB_PATH)
    parser.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB_PATH)
    parser.add_argument("--knowledge-cache", type=Path, default=DEFAULT_KNOWLEDGE_CACHE_PATH)
    parser.add_argument("--sleep-seconds", type=float, default=DEFAULT_SLEEP_SECONDS)
    parser.add_argument("--max-parallel-validations", type=int, default=DEFAULT_MAX_PARALLEL_VALIDATIONS)
    parser.add_argument("--image-dest", type=Path, default=DEFAULT_IMAGE_DEST_ROOT)
    parser.add_argument("--snapshot-path", default="", help="Optional local source snapshot path for source-backed tasks.")
    parser.add_argument("--snapshot-url", default="", help="Optional source snapshot URL for source-backed tasks.")
    return parser


def build_engine_from_args(args: argparse.Namespace) -> MiruLearningEngine:
    return MiruLearningEngine(
        queue_db_path=args.queue_db,
        status_db_path=args.status_db,
        dossier_db_path=args.dossier_db,
        knowledge_cache_path=args.knowledge_cache,
        catalog_db_path=args.catalog_db,
        image_dest_root=args.image_dest,
        sleep_seconds=args.sleep_seconds,
        max_parallel_validations=args.max_parallel_validations,
    )


def main() -> int:
    args = build_parser().parse_args()
    engine = build_engine_from_args(args)
    task_payload = {
        key: value
        for key, value in {
            "snapshot_path": args.snapshot_path,
            "snapshot_url": args.snapshot_url,
            "variant_key": args.variant,
        }.items()
        if str(value).strip()
    }

    if args.mode == "status":
        engine.ensure_datastores()
        print(
            json.dumps(
                load_learning_engine_status(
                    queue_db_path=engine.queue_db_path,
                    status_db_path=engine.status_db_path,
                    dossier_db_path=engine.dossier_db_path,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if args.mode == "once":
        result = engine.run_once(
            card_code=args.card,
            variant_key=args.variant,
            task_type=args.task,
            source_id=args.source,
            task_payload=task_payload,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    engine.run_continuous(
        card_code=args.card,
        variant_key=args.variant,
        task_type=args.task,
        source_id=args.source,
        task_payload=task_payload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
