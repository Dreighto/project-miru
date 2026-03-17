from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import sqlite3
import struct
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
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
    is_placeholder_card_reference,
    normalize_card_code,
    validate_one_piece_reference,
)
from tools.miru_dossier_store import MiruDossierStore
from tools.miru_learning_notifications import (
    build_batch_progress_notification,
    build_learning_notification,
    build_set_completion_notification,
    format_compact_number,
    learning_batch_threshold,
    load_completed_verified_sets,
    load_learning_batch_state,
    load_verified_learning_totals,
    load_notified_completed_sets,
    save_learning_batch_state,
    save_notified_completed_sets,
    save_learning_notification_baseline,
)
from tools.miru_source_adapters import (
    MiruSourceCache,
    NormalizedImageRecord,
    NormalizedSourceRecord,
    OfficialCardImageSourceAdapter,
    OfficialCardListSourceAdapter,
    SourceAdapterError,
)
from tools.miru_source_registry import (
    MiruSourceEntry,
    build_source_registry,
    get_source_entry,
)
from tools.miru_project_sync import MiruProjectDbSync, ensure_catalog_sync_schema
from tools.miru_pushover import send_operator_notification
from tools.miru_source_discovery import (
    DiscoveredSourceCandidate,
    discover_source_candidates,
)
from tools.miru_visual_intelligence import VisualAnalysisResult, analyze_card_image

try:
    from tools.miru_preflight_safety import (
        PRIORITY_HIGH_INT,
        PRIORITY_LOW_INT,
        PRIORITY_MEDIUM_INT,
        review_priority_for_task,
    )
except ImportError:
    PRIORITY_HIGH_INT = 100
    PRIORITY_MEDIUM_INT = 50
    PRIORITY_LOW_INT = 10

    def review_priority_for_task(task_type: str, *, conflict_type: str | None = None) -> int:
        return PRIORITY_MEDIUM_INT


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_queue.db"
DEFAULT_STATUS_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_log.db"
DEFAULT_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"
DEFAULT_VERIFIED_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_dossiers.db"
DEFAULT_PROJECT_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_KNOWLEDGE_CACHE_PATH = PROJECT_ROOT / "data" / "miru_ai_onepiece_knowledge.json"
DEFAULT_CATALOG_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_SOURCE_CACHE_DB_PATH = PROJECT_ROOT / "data" / "miru_source_cache.db"
DEFAULT_IMAGE_DEST_ROOT = PROJECT_ROOT / "data" / "miru_images"
DEFAULT_SLEEP_SECONDS = 2.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_SEED_BATCH_SIZE = 25
DEFAULT_MAX_PARALLEL_VALIDATIONS = 2
QUEUE_LOW_THRESHOLD = 12
SEEDER_REFILL_CAP = 24
DEFAULT_STALE_TASK_SECONDS = 900
DEFAULT_WORKER_HEARTBEAT_STALE_SECONDS = 180
DEFAULT_LOCK_FILE_PATH = PROJECT_ROOT / "data" / "miru_learning_engine.lock"
LEARNING_ENGINE_SCHEMA_VERSION = "2026-03-stability-1"

DEFAULT_SOURCE_SNAPSHOT_ENV: dict[str, tuple[str, str]] = {
    "official-cardlist": (
        "MIRU_OFFICIAL_CARDLIST_SNAPSHOT_PATH",
        "MIRU_OFFICIAL_CARDLIST_SNAPSHOT_URL",
    ),
    "reputable-card-db": (
        "MIRU_REPUTABLE_CARD_DB_SNAPSHOT_PATH",
        "MIRU_REPUTABLE_CARD_DB_SNAPSHOT_URL",
    ),
    "official-card-images": (
        "MIRU_OFFICIAL_CARD_IMAGES_SNAPSHOT_PATH",
        "MIRU_OFFICIAL_CARD_IMAGES_SNAPSHOT_URL",
    ),
}

DEFAULT_SOURCE_SNAPSHOT_PATHS: dict[str, tuple[Path, ...]] = {
    "official-cardlist": (
        PROJECT_ROOT / "data" / "official-cardlist-snapshot.json",
        PROJECT_ROOT / "data" / "official_cardlist_snapshot.json",
    ),
    "reputable-card-db": (
        PROJECT_ROOT / "data" / "reputable-card-db-snapshot.json",
        PROJECT_ROOT / "data" / "reputable_card_db_snapshot.json",
    ),
    "official-card-images": (
        PROJECT_ROOT / "data" / "official-card-images-snapshot.json",
        PROJECT_ROOT / "data" / "official_card_images_snapshot.json",
    ),
}


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class SingleInstanceLock:
    def __init__(self, path: Path, fd: int) -> None:
        self.path = Path(path)
        self._fd = int(fd)

    def close(self) -> None:
        if self._fd < 0:
            return
        try:
            os.close(self._fd)
        finally:
            self._fd = -1
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _lock_file_payload(pid: int) -> str:
    return json.dumps({"pid": int(pid), "created_at": utc_timestamp()}, sort_keys=True)


def _read_lock_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return {"pid": 0, "raw": ""}
    if not raw:
        return {"pid": 0, "raw": raw}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = raw
    if isinstance(payload, dict):
        try:
            pid = int(payload.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        return {"pid": pid, "raw": raw, "created_at": str(payload.get("created_at") or "")}
    try:
        pid = int(str(payload).strip())
    except (TypeError, ValueError):
        pid = 0
    return {"pid": pid, "raw": raw}


def _pid_is_running(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def inspect_single_instance_lock(path: str | Path) -> dict[str, Any]:
    lock_path = Path(path)
    info = {
        "path": str(lock_path),
        "exists": lock_path.exists(),
        "pid": 0,
        "is_running": False,
        "is_stale": False,
        "created_at": "",
    }
    if not info["exists"]:
        return info
    payload = _read_lock_file(lock_path)
    info["pid"] = int(payload.get("pid") or 0)
    info["created_at"] = str(payload.get("created_at") or "")
    info["is_running"] = _pid_is_running(info["pid"])
    info["is_stale"] = bool(info["exists"] and not info["is_running"])
    return info


def acquire_single_instance_lock(path: str | Path) -> SingleInstanceLock | None:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            lock_info = inspect_single_instance_lock(lock_path)
            if lock_info["is_stale"]:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    return None
                continue
            return None
        os.write(fd, _lock_file_payload(os.getpid()).encode("utf-8", errors="ignore"))
        return SingleInstanceLock(lock_path, fd)
    return None


def format_learning_summary(*, processed: int, success: int, retries: int, failures: int) -> str:
    if processed <= 0:
        return "Miru is awake, but no learning tasks have finished yet."
    if failures <= 0 and retries <= 0:
        return (
            f"Miru worked through {format_compact_number(processed)} tasks cleanly, "
            f"with {format_compact_number(success)} successful outcomes."
        )
    if failures <= 0:
        return (
            f"Miru worked through {format_compact_number(processed)} tasks with "
            f"{format_compact_number(retries)} retries and no persistent failures."
        )
    return (
        f"Miru worked through {format_compact_number(processed)} tasks, with "
        f"{format_compact_number(failures)} failures still needing review."
    )


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


def derive_set_family(value: str) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("PRB-"):
        return "PRB"
    if text.startswith("P-"):
        return "P"
    if "-" in text:
        text = text.split("-", 1)[0]
    return "".join(char for char in text if char.isalpha())


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


IMAGE_SAMPLE_RE = re.compile(r"\b(sample|not[-_\s]?for[-_\s]?sale|for[-_\s]?display)\b", re.IGNORECASE)
IMAGE_POOR_QUALITY_RE = re.compile(r"\b(thumb|thumbnail|tiny|preview|blurry|blur|cropped|crop)\b", re.IGNORECASE)
IMAGE_QUALITY_BASE_SCORES = {
    "official_clean": 120.0,
    "official_sample": 92.0,
    "trusted_scan": 88.0,
    "fallback_lowres": 42.0,
    "rejected": -200.0,
}
IMAGE_QUALITY_RANKS = {
    "rejected": 0,
    "fallback_lowres": 1,
    "trusted_scan": 2,
    "official_sample": 3,
    "official_clean": 4,
}
IMAGE_SELECTION_SCOPES = ("card_default", "print_default", "gallery_preferred")
IMAGE_INTELLIGENCE_TASK_TYPES = {
    "discover_image_candidates",
    "verify_image_candidate",
    "link_variant_image",
    "resolve_print_relationships",
    "select_best_image",
    "scan_image_upgrades",
    "scan_missing_images",
    "rescore_image_candidates",
    "sync_verified_image_selection",
}
ALT_ART_SIGNAL_TOKENS = {"alt", "altart", "sp", "manga", "signed", "illustration", "special", "serial"}
PARALLEL_SIGNAL_TOKENS = {"parallel", "foil", "gold", "silver"}
PROMO_SIGNAL_TOKENS = {"promo", "prize", "winner", "stamped"}
REPRINT_SIGNAL_TOKENS = {"reprint", "prb", "revival", "reissue"}


def humanize_variant_label(value: str) -> str:
    normalized = normalize_variant_key(value)
    if not normalized:
        return "Base"
    return normalized.replace("-", " ").replace("_", " ").title()


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


@dataclass(slots=True)
class SourceTrustIntake:
    source_id: str
    source_type: str
    source_classification: str
    access_expectation: str
    allowed_for_learning: bool
    permission_status: str
    eligibility: str
    trust_tier: int
    trust_label: str
    evidence_role: str
    manual_approval_required: bool
    rationale: str
    notes: str
    last_reviewed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "source_classification": self.source_classification,
            "access_expectation": self.access_expectation,
            "allowed_for_learning": self.allowed_for_learning,
            "permission_status": self.permission_status,
            "eligibility": self.eligibility,
            "trust_tier": self.trust_tier,
            "trust_label": self.trust_label,
            "evidence_role": self.evidence_role,
            "manual_approval_required": self.manual_approval_required,
            "rationale": self.rationale,
            "notes": self.notes,
            "last_reviewed_at": self.last_reviewed_at,
        }


class MiruLearningEngine:
    def __init__(
        self,
        *,
        queue_db_path: Path = DEFAULT_QUEUE_DB_PATH,
        status_db_path: Path = DEFAULT_STATUS_DB_PATH,
        dossier_db_path: Path = DEFAULT_DOSSIER_DB_PATH,
        verified_dossier_db_path: Path = DEFAULT_VERIFIED_DOSSIER_DB_PATH,
        project_db_path: Path = DEFAULT_PROJECT_DB_PATH,
        knowledge_cache_path: Path = DEFAULT_KNOWLEDGE_CACHE_PATH,
        catalog_db_path: Path = DEFAULT_CATALOG_DB_PATH,
        image_dest_root: Path = DEFAULT_IMAGE_DEST_ROOT,
        source_cache_db_path: Path = DEFAULT_SOURCE_CACHE_DB_PATH,
        sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        seed_batch_size: int = DEFAULT_SEED_BATCH_SIZE,
        max_parallel_validations: int = DEFAULT_MAX_PARALLEL_VALIDATIONS,
        queue_low_threshold: int = QUEUE_LOW_THRESHOLD,
        seeder_refill_cap: int = SEEDER_REFILL_CAP,
        stale_task_seconds: int = DEFAULT_STALE_TASK_SECONDS,
        sync_batch_size: int = 3,
        sync_immediate: bool = True,
        lock_file_path: Path | None = None,
    ) -> None:
        self.queue_db_path = Path(queue_db_path)
        self.status_db_path = Path(status_db_path)
        self.dossier_db_path = Path(dossier_db_path)
        self.verified_dossier_db_path = Path(verified_dossier_db_path)
        self.project_db_path = Path(project_db_path)
        self.knowledge_cache_path = Path(knowledge_cache_path)
        self.catalog_db_path = Path(catalog_db_path)
        self.image_dest_root = Path(image_dest_root)
        self.source_cache_db_path = Path(source_cache_db_path)
        self.sleep_seconds = max(float(sleep_seconds), 0.1)
        self.max_attempts = max(int(max_attempts), 1)
        self.seed_batch_size = max(int(seed_batch_size), 1)
        self.max_parallel_validations = max(int(max_parallel_validations), 1)
        self.queue_low_threshold = max(int(queue_low_threshold), 1)
        self.seeder_refill_cap = max(int(seeder_refill_cap), 1)
        self.stale_task_seconds = max(int(stale_task_seconds), 60)
        self.lock_file_path = Path(lock_file_path) if lock_file_path else DEFAULT_LOCK_FILE_PATH
        self._instance_lock: SingleInstanceLock | None = None
        self._lock_issue_message = ""
        self._knowledge_cache: dict[str, Any] | None = None
        self._known_set_codes: set[str] | None = None
        self.source_registry = build_source_registry()
        self.source_cache = MiruSourceCache(
            db_path=self.source_cache_db_path,
            notifier=self.send_operator_notification,
        )
        self.official_source_adapter = OfficialCardListSourceAdapter(cache=self.source_cache)
        self.official_image_adapter = OfficialCardImageSourceAdapter(cache=self.source_cache)
        self.project_sync = MiruProjectDbSync(
            project_db_path=self.project_db_path,
            batch_size=sync_batch_size,
            sync_immediate=sync_immediate,
            logger=self.append_log,
        )
        self.verified_dossier_store = MiruDossierStore(self.verified_dossier_db_path)
        self._last_milestone_check = 0.0

    def ensure_datastores(self) -> None:
        for path in (self.queue_db_path, self.status_db_path, self.dossier_db_path, self.verified_dossier_db_path, self.catalog_db_path, self.project_db_path):
            path.parent.mkdir(parents=True, exist_ok=True)
        if self.lock_file_path is not None:
            self.lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.image_dest_root.mkdir(parents=True, exist_ok=True)

        # Keep the shared catalog schema compatible with older runtime DBs.
        ensure_catalog_sync_schema(self.catalog_db_path)
        catalog_status = inspect_fallback_catalog_db(self.catalog_db_path)
        if not catalog_status["usable"]:
            initialize_fallback_catalog_db(
                db_path=self.catalog_db_path,
                cache_path=self.knowledge_cache_path,
            )
        self.verified_dossier_store.ensure_schema()

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
            CREATE TABLE IF NOT EXISTS runtime_metadata (
                component TEXT NOT NULL PRIMARY KEY,
                schema_version TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
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
            CREATE TABLE IF NOT EXISTS budget_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL DEFAULT '',
                card_code TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                extra_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_budget_signals_created ON budget_signals(created_at DESC);
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
                print_id TEXT NOT NULL DEFAULT '',
                print_label TEXT NOT NULL DEFAULT '',
                variant_label TEXT NOT NULL DEFAULT '',
                filename TEXT NOT NULL,
                local_path TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT '',
                source_reference TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                verification_state TEXT NOT NULL DEFAULT 'provisional',
                image_hash TEXT NOT NULL DEFAULT '',
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                bytes_size INTEGER NOT NULL DEFAULT 0,
                mime_type TEXT NOT NULL DEFAULT '',
                source_trust_tier INTEGER NOT NULL DEFAULT 4,
                source_trust_label TEXT NOT NULL DEFAULT '',
                quality_tier TEXT NOT NULL DEFAULT 'fallback_lowres',
                sample_flag INTEGER NOT NULL DEFAULT 0,
                image_score REAL NOT NULL DEFAULT 0.0,
                print_match_confidence REAL NOT NULL DEFAULT 0.0,
                quality_score REAL NOT NULL DEFAULT 0.0,
                trust_score REAL NOT NULL DEFAULT 0.0,
                selection_confidence REAL NOT NULL DEFAULT 0.0,
                card_code_match_confidence REAL NOT NULL DEFAULT 0.0,
                variant_match_confidence REAL NOT NULL DEFAULT 0.0,
                art_family_confidence REAL NOT NULL DEFAULT 0.0,
                clarity_score REAL NOT NULL DEFAULT 0.0,
                crop_confidence REAL NOT NULL DEFAULT 0.0,
                selection_scope TEXT NOT NULL DEFAULT '',
                selection_reason TEXT NOT NULL DEFAULT '',
                content_status TEXT NOT NULL DEFAULT 'candidate',
                duplicate_group TEXT NOT NULL DEFAULT '',
                perceptual_hash TEXT NOT NULL DEFAULT '',
                origin_language TEXT NOT NULL DEFAULT 'en',
                english_print_exists INTEGER NOT NULL DEFAULT 1,
                display_policy TEXT NOT NULL DEFAULT 'english-first',
                provisional_language_display INTEGER NOT NULL DEFAULT 0,
                review_notes_json TEXT NOT NULL DEFAULT '[]',
                citation_payload_json TEXT NOT NULL DEFAULT '{}',
                score_breakdown_json TEXT NOT NULL DEFAULT '{}',
                replacement_eligible INTEGER NOT NULL DEFAULT 1,
                upgrade_status TEXT NOT NULL DEFAULT 'review-pending',
                is_current_best INTEGER NOT NULL DEFAULT 0,
                superseded_by_image_id INTEGER NOT NULL DEFAULT 0,
                downloaded_at TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                last_reviewed_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                UNIQUE(card_code, variant_key, source_id, filename)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_card
                ON learning_dossier_images(card_code);
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_source
                ON learning_dossier_images(source_id);
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_state
                ON learning_dossier_images(verification_state);
            CREATE TABLE IF NOT EXISTS learning_dossier_prints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                print_id TEXT NOT NULL,
                variant_key TEXT NOT NULL DEFAULT '',
                variant_label TEXT NOT NULL DEFAULT '',
                print_label TEXT NOT NULL DEFAULT '',
                print_group TEXT NOT NULL DEFAULT '',
                art_family_key TEXT NOT NULL DEFAULT '',
                parent_print_id TEXT NOT NULL DEFAULT '',
                release_set_code TEXT NOT NULL DEFAULT '',
                release_set_name TEXT NOT NULL DEFAULT '',
                is_base INTEGER NOT NULL DEFAULT 0,
                is_alt_art INTEGER NOT NULL DEFAULT 0,
                is_parallel INTEGER NOT NULL DEFAULT 0,
                is_promo INTEGER NOT NULL DEFAULT 0,
                is_reprint INTEGER NOT NULL DEFAULT 0,
                illustration_type TEXT NOT NULL DEFAULT '',
                verification_state TEXT NOT NULL DEFAULT 'scaffolded',
                match_confidence REAL NOT NULL DEFAULT 0.0,
                supporting_sources_json TEXT NOT NULL DEFAULT '[]',
                citation_payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT '',
                verified_at TEXT NOT NULL DEFAULT '',
                UNIQUE(card_code, print_id)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_prints_card
                ON learning_dossier_prints(card_code);
            CREATE INDEX IF NOT EXISTS idx_learning_dossier_prints_variant
                ON learning_dossier_prints(card_code, variant_key);
            CREATE TABLE IF NOT EXISTS learning_image_selections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                print_id TEXT NOT NULL DEFAULT '',
                variant_key TEXT NOT NULL DEFAULT '',
                selection_scope TEXT NOT NULL DEFAULT '',
                image_candidate_id INTEGER NOT NULL DEFAULT 0,
                best_image_flag INTEGER NOT NULL DEFAULT 1,
                upgrade_eligible INTEGER NOT NULL DEFAULT 1,
                selection_confidence REAL NOT NULL DEFAULT 0.0,
                quality_tier TEXT NOT NULL DEFAULT '',
                trust_tier INTEGER NOT NULL DEFAULT 4,
                selection_reason TEXT NOT NULL DEFAULT '',
                comparison_summary_json TEXT NOT NULL DEFAULT '{}',
                citation_payload_json TEXT NOT NULL DEFAULT '{}',
                origin_language TEXT NOT NULL DEFAULT 'en',
                english_print_exists INTEGER NOT NULL DEFAULT 1,
                display_policy TEXT NOT NULL DEFAULT 'english-first',
                provisional_language_display INTEGER NOT NULL DEFAULT 0,
                locked_by_operator INTEGER NOT NULL DEFAULT 0,
                selected_at TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                UNIQUE(card_code, print_id, selection_scope)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_image_selections_card
                ON learning_image_selections(card_code, selection_scope);
            CREATE TABLE IF NOT EXISTS learning_image_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                variant_key TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                image_hash TEXT NOT NULL DEFAULT '',
                local_path TEXT NOT NULL DEFAULT '',
                extraction_method TEXT NOT NULL DEFAULT 'local-parser',
                extracted_fields_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                verification_status TEXT NOT NULL DEFAULT 'no_visual_signal',
                source_rollup_json TEXT NOT NULL DEFAULT '{}',
                conflict_flags_json TEXT NOT NULL DEFAULT '[]',
                analysis_notes_json TEXT NOT NULL DEFAULT '[]',
                ocr_text_excerpt TEXT NOT NULL DEFAULT '',
                analyzed_at TEXT NOT NULL DEFAULT '',
                UNIQUE(card_code, variant_key, source_id, image_hash)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_image_analysis_card
                ON learning_image_analysis(card_code, analyzed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_learning_image_analysis_hash
                ON learning_image_analysis(image_hash, analyzed_at DESC);
            CREATE TABLE IF NOT EXISTS learning_source_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL DEFAULT '',
                source_classification TEXT NOT NULL DEFAULT '',
                eligibility TEXT NOT NULL DEFAULT 'ineligible_unknown_permissions',
                allowed_for_learning INTEGER NOT NULL DEFAULT 0,
                permission_status TEXT NOT NULL DEFAULT 'unknown-permissions',
                trust_tier INTEGER NOT NULL DEFAULT 4,
                trust_label TEXT NOT NULL DEFAULT '',
                evidence_role TEXT NOT NULL DEFAULT '',
                manual_approval_required INTEGER NOT NULL DEFAULT 1,
                review_status TEXT NOT NULL DEFAULT 'reviewed',
                rationale TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_source_reviews_eligibility
                ON learning_source_reviews(eligibility, updated_at DESC);
            CREATE TABLE IF NOT EXISTS learning_source_limited_use_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                card_code TEXT NOT NULL DEFAULT '',
                variant_key TEXT NOT NULL DEFAULT '',
                task_type TEXT NOT NULL DEFAULT '',
                evidence_role TEXT NOT NULL DEFAULT '',
                execution_outcome TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_learning_source_limited_use_events_source
                ON learning_source_limited_use_events(source_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS learning_fact_corroboration_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact_key TEXT NOT NULL,
                fact_type TEXT NOT NULL DEFAULT '',
                support_outcome TEXT NOT NULL DEFAULT 'insufficient_support',
                acceptance_outcome TEXT NOT NULL DEFAULT 'insufficient_support',
                evidence_mix_json TEXT NOT NULL DEFAULT '{}',
                stronger_source_support INTEGER NOT NULL DEFAULT 0,
                stronger_source_level TEXT NOT NULL DEFAULT 'none',
                conflict_detected INTEGER NOT NULL DEFAULT 0,
                classification_signals_json TEXT NOT NULL DEFAULT '[]',
                reasoning_summary TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                reviewed_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(fact_key, fact_type)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_fact_corroboration_records_fact
                ON learning_fact_corroboration_records(fact_type, updated_at DESC);
            CREATE TABLE IF NOT EXISTS learning_accepted_fact_provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL DEFAULT '',
                fact_key TEXT NOT NULL,
                fact_type TEXT NOT NULL DEFAULT '',
                accepted_value TEXT NOT NULL DEFAULT '',
                acceptance_outcome TEXT NOT NULL DEFAULT 'accept_verified_candidate',
                support_outcome TEXT NOT NULL DEFAULT 'verified_ready',
                corroboration_record_id INTEGER NOT NULL DEFAULT 0,
                evidence_mix_json TEXT NOT NULL DEFAULT '{}',
                classification_signals_json TEXT NOT NULL DEFAULT '[]',
                reasoning_summary TEXT NOT NULL DEFAULT '',
                source_context_json TEXT NOT NULL DEFAULT '{}',
                stored_in_dossier INTEGER NOT NULL DEFAULT 1,
                accepted_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(fact_key, fact_type)
            );
            CREATE INDEX IF NOT EXISTS idx_learning_accepted_fact_provenance_card
                ON learning_accepted_fact_provenance(card_code, updated_at DESC);
            CREATE TABLE IF NOT EXISTS learning_accepted_fact_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL DEFAULT '',
                fact_key TEXT NOT NULL,
                fact_type TEXT NOT NULL DEFAULT '',
                accepted_value TEXT NOT NULL DEFAULT '',
                acceptance_outcome TEXT NOT NULL DEFAULT '',
                support_outcome TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT 'accepted',
                change_summary TEXT NOT NULL DEFAULT '',
                corroboration_record_id INTEGER NOT NULL DEFAULT 0,
                source_context_json TEXT NOT NULL DEFAULT '{}',
                field_sensitivity TEXT NOT NULL DEFAULT 'contextual',
                acceptance_strength INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_learning_accepted_fact_history_fact
                ON learning_accepted_fact_history(fact_key, recorded_at DESC);
            CREATE TABLE IF NOT EXISTS learning_reverification_execution_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL DEFAULT '',
                fact_key TEXT NOT NULL DEFAULT '',
                fact_type TEXT NOT NULL DEFAULT '',
                execution_outcome TEXT NOT NULL DEFAULT '',
                reason_marker TEXT NOT NULL DEFAULT '',
                resolution_path TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                governance_json TEXT NOT NULL DEFAULT '{}',
                execution_summary_json TEXT NOT NULL DEFAULT '{}',
                recorded_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_learning_reverification_execution_log_fact
                ON learning_reverification_execution_log(fact_key, recorded_at DESC);
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
                leader_code TEXT NOT NULL DEFAULT '',
                leader_name TEXT NOT NULL DEFAULT '',
                archetype_key TEXT NOT NULL DEFAULT '',
                role_classification TEXT NOT NULL DEFAULT '',
                usage_frequency REAL NOT NULL DEFAULT 0.0,
                sample_size INTEGER NOT NULL DEFAULT 0,
                confidence_label TEXT NOT NULL DEFAULT 'no_evidence',
                fact_status TEXT NOT NULL DEFAULT 'tentative',
                source_id TEXT NOT NULL DEFAULT '',
                source_reference TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                freshness_at TEXT NOT NULL DEFAULT '',
                provenance_json TEXT NOT NULL DEFAULT '{}',
                source_summary TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS learning_usage_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                evidence_key TEXT NOT NULL UNIQUE,
                source_id TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                source_reference TEXT NOT NULL DEFAULT '',
                event_key TEXT NOT NULL DEFAULT '',
                event_name TEXT NOT NULL DEFAULT '',
                placement INTEGER NOT NULL DEFAULT 0,
                decklist_key TEXT NOT NULL DEFAULT '',
                card_code TEXT NOT NULL,
                leader_code TEXT NOT NULL DEFAULT '',
                leader_name TEXT NOT NULL DEFAULT '',
                archetype_label TEXT NOT NULL DEFAULT '',
                role_classification TEXT NOT NULL DEFAULT '',
                appearance_count INTEGER NOT NULL DEFAULT 1,
                confidence_input REAL NOT NULL DEFAULT 0.0,
                observed_at TEXT NOT NULL DEFAULT '',
                citation_payload_json TEXT NOT NULL DEFAULT '{}',
                provenance_json TEXT NOT NULL DEFAULT '{}',
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
                    CREATE INDEX IF NOT EXISTS idx_learning_queue_signature_status
                        ON learning_queue(task_signature, status)
                    """
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_queue_card_variant_task
                        ON learning_queue(card_code, variant_key, task_type, status)
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
            conn.execute(
                """
                INSERT INTO runtime_metadata (component, schema_version, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(component) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    updated_at = excluded.updated_at
                """,
                ("miru_learning_engine", LEARNING_ENGINE_SCHEMA_VERSION, utc_timestamp()),
            )
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.executescript(dossier_schema)
            self.ensure_column(conn, "learning_card_usage", "leader_code TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_card_usage", "leader_name TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_card_usage", "role_classification TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_card_usage", "confidence_label TEXT NOT NULL DEFAULT 'no_evidence'")
            self.ensure_column(conn, "learning_card_usage", "fact_status TEXT NOT NULL DEFAULT 'tentative'")
            self.ensure_column(conn, "learning_card_usage", "source_id TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_card_usage", "source_reference TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_card_usage", "source_url TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_card_usage", "freshness_at TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_card_usage", "provenance_json TEXT NOT NULL DEFAULT '{}'")
            try:
                conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_card_usage_identity
                        ON learning_card_usage(card_code, leader_code, archetype_key, role_classification)
                    """
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_card_usage_card
                        ON learning_card_usage(card_code, usage_frequency DESC, sample_size DESC)
                    """
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_usage_evidence_card
                        ON learning_usage_evidence(card_code, observed_at DESC, source_id)
                    """
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_usage_evidence_event
                        ON learning_usage_evidence(event_key, decklist_key, card_code)
                    """
                )
            except sqlite3.OperationalError:
                pass
            self.ensure_column(conn, "learning_dossier_images", "print_id TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "print_label TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "variant_label TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "source_type TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "bytes_size INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "learning_dossier_images", "mime_type TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "source_trust_tier INTEGER NOT NULL DEFAULT 4")
            self.ensure_column(conn, "learning_dossier_images", "source_trust_label TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "quality_tier TEXT NOT NULL DEFAULT 'fallback_lowres'")
            self.ensure_column(conn, "learning_dossier_images", "sample_flag INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "learning_dossier_images", "image_score REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "print_match_confidence REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "quality_score REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "trust_score REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "selection_confidence REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "card_code_match_confidence REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "variant_match_confidence REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "art_family_confidence REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "clarity_score REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "crop_confidence REAL NOT NULL DEFAULT 0.0")
            self.ensure_column(conn, "learning_dossier_images", "selection_scope TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "selection_reason TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "content_status TEXT NOT NULL DEFAULT 'candidate'")
            self.ensure_column(conn, "learning_dossier_images", "duplicate_group TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "perceptual_hash TEXT NOT NULL DEFAULT ''")
            self.ensure_column(conn, "learning_dossier_images", "origin_language TEXT NOT NULL DEFAULT 'en'")
            self.ensure_column(conn, "learning_dossier_images", "english_print_exists INTEGER NOT NULL DEFAULT 1")
            self.ensure_column(conn, "learning_dossier_images", "display_policy TEXT NOT NULL DEFAULT 'english-first'")
            self.ensure_column(conn, "learning_dossier_images", "provisional_language_display INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "learning_dossier_images", "review_notes_json TEXT NOT NULL DEFAULT '[]'")
            self.ensure_column(conn, "learning_dossier_images", "citation_payload_json TEXT NOT NULL DEFAULT '{}'")
            self.ensure_column(conn, "learning_dossier_images", "score_breakdown_json TEXT NOT NULL DEFAULT '{}'")
            self.ensure_column(conn, "learning_dossier_images", "replacement_eligible INTEGER NOT NULL DEFAULT 1")
            self.ensure_column(conn, "learning_dossier_images", "upgrade_status TEXT NOT NULL DEFAULT 'review-pending'")
            self.ensure_column(conn, "learning_dossier_images", "is_current_best INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "learning_dossier_images", "superseded_by_image_id INTEGER NOT NULL DEFAULT 0")
            self.ensure_column(conn, "learning_dossier_images", "last_reviewed_at TEXT NOT NULL DEFAULT ''")
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_best
                        ON learning_dossier_images(card_code, variant_key, is_current_best, image_score DESC)
                    """
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_learning_dossier_images_print
                        ON learning_dossier_images(card_code, print_id, variant_key)
                    """
                )
            except sqlite3.OperationalError:
                pass
            self.ensure_column(conn, "learning_image_selections", "origin_language TEXT NOT NULL DEFAULT 'en'")
            self.ensure_column(conn, "learning_image_selections", "english_print_exists INTEGER NOT NULL DEFAULT 1")
            self.ensure_column(conn, "learning_image_selections", "display_policy TEXT NOT NULL DEFAULT 'english-first'")
            self.ensure_column(conn, "learning_image_selections", "provisional_language_display INTEGER NOT NULL DEFAULT 0")
        if int(self.verified_dossier_store.inspect_summary().get("dossiers_created") or 0) == 0:
            self.backfill_verified_dossier_store()
        self.backfill_image_intelligence_phase1()
        self.normalize_historical_failure_backlog()

    def acquire_configured_lock(self) -> bool:
        if self.lock_file_path is None:
            return True
        if self._instance_lock is not None:
            return True
        lock_info = inspect_single_instance_lock(self.lock_file_path)
        self._instance_lock = acquire_single_instance_lock(self.lock_file_path)
        if self._instance_lock is not None:
            self._lock_issue_message = ""
            return True
        if lock_info.get("exists") and lock_info.get("is_running"):
            pid = int(lock_info.get("pid") or 0)
            self._lock_issue_message = (
                f"Another Miru learning worker is already running"
                + (f" (PID {pid})" if pid else "")
                + "."
            )
            return False
        self._lock_issue_message = (
            f"Miru could not acquire the worker lock at {self.lock_file_path}. "
            "Check file permissions or remove a stale lock if needed."
        )
        return False

    def release_configured_lock(self) -> None:
        if self._instance_lock is None:
            return
        self._instance_lock.close()
        self._instance_lock = None

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

    @staticmethod
    def _variant_tokens(value: str) -> set[str]:
        normalized = normalize_variant_key(value)
        tokens = {item for item in re.split(r"[-_]+", normalized) if item}
        if not tokens and normalized:
            tokens.add(normalized)
        return tokens

    def classify_print_profile(
        self,
        *,
        variant_key: str = "",
        variant_label: str = "",
        print_label: str = "",
        card_code: str = "",
    ) -> dict[str, Any]:
        token_source = " ".join(
            value
            for value in (
                normalize_variant_key(variant_key),
                normalize_variant_key(variant_label),
                normalize_variant_key(print_label),
            )
            if value
        )
        tokens = self._variant_tokens(token_source)
        alt_style = bool(tokens & ALT_ART_SIGNAL_TOKENS)
        parallel = bool(tokens & PARALLEL_SIGNAL_TOKENS)
        promo = bool(tokens & PROMO_SIGNAL_TOKENS) or str(card_code or "").strip().upper().startswith("P-")
        reprint = bool(tokens & REPRINT_SIGNAL_TOKENS)
        art_style = "alt" if alt_style else "base"
        finish_style = "parallel" if parallel else "standard"
        if promo:
            release_class = "promo"
        elif reprint:
            release_class = "reprint"
        else:
            release_class = "standard"
        return {
            "tokens": sorted(tokens),
            "art_style": art_style,
            "finish_style": finish_style,
            "release_class": release_class,
            "is_alt_art": alt_style,
            "is_parallel": parallel,
            "is_promo": promo,
            "is_reprint": reprint,
        }

    @staticmethod
    def normalize_language_code(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"", "en", "eng", "english"}:
            return "en"
        if text in {"jp", "ja", "jpn", "japanese"}:
            return "ja"
        if text in {"kr", "ko", "kor", "korean"}:
            return "ko"
        return text[:8] or "en"

    def resolve_image_language_policy(
        self,
        *,
        source_id: str,
        source_url: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(metadata or {})
        explicit_language = (
            payload.get("origin_language")
            or payload.get("language")
            or payload.get("locale")
            or payload.get("source_language")
            or ""
        )
        origin_language = self.normalize_language_code(explicit_language)
        if not explicit_language:
            url_lower = str(source_url or "").lower()
            source_lower = str(source_id or "").lower()
            if any(marker in url_lower for marker in ("/jp/", ".jp/", "language=jp", "locale=jp", "lang=jp")):
                origin_language = "ja"
            elif "official-card-images" in source_lower:
                origin_language = "en"
            elif "trusted-scan-images" in source_lower:
                origin_language = "en"

        explicit_english_exists = payload.get("english_print_exists")
        if isinstance(explicit_english_exists, str):
            english_print_exists = explicit_english_exists.strip().lower() not in {"0", "false", "no", ""}
        elif explicit_english_exists is None:
            english_print_exists = True if origin_language != "en" else True
        else:
            english_print_exists = bool(explicit_english_exists)

        provisional_language_display = origin_language != "en" and not english_print_exists
        if provisional_language_display:
            display_policy = "translated-origin-provisional"
        elif origin_language == "en":
            display_policy = "english-first"
        else:
            display_policy = "english-first-non-english-deferred"

        return {
            "origin_language": origin_language,
            "english_print_exists": bool(english_print_exists),
            "display_policy": display_policy,
            "provisional_language_display": bool(provisional_language_display),
        }

    def derive_duplicate_identity(
        self,
        *,
        card_code: str,
        print_identity: dict[str, Any],
        image_hash: str = "",
        source_reference: str = "",
        source_url: str = "",
        width: int = 0,
        height: int = 0,
        origin_language: str = "en",
    ) -> dict[str, str]:
        width_bucket = int(max(int(width or 0), 0) / 120)
        height_bucket = int(max(int(height or 0), 0) / 160)
        if image_hash:
            perceptual_hash = f"img:{str(image_hash)[:16]}"
        else:
            reference_basis = normalize_variant_key(Path(str(source_url or source_reference or "")).stem)
            perceptual_hash = (
                f"family:{print_identity['art_family_key']}:{origin_language}:{reference_basis}:{width_bucket}x{height_bucket}"
            )[:96]
        duplicate_group = f"family:{print_identity['art_family_key']}:{origin_language}"
        if image_hash:
            duplicate_group = f"{duplicate_group}:{str(image_hash)[:8]}"
        return {
            "perceptual_hash": perceptual_hash,
            "duplicate_group": duplicate_group[:120],
            "family_group": f"family:{print_identity['art_family_key']}:{origin_language}",
        }

    @staticmethod
    def normalize_duplicate_family_group(
        duplicate_group: Any,
        *,
        perceptual_hash: Any = "",
    ) -> str:
        group = str(duplicate_group or "").strip()
        if not group:
            return ""
        resolved_hash = str(perceptual_hash or "").strip().lower()
        if resolved_hash.startswith("img:") and ":" in group:
            return group.rsplit(":", 1)[0]
        return group

    @staticmethod
    def _compact_signal_list(signals: list[str], *, limit: int = 5) -> list[str]:
        compact: list[str] = []
        seen: set[str] = set()
        for raw_signal in signals:
            signal = str(raw_signal or "").strip()
            if not signal or signal in seen:
                continue
            compact.append(signal)
            seen.add(signal)
            if len(compact) >= limit:
                break
        return compact

    @staticmethod
    def _resolve_candidate_family_group(candidate: dict[str, Any]) -> tuple[str, str]:
        """Return (family_group, signal_name).

        Tries duplicate_group normalization first.  Falls back to print_id-derived
        family group when duplicate_group is absent so that candidates stored before
        duplicate identity was populated are still grouped with their art family.

        signal_name values:
        'duplicate_group' | 'print_id_fallback' | 'print_profile_fallback' | 'none'
        """
        perceptual_hash = str(candidate.get("perceptual_hash") or "").strip()
        duplicate_group = str(candidate.get("duplicate_group") or "").strip()
        group = MiruLearningEngine.normalize_duplicate_family_group(
            duplicate_group, perceptual_hash=perceptual_hash
        )
        if group:
            return group, "duplicate_group"

        print_id = str(candidate.get("print_id") or "").strip()
        card_code = str(candidate.get("card_code") or "").strip().upper()
        # Normalize language via the engine helper so fallback key format exactly matches
        # the key produced by derive_duplicate_identity (which also normalizes at store time).
        origin_language = MiruLearningEngine.normalize_language_code(
            candidate.get("origin_language") or ""
        )
        if print_id and card_code and "::" in print_id:
            print_group = print_id.split("::", 1)[1]
            # Use the same token set as classify_print_profile so that alt-art variants
            # such as "sp", "manga", "signed", "illustration" are correctly classified.
            group_tokens = frozenset(
                t for t in normalize_variant_key(print_group).replace("-", " ").split() if t
            )
            art_style = "alt" if group_tokens & ALT_ART_SIGNAL_TOKENS else "base"
            fallback_group = f"family:{card_code}::art::{art_style}:{origin_language}"
            return fallback_group, "print_id_fallback"

        token_source = " ".join(
            value
            for value in (
                normalize_variant_key(candidate.get("variant_key") or ""),
                normalize_variant_key(candidate.get("variant_label") or ""),
                normalize_variant_key(candidate.get("print_label") or ""),
            )
            if value
        ).strip()
        if card_code and token_source:
            profile_tokens = frozenset(
                token
                for token in re.split(r"[\s\-_]+", token_source)
                if token
            )
            art_style = "alt" if profile_tokens & ALT_ART_SIGNAL_TOKENS else "base"
            fallback_group = f"family:{card_code}::art::{art_style}:{origin_language}"
            return fallback_group, "print_profile_fallback"

        return "", "none"

    @staticmethod
    def evaluate_visual_similarity(
        candidate_a: dict[str, Any],
        candidate_b: dict[str, Any],
    ) -> dict[str, Any]:
        support_score = 0
        conflict_score = 0
        weak_score = 0
        detail_signals: list[str] = []

        hash_a = str(candidate_a.get("perceptual_hash") or "").strip().lower()
        hash_b = str(candidate_b.get("perceptual_hash") or "").strip().lower()
        visual_hash_a = hash_a[4:] if hash_a.startswith("img:") else ""
        visual_hash_b = hash_b[4:] if hash_b.startswith("img:") else ""
        if visual_hash_a and visual_hash_b:
            common_prefix = len(os.path.commonprefix([visual_hash_a, visual_hash_b]))
            if visual_hash_a == visual_hash_b:
                support_score += 3
                detail_signals.append("visual_hash_exact")
            elif common_prefix >= 8:
                support_score += 2
                detail_signals.append("visual_hash_prefix")
            elif common_prefix <= 2:
                conflict_score += 1
                detail_signals.append("visual_hash_divergent")
            else:
                weak_score += 1
                detail_signals.append("visual_hash_weak")

        width_a = max(int(candidate_a.get("width") or 0), 0)
        height_a = max(int(candidate_a.get("height") or 0), 0)
        width_b = max(int(candidate_b.get("width") or 0), 0)
        height_b = max(int(candidate_b.get("height") or 0), 0)
        if width_a > 0 and height_a > 0 and width_b > 0 and height_b > 0:
            aspect_a = float(width_a) / float(max(height_a, 1))
            aspect_b = float(width_b) / float(max(height_b, 1))
            aspect_delta = abs(aspect_a - aspect_b)
            if aspect_delta <= 0.035:
                support_score += 1
                detail_signals.append("visual_aspect_match")
            elif aspect_delta >= 0.18:
                conflict_score += 1
                detail_signals.append("visual_aspect_conflict")
            else:
                weak_score += 1
                detail_signals.append("visual_aspect_weak")

            area_a = width_a * height_a
            area_b = width_b * height_b
            if area_a > 0 and area_b > 0:
                size_ratio = float(min(area_a, area_b)) / float(max(area_a, area_b))
                if size_ratio >= 0.72:
                    support_score += 1
                    detail_signals.append("visual_resolution_close")
                elif size_ratio <= 0.22:
                    weak_score += 1
                    detail_signals.append("visual_resolution_far")

        crop_a = float(candidate_a.get("crop_confidence") or 0.0)
        crop_b = float(candidate_b.get("crop_confidence") or 0.0)
        if crop_a > 0.0 and crop_b > 0.0:
            crop_delta = abs(crop_a - crop_b)
            if crop_delta <= 0.1:
                support_score += 1
                detail_signals.append("visual_crop_match")
            elif crop_delta >= 0.4:
                conflict_score += 1
                detail_signals.append("visual_crop_conflict")
            else:
                weak_score += 1
                detail_signals.append("visual_crop_weak")

        available = bool(detail_signals)
        primary_signal = ""
        summary = "No lightweight visual evidence was available."
        if available:
            if support_score >= 2 and conflict_score == 0:
                primary_signal = "visual_similarity_support"
                summary = "Lightweight visual checks reinforce the existing family grouping."
            elif conflict_score >= 2 and support_score == 0:
                primary_signal = "visual_similarity_conflict"
                summary = "Lightweight visual checks diverge from the metadata grouping, so classification should stay cautious."
            else:
                primary_signal = "visual_similarity_weak"
                summary = "Lightweight visual checks were mixed or inconclusive, so metadata remains primary."

        return {
            "available": available,
            "primary_signal": primary_signal,
            "detail_signals": MiruLearningEngine._compact_signal_list(detail_signals, limit=3),
            "support_score": int(support_score),
            "conflict_score": int(conflict_score),
            "weak_score": int(weak_score),
            "summary": summary,
        }

    def _summarize_visual_similarity(
        self,
        candidate: dict[str, Any],
        peers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evaluations = [
            self.evaluate_visual_similarity(candidate, peer)
            for peer in peers
            if int(peer.get("id") or 0) != int(candidate.get("id") or 0)
        ]
        available = [item for item in evaluations if bool(item.get("available"))]
        if not available:
            return {
                "available": False,
                "primary_signal": "",
                "classification_signals": [],
                "summary": "",
            }

        has_support = any(int(item.get("support_score") or 0) > 0 for item in available)
        has_conflict = any(int(item.get("conflict_score") or 0) > 0 for item in available)
        best = max(
            available,
            key=lambda item: (
                3 if item.get("primary_signal") == "visual_similarity_support" else (
                    2 if item.get("primary_signal") == "visual_similarity_conflict" else 1
                ),
                int(item.get("support_score") or 0),
                -int(item.get("conflict_score") or 0),
            ),
        )

        if has_support and not has_conflict:
            primary_signal = "visual_similarity_support"
            summary = "Lightweight visual checks reinforce the metadata-based family grouping."
        elif has_conflict and not has_support:
            primary_signal = "visual_similarity_conflict"
            summary = "Lightweight visual checks diverge from the metadata grouping, so Miru keeps the classification conservative."
        else:
            primary_signal = "visual_similarity_weak"
            summary = "Lightweight visual checks were mixed across nearby candidates, so metadata remains primary and Miru keeps the classification conservative."

        if has_conflict:
            classification_signals = ["visual_conflict", primary_signal]
        else:
            classification_signals = [primary_signal]
        for signal in list(best.get("detail_signals") or []):
            classification_signals.append(signal)

        return {
            "available": True,
            "primary_signal": primary_signal,
            "classification_signals": self._compact_signal_list(classification_signals),
            "summary": summary,
        }

    def analyze_duplicate_family(
        self,
        candidates: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        candidate_lookup: dict[int, dict[str, Any]] = {}
        exact_groups: dict[str, list[int]] = {}
        family_groups: dict[str, list[int]] = {}
        candidate_signals: dict[int, str] = {}
        for item in candidates:
            candidate_id = int(item.get("id") or 0)
            if candidate_id <= 0:
                continue
            candidate_lookup[candidate_id] = item
            perceptual_hash = str(item.get("perceptual_hash") or "").strip()
            family_group, group_signal = self._resolve_candidate_family_group(item)
            candidate_signals[candidate_id] = group_signal
            if perceptual_hash:
                exact_groups.setdefault(perceptual_hash, []).append(candidate_id)
            if family_group:
                family_groups.setdefault(family_group, []).append(candidate_id)
        results: dict[int, dict[str, Any]] = {}
        for item in candidates:
            candidate_id = int(item.get("id") or 0)
            perceptual_hash = str(item.get("perceptual_hash") or "").strip()
            family_group, _ = self._resolve_candidate_family_group(item)
            exact_count = len(exact_groups.get(perceptual_hash, [])) if perceptual_hash else 0
            family_members = [
                candidate_lookup[member_id]
                for member_id in family_groups.get(family_group, [])
                if member_id in candidate_lookup
            ]
            family_count = len(family_members)
            print_ids = {
                str(member.get("print_id") or "")
                for member in family_members
                if str(member.get("print_id") or "").strip()
            }
            variant_keys = {
                normalize_variant_key(member.get("variant_key") or "")
                for member in family_members
            }
            relationship = "unique"
            family_reasoning = "No duplicate-family overlap was detected."
            classification_signals: list[str] = []
            group_signal = candidate_signals.get(candidate_id, "none")
            if group_signal == "print_id_fallback":
                classification_signals.append("print_id_fallback_grouping")
            elif group_signal == "print_profile_fallback":
                classification_signals.append("print_profile_fallback_grouping")
            stored_afc = float(item.get("art_family_confidence") or 0.0)
            if stored_afc >= 0.82:
                classification_signals.append("stored_art_family_confidence_high")
            if exact_count > 1:
                relationship = "exact-duplicate"
                family_reasoning = "Multiple candidates appear to resolve to the same underlying image hash."
                classification_signals.append("exact_hash_match")
            elif family_count > 1:
                classification_signals.append("shared_family_group")
                if len(print_ids) > 1 or len(variant_keys) > 1:
                    relationship = "same-art-different-crop-or-treatment"
                    family_reasoning = "Candidates share an art family but diverge by print treatment, crop, or variant identity."
                    if group_signal == "print_profile_fallback":
                        family_reasoning = (
                            "Candidates share an art family via print-profile fallback, but diverge by print treatment, crop, or variant identity."
                        )
                    if len(print_ids) > 1:
                        classification_signals.append("print_id_divergence")
                    if len(variant_keys) > 1:
                        classification_signals.append("variant_key_divergence")
                else:
                    relationship = "same-family-cautious"
                    family_reasoning = (
                        "Candidates cluster into the same family, but evidence is not strong enough to collapse them fully."
                    )
                    if group_signal == "print_profile_fallback":
                        family_reasoning = (
                            "Candidates cluster into the same family via print-profile fallback, but evidence is not strong enough to collapse them fully."
                        )
                    classification_signals.append("single_print_cautious")
            elif group_signal == "none" and stored_afc < 0.55:
                classification_signals.append("weak_family_evidence")

            visual_peers = []
            if exact_count > 1:
                visual_peers = [
                    candidate_lookup[member_id]
                    for member_id in exact_groups.get(perceptual_hash, [])
                    if member_id in candidate_lookup and member_id != candidate_id
                ]
            elif family_count > 1:
                visual_peers = [member for member in family_members if int(member.get("id") or 0) != candidate_id]
            visual_summary = self._summarize_visual_similarity(item, visual_peers)
            if visual_summary.get("available") and relationship != "unique":
                classification_signals = list(visual_summary.get("classification_signals") or []) + classification_signals
                family_reasoning = (
                    f"{family_reasoning} {str(visual_summary.get('summary') or '').strip()}".strip()
                )

            results[candidate_id] = {
                "duplicate_relationship": relationship,
                "exact_duplicate_count": exact_count,
                "family_duplicate_count": family_count,
                "family_group": family_group,
                "family_print_count": len(print_ids),
                "family_variant_count": len(variant_keys),
                "family_reasoning": family_reasoning,
                "classification_signals": self._compact_signal_list(classification_signals, limit=6),
            }
        return results

    def inspect_duplicate_relationship(
        self,
        current_candidate: dict[str, Any],
        winning_candidate: dict[str, Any],
    ) -> dict[str, Any]:
        pair_candidates = [dict(current_candidate or {}), dict(winning_candidate or {})]
        for index, item in enumerate(pair_candidates, start=1):
            item["id"] = index
        analysis = self.analyze_duplicate_family(pair_candidates)
        detail = dict(analysis.get(1, {}))
        relationship = str(detail.get("duplicate_relationship") or "unique")
        if relationship == "unique":
            detail["duplicate_relationship"] = "distinct-family"
        detail.setdefault("classification_signals", [])
        detail.setdefault("family_reasoning", "No duplicate-family overlap was detected.")
        return detail

    def compare_duplicate_relationship(
        self,
        current_candidate: dict[str, Any],
        winning_candidate: dict[str, Any],
    ) -> str:
        detail = self.inspect_duplicate_relationship(current_candidate, winning_candidate)
        return str(detail.get("duplicate_relationship") or "distinct-family")

    @staticmethod
    def compare_print_profiles(
        *,
        target_profile: dict[str, Any],
        candidate_profile: dict[str, Any],
        target_variant_key: str = "",
        candidate_variant_key: str = "",
    ) -> dict[str, Any]:
        normalized_target = normalize_variant_key(target_variant_key)
        normalized_candidate = normalize_variant_key(candidate_variant_key)
        exact_variant_match = bool(normalized_target == normalized_candidate and normalized_target)
        both_base = not normalized_target and not normalized_candidate

        if exact_variant_match:
            variant_match_confidence = 1.0
        elif both_base:
            variant_match_confidence = 0.98
        elif not normalized_target:
            if target_profile.get("art_style") == candidate_profile.get("art_style") == "base":
                variant_match_confidence = 0.68
            elif candidate_profile.get("art_style") == "alt":
                variant_match_confidence = 0.22
            else:
                variant_match_confidence = 0.48
        elif not normalized_candidate:
            variant_match_confidence = 0.3 if target_profile.get("art_style") == "alt" else 0.62
        elif target_profile.get("art_style") == candidate_profile.get("art_style"):
            variant_match_confidence = 0.7
        else:
            variant_match_confidence = 0.24

        if target_profile.get("art_style") == candidate_profile.get("art_style"):
            art_family_confidence = 0.95 if target_profile.get("art_style") == "base" else 0.9
        elif (
            target_profile.get("finish_style") == "parallel"
            and candidate_profile.get("art_style") == "base"
            or candidate_profile.get("finish_style") == "parallel"
            and target_profile.get("art_style") == "base"
        ):
            art_family_confidence = 0.72
        else:
            art_family_confidence = 0.22

        treatment_matches = 0
        treatment_checks = 0
        mismatch_flags: list[str] = []
        for key in ("finish_style", "release_class"):
            treatment_checks += 1
            if str(target_profile.get(key) or "") == str(candidate_profile.get(key) or ""):
                treatment_matches += 1
            else:
                mismatch_flags.append(key)
        treatment_confidence = treatment_matches / float(max(treatment_checks, 1))

        if exact_variant_match:
            print_match_confidence = 1.0
        else:
            print_match_confidence = round(
                max(
                    0.12,
                    min(
                        0.98,
                        (variant_match_confidence * 0.5)
                        + (art_family_confidence * 0.3)
                        + (treatment_confidence * 0.2),
                    ),
                ),
                2,
            )

        if exact_variant_match:
            relationship = "exact-print"
        elif art_family_confidence >= 0.82 and treatment_confidence < 1.0:
            relationship = "same-art-different-treatment"
        elif art_family_confidence >= 0.82:
            relationship = "same-art-family"
        elif variant_match_confidence >= 0.6:
            relationship = "likely-related-print"
        else:
            relationship = "uncertain-print-link"

        return {
            "variant_match_confidence": round(variant_match_confidence, 2),
            "art_family_confidence": round(art_family_confidence, 2),
            "print_match_confidence": print_match_confidence,
            "treatment_confidence": round(treatment_confidence, 2),
            "relationship": relationship,
            "mismatch_flags": mismatch_flags,
        }

    def build_print_identity(
        self,
        *,
        card_code: str,
        variant_key: str = "",
        variant_label: str = "",
        print_label: str = "",
        release_set_code: str = "",
        release_set_name: str = "",
    ) -> dict[str, Any]:
        normalized = normalize_card_code(card_code)
        canonical_code = (normalized["canonical_code"] or card_code or "").strip().upper()
        resolved_variant = normalize_variant_key(variant_key)
        resolved_variant_label = clean_display_text(variant_label or humanize_variant_label(resolved_variant))
        resolved_print_label = clean_display_text(print_label or resolved_variant_label or "Base")
        print_profile = self.classify_print_profile(
            variant_key=resolved_variant,
            variant_label=resolved_variant_label,
            print_label=resolved_print_label,
            card_code=canonical_code,
        )

        is_base = not resolved_variant
        is_alt_art = bool(print_profile["is_alt_art"])
        is_parallel = bool(print_profile["is_parallel"]) and not is_base
        is_promo = bool(print_profile["is_promo"])
        is_reprint = bool(print_profile["is_reprint"])
        art_family_label = str(print_profile["art_style"] or "base")
        if is_parallel and art_family_label == "base":
            print_group = f"parallel-{art_family_label}"
        else:
            print_group = resolved_variant or "base"
        parent_print_id = f"{canonical_code}::base"
        if is_base:
            parent_print_id = canonical_code
        elif is_parallel and art_family_label == "alt":
            parent_print_id = f"{canonical_code}::alt"
        elif is_parallel:
            parent_print_id = f"{canonical_code}::base"

        return {
            "card_code": canonical_code,
            "variant_key": resolved_variant,
            "variant_label": resolved_variant_label or "Base",
            "print_label": resolved_print_label or resolved_variant_label or "Base",
            "print_group": print_group,
            "print_id": f"{canonical_code}::{print_group}",
            "art_family_key": f"{canonical_code}::art::{art_family_label}",
            "parent_print_id": parent_print_id,
            "release_set_code": str(release_set_code or normalized.get("set_code") or "").strip().upper(),
            "release_set_name": clean_display_text(release_set_name),
            "is_base": is_base,
            "is_alt_art": is_alt_art,
            "is_parallel": is_parallel,
            "is_promo": is_promo,
            "is_reprint": is_reprint,
            "illustration_type": "Alt Art" if is_alt_art else ("Base Artwork" if art_family_label == "base" else ""),
            "print_profile": print_profile,
        }

    @staticmethod
    def _selection_confidence_from_row(row: dict[str, Any]) -> float:
        image_score = float(row.get("image_score") or 0.0)
        print_match = float(row.get("print_match_confidence") or row.get("variant_match_confidence") or 0.0)
        verified_bonus = 0.08 if str(row.get("verification_state") or "").strip().lower() == "verified" else 0.0
        confidence = max(0.18, min(0.98, (image_score / 240.0) + (print_match * 0.18) + verified_bonus))
        return round(confidence, 2)

    def upsert_learning_print(
        self,
        *,
        print_identity: dict[str, Any],
        verification_state: str = "scaffolded",
        match_confidence: float = 0.0,
        supporting_sources: list[dict[str, Any]] | None = None,
        citation_payload: dict[str, Any] | None = None,
        verified_at: str = "",
    ) -> None:
        now = utc_timestamp()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_dossier_prints (
                    card_code,
                    print_id,
                    variant_key,
                    variant_label,
                    print_label,
                    print_group,
                    art_family_key,
                    parent_print_id,
                    release_set_code,
                    release_set_name,
                    is_base,
                    is_alt_art,
                    is_parallel,
                    is_promo,
                    is_reprint,
                    illustration_type,
                    verification_state,
                    match_confidence,
                    supporting_sources_json,
                    citation_payload_json,
                    updated_at,
                    verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, print_id) DO UPDATE SET
                    variant_key = excluded.variant_key,
                    variant_label = excluded.variant_label,
                    print_label = excluded.print_label,
                    print_group = excluded.print_group,
                    art_family_key = excluded.art_family_key,
                    parent_print_id = excluded.parent_print_id,
                    release_set_code = excluded.release_set_code,
                    release_set_name = excluded.release_set_name,
                    is_base = excluded.is_base,
                    is_alt_art = excluded.is_alt_art,
                    is_parallel = excluded.is_parallel,
                    is_promo = excluded.is_promo,
                    is_reprint = excluded.is_reprint,
                    illustration_type = excluded.illustration_type,
                    verification_state = excluded.verification_state,
                    match_confidence = excluded.match_confidence,
                    supporting_sources_json = excluded.supporting_sources_json,
                    citation_payload_json = excluded.citation_payload_json,
                    updated_at = excluded.updated_at,
                    verified_at = excluded.verified_at
                """,
                (
                    print_identity["card_code"],
                    print_identity["print_id"],
                    print_identity["variant_key"],
                    print_identity["variant_label"],
                    print_identity["print_label"],
                    print_identity["print_group"],
                    print_identity["art_family_key"],
                    print_identity["parent_print_id"],
                    print_identity["release_set_code"],
                    print_identity["release_set_name"],
                    1 if print_identity["is_base"] else 0,
                    1 if print_identity["is_alt_art"] else 0,
                    1 if print_identity["is_parallel"] else 0,
                    1 if print_identity["is_promo"] else 0,
                    1 if print_identity["is_reprint"] else 0,
                    print_identity["illustration_type"],
                    verification_state,
                    float(match_confidence),
                    json.dumps(list(supporting_sources or []), ensure_ascii=False),
                    json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True),
                    now,
                    verified_at,
                ),
            )

    def update_image_candidate_scaffold(
        self,
        *,
        candidate_id: int,
        print_identity: dict[str, Any],
        source_type: str = "",
        selection_scope: str = "",
        selection_reason: str = "",
        selection_confidence: float = 0.0,
        print_match_confidence: float = 0.0,
        variant_match_confidence: float = 0.0,
        art_family_confidence: float = 0.0,
        quality_score: float = 0.0,
        trust_score: float = 0.0,
        content_status: str = "candidate",
        duplicate_group: str = "",
        perceptual_hash: str = "",
        origin_language: str = "",
        english_print_exists: bool | None = None,
        display_policy: str = "",
        provisional_language_display: bool = False,
        citation_payload: dict[str, Any] | None = None,
        score_breakdown: dict[str, Any] | None = None,
    ) -> None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                UPDATE learning_dossier_images
                SET print_id = ?,
                    print_label = ?,
                    variant_label = ?,
                    source_type = ?,
                    selection_scope = ?,
                    selection_reason = ?,
                    selection_confidence = ?,
                    print_match_confidence = ?,
                    variant_match_confidence = ?,
                    art_family_confidence = ?,
                    quality_score = ?,
                    trust_score = ?,
                    content_status = ?,
                    duplicate_group = ?,
                    perceptual_hash = ?,
                    origin_language = ?,
                    english_print_exists = ?,
                    display_policy = ?,
                    provisional_language_display = ?,
                    citation_payload_json = ?,
                    score_breakdown_json = ?
                WHERE id = ?
                """,
                (
                    print_identity["print_id"],
                    print_identity["print_label"],
                    print_identity["variant_label"],
                    str(source_type or ""),
                    str(selection_scope or ""),
                    str(selection_reason or ""),
                    float(selection_confidence),
                    float(print_match_confidence),
                    float(variant_match_confidence),
                    float(art_family_confidence),
                    float(quality_score),
                    float(trust_score),
                    str(content_status or "candidate"),
                    str(duplicate_group or ""),
                    str(perceptual_hash or ""),
                    self.normalize_language_code(origin_language),
                    1 if english_print_exists else 0,
                    str(display_policy or "english-first"),
                    1 if provisional_language_display else 0,
                    json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(score_breakdown or {}), ensure_ascii=False, sort_keys=True),
                    int(candidate_id),
                ),
            )

    def upsert_image_selection(
        self,
        *,
        card_code: str,
        print_id: str,
        variant_key: str,
        selection_scope: str,
        image_candidate_id: int,
        best_image_flag: bool,
        upgrade_eligible: bool,
        selection_confidence: float,
        quality_tier: str,
        trust_tier: int,
        selection_reason: str,
        comparison_summary: dict[str, Any] | None = None,
        citation_payload: dict[str, Any] | None = None,
        origin_language: str = "en",
        english_print_exists: bool = True,
        display_policy: str = "english-first",
        provisional_language_display: bool = False,
        locked_by_operator: bool = False,
    ) -> None:
        now = utc_timestamp()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_image_selections (
                    card_code,
                    print_id,
                    variant_key,
                    selection_scope,
                    image_candidate_id,
                    best_image_flag,
                    upgrade_eligible,
                    selection_confidence,
                    quality_tier,
                    trust_tier,
                    selection_reason,
                    comparison_summary_json,
                    citation_payload_json,
                    origin_language,
                    english_print_exists,
                    display_policy,
                    provisional_language_display,
                    locked_by_operator,
                    selected_at,
                    reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, print_id, selection_scope) DO UPDATE SET
                    variant_key = excluded.variant_key,
                    image_candidate_id = excluded.image_candidate_id,
                    best_image_flag = excluded.best_image_flag,
                    upgrade_eligible = excluded.upgrade_eligible,
                    selection_confidence = excluded.selection_confidence,
                    quality_tier = excluded.quality_tier,
                    trust_tier = excluded.trust_tier,
                    selection_reason = excluded.selection_reason,
                    comparison_summary_json = excluded.comparison_summary_json,
                    citation_payload_json = excluded.citation_payload_json,
                    origin_language = excluded.origin_language,
                    english_print_exists = excluded.english_print_exists,
                    display_policy = excluded.display_policy,
                    provisional_language_display = excluded.provisional_language_display,
                    selected_at = excluded.selected_at,
                    reviewed_at = excluded.reviewed_at
                """,
                (
                    card_code,
                    print_id,
                    normalize_variant_key(variant_key),
                    selection_scope,
                    int(image_candidate_id),
                    1 if best_image_flag else 0,
                    1 if upgrade_eligible else 0,
                    float(selection_confidence),
                    quality_tier,
                    int(trust_tier),
                    selection_reason,
                    json.dumps(dict(comparison_summary or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True),
                    self.normalize_language_code(origin_language),
                    1 if english_print_exists else 0,
                    str(display_policy or "english-first"),
                    1 if provisional_language_display else 0,
                    1 if locked_by_operator else 0,
                    now,
                    now,
                ),
            )

    def fetch_image_candidates(
        self,
        *,
        card_code: str,
        variant_key: str = "",
        include_other_variants: bool = False,
        include_rejected: bool = False,
    ) -> list[dict[str, Any]]:
        normalized_variant = normalize_variant_key(variant_key)
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            if include_other_variants:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM learning_dossier_images
                    WHERE card_code = ?
                    ORDER BY image_score DESC, last_reviewed_at DESC, downloaded_at DESC, id DESC
                    """,
                    (card_code,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM learning_dossier_images
                    WHERE card_code = ?
                      AND variant_key = ?
                    ORDER BY image_score DESC, last_reviewed_at DESC, downloaded_at DESC, id DESC
                    """,
                    (card_code, normalized_variant),
                ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["review_notes"] = json.loads(item.get("review_notes_json") or "[]")
            item["citation_payload"] = json.loads(item.get("citation_payload_json") or "{}")
            item["score_breakdown"] = json.loads(item.get("score_breakdown_json") or "{}")
            if not include_rejected and str(item.get("quality_tier") or "") == "rejected":
                continue
            results.append(item)
        return results

    def score_candidate_for_scope(
        self,
        *,
        candidate: dict[str, Any],
        selection_scope: str,
        target_variant_key: str = "",
        duplicate_info: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target_variant = normalize_variant_key(target_variant_key)
        candidate_variant = normalize_variant_key(str(candidate.get("variant_key") or ""))
        candidate_print = self.build_print_identity(
            card_code=str(candidate.get("card_code") or ""),
            variant_key=candidate_variant,
            variant_label=str(candidate.get("variant_label") or ""),
            print_label=str(candidate.get("print_label") or ""),
        )
        target_print = self.build_print_identity(card_code=str(candidate.get("card_code") or ""), variant_key=target_variant)
        print_comparison = self.compare_print_profiles(
            target_profile=dict(target_print.get("print_profile") or {}),
            candidate_profile=dict(candidate_print.get("print_profile") or {}),
            target_variant_key=target_variant,
            candidate_variant_key=candidate_variant,
        )
        print_match = float(candidate.get("print_match_confidence") or print_comparison["print_match_confidence"])
        variant_match = float(candidate.get("variant_match_confidence") or print_comparison["variant_match_confidence"])
        art_family = float(candidate.get("art_family_confidence") or print_comparison["art_family_confidence"])
        card_match = float(candidate.get("card_code_match_confidence") or 0.0)
        quality_score = float(candidate.get("quality_score") or candidate.get("image_score") or 0.0)
        trust_score = float(candidate.get("trust_score") or 0.0)
        clarity = float(candidate.get("clarity_score") or 0.0)
        crop = float(candidate.get("crop_confidence") or 0.0)
        origin_language = self.normalize_language_code(candidate.get("origin_language") or "en")
        english_print_exists = bool(int(candidate.get("english_print_exists") or 0))
        display_policy = str(candidate.get("display_policy") or "english-first")
        verified_bonus = 14.0 if str(candidate.get("verification_state") or "").strip().lower() == "verified" else 0.0
        sample_penalty = 10.0 if bool(int(candidate.get("sample_flag") or 0)) else 0.0
        lowres_penalty = 12.0 if str(candidate.get("quality_tier") or "") == "fallback_lowres" else 0.0
        alt_penalty = 0.0
        base_bonus = 0.0
        duplicate_penalty = 0.0
        duplicate_relationship = str((duplicate_info or {}).get("duplicate_relationship") or "unique")
        exact_duplicate_count = int((duplicate_info or {}).get("exact_duplicate_count") or 0)
        family_duplicate_count = int((duplicate_info or {}).get("family_duplicate_count") or 0)
        language_penalty = 0.0
        provisional_language_bonus = 0.0

        if origin_language != "en":
            if english_print_exists:
                language_penalty = 18.0
            else:
                provisional_language_bonus = 2.0

        if duplicate_relationship == "exact-duplicate" and exact_duplicate_count > 1:
            duplicate_penalty = 6.0
        elif duplicate_relationship == "same-art-different-crop-or-treatment" and family_duplicate_count > 1:
            duplicate_penalty = 3.5
        elif duplicate_relationship == "same-family-cautious" and family_duplicate_count > 1:
            duplicate_penalty = 1.75

        if selection_scope == "print_default":
            viability = card_match >= 0.55 and print_match >= 0.62 and not (origin_language != "en" and english_print_exists)
            selection_score = (
                (print_match * 62.0)
                + (art_family * 14.0)
                + (trust_score * 18.0)
                + (quality_score * 0.45)
                + (clarity * 8.0)
                + (crop * 8.0)
                + verified_bonus
                - sample_penalty
                - lowres_penalty
                - duplicate_penalty
                - language_penalty
                + provisional_language_bonus
            )
        elif selection_scope == "gallery_preferred":
            viability = card_match >= 0.55 and print_match >= 0.54 and not (origin_language != "en" and english_print_exists)
            selection_score = (
                (print_match * 50.0)
                + (art_family * 10.0)
                + (trust_score * 14.0)
                + (quality_score * 0.62)
                + (clarity * 10.0)
                + (crop * 10.0)
                + verified_bonus
                - (sample_penalty * 0.55)
                - (lowres_penalty * 0.7)
                - duplicate_penalty
                - language_penalty
                + provisional_language_bonus
            )
        else:
            is_base_like = bool(candidate_print["is_base"] or (candidate_print["is_parallel"] and not candidate_print["is_alt_art"]))
            base_bonus = 14.0 if is_base_like else 0.0
            alt_penalty = 22.0 if candidate_print["is_alt_art"] else 0.0
            viability = (
                card_match >= 0.55
                and art_family >= 0.6
                and (is_base_like or print_match >= 0.7)
                and not (origin_language != "en" and english_print_exists)
            )
            selection_score = (
                (card_match * 24.0)
                + (print_match * 30.0)
                + (art_family * 28.0)
                + (trust_score * 16.0)
                + (quality_score * 0.5)
                + (clarity * 8.0)
                + base_bonus
                + verified_bonus
                - sample_penalty
                - lowres_penalty
                - alt_penalty
                - duplicate_penalty
                - language_penalty
                + provisional_language_bonus
            )

        selection_reason = print_comparison["relationship"]
        if selection_scope == "card_default" and alt_penalty > 0:
            selection_reason = "deprioritized-alt-art-for-card-default"
        elif bool(int(candidate.get("sample_flag") or 0)):
            selection_reason = f"{selection_reason}-sample"
        if origin_language != "en" and not english_print_exists:
            selection_reason = f"{selection_reason}-translated-origin-provisional"
        elif origin_language != "en" and english_print_exists:
            selection_reason = f"{selection_reason}-english-preferred-deferred"

        return {
            "candidate_id": int(candidate.get("id") or 0),
            "card_code": str(candidate.get("card_code") or ""),
            "variant_key": candidate_variant,
            "print_id": str(candidate.get("print_id") or candidate_print["print_id"]),
            "selection_scope": selection_scope,
            "selection_score": round(selection_score, 2),
            "viable": bool(viability and str(candidate.get("quality_tier") or "") != "rejected"),
            "print_match_confidence": round(print_match, 2),
            "variant_match_confidence": round(variant_match, 2),
            "art_family_confidence": round(art_family, 2),
            "selection_confidence": round(
                max(
                    0.18,
                    min(
                        0.98,
                        (float(candidate.get("selection_confidence") or self._selection_confidence_from_row(candidate)) * 0.72)
                        + (max(selection_score, 0.0) / 300.0),
                    ),
                ),
                2,
            ),
            "selection_reason": selection_reason,
            "comparison_summary": {
                "selection_scope": selection_scope,
                "selection_score": round(selection_score, 2),
                "viable": bool(viability),
                "print_relationship": print_comparison["relationship"],
                "mismatch_flags": list(print_comparison["mismatch_flags"]),
                "print_match_confidence": round(print_match, 2),
                "variant_match_confidence": round(variant_match, 2),
                "art_family_confidence": round(art_family, 2),
                "quality_score": round(quality_score, 2),
                "trust_score": round(trust_score, 2),
                "verified_bonus": verified_bonus,
                "sample_penalty": sample_penalty,
                "lowres_penalty": lowres_penalty,
                "base_bonus": base_bonus,
                "alt_penalty": alt_penalty,
                "duplicate_relationship": duplicate_relationship,
                "duplicate_penalty": duplicate_penalty,
                "exact_duplicate_count": exact_duplicate_count,
                "family_duplicate_count": family_duplicate_count,
                "family_group": str((duplicate_info or {}).get("family_group") or ""),
                "family_reasoning": str((duplicate_info or {}).get("family_reasoning") or ""),
                "classification_signals": list((duplicate_info or {}).get("classification_signals") or []),
                "origin_language": origin_language,
                "english_print_exists": bool(english_print_exists),
                "display_policy": display_policy,
                "language_penalty": language_penalty,
                "provisional_language_bonus": provisional_language_bonus,
            },
        }

    def evaluate_selection_scope(
        self,
        *,
        card_code: str,
        variant_key: str = "",
        selection_scope: str,
    ) -> dict[str, Any]:
        include_other_variants = selection_scope == "card_default"
        candidates = self.fetch_image_candidates(
            card_code=card_code,
            variant_key=variant_key,
            include_other_variants=include_other_variants,
        )
        duplicate_analysis = self.analyze_duplicate_family(candidates)
        evaluations = [
            self.score_candidate_for_scope(
                candidate=item,
                selection_scope=selection_scope,
                target_variant_key=variant_key,
                duplicate_info=duplicate_analysis.get(int(item.get("id") or 0), {}),
            )
            | {"candidate": item}
            for item in candidates
        ]
        viable = [item for item in evaluations if item["viable"]]
        ranked = sorted(
            viable,
            key=lambda item: (
                float(item["selection_score"]),
                float(item["print_match_confidence"]),
                float(item["candidate"].get("image_score") or 0.0),
            ),
            reverse=True,
        )
        winner = ranked[0] if ranked else None
        runner_up = ranked[1] if len(ranked) > 1 else None
        return {
            "selection_scope": selection_scope,
            "target_variant_key": normalize_variant_key(variant_key),
            "candidates_considered": len(evaluations),
            "viable_candidates": len(viable),
            "ranked_candidates": ranked,
            "winner": winner,
            "runner_up": runner_up,
        }

    def apply_selection_scope(
        self,
        *,
        card_code: str,
        variant_key: str = "",
        selection_scope: str,
        reason: str = "",
    ) -> bool:
        target_print = self.build_print_identity(card_code=card_code, variant_key=variant_key)
        evaluation = self.evaluate_selection_scope(
            card_code=card_code,
            variant_key=variant_key,
            selection_scope=selection_scope,
        )
        winner = evaluation.get("winner")
        if not isinstance(winner, dict):
            return False
        existing_selection = self.fetch_image_selection(
            card_code=card_code,
            selection_scope=selection_scope,
            print_id=target_print["print_id"],
        )
        ranked_candidates = list(evaluation.get("ranked_candidates") or [])
        if existing_selection is not None and ranked_candidates:
            current_candidate_id = int(existing_selection.get("image_candidate_id") or 0)
            current_evaluation = next(
                (item for item in ranked_candidates if int(item.get("candidate_id") or 0) == current_candidate_id),
                None,
            )
            replacement_margin = {
                "card_default": 8.0,
                "print_default": 6.0,
                "gallery_preferred": 4.0,
            }.get(selection_scope, 6.0)
            if current_evaluation is not None and int(winner.get("candidate_id") or 0) != current_candidate_id:
                current_score = float(current_evaluation.get("selection_score") or 0.0)
                winner_score = float(winner.get("selection_score") or 0.0)
                winner_print_match = float(winner.get("print_match_confidence") or 0.0)
                current_print_match = float(current_evaluation.get("print_match_confidence") or 0.0)
                duplicate_relationship = self.compare_duplicate_relationship(
                    dict(current_evaluation.get("candidate") or {}),
                    dict(winner.get("candidate") or {}),
                )
                if duplicate_relationship == "exact-duplicate":
                    replacement_margin += 4.0
                elif duplicate_relationship == "same-art-different-crop-or-treatment":
                    replacement_margin += 4.0
                elif duplicate_relationship == "same-family-cautious":
                    replacement_margin += 2.0
                if winner_score < current_score + replacement_margin and winner_print_match < current_print_match + 0.14:
                    winner = current_evaluation
        winner_candidate = dict(winner.get("candidate") or {})
        runner_up = evaluation.get("runner_up")
        runner_up_score = float((runner_up or {}).get("selection_score") or 0.0)
        winner_score = float(winner.get("selection_score") or 0.0)
        selection_reason = str(reason or winner.get("selection_reason") or "phase3-selection")
        comparison_summary = dict(winner.get("comparison_summary") or {})
        comparison_summary["phase"] = "phase3"
        comparison_summary["winner_score"] = winner_score
        comparison_summary["runner_up_score"] = runner_up_score
        comparison_summary["score_margin"] = round(winner_score - runner_up_score, 2)
        if runner_up:
            comparison_summary["runner_up_candidate_id"] = int(runner_up.get("candidate_id") or 0)
            comparison_summary["runner_up_reason"] = str(runner_up.get("selection_reason") or "")
            comparison_summary["runner_up_duplicate_relationship"] = self.compare_duplicate_relationship(
                dict(winner.get("candidate") or {}),
                dict(runner_up.get("candidate") or {}),
            )
        comparison_summary["winner_candidate_id"] = int(winner.get("candidate_id") or 0)
        comparison_summary["winner_variant_key"] = str(winner.get("variant_key") or "")
        comparison_summary["winner_duplicate_relationship"] = str(
            comparison_summary.get("duplicate_relationship") or "unique"
        )
        self.upsert_image_selection(
            card_code=card_code,
            print_id=target_print["print_id"],
            variant_key=variant_key,
            selection_scope=selection_scope,
            image_candidate_id=int(winner.get("candidate_id") or 0),
            best_image_flag=True,
            upgrade_eligible=bool(int(winner_candidate.get("replacement_eligible") or 0)),
            selection_confidence=float(winner.get("selection_confidence") or 0.0),
            quality_tier=str(winner_candidate.get("quality_tier") or ""),
            trust_tier=int(winner_candidate.get("source_trust_tier") or 4),
            selection_reason=selection_reason,
            comparison_summary=comparison_summary,
            citation_payload=dict(winner_candidate.get("citation_payload") or {}),
            origin_language=str(winner_candidate.get("origin_language") or "en"),
            english_print_exists=bool(int(winner_candidate.get("english_print_exists") or 0)),
            display_policy=str(winner_candidate.get("display_policy") or "english-first"),
            provisional_language_display=bool(int(winner_candidate.get("provisional_language_display") or 0)),
        )
        if selection_scope == "print_default":
            with closing(connect_sqlite(self.dossier_db_path)) as conn:
                conn.execute(
                    """
                    UPDATE learning_dossier_images
                    SET is_current_best = CASE WHEN id = ? THEN 1 ELSE 0 END,
                        selection_scope = CASE WHEN id = ? THEN ? ELSE selection_scope END,
                        selection_reason = CASE WHEN id = ? THEN ? ELSE selection_reason END,
                        selection_confidence = CASE WHEN id = ? THEN ? ELSE selection_confidence END
                    WHERE card_code = ?
                      AND variant_key = ?
                    """,
                    (
                        int(winner.get("candidate_id") or 0),
                        int(winner.get("candidate_id") or 0),
                        selection_scope,
                        int(winner.get("candidate_id") or 0),
                        selection_reason,
                        int(winner.get("candidate_id") or 0),
                        float(winner.get("selection_confidence") or 0.0),
                        card_code,
                        normalize_variant_key(variant_key),
                    ),
                )
        return True

    def refresh_scaffolded_image_selections(
        self,
        *,
        card_code: str,
        variant_key: str,
        reason: str = "phase3-selection-refresh",
    ) -> int:
        scopes = ["print_default", "gallery_preferred"]
        if not normalize_variant_key(variant_key):
            scopes.insert(0, "card_default")
        updated = 0
        for scope in scopes:
            if self.apply_selection_scope(
                card_code=card_code,
                variant_key=variant_key,
                selection_scope=scope,
                reason=reason,
            ):
                updated += 1
        return updated

    def backfill_image_intelligence_phase1(self, limit: int = 500) -> None:
        try:
            with closing(connect_sqlite(self.dossier_db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM learning_dossier_images
                    ORDER BY id ASC
                    LIMIT ?
                    """,
                    (max(int(limit), 1),),
                ).fetchall()
        except sqlite3.Error:
            return

        for row in rows:
            item = {key: row[key] for key in row.keys()}
            print_identity = self.build_print_identity(
                card_code=str(item.get("card_code") or ""),
                variant_key=str(item.get("variant_key") or ""),
                variant_label=str(item.get("variant_label") or ""),
                print_label=str(item.get("print_label") or ""),
            )
            citation_payload = {
                "source_id": str(item.get("source_id") or ""),
                "source_reference": str(item.get("source_reference") or ""),
                "source_url": str(item.get("source_url") or ""),
                "image_hash": str(item.get("image_hash") or ""),
            }
            score_breakdown = {
                "image_score": float(item.get("image_score") or 0.0),
                "quality_tier": str(item.get("quality_tier") or ""),
                "card_code_match_confidence": float(item.get("card_code_match_confidence") or 0.0),
                "clarity_score": float(item.get("clarity_score") or 0.0),
                "crop_confidence": float(item.get("crop_confidence") or 0.0),
            }
            self.update_image_candidate_scaffold(
                candidate_id=int(item.get("id") or 0),
                print_identity=print_identity,
                source_type=str(item.get("source_type") or ""),
                selection_scope="print_default" if int(item.get("is_current_best") or 0) else "",
                selection_reason=str(item.get("selection_reason") or "phase1-backfill"),
                selection_confidence=self._selection_confidence_from_row(item),
                print_match_confidence=float(item.get("print_match_confidence") or item.get("variant_match_confidence") or 0.0),
                variant_match_confidence=float(item.get("variant_match_confidence") or (1.0 if not print_identity["variant_key"] else 0.85)),
                art_family_confidence=float(item.get("art_family_confidence") or (0.9 if print_identity["is_base"] else 0.72)),
                quality_score=float(item.get("quality_score") or item.get("image_score") or 0.0),
                trust_score=float(item.get("trust_score") or max(0.1, 1.1 - (0.2 * int(item.get("source_trust_tier") or 4)))),
                content_status=str(item.get("content_status") or "candidate"),
                duplicate_group=str(item.get("duplicate_group") or ""),
                perceptual_hash=str(item.get("perceptual_hash") or ""),
                origin_language=str(item.get("origin_language") or "en"),
                english_print_exists=bool(int(item.get("english_print_exists") or 0)),
                display_policy=str(item.get("display_policy") or "english-first"),
                provisional_language_display=bool(int(item.get("provisional_language_display") or 0)),
                citation_payload=citation_payload,
                score_breakdown=score_breakdown,
            )
            self.upsert_learning_print(
                print_identity=print_identity,
                verification_state="source-backed" if str(item.get("verification_state") or "").strip().lower() == "verified" else "scaffolded",
                match_confidence=float(item.get("card_code_match_confidence") or 0.0),
                supporting_sources=[citation_payload],
                citation_payload=citation_payload,
                verified_at=str(item.get("last_verified_at") or ""),
            )
            if int(item.get("is_current_best") or 0):
                self.refresh_scaffolded_image_selections(
                    card_code=str(item.get("card_code") or ""),
                    variant_key=str(item.get("variant_key") or ""),
                    reason="phase1-backfill-current-best",
                )

    def load_knowledge_cache(self) -> dict[str, Any]:
        if self._knowledge_cache is None:
            payload = json.loads(self.knowledge_cache_path.read_text(encoding="utf-8"))
            self._knowledge_cache = payload if isinstance(payload, dict) else {}
        return self._knowledge_cache

    def load_cached_knowledge_source_records(self, *, source_id: str) -> list[NormalizedSourceRecord]:
        cards = self.load_knowledge_cache().get("cards") or {}
        target_source = str(source_id or "").strip().lower()
        records: list[NormalizedSourceRecord] = []
        for card_code, payload in cards.items():
            if not isinstance(payload, dict):
                continue
            sources = [str(item or "").strip().lower() for item in payload.get("sources") or [] if str(item or "").strip()]
            if target_source and target_source not in sources:
                continue
            canonical = normalize_card_code(str(payload.get("canonical_code") or card_code))
            resolved_code = canonical["canonical_code"] or str(card_code or "").strip().upper()
            if not resolved_code:
                continue
            records.append(
                NormalizedSourceRecord(
                    card_code=resolved_code,
                    card_name=clean_display_text(str(payload.get("card_name") or "")),
                    set_code=clean_display_text(str(payload.get("set_code") or canonical.get("set_code") or "")).upper(),
                    set_name=clean_display_text(str(payload.get("set_name") or "")),
                    rarity=clean_display_text(str(payload.get("rarity") or "")),
                    color=clean_display_text(str(payload.get("color") or "")),
                    card_type=clean_display_text(str(payload.get("card_type") or "")),
                    cost=clean_display_text(str(payload.get("cost") or "")),
                    power=clean_display_text(str(payload.get("power") or "")),
                    counter=clean_display_text(str(payload.get("counter") or "")),
                    attribute=clean_display_text(str(payload.get("attribute") or "")),
                    traits=[clean_display_text(str(item)) for item in (payload.get("traits") or []) if clean_display_text(str(item))],
                    life=clean_display_text(str(payload.get("life") or "")),
                    effect_text=clean_display_text(str(payload.get("effect_text") or "")),
                    trigger_text=clean_display_text(str(payload.get("trigger_text") or "")),
                    source_id=target_source,
                    source_url="",
                    source_reference=f"knowledge-cache:{resolved_code}",
                    fetched_at=utc_timestamp(),
                    illustrator=clean_display_text(str(payload.get("artist_credit") or payload.get("illustrator") or "")),
                )
            )
        return records

    def load_cached_knowledge_card_payload(self, card_code: str) -> dict[str, Any]:
        cards = self.load_knowledge_cache().get("cards") or {}
        normalized = normalize_card_code(card_code)
        candidates = [
            str(normalized.get("canonical_code") or "").strip().upper(),
            str(card_code or "").strip().upper(),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            payload = cards.get(candidate)
            if isinstance(payload, dict):
                return payload
        return {}

    def fetch_catalog_card_payload(self, card_code: str) -> dict[str, Any]:
        if not self.project_db_path.is_file():
            return {}
        normalized = normalize_card_code(card_code)
        resolved_code = str(normalized.get("canonical_code") or card_code or "").strip().upper()
        if not resolved_code:
            return {}
        with closing(connect_sqlite(self.project_db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM cards WHERE canonical_code = ?",
                (resolved_code,),
            ).fetchone()
        if row is None:
            return {}
        return {key: row[key] for key in row.keys()}

    def local_verify_fallback_has_meaningful_data(
        self,
        knowledge_payload: dict[str, Any],
        catalog_payload: dict[str, Any],
    ) -> bool:
        meaningful_fields = (
            "card_name",
            "set_name",
            "rarity",
            "color",
            "card_type",
            "cost",
            "power",
            "counter",
            "attribute",
            "life",
            "effect_text",
            "trigger_text",
            "illustrator",
            "artist_credit",
        )
        for payload in (knowledge_payload, catalog_payload):
            if not isinstance(payload, dict):
                continue
            for field_name in meaningful_fields:
                if clean_display_text(str(payload.get(field_name) or "")):
                    return True
            traits_value = payload.get("traits")
            if isinstance(traits_value, list) and any(clean_display_text(str(item)) for item in traits_value):
                return True
            if clean_display_text(str(traits_value or "")):
                return True
        return False

    def build_local_verify_fallback_record(
        self,
        card_code: str,
        *,
        preferred_source_id: str = "official-cardlist",
    ) -> NormalizedSourceRecord | None:
        normalized = normalize_card_code(card_code)
        resolved_code = str(normalized.get("canonical_code") or card_code or "").strip().upper()
        if not resolved_code:
            return None
        knowledge_payload = self.load_cached_knowledge_card_payload(resolved_code) if self.knowledge_cache_path.is_file() else {}
        catalog_payload = self.fetch_catalog_card_payload(resolved_code)
        if not self.local_verify_fallback_has_meaningful_data(knowledge_payload, catalog_payload):
            return None

        def pick(*values: Any) -> str:
            for value in values:
                cleaned = clean_display_text(str(value or ""))
                if cleaned:
                    return cleaned
            return ""

        traits_payload = knowledge_payload.get("traits")
        if isinstance(traits_payload, list):
            traits = [clean_display_text(str(item)) for item in traits_payload if clean_display_text(str(item))]
        else:
            traits_raw = traits_payload or catalog_payload.get("traits") or ""
            traits = [
                clean_display_text(part)
                for part in str(traits_raw).replace("|", "/").split("/")
                if clean_display_text(part)
            ]

        source_tags = [
            str(item or "").strip().lower()
            for item in (knowledge_payload.get("sources") or [])
            if str(item or "").strip()
        ]
        fallback_source_id = source_tags[0] if source_tags else str(catalog_payload.get("canonical_source") or "").strip().lower()
        if not fallback_source_id:
            fallback_source_id = str(preferred_source_id or "local-corroboration").strip().lower()

        observed_at = pick(
            knowledge_payload.get("updated_at"),
            knowledge_payload.get("last_seen_at"),
            catalog_payload.get("source_updated_at"),
            catalog_payload.get("metadata_updated_at"),
        )

        return NormalizedSourceRecord(
            card_code=resolved_code,
            card_name=pick(knowledge_payload.get("card_name"), catalog_payload.get("card_name")),
            set_code=pick(knowledge_payload.get("set_code"), catalog_payload.get("set_code"), normalized.get("set_code")).upper(),
            set_name=pick(knowledge_payload.get("set_name"), catalog_payload.get("set_name")),
            rarity=pick(knowledge_payload.get("rarity"), catalog_payload.get("rarity")),
            color=pick(knowledge_payload.get("color"), catalog_payload.get("color")),
            card_type=pick(knowledge_payload.get("card_type"), catalog_payload.get("card_type")),
            cost=pick(knowledge_payload.get("cost"), catalog_payload.get("cost")),
            power=pick(knowledge_payload.get("power"), catalog_payload.get("power")),
            counter=pick(knowledge_payload.get("counter"), catalog_payload.get("counter")),
            attribute=pick(knowledge_payload.get("attribute"), catalog_payload.get("attribute")),
            traits=traits,
            life=pick(knowledge_payload.get("life"), catalog_payload.get("life")),
            effect_text=pick(knowledge_payload.get("effect_text"), catalog_payload.get("effect_text")),
            trigger_text=pick(knowledge_payload.get("trigger_text"), catalog_payload.get("trigger_text")),
            source_id=fallback_source_id,
            source_url=pick(knowledge_payload.get("source_url"), catalog_payload.get("canonical_source_url")),
            source_reference=observed_at or f"local-fallback:{resolved_code}",
            fetched_at=utc_timestamp(),
            illustrator=pick(
                knowledge_payload.get("artist_credit"),
                knowledge_payload.get("illustrator"),
                catalog_payload.get("illustrator"),
                catalog_payload.get("artist_credit"),
            ),
        )

    def load_known_set_codes(self) -> set[str]:
        if self._known_set_codes is not None:
            return set(self._known_set_codes)
        known: set[str] = set()
        if self.catalog_db_path.is_file():
            try:
                with closing(connect_sqlite(self.catalog_db_path)) as conn:
                    rows = conn.execute(
                        "SELECT DISTINCT set_code FROM cards WHERE trim(coalesce(set_code, '')) != ''"
                    ).fetchall()
                for row in rows:
                    code = str(row["set_code"] or "").strip().upper()
                    if code:
                        known.add(code)
            except sqlite3.Error:
                pass
        knowledge_cards = (self.load_knowledge_cache().get("cards") or {}) if self.knowledge_cache_path.is_file() else {}
        for payload in knowledge_cards.values():
            if not isinstance(payload, dict):
                continue
            code = clean_display_text(str(payload.get("set_code") or "")).upper()
            if code:
                known.add(code)
        self._known_set_codes = known
        return set(known)

    def validate_learning_reference(self, *, card_code: str = "", set_code: str = "") -> dict[str, Any]:
        return validate_one_piece_reference(
            card_code=card_code,
            set_code=set_code,
            known_set_codes=self.load_known_set_codes(),
        )

    def count_invalid_references_today(self) -> int:
        try:
            with closing(connect_sqlite(self.status_db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM engine_log
                    WHERE event_type = 'ignored_invalid_reference'
                      AND created_at >= datetime('now', 'start of day')
                    """
                ).fetchone()
            return int(row["total"] or 0) if row is not None else 0
        except sqlite3.Error:
            return 0

    def has_recent_invalid_reference_log(
        self,
        *,
        card_code: str = "",
        set_code: str = "",
        reason: str = "invalid_set_reference",
        cooldown_hours: int = 24,
    ) -> bool:
        target = (card_code or set_code or "").strip().upper()
        if not target:
            return False
        try:
            with closing(connect_sqlite(self.status_db_path)) as conn:
                row = conn.execute(
                    """
                    SELECT 1
                    FROM engine_log
                    WHERE event_type = 'ignored_invalid_reference'
                      AND card_code = ?
                      AND task_type = ?
                      AND created_at >= datetime('now', ?)
                    LIMIT 1
                    """,
                    (target, reason, f"-{max(int(cooldown_hours), 1)} hours"),
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def note_invalid_reference(self, *, card_code: str = "", set_code: str = "", reason: str = "invalid_set_reference") -> None:
        target = card_code or set_code or "unknown reference"
        if not self.has_recent_invalid_reference_log(card_code=card_code, set_code=set_code, reason=reason):
            self.append_log(
                level="warning",
                event_type="ignored_invalid_reference",
                message=f"Skipped learning work for invalid reference {target}.",
                card_code=card_code or set_code,
                task_type=reason,
            )
        total = self.count_invalid_references_today()
        if total >= 50:
            self.send_operator_notification(
                "invalid_reference_daily_threshold",
                "Miru ignored multiple placeholder card references today. Learning continues normally.",
                cooldown_seconds=86400,
            )

    def normalize_historical_failure_backlog(self) -> dict[str, int]:
        summary = {
            "invalid_reference_reclassified": 0,
            "resolved_bug_reclassified": 0,
        }
        if not self.queue_db_path.exists():
            return summary
        now = utc_timestamp()
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            invalid_result = conn.execute(
                """
                UPDATE learning_queue
                SET status = 'skipped_invalid_set',
                    completed_at = CASE WHEN trim(coalesce(completed_at, '')) = '' THEN ? ELSE completed_at END,
                    updated_at = ?,
                    last_error = ''
                WHERE status = 'failed'
                  AND (
                      last_error LIKE 'LookupError: No local card profile found for %'
                      OR last_error LIKE 'LookupError: No source record found for ST01-%'
                      OR last_error LIKE 'LookupError: No source record found for OP99-%'
                  )
                """,
                (now, now),
            )
            summary["invalid_reference_reclassified"] += int(invalid_result.rowcount or 0)

            resolved_result = conn.execute(
                """
                UPDATE learning_queue
                SET status = 'retired_non_actionable',
                    completed_at = CASE WHEN trim(coalesce(completed_at, '')) = '' THEN ? ELSE completed_at END,
                    updated_at = ?
                WHERE status = 'failed'
                  AND (
                      last_error = 'SourceAdapterError: OfficialCardListSourceAdapter requires payload, snapshot_path, or snapshot_url.'
                      OR last_error = 'OperationalError: no such column: v.image_url'
                      OR last_error = 'OperationalError: attempt to write a readonly database'
                      OR last_error = 'AttributeError: ''sqlite3.Row'' object has no attribute ''get'''
                      OR last_error = 'KeyError: ''Unknown learning task type: analyze_card_effect'''
                      OR last_error = 'KeyError: ''Unknown learning task type: discover_set_cards'''
                      OR last_error = 'KeyError: ''Unknown learning task type: pushover_test'''
                      OR last_error = 'KeyError: ''Unknown learning task type: operator_command'''
                      OR last_error LIKE 'LookupError: No source record found for EB04-% from official-cardlist'
                  )
                """,
                (now, now),
            )
            summary["resolved_bug_reclassified"] += int(resolved_result.rowcount or 0)

        if summary["invalid_reference_reclassified"] or summary["resolved_bug_reclassified"]:
            self.append_log(
                level="info",
                event_type="queue_backlog_normalized",
                message=(
                    "Reclassified historical non-actionable learning failures: "
                    f"{summary['invalid_reference_reclassified']} invalid-reference row(s), "
                    f"{summary['resolved_bug_reclassified']} resolved-bug row(s)."
                ),
            )
        return summary

    def record_skipped_invalid_task(
        self,
        *,
        card_code: str,
        variant_key: str,
        task_type: str,
        source_id: str,
        priority: int,
        task_payload: dict[str, Any],
        task_signature: str,
        reason: str,
    ) -> None:
        now = utc_timestamp()
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            existing = conn.execute(
                """
                SELECT 1
                FROM learning_queue
                WHERE task_signature = ?
                  AND status = 'skipped_invalid_set'
                LIMIT 1
                """,
                (task_signature,),
            ).fetchone()
            if existing:
                return
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
                    updated_at,
                    completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'skipped_invalid_set', 0, '', ?, ?, ?, ?)
                """,
                (
                    card_code,
                    variant_key,
                    task_type,
                    source_id.strip().lower(),
                    task_signature,
                    int(priority),
                    json.dumps(task_payload, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    now,
                ),
            )

    def skip_invalid_task(self, task: LearningTask, message: str, *, source_reference: str = "") -> None:
        now = utc_timestamp()
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            conn.execute(
                """
                UPDATE learning_queue
                SET status = 'skipped_invalid_set',
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
            increment_processed=1,
        )
        self.append_log(
            level="info",
            event_type="task_skipped_invalid_set",
            message=message,
            card_code=task.card_code,
            task_type=task.task_type,
        )

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

    def append_budget_signal(
        self,
        *,
        event_type: str = "",
        card_code: str = "",
        task_type: str = "",
        detail: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Record a budget guardrail signal (expensive_avoided, cached_reused, deferred, etc.) for Dev visibility."""
        if not self.status_db_path.is_file():
            return
        now = utc_timestamp()
        extra_json = json.dumps(dict(extra or {}), ensure_ascii=False, sort_keys=True)
        with closing(connect_sqlite(self.status_db_path)) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO budget_signals (event_type, card_code, task_type, detail, extra_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(event_type or "").strip(),
                        str(card_code or "").strip(),
                        str(task_type or "").strip(),
                        str(detail or "").strip(),
                        extra_json,
                        now,
                    ),
                )
            except sqlite3.OperationalError:
                pass

    def send_operator_notification(
        self,
        event_type: str,
        message: str,
        *,
        title: str = "Miru Learning",
        priority: int | None = None,
        cooldown_seconds: int | None = None,
    ) -> dict[str, Any]:
        try:
            return send_operator_notification(
                event_type,
                message,
                title=title,
                priority=priority,
                cooldown_seconds=cooldown_seconds,
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            self.append_log(
                level="warning",
                event_type="operator_notification_failed",
                message=f"{event_type}: {exc.__class__.__name__}: {exc}",
            )
            return {
                "ok": False,
                "suppressed": False,
                "event_type": event_type,
                "error": f"{exc.__class__.__name__}: {exc}",
            }

    def maybe_send_learning_milestone(self) -> None:
        now = time.time()
        if now - self._last_milestone_check < 30.0:
            return
        self._last_milestone_check = now
        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db_path,
            status_db_path=self.status_db_path,
            dossier_db_path=self.dossier_db_path,
        )
        totals = load_verified_learning_totals(
            catalog_db_path=self.catalog_db_path,
            dossier_db_path=self.dossier_db_path,
        )
        current_verified = int(totals.get("verified_dossiers") or 0)
        coverage_percent = float(totals.get("verified_coverage_percent") or 0.0)
        threshold = learning_batch_threshold(current_verified)
        batch_state = load_learning_batch_state()
        current_floor = (current_verified // threshold) * threshold if threshold > 0 else current_verified
        last_notified_verified = int(batch_state.get("last_notified_verified_count") or 0)
        if last_notified_verified <= 0:
            save_learning_batch_state(
                {
                    "last_notified_verified_count": current_floor,
                    "last_threshold": threshold,
                    "updated_at": utc_timestamp(),
                }
            )
            last_notified_verified = current_floor
        elif current_verified < last_notified_verified:
            save_learning_batch_state(
                {
                    "last_notified_verified_count": current_floor,
                    "last_threshold": threshold,
                    "updated_at": utc_timestamp(),
                }
            )
            last_notified_verified = current_floor
        verified_delta = current_verified - last_notified_verified
        if threshold > 0 and verified_delta >= threshold:
            notification = build_batch_progress_notification(
                learning_status=snapshot,
                verified_delta=verified_delta,
                current_verified=current_verified,
                coverage_percent=coverage_percent,
            )
            result = self.send_operator_notification(
                f"learning_batch_progress_{current_verified}",
                notification["message"],
                title=str(notification["title"] or "Miru Learning"),
                cooldown_seconds=1800,
            )
            if bool(result.get("ok")):
                save_learning_batch_state(
                    {
                        "last_notified_verified_count": current_verified,
                        "last_threshold": threshold,
                        "updated_at": utc_timestamp(),
                    }
                )
                save_learning_notification_baseline(dict(notification["snapshot"]))
        self.maybe_send_set_completion_notifications()

    def maybe_send_set_completion_notifications(self) -> None:
        completed_sets = load_completed_verified_sets(
            catalog_db_path=self.catalog_db_path,
        )
        if not completed_sets:
            return
        notified_sets = load_notified_completed_sets()
        current_completed_set_codes = set(completed_sets.keys())
        if not notified_sets:
            save_notified_completed_sets(current_completed_set_codes)
            return
        newly_completed = sorted(current_completed_set_codes - notified_sets)
        if not newly_completed:
            return
        updated_notified_sets = set(notified_sets)
        for set_code in newly_completed:
            notification = build_set_completion_notification(set_code=set_code)
            result = self.send_operator_notification(
                f"set_completed_{set_code.lower()}",
                notification["message"],
                title=str(notification["title"] or "Miru Learning"),
                cooldown_seconds=0,
            )
            if bool(result.get("ok")):
                updated_notified_sets.add(set_code)
        if updated_notified_sets != notified_sets:
            save_notified_completed_sets(updated_notified_sets)

    def maybe_send_daily_summary(self) -> None:
        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db_path,
            status_db_path=self.status_db_path,
            dossier_db_path=self.dossier_db_path,
        )
        event_key = time.strftime("daily_summary_%Y%m%d", time.gmtime())
        notification = build_learning_notification(
            learning_status=snapshot,
            catalog_db_path=self.catalog_db_path,
        )
        result = self.send_operator_notification(
            event_key,
            notification["message"],
            title=str(notification["title"] or "Miru Learning"),
            cooldown_seconds=86400,
        )
        if bool(result.get("ok")):
            save_learning_notification_baseline(dict(notification["snapshot"]))

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
            validation = self.validate_learning_reference(card_code=canonical_card_code)
            if not validation["ok"]:
                normalized_variant = normalize_variant_key(variant_key or "")
                payload = task_payload or {}
                task_signature = build_task_signature(
                    card_code=canonical_card_code,
                    variant_key=normalized_variant,
                    task_type=task_type,
                    source_id=source_id,
                    task_payload=payload,
                )
                self.record_skipped_invalid_task(
                    card_code=canonical_card_code,
                    variant_key=normalized_variant,
                    task_type=task_type,
                    source_id=source_id,
                    priority=priority,
                    task_payload=payload,
                    task_signature=task_signature,
                    reason=str(validation["reason"] or "invalid_set_reference"),
                )
                self.note_invalid_reference(card_code=canonical_card_code, reason=str(validation["reason"] or "invalid_set_reference"))
                return False

        payload = task_payload or {}
        payload_set_code = str(payload.get("set_code") or "").strip().upper()
        if payload_set_code:
            validation = self.validate_learning_reference(card_code=canonical_card_code, set_code=payload_set_code)
            if not validation["ok"]:
                normalized_variant = normalize_variant_key(variant_key or payload.get("variant_key") or "")
                task_signature = build_task_signature(
                    card_code=canonical_card_code,
                    variant_key=normalized_variant,
                    task_type=task_type,
                    source_id=source_id,
                    task_payload=payload,
                )
                self.record_skipped_invalid_task(
                    card_code=canonical_card_code,
                    variant_key=normalized_variant,
                    task_type=task_type,
                    source_id=source_id,
                    priority=priority,
                    task_payload=payload,
                    task_signature=task_signature,
                    reason=str(validation["reason"] or "invalid_set_reference"),
                )
                self.note_invalid_reference(card_code=canonical_card_code, set_code=payload_set_code, reason=str(validation["reason"] or "invalid_set_reference"))
                return False
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
        counts = {"queued": 0, "running": 0, "failed": 0, "completed": 0, "skipped_invalid_set": 0, "retired_non_actionable": 0}
        if not self.queue_db_path.exists():
            return counts
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM learning_queue GROUP BY status"
            ).fetchall()
        for row in rows:
            counts[str(row["status"])] = int(row["total"])
        return counts

    def resolve_default_source_task_payload(self, source_id: str) -> dict[str, Any]:
        key = (source_id or "").strip().lower()
        source_entry = self.resolve_source_entry(key)
        payload: dict[str, Any] = {}
        env_path_key, env_url_key = DEFAULT_SOURCE_SNAPSHOT_ENV.get(key, ("", ""))
        snapshot_path = str(os.getenv(env_path_key, "") or "").strip() if env_path_key else ""
        if snapshot_path and Path(snapshot_path).is_file():
            payload["snapshot_path"] = snapshot_path
            return payload

        for candidate in DEFAULT_SOURCE_SNAPSHOT_PATHS.get(key, ()):
            if candidate.is_file():
                payload["snapshot_path"] = str(candidate)
                return payload

        snapshot_url = str(os.getenv(env_url_key, "") or "").strip() if env_url_key else ""
        if not snapshot_url:
            snapshot_url = str(source_entry.snapshot_url or "").strip()
        if snapshot_url:
            payload["snapshot_url"] = snapshot_url
        return payload

    def source_available_for_queueing(self, source_id: str) -> bool:
        return bool(self.resolve_default_source_task_payload(source_id))

    @staticmethod
    def source_payload_has_adapter_input(task_payload: dict[str, Any] | None = None) -> bool:
        payload = dict(task_payload or {})
        if isinstance(payload.get("payload"), dict) and payload.get("payload"):
            return True
        return bool(str(payload.get("snapshot_path") or "").strip() or str(payload.get("snapshot_url") or "").strip())

    @staticmethod
    def recoverable_successor_task(task_type: str, *, card_code: str = "") -> str:
        legacy_reasoning_tasks = {
            "cross_reference_traits",
            "analyze_card_effect",
            "extract_card_traits",
            "map_card_mechanics",
            "interpret_effect_structure",
        }
        normalized = str(task_type or "").strip()
        if normalized in legacy_reasoning_tasks and str(card_code or "").strip():
            return "sync_missing_fields"
        return ""

    def recover_stale_running_tasks(self, stale_after_seconds: int | None = None) -> int:
        stale_seconds = max(int(stale_after_seconds or self.stale_task_seconds), 60)
        cutoff_epoch = int(time.time()) - stale_seconds
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT id, card_code, variant_key, task_type, source_id, priority, task_payload_json
                FROM learning_queue
                WHERE status = 'running'
                  AND trim(coalesce(claimed_at, '')) != ''
                  AND CAST(strftime('%s', claimed_at) AS INTEGER) <= ?
                ORDER BY claimed_at ASC, id ASC
                """,
                (cutoff_epoch,),
            ).fetchall()
            if not rows:
                return 0

            supported_ids = [int(row["id"]) for row in rows if str(row["task_type"]) in TASK_HANDLERS]
            if supported_ids:
                placeholders = ", ".join("?" for _ in supported_ids)
                now = utc_timestamp()
                conn.execute(
                    f"""
                    UPDATE learning_queue
                    SET status = 'queued',
                        claimed_at = '',
                        updated_at = ?,
                        last_error = ?
                    WHERE id IN ({placeholders})
                    """,
                    (
                        now,
                        f"Recovered stale running task after {stale_seconds} seconds without completion.",
                        *supported_ids,
                    ),
                )

        unsupported_rows = [row for row in rows if int(row["id"]) not in set(supported_ids)]
        recovered_successors = 0
        retired_stale_ids: list[int] = []
        successor_messages: list[str] = []
        if unsupported_rows:
            now = utc_timestamp()
            with closing(connect_sqlite(self.queue_db_path)) as conn:
                for row in unsupported_rows:
                    successor_task = self.recoverable_successor_task(
                        str(row["task_type"] or ""),
                        card_code=str(row["card_code"] or ""),
                    )
                    if not successor_task:
                        continue
                    payload = json.loads(row["task_payload_json"] or "{}")
                    if self.enqueue_task(
                        card_code=str(row["card_code"] or ""),
                        variant_key=str(row["variant_key"] or ""),
                        task_type=successor_task,
                        source_id=str(row["source_id"] or ""),
                        priority=max(int(row["priority"] or 0), 35),
                        task_payload=payload if isinstance(payload, dict) else {},
                    ):
                        recovered_successors += 1
                    retired_stale_ids.append(int(row["id"]))
                    successor_messages.append(
                        f"{row['task_type']}->{successor_task}:{str(row['card_code'] or '').strip().upper()}"
                    )
                if retired_stale_ids:
                    placeholders = ", ".join("?" for _ in retired_stale_ids)
                    conn.execute(
                        f"""
                        UPDATE learning_queue
                        SET status = 'completed',
                            claimed_at = '',
                            completed_at = ?,
                            updated_at = ?,
                            last_error = ?
                        WHERE id IN ({placeholders})
                        """,
                        (
                            now,
                            now,
                            "Recovered from stale running state into a current actionable successor task.",
                            *retired_stale_ids,
                        ),
                    )

        unsupported_count = len(unsupported_rows) - len(retired_stale_ids)
        if supported_ids:
            self.append_log(
                level="warning",
                event_type="recovered_stale_tasks",
                message=f"Re-queued {len(supported_ids)} stale running learning task(s).",
            )
        if recovered_successors:
            self.append_log(
                level="warning",
                event_type="recovered_stale_successors",
                message=(
                    f"Recovered {recovered_successors} stale legacy reasoning task(s) into current actionable work: "
                    + ", ".join(successor_messages[:8])
                ),
            )
            self.send_operator_notification(
                "stale_learning_recovered",
                f"Miru recovered {recovered_successors} stale learning tasks and resumed card analysis.",
                cooldown_seconds=1800,
            )
        if unsupported_count:
            self.append_log(
                level="warning",
                event_type="stale_tasks_unsupported",
                message=(
                    f"Left {unsupported_count} stale running task(s) in place because their task types "
                    "are not registered in this engine build."
                ),
            )
        return len(supported_ids) + recovered_successors

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

    def seed_verified_field_tasks(self, limit: int | None = None) -> int:
        task_payload = self.resolve_default_source_task_payload("official-cardlist")
        has_official_snapshot = self.source_payload_has_adapter_input(task_payload)
        has_cache_fallback = False
        if not has_official_snapshot and self.knowledge_cache_path.is_file():
            try:
                has_cache_fallback = bool(self.load_cached_knowledge_source_records(source_id="official-cardlist"))
            except Exception:
                has_cache_fallback = False
        if not has_official_snapshot and not has_cache_fallback:
            return 0
        if not has_official_snapshot:
            task_payload = {}

        batch_limit = max(int(limit or self.seed_batch_size), 1)
        inserted = 0
        with closing(connect_sqlite(self.project_db_path)) as conn:
            conn.execute("ATTACH DATABASE ? AS learning_dossiers", (str(self.dossier_db_path),))
            conn.execute("ATTACH DATABASE ? AS learning_queue_db", (str(self.queue_db_path),))
            rows = conn.execute(
                """
                SELECT c.canonical_code
                FROM cards c
                LEFT JOIN miru_validations validations
                    ON validations.card_code = c.canonical_code
                LEFT JOIN learning_dossiers.learning_dossiers dossiers
                    ON dossiers.card_code = c.canonical_code
                LEFT JOIN learning_queue_db.learning_queue active_queue
                    ON active_queue.card_code = c.canonical_code
                   AND active_queue.task_type = 'verify_official_fields'
                   AND active_queue.status IN ('queued', 'running')
                LEFT JOIN learning_queue_db.learning_queue recent_queue
                    ON recent_queue.card_code = c.canonical_code
                   AND recent_queue.task_type = 'verify_official_fields'
                   AND recent_queue.status = 'completed'
                   AND recent_queue.updated_at >= datetime('now', '-72 hours')
                WHERE active_queue.id IS NULL
                  AND recent_queue.id IS NULL
                  AND (
                      validations.card_code IS NULL
                      OR COALESCE(validations.confidence, 0.0) < 0.9
                      OR dossiers.card_code IS NULL
                      OR COALESCE(dossiers.confidence, 0.0) < 0.9
                      OR dossiers.verification_state IN ('placeholder', 'local-bootstrap', 'local-catalog')
                  )
                ORDER BY
                    CASE WHEN validations.card_code IS NULL THEN 0 ELSE 1 END ASC,
                    CASE WHEN dossiers.card_code IS NULL THEN 0 ELSE 1 END ASC,
                    COALESCE(dossiers.confidence, 0.0) ASC,
                    c.canonical_code ASC
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
        for row in rows:
            if self.enqueue_task(
                card_code=str(row["canonical_code"]),
                task_type="verify_official_fields",
                source_id="official-cardlist",
                priority=90,
                task_payload=task_payload,
            ):
                inserted += 1
        if inserted:
            self.append_log(
                level="info",
                event_type="seed_queue",
                message=f"Queued {inserted} verify_official_fields task(s).",
            )
        return inserted

    def seed_bulk_registry_ingest_tasks(self, limit: int | None = None) -> int:
        if int(limit or 1) <= 0:
            return 0
        task_payload = self.resolve_default_source_task_payload("official-cardlist")
        has_official_snapshot = self.source_payload_has_adapter_input(task_payload)
        has_cache_fallback = False
        if not has_official_snapshot and self.knowledge_cache_path.is_file():
            try:
                has_cache_fallback = bool(
                    self.load_cached_knowledge_source_records(source_id="official-cardlist")
                )
            except Exception as exc:
                self.append_log(
                    level="warning",
                    event_type="bulk_registry_cache_unavailable",
                    message=f"Knowledge-cache fallback could not be loaded for bulk registry ingestion: {exc}",
                )
        if not has_official_snapshot and not has_cache_fallback:
            return 0
        if not has_official_snapshot:
            task_payload = {}
        source_ids = ["official-cardlist"]
        reputable_payload = self.resolve_default_source_task_payload("reputable-card-db")
        if self.source_payload_has_adapter_input(reputable_payload):
            source_ids.append("reputable-card-db")
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            active = conn.execute(
                """
                SELECT 1
                FROM learning_queue
                WHERE task_type = 'bulk_ingest_registry'
                  AND status IN ('queued', 'running')
                LIMIT 1
                """
            ).fetchone()
            recent = conn.execute(
                """
                SELECT 1
                FROM learning_queue
                WHERE task_type = 'bulk_ingest_registry'
                  AND status = 'completed'
                  AND updated_at >= datetime('now', '-12 hours')
                LIMIT 1
                """
            ).fetchone()
        if active or recent:
            return 0
        with closing(connect_sqlite(self.project_db_path)) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_cards,
                    SUM(CASE WHEN v.card_code IS NULL THEN 1 ELSE 0 END) AS missing_validation,
                    SUM(CASE WHEN COALESCE(v.confidence, 0.0) < 0.9 THEN 1 ELSE 0 END) AS low_confidence
                FROM cards c
                LEFT JOIN miru_validations v
                    ON v.card_code = c.canonical_code
                """
            ).fetchone()
        total_cards = int(row["total_cards"] or 0) if row is not None else 0
        missing_validation = int(row["missing_validation"] or 0) if row is not None else 0
        low_confidence = int(row["low_confidence"] or 0) if row is not None else 0
        if total_cards > 0 and missing_validation <= 0 and low_confidence <= 10:
            return 0
        bulk_payload = {
            **task_payload,
            "sources": source_ids,
            "batch_limit": max(self.seed_batch_size * 10, 250),
        }
        if self.enqueue_task(task_type="bulk_ingest_registry", priority=40, task_payload=bulk_payload):
            self.append_log(
                level="info",
                event_type="seed_queue",
                message="Queued bulk_ingest_registry to refresh the canonical card registry from structured sources.",
            )
            return 1
        return 0

    def seed_sync_missing_tasks(self, limit: int | None = None) -> int:
        batch_limit = max(int(limit or self.seed_batch_size), 1)
        inserted = 0
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute("ATTACH DATABASE ? AS learning_queue_db", (str(self.queue_db_path),))
            rows = conn.execute(
                """
                SELECT dossiers.card_code
                FROM learning_dossiers dossiers
                LEFT JOIN learning_queue_db.learning_queue active_queue
                    ON active_queue.card_code = dossiers.card_code
                   AND active_queue.task_type = 'sync_missing_fields'
                   AND active_queue.status IN ('queued', 'running')
                LEFT JOIN learning_queue_db.learning_queue recent_queue
                    ON recent_queue.card_code = dossiers.card_code
                   AND recent_queue.task_type = 'sync_missing_fields'
                   AND recent_queue.status = 'completed'
                   AND recent_queue.updated_at >= datetime('now', '-24 hours')
                WHERE active_queue.id IS NULL
                  AND recent_queue.id IS NULL
                  AND (
                      COALESCE(dossiers.confidence, 0.0) < 0.9
                      OR dossiers.verification_state IN ('placeholder', 'local-bootstrap', 'local-catalog')
                  )
                ORDER BY COALESCE(dossiers.confidence, 0.0) ASC, dossiers.updated_at ASC, dossiers.card_code ASC
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
        for row in rows:
            if self.enqueue_task(
                card_code=str(row["card_code"]),
                task_type="sync_missing_fields",
                priority=55,
            ):
                inserted += 1
        if inserted:
            self.append_log(
                level="info",
                event_type="seed_queue",
                message=f"Queued {inserted} sync_missing_fields task(s).",
            )
        return inserted

    def seed_low_confidence_followup_tasks(self, limit: int | None = None) -> int:
        task_payload = self.resolve_default_source_task_payload("official-cardlist")
        has_official_snapshot = self.source_payload_has_adapter_input(task_payload)
        has_cache_fallback = False
        if not has_official_snapshot and self.knowledge_cache_path.is_file():
            try:
                has_cache_fallback = bool(self.load_cached_knowledge_source_records(source_id="official-cardlist"))
            except Exception:
                has_cache_fallback = False
        if not has_official_snapshot and not has_cache_fallback:
            return 0
        if not has_official_snapshot:
            task_payload = {}

        batch_limit = max(int(limit or self.seed_batch_size), 1)
        inserted = 0
        with closing(connect_sqlite(self.project_db_path)) as conn:
            conn.execute("ATTACH DATABASE ? AS learning_dossiers", (str(self.dossier_db_path),))
            conn.execute("ATTACH DATABASE ? AS learning_queue_db", (str(self.queue_db_path),))
            rows = conn.execute(
                """
                SELECT c.canonical_code
                FROM cards c
                LEFT JOIN miru_validations validations
                    ON validations.card_code = c.canonical_code
                LEFT JOIN learning_dossiers.learning_dossiers dossiers
                    ON dossiers.card_code = c.canonical_code
                LEFT JOIN learning_queue_db.learning_queue active_queue
                    ON active_queue.card_code = c.canonical_code
                   AND active_queue.task_type = 'verify_official_fields'
                   AND active_queue.status IN ('queued', 'running')
                LEFT JOIN learning_queue_db.learning_queue recent_queue
                    ON recent_queue.card_code = c.canonical_code
                   AND recent_queue.task_type = 'verify_official_fields'
                   AND recent_queue.status = 'completed'
                   AND recent_queue.updated_at >= datetime('now', '-12 hours')
                WHERE active_queue.id IS NULL
                  AND recent_queue.id IS NULL
                  AND (
                      validations.card_code IS NULL
                      OR COALESCE(validations.confidence, 0.0) < 0.85
                      OR COALESCE(validations.verification_status, '') LIKE 'pending%'
                      OR COALESCE(validations.confidence_level, '') IN ('', 'low', 'medium')
                      OR COALESCE(c.illustrator, '') = ''
                      OR COALESCE(c.rarity, '') = ''
                      OR COALESCE(c.source_rollup_json, '') = ''
                      OR COALESCE(dossiers.confidence, 0.0) < 0.85
                      OR COALESCE(dossiers.verification_state, '') IN ('pending-confirmation', 'local-bootstrap', 'source-backed', 'pending-review-image-conflict')
                  )
                ORDER BY
                    CASE WHEN COALESCE(c.illustrator, '') = '' THEN 0 ELSE 1 END ASC,
                    CASE WHEN COALESCE(c.rarity, '') = '' THEN 0 ELSE 1 END ASC,
                    COALESCE(validations.confidence, 0.0) ASC,
                    COALESCE(dossiers.confidence, 0.0) ASC,
                    c.canonical_code ASC
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
        for row in rows:
            if self.enqueue_task(
                card_code=str(row["canonical_code"]),
                task_type="verify_official_fields",
                source_id="official-cardlist",
                priority=85,
                task_payload=task_payload,
            ):
                inserted += 1
        if inserted:
            self.append_log(
                level="info",
                event_type="seed_queue",
                message=f"Queued {inserted} low-confidence follow-up verification task(s).",
            )
        return inserted

    def seed_dossier_promotion_tasks(self, limit: int | None = None) -> int:
        card_codes = self.promotable_dossier_card_codes(limit=limit)
        if not card_codes:
            return 0
        with closing(connect_sqlite(self.queue_db_path)) as conn:
            active = conn.execute(
                """
                SELECT 1
                FROM learning_queue
                WHERE task_type = 'promote_verified_dossiers'
                  AND status IN ('queued', 'running')
                LIMIT 1
                """
            ).fetchone()
            recent = conn.execute(
                """
                SELECT 1
                FROM learning_queue
                WHERE task_type = 'promote_verified_dossiers'
                  AND status = 'completed'
                  AND updated_at >= datetime('now', '-30 minutes')
                LIMIT 1
                """
            ).fetchone()
        if active or recent:
            return 0
        inserted = 1 if self.enqueue_task(
            task_type="promote_verified_dossiers",
            priority=95,
            task_payload={
                "batch_limit": max(int(limit or self.seed_batch_size), 1),
                "reason": "promote_pending_confirmation",
            },
        ) else 0
        if inserted:
            self.append_log(
                level="info",
                event_type="seed_queue",
                message=f"Queued dossier promotion batch for {len(card_codes)} promotable dossier(s).",
            )
        return inserted

    def seed_inspect_missing_image_tasks(self, limit: int | None = None) -> int:
        batch_limit = max(int(limit or self.seed_batch_size), 1)
        inserted = 0
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute("ATTACH DATABASE ? AS learning_queue_db", (str(self.queue_db_path),))
            rows = conn.execute(
                """
                SELECT dossiers.card_code
                FROM learning_dossiers dossiers
                LEFT JOIN learning_dossier_images images
                    ON images.card_code = dossiers.card_code
                LEFT JOIN learning_queue_db.learning_queue active_queue
                    ON active_queue.card_code = dossiers.card_code
                   AND active_queue.task_type = 'inspect_missing_image'
                   AND active_queue.status IN ('queued', 'running')
                LEFT JOIN learning_queue_db.learning_queue recent_queue
                    ON recent_queue.card_code = dossiers.card_code
                   AND recent_queue.task_type = 'inspect_missing_image'
                   AND recent_queue.status = 'completed'
                   AND recent_queue.updated_at >= datetime('now', '-24 hours')
                WHERE images.id IS NULL
                  AND active_queue.id IS NULL
                  AND recent_queue.id IS NULL
                GROUP BY dossiers.card_code
                ORDER BY dossiers.updated_at ASC, dossiers.card_code ASC
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
        for row in rows:
            if self.enqueue_task(
                card_code=str(row["card_code"]),
                task_type="inspect_missing_image",
                priority=25,
            ):
                inserted += 1
        if inserted:
            self.append_log(
                level="info",
                event_type="seed_queue",
                message=f"Queued {inserted} inspect_missing_image task(s).",
            )
        return inserted

    def run_learning_seeder(self, *, force: bool = False) -> int:
        counts = self.queue_counts()
        if not force and counts["queued"] >= self.queue_low_threshold:
            return 0

        refill_budget = min(
            max(self.queue_low_threshold - counts["queued"], 1),
            max(self.seeder_refill_cap, self.seed_batch_size),
        )
        inserted = 0
        seeders: tuple[Callable[[int | None], int], ...] = (
            self.seed_dossier_promotion_tasks,
            self.seed_verified_field_tasks,
            self.seed_low_confidence_followup_tasks,
            self.seed_sync_missing_tasks,
            self.seed_inspect_missing_image_tasks,
            self.seed_bulk_registry_ingest_tasks,
            self.seed_missing_bootstrap_tasks,
        )
        for seeder in seeders:
            remaining = refill_budget - inserted
            if remaining <= 0:
                break
            inserted += seeder(remaining)

        if inserted == 0 and counts["queued"] == 0:
            if self.enqueue_task(task_type="refresh_progress", priority=5):
                inserted = 1

        if inserted:
            refreshed_counts = self.queue_counts()
            self.append_log(
                level="info",
                event_type="queue_refill",
                message=(
                    f"Learning seeder added {inserted} task(s); "
                    f"queue now has {refreshed_counts['queued']} queued task(s)."
                ),
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
        is_image_task = task.task_type in ({"fetch_card_image", "verify_card_image", "refresh_card_image"} | IMAGE_INTELLIGENCE_TASK_TYPES)
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
        self.maybe_send_learning_milestone()

    def fail_task(self, task: LearningTask, exc: Exception) -> None:
        error_message = f"{exc.__class__.__name__}: {exc}"
        retry_status = "failed" if task.attempts >= self.max_attempts else "queued"
        is_image_task = task.task_type in ({"fetch_card_image", "verify_card_image", "refresh_card_image"} | IMAGE_INTELLIGENCE_TASK_TYPES)
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
        if retry_status == "failed":
            self.send_operator_notification(
                "critical_learning_failure",
                f"Critical learning failure on {task.label}: {error_message[:300]}",
                priority=1,
                cooldown_seconds=300,
            )

    def catalog_card_row(self, card_code: str) -> dict[str, Any]:
        with closing(connect_sqlite(self.catalog_db_path)) as conn:
            row = conn.execute(
                """
                SELECT
                    c.canonical_code,
                    c.set_family,
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
                    c.illustrator,
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
        knowledge_cards: dict[str, Any] = {}
        if self.knowledge_cache_path.is_file():
            try:
                knowledge_cards = self.load_knowledge_cache().get("cards") or {}
            except Exception:
                knowledge_cards = {}
        knowledge_entry = knowledge_cards.get(resolved_code, {})
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
            "set_family": clean_display_text(str(choose("set_family") or "")),
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
            "illustrator": clean_display_text(str(choose("illustrator") or choose("artist_credit") or "")),
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

    @staticmethod
    def _source_intake_tokens(*values: Any) -> set[str]:
        tokens: set[str] = set()
        for value in values:
            text = normalize_variant_key(str(value or ""))
            if not text:
                continue
            tokens.update(part for part in re.split(r"[\s\-_:/\.]+", text) if part)
        return tokens

    @classmethod
    def _infer_source_classification(
        cls,
        *,
        source_id: str,
        source_type: str,
        source_url: str,
        notes: str,
    ) -> str:
        tokens = cls._source_intake_tokens(source_id, source_type, source_url, notes)
        if "official" in tokens:
            return "official"
        if tokens & {"market", "price", "pricing", "tcgplayer", "ebay", "auction", "listing"}:
            return "market"
        if tokens & {"community", "forum", "reddit", "discord", "fan", "social"}:
            return "community"
        if tokens & {"reference", "db", "database", "catalog", "local", "corroboration", "analysis", "cache", "wiki"}:
            return "reference"
        return "unknown"

    def evaluate_source_trust_intake(
        self,
        *,
        source_id: str,
        source_type: str = "",
        source_url: str = "",
        notes: str = "",
        trust_tier: int = 4,
        trust_label: str = "",
        public_data_only: bool | None = None,
        requires_login: bool | None = None,
        respect_site_policies: bool | None = None,
        review_state: str = "",
        last_reviewed_at: str = "",
    ) -> dict[str, Any]:
        resolved_source_id = str(source_id or "").strip().lower()
        resolved_source_type = str(source_type or "").strip().lower()
        resolved_notes = str(notes or "").strip()
        review_tokens = self._source_intake_tokens(review_state, resolved_notes)
        source_classification = self._infer_source_classification(
            source_id=resolved_source_id,
            source_type=resolved_source_type,
            source_url=source_url,
            notes=resolved_notes,
        )

        public_only = True if public_data_only is None else bool(public_data_only)
        login_required = False if requires_login is None else bool(requires_login)
        respect_policies = True if respect_site_policies is None else bool(respect_site_policies)

        access_expectation = "public"
        permission_status = "public-permitted"
        eligibility = "eligible_public_reference"
        allowed_for_learning = True
        evidence_role = "reference-facts"
        manual_approval_required = False

        if login_required or not public_only:
            access_expectation = "restricted"
            permission_status = "restricted"
            eligibility = "ineligible_restricted"
            allowed_for_learning = False
            evidence_role = "blocked"
            manual_approval_required = True
        elif not respect_policies or review_tokens & {"unknown", "experimental"}:
            access_expectation = "unknown"
            permission_status = "unknown-permissions"
            eligibility = "ineligible_unknown_permissions"
            allowed_for_learning = False
            evidence_role = "blocked"
            manual_approval_required = True
        elif source_classification == "official":
            eligibility = "eligible_public_official"
            evidence_role = "verified-facts"
        elif source_classification == "reference":
            eligibility = "eligible_public_reference"
            evidence_role = "reference-facts"
        elif source_classification == "community":
            eligibility = "caution_community"
            evidence_role = "lead-signal-only"
            manual_approval_required = True
        elif source_classification == "market":
            eligibility = "caution_market_signal"
            evidence_role = "market-hint-only"
            manual_approval_required = True
        else:
            access_expectation = "unknown"
            permission_status = "unknown-permissions"
            eligibility = "ineligible_unknown_permissions"
            allowed_for_learning = False
            evidence_role = "blocked"
            manual_approval_required = True

        rationale_map = {
            "eligible_public_official": "Public official source may supply verified facts within normal permitted access boundaries.",
            "eligible_public_reference": "Public reference source may support structured fact collection, but remains below official sources.",
            "caution_community": "Community source is retained only as a lead and requires stronger verification before facts are trusted.",
            "caution_market_signal": "Market source may provide hint-level signals only and must never be treated as authoritative truth.",
            "ineligible_restricted": "Restricted or gated source is not eligible for automated learning intake.",
            "ineligible_unknown_permissions": "Source permissions are unclear, so Miru defers use until manual review confirms policy-safe access.",
        }
        resolved_trust_label = str(trust_label or "").strip() or (
            "official" if source_classification == "official" else (
                "secondary/reference" if source_classification == "reference" else (
                    "community lead only" if source_classification == "community" else (
                        "market signal only" if source_classification == "market" else "manual review only"
                    )
                )
            )
        )
        intake = SourceTrustIntake(
            source_id=resolved_source_id,
            source_type=resolved_source_type,
            source_classification=source_classification,
            access_expectation=access_expectation,
            allowed_for_learning=allowed_for_learning,
            permission_status=permission_status,
            eligibility=eligibility,
            trust_tier=int(trust_tier or 4),
            trust_label=resolved_trust_label,
            evidence_role=evidence_role,
            manual_approval_required=manual_approval_required,
            rationale=rationale_map[eligibility],
            notes=resolved_notes,
            last_reviewed_at=str(last_reviewed_at or "").strip(),
        )
        return intake.to_dict()

    def build_source_trust_intake_for_source(
        self,
        source_id: str,
    ) -> dict[str, Any]:
        resolved_source_id = str(source_id or "").strip().lower()
        if not resolved_source_id:
            return self.evaluate_source_trust_intake(source_id="")
        try:
            profile = self.resolve_source_entry(resolved_source_id)
        except KeyError:
            return self.evaluate_source_trust_intake(source_id=resolved_source_id)
        return self.evaluate_source_trust_intake(
            source_id=profile.source_id,
            source_type=profile.source_type,
            source_url=profile.base_url or profile.snapshot_url,
            notes=profile.notes,
            trust_tier=profile.trust_tier,
            trust_label=profile.trust_label,
            public_data_only=profile.public_data_only,
            requires_login=profile.requires_login,
            respect_site_policies=profile.respect_site_policies,
            review_state=profile.review_state,
        )

    def upsert_reviewed_source_candidate(
        self,
        *,
        intake: dict[str, Any],
        review_status: str = "reviewed",
        notes: str = "",
        reviewed_at: str = "",
    ) -> None:
        now = utc_timestamp()
        resolved_reviewed_at = str(reviewed_at or now).strip()
        merged_notes = str(notes or intake.get("notes") or "").strip()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_source_reviews (
                    source_id,
                    source_type,
                    source_classification,
                    eligibility,
                    allowed_for_learning,
                    permission_status,
                    trust_tier,
                    trust_label,
                    evidence_role,
                    manual_approval_required,
                    review_status,
                    rationale,
                    notes,
                    reviewed_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    source_type = excluded.source_type,
                    source_classification = excluded.source_classification,
                    eligibility = excluded.eligibility,
                    allowed_for_learning = excluded.allowed_for_learning,
                    permission_status = excluded.permission_status,
                    trust_tier = excluded.trust_tier,
                    trust_label = excluded.trust_label,
                    evidence_role = excluded.evidence_role,
                    manual_approval_required = excluded.manual_approval_required,
                    review_status = excluded.review_status,
                    rationale = excluded.rationale,
                    notes = excluded.notes,
                    reviewed_at = excluded.reviewed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    str(intake.get("source_id") or "").strip().lower(),
                    str(intake.get("source_type") or "").strip().lower(),
                    str(intake.get("source_classification") or "").strip().lower(),
                    str(intake.get("eligibility") or "ineligible_unknown_permissions"),
                    1 if bool(intake.get("allowed_for_learning")) else 0,
                    str(intake.get("permission_status") or "unknown-permissions"),
                    int(intake.get("trust_tier") or 4),
                    str(intake.get("trust_label") or "").strip(),
                    str(intake.get("evidence_role") or "").strip(),
                    1 if bool(intake.get("manual_approval_required")) else 0,
                    str(review_status or "reviewed").strip(),
                    str(intake.get("rationale") or "").strip(),
                    merged_notes,
                    resolved_reviewed_at,
                    now,
                ),
            )

    def fetch_reviewed_source_candidate(
        self,
        source_id: str,
    ) -> dict[str, Any] | None:
        resolved_source_id = str(source_id or "").strip().lower()
        if not resolved_source_id:
            return None
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_source_reviews
                WHERE source_id = ?
                """,
                (resolved_source_id,),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["allowed_for_learning"] = bool(int(result.get("allowed_for_learning") or 0))
        result["manual_approval_required"] = bool(int(result.get("manual_approval_required") or 0))
        return result

    def list_reviewed_source_candidates(
        self,
        *,
        eligibility: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        query = """
            SELECT *
            FROM learning_source_reviews
        """
        if str(eligibility or "").strip():
            query += " WHERE eligibility = ?"
            params.append(str(eligibility).strip())
        query += " ORDER BY updated_at DESC, source_id ASC LIMIT ?"
        params.append(max(1, int(limit or 50)))
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["allowed_for_learning"] = bool(int(item.get("allowed_for_learning") or 0))
            item["manual_approval_required"] = bool(int(item.get("manual_approval_required") or 0))
            results.append(item)
        return results

    @staticmethod
    def normalize_source_review_decision_state(
        review_status: str,
        *,
        allowed_for_learning: bool,
        manual_approval_required: bool,
        source_reviewed: bool,
    ) -> str:
        normalized = normalize_variant_key(review_status)
        if not allowed_for_learning:
            return "blocked"
        if normalized in {"blocked", "deny", "denied", "rejected"}:
            return "blocked"
        if normalized in {"approved", "approved-for-learning", "approved-for-learning-facts", "approved-for-learning-source", "approved_for_learning"}:
            return "approved_for_learning"
        if normalized in {
            "approved-limited",
            "approved-for-limited-use",
            "approved-limited-use",
            "approved-leads-only",
            "approved-reference-only",
            "approved_for_limited_use",
        }:
            return "approved_for_limited_use"
        if normalized in {
            "pending-manual-review",
            "manual-review",
            "manual-review-only",
            "pending-review",
            "pending_manual_review",
        }:
            return "pending_manual_review"
        if not source_reviewed:
            return "pending_manual_review" if manual_approval_required else "approved_for_learning"
        if manual_approval_required:
            return "pending_manual_review"
        return "approved_for_learning"

    @staticmethod
    def resolve_source_evidence_role_policy(
        *,
        evidence_role: str,
        decision_state: str,
        allowed_for_learning: bool,
    ) -> dict[str, str]:
        resolved_role = str(evidence_role or "").strip() or "blocked"
        resolved_decision = str(decision_state or "").strip() or "blocked"
        if not allowed_for_learning or resolved_role == "blocked" or resolved_decision == "blocked":
            return {
                "policy_evidence_role": "blocked",
                "gate_action": "block",
                "policy_summary": "Source is blocked from learning use.",
            }
        if resolved_decision == "pending_manual_review":
            return {
                "policy_evidence_role": resolved_role,
                "gate_action": "manual-review",
                "policy_summary": "Source requires manual approval before Miru can rely on it.",
            }
        if resolved_role == "verified-facts":
            return {
                "policy_evidence_role": "verified-facts",
                "gate_action": "allow-learning",
                "policy_summary": "Source may support verified fact learning.",
            }
        if resolved_role == "reference-facts":
            return {
                "policy_evidence_role": "reference-facts",
                "gate_action": "allow-reference-only",
                "policy_summary": "Source may support secondary reference facts but does not outrank official sources.",
            }
        if resolved_role == "lead-signal-only":
            return {
                "policy_evidence_role": "lead-signal-only",
                "gate_action": "lead-only",
                "policy_summary": "Source may provide lead signals only and cannot be treated as verified truth.",
            }
        if resolved_role == "market-hint-only":
            return {
                "policy_evidence_role": "market-hint-only",
                "gate_action": "market-hint-only",
                "policy_summary": "Source may provide market hints only and cannot be treated as verified truth.",
            }
        return {
            "policy_evidence_role": "blocked",
            "gate_action": "block",
            "policy_summary": "Source role is not recognized for safe learning use.",
        }

    def evaluate_source_governance_policy(
        self,
        *,
        source_id: str,
        source_type: str = "",
        source_url: str = "",
        notes: str = "",
        trust_tier: int = 4,
        trust_label: str = "",
        public_data_only: bool | None = None,
        requires_login: bool | None = None,
        respect_site_policies: bool | None = None,
        review_state: str = "",
    ) -> dict[str, Any]:
        reviewed = self.fetch_reviewed_source_candidate(source_id)
        if reviewed is not None:
            intake = {
                "source_id": str(reviewed.get("source_id") or "").strip().lower(),
                "source_type": str(reviewed.get("source_type") or "").strip().lower(),
                "source_classification": str(reviewed.get("source_classification") or "").strip().lower(),
                "eligibility": str(reviewed.get("eligibility") or "ineligible_unknown_permissions"),
                "allowed_for_learning": bool(reviewed.get("allowed_for_learning")),
                "permission_status": str(reviewed.get("permission_status") or "unknown-permissions"),
                "trust_tier": int(reviewed.get("trust_tier") or 4),
                "trust_label": str(reviewed.get("trust_label") or "").strip(),
                "evidence_role": str(reviewed.get("evidence_role") or "").strip(),
                "manual_approval_required": bool(reviewed.get("manual_approval_required")),
                "rationale": str(reviewed.get("rationale") or "").strip(),
                "notes": str(reviewed.get("notes") or "").strip(),
                "last_reviewed_at": str(reviewed.get("reviewed_at") or "").strip(),
            }
            review_status = str(reviewed.get("review_status") or "reviewed")
            source_reviewed = True
        else:
            if not any(
                str(value or "").strip()
                for value in (source_type, source_url, notes, trust_label, review_state)
            ) and int(trust_tier or 4) == 4 and public_data_only is None and requires_login is None and respect_site_policies is None:
                intake = self.build_source_trust_intake_for_source(source_id)
            else:
                intake = self.evaluate_source_trust_intake(
                    source_id=source_id,
                    source_type=source_type,
                    source_url=source_url,
                    notes=notes,
                    trust_tier=trust_tier,
                    trust_label=trust_label,
                    public_data_only=public_data_only,
                    requires_login=requires_login,
                    respect_site_policies=respect_site_policies,
                    review_state=review_state,
                )
            review_status = "unreviewed"
            source_reviewed = False

        decision_state = self.normalize_source_review_decision_state(
            review_status,
            allowed_for_learning=bool(intake.get("allowed_for_learning")),
            manual_approval_required=bool(intake.get("manual_approval_required")),
            source_reviewed=source_reviewed,
        )
        role_policy = self.resolve_source_evidence_role_policy(
            evidence_role=str(intake.get("evidence_role") or ""),
            decision_state=decision_state,
            allowed_for_learning=bool(intake.get("allowed_for_learning")),
        )
        return {
            "source_id": str(intake.get("source_id") or "").strip().lower(),
            "source_reviewed": source_reviewed,
            "review_status": review_status,
            "decision_state": decision_state,
            "gate_action": str(role_policy.get("gate_action") or "block"),
            "policy_evidence_role": str(role_policy.get("policy_evidence_role") or "blocked"),
            "policy_summary": str(role_policy.get("policy_summary") or ""),
            "allowed_for_learning": bool(intake.get("allowed_for_learning")),
            "manual_approval_required": bool(intake.get("manual_approval_required")),
            "eligibility": str(intake.get("eligibility") or ""),
            "permission_status": str(intake.get("permission_status") or ""),
            "evidence_role": str(intake.get("evidence_role") or ""),
            "trust_tier": int(intake.get("trust_tier") or 4),
            "trust_label": str(intake.get("trust_label") or ""),
            "rationale": str(intake.get("rationale") or ""),
            "last_reviewed_at": str(intake.get("last_reviewed_at") or ""),
        }

    def evaluate_source_discovery_gate(
        self,
        *,
        source_id: str,
        source_type: str = "",
        source_url: str = "",
        notes: str = "",
        trust_tier: int = 4,
        trust_label: str = "",
        public_data_only: bool | None = None,
        requires_login: bool | None = None,
        respect_site_policies: bool | None = None,
        review_state: str = "",
        ) -> dict[str, Any]:
        return self.evaluate_source_governance_policy(
            source_id=source_id,
            source_type=source_type,
            source_url=source_url,
            notes=notes,
            trust_tier=trust_tier,
            trust_label=trust_label,
            public_data_only=public_data_only,
            requires_login=requires_login,
            respect_site_policies=respect_site_policies,
            review_state=review_state,
        )

    def evaluate_source_execution_gate(
        self,
        *,
        source_id: str,
        execution_kind: str = "learning-intake",
        source_type: str = "",
        source_url: str = "",
        notes: str = "",
        trust_tier: int = 4,
        trust_label: str = "",
        public_data_only: bool | None = None,
        requires_login: bool | None = None,
        respect_site_policies: bool | None = None,
        review_state: str = "",
    ) -> dict[str, Any]:
        governance = self.evaluate_source_governance_policy(
            source_id=source_id,
            source_type=source_type,
            source_url=source_url,
            notes=notes,
            trust_tier=trust_tier,
            trust_label=trust_label,
            public_data_only=public_data_only,
            requires_login=requires_login,
            respect_site_policies=respect_site_policies,
            review_state=review_state,
        )
        resolved_execution_kind = str(execution_kind or "learning-intake").strip().lower()
        gate_action = str(governance.get("gate_action") or "block")
        proceed = False
        execution_outcome = "block"
        reason = str(governance.get("policy_summary") or governance.get("rationale") or "").strip()

        if gate_action == "allow-learning":
            proceed = True
            execution_outcome = "allow-learning"
        elif gate_action == "allow-reference-only":
            if resolved_execution_kind == "reference-safe":
                proceed = True
                execution_outcome = "allow-reference-only"
            else:
                execution_outcome = "defer-reference-only"
                reason = "Source is approved only for secondary reference use; no limited reference-safe path exists here."
        elif gate_action == "manual-review":
            execution_outcome = "defer-manual-review"
            reason = "Source requires manual approval before this task may proceed."
        elif gate_action == "lead-only":
            if resolved_execution_kind == "lead-safe":
                proceed = True
                execution_outcome = "lead-only"
            else:
                execution_outcome = "defer-lead-only"
                reason = "Source is limited to lead-only use and cannot proceed through verified-fact intake."
        elif gate_action == "market-hint-only":
            if resolved_execution_kind == "market-hint-safe":
                proceed = True
                execution_outcome = "market-hint-only"
            else:
                execution_outcome = "defer-market-hint-only"
                reason = "Source is limited to market-hint use and cannot proceed through verified-fact intake."
        else:
            execution_outcome = "block"
            reason = reason or "Source is blocked by governance policy."

        return {
            **governance,
            "execution_kind": resolved_execution_kind,
            "execution_outcome": execution_outcome,
            "proceed": proceed,
            "reason": reason,
        }

    def record_source_limited_use_event(
        self,
        *,
        source_id: str,
        card_code: str = "",
        variant_key: str = "",
        task_type: str = "",
        evidence_role: str = "",
        execution_outcome: str = "",
        provenance: dict[str, Any] | None = None,
        notes: str = "",
    ) -> int:
        created_at = utc_timestamp()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO learning_source_limited_use_events (
                    source_id,
                    card_code,
                    variant_key,
                    task_type,
                    evidence_role,
                    execution_outcome,
                    provenance_json,
                    notes,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(source_id or "").strip().lower(),
                    str(card_code or "").strip().upper(),
                    normalize_variant_key(variant_key or ""),
                    str(task_type or "").strip(),
                    str(evidence_role or "").strip(),
                    str(execution_outcome or "").strip(),
                    json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True),
                    str(notes or "").strip(),
                    created_at,
                ),
            )
            return int(cursor.lastrowid or 0)

    def fetch_latest_source_limited_use_event(
        self,
        source_id: str,
    ) -> dict[str, Any] | None:
        resolved_source_id = str(source_id or "").strip().lower()
        if not resolved_source_id:
            return None
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_source_limited_use_events
                WHERE source_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (resolved_source_id,),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["provenance"] = json.loads(result.get("provenance_json") or "{}")
        return result

    def execute_limited_use_source_path(
        self,
        *,
        task: LearningTask,
        governance: dict[str, Any],
    ) -> dict[str, Any] | None:
        execution_outcome = str(governance.get("execution_outcome") or "")
        if execution_outcome not in {"defer-reference-only", "defer-lead-only", "defer-market-hint-only"}:
            return None
        role = str(governance.get("policy_evidence_role") or governance.get("evidence_role") or "blocked")
        event_id = self.record_source_limited_use_event(
            source_id=str(governance.get("source_id") or task.source_id or "").strip().lower(),
            card_code=task.card_code,
            variant_key=task.variant_key,
            task_type=task.task_type,
            evidence_role=role,
            execution_outcome=execution_outcome,
            provenance={
                "source_reviewed": bool(governance.get("source_reviewed")),
                "review_status": str(governance.get("review_status") or ""),
                "decision_state": str(governance.get("decision_state") or ""),
                "policy_evidence_role": role,
                "verified_fact_intake": False,
            },
            notes=str(governance.get("reason") or governance.get("policy_summary") or ""),
        )
        return {
            "message": (
                f"Recorded limited-use {role} context for {str(governance.get('source_id') or task.source_id or '')}: "
                f"{execution_outcome}."
            ),
            "task_type": task.task_type,
            "card_code": str(task.card_code or "").strip().upper(),
            "variant_key": normalize_variant_key(task.variant_key or ""),
            "source_id": str(governance.get("source_id") or task.source_id or "").strip().lower(),
            "source_reference": "",
            "limited_use": True,
            "limited_use_event_id": event_id,
            "limited_use_role": role,
            "verified_fact_intake": False,
            "governance": governance,
        }

    @staticmethod
    def normalize_corroboration_evidence_role(evidence_role: str) -> str:
        normalized = str(evidence_role or "").strip().lower()
        if normalized in {"verified-facts", "verified-fact", "official-facts", "official-fact"}:
            return "verified-facts"
        if normalized in {
            "reference-facts",
            "reference-fact",
            "reference-only",
            "reference",
            "local-corroboration",
            "image-confirmation",
        }:
            return "reference-facts"
        if normalized in {"lead-signal-only", "lead-only", "community-lead-only"}:
            return "lead-signal-only"
        if normalized in {"market-hint-only", "market-only", "market-signal-only"}:
            return "market-hint-only"
        return "blocked"

    def evaluate_fact_corroboration(
        self,
        *,
        evidence_items: Sequence[dict[str, Any]],
        candidate_value: str = "",
    ) -> dict[str, Any]:
        resolved_candidate = str(candidate_value or "").strip()
        role_counts: dict[str, int] = {
            "verified-facts": 0,
            "reference-facts": 0,
            "lead-signal-only": 0,
            "market-hint-only": 0,
            "blocked": 0,
        }
        signals: list[str] = []
        normalized_items: list[dict[str, Any]] = []
        stronger_support = 0
        stronger_conflict = 0
        weaker_support = 0
        weaker_conflict = 0
        stronger_values: set[str] = set()
        stronger_conflicting_values: set[str] = set()

        for raw_item in list(evidence_items or []):
            item = dict(raw_item or {})
            role = self.normalize_corroboration_evidence_role(item.get("evidence_role") or "")
            role_counts[role] = role_counts.get(role, 0) + 1
            raw_value = str(
                item.get("claim_value")
                or item.get("fact_value")
                or item.get("value")
                or ""
            ).strip()
            supports_claim = item.get("supports_claim")
            if supports_claim is None:
                if resolved_candidate and raw_value:
                    supports = raw_value == resolved_candidate
                else:
                    supports = not bool(item.get("conflicts_claim"))
            else:
                supports = bool(supports_claim)
            if bool(item.get("conflicts_claim")):
                supports = False
            if role == "blocked":
                stance = "blocked"
            elif supports:
                stance = "support"
            else:
                stance = "conflict"
            is_stronger = role in {"verified-facts", "reference-facts"}
            if is_stronger:
                if stance == "support":
                    stronger_support += 1
                    if raw_value:
                        stronger_values.add(raw_value)
                elif stance == "conflict":
                    stronger_conflict += 1
                    if raw_value:
                        stronger_conflicting_values.add(raw_value)
            elif role in {"lead-signal-only", "market-hint-only"}:
                if stance == "support":
                    weaker_support += 1
                elif stance == "conflict":
                    weaker_conflict += 1
            normalized_items.append(
                {
                    "source_id": str(item.get("source_id") or "").strip().lower(),
                    "evidence_role": role,
                    "claim_value": raw_value,
                    "stance": stance,
                }
            )

        usable_count = sum(
            1
            for item in normalized_items
            if str(item.get("evidence_role") or "") != "blocked"
        )
        verified_support = any(
            item["evidence_role"] == "verified-facts" and item["stance"] == "support"
            for item in normalized_items
        )
        reference_support = any(
            item["evidence_role"] == "reference-facts" and item["stance"] == "support"
            for item in normalized_items
        )
        stronger_conflict_detected = stronger_conflict > 0 or len(stronger_values) > 1
        weaker_conflict_detected = weaker_conflict > 0
        weaker_only = weaker_support > 0 and not verified_support and not reference_support

        if usable_count == 0:
            outcome = "unusable_evidence"
            signals.append("no_usable_evidence")
        elif stronger_conflict_detected:
            outcome = "conflicting_evidence"
            signals.append("stronger_source_conflict")
        elif verified_support:
            outcome = "verified_ready"
            signals.append("verified_source_support")
        elif reference_support:
            outcome = "corroborated_reference_only"
            signals.append("reference_only_support")
        elif weaker_only:
            if role_counts.get("lead-signal-only", 0) > 0:
                outcome = "insufficient_support"
                signals.append("lead_only_insufficient")
            else:
                outcome = "unusable_evidence"
                signals.append("market_hint_only_unusable")
        else:
            outcome = "insufficient_support"
            signals.append("insufficient_support")

        if verified_support and (
            role_counts.get("reference-facts", 0) > 0 or weaker_support > 0 or weaker_conflict > 0
        ):
            signals.append("stronger_source_hierarchy")
        elif reference_support and (weaker_support > 0 or weaker_conflict > 0):
            signals.append("reference_source_hierarchy")
        if weaker_conflict_detected and outcome in {"verified_ready", "corroborated_reference_only"}:
            signals.append("weaker_conflict_not_authoritative")
        elif weaker_conflict_detected:
            signals.append("weaker_conflict")
        if role_counts.get("blocked", 0) > 0:
            signals.append("blocked_evidence_present")

        stronger_summary = "verified" if verified_support else ("reference" if reference_support else "none")
        if outcome == "verified_ready":
            summary = "Verified-fact support is strong enough for future verified use."
        elif outcome == "corroborated_reference_only":
            summary = "Reference-level corroboration exists, but stronger official support is still absent."
        elif outcome == "conflicting_evidence":
            summary = "Stronger evidence conflicts, so the fact must remain unresolved."
        elif outcome == "unusable_evidence":
            summary = "Available evidence is unusable for verified fact support."
        else:
            summary = "Evidence remains insufficient for verified fact use."

        return {
            "support_outcome": outcome,
            "candidate_value": resolved_candidate,
            "evidence_mix": role_counts,
            "stronger_source_support": verified_support or reference_support,
            "stronger_source_level": stronger_summary,
            "verified_source_support": verified_support,
            "conflict_detected": stronger_conflict_detected or weaker_conflict_detected,
            "stronger_conflict_detected": stronger_conflict_detected,
            "classification_signals": self._compact_signal_list(signals, limit=6),
            "reasoning_summary": summary,
            "supporting_evidence_count": stronger_support + weaker_support,
            "conflicting_evidence_count": stronger_conflict + weaker_conflict,
            "normalized_evidence": normalized_items,
            "verified_fact_ready": outcome == "verified_ready",
            "reference_only_corroborated": outcome == "corroborated_reference_only",
        }

    def upsert_fact_corroboration_record(
        self,
        *,
        fact_key: str,
        fact_type: str = "",
        corroboration: dict[str, Any],
        acceptance_outcome: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> int:
        resolved_fact_key = str(fact_key or "").strip()
        if not resolved_fact_key:
            return 0
        now = utc_timestamp()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO learning_fact_corroboration_records (
                    fact_key,
                    fact_type,
                    support_outcome,
                    acceptance_outcome,
                    evidence_mix_json,
                    stronger_source_support,
                    stronger_source_level,
                    conflict_detected,
                    classification_signals_json,
                    reasoning_summary,
                    provenance_json,
                    reviewed_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_key, fact_type) DO UPDATE SET
                    support_outcome = excluded.support_outcome,
                    acceptance_outcome = excluded.acceptance_outcome,
                    evidence_mix_json = excluded.evidence_mix_json,
                    stronger_source_support = excluded.stronger_source_support,
                    stronger_source_level = excluded.stronger_source_level,
                    conflict_detected = excluded.conflict_detected,
                    classification_signals_json = excluded.classification_signals_json,
                    reasoning_summary = excluded.reasoning_summary,
                    provenance_json = excluded.provenance_json,
                    reviewed_at = excluded.reviewed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_fact_key,
                    str(fact_type or "").strip(),
                    str(corroboration.get("support_outcome") or "insufficient_support"),
                    str(acceptance_outcome or "insufficient_support"),
                    json.dumps(dict(corroboration.get("evidence_mix") or {}), ensure_ascii=False, sort_keys=True),
                    1 if bool(corroboration.get("stronger_source_support")) else 0,
                    str(corroboration.get("stronger_source_level") or "none"),
                    1 if bool(corroboration.get("conflict_detected")) else 0,
                    json.dumps(
                        list(corroboration.get("classification_signals") or []),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    str(corroboration.get("reasoning_summary") or "").strip(),
                    json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def fetch_fact_corroboration_record(
        self,
        *,
        fact_key: str,
        fact_type: str = "",
    ) -> dict[str, Any] | None:
        resolved_fact_key = str(fact_key or "").strip()
        if not resolved_fact_key:
            return None
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_fact_corroboration_records
                WHERE fact_key = ? AND fact_type = ?
                LIMIT 1
                """,
                (resolved_fact_key, str(fact_type or "").strip()),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["evidence_mix"] = json.loads(result.get("evidence_mix_json") or "{}")
        result["classification_signals"] = json.loads(result.get("classification_signals_json") or "[]")
        result["provenance"] = json.loads(result.get("provenance_json") or "{}")
        return result

    def upsert_accepted_fact_provenance(
        self,
        *,
        card_code: str,
        fact_key: str,
        fact_type: str,
        accepted_value: str,
        acceptance_outcome: str,
        corroboration_record_id: int = 0,
        corroboration_record: dict[str, Any] | None = None,
        source_context: dict[str, Any] | None = None,
        stored_in_dossier: bool = True,
    ) -> int:
        resolved_fact_key = str(fact_key or "").strip()
        if not resolved_fact_key:
            return 0
        resolved_corroboration = dict(corroboration_record or {})
        now = utc_timestamp()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO learning_accepted_fact_provenance (
                    card_code,
                    fact_key,
                    fact_type,
                    accepted_value,
                    acceptance_outcome,
                    support_outcome,
                    corroboration_record_id,
                    evidence_mix_json,
                    classification_signals_json,
                    reasoning_summary,
                    source_context_json,
                    stored_in_dossier,
                    accepted_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_key, fact_type) DO UPDATE SET
                    card_code = excluded.card_code,
                    accepted_value = excluded.accepted_value,
                    acceptance_outcome = excluded.acceptance_outcome,
                    support_outcome = excluded.support_outcome,
                    corroboration_record_id = excluded.corroboration_record_id,
                    evidence_mix_json = excluded.evidence_mix_json,
                    classification_signals_json = excluded.classification_signals_json,
                    reasoning_summary = excluded.reasoning_summary,
                    source_context_json = excluded.source_context_json,
                    stored_in_dossier = excluded.stored_in_dossier,
                    accepted_at = excluded.accepted_at,
                    updated_at = excluded.updated_at
                """,
                (
                    str(card_code or "").strip().upper(),
                    resolved_fact_key,
                    str(fact_type or "").strip(),
                    str(accepted_value or "").strip(),
                    str(acceptance_outcome or "accept_verified_candidate"),
                    str(resolved_corroboration.get("support_outcome") or "verified_ready"),
                    int(corroboration_record_id or 0),
                    json.dumps(dict(resolved_corroboration.get("evidence_mix") or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(
                        list(resolved_corroboration.get("classification_signals") or []),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    str(resolved_corroboration.get("reasoning_summary") or "").strip(),
                    json.dumps(dict(source_context or {}), ensure_ascii=False, sort_keys=True),
                    1 if stored_in_dossier else 0,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def fetch_accepted_fact_provenance(
        self,
        *,
        fact_key: str,
        fact_type: str = "",
    ) -> dict[str, Any] | None:
        resolved_fact_key = str(fact_key or "").strip()
        if not resolved_fact_key:
            return None
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_accepted_fact_provenance
                WHERE fact_key = ? AND fact_type = ?
                LIMIT 1
                """,
                (resolved_fact_key, str(fact_type or "").strip()),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["evidence_mix"] = json.loads(result.get("evidence_mix_json") or "{}")
        result["classification_signals"] = json.loads(result.get("classification_signals_json") or "[]")
        result["source_context"] = json.loads(result.get("source_context_json") or "{}")
        return result

    def list_accepted_fact_provenance(
        self,
        *,
        card_code: str,
    ) -> list[dict[str, Any]]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return []
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_accepted_fact_provenance
                WHERE card_code = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (resolved_card_code,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["evidence_mix"] = json.loads(item.get("evidence_mix_json") or "{}")
            item["classification_signals"] = json.loads(item.get("classification_signals_json") or "[]")
            item["source_context"] = json.loads(item.get("source_context_json") or "{}")
            results.append(item)
        return results

    def build_dossier_fact_projection(
        self,
        *,
        provenance: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        resolved_history = list(history or [])
        latest_event = dict(resolved_history[-1] or {}) if resolved_history else {}
        latest_event_type = str(latest_event.get("event_type") or "")
        evidence_mix = dict(provenance.get("evidence_mix") or {})
        verified_count = int(evidence_mix.get("verified-facts") or 0)
        reference_count = int(evidence_mix.get("reference-facts") or 0)
        support_outcome = str(provenance.get("support_outcome") or "")
        acceptance_outcome = str(provenance.get("acceptance_outcome") or "")
        source_context = dict(provenance.get("source_context") or {})

        if latest_event_type == "superseded":
            fact_status = "superseded"
        elif support_outcome == "verified_ready" and verified_count >= 2:
            fact_status = "corroborated"
        elif acceptance_outcome == "accept_verified_candidate":
            fact_status = "accepted"
        else:
            fact_status = "tentative"

        confidence = 0.48
        confidence += min(verified_count, 3) * 0.12
        confidence += min(reference_count, 2) * 0.05
        if support_outcome == "verified_ready":
            confidence += 0.12
        elif support_outcome == "corroborated_reference_only":
            confidence += 0.04
        if latest_event_type == "reconfirmed":
            confidence += 0.04
        if latest_event_type in {"conflict_flagged", "unresolved_preserved"}:
            confidence -= 0.18
        if latest_event_type == "superseded":
            confidence -= 0.1
        confidence = round(max(min(confidence, 0.98), 0.2), 2)

        source_ids = self._compact_signal_list(
            [
                str(source_context.get("source_id") or "").strip().lower(),
                str(source_context.get("incoming_source_id") or "").strip().lower(),
            ],
            limit=4,
        )
        primary_source_id = source_ids[0] if source_ids else str(source_context.get("source_id") or "").strip().lower()
        return {
            "fact_status": fact_status,
            "confidence": confidence,
            "primary_source_id": primary_source_id,
            "source_ids": source_ids,
            "support_outcome": support_outcome,
            "latest_event_type": latest_event_type or "accepted",
            "source_context": source_context,
        }

    @staticmethod
    def build_usage_evidence_key(
        *,
        source_id: str = "",
        source_reference: str = "",
        event_key: str = "",
        decklist_key: str = "",
        card_code: str = "",
        leader_code: str = "",
        archetype_label: str = "",
        role_classification: str = "",
    ) -> str:
        return json.dumps(
            {
                "source_id": str(source_id or "").strip().lower(),
                "source_reference": str(source_reference or "").strip(),
                "event_key": str(event_key or "").strip(),
                "decklist_key": str(decklist_key or "").strip(),
                "card_code": str(card_code or "").strip().upper(),
                "leader_code": str(leader_code or "").strip().upper(),
                "archetype_label": str(archetype_label or "").strip(),
                "role_classification": str(role_classification or "").strip().lower(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def record_usage_evidence(
        self,
        *,
        source_id: str,
        source_type: str = "",
        source_url: str = "",
        source_reference: str = "",
        event_key: str = "",
        event_name: str = "",
        placement: int = 0,
        decklist_key: str = "",
        card_code: str,
        leader_code: str = "",
        leader_name: str = "",
        archetype_label: str = "",
        role_classification: str = "",
        appearance_count: int = 1,
        confidence_input: float = 0.0,
        observed_at: str = "",
        citation_payload: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return {"stored": False, "reason": "missing-card-code"}
        evidence_key = self.build_usage_evidence_key(
            source_id=source_id,
            source_reference=source_reference,
            event_key=event_key,
            decklist_key=decklist_key,
            card_code=resolved_card_code,
            leader_code=leader_code,
            archetype_label=archetype_label,
            role_classification=role_classification,
        )
        now = utc_timestamp()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_usage_evidence (
                    evidence_key,
                    source_id,
                    source_type,
                    source_url,
                    source_reference,
                    event_key,
                    event_name,
                    placement,
                    decklist_key,
                    card_code,
                    leader_code,
                    leader_name,
                    archetype_label,
                    role_classification,
                    appearance_count,
                    confidence_input,
                    observed_at,
                    citation_payload_json,
                    provenance_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(evidence_key) DO UPDATE SET
                    source_type = excluded.source_type,
                    source_url = excluded.source_url,
                    event_name = excluded.event_name,
                    placement = excluded.placement,
                    leader_name = excluded.leader_name,
                    appearance_count = excluded.appearance_count,
                    confidence_input = excluded.confidence_input,
                    observed_at = excluded.observed_at,
                    citation_payload_json = excluded.citation_payload_json,
                    provenance_json = excluded.provenance_json,
                    updated_at = excluded.updated_at
                """,
                (
                    evidence_key,
                    str(source_id or "").strip().lower(),
                    str(source_type or "").strip().lower(),
                    str(source_url or "").strip(),
                    str(source_reference or "").strip(),
                    str(event_key or "").strip(),
                    str(event_name or "").strip(),
                    max(int(placement or 0), 0),
                    str(decklist_key or "").strip(),
                    resolved_card_code,
                    str(leader_code or "").strip().upper(),
                    str(leader_name or "").strip(),
                    str(archetype_label or "").strip(),
                    str(role_classification or "").strip().lower(),
                    max(int(appearance_count or 0), 1),
                    round(max(min(float(confidence_input or 0.0), 1.0), 0.0), 3),
                    str(observed_at or now).strip(),
                    json.dumps(dict(citation_payload or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            if str(event_key or "").strip() or str(event_name or "").strip():
                conn.execute(
                    """
                    INSERT INTO learning_tournament_placements (
                        event_key,
                        event_name,
                        placement,
                        archetype_key,
                        deck_label,
                        source_summary,
                        event_date,
                        updated_at
                    )
                    SELECT ?, ?, ?, ?, ?, ?, ?, ?
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM learning_tournament_placements
                        WHERE event_key = ?
                          AND deck_label = ?
                          AND archetype_key = ?
                          AND source_summary = ?
                    )
                    """,
                    (
                        str(event_key or "").strip(),
                        str(event_name or "").strip(),
                        max(int(placement or 0), 0),
                        str(archetype_label or "").strip(),
                        str(leader_name or leader_code or "").strip(),
                        str(source_id or "").strip().lower(),
                        str(observed_at or "").strip(),
                        now,
                        str(event_key or "").strip(),
                        str(leader_name or leader_code or "").strip(),
                        str(archetype_label or "").strip(),
                        str(source_id or "").strip().lower(),
                    ),
                )
        return {
            "stored": True,
            "evidence_key": evidence_key,
            "card_code": resolved_card_code,
        }

    def fetch_usage_evidence(
        self,
        *,
        card_code: str,
    ) -> list[dict[str, Any]]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return []
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_usage_evidence
                WHERE card_code = ?
                ORDER BY observed_at DESC, id DESC
                """,
                (resolved_card_code,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["citation_payload"] = json.loads(item.get("citation_payload_json") or "{}")
            item["provenance"] = json.loads(item.get("provenance_json") or "{}")
            results.append(item)
        return results

    def rebuild_card_usage_intelligence(
        self,
        *,
        card_code: str,
    ) -> dict[str, Any]:
        resolved_card_code = str(card_code or "").strip().upper()
        evidence_rows = self.fetch_usage_evidence(card_code=resolved_card_code)
        if not evidence_rows:
            return {"stored": False, "reason": "no-usage-evidence"}
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in evidence_rows:
            group_key = (
                str(row.get("leader_code") or "").strip().upper(),
                str(row.get("archetype_label") or "").strip(),
                str(row.get("role_classification") or "").strip().lower(),
            )
            grouped.setdefault(group_key, []).append(row)

        support_totals: dict[tuple[str, str, str], int] = {}
        for key, rows in grouped.items():
            support_totals[key] = sum(max(int(row.get("appearance_count") or 0), 1) for row in rows)
        sorted_groups = sorted(
            support_totals.items(),
            key=lambda item: (item[1], len({str(r.get("source_id") or "").strip().lower() for r in grouped[item[0]]})),
            reverse=True,
        )
        dominant_support = int(sorted_groups[0][1] or 0) if sorted_groups else 0
        competitor_support = int(sorted_groups[1][1] or 0) if len(sorted_groups) > 1 else 0

        results: list[dict[str, Any]] = []
        for (leader_code, archetype_label, role_classification), rows in grouped.items():
            source_ids = self._compact_signal_list(
                [str(row.get("source_id") or "").strip().lower() for row in rows],
                limit=8,
            )
            support_count = sum(max(int(row.get("appearance_count") or 0), 1) for row in rows)
            evidence_record_count = len(rows)
            source_count = len(source_ids)
            confidence_inputs = [float(row.get("confidence_input") or 0.0) for row in rows]
            observed_times = [
                self._parse_utc_timestamp(str(row.get("observed_at") or ""))
                for row in rows
            ]
            observed_times = [item for item in observed_times if item is not None]
            latest_seen = max(observed_times) if observed_times else None
            age_days = 0
            if latest_seen is not None:
                age_days = max(int((datetime.now(timezone.utc) - latest_seen).total_seconds() // 86400), 0)
            stale_evidence = age_days >= 180
            one_source_only = source_count <= 1
            conflicting_usage_signals = (
                dominant_support > 0
                and support_count < dominant_support
                and (dominant_support - support_count) <= 1
                and competitor_support > 0
            )
            confidence = 0.34
            confidence += min(support_count, 5) * 0.08
            confidence += min(source_count, 3) * 0.07
            confidence += min(evidence_record_count, 4) * 0.03
            if confidence_inputs:
                confidence += sum(confidence_inputs) / len(confidence_inputs) * 0.18
            if role_classification == "core":
                confidence += 0.04
            elif role_classification == "staple":
                confidence += 0.03
            if one_source_only:
                confidence -= 0.12
            if stale_evidence:
                confidence -= 0.18
            if conflicting_usage_signals:
                confidence -= 0.14
            confidence = round(max(min(confidence, 0.96), 0.12), 2)
            if stale_evidence:
                fact_status = "tentative"
            elif support_count >= 4 and source_count >= 2 and not conflicting_usage_signals:
                fact_status = "corroborated"
            elif support_count >= 1:
                fact_status = "accepted"
            else:
                fact_status = "tentative"
            top_row = rows[0]
            latest_seen_text = latest_seen.strftime("%Y-%m-%d %H:%M:%S") if latest_seen is not None else ""
            provenance = {
                "evidence_record_count": evidence_record_count,
                "source_count": source_count,
                "source_ids": source_ids,
                "one_source_only": one_source_only,
                "stale_evidence": stale_evidence,
                "age_days": age_days,
                "conflicting_usage_signals": conflicting_usage_signals,
                "latest_seen_at": latest_seen_text,
                "event_keys": self._compact_signal_list([str(row.get("event_key") or "").strip() for row in rows], limit=6),
                "decklist_keys": self._compact_signal_list([str(row.get("decklist_key") or "").strip() for row in rows], limit=6),
                "coverage_type": str((top_row.get("provenance") or {}).get("coverage_type") or "").strip(),
            }
            stored = self.store_card_usage_intelligence(
                card_code=resolved_card_code,
                leader_code=leader_code,
                leader_name=str(top_row.get("leader_name") or "").strip(),
                archetype_label=archetype_label,
                role_classification=role_classification,
                support_count=support_count,
                confidence=confidence,
                source_id=str(top_row.get("source_id") or "").strip().lower(),
                source_reference=str(top_row.get("source_reference") or "").strip(),
                source_url=str(top_row.get("source_url") or "").strip(),
                freshness_at=latest_seen_text,
                fact_status=fact_status,
                provenance=provenance,
            )
            results.append(stored)
        return {
            "stored": bool(results),
            "card_code": resolved_card_code,
            "usage_groups": len(results),
            "groups": results,
        }

    def ingest_decklist_usage_evidence(
        self,
        *,
        source_id: str,
        source_type: str = "",
        source_url: str = "",
        source_reference: str = "",
        event_key: str = "",
        event_name: str = "",
        placement: int = 0,
        decklist_key: str = "",
        card_code: str,
        leader_code: str = "",
        leader_name: str = "",
        archetype_label: str = "",
        role_classification: str = "",
        appearance_count: int = 1,
        confidence_input: float = 0.0,
        observed_at: str = "",
        citation_payload: dict[str, Any] | None = None,
        provenance: dict[str, Any] | None = None,
        public_data_only: bool | None = True,
        requires_login: bool | None = False,
        respect_site_policies: bool | None = True,
    ) -> dict[str, Any]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return {"stored": False, "reason": "missing-card-code"}
        validation = self.validate_learning_reference(card_code=resolved_card_code)
        if not validation["ok"]:
            return {"stored": False, "reason": str(validation.get("reason") or "invalid-card-reference")}
        governance = self.evaluate_source_execution_gate(
            source_id=source_id,
            execution_kind="reference-safe",
            source_type=source_type,
            source_url=source_url,
            notes="decklist tournament usage evidence",
            public_data_only=public_data_only,
            requires_login=requires_login,
            respect_site_policies=respect_site_policies,
        )
        if not governance["proceed"]:
            return {
                "stored": False,
                "reason": str(governance.get("reason") or "governance-blocked"),
                "governance": governance,
            }
        evidence_result = self.record_usage_evidence(
            source_id=source_id,
            source_type=source_type,
            source_url=source_url,
            source_reference=source_reference,
            event_key=event_key,
            event_name=event_name,
            placement=placement,
            decklist_key=decklist_key,
            card_code=resolved_card_code,
            leader_code=leader_code,
            leader_name=leader_name,
            archetype_label=archetype_label,
            role_classification=role_classification,
            appearance_count=appearance_count,
            confidence_input=confidence_input,
            observed_at=observed_at,
            citation_payload=citation_payload,
            provenance={
                **dict(provenance or {}),
                "governance_evidence_role": str(governance.get("policy_evidence_role") or ""),
                "source_reviewed": bool(governance.get("source_reviewed")),
            },
        )
        aggregate_result = self.rebuild_card_usage_intelligence(card_code=resolved_card_code)
        verified_sync = self.sync_card_to_verified_dossier_store(card_code=resolved_card_code)
        return {
            "stored": bool(evidence_result.get("stored")) and bool(aggregate_result.get("stored")),
            "card_code": resolved_card_code,
            "usage_evidence": evidence_result,
            "usage_aggregate": aggregate_result,
            "verified_dossier_sync": verified_sync,
            "governance": governance,
        }

    def store_card_usage_intelligence(
        self,
        *,
        card_code: str,
        leader_code: str = "",
        leader_name: str = "",
        archetype_label: str = "",
        role_classification: str = "",
        support_count: int = 0,
        confidence: float = 0.0,
        source_id: str = "",
        source_reference: str = "",
        source_url: str = "",
        freshness_at: str = "",
        fact_status: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return {"stored": False, "reason": "missing-card-code"}
        resolved_leader_code = str(leader_code or "").strip().upper()
        resolved_role = str(role_classification or "").strip().lower()
        resolved_support = max(int(support_count or 0), 0)
        resolved_confidence = round(max(min(float(confidence or 0.0), 0.98), 0.0), 2)
        resolved_status = str(fact_status or "").strip().lower()
        if not resolved_status:
            if resolved_confidence >= 0.82 and resolved_support >= 3:
                resolved_status = "corroborated"
            elif resolved_confidence >= 0.55 or resolved_support >= 1:
                resolved_status = "accepted"
            else:
                resolved_status = "tentative"
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_card_usage (
                    card_code,
                    leader_code,
                    leader_name,
                    archetype_key,
                    role_classification,
                    usage_frequency,
                    sample_size,
                    confidence_label,
                    fact_status,
                    source_id,
                    source_reference,
                    source_url,
                    freshness_at,
                    provenance_json,
                    source_summary,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, leader_code, archetype_key, role_classification) DO UPDATE SET
                    leader_name = excluded.leader_name,
                    usage_frequency = excluded.usage_frequency,
                    sample_size = excluded.sample_size,
                    confidence_label = excluded.confidence_label,
                    fact_status = excluded.fact_status,
                    source_id = excluded.source_id,
                    source_reference = excluded.source_reference,
                    source_url = excluded.source_url,
                    freshness_at = excluded.freshness_at,
                    provenance_json = excluded.provenance_json,
                    source_summary = excluded.source_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved_card_code,
                    resolved_leader_code,
                    str(leader_name or "").strip(),
                    str(archetype_label or "").strip(),
                    resolved_role,
                    resolved_confidence,
                    resolved_support,
                    self.verified_dossier_store.confidence_label(resolved_confidence),
                    resolved_status,
                    str(source_id or "").strip().lower(),
                    str(source_reference or "").strip(),
                    str(source_url or "").strip(),
                    str(freshness_at or "").strip(),
                    json.dumps(dict(provenance or {}), ensure_ascii=False, sort_keys=True),
                    ", ".join(
                        part
                        for part in (
                            str(leader_name or "").strip(),
                            str(archetype_label or "").strip(),
                            str(source_id or "").strip().lower(),
                        )
                        if part
                    ),
                    utc_timestamp(),
                ),
            )
        return {
            "stored": True,
            "card_code": resolved_card_code,
            "leader_code": resolved_leader_code,
            "archetype_label": str(archetype_label or "").strip(),
            "role_classification": resolved_role,
            "status": resolved_status,
            "confidence": resolved_confidence,
        }

    def fetch_learning_card_usage(
        self,
        *,
        card_code: str,
    ) -> list[dict[str, Any]]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return []
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_card_usage
                WHERE card_code = ?
                ORDER BY usage_frequency DESC, sample_size DESC, archetype_key ASC, role_classification ASC
                """,
                (resolved_card_code,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["provenance"] = json.loads(item.get("provenance_json") or "{}")
            results.append(item)
        return results

    def fetch_learning_card_usage_by_leader(
        self,
        *,
        leader_code: str,
    ) -> list[dict[str, Any]]:
        resolved_leader_code = str(leader_code or "").strip().upper()
        if not resolved_leader_code:
            return []
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_card_usage
                WHERE leader_code = ?
                ORDER BY usage_frequency DESC, sample_size DESC, card_code ASC
                """,
                (resolved_leader_code,),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["provenance"] = json.loads(item.get("provenance_json") or "{}")
            results.append(item)
        return results

    def rebuild_leader_intelligence(
        self,
        *,
        leader_code: str,
    ) -> dict[str, Any]:
        resolved_leader_code = str(leader_code or "").strip().upper()
        if not resolved_leader_code:
            return {"stored": False, "reason": "missing-leader-code"}
        rows = self.fetch_learning_card_usage_by_leader(leader_code=resolved_leader_code)
        if not rows:
            return {"stored": False, "reason": "no-leader-usage"}

        leader_name = ""
        archetype_labels: list[str] = []
        source_ids: set[str] = set()
        latest_seen: datetime | None = None
        role_counts = {"core": 0, "flex": 0, "tech": 0, "staple": 0}
        total_support = 0
        role_conflict_cards = 0
        cards_by_role: dict[str, list[dict[str, Any]]] = {}

        for row in rows:
            if not leader_name:
                leader_name = str(row.get("leader_name") or "").strip()
            archetype = str(row.get("archetype_key") or "").strip()
            if archetype and archetype not in archetype_labels:
                archetype_labels.append(archetype)
            role = str(row.get("role_classification") or "").strip().lower()
            if role in role_counts:
                role_counts[role] += 1
            total_support += int(row.get("sample_size") or 0)
            provenance = dict(row.get("provenance") or {})
            source_ids.update(str(item or "").strip().lower() for item in provenance.get("source_ids") or [])
            if not source_ids and str(row.get("source_id") or "").strip():
                source_ids.add(str(row.get("source_id") or "").strip().lower())
            seen_at = self._parse_utc_timestamp(str(row.get("freshness_at") or ""))
            if seen_at is not None and (latest_seen is None or seen_at > latest_seen):
                latest_seen = seen_at
            cards_by_role.setdefault(str(row.get("card_code") or "").strip().upper(), []).append(row)

        for card_rows in cards_by_role.values():
            if len(card_rows) < 2:
                continue
            sorted_rows = sorted(card_rows, key=lambda item: float(item.get("usage_frequency") or 0.0), reverse=True)
            if abs(float(sorted_rows[0].get("usage_frequency") or 0.0) - float(sorted_rows[1].get("usage_frequency") or 0.0)) <= 0.08:
                role_conflict_cards += 1

        linked_card_count = len(rows)
        source_count = len(source_ids)
        average_confidence = (
            sum(float(row.get("usage_frequency") or 0.0) for row in rows) / len(rows)
            if rows else 0.0
        )
        age_days = 0
        if latest_seen is not None:
            age_days = max(int((datetime.now(timezone.utc) - latest_seen).total_seconds() // 86400), 0)
        stale_evidence = age_days >= 180
        one_source_only = source_count <= 1

        confidence = 0.32
        confidence += min(linked_card_count, 12) * 0.03
        confidence += min(total_support, 24) * 0.015
        confidence += min(source_count, 3) * 0.08
        confidence += average_confidence * 0.18
        if stale_evidence:
            confidence -= 0.18
        if one_source_only:
            confidence -= 0.12
        if role_conflict_cards:
            confidence -= min(role_conflict_cards, 3) * 0.06
        confidence = round(max(min(confidence, 0.96), 0.1), 2)

        if stale_evidence:
            evidence_posture = "stale_leader_evidence"
            caution_note = "Miru's current leader pattern evidence looks stale, so it should not present the pattern as current certainty."
            reassurance_note = "It can still keep the historical picture available while waiting for fresher verified list coverage."
        elif role_conflict_cards:
            evidence_posture = "partial_leader_pattern"
            caution_note = "Several nearby role labels are still competing for this leader, so Miru should avoid overcalling exact core or flex lines."
            reassurance_note = "It can keep the leader summary narrow until stronger list coverage separates those roles more clearly."
        elif confidence >= 0.8 and total_support >= 8 and source_count >= 2:
            evidence_posture = "verified_leader_pattern"
            caution_note = ""
            reassurance_note = "Miru can describe this leader pattern from stored verified usage evidence without stretching into speculation."
        elif linked_card_count >= 1:
            evidence_posture = "partial_leader_pattern"
            caution_note = "Leader-pattern evidence exists, but coverage is still limited."
            if one_source_only:
                caution_note = "Leader-pattern evidence currently comes from a narrow source slice, so Miru should keep the summary provisional."
            reassurance_note = "It can still point to the current pattern carefully without overstating the leader's full structure."
        else:
            evidence_posture = "incomplete_leader_evidence"
            caution_note = "Leader-pattern evidence is still too thin for a strong structure claim."
            reassurance_note = "Miru can stay cautious and wait for more verified list coverage."

        freshness_at = latest_seen.strftime("%Y-%m-%d %H:%M:%S") if latest_seen is not None else ""
        provenance = {
            "source_ids": sorted(source_ids),
            "role_conflict_cards": role_conflict_cards,
            "one_source_only": one_source_only,
            "stale_evidence": stale_evidence,
            "age_days": age_days,
            "caution_note": caution_note,
            "reassurance_note": reassurance_note,
        }
        self.verified_dossier_store.upsert_leader_intelligence(
            leader_code=resolved_leader_code,
            leader_name=leader_name,
            archetype_labels=sorted(archetype_labels),
            confidence=confidence,
            evidence_posture=evidence_posture,
            support_count=total_support,
            linked_card_count=linked_card_count,
            source_count=source_count,
            role_counts=role_counts,
            freshness_at=freshness_at,
            citation_payload={"source_ids": sorted(source_ids)},
            provenance=provenance,
        )
        return {
            "stored": True,
            "leader_code": resolved_leader_code,
            "evidence_posture": evidence_posture,
            "linked_card_count": linked_card_count,
        }

    def store_card_strategy_intel(
        self,
        *,
        card_code: str,
        leader_code: str = "",
        leader_name: str = "",
        archetype_label: str = "",
        role_classification: str = "",
        support_count: int = 0,
        source_count: int = 0,
        confidence: float = 0.0,
        freshness_at: str = "",
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store structured strategy reasoning derived from verified usage evidence.

        Applies sparse-evidence failsafe: weak or single-source evidence never
        produces a strong strategy claim.  The caller supplies observed usage
        signals; this method derives the posture, rationale, and synergy tags
        from those signals conservatively.
        """
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return {"stored": False, "reason": "missing-card-code"}
        resolved_leader_code = str(leader_code or "").strip().upper()
        resolved_role = str(role_classification or "").strip().lower()
        resolved_confidence = round(max(min(float(confidence or 0.0), 0.98), 0.0), 4)
        resolved_support = max(int(support_count or 0), 0)
        resolved_sources = max(int(source_count or 0), 0)
        prov = dict(provenance or {})

        stale_evidence = bool(prov.get("stale_evidence"))
        one_source_only = resolved_sources <= 1
        age_days = int(prov.get("age_days") or 0)

        # --- Sparse-evidence failsafe ---
        # Posture never rises to verified unless all minimum thresholds are met.
        # This prevents overclaiming from a single deck reference or thin evidence.
        if stale_evidence or age_days >= 180:
            evidence_posture = "stale_strategy_evidence"
            caution_note = "Strategy evidence is stale; Miru should not present this as a current meta claim."
        elif resolved_support < 3 or resolved_sources < 2:
            evidence_posture = "incomplete_strategy_evidence"
            caution_note = (
                "Too few verified records to make a strong strategy claim. "
                "Miru should keep any strategy description clearly provisional."
            )
        elif bool(prov.get("conflicting_roles")):
            evidence_posture = "partial_strategy_pattern"
            caution_note = "Role evidence is split across multiple classifications; Miru should avoid overcalling a single strategy line."
        elif resolved_confidence >= 0.75 and resolved_support >= 5 and resolved_sources >= 2:
            evidence_posture = "verified_strategy_pattern"
            caution_note = ""
        elif resolved_confidence >= 0.50 or resolved_support >= 2:
            evidence_posture = "partial_strategy_pattern"
            caution_note = (
                "Strategy evidence exists, but coverage is still limited."
                if not one_source_only
                else "Strategy evidence currently comes from a narrow source slice; Miru should keep the pattern provisional."
            )
        else:
            evidence_posture = "incomplete_strategy_evidence"
            caution_note = "Strategy evidence is still too thin for a strong role or synergy claim."

        # Derive lightweight role_purpose from role classification
        role_purpose_map = {
            "core": f"Consistently appears as a key piece in verified {archetype_label or 'this archetype'} coverage.",
            "staple": f"Broadly included across verified {archetype_label or 'this archetype'} lists without exception.",
            "flex": f"Conditionally included based on matchup read in {archetype_label or 'this archetype'}.",
            "tech": f"Situational choice in {archetype_label or 'this archetype'} tuned for specific match conditions.",
        }
        role_purpose = role_purpose_map.get(resolved_role, "")

        # Derive synergy tags from archetype label tokens
        archetype_tokens = [
            t.strip().lower()
            for t in (archetype_label or "").replace("-", " ").split()
            if t.strip() and len(t.strip()) > 2
        ]
        synergy_tags: list[str] = []
        if resolved_role in {"core", "staple"}:
            synergy_tags.append("consistency")
        if resolved_role in {"flex", "tech"}:
            synergy_tags.append("flexibility")
        if archetype_tokens:
            synergy_tags.extend(archetype_tokens[:3])

        # Game-plan relevance label
        if evidence_posture == "verified_strategy_pattern":
            game_plan_relevance = f"Verified {resolved_role or 'support'} role in {archetype_label or 'known archetype'}."
        elif evidence_posture == "partial_strategy_pattern":
            game_plan_relevance = f"Partial {resolved_role or 'support'} role evidence available; coverage still growing."
        else:
            game_plan_relevance = ""

        strategy_rationale = (
            f"Based on {resolved_support} verified usage record(s) across {resolved_sources} source(s)"
            + (f" for {leader_name}" if leader_name else "")
            + (f" / {archetype_label}" if archetype_label else "")
            + "."
        ) if resolved_support > 0 else ""

        self.verified_dossier_store.upsert_card_strategy_intel(
            card_code=resolved_card_code,
            leader_code=resolved_leader_code,
            role_label=resolved_role,
            role_purpose=role_purpose,
            synergy_tags=synergy_tags,
            game_plan_relevance=game_plan_relevance,
            strategy_rationale=strategy_rationale,
            evidence_posture=evidence_posture,
            confidence=resolved_confidence,
            support_count=resolved_support,
            source_count=resolved_sources,
            freshness_at=str(freshness_at or "").strip(),
            provenance={
                **prov,
                "caution_note": caution_note,
                "leader_name": leader_name,
                "archetype_label": archetype_label,
            },
        )
        return {
            "stored": True,
            "card_code": resolved_card_code,
            "leader_code": resolved_leader_code,
            "role_label": resolved_role,
            "evidence_posture": evidence_posture,
        }

    # ------------------------------------------------------------------
    # Phase 12 – Rulings Intelligence (worker-side ingestion)
    # ------------------------------------------------------------------

    def store_card_ruling_intel(
        self,
        *,
        card_code: str,
        ruling_text: str,
        ruling_topic: str = "",
        interaction_context: str = "",
        source_id: str,
        source_reference: str = "",
        source_url: str = "",
        confidence: float = 0.0,
        freshness_at: str = "",
        provenance: dict[str, Any] | None = None,
        source_count: int = 1,
    ) -> dict[str, Any]:
        """Store a verified ruling record derived from an authoritative source.

        Governance checks (worker-side, before any write):
        - Ruling text must be non-empty.
        - Source ID must be provided.
        - Confidence must be positive.
        - Ruling text must meet a minimum length to prevent stub entries.

        Failsafe posture derivation:
        - Single source → at most partial_ruling_evidence
        - Stale (>365 days) → stale_ruling_evidence
        - Low confidence (<0.55) → incomplete_ruling_evidence
        - Strong, multi-source, fresh → verified_ruling

        Returns a result dict indicating whether the record was stored and why.
        """
        resolved_card = str(card_code or "").strip().upper()
        cleaned_text = str(ruling_text or "").strip()
        cleaned_source = str(source_id or "").strip()

        # Governance: mandatory fields
        if not resolved_card:
            return {"stored": False, "reason": "missing_card_code"}
        if not cleaned_text or len(cleaned_text) < 10:
            return {"stored": False, "reason": "ruling_text_too_short_or_missing"}
        if not cleaned_source:
            return {"stored": False, "reason": "missing_source_id"}
        if confidence <= 0.0:
            return {"stored": False, "reason": "non_positive_confidence"}

        # Governance: reject obviously unofficial sources
        disallowed_prefixes = ("anon", "unknown", "unverified", "speculation")
        if any(cleaned_source.startswith(p) for p in disallowed_prefixes):
            return {"stored": False, "reason": "disallowed_source_prefix"}

        # Compute ruling key: deterministic from card + topic + source for idempotency
        import hashlib as _hashlib
        topic_slug = "".join(c for c in (ruling_topic or "general").lower() if c.isalnum() or c == "_")[:32]
        raw_key = f"{resolved_card}::{topic_slug}::{cleaned_source}"
        ruling_key = _hashlib.md5(raw_key.encode()).hexdigest()[:16]  # noqa: S324 (non-security use)

        # Compute staleness (days old)
        import re as _re
        days_old = 0
        if freshness_at:
            date_match = _re.match(r"(\d{4}-\d{2}-\d{2})", str(freshness_at))
            if date_match:
                from datetime import date as _date
                try:
                    parsed = _date.fromisoformat(date_match.group(1))
                    days_old = (self._today_date() - parsed).days
                except ValueError:
                    days_old = 0

        # Derive posture and confidence label via shared static helper
        evidence_posture, confidence_label = MiruDossierStore._derive_ruling_posture_and_label(
            confidence=confidence,
            source_count=source_count,
            days_old=days_old,
        )

        self.verified_dossier_store.upsert_card_ruling_intel(
            card_code=resolved_card,
            ruling_key=ruling_key,
            ruling_text=cleaned_text,
            ruling_topic=str(ruling_topic or "").strip(),
            interaction_context=str(interaction_context or "").strip(),
            source_id=cleaned_source,
            source_reference=str(source_reference or "").strip(),
            source_url=str(source_url or "").strip(),
            confidence=float(confidence),
            evidence_posture=evidence_posture,
            confidence_label=confidence_label,
            freshness_at=str(freshness_at or "").strip(),
            provenance=provenance or {},
        )

        return {
            "stored": True,
            "card_code": resolved_card,
            "ruling_key": ruling_key,
            "evidence_posture": evidence_posture,
            "confidence_label": confidence_label,
            "days_old": days_old,
        }

    def _today_date(self):
        """Return today's date (UTC) as a datetime.date; isolated for testability."""
        from datetime import datetime as _dt, timezone as _tz
        return _dt.now(tz=_tz.utc).date()

    @staticmethod
    def _compute_recency_score(age_days: int) -> float:
        """Compute a recency score in [0, 1] with aggressive penalty for stale evidence.

        Failsafe: evidence older than 90 days receives a strong penalty so that
        stale patterns cannot masquerade as current meta signals.
        """
        if age_days <= 0:
            return 1.0
        if age_days <= 14:
            return round(1.0 - age_days * 0.01, 4)
        if age_days <= 45:
            return round(0.85 - (age_days - 14) * 0.012, 4)
        if age_days <= 90:
            return round(0.48 - (age_days - 45) * 0.007, 4)
        # >90 days: recency penalty kicks in hard (failsafe)
        if age_days <= 180:
            return round(max(0.17 - (age_days - 90) * 0.0015, 0.05), 4)
        return 0.02

    @staticmethod
    def _derive_meta_posture_and_trend(
        *,
        support_count: int,
        source_count: int,
        confidence: float,
        recency_score: float,
        age_days: int,
        evidence_window_days: int,
        leader_count: int = 0,
        archetype_count: int = 0,
        one_source_only: bool = False,
        stale_evidence: bool = False,
    ) -> tuple[str, str, str]:
        """Return (meta_posture, trend_label, caution_note).

        Failsafe rules enforced before posture can be raised:
        - stale (>180 days or explicit flag) → stale_meta_evidence
        - age > 90 days → at most partial_meta_evidence
        - support < 3 or source < 2 → at most incomplete_meta_evidence
        - one_source_only → at most partial_meta_evidence
        - narrow scope (leader_count<=1, archetype_count<=1) → at most partial_meta_evidence
        """
        if stale_evidence or age_days >= 180:
            posture = "stale_meta_evidence"
            trend = "stale"
            caution = "Meta evidence is stale and should not be presented as reflecting the current environment."
            return posture, trend, caution

        # Sparse-evidence failsafe
        if support_count < 3 or source_count < 2:
            posture = "incomplete_meta_evidence"
            trend = "limited" if support_count > 0 else "unknown"
            caution = (
                "Too few verified records for a meaningful meta claim. "
                "Miru should keep any meta description clearly provisional."
            )
            return posture, trend, caution

        # Age-based ceiling: >90 days caps at partial even if other signals look good
        age_ceiling = age_days > 90

        # One-source-only ceiling
        if one_source_only:
            posture = "partial_meta_evidence"
            trend = "limited"
            caution = "Meta evidence comes from a single source only; Miru should keep the pattern provisional."
            return posture, trend, caution

        # Narrow-scope ceiling
        narrow_scope = (leader_count <= 1 and archetype_count <= 1) and (leader_count + archetype_count > 0)
        if narrow_scope:
            posture = "partial_meta_evidence"
            trend = "limited"
            caution = "Meta evidence is narrow in scope (single leader / archetype); broader coverage is needed before a pattern claim."
            return posture, trend, caution

        if age_ceiling:
            posture = "partial_meta_evidence"
            trend = "limited"
            caution = "Evidence is more than 90 days old; Miru should describe this pattern as historical rather than current."
            return posture, trend, caution

        # Rising vs stable determination
        if recency_score >= 0.70 and evidence_window_days <= 45:
            trend = "rising"
        elif recency_score >= 0.40:
            trend = "stable"
        else:
            trend = "limited"

        # Posture elevation
        if confidence >= 0.75 and support_count >= 5 and source_count >= 2 and recency_score >= 0.40:
            if evidence_window_days <= 45 and recency_score >= 0.70:
                posture = "emerging_meta_pattern"
                caution = "This pattern is still recent; coverage may not yet reflect full meta adoption."
            else:
                posture = "verified_meta_pattern"
                caution = ""
        elif confidence >= 0.55 or support_count >= 3:
            posture = "partial_meta_evidence"
            caution = "Meta evidence exists, but coverage is still limited in scope or source diversity."
        else:
            posture = "incomplete_meta_evidence"
            trend = "limited"
            caution = "Meta evidence is still too thin for any strong pattern claim."

        return posture, trend, caution

    def rebuild_card_meta_intel(
        self,
        *,
        card_code: str,
    ) -> dict[str, Any]:
        """Aggregate card-level meta intelligence from verified usage records.

        Reads learning_card_usage for the card, derives recency, scope, and
        trend signals, then writes a structured meta record to the verified
        dossier store.  Heavy work stays worker-side; the stored record is
        lightweight to read.
        """
        resolved_code = str(card_code or "").strip().upper()
        if not resolved_code:
            return {"stored": False, "reason": "missing-card-code"}
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT card_code, leader_code, archetype_key, role_classification,
                       usage_frequency, sample_size, source_id, freshness_at, provenance_json
                FROM learning_card_usage
                WHERE card_code = ?
                ORDER BY usage_frequency DESC, sample_size DESC
                """,
                (resolved_code,),
            ).fetchall()
        if not rows:
            return {"stored": False, "reason": "no-card-usage-records"}

        total_support = 0
        source_ids: set[str] = set()
        leader_codes: set[str] = set()
        archetype_keys: set[str] = set()
        observed_times: list[datetime] = []
        confidence_inputs: list[float] = []

        for row in rows:
            total_support += max(int(row["sample_size"] or 0), 0)
            sid = str(row["source_id"] or "").strip().lower()
            if sid:
                source_ids.add(sid)
            # provenance_json may contain a richer source_ids list from rebuild aggregation
            prov = json.loads(row["provenance_json"] or "{}")
            for extra_sid in list(prov.get("source_ids") or []):
                cleaned = str(extra_sid or "").strip().lower()
                if cleaned:
                    source_ids.add(cleaned)
            lc = str(row["leader_code"] or "").strip().upper()
            if lc:
                leader_codes.add(lc)
            ak = str(row["archetype_key"] or "").strip().lower()
            if ak:
                archetype_keys.add(ak)
            ts = self._parse_utc_timestamp(str(row["freshness_at"] or ""))
            if ts is not None:
                observed_times.append(ts)
            confidence_inputs.append(float(row["usage_frequency"] or 0.0))

        if not observed_times:
            return {"stored": False, "reason": "no-freshness-timestamps"}

        latest_seen = max(observed_times)
        earliest_seen = min(observed_times)
        now_utc = datetime.now(timezone.utc)
        age_days = max(int((now_utc - latest_seen).total_seconds() // 86400), 0)
        evidence_window_days = max(int((latest_seen - earliest_seen).total_seconds() // 86400), 0)
        stale_evidence = age_days >= 180
        one_source_only = len(source_ids) <= 1
        source_count = len(source_ids)
        leader_count = len(leader_codes)
        archetype_count = len(archetype_keys)
        average_confidence = sum(confidence_inputs) / len(confidence_inputs) if confidence_inputs else 0.0

        confidence = 0.28
        confidence += min(total_support, 8) * 0.06
        confidence += min(source_count, 4) * 0.06
        confidence += min(leader_count, 3) * 0.04
        confidence += average_confidence * 0.20
        if one_source_only:
            confidence -= 0.12
        if stale_evidence:
            confidence -= 0.20
        elif age_days > 90:
            confidence -= 0.10
        confidence = round(max(min(confidence, 0.96), 0.08), 2)

        recency_score = self._compute_recency_score(age_days)
        meta_posture, trend_label, caution_note = self._derive_meta_posture_and_trend(
            support_count=total_support,
            source_count=source_count,
            confidence=confidence,
            recency_score=recency_score,
            age_days=age_days,
            evidence_window_days=evidence_window_days,
            leader_count=leader_count,
            archetype_count=archetype_count,
            one_source_only=one_source_only,
            stale_evidence=stale_evidence,
        )

        freshness_text = latest_seen.strftime("%Y-%m-%d %H:%M:%S")
        first_seen_text = earliest_seen.strftime("%Y-%m-%d %H:%M:%S")

        self.verified_dossier_store.upsert_card_meta_intel(
            card_code=resolved_code,
            trend_label=trend_label,
            meta_posture=meta_posture,
            confidence=confidence,
            support_count=total_support,
            source_count=source_count,
            leader_count=leader_count,
            archetype_count=archetype_count,
            recency_score=recency_score,
            first_seen_at=first_seen_text,
            freshness_at=freshness_text,
            evidence_window_days=evidence_window_days,
            provenance={
                "caution_note": caution_note,
                "age_days": age_days,
                "stale_evidence": stale_evidence,
                "one_source_only": one_source_only,
                "source_ids": sorted(source_ids),
                "leader_codes": sorted(leader_codes),
                "archetype_keys": sorted(archetype_keys),
            },
        )
        return {
            "stored": True,
            "card_code": resolved_code,
            "meta_posture": meta_posture,
            "trend_label": trend_label,
            "confidence": confidence,
        }

    def rebuild_leader_meta_intel(
        self,
        *,
        leader_code: str,
    ) -> dict[str, Any]:
        """Aggregate leader-level meta intelligence from verified usage records.

        Reads learning_card_usage for the leader, combines with stored
        leader_intelligence posture, then writes a structured leader meta record.
        """
        resolved_leader = str(leader_code or "").strip().upper()
        if not resolved_leader:
            return {"stored": False, "reason": "missing-leader-code"}

        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT card_code, leader_code, leader_name, archetype_key,
                       role_classification, usage_frequency, sample_size,
                       source_id, freshness_at, provenance_json
                FROM learning_card_usage
                WHERE leader_code = ?
                ORDER BY usage_frequency DESC, sample_size DESC
                """,
                (resolved_leader,),
            ).fetchall()
        if not rows:
            return {"stored": False, "reason": "no-leader-usage-records"}

        leader_name = ""
        total_support = 0
        source_ids: set[str] = set()
        archetype_keys: set[str] = set()
        card_codes: set[str] = set()
        observed_times: list[datetime] = []
        confidence_inputs: list[float] = []

        for row in rows:
            if not leader_name:
                leader_name = str(row["leader_name"] or "").strip()
            total_support += max(int(row["sample_size"] or 0), 0)
            sid = str(row["source_id"] or "").strip().lower()
            if sid:
                source_ids.add(sid)
            # provenance_json may contain a richer source_ids list
            prov = json.loads(row["provenance_json"] or "{}")
            for extra_sid in list(prov.get("source_ids") or []):
                cleaned = str(extra_sid or "").strip().lower()
                if cleaned:
                    source_ids.add(cleaned)
            ak = str(row["archetype_key"] or "").strip().lower()
            if ak:
                archetype_keys.add(ak)
            cc = str(row["card_code"] or "").strip().upper()
            if cc:
                card_codes.add(cc)
            ts = self._parse_utc_timestamp(str(row["freshness_at"] or ""))
            if ts is not None:
                observed_times.append(ts)
            confidence_inputs.append(float(row["usage_frequency"] or 0.0))

        if not observed_times:
            return {"stored": False, "reason": "no-freshness-timestamps"}

        latest_seen = max(observed_times)
        earliest_seen = min(observed_times)
        now_utc = datetime.now(timezone.utc)
        age_days = max(int((now_utc - latest_seen).total_seconds() // 86400), 0)
        evidence_window_days = max(int((latest_seen - earliest_seen).total_seconds() // 86400), 0)
        stale_evidence = age_days >= 180
        one_source_only = len(source_ids) <= 1
        source_count = len(source_ids)
        archetype_count = len(archetype_keys)
        linked_card_count = len(card_codes)
        average_confidence = sum(confidence_inputs) / len(confidence_inputs) if confidence_inputs else 0.0

        confidence = 0.30
        confidence += min(linked_card_count, 12) * 0.03
        confidence += min(total_support, 20) * 0.015
        confidence += min(source_count, 4) * 0.07
        confidence += average_confidence * 0.20
        if one_source_only:
            confidence -= 0.12
        if stale_evidence:
            confidence -= 0.20
        elif age_days > 90:
            confidence -= 0.10
        confidence = round(max(min(confidence, 0.96), 0.08), 2)

        recency_score = self._compute_recency_score(age_days)
        # For leaders: archetype_count replaces archetype_count in scope check,
        # leader_count is not applicable (this IS the leader record).
        meta_posture, trend_label, caution_note = self._derive_meta_posture_and_trend(
            support_count=total_support,
            source_count=source_count,
            confidence=confidence,
            recency_score=recency_score,
            age_days=age_days,
            evidence_window_days=evidence_window_days,
            leader_count=2,  # always >= 1 in leader context, use 2 to disable narrow-scope check
            archetype_count=archetype_count,
            one_source_only=one_source_only,
            stale_evidence=stale_evidence,
        )

        freshness_text = latest_seen.strftime("%Y-%m-%d %H:%M:%S")
        first_seen_text = earliest_seen.strftime("%Y-%m-%d %H:%M:%S")

        self.verified_dossier_store.upsert_leader_meta_intel(
            leader_code=resolved_leader,
            leader_name=leader_name,
            trend_label=trend_label,
            meta_posture=meta_posture,
            confidence=confidence,
            support_count=total_support,
            source_count=source_count,
            linked_card_count=linked_card_count,
            archetype_count=archetype_count,
            recency_score=recency_score,
            first_seen_at=first_seen_text,
            freshness_at=freshness_text,
            evidence_window_days=evidence_window_days,
            provenance={
                "caution_note": caution_note,
                "age_days": age_days,
                "stale_evidence": stale_evidence,
                "one_source_only": one_source_only,
                "source_ids": sorted(source_ids),
                "archetype_keys": sorted(archetype_keys),
            },
        )
        return {
            "stored": True,
            "leader_code": resolved_leader,
            "meta_posture": meta_posture,
            "trend_label": trend_label,
            "confidence": confidence,
        }

    # ------------------------------------------------------------------
    # Phase 13 – Synergy Intelligence (worker-side derivation)
    # ------------------------------------------------------------------

    def rebuild_card_synergy_intel(
        self,
        *,
        card_code: str,
        min_co_frequency: float = 0.15,
        min_support: int = 2,
    ) -> dict[str, Any]:
        """Derive synergy relationships for a card from verified co-appearance evidence.

        Strategy:
        - Query learning_card_usage for all (leader, archetype) contexts where card_code appears
        - For each such context, find other cards with usage_frequency >= min_co_frequency
        - Each such card is a synergy candidate (recurring_pair relationship)
        - Derive confidence from the geometric mean of usage frequencies of both cards
        - Apply posture failsafes: single-source, thin support, staleness

        Never infers synergy from card text. All relationships must be backed by
        verified co-appearance in the same leader+archetype context.

        Returns a summary dict with count of synergy records written.
        """
        resolved_card = str(card_code or "").strip().upper()
        if not resolved_card:
            return {"stored": False, "reason": "missing_card_code"}

        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            # Find all (leader, archetype, frequency, source_id, freshness) rows for this card
            card_rows = conn.execute(
                """
                SELECT leader_code, archetype_key, usage_frequency, sample_size,
                       source_id, freshness_at, provenance_json
                FROM learning_card_usage
                WHERE card_code = ?
                  AND usage_frequency >= ?
                  AND leader_code != ''
                """,
                (resolved_card, min_co_frequency),
            ).fetchall()

            if not card_rows:
                return {"stored": False, "reason": "no_qualified_usage_rows"}

            # Build a context map: (leader_code, archetype_key) → metadata
            context_map: dict[tuple[str, str], dict[str, Any]] = {}
            for row in card_rows:
                lc = str(row["leader_code"] or "").strip().upper()
                ak = str(row["archetype_key"] or "").strip().lower()
                key = (lc, ak)
                entry = context_map.setdefault(key, {
                    "leader_code": lc,
                    "archetype_key": ak,
                    "my_frequency": 0.0,
                    "source_ids": set(),
                    "sample_sizes": [],
                    "freshness_list": [],
                })
                freq = float(row["usage_frequency"] or 0.0)
                if freq > entry["my_frequency"]:
                    entry["my_frequency"] = freq
                sid = str(row["source_id"] or "").strip().lower()
                if sid:
                    entry["source_ids"].add(sid)
                # Also collect from provenance_json
                prov = json.loads(row["provenance_json"] or "{}")
                for extra in list(prov.get("source_ids") or []):
                    cleaned = str(extra or "").strip().lower()
                    if cleaned:
                        entry["source_ids"].add(cleaned)
                ss = int(row["sample_size"] or 0)
                if ss > 0:
                    entry["sample_sizes"].append(ss)
                fa = str(row["freshness_at"] or "").strip()
                if fa:
                    entry["freshness_list"].append(fa)

            if not context_map:
                return {"stored": False, "reason": "empty_context_map"}

            # For each context, find co-appearing cards
            stored_count = 0
            for ctx_key, ctx_meta in context_map.items():
                lc, ak = ctx_key
                my_freq = ctx_meta["my_frequency"]
                my_sources = ctx_meta["source_ids"]
                my_support = sum(ctx_meta["sample_sizes"])
                freshness_at = max(ctx_meta["freshness_list"], default="")

                # Compute staleness for this context
                days_old = 0
                if freshness_at:
                    import re as _re
                    date_match = _re.match(r"(\d{4}-\d{2}-\d{2})", freshness_at)
                    if date_match:
                        try:
                            parsed = self._today_date().__class__.fromisoformat(date_match.group(1))
                            days_old = (self._today_date() - parsed).days
                        except ValueError:
                            days_old = 0

                # Find partner cards in same context
                partner_rows = conn.execute(
                    """
                    SELECT card_code, leader_name, usage_frequency, sample_size, source_id, provenance_json
                    FROM learning_card_usage
                    WHERE leader_code = ?
                      AND archetype_key = ?
                      AND card_code != ?
                      AND usage_frequency >= ?
                    ORDER BY usage_frequency DESC
                    LIMIT 20
                    """,
                    (lc, ak, resolved_card, min_co_frequency),
                ).fetchall()

                for p_row in partner_rows:
                    partner_code = str(p_row["card_code"] or "").strip().upper()
                    if not partner_code or partner_code == resolved_card:
                        continue

                    partner_freq = float(p_row["usage_frequency"] or 0.0)
                    partner_support = int(p_row["sample_size"] or 0)
                    partner_sid = str(p_row["source_id"] or "").strip().lower()
                    p_prov = json.loads(p_row["provenance_json"] or "{}")

                    # Collect combined source_ids
                    combined_sources: set[str] = set(my_sources)
                    if partner_sid:
                        combined_sources.add(partner_sid)
                    for extra in list(p_prov.get("source_ids") or []):
                        cleaned = str(extra or "").strip().lower()
                        if cleaned:
                            combined_sources.add(cleaned)

                    combined_support = my_support + partner_support
                    source_count = len(combined_sources)

                    # Skip if below min support
                    if combined_support < min_support:
                        continue

                    # Confidence: geometric mean of both usage frequencies, adjusted by breadth
                    import math as _math
                    raw_confidence = _math.sqrt(my_freq * partner_freq)
                    breadth_bonus = min(source_count, 4) * 0.05
                    confidence = round(min(raw_confidence + breadth_bonus, 0.95), 3)

                    evidence_posture, confidence_label = MiruDossierStore._derive_synergy_posture_and_label(
                        confidence=confidence,
                        source_count=source_count,
                        support_count=combined_support,
                        days_old=days_old,
                    )

                    # Relationship summary
                    leader_name_raw = str(p_row["leader_name"] or "").strip()
                    archetype_display = ak.replace("_", " ").title() if ak else ""
                    relationship_summary = (
                        f"Both cards appear frequently in {archetype_display or lc} "
                        f"decks ({source_count} source(s), {combined_support} obs.)."
                        if combined_support else ""
                    )

                    # Write canonical pair (lower code first for dedup) AND card's own record
                    self.verified_dossier_store.upsert_card_synergy_intel(
                        card_code=resolved_card,
                        related_card_code=partner_code,
                        leader_code=lc,
                        archetype_label=ak,
                        relationship_type="recurring_pair",
                        support_count=combined_support,
                        source_count=source_count,
                        confidence=confidence,
                        confidence_label=confidence_label,
                        evidence_posture=evidence_posture,
                        freshness_at=freshness_at,
                        provenance={
                            "source_ids": sorted(combined_sources),
                            "my_frequency": my_freq,
                            "partner_frequency": partner_freq,
                            "days_old": days_old,
                        },
                        relationship_summary=relationship_summary,
                    )
                    stored_count += 1

        return {
            "stored": stored_count > 0,
            "card_code": resolved_card,
            "synergy_records_written": stored_count,
        }

    def sync_card_to_verified_dossier_store(
        self,
        *,
        card_code: str,
        source_record: NormalizedSourceRecord | None = None,
        merged_dossier: dict[str, Any] | None = None,
        acceptance: dict[str, Any] | None = None,
        source_rollup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return {"stored": False, "reason": "missing-card-code"}
        dossier = dict(merged_dossier or self.fetch_dossier(resolved_card_code) or {})
        if not dossier:
            return {"stored": False, "reason": "missing-learning-dossier"}
        basic_facts = dict(dossier.get("basic_facts") or {})
        accepted_rows = [
            item
            for item in self.list_accepted_fact_provenance(card_code=resolved_card_code)
            if bool(item.get("stored_in_dossier"))
        ]
        if not accepted_rows and acceptance and not list(acceptance.get("accepted_fields") or []):
            return {"stored": False, "reason": "no-accepted-facts"}

        resolved_rollup = dict(source_rollup or self.summarize_dossier_sources(resolved_card_code))
        source_rows = self.fetch_dossier_source_records(resolved_card_code)
        for item in source_rows:
            payload = dict(item.get("payload") or {})
            self.verified_dossier_store.upsert_card_source(
                card_code=resolved_card_code,
                source_id=str(item.get("source_id") or "").strip().lower(),
                source_type=str(payload.get("source_type") or "source-record").strip(),
                source_url=str(payload.get("source_url") or "").strip(),
                source_reference=str(item.get("source_reference") or "").strip(),
                fetched_at=str(item.get("fetched_at") or "").strip(),
                trust_level="official" if str(item.get("source_id") or "").startswith("official") else "source-backed",
                trust_score=0.95 if str(item.get("source_id") or "").startswith("official") else 0.62,
                citation_payload={
                    "source_id": str(item.get("source_id") or "").strip().lower(),
                    "source_reference": str(item.get("source_reference") or "").strip(),
                    "source_url": str(payload.get("source_url") or "").strip(),
                },
                notes=f"verification_state={str(item.get('verification_state') or '').strip()}",
            )

        highest_confidence = max(
            [float(dossier.get("confidence") or 0.0)]
            + [float(self.build_dossier_fact_projection(provenance=item).get("confidence") or 0.0) for item in accepted_rows]
        )
        self.verified_dossier_store.upsert_card_snapshot(
            card_code=resolved_card_code,
            canonical_code=resolved_card_code,
            facts=basic_facts,
            confidence=highest_confidence,
            verification_state=str(dossier.get("verification_state") or "pending-confirmation"),
            source_summary=str(dossier.get("source_summary") or ""),
        )

        for item in accepted_rows:
            fact_type = str(item.get("fact_type") or "").strip()
            fact_key = str(item.get("fact_key") or "").strip()
            history = self.list_accepted_fact_history(card_code=resolved_card_code, fact_key=fact_key)
            projection = self.build_dossier_fact_projection(provenance=item, history=history)
            self.verified_dossier_store.upsert_card_fact(
                card_code=resolved_card_code,
                fact_key=fact_key,
                fact_type=fact_type,
                fact_value=str(item.get("accepted_value") or ""),
                confidence=float(projection.get("confidence") or 0.0),
                status=str(projection.get("fact_status") or "accepted"),
                verification_state=str(dossier.get("verification_state") or "pending-confirmation"),
                primary_source_id=str(projection.get("primary_source_id") or ""),
                source_ids=list(projection.get("source_ids") or []),
                citation_payload={
                    "source_reference": str((projection.get("source_context") or {}).get("source_reference") or ""),
                    "source_url": str((projection.get("source_context") or {}).get("source_url") or ""),
                },
                provenance={
                    "support_outcome": str(projection.get("support_outcome") or ""),
                    "latest_event_type": str(projection.get("latest_event_type") or ""),
                    "evidence_mix": dict(item.get("evidence_mix") or {}),
                    "source_context": dict(projection.get("source_context") or {}),
                    "updated_at": str(item.get("updated_at") or item.get("accepted_at") or ""),
                },
            )
            if fact_type in {"effect_text", "trigger_text"} and str(item.get("accepted_value") or "").strip():
                self.verified_dossier_store.upsert_card_effect(
                    card_code=resolved_card_code,
                    effect_type=fact_type,
                    effect_text=str(item.get("accepted_value") or "").strip(),
                    confidence=float(projection.get("confidence") or 0.0),
                    primary_source_id=str(projection.get("primary_source_id") or ""),
                    source_reference=str((projection.get("source_context") or {}).get("source_reference") or ""),
                    source_count=max(int(resolved_rollup.get("source_count") or 0), len(list(projection.get("source_ids") or []))),
                    status=str(projection.get("fact_status") or "accepted"),
                    parsed_payload={"support_outcome": str(projection.get("support_outcome") or "")},
                )

        self.verified_dossier_store.upsert_card_variant(
            card_code=resolved_card_code,
            variant_key="",
            variant_label="Base",
            print_label="Base",
            language_code="en",
            confidence=highest_confidence,
            status=str(dossier.get("verification_state") or "pending-confirmation"),
            print_profile={
                "source_count": int(resolved_rollup.get("source_count") or 0),
                "confidence_level": str(resolved_rollup.get("confidence_level") or ""),
            },
        )

        card_name = str(basic_facts.get("card_name") or dossier.get("card_name") or "").strip()
        set_name = str(basic_facts.get("set_name") or "").strip()
        effect_text = str(basic_facts.get("effect_text") or "").strip()
        if card_name:
            summary_text = f"{resolved_card_code} is {card_name}" + (f" from {set_name}" if set_name else "")
            self.verified_dossier_store.upsert_answer_fragment(
                card_code=resolved_card_code,
                fragment_key="core_identity",
                fragment_type="verified_fact",
                answer_text=summary_text + ".",
                confidence_label=self.verified_dossier_store.confidence_label(highest_confidence),
                status=str(dossier.get("verification_state") or "pending-confirmation"),
                provenance={"source_count": int(resolved_rollup.get("source_count") or 0)},
            )
        if effect_text:
            self.verified_dossier_store.upsert_answer_fragment(
                card_code=resolved_card_code,
                fragment_key="gameplay_effect",
                fragment_type="verified_fact",
                answer_text=effect_text,
                confidence_label=self.verified_dossier_store.confidence_label(highest_confidence),
                status=str(dossier.get("verification_state") or "pending-confirmation"),
                provenance={"source_count": int(resolved_rollup.get("source_count") or 0)},
            )
        usage_rows = self.fetch_learning_card_usage(card_code=resolved_card_code)
        for usage_row in usage_rows:
            usage_source_ids = self._compact_signal_list(
                [str(usage_row.get("source_id") or "").strip().lower()],
                limit=4,
            )
            usage_citation = {
                "source_reference": str(usage_row.get("source_reference") or "").strip(),
                "source_url": str(usage_row.get("source_url") or "").strip(),
            }
            usage_provenance = dict(usage_row.get("provenance") or {})
            if str(usage_row.get("source_id") or "").strip():
                self.verified_dossier_store.upsert_card_source(
                    card_code=resolved_card_code,
                    source_id=str(usage_row.get("source_id") or "").strip().lower(),
                    source_type="decklist-usage-evidence",
                    source_url=str(usage_row.get("source_url") or "").strip(),
                    source_reference=str(usage_row.get("source_reference") or "").strip(),
                    fetched_at=str(usage_row.get("freshness_at") or "").strip(),
                    trust_level="official" if str(usage_row.get("source_id") or "").strip().lower().startswith("official") else "trusted",
                    trust_score=0.95 if str(usage_row.get("source_id") or "").strip().lower().startswith("official") else 0.74,
                    citation_payload=usage_citation,
                    notes="usage-intelligence-source",
                )
            self.verified_dossier_store.upsert_card_usage(
                card_code=resolved_card_code,
                leader_code=str(usage_row.get("leader_code") or "").strip().upper(),
                leader_name=str(usage_row.get("leader_name") or "").strip(),
                archetype_label=str(usage_row.get("archetype_key") or "").strip(),
                role_classification=str(usage_row.get("role_classification") or "").strip().lower(),
                support_count=int(usage_row.get("sample_size") or 0),
                confidence=float(usage_row.get("usage_frequency") or 0.0),
                status=str(usage_row.get("fact_status") or "tentative").strip(),
                primary_source_id=str(usage_row.get("source_id") or "").strip().lower(),
                source_ids=usage_source_ids,
                citation_payload=usage_citation,
                provenance=usage_provenance,
                freshness_at=str(usage_row.get("freshness_at") or "").strip(),
            )
            if str(usage_row.get("leader_code") or "").strip():
                self.verified_dossier_store.upsert_leader_link(
                    card_code=resolved_card_code,
                    leader_code=str(usage_row.get("leader_code") or "").strip().upper(),
                    leader_name=str(usage_row.get("leader_name") or "").strip(),
                    archetype_label=str(usage_row.get("archetype_key") or "").strip(),
                    role_classification=str(usage_row.get("role_classification") or "").strip().lower(),
                    support_count=int(usage_row.get("sample_size") or 0),
                    confidence=float(usage_row.get("usage_frequency") or 0.0),
                    status=str(usage_row.get("fact_status") or "tentative").strip(),
                    primary_source_id=str(usage_row.get("source_id") or "").strip().lower(),
                    source_ids=usage_source_ids,
                    citation_payload=usage_citation,
                    provenance=usage_provenance,
                    freshness_at=str(usage_row.get("freshness_at") or "").strip(),
                )
        posture = self.verified_dossier_store.build_answer_posture(resolved_card_code)
        self.verified_dossier_store.upsert_answer_fragment(
            card_code=resolved_card_code,
            fragment_key="confidence_posture",
            fragment_type=str(posture.get("evidence_posture") or "no_evidence_found"),
            answer_text=str(posture.get("reassurance_note") or posture.get("caution_note") or "").strip(),
            confidence_label=str(posture.get("confidence_label") or "no_evidence"),
            status=str(dossier.get("verification_state") or "pending-confirmation"),
            provenance={"caution_note": str(posture.get("caution_note") or "")},
        )
        usage_posture = self.verified_dossier_store.build_usage_posture(resolved_card_code)
        if usage_rows:
            self.verified_dossier_store.upsert_answer_fragment(
                card_code=resolved_card_code,
                fragment_key="usage_posture",
                fragment_type=str(usage_posture.get("evidence_posture") or "no_usage_evidence_found"),
                answer_text=str(
                    usage_posture.get("reassurance_note")
                    or usage_posture.get("caution_note")
                    or ""
                ).strip(),
                confidence_label=str(usage_posture.get("confidence_label") or "no_evidence"),
                status=str(dossier.get("verification_state") or "pending-confirmation"),
                provenance={
                    "leader_code": str(usage_posture.get("leader_code") or ""),
                    "archetype_label": str(usage_posture.get("archetype_label") or ""),
                    "role_classification": str(usage_posture.get("role_classification") or ""),
                },
            )
            for leader_code in {
                str(item.get("leader_code") or "").strip().upper()
                for item in usage_rows
                if str(item.get("leader_code") or "").strip()
            }:
                self.rebuild_leader_intelligence(leader_code=leader_code)
        return {
            "stored": True,
            "card_code": resolved_card_code,
            "fact_count": len(accepted_rows),
            "source_count": int(resolved_rollup.get("source_count") or 0),
            "usage_count": len(usage_rows),
        }

    def backfill_verified_dossier_store(
        self,
        *,
        card_code: str = "",
        limit: int | None = None,
    ) -> dict[str, Any]:
        if str(card_code or "").strip():
            card_codes = [str(card_code or "").strip().upper()]
        else:
            with closing(connect_sqlite(self.dossier_db_path)) as conn:
                query = """
                    SELECT DISTINCT card_code
                    FROM learning_accepted_fact_provenance
                    WHERE stored_in_dossier = 1
                    ORDER BY updated_at DESC, card_code ASC
                """
                if limit is not None:
                    query += f" LIMIT {max(int(limit or 0), 1)}"
                rows = conn.execute(query).fetchall()
            card_codes = [str(row["card_code"] or "").strip().upper() for row in rows if str(row["card_code"] or "").strip()]
        results: list[dict[str, Any]] = []
        for resolved_card_code in card_codes:
            result = self.sync_card_to_verified_dossier_store(card_code=resolved_card_code)
            if result.get("stored"):
                results.append(result)
        return {
            "card_count": len(results),
            "cards": [str(item.get("card_code") or "") for item in results],
        }

    @staticmethod
    def classify_fact_field_sensitivity(fact_type: str) -> str:
        normalized = normalize_variant_key(str(fact_type or ""))
        if normalized in {
            "card_code",
            "card_name",
            "set_code",
            "set_name",
            "rarity",
            "card_type",
            "color",
        }:
            return "core_identity"
        if normalized in {
            "cost",
            "power",
            "life",
            "counter",
            "effect_text",
            "trigger_text",
            "attribute",
            "traits",
        }:
            return "gameplay"
        if normalized in {
            "alt_art",
            "parallel",
            "promo",
            "illustration_variant",
            "illustrator",
        }:
            return "print_treatment"
        return "contextual"

    @staticmethod
    def _corroboration_strength_score(corroboration_record: dict[str, Any] | None) -> int:
        record = dict(corroboration_record or {})
        support_outcome = str(record.get("support_outcome") or "")
        stronger_level = str(record.get("stronger_source_level") or "none")
        evidence_mix = dict(record.get("evidence_mix") or {})
        score = 0
        if support_outcome == "verified_ready":
            score += 4
        elif support_outcome == "corroborated_reference_only":
            score += 2
        if stronger_level == "verified":
            score += 2
        elif stronger_level == "reference":
            score += 1
        score += min(int(evidence_mix.get("verified-facts") or 0), 3)
        score += min(int(evidence_mix.get("reference-facts") or 0), 2)
        return score

    def evaluate_fact_supersession(
        self,
        *,
        current_provenance: dict[str, Any] | None,
        accepted_value: str,
        acceptance_outcome: str,
        corroboration_record: dict[str, Any] | None = None,
        fact_type: str = "",
    ) -> dict[str, Any]:
        resolved_value = str(accepted_value or "").strip()
        field_sensitivity = self.classify_fact_field_sensitivity(fact_type)
        new_strength = self._corroboration_strength_score(corroboration_record)
        if current_provenance is None:
            return {
                "supersession_outcome": "accept_new",
                "event_type": "accepted",
                "change_summary": "stronger_official_support",
                "field_sensitivity": field_sensitivity,
                "acceptance_strength": new_strength,
                "update_latest_snapshot": True,
            }

        current_value = str(current_provenance.get("accepted_value") or "").strip()
        current_support = {
            "support_outcome": str(current_provenance.get("support_outcome") or ""),
            "stronger_source_level": str(
                (current_provenance.get("source_context") or {}).get("stronger_source_level")
                or current_provenance.get("stronger_source_level")
                or "none"
            ),
            "evidence_mix": dict(current_provenance.get("evidence_mix") or {}),
        }
        current_strength = int(
            (current_provenance.get("source_context") or {}).get("acceptance_strength")
            or self._corroboration_strength_score(current_support)
        )
        if resolved_value == current_value:
            return {
                "supersession_outcome": "no_change",
                "event_type": "reconfirmed",
                "change_summary": "reconfirmed_no_material_change",
                "field_sensitivity": field_sensitivity,
                "acceptance_strength": max(new_strength, current_strength),
                "update_latest_snapshot": True,
            }

        required_margin = 2 if field_sensitivity in {"core_identity", "gameplay"} else 1
        if new_strength >= current_strength + required_margin:
            return {
                "supersession_outcome": "supersede_existing",
                "event_type": "superseded",
                "change_summary": "superseded_by_better_corroboration",
                "field_sensitivity": field_sensitivity,
                "acceptance_strength": new_strength,
                "update_latest_snapshot": True,
            }
        if field_sensitivity in {"core_identity", "gameplay"}:
            return {
                "supersession_outcome": "unresolved_preserved",
                "event_type": "unresolved_preserved",
                "change_summary": "unresolved_due_to_conflict",
                "field_sensitivity": field_sensitivity,
                "acceptance_strength": current_strength,
                "update_latest_snapshot": False,
            }
        return {
            "supersession_outcome": "keep_existing",
            "event_type": "conflict_flagged",
            "change_summary": "conflicting_stronger_evidence",
            "field_sensitivity": field_sensitivity,
            "acceptance_strength": current_strength,
            "update_latest_snapshot": False,
        }

    def record_accepted_fact_history(
        self,
        *,
        card_code: str,
        fact_key: str,
        fact_type: str,
        accepted_value: str,
        acceptance_outcome: str,
        support_outcome: str,
        event_type: str,
        change_summary: str,
        corroboration_record_id: int = 0,
        source_context: dict[str, Any] | None = None,
        field_sensitivity: str = "contextual",
        acceptance_strength: int = 0,
    ) -> int:
        now = utc_timestamp()
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO learning_accepted_fact_history (
                    card_code,
                    fact_key,
                    fact_type,
                    accepted_value,
                    acceptance_outcome,
                    support_outcome,
                    event_type,
                    change_summary,
                    corroboration_record_id,
                    source_context_json,
                    field_sensitivity,
                    acceptance_strength,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(card_code or "").strip().upper(),
                    str(fact_key or "").strip(),
                    str(fact_type or "").strip(),
                    str(accepted_value or "").strip(),
                    str(acceptance_outcome or "").strip(),
                    str(support_outcome or "").strip(),
                    str(event_type or "").strip(),
                    str(change_summary or "").strip(),
                    int(corroboration_record_id or 0),
                    json.dumps(dict(source_context or {}), ensure_ascii=False, sort_keys=True),
                    str(field_sensitivity or "contextual"),
                    int(acceptance_strength or 0),
                    now,
                ),
            )
            return int(cursor.lastrowid or 0)

    def list_accepted_fact_history(
        self,
        *,
        card_code: str,
        fact_key: str = "",
    ) -> list[dict[str, Any]]:
        resolved_card_code = str(card_code or "").strip().upper()
        if not resolved_card_code:
            return []
        query = """
            SELECT *
            FROM learning_accepted_fact_history
            WHERE card_code = ?
        """
        params: list[Any] = [resolved_card_code]
        if str(fact_key or "").strip():
            query += " AND fact_key = ?"
            params.append(str(fact_key or "").strip())
        query += " ORDER BY id ASC"
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["source_context"] = json.loads(item.get("source_context_json") or "{}")
            results.append(item)
        return results

    @staticmethod
    def _parse_utc_timestamp(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def evaluate_fact_health(
        self,
        *,
        fact_key: str,
        fact_type: str = "",
        current_provenance: dict[str, Any] | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        provenance = current_provenance or self.fetch_accepted_fact_provenance(
            fact_key=fact_key,
            fact_type=fact_type,
        )
        if provenance is None:
            return {
                "fact_key": str(fact_key or "").strip(),
                "fact_type": str(fact_type or "").strip(),
                "health_status": "needs_recheck",
                "review_priority": 100,
                "field_sensitivity": self.classify_fact_field_sensitivity(fact_type),
                "summary": "Accepted fact snapshot is missing and needs review.",
            }

        resolved_fact_type = str(fact_type or provenance.get("fact_type") or "").strip()
        field_sensitivity = self.classify_fact_field_sensitivity(resolved_fact_type)
        if history is None:
            history = self.list_accepted_fact_history(
                card_code=str(provenance.get("card_code") or ""),
                fact_key=str(fact_key or provenance.get("fact_key") or ""),
            )
        latest_event = dict(history[-1] or {}) if history else {}
        latest_event_type = str(latest_event.get("event_type") or "")
        recent_conflict = any(
            str(item.get("event_type") or "") in {"unresolved_preserved", "conflict_flagged"}
            for item in list(history or [])[-3:]
        )
        recent_supersession = any(
            str(item.get("event_type") or "") == "superseded"
            for item in list(history or [])[-3:]
        )
        last_reconfirmed = None
        for item in reversed(list(history or [])):
            if str(item.get("event_type") or "") == "reconfirmed":
                last_reconfirmed = item
                break
        now = datetime.now(timezone.utc)
        last_updated_at = self._parse_utc_timestamp(str(provenance.get("updated_at") or provenance.get("accepted_at") or ""))
        hours_since_update = (
            round((now - last_updated_at).total_seconds() / 3600.0, 2)
            if last_updated_at is not None else None
        )
        recently_reconfirmed = False
        if last_reconfirmed is not None:
            reconfirmed_at = self._parse_utc_timestamp(str(last_reconfirmed.get("recorded_at") or ""))
            if reconfirmed_at is not None:
                recently_reconfirmed = (now - reconfirmed_at).total_seconds() <= 7 * 24 * 3600

        if latest_event_type == "superseded":
            health_status = "superseded_recently"
            summary = "Fact was superseded recently and should be monitored."
        elif recent_conflict:
            health_status = "unresolved_conflict"
            summary = "Recent conflicting evidence keeps this fact under review."
        elif recently_reconfirmed or latest_event_type == "reconfirmed":
            health_status = "recently_reconfirmed"
            summary = "Fact was recently reconfirmed with no material change."
        elif recent_supersession:
            health_status = "needs_recheck"
            summary = "Recent supersession history suggests conservative recheck."
        elif field_sensitivity in {"core_identity", "gameplay"} and history and len(history) == 1:
            health_status = "needs_recheck"
            summary = "High-sensitivity fact has not yet been reconfirmed."
        else:
            health_status = "stable_verified"
            summary = "Accepted fact is currently stable and verified."

        base_priority = {
            "unresolved_conflict": 95,
            "superseded_recently": 80,
            "needs_recheck": 70,
            "recently_reconfirmed": 25,
            "stable_verified": 10,
        }.get(health_status, 50)
        sensitivity_bonus = {
            "core_identity": 20,
            "gameplay": 15,
            "print_treatment": 8,
            "contextual": 0,
        }.get(field_sensitivity, 0)
        review_priority = min(base_priority + sensitivity_bonus, 100)

        return {
            "fact_key": str(provenance.get("fact_key") or fact_key or "").strip(),
            "fact_type": resolved_fact_type,
            "accepted_value": str(provenance.get("accepted_value") or ""),
            "health_status": health_status,
            "review_priority": review_priority,
            "field_sensitivity": field_sensitivity,
            "acceptance_outcome": str(provenance.get("acceptance_outcome") or ""),
            "support_outcome": str(provenance.get("support_outcome") or ""),
            "latest_event_type": latest_event_type or "accepted",
            "recent_conflict": recent_conflict,
            "recent_supersession": recent_supersession,
            "recently_reconfirmed": recently_reconfirmed,
            "hours_since_update": hours_since_update,
            "summary": summary,
        }

    def build_card_fact_audit_report(
        self,
        *,
        card_code: str,
    ) -> dict[str, Any]:
        items = self.list_accepted_fact_provenance(card_code=card_code)
        accepted_facts: list[dict[str, Any]] = []
        for item in items:
            fact_key = str(item.get("fact_key") or "")
            history = self.list_accepted_fact_history(
                card_code=str(item.get("card_code") or card_code or ""),
                fact_key=fact_key,
            )
            health = self.evaluate_fact_health(
                fact_key=fact_key,
                fact_type=str(item.get("fact_type") or ""),
                current_provenance=item,
                history=history,
            )
            accepted_facts.append(
                {
                    "fact_key": fact_key,
                    "fact_type": str(item.get("fact_type") or ""),
                    "accepted_value": str(item.get("accepted_value") or ""),
                    "health_status": str(health.get("health_status") or ""),
                    "review_priority": int(health.get("review_priority") or 0),
                    "field_sensitivity": str(health.get("field_sensitivity") or ""),
                    "latest_event_type": str(health.get("latest_event_type") or ""),
                    "recent_conflict": bool(health.get("recent_conflict")),
                    "updated_at": str(item.get("updated_at") or item.get("accepted_at") or ""),
                }
            )
        accepted_facts.sort(key=lambda item: (-int(item.get("review_priority") or 0), str(item.get("fact_key") or "")))
        return {
            "card_code": str(card_code or "").strip().upper(),
            "accepted_fact_count": len(accepted_facts),
            "conflict_count": sum(1 for item in accepted_facts if bool(item.get("recent_conflict"))),
            "facts_needing_recheck": sum(
                1
                for item in accepted_facts
                if str(item.get("health_status") or "") in {"needs_recheck", "unresolved_conflict", "superseded_recently"}
            ),
            "accepted_facts": accepted_facts,
        }

    def list_reverification_candidates(
        self,
        *,
        card_code: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if str(card_code or "").strip():
            provenance_rows = self.list_accepted_fact_provenance(card_code=str(card_code or "").strip().upper())
        else:
            with closing(connect_sqlite(self.dossier_db_path)) as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM learning_accepted_fact_provenance
                    ORDER BY updated_at DESC, id DESC
                    """
                ).fetchall()
            provenance_rows = []
            for row in rows:
                item = {key: row[key] for key in row.keys()}
                item["evidence_mix"] = json.loads(item.get("evidence_mix_json") or "{}")
                item["classification_signals"] = json.loads(item.get("classification_signals_json") or "[]")
                item["source_context"] = json.loads(item.get("source_context_json") or "{}")
                provenance_rows.append(item)

        candidates: list[dict[str, Any]] = []
        for item in provenance_rows:
            fact_key = str(item.get("fact_key") or "")
            history = self.list_accepted_fact_history(
                card_code=str(item.get("card_code") or ""),
                fact_key=fact_key,
            )
            health = self.evaluate_fact_health(
                fact_key=fact_key,
                fact_type=str(item.get("fact_type") or ""),
                current_provenance=item,
                history=history,
            )
            if str(health.get("health_status") or "") not in {
                "needs_recheck",
                "unresolved_conflict",
                "superseded_recently",
            }:
                continue
            candidates.append(
                {
                    "card_code": str(item.get("card_code") or ""),
                    "fact_key": fact_key,
                    "fact_type": str(item.get("fact_type") or ""),
                    "accepted_value": str(item.get("accepted_value") or ""),
                    "health_status": str(health.get("health_status") or ""),
                    "review_priority": int(health.get("review_priority") or 0),
                    "field_sensitivity": str(health.get("field_sensitivity") or ""),
                    "latest_event_type": str(health.get("latest_event_type") or ""),
                    "summary": str(health.get("summary") or ""),
                }
            )
        candidates.sort(
            key=lambda item: (
                -int(item.get("review_priority") or 0),
                str(item.get("card_code") or ""),
                str(item.get("fact_key") or ""),
            )
        )
        return candidates[: max(int(limit or 0), 1)]

    def build_fact_review_reason_labels(
        self,
        *,
        current_provenance: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        health: dict[str, Any] | None = None,
    ) -> list[str]:
        provenance = dict(current_provenance or {})
        fact_type = str(provenance.get("fact_type") or "")
        source_context = dict(provenance.get("source_context") or {})
        resolved_history = list(history or [])
        resolved_health = health or self.evaluate_fact_health(
            fact_key=str(provenance.get("fact_key") or ""),
            fact_type=fact_type,
            current_provenance=provenance,
            history=resolved_history,
        )
        labels: list[str] = []
        health_status = str(resolved_health.get("health_status") or "")
        if health_status == "unresolved_conflict":
            labels.append("conflict_present")
        if health_status == "superseded_recently":
            labels.append("recent_supersession")
        if health_status == "needs_recheck":
            if self.classify_fact_field_sensitivity(fact_type) == "core_identity":
                labels.append("core_identity_stale")
            else:
                labels.append("reconfirmation_due")
        if len(resolved_history) >= 3:
            labels.append("unstable_history")
        evidence_mix = dict(provenance.get("evidence_mix") or {})
        if int(evidence_mix.get("reference-facts") or 0) > 0 and int(evidence_mix.get("verified-facts") or 0) <= 1:
            labels.append("weaker_support_mix")
        if bool(source_context.get("new_print")) or bool(source_context.get("variant_attention")):
            labels.append("new_print_or_variant_attention")
        if bool(source_context.get("english_print_pending")) or bool(source_context.get("provisional_language_display")):
            labels.append("english_print_pending")
        if bool(source_context.get("set_freshness_bias")) or int(source_context.get("set_release_recency_days") or 9999) <= 30:
            labels.append("set_freshness_bias")
        return self._compact_signal_list(labels, limit=6)

    def build_reverification_plan(
        self,
        *,
        card_code: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        raw_candidates = self.list_reverification_candidates(card_code=card_code, limit=max(int(limit or 0), 1) * 3)
        planned: list[dict[str, Any]] = []
        for candidate in raw_candidates:
            provenance = self.fetch_accepted_fact_provenance(
                fact_key=str(candidate.get("fact_key") or ""),
                fact_type=str(candidate.get("fact_type") or ""),
            )
            if provenance is None:
                continue
            history = self.list_accepted_fact_history(
                card_code=str(candidate.get("card_code") or ""),
                fact_key=str(candidate.get("fact_key") or ""),
            )
            health = self.evaluate_fact_health(
                fact_key=str(candidate.get("fact_key") or ""),
                fact_type=str(candidate.get("fact_type") or ""),
                current_provenance=provenance,
                history=history,
            )
            reason_labels = self.build_fact_review_reason_labels(
                current_provenance=provenance,
                history=history,
                health=health,
            )
            freshness_bias = 0
            if "english_print_pending" in reason_labels:
                freshness_bias += 12
            if "new_print_or_variant_attention" in reason_labels:
                freshness_bias += 8
            if "set_freshness_bias" in reason_labels:
                freshness_bias += 6
            planned.append(
                {
                    **candidate,
                    "planned_priority": min(int(candidate.get("review_priority") or 0) + freshness_bias, 100),
                    "reason_labels": reason_labels,
                    "freshness_bias": freshness_bias,
                }
            )
        planned.sort(
            key=lambda item: (
                -int(item.get("planned_priority") or 0),
                str(item.get("card_code") or ""),
                str(item.get("fact_key") or ""),
            )
        )
        planned = planned[: max(int(limit or 0), 1)]
        return {
            "card_code": str(card_code or "").strip().upper(),
            "candidate_count": len(planned),
            "candidates": planned,
            "priority_summary": self.summarize_reverification_priority(planned),
        }

    @staticmethod
    def summarize_reverification_priority(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        reason_counts: dict[str, int] = {}
        for item in list(candidates or []):
            for label in list(item.get("reason_labels") or []):
                reason_counts[label] = reason_counts.get(label, 0) + 1
        return {
            "highest_priority": max((int(item.get("planned_priority") or 0) for item in list(candidates or [])), default=0),
            "reason_counts": reason_counts,
        }

    def resolve_fast_fact_lookup(
        self,
        *,
        card_code: str,
        fact_type: str,
        fact_key: str = "",
        allow_reference_only: bool = False,
    ) -> dict[str, Any]:
        resolved_fact_type = str(fact_type or "").strip()
        resolved_fact_key = str(fact_key or f"{resolved_fact_type}:{str(card_code or '').strip().upper()}").strip()
        dossier = self.fetch_dossier(str(card_code or "").strip().upper())
        provenance = self.fetch_accepted_fact_provenance(
            fact_key=resolved_fact_key,
            fact_type=resolved_fact_type,
        )
        if provenance is not None:
            dossier_value = None
            if dossier is not None:
                basic_facts = dict(dossier.get("basic_facts") or {})
                dossier_value = basic_facts.get(resolved_fact_type)
                if dossier_value in (None, "", [], {}) and resolved_fact_type in {"card_name", "set_code", "rarity"}:
                    dossier_value = dossier.get(resolved_fact_type)
            normalized_dossier_value = self._normalize_candidate_fact_value(dossier_value)
            accepted_value = str(provenance.get("accepted_value") or "")
            if normalized_dossier_value and normalized_dossier_value == accepted_value:
                return {
                    "fact_key": resolved_fact_key,
                    "fact_type": resolved_fact_type,
                    "resolution_path": "accepted_verified_dossier_fact",
                    "verified": True,
                    "value": dossier_value,
                    "current_memory_sufficient": True,
                    "requires_fresh_fetch": False,
                }
            return {
                "fact_key": resolved_fact_key,
                "fact_type": resolved_fact_type,
                "resolution_path": "accepted_fact_provenance_snapshot",
                "verified": True,
                "value": accepted_value,
                "current_memory_sufficient": True,
                "requires_fresh_fetch": False,
            }

        history = self.list_accepted_fact_history(
            card_code=str(card_code or "").strip().upper(),
            fact_key=resolved_fact_key,
        )
        if history:
            latest = dict(history[-1] or {})
            return {
                "fact_key": resolved_fact_key,
                "fact_type": resolved_fact_type,
                "resolution_path": "accepted_fact_history_context",
                "verified": False,
                "value": str(latest.get("accepted_value") or ""),
                "current_memory_sufficient": True,
                "requires_fresh_fetch": False,
            }

        corroboration = self.fetch_fact_corroboration_record(
            fact_key=resolved_fact_key,
            fact_type=resolved_fact_type,
        )
        if corroboration is not None:
            acceptance_outcome = str(corroboration.get("acceptance_outcome") or "")
            if acceptance_outcome == "keep_reference_only" and allow_reference_only:
                return {
                    "fact_key": resolved_fact_key,
                    "fact_type": resolved_fact_type,
                    "resolution_path": "reference_only_corroboration_context",
                    "verified": False,
                    "value": str((corroboration.get("provenance") or {}).get("candidate_value") or ""),
                    "current_memory_sufficient": True,
                    "requires_fresh_fetch": False,
                }
            return {
                "fact_key": resolved_fact_key,
                "fact_type": resolved_fact_type,
                "resolution_path": "corroboration_context",
                "verified": False,
                "value": str((corroboration.get("provenance") or {}).get("candidate_value") or ""),
                "current_memory_sufficient": True,
                "requires_fresh_fetch": False,
            }

        reviewed_sources = self.list_reviewed_source_candidates()
        if reviewed_sources:
            return {
                "fact_key": resolved_fact_key,
                "fact_type": resolved_fact_type,
                "resolution_path": "reviewed_source_governance_memory",
                "verified": False,
                "value": "",
                "current_memory_sufficient": False,
                "requires_fresh_fetch": True,
            }
        return {
            "fact_key": resolved_fact_key,
            "fact_type": resolved_fact_type,
            "resolution_path": "fresh_fetch_required_last_resort",
            "verified": False,
            "value": "",
            "current_memory_sufficient": False,
            "requires_fresh_fetch": True,
        }

    def plan_min_cost_fact_resolution(
        self,
        *,
        card_code: str,
        fact_type: str,
        fact_key: str = "",
        allow_reference_only: bool = False,
    ) -> dict[str, Any]:
        route = self.resolve_fast_fact_lookup(
            card_code=card_code,
            fact_type=fact_type,
            fact_key=fact_key,
            allow_reference_only=allow_reference_only,
        )
        return {
            **route,
            "cheapest_safe_path": str(route.get("resolution_path") or ""),
            "verified_answer_available": bool(route.get("verified")),
            "fresh_fetch_only_as_last_resort": bool(route.get("requires_fresh_fetch")),
        }

    @staticmethod
    def _intent_bundle_fields() -> dict[str, list[str]]:
        return {
            "core_identity": [
                "card_name",
                "set_code",
                "set_name",
                "rarity",
                "card_type",
                "color",
            ],
            "gameplay": [
                "cost",
                "power",
                "life",
                "counter",
                "effect_text",
                "trigger_text",
                "attribute",
                "traits",
            ],
            "print_family": [
                "rarity",
                "illustrator",
                "alt_art",
                "parallel",
                "promo",
                "illustration_variant",
            ],
            "set_release": [
                "set_code",
                "set_name",
            ],
            "market_or_meta_context": [
                "market_note",
                "popularity_signal",
                "tournament_signal",
            ],
        }

    @staticmethod
    def _response_voice_profiles() -> dict[str, dict[str, Any]]:
        return {
            "neutral": {
                "mode": "neutral",
                "tone": "clear and factual",
                "style_tags": ["grounded", "direct"],
                "truth_layer_unchanged": True,
            },
            "friendly_guide": {
                "mode": "friendly_guide",
                "tone": "calm local-player guidance",
                "style_tags": ["kind", "quietly_confident", "helpful_to_newer_players"],
                "truth_layer_unchanged": True,
            },
            "concise": {
                "mode": "concise",
                "tone": "compact and efficient",
                "style_tags": ["brief", "grounded"],
                "truth_layer_unchanged": True,
            },
        }

    @staticmethod
    def _flavor_insight_catalog() -> dict[str, dict[str, Any]]:
        return {
            "OP01-001": {
                "card_code": "OP01-001",
                "flavor_connection_light": (
                    "Small design note: this leader's attack-triggered draw reinforces "
                    "Luffy's straightforward momentum-first style."
                ),
                "flavor_connection_full": (
                    "Small story detail: this leader's attack-triggered draw mirrors "
                    "Luffy's keep-moving-forward energy, turning offense into momentum."
                ),
                "spoiler_level": "light",
                "confidence": 0.84,
                "tone_hint": "warm_design_note",
                "required_fields": ["card_name", "effect_text"],
                "allowed_intents": ["card_identity_lookup", "gameplay_effect_lookup"],
            }
        }

    @staticmethod
    def _compact_query_terms(query: str, *, limit: int = 6) -> list[str]:
        normalized = " ".join(str(query or "").strip().lower().replace("?", " ").replace(",", " ").split())
        if not normalized:
            return []
        seen: list[str] = []
        for term in normalized.split():
            if term not in seen:
                seen.append(term)
        return seen[: max(int(limit or 0), 1)]

    @staticmethod
    def _dossier_fact_value(dossier: dict[str, Any] | None, fact_type: str) -> Any:
        if dossier is None:
            return None
        resolved_fact_type = str(fact_type or "").strip()
        basic_facts = dict(dossier.get("basic_facts") or {})
        if resolved_fact_type in basic_facts:
            return basic_facts.get(resolved_fact_type)
        return dossier.get(resolved_fact_type)

    def classify_query_intent(self, query: str) -> dict[str, Any]:
        normalized = " ".join(str(query or "").strip().lower().split())
        if not normalized:
            return {
                "intent": "unknown_needs_reasoning",
                "bundle_type": "core_identity",
                "primary_fact_type": "card_name",
                "query_terms": [],
                "needs_reasoning": True,
            }

        def contains(*terms: str) -> bool:
            return any(term in normalized for term in terms)

        if contains("compare", "difference", "versus", " vs ", "better than"):
            intent = "comparison_question"
            bundle_type = "gameplay" if contains("effect", "trigger", "power", "cost") else "core_identity"
            primary_fact_type = "effect_text" if bundle_type == "gameplay" else "card_name"
        elif contains("meta", "strategy", "combo", "best in", "good in", "deck", "play pattern"):
            intent = "meta_or_strategy_question"
            bundle_type = "market_or_meta_context"
            primary_fact_type = "tournament_signal"
        elif contains("price", "value", "collect", "collector", "grading", "chase"):
            intent = "collector_question"
            bundle_type = "print_family"
            primary_fact_type = "rarity"
        elif contains("parallel", "alt art", "alt-art", "promo", "variant", "illustrator", "rarity", "print"):
            intent = "print_or_rarity_question"
            bundle_type = "print_family"
            if contains("illustrator"):
                primary_fact_type = "illustrator"
            elif contains("rarity"):
                primary_fact_type = "rarity"
            elif contains("promo"):
                primary_fact_type = "promo"
            elif contains("parallel"):
                primary_fact_type = "parallel"
            else:
                primary_fact_type = "illustration_variant"
        elif contains("set", "release", "released", "which set", "set code"):
            intent = "set_question"
            bundle_type = "set_release"
            primary_fact_type = "set_code" if contains("set code") else "set_name"
        elif contains("effect", "trigger", "counter", "power", "cost", "life", "ability", "text", "when attacking"):
            intent = "gameplay_effect_lookup"
            bundle_type = "gameplay"
            if contains("trigger"):
                primary_fact_type = "trigger_text"
            elif contains("power"):
                primary_fact_type = "power"
            elif contains("cost"):
                primary_fact_type = "cost"
            elif contains("life"):
                primary_fact_type = "life"
            elif contains("counter"):
                primary_fact_type = "counter"
            else:
                primary_fact_type = "effect_text"
        elif contains("who is", "what card", "card name", "color", "type", "leader", "character"):
            intent = "card_identity_lookup"
            bundle_type = "core_identity"
            if contains("color"):
                primary_fact_type = "color"
            elif contains("type", "leader", "character"):
                primary_fact_type = "card_type"
            else:
                primary_fact_type = "card_name"
        else:
            intent = "unknown_needs_reasoning"
            bundle_type = "core_identity"
            primary_fact_type = "card_name"

        return {
            "intent": intent,
            "bundle_type": bundle_type,
            "primary_fact_type": primary_fact_type,
            "query_terms": self._compact_query_terms(normalized),
            "needs_reasoning": intent in {"comparison_question", "meta_or_strategy_question", "unknown_needs_reasoning"},
        }

    def load_fact_bundle(
        self,
        *,
        card_code: str,
        query: str = "",
        intent: str = "",
        bundle_type: str = "",
        allow_reference_only: bool = False,
    ) -> dict[str, Any]:
        resolved_card_code = str(card_code or "").strip().upper()
        intent_info = self.classify_query_intent(query) if query else {}
        resolved_intent = str(intent or intent_info.get("intent") or "unknown_needs_reasoning")
        resolved_bundle_type = str(
            bundle_type or intent_info.get("bundle_type") or "core_identity"
        ).strip() or "core_identity"
        bundle_fields = list(self._intent_bundle_fields().get(resolved_bundle_type, self._intent_bundle_fields()["core_identity"]))
        dossier = self.fetch_dossier(resolved_card_code)
        accepted_rows = self.list_accepted_fact_provenance(card_code=resolved_card_code)
        accepted_by_type: dict[str, dict[str, Any]] = {}
        for row in accepted_rows:
            fact_type = str(row.get("fact_type") or "").strip()
            if fact_type and fact_type not in accepted_by_type:
                accepted_by_type[fact_type] = row

        facts: dict[str, dict[str, Any]] = {}
        for fact_type in bundle_fields:
            accepted = accepted_by_type.get(fact_type)
            fact_key = f"{fact_type}:{resolved_card_code}"
            if accepted is not None:
                dossier_value = self._dossier_fact_value(dossier, fact_type)
                normalized_dossier_value = self._normalize_candidate_fact_value(dossier_value)
                accepted_value = str(accepted.get("accepted_value") or "")
                source_path = "accepted_fact_provenance_snapshot"
                value = accepted_value
                if normalized_dossier_value and normalized_dossier_value == accepted_value:
                    source_path = "accepted_verified_dossier_fact"
                    value = dossier_value
                facts[fact_type] = {
                    "fact_key": fact_key,
                    "value": value,
                    "verified": True,
                    "source_path": source_path,
                    "field_sensitivity": self.classify_fact_field_sensitivity(fact_type),
                    "acceptance_outcome": str(accepted.get("acceptance_outcome") or ""),
                    "support_outcome": str(accepted.get("support_outcome") or ""),
                }
                continue

            corroboration = self.fetch_fact_corroboration_record(
                fact_key=fact_key,
                fact_type=fact_type,
            )
            if corroboration is None:
                continue
            acceptance_outcome = str(corroboration.get("acceptance_outcome") or "")
            if acceptance_outcome != "keep_reference_only" or not allow_reference_only:
                continue
            facts[fact_type] = {
                "fact_key": fact_key,
                "value": str((corroboration.get("provenance") or {}).get("candidate_value") or ""),
                "verified": False,
                "reference_only": True,
                "source_path": "reference_only_corroboration_context",
                "field_sensitivity": self.classify_fact_field_sensitivity(fact_type),
                "acceptance_outcome": acceptance_outcome,
                "support_outcome": str(corroboration.get("support_outcome") or ""),
            }

        audit_report = self.build_card_fact_audit_report(card_code=resolved_card_code)
        return {
            "card_code": resolved_card_code,
            "intent": resolved_intent,
            "bundle_type": resolved_bundle_type,
            "bundle_fields": bundle_fields,
            "fact_count": len(facts),
            "verified_fact_count": sum(1 for item in facts.values() if bool(item.get("verified"))),
            "reference_only_fact_count": sum(1 for item in facts.values() if bool(item.get("reference_only"))),
            "facts": facts,
            "audit_summary": {
                "accepted_fact_count": int(audit_report.get("accepted_fact_count") or 0),
                "facts_needing_recheck": int(audit_report.get("facts_needing_recheck") or 0),
                "conflict_count": int(audit_report.get("conflict_count") or 0),
            },
        }

    def build_response_voice_profile(self, mode: str = "neutral") -> dict[str, Any]:
        resolved_mode = str(mode or "neutral").strip()
        profile = dict(self._response_voice_profiles().get(resolved_mode, self._response_voice_profiles()["neutral"]))
        profile["mode"] = str(profile.get("mode") or "neutral")
        return profile

    def select_flavor_insight(
        self,
        *,
        card_code: str,
        query_intent: str = "",
        fact_bundle: dict[str, Any] | None = None,
        spoiler_mode: str = "off",
    ) -> dict[str, Any] | None:
        resolved_mode = str(spoiler_mode or "off").strip().lower()
        if resolved_mode == "off":
            return None
        catalog_record = dict(self._flavor_insight_catalog().get(str(card_code or "").strip().upper()) or {})
        if not catalog_record:
            return None
        if float(catalog_record.get("confidence") or 0.0) < 0.8:
            return None
        allowed_intents = set(str(item or "").strip() for item in list(catalog_record.get("allowed_intents") or []))
        if query_intent and allowed_intents and str(query_intent or "").strip() not in allowed_intents:
            return None
        bundle_facts = dict((fact_bundle or {}).get("facts") or {})
        required_fields = list(catalog_record.get("required_fields") or [])
        if required_fields:
            dossier = self.fetch_dossier(str(card_code or "").strip().upper())
            for field in required_fields:
                bundle_value = str((bundle_facts.get(field) or {}).get("value") or "").strip()
                if bundle_value:
                    continue
                accepted = self.fetch_accepted_fact_provenance(
                    fact_key=f"{str(field or '').strip()}:{str(card_code or '').strip().upper()}",
                    fact_type=str(field or "").strip(),
                )
                accepted_value = str((accepted or {}).get("accepted_value") or "").strip()
                dossier_value = str(self._dossier_fact_value(dossier, field) or "").strip()
                if accepted_value or dossier_value:
                    continue
                return None
        if resolved_mode == "light":
            note = str(catalog_record.get("flavor_connection_light") or "").strip()
        else:
            note = str(
                catalog_record.get("flavor_connection_full")
                or catalog_record.get("flavor_connection_light")
                or ""
            ).strip()
        if not note:
            return None
        return {
            "card_code": str(card_code or "").strip().upper(),
            "flavor_connection": note,
            "spoiler_mode_applied": resolved_mode,
            "confidence": float(catalog_record.get("confidence") or 0.0),
            "tone_hint": str(catalog_record.get("tone_hint") or ""),
            "truth_layer_unchanged": True,
        }

    def plan_internal_answer_context(
        self,
        *,
        card_code: str,
        query: str,
        voice_mode: str = "neutral",
        spoiler_mode: str = "off",
        allow_reference_only: bool = False,
    ) -> dict[str, Any]:
        intent = self.classify_query_intent(query)
        bundle = self.load_fact_bundle(
            card_code=card_code,
            query=query,
            intent=str(intent.get("intent") or ""),
            bundle_type=str(intent.get("bundle_type") or ""),
            allow_reference_only=allow_reference_only,
        )
        resolution = self.plan_min_cost_fact_resolution(
            card_code=card_code,
            fact_type=str(intent.get("primary_fact_type") or "card_name"),
            allow_reference_only=allow_reference_only,
        )
        return {
            "card_code": str(card_code or "").strip().upper(),
            "query": str(query or "").strip(),
            "intent": intent,
            "fact_bundle": bundle,
            "resolution_plan": resolution,
            "voice_profile": self.build_response_voice_profile(mode=voice_mode),
            "flavor_insight": self.select_flavor_insight(
                card_code=card_code,
                query_intent=str(intent.get("intent") or ""),
                fact_bundle=bundle,
                spoiler_mode=spoiler_mode,
            ),
            "memory_first": True,
            "current_memory_sufficient": bool(resolution.get("current_memory_sufficient")),
            "requires_fresh_fetch": bool(resolution.get("requires_fresh_fetch")),
        }

    def record_reverification_execution_event(
        self,
        *,
        card_code: str,
        fact_key: str,
        fact_type: str,
        execution_outcome: str,
        reason_marker: str,
        resolution_path: str,
        source_id: str = "",
        governance: dict[str, Any] | None = None,
        execution_summary: dict[str, Any] | None = None,
    ) -> int:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO learning_reverification_execution_log (
                    card_code,
                    fact_key,
                    fact_type,
                    execution_outcome,
                    reason_marker,
                    resolution_path,
                    source_id,
                    governance_json,
                    execution_summary_json,
                    recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(card_code or "").strip().upper(),
                    str(fact_key or "").strip(),
                    str(fact_type or "").strip(),
                    str(execution_outcome or "").strip(),
                    str(reason_marker or "").strip(),
                    str(resolution_path or "").strip(),
                    str(source_id or "").strip().lower(),
                    json.dumps(dict(governance or {}), ensure_ascii=False, sort_keys=True),
                    json.dumps(dict(execution_summary or {}), ensure_ascii=False, sort_keys=True),
                    utc_timestamp(),
                ),
            )
            return int(cursor.lastrowid or 0)

    def list_reverification_execution_events(
        self,
        *,
        card_code: str = "",
        fact_key: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if str(card_code or "").strip():
            clauses.append("card_code = ?")
            params.append(str(card_code or "").strip().upper())
        if str(fact_key or "").strip():
            clauses.append("fact_key = ?")
            params.append(str(fact_key or "").strip())
        query = """
            SELECT *
            FROM learning_reverification_execution_log
        """
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(max(int(limit or 0), 1))
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            item = {key: row[key] for key in row.keys()}
            item["governance"] = json.loads(item.get("governance_json") or "{}")
            item["execution_summary"] = json.loads(item.get("execution_summary_json") or "{}")
            results.append(item)
        return results

    @staticmethod
    def build_reverification_execution_result(
        *,
        card_code: str,
        fact_key: str,
        fact_type: str,
        execution_outcome: str,
        reason_marker: str,
        resolution_path: str,
        source_id: str = "",
        governance: dict[str, Any] | None = None,
        fast_path: dict[str, Any] | None = None,
        health: dict[str, Any] | None = None,
        execution_log_id: int = 0,
    ) -> dict[str, Any]:
        return {
            "card_code": str(card_code or "").strip().upper(),
            "fact_key": str(fact_key or "").strip(),
            "fact_type": str(fact_type or "").strip(),
            "execution_outcome": str(execution_outcome or "").strip(),
            "reason_marker": str(reason_marker or "").strip(),
            "resolution_path": str(resolution_path or "").strip(),
            "source_id": str(source_id or "").strip().lower(),
            "governance": dict(governance or {}),
            "fast_path": dict(fast_path or {}),
            "health": dict(health or {}),
            "execution_log_id": int(execution_log_id or 0),
        }

    def execute_reverification_step(
        self,
        *,
        card_code: str,
        fact_type: str,
        fact_key: str = "",
        source_id: str = "",
        allow_reference_only: bool = False,
    ) -> dict[str, Any]:
        resolved_card_code = str(card_code or "").strip().upper()
        resolved_fact_type = str(fact_type or "").strip()
        resolved_fact_key = str(fact_key or f"{resolved_fact_type}:{resolved_card_code}").strip()
        fast_path = self.plan_min_cost_fact_resolution(
            card_code=resolved_card_code,
            fact_type=resolved_fact_type,
            fact_key=resolved_fact_key,
            allow_reference_only=allow_reference_only,
        )
        provenance = self.fetch_accepted_fact_provenance(
            fact_key=resolved_fact_key,
            fact_type=resolved_fact_type,
        )
        history = self.list_accepted_fact_history(
            card_code=resolved_card_code,
            fact_key=resolved_fact_key,
        )
        health = self.evaluate_fact_health(
            fact_key=resolved_fact_key,
            fact_type=resolved_fact_type,
            current_provenance=provenance,
            history=history,
        )
        reason_labels = self.build_fact_review_reason_labels(
            current_provenance=provenance or {
                "fact_key": resolved_fact_key,
                "fact_type": resolved_fact_type,
                "source_context": {},
            },
            history=history,
            health=health,
        )
        route_sufficient = bool(fast_path.get("current_memory_sufficient"))
        needs_reverification = str(health.get("health_status") or "") in {
            "needs_recheck",
            "unresolved_conflict",
            "superseded_recently",
        }
        if route_sufficient and not needs_reverification:
            log_id = self.record_reverification_execution_event(
                card_code=resolved_card_code,
                fact_key=resolved_fact_key,
                fact_type=resolved_fact_type,
                execution_outcome="execution_skipped_fast_path_sufficient",
                reason_marker="fast_path_sufficient",
                resolution_path=str(fast_path.get("resolution_path") or ""),
                execution_summary={
                    "reason_labels": reason_labels,
                    "verified_answer_available": bool(fast_path.get("verified_answer_available")),
                },
            )
            return self.build_reverification_execution_result(
                card_code=resolved_card_code,
                fact_key=resolved_fact_key,
                fact_type=resolved_fact_type,
                execution_outcome="execution_skipped_fast_path_sufficient",
                reason_marker="fast_path_sufficient",
                resolution_path=str(fast_path.get("resolution_path") or ""),
                fast_path=fast_path,
                health=health,
                execution_log_id=log_id,
            )

        preferred_source_id = str(
            source_id
            or ((provenance or {}).get("source_context") or {}).get("source_id")
            or ((self.fetch_dossier(resolved_card_code) or {}).get("basic_facts") or {}).get("source_id")
            or ""
        ).strip().lower()
        if not preferred_source_id:
            reviewed_sources = self.list_reviewed_source_candidates(limit=10)
            preferred_source_id = str(
                next(
                    (
                        item.get("source_id")
                        for item in reviewed_sources
                        if str(item.get("evidence_role") or "") == "verified-facts"
                    ),
                    "",
                )
                or ""
            ).strip().lower()

        reason_marker = "fresh_fetch_last_resort"
        if not provenance and not history and bool(fast_path.get("requires_fresh_fetch")):
            reason_marker = "fresh_fetch_last_resort"
        elif "conflict_present" in reason_labels:
            reason_marker = "conflict_driven_recheck"
        elif "core_identity_stale" in reason_labels:
            reason_marker = "stale_core_identity"
        elif "recent_supersession" in reason_labels:
            reason_marker = "recent_supersession_review"
        elif "english_print_pending" in reason_labels:
            reason_marker = "english_print_pending"

        if not preferred_source_id:
            log_id = self.record_reverification_execution_event(
                card_code=resolved_card_code,
                fact_key=resolved_fact_key,
                fact_type=resolved_fact_type,
                execution_outcome="recheck_deferred",
                reason_marker=reason_marker,
                resolution_path=str(fast_path.get("resolution_path") or ""),
                execution_summary={"reason_labels": reason_labels},
            )
            return self.build_reverification_execution_result(
                card_code=resolved_card_code,
                fact_key=resolved_fact_key,
                fact_type=resolved_fact_type,
                execution_outcome="recheck_deferred",
                reason_marker=reason_marker,
                resolution_path=str(fast_path.get("resolution_path") or ""),
                fast_path=fast_path,
                health=health,
                execution_log_id=log_id,
            )

        governance = self.evaluate_source_execution_gate(
            source_id=preferred_source_id,
            execution_kind="learning-intake",
        )
        execution_outcome = "source_touch_allowed"
        if not bool(governance.get("proceed")):
            gate_outcome = str(governance.get("execution_outcome") or "")
            if gate_outcome == "defer-manual-review":
                execution_outcome = "manual_review_required"
                reason_marker = "governance_manual_review"
            elif gate_outcome in {"defer-reference-only", "defer-lead-only", "defer-market-hint-only"}:
                execution_outcome = "limited_use_only"
                reason_marker = "reference_only_limit"
            elif gate_outcome == "block":
                execution_outcome = "source_touch_blocked"
                reason_marker = "governance_blocked"
            else:
                execution_outcome = "execution_skipped_policy"
                reason_marker = "governance_blocked"
        log_id = self.record_reverification_execution_event(
            card_code=resolved_card_code,
            fact_key=resolved_fact_key,
            fact_type=resolved_fact_type,
            execution_outcome=execution_outcome,
            reason_marker=reason_marker,
            resolution_path=str(fast_path.get("resolution_path") or ""),
            source_id=preferred_source_id,
            governance=governance,
            execution_summary={"reason_labels": reason_labels},
        )
        return self.build_reverification_execution_result(
            card_code=resolved_card_code,
            fact_key=resolved_fact_key,
            fact_type=resolved_fact_type,
            execution_outcome=execution_outcome,
            reason_marker=reason_marker,
            resolution_path=str(fast_path.get("resolution_path") or ""),
            source_id=preferred_source_id,
            governance=governance,
            fast_path=fast_path,
            health=health,
            execution_log_id=log_id,
        )

    def run_controlled_reverification(
        self,
        *,
        card_code: str = "",
        limit: int = 10,
        source_overrides: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        plan = self.build_reverification_plan(card_code=card_code, limit=limit)
        overrides = dict(source_overrides or {})
        results: list[dict[str, Any]] = []
        for candidate in list(plan.get("candidates") or []):
            fact_key = str(candidate.get("fact_key") or "")
            results.append(
                self.execute_reverification_step(
                    card_code=str(candidate.get("card_code") or card_code or ""),
                    fact_type=str(candidate.get("fact_type") or ""),
                    fact_key=fact_key,
                    source_id=str(overrides.get(fact_key) or ""),
                )
            )
        return {
            "card_code": str(card_code or "").strip().upper(),
            "candidate_count": int(plan.get("candidate_count") or 0),
            "priority_summary": dict(plan.get("priority_summary") or {}),
            "results": results,
        }

    def evaluate_candidate_fact_acceptance(
        self,
        *,
        fact_key: str,
        fact_type: str = "",
        candidate_value: str = "",
        evidence_items: Sequence[dict[str, Any]],
        provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        corroboration = self.evaluate_fact_corroboration(
            evidence_items=evidence_items,
            candidate_value=candidate_value,
        )
        support_outcome = str(corroboration.get("support_outcome") or "insufficient_support")
        if support_outcome == "verified_ready":
            acceptance_outcome = "accept_verified_candidate"
        elif support_outcome == "corroborated_reference_only":
            acceptance_outcome = "keep_reference_only"
        elif support_outcome == "conflicting_evidence":
            acceptance_outcome = "unresolved_conflict"
        elif support_outcome == "unusable_evidence":
            acceptance_outcome = "reject_unusable"
        else:
            acceptance_outcome = "insufficient_support"

        record_id = self.upsert_fact_corroboration_record(
            fact_key=fact_key,
            fact_type=fact_type,
            corroboration=corroboration,
            acceptance_outcome=acceptance_outcome,
            provenance={
                "candidate_value": str(candidate_value or "").strip(),
                "evidence_count": len(list(evidence_items or [])),
                **dict(provenance or {}),
            },
        )
        return {
            "fact_key": str(fact_key or "").strip(),
            "fact_type": str(fact_type or "").strip(),
            "candidate_value": str(candidate_value or "").strip(),
            "acceptance_outcome": acceptance_outcome,
            "corroboration_record_id": record_id,
            "verified_fact_acceptance": acceptance_outcome == "accept_verified_candidate",
            "reference_only_retained": acceptance_outcome == "keep_reference_only",
            "corroboration": corroboration,
        }

    @staticmethod
    def build_source_governance_task_result(
        *,
        task: LearningTask,
        governance: dict[str, Any],
        source_id: str,
        message_prefix: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        outcome = str(governance.get("execution_outcome") or governance.get("gate_action") or "block")
        reason = str(governance.get("reason") or governance.get("policy_summary") or governance.get("rationale") or "").strip()
        return {
            "message": f"{message_prefix} for {source_id}: {outcome}. {reason}".strip(),
            "task_type": task.task_type,
            "card_code": str(task.card_code or "").strip().upper(),
            "variant_key": normalize_variant_key(task.variant_key or ""),
            "source_id": source_id,
            "source_reference": "",
            "skipped": True,
            "governance": governance,
            **dict(extra or {}),
        }

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

    def resolve_source_task_payload(self, source_id: str, task_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(task_payload or {})
        source_payloads = payload.get("source_payloads") if isinstance(payload.get("source_payloads"), dict) else {}
        scoped_payload = dict(source_payloads.get(source_id) or {})
        merged = {**self.resolve_default_source_task_payload(source_id), **scoped_payload}
        for key in ("snapshot_path", "snapshot_url", "set_code", "batch_limit"):
            if key in payload and key not in merged:
                merged[key] = payload[key]
        return merged

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
        self.verified_dossier_store.upsert_card_source(
            card_code=record.card_code,
            source_id=record.source_id,
            source_type=getattr(record, "source_type", "") or "source-record",
            source_url=record.source_url,
            source_reference=record.source_reference,
            fetched_at=record.fetched_at,
            trust_level="official" if str(record.source_id or "").startswith("official") else "source-backed",
            trust_score=0.95 if str(record.source_id or "").startswith("official") else 0.62,
            citation_payload={
                "source_id": record.source_id,
                "source_reference": record.source_reference,
                "source_url": record.source_url,
            },
            notes=f"learning_verification_state={verification_state}",
        )

    def merge_source_record_into_dossier(
        self,
        record: NormalizedSourceRecord,
        *,
        verification_state: str,
    ) -> dict[str, Any]:
        acceptance = self.evaluate_source_record_fact_acceptance(record)
        if not acceptance["stored"]:
            accepted_fact_history_ids: dict[str, int] = {}
            accepted_fact_change_summaries: dict[str, str] = {}
            for field_name, outcome in dict(acceptance.get("deferred_fields") or {}).items():
                if str(outcome or "") != "unresolved_conflict":
                    continue
                fact_key = f"{field_name}:{record.card_code}"
                current_provenance = self.fetch_accepted_fact_provenance(
                    fact_key=fact_key,
                    fact_type=field_name,
                )
                if current_provenance is None:
                    continue
                corroboration_record = self.fetch_fact_corroboration_record(
                    fact_key=fact_key,
                    fact_type=field_name,
                )
                field_sensitivity = self.classify_fact_field_sensitivity(field_name)
                change_summary = "unresolved_due_to_conflict"
                accepted_fact_history_ids[field_name] = self.record_accepted_fact_history(
                    card_code=record.card_code,
                    fact_key=fact_key,
                    fact_type=field_name,
                    accepted_value=str(current_provenance.get("accepted_value") or ""),
                    acceptance_outcome=str(current_provenance.get("acceptance_outcome") or "accept_verified_candidate"),
                    support_outcome=str(current_provenance.get("support_outcome") or "verified_ready"),
                    event_type="unresolved_preserved",
                    change_summary=change_summary,
                    corroboration_record_id=int((current_provenance.get("corroboration_record_id") or 0)),
                    source_context={
                        **dict(current_provenance.get("source_context") or {}),
                        "incoming_source_id": str(record.source_id or "").strip().lower(),
                        "incoming_source_reference": str(record.source_reference or "").strip(),
                        "verification_state": str(verification_state or "").strip(),
                        "storage_outcome": str(acceptance.get("storage_outcome") or ""),
                    },
                    field_sensitivity=field_sensitivity,
                    acceptance_strength=int(
                        (current_provenance.get("source_context") or {}).get("acceptance_strength") or 0
                    ),
                )
                accepted_fact_change_summaries[field_name] = change_summary
            acceptance["accepted_fact_history_ids"] = accepted_fact_history_ids
            acceptance["accepted_fact_change_summaries"] = accepted_fact_change_summaries
            return acceptance

        existing = self.fetch_dossier(record.card_code)
        source_rollup = self.summarize_dossier_sources(record.card_code)
        merged_facts = dict((existing or {}).get("basic_facts") or {})
        raw_accepted_values = dict(acceptance.get("accepted_values") or {})
        accepted_values: dict[str, Any] = {}
        accepted_fact_provenance_ids: dict[str, int] = {}
        accepted_fact_history_ids: dict[str, int] = {}
        accepted_fact_change_summaries: dict[str, str] = {}
        final_accepted_fields: list[str] = []
        for field_name, raw_value in raw_accepted_values.items():
            fact_key = f"{field_name}:{record.card_code}"
            corroboration_record_id = int((acceptance.get("corroboration_record_ids") or {}).get(field_name) or 0)
            corroboration_record = self.fetch_fact_corroboration_record(
                fact_key=fact_key,
                fact_type=field_name,
            )
            current_provenance = self.fetch_accepted_fact_provenance(
                fact_key=fact_key,
                fact_type=field_name,
            )
            supersession = self.evaluate_fact_supersession(
                current_provenance=current_provenance,
                accepted_value=self._normalize_candidate_fact_value(raw_value),
                acceptance_outcome="accept_verified_candidate",
                corroboration_record=corroboration_record,
                fact_type=field_name,
            )
            change_summary = str(supersession.get("change_summary") or "")
            accepted_fact_change_summaries[field_name] = change_summary
            history_source_context = {
                "source_id": str(record.source_id or "").strip().lower(),
                "source_reference": str(record.source_reference or "").strip(),
                "source_url": str(record.source_url or "").strip(),
                "verification_state": str(verification_state or "").strip(),
                "storage_outcome": str(acceptance.get("storage_outcome") or ""),
                "supersession_outcome": str(supersession.get("supersession_outcome") or ""),
                "stronger_source_level": str((corroboration_record or {}).get("stronger_source_level") or ""),
                "acceptance_strength": int(supersession.get("acceptance_strength") or 0),
            }
            accepted_fact_history_ids[field_name] = self.record_accepted_fact_history(
                card_code=record.card_code,
                fact_key=fact_key,
                fact_type=field_name,
                accepted_value=self._normalize_candidate_fact_value(raw_value),
                acceptance_outcome="accept_verified_candidate",
                support_outcome=str((corroboration_record or {}).get("support_outcome") or "verified_ready"),
                event_type=str(supersession.get("event_type") or "accepted"),
                change_summary=change_summary,
                corroboration_record_id=corroboration_record_id,
                source_context=history_source_context,
                field_sensitivity=str(supersession.get("field_sensitivity") or "contextual"),
                acceptance_strength=int(supersession.get("acceptance_strength") or 0),
            )
            if not bool(supersession.get("update_latest_snapshot")):
                continue
            accepted_values[field_name] = raw_value
            final_accepted_fields.append(field_name)
            accepted_fact_provenance_ids[field_name] = self.upsert_accepted_fact_provenance(
                card_code=record.card_code,
                fact_key=fact_key,
                fact_type=field_name,
                accepted_value=self._normalize_candidate_fact_value(raw_value),
                acceptance_outcome="accept_verified_candidate",
                corroboration_record_id=corroboration_record_id,
                corroboration_record=corroboration_record,
                source_context={
                    **history_source_context,
                    "event_type": str(supersession.get("event_type") or "accepted"),
                    "change_summary": change_summary,
                    "field_sensitivity": str(supersession.get("field_sensitivity") or "contextual"),
                },
                stored_in_dossier=True,
            )
        acceptance["accepted_fields"] = sorted(final_accepted_fields)
        acceptance["accepted_values"] = accepted_values
        acceptance["accepted_fact_provenance_ids"] = accepted_fact_provenance_ids
        acceptance["accepted_fact_history_ids"] = accepted_fact_history_ids
        acceptance["accepted_fact_change_summaries"] = accepted_fact_change_summaries
        merged_facts.update(
            {
                "card_code": record.card_code,
                "card_name": accepted_values.get("card_name", merged_facts.get("card_name", "")),
                "set_code": accepted_values.get("set_code", merged_facts.get("set_code", "")),
                "set_family": derive_set_family(
                    str(accepted_values.get("set_code") or merged_facts.get("set_code") or record.card_code)
                ),
                "set_name": accepted_values.get("set_name", merged_facts.get("set_name", "")),
                "rarity": accepted_values.get("rarity", merged_facts.get("rarity", "")),
                "color": accepted_values.get("color", merged_facts.get("color", "")),
                "card_type": accepted_values.get("card_type", merged_facts.get("card_type", "")),
                "cost": accepted_values.get("cost", merged_facts.get("cost", "")),
                "power": accepted_values.get("power", merged_facts.get("power", "")),
                "counter": accepted_values.get("counter", merged_facts.get("counter", "")),
                "attribute": accepted_values.get("attribute", merged_facts.get("attribute", "")),
                "traits": accepted_values.get("traits", merged_facts.get("traits", "")),
                "life": accepted_values.get("life", merged_facts.get("life", "")),
                "effect_text": accepted_values.get("effect_text", merged_facts.get("effect_text", "")),
                "trigger_text": accepted_values.get("trigger_text", merged_facts.get("trigger_text", "")),
                "illustrator": accepted_values.get("illustrator", merged_facts.get("illustrator", "")),
                "source_id": record.source_id,
                "source_url": record.source_url,
                "source_reference": record.source_reference,
                "source_fetched_at": record.fetched_at,
                "source_count": source_rollup["source_count"],
                "source_names": list(source_rollup["display_names"]),
                "source_confidence_level": source_rollup["confidence_level"],
                "fact_acceptance": {
                    "storage_outcome": str(acceptance.get("storage_outcome") or ""),
                    "accepted_field_count": len(list(acceptance.get("accepted_fields") or [])),
                    "deferred_field_count": len(dict(acceptance.get("deferred_fields") or {})),
                    "accepted_fields": list(acceptance.get("accepted_fields") or [])[:8],
                    "source_id": str(record.source_id or "").strip().lower(),
                    "change_summaries": dict(list(accepted_fact_change_summaries.items())[:8]),
                },
            }
        )
        merged = {
            "card_code": record.card_code,
            "card_name": str(accepted_values.get("card_name") or (existing or {}).get("card_name", "")),
            "set_code": str(accepted_values.get("set_code") or (existing or {}).get("set_code", "")),
            "rarity": str(accepted_values.get("rarity") or (existing or {}).get("rarity", "")),
            "basic_facts": merged_facts,
            "source_summary": source_rollup["summary_text"],
            "confidence": max(
                float((existing or {}).get("confidence") or 0.0),
                0.55 if source_rollup["source_count"] < 2 else (0.78 if source_rollup["source_count"] == 2 else 0.9),
            ),
            "verification_state": verification_state if source_rollup["source_count"] >= 2 else "pending-confirmation",
        }
        self.upsert_dossier(merged)
        acceptance["verified_dossier_ingestion"] = self.sync_card_to_verified_dossier_store(
            card_code=record.card_code,
            source_record=record,
            merged_dossier=merged,
            acceptance=acceptance,
            source_rollup=source_rollup,
        )
        return acceptance

    def resolve_image_source_entry(self, source_id: str) -> MiruSourceEntry:
        entry = get_source_entry(source_id or "official-card-images", self.source_registry)
        if "image" not in entry.source_type:
            raise ValueError(f"Source {entry.source_id} is not an image source.")
        return entry

    def fetch_dossier_source_records(self, card_code: str) -> list[dict[str, Any]]:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_dossier_sources
                WHERE card_code = ?
                ORDER BY updated_at DESC, fetched_at DESC, id DESC
                """,
                (card_code,),
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row["field_payload_json"] or "{}")
            records.append(
                {
                    "card_code": str(row["card_code"] or ""),
                    "source_id": str(row["source_id"] or ""),
                    "source_reference": str(row["source_reference"] or ""),
                    "verification_state": str(row["verification_state"] or ""),
                    "payload": payload if isinstance(payload, dict) else {},
                    "fetched_at": str(row["fetched_at"] or ""),
                    "updated_at": str(row["updated_at"] or ""),
                }
            )
        return records

    @staticmethod
    def _normalize_candidate_fact_value(value: Any) -> str:
        if isinstance(value, list):
            parts = [clean_display_text(str(item or "")) for item in value if clean_display_text(str(item or ""))]
            return " | ".join(parts)
        return clean_display_text(str(value or ""))

    @classmethod
    def _source_record_fact_fields(cls, payload: dict[str, Any]) -> dict[str, Any]:
        traits_value = payload.get("traits") or []
        if isinstance(traits_value, str):
            traits_value = [part.strip() for part in traits_value.split("/") if part.strip()]
        elif not isinstance(traits_value, list):
            traits_value = [str(traits_value).strip()] if str(traits_value or "").strip() else []
        return {
            "card_name": clean_display_text(str(payload.get("card_name") or "")),
            "set_code": clean_display_text(str(payload.get("set_code") or "")).upper(),
            "set_name": clean_display_text(str(payload.get("set_name") or "")),
            "rarity": clean_display_text(str(payload.get("rarity") or "")),
            "color": clean_display_text(str(payload.get("color") or "")),
            "card_type": clean_display_text(str(payload.get("card_type") or "")),
            "cost": str(payload.get("cost") or "").strip(),
            "power": clean_display_text(str(payload.get("power") or "")),
            "counter": clean_display_text(str(payload.get("counter") or "")),
            "attribute": clean_display_text(str(payload.get("attribute") or "")),
            "traits": [clean_display_text(str(item or "")) for item in traits_value if clean_display_text(str(item or ""))],
            "life": clean_display_text(str(payload.get("life") or "")),
            "effect_text": clean_display_text(str(payload.get("effect_text") or "")),
            "trigger_text": clean_display_text(str(payload.get("trigger_text") or "")),
            "illustrator": clean_display_text(str(payload.get("illustrator") or "")),
        }

    def evaluate_source_record_fact_acceptance(
        self,
        record: NormalizedSourceRecord,
    ) -> dict[str, Any]:
        fact_fields = self._source_record_fact_fields(record.to_dict())
        source_records = list(self.fetch_dossier_source_records(record.card_code))
        if not any(
            str(item.get("source_id") or "").strip().lower() == str(record.source_id or "").strip().lower()
            and str(item.get("source_reference") or "").strip() == str(record.source_reference or "").strip()
            for item in source_records
        ):
            source_records.append(
                {
                    "source_id": str(record.source_id or "").strip().lower(),
                    "source_reference": str(record.source_reference or "").strip(),
                    "payload": record.to_dict(),
                }
            )

        accepted_fields: dict[str, Any] = {}
        field_outcomes: dict[str, str] = {}
        field_record_ids: dict[str, int] = {}
        deferred_fields: dict[str, str] = {}

        for field_name, raw_value in fact_fields.items():
            candidate_value = self._normalize_candidate_fact_value(raw_value)
            if not candidate_value:
                continue
            evidence_items: list[dict[str, Any]] = []
            for row in source_records:
                payload = dict(row.get("payload") or {})
                payload_fields = self._source_record_fact_fields(payload)
                claim_value = self._normalize_candidate_fact_value(payload_fields.get(field_name))
                if not claim_value:
                    continue
                governance = self.evaluate_source_governance_policy(
                    source_id=str(row.get("source_id") or payload.get("source_id") or ""),
                )
                evidence_items.append(
                    {
                        "source_id": str(row.get("source_id") or payload.get("source_id") or "").strip().lower(),
                        "evidence_role": str(
                            governance.get("policy_evidence_role") or governance.get("evidence_role") or "blocked"
                        ),
                        "claim_value": claim_value,
                    }
                )
            acceptance = self.evaluate_candidate_fact_acceptance(
                fact_key=f"{field_name}:{record.card_code}",
                fact_type=field_name,
                candidate_value=candidate_value,
                evidence_items=evidence_items,
                provenance={
                    "card_code": str(record.card_code or "").strip().upper(),
                    "source_id": str(record.source_id or "").strip().lower(),
                    "source_reference": str(record.source_reference or "").strip(),
                },
            )
            acceptance_outcome = str(acceptance.get("acceptance_outcome") or "insufficient_support")
            field_outcomes[field_name] = acceptance_outcome
            field_record_ids[field_name] = int(acceptance.get("corroboration_record_id") or 0)
            if acceptance_outcome == "accept_verified_candidate":
                accepted_fields[field_name] = raw_value
            else:
                deferred_fields[field_name] = acceptance_outcome

        required_fields_met = bool(
            self._normalize_candidate_fact_value(accepted_fields.get("card_name"))
            and self._normalize_candidate_fact_value(accepted_fields.get("set_code"))
        )
        if required_fields_met:
            storage_outcome = "accept_verified_candidate"
            stored = True
            storage_reason = "Core dossier identity facts met corroboration requirements."
        elif "unresolved_conflict" in deferred_fields.values():
            storage_outcome = "unresolved_conflict"
            stored = False
            storage_reason = "Conflicting stronger evidence prevented verified dossier storage."
        elif "keep_reference_only" in deferred_fields.values():
            storage_outcome = "keep_reference_only"
            stored = False
            storage_reason = "Available support remained reference-only, so the dossier kept source records separate."
        elif "reject_unusable" in deferred_fields.values():
            storage_outcome = "reject_unusable"
            stored = False
            storage_reason = "Candidate facts lacked usable corroboration for verified dossier storage."
        else:
            storage_outcome = "insufficient_support"
            stored = False
            storage_reason = "Candidate facts did not meet corroboration requirements for verified dossier storage."

        return {
            "stored": stored,
            "storage_outcome": storage_outcome,
            "storage_path": "verified-dossier" if stored else "source-record-only",
            "storage_reason": storage_reason,
            "accepted_fields": sorted(accepted_fields.keys()),
            "accepted_values": accepted_fields,
            "deferred_fields": deferred_fields,
            "field_outcomes": field_outcomes,
            "corroboration_record_ids": field_record_ids,
        }

    def summarize_dossier_sources(self, card_code: str) -> dict[str, Any]:
        records = self.fetch_dossier_source_records(card_code)
        display_names: list[str] = []
        distinct_sources: set[str] = set()
        for item in records:
            source_id = str(item.get("source_id") or "").strip().lower()
            if not source_id:
                continue
            distinct_sources.add(source_id)
            try:
                profile = self.resolve_source_entry(source_id)
                display_name = profile.source_name
            except KeyError:
                display_name = source_id.replace("-", " ").title()
            if display_name not in display_names:
                display_names.append(display_name)
        source_count = len(distinct_sources)
        confidence_level = "high" if source_count >= 3 else ("medium" if source_count >= 2 else "low")
        return {
            "source_count": source_count,
            "display_names": display_names,
            "confidence_level": confidence_level,
            "summary_text": ", ".join(display_names[:4]) if display_names else "No recorded sources yet",
        }

    def build_dossier_source_entries(self, card_code: str) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in self.fetch_dossier_source_records(card_code):
            source_id = str(item.get("source_id") or "").strip().lower()
            if not source_id:
                continue
            source_reference = str(item.get("source_reference") or "").strip()
            key = (source_id, source_reference)
            if key in seen:
                continue
            seen.add(key)
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            try:
                profile = self.resolve_source_entry(source_id)
                display_name = profile.source_name
                source_type = profile.source_type
                trust_tier = profile.trust_tier
                trust_label = profile.trust_label
                enabled = profile.enabled
                review_state = profile.review_state
                rate_limit_hint = profile.rate_limit_hint
                backoff_policy = profile.backoff_policy
                notes = profile.notes
                public_data_only = profile.public_data_only
                requires_login = profile.requires_login
                respect_site_policies = profile.respect_site_policies
                allow_aggressive_crawling = profile.allow_aggressive_crawling
                data_categories = list(profile.data_categories)
            except KeyError:
                display_name = source_id.replace("-", " ").title()
                source_type = "unknown"
                trust_tier = 4
                trust_label = "experimental/manual review only"
                enabled = False
                review_state = "manual-review-only"
                rate_limit_hint = "Do not poll automatically."
                backoff_policy = "manual review only"
                notes = "Unknown source encountered during dossier review."
                public_data_only = True
                requires_login = False
                respect_site_policies = True
                allow_aggressive_crawling = False
                data_categories = []
            entries.append(
                {
                    "source_id": source_id,
                    "source_reference": source_reference,
                    "source_url": str(payload.get("source_url") or ""),
                    "display_name": display_name,
                    "source_type": source_type,
                    "trust_tier": trust_tier,
                    "trust_label": trust_label,
                    "enabled": enabled,
                    "review_state": review_state,
                    "rate_limit_hint": rate_limit_hint,
                    "backoff_policy": backoff_policy,
                    "public_data_only": public_data_only,
                    "requires_login": requires_login,
                    "respect_site_policies": respect_site_policies,
                    "allow_aggressive_crawling": allow_aggressive_crawling,
                    "data_categories": data_categories,
                    "notes": notes,
                    "observed_at": str(item.get("fetched_at") or ""),
                    "source_intake": self.evaluate_source_trust_intake(
                        source_id=source_id,
                        source_type=source_type,
                        source_url=str(payload.get("source_url") or ""),
                        notes=notes,
                        trust_tier=trust_tier,
                        trust_label=trust_label,
                        public_data_only=public_data_only,
                        requires_login=requires_login,
                        respect_site_policies=respect_site_policies,
                        review_state=review_state,
                        last_reviewed_at=str(item.get("fetched_at") or ""),
                    ),
                }
            )
        return entries

    def build_visual_supporting_entries(self, card_code: str) -> list[dict[str, Any]]:
        dossier = self.fetch_dossier(card_code)
        visual = dict((dossier or {}).get("basic_facts", {}).get("visual_analysis") or {})
        verification_status = str(visual.get("verification_status") or "").strip()
        if not verification_status:
            return []
        return [
            {
                "source_id": "local-image-analysis",
                "source_reference": str(visual.get("analyzed_at") or verification_status),
                "source_url": "",
                "display_name": "Local Image Analysis",
                "source_type": "local-image-analysis",
                "trust_tier": 3,
                "trust_label": "secondary/reference",
                "enabled": True,
                "review_state": "active",
                "rate_limit_hint": "local cached image analysis only",
                "backoff_policy": "no external calls",
                "public_data_only": True,
                "requires_login": False,
                "respect_site_policies": True,
                "allow_aggressive_crawling": False,
                "data_categories": ["card-images"],
                "notes": "Image-derived evidence is supportive only and never final by itself.",
                "observed_at": str(visual.get("analyzed_at") or ""),
                "evidence_role": "image-confirmation",
                "verification_status": verification_status,
                "confidence_score": float(visual.get("confidence") or 0.0),
                "conflict_flags": list(visual.get("conflict_flags") or []),
                "source_intake": self.evaluate_source_trust_intake(
                    source_id="local-image-analysis",
                    source_type="local-image-analysis",
                    notes="Image-derived evidence is supportive only and never final by itself.",
                    trust_tier=3,
                    trust_label="secondary/reference",
                    public_data_only=True,
                    requires_login=False,
                    respect_site_policies=True,
                    review_state="active",
                    last_reviewed_at=str(visual.get("analyzed_at") or ""),
                ),
            }
        ]

    def build_local_corroboration_entries(self, card_code: str) -> list[dict[str, Any]]:
        canonical = normalize_card_code(card_code)
        resolved_code = canonical["canonical_code"] or str(card_code or "").strip().upper()
        if not resolved_code:
            return []
        knowledge_entry = (self.load_knowledge_cache().get("cards") or {}).get(resolved_code, {})
        catalog_entry = self.catalog_card_row(resolved_code)
        has_knowledge = isinstance(knowledge_entry, dict) and bool(knowledge_entry)
        has_catalog = bool(catalog_entry)
        if not has_knowledge and not has_catalog:
            return []
        observed_at = ""
        for candidate in (
            str(knowledge_entry.get("updated_at") or knowledge_entry.get("last_seen_at") or "").strip() if has_knowledge else "",
            str(catalog_entry.get("updated_at") or "").strip() if has_catalog else "",
        ):
            if candidate:
                observed_at = candidate
                break
        categories: list[str] = []
        source_parts: list[str] = []
        if has_knowledge:
            categories.append("knowledge-cache")
            source_parts.append("knowledge cache")
        if has_catalog:
            categories.extend(["card-metadata", "variants"])
            source_parts.append("canonical catalog")
        return [
            {
                "source_id": "local-corroboration",
                "source_reference": observed_at or f"local-corroboration:{resolved_code}",
                "source_url": "",
                "display_name": "Local Corroboration",
                "source_type": "local-card-intelligence",
                "trust_tier": 3,
                "trust_label": "secondary/reference",
                "enabled": True,
                "review_state": "active",
                "rate_limit_hint": "local data only",
                "backoff_policy": "no external calls",
                "public_data_only": True,
                "requires_login": False,
                "respect_site_policies": True,
                "allow_aggressive_crawling": False,
                "data_categories": categories,
                "notes": (
                    "Local corroboration from "
                    + " and ".join(source_parts)
                    + " can strengthen a source-backed dossier, but does not replace a trusted external source."
                ),
                "observed_at": observed_at,
                "evidence_role": "local-corroboration",
                "source_intake": self.evaluate_source_trust_intake(
                    source_id="local-corroboration",
                    source_type="local-card-intelligence",
                    notes=(
                        "Local corroboration from "
                        + " and ".join(source_parts)
                        + " can strengthen a source-backed dossier, but does not replace a trusted external source."
                    ),
                    trust_tier=3,
                    trust_label="secondary/reference",
                    public_data_only=True,
                    requires_login=False,
                    respect_site_policies=True,
                    review_state="active",
                    last_reviewed_at=observed_at,
                ),
            }
        ]

    def build_promotion_supporting_entries(self, card_code: str) -> list[dict[str, Any]]:
        return (
            self.build_dossier_source_entries(card_code)
            + self.build_local_corroboration_entries(card_code)
            + self.build_visual_supporting_entries(card_code)
        )

    def build_registry_supporting_entries(self, card_code: str) -> list[dict[str, Any]]:
        return self.build_promotion_supporting_entries(card_code)

    def build_promotion_record(self, card_code: str) -> NormalizedSourceRecord | None:
        dossier = self.fetch_dossier(card_code)
        if dossier is None:
            return None
        basic_facts = dict(dossier.get("basic_facts") or {})
        resolved_code = normalize_card_code(card_code).get("canonical_code") or str(card_code or "").strip().upper()
        if not resolved_code:
            return None
        traits_value = basic_facts.get("traits")
        if isinstance(traits_value, list):
            traits = [clean_display_text(str(item)) for item in traits_value if clean_display_text(str(item))]
        else:
            traits = [
                clean_display_text(part)
                for part in str(traits_value or "").replace("|", "/").split("/")
                if clean_display_text(part)
            ]
        source_rows = self.fetch_dossier_source_records(resolved_code)
        source_id = str((source_rows[0] or {}).get("source_id") or basic_facts.get("source_id") or "official-cardlist").strip().lower() if source_rows else str(basic_facts.get("source_id") or "official-cardlist").strip().lower()
        source_reference = str((source_rows[0] or {}).get("source_reference") or basic_facts.get("source_reference") or "dossier-promotion").strip() if source_rows else str(basic_facts.get("source_reference") or "dossier-promotion").strip()
        source_url = ""
        if source_rows:
            payload = source_rows[0].get("payload") if isinstance(source_rows[0].get("payload"), dict) else {}
            source_url = str(payload.get("source_url") or basic_facts.get("source_url") or "")
        else:
            source_url = str(basic_facts.get("source_url") or "")
        return NormalizedSourceRecord(
            card_code=resolved_code,
            card_name=clean_display_text(str(dossier.get("card_name") or basic_facts.get("card_name") or "")),
            set_code=clean_display_text(str(dossier.get("set_code") or basic_facts.get("set_code") or "")).upper(),
            set_name=clean_display_text(str(basic_facts.get("set_name") or "")),
            rarity=clean_display_text(str(dossier.get("rarity") or basic_facts.get("rarity") or "")),
            color=clean_display_text(str(basic_facts.get("color") or "")),
            card_type=clean_display_text(str(basic_facts.get("card_type") or "")),
            cost=str(basic_facts.get("cost") or ""),
            power=clean_display_text(str(basic_facts.get("power") or "")),
            counter=clean_display_text(str(basic_facts.get("counter") or "")),
            attribute=clean_display_text(str(basic_facts.get("attribute") or "")),
            traits=traits,
            life=clean_display_text(str(basic_facts.get("life") or "")),
            effect_text=clean_display_text(str(basic_facts.get("effect_text") or "")),
            trigger_text=clean_display_text(str(basic_facts.get("trigger_text") or "")),
            source_id=source_id or "official-cardlist",
            source_url=source_url,
            source_reference=source_reference or "dossier-promotion",
            fetched_at=str(basic_facts.get("source_fetched_at") or utc_timestamp()),
            illustrator=clean_display_text(str(basic_facts.get("illustrator") or "")),
        )

    @staticmethod
    def _distinct_non_image_source_count(entries: list[dict[str, Any]]) -> int:
        return len(
            {
                str(entry.get("source_id") or "").strip().lower()
                for entry in entries
                if str(entry.get("source_id") or "").strip()
                and str(entry.get("evidence_role") or "").strip().lower() != "image-confirmation"
            }
        )

    def is_dossier_promotable(self, card_code: str) -> bool:
        dossier = self.fetch_dossier(card_code)
        if dossier is None:
            return False
        supporting_entries = self.build_promotion_supporting_entries(card_code)
        external_sources = {
            str(entry.get("source_id") or "").strip().lower()
            for entry in supporting_entries
            if str(entry.get("source_id") or "").strip()
            and str(entry.get("evidence_role") or "").strip().lower() != "image-confirmation"
            and str(entry.get("source_id") or "").strip().lower() not in {"knowledge-cache", "card-catalog"}
        }
        if not external_sources:
            return False
        validation = self.fetch_project_validation(card_code)
        current_status = str((validation or {}).get("verification_status") or dossier.get("verification_state") or "").strip().lower()
        if current_status in {"verified", "high-confidence", "verified-with-image-confirmation"}:
            return False
        return self._distinct_non_image_source_count(supporting_entries) >= self.project_sync.min_verified_sources

    def fetch_project_validation(self, card_code: str) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.project_db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM miru_validations WHERE card_code = ?",
                (str(card_code or "").strip().upper(),),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["review_notes"] = json.loads(result.get("review_notes_json") or "[]")
        result["citation_payload"] = json.loads(result.get("citation_payload_json") or "{}")
        result["score_breakdown"] = json.loads(result.get("score_breakdown_json") or "{}")
        return result

    def apply_project_sync_result_to_dossier(self, card_code: str, sync_result: dict[str, Any]) -> None:
        dossier = self.fetch_dossier(card_code)
        if dossier is None:
            return
        basic_facts = dict(dossier.get("basic_facts") or {})
        source_rollup = dict(sync_result.get("source_rollup") or {})
        basic_facts["source_count"] = int(source_rollup.get("source_count") or 0)
        basic_facts["source_names"] = list(source_rollup.get("source_names") or [])
        basic_facts["source_confidence_level"] = str(sync_result.get("confidence_level") or source_rollup.get("confidence_level") or "")
        basic_facts["last_promoted_at"] = utc_timestamp()
        basic_facts["last_promotion_status"] = str(sync_result.get("status") or "")
        dossier["basic_facts"] = basic_facts
        dossier["source_summary"] = ", ".join(list(source_rollup.get("source_names") or [])[:4]) or dossier.get("source_summary") or "No recorded sources yet"
        dossier["confidence"] = round(max(float(dossier.get("confidence") or 0.0), float(sync_result.get("confidence_score") or 0.0)), 2)
        dossier["verification_state"] = str(sync_result.get("verification_status") or dossier.get("verification_state") or "pending-confirmation").replace("_", "-")
        self.upsert_dossier(dossier)

    def promotable_dossier_card_codes(self, limit: int | None = None) -> list[str]:
        batch_limit = max(int(limit or self.seed_batch_size), 1)
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute("ATTACH DATABASE ? AS learning_queue_db", (str(self.queue_db_path),))
            rows = conn.execute(
                """
                SELECT dossiers.card_code
                FROM learning_dossiers dossiers
                LEFT JOIN learning_queue_db.learning_queue active_queue
                    ON active_queue.card_code = dossiers.card_code
                   AND active_queue.task_type IN ('verify_official_fields', 'promote_verified_dossiers')
                   AND active_queue.status IN ('queued', 'running')
                LEFT JOIN learning_queue_db.learning_queue recent_promotions
                    ON recent_promotions.card_code = dossiers.card_code
                   AND recent_promotions.task_type = 'promote_verified_dossiers'
                   AND recent_promotions.status = 'completed'
                   AND recent_promotions.updated_at >= datetime('now', '-12 hours')
                WHERE active_queue.id IS NULL
                  AND recent_promotions.id IS NULL
                  AND dossiers.verification_state IN ('pending-confirmation', 'local-bootstrap', 'source-backed', 'pending-review-image-conflict')
                ORDER BY
                    CASE
                        WHEN dossiers.verification_state IN ('pending-confirmation', 'source-backed', 'pending-review-image-conflict')
                        THEN 0
                        ELSE 1
                    END ASC,
                    dossiers.confidence DESC,
                    dossiers.updated_at ASC,
                    dossiers.card_code ASC
                LIMIT ?
                """,
                (max(batch_limit * 25, 250),),
            ).fetchall()
        results: list[str] = []
        for row in rows:
            card_code = str(row["card_code"] or "").strip().upper()
            if not card_code:
                continue
            if self.is_dossier_promotable(card_code):
                results.append(card_code)
            if len(results) >= batch_limit:
                break
        return results

    @staticmethod
    def _image_text_hints(*values: Any) -> str:
        return " ".join(str(value or "") for value in values if str(value or "").strip())

    def assess_image_candidate(
        self,
        *,
        record: NormalizedImageRecord,
        target_card_code: str,
        target_variant_key: str = "",
        width: int | None = None,
        height: int | None = None,
        analysis: VisualAnalysisResult | None = None,
    ) -> dict[str, Any]:
        source_entry = self.resolve_image_source_entry(record.source_id)
        target_code = str(target_card_code or record.card_code or "").strip().upper()
        target_variant = normalize_variant_key(target_variant_key)
        record_variant = normalize_variant_key(record.variant_key)
        language_policy = self.resolve_image_language_policy(
            source_id=record.source_id,
            source_url=record.source_url,
            metadata=dict(record.metadata or {}),
        )
        target_print_identity = self.build_print_identity(card_code=target_code or record.card_code, variant_key=target_variant)
        record_print_identity = self.build_print_identity(
            card_code=record.card_code,
            variant_key=record.variant_key,
            variant_label=record.variant_label,
            print_label=record.print_label,
        )
        duplicate_identity = self.derive_duplicate_identity(
            card_code=record.card_code,
            print_identity=record_print_identity,
            image_hash="",
            source_reference=record.source_reference,
            source_url=record.source_url,
            width=int(width or record.width or 0),
            height=int(height or record.height or 0),
            origin_language=self.normalize_language_code(language_policy["origin_language"]),
        )
        print_comparison = self.compare_print_profiles(
            target_profile=dict(target_print_identity.get("print_profile") or {}),
            candidate_profile=dict(record_print_identity.get("print_profile") or {}),
            target_variant_key=target_variant,
            candidate_variant_key=record_variant,
        )
        resolved_width = max(int(width or 0), int(record.width or 0))
        resolved_height = max(int(height or 0), int(record.height or 0))
        metadata = dict(record.metadata or {})
        hint_text = self._image_text_hints(
            record.source_url,
            record.source_reference,
            record.image_path,
            record.variant_key,
            metadata.get("quality_hint"),
            metadata.get("crop_hint"),
            metadata.get("clarity_hint"),
            analysis.ocr_text_excerpt if analysis is not None else "",
        )
        sample_flag = bool(record.sample_flag or IMAGE_SAMPLE_RE.search(hint_text))
        poor_quality_hint = bool(IMAGE_POOR_QUALITY_RE.search(hint_text))

        if target_code and record.card_code.strip().upper() == target_code:
            card_match_confidence = 1.0
        elif target_code and target_code in hint_text.upper():
            card_match_confidence = 0.72
        elif record.card_code:
            card_match_confidence = 0.45
        else:
            card_match_confidence = 0.2

        variant_match_confidence = float(print_comparison["variant_match_confidence"])
        art_family_confidence = float(print_comparison["art_family_confidence"])
        print_match_confidence = float(print_comparison["print_match_confidence"])

        if resolved_width >= 744 and resolved_height >= 1039:
            resolution_score = 1.0
        elif resolved_width >= 600 and resolved_height >= 840:
            resolution_score = 0.85
        elif resolved_width >= 420 and resolved_height >= 600:
            resolution_score = 0.65
        elif resolved_width >= 240 and resolved_height >= 340:
            resolution_score = 0.4
        elif resolved_width > 0 and resolved_height > 0:
            resolution_score = 0.15
        else:
            resolution_score = 0.25

        if resolved_width > 0 and resolved_height > 0:
            aspect_ratio = float(resolved_width) / float(max(resolved_height, 1))
            if 0.6 <= aspect_ratio <= 0.78:
                crop_confidence = 1.0
            elif 0.5 <= aspect_ratio <= 0.9:
                crop_confidence = 0.72
            else:
                crop_confidence = 0.25
        else:
            crop_confidence = 0.4

        clarity_score = min(1.0, (resolution_score * 0.75) + (crop_confidence * 0.25))
        if poor_quality_hint:
            clarity_score = max(0.0, clarity_score - 0.25)
            crop_confidence = max(0.0, crop_confidence - 0.15)

        quality_tier = "fallback_lowres"
        notes: list[str] = []
        if int(source_entry.trust_tier) == 1 and not sample_flag and resolution_score >= 0.6 and crop_confidence >= 0.55:
            quality_tier = "official_clean"
            notes.append("Official source with clean full-card image quality.")
        elif int(source_entry.trust_tier) == 1 and sample_flag and resolution_score >= 0.35 and crop_confidence >= 0.45:
            quality_tier = "official_sample"
            notes.append("Official source image is usable but marked with a sample watermark.")
        elif int(source_entry.trust_tier) <= 2 and not sample_flag and resolution_score >= 0.5 and crop_confidence >= 0.5:
            quality_tier = "trusted_scan"
            notes.append("Trusted clean scan is acceptable while better official art is unavailable.")
        elif resolution_score < 0.3 or poor_quality_hint:
            quality_tier = "fallback_lowres"
            notes.append("Only a fallback low-resolution or lower-clarity image is available.")

        reject_reasons: list[str] = []
        if card_match_confidence < 0.55:
            reject_reasons.append("card-code confidence too low")
        if print_match_confidence < 0.2:
            reject_reasons.append("print match confidence too low")
        if resolved_width and resolved_height and min(resolved_width, resolved_height) < 180:
            reject_reasons.append("image is too small")
        if crop_confidence < 0.2:
            reject_reasons.append("crop does not resemble a full card")
        if analysis is not None and str(analysis.verification_status or "") == "conflict":
            reject_reasons.append("visual analysis conflicts with expected card identity")

        if reject_reasons:
            quality_tier = "rejected"
            notes = reject_reasons

        trust_score = max(0.1, round(1.05 - (0.18 * int(source_entry.trust_tier)), 2))
        quality_score = round(
            max(0.0, (resolution_score * 40.0) + (clarity_score * 35.0) + (crop_confidence * 25.0)),
            2,
        )
        score = IMAGE_QUALITY_BASE_SCORES[quality_tier]
        score += round(card_match_confidence * 42.0, 2)
        score += round(print_match_confidence * 22.0, 2)
        score += round(variant_match_confidence * 16.0, 2)
        score += round(resolution_score * 18.0, 2)
        score += round(clarity_score * 12.0, 2)
        score += round(crop_confidence * 12.0, 2)
        score += round(art_family_confidence * 7.0, 2)
        if sample_flag:
            score -= 10.0
        if poor_quality_hint:
            score -= 8.0
        if analysis is not None:
            score += round(float(analysis.confidence or 0.0) * 8.0, 2)

        replacement_eligible = quality_tier != "official_clean"
        if quality_tier == "rejected":
            upgrade_status = "rejected"
        elif replacement_eligible:
            upgrade_status = "upgrade-recommended"
        else:
            upgrade_status = "stable"

        selection_confidence = round(
            max(
                0.18,
                min(
                    0.98,
                    (
                        (card_match_confidence * 0.42)
                        + (print_match_confidence * 0.18)
                        + (variant_match_confidence * 0.16)
                        + (trust_score * 0.14)
                        + ((quality_score / 100.0) * 0.18)
                        + (crop_confidence * 0.1)
                    ),
                ),
            ),
            2,
        )
        score_breakdown = {
            "card_code_match_confidence": round(card_match_confidence, 2),
            "print_match_confidence": round(print_match_confidence, 2),
            "variant_match_confidence": round(variant_match_confidence, 2),
            "art_family_confidence": round(art_family_confidence, 2),
            "print_relationship": print_comparison["relationship"],
            "mismatch_flags": list(print_comparison["mismatch_flags"]),
            "resolution_score": round(resolution_score, 2),
            "quality_score": quality_score,
            "trust_score": trust_score,
            "crop_confidence": round(crop_confidence, 2),
            "clarity_score": round(clarity_score, 2),
        }
        content_status = "rejected" if quality_tier == "rejected" else ("watermarked" if sample_flag else "candidate")
        print_identity = record_print_identity

        return {
            "print_id": print_identity["print_id"],
            "print_label": print_identity["print_label"],
            "variant_label": print_identity["variant_label"],
            "source_type": str(record.source_type or source_entry.source_type or ""),
            "quality_tier": quality_tier,
            "sample_flag": sample_flag,
            "source_trust_tier": int(source_entry.trust_tier),
            "source_trust_label": str(source_entry.trust_label or ""),
            "image_score": round(score, 2),
            "print_match_confidence": round(print_match_confidence, 2),
            "quality_score": quality_score,
            "trust_score": trust_score,
            "selection_confidence": selection_confidence,
            "card_code_match_confidence": round(card_match_confidence, 2),
            "variant_match_confidence": round(variant_match_confidence, 2),
            "art_family_confidence": round(art_family_confidence, 2),
            "clarity_score": round(clarity_score, 2),
            "crop_confidence": round(crop_confidence, 2),
            "selection_scope": "print_default",
            "selection_reason": f"phase3-{print_comparison['relationship']}",
            "content_status": content_status,
            "duplicate_group": duplicate_identity["duplicate_group"],
            "perceptual_hash": duplicate_identity["perceptual_hash"],
            "origin_language": self.normalize_language_code(language_policy["origin_language"]),
            "english_print_exists": bool(language_policy["english_print_exists"]),
            "display_policy": str(language_policy["display_policy"]),
            "provisional_language_display": bool(language_policy["provisional_language_display"]),
            "citation_payload": {
                "source_id": record.source_id,
                "source_reference": record.source_reference,
                "source_url": record.source_url,
                "origin_language": self.normalize_language_code(language_policy["origin_language"]),
                "english_print_exists": bool(language_policy["english_print_exists"]),
                "display_policy": str(language_policy["display_policy"]),
            },
            "score_breakdown": score_breakdown,
            "replacement_eligible": bool(replacement_eligible),
            "upgrade_status": upgrade_status,
            "review_notes": notes,
        }

    def candidate_should_replace_existing(
        self,
        *,
        candidate: dict[str, Any],
        existing: dict[str, Any] | None,
    ) -> bool:
        if existing is None:
            return candidate.get("quality_tier") != "rejected"
        existing_print_match = float(existing.get("print_match_confidence") or existing.get("variant_match_confidence") or 0.0)
        candidate_print_match = float(candidate.get("print_match_confidence") or candidate.get("variant_match_confidence") or 0.0)
        if candidate_print_match >= existing_print_match + 0.16:
            return True
        if existing_print_match >= candidate_print_match + 0.16:
            return False
        existing_tier = str(existing.get("quality_tier") or "fallback_lowres")
        candidate_tier = str(candidate.get("quality_tier") or "fallback_lowres")
        existing_rank = IMAGE_QUALITY_RANKS.get(existing_tier, 0)
        candidate_rank = IMAGE_QUALITY_RANKS.get(candidate_tier, 0)
        candidate_score = float(candidate.get("image_score") or 0.0)
        existing_score = float(existing.get("image_score") or 0.0)
        if str(existing.get("upgrade_status") or "") == "stable" and candidate_rank == existing_rank:
            return candidate_score > existing_score + 6.0
        if {candidate_tier, existing_tier} == {"trusted_scan", "official_sample"}:
            if candidate_score > existing_score + 8.0:
                return True
            if candidate_score + 8.0 < existing_score:
                return False
        if candidate_rank > existing_rank:
            return True
        if candidate_rank < existing_rank:
            return False
        return candidate_score > existing_score + 3.0

    def fetch_current_best_image_record(
        self,
        *,
        card_code: str,
        variant_key: str,
    ) -> dict[str, Any] | None:
        normalized_variant = normalize_variant_key(variant_key)
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_dossier_images
                WHERE card_code = ?
                  AND variant_key = ?
                ORDER BY is_current_best DESC, image_score DESC, last_reviewed_at DESC, downloaded_at DESC, id DESC
                LIMIT 1
                """,
                (card_code, normalized_variant),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["review_notes"] = json.loads(result.get("review_notes_json") or "[]")
        result["citation_payload"] = json.loads(result.get("citation_payload_json") or "{}")
        result["score_breakdown"] = json.loads(result.get("score_breakdown_json") or "{}")
        return result

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
        records = self.official_image_adapter.fetch_records(
            source_entry=source_entry,
            card_code=card_code,
            set_code=set_code,
            variant_key=variant_key,
            snapshot_path=snapshot_path,
            snapshot_url=snapshot_url,
        )
        target_card_code = str(card_code or "").strip().upper()
        return sorted(
            records,
            key=lambda item: self.assess_image_candidate(
                record=item,
                target_card_code=target_card_code,
                target_variant_key=variant_key,
                width=item.width,
                height=item.height,
            )["image_score"],
            reverse=True,
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
        last_reviewed_at: str,
        last_error: str,
        source_trust_tier: int,
        source_trust_label: str,
        quality_tier: str,
        sample_flag: bool,
        image_score: float,
        print_match_confidence: float,
        card_code_match_confidence: float,
        clarity_score: float,
        crop_confidence: float,
        review_notes: list[str],
        replacement_eligible: bool,
        upgrade_status: str,
        is_current_best: bool,
        print_id: str = "",
        print_label: str = "",
        variant_label: str = "",
        source_type: str = "",
        selection_scope: str = "",
        selection_reason: str = "",
        selection_confidence: float = 0.0,
        variant_match_confidence: float = 0.0,
        art_family_confidence: float = 0.0,
        quality_score: float = 0.0,
        trust_score: float = 0.0,
        duplicate_group: str = "",
        perceptual_hash: str = "",
        origin_language: str = "en",
        english_print_exists: bool = True,
        display_policy: str = "english-first",
        provisional_language_display: bool = False,
        bytes_size: int = 0,
        mime_type: str = "",
        content_status: str = "candidate",
        citation_payload: dict[str, Any] | None = None,
        score_breakdown: dict[str, Any] | None = None,
        superseded_by_image_id: int = 0,
    ) -> None:
        print_identity = self.build_print_identity(
            card_code=record.card_code,
            variant_key=record.variant_key,
            variant_label=variant_label or getattr(record, "variant_label", ""),
            print_label=print_label or getattr(record, "print_label", ""),
        )
        resolved_print_id = str(print_id or getattr(record, "print_id", "") or print_identity["print_id"])
        resolved_print_label = str(print_label or getattr(record, "print_label", "") or print_identity["print_label"])
        resolved_variant_label = str(variant_label or getattr(record, "variant_label", "") or print_identity["variant_label"])
        resolved_source_type = str(source_type or getattr(record, "source_type", "") or "")
        language_policy = self.resolve_image_language_policy(
            source_id=record.source_id,
            source_url=record.source_url,
            metadata=dict(getattr(record, "metadata", {}) or {}),
        )
        resolved_origin_language = self.normalize_language_code(origin_language or language_policy["origin_language"])
        resolved_english_print_exists = bool(
            language_policy["english_print_exists"] if english_print_exists is None else english_print_exists
        )
        resolved_display_policy = str(display_policy or language_policy["display_policy"])
        resolved_provisional_language_display = bool(
            provisional_language_display or (
                resolved_origin_language != "en" and not resolved_english_print_exists
            )
        )
        duplicate_identity = self.derive_duplicate_identity(
            card_code=record.card_code,
            print_identity=print_identity,
            image_hash=image_hash,
            source_reference=record.source_reference,
            source_url=record.source_url,
            width=width,
            height=height,
            origin_language=resolved_origin_language,
        )
        resolved_duplicate_group = str(duplicate_group or duplicate_identity["duplicate_group"])
        resolved_perceptual_hash = str(perceptual_hash or duplicate_identity["perceptual_hash"])
        resolved_citation_payload = dict(
            citation_payload
            or {
                "source_id": record.source_id,
                "source_reference": record.source_reference,
                "source_url": record.source_url,
                "image_hash": image_hash,
                "origin_language": resolved_origin_language,
                "english_print_exists": bool(resolved_english_print_exists),
                "display_policy": resolved_display_policy,
            }
        )
        resolved_score_breakdown = dict(
            score_breakdown
            or {
                "image_score": float(image_score),
                "quality_score": float(quality_score or image_score),
                "trust_score": float(trust_score),
                "print_match_confidence": float(print_match_confidence),
                "card_code_match_confidence": float(card_code_match_confidence),
                "variant_match_confidence": float(variant_match_confidence),
                "art_family_confidence": float(art_family_confidence),
                "clarity_score": float(clarity_score),
                "crop_confidence": float(crop_confidence),
                "duplicate_group": resolved_duplicate_group,
                "perceptual_hash": resolved_perceptual_hash,
                "origin_language": resolved_origin_language,
                "english_print_exists": bool(resolved_english_print_exists),
                "display_policy": resolved_display_policy,
            }
        )
        insert_placeholders = ", ".join("?" for _ in range(51))
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                f"""
                INSERT INTO learning_dossier_images (
                    card_code,
                    variant_key,
                    print_id,
                    print_label,
                    variant_label,
                    filename,
                    local_path,
                    source_id,
                    source_type,
                    source_reference,
                    source_url,
                    verification_state,
                    image_hash,
                    width,
                    height,
                    bytes_size,
                    mime_type,
                    source_trust_tier,
                    source_trust_label,
                    quality_tier,
                    sample_flag,
                    image_score,
                    print_match_confidence,
                    quality_score,
                    trust_score,
                    selection_confidence,
                    card_code_match_confidence,
                    variant_match_confidence,
                    art_family_confidence,
                    clarity_score,
                    crop_confidence,
                    selection_scope,
                    selection_reason,
                    content_status,
                    duplicate_group,
                    perceptual_hash,
                    origin_language,
                    english_print_exists,
                    display_policy,
                    provisional_language_display,
                    review_notes_json,
                    citation_payload_json,
                    score_breakdown_json,
                    replacement_eligible,
                    upgrade_status,
                    is_current_best,
                    superseded_by_image_id,
                    downloaded_at,
                    last_verified_at,
                    last_reviewed_at,
                    last_error
                ) VALUES ({insert_placeholders})
                ON CONFLICT(card_code, variant_key, source_id, filename) DO UPDATE SET
                    print_id = excluded.print_id,
                    print_label = excluded.print_label,
                    variant_label = excluded.variant_label,
                    local_path = excluded.local_path,
                    source_type = excluded.source_type,
                    source_reference = excluded.source_reference,
                    source_url = excluded.source_url,
                    verification_state = excluded.verification_state,
                    image_hash = excluded.image_hash,
                    width = excluded.width,
                    height = excluded.height,
                    bytes_size = excluded.bytes_size,
                    mime_type = excluded.mime_type,
                    source_trust_tier = excluded.source_trust_tier,
                    source_trust_label = excluded.source_trust_label,
                    quality_tier = excluded.quality_tier,
                    sample_flag = excluded.sample_flag,
                    image_score = excluded.image_score,
                    print_match_confidence = excluded.print_match_confidence,
                    quality_score = excluded.quality_score,
                    trust_score = excluded.trust_score,
                    selection_confidence = excluded.selection_confidence,
                    card_code_match_confidence = excluded.card_code_match_confidence,
                    variant_match_confidence = excluded.variant_match_confidence,
                    art_family_confidence = excluded.art_family_confidence,
                    clarity_score = excluded.clarity_score,
                    crop_confidence = excluded.crop_confidence,
                    selection_scope = excluded.selection_scope,
                    selection_reason = excluded.selection_reason,
                    content_status = excluded.content_status,
                    duplicate_group = excluded.duplicate_group,
                    perceptual_hash = excluded.perceptual_hash,
                    origin_language = excluded.origin_language,
                    english_print_exists = excluded.english_print_exists,
                    display_policy = excluded.display_policy,
                    provisional_language_display = excluded.provisional_language_display,
                    review_notes_json = excluded.review_notes_json,
                    citation_payload_json = excluded.citation_payload_json,
                    score_breakdown_json = excluded.score_breakdown_json,
                    replacement_eligible = excluded.replacement_eligible,
                    upgrade_status = excluded.upgrade_status,
                    is_current_best = excluded.is_current_best,
                    superseded_by_image_id = excluded.superseded_by_image_id,
                    downloaded_at = excluded.downloaded_at,
                    last_verified_at = excluded.last_verified_at,
                    last_reviewed_at = excluded.last_reviewed_at,
                    last_error = excluded.last_error
                """,
                (
                    record.card_code,
                    normalize_variant_key(record.variant_key),
                    resolved_print_id,
                    resolved_print_label,
                    resolved_variant_label,
                    filename,
                    local_path,
                    record.source_id,
                    resolved_source_type,
                    record.source_reference,
                    record.source_url,
                    verification_state,
                    image_hash,
                    int(width),
                    int(height),
                    int(bytes_size),
                    mime_type,
                    int(source_trust_tier),
                    source_trust_label,
                    quality_tier,
                    1 if sample_flag else 0,
                    float(image_score),
                    float(print_match_confidence),
                    float(quality_score or image_score),
                    float(trust_score),
                    float(selection_confidence),
                    float(card_code_match_confidence),
                    float(variant_match_confidence),
                    float(art_family_confidence),
                    float(clarity_score),
                    float(crop_confidence),
                    selection_scope,
                    selection_reason,
                    content_status,
                    resolved_duplicate_group,
                    resolved_perceptual_hash,
                    resolved_origin_language,
                    1 if resolved_english_print_exists else 0,
                    resolved_display_policy,
                    1 if resolved_provisional_language_display else 0,
                    json.dumps(review_notes, ensure_ascii=False),
                    json.dumps(resolved_citation_payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(resolved_score_breakdown, ensure_ascii=False, sort_keys=True),
                    1 if replacement_eligible else 0,
                    upgrade_status,
                    1 if is_current_best else 0,
                    int(superseded_by_image_id),
                    downloaded_at,
                    last_verified_at,
                    last_reviewed_at,
                    last_error,
                ),
            )
            if is_current_best:
                conn.execute(
                    """
                    UPDATE learning_dossier_images
                    SET is_current_best = 0
                    WHERE card_code = ?
                      AND variant_key = ?
                      AND NOT (source_id = ? AND filename = ?)
                    """,
                    (
                        record.card_code,
                        normalize_variant_key(record.variant_key),
                        record.source_id,
                        filename,
                    ),
                )
        self.upsert_learning_print(
            print_identity=print_identity,
            verification_state="source-backed" if verification_state == "verified" else "scaffolded",
            match_confidence=max(float(card_code_match_confidence), float(variant_match_confidence)),
            supporting_sources=[resolved_citation_payload],
            citation_payload=resolved_citation_payload,
            verified_at=last_verified_at,
        )
        if is_current_best:
            self.refresh_scaffolded_image_selections(
                card_code=record.card_code,
                variant_key=normalize_variant_key(record.variant_key),
                reason=selection_reason or "phase1-current-best-image",
            )

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
        result = {key: row[key] for key in row.keys()}
        result["review_notes"] = json.loads(result.get("review_notes_json") or "[]")
        result["citation_payload"] = json.loads(result.get("citation_payload_json") or "{}")
        result["score_breakdown"] = json.loads(result.get("score_breakdown_json") or "{}")
        return result

    def fetch_latest_image_source_candidate(
        self,
        *,
        card_code: str,
        source_id: str,
    ) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_dossier_images
                WHERE card_code = ?
                  AND source_id = ?
                ORDER BY last_reviewed_at DESC, downloaded_at DESC, id DESC
                LIMIT 1
                """,
                (card_code, str(source_id or "").strip().lower()),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["review_notes"] = json.loads(result.get("review_notes_json") or "[]")
        result["citation_payload"] = json.loads(result.get("citation_payload_json") or "{}")
        result["score_breakdown"] = json.loads(result.get("score_breakdown_json") or "{}")
        return result

    def fetch_learning_print(
        self,
        *,
        card_code: str,
        variant_key: str = "",
    ) -> dict[str, Any] | None:
        normalized_variant = normalize_variant_key(variant_key)
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_dossier_prints
                WHERE card_code = ?
                  AND variant_key = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (card_code, normalized_variant),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["supporting_sources"] = json.loads(result.get("supporting_sources_json") or "[]")
        result["citation_payload"] = json.loads(result.get("citation_payload_json") or "{}")
        return result

    def fetch_image_selection(
        self,
        *,
        card_code: str,
        selection_scope: str,
        print_id: str = "",
    ) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_image_selections
                WHERE card_code = ?
                  AND selection_scope = ?
                  AND print_id = ?
                ORDER BY reviewed_at DESC, id DESC
                LIMIT 1
                """,
                (card_code, selection_scope, print_id),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["comparison_summary"] = json.loads(result.get("comparison_summary_json") or "{}")
        result["citation_payload"] = json.loads(result.get("citation_payload_json") or "{}")
        return result

    def fetch_image_candidate_by_id(self, candidate_id: int) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_dossier_images
                WHERE id = ?
                LIMIT 1
                """,
                (int(candidate_id),),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["review_notes"] = json.loads(result.get("review_notes_json") or "[]")
        result["citation_payload"] = json.loads(result.get("citation_payload_json") or "{}")
        result["score_breakdown"] = json.loads(result.get("score_breakdown_json") or "{}")
        return result

    def fetch_project_card_row(self, card_code: str) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.project_db_path)) as conn:
            row = conn.execute(
                "SELECT * FROM cards WHERE canonical_code = ?",
                (str(card_code or "").strip().upper(),),
            ).fetchone()
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    def fetch_project_variant_row(
        self,
        *,
        card_id: int,
        variant_key: str,
    ) -> dict[str, Any] | None:
        normalized_variant = normalize_variant_key(variant_key)
        lookup_keys = [normalized_variant]
        if not normalized_variant:
            lookup_keys.extend(["base", ""])
        elif normalized_variant != "base":
            lookup_keys.append("base" if normalized_variant == "" else normalized_variant)
        lookup_keys = list(dict.fromkeys(lookup_keys))
        placeholders = ", ".join("?" for _ in lookup_keys)
        with closing(connect_sqlite(self.project_db_path)) as conn:
            row = conn.execute(
                f"""
                SELECT *
                FROM card_variants
                WHERE card_id = ?
                  AND variant_key IN ({placeholders})
                ORDER BY
                    CASE WHEN variant_key = ? THEN 0 ELSE 1 END,
                    id ASC
                LIMIT 1
                """,
                (int(card_id), *lookup_keys, normalized_variant),
            ).fetchone()
        if row is None:
            return None
        return {key: row[key] for key in row.keys()}

    def plan_image_upgrade(
        self,
        *,
        card_code: str,
        variant_key: str,
    ) -> dict[str, Any]:
        evaluation = self.evaluate_selection_scope(
            card_code=card_code,
            variant_key=variant_key,
            selection_scope="print_default",
        )
        ranked = list(evaluation.get("ranked_candidates") or [])
        winner = evaluation.get("winner")
        target_print = self.build_print_identity(card_code=card_code, variant_key=variant_key)
        current_selection = self.fetch_image_selection(
            card_code=card_code,
            selection_scope="print_default",
            print_id=target_print["print_id"],
        )
        current_candidate = (
            self.fetch_image_candidate_by_id(int(current_selection.get("image_candidate_id") or 0))
            if current_selection is not None
            else self.fetch_current_best_image_record(card_code=card_code, variant_key=variant_key)
        )
        if not isinstance(winner, dict):
            return {
                "action": "defer-no-viable-candidate",
                "card_code": card_code,
                "variant_key": normalize_variant_key(variant_key),
                "reason": "No viable candidate was available during upgrade scan.",
                "evaluation": evaluation,
            }
        winner_candidate = dict(winner.get("candidate") or {})
        if current_candidate is None:
            return {
                "action": "promote-first-winner",
                "card_code": card_code,
                "variant_key": normalize_variant_key(variant_key),
                "winner": winner,
                "current_candidate": None,
                "reason": "No current stable winner existed yet.",
                "evaluation": evaluation,
            }
        current_eval = next(
            (item for item in ranked if int(item.get("candidate_id") or 0) == int(current_candidate.get("id") or 0)),
            None,
        )
        if current_eval is None:
            current_eval = self.score_candidate_for_scope(
                candidate=current_candidate,
                selection_scope="print_default",
                target_variant_key=variant_key,
                duplicate_info=self.analyze_duplicate_family(ranked and [item.get("candidate") for item in ranked if isinstance(item.get("candidate"), dict)] or [current_candidate]).get(
                    int(current_candidate.get("id") or 0),
                    {},
                ),
            )
        current_score = float(current_eval.get("selection_score") or 0.0)
        winner_score = float(winner.get("selection_score") or 0.0)
        score_margin = round(winner_score - current_score, 2)
        duplicate_detail = self.inspect_duplicate_relationship(current_candidate, winner_candidate)
        relationship = str(duplicate_detail.get("duplicate_relationship") or "distinct-family")
        current_trust = float(current_candidate.get("trust_score") or 0.0)
        winner_trust = float(winner_candidate.get("trust_score") or 0.0)
        current_quality = float(current_candidate.get("quality_score") or current_candidate.get("image_score") or 0.0)
        winner_quality = float(winner_candidate.get("quality_score") or winner_candidate.get("image_score") or 0.0)
        current_print_match = float(current_candidate.get("print_match_confidence") or current_candidate.get("variant_match_confidence") or 0.0)
        winner_print_match = float(winner_candidate.get("print_match_confidence") or winner_candidate.get("variant_match_confidence") or 0.0)
        quality_delta = round(winner_quality - current_quality, 1)
        trust_delta = round(winner_trust - current_trust, 2)
        print_match_delta = round(winner_print_match - current_print_match, 2)

        if int(winner.get("candidate_id") or 0) == int(current_candidate.get("id") or 0):
            return {
                "action": "preserve-current-winner",
                "card_code": card_code,
                "variant_key": normalize_variant_key(variant_key),
                "winner": winner,
                "current_candidate": current_candidate,
                "reason": "Current winner remains the best candidate.",
                "score_margin": 0.0,
                "duplicate_relationship": relationship,
                "evaluation": evaluation,
            }

        replace_threshold = 12.0
        if relationship == "same-family-cautious":
            replace_threshold = 14.0
        elif relationship == "same-art-different-crop-or-treatment":
            replace_threshold = 16.0
        elif relationship == "exact-duplicate":
            replace_threshold = 18.0

        if winner_print_match >= current_print_match + 0.12 and score_margin >= max(replace_threshold - 3.0, 8.0):
            action = "replace-meaningful-print-upgrade"
        elif (
            winner_trust >= current_trust + 0.12
            and winner_quality >= current_quality + 8.0
            and score_margin >= replace_threshold
        ):
            action = "replace-meaningful-trust-quality-upgrade"
        elif (
            relationship in {"same-family-cautious", "same-art-different-crop-or-treatment", "exact-duplicate"}
            and winner_quality >= current_quality + 18.0
            and score_margin >= replace_threshold
        ):
            action = "replace-meaningful-quality-upgrade"
        elif (
            relationship == "same-art-different-crop-or-treatment"
            and winner_quality >= current_quality + 12.0
            and score_margin >= replace_threshold
        ):
            action = "replace-meaningful-crop-treatment-upgrade"
        elif score_margin >= replace_threshold + 4.0 and relationship == "distinct-family":
            action = "replace-clear-superior-winner"
        else:
            action = "preserve-stable-winner"

        blocked_reason = ""
        threshold_signal = ""
        decision_summary = ""
        if action == "preserve-stable-winner":
            if relationship in {"exact-duplicate", "same-art-different-crop-or-treatment", "same-family-cautious"}:
                blocked_reason = (
                    f"Challenger is a {relationship}; score margin {score_margin:.1f} "
                    f"did not exceed threshold {replace_threshold:.1f}."
                )
                threshold_signal = {
                    "exact-duplicate": "exact-duplicate-threshold",
                    "same-art-different-crop-or-treatment": "crop-treatment-threshold",
                    "same-family-cautious": "same-family-threshold",
                }.get(relationship, "near-duplicate-threshold")
            else:
                blocked_reason = (
                    f"Score margin {score_margin:.1f} insufficient (threshold {replace_threshold:.1f})."
                )
                threshold_signal = "distinct-family-threshold"
            decision_summary = blocked_reason
        elif action == "replace-meaningful-print-upgrade":
            threshold_signal = "meaningful-print-match-threshold"
            decision_summary = (
                f"Promoted challenger for stronger print match (+{print_match_delta:.2f}) "
                f"with margin {score_margin:.1f} against threshold {replace_threshold:.1f}."
            )
        elif action == "replace-meaningful-trust-quality-upgrade":
            threshold_signal = "meaningful-trust-quality-threshold"
            decision_summary = (
                f"Promoted challenger for trust/quality gains (trust +{trust_delta:.2f}, quality +{quality_delta:.1f}) "
                f"with margin {score_margin:.1f} against threshold {replace_threshold:.1f}."
            )
        elif action == "replace-meaningful-quality-upgrade":
            threshold_signal = "meaningful-same-family-quality-threshold"
            decision_summary = (
                f"Promoted challenger within the same family for a quality gain of {quality_delta:.1f} "
                f"with margin {score_margin:.1f} against threshold {replace_threshold:.1f}."
            )
        elif action == "replace-meaningful-crop-treatment-upgrade":
            threshold_signal = "meaningful-crop-treatment-threshold"
            decision_summary = (
                f"Promoted challenger for a clearer crop/treatment presentation (quality +{quality_delta:.1f}) "
                f"with margin {score_margin:.1f} against threshold {replace_threshold:.1f}."
            )
        elif action == "replace-clear-superior-winner":
            threshold_signal = "clear-superiority-threshold"
            decision_summary = (
                f"Promoted challenger as a distinct-family winner with margin {score_margin:.1f} "
                f"clearing threshold {replace_threshold + 4.0:.1f}."
            )

        upgrade_reasoning = {
            "relationship": relationship,
            "relationship_signals": list(duplicate_detail.get("classification_signals") or []),
            "family_reasoning": str(duplicate_detail.get("family_reasoning") or ""),
            "replace_threshold": replace_threshold,
            "score_margin": score_margin,
            "winner_print_match": round(winner_print_match, 3),
            "current_print_match": round(current_print_match, 3),
            "print_match_delta": print_match_delta,
            "winner_quality": round(winner_quality, 1),
            "current_quality": round(current_quality, 1),
            "quality_delta": quality_delta,
            "winner_trust": round(winner_trust, 2),
            "current_trust": round(current_trust, 2),
            "trust_delta": trust_delta,
            "threshold_signal": threshold_signal,
            "blocked_reason": blocked_reason,
            "decision_summary": decision_summary,
        }

        return {
            "action": action,
            "card_code": card_code,
            "variant_key": normalize_variant_key(variant_key),
            "winner": winner,
            "current_candidate": current_candidate,
            "score_margin": score_margin,
            "duplicate_relationship": relationship,
            "replace_threshold": replace_threshold,
            "upgrade_reason": action,
            "upgrade_reasoning": upgrade_reasoning,
            "reason": (
                f"winner_score={winner_score}, current_score={current_score}, relationship={relationship}, "
                f"winner_print_match={winner_print_match}, current_print_match={current_print_match}"
            ),
            "evaluation": evaluation,
        }

    def sync_image_selection_to_runtime(
        self,
        *,
        card_code: str,
        selection_scope: str,
        variant_key: str = "",
    ) -> dict[str, Any]:
        target_print = self.build_print_identity(card_code=card_code, variant_key=variant_key)
        selection = self.fetch_image_selection(
            card_code=card_code,
            selection_scope=selection_scope,
            print_id=target_print["print_id"],
        )
        if selection_scope not in {"card_default", "print_default"}:
            return {
                "status": "deferred-unsupported-scope",
                "reason": "Only card_default and print_default selections are safe to sync in Phase 3.",
            }
        if selection is None:
            return {"status": "deferred-no-selection", "reason": "No image selection row exists yet."}
        candidate = self.fetch_image_candidate_by_id(int(selection.get("image_candidate_id") or 0))
        if candidate is None:
            return {"status": "deferred-missing-candidate", "reason": "Selected image candidate could not be loaded."}
        comparison_summary = dict(selection.get("comparison_summary") or {})
        if str(candidate.get("verification_state") or "").strip().lower() != "verified":
            return {"status": "deferred-unverified", "reason": "Selected image candidate is not verified."}
        if float(selection.get("selection_confidence") or 0.0) < 0.78:
            return {"status": "deferred-low-confidence", "reason": "Selection confidence is below the runtime sync threshold."}
        score_margin = float(comparison_summary.get("score_margin") or 0.0)
        duplicate_relationship = str(comparison_summary.get("runner_up_duplicate_relationship") or "")
        minimum_margin = 6.0
        if duplicate_relationship == "same-family-cautious":
            minimum_margin = 8.0
        elif duplicate_relationship in {"same-art-different-crop-or-treatment", "exact-duplicate"}:
            minimum_margin = 10.0
        if score_margin < minimum_margin:
            return {"status": "deferred-low-margin", "reason": "Selection margin is too small for safe runtime sync."}
        if str(candidate.get("quality_tier") or "") == "rejected":
            return {"status": "deferred-rejected", "reason": "Rejected image candidates cannot sync to runtime."}

        origin_language = self.normalize_language_code(candidate.get("origin_language") or selection.get("origin_language") or "en")
        english_print_exists = bool(int(candidate.get("english_print_exists") or selection.get("english_print_exists") or 0))
        display_policy = str(candidate.get("display_policy") or selection.get("display_policy") or "english-first")
        provisional_language_display = bool(int(candidate.get("provisional_language_display") or selection.get("provisional_language_display") or 0))
        if origin_language != "en" and english_print_exists:
            return {
                "status": "deferred-english-preferred",
                "reason": "A non-English candidate will not override an available English print.",
                "origin_language": origin_language,
            }

        project_card = self.fetch_project_card_row(card_code)
        if project_card is None:
            return {"status": "deferred-missing-project-card", "reason": "Project Miru runtime card row does not exist yet."}
        sync_variant_key = normalize_variant_key(variant_key)
        if selection_scope == "card_default":
            sync_variant_key = ""
        project_variant = self.fetch_project_variant_row(card_id=int(project_card["id"]), variant_key=sync_variant_key)
        with closing(connect_sqlite(self.project_db_path)) as conn:
            if project_variant is None:
                if sync_variant_key:
                    return {
                        "status": "deferred-missing-project-variant",
                        "reason": "Variant row is missing in Project Miru runtime structures.",
                    }
                conn.execute(
                    """
                    INSERT INTO card_variants (
                        card_id,
                        variant_key,
                        variant_label,
                        print_id,
                        release_set_code,
                        release_set_name,
                        image_path,
                        image_url,
                        print_treatment,
                        illustration_type,
                        source_attribution_json,
                        sync_status,
                        unresolved_reason,
                        source,
                        is_base,
                        is_alt,
                        is_sp,
                        has_variant_evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(project_card["id"]),
                        "base",
                        "Base",
                        target_print["print_id"],
                        target_print["release_set_code"],
                        target_print["release_set_name"],
                        str(candidate.get("local_path") or ""),
                        str(candidate.get("source_url") or ""),
                        target_print["print_label"],
                        target_print["illustration_type"],
                        json.dumps({}, ensure_ascii=False, sort_keys=True),
                        "",
                        "",
                        "miru-image-intel",
                        1,
                        0,
                        0,
                        1,
                    ),
                )
                project_variant = self.fetch_project_variant_row(card_id=int(project_card["id"]), variant_key=sync_variant_key)
            if project_variant is None:
                return {"status": "deferred-runtime-write-failed", "reason": "Runtime variant row could not be prepared safely."}

            existing_attribution = {}
            try:
                existing_attribution = json.loads(str(project_variant.get("source_attribution_json") or "{}"))
            except json.JSONDecodeError:
                existing_attribution = {}
            selection_payload = {
                "selection_scope": selection_scope,
                "selection_confidence": float(selection.get("selection_confidence") or 0.0),
                "selection_reason": str(selection.get("selection_reason") or ""),
                "quality_tier": str(candidate.get("quality_tier") or ""),
                "image_score": float(candidate.get("image_score") or 0.0),
                "print_match_confidence": float(candidate.get("print_match_confidence") or 0.0),
                "variant_match_confidence": float(candidate.get("variant_match_confidence") or 0.0),
                "art_family_confidence": float(candidate.get("art_family_confidence") or 0.0),
                "origin_language": origin_language,
                "english_print_exists": bool(english_print_exists),
                "display_policy": display_policy,
                "provisional_language_display": bool(provisional_language_display),
                "duplicate_group": str(candidate.get("duplicate_group") or ""),
                "perceptual_hash": str(candidate.get("perceptual_hash") or ""),
                "comparison_summary": comparison_summary,
                "updated_at": utc_timestamp(),
            }
            merged_attribution = dict(existing_attribution)
            merged_attribution["miru_image_selection"] = selection_payload
            sync_status = "miru-image-provisional-non-english" if provisional_language_display else "miru-image-verified"
            unresolved_reason = "" if not provisional_language_display else "Using verified translated-origin image until English print exists."
            conn.execute(
                """
                UPDATE card_variants
                SET image_path = ?,
                    image_url = ?,
                    variant_label = ?,
                    print_id = ?,
                    release_set_code = ?,
                    release_set_name = ?,
                    print_treatment = ?,
                    illustration_type = ?,
                    source_attribution_json = ?,
                    sync_status = ?,
                    unresolved_reason = ?,
                    source = ?
                WHERE id = ?
                """,
                (
                    str(candidate.get("local_path") or ""),
                    str(candidate.get("source_url") or ""),
                    str(target_print["variant_label"] or project_variant.get("variant_label") or ""),
                    str(target_print["print_id"] or project_variant.get("print_id") or ""),
                    str(target_print["release_set_code"] or project_variant.get("release_set_code") or ""),
                    str(target_print["release_set_name"] or project_variant.get("release_set_name") or ""),
                    str(target_print["print_label"] or project_variant.get("print_treatment") or ""),
                    str(target_print["illustration_type"] or project_variant.get("illustration_type") or ""),
                    json.dumps(merged_attribution, ensure_ascii=False, sort_keys=True),
                    sync_status,
                    unresolved_reason,
                    "miru-image-intel",
                    int(project_variant["id"]),
                ),
            )
        return {
            "status": "synced",
            "sync_status": sync_status,
            "variant_key": normalize_variant_key(sync_variant_key) or "base",
            "origin_language": origin_language,
            "english_print_exists": bool(english_print_exists),
            "display_policy": display_policy,
            "project_variant_id": int(project_variant["id"]),
        }

    def fetch_cached_image_analysis(
        self,
        *,
        card_code: str,
        variant_key: str,
        source_id: str,
        image_hash: str,
    ) -> dict[str, Any] | None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM learning_image_analysis
                WHERE card_code = ?
                  AND variant_key = ?
                  AND source_id = ?
                  AND image_hash = ?
                ORDER BY analyzed_at DESC, id DESC
                LIMIT 1
                """,
                (card_code, normalize_variant_key(variant_key), source_id, image_hash),
            ).fetchone()
        if row is None:
            return None
        result = {key: row[key] for key in row.keys()}
        result["extracted_fields"] = json.loads(result.get("extracted_fields_json") or "{}")
        result["source_rollup"] = json.loads(result.get("source_rollup_json") or "{}")
        result["conflict_flags"] = json.loads(result.get("conflict_flags_json") or "[]")
        result["analysis_notes"] = json.loads(result.get("analysis_notes_json") or "[]")
        return result

    def store_image_analysis(
        self,
        *,
        card_code: str,
        variant_key: str,
        source_id: str,
        image_hash: str,
        local_path: str,
        analysis: VisualAnalysisResult,
    ) -> None:
        with closing(connect_sqlite(self.dossier_db_path)) as conn:
            conn.execute(
                """
                INSERT INTO learning_image_analysis (
                    card_code,
                    variant_key,
                    source_id,
                    image_hash,
                    local_path,
                    extraction_method,
                    extracted_fields_json,
                    confidence,
                    verification_status,
                    source_rollup_json,
                    conflict_flags_json,
                    analysis_notes_json,
                    ocr_text_excerpt,
                    analyzed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(card_code, variant_key, source_id, image_hash) DO UPDATE SET
                    local_path = excluded.local_path,
                    extraction_method = excluded.extraction_method,
                    extracted_fields_json = excluded.extracted_fields_json,
                    confidence = excluded.confidence,
                    verification_status = excluded.verification_status,
                    source_rollup_json = excluded.source_rollup_json,
                    conflict_flags_json = excluded.conflict_flags_json,
                    analysis_notes_json = excluded.analysis_notes_json,
                    ocr_text_excerpt = excluded.ocr_text_excerpt,
                    analyzed_at = excluded.analyzed_at
                """,
                (
                    card_code,
                    normalize_variant_key(variant_key),
                    source_id.strip().lower(),
                    image_hash,
                    local_path,
                    analysis.extraction_method,
                    json.dumps(analysis.extracted_fields, ensure_ascii=False, sort_keys=True),
                    float(analysis.confidence),
                    analysis.verification_status,
                    json.dumps(analysis.source_rollup, ensure_ascii=False, sort_keys=True),
                    json.dumps(analysis.conflict_flags, ensure_ascii=False),
                    json.dumps(analysis.analysis_notes, ensure_ascii=False),
                    analysis.ocr_text_excerpt,
                    analysis.analyzed_at,
                ),
            )

    def apply_image_analysis_to_dossier(
        self,
        *,
        card_code: str,
        analysis: VisualAnalysisResult,
    ) -> None:
        existing = self.fetch_dossier(card_code)
        if existing is None:
            return
        basic_facts = dict(existing.get("basic_facts") or {})
        visual_payload = {
            "extraction_method": analysis.extraction_method,
            "extracted_fields": dict(analysis.extracted_fields),
            "confidence": float(analysis.confidence),
            "verification_status": analysis.verification_status,
            "source_rollup": dict(analysis.source_rollup),
            "conflict_flags": list(analysis.conflict_flags),
            "analysis_notes": list(analysis.analysis_notes),
            "analyzed_at": analysis.analyzed_at,
            "ocr_text_excerpt": analysis.ocr_text_excerpt,
        }
        basic_facts["visual_analysis"] = visual_payload
        current_best_image = self.fetch_current_best_image_record(card_code=card_code, variant_key="")
        if current_best_image is not None:
            basic_facts["preferred_image"] = {
                "local_path": str(current_best_image.get("local_path") or ""),
                "source_id": str(current_best_image.get("source_id") or ""),
                "print_id": str(current_best_image.get("print_id") or ""),
                "print_label": str(current_best_image.get("print_label") or ""),
                "variant_label": str(current_best_image.get("variant_label") or ""),
                "quality_tier": str(current_best_image.get("quality_tier") or ""),
                "sample_flag": bool(int(current_best_image.get("sample_flag") or 0)),
                "image_score": float(current_best_image.get("image_score") or 0.0),
                "selection_confidence": float(current_best_image.get("selection_confidence") or 0.0),
                "upgrade_status": str(current_best_image.get("upgrade_status") or ""),
                "last_reviewed_at": str(current_best_image.get("last_reviewed_at") or ""),
            }
        if not analysis.conflict_flags and str(analysis.extracted_fields.get("card_code") or "").upper() == card_code.upper():
            basic_facts["image_confirmed_card_code"] = True
        elif analysis.conflict_flags:
            basic_facts["image_conflict_flags"] = list(analysis.conflict_flags)
        source_count = int((analysis.source_rollup or {}).get("source_count") or 0)
        verification_state = str(existing.get("verification_state") or "placeholder")
        confidence = float(existing.get("confidence") or 0.0)
        if analysis.conflict_flags:
            verification_state = "pending-review-image-conflict" if source_count >= 2 else "pending-confirmation"
        elif analysis.verification_status == "verified_with_image_confirmation":
            verification_state = "verified-with-image-confirmation"
            confidence = max(confidence, 0.9)
        elif analysis.verification_status == "source_backed_image_confirmation":
            confidence = max(confidence, 0.7)
        existing["basic_facts"] = basic_facts
        existing["verification_state"] = verification_state
        existing["confidence"] = round(confidence, 2)
        self.upsert_dossier(existing)

    def maybe_send_visual_learning_update(self) -> None:
        try:
            with closing(connect_sqlite(self.dossier_db_path)) as conn:
                confirmed_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM learning_image_analysis
                    WHERE analyzed_at >= datetime('now', 'start of day')
                      AND verification_status IN ('source_backed_image_confirmation', 'verified_with_image_confirmation')
                    """
                ).fetchone()
                conflict_row = conn.execute(
                    """
                    SELECT card_code
                    FROM learning_image_analysis
                    WHERE analyzed_at >= datetime('now', 'start of day')
                      AND verification_status = 'conflict'
                    ORDER BY analyzed_at DESC, id DESC
                    LIMIT 1
                    """
                ).fetchone()
        except sqlite3.Error:
            return
        confirmed_total = int(confirmed_row["total"] or 0) if confirmed_row is not None else 0
        if confirmed_total > 0 and confirmed_total % 25 == 0:
            self.send_operator_notification(
                f"visual_learning_milestone_{confirmed_total}",
                f"Miru expanded visual card understanding for {confirmed_total} cards today.",
                cooldown_seconds=43200,
            )
        if conflict_row is not None:
            card_code = str(conflict_row["card_code"] or "").strip().upper()
            if card_code:
                self.send_operator_notification(
                    f"visual_conflict_{card_code}",
                    f"Miru found a conflict between source data and image data for {card_code}. Confidence remains conservative.",
                    cooldown_seconds=21600,
                )

    def process_task(self, task: LearningTask) -> dict[str, Any]:
        handler = TASK_HANDLERS.get(task.task_type)
        if handler is None:
            raise KeyError(f"Unknown learning task type: {task.task_type}")

        validation = self.validate_learning_reference(
            card_code=task.card_code,
            set_code=str(task.task_payload.get("set_code") or "") if isinstance(task.task_payload, dict) else "",
        )
        if (task.card_code or str(task.task_payload.get("set_code") or "").strip()) and not validation["ok"]:
            self.note_invalid_reference(
                card_code=task.card_code,
                set_code=str(task.task_payload.get("set_code") or "") if isinstance(task.task_payload, dict) else "",
                reason=str(validation["reason"] or "invalid_set_reference"),
            )
            return {
                "message": f"Skipped invalid set reference for {task.card_code or task.task_payload.get('set_code')}.",
                "task_type": task.task_type,
                "card_code": task.card_code,
                "invalid_reason": str(validation["reason"] or "invalid_set_reference"),
                "skip_status": "skipped_invalid_set",
            }

        current_image_task = (
            task.label
            if task.task_type in ({"fetch_card_image", "verify_card_image", "refresh_card_image"} | IMAGE_INTELLIGENCE_TASK_TYPES)
            else ""
        )
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
        try:
            result = self.process_task(task)
        except Exception as exc:
            self.fail_task(task, exc)
            return {"ok": False, "task": task.label, "error": f"{exc.__class__.__name__}: {exc}"}

        message = str(result.get("message") or f"Completed {task.label}")
        if str(result.get("skip_status") or "") == "skipped_invalid_set":
            self.skip_invalid_task(task, message, source_reference=str(result.get("source_reference") or ""))
            return {"ok": True, "skipped": True, "task": task.label, **result}
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
        suppress_notifications: bool = False,
    ) -> dict[str, Any]:
        supporting_sources = self.build_registry_supporting_entries(record.card_code)
        result = self.project_sync.queue_validated_record(
            record,
            task_type=task_type,
            additional_sources=supporting_sources,
        )
        if not suppress_notifications:
            self.notify_project_sync_outcomes(result)
        return result

    def bulk_queue_project_sync(
        self,
        records: list[NormalizedSourceRecord],
        *,
        task_type: str,
        suppress_notifications: bool = False,
        reason: str = "bulk-registry-ingest",
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for record in records:
            items.append(
                {
                    "record": record,
                    "task_type": task_type,
                    "additional_sources": self.build_registry_supporting_entries(record.card_code),
                }
            )
        result = self.project_sync.queue_validated_records(
            items,
            task_type=task_type,
            reason=reason,
            progress_callback=progress_callback,
        )
        if not suppress_notifications:
            self.notify_project_sync_outcomes(result)
        return result

    def notify_project_sync_outcomes(self, sync_result: dict[str, Any]) -> None:
        for outcome in sync_result.get("outcomes", []) if isinstance(sync_result, dict) else []:
            status = str(outcome.get("status") or "").strip().lower()
            if status in {"synced", "pending_confirmation"}:
                continue

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
        if not self.acquire_configured_lock():
            return {"ok": False, "message": self._lock_issue_message or "Another Miru learning worker instance already holds the configured lock."}
        self.prime_work(
            card_code=card_code,
            variant_key=variant_key,
            task_type=task_type,
            source_id=source_id,
            task_payload=task_payload,
        )
        try:
            self.recover_stale_running_tasks()
            if self.queue_counts()["queued"] == 0:
                self.run_learning_seeder(force=True)
            result = self.process_one()
            if result is None:
                self.run_learning_seeder(force=True)
                result = self.process_one()
            if result is None:
                self.update_status(current_state="idle", current_task_label="", current_card_code="", current_task_type="")
                return {"ok": True, "message": "No queued learning work was available."}
            return result
        finally:
            self.flush_pending_project_sync(reason="run-once")
            self.release_configured_lock()

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
        if not self.acquire_configured_lock():
            raise RuntimeError(self._lock_issue_message or "Another Miru learning worker instance already holds the configured lock.")
        self.prime_work(
            card_code=card_code,
            variant_key=variant_key,
            task_type=task_type,
            source_id=source_id,
            task_payload=task_payload,
        )
        self.update_status(current_state="starting", current_task_label="", current_card_code="", current_task_type="", last_error="")
        self.append_log(level="info", event_type="engine_started", message="Continuous learning engine started.")
        self.send_operator_notification(
            "learning_worker_started",
            "Miru is waking up and beginning its learning cycle.",
            cooldown_seconds=60,
        )
        try:
            while True:
                self.recover_stale_running_tasks()
                counts = self.queue_counts()
                if counts["queued"] < self.queue_low_threshold:
                    self.run_learning_seeder()

                results = self.process_parallel_batch(self.max_parallel_validations)
                if not results:
                    if self.run_learning_seeder(force=True):
                        continue
                    self.update_status(
                        current_state="sleeping",
                        current_task_label="Waiting for queued work",
                        current_card_code="",
                        current_task_type="",
                    )
                    time.sleep(self.sleep_seconds)
                    continue

                backlog = self.queue_counts()
                if backlog["queued"] > 0 or backlog["running"] > 0:
                    time.sleep(min(self.sleep_seconds, 0.1))
                else:
                    time.sleep(min(self.sleep_seconds, 0.25))
                self.maybe_send_daily_summary()
        except KeyboardInterrupt:
            self.append_log(level="info", event_type="engine_stopped", message="Continuous learning engine stopped by operator.")
            self.update_status(current_state="idle", current_task_label="", current_card_code="", current_task_type="")
            self.send_operator_notification(
                "learning_worker_stopped",
                "Miru is going to sleep. All systems shutting down safely.",
                cooldown_seconds=60,
            )
        except Exception as exc:
            self.append_log(
                level="error",
                event_type="engine_crashed",
                message=f"{exc.__class__.__name__}: {exc}",
            )
            self.update_status(
                current_state="error",
                current_task_label="",
                current_card_code="",
                current_task_type="",
                last_error=f"{exc.__class__.__name__}: {exc}"[:1000],
            )
            self.send_operator_notification(
                "critical_learning_failure",
                "Miru hit a critical learning problem and needs operator review. Learning evidence already stored remains safe.",
                priority=1,
                cooldown_seconds=300,
            )
            raise
        finally:
            self.flush_pending_project_sync(reason="engine-shutdown")
            self.release_configured_lock()


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


def handle_promote_verified_dossiers(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    batch_limit = max(int(payload.get("batch_limit") or engine.seed_batch_size), 1)
    card_codes = engine.promotable_dossier_card_codes(limit=batch_limit)
    if not card_codes:
        return {
            "message": "No promotable pending-confirmation dossiers were ready for verified canonical promotion.",
            "task_type": task.task_type,
            "promoted": 0,
            "pending": 0,
        }

    records: list[NormalizedSourceRecord] = []
    for card_code in card_codes:
        record = engine.build_promotion_record(card_code)
        if record is not None:
            records.append(record)
    if not records:
        return {
            "message": "Promotion scan found candidate dossiers, but none had enough canonical facts to sync safely.",
            "task_type": task.task_type,
            "promoted": 0,
            "pending": 0,
        }

    sync_result = engine.bulk_queue_project_sync(
        records,
        task_type=task.task_type,
        suppress_notifications=True,
        reason="dossier-promotion",
    )
    outcomes = list(sync_result.get("outcomes", []) or [])
    promoted = 0
    pending = 0
    high_confidence = 0
    for outcome in outcomes:
        card_code = str(outcome.get("card_code") or "").strip().upper()
        if card_code:
            engine.apply_project_sync_result_to_dossier(card_code, outcome)
        status = str(outcome.get("status") or "").strip().lower()
        verification_status = str(outcome.get("verification_status") or "").strip().lower()
        if status == "synced":
            promoted += 1
        elif status == "pending_confirmation":
            pending += 1
        if verification_status in {"high-confidence", "verified-with-image-confirmation"}:
            high_confidence += 1

    message = f"Miru promoted {promoted} card{'s' if promoted != 1 else ''} from pending confirmation into verified knowledge."
    if pending > 0:
        message += f" {pending} still need stronger corroboration."
    if high_confidence > 0:
        message += f" {high_confidence} now qualify as high-confidence canonical records."
    if promoted > 0:
        engine.send_operator_notification(
            f"dossier_promotion_{time.strftime('%Y%m%d%H', time.gmtime())}",
            message,
            cooldown_seconds=3600,
        )
    return {
        "message": message,
        "task_type": task.task_type,
        "promoted": promoted,
        "pending": pending,
        "high_confidence": high_confidence,
        "project_sync": sync_result,
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
    governance = engine.evaluate_source_execution_gate(
        source_id=source_id,
        execution_kind="learning-intake",
    )
    if not governance["proceed"]:
        limited_use_result = engine.execute_limited_use_source_path(task=task, governance=governance)
        if limited_use_result is not None:
            return limited_use_result
        return engine.build_source_governance_task_result(
            task=task,
            governance=governance,
            source_id=source_id,
            message_prefix="Deferred governed source fetch",
        )
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
    governance = engine.evaluate_source_execution_gate(
        source_id=source_id,
        execution_kind="learning-intake",
    )
    if not governance["proceed"]:
        limited_use_result = engine.execute_limited_use_source_path(task=task, governance=governance)
        if limited_use_result is not None:
            return limited_use_result
        return engine.build_source_governance_task_result(
            task=task,
            governance=governance,
            source_id=source_id,
            message_prefix="Deferred governed source verification",
        )
    payload = dict(task.task_payload or {})
    records: list[NormalizedSourceRecord] = []
    if engine.source_payload_has_adapter_input(payload):
        records = engine.fetch_official_source_records(
            source_id=source_id,
            card_code=task.card_code,
            task_payload=payload,
        )
    elif source_id == "official-cardlist" and engine.knowledge_cache_path.is_file():
        records = [
            item
            for item in engine.load_cached_knowledge_source_records(source_id=source_id)
            if str(item.card_code or "").strip().upper() == str(task.card_code or "").strip().upper()
        ]
        if records:
            engine.append_budget_signal(
                event_type="cached_reused",
                card_code=task.card_code,
                task_type=task.task_type,
                detail="source_from_knowledge_cache",
            )
    if records:
        record = records[0]
        engine.store_source_record(record, verification_state="verified-source-fields")
        fact_acceptance = engine.merge_source_record_into_dossier(record, verification_state="source-backed")
        if not bool(fact_acceptance.get("stored")):
            return {
                "message": (
                    f"Stored source record for {record.card_code} from {source_id}, but deferred verified dossier assembly: "
                    f"{str(fact_acceptance.get('storage_reason') or '')}"
                ).strip(),
                "card_code": record.card_code,
                "task_type": task.task_type,
                "source_id": source_id,
                "source_reference": record.source_reference,
                "fact_acceptance": fact_acceptance,
                "verified_fact_storage": False,
            }
        sync_result = engine.queue_validated_card_for_project_sync(record, task_type=task.task_type)
        for outcome in list(sync_result.get("outcomes", []) or []):
            if str(outcome.get("card_code") or "").strip().upper() == record.card_code:
                engine.apply_project_sync_result_to_dossier(record.card_code, outcome)
        return {
            "message": f"Verified source-backed fields for {record.card_code} from {source_id}",
            "card_code": record.card_code,
            "task_type": task.task_type,
            "source_id": source_id,
            "source_reference": record.source_reference,
            "fact_acceptance": fact_acceptance,
            "project_sync": sync_result,
        }

    fallback_record = engine.build_local_verify_fallback_record(
        task.card_code,
        preferred_source_id=source_id,
    )
    if fallback_record is not None:
        engine.store_source_record(fallback_record, verification_state="local-cache-fallback")
        fact_acceptance = engine.merge_source_record_into_dossier(
            fallback_record,
            verification_state="pending-confirmation",
        )
        return {
            "message": (
                f"Used conservative local fallback for {fallback_record.card_code} because "
                f"no direct {source_id} source row was available. "
                f"{str(fact_acceptance.get('storage_reason') or '')}"
            ),
            "card_code": fallback_record.card_code,
            "task_type": task.task_type,
            "source_id": fallback_record.source_id,
            "source_reference": fallback_record.source_reference,
            "fallback_used": True,
            "fact_acceptance": fact_acceptance,
        }

    return {
        "message": (
            f"Skipped verify_official_fields for {task.card_code} because "
            f"no {source_id} source row or approved local fallback was available."
        ),
        "card_code": str(task.card_code or "").strip().upper(),
        "task_type": task.task_type,
        "source_id": source_id,
        "source_reference": "",
        "skipped": True,
    }


def handle_refresh_from_source(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    source_id = task.source_id or "official-cardlist"
    governance = engine.evaluate_source_execution_gate(
        source_id=source_id,
        execution_kind="learning-intake",
    )
    if not governance["proceed"]:
        limited_use_result = engine.execute_limited_use_source_path(task=task, governance=governance)
        if limited_use_result is not None:
            return limited_use_result
        return engine.build_source_governance_task_result(
            task=task,
            governance=governance,
            source_id=source_id,
            message_prefix="Deferred governed source refresh",
        )
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
    governance = engine.evaluate_source_execution_gate(
        source_id=source_id,
        execution_kind="learning-intake",
    )
    if not governance["proceed"]:
        limited_use_result = engine.execute_limited_use_source_path(task=task, governance=governance)
        if limited_use_result is not None:
            return limited_use_result
        return engine.build_source_governance_task_result(
            task=task,
            governance=governance,
            source_id=source_id,
            message_prefix="Deferred governed set discovery",
            extra={"set_code": str((task.task_payload or {}).get("set_code") or "").strip().upper()},
        )
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


def handle_bulk_ingest_registry(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    requested_sources = payload.get("sources") if isinstance(payload.get("sources"), list) else ["official-cardlist"]
    source_ids = [str(item or "").strip().lower() for item in requested_sources if str(item or "").strip()]
    if not source_ids:
        source_ids = ["official-cardlist"]

    by_card: dict[str, dict[str, NormalizedSourceRecord]] = {}
    fetched_total = 0
    ignored_invalid = 0
    missing_sources: list[str] = []
    fallback_sources_used: list[str] = []
    governance_skipped_sources: list[dict[str, Any]] = []
    fact_acceptance_deferred = 0

    for source_id in source_ids:
        governance = engine.evaluate_source_execution_gate(
            source_id=source_id,
            execution_kind="learning-intake",
        )
        if not governance["proceed"]:
            governance_skipped_sources.append(
                {
                    "source_id": source_id,
                    "execution_outcome": str(governance.get("execution_outcome") or ""),
                    "reason": str(governance.get("reason") or ""),
                    "source_reviewed": bool(governance.get("source_reviewed")),
                }
            )
            missing_sources.append(source_id)
            continue
        engine.update_status(
            current_state="processing",
            current_task_label=f"Refreshing registry source {source_id}",
            current_task_type=task.task_type,
            current_source_id=source_id,
        )
        source_payload = engine.resolve_source_task_payload(source_id, payload)
        records: list[NormalizedSourceRecord] = []
        if engine.source_payload_has_adapter_input(source_payload):
            records = engine.fetch_official_source_records(
                source_id=source_id,
                set_code=str(payload.get("set_code") or "").strip().upper(),
                task_payload=source_payload,
            )
        elif source_id == "official-cardlist" and engine.knowledge_cache_path.is_file():
            records = engine.load_cached_knowledge_source_records(source_id=source_id)
            if records:
                fallback_sources_used.append("knowledge-cache")
            else:
                missing_sources.append(source_id)
                continue
        else:
            missing_sources.append(source_id)
            continue
        fetched_total += len(records)
        for index, record in enumerate(records, start=1):
            if index == 1 or index % 50 == 0:
                engine.update_status(
                    current_state="processing",
                    current_task_label=f"Reviewing {source_id} registry records",
                    current_task_type=task.task_type,
                    current_source_id=source_id,
                    current_card_code=record.card_code,
                )
            validation = engine.validate_learning_reference(card_code=record.card_code, set_code=record.set_code)
            if not validation["ok"]:
                ignored_invalid += 1
                engine.note_invalid_reference(
                    card_code=record.card_code,
                    set_code=record.set_code,
                    reason=str(validation["reason"] or "invalid_set_reference"),
                )
                continue
            engine.store_source_record(record, verification_state="verified-source-fields")
            fact_acceptance = engine.merge_source_record_into_dossier(record, verification_state="source-backed")
            if bool(fact_acceptance.get("stored")):
                by_card.setdefault(record.card_code, {})[source_id] = record
            else:
                fact_acceptance_deferred += 1

    if not by_card:
        if missing_sources and len(missing_sources) == len(source_ids):
            return {
                "message": "Skipped bulk registry ingestion because no valid source snapshot or cache fallback was available.",
                "task_type": task.task_type,
                "fetched_records": fetched_total,
                "ignored_invalid": ignored_invalid,
                "sources_skipped": missing_sources,
                "governance_skipped_sources": governance_skipped_sources,
                "fact_acceptance_deferred": fact_acceptance_deferred,
                "skipped": True,
            }
        return {
            "message": "Bulk registry ingestion stored source records, but none had enough corroborated facts to sync safely.",
            "task_type": task.task_type,
            "fetched_records": fetched_total,
            "ignored_invalid": ignored_invalid,
            "sources_used": [source_id for source_id in source_ids if source_id not in missing_sources],
            "governance_skipped_sources": governance_skipped_sources,
            "fact_acceptance_deferred": fact_acceptance_deferred,
        }

    engine.send_operator_notification(
        "bulk_registry_cycle_started",
        "Miru began a bulk registry learning cycle using trusted local and cached data.",
        cooldown_seconds=1800,
    )

    prioritized_records: list[NormalizedSourceRecord] = []
    for card_code in sorted(by_card):
        candidates = list(by_card[card_code].values())
        candidates.sort(
            key=lambda item: (
                int(engine.resolve_source_entry(item.source_id).trust_tier),
                item.source_id,
            )
        )
        prioritized_records.append(candidates[0])

    if payload.get("batch_limit") not in (None, ""):
        prioritized_records = prioritized_records[: max(int(payload.get("batch_limit") or 0), 1)]

    def _sync_progress(card_code: str, reason: str) -> None:
        engine.update_status(
            current_state="processing",
            current_task_label=f"Syncing verified registry records ({reason})",
            current_task_type=task.task_type,
            current_card_code=card_code,
            current_source_id="project-sync",
        )

    engine.update_status(
        current_state="processing",
        current_task_label="Preparing Project Miru sync",
        current_task_type=task.task_type,
        current_source_id="project-sync",
    )
    sync_result = engine.bulk_queue_project_sync(
        prioritized_records,
        task_type=task.task_type,
        suppress_notifications=True,
        reason="bulk-registry-ingest",
        progress_callback=_sync_progress,
    )
    outcomes = list(sync_result.get("outcomes", []) or [])
    for outcome in outcomes:
        card_code = str(outcome.get("card_code") or "").strip().upper()
        if card_code:
            engine.apply_project_sync_result_to_dossier(card_code, outcome)
    verified = sum(1 for item in outcomes if str(item.get("status") or "") == "synced")
    pending = sum(1 for item in outcomes if str(item.get("status") or "") == "pending_confirmation")
    conflicts = sum(
        1
        for item in outcomes
        if int(((item.get("conflict_summary") or {}).get("rejected_field_count") or 0)) > 0
        or "conflict" in str(item.get("verification_status") or "")
    )
    status_line = (
        f"Miru ingested {len(prioritized_records)} card records from trusted sources and verified {verified} of them."
    )
    if pending > 0 or conflicts > 0:
        status_line += f" {pending} remain pending confirmation and {conflicts} need stronger conflict review."
    if fact_acceptance_deferred > 0:
        status_line += (
            f" {fact_acceptance_deferred} additional source-backed record"
            f"{'s' if fact_acceptance_deferred != 1 else ''} stayed outside verified dossier storage."
        )
    if ignored_invalid > 0:
        status_line += f" Miru skipped {ignored_invalid} invalid placeholder reference{'s' if ignored_invalid != 1 else ''} and continued learning normally."
    engine.send_operator_notification(
        f"bulk_registry_ingest_{time.strftime('%Y%m%d%H', time.gmtime())}",
        status_line,
        cooldown_seconds=3600,
    )
    return {
        "message": status_line,
        "task_type": task.task_type,
        "fetched_records": fetched_total,
        "registry_records": len(prioritized_records),
        "verified_records": verified,
        "pending_records": pending,
        "conflict_records": conflicts,
        "ignored_invalid": ignored_invalid,
        "sources_used": [source_id for source_id in source_ids if source_id not in missing_sources],
        "fallback_sources_used": fallback_sources_used,
        "governance_skipped_sources": governance_skipped_sources,
        "fact_acceptance_deferred": fact_acceptance_deferred,
        "project_sync": sync_result,
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
    governance = engine.evaluate_source_execution_gate(
        source_id=source_id,
        execution_kind="learning-intake",
    )
    if not governance["proceed"]:
        limited_use_result = engine.execute_limited_use_source_path(task=task, governance=governance)
        if limited_use_result is not None:
            return limited_use_result
        return engine.build_source_governance_task_result(
            task=task,
            governance=governance,
            source_id=source_id,
            message_prefix="Deferred governed image fetch",
            extra={"variant_key": normalize_variant_key(task.variant_key or payload.get("variant_key") or "")},
        )
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
    prefetch_assessment = engine.assess_image_candidate(
        record=record,
        target_card_code=task.card_code,
        target_variant_key=resolved_variant,
        width=record.width,
        height=record.height,
    )
    if prefetch_assessment["quality_tier"] == "rejected":
        engine.store_image_record(
            record,
            filename=build_image_filename(record.card_code, resolved_variant),
            local_path="",
            image_hash="",
            width=int(record.width or 0),
            height=int(record.height or 0),
            verification_state="rejected",
            downloaded_at=utc_timestamp(),
            last_verified_at="",
            last_reviewed_at=utc_timestamp(),
            last_error="; ".join(prefetch_assessment["review_notes"]),
            source_trust_tier=prefetch_assessment["source_trust_tier"],
            source_trust_label=prefetch_assessment["source_trust_label"],
            quality_tier=prefetch_assessment["quality_tier"],
            sample_flag=bool(prefetch_assessment["sample_flag"]),
            image_score=float(prefetch_assessment["image_score"]),
            print_match_confidence=float(prefetch_assessment["print_match_confidence"]),
            card_code_match_confidence=float(prefetch_assessment["card_code_match_confidence"]),
            clarity_score=float(prefetch_assessment["clarity_score"]),
            crop_confidence=float(prefetch_assessment["crop_confidence"]),
            review_notes=list(prefetch_assessment["review_notes"]),
            replacement_eligible=bool(prefetch_assessment["replacement_eligible"]),
            upgrade_status=str(prefetch_assessment["upgrade_status"]),
            is_current_best=False,
            print_id=str(prefetch_assessment["print_id"]),
            print_label=str(prefetch_assessment["print_label"]),
            variant_label=str(prefetch_assessment["variant_label"]),
            source_type=str(prefetch_assessment["source_type"]),
            selection_scope=str(prefetch_assessment["selection_scope"]),
            selection_reason=str(prefetch_assessment["selection_reason"]),
            selection_confidence=float(prefetch_assessment["selection_confidence"]),
            variant_match_confidence=float(prefetch_assessment["variant_match_confidence"]),
            art_family_confidence=float(prefetch_assessment["art_family_confidence"]),
            quality_score=float(prefetch_assessment["quality_score"]),
            trust_score=float(prefetch_assessment["trust_score"]),
            duplicate_group=str(prefetch_assessment["duplicate_group"]),
            perceptual_hash=str(prefetch_assessment["perceptual_hash"]),
            origin_language=str(prefetch_assessment["origin_language"]),
            english_print_exists=bool(prefetch_assessment["english_print_exists"]),
            display_policy=str(prefetch_assessment["display_policy"]),
            provisional_language_display=bool(prefetch_assessment["provisional_language_display"]),
            content_status=str(prefetch_assessment["content_status"]),
            citation_payload=dict(prefetch_assessment["citation_payload"]),
            score_breakdown=dict(prefetch_assessment["score_breakdown"]),
        )
        return {
            "message": f"Rejected image candidate for {task.card_code} from {source_id}",
            "task_type": task.task_type,
            "card_code": task.card_code,
            "variant_key": resolved_variant,
            "source_id": source_id,
            "quality_tier": prefetch_assessment["quality_tier"],
            "review_notes": list(prefetch_assessment["review_notes"]),
            "skipped": True,
        }
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
    final_assessment = engine.assess_image_candidate(
        record=record,
        target_card_code=task.card_code,
        target_variant_key=resolved_variant,
        width=width,
        height=height,
    )
    existing_best = engine.fetch_current_best_image_record(
        card_code=record.card_code,
        variant_key=resolved_variant,
    )
    same_as_existing_best = bool(
        existing_best
        and str(existing_best.get("source_id") or "") == record.source_id
        and str(existing_best.get("filename") or "") == filename
    )
    if same_as_existing_best:
        is_current_best = bool(int(existing_best.get("is_current_best") or 0)) or final_assessment["quality_tier"] != "rejected"
    else:
        is_current_best = engine.candidate_should_replace_existing(
            candidate=final_assessment,
            existing=existing_best,
        )
    engine.store_image_record(
        record,
        filename=filename,
        local_path=relative_path,
        image_hash=image_hash,
        width=width,
        height=height,
        verification_state="provisional" if final_assessment["quality_tier"] != "rejected" else "rejected",
        downloaded_at=now,
        last_verified_at="",
        last_reviewed_at=now,
        last_error="",
        source_trust_tier=final_assessment["source_trust_tier"],
        source_trust_label=final_assessment["source_trust_label"],
        quality_tier=final_assessment["quality_tier"],
        sample_flag=bool(final_assessment["sample_flag"]),
        image_score=float(final_assessment["image_score"]),
        print_match_confidence=float(final_assessment["print_match_confidence"]),
        card_code_match_confidence=float(final_assessment["card_code_match_confidence"]),
        clarity_score=float(final_assessment["clarity_score"]),
        crop_confidence=float(final_assessment["crop_confidence"]),
        review_notes=list(final_assessment["review_notes"]),
        replacement_eligible=bool(final_assessment["replacement_eligible"]),
        upgrade_status=str(final_assessment["upgrade_status"]),
        is_current_best=is_current_best,
        print_id=str(final_assessment["print_id"]),
        print_label=str(final_assessment["print_label"]),
        variant_label=str(final_assessment["variant_label"]),
        source_type=str(final_assessment["source_type"]),
        selection_scope=str(final_assessment["selection_scope"]),
        selection_reason=str(final_assessment["selection_reason"]),
        selection_confidence=float(final_assessment["selection_confidence"]),
        variant_match_confidence=float(final_assessment["variant_match_confidence"]),
        art_family_confidence=float(final_assessment["art_family_confidence"]),
        quality_score=float(final_assessment["quality_score"]),
        trust_score=float(final_assessment["trust_score"]),
        duplicate_group=str(final_assessment["duplicate_group"]),
        perceptual_hash=str(final_assessment["perceptual_hash"]),
        origin_language=str(final_assessment["origin_language"]),
        english_print_exists=bool(final_assessment["english_print_exists"]),
        display_policy=str(final_assessment["display_policy"]),
        provisional_language_display=bool(final_assessment["provisional_language_display"]),
        bytes_size=len(image_bytes),
        mime_type="image/png",
        content_status=str(final_assessment["content_status"]),
        citation_payload=dict(final_assessment["citation_payload"]),
        score_breakdown=dict(final_assessment["score_breakdown"]),
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
        "quality_tier": final_assessment["quality_tier"],
        "sample_flag": bool(final_assessment["sample_flag"]),
        "image_score": float(final_assessment["image_score"]),
        "is_current_best": is_current_best,
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
    source_rollup = engine.summarize_dossier_sources(task.card_code)
    cached_analysis = engine.fetch_cached_image_analysis(
        card_code=task.card_code,
        variant_key=variant_key,
        source_id=source_id,
        image_hash=image_hash,
    )
    if cached_analysis is not None:
        analysis = VisualAnalysisResult(
            extraction_method=str(cached_analysis.get("extraction_method") or "local-parser"),
            extracted_fields=dict(cached_analysis.get("extracted_fields") or {}),
            confidence=float(cached_analysis.get("confidence") or 0.0),
            verification_status=str(cached_analysis.get("verification_status") or "no_visual_signal"),
            source_rollup=dict(cached_analysis.get("source_rollup") or {}),
            conflict_flags=list(cached_analysis.get("conflict_flags") or []),
            analysis_notes=list(cached_analysis.get("analysis_notes") or []),
            analyzed_at=str(cached_analysis.get("analyzed_at") or now),
            cache_hit=True,
            ocr_text_excerpt=str(cached_analysis.get("ocr_text_excerpt") or ""),
        )
    else:
        try:
            expected_profile = engine.resolve_local_profile(task.card_code)
            expected_facts = dict(expected_profile.get("basic_facts") or {})
        except LookupError:
            expected_facts = {"card_code": task.card_code}
        analysis = analyze_card_image(
            image_path=path,
            analyzed_at=now,
            image_hash=image_hash,
            source_reference=str(existing.get("source_reference") or ""),
            source_url=str(existing.get("source_url") or ""),
            variant_key=variant_key,
            expected_facts=expected_facts,
            source_rollup=source_rollup,
        )
        engine.store_image_analysis(
            card_code=task.card_code,
            variant_key=variant_key,
            source_id=source_id,
            image_hash=image_hash,
            local_path=str(existing.get("local_path") or ""),
            analysis=analysis,
        )
    engine.apply_image_analysis_to_dossier(card_code=task.card_code, analysis=analysis)
    engine.maybe_send_visual_learning_update()
    record = NormalizedImageRecord(
        card_code=task.card_code,
        print_id=str(existing.get("print_id") or ""),
        print_label=str(existing.get("print_label") or ""),
        variant_key=variant_key,
        variant_label=str(existing.get("variant_label") or ""),
        source_id=source_id,
        source_type=str(existing.get("source_type") or ""),
        source_url=str(existing.get("source_url") or ""),
        source_reference=str(existing.get("source_reference") or ""),
        image_path=str(path),
        fetched_at=now,
        width=width,
        height=height,
        sample_flag=bool(int(existing.get("sample_flag") or 0)),
        source_trust_tier=int(existing.get("source_trust_tier") or 4),
        source_trust_label=str(existing.get("source_trust_label") or ""),
        metadata={},
    )
    final_assessment = engine.assess_image_candidate(
        record=record,
        target_card_code=task.card_code,
        target_variant_key=variant_key,
        width=width,
        height=height,
        analysis=analysis,
    )
    existing_best = engine.fetch_current_best_image_record(
        card_code=task.card_code,
        variant_key=variant_key,
    )
    same_as_existing_best = bool(
        existing_best
        and str(existing_best.get("source_id") or "") == record.source_id
        and str(existing_best.get("filename") or "") == str(existing.get("filename") or build_image_filename(task.card_code, variant_key))
    )
    if same_as_existing_best:
        is_current_best = bool(int(existing_best.get("is_current_best") or 0)) or final_assessment["quality_tier"] != "rejected"
    else:
        is_current_best = engine.candidate_should_replace_existing(
            candidate=final_assessment,
            existing=existing_best,
        )
    engine.store_image_record(
        record,
        filename=str(existing.get("filename") or build_image_filename(task.card_code, variant_key)),
        local_path=str(existing.get("local_path") or ""),
        image_hash=image_hash,
        width=width,
        height=height,
        verification_state="verified" if final_assessment["quality_tier"] != "rejected" else "rejected",
        downloaded_at=str(existing.get("downloaded_at") or now),
        last_verified_at=now,
        last_reviewed_at=now,
        last_error="",
        source_trust_tier=final_assessment["source_trust_tier"],
        source_trust_label=final_assessment["source_trust_label"],
        quality_tier=final_assessment["quality_tier"],
        sample_flag=bool(final_assessment["sample_flag"]),
        image_score=float(final_assessment["image_score"]),
        print_match_confidence=float(final_assessment["print_match_confidence"]),
        card_code_match_confidence=float(final_assessment["card_code_match_confidence"]),
        clarity_score=float(final_assessment["clarity_score"]),
        crop_confidence=float(final_assessment["crop_confidence"]),
        review_notes=list(final_assessment["review_notes"]),
        replacement_eligible=bool(final_assessment["replacement_eligible"]),
        upgrade_status=str(final_assessment["upgrade_status"]),
        is_current_best=is_current_best,
        print_id=str(final_assessment["print_id"]),
        print_label=str(final_assessment["print_label"]),
        variant_label=str(final_assessment["variant_label"]),
        source_type=str(final_assessment["source_type"]),
        selection_scope=str(final_assessment["selection_scope"]),
        selection_reason=str(final_assessment["selection_reason"]),
        selection_confidence=float(final_assessment["selection_confidence"]),
        variant_match_confidence=float(final_assessment["variant_match_confidence"]),
        art_family_confidence=float(final_assessment["art_family_confidence"]),
        quality_score=float(final_assessment["quality_score"]),
        trust_score=float(final_assessment["trust_score"]),
        duplicate_group=str(final_assessment["duplicate_group"]),
        perceptual_hash=str(final_assessment["perceptual_hash"]),
        origin_language=str(final_assessment["origin_language"]),
        english_print_exists=bool(final_assessment["english_print_exists"]),
        display_policy=str(final_assessment["display_policy"]),
        provisional_language_display=bool(final_assessment["provisional_language_display"]),
        bytes_size=len(image_bytes),
        mime_type="image/png",
        content_status=str(final_assessment["content_status"]),
        citation_payload=dict(final_assessment["citation_payload"]),
        score_breakdown=dict(final_assessment["score_breakdown"]),
    )
    return {
        "message": (
            f"Verified image for {task.card_code}{f'({variant_key})' if variant_key else ''}"
            f" using {analysis.extraction_method} with {analysis.verification_status.replace('-', ' ')}."
        ),
        "task_type": task.task_type,
        "card_code": task.card_code,
        "variant_key": variant_key,
        "filename": existing.get("filename") or "",
        "source_id": source_id,
        "source_reference": existing.get("source_reference") or "",
        "image_hash": image_hash,
        "image_analysis_method": analysis.extraction_method,
        "image_analysis_status": analysis.verification_status,
        "image_analysis_confidence": analysis.confidence,
        "image_analysis_conflicts": list(analysis.conflict_flags),
        "quality_tier": final_assessment["quality_tier"],
        "sample_flag": bool(final_assessment["sample_flag"]),
        "image_score": float(final_assessment["image_score"]),
        "is_current_best": is_current_best,
    }


def handle_refresh_card_image(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    source_id = task.source_id or "official-card-images"
    governance = engine.evaluate_source_execution_gate(
        source_id=source_id,
        execution_kind="learning-intake",
    )
    if not governance["proceed"]:
        limited_use_result = engine.execute_limited_use_source_path(task=task, governance=governance)
        if limited_use_result is not None:
            return limited_use_result
        return engine.build_source_governance_task_result(
            task=task,
            governance=governance,
            source_id=source_id,
            message_prefix="Deferred governed image refresh",
            extra={"variant_key": normalize_variant_key(task.variant_key or payload.get("variant_key") or "")},
        )
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


def handle_discover_image_candidates(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("discover_image_candidates requires a card_code")
    payload = dict(task.task_payload or {})
    variant_key = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    payload_source_ids = payload.get("source_ids") or []
    if isinstance(payload_source_ids, list) and payload_source_ids:
        source_ids = [str(item or "").strip().lower() for item in payload_source_ids if str(item or "").strip()]
    else:
        source_ids = [str(task.source_id or "official-card-images").strip().lower()]
    source_ids = list(dict.fromkeys(source_ids))

    discovered = 0
    queued = 0
    print_records = 0
    missing_sources: list[str] = []
    governance_skipped_sources: list[dict[str, Any]] = []
    for source_id in source_ids:
        governance = engine.evaluate_source_execution_gate(
            source_id=source_id,
            execution_kind="learning-intake",
        )
        if not governance["proceed"]:
            governance_skipped_sources.append(
                {
                    "source_id": source_id,
                    "execution_outcome": str(governance.get("execution_outcome") or ""),
                    "reason": str(governance.get("reason") or ""),
                    "source_reviewed": bool(governance.get("source_reviewed")),
                }
            )
            missing_sources.append(source_id)
            continue
        try:
            records = engine.fetch_image_source_records(
                source_id=source_id,
                card_code=task.card_code,
                variant_key=variant_key,
                task_payload=payload,
            )
        except Exception:
            missing_sources.append(source_id)
            continue
        if not records:
            missing_sources.append(source_id)
            continue
        for record in records:
            discovered += 1
            print_identity = engine.build_print_identity(
                card_code=record.card_code,
                variant_key=record.variant_key,
                variant_label=record.variant_label,
                print_label=record.print_label,
            )
            engine.upsert_learning_print(
                print_identity=print_identity,
                verification_state="discovered",
                match_confidence=0.6 if print_identity["variant_key"] else 0.72,
                supporting_sources=[
                    {
                        "source_id": record.source_id,
                        "source_reference": record.source_reference,
                        "source_url": record.source_url,
                    }
                ],
                citation_payload={
                    "source_id": record.source_id,
                    "source_reference": record.source_reference,
                    "source_url": record.source_url,
                },
            )
            print_records += 1
            if engine.enqueue_task(
                card_code=record.card_code,
                variant_key=record.variant_key,
                task_type="verify_image_candidate",
                source_id=record.source_id,
                priority=max(task.priority - 1, 0),
                task_payload=payload,
            ):
                queued += 1

    return {
        "message": (
            f"Discovered {discovered} image candidate record(s) for {task.card_code}"
            f" and queued {queued} verification task(s)."
        ),
        "task_type": task.task_type,
        "card_code": task.card_code,
        "variant_key": variant_key,
        "discovered_candidates": discovered,
        "queued_verifications": queued,
        "print_records": print_records,
        "missing_sources": missing_sources,
        "governance_skipped_sources": governance_skipped_sources,
    }


def handle_verify_image_candidate(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("verify_image_candidate requires a card_code")
    payload = dict(task.task_payload or {})
    source_id = task.source_id or "official-card-images"
    variant_key = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    bridge_task = LearningTask(
        id=task.id,
        card_code=task.card_code,
        variant_key=variant_key,
        task_type="fetch_card_image",
        source_id=source_id,
        priority=task.priority,
        status=task.status,
        attempts=task.attempts,
        last_error=task.last_error,
        task_payload=payload,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
    fetch_result = handle_fetch_card_image(engine, bridge_task)
    if fetch_result.get("skipped"):
        fetch_result["task_type"] = task.task_type
        fetch_result["bridge_task"] = "fetch_card_image"
        return fetch_result

    verify_task = LearningTask(
        id=task.id,
        card_code=task.card_code,
        variant_key=variant_key,
        task_type="verify_card_image",
        source_id=source_id,
        priority=task.priority,
        status=task.status,
        attempts=task.attempts,
        last_error=task.last_error,
        task_payload=payload,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )
    verify_result = handle_verify_card_image(engine, verify_task)
    link_result = handle_link_variant_image(engine, task)
    select_result = handle_select_best_image(engine, task)
    verify_result["task_type"] = task.task_type
    verify_result["bridge_tasks"] = ["fetch_card_image", "verify_card_image"]
    verify_result["link_result"] = link_result
    verify_result["selection_result"] = {
        "selection_updates": int(select_result.get("selection_updates") or 0),
        "selection_scope": str(select_result.get("selection_scope") or ""),
        "selection": select_result.get("selection"),
    }
    return verify_result


def handle_link_variant_image(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("link_variant_image requires a card_code")
    payload = dict(task.task_payload or {})
    variant_key = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    source_id = task.source_id or payload.get("source_id") or ""
    candidate = (
        engine.fetch_image_registry_record(card_code=task.card_code, variant_key=variant_key, source_id=source_id)
        if source_id
        else engine.fetch_current_best_image_record(card_code=task.card_code, variant_key=variant_key)
    )
    if candidate is None and source_id:
        candidate = engine.fetch_latest_image_source_candidate(card_code=task.card_code, source_id=source_id)
    if candidate is None:
        return {
            "message": f"No stored image candidate was available to link for {task.card_code}.",
            "task_type": task.task_type,
            "card_code": task.card_code,
            "variant_key": variant_key,
            "linked": False,
        }
    print_identity = engine.build_print_identity(
        card_code=task.card_code,
        variant_key=variant_key,
        variant_label=str(candidate.get("variant_label") or ""),
        print_label=str(candidate.get("print_label") or ""),
    )
    candidate_print_identity = engine.build_print_identity(
        card_code=task.card_code,
        variant_key=str(candidate.get("variant_key") or ""),
        variant_label=str(candidate.get("variant_label") or ""),
        print_label=str(candidate.get("print_label") or ""),
    )
    print_comparison = engine.compare_print_profiles(
        target_profile=dict(print_identity.get("print_profile") or {}),
        candidate_profile=dict(candidate_print_identity.get("print_profile") or {}),
        target_variant_key=variant_key,
        candidate_variant_key=str(candidate.get("variant_key") or ""),
    )
    citation_payload = {
        "source_id": str(candidate.get("source_id") or ""),
        "source_reference": str(candidate.get("source_reference") or ""),
        "source_url": str(candidate.get("source_url") or ""),
        "image_hash": str(candidate.get("image_hash") or ""),
        "print_relationship": str(print_comparison.get("relationship") or ""),
        "mismatch_flags": list(print_comparison.get("mismatch_flags") or []),
    }
    print_match_confidence = float(candidate.get("print_match_confidence") or print_comparison["print_match_confidence"])
    if print_match_confidence >= 0.9 and str(candidate.get("verification_state") or "").strip().lower() == "verified":
        verification_state = "source-backed"
    elif print_match_confidence >= 0.72:
        verification_state = "linked"
    else:
        verification_state = "deferred-low-confidence"
    engine.upsert_learning_print(
        print_identity=print_identity,
        verification_state=verification_state,
        match_confidence=print_match_confidence,
        supporting_sources=[citation_payload],
        citation_payload=citation_payload,
        verified_at=str(candidate.get("last_verified_at") or ""),
    )
    engine.update_image_candidate_scaffold(
        candidate_id=int(candidate.get("id") or 0),
        print_identity=print_identity,
        source_type=str(candidate.get("source_type") or ""),
        selection_scope="print_default" if int(candidate.get("is_current_best") or 0) else "",
        selection_reason=f"phase3-link-{print_comparison['relationship']}",
        selection_confidence=float(candidate.get("selection_confidence") or 0.0),
        print_match_confidence=print_match_confidence,
        variant_match_confidence=float(candidate.get("variant_match_confidence") or print_comparison["variant_match_confidence"]),
        art_family_confidence=float(candidate.get("art_family_confidence") or print_comparison["art_family_confidence"]),
        quality_score=float(candidate.get("quality_score") or candidate.get("image_score") or 0.0),
        trust_score=float(candidate.get("trust_score") or 0.0),
        content_status="linked" if print_match_confidence >= 0.72 else "candidate",
        duplicate_group=str(candidate.get("duplicate_group") or ""),
        perceptual_hash=str(candidate.get("perceptual_hash") or ""),
        origin_language=str(candidate.get("origin_language") or "en"),
        english_print_exists=bool(int(candidate.get("english_print_exists") or 0)),
        display_policy=str(candidate.get("display_policy") or "english-first"),
        provisional_language_display=bool(int(candidate.get("provisional_language_display") or 0)),
        citation_payload=citation_payload,
        score_breakdown=dict(candidate.get("score_breakdown") or {}) | {
            "phase3_print_relationship": print_comparison["relationship"],
            "phase3_print_match_confidence": print_match_confidence,
        },
    )
    return {
        "message": (
            f"{'Linked' if print_match_confidence >= 0.72 else 'Deferred'} image candidate for {task.card_code} "
            f"against print {print_identity['print_id']} with {print_comparison['relationship']} reasoning."
        ),
        "task_type": task.task_type,
        "card_code": task.card_code,
        "variant_key": variant_key,
        "print_id": print_identity["print_id"],
        "linked": bool(print_match_confidence >= 0.72),
        "link_verification_state": verification_state,
        "print_match_confidence": round(print_match_confidence, 2),
        "relationship": print_comparison["relationship"],
    }


def handle_resolve_print_relationships(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("resolve_print_relationships requires a card_code")
    with closing(connect_sqlite(engine.dossier_db_path)) as conn:
        rows = conn.execute(
            """
            SELECT
                card_code,
                variant_key,
                COALESCE(MAX(variant_label), '') AS variant_label,
                COALESCE(MAX(print_label), '') AS print_label,
                COALESCE(MAX(source_url), '') AS source_url,
                COALESCE(MAX(source_reference), '') AS source_reference,
                MAX(last_verified_at) AS last_verified_at,
                MAX(card_code_match_confidence) AS card_code_match_confidence,
                MAX(print_match_confidence) AS print_match_confidence,
                MAX(variant_match_confidence) AS variant_match_confidence,
                MAX(art_family_confidence) AS art_family_confidence,
                SUM(CASE WHEN verification_state = 'verified' THEN 1 ELSE 0 END) AS verified_candidate_count,
                COUNT(*) AS candidate_count
            FROM learning_dossier_images
            WHERE card_code = ?
            GROUP BY card_code, variant_key
            ORDER BY variant_key ASC
            """,
            (task.card_code,),
        ).fetchall()
    resolved = 0
    deferred = 0
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        candidate_rows = engine.fetch_image_candidates(
            card_code=task.card_code,
            variant_key=str(item.get("variant_key") or ""),
        )
        duplicate_summary = {
            "exact-duplicate": 0,
            "same-art-different-crop-or-treatment": 0,
            "same-family-cautious": 0,
        }
        for duplicate_info in engine.analyze_duplicate_family(candidate_rows).values():
            relationship = str(duplicate_info.get("duplicate_relationship") or "")
            if relationship in duplicate_summary:
                duplicate_summary[relationship] += 1
        print_identity = engine.build_print_identity(
            card_code=task.card_code,
            variant_key=str(item.get("variant_key") or ""),
            variant_label=str(item.get("variant_label") or ""),
            print_label=str(item.get("print_label") or ""),
        )
        match_confidence = float(
            item.get("print_match_confidence")
            or item.get("variant_match_confidence")
            or item.get("card_code_match_confidence")
            or 0.0
        )
        if match_confidence >= 0.9 and int(item.get("verified_candidate_count") or 0) > 0:
            verification_state = "source-backed"
        elif match_confidence >= 0.72:
            verification_state = "linked"
        else:
            verification_state = "deferred-low-confidence"
            deferred += 1
        engine.upsert_learning_print(
            print_identity=print_identity,
            verification_state=verification_state,
            match_confidence=match_confidence,
            supporting_sources=[
                {
                    "source_reference": str(item.get("source_reference") or ""),
                    "source_url": str(item.get("source_url") or ""),
                    "candidate_count": int(item.get("candidate_count") or 0),
                    "verified_candidate_count": int(item.get("verified_candidate_count") or 0),
                    "art_family_confidence": float(item.get("art_family_confidence") or 0.0),
                    "duplicate_summary": duplicate_summary,
                }
            ],
            citation_payload={
                "source_reference": str(item.get("source_reference") or ""),
                "source_url": str(item.get("source_url") or ""),
                "candidate_count": int(item.get("candidate_count") or 0),
                "verified_candidate_count": int(item.get("verified_candidate_count") or 0),
                "duplicate_summary": duplicate_summary,
            },
            verified_at=str(item.get("last_verified_at") or ""),
        )
        resolved += 1
    return {
        "message": (
            f"Resolved {resolved} print relationship record(s) for {task.card_code}; "
            f"{deferred} remain low-confidence and deferred."
        ),
        "task_type": task.task_type,
        "card_code": task.card_code,
        "resolved_prints": resolved,
        "deferred_prints": deferred,
    }


def handle_select_best_image(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    if not task.card_code:
        raise ValueError("select_best_image requires a card_code")
    payload = dict(task.task_payload or {})
    variant_key = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    relationship_result = handle_resolve_print_relationships(engine, task)
    updated = engine.refresh_scaffolded_image_selections(
        card_code=task.card_code,
        variant_key=variant_key,
        reason="phase3-select-best-image",
    )
    if updated <= 0:
        return {
            "message": f"No current best image candidate exists yet for {task.card_code}.",
            "task_type": task.task_type,
            "card_code": task.card_code,
            "variant_key": variant_key,
            "selection_updates": 0,
            "print_resolution": relationship_result,
        }
    selection_scope = "print_default"
    selection = engine.fetch_image_selection(
        card_code=task.card_code,
        selection_scope=selection_scope,
        print_id=engine.build_print_identity(card_code=task.card_code, variant_key=variant_key)["print_id"],
    )
    upgrade_plan = engine.plan_image_upgrade(card_code=task.card_code, variant_key=variant_key)
    return {
        "message": f"Selected {updated} conservative image scope winner(s) for {task.card_code}.",
        "task_type": task.task_type,
        "card_code": task.card_code,
        "variant_key": variant_key,
        "selection_updates": updated,
        "selection_scope": selection_scope,
        "selection": selection,
        "upgrade_plan": upgrade_plan,
        "print_resolution": relationship_result,
    }


def handle_scan_image_upgrades(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    limit = max(int(payload.get("limit") or 25), 1)
    queued = 0
    selection_refreshes = 0
    replacements = 0
    preserved = 0
    runtime_sync_queued = 0
    scanned = 0
    with closing(connect_sqlite(engine.dossier_db_path)) as conn:
        if task.card_code:
            rows = conn.execute(
                """
                SELECT card_code, variant_key
                FROM learning_dossier_images
                WHERE card_code = ?
                  AND is_current_best = 1
                  AND replacement_eligible = 1
                ORDER BY image_score DESC, id DESC
                LIMIT ?
                """,
                (task.card_code, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT card_code, variant_key
                FROM learning_dossier_images
                WHERE is_current_best = 1
                  AND replacement_eligible = 1
                ORDER BY image_score DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    for row in rows:
        scanned += 1
        card_code = str(row["card_code"] or "")
        variant_key = str(row["variant_key"] or "")
        plan = engine.plan_image_upgrade(card_code=card_code, variant_key=variant_key)
        action = str(plan.get("action") or "")
        if action.startswith("replace-") or action == "promote-first-winner":
            if engine.refresh_scaffolded_image_selections(
                card_code=card_code,
                variant_key=variant_key,
                reason=f"phase3-upgrade-scan-{action}",
            ):
                selection_refreshes += 1
            replacements += 1
            if engine.enqueue_task(
                card_code=card_code,
                variant_key=variant_key,
                task_type="sync_verified_image_selection",
                priority=max(task.priority - 1, 0),
                task_payload={"selection_scope": "print_default", "variant_key": variant_key},
            ):
                runtime_sync_queued += 1
            continue
        preserved += 1
        current_candidate = dict(plan.get("current_candidate") or {})
        current_quality_tier = str(current_candidate.get("quality_tier") or "")
        if (
            current_quality_tier in {"official_sample", "trusted_scan", "fallback_lowres"}
            and engine.enqueue_task(
                card_code=card_code,
                variant_key=variant_key,
                task_type="discover_image_candidates",
                source_id=str(task.source_id or "official-card-images"),
                priority=max(task.priority - 1, 0),
                task_payload=payload | {"upgrade_plan_action": action},
            )
        ):
            queued += 1
    return {
        "message": (
            f"Scanned {scanned} upgrade-eligible image selection(s), applied {replacements} conservative upgrade "
            f"decision(s), preserved {preserved} stable winner(s), queued {queued} discovery task(s), and "
            f"queued {runtime_sync_queued} runtime sync task(s)."
        ),
        "task_type": task.task_type,
        "queued_tasks": queued,
        "scanned": scanned,
        "selection_refreshes": selection_refreshes,
        "replacements": replacements,
        "preserved": preserved,
        "runtime_sync_queued": runtime_sync_queued,
    }


def handle_scan_missing_images(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    if task.card_code:
        existing = engine.fetch_current_best_image_record(card_code=task.card_code, variant_key=normalize_variant_key(task.variant_key))
        if existing is None and engine.enqueue_task(
            card_code=task.card_code,
            variant_key=normalize_variant_key(task.variant_key),
            task_type="discover_image_candidates",
            source_id=str(task.source_id or "official-card-images"),
            priority=max(task.priority - 1, 0),
            task_payload=payload,
        ):
            return {
                "message": f"Queued image discovery for missing image coverage on {task.card_code}.",
                "task_type": task.task_type,
                "card_code": task.card_code,
                "queued_tasks": 1,
            }
        return {
            "message": f"No missing image discovery work was needed for {task.card_code}.",
            "task_type": task.task_type,
            "card_code": task.card_code,
            "queued_tasks": 0,
        }
    queued = engine.seed_inspect_missing_image_tasks(limit=max(int(payload.get("limit") or 25), 1))
    return {
        "message": f"Queued {queued} inspect_missing_image task(s) during missing-image scan.",
        "task_type": task.task_type,
        "queued_tasks": queued,
    }


def handle_rescore_image_candidates(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    limit = max(int(payload.get("limit") or 50), 1)
    with closing(connect_sqlite(engine.dossier_db_path)) as conn:
        if task.card_code:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_dossier_images
                WHERE card_code = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (task.card_code, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM learning_dossier_images
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    refreshed = 0
    candidates = [{key: row[key] for key in row.keys()} for row in rows]
    duplicate_analysis = engine.analyze_duplicate_family(candidates)
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        duplicate_info = duplicate_analysis.get(int(item.get("id") or 0), {})
        score_breakdown = json.loads(str(item.get("score_breakdown_json") or "{}"))
        score_breakdown["phase3_duplicate_relationship"] = str(duplicate_info.get("duplicate_relationship") or "unique")
        score_breakdown["phase3_family_reasoning"] = str(duplicate_info.get("family_reasoning") or "")
        print_identity = engine.build_print_identity(
            card_code=str(item.get("card_code") or ""),
            variant_key=str(item.get("variant_key") or ""),
            variant_label=str(item.get("variant_label") or ""),
            print_label=str(item.get("print_label") or ""),
        )
        engine.update_image_candidate_scaffold(
            candidate_id=int(item.get("id") or 0),
            print_identity=print_identity,
            source_type=str(item.get("source_type") or ""),
            selection_scope=str(item.get("selection_scope") or ""),
            selection_reason="phase3-rescore-scaffold-refresh",
            selection_confidence=engine._selection_confidence_from_row(item),
            print_match_confidence=float(item.get("print_match_confidence") or item.get("variant_match_confidence") or 0.0),
            variant_match_confidence=float(item.get("variant_match_confidence") or (1.0 if not print_identity["variant_key"] else 0.85)),
            art_family_confidence=float(item.get("art_family_confidence") or (0.9 if print_identity["is_base"] else 0.72)),
            quality_score=float(item.get("quality_score") or item.get("image_score") or 0.0),
            trust_score=float(item.get("trust_score") or max(0.1, 1.1 - (0.2 * int(item.get("source_trust_tier") or 4)))),
            content_status=str(item.get("content_status") or "candidate"),
            duplicate_group=str(item.get("duplicate_group") or ""),
            perceptual_hash=str(item.get("perceptual_hash") or ""),
            origin_language=str(item.get("origin_language") or "en"),
            english_print_exists=bool(int(item.get("english_print_exists") or 0)),
            display_policy=str(item.get("display_policy") or "english-first"),
            provisional_language_display=bool(int(item.get("provisional_language_display") or 0)),
            citation_payload=json.loads(str(item.get("citation_payload_json") or "{}")),
            score_breakdown=score_breakdown,
        )
        refreshed += 1
    return {
        "message": f"Refreshed Phase 3 image candidate metadata for {refreshed} image candidate(s). Conservative duplicate-family and selection inputs were recomputed.",
        "task_type": task.task_type,
        "refreshed_candidates": refreshed,
    }


def handle_sync_verified_image_selection(engine: MiruLearningEngine, task: LearningTask) -> dict[str, Any]:
    payload = dict(task.task_payload or {})
    target_card_code = str(task.card_code or payload.get("card_code") or "").strip().upper()
    if not target_card_code:
        raise ValueError("sync_verified_image_selection requires a card_code")
    inferred_default_scope = "print_default" if normalize_variant_key(task.variant_key or payload.get("variant_key") or "") else "card_default"
    scope = str(payload.get("selection_scope") or inferred_default_scope).strip() or inferred_default_scope
    target_variant = normalize_variant_key(task.variant_key or payload.get("variant_key") or "")
    sync_result = engine.sync_image_selection_to_runtime(
        card_code=target_card_code,
        selection_scope=scope,
        variant_key=target_variant,
    )
    return {
        "message": str(sync_result.get("reason") or f"Runtime image sync finished with status {sync_result.get('status')}."),
        "task_type": task.task_type,
        "sync_status": str(sync_result.get("sync_status") or sync_result.get("status") or ""),
        "selection_scope": scope,
        "card_code": target_card_code,
        "variant_key": target_variant,
        **sync_result,
    }


TASK_HANDLERS: dict[str, Callable[[MiruLearningEngine, LearningTask], dict[str, Any]]] = {
    "bootstrap_dossier": handle_bootstrap_dossier,
    "sync_missing_fields": handle_sync_missing_fields,
    "inspect_missing_image": handle_inspect_missing_image,
    "promote_verified_dossiers": handle_promote_verified_dossiers,
    "refresh_progress": handle_refresh_progress,
    "fetch_official_source": handle_fetch_official_source,
    "verify_official_fields": handle_verify_official_fields,
    "bulk_ingest_registry": handle_bulk_ingest_registry,
    "refresh_from_source": handle_refresh_from_source,
    "discover_set_cards": handle_discover_set_cards,
    "discover_sources": handle_discover_sources,
    "fetch_card_image": handle_fetch_card_image,
    "verify_card_image": handle_verify_card_image,
    "refresh_card_image": handle_refresh_card_image,
    "discover_image_candidates": handle_discover_image_candidates,
    "verify_image_candidate": handle_verify_image_candidate,
    "link_variant_image": handle_link_variant_image,
    "resolve_print_relationships": handle_resolve_print_relationships,
    "select_best_image": handle_select_best_image,
    "scan_image_upgrades": handle_scan_image_upgrades,
    "scan_missing_images": handle_scan_missing_images,
    "rescore_image_candidates": handle_rescore_image_candidates,
    "sync_verified_image_selection": handle_sync_verified_image_selection,
}


def parse_utc_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def format_age_compact(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def build_learning_worker_status(snapshot: dict[str, Any], *, stale_after_seconds: int = DEFAULT_WORKER_HEARTBEAT_STALE_SECONDS) -> dict[str, Any]:
    last_heartbeat = str(snapshot.get("last_heartbeat") or "").strip()
    heartbeat_age_seconds: int | None = None
    parsed_heartbeat = parse_utc_timestamp(last_heartbeat)
    if parsed_heartbeat is not None:
        heartbeat_age_seconds = max(int((datetime.now(timezone.utc) - parsed_heartbeat).total_seconds()), 0)
    current_state = str(snapshot.get("current_state") or "").strip().lower()
    running_count = int(snapshot.get("running_count") or 0)
    queue_length = int(snapshot.get("queue_length") or 0)
    has_status = bool(snapshot.get("status_db_exists"))
    if not has_status:
        return {
            "status": "stopped",
            "label": "Stopped",
            "detail": "Worker status DB does not exist yet.",
            "heartbeat_age_seconds": None,
            "heartbeat_stale": False,
        }
    if not last_heartbeat:
        return {
            "status": "no_heartbeat",
            "label": "No heartbeat",
            "detail": "Worker status DB exists, but no heartbeat has been recorded yet.",
            "heartbeat_age_seconds": None,
            "heartbeat_stale": False,
        }
    if heartbeat_age_seconds is not None and heartbeat_age_seconds > stale_after_seconds:
        return {
            "status": "stale",
            "label": "Stale",
            "detail": f"Last heartbeat was {format_age_compact(heartbeat_age_seconds)} ago.",
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "heartbeat_stale": True,
        }
    if current_state in {"processing", "running", "starting"} or running_count > 0:
        return {
            "status": "running",
            "label": "Running",
            "detail": "Worker heartbeat is fresh and learning work is active.",
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "heartbeat_stale": False,
        }
    if current_state in {"idle", "sleeping"}:
        return {
            "status": "idle",
            "label": "Idle",
            "detail": (
                "Worker heartbeat is fresh and the queue is empty."
                if queue_length <= 0
                else "Worker heartbeat is fresh and the worker is waiting for the next task."
            ),
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "heartbeat_stale": False,
        }
    return {
        "status": "stopped",
        "label": "Stopped",
        "detail": "Worker status exists, but it is not currently reporting a running or idle loop.",
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "heartbeat_stale": False,
    }


def load_learning_engine_status(
    *,
    queue_db_path: Path = DEFAULT_QUEUE_DB_PATH,
    status_db_path: Path = DEFAULT_STATUS_DB_PATH,
    dossier_db_path: Path = DEFAULT_DOSSIER_DB_PATH,
    total_cards: int | None = None,
    lock_file_path: Path = DEFAULT_LOCK_FILE_PATH,
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
        "skipped_invalid_set_count": 0,
        "retired_non_actionable_count": 0,
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
        "api_usage": {
            "requests_per_hour": 0,
            "requests_per_day": 0,
            "requests_per_source": {},
        },
        "invalid_reference_count": 0,
        "priority_bands": {"high": PRIORITY_HIGH_INT, "medium": PRIORITY_MEDIUM_INT, "low": PRIORITY_LOW_INT},
        "recent_budget_signals": [],
        "schema_version": "",
        "worker_status": {
            "status": "stopped",
            "label": "Stopped",
            "detail": "Worker status is unavailable.",
            "heartbeat_age_seconds": None,
            "heartbeat_stale": False,
        },
        "lock": inspect_single_instance_lock(lock_file_path),
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
            elif status == "skipped_invalid_set":
                snapshot["skipped_invalid_set_count"] = total
            elif status == "retired_non_actionable":
                snapshot["retired_non_actionable_count"] = total
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
                metadata_row = conn.execute(
                    "SELECT schema_version FROM runtime_metadata WHERE component = ?",
                    ("miru_learning_engine",),
                ).fetchone()
                if metadata_row is not None:
                    snapshot["schema_version"] = str(metadata_row[0] or "")
            except sqlite3.OperationalError:
                snapshot["schema_version"] = ""
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
            try:
                invalid_row = conn.execute(
                    """
                    SELECT COUNT(*) AS total
                    FROM engine_log
                    WHERE event_type = 'ignored_invalid_reference'
                      AND created_at >= datetime('now', 'start of day')
                    """
                ).fetchone()
                if invalid_row is not None:
                    snapshot["invalid_reference_count"] = int(invalid_row["total"] or 0)
            except sqlite3.OperationalError:
                snapshot["invalid_reference_count"] = 0
            try:
                signal_rows = conn.execute(
                    """
                    SELECT event_type, card_code, task_type, detail, created_at
                    FROM budget_signals
                    ORDER BY id DESC
                    LIMIT 20
                    """
                ).fetchall()
                snapshot["recent_budget_signals"] = [
                    {
                        "event_type": str(r["event_type"] or ""),
                        "card_code": str(r["card_code"] or ""),
                        "task_type": str(r["task_type"] or ""),
                        "detail": str(r["detail"] or ""),
                        "created_at": str(r["created_at"] or ""),
                    }
                    for r in (signal_rows or [])
                ]
            except sqlite3.OperationalError:
                snapshot["recent_budget_signals"] = []
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
            ):
                if key in available_keys:
                    snapshot[key] = row[key]
    snapshot["worker_status"] = build_learning_worker_status(snapshot)

    dossier_path = Path(dossier_db_path)
    if dossier_path.is_file():
        with closing(connect_sqlite(dossier_path)) as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM learning_dossiers").fetchone()
            snapshot["dossier_count"] = int(row["total"] if row is not None else 0)
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
        cache = MiruSourceCache()
        snapshot["api_usage"] = cache.snapshot_usage()
    except Exception:
        snapshot["api_usage"] = {
            "requests_per_hour": 0,
            "requests_per_day": 0,
            "requests_per_source": {},
        }

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
    parser.add_argument("--queue-low-threshold", type=int, default=QUEUE_LOW_THRESHOLD)
    parser.add_argument("--seeder-refill-cap", type=int, default=SEEDER_REFILL_CAP)
    parser.add_argument("--stale-task-seconds", type=int, default=DEFAULT_STALE_TASK_SECONDS)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE_PATH)
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
        queue_low_threshold=args.queue_low_threshold,
        seeder_refill_cap=args.seeder_refill_cap,
        stale_task_seconds=args.stale_task_seconds,
        lock_file_path=args.lock_file,
    )


def _worktree_append_review_item(
    self: MiruLearningEngine,
    *,
    card_code: str,
    source_id: str,
    confidence: float,
    reason: str,
) -> dict[str, Any]:
    """Worktree compatibility hook: queue explicit learner review items for operator flow."""
    now = utc_timestamp()
    with closing(connect_sqlite(self.queue_db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learner_review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_code TEXT NOT NULL,
                source_id TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learner_review_queue_created "
            "ON learner_review_queue(created_at DESC)"
        )
        conn.execute(
            """
            INSERT INTO learner_review_queue (card_code, source_id, confidence, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(card_code or "").strip().upper(),
                str(source_id or "").strip(),
                float(confidence or 0.0),
                str(reason or "").strip(),
                now,
            ),
        )
        conn.commit()
    return {"queued": True, "card_code": str(card_code or "").strip().upper(), "source_id": str(source_id or "").strip()}


def _worktree_maybe_send_learning_notification(
    self: MiruLearningEngine,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    """Worktree compatibility hook: keep existing operator notification touchpoint name."""
    try:
        if force:
            self.maybe_send_daily_summary()
            return {"ok": True, "event": "daily_summary_forced"}
        self.maybe_send_batch_progress_notifications()
        return {"ok": True, "event": "batch_progress"}
    except Exception as exc:
        self.log_event("notification_hook_error", message=f"{exc.__class__.__name__}: {exc}")
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}


if not hasattr(MiruLearningEngine, "append_review_item"):
    MiruLearningEngine.append_review_item = _worktree_append_review_item  # type: ignore[attr-defined]
if not hasattr(MiruLearningEngine, "maybe_send_learning_notification"):
    MiruLearningEngine.maybe_send_learning_notification = _worktree_maybe_send_learning_notification  # type: ignore[attr-defined]


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
                    lock_file_path=engine.lock_file_path,
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
