"""
Low-API activation and budget guardrails.

- Cost-aware decision: prefer stored data, deterministic logic, cache; API only when justified.
- Duplicate-work avoidance: content hashes, skip when unchanged.
- Translation reuse: same JP text → reuse stored translation.
- Worth-doing thresholds: skip low-priority enrichment, defer when no site impact.
- Budget signals: expensive_avoided, cached_reused, deferred, skipped_pending_budget.

Does not activate unrestricted learning. Preserves review-first safety.
"""
from __future__ import annotations

import hashlib
from typing import Any

# ---------------------------------------------------------------------------
# Budget signal types (for Dev / logging)
# ---------------------------------------------------------------------------

BUDGET_SIGNAL_EXPENSIVE_AVOIDED = "expensive_avoided"
BUDGET_SIGNAL_CACHED_REUSED = "cached_reused"
BUDGET_SIGNAL_DEFERRED = "deferred"
BUDGET_SIGNAL_SKIPPED_PENDING_BUDGET = "skipped_pending_budget"
BUDGET_SIGNAL_DETERMINISTIC_SUFFICIENT = "deterministic_sufficient"
BUDGET_SIGNAL_DUPLICATE_WORK_SKIPPED = "duplicate_work_skipped"
BUDGET_SIGNAL_TRANSLATION_REUSED = "translation_reused"
BUDGET_SIGNAL_BATCH_DISCIPLINE = "batch_discipline"


def content_hash_jp(card_name_jp: str, effect_text_jp: str) -> str:
    """Stable hash for Japanese source text. Used to avoid re-translation when unchanged."""
    raw = f"{str(card_name_jp or '').strip()}\n{str(effect_text_jp or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def content_hash_image(card_code: str, image_url: str, image_path: str) -> str:
    """Stable signature for image identity. Used to avoid re-analysis when same image already verified."""
    raw = f"{str(card_code or '').strip()}|{str(image_url or '').strip()}|{str(image_path or '').strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def content_hash_insight(card_code: str, structured_payload: dict[str, Any]) -> str:
    """Stable hash of key structured fields. Used to avoid regenerating insight when data unchanged."""
    import json
    keys = ("primary_insight_type", "posture_summary", "publication_eligible", "banlist_status")
    subset = {k: structured_payload.get(k) for k in keys if k in structured_payload}
    raw = f"{str(card_code or '').strip()}|{json.dumps(subset, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Cost-aware decision: should we use API/model for this step?
# Prefer: (1) existing stored, (2) deterministic, (3) cached, (4) lightweight, (5) API only when justified.
# ---------------------------------------------------------------------------

def should_use_api(
    step_name: str,
    *,
    existing_data: bool = False,
    source_unchanged: bool = False,
    deterministic_sufficient: bool = False,
    cache_hit: bool = False,
) -> tuple[bool, str]:
    """
    Return (use_api, reason).
    use_api is False when we can avoid the call (existing data, unchanged source, deterministic path, or cache).
    """
    if existing_data:
        return (False, "existing_stored_data")
    if source_unchanged:
        return (False, "source_unchanged")
    if deterministic_sufficient:
        return (False, "deterministic_sufficient")
    if cache_hit:
        return (False, "cache_hit")
    return (True, "needs_api")


# ---------------------------------------------------------------------------
# Worth doing? Thresholds so we skip or delay low-value API work.
# ---------------------------------------------------------------------------

def worth_doing(
    task_type: str,
    *,
    priority_class: str = "medium",
    has_site_impact: bool = True,
    is_prerelease: bool = False,
    confidence_already_high: bool = False,
    is_essential: bool = False,
) -> tuple[bool, str]:
    """
    Return (do_it, reason).
    Prefer: essential facts, legality, rules, publish-relevant.
    Skip or delay: optional enrichment, lore refinement, speculative analysis, repeated stylistic rewrite.
    """
    t = str(task_type or "").strip().lower()
    if is_essential:
        return (True, "essential")
    if confidence_already_high and t in ("lore", "enrichment", "cleanup", "stylistic"):
        return (False, "high_confidence_enrichment_skip")
    if not has_site_impact and priority_class == "low":
        return (False, "no_site_impact_low_priority")
    if is_prerelease and t not in ("verify_official_fields", "bootstrap_dossier", "banlist", "ruling"):
        return (False, "prerelease_minimal")
    if priority_class == "low" and t in ("lore", "enrichment"):
        return (False, "low_priority_enrichment_deferred")
    return (True, "worth_doing")


# ---------------------------------------------------------------------------
# Batch / cadence: minimum interval between batch starts (caller can enforce).
# ---------------------------------------------------------------------------

def min_interval_seconds_for_batch(batch_kind: str) -> int:
    """Suggested minimum seconds between batch starts. Deliberate batch worker, not continuous burner."""
    k = str(batch_kind or "").strip().lower()
    if k in ("translation", "reasoning", "model"):
        return 60
    if k in ("verify", "bootstrap"):
        return 30
    return 15


# ---------------------------------------------------------------------------
# Record budget signal (caller passes store or engine that has append_budget_signal).
# ---------------------------------------------------------------------------

def record_budget_signal(
    target: Any,
    event_type: str,
    *,
    card_code: str = "",
    task_type: str = "",
    detail: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Call target.append_budget_signal(...) if present. Lightweight; no-op if method missing."""
    if target is None:
        return
    fn = getattr(target, "append_budget_signal", None)
    if callable(fn):
        try:
            fn(
                event_type=event_type,
                card_code=card_code,
                task_type=task_type,
                detail=detail,
                extra=extra or {},
            )
        except Exception:
            pass
