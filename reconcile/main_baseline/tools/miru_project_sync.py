from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from tools.miru_ai_onepiece import clean_display_text, normalize_card_code, normalize_set_code
from tools.miru_source_adapters import NormalizedSourceRecord
from tools.miru_source_registry import (
    MiruSourceEntry,
    build_source_registry,
    build_unknown_source_entry,
    get_source_entry,
)

VALID_SET_FAMILIES = {"OP", "EB", "P", "PRB"}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROJECT_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"


def connect_catalog_db(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_catalog_sync_schema(db_path: str | Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = """
        CREATE TABLE IF NOT EXISTS sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            set_code TEXT NOT NULL UNIQUE,
            set_name TEXT NOT NULL DEFAULT '',
            series_code_display TEXT NOT NULL DEFAULT '',
            series_id TEXT NOT NULL DEFAULT '',
            sources_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_code TEXT NOT NULL UNIQUE,
            set_family TEXT NOT NULL DEFAULT '',
            set_code TEXT NOT NULL DEFAULT '',
            card_number TEXT NOT NULL DEFAULT '',
            set_name TEXT NOT NULL DEFAULT '',
            card_name TEXT NOT NULL DEFAULT '',
            rarity TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT '',
            card_type TEXT NOT NULL DEFAULT '',
            cost INTEGER,
            power TEXT NOT NULL DEFAULT '',
            counter TEXT NOT NULL DEFAULT '',
            attribute TEXT NOT NULL DEFAULT '',
            traits TEXT NOT NULL DEFAULT '',
            life TEXT NOT NULL DEFAULT '',
            block_icon TEXT NOT NULL DEFAULT '',
            effect_text TEXT NOT NULL DEFAULT '',
            trigger_text TEXT NOT NULL DEFAULT '',
            illustrator TEXT NOT NULL DEFAULT '',
            confidence_level TEXT NOT NULL DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT '',
            source_rollup_json TEXT NOT NULL DEFAULT '{}',
            aliases_json TEXT NOT NULL DEFAULT '[]',
            sources_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS card_variants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id INTEGER NOT NULL,
            variant_key TEXT NOT NULL,
            variant_label TEXT NOT NULL DEFAULT '',
            print_id TEXT NOT NULL DEFAULT '',
            release_set_code TEXT NOT NULL DEFAULT '',
            release_set_name TEXT NOT NULL DEFAULT '',
            image_path TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            print_treatment TEXT NOT NULL DEFAULT '',
            artist_credit TEXT NOT NULL DEFAULT '',
            illustration_type TEXT NOT NULL DEFAULT '',
            source_attribution_json TEXT NOT NULL DEFAULT '{}',
            sync_status TEXT NOT NULL DEFAULT '',
            unresolved_reason TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'local-catalog',
            is_base INTEGER NOT NULL DEFAULT 0,
            is_alt INTEGER NOT NULL DEFAULT 0,
            is_sp INTEGER NOT NULL DEFAULT 0,
            has_variant_evidence INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(card_id) REFERENCES cards(id) ON DELETE CASCADE,
            UNIQUE(card_id, variant_key, print_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cards_set_code ON cards(set_code);
        CREATE INDEX IF NOT EXISTS idx_cards_card_name ON cards(card_name);
        CREATE INDEX IF NOT EXISTS idx_variants_card_id ON card_variants(card_id);
        CREATE INDEX IF NOT EXISTS idx_variants_variant_key ON card_variants(variant_key);

        CREATE TABLE IF NOT EXISTS miru_validations (
            card_code TEXT PRIMARY KEY,
            card_id INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0.0,
            task_type TEXT NOT NULL DEFAULT '',
            verified_at TEXT NOT NULL DEFAULT '',
            sources_json TEXT NOT NULL DEFAULT '[]',
            winning_source_json TEXT NOT NULL DEFAULT '{}',
            rejected_sources_json TEXT NOT NULL DEFAULT '[]',
            validated_fields_json TEXT NOT NULL DEFAULT '[]',
            canonical_values_json TEXT NOT NULL DEFAULT '{}',
            conflict_summary_json TEXT NOT NULL DEFAULT '{}',
            confidence_reason TEXT NOT NULL DEFAULT '',
            confidence_level TEXT NOT NULL DEFAULT '',
            verification_status TEXT NOT NULL DEFAULT '',
            source_rollup_json TEXT NOT NULL DEFAULT '{}',
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(card_code) REFERENCES cards(canonical_code) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_miru_validations_verified_at ON miru_validations(verified_at);

        CREATE TABLE IF NOT EXISTS miru_card_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_code TEXT NOT NULL,
            archetype_key TEXT NOT NULL DEFAULT '',
            usage_count INTEGER NOT NULL DEFAULT 0,
            format_name TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT '',
            period_label TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(card_code, archetype_key, source_kind, period_label)
        );

        CREATE TABLE IF NOT EXISTS miru_deck_archetypes (
            archetype_key TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            format_name TEXT NOT NULL DEFAULT '',
            representative_leader_code TEXT NOT NULL DEFAULT '',
            confidence_score REAL NOT NULL DEFAULT 0.0,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS miru_meta_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT NOT NULL UNIQUE,
            event_name TEXT NOT NULL DEFAULT '',
            format_name TEXT NOT NULL DEFAULT '',
            event_date TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            source_kind TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """
    with closing(connect_catalog_db(path)) as conn:
        conn.executescript(schema)
        _ensure_column(conn, "cards", "set_family TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "cards", "block_icon TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "cards", "illustrator TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "cards", "confidence_level TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "cards", "verification_status TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "cards", "source_rollup_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "cards", "aliases_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "cards", "sources_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "card_variants", "image_path TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_variants", "image_url TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_variants", "print_treatment TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_variants", "artist_credit TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_variants", "illustration_type TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_variants", "source_attribution_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "card_variants", "sync_status TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "card_variants", "unresolved_reason TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_validations", "card_id INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "miru_validations", "winning_source_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "miru_validations", "rejected_sources_json TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(conn, "miru_validations", "conflict_summary_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "miru_validations", "canonical_values_json TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "miru_validations", "confidence_reason TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_validations", "confidence_level TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_validations", "verification_status TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "miru_validations", "source_rollup_json TEXT NOT NULL DEFAULT '{}'")


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_definition: str) -> None:
    existing_columns = {
        row["name"] if isinstance(row, sqlite3.Row) else row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    column_name = column_definition.split()[0]
    if column_name not in existing_columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def load_card_validation_audit(
    card_code: str,
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
) -> dict[str, Any] | None:
    """Read-only validation audit payload for a canonical card code."""
    normalized = normalize_card_code(card_code)
    canonical_code = (normalized["canonical_code"] or card_code or "").strip().upper()
    if not canonical_code:
        return None
    path = Path(project_db_path)
    if not path.is_file():
        return None

    try:
        with closing(connect_catalog_db(path)) as conn:
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
                    c.effect_text,
                    c.trigger_text,
                    c.illustrator,
                    c.confidence_level,
                    c.verification_status,
                    c.source_rollup_json,
                    c.sources_json AS canonical_sources_json,
                    v.card_id,
                    v.confidence,
                    v.task_type,
                    v.verified_at,
                    v.sources_json,
                    v.winning_source_json,
                    v.rejected_sources_json,
                    v.validated_fields_json,
                    v.canonical_values_json,
                    v.conflict_summary_json,
                    v.confidence_reason,
                    v.payload_json,
                    v.updated_at
                FROM cards c
                LEFT JOIN miru_validations v
                    ON v.card_code = c.canonical_code
                WHERE c.canonical_code = ?
                """,
                (canonical_code,),
            ).fetchone()
    except sqlite3.Error:
        return None

    if row is None:
        return None

    canonical_values = {
        "card_code": str(row["canonical_code"] or ""),
        "set_family": str(row["set_family"] or ""),
        "set_code": str(row["set_code"] or ""),
        "card_number": str(row["card_number"] or ""),
        "set_name": str(row["set_name"] or ""),
        "card_name": str(row["card_name"] or ""),
        "rarity": str(row["rarity"] or ""),
        "color": str(row["color"] or ""),
        "card_type": str(row["card_type"] or ""),
        "cost": "" if row["cost"] is None else str(row["cost"]),
        "power": str(row["power"] or ""),
        "counter": str(row["counter"] or ""),
        "attribute": str(row["attribute"] or ""),
        "traits": str(row["traits"] or ""),
        "life": str(row["life"] or ""),
        "effect_text": str(row["effect_text"] or ""),
        "trigger_text": str(row["trigger_text"] or ""),
        "illustrator": str(row["illustrator"] or ""),
        "confidence_level": str(row["confidence_level"] or ""),
        "verification_status": str(row["verification_status"] or ""),
    }
    sources = MiruProjectDbSync._load_json_objects(str(row["sources_json"] or "[]"))
    winning_source = MiruProjectDbSync._load_json_object(str(row["winning_source_json"] or "{}"))
    rejected_sources = MiruProjectDbSync._load_json_objects(str(row["rejected_sources_json"] or "[]"))
    conflict_summary = MiruProjectDbSync._load_json_object(str(row["conflict_summary_json"] or "{}"))
    validated_fields = MiruProjectDbSync._load_json_list(str(row["validated_fields_json"] or "[]"))
    canonical_values_json = MiruProjectDbSync._load_json_object(str(row["canonical_values_json"] or "{}"))
    payload_json = MiruProjectDbSync._load_json_object(str(row["payload_json"] or "{}"))
    canonical_source_keys = MiruProjectDbSync._load_json_list(str(row["canonical_sources_json"] or "[]"))

    return {
        "card_code": canonical_code,
        "card_id": int(row["card_id"] or 0) if row["card_id"] is not None else 0,
        "validated_fields": validated_fields,
        "canonical_values": canonical_values_json or canonical_values,
        "canonical_source_keys": canonical_source_keys,
        "sources": sources,
        "winning_source": winning_source,
        "rejected_sources": rejected_sources,
        "conflict_summary": conflict_summary,
        "confidence": float(row["confidence"] or 0.0) if row["confidence"] is not None else 0.0,
        "confidence_reason": str(row["confidence_reason"] or ""),
        "verified_at": str(row["verified_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
        "task_type": str(row["task_type"] or ""),
        "payload": payload_json,
        "source_rollup": MiruProjectDbSync._load_json_object(str(row["source_rollup_json"] or "{}")),
        "has_rejected_conflicts": bool(rejected_sources) or int(conflict_summary.get("rejected_field_count") or 0) > 0,
        "sync_boundary": {
            "owns_canonical_upsert": True,
            "summary": "MiruProjectDbSync is the trust-aware decision boundary and performs canonical card row upserts into card_catalog.db.",
        },
    }


def list_validation_audit_insights(
    *,
    project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
    limit: int = 6,
) -> dict[str, list[dict[str, Any]]]:
    """Read-only insight summaries for the Dev Monitor validation audit panel."""
    path = Path(project_db_path)
    if not path.is_file():
        return {
            "recent_conflicts": [],
            "lowest_confidence": [],
            "recently_validated": [],
            "rejected_evidence": [],
        }

    try:
        with closing(connect_catalog_db(path)) as conn:
            rows = conn.execute(
                """
                SELECT
                    c.canonical_code,
                    c.card_name,
                    c.set_name,
                    v.confidence,
                    v.verified_at,
                    v.updated_at,
                    v.winning_source_json,
                    v.rejected_sources_json,
                    v.conflict_summary_json,
                    v.confidence_reason
                FROM miru_validations v
                JOIN cards c
                    ON c.canonical_code = v.card_code
                ORDER BY v.verified_at DESC, v.updated_at DESC, c.canonical_code ASC
                LIMIT 200
                """
            ).fetchall()
    except sqlite3.Error:
        rows = []

    items: list[dict[str, Any]] = []
    for row in rows:
        winning_source = MiruProjectDbSync._load_json_object(str(row["winning_source_json"] or "{}"))
        rejected_sources = MiruProjectDbSync._load_json_objects(str(row["rejected_sources_json"] or "[]"))
        conflict_summary = MiruProjectDbSync._load_json_object(str(row["conflict_summary_json"] or "{}"))
        items.append(
            {
                "card_code": str(row["canonical_code"] or ""),
                "card_name": str(row["card_name"] or ""),
                "set_name": str(row["set_name"] or ""),
                "confidence": float(row["confidence"] or 0.0) if row["confidence"] is not None else 0.0,
                "verified_at": str(row["verified_at"] or ""),
                "updated_at": str(row["updated_at"] or ""),
                "winning_source": winning_source,
                "winning_source_id": str(winning_source.get("source_id") or ""),
                "winning_trust_label": str(winning_source.get("trust_label") or ""),
                "rejected_sources": rejected_sources,
                "rejected_source_count": len(rejected_sources),
                "conflict_summary": conflict_summary,
                "conflict_rule": str(conflict_summary.get("rule") or "no-conflict"),
                "rejected_fields": [str(item) for item in (conflict_summary.get("rejected_fields") or []) if str(item).strip()],
                "confidence_reason": str(row["confidence_reason"] or ""),
            }
        )

    def summarize(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "card_code": entry["card_code"],
            "card_name": entry["card_name"],
            "set_name": entry["set_name"],
            "confidence": entry["confidence"],
            "verified_at": entry["verified_at"],
            "winning_source_id": entry["winning_source_id"],
            "winning_trust_label": entry["winning_trust_label"],
            "rejected_source_count": entry["rejected_source_count"],
            "rejected_fields": entry["rejected_fields"],
            "conflict_rule": entry["conflict_rule"],
            "confidence_reason": entry["confidence_reason"],
        }

    recent_conflicts = [
        summarize(item)
        for item in items
        if item["conflict_rule"] != "no-conflict"
    ][:limit]
    lowest_confidence = [
        summarize(item)
        for item in sorted(items, key=lambda item: (item["confidence"], item["verified_at"], item["card_code"]))
    ][:limit]
    recently_validated = [summarize(item) for item in items[:limit]]
    rejected_evidence = [
        summarize(item)
        for item in items
        if item["rejected_source_count"] > 0
    ][:limit]
    return {
        "recent_conflicts": recent_conflicts,
        "lowest_confidence": lowest_confidence,
        "recently_validated": recently_validated,
        "rejected_evidence": rejected_evidence,
    }


class MiruProjectDbSync:
    def __init__(
        self,
        *,
        project_db_path: str | Path = DEFAULT_PROJECT_DB_PATH,
        batch_size: int = 3,
        sync_immediate: bool = True,
        confidence_threshold: float = 0.75,
        min_verified_sources: int = 2,
        preferred_verified_sources: int = 3,
        logger: Callable[..., None] | None = None,
    ) -> None:
        self.project_db_path = str(project_db_path)
        self.batch_size = max(int(batch_size), 1)
        self.sync_immediate = bool(sync_immediate)
        self.confidence_threshold = float(confidence_threshold)
        self.min_verified_sources = max(int(min_verified_sources), 2)
        self.preferred_verified_sources = max(int(preferred_verified_sources), self.min_verified_sources)
        self.logger = logger
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = RLock()
        self._source_registry = build_source_registry()
        ensure_catalog_sync_schema(self.project_db_path)

    def queue_validated_record(
        self,
        record: NormalizedSourceRecord,
        *,
        task_type: str = "verify_official_fields",
        additional_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = self.build_sync_payload(record, task_type=task_type, additional_sources=additional_sources)
        card_code = payload["card_code"]
        self._log(
            event_type="card_validated",
            message=f"Validated {card_code} for Project Miru library sync.",
            card_code=card_code,
        )
        with self._lock:
            self._pending[card_code] = payload
            self._log(
                event_type="card_sync_queued",
                message=f"Queued {card_code} for Project Miru library sync.",
                card_code=card_code,
            )

            flushed = 0
            failed = 0
            pending = 0
            outcomes: list[dict[str, Any]] = []
            if self.sync_immediate:
                result = self.flush_cards([card_code], reason="immediate")
                flushed += result["flushed"]
                failed += result["failed"]
                pending += result.get("pending", 0)
                outcomes.extend(result.get("outcomes", []))
            elif len(self._pending) >= self.batch_size:
                result = self.flush_pending(reason="batch-threshold")
                flushed += result["flushed"]
                failed += result["failed"]
                pending += result.get("pending", 0)
                outcomes.extend(result.get("outcomes", []))
            return {
                "queued": len(self._pending),
                "flushed": flushed,
                "failed": failed,
                "pending": pending,
                "outcomes": outcomes,
            }

    def queue_validated_records(
        self,
        records: list[dict[str, Any]],
        *,
        task_type: str = "bulk_ingest_registry",
        reason: str = "bulk",
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, Any]:
        queued_codes: list[str] = []
        with self._lock:
            for item in records:
                record = item.get("record")
                if not isinstance(record, NormalizedSourceRecord):
                    continue
                payload = self.build_sync_payload(
                    record,
                    task_type=str(item.get("task_type") or task_type),
                    additional_sources=item.get("additional_sources"),
                )
                card_code = payload["card_code"]
                self._pending[card_code] = payload
                queued_codes.append(card_code)
                self._log(
                    event_type="card_sync_queued",
                    message=f"Queued {card_code} for Project Miru library sync.",
                    card_code=card_code,
                )
            result = self.flush_cards(
                queued_codes,
                reason=reason,
                progress_callback=progress_callback,
            ) if queued_codes else {
                "flushed": 0,
                "failed": 0,
                "pending": 0,
                "pending_queue": len(self._pending),
                "outcomes": [],
            }
            result["queued"] = len(queued_codes)
            return result

    def flush_pending(
        self,
        *,
        reason: str = "manual",
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, int]:
        with self._lock:
            return self.flush_cards(
                list(self._pending.keys()),
                reason=reason,
                progress_callback=progress_callback,
            )

    def flush_cards(
        self,
        card_codes: list[str],
        *,
        reason: str,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> dict[str, int]:
        flushed = 0
        failed = 0
        pending = 0
        outcomes: list[dict[str, Any]] = []
        with self._lock:
            for index, card_code in enumerate(list(card_codes), start=1):
                payload = self._pending.get(card_code)
                if not payload:
                    continue
                if progress_callback and (index == 1 or index % 10 == 0):
                    progress_callback(card_code, reason)
                try:
                    result = self._sync_payload(payload)
                except Exception as exc:
                    failed += 1
                    self._log(
                        event_type="card_sync_failed",
                        level="error",
                        message=f"Library sync failed for {card_code} during {reason}: {exc}",
                        card_code=card_code,
                    )
                    continue
                result_payload = dict(result or {})
                result_payload["card_code"] = card_code
                outcomes.append(result_payload)
                status = str(result_payload.get("status") or "synced")
                if status == "pending_confirmation":
                    pending += 1
                    self._pending.pop(card_code, None)
                    self._log(
                        event_type="card_sync_pending_confirmation",
                        message=str(
                            result_payload.get("operator_message")
                            or f"Holding {card_code} until more sources confirm it."
                        ),
                        card_code=card_code,
                    )
                    continue
                flushed += 1
                self._pending.pop(card_code, None)
                self._log(
                    event_type="card_synced",
                    message=str(
                        result_payload.get("operator_message")
                        or f"Synced {card_code} into card_catalog.db during {reason}."
                    ),
                    card_code=card_code,
                )
            return {
                "flushed": flushed,
                "failed": failed,
                "pending": pending,
                "pending_queue": len(self._pending),
                "outcomes": outcomes,
            }

    def build_sync_payload(
        self,
        record: NormalizedSourceRecord,
        *,
        task_type: str = "verify_official_fields",
        additional_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        profile = self._resolve_source_profile(record.source_id)
        source_entry = self._build_source_entry(profile, record)
        merged_payload_sources = self._merge_source_entries(list(additional_sources or []), [source_entry])
        confidence_score = self._score_source_confidence(merged_payload_sources)
        normalized = normalize_card_code(record.card_code)
        card_code = normalized["canonical_code"] or record.card_code.strip().upper()
        set_code = normalize_set_code(record.set_code or normalized["set_code"])
        set_family = self._set_family(set_code or card_code)
        traits_text = " / ".join(clean_display_text(item) for item in (record.traits or []) if clean_display_text(item))
        validated_fields = [
            key
            for key, value in {
                "card_name": record.card_name,
                "set_family": set_family,
                "set_code": set_code,
                "set_name": record.set_name,
                "rarity": record.rarity,
                "color": record.color,
                "card_type": record.card_type,
                "cost": record.cost,
                "power": record.power,
                "counter": record.counter,
                "attribute": record.attribute,
                "traits": traits_text,
                "life": record.life,
                "effect_text": record.effect_text,
                "trigger_text": record.trigger_text,
                "illustrator": record.illustrator,
            }.items()
            if value not in (None, "", [], {})
        ]
        source_count = self._distinct_source_count(merged_payload_sources)
        confidence_reason = self._describe_confidence(
            source_entries=merged_payload_sources,
            conflict_count=0,
        )
        return {
            "card_code": card_code,
            "set_family": set_family,
            "set_code": set_code,
            "card_number": clean_display_text(normalized["card_number"]),
            "set_name": clean_display_text(record.set_name),
            "card_name": clean_display_text(record.card_name),
            "rarity": clean_display_text(record.rarity),
            "color": clean_display_text(record.color),
            "card_type": clean_display_text(record.card_type),
            "cost": self._coerce_int(record.cost),
            "power": clean_display_text(record.power),
            "counter": clean_display_text(record.counter),
            "attribute": clean_display_text(record.attribute),
            "traits": traits_text,
            "life": clean_display_text(record.life),
            "effect_text": clean_display_text(record.effect_text),
            "trigger_text": clean_display_text(record.trigger_text),
            "illustrator": clean_display_text(record.illustrator),
            "confidence_score": confidence_score,
            "confidence_level": self._confidence_level(source_count),
            "confidence_reason": confidence_reason,
            "validated_at": record.fetched_at,
            "validated_fields": validated_fields,
            "task_type": task_type,
            "sources": merged_payload_sources,
            "source_count": source_count,
            "winning_source": source_entry,
            "rejected_sources": [],
            "conflict_summary": {
                "rule": "single-source validation",
                "conflicts": [],
                "reason": "Only one validation source contributed to this sync payload.",
            },
            "verification_status": "pending_confirmation" if source_count < self.min_verified_sources else "verified",
            "source_rollup": {
                "source_count": source_count,
                "source_names": self._source_display_names(merged_payload_sources),
                "confidence_level": self._confidence_level(source_count),
            },
            "payload_json": record.to_dict(),
        }

    def _upsert_validation_row(
        self,
        conn: sqlite3.Connection,
        *,
        card_code: str,
        card_id: int,
        payload: dict[str, Any],
        confidence_score: float,
        confidence_level: str,
        verification_status: str,
        source_entries: list[dict[str, Any]],
        winning_source: dict[str, Any],
        rejected_sources: list[dict[str, Any]],
        conflict_summary: dict[str, Any],
        confidence_reason: str,
        source_rollup: dict[str, Any],
        canonical_values: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO miru_validations (
                card_code, card_id, confidence, task_type, verified_at, sources_json,
                winning_source_json, rejected_sources_json, validated_fields_json, canonical_values_json,
                conflict_summary_json, confidence_reason, confidence_level, verification_status,
                source_rollup_json, payload_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(card_code) DO UPDATE SET
                card_id = excluded.card_id,
                confidence = excluded.confidence,
                task_type = excluded.task_type,
                verified_at = excluded.verified_at,
                sources_json = excluded.sources_json,
                winning_source_json = excluded.winning_source_json,
                rejected_sources_json = excluded.rejected_sources_json,
                validated_fields_json = excluded.validated_fields_json,
                canonical_values_json = excluded.canonical_values_json,
                conflict_summary_json = excluded.conflict_summary_json,
                confidence_reason = excluded.confidence_reason,
                confidence_level = excluded.confidence_level,
                verification_status = excluded.verification_status,
                source_rollup_json = excluded.source_rollup_json,
                payload_json = excluded.payload_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                card_code,
                max(int(card_id or 0), 0),
                float(confidence_score or 0.0),
                str(payload.get("task_type") or ""),
                str(payload.get("validated_at") or ""),
                json.dumps(source_entries, ensure_ascii=True, sort_keys=True),
                json.dumps(winning_source or {}, ensure_ascii=True, sort_keys=True),
                json.dumps(rejected_sources or [], ensure_ascii=True, sort_keys=True),
                json.dumps(payload.get("validated_fields") or [], ensure_ascii=True, sort_keys=True),
                json.dumps(canonical_values or {}, ensure_ascii=True, sort_keys=True),
                json.dumps(conflict_summary or {}, ensure_ascii=True, sort_keys=True),
                str(confidence_reason or ""),
                str(confidence_level or ""),
                str(verification_status or ""),
                json.dumps(source_rollup or {}, ensure_ascii=True, sort_keys=True),
                json.dumps(payload.get("payload_json") or {}, ensure_ascii=True, sort_keys=True),
            ),
        )

    def _sync_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        card_code = str(payload.get("card_code") or "").strip().upper()
        if not card_code:
            raise ValueError("Sync payload is missing card_code.")
        set_family = self._set_family(str(payload.get("set_code") or "").strip().upper() or card_code)
        if set_family and set_family not in VALID_SET_FAMILIES:
            raise ValueError(f"Unsupported set family for canonical sync: {set_family}")

        ensure_catalog_sync_schema(self.project_db_path)
        with closing(connect_catalog_db(self.project_db_path)) as conn:
            set_code = str(payload.get("set_code") or "").strip().upper()
            set_name = str(payload.get("set_name") or "").strip()
            if set_code:
                existing_set = conn.execute(
                    "SELECT * FROM sets WHERE set_code = ?",
                    (set_code,),
                ).fetchone()
                existing_sources = self._load_json_list(existing_set["sources_json"] if existing_set else "[]")
                merged_set_sources = self._merge_source_keys(existing_sources, payload.get("sources") or [])
                if existing_set:
                    conn.execute(
                        """
                        UPDATE sets
                        SET set_name = ?, series_code_display = ?, sources_json = ?
                        WHERE set_code = ?
                        """,
                        (
                            set_name or existing_set["set_name"] or "",
                            existing_set["series_code_display"] or set_code,
                            json.dumps(merged_set_sources, ensure_ascii=True, sort_keys=True),
                            set_code,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO sets (
                            set_code, set_name, series_code_display, series_id, sources_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            set_code,
                            set_name,
                            set_code,
                            "",
                            json.dumps(merged_set_sources, ensure_ascii=True, sort_keys=True),
                        ),
                    )

            existing_card = conn.execute(
                "SELECT * FROM cards WHERE canonical_code = ?",
                (card_code,),
            ).fetchone()
            existing_validation = conn.execute(
                "SELECT * FROM miru_validations WHERE card_code = ?",
                (card_code,),
            ).fetchone()
            existing_validation_sources = self._load_json_objects(existing_validation["sources_json"] if existing_validation else "[]")
            combined_validation_sources = self._merge_source_entries(existing_validation_sources, payload.get("sources") or [])
            combined_source_count = self._distinct_source_count(combined_validation_sources)
            combined_confidence_score = self._score_source_confidence(combined_validation_sources)
            confidence_level = self._confidence_level(combined_source_count)
            image_confirmation = self._image_confirmation_state(combined_validation_sources)
            confidence_reason = self._describe_confidence(
                source_entries=combined_validation_sources,
                conflict_count=0,
            )
            if combined_source_count < self.min_verified_sources or combined_confidence_score < self.confidence_threshold:
                pending_canonical_values = {
                    "card_code": card_code,
                    "set_family": set_family,
                    "set_code": set_code,
                    "card_number": str(payload.get("card_number") or "").strip(),
                    "set_name": str(payload.get("set_name") or "").strip(),
                    "card_name": str(payload.get("card_name") or "").strip(),
                    "rarity": str(payload.get("rarity") or "").strip(),
                    "color": str(payload.get("color") or "").strip(),
                    "card_type": str(payload.get("card_type") or "").strip(),
                    "cost": "" if payload.get("cost") is None else str(payload.get("cost")),
                    "power": str(payload.get("power") or "").strip(),
                    "counter": str(payload.get("counter") or "").strip(),
                    "attribute": str(payload.get("attribute") or "").strip(),
                    "traits": str(payload.get("traits") or "").strip(),
                    "life": str(payload.get("life") or "").strip(),
                    "effect_text": str(payload.get("effect_text") or "").strip(),
                    "trigger_text": str(payload.get("trigger_text") or "").strip(),
                    "illustrator": str(payload.get("illustrator") or "").strip(),
                }
                self._upsert_validation_row(
                    conn,
                    card_code=card_code,
                    card_id=int(existing_card["id"]) if existing_card else 0,
                    payload=payload,
                    confidence_score=combined_confidence_score,
                    confidence_level=confidence_level,
                    verification_status="pending_confirmation",
                    source_entries=combined_validation_sources,
                    winning_source=payload.get("winning_source") if isinstance(payload.get("winning_source"), dict) else {},
                    rejected_sources=[],
                    conflict_summary={"rule": "pending_confirmation", "reason": "Awaiting stronger corroboration before canonical card sync."},
                    confidence_reason=confidence_reason,
                    source_rollup={
                        "source_count": combined_source_count,
                        "source_names": self._source_display_names(combined_validation_sources),
                        "confidence_level": confidence_level,
                        "image_confirmation": image_confirmation,
                    },
                    canonical_values=pending_canonical_values,
                )
                return {
                    "status": "pending_confirmation",
                    "card_code": card_code,
                    "source_count": combined_source_count,
                    "confidence_score": combined_confidence_score,
                    "confidence_level": confidence_level,
                    "sources": combined_validation_sources,
                    "verification_status": "pending_confirmation",
                    "source_rollup": {
                        "source_count": combined_source_count,
                        "source_names": self._source_display_names(combined_validation_sources),
                        "confidence_level": confidence_level,
                        "image_confirmation": image_confirmation,
                    },
                    "confidence_reason": confidence_reason,
                    "operator_message": self._build_operator_message(
                        card_code=card_code,
                        source_entries=combined_validation_sources,
                        confidence_level=confidence_level,
                        confidence_score=combined_confidence_score,
                        action="pending_confirmation",
                        headline=f"Miru found new evidence for {card_code}, but it is waiting for more corroboration before treating it as final.",
                        summary=f"Miru currently has {combined_source_count} confirming source{'s' if combined_source_count != 1 else ''}; at least {self.min_verified_sources} are required for final verified knowledge.",
                    ),
                }

            existing_sources = self._load_json_list(existing_card["sources_json"] if existing_card else "[]")
            merged_sources = self._merge_source_keys(existing_sources, combined_validation_sources)
            aliases_json = existing_card["aliases_json"] if existing_card else "[]"
            decision_context = self._build_decision_context(existing_validation, combined_validation_sources)
            field_decisions: dict[str, dict[str, Any]] = {}
            merged_card = {
                "canonical_code": card_code,
                "set_family": set_family,
                "set_code": set_code or (existing_card["set_code"] if existing_card else ""),
                "card_number": str(payload.get("card_number") or "").strip() or (existing_card["card_number"] if existing_card else ""),
                "set_name": self._merge_text("set_name", existing_card["set_name"] if existing_card else "", payload.get("set_name"), decision_context, field_decisions),
                "card_name": self._merge_text("card_name", existing_card["card_name"] if existing_card else "", payload.get("card_name"), decision_context, field_decisions),
                "rarity": self._merge_text("rarity", existing_card["rarity"] if existing_card else "", payload.get("rarity"), decision_context, field_decisions),
                "color": self._merge_text("color", existing_card["color"] if existing_card else "", payload.get("color"), decision_context, field_decisions),
                "card_type": self._merge_text("card_type", existing_card["card_type"] if existing_card else "", payload.get("card_type"), decision_context, field_decisions),
                "cost": self._merge_int("cost", existing_card["cost"] if existing_card else None, payload.get("cost"), decision_context, field_decisions),
                "power": self._merge_text("power", existing_card["power"] if existing_card else "", payload.get("power"), decision_context, field_decisions),
                "counter": self._merge_text("counter", existing_card["counter"] if existing_card else "", payload.get("counter"), decision_context, field_decisions),
                "attribute": self._merge_text("attribute", existing_card["attribute"] if existing_card else "", payload.get("attribute"), decision_context, field_decisions),
                "traits": self._merge_text("traits", existing_card["traits"] if existing_card else "", payload.get("traits"), decision_context, field_decisions),
                "life": self._merge_text("life", existing_card["life"] if existing_card else "", payload.get("life"), decision_context, field_decisions),
                "block_icon": existing_card["block_icon"] if existing_card else "",
                "effect_text": self._merge_text("effect_text", existing_card["effect_text"] if existing_card else "", payload.get("effect_text"), decision_context, field_decisions),
                "trigger_text": self._merge_text("trigger_text", existing_card["trigger_text"] if existing_card else "", payload.get("trigger_text"), decision_context, field_decisions),
                "illustrator": self._merge_text("illustrator", existing_card["illustrator"] if existing_card else "", payload.get("illustrator"), decision_context, field_decisions),
                "confidence_level": confidence_level,
                "verification_status": self._verification_status(combined_source_count, image_confirmation),
                "source_rollup_json": json.dumps(
                    {
                        "source_count": combined_source_count,
                        "source_names": self._source_display_names(combined_validation_sources),
                        "confidence_level": confidence_level,
                        "image_confirmation": image_confirmation,
                    },
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                "aliases_json": aliases_json,
                "sources_json": json.dumps(merged_sources, ensure_ascii=True, sort_keys=True),
            }
            conflict_summary = self._build_conflict_summary(field_decisions, combined_validation_sources, decision_context)
            winning_source = self._build_winning_source(combined_validation_sources, conflict_summary, decision_context)
            rejected_sources = self._build_rejected_sources(combined_validation_sources, conflict_summary)
            confidence_reason = self._describe_confidence(
                source_entries=combined_validation_sources,
                conflict_count=int(conflict_summary.get("rejected_field_count") or 0),
            )
            self._log(
                event_type="card_sync_decision",
                message=(
                    f"{card_code}: chose {winning_source.get('source_id') or 'unknown'} "
                    f"({winning_source.get('trust_label') or 'unknown'})"
                    + (
                        f"; rejected {', '.join(item.get('source_id', '') for item in rejected_sources if item.get('source_id'))}"
                        if rejected_sources
                        else "; no conflicting lower-trust source won"
                    )
                    + f"; {confidence_reason}"
                ),
                card_code=card_code,
            )

            if existing_card:
                conn.execute(
                    """
                    UPDATE cards
                    SET set_family = ?, set_code = ?, card_number = ?, set_name = ?, card_name = ?, rarity = ?,
                        color = ?, card_type = ?, cost = ?, power = ?, counter = ?, attribute = ?,
                        traits = ?, life = ?, effect_text = ?, trigger_text = ?, illustrator = ?, confidence_level = ?,
                        verification_status = ?, source_rollup_json = ?, aliases_json = ?, sources_json = ?
                    WHERE canonical_code = ?
                    """,
                    (
                        merged_card["set_family"],
                        merged_card["set_code"],
                        merged_card["card_number"],
                        merged_card["set_name"],
                        merged_card["card_name"],
                        merged_card["rarity"],
                        merged_card["color"],
                        merged_card["card_type"],
                        merged_card["cost"],
                        merged_card["power"],
                        merged_card["counter"],
                        merged_card["attribute"],
                        merged_card["traits"],
                        merged_card["life"],
                        merged_card["effect_text"],
                        merged_card["trigger_text"],
                        merged_card["illustrator"],
                        merged_card["confidence_level"],
                        merged_card["verification_status"],
                        merged_card["source_rollup_json"],
                        merged_card["aliases_json"],
                        merged_card["sources_json"],
                        card_code,
                    ),
                )
                card_id = int(existing_card["id"])
            else:
                conn.execute(
                    """
                    INSERT INTO cards (
                        canonical_code, set_family, set_code, card_number, set_name, card_name, rarity, color,
                        card_type, cost, power, counter, attribute, traits, life, block_icon,
                        effect_text, trigger_text, illustrator, confidence_level, verification_status,
                        source_rollup_json, aliases_json, sources_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        merged_card["canonical_code"],
                        merged_card["set_family"],
                        merged_card["set_code"],
                        merged_card["card_number"],
                        merged_card["set_name"],
                        merged_card["card_name"],
                        merged_card["rarity"],
                        merged_card["color"],
                        merged_card["card_type"],
                        merged_card["cost"],
                        merged_card["power"],
                        merged_card["counter"],
                        merged_card["attribute"],
                        merged_card["traits"],
                        merged_card["life"],
                        merged_card["block_icon"],
                        merged_card["effect_text"],
                        merged_card["trigger_text"],
                        merged_card["illustrator"],
                        merged_card["confidence_level"],
                        merged_card["verification_status"],
                        merged_card["source_rollup_json"],
                        merged_card["aliases_json"],
                        merged_card["sources_json"],
                    ),
                )
                card_id = int(
                    conn.execute(
                        "SELECT id FROM cards WHERE canonical_code = ?",
                        (card_code,),
                    ).fetchone()["id"]
                )

            canonical_values = {
                "card_code": card_code,
                "set_family": merged_card["set_family"],
                "set_code": merged_card["set_code"],
                "card_number": merged_card["card_number"],
                "set_name": merged_card["set_name"],
                "card_name": merged_card["card_name"],
                "rarity": merged_card["rarity"],
                "color": merged_card["color"],
                "card_type": merged_card["card_type"],
                "cost": "" if merged_card["cost"] is None else str(merged_card["cost"]),
                "power": merged_card["power"],
                "counter": merged_card["counter"],
                "attribute": merged_card["attribute"],
                "traits": merged_card["traits"],
                "life": merged_card["life"],
                "effect_text": merged_card["effect_text"],
                "trigger_text": merged_card["trigger_text"],
                "illustrator": merged_card["illustrator"],
            }
            self._upsert_validation_row(
                conn,
                card_code=card_code,
                card_id=card_id,
                payload=payload,
                confidence_score=combined_confidence_score,
                confidence_level=confidence_level,
                verification_status=merged_card["verification_status"],
                source_entries=combined_validation_sources,
                winning_source=winning_source,
                rejected_sources=rejected_sources,
                conflict_summary=conflict_summary,
                confidence_reason=confidence_reason,
                source_rollup=json.loads(merged_card["source_rollup_json"]),
                canonical_values=canonical_values,
            )
        return {
            "status": "synced",
            "card_code": card_code,
            "source_count": combined_source_count,
            "confidence_score": combined_confidence_score,
            "confidence_level": confidence_level,
            "sources": combined_validation_sources,
            "verification_status": merged_card["verification_status"],
            "source_rollup": json.loads(merged_card["source_rollup_json"]),
            "confidence_reason": confidence_reason,
            "conflict_summary": conflict_summary,
            "operator_message": self._build_operator_message(
                card_code=card_code,
                source_entries=combined_validation_sources,
                confidence_level=confidence_level,
                confidence_score=combined_confidence_score,
                action="verified",
                headline=f"Miru verified new information for {card_code}.",
                summary=f"Confidence level: {confidence_level} ({combined_source_count} source{'s' if combined_source_count != 1 else ''} confirmed).",
            ),
        }

    @staticmethod
    def _load_json_list(value: str) -> list[str]:
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [str(item).strip() for item in payload if str(item).strip()]

    @staticmethod
    def _merge_source_keys(existing: list[str], source_entries: list[dict[str, Any]]) -> list[str]:
        merged = list(existing)
        for entry in source_entries:
            key = str(entry.get("source_id") or "").strip()
            if key and key not in merged:
                merged.append(key)
        return merged

    def _resolve_source_profile(self, source_id: str) -> MiruSourceEntry:
        try:
            return get_source_entry(source_id, self._source_registry)
        except KeyError:
            return build_unknown_source_entry(source_id)

    @staticmethod
    def _build_source_entry(profile: MiruSourceEntry, record: NormalizedSourceRecord) -> dict[str, Any]:
        return {
            "source_id": record.source_id,
            "source_url": record.source_url,
            "source_reference": record.source_reference,
            "display_name": profile.source_name,
            "source_type": profile.source_type,
            "trust_tier": profile.trust_tier,
            "trust_label": profile.trust_label,
            "enabled": profile.enabled,
            "review_state": profile.review_state,
            "rate_limit_hint": profile.rate_limit_hint,
            "backoff_policy": profile.backoff_policy,
            "public_data_only": profile.public_data_only,
            "requires_login": profile.requires_login,
            "respect_site_policies": profile.respect_site_policies,
            "allow_aggressive_crawling": profile.allow_aggressive_crawling,
            "anti_crawl_policy": profile.anti_crawl_policy,
            "data_categories": list(profile.data_categories),
            "notes": profile.notes,
            "observed_at": record.fetched_at,
        }

    @staticmethod
    def _distinct_source_count(source_entries: list[dict[str, Any]]) -> int:
        return len(
            {
                str(entry.get("source_id") or "").strip().lower()
                for entry in source_entries
                if str(entry.get("source_id") or "").strip()
                and str(entry.get("evidence_role") or "").strip().lower() != "image-confirmation"
            }
        )

    @staticmethod
    def _set_family(set_code_or_card_code: str) -> str:
        text = str(set_code_or_card_code or "").strip().upper()
        if text.startswith("PRB-"):
            return "PRB"
        if text.startswith("P-"):
            return "P"
        if "-" in text:
            text = text.split("-", 1)[0]
        return "".join(char for char in text if char.isalpha())

    @staticmethod
    def _image_confirmation_state(source_entries: list[dict[str, Any]]) -> str:
        states = {
            str(entry.get("verification_status") or "").strip().lower()
            for entry in source_entries
            if str(entry.get("evidence_role") or "").strip().lower() == "image-confirmation"
        }
        if any("conflict" in state for state in states):
            return "conflict"
        if "verified_with_image_confirmation" in states or "source_backed_image_confirmation" in states:
            return "confirmed"
        return ""

    @staticmethod
    def _verification_status(source_count: int, image_confirmation: str) -> str:
        if image_confirmation == "conflict":
            return "pending-review-image-conflict"
        if source_count >= 3 and image_confirmation == "confirmed":
            return "verified-with-image-confirmation"
        if source_count >= 2 and image_confirmation == "confirmed":
            return "verified-with-image-confirmation"
        if source_count >= 3:
            return "high-confidence"
        if source_count >= 2:
            return "verified"
        return "pending_confirmation"

    @staticmethod
    def _merge_source_entries(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in [*existing, *incoming]:
            source_id = str(entry.get("source_id") or "").strip().lower()
            source_reference = str(entry.get("source_reference") or "").strip()
            if not source_id:
                continue
            merged[(source_id, source_reference)] = dict(entry)
        return sorted(
            merged.values(),
            key=lambda item: (
                int(item.get("trust_tier") or 4),
                str(item.get("display_name") or item.get("source_id") or ""),
                str(item.get("source_reference") or ""),
            ),
        )

    @staticmethod
    def _confidence_level(source_count: int) -> str:
        if source_count >= 3:
            return "high"
        if source_count >= 2:
            return "medium"
        return "low"

    @staticmethod
    def _source_display_names(source_entries: list[dict[str, Any]]) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for entry in source_entries:
            name = str(entry.get("display_name") or entry.get("source_name") or entry.get("source_id") or "").strip()
            if not name:
                continue
            normalized = name.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            names.append(name)
        return names

    def _build_operator_message(
        self,
        *,
        card_code: str,
        source_entries: list[dict[str, Any]],
        confidence_level: str,
        confidence_score: float,
        action: str,
        headline: str,
        summary: str,
    ) -> str:
        source_lines = self._source_display_names(source_entries)
        message_lines = [
            headline.strip(),
            "",
            summary.strip(),
            "",
            "Sources:",
        ]
        if source_lines:
            message_lines.extend(f"- {line}" for line in source_lines[:5])
        else:
            message_lines.append("- No confirmed public sources recorded yet.")
        message_lines.append("")
        message_lines.append(f"Confidence score: {confidence_score:.2f} ({confidence_level}).")
        if action == "pending_confirmation":
            message_lines.append("Miru will keep this evidence in its dossiers until more sources confirm it.")
        else:
            message_lines.append("Miru stored this knowledge in its verified dossiers and canonical catalog.")
        return "\n".join(message_lines)

    @staticmethod
    def _score_source_confidence(source_entries: list[dict[str, Any]]) -> float:
        if not source_entries:
            return 0.0
        best_tier = min(int(entry.get("trust_tier") or 4) for entry in source_entries)
        distinct_sources = len(
            {
                str(entry.get("source_id") or "").strip().lower()
                for entry in source_entries
                if str(entry.get("source_id") or "").strip()
                and str(entry.get("evidence_role") or "").strip().lower() != "image-confirmation"
            }
        )
        if best_tier <= 1:
            base = 0.95
        elif best_tier == 2:
            base = 0.78
        elif best_tier == 3:
            base = 0.58
        else:
            base = 0.35
        if best_tier == 2 and distinct_sources >= 2:
            base = min(base + 0.07, 0.85)
        if distinct_sources >= 3:
            base = min(base + 0.08, 0.98)
        if best_tier >= 3 and distinct_sources == 1:
            base = max(base - 0.05, 0.0)
        return round(base, 2)

    @staticmethod
    def _describe_confidence(*, source_entries: list[dict[str, Any]], conflict_count: int) -> str:
        if not source_entries:
            return "No source evidence was attached to this validation."
        best_tier = min(int(entry.get("trust_tier") or 4) for entry in source_entries)
        distinct_sources = len(
            {
                str(entry.get("source_id") or "").strip().lower()
                for entry in source_entries
                if str(entry.get("source_id") or "").strip()
                and str(entry.get("evidence_role") or "").strip().lower() != "image-confirmation"
            }
        )
        if best_tier == 1:
            reason = "Official source evidence drives verified confidence."
        elif best_tier == 2 and distinct_sources >= 2:
            reason = "Multiple high-confidence community sources agree, so Miru allows moderate confidence."
        elif best_tier == 2:
            reason = "Single high-confidence community source is accepted, but below official certainty."
        elif best_tier == 3:
            reason = "Secondary/reference evidence is advisory and kept below strong validation confidence."
        else:
            reason = "Experimental or unknown source evidence remains review-only unless stronger support exists."
        if conflict_count:
            reason += f" {conflict_count} field conflict(s) were rejected in favor of stronger existing evidence."
        return reason

    def _build_decision_context(
        self,
        existing_validation: sqlite3.Row | None,
        incoming_sources: list[dict[str, Any]],
    ) -> dict[str, Any]:
        existing_sources = MiruProjectDbSync._load_json_objects(existing_validation["sources_json"] if existing_validation else "[]")
        existing_winning = MiruProjectDbSync._load_json_object(existing_validation["winning_source_json"] if existing_validation else "{}")
        existing_confidence = float(existing_validation["confidence"] if existing_validation else 0.0)
        existing_best_tier = min(
            [int(item.get("trust_tier") or 4) for item in existing_sources] or [int(existing_winning.get("trust_tier") or 4)]
        )
        incoming_best_tier = min([int(item.get("trust_tier") or 4) for item in incoming_sources] or [4])
        incoming_confidence = MiruProjectDbSync._score_source_confidence(incoming_sources)
        return {
            "existing_sources": existing_sources,
            "existing_winning_source": existing_winning,
            "existing_confidence": existing_confidence,
            "existing_best_tier": existing_best_tier,
            "existing_source_count": MiruProjectDbSync._distinct_source_count(existing_sources),
            "incoming_sources": incoming_sources,
            "incoming_best_tier": incoming_best_tier,
            "incoming_confidence": incoming_confidence,
            "incoming_source_count": MiruProjectDbSync._distinct_source_count(incoming_sources),
            "preferred_verified_sources": self.preferred_verified_sources,
        }

    def _merge_text(
        self,
        field_name: str,
        existing: Any,
        incoming: Any,
        context: dict[str, Any],
        field_decisions: dict[str, dict[str, Any]],
    ) -> str:
        incoming_text = clean_display_text(str(incoming or ""))
        existing_text = clean_display_text(str(existing or ""))
        selected, decision = self._select_value(field_name, existing_text, incoming_text, context)
        field_decisions[field_name] = decision
        return clean_display_text(str(selected or ""))

    def _merge_int(
        self,
        field_name: str,
        existing: Any,
        incoming: Any,
        context: dict[str, Any],
        field_decisions: dict[str, dict[str, Any]],
    ) -> int | None:
        incoming_value = MiruProjectDbSync._coerce_int(incoming)
        existing_value = MiruProjectDbSync._coerce_int(existing)
        selected, decision = self._select_value(field_name, existing_value, incoming_value, context)
        field_decisions[field_name] = decision
        return MiruProjectDbSync._coerce_int(selected)

    @staticmethod
    def _select_value(field_name: str, existing: Any, incoming: Any, context: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        existing_present = existing not in (None, "", [], {})
        incoming_present = incoming not in (None, "", [], {})
        if not incoming_present:
            return existing, {
                "field_name": field_name,
                "selected": "existing",
                "reason": "incoming-blank",
                "conflict": False,
            }
        if not existing_present:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "fill-missing",
                "conflict": False,
            }
        if existing == incoming:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "agreement",
                "conflict": False,
            }
        incoming_tier = int(context.get("incoming_best_tier") or 4)
        existing_tier = int(context.get("existing_best_tier") or 4)
        incoming_confidence = float(context.get("incoming_confidence") or 0.0)
        existing_confidence = float(context.get("existing_confidence") or 0.0)
        incoming_source_count = int(context.get("incoming_source_count") or 0)
        if incoming_tier < existing_tier:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "higher-trust-source",
                "conflict": True,
                "existing_value": existing,
                "incoming_value": incoming,
            }
        preferred_verified_sources = int(context.get("preferred_verified_sources") or 2)
        if incoming_tier == existing_tier and incoming_source_count >= preferred_verified_sources and incoming_confidence > existing_confidence:
            return incoming, {
                "field_name": field_name,
                "selected": "incoming",
                "reason": "same-tier-refresh-with-extra-confirmation",
                "conflict": True,
                "existing_value": existing,
                "incoming_value": incoming,
            }
        return existing, {
            "field_name": field_name,
            "selected": "existing",
            "reason": "preserve-existing-until-better-confirmation",
            "conflict": True,
            "existing_value": existing,
            "incoming_value": incoming,
        }

    @staticmethod
    def _build_conflict_summary(
        field_decisions: dict[str, dict[str, Any]],
        incoming_sources: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        rejected_fields = [
            decision["field_name"]
            for decision in field_decisions.values()
            if decision.get("conflict") and decision.get("selected") == "existing"
        ]
        accepted_conflicts = [
            decision["field_name"]
            for decision in field_decisions.values()
            if decision.get("conflict") and decision.get("selected") == "incoming"
        ]
        if rejected_fields:
            rule = "prefer-existing-higher-trust"
            summary = "Conflicting lower-trust data was rejected in favor of stronger previously verified evidence."
        elif accepted_conflicts:
            rule = "incoming-higher-trust-wins"
            summary = "Incoming higher-trust validation replaced weaker previously stored values."
        elif len(incoming_sources) >= 2 and int(context.get("incoming_best_tier") or 4) == 2:
            rule = "trusted-non-official-agreement"
            summary = "Multiple high-confidence community sources agreed, so Miru accepted a moderate-confidence validation."
        elif int(context.get("incoming_best_tier") or 4) >= 3:
            rule = "single-weak-source"
            summary = "Single weak source remained low confidence and was only accepted when no stronger verified value existed."
        else:
            rule = "no-conflict"
            summary = "No conflicting higher-trust evidence was present."
        return {
            "rule": rule,
            "summary": summary,
            "rejected_field_count": len(rejected_fields),
            "accepted_conflict_count": len(accepted_conflicts),
            "rejected_fields": rejected_fields,
            "accepted_conflict_fields": accepted_conflicts,
            "field_decisions": field_decisions,
        }

    @staticmethod
    def _build_winning_source(
        source_entries: list[dict[str, Any]],
        conflict_summary: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if not source_entries:
            return dict(context.get("existing_winning_source") or {})
        if conflict_summary.get("rule") == "prefer-existing-higher-trust":
            existing_winner = dict(context.get("existing_winning_source") or {})
            if existing_winner:
                return existing_winner
        if conflict_summary.get("rule") == "single-weak-source" and len(source_entries) == 1:
            return dict(source_entries[0])
        winner = min(
            source_entries,
            key=lambda entry: (
                int(entry.get("trust_tier") or 4),
                str(entry.get("source_id") or ""),
            ),
        )
        return dict(winner)

    @staticmethod
    def _build_rejected_sources(source_entries: list[dict[str, Any]], conflict_summary: dict[str, Any]) -> list[dict[str, Any]]:
        if not source_entries:
            return []
        if not conflict_summary.get("rejected_fields"):
            return []
        return [
            {
                **dict(entry),
                "rejected_fields": list(conflict_summary.get("rejected_fields") or []),
                "rejection_reason": "lower-trust conflicting source did not override stronger existing evidence",
            }
            for entry in source_entries
        ]

    @staticmethod
    def _load_json_objects(value: str) -> list[dict[str, Any]]:
        try:
            payload = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        return [dict(item) for item in payload if isinstance(item, dict)]

    @staticmethod
    def _load_json_object(value: str) -> dict[str, Any]:
        try:
            payload = json.loads(value or "{}")
        except json.JSONDecodeError:
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _log(
        self,
        *,
        event_type: str,
        message: str,
        card_code: str = "",
        level: str = "info",
    ) -> None:
        if self.logger is None:
            return
        self.logger(
            level=level,
            event_type=event_type,
            message=message,
            card_code=card_code,
            task_type="project_db_sync",
        )
