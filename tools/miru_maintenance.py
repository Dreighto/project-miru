from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.miru_storage_layout import build_storage_layout
from tools.miru_brain import (
    DEFAULT_SNAPSHOT_DESTINATION_KEYS,
    miru_brain_manifest,
    miru_brain_snapshot_bundle,
)
from tools.miru_dossier_store import MiruDossierStore, inspect_miru_dossier_store
from tools.miru_insight_cache import (
    CARD_INTELLIGENCE_SUMMARY_TYPE,
    INSIGHT_CACHE_SCHEMA_VERSION,
    MiruInsightCacheRepository,
    build_source_hash,
    flush_insight_cache_metrics_rollup,
    get_insight_cache_metrics_snapshot,
    get_persistent_insight_cache_rollup_snapshot,
    normalize_backfill_insight_types,
)
from tools.miru_learning_engine import load_learning_engine_status
from tools.miru_runtime_preflight import build_runtime_preflight_report


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 900
DEFAULT_DAILY_SNAPSHOT_INTERVAL_SECONDS = 24 * 60 * 60
DEFAULT_WEEKLY_SNAPSHOT_INTERVAL_SECONDS = 7 * 24 * 60 * 60
DOSSIER_GROWTH_CARD_THRESHOLD = 50
INSIGHT_CACHE_GROWTH_BYTES_THRESHOLD = 2 * 1024 * 1024
LEARNING_PROGRESS_THRESHOLD = 250
IMAGE_GROWTH_FILE_THRESHOLD = 100
DEFAULT_BACKFILL_PLAN_LIMIT = 120
DEFAULT_BACKFILL_APPLY_LIMIT = 40
BACKFILL_MIN_CONFIDENCE = 0.6
BACKFILL_RUN_ID_PREFIX = "maintenance-backfill"
MAINTENANCE_LEASE_SCHEMA_VERSION = "2026-03-maintenance-lease-1"
DEFAULT_MAINTENANCE_LEASE_SECONDS = 20 * 60
BACKFILL_GUARDRAIL_SCHEMA_VERSION = "2026-03-backfill-guardrails-1"
DEFAULT_BACKFILL_COOLDOWN_SECONDS = 45 * 60
DEFAULT_BACKFILL_APPLY_TIME_BUDGET_SECONDS = 8
STRONG_HOTSPOT_MISS_RATE = 0.6
STRONG_CONTEXT_REBUILD_RATE = 0.35
BACKFILL_GUARDRAIL_MAX_TRACKED_KEYS = 8000
SUPPORTED_QUEUE_INSIGHT_TYPES = (
    "card_insight",
    CARD_INTELLIGENCE_SUMMARY_TYPE,
    "usage_insight",
    "leader_insight",
    "strategy_insight",
    "meta_insight",
    "verified_loop_card_summary",
)


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(str(os.getenv(name, str(default))).strip() or default)
    except Exception:
        value = int(default)
    return max(int(value), int(minimum))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "")
    text = str(raw or "").strip().lower()
    if not text:
        return bool(default)
    return text not in {"0", "false", "off", "no"}


def _json_load(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_timestamp(value: str) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        return int(datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp())
    except ValueError:
        return 0


def _timestamp_from_epoch(epoch_seconds: int) -> str:
    return datetime.fromtimestamp(int(epoch_seconds), tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


def _maintenance_paths(project_root: Path) -> dict[str, Path]:
    layout = build_storage_layout(project_root=project_root)
    runtime_paths = layout.recommended_runtime_paths()
    root = Path(runtime_paths["maintenance_root"])
    return {
        "root": root,
        "state": Path(runtime_paths["maintenance_state_json"]),
        "log": Path(runtime_paths["maintenance_log_path"]),
        "cache_rollup_state": root / "miru_cache_effectiveness_rollup.json",
        "cache_rollup_history": root / "miru_cache_effectiveness_history.jsonl",
        "cache_hotspot_report": root / "miru_cache_hotspots.json",
        "lease": root / "miru_maintenance_lease.json",
        "backfill_plan": root / "miru_backfill_plan.json",
        "backfill_history": root / "miru_backfill_history.jsonl",
        "backfill_last_apply": root / "miru_backfill_last_apply.json",
        "backfill_guardrails": root / "miru_backfill_guardrails.json",
    }


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_utc_timestamp()}] {message}\n")


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        handle.write("\n")


def _safe_rate(numerator: int, denominator: int) -> float:
    if int(denominator or 0) <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _backfill_guardrail_key(entity_id: str, insight_type: str) -> str:
    return f"{str(entity_id or '').strip().upper()}|{str(insight_type or '').strip()}"


def _empty_backfill_guardrail_state() -> dict[str, Any]:
    return {
        "schema_version": BACKFILL_GUARDRAIL_SCHEMA_VERSION,
        "updated_at": _utc_timestamp(),
        "entity_insight_last_applied_at": {},
        "entity_insight_last_skip": {},
        "counters": {
            "planned_candidates": 0,
            "applied_count": 0,
            "cooldown_skips": 0,
            "no_grounded_reason_skips": 0,
            "cache_missing_requeues": 0,
            "stale_requeues": 0,
            "source_hash_mismatch_requeues": 0,
            "invalidation_requeues": 0,
            "hotspot_requeues": 0,
            "apply_time_budget_deferrals": 0,
        },
        "recent_cycle": {},
    }


def _normalize_backfill_guardrail_state(payload: dict[str, Any] | None) -> dict[str, Any]:
    baseline = _empty_backfill_guardrail_state()
    candidate = dict(payload or {})
    normalized = {
        **baseline,
        **candidate,
    }
    normalized["entity_insight_last_applied_at"] = dict(
        candidate.get("entity_insight_last_applied_at") or baseline["entity_insight_last_applied_at"]
    )
    normalized["entity_insight_last_skip"] = dict(
        candidate.get("entity_insight_last_skip") or baseline["entity_insight_last_skip"]
    )
    normalized["counters"] = {
        **dict(baseline.get("counters") or {}),
        **dict(candidate.get("counters") or {}),
    }
    normalized["recent_cycle"] = dict(candidate.get("recent_cycle") or {})
    return normalized


def _trim_guardrail_timestamp_map(values: dict[str, str], *, max_items: int) -> dict[str, str]:
    if len(values) <= int(max_items):
        return values
    sortable: list[tuple[int, str, str]] = []
    for key, raw in values.items():
        sortable.append((_parse_timestamp(str(raw or "")), key, str(raw or "")))
    sortable.sort(key=lambda item: (item[0], item[1]), reverse=True)
    trimmed: dict[str, str] = {}
    for _, key, ts in sortable[: int(max_items)]:
        trimmed[key] = ts
    return trimmed


def _trim_guardrail_skip_map(values: dict[str, dict[str, Any]], *, max_items: int) -> dict[str, dict[str, Any]]:
    if len(values) <= int(max_items):
        return values
    sortable: list[tuple[int, str, dict[str, Any]]] = []
    for key, payload in values.items():
        item = dict(payload or {})
        sortable.append((_parse_timestamp(str(item.get("at") or "")), key, item))
    sortable.sort(key=lambda item: (item[0], item[1]), reverse=True)
    trimmed: dict[str, dict[str, Any]] = {}
    for _, key, payload in sortable[: int(max_items)]:
        trimmed[key] = payload
    return trimmed


def _load_backfill_guardrail_state(path: Path) -> dict[str, Any]:
    return _normalize_backfill_guardrail_state(dict(_json_load(path, {})))


def _save_backfill_guardrail_state(path: Path, payload: dict[str, Any]) -> None:
    normalized = _normalize_backfill_guardrail_state(payload)
    normalized["updated_at"] = _utc_timestamp()
    normalized["entity_insight_last_applied_at"] = _trim_guardrail_timestamp_map(
        dict(normalized.get("entity_insight_last_applied_at") or {}),
        max_items=BACKFILL_GUARDRAIL_MAX_TRACKED_KEYS,
    )
    normalized["entity_insight_last_skip"] = _trim_guardrail_skip_map(
        dict(normalized.get("entity_insight_last_skip") or {}),
        max_items=BACKFILL_GUARDRAIL_MAX_TRACKED_KEYS,
    )
    _json_dump(path, normalized)


def _increment_counter(counters: dict[str, Any], key: str, amount: int = 1) -> None:
    counters[str(key)] = int(counters.get(str(key)) or 0) + int(amount or 0)


def _load_maintenance_lease_snapshot(lease_path: Path) -> dict[str, Any]:
    payload = dict(_json_load(lease_path, {}))
    now_ts = int(time.time())
    expires_ts = _parse_timestamp(str(payload.get("expires_at") or ""))
    acquired_ts = _parse_timestamp(str(payload.get("acquired_at") or ""))
    active = expires_ts > now_ts if expires_ts > 0 else False
    return {
        "path": str(lease_path),
        "exists": bool(lease_path.is_file()),
        "active": active,
        "owner_id": str(payload.get("owner_id") or ""),
        "pid": int(payload.get("pid") or 0),
        "acquired_at": str(payload.get("acquired_at") or ""),
        "expires_at": str(payload.get("expires_at") or ""),
        "seconds_remaining": max(expires_ts - now_ts, 0) if active else 0,
        "seconds_since_acquire": max(now_ts - acquired_ts, 0) if acquired_ts > 0 else 0,
        "lease_seconds": int(payload.get("lease_seconds") or 0),
        "schema_version": str(payload.get("schema_version") or ""),
    }


def _try_acquire_maintenance_lease(
    *,
    lease_path: Path,
    owner_id: str,
    project_root: Path,
    lease_seconds: int,
) -> dict[str, Any]:
    now_ts = int(time.time())
    resolved_lease_seconds = max(int(lease_seconds or DEFAULT_MAINTENANCE_LEASE_SECONDS), 30)
    lease_payload = {
        "schema_version": MAINTENANCE_LEASE_SCHEMA_VERSION,
        "owner_id": str(owner_id or "").strip(),
        "pid": int(os.getpid()),
        "project_root": str(project_root),
        "lease_seconds": int(resolved_lease_seconds),
        "acquired_at": _timestamp_from_epoch(now_ts),
        "expires_at": _timestamp_from_epoch(now_ts + int(resolved_lease_seconds)),
    }
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= int(getattr(os, "O_BINARY"))
    for attempt in range(2):
        try:
            fd = os.open(lease_path, flags)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(lease_payload, ensure_ascii=True, indent=2, sort_keys=True))
                handle.write("\n")
            return {
                "acquired": True,
                "reason": "lease_acquired",
                "path": str(lease_path),
                "owner_id": str(lease_payload.get("owner_id") or ""),
                "lease_seconds": int(resolved_lease_seconds),
                "acquired_at": str(lease_payload.get("acquired_at") or ""),
                "expires_at": str(lease_payload.get("expires_at") or ""),
            }
        except FileExistsError:
            existing = dict(_json_load(lease_path, {}))
            existing_expires = _parse_timestamp(str(existing.get("expires_at") or ""))
            existing_owner = str(existing.get("owner_id") or "")
            if existing_expires > now_ts:
                return {
                    "acquired": False,
                    "reason": "lease_held",
                    "path": str(lease_path),
                    "owner_id": existing_owner,
                    "expires_at": str(existing.get("expires_at") or ""),
                    "seconds_remaining": max(existing_expires - now_ts, 0),
                    "active_lease": existing,
                }
            try:
                lease_path.unlink()
            except OSError:
                return {
                    "acquired": False,
                    "reason": "lease_stale_reclaim_failed",
                    "path": str(lease_path),
                    "owner_id": existing_owner,
                    "expires_at": str(existing.get("expires_at") or ""),
                    "active_lease": existing,
                }
            if attempt == 0:
                continue
        except OSError as exc:
            return {
                "acquired": False,
                "reason": "lease_io_error",
                "path": str(lease_path),
                "detail": str(exc),
            }
    return {
        "acquired": False,
        "reason": "lease_contended",
        "path": str(lease_path),
    }


def _release_maintenance_lease(*, lease_path: Path, owner_id: str) -> dict[str, Any]:
    if not lease_path.exists():
        return {"released": False, "reason": "lease_missing", "path": str(lease_path)}
    existing = dict(_json_load(lease_path, {}))
    existing_owner = str(existing.get("owner_id") or "")
    if existing_owner and existing_owner != str(owner_id or ""):
        return {
            "released": False,
            "reason": "lease_owner_mismatch",
            "path": str(lease_path),
            "owner_id": existing_owner,
        }
    try:
        lease_path.unlink()
        return {"released": True, "reason": "released", "path": str(lease_path)}
    except OSError as exc:
        return {"released": False, "reason": "lease_release_failed", "path": str(lease_path), "detail": str(exc)}


def _build_cache_hotspot_report(
    *,
    persistent_rollup: dict[str, Any],
) -> dict[str, Any]:
    totals = dict(persistent_rollup.get("totals") or {})
    by_type = dict(persistent_rollup.get("by_insight_type") or {})
    by_context = dict(persistent_rollup.get("by_context") or {})
    invalidations_by_reason = dict(persistent_rollup.get("invalidations_by_reason") or {})
    contextual = dict(persistent_rollup.get("contextual") or {})
    contextual_by_type = dict(contextual.get("opportunities_by_type") or {})
    contextual_by_context = dict(contextual.get("opportunities_by_context") or {})
    contextual_views_by_context = dict(contextual.get("views_by_context") or {})

    miss_rate_by_type: list[dict[str, Any]] = []
    for insight_type, counters in by_type.items():
        bucket = dict(counters or {})
        hits = int(bucket.get("hits") or 0)
        misses = int(bucket.get("misses") or 0)
        invalidations = int(bucket.get("invalidations") or 0)
        rebuilds = int(bucket.get("rebuilds") or 0)
        sample_size = hits + misses
        miss_rate_by_type.append(
            {
                "insight_type": str(insight_type or ""),
                "hits": hits,
                "misses": misses,
                "invalidations": invalidations,
                "rebuilds": rebuilds,
                "sample_size": sample_size,
                "miss_rate": _safe_rate(misses, sample_size),
                "rebuild_rate": _safe_rate(rebuilds, sample_size),
            }
        )
    miss_rate_by_type.sort(
        key=lambda item: (
            float(item.get("miss_rate") or 0.0),
            int(item.get("sample_size") or 0),
        ),
        reverse=True,
    )

    rebuild_rate_by_context: list[dict[str, Any]] = []
    fallback_heavy_contexts: list[dict[str, Any]] = []
    for context_tag, counters in by_context.items():
        bucket = dict(counters or {})
        hits = int(bucket.get("hits") or 0)
        misses = int(bucket.get("misses") or 0)
        rebuilds = int(bucket.get("rebuilds") or 0)
        invalidations = int(bucket.get("invalidations") or 0)
        sample_size = hits + misses
        miss_rate = _safe_rate(misses, sample_size)
        rebuild_rate = _safe_rate(rebuilds, sample_size)
        contextual_views = int(contextual_views_by_context.get(str(context_tag or "")) or 0)
        contextual_opportunities = int(contextual_by_context.get(str(context_tag or "")) or 0)
        payload = {
            "context_tag": str(context_tag or ""),
            "hits": hits,
            "misses": misses,
            "rebuilds": rebuilds,
            "invalidations": invalidations,
            "sample_size": sample_size,
            "miss_rate": miss_rate,
            "rebuild_rate": rebuild_rate,
            "contextual_views": contextual_views,
            "contextual_opportunities": contextual_opportunities,
            "contextual_coverage_rate": _safe_rate(contextual_opportunities, contextual_views),
        }
        rebuild_rate_by_context.append(payload)
        if sample_size >= 8 and (miss_rate >= 0.45 or rebuild_rate >= 0.30):
            fallback_heavy_contexts.append(
                {
                    **payload,
                    "hotspot_reason": "high_miss_or_rebuild_rate",
                }
            )
    rebuild_rate_by_context.sort(
        key=lambda item: (
            float(item.get("rebuild_rate") or 0.0),
            int(item.get("sample_size") or 0),
        ),
        reverse=True,
    )
    fallback_heavy_contexts.sort(
        key=lambda item: (
            float(item.get("miss_rate") or 0.0),
            float(item.get("rebuild_rate") or 0.0),
            int(item.get("sample_size") or 0),
        ),
        reverse=True,
    )

    frequent_invalidations = [
        {"reason": str(reason or ""), "count": int(count or 0)}
        for reason, count in invalidations_by_reason.items()
    ]
    frequent_invalidations.sort(key=lambda item: int(item.get("count") or 0), reverse=True)

    total_contextual_views = int(sum(int(value or 0) for value in contextual_views_by_context.values()))
    contextual_underused_types = []
    for opportunity_type, count in contextual_by_type.items():
        emitted = int(count or 0)
        coverage = _safe_rate(emitted, total_contextual_views)
        if total_contextual_views >= 10 and coverage <= 0.10:
            contextual_underused_types.append(
                {
                    "opportunity_type": str(opportunity_type or ""),
                    "emitted": emitted,
                    "coverage_rate": coverage,
                    "contextual_views": total_contextual_views,
                    "hotspot_reason": "low_contextual_coverage",
                }
            )
    contextual_underused_types.sort(key=lambda item: float(item.get("coverage_rate") or 0.0))

    backfill_priorities: list[dict[str, Any]] = []
    for item in miss_rate_by_type:
        sample_size = int(item.get("sample_size") or 0)
        misses = int(item.get("misses") or 0)
        miss_rate = float(item.get("miss_rate") or 0.0)
        if sample_size < 8 or misses < 4 or miss_rate < 0.30:
            continue
        backfill_priorities.append(
            {
                "insight_type": str(item.get("insight_type") or ""),
                "priority_reason": "high_cache_miss_rate",
                "miss_rate": miss_rate,
                "sample_size": sample_size,
                "misses": misses,
                "recommended_action": "deterministic_backfill_candidate",
            }
        )
    backfill_priorities = backfill_priorities[:8]
    for index, entry in enumerate(backfill_priorities, start=1):
        entry["priority_rank"] = index

    return {
        "generated_at": _utc_timestamp(),
        "totals": {
            "hits": int(totals.get("hits") or 0),
            "misses": int(totals.get("misses") or 0),
            "invalidations": int(totals.get("invalidations") or 0),
            "rebuilds": int(totals.get("rebuilds") or 0),
            "contextual_views": int(totals.get("contextual_views") or 0),
            "contextual_opportunities": int(totals.get("contextual_opportunities") or 0),
            "overall_hit_rate": _safe_rate(
                int(totals.get("hits") or 0),
                int(totals.get("hits") or 0) + int(totals.get("misses") or 0),
            ),
            "overall_miss_rate": _safe_rate(
                int(totals.get("misses") or 0),
                int(totals.get("hits") or 0) + int(totals.get("misses") or 0),
            ),
        },
        "miss_rate_by_insight_type": miss_rate_by_type[:12],
        "rebuild_rate_by_context": rebuild_rate_by_context[:12],
        "frequent_invalidations": frequent_invalidations[:10],
        "fallback_heavy_contexts": fallback_heavy_contexts[:10],
        "contextual_underused_types": contextual_underused_types[:10],
        "backfill_priorities": backfill_priorities,
    }


def load_cache_effectiveness_report(*, project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    paths = _maintenance_paths(root)
    return dict(_json_load(paths["cache_rollup_state"], {}))


def _context_insight_type_hints(context_tag: str) -> list[str]:
    context = str(context_tag or "").strip().lower()
    if not context:
        return []
    if "dashboard_card_insight" in context or "api_miru_insight" in context:
        return [
            "card_insight",
            CARD_INTELLIGENCE_SUMMARY_TYPE,
            "usage_insight",
            "strategy_insight",
            "meta_insight",
            "verified_loop_card_summary",
        ]
    if "catalog_search" in context or "insights_page" in context:
        return [
            CARD_INTELLIGENCE_SUMMARY_TYPE,
            "usage_insight",
            "strategy_insight",
            "meta_insight",
        ]
    if "watchlist" in context:
        return [
            CARD_INTELLIGENCE_SUMMARY_TYPE,
            "verified_loop_card_summary",
            "meta_insight",
        ]
    if "contextual" in context:
        return [
            "usage_insight",
            "meta_insight",
            "strategy_insight",
        ]
    return []


def _extract_priority_insight_types(hotspot_report: dict[str, Any]) -> list[dict[str, Any]]:
    prioritized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(hotspot_report.get("backfill_priorities") or []):
        insight_type = str(item.get("insight_type") or "").strip()
        if not insight_type or insight_type in seen:
            continue
        if insight_type not in SUPPORTED_QUEUE_INSIGHT_TYPES:
            continue
        seen.add(insight_type)
        prioritized.append(
            {
                "insight_type": insight_type,
                "miss_rate": float(item.get("miss_rate") or 0.0),
                "priority_reason": str(item.get("priority_reason") or "high_cache_miss_rate"),
                "source": "backfill_priorities",
            }
        )
    if len(prioritized) >= 5:
        return prioritized[:5]
    for item in list(hotspot_report.get("miss_rate_by_insight_type") or []):
        insight_type = str(item.get("insight_type") or "").strip()
        if not insight_type or insight_type in seen:
            continue
        if insight_type not in SUPPORTED_QUEUE_INSIGHT_TYPES:
            continue
        sample_size = int(item.get("sample_size") or 0)
        miss_rate = float(item.get("miss_rate") or 0.0)
        if sample_size < 8 or miss_rate < 0.25:
            continue
        seen.add(insight_type)
        prioritized.append(
            {
                "insight_type": insight_type,
                "miss_rate": miss_rate,
                "priority_reason": "high_miss_rate_fallback",
                "source": "miss_rate_by_insight_type",
            }
        )
        if len(prioritized) >= 6:
            break
    return prioritized


def _extract_priority_context_tags(hotspot_report: dict[str, Any]) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(hotspot_report.get("fallback_heavy_contexts") or []):
        context_tag = str(item.get("context_tag") or "").strip()
        if not context_tag or context_tag in seen:
            continue
        seen.add(context_tag)
        contexts.append(
            {
                "context_tag": context_tag,
                "miss_rate": float(item.get("miss_rate") or 0.0),
                "rebuild_rate": float(item.get("rebuild_rate") or 0.0),
                "priority_reason": str(item.get("hotspot_reason") or "high_miss_or_rebuild_rate"),
            }
        )
    if len(contexts) >= 6:
        return contexts[:6]
    for item in list(hotspot_report.get("rebuild_rate_by_context") or []):
        context_tag = str(item.get("context_tag") or "").strip()
        if not context_tag or context_tag in seen:
            continue
        sample_size = int(item.get("sample_size") or 0)
        miss_rate = float(item.get("miss_rate") or 0.0)
        rebuild_rate = float(item.get("rebuild_rate") or 0.0)
        if sample_size < 8:
            continue
        if miss_rate < 0.3 and rebuild_rate < 0.2:
            continue
        seen.add(context_tag)
        contexts.append(
            {
                "context_tag": context_tag,
                "miss_rate": miss_rate,
                "rebuild_rate": rebuild_rate,
                "priority_reason": "high_rebuild_or_miss_fallback",
            }
        )
        if len(contexts) >= 8:
            break
    return contexts


def _load_dossier_backfill_entities(*, dossier_db_path: Path, limit: int) -> list[dict[str, Any]]:
    if not dossier_db_path.is_file():
        return []
    query = (
        "SELECT card_code, canonical_code, card_name, card_type, confidence, verification_state, "
        "verified_at, updated_at "
        "FROM cards "
        "WHERE trim(coalesce(card_code, '')) != '' "
        "ORDER BY confidence DESC, verified_at DESC, updated_at DESC, card_code ASC"
    )
    params: tuple[Any, ...] = ()
    if int(limit or 0) > 0:
        query += " LIMIT ?"
        params = (int(limit),)
    try:
        with closing(sqlite3.connect(dossier_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []
    entities: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row)
        card_code = str(payload.get("card_code") or "").strip().upper()
        if not card_code:
            continue
        confidence = float(payload.get("confidence") or 0.0)
        verification_state = str(payload.get("verification_state") or "").strip().lower()
        if confidence < BACKFILL_MIN_CONFIDENCE:
            continue
        if verification_state in {"", "placeholder", "missing", "unknown", "no_evidence"}:
            continue
        entities.append(
            {
                "entity_id": card_code,
                "card_name": str(payload.get("card_name") or "").strip(),
                "card_type": str(payload.get("card_type") or "").strip().lower(),
                "confidence": round(confidence, 4),
                "verification_state": verification_state,
                "verified_at": str(payload.get("verified_at") or "").strip(),
                "updated_at": str(payload.get("updated_at") or "").strip(),
            }
        )
    return entities


def _fetch_cached_entry_for_insight_type(
    repository: MiruInsightCacheRepository,
    *,
    entity_id: str,
    insight_type: str,
) -> dict[str, Any] | None:
    normalized_entity = str(entity_id or "").strip().upper()
    normalized_type = str(insight_type or "").strip()
    if not normalized_entity or not normalized_type:
        return None
    if normalized_type in {"card_insight", CARD_INTELLIGENCE_SUMMARY_TYPE}:
        return repository.fetch_card_insight(normalized_entity, insight_type=normalized_type)
    if normalized_type in {"usage_insight", "leader_insight"}:
        return repository.fetch_leader_usage(normalized_entity, insight_type=normalized_type)
    if normalized_type == "strategy_insight":
        return repository.fetch_strategy_fragment(normalized_entity, insight_type=normalized_type)
    if normalized_type == "meta_insight":
        return repository.fetch_meta_insight(normalized_entity, insight_type=normalized_type)
    if normalized_type == "verified_loop_card_summary":
        return repository.fetch_surface_response(normalized_entity, insight_type=normalized_type)
    return None


def _build_entity_expected_hashes(
    dossier_store: MiruDossierStore,
    *,
    entity_id: str,
) -> dict[str, str]:
    canonical_code = str(entity_id or "").strip().upper()
    if not canonical_code:
        return {}
    try:
        answer_context = dict(dossier_store.build_answer_context(canonical_code) or {})
        snapshot = dict(answer_context.get("snapshot") or {})
        if not snapshot:
            return {}
        usage_context = dict(dossier_store.build_usage_context(canonical_code) or {})
        intelligence_summary = dict(dossier_store.build_card_intelligence_summary(canonical_code) or {})
        strategy_context = dict(dossier_store.build_strategy_context(canonical_code) or {})
        answer_posture = dict(answer_context.get("answer_posture") or {})
        verified_facts = list(answer_context.get("facts") or [])
        answer_fragments = list(answer_context.get("answer_fragments") or [])
        strategy_posture = dict(strategy_context.get("strategy_posture") or intelligence_summary.get("strategy_posture") or {})
        meta_posture = dict(intelligence_summary.get("meta_posture") or {})
        top_leader = dict(intelligence_summary.get("top_leader") or {})
        summary_truth_context = intelligence_summary or {
            "canonical_code": canonical_code,
            "card_name": str(snapshot.get("card_name") or ""),
            "verification_state": str(snapshot.get("verification_state") or ""),
            "overall_confidence": float(snapshot.get("confidence") or 0.0),
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
            "confidence": float(
                intelligence_summary.get("overall_confidence")
                or snapshot.get("confidence")
                or 0.0
            ),
            "top_leader": top_leader,
            "summary_hint": str(intelligence_summary.get("role_purpose") or ""),
        }
        hashes = {
            "card_insight": build_source_hash(card_truth_context),
            CARD_INTELLIGENCE_SUMMARY_TYPE: build_source_hash(summary_truth_context),
            "usage_insight": build_source_hash(usage_truth_context),
            "strategy_insight": build_source_hash(strategy_truth_context),
            "meta_insight": build_source_hash(meta_truth_context),
            "verified_loop_card_summary": build_source_hash(surface_truth_context),
        }
        if str(snapshot.get("card_type") or "").strip().lower() == "leader":
            leader_payload = dict(dossier_store.build_leader_context(canonical_code) or {})
            hashes["leader_insight"] = build_source_hash(leader_payload)
        return hashes
    except Exception:
        return {}


def _evaluate_backfill_requeue_decision(
    *,
    cached_entry: dict[str, Any] | None,
    expected_source_hash: str,
    insight_miss_rate: float,
    context_miss_rate: float,
    context_rebuild_rate: float,
    invalidation_pressure: bool,
    cooldown_seconds: int,
    last_applied_ts: int,
    now_ts: int,
) -> dict[str, Any]:
    requeue_reason = ""
    cached_hash = str((cached_entry or {}).get("source_hash") or "").strip()
    is_fresh = bool((cached_entry or {}).get("fresh"))
    expected_hash = str(expected_source_hash or "").strip()
    if not cached_entry:
        requeue_reason = "cache_missing"
    elif expected_hash and cached_hash and expected_hash != cached_hash:
        requeue_reason = "source_hash_mismatch"
    elif not is_fresh:
        requeue_reason = "stale_cache_window_exceeded"
    elif bool(invalidation_pressure):
        requeue_reason = "invalidation_event"
    elif (
        float(insight_miss_rate or 0.0) >= STRONG_HOTSPOT_MISS_RATE
        or float(context_miss_rate or 0.0) >= STRONG_HOTSPOT_MISS_RATE
        or float(context_rebuild_rate or 0.0) >= STRONG_CONTEXT_REBUILD_RATE
    ):
        requeue_reason = "strong_hotspot_pressure_after_cooldown"
    else:
        return {
            "eligible": False,
            "requeue_reason": "",
            "skip_reason": "no_grounded_requeue_reason",
            "cooldown_remaining_seconds": 0,
        }

    elapsed = max(int(now_ts - int(last_applied_ts or 0)), 0) if int(last_applied_ts or 0) > 0 else 0
    remaining = (
        max(int(cooldown_seconds or 0) - elapsed, 0)
        if int(last_applied_ts or 0) > 0 and int(cooldown_seconds or 0) > 0
        else 0
    )
    if (
        int(last_applied_ts or 0) > 0
        and int(cooldown_seconds or 0) > 0
        and remaining > 0
        and requeue_reason not in {"source_hash_mismatch", "invalidation_event"}
    ):
        return {
            "eligible": False,
            "requeue_reason": requeue_reason,
            "skip_reason": "cooldown_active",
            "cooldown_remaining_seconds": remaining,
        }

    return {
        "eligible": True,
        "requeue_reason": requeue_reason,
        "skip_reason": "",
        "cooldown_remaining_seconds": remaining,
    }


def _clamp_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(int(minimum), min(int(value), int(maximum)))


def _build_adaptive_cooldown_profile(
    *,
    base_cooldown_seconds: int,
    hotspot_report: dict[str, Any],
    guardrail_state: dict[str, Any],
) -> dict[str, Any]:
    resolved_base = max(int(base_cooldown_seconds or DEFAULT_BACKFILL_COOLDOWN_SECONDS), 60)
    enabled = _env_bool("MIRU_BACKFILL_ADAPTIVE_COOLDOWN_ENABLED", True)
    adaptive_min = _env_int(
        "MIRU_BACKFILL_ADAPTIVE_MIN_SECONDS",
        max(int(resolved_base * 0.7), 5 * 60),
        minimum=60,
    )
    adaptive_max = _env_int(
        "MIRU_BACKFILL_ADAPTIVE_MAX_SECONDS",
        max(int(resolved_base * 2), adaptive_min),
        minimum=adaptive_min,
    )
    cycle = dict(guardrail_state.get("recent_cycle") or {})
    candidate_count = max(int(cycle.get("candidate_count") or 0), 1)
    cooldown_skip_ratio = _safe_rate(int(cycle.get("cooldown_skips") or 0), candidate_count)
    apply_rate = float(cycle.get("apply_rate") or 0.0)

    by_type = list(hotspot_report.get("miss_rate_by_insight_type") or [])
    by_context = list(hotspot_report.get("rebuild_rate_by_context") or [])
    insight_overrides: dict[str, int] = {}
    context_overrides: dict[str, int] = {}
    tuning_reasons: dict[str, str] = {}
    if enabled:
        for item in by_type:
            insight_type = str(item.get("insight_type") or "").strip()
            if not insight_type:
                continue
            sample_size = int(item.get("sample_size") or 0)
            if sample_size < 8:
                continue
            miss_rate = float(item.get("miss_rate") or 0.0)
            rebuild_rate = float(item.get("rebuild_rate") or 0.0)
            factor = 1.0
            reason = "baseline"
            if miss_rate >= 0.75:
                factor = 0.78
                reason = "high_miss_rate_reduce_cooldown"
            elif miss_rate >= 0.55 or rebuild_rate >= 0.35:
                factor = 0.88
                reason = "moderate_hotspot_reduce_cooldown"
            elif miss_rate <= 0.20 and rebuild_rate <= 0.10 and cooldown_skip_ratio >= 0.35:
                factor = 1.1
                reason = "low_pressure_with_skip_churn_increase_cooldown"
            if apply_rate < 0.25 and cooldown_skip_ratio > 0.55 and factor > 1.0:
                factor = min(factor + 0.05, 1.2)
                reason = "low_apply_rate_increase_cooldown"
            tuned = _clamp_int(int(round(resolved_base * factor)), minimum=adaptive_min, maximum=adaptive_max)
            if tuned != resolved_base:
                insight_overrides[insight_type] = tuned
                tuning_reasons[f"type:{insight_type}"] = reason

        for item in by_context:
            context_tag = str(item.get("context_tag") or "").strip()
            if not context_tag:
                continue
            sample_size = int(item.get("sample_size") or 0)
            if sample_size < 8:
                continue
            miss_rate = float(item.get("miss_rate") or 0.0)
            rebuild_rate = float(item.get("rebuild_rate") or 0.0)
            factor = 1.0
            reason = "baseline"
            if miss_rate >= 0.70 or rebuild_rate >= 0.45:
                factor = 0.85
                reason = "high_context_pressure_reduce_cooldown"
            elif miss_rate <= 0.25 and rebuild_rate <= 0.15 and cooldown_skip_ratio >= 0.35:
                factor = 1.08
                reason = "low_context_pressure_increase_cooldown"
            tuned = _clamp_int(int(round(resolved_base * factor)), minimum=adaptive_min, maximum=adaptive_max)
            if tuned != resolved_base:
                context_overrides[context_tag] = tuned
                tuning_reasons[f"context:{context_tag}"] = reason
            if len(context_overrides) >= 16:
                break

    return {
        "enabled": bool(enabled),
        "base_cooldown_seconds": int(resolved_base),
        "min_cooldown_seconds": int(adaptive_min),
        "max_cooldown_seconds": int(adaptive_max),
        "insight_type_overrides": dict(sorted(insight_overrides.items())),
        "context_overrides": dict(sorted(context_overrides.items())),
        "signals": {
            "cooldown_skip_ratio": float(cooldown_skip_ratio),
            "recent_apply_rate": float(apply_rate),
            "candidate_count": int(cycle.get("candidate_count") or 0),
            "cooldown_skips": int(cycle.get("cooldown_skips") or 0),
        },
        "tuning_reasons": tuning_reasons,
    }


def _resolve_effective_cooldown_seconds(
    *,
    base_cooldown_seconds: int,
    adaptive_profile: dict[str, Any],
    insight_type: str,
    context_tag: str,
) -> int:
    base = int(base_cooldown_seconds or 0)
    if base <= 0:
        return max(DEFAULT_BACKFILL_COOLDOWN_SECONDS, 60)
    if not bool(adaptive_profile.get("enabled")):
        return int(base)
    min_seconds = int(adaptive_profile.get("min_cooldown_seconds") or max(int(base * 0.7), 60))
    max_seconds = int(adaptive_profile.get("max_cooldown_seconds") or max(base, min_seconds))
    by_type = dict(adaptive_profile.get("insight_type_overrides") or {})
    by_context = dict(adaptive_profile.get("context_overrides") or {})
    candidate = int(base)
    type_override = int(by_type.get(str(insight_type or "").strip()) or 0)
    context_override = int(by_context.get(str(context_tag or "").strip()) or 0)
    if type_override > 0:
        candidate = int(round((candidate + type_override) / 2.0))
    if context_override > 0:
        candidate = int(round((candidate + context_override) / 2.0))
    return _clamp_int(candidate, minimum=min_seconds, maximum=max_seconds)


def build_entity_backfill_plan(
    *,
    project_root: Path | None = None,
    hotspot_report: dict[str, Any],
    max_candidates: int = DEFAULT_BACKFILL_PLAN_LIMIT,
    guardrail_state: dict[str, Any] | None = None,
    cooldown_seconds: int | None = None,
) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    layout = build_storage_layout(project_root=root)
    runtime_paths = layout.current_runtime_paths()
    dossier_db_path = Path(runtime_paths["verified_dossier_db"])
    insight_cache_db_path = str(runtime_paths["insight_cache_db"])
    resolved_cooldown_seconds = _env_int(
        "MIRU_BACKFILL_COOLDOWN_SECONDS",
        int(cooldown_seconds or DEFAULT_BACKFILL_COOLDOWN_SECONDS),
        minimum=60,
    )
    normalized_guardrails = _normalize_backfill_guardrail_state(guardrail_state)
    last_applied = dict(normalized_guardrails.get("entity_insight_last_applied_at") or {})
    adaptive_cooldown = _build_adaptive_cooldown_profile(
        base_cooldown_seconds=resolved_cooldown_seconds,
        hotspot_report=hotspot_report,
        guardrail_state=normalized_guardrails,
    )
    insight_types = _extract_priority_insight_types(hotspot_report)
    contexts = _extract_priority_context_tags(hotspot_report)
    entities = _load_dossier_backfill_entities(
        dossier_db_path=dossier_db_path,
        limit=max(int(max_candidates or DEFAULT_BACKFILL_PLAN_LIMIT), 20),
    )
    baseline_skip_reasons: list[dict[str, Any]] = []
    if not insight_types or not entities:
        if not insight_types:
            baseline_skip_reasons.append({"reason": "no_priority_insight_types", "count": 1})
        if not entities:
            baseline_skip_reasons.append({"reason": "no_verified_entities", "count": 1})
        return {
            "generated_at": _utc_timestamp(),
            "mode": "plan",
            "ok": True,
            "dossier_db_path": str(dossier_db_path),
            "insight_cache_db_path": insight_cache_db_path,
            "candidate_count": 0,
            "applied_count": 0,
            "entries": [],
            "summary": {
                "by_insight_type": {},
                "by_context": {},
            },
            "guardrails": {
                "cooldown_seconds": resolved_cooldown_seconds,
                "adaptive_cooldown": adaptive_cooldown,
                "skip_reasons": baseline_skip_reasons,
                "requeue_reasons": [],
                "cooldown_skips": 0,
                "sample_skips": [],
            },
            "skipped_reasons": baseline_skip_reasons,
        }

    now_ts = int(time.time())
    repository = MiruInsightCacheRepository(insight_cache_db_path)
    dossier_store = MiruDossierStore(dossier_db_path)
    has_invalidation_pressure = bool(list(hotspot_report.get("frequent_invalidations") or []))
    entries: list[dict[str, Any]] = []
    skip_reason_counts: dict[str, int] = {}
    requeue_reason_counts: dict[str, int] = {}
    sample_skips: list[dict[str, Any]] = []
    entry_cache: dict[str, dict[str, Any] | None] = {}
    for entity in entities:
        entity_id = str(entity.get("entity_id") or "").strip().upper()
        card_type = str(entity.get("card_type") or "").strip().lower()
        confidence = float(entity.get("confidence") or 0.0)
        verification_state = str(entity.get("verification_state") or "")
        expected_hashes = _build_entity_expected_hashes(
            dossier_store,
            entity_id=entity_id,
        )
        for insight in insight_types:
            insight_type = str(insight.get("insight_type") or "").strip()
            if not insight_type:
                continue
            if insight_type == "leader_insight" and card_type != "leader":
                continue
            context_candidates = [
                item for item in contexts if insight_type in _context_insight_type_hints(str(item.get("context_tag") or ""))
            ]
            if not context_candidates:
                context_candidates = contexts[:1]
            if not context_candidates:
                context_candidates = [
                    {
                        "context_tag": "unspecified",
                        "miss_rate": 0.0,
                        "rebuild_rate": 0.0,
                        "priority_reason": "insight_priority_only",
                    }
                ]
            context_candidates.sort(
                key=lambda item: (
                    float(item.get("miss_rate") or 0.0),
                    float(item.get("rebuild_rate") or 0.0),
                ),
                reverse=True,
            )
            context_item = dict(context_candidates[0] or {})
            context_tag = str(context_item.get("context_tag") or "").strip() or "unspecified"
            miss_rate = float(insight.get("miss_rate") or 0.0)
            context_miss_rate = float(context_item.get("miss_rate") or 0.0)
            context_rebuild_rate = float(context_item.get("rebuild_rate") or 0.0)
            priority_score = round(
                (miss_rate * 0.45)
                + (context_miss_rate * 0.25)
                + (context_rebuild_rate * 0.15)
                + (confidence * 0.15),
                4,
            )
            effective_cooldown_seconds = _resolve_effective_cooldown_seconds(
                base_cooldown_seconds=resolved_cooldown_seconds,
                adaptive_profile=adaptive_cooldown,
                insight_type=insight_type,
                context_tag=context_tag,
            )
            lookup_key = _backfill_guardrail_key(entity_id, insight_type)
            if lookup_key in entry_cache:
                cached_entry = entry_cache[lookup_key]
            else:
                cached_entry = _fetch_cached_entry_for_insight_type(
                    repository,
                    entity_id=entity_id,
                    insight_type=insight_type,
                )
                entry_cache[lookup_key] = cached_entry
            decision = _evaluate_backfill_requeue_decision(
                cached_entry=cached_entry,
                expected_source_hash=str(expected_hashes.get(insight_type) or ""),
                insight_miss_rate=miss_rate,
                context_miss_rate=context_miss_rate,
                context_rebuild_rate=context_rebuild_rate,
                invalidation_pressure=has_invalidation_pressure,
                cooldown_seconds=effective_cooldown_seconds,
                last_applied_ts=_parse_timestamp(str(last_applied.get(lookup_key) or "")),
                now_ts=now_ts,
            )
            if not bool(decision.get("eligible")):
                skip_reason = str(decision.get("skip_reason") or "no_grounded_requeue_reason")
                _increment_counter(skip_reason_counts, skip_reason)
                if len(sample_skips) < 120:
                    sample_skips.append(
                        {
                            "entity_id": entity_id,
                            "insight_type": insight_type,
                            "context_tag": context_tag,
                            "skip_reason": skip_reason,
                            "deferred_requeue_reason": str(decision.get("requeue_reason") or ""),
                            "cooldown_remaining_seconds": int(decision.get("cooldown_remaining_seconds") or 0),
                            "effective_cooldown_seconds": int(effective_cooldown_seconds),
                        }
                    )
                continue
            requeue_reason = str(decision.get("requeue_reason") or "grounded")
            _increment_counter(requeue_reason_counts, requeue_reason)
            entries.append(
                {
                    "entity_id": entity_id,
                    "entity_kind": "card",
                    "card_name": str(entity.get("card_name") or ""),
                    "card_type": card_type,
                    "verification_state": verification_state,
                    "confidence": confidence,
                    "insight_type": insight_type,
                    "context_tag": context_tag,
                    "priority_score": priority_score,
                    "priority_reason": [
                        str(insight.get("priority_reason") or "insight_hotspot"),
                        str(context_item.get("priority_reason") or "context_hotspot"),
                        "verified_dossier_backing",
                    ],
                    "hotspot_signals": {
                        "insight_miss_rate": miss_rate,
                        "context_miss_rate": context_miss_rate,
                        "context_rebuild_rate": context_rebuild_rate,
                    },
                    "effective_cooldown_seconds": int(effective_cooldown_seconds),
                    "requeue_reason": requeue_reason,
                    "status": "planned",
                    "apply_mode": "deterministic_local_only",
                }
            )
    entries.sort(
        key=lambda item: (
            float(item.get("priority_score") or 0.0),
            float(item.get("confidence") or 0.0),
        ),
        reverse=True,
    )
    limited = entries[: max(int(max_candidates or DEFAULT_BACKFILL_PLAN_LIMIT), 1)]
    if len(entries) > len(limited):
        _increment_counter(skip_reason_counts, "plan_limit_deferred", len(entries) - len(limited))

    by_insight_type: dict[str, int] = {}
    by_context: dict[str, int] = {}
    for item in limited:
        insight_type = str(item.get("insight_type") or "")
        context_tag = str(item.get("context_tag") or "")
        by_insight_type[insight_type] = int(by_insight_type.get(insight_type) or 0) + 1
        by_context[context_tag] = int(by_context.get(context_tag) or 0) + 1
    skipped_reasons = [{"reason": key, "count": int(value)} for key, value in sorted(skip_reason_counts.items())]
    requeue_reasons = [{"reason": key, "count": int(value)} for key, value in sorted(requeue_reason_counts.items())]

    return {
        "generated_at": _utc_timestamp(),
        "mode": "plan",
        "ok": True,
        "dossier_db_path": str(dossier_db_path),
        "insight_cache_db_path": insight_cache_db_path,
        "candidate_count": len(limited),
        "applied_count": 0,
        "entries": limited,
        "summary": {
            "by_insight_type": dict(sorted(by_insight_type.items())),
            "by_context": dict(sorted(by_context.items())),
            "priority_insight_types": insight_types,
            "priority_contexts": contexts,
        },
        "guardrails": {
            "cooldown_seconds": resolved_cooldown_seconds,
            "adaptive_cooldown": adaptive_cooldown,
            "skip_reasons": skipped_reasons,
            "requeue_reasons": requeue_reasons,
            "cooldown_skips": int(skip_reason_counts.get("cooldown_active") or 0),
            "sample_skips": sample_skips,
        },
        "skipped_reasons": skipped_reasons,
    }


def apply_entity_backfill_plan(
    *,
    project_root: Path | None = None,
    plan_payload: dict[str, Any],
    apply_limit: int = DEFAULT_BACKFILL_APPLY_LIMIT,
    guardrail_state: dict[str, Any] | None = None,
    cooldown_seconds: int | None = None,
    apply_time_budget_seconds: int | None = None,
) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    layout = build_storage_layout(project_root=root)
    runtime_paths = layout.current_runtime_paths()
    dossier_db_path = Path(runtime_paths["verified_dossier_db"])
    insight_cache_db_path = str(runtime_paths["insight_cache_db"])
    max_apply = max(int(apply_limit or DEFAULT_BACKFILL_APPLY_LIMIT), 1)
    resolved_cooldown_seconds = _env_int(
        "MIRU_BACKFILL_COOLDOWN_SECONDS",
        int(cooldown_seconds or DEFAULT_BACKFILL_COOLDOWN_SECONDS),
        minimum=60,
    )
    resolved_budget_seconds = _env_int(
        "MIRU_BACKFILL_APPLY_TIME_BUDGET_SECONDS",
        int(apply_time_budget_seconds or DEFAULT_BACKFILL_APPLY_TIME_BUDGET_SECONDS),
        minimum=1,
    )
    normalized_guardrails = _normalize_backfill_guardrail_state(guardrail_state)
    last_applied = dict(normalized_guardrails.get("entity_insight_last_applied_at") or {})
    raw_entries = [dict(item) for item in list(plan_payload.get("entries") or []) if isinstance(item, dict)]
    if not raw_entries:
        return {
            "generated_at": _utc_timestamp(),
            "mode": "apply",
            "ok": True,
            "applied_count": 0,
            "planned_count": 0,
            "by_insight_type": {},
            "by_context": {},
            "skipped": [{"reason": "no_plan_entries"}],
            "results": [],
            "skip_reasons": {"no_plan_entries": 1},
            "requeue_reasons_applied": {},
            "cooldown_skipped_count": 0,
            "time_budget_exhausted": False,
            "deferred_due_to_budget": 0,
            "apply_time_budget_seconds": resolved_budget_seconds,
            "duration_seconds": 0.0,
        }

    started_at = time.monotonic()
    selected_entries = raw_entries[:max_apply]
    grouped: dict[str, dict[str, Any]] = {}
    for entry in selected_entries:
        entity_id = str(entry.get("entity_id") or "").strip().upper()
        insight_type = str(entry.get("insight_type") or "").strip()
        if not entity_id or not insight_type:
            continue
        bucket = grouped.get(entity_id)
        if bucket is None:
            bucket = {
                "entity_id": entity_id,
                "insight_types": set(),
                "contexts": set(),
                "entries": [],
                "requeue_reason_by_type": {},
                "cooldown_by_type": {},
            }
            grouped[entity_id] = bucket
        bucket["insight_types"].add(insight_type)
        bucket["contexts"].add(str(entry.get("context_tag") or "").strip() or "unspecified")
        bucket["entries"].append(entry)
        reason = str(entry.get("requeue_reason") or "").strip()
        if reason:
            bucket["requeue_reason_by_type"][insight_type] = reason
        entry_cooldown = max(
            int(entry.get("effective_cooldown_seconds") or resolved_cooldown_seconds),
            60,
        )
        existing_cooldown = int((bucket.get("cooldown_by_type") or {}).get(insight_type) or 0)
        if existing_cooldown <= 0 or entry_cooldown > existing_cooldown:
            bucket["cooldown_by_type"][insight_type] = entry_cooldown

    repository = MiruInsightCacheRepository(insight_cache_db_path)
    dossier_store = MiruDossierStore(dossier_db_path)
    results: list[dict[str, Any]] = []
    applied_by_type: dict[str, int] = {}
    applied_by_context: dict[str, int] = {}
    requeue_reasons_applied: dict[str, int] = {}
    skipped: list[dict[str, Any]] = []
    skip_reason_counts: dict[str, int] = {}
    applied_count = 0
    cooldown_skipped_count = 0
    deferred_due_to_budget = 0
    time_budget_exhausted = False
    for entity_id, payload in grouped.items():
        if (time.monotonic() - started_at) >= float(resolved_budget_seconds):
            time_budget_exhausted = True
            deferred_due_to_budget += len(list(payload.get("entries") or []))
            _increment_counter(skip_reason_counts, "apply_time_budget_exceeded")
            continue

        requested_types = sorted(normalize_backfill_insight_types(list(payload["insight_types"])))
        allowed_types: list[str] = []
        skipped_types: list[dict[str, Any]] = []
        for insight_type in requested_types:
            lookup_key = _backfill_guardrail_key(entity_id, insight_type)
            last_applied_ts = _parse_timestamp(str(last_applied.get(lookup_key) or ""))
            effective_cooldown = int(
                (payload.get("cooldown_by_type") or {}).get(insight_type) or resolved_cooldown_seconds
            )
            elapsed = max(int(time.time() - last_applied_ts), 0) if last_applied_ts > 0 else 0
            remaining = (
                max(int(effective_cooldown) - elapsed, 0)
                if last_applied_ts > 0 and int(effective_cooldown) > 0
                else 0
            )
            requeue_reason = str((payload.get("requeue_reason_by_type") or {}).get(insight_type) or "")
            bypass = requeue_reason in {"source_hash_mismatch", "invalidation_event"}
            if last_applied_ts > 0 and remaining > 0 and not bypass:
                cooldown_skipped_count += 1
                _increment_counter(skip_reason_counts, "cooldown_active")
                skipped_types.append(
                    {
                        "insight_type": insight_type,
                        "reason": "cooldown_active",
                        "cooldown_remaining_seconds": remaining,
                        "effective_cooldown_seconds": effective_cooldown,
                    }
                )
                continue
            allowed_types.append(insight_type)
        if not allowed_types:
            skipped.append(
                {
                    "entity_id": entity_id,
                    "reason": "cooldown_active",
                    "insight_types": [str(item.get("insight_type") or "") for item in skipped_types],
                }
            )
            continue
        try:
            outcome = repository.cache_card_from_dossier_store(
                dossier_store,
                card_code=entity_id,
                run_id=f"{BACKFILL_RUN_ID_PREFIX}-apply",
                selected_insight_types=list(allowed_types),
            )
        except Exception as exc:
            skipped.append({"entity_id": entity_id, "reason": "cache_apply_error", "detail": str(exc)})
            _increment_counter(skip_reason_counts, "cache_apply_error")
            continue
        written_entries = [str(item or "").strip() for item in list(outcome.get("entries") or []) if str(item or "").strip()]
        if not outcome.get("cached") or not written_entries:
            reason = str(outcome.get("reason") or "not_cached")
            skipped.append({"entity_id": entity_id, "reason": reason})
            _increment_counter(skip_reason_counts, reason)
            continue
        applied_count += 1
        for insight_type in written_entries:
            applied_by_type[insight_type] = int(applied_by_type.get(insight_type) or 0) + 1
            requeue_reason = str((payload.get("requeue_reason_by_type") or {}).get(insight_type) or "")
            if requeue_reason:
                _increment_counter(requeue_reasons_applied, requeue_reason)
        for context_tag in list(payload.get("contexts") or []):
            applied_by_context[str(context_tag or "")] = int(applied_by_context.get(str(context_tag or "")) or 0) + 1
        results.append(
            {
                "entity_id": entity_id,
                "requested_insight_types": sorted(requested_types),
                "applied_insight_types": sorted(allowed_types),
                "written_insight_types": sorted(written_entries),
                "contexts": sorted(list(payload.get("contexts") or [])),
                "requeue_reason_by_type": dict(payload.get("requeue_reason_by_type") or {}),
                "cooldown_by_type": dict(payload.get("cooldown_by_type") or {}),
                "applied_at": _utc_timestamp(),
                "ok": True,
            }
        )
        if skipped_types:
            skipped.append(
                {
                    "entity_id": entity_id,
                    "reason": "partial_cooldown_skip",
                    "details": skipped_types,
                }
            )

    duration_seconds = round(max(time.monotonic() - started_at, 0.0), 4)
    return {
        "generated_at": _utc_timestamp(),
        "mode": "apply",
        "ok": True,
        "planned_count": len(selected_entries),
        "applied_count": applied_count,
        "by_insight_type": dict(sorted(applied_by_type.items())),
        "by_context": dict(sorted(applied_by_context.items())),
        "skipped": skipped,
        "results": results,
        "skip_reasons": dict(sorted(skip_reason_counts.items())),
        "requeue_reasons_applied": dict(sorted(requeue_reasons_applied.items())),
        "cooldown_skipped_count": cooldown_skipped_count,
        "time_budget_exhausted": time_budget_exhausted,
        "deferred_due_to_budget": deferred_due_to_budget,
        "apply_time_budget_seconds": resolved_budget_seconds,
        "duration_seconds": duration_seconds,
        "cooldown_seconds": resolved_cooldown_seconds,
        "dossier_db_path": str(dossier_db_path),
        "insight_cache_db_path": insight_cache_db_path,
    }


def _update_backfill_guardrail_state(
    *,
    previous_state: dict[str, Any] | None,
    plan_payload: dict[str, Any],
    apply_payload: dict[str, Any],
) -> dict[str, Any]:
    state = _normalize_backfill_guardrail_state(previous_state)
    counters = dict(state.get("counters") or {})
    last_applied = dict(state.get("entity_insight_last_applied_at") or {})
    last_skip = dict(state.get("entity_insight_last_skip") or {})
    generated_at = str(apply_payload.get("generated_at") or plan_payload.get("generated_at") or _utc_timestamp())

    plan_guardrails = dict(plan_payload.get("guardrails") or {})
    adaptive_cooldown = dict(plan_guardrails.get("adaptive_cooldown") or {})
    plan_requeue_reasons = {
        str(item.get("reason") or ""): int(item.get("count") or 0)
        for item in list(plan_guardrails.get("requeue_reasons") or [])
        if str(item.get("reason") or "").strip()
    }
    plan_skip_reasons = {
        str(item.get("reason") or ""): int(item.get("count") or 0)
        for item in list(plan_guardrails.get("skip_reasons") or [])
        if str(item.get("reason") or "").strip()
    }
    _increment_counter(counters, "planned_candidates", int(plan_payload.get("candidate_count") or 0))
    _increment_counter(counters, "cooldown_skips", int(plan_skip_reasons.get("cooldown_active") or 0))
    _increment_counter(counters, "no_grounded_reason_skips", int(plan_skip_reasons.get("no_grounded_requeue_reason") or 0))
    _increment_counter(counters, "cache_missing_requeues", int(plan_requeue_reasons.get("cache_missing") or 0))
    _increment_counter(counters, "stale_requeues", int(plan_requeue_reasons.get("stale_cache_window_exceeded") or 0))
    _increment_counter(counters, "source_hash_mismatch_requeues", int(plan_requeue_reasons.get("source_hash_mismatch") or 0))
    _increment_counter(counters, "invalidation_requeues", int(plan_requeue_reasons.get("invalidation_event") or 0))
    _increment_counter(
        counters,
        "hotspot_requeues",
        int(plan_requeue_reasons.get("strong_hotspot_pressure_after_cooldown") or 0),
    )

    for item in list(plan_guardrails.get("sample_skips") or []):
        payload = dict(item or {})
        entity_id = str(payload.get("entity_id") or "").strip().upper()
        insight_type = str(payload.get("insight_type") or "").strip()
        reason = str(payload.get("skip_reason") or "").strip()
        if not entity_id or not insight_type or not reason:
            continue
        last_skip[_backfill_guardrail_key(entity_id, insight_type)] = {
            "at": generated_at,
            "reason": reason,
            "deferred_requeue_reason": str(payload.get("deferred_requeue_reason") or ""),
            "cooldown_remaining_seconds": int(payload.get("cooldown_remaining_seconds") or 0),
        }

    applied_count = int(apply_payload.get("applied_count") or 0)
    _increment_counter(counters, "applied_count", applied_count)
    _increment_counter(counters, "apply_time_budget_deferrals", int(apply_payload.get("deferred_due_to_budget") or 0))

    for result in list(apply_payload.get("results") or []):
        payload = dict(result or {})
        entity_id = str(payload.get("entity_id") or "").strip().upper()
        applied_at = str(payload.get("applied_at") or generated_at)
        for insight_type in list(payload.get("written_insight_types") or []):
            normalized_type = str(insight_type or "").strip()
            if not entity_id or not normalized_type:
                continue
            key = _backfill_guardrail_key(entity_id, normalized_type)
            last_applied[key] = applied_at
            if key in last_skip:
                last_skip.pop(key, None)

    for item in list(apply_payload.get("skipped") or []):
        payload = dict(item or {})
        entity_id = str(payload.get("entity_id") or "").strip().upper()
        reason = str(payload.get("reason") or "").strip()
        if not entity_id or not reason:
            continue
        types = [str(value or "").strip() for value in list(payload.get("insight_types") or []) if str(value or "").strip()]
        details = list(payload.get("details") or [])
        if reason == "partial_cooldown_skip" and details:
            for detail in details:
                bucket = dict(detail or {})
                insight_type = str(bucket.get("insight_type") or "").strip()
                if not insight_type:
                    continue
                last_skip[_backfill_guardrail_key(entity_id, insight_type)] = {
                    "at": generated_at,
                    "reason": str(bucket.get("reason") or reason),
                    "cooldown_remaining_seconds": int(bucket.get("cooldown_remaining_seconds") or 0),
                }
            continue
        for insight_type in types:
            last_skip[_backfill_guardrail_key(entity_id, insight_type)] = {
                "at": generated_at,
                "reason": reason,
            }

    state["entity_insight_last_applied_at"] = last_applied
    state["entity_insight_last_skip"] = last_skip
    state["counters"] = counters
    state["recent_cycle"] = {
        "generated_at": generated_at,
        "candidate_count": int(plan_payload.get("candidate_count") or 0),
        "planned_count": int(apply_payload.get("planned_count") or 0),
        "applied_count": applied_count,
        "cooldown_skips": int(plan_skip_reasons.get("cooldown_active") or 0)
        + int(apply_payload.get("cooldown_skipped_count") or 0),
        "skip_reasons": {
            **plan_skip_reasons,
            **dict(apply_payload.get("skip_reasons") or {}),
        },
        "requeue_reasons": {
            **plan_requeue_reasons,
            **dict(apply_payload.get("requeue_reasons_applied") or {}),
        },
        "apply_time_budget_seconds": int(apply_payload.get("apply_time_budget_seconds") or 0),
        "time_budget_exhausted": bool(apply_payload.get("time_budget_exhausted")),
        "deferred_due_to_budget": int(apply_payload.get("deferred_due_to_budget") or 0),
        "duration_seconds": float(apply_payload.get("duration_seconds") or 0.0),
        "apply_rate": _safe_rate(
            applied_count,
            int(apply_payload.get("planned_count") or 0),
        ),
        "adaptive_cooldown_enabled": bool(adaptive_cooldown.get("enabled")),
        "adaptive_base_cooldown_seconds": int(adaptive_cooldown.get("base_cooldown_seconds") or 0),
        "adaptive_insight_override_count": len(dict(adaptive_cooldown.get("insight_type_overrides") or {})),
        "adaptive_context_override_count": len(dict(adaptive_cooldown.get("context_overrides") or {})),
    }
    state["updated_at"] = generated_at
    return state


def load_backfill_queue_report(*, project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    paths = _maintenance_paths(root)
    plan = dict(_json_load(paths["backfill_plan"], {}))
    last_apply = dict(_json_load(paths["backfill_last_apply"], {}))
    guardrails = _load_backfill_guardrail_state(paths["backfill_guardrails"])
    lease = _load_maintenance_lease_snapshot(paths["lease"])
    maintenance_state = dict(_json_load(paths["state"], {}))
    backfill_state = dict(maintenance_state.get("deterministic_backfill") or {})
    return {
        "generated_at": _utc_timestamp(),
        "plan_path": str(paths["backfill_plan"]),
        "last_apply_path": str(paths["backfill_last_apply"]),
        "history_path": str(paths["backfill_history"]),
        "guardrails_path": str(paths["backfill_guardrails"]),
        "lease_path": str(paths["lease"]),
        "plan": plan,
        "last_apply": last_apply,
        "guardrails": guardrails,
        "lease": lease,
        "latest_runtime_summary": {
            "plan_summary": dict(backfill_state.get("plan_summary") or {}),
            "apply_summary": dict(backfill_state.get("apply_summary") or {}),
            "guardrail_summary": dict(backfill_state.get("guardrail_summary") or {}),
            "lease_summary": dict(backfill_state.get("lease_summary") or {}),
        },
    }


def collect_maintenance_metrics(*, project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    layout = build_storage_layout(project_root=root)
    manifest = miru_brain_manifest(project_root=root, snapshot_type="maintenance_probe", reason="metrics_probe")
    report = layout.to_report()
    current = report["current_runtime_paths"]
    core_dbs = dict(manifest.get("core_dbs") or {})
    image_dirs = dict(manifest.get("image_dirs") or {})
    dossier_status = inspect_miru_dossier_store(Path(current["verified_dossier_db"]))
    try:
        learning_status = load_learning_engine_status(
            queue_db_path=Path(current["learning_queue_db"]),
            status_db_path=Path(current["learning_status_db"]),
            dossier_db_path=Path(current["learning_dossier_db"]),
            lock_file_path=Path(current["learning_lock_file"]),
        )
    except Exception as exc:
        learning_status = {
            "status_db_exists": False,
            "schema_version": "",
            "processed_count": 0,
            "validated_card_count": 0,
            "queue_length": 0,
            "worker_status": {"status": "unknown", "detail": str(exc)},
        }
    total_core_db_bytes = sum(int(entry.get("size_bytes") or 0) for entry in core_dbs.values())
    return {
        "generated_at": _utc_timestamp(),
        "project_root": str(root),
        "dossier_cards": int(dossier_status.get("dossiers_created") or 0),
        "verified_dossiers": int(dossier_status.get("verified_dossiers") or 0),
        "variant_records": int(dossier_status.get("variant_records") or 0),
        "insight_cache_bytes": int((core_dbs.get("insight_cache_db") or {}).get("size_bytes") or 0),
        "verified_dossier_bytes": int((core_dbs.get("verified_dossier_db") or {}).get("size_bytes") or 0),
        "catalog_db_bytes": int((core_dbs.get("catalog_db") or {}).get("size_bytes") or 0),
        "total_core_db_bytes": total_core_db_bytes,
        "thumbnail_files": int((image_dirs.get("served_thumbnail_root") or {}).get("file_count") or 0),
        "thumbnail_bytes": int((image_dirs.get("served_thumbnail_root") or {}).get("total_bytes") or 0),
        "served_image_files": int((image_dirs.get("served_image_root") or {}).get("file_count") or 0),
        "served_image_bytes": int((image_dirs.get("served_image_root") or {}).get("total_bytes") or 0),
        "learning_processed_count": int(learning_status.get("processed_count") or 0),
        "learning_validated_card_count": int(learning_status.get("validated_card_count") or 0),
        "learning_queue_length": int(learning_status.get("queue_length") or 0),
        "learning_worker_status": dict(learning_status.get("worker_status") or {}),
        "schema_markers": {
            "learning_engine": str(learning_status.get("schema_version") or ""),
            "dossier_store": str(dossier_status.get("schema_version") or ""),
            "insight_cache": INSIGHT_CACHE_SCHEMA_VERSION,
        },
    }


def _growth_trigger_reasons(metrics: dict[str, Any], previous_metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if (int(metrics.get("dossier_cards") or 0) - int(previous_metrics.get("dossier_cards") or 0)) >= DOSSIER_GROWTH_CARD_THRESHOLD:
        reasons.append("verified_dossier_growth")
    if (int(metrics.get("insight_cache_bytes") or 0) - int(previous_metrics.get("insight_cache_bytes") or 0)) >= INSIGHT_CACHE_GROWTH_BYTES_THRESHOLD:
        reasons.append("insight_cache_growth")
    if (int(metrics.get("learning_processed_count") or 0) - int(previous_metrics.get("learning_processed_count") or 0)) >= LEARNING_PROGRESS_THRESHOLD:
        reasons.append("learning_progress_growth")
    if (int(metrics.get("served_image_files") or 0) - int(previous_metrics.get("served_image_files") or 0)) >= IMAGE_GROWTH_FILE_THRESHOLD:
        reasons.append("image_growth")
    return reasons


def _schema_milestone_reasons(metrics: dict[str, Any], previous_state: dict[str, Any]) -> list[str]:
    previous = dict(previous_state.get("schema_markers") or {})
    current = dict(metrics.get("schema_markers") or {})
    reasons: list[str] = []
    for key, value in current.items():
        if str(value or "").strip() and str(previous.get(key) or "") != str(value or ""):
            reasons.append(f"{key}_schema_changed")
    return reasons


def _snapshot_due(last_ts: int, interval_seconds: int) -> bool:
    if last_ts <= 0:
        return True
    return (int(time.time()) - last_ts) >= int(interval_seconds)


def build_snapshot_jobs(
    *,
    previous_state: dict[str, Any],
    metrics: dict[str, Any],
    manual_milestone: str = "",
    force_daily: bool = False,
    force_weekly: bool = False,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    last_runs = dict(previous_state.get("last_snapshot_runs") or {})
    growth_reasons = _growth_trigger_reasons(metrics, dict(previous_state.get("last_metrics") or {}))
    schema_reasons = _schema_milestone_reasons(metrics, previous_state)

    if manual_milestone:
        jobs.append(
            {
                "snapshot_type": "manual_milestone",
                "reason": "manual_milestone",
                "milestone": manual_milestone,
                "destination_keys": list(DEFAULT_SNAPSHOT_DESTINATION_KEYS),
            }
        )

    weekly_due = force_weekly or _snapshot_due(
        _parse_timestamp(str(last_runs.get("weekly_full") or "")),
        DEFAULT_WEEKLY_SNAPSHOT_INTERVAL_SECONDS,
    )
    if weekly_due:
        jobs.append(
            {
                "snapshot_type": "weekly_full",
                "reason": "weekly_schedule",
                "milestone": "",
                "destination_keys": list(DEFAULT_SNAPSHOT_DESTINATION_KEYS),
            }
        )
    elif schema_reasons:
        jobs.append(
            {
                "snapshot_type": "schema_milestone",
                "reason": "+".join(schema_reasons[:3]),
                "milestone": "schema",
                "destination_keys": list(DEFAULT_SNAPSHOT_DESTINATION_KEYS),
            }
        )

    daily_due = force_daily or _snapshot_due(
        _parse_timestamp(str(last_runs.get("daily_light") or "")),
        DEFAULT_DAILY_SNAPSHOT_INTERVAL_SECONDS,
    )
    if daily_due:
        jobs.append(
            {
                "snapshot_type": "daily_light",
                "reason": "daily_schedule",
                "milestone": "",
                "destination_keys": ["external", "archive"],
            }
        )
    elif growth_reasons:
        jobs.append(
            {
                "snapshot_type": "growth_guard",
                "reason": "+".join(growth_reasons[:3]),
                "milestone": "",
                "destination_keys": ["external", "archive"],
            }
        )
    return jobs


def build_log_rotation_plan(*, project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    layout = build_storage_layout(project_root=root)
    current = layout.current_runtime_paths()
    candidates: list[dict[str, Any]] = []
    for path_text in (
        current["learning_stdout_log"],
        current["learning_stderr_log"],
    ):
        path = Path(path_text)
        if not path.is_file():
            continue
        try:
            size_bytes = int(path.stat().st_size)
        except OSError:
            size_bytes = 0
        if size_bytes >= 10 * 1024 * 1024:
            candidates.append({"path": str(path), "reason": "large_file", "size_bytes": size_bytes})
    startup_root = Path(current["startup_log_root"])
    if startup_root.is_dir():
        for file_path in startup_root.glob("*.log"):
            try:
                age_seconds = int(time.time() - file_path.stat().st_mtime)
                size_bytes = int(file_path.stat().st_size)
            except OSError:
                continue
            if age_seconds >= 14 * 24 * 60 * 60 or size_bytes >= 10 * 1024 * 1024:
                candidates.append(
                    {
                        "path": str(file_path),
                        "reason": "age_or_size",
                        "size_bytes": size_bytes,
                        "age_seconds": age_seconds,
                    }
                )
    return {
        "generated_at": _utc_timestamp(),
        "candidate_count": len(candidates),
        "candidates": candidates[:50],
        "action": "plan_only",
    }


def build_retention_plan(*, project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    destinations = build_storage_layout(project_root=root).backup_destination_paths()
    keep_counts = {"light": 7, "full": 4}
    suggestions: list[dict[str, Any]] = []
    for name, record in destinations.items():
        snapshot_root = Path(str(record.get("snapshot_path") or record.get("brain_backup_path") or ""))
        if not snapshot_root.is_dir():
            continue
        directories = [item for item in snapshot_root.iterdir() if item.is_dir()]
        light_dirs = sorted([item for item in directories if "__weekly-full__" not in item.name and "__manual-milestone__" not in item.name and "__schema-milestone__" not in item.name], key=lambda item: item.name, reverse=True)
        full_dirs = sorted([item for item in directories if item not in light_dirs], key=lambda item: item.name, reverse=True)
        for label, bucket in (("light", light_dirs), ("full", full_dirs)):
            for item in bucket[keep_counts[label] :]:
                suggestions.append(
                    {
                        "destination": name,
                        "path": str(item),
                        "retention_tag": label,
                        "action": "review_for_prune",
                    }
                )
    return {
        "generated_at": _utc_timestamp(),
        "action": "plan_only",
        "suggestions": suggestions[:100],
    }


def load_maintenance_state(*, project_root: Path | None = None) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    return dict(_json_load(_maintenance_paths(root)["state"], {}))


def _run_maintenance_cycle_locked(
    *,
    project_root: Path | None = None,
    manual_milestone: str = "",
    force_daily: bool = False,
    force_weekly: bool = False,
    backfill_mode: str = "plan",
    backfill_plan_limit: int = DEFAULT_BACKFILL_PLAN_LIMIT,
    backfill_apply_limit: int = DEFAULT_BACKFILL_APPLY_LIMIT,
    lease_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    paths = _maintenance_paths(root)
    previous_state = load_maintenance_state(project_root=root)
    health_report = build_runtime_preflight_report(
        target="all",
        check_server_port_available=False,
        check_worker_lock_available=False,
    )
    metrics = collect_maintenance_metrics(project_root=root)
    runtime_cache_metrics = get_insight_cache_metrics_snapshot()
    flush_insight_cache_metrics_rollup()
    persistent_cache_rollup = get_persistent_insight_cache_rollup_snapshot()
    hotspot_report = _build_cache_hotspot_report(
        persistent_rollup=persistent_cache_rollup,
    )
    cache_rollup_snapshot = {
        "generated_at": _utc_timestamp(),
        "runtime_cache_metrics": runtime_cache_metrics,
        "persistent_cache_rollup": persistent_cache_rollup,
        "hotspots": hotspot_report,
    }
    _json_dump(paths["cache_rollup_state"], cache_rollup_snapshot)
    _json_dump(paths["cache_hotspot_report"], hotspot_report)
    _append_jsonl(
        paths["cache_rollup_history"],
        {
            "generated_at": cache_rollup_snapshot["generated_at"],
            "totals": dict((persistent_cache_rollup or {}).get("totals") or {}),
            "hotspots": {
                "fallback_heavy_context_count": len(list((hotspot_report or {}).get("fallback_heavy_contexts") or [])),
                "backfill_priority_count": len(list((hotspot_report or {}).get("backfill_priorities") or [])),
                "underused_contextual_type_count": len(list((hotspot_report or {}).get("contextual_underused_types") or [])),
            },
        },
    )
    normalized_backfill_mode = str(backfill_mode or "plan").strip().lower()
    if normalized_backfill_mode not in {"off", "plan", "apply"}:
        normalized_backfill_mode = "plan"
    backfill_guardrails = _load_backfill_guardrail_state(paths["backfill_guardrails"])
    backfill_plan = build_entity_backfill_plan(
        project_root=root,
        hotspot_report=hotspot_report,
        max_candidates=max(int(backfill_plan_limit or DEFAULT_BACKFILL_PLAN_LIMIT), 1),
        guardrail_state=backfill_guardrails,
    )
    _json_dump(paths["backfill_plan"], backfill_plan)
    _append_jsonl(
        paths["backfill_history"],
        {
            "generated_at": backfill_plan.get("generated_at") or _utc_timestamp(),
            "mode": "plan",
            "candidate_count": int(backfill_plan.get("candidate_count") or 0),
            "summary": dict(backfill_plan.get("summary") or {}),
            "guardrails": {
                "cooldown_seconds": int((backfill_plan.get("guardrails") or {}).get("cooldown_seconds") or 0),
                "adaptive_cooldown": {
                    "enabled": bool(((backfill_plan.get("guardrails") or {}).get("adaptive_cooldown") or {}).get("enabled")),
                    "base_cooldown_seconds": int((((backfill_plan.get("guardrails") or {}).get("adaptive_cooldown") or {}).get("base_cooldown_seconds") or 0)),
                    "insight_override_count": len(
                        dict((((backfill_plan.get("guardrails") or {}).get("adaptive_cooldown") or {}).get("insight_type_overrides") or {}))
                    ),
                    "context_override_count": len(
                        dict((((backfill_plan.get("guardrails") or {}).get("adaptive_cooldown") or {}).get("context_overrides") or {}))
                    ),
                },
                "skip_reasons": list((backfill_plan.get("guardrails") or {}).get("skip_reasons") or []),
                "requeue_reasons": list((backfill_plan.get("guardrails") or {}).get("requeue_reasons") or []),
            },
        },
    )
    backfill_apply = {
        "generated_at": _utc_timestamp(),
        "mode": "apply",
        "ok": True,
        "planned_count": 0,
        "applied_count": 0,
        "by_insight_type": {},
        "by_context": {},
        "skipped": [{"reason": "apply_not_requested"}],
        "results": [],
        "skip_reasons": {"apply_not_requested": 1},
        "requeue_reasons_applied": {},
        "cooldown_skipped_count": 0,
        "time_budget_exhausted": False,
        "deferred_due_to_budget": 0,
        "apply_time_budget_seconds": _env_int(
            "MIRU_BACKFILL_APPLY_TIME_BUDGET_SECONDS",
            DEFAULT_BACKFILL_APPLY_TIME_BUDGET_SECONDS,
            minimum=1,
        ),
        "duration_seconds": 0.0,
    }
    if normalized_backfill_mode == "apply":
        backfill_apply = apply_entity_backfill_plan(
            project_root=root,
            plan_payload=backfill_plan,
            apply_limit=max(int(backfill_apply_limit or DEFAULT_BACKFILL_APPLY_LIMIT), 1),
            guardrail_state=backfill_guardrails,
        )
        _json_dump(paths["backfill_last_apply"], backfill_apply)
        _append_jsonl(
            paths["backfill_history"],
            {
                "generated_at": backfill_apply.get("generated_at") or _utc_timestamp(),
                "mode": "apply",
                "planned_count": int(backfill_apply.get("planned_count") or 0),
                "applied_count": int(backfill_apply.get("applied_count") or 0),
                "by_insight_type": dict(backfill_apply.get("by_insight_type") or {}),
                "by_context": dict(backfill_apply.get("by_context") or {}),
                "skipped_count": len(list(backfill_apply.get("skipped") or [])),
                "skip_reasons": dict(backfill_apply.get("skip_reasons") or {}),
                "requeue_reasons_applied": dict(backfill_apply.get("requeue_reasons_applied") or {}),
                "cooldown_skipped_count": int(backfill_apply.get("cooldown_skipped_count") or 0),
                "time_budget_exhausted": bool(backfill_apply.get("time_budget_exhausted")),
                "deferred_due_to_budget": int(backfill_apply.get("deferred_due_to_budget") or 0),
            },
        )
    backfill_guardrails = _update_backfill_guardrail_state(
        previous_state=backfill_guardrails,
        plan_payload=backfill_plan,
        apply_payload=backfill_apply,
    )
    _save_backfill_guardrail_state(paths["backfill_guardrails"], backfill_guardrails)
    jobs = build_snapshot_jobs(
        previous_state=previous_state,
        metrics=metrics,
        manual_milestone=manual_milestone,
        force_daily=force_daily,
        force_weekly=force_weekly,
    )
    snapshot_runs: list[dict[str, Any]] = []
    for job in jobs:
        result = miru_brain_snapshot_bundle(
            list(job.get("destination_keys") or []),
            project_root=root,
            snapshot_type=str(job.get("snapshot_type") or "daily_light"),
            reason=str(job.get("reason") or "scheduled"),
            milestone=str(job.get("milestone") or ""),
        )
        snapshot_runs.append(
            {
                "generated_at": _utc_timestamp(),
                "snapshot_id": str(result.get("snapshot_id") or ""),
                "snapshot_type": str(job.get("snapshot_type") or ""),
                "reason": str(job.get("reason") or ""),
                "milestone": str(job.get("milestone") or ""),
                "ok": bool(result.get("ok")),
                "successful_destinations": list(result.get("successful_destinations") or []),
                "failed_destinations": list(result.get("failed_destinations") or []),
            }
        )
        _append_log(
            paths["log"],
            f"snapshot type={job.get('snapshot_type')} ok={bool(result.get('ok'))} successful={','.join(result.get('successful_destinations') or [])} failed={','.join(result.get('failed_destinations') or [])}",
        )

    retention_plan = build_retention_plan(project_root=root)
    log_rotation_plan = build_log_rotation_plan(project_root=root)
    last_snapshot_runs = dict(previous_state.get("last_snapshot_runs") or {})
    for item in snapshot_runs:
        if item.get("ok"):
            last_snapshot_runs[str(item.get("snapshot_type") or "")] = str(item.get("generated_at") or "")

    state = {
        "generated_at": _utc_timestamp(),
        "project_root": str(root),
        "maintenance_mode": "headless_safe",
        "lease": dict(lease_info or {}),
        "health": {
            "ok": bool(health_report.get("ok")),
            "summary": dict(health_report.get("summary") or {}),
        },
        "last_metrics": metrics,
        "schema_markers": dict(metrics.get("schema_markers") or {}),
        "last_snapshot_runs": last_snapshot_runs,
        "recent_snapshots": (list(previous_state.get("recent_snapshots") or []) + snapshot_runs)[-20:],
        "pending_snapshot_jobs": jobs,
        "retention_plan": retention_plan,
        "log_rotation_plan": log_rotation_plan,
        "backup_destinations": miru_brain_manifest(project_root=root).get("destination_availability") or {},
        "cache_effectiveness": {
            "rollup_state_path": str(paths["cache_rollup_state"]),
            "rollup_history_path": str(paths["cache_rollup_history"]),
            "hotspot_report_path": str(paths["cache_hotspot_report"]),
            "summary": {
                "overall_hit_rate": float((hotspot_report.get("totals") or {}).get("overall_hit_rate") or 0.0),
                "overall_miss_rate": float((hotspot_report.get("totals") or {}).get("overall_miss_rate") or 0.0),
                "fallback_heavy_context_count": len(list(hotspot_report.get("fallback_heavy_contexts") or [])),
                "backfill_priority_count": len(list(hotspot_report.get("backfill_priorities") or [])),
            },
            "hotspots": hotspot_report,
        },
        "deterministic_backfill": {
            "mode": normalized_backfill_mode,
            "plan_path": str(paths["backfill_plan"]),
            "history_path": str(paths["backfill_history"]),
            "last_apply_path": str(paths["backfill_last_apply"]),
            "guardrails_path": str(paths["backfill_guardrails"]),
            "adaptive_cooldown": dict((backfill_plan.get("guardrails") or {}).get("adaptive_cooldown") or {}),
            "plan_summary": {
                "candidate_count": int(backfill_plan.get("candidate_count") or 0),
                "by_insight_type": dict((backfill_plan.get("summary") or {}).get("by_insight_type") or {}),
                "by_context": dict((backfill_plan.get("summary") or {}).get("by_context") or {}),
                "cooldown_skips": int((backfill_plan.get("guardrails") or {}).get("cooldown_skips") or 0),
                "requeue_reasons": list((backfill_plan.get("guardrails") or {}).get("requeue_reasons") or []),
            },
            "apply_summary": {
                "planned_count": int(backfill_apply.get("planned_count") or 0),
                "applied_count": int(backfill_apply.get("applied_count") or 0),
                "by_insight_type": dict(backfill_apply.get("by_insight_type") or {}),
                "by_context": dict(backfill_apply.get("by_context") or {}),
                "skipped_count": len(list(backfill_apply.get("skipped") or [])),
                "skip_reasons": dict(backfill_apply.get("skip_reasons") or {}),
                "cooldown_skipped_count": int(backfill_apply.get("cooldown_skipped_count") or 0),
                "time_budget_exhausted": bool(backfill_apply.get("time_budget_exhausted")),
                "deferred_due_to_budget": int(backfill_apply.get("deferred_due_to_budget") or 0),
                "apply_time_budget_seconds": int(backfill_apply.get("apply_time_budget_seconds") or 0),
                "duration_seconds": float(backfill_apply.get("duration_seconds") or 0.0),
            },
            "guardrail_summary": {
                "recent_cycle": dict(backfill_guardrails.get("recent_cycle") or {}),
                "counters": dict(backfill_guardrails.get("counters") or {}),
                "tracked_keys": {
                    "last_applied": len(dict(backfill_guardrails.get("entity_insight_last_applied_at") or {})),
                    "last_skip": len(dict(backfill_guardrails.get("entity_insight_last_skip") or {})),
                },
            },
            "lease_summary": dict(lease_info or {}),
        },
    }
    _json_dump(paths["state"], state)
    _append_log(
        paths["log"],
        (
            f"maintenance cycle ok={bool(health_report.get('ok'))} snapshots={len(snapshot_runs)} "
            f"queued_jobs={len(jobs)} backfill_mode={normalized_backfill_mode} "
            f"backfill_candidates={int(backfill_plan.get('candidate_count') or 0)} "
            f"backfill_applied={int(backfill_apply.get('applied_count') or 0)} "
            f"cooldown_skips={int((backfill_plan.get('guardrails') or {}).get('cooldown_skips') or 0)}"
        ),
    )
    return {
        "ok": True,
        "mode": "maintenance_cycle",
        "lease": dict(lease_info or {}),
        "state_path": str(paths["state"]),
        "log_path": str(paths["log"]),
        "health_report": health_report,
        "metrics": metrics,
        "snapshot_jobs": jobs,
        "snapshot_runs": snapshot_runs,
        "retention_plan": retention_plan,
        "log_rotation_plan": log_rotation_plan,
        "cache_rollup": cache_rollup_snapshot,
        "cache_hotspots": hotspot_report,
        "backfill": {
            "mode": normalized_backfill_mode,
            "plan": backfill_plan,
            "apply": backfill_apply,
            "plan_path": str(paths["backfill_plan"]),
            "history_path": str(paths["backfill_history"]),
            "last_apply_path": str(paths["backfill_last_apply"]),
            "guardrails_path": str(paths["backfill_guardrails"]),
            "guardrails": backfill_guardrails,
        },
        "state": state,
    }


def run_maintenance_cycle(
    *,
    project_root: Path | None = None,
    manual_milestone: str = "",
    force_daily: bool = False,
    force_weekly: bool = False,
    backfill_mode: str = "plan",
    backfill_plan_limit: int = DEFAULT_BACKFILL_PLAN_LIMIT,
    backfill_apply_limit: int = DEFAULT_BACKFILL_APPLY_LIMIT,
) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    paths = _maintenance_paths(root)
    lease_seconds = _env_int(
        "MIRU_MAINTENANCE_LEASE_SECONDS",
        DEFAULT_MAINTENANCE_LEASE_SECONDS,
        minimum=30,
    )
    owner_id = f"pid-{os.getpid()}-{int(time.time() * 1000)}"
    lease = _try_acquire_maintenance_lease(
        lease_path=paths["lease"],
        owner_id=owner_id,
        project_root=root,
        lease_seconds=lease_seconds,
    )
    if not bool(lease.get("acquired")):
        _append_log(
            paths["log"],
            (
                f"maintenance cycle skipped reason={str(lease.get('reason') or 'lease_held')} "
                f"owner={str(lease.get('owner_id') or '')} "
                f"remaining={int(lease.get('seconds_remaining') or 0)}"
            ),
        )
        existing_state = load_maintenance_state(project_root=root)
        existing_state["lease"] = {
            **dict(lease),
            "skipped_at": _utc_timestamp(),
        }
        _json_dump(paths["state"], existing_state)
        return {
            "ok": True,
            "mode": "maintenance_cycle",
            "skipped": True,
            "skip_reason": "maintenance_lease_held",
            "lease": lease,
            "state_path": str(paths["state"]),
            "log_path": str(paths["log"]),
            "state": existing_state,
            "snapshot_jobs": [],
            "snapshot_runs": [],
            "backfill": {
                "mode": str(backfill_mode or "plan"),
                "plan": {},
                "apply": {},
                "plan_path": str(paths["backfill_plan"]),
                "history_path": str(paths["backfill_history"]),
                "last_apply_path": str(paths["backfill_last_apply"]),
                "guardrails_path": str(paths["backfill_guardrails"]),
                "guardrails": dict(_load_backfill_guardrail_state(paths["backfill_guardrails"])),
            },
        }
    try:
        report = _run_maintenance_cycle_locked(
            project_root=root,
            manual_milestone=manual_milestone,
            force_daily=force_daily,
            force_weekly=force_weekly,
            backfill_mode=backfill_mode,
            backfill_plan_limit=backfill_plan_limit,
            backfill_apply_limit=backfill_apply_limit,
            lease_info=lease,
        )
        return report
    finally:
        release = _release_maintenance_lease(lease_path=paths["lease"], owner_id=owner_id)
        if not bool(release.get("released")) and str(release.get("reason") or "") not in {"lease_missing"}:
            _append_log(
                paths["log"],
                f"maintenance lease release warning reason={str(release.get('reason') or '')} path={str(release.get('path') or '')}",
            )


def format_maintenance_report(report: dict[str, Any]) -> str:
    if bool(report.get("skipped")):
        lease = dict(report.get("lease") or {})
        return "\n".join(
            [
                "Miru maintenance cycle",
                "OK: True",
                f"Skipped: True ({str(report.get('skip_reason') or 'maintenance_lease_held')})",
                f"Lease owner: {str(lease.get('owner_id') or '')}",
                f"Lease remaining seconds: {int(lease.get('seconds_remaining') or 0)}",
                f"State path: {report.get('state_path', '')}",
            ]
        )
    cache_hotspots = dict(report.get("cache_hotspots") or {})
    cache_totals = dict(cache_hotspots.get("totals") or {})
    backfill = dict(report.get("backfill") or {})
    backfill_plan = dict(backfill.get("plan") or {})
    backfill_apply = dict(backfill.get("apply") or {})
    lines = [
        "Miru maintenance cycle",
        f"OK: {bool(report.get('ok'))}",
        f"State path: {report.get('state_path', '')}",
        f"Snapshot jobs: {len(list(report.get('snapshot_jobs') or []))}",
        f"Snapshot runs: {len(list(report.get('snapshot_runs') or []))}",
        (
            "Cache effectiveness:"
            f" hit_rate={float(cache_totals.get('overall_hit_rate') or 0.0):.2f}"
            f" miss_rate={float(cache_totals.get('overall_miss_rate') or 0.0):.2f}"
            f" fallback_hotspots={len(list(cache_hotspots.get('fallback_heavy_contexts') or []))}"
        ),
        (
            "Backfill queue:"
            f" mode={str(backfill.get('mode') or 'plan')}"
            f" candidates={int(backfill_plan.get('candidate_count') or 0)}"
            f" applied={int(backfill_apply.get('applied_count') or 0)}"
            f" cooldown_skips={int((backfill_plan.get('guardrails') or {}).get('cooldown_skips') or 0)}"
        ),
    ]
    for item in list(report.get("snapshot_runs") or []):
        lines.append(
            f"- {item.get('snapshot_type', '')}: ok={bool(item.get('ok'))} success={','.join(item.get('successful_destinations') or [])} failed={','.join(item.get('failed_destinations') or [])}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Project Miru maintenance worker")
    parser.add_argument("--loop", action="store_true", help="Run continuously in headless maintenance mode.")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_MAINTENANCE_INTERVAL_SECONDS)
    parser.add_argument("--milestone", default="", help="Optional named milestone snapshot.")
    parser.add_argument("--force-daily", action="store_true")
    parser.add_argument("--force-weekly", action="store_true")
    parser.add_argument("--backfill-mode", choices=("off", "plan", "apply"), default="plan")
    parser.add_argument("--backfill-plan-limit", type=int, default=DEFAULT_BACKFILL_PLAN_LIMIT)
    parser.add_argument("--backfill-apply-limit", type=int, default=DEFAULT_BACKFILL_APPLY_LIMIT)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()

    while True:
        report = run_maintenance_cycle(
            manual_milestone=str(args.milestone or ""),
            force_daily=bool(args.force_daily),
            force_weekly=bool(args.force_weekly),
            backfill_mode=str(args.backfill_mode or "plan"),
            backfill_plan_limit=max(int(args.backfill_plan_limit or DEFAULT_BACKFILL_PLAN_LIMIT), 1),
            backfill_apply_limit=max(int(args.backfill_apply_limit or DEFAULT_BACKFILL_APPLY_LIMIT), 1),
        )
        if args.format == "json":
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(format_maintenance_report(report))
        if not args.loop:
            return 0 if report.get("ok") else 1
        time.sleep(max(int(args.interval_seconds or DEFAULT_MAINTENANCE_INTERVAL_SECONDS), 60))


if __name__ == "__main__":
    raise SystemExit(main())
