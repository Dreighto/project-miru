from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from datetime import UTC, datetime
from threading import Lock
from typing import Any, Callable

from config.miru_storage_layout import build_storage_layout

try:
    from dashboard.miru_intel_models import CardDossier
except Exception:  # pragma: no cover - optional import for runtime flexibility
    CardDossier = Any  # type: ignore[misc,assignment]


DEFAULT_INSIGHT_CACHE_DB_PATH = build_storage_layout().recommended_db_paths()["insight_cache_db"]
INSIGHT_CACHE_DB_PATH = os.getenv("MIRU_INSIGHT_CACHE_DB_PATH", DEFAULT_INSIGHT_CACHE_DB_PATH)
INSIGHT_CACHE_SCHEMA_VERSION = "2026-03-cache-1"
DEFAULT_CARD_CACHE_FRESHNESS_SECONDS = 3600
DEFAULT_SURFACE_CACHE_FRESHNESS_SECONDS = 300
CARD_INTELLIGENCE_SUMMARY_TYPE = "card_intelligence_summary"
DEFAULT_MAINTENANCE_ROOT = build_storage_layout().recommended_runtime_paths()["maintenance_root"]
DEFAULT_INSIGHT_METRICS_ROLLUP_PATH = str(Path(DEFAULT_MAINTENANCE_ROOT) / "miru_insight_metrics_rollup.json")
INSIGHT_METRICS_ROLLUP_PATH = os.getenv("MIRU_INSIGHT_METRICS_ROLLUP_PATH", DEFAULT_INSIGHT_METRICS_ROLLUP_PATH)
DEFAULT_INSIGHT_METRICS_HISTORY_PATH = str(Path(DEFAULT_MAINTENANCE_ROOT) / "miru_insight_metrics_history.jsonl")
INSIGHT_METRICS_HISTORY_PATH = os.getenv("MIRU_INSIGHT_METRICS_HISTORY_PATH", DEFAULT_INSIGHT_METRICS_HISTORY_PATH)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip() or default)
    except Exception:
        return int(default)


METRICS_ROLLUP_FLUSH_SECONDS = max(_env_int("MIRU_INSIGHT_METRICS_FLUSH_SECONDS", 30), 5)
METRICS_ROLLUP_FLUSH_EVENTS = max(_env_int("MIRU_INSIGHT_METRICS_FLUSH_EVENTS", 20), 1)


class InsightCacheConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _json_dump(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)
    except Exception:
        return "{}"


def _json_load(payload: str) -> Any:
    try:
        return json.loads(payload or "{}")
    except Exception:
        return {}


def build_source_hash(payload: Any) -> str:
    normalized = _json_dump(payload)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _derive_freshness_window(
    *,
    last_verified_at: str,
    refresh_after_at: str,
    default_seconds: int,
) -> int:
    verified = _parse_timestamp(last_verified_at)
    refresh = _parse_timestamp(refresh_after_at)
    if verified and refresh:
        delta = int((refresh - verified).total_seconds())
        if delta > 0:
            return delta
    return int(max(default_seconds, 0))


def get_insight_cache_conn(db_path: str = INSIGHT_CACHE_DB_PATH):
    conn = sqlite3.connect(db_path, factory=InsightCacheConnection)
    conn.row_factory = sqlite3.Row
    return conn


def init_miru_insight_cache_schema(db_path: str = INSIGHT_CACHE_DB_PATH) -> None:
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with get_insight_cache_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS miru_insight_cache_schema_metadata (
                schema_key TEXT PRIMARY KEY,
                schema_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS card_insight_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                source_hash TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                freshness_window INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_id, insight_type)
            );

            CREATE TABLE IF NOT EXISTS leader_usage_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                source_hash TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                freshness_window INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_id, insight_type)
            );

            CREATE TABLE IF NOT EXISTS meta_insight_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                source_hash TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                freshness_window INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_id, insight_type)
            );

            CREATE TABLE IF NOT EXISTS strategy_fragments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                source_hash TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                freshness_window INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_id, insight_type)
            );

            CREATE TABLE IF NOT EXISTS surface_response_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT NOT NULL,
                insight_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                confidence REAL NOT NULL DEFAULT 0.0,
                source_hash TEXT NOT NULL DEFAULT '',
                last_verified_at TEXT NOT NULL DEFAULT '',
                freshness_window INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(entity_id, insight_type)
            );

            CREATE INDEX IF NOT EXISTS idx_card_insight_cache_entity ON card_insight_cache(entity_id, insight_type);
            CREATE INDEX IF NOT EXISTS idx_leader_usage_cache_entity ON leader_usage_cache(entity_id, insight_type);
            CREATE INDEX IF NOT EXISTS idx_meta_insight_cache_entity ON meta_insight_cache(entity_id, insight_type);
            CREATE INDEX IF NOT EXISTS idx_strategy_fragments_entity ON strategy_fragments(entity_id, insight_type);
            CREATE INDEX IF NOT EXISTS idx_surface_response_cache_entity ON surface_response_cache(entity_id, insight_type);
            """
        )
        now = utc_timestamp()
        conn.execute(
            """
            INSERT INTO miru_insight_cache_schema_metadata (schema_key, schema_value, updated_at)
            VALUES ('schema_version', ?, ?)
            ON CONFLICT(schema_key) DO UPDATE SET
                schema_value = excluded.schema_value,
                updated_at = excluded.updated_at
            """,
            (INSIGHT_CACHE_SCHEMA_VERSION, now),
        )


def _is_fresh(last_verified_at: str, freshness_window: int) -> bool:
    verified = _parse_timestamp(last_verified_at)
    if not verified:
        return False
    if int(freshness_window or 0) <= 0:
        return True
    age_seconds = int((datetime.now(UTC) - verified.replace(tzinfo=UTC)).total_seconds())
    return age_seconds <= int(freshness_window)


SUPPORTED_BACKFILL_INSIGHT_TYPES = {
    "card_insight",
    CARD_INTELLIGENCE_SUMMARY_TYPE,
    "usage_insight",
    "leader_insight",
    "strategy_insight",
    "meta_insight",
    "verified_loop_card_summary",
}


def normalize_backfill_insight_types(selected_insight_types: list[str] | tuple[str, ...] | None) -> set[str]:
    if not selected_insight_types:
        return set(SUPPORTED_BACKFILL_INSIGHT_TYPES)
    normalized: set[str] = set()
    for raw in list(selected_insight_types or []):
        candidate = str(raw or "").strip()
        if not candidate:
            continue
        if candidate == "card_intelligence_summary":
            candidate = CARD_INTELLIGENCE_SUMMARY_TYPE
        if candidate in SUPPORTED_BACKFILL_INSIGHT_TYPES:
            normalized.add(candidate)
    return normalized or set(SUPPORTED_BACKFILL_INSIGHT_TYPES)


def evaluate_cached_entry(
    cached_entry: dict[str, Any] | None,
    *,
    expected_source_hash: str,
) -> dict[str, Any]:
    if not cached_entry:
        return {
            "usable": False,
            "reason": "missing",
            "cached_source_hash": "",
            "expected_source_hash": str(expected_source_hash or "").strip(),
        }

    cached_source_hash = str(cached_entry.get("source_hash") or "").strip()
    if not cached_entry.get("fresh"):
        return {
            "usable": False,
            "reason": "stale",
            "cached_source_hash": cached_source_hash,
            "expected_source_hash": str(expected_source_hash or "").strip(),
        }
    if cached_source_hash != str(expected_source_hash or "").strip():
        return {
            "usable": False,
            "reason": "source_hash_mismatch",
            "cached_source_hash": cached_source_hash,
            "expected_source_hash": str(expected_source_hash or "").strip(),
        }
    return {
        "usable": True,
        "reason": "valid",
        "cached_source_hash": cached_source_hash,
        "expected_source_hash": str(expected_source_hash or "").strip(),
    }


class _InsightCacheMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._started_at = utc_timestamp()
        self._totals: dict[str, int] = {
            "hits": 0,
            "misses": 0,
            "invalidations": 0,
            "rebuilds": 0,
            "contextual_views": 0,
            "contextual_opportunities": 0,
        }
        self._by_insight_type: dict[str, dict[str, int]] = {}
        self._by_context: dict[str, dict[str, int]] = {}
        self._invalidations_by_reason: dict[str, int] = {}
        self._contextual_by_type: dict[str, int] = {}
        self._contextual_by_context: dict[str, int] = {}
        self._contextual_views_by_context: dict[str, int] = {}
        self._persistent_rollup_path = Path(INSIGHT_METRICS_ROLLUP_PATH)
        self._persistent_history_path = Path(INSIGHT_METRICS_HISTORY_PATH)
        self._persistent_rollup = self._load_persistent_rollup()
        self._pending_persistent_events = 0
        self._last_persistent_flush_at = time.monotonic()
        self._last_history_emit_at = time.monotonic()

    def _empty_counter_bucket(self) -> dict[str, int]:
        return {
            "hits": 0,
            "misses": 0,
            "invalidations": 0,
            "rebuilds": 0,
        }

    def _empty_persistent_rollup(self) -> dict[str, Any]:
        now = utc_timestamp()
        return {
            "schema_version": "2026-03-insight-metrics-rollup-1",
            "created_at": now,
            "updated_at": now,
            "flush_count": 0,
            "totals": {
                "hits": 0,
                "misses": 0,
                "invalidations": 0,
                "rebuilds": 0,
                "contextual_views": 0,
                "contextual_opportunities": 0,
            },
            "by_insight_type": {},
            "by_context": {},
            "invalidations_by_reason": {},
            "contextual": {
                "opportunities_by_type": {},
                "opportunities_by_context": {},
                "views_by_context": {},
            },
        }

    def _load_persistent_rollup(self) -> dict[str, Any]:
        if not self._persistent_rollup_path.is_file():
            return self._empty_persistent_rollup()
        try:
            payload = json.loads(self._persistent_rollup_path.read_text(encoding="utf-8"))
        except Exception:
            return self._empty_persistent_rollup()
        if not isinstance(payload, dict):
            return self._empty_persistent_rollup()
        baseline = self._empty_persistent_rollup()
        baseline.update(payload)
        baseline["totals"] = {
            **dict(self._empty_persistent_rollup().get("totals") or {}),
            **dict(payload.get("totals") or {}),
        }
        baseline["by_insight_type"] = dict(payload.get("by_insight_type") or {})
        baseline["by_context"] = dict(payload.get("by_context") or {})
        baseline["invalidations_by_reason"] = dict(payload.get("invalidations_by_reason") or {})
        contextual = dict(payload.get("contextual") or {})
        baseline["contextual"] = {
            "opportunities_by_type": dict(contextual.get("opportunities_by_type") or {}),
            "opportunities_by_context": dict(contextual.get("opportunities_by_context") or {}),
            "views_by_context": dict(contextual.get("views_by_context") or {}),
        }
        return baseline

    def _ensure_type_bucket(self, insight_type: str) -> dict[str, int]:
        normalized = str(insight_type or "").strip() or "unknown"
        bucket = self._by_insight_type.get(normalized)
        if bucket is None:
            bucket = self._empty_counter_bucket()
            self._by_insight_type[normalized] = bucket
        return bucket

    def _ensure_context_bucket(self, context_tag: str) -> dict[str, int]:
        normalized = str(context_tag or "").strip() or "unspecified"
        bucket = self._by_context.get(normalized)
        if bucket is None:
            bucket = self._empty_counter_bucket()
            self._by_context[normalized] = bucket
        return bucket

    def _ensure_rollup_counter_bucket(self, parent: dict[str, Any], key: str) -> dict[str, int]:
        bucket = parent.get(key)
        if not isinstance(bucket, dict):
            bucket = self._empty_counter_bucket()
            parent[key] = bucket
        else:
            for metric in ("hits", "misses", "invalidations", "rebuilds"):
                bucket[metric] = int(bucket.get(metric) or 0)
        return bucket

    def _rollup_total(self, metric: str, amount: int = 1) -> None:
        totals = dict(self._persistent_rollup.get("totals") or {})
        totals[metric] = int(totals.get(metric) or 0) + int(amount)
        self._persistent_rollup["totals"] = totals

    def _rollup_record_cache_metric(self, insight_type: str, context_tag: str, metric: str) -> None:
        by_type = dict(self._persistent_rollup.get("by_insight_type") or {})
        by_context = dict(self._persistent_rollup.get("by_context") or {})
        type_key = str(insight_type or "").strip() or "unknown"
        context_key = str(context_tag or "").strip() or "unspecified"
        self._ensure_rollup_counter_bucket(by_type, type_key)[metric] += 1
        self._ensure_rollup_counter_bucket(by_context, context_key)[metric] += 1
        self._persistent_rollup["by_insight_type"] = by_type
        self._persistent_rollup["by_context"] = by_context

    def _rollup_record_contextual_view(self, context_tag: str) -> None:
        contextual = dict(self._persistent_rollup.get("contextual") or {})
        views = dict(contextual.get("views_by_context") or {})
        context_key = str(context_tag or "").strip() or "unspecified"
        views[context_key] = int(views.get(context_key) or 0) + 1
        contextual["views_by_context"] = views
        self._persistent_rollup["contextual"] = contextual
        self._rollup_total("contextual_views")

    def _rollup_record_contextual_opportunity(self, context_tag: str, opportunity_type: str) -> None:
        contextual = dict(self._persistent_rollup.get("contextual") or {})
        by_type = dict(contextual.get("opportunities_by_type") or {})
        by_context = dict(contextual.get("opportunities_by_context") or {})
        type_key = str(opportunity_type or "").strip() or "unknown"
        context_key = str(context_tag or "").strip() or "unspecified"
        by_type[type_key] = int(by_type.get(type_key) or 0) + 1
        by_context[context_key] = int(by_context.get(context_key) or 0) + 1
        contextual["opportunities_by_type"] = by_type
        contextual["opportunities_by_context"] = by_context
        self._persistent_rollup["contextual"] = contextual
        self._rollup_total("contextual_opportunities")

    def _mark_persistent_dirty(self) -> None:
        self._pending_persistent_events += 1
        elapsed = time.monotonic() - self._last_persistent_flush_at
        if self._pending_persistent_events >= METRICS_ROLLUP_FLUSH_EVENTS or elapsed >= METRICS_ROLLUP_FLUSH_SECONDS:
            self._flush_persistent(force=False)

    def _flush_persistent(self, *, force: bool) -> None:
        if not force and self._pending_persistent_events <= 0:
            return
        now = utc_timestamp()
        self._persistent_rollup["updated_at"] = now
        self._persistent_rollup["flush_count"] = int(self._persistent_rollup.get("flush_count") or 0) + 1
        try:
            self._persistent_rollup_path.parent.mkdir(parents=True, exist_ok=True)
            self._persistent_rollup_path.write_text(
                json.dumps(self._persistent_rollup, ensure_ascii=True, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except Exception:
            return
        history_elapsed = time.monotonic() - self._last_history_emit_at
        if force or history_elapsed >= METRICS_ROLLUP_FLUSH_SECONDS:
            history_entry = {
                "generated_at": now,
                "totals": dict(self._persistent_rollup.get("totals") or {}),
                "by_insight_type": dict(self._persistent_rollup.get("by_insight_type") or {}),
                "by_context": dict(self._persistent_rollup.get("by_context") or {}),
                "invalidations_by_reason": dict(self._persistent_rollup.get("invalidations_by_reason") or {}),
                "contextual": dict(self._persistent_rollup.get("contextual") or {}),
            }
            try:
                self._persistent_history_path.parent.mkdir(parents=True, exist_ok=True)
                with self._persistent_history_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(history_entry, ensure_ascii=True, sort_keys=True))
                    handle.write("\n")
                self._last_history_emit_at = time.monotonic()
            except Exception:
                pass
        self._pending_persistent_events = 0
        self._last_persistent_flush_at = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._started_at = utc_timestamp()
            self._totals = {
                "hits": 0,
                "misses": 0,
                "invalidations": 0,
                "rebuilds": 0,
                "contextual_views": 0,
                "contextual_opportunities": 0,
            }
            self._by_insight_type = {}
            self._by_context = {}
            self._invalidations_by_reason = {}
            self._contextual_by_type = {}
            self._contextual_by_context = {}
            self._contextual_views_by_context = {}

    def record_hit(self, insight_type: str, context_tag: str = "") -> None:
        with self._lock:
            self._totals["hits"] += 1
            self._ensure_type_bucket(insight_type)["hits"] += 1
            self._ensure_context_bucket(context_tag)["hits"] += 1
            self._rollup_total("hits")
            self._rollup_record_cache_metric(insight_type, context_tag, "hits")
            self._mark_persistent_dirty()

    def record_miss(self, insight_type: str, context_tag: str = "") -> None:
        with self._lock:
            self._totals["misses"] += 1
            self._ensure_type_bucket(insight_type)["misses"] += 1
            self._ensure_context_bucket(context_tag)["misses"] += 1
            self._rollup_total("misses")
            self._rollup_record_cache_metric(insight_type, context_tag, "misses")
            self._mark_persistent_dirty()

    def record_invalidation(self, insight_type: str, reason: str, context_tag: str = "") -> None:
        with self._lock:
            self._totals["invalidations"] += 1
            self._ensure_type_bucket(insight_type)["invalidations"] += 1
            self._ensure_context_bucket(context_tag)["invalidations"] += 1
            normalized_reason = str(reason or "").strip() or "unknown"
            self._invalidations_by_reason[normalized_reason] = self._invalidations_by_reason.get(normalized_reason, 0) + 1
            invalidations_by_reason = dict(self._persistent_rollup.get("invalidations_by_reason") or {})
            invalidations_by_reason[normalized_reason] = int(invalidations_by_reason.get(normalized_reason) or 0) + 1
            self._persistent_rollup["invalidations_by_reason"] = invalidations_by_reason
            self._rollup_total("invalidations")
            self._rollup_record_cache_metric(insight_type, context_tag, "invalidations")
            self._mark_persistent_dirty()

    def record_rebuild(self, insight_type: str, context_tag: str = "") -> None:
        with self._lock:
            self._totals["rebuilds"] += 1
            self._ensure_type_bucket(insight_type)["rebuilds"] += 1
            self._ensure_context_bucket(context_tag)["rebuilds"] += 1
            self._rollup_total("rebuilds")
            self._rollup_record_cache_metric(insight_type, context_tag, "rebuilds")
            self._mark_persistent_dirty()

    def record_contextual_view(self, context_tag: str = "") -> None:
        normalized_context = str(context_tag or "").strip() or "unspecified"
        with self._lock:
            self._totals["contextual_views"] += 1
            self._contextual_views_by_context[normalized_context] = (
                int(self._contextual_views_by_context.get(normalized_context) or 0) + 1
            )
            self._rollup_record_contextual_view(normalized_context)
            self._mark_persistent_dirty()

    def record_contextual_opportunity(self, opportunity_type: str, *, context_tag: str = "") -> None:
        normalized_type = str(opportunity_type or "").strip() or "unknown"
        normalized_context = str(context_tag or "").strip() or "unspecified"
        with self._lock:
            self._totals["contextual_opportunities"] += 1
            self._contextual_by_type[normalized_type] = int(self._contextual_by_type.get(normalized_type) or 0) + 1
            self._contextual_by_context[normalized_context] = int(self._contextual_by_context.get(normalized_context) or 0) + 1
            self._rollup_record_contextual_opportunity(normalized_context, normalized_type)
            self._mark_persistent_dirty()

    def flush_persistent(self) -> None:
        with self._lock:
            self._flush_persistent(force=True)

    def persistent_rollup_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                **dict(self._persistent_rollup),
                "rollup_path": str(self._persistent_rollup_path),
                "history_path": str(self._persistent_history_path),
            }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started_at": self._started_at,
                "generated_at": utc_timestamp(),
                "totals": dict(self._totals),
                "by_insight_type": {
                    key: dict(value)
                    for key, value in sorted(self._by_insight_type.items())
                },
                "by_context": {
                    key: dict(value)
                    for key, value in sorted(self._by_context.items())
                },
                "invalidations_by_reason": dict(sorted(self._invalidations_by_reason.items())),
                "contextual": {
                    "opportunities_by_type": dict(sorted(self._contextual_by_type.items())),
                    "opportunities_by_context": dict(sorted(self._contextual_by_context.items())),
                    "views_by_context": dict(sorted(self._contextual_views_by_context.items())),
                },
                "persistent_rollup_path": str(self._persistent_rollup_path),
                "persistent_history_path": str(self._persistent_history_path),
            }


_INSIGHT_CACHE_METRICS = _InsightCacheMetrics()


def reset_insight_cache_metrics() -> None:
    _INSIGHT_CACHE_METRICS.reset()


def get_insight_cache_metrics_snapshot() -> dict[str, Any]:
    return _INSIGHT_CACHE_METRICS.snapshot()


def get_persistent_insight_cache_rollup_snapshot() -> dict[str, Any]:
    return _INSIGHT_CACHE_METRICS.persistent_rollup_snapshot()


def flush_insight_cache_metrics_rollup() -> None:
    _INSIGHT_CACHE_METRICS.flush_persistent()


def record_contextual_view_metric(*, context_tag: str = "") -> None:
    _INSIGHT_CACHE_METRICS.record_contextual_view(context_tag=context_tag)


def record_contextual_opportunity_metrics(
    opportunity_types: list[str] | tuple[str, ...],
    *,
    context_tag: str = "",
) -> None:
    for opportunity_type in list(opportunity_types or []):
        normalized_type = str(opportunity_type or "").strip()
        if not normalized_type:
            continue
        _INSIGHT_CACHE_METRICS.record_contextual_opportunity(
            normalized_type,
            context_tag=context_tag,
        )


class MiruInsightCacheRepository:
    def __init__(self, db_path: str = INSIGHT_CACHE_DB_PATH):
        self.db_path = db_path
        init_miru_insight_cache_schema(self.db_path)

    def _upsert_entry(
        self,
        table_name: str,
        *,
        entity_id: str,
        insight_type: str,
        payload: dict[str, Any],
        confidence: float,
        source_hash: str,
        last_verified_at: str,
        freshness_window: int,
    ) -> None:
        now = utc_timestamp()
        with get_insight_cache_conn(self.db_path) as conn:
            conn.execute(
                f"""
                INSERT INTO {table_name} (
                    entity_id, insight_type, payload_json, confidence, source_hash,
                    last_verified_at, freshness_window, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(entity_id, insight_type) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    confidence = excluded.confidence,
                    source_hash = excluded.source_hash,
                    last_verified_at = excluded.last_verified_at,
                    freshness_window = excluded.freshness_window,
                    updated_at = excluded.updated_at
                """,
                (
                    str(entity_id or "").strip(),
                    str(insight_type or "").strip(),
                    _json_dump(payload),
                    float(confidence or 0.0),
                    str(source_hash or "").strip(),
                    str(last_verified_at or "").strip(),
                    int(freshness_window or 0),
                    now,
                    now,
                ),
            )

    def _fetch_entry(self, table_name: str, *, entity_id: str, insight_type: str) -> dict[str, Any] | None:
        with get_insight_cache_conn(self.db_path) as conn:
            row = conn.execute(
                f"""
                SELECT entity_id, insight_type, payload_json, confidence, source_hash,
                       last_verified_at, freshness_window, created_at, updated_at
                FROM {table_name}
                WHERE entity_id = ? AND insight_type = ?
                LIMIT 1
                """,
                (str(entity_id or "").strip(), str(insight_type or "").strip()),
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["payload"] = _json_load(payload.pop("payload_json", "{}"))
        payload["fresh"] = _is_fresh(
            str(payload.get("last_verified_at") or ""),
            int(payload.get("freshness_window") or 0),
        )
        return payload

    def _delete_entry(self, table_name: str, *, entity_id: str, insight_type: str) -> int:
        with get_insight_cache_conn(self.db_path) as conn:
            cursor = conn.execute(
                f"DELETE FROM {table_name} WHERE entity_id = ? AND insight_type = ?",
                (str(entity_id or "").strip(), str(insight_type or "").strip()),
            )
        return int(cursor.rowcount or 0)

    def upsert_card_insight(
        self,
        *,
        entity_id: str,
        insight_type: str,
        payload: dict[str, Any],
        confidence: float,
        source_hash: str,
        last_verified_at: str,
        freshness_window: int,
    ) -> None:
        self._upsert_entry(
            "card_insight_cache",
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=confidence,
            source_hash=source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )

    def upsert_leader_usage(
        self,
        *,
        entity_id: str,
        insight_type: str,
        payload: dict[str, Any],
        confidence: float,
        source_hash: str,
        last_verified_at: str,
        freshness_window: int,
    ) -> None:
        self._upsert_entry(
            "leader_usage_cache",
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=confidence,
            source_hash=source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )

    def upsert_meta_insight(
        self,
        *,
        entity_id: str,
        insight_type: str,
        payload: dict[str, Any],
        confidence: float,
        source_hash: str,
        last_verified_at: str,
        freshness_window: int,
    ) -> None:
        self._upsert_entry(
            "meta_insight_cache",
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=confidence,
            source_hash=source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )

    def upsert_strategy_fragment(
        self,
        *,
        entity_id: str,
        insight_type: str,
        payload: dict[str, Any],
        confidence: float,
        source_hash: str,
        last_verified_at: str,
        freshness_window: int,
    ) -> None:
        self._upsert_entry(
            "strategy_fragments",
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=confidence,
            source_hash=source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )

    def upsert_surface_response(
        self,
        *,
        entity_id: str,
        insight_type: str,
        payload: dict[str, Any],
        confidence: float,
        source_hash: str,
        last_verified_at: str,
        freshness_window: int,
    ) -> None:
        self._upsert_entry(
            "surface_response_cache",
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=confidence,
            source_hash=source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )

    def fetch_card_insight(self, entity_id: str, *, insight_type: str = "card_insight") -> dict[str, Any] | None:
        return self._fetch_entry("card_insight_cache", entity_id=entity_id, insight_type=insight_type)

    def fetch_leader_usage(self, entity_id: str, *, insight_type: str) -> dict[str, Any] | None:
        return self._fetch_entry("leader_usage_cache", entity_id=entity_id, insight_type=insight_type)

    def fetch_meta_insight(self, entity_id: str, *, insight_type: str = "meta_insight") -> dict[str, Any] | None:
        return self._fetch_entry("meta_insight_cache", entity_id=entity_id, insight_type=insight_type)

    def fetch_strategy_fragment(self, entity_id: str, *, insight_type: str = "strategy_insight") -> dict[str, Any] | None:
        return self._fetch_entry("strategy_fragments", entity_id=entity_id, insight_type=insight_type)

    def fetch_surface_response(self, entity_id: str, *, insight_type: str) -> dict[str, Any] | None:
        return self._fetch_entry("surface_response_cache", entity_id=entity_id, insight_type=insight_type)

    def invalidate_card_insight(self, entity_id: str, *, insight_type: str = "card_insight") -> int:
        return self._delete_entry("card_insight_cache", entity_id=entity_id, insight_type=insight_type)

    def invalidate_leader_usage(self, entity_id: str, *, insight_type: str) -> int:
        return self._delete_entry("leader_usage_cache", entity_id=entity_id, insight_type=insight_type)

    def invalidate_meta_insight(self, entity_id: str, *, insight_type: str = "meta_insight") -> int:
        return self._delete_entry("meta_insight_cache", entity_id=entity_id, insight_type=insight_type)

    def invalidate_strategy_fragment(self, entity_id: str, *, insight_type: str = "strategy_insight") -> int:
        return self._delete_entry("strategy_fragments", entity_id=entity_id, insight_type=insight_type)

    def invalidate_surface_response(self, entity_id: str, *, insight_type: str) -> int:
        return self._delete_entry("surface_response_cache", entity_id=entity_id, insight_type=insight_type)

    def invalidate_card_family(self, entity_id: str) -> dict[str, int]:
        normalized = str(entity_id or "").strip().upper()
        if not normalized:
            return {}
        return {
            "card_insight_cache": self.invalidate_card_insight(normalized, insight_type="card_insight"),
            "card_intelligence_summary": self.invalidate_card_insight(
                normalized,
                insight_type=CARD_INTELLIGENCE_SUMMARY_TYPE,
            ),
            "leader_usage_cache": self.invalidate_leader_usage(normalized, insight_type="usage_insight")
            + self.invalidate_leader_usage(normalized, insight_type="leader_insight"),
            "meta_insight_cache": self.invalidate_meta_insight(normalized, insight_type="meta_insight"),
            "strategy_fragments": self.invalidate_strategy_fragment(normalized, insight_type="strategy_insight"),
            "surface_response_cache": self.invalidate_surface_response(
                normalized,
                insight_type="verified_loop_card_summary",
            )
            + self.invalidate_surface_response(normalized, insight_type="watchlist_card_brief")
            + self.invalidate_surface_response(normalized, insight_type="dashboard_card_insight"),
        }

    def cache_verified_dossier(self, dossier: CardDossier, *, run_id: str = "") -> dict[str, Any]:
        canonical_code = str(getattr(dossier, "canonical_code", "") or "").strip().upper()
        if not canonical_code:
            return {"cached": False, "reason": "missing_card_code", "entries": []}
        dossier_payload = dossier.to_dict() if hasattr(dossier, "to_dict") else dict(dossier or {})
        confidence_summary = dict(dossier_payload.get("confidence_summary") or {})
        refresh = dict(dossier_payload.get("refresh") or {})
        overall_score = float(confidence_summary.get("overall_score") or 0.0)
        last_verified_at = str(refresh.get("last_checked_at") or utc_timestamp())
        freshness_window = _derive_freshness_window(
            last_verified_at=last_verified_at,
            refresh_after_at=str(refresh.get("dynamic_refresh_after_at") or refresh.get("stable_refresh_after_at") or ""),
            default_seconds=DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
        )
        source_hash = build_source_hash(dossier_payload)
        identity = dict(dossier_payload.get("identity") or {})
        set_info = dict(dossier_payload.get("set_info") or {})
        relationships = list(dossier_payload.get("relationships") or [])
        gameplay_context = dict(dossier_payload.get("gameplay_context") or {})
        market_context = dict(dossier_payload.get("market_context") or {})
        top_relationship = dict(relationships[0] or {}) if relationships else {}

        card_truth_context = {
            "canonical_code": canonical_code,
            "identity": identity,
            "set_info": set_info,
            "confidence_summary": confidence_summary,
            "refresh": refresh,
            "relationship_count": len(relationships),
        }
        usage_truth_context = {
            "canonical_code": canonical_code,
            "usage_status": str(gameplay_context.get("status") or "placeholder"),
            "top_relationships": relationships[:5],
            "leader_hint": {
                "related_code": str(top_relationship.get("related_code") or ""),
                "related_name": str(top_relationship.get("related_name") or ""),
                "relationship_type": str(top_relationship.get("relationship_type") or ""),
            },
        }
        strategy_truth_context = {
            "canonical_code": canonical_code,
            "overall_state": str(confidence_summary.get("overall_state") or ""),
            "verified_fields": list(confidence_summary.get("verified_fields") or []),
            "likely_fields": list(confidence_summary.get("likely_fields") or []),
            "conflicting_fields": list(confidence_summary.get("conflicting_fields") or []),
            "missing_fields": list(confidence_summary.get("missing_fields") or []),
        }
        meta_truth_context = {
            "canonical_code": canonical_code,
            "market_context": market_context,
            "overall_state": str(confidence_summary.get("overall_state") or ""),
            "overall_score": overall_score,
        }
        surface_truth_context = {
            "canonical_code": canonical_code,
            "card_name": str(identity.get("card_name") or ""),
            "overall_state": str(confidence_summary.get("overall_state") or ""),
            "overall_score": overall_score,
            "set_name": str(set_info.get("set_name") or ""),
        }

        card_payload = {
            "canonical_code": canonical_code,
            "identity": identity,
            "set_info": set_info,
            "confidence_summary": confidence_summary,
            "refresh": refresh,
            "variant_count": len(list(dossier_payload.get("variants") or [])),
            "relationship_count": len(relationships),
            "run_id": str(run_id or ""),
        }
        card_summary_payload = {
            "canonical_code": canonical_code,
            "card_name": str(identity.get("card_name") or ""),
            "set_code": str(set_info.get("set_code") or ""),
            "set_name": str(set_info.get("set_name") or ""),
            "card_type": str(identity.get("card_type") or ""),
            "verification_state": str(confidence_summary.get("overall_state") or ""),
            "confidence_label": str(confidence_summary.get("confidence_label") or ""),
            "overall_confidence": overall_score,
            "top_relationship": {
                "related_code": str(top_relationship.get("related_code") or ""),
                "related_name": str(top_relationship.get("related_name") or ""),
                "relationship_type": str(top_relationship.get("relationship_type") or ""),
            },
            "usage_status": str(gameplay_context.get("status") or "placeholder"),
            "trend_label": str(market_context.get("trend_label") or "unknown"),
            "source": "verified_intel_cache",
        }
        usage_payload = {
            "canonical_code": canonical_code,
            "usage_status": str(gameplay_context.get("status") or "placeholder"),
            "gameplay_context": gameplay_context,
            "relationship_count": len(relationships),
            "top_relationships": relationships[:5],
            "run_id": str(run_id or ""),
        }
        strategy_payload = {
            "canonical_code": canonical_code,
            "overall_state": str(confidence_summary.get("overall_state") or ""),
            "verified_fields": list(confidence_summary.get("verified_fields") or []),
            "likely_fields": list(confidence_summary.get("likely_fields") or []),
            "conflicting_fields": list(confidence_summary.get("conflicting_fields") or []),
            "missing_fields": list(confidence_summary.get("missing_fields") or []),
            "run_id": str(run_id or ""),
        }
        meta_payload = {
            "canonical_code": canonical_code,
            "market_context": market_context,
            "overall_state": str(confidence_summary.get("overall_state") or ""),
            "overall_score": overall_score,
            "run_id": str(run_id or ""),
        }
        surface_payload = {
            "canonical_code": canonical_code,
            "card_name": str(identity.get("card_name") or ""),
            "overall_state": str(confidence_summary.get("overall_state") or ""),
            "overall_score": overall_score,
            "set_name": str(set_info.get("set_name") or ""),
            "run_id": str(run_id or ""),
            "source": "verified_intel_cache",
        }

        card_source_hash = build_source_hash(card_truth_context)
        card_summary_hash = build_source_hash(card_summary_payload)
        usage_source_hash = build_source_hash(usage_truth_context)
        strategy_source_hash = build_source_hash(strategy_truth_context)
        meta_source_hash = build_source_hash(meta_truth_context)
        surface_source_hash = build_source_hash(surface_truth_context)

        self.upsert_card_insight(
            entity_id=canonical_code,
            insight_type="card_insight",
            payload=card_payload,
            confidence=overall_score,
            source_hash=card_source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )
        self.upsert_card_insight(
            entity_id=canonical_code,
            insight_type=CARD_INTELLIGENCE_SUMMARY_TYPE,
            payload=card_summary_payload,
            confidence=overall_score,
            source_hash=card_summary_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )
        self.upsert_leader_usage(
            entity_id=canonical_code,
            insight_type="usage_insight",
            payload=usage_payload,
            confidence=overall_score,
            source_hash=usage_source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )
        if str(identity.get("card_type") or "").strip().lower() == "leader":
            self.upsert_leader_usage(
                entity_id=canonical_code,
                insight_type="leader_insight",
                payload=card_payload,
                confidence=overall_score,
                source_hash=card_source_hash,
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
        self.upsert_strategy_fragment(
            entity_id=canonical_code,
            insight_type="strategy_insight",
            payload=strategy_payload,
            confidence=overall_score,
            source_hash=strategy_source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )
        self.upsert_meta_insight(
            entity_id=canonical_code,
            insight_type="meta_insight",
            payload=meta_payload,
            confidence=overall_score,
            source_hash=meta_source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )
        self.upsert_surface_response(
            entity_id=canonical_code,
            insight_type="verified_loop_card_summary",
            payload=surface_payload,
            confidence=overall_score,
            source_hash=surface_source_hash,
            last_verified_at=last_verified_at,
            freshness_window=freshness_window,
        )
        return {
            "cached": True,
            "entity_id": canonical_code,
            "entries": [
                "card_insight",
                CARD_INTELLIGENCE_SUMMARY_TYPE,
                "usage_insight",
                "strategy_insight",
                "meta_insight",
                "verified_loop_card_summary",
            ]
            + (["leader_insight"] if str(identity.get("card_type") or "").strip().lower() == "leader" else []),
            "source_hash": card_source_hash,
        }

    def cache_card_from_dossier_store(
        self,
        dossier_store: Any,
        *,
        card_code: str,
        run_id: str = "",
        selected_insight_types: list[str] | tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        canonical_code = str(card_code or "").strip().upper()
        if not canonical_code:
            return {"cached": False, "reason": "missing_card_code", "entries": []}
        selected = normalize_backfill_insight_types(selected_insight_types)

        answer_context = dict(dossier_store.build_answer_context(canonical_code) or {})
        snapshot = dict(answer_context.get("snapshot") or {})
        if not snapshot:
            return {"cached": False, "reason": "missing_snapshot", "entity_id": canonical_code, "entries": []}

        usage_context = dict(dossier_store.build_usage_context(canonical_code) or {})
        intelligence_summary = dict(dossier_store.build_card_intelligence_summary(canonical_code) or {})
        strategy_context = dict(dossier_store.build_strategy_context(canonical_code) or {})
        last_verified_at = str(snapshot.get("verified_at") or snapshot.get("updated_at") or utc_timestamp())
        freshness_window = DEFAULT_CARD_CACHE_FRESHNESS_SECONDS
        overall_confidence = float(
            intelligence_summary.get("overall_confidence")
            or snapshot.get("confidence")
            or 0.0
        )
        answer_posture = dict(answer_context.get("answer_posture") or {})
        verified_facts = list(answer_context.get("facts") or [])
        answer_fragments = list(answer_context.get("answer_fragments") or [])
        strategy_posture = dict(strategy_context.get("strategy_posture") or intelligence_summary.get("strategy_posture") or {})
        meta_posture = dict(intelligence_summary.get("meta_posture") or {})
        top_leader = dict(intelligence_summary.get("top_leader") or {})

        card_payload = {
            "canonical_code": canonical_code,
            "card_name": str(snapshot.get("card_name") or ""),
            "set_code": str(snapshot.get("set_code") or ""),
            "set_name": str(snapshot.get("set_name") or ""),
            "card_type": str(snapshot.get("card_type") or ""),
            "verification_state": str(snapshot.get("verification_state") or ""),
            "confidence": float(snapshot.get("confidence") or 0.0),
            "intelligence_summary": intelligence_summary,
            "run_id": str(run_id or ""),
            "source": "verified_dossier_store",
        }
        summary_payload = {
            **intelligence_summary,
            "canonical_code": canonical_code,
            "run_id": str(run_id or ""),
            "source": "verified_dossier_store",
        }
        usage_payload = {
            "canonical_code": canonical_code,
            "usage_context": usage_context,
            "top_leader": top_leader,
            "run_id": str(run_id or ""),
            "source": "verified_dossier_store",
        }
        strategy_payload = {
            "canonical_code": canonical_code,
            "strategy_posture": strategy_posture,
            "strategy_records": list(strategy_context.get("strategy_records") or []),
            "run_id": str(run_id or ""),
            "source": "verified_dossier_store",
        }
        meta_payload = {
            "canonical_code": canonical_code,
            "meta_posture": meta_posture,
            "trend_label": str(intelligence_summary.get("trend_label") or "unknown"),
            "run_id": str(run_id or ""),
            "source": "verified_dossier_store",
        }
        surface_payload = {
            "canonical_code": canonical_code,
            "card_name": str(snapshot.get("card_name") or ""),
            "overall_state": str(snapshot.get("verification_state") or intelligence_summary.get("verification_state") or ""),
            "overall_score": overall_confidence,
            "set_name": str(snapshot.get("set_name") or ""),
            "summary": str(intelligence_summary.get("reusable_summary") or intelligence_summary.get("role_purpose") or ""),
            "run_id": str(run_id or ""),
            "source": "verified_dossier_store",
        }
        card_truth_context = {
            "snapshot": snapshot,
            "answer_posture": answer_posture,
            "summary": intelligence_summary,
            "verified_fact_count": len(verified_facts),
            "answer_fragment_count": len(answer_fragments),
        }
        usage_truth_context = {
            "usage_context": usage_context,
            "top_leader": top_leader,
        }
        strategy_truth_context = {
            "strategy_posture": strategy_posture,
            "strategy_records": list(strategy_context.get("strategy_records") or []),
        }
        meta_truth_context = {
            "meta_posture": meta_posture,
            "trend_label": str(intelligence_summary.get("trend_label") or "unknown"),
        }
        surface_truth_context = {
            "card_name": str(snapshot.get("card_name") or ""),
            "verification_state": str(snapshot.get("verification_state") or ""),
            "confidence": overall_confidence,
            "top_leader": top_leader,
            "summary_hint": str(intelligence_summary.get("role_purpose") or ""),
        }
        summary_truth_context = intelligence_summary or {
            "canonical_code": canonical_code,
            "card_name": str(snapshot.get("card_name") or ""),
            "verification_state": str(snapshot.get("verification_state") or ""),
            "overall_confidence": overall_confidence,
        }

        entries_written: list[str] = []
        if "card_insight" in selected:
            self.upsert_card_insight(
                entity_id=canonical_code,
                insight_type="card_insight",
                payload=card_payload,
                confidence=overall_confidence,
                source_hash=build_source_hash(card_truth_context),
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
            entries_written.append("card_insight")
        if CARD_INTELLIGENCE_SUMMARY_TYPE in selected:
            self.upsert_card_insight(
                entity_id=canonical_code,
                insight_type=CARD_INTELLIGENCE_SUMMARY_TYPE,
                payload=summary_payload,
                confidence=overall_confidence,
                source_hash=build_source_hash(summary_truth_context),
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
            entries_written.append(CARD_INTELLIGENCE_SUMMARY_TYPE)
        if "usage_insight" in selected:
            self.upsert_leader_usage(
                entity_id=canonical_code,
                insight_type="usage_insight",
                payload=usage_payload,
                confidence=overall_confidence,
                source_hash=build_source_hash(usage_truth_context),
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
            entries_written.append("usage_insight")
        if "leader_insight" in selected and str(snapshot.get("card_type") or "").strip().lower() == "leader":
            leader_payload = dict(dossier_store.build_leader_context(canonical_code) or {})
            self.upsert_leader_usage(
                entity_id=canonical_code,
                insight_type="leader_insight",
                payload=leader_payload,
                confidence=overall_confidence,
                source_hash=build_source_hash(leader_payload),
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
            entries_written.append("leader_insight")
        if "strategy_insight" in selected:
            self.upsert_strategy_fragment(
                entity_id=canonical_code,
                insight_type="strategy_insight",
                payload=strategy_payload,
                confidence=overall_confidence,
                source_hash=build_source_hash(strategy_truth_context),
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
            entries_written.append("strategy_insight")
        if "meta_insight" in selected:
            self.upsert_meta_insight(
                entity_id=canonical_code,
                insight_type="meta_insight",
                payload=meta_payload,
                confidence=overall_confidence,
                source_hash=build_source_hash(meta_truth_context),
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
            entries_written.append("meta_insight")
        if "verified_loop_card_summary" in selected:
            self.upsert_surface_response(
                entity_id=canonical_code,
                insight_type="verified_loop_card_summary",
                payload=surface_payload,
                confidence=overall_confidence,
                source_hash=build_source_hash(surface_truth_context),
                last_verified_at=last_verified_at,
                freshness_window=freshness_window,
            )
            entries_written.append("verified_loop_card_summary")

        if not entries_written:
            return {
                "cached": False,
                "reason": "no_supported_selected_insight_types",
                "entity_id": canonical_code,
                "entries": [],
            }
        return {
            "cached": True,
            "entity_id": canonical_code,
            "entries": entries_written,
        }

    def backfill_from_dossier_store(
        self,
        dossier_db_path: str | Path,
        *,
        card_codes: list[str] | None = None,
        selected_insight_types: list[str] | tuple[str, ...] | None = None,
        limit: int = 0,
        run_id: str = "insight-cache-backfill",
    ) -> dict[str, Any]:
        from tools.miru_dossier_store import MiruDossierStore

        resolved_db_path = Path(dossier_db_path)
        if not resolved_db_path.is_file():
            return {
                "ok": False,
                "reason": "dossier_db_missing",
                "dossier_db_path": str(resolved_db_path),
                "processed": 0,
                "cached": 0,
                "skipped": 0,
                "entries_written": 0,
            }

        requested_codes = [str(code or "").strip().upper() for code in list(card_codes or []) if str(code or "").strip()]
        if requested_codes:
            ordered_codes = requested_codes
        else:
            query = "SELECT card_code FROM cards WHERE trim(coalesce(card_code, '')) != '' ORDER BY updated_at DESC, card_code ASC"
            params: tuple[Any, ...] = ()
            if int(limit or 0) > 0:
                query += " LIMIT ?"
                params = (int(limit),)
            with closing(sqlite3.connect(resolved_db_path)) as conn:
                rows = conn.execute(query, params).fetchall()
            ordered_codes = [str(row[0] or "").strip().upper() for row in rows if str(row[0] or "").strip()]

        store = MiruDossierStore(resolved_db_path)
        processed = 0
        cached = 0
        skipped = 0
        entries_written = 0
        errors: list[dict[str, str]] = []
        for code in ordered_codes:
            processed += 1
            try:
                result = self.cache_card_from_dossier_store(
                    store,
                    card_code=code,
                    run_id=run_id,
                    selected_insight_types=selected_insight_types,
                )
            except Exception as exc:
                errors.append({"card_code": code, "error": str(exc)})
                continue
            if result.get("cached"):
                cached += 1
                entries_written += len(list(result.get("entries") or []))
            else:
                skipped += 1
        return {
            "ok": len(errors) == 0,
            "dossier_db_path": str(resolved_db_path),
            "processed": processed,
            "cached": cached,
            "skipped": skipped,
            "entries_written": entries_written,
            "errors": errors,
        }


def resolve_local_first_entity_insight(
    *,
    cache_fetcher: Callable[[str, str], dict[str, Any] | None],
    cache_writer: Callable[[dict[str, Any], float, str, str, int], None],
    cache_invalidator: Callable[[str, str, dict[str, Any]], None] | None = None,
    entity_id: str,
    insight_type: str,
    truth_context: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    confidence: float,
    last_verified_at: str,
    freshness_window: int,
    context_tag: str = "",
) -> dict[str, Any]:
    source_hash = build_source_hash(truth_context)
    cached = cache_fetcher(entity_id, insight_type)
    validation = evaluate_cached_entry(cached, expected_source_hash=source_hash)
    if validation["usable"]:
        _INSIGHT_CACHE_METRICS.record_hit(insight_type, context_tag=context_tag)
        payload = dict(cached.get("payload") or {})
        payload.setdefault("cache_status", "hit")
        return {
            "layer": "local_cached_insight",
            "source_hash": source_hash,
            "payload": payload,
            "cache_hit": True,
        }
    _INSIGHT_CACHE_METRICS.record_miss(insight_type, context_tag=context_tag)
    if cached and cache_invalidator is not None:
        _INSIGHT_CACHE_METRICS.record_invalidation(
            insight_type,
            str(validation.get("reason") or "unknown"),
            context_tag=context_tag,
        )
        cache_invalidator(entity_id, insight_type, validation)
    payload = dict(deterministic_builder() or {})
    cache_writer(payload, confidence, source_hash, last_verified_at, freshness_window)
    _INSIGHT_CACHE_METRICS.record_rebuild(insight_type, context_tag=context_tag)
    payload.setdefault("cache_status", "miss")
    return {
        "layer": "deterministic_logic",
        "source_hash": source_hash,
        "payload": payload,
        "cache_hit": False,
    }


def resolve_local_first_surface_response(
    repository: MiruInsightCacheRepository,
    *,
    entity_id: str,
    insight_type: str,
    truth_context: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    confidence: float,
    last_verified_at: str,
    freshness_window: int = DEFAULT_SURFACE_CACHE_FRESHNESS_SECONDS,
    context_tag: str = "",
) -> dict[str, Any]:
    return resolve_local_first_entity_insight(
        cache_fetcher=lambda key, kind: repository.fetch_surface_response(key, insight_type=kind),
        cache_writer=lambda payload, conf, source_hash, verified_at, window: repository.upsert_surface_response(
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=conf,
            source_hash=source_hash,
            last_verified_at=verified_at,
            freshness_window=window,
        ),
        cache_invalidator=lambda key, kind, _validation: repository.invalidate_surface_response(
            key,
            insight_type=kind,
        ),
        entity_id=entity_id,
        insight_type=insight_type,
        truth_context=truth_context,
        deterministic_builder=deterministic_builder,
        confidence=confidence,
        last_verified_at=last_verified_at,
        freshness_window=freshness_window,
        context_tag=context_tag,
    )


def resolve_local_first_card_insight(
    repository: MiruInsightCacheRepository,
    *,
    entity_id: str,
    insight_type: str,
    truth_context: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    confidence: float,
    last_verified_at: str,
    freshness_window: int = DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
    context_tag: str = "",
) -> dict[str, Any]:
    return resolve_local_first_entity_insight(
        cache_fetcher=lambda key, kind: repository.fetch_card_insight(key, insight_type=kind),
        cache_writer=lambda payload, conf, source_hash, verified_at, window: repository.upsert_card_insight(
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=conf,
            source_hash=source_hash,
            last_verified_at=verified_at,
            freshness_window=window,
        ),
        cache_invalidator=lambda key, kind, _validation: repository.invalidate_card_insight(
            key,
            insight_type=kind,
        ),
        entity_id=entity_id,
        insight_type=insight_type,
        truth_context=truth_context,
        deterministic_builder=deterministic_builder,
        confidence=confidence,
        last_verified_at=last_verified_at,
        freshness_window=freshness_window,
        context_tag=context_tag,
    )


def resolve_local_first_usage_insight(
    repository: MiruInsightCacheRepository,
    *,
    entity_id: str,
    insight_type: str = "usage_insight",
    truth_context: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    confidence: float,
    last_verified_at: str,
    freshness_window: int = DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
    context_tag: str = "",
) -> dict[str, Any]:
    return resolve_local_first_entity_insight(
        cache_fetcher=lambda key, kind: repository.fetch_leader_usage(key, insight_type=kind),
        cache_writer=lambda payload, conf, source_hash, verified_at, window: repository.upsert_leader_usage(
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=conf,
            source_hash=source_hash,
            last_verified_at=verified_at,
            freshness_window=window,
        ),
        cache_invalidator=lambda key, kind, _validation: repository.invalidate_leader_usage(
            key,
            insight_type=kind,
        ),
        entity_id=entity_id,
        insight_type=insight_type,
        truth_context=truth_context,
        deterministic_builder=deterministic_builder,
        confidence=confidence,
        last_verified_at=last_verified_at,
        freshness_window=freshness_window,
        context_tag=context_tag,
    )


def resolve_local_first_strategy_insight(
    repository: MiruInsightCacheRepository,
    *,
    entity_id: str,
    insight_type: str = "strategy_insight",
    truth_context: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    confidence: float,
    last_verified_at: str,
    freshness_window: int = DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
    context_tag: str = "",
) -> dict[str, Any]:
    return resolve_local_first_entity_insight(
        cache_fetcher=lambda key, kind: repository.fetch_strategy_fragment(key, insight_type=kind),
        cache_writer=lambda payload, conf, source_hash, verified_at, window: repository.upsert_strategy_fragment(
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=conf,
            source_hash=source_hash,
            last_verified_at=verified_at,
            freshness_window=window,
        ),
        cache_invalidator=lambda key, kind, _validation: repository.invalidate_strategy_fragment(
            key,
            insight_type=kind,
        ),
        entity_id=entity_id,
        insight_type=insight_type,
        truth_context=truth_context,
        deterministic_builder=deterministic_builder,
        confidence=confidence,
        last_verified_at=last_verified_at,
        freshness_window=freshness_window,
        context_tag=context_tag,
    )


def resolve_local_first_meta_insight(
    repository: MiruInsightCacheRepository,
    *,
    entity_id: str,
    insight_type: str = "meta_insight",
    truth_context: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    confidence: float,
    last_verified_at: str,
    freshness_window: int = DEFAULT_CARD_CACHE_FRESHNESS_SECONDS,
    context_tag: str = "",
) -> dict[str, Any]:
    return resolve_local_first_entity_insight(
        cache_fetcher=lambda key, kind: repository.fetch_meta_insight(key, insight_type=kind),
        cache_writer=lambda payload, conf, source_hash, verified_at, window: repository.upsert_meta_insight(
            entity_id=entity_id,
            insight_type=insight_type,
            payload=payload,
            confidence=conf,
            source_hash=source_hash,
            last_verified_at=verified_at,
            freshness_window=window,
        ),
        cache_invalidator=lambda key, kind, _validation: repository.invalidate_meta_insight(
            key,
            insight_type=kind,
        ),
        entity_id=entity_id,
        insight_type=insight_type,
        truth_context=truth_context,
        deterministic_builder=deterministic_builder,
        confidence=confidence,
        last_verified_at=last_verified_at,
        freshness_window=freshness_window,
        context_tag=context_tag,
    )


def resolve_local_first_verified_loop_summary(
    repository: MiruInsightCacheRepository,
    *,
    entity_id: str,
    insight_type: str = "verified_loop_card_summary",
    truth_context: dict[str, Any],
    deterministic_builder: Callable[[], dict[str, Any]],
    confidence: float,
    last_verified_at: str,
    freshness_window: int = DEFAULT_SURFACE_CACHE_FRESHNESS_SECONDS,
    context_tag: str = "",
) -> dict[str, Any]:
    return resolve_local_first_surface_response(
        repository,
        entity_id=entity_id,
        insight_type=insight_type,
        truth_context=truth_context,
        deterministic_builder=deterministic_builder,
        confidence=confidence,
        last_verified_at=last_verified_at,
        freshness_window=freshness_window,
        context_tag=context_tag,
    )


def _format_backfill_text(result: dict[str, Any]) -> str:
    lines = [
        "Miru insight cache backfill",
        f"OK: {bool(result.get('ok'))}",
        f"Dossier DB: {result.get('dossier_db_path', '')}",
        f"Processed: {int(result.get('processed') or 0)}",
        f"Cached: {int(result.get('cached') or 0)}",
        f"Skipped: {int(result.get('skipped') or 0)}",
        f"Entries written: {int(result.get('entries_written') or 0)}",
    ]
    errors = list(result.get("errors") or [])
    if errors:
        lines.append(f"Errors: {len(errors)}")
        for item in errors[:10]:
            lines.append(f"- {item.get('card_code', '')}: {item.get('error', '')}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Project Miru insight cache helper")
    parser.add_argument("--backfill-dossier-db", default="", help="Path to miru_dossiers.db for deterministic cache backfill.")
    parser.add_argument("--cache-db", default=INSIGHT_CACHE_DB_PATH, help="Path to miru_insight_cache.db.")
    parser.add_argument("--card-code", action="append", default=[], help="Optional card code(s) to backfill.")
    parser.add_argument("--limit", type=int, default=0, help="Optional limit when backfilling all dossier rows.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    repository = MiruInsightCacheRepository(args.cache_db)
    if args.backfill_dossier_db:
        result = repository.backfill_from_dossier_store(
            args.backfill_dossier_db,
            card_codes=list(args.card_code or []),
            limit=int(args.limit or 0),
        )
        if args.format == "json":
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(_format_backfill_text(result))
        return 0 if result.get("ok") else 1

    payload = {
        "ok": True,
        "cache_db_path": repository.db_path,
        "schema_version": INSIGHT_CACHE_SCHEMA_VERSION,
        "supported_card_insight_types": [
            "card_insight",
            CARD_INTELLIGENCE_SUMMARY_TYPE,
            "usage_insight",
            "leader_insight",
            "strategy_insight",
            "meta_insight",
            "verified_loop_card_summary",
            "watchlist_card_brief",
            "dashboard_card_insight",
        ],
    }
    if args.format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(_format_backfill_text({"ok": True, "dossier_db_path": "", "processed": 0, "cached": 0, "skipped": 0, "entries_written": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
