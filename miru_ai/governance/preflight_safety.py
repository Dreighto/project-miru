"""
Preflight / safety layer before learner activation.

- Conflict resolution: detect conflicts, lower confidence, mark for review.
- Category-specific confidence: distinct confidence by category (card_fact, image, translation, meta, price, ruling, legality).
- Weak-signal / "nothing useful yet": explicit states so Miru does not fill with weak filler.
- Review queue prioritization: HIGH / MEDIUM / LOW for learner review queue.
- Site-impact awareness: which surfaces are affected by a learned change.

Preserves: learner defaults (review-required), source registry, API halt, compliance, publish layer.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

CONFLICT_TYPE_CARD_FACT = "contradictory_card_fact"
CONFLICT_TYPE_LEGALITY = "contradictory_legality_banlist"
CONFLICT_TYPE_TRANSLATION = "conflicting_translation"
CONFLICT_TYPE_IMAGE_PRINT = "image_print_mismatch"
CONFLICT_TYPE_META_USAGE = "meta_usage_disagreement"
CONFLICT_TYPE_RULING = "conflicting_ruling"

CONFLICT_STATUS_DETECTED = "conflict_detected"
CONFLICT_STATUS_WITHHELD = "withheld"
CONFLICT_STATUS_REVIEW_REQUIRED = "review_required"

# When conflict is detected: lower confidence cap and require review
CONFLICT_CONFIDENCE_CAP = 0.5


def conflict_reason_to_block_reason(conflict_type: str) -> str:
    """Map conflict type to publication block reason."""
    return f"conflict_{conflict_type}"


def should_prefer_official_source(source_id: str) -> bool:
    """True if source is official (prefer over others in conflict)."""
    return str(source_id or "").strip().lower().startswith("official_")


# ---------------------------------------------------------------------------
# Category-specific confidence (path for reasoning per category)
# ---------------------------------------------------------------------------

CONFIDENCE_CATEGORY_CARD_FACT = "card_fact"
CONFIDENCE_CATEGORY_IMAGE = "image"
CONFIDENCE_CATEGORY_TRANSLATION = "translation"
CONFIDENCE_CATEGORY_META = "meta"
CONFIDENCE_CATEGORY_PRICE = "price"
CONFIDENCE_CATEGORY_RULING = "ruling"
CONFIDENCE_CATEGORY_LEGALITY = "legality"

CONFIDENCE_CATEGORIES = (
    CONFIDENCE_CATEGORY_CARD_FACT,
    CONFIDENCE_CATEGORY_IMAGE,
    CONFIDENCE_CATEGORY_TRANSLATION,
    CONFIDENCE_CATEGORY_META,
    CONFIDENCE_CATEGORY_PRICE,
    CONFIDENCE_CATEGORY_RULING,
    CONFIDENCE_CATEGORY_LEGALITY,
)


def confidence_by_category_schema() -> dict[str, float]:
    """Return empty schema: category -> 0.0 for each category."""
    return {c: 0.0 for c in CONFIDENCE_CATEGORIES}


# ---------------------------------------------------------------------------
# Weak-signal / "nothing useful yet" (explicit; do not fill with filler)
# ---------------------------------------------------------------------------

WEAK_SIGNAL_STILL_VERIFYING = "still_verifying"
WEAK_SIGNAL_TOO_EARLY_TO_CALL = "too_early_to_call"
WEAK_SIGNAL_NO_STRONG_META_SIGNAL = "no_strong_meta_signal"
WEAK_SIGNAL_NO_OFFICIAL_RULING_FOUND = "no_official_ruling_found"
WEAK_SIGNAL_NOTHING_PUBLISH_WORTHY_YET = "nothing_publish_worthy_yet"
WEAK_SIGNAL_NO_OFFICIAL_TRANSLATION = "no_official_translation_yet"

WEAK_SIGNAL_STATES = (
    WEAK_SIGNAL_STILL_VERIFYING,
    WEAK_SIGNAL_TOO_EARLY_TO_CALL,
    WEAK_SIGNAL_NO_STRONG_META_SIGNAL,
    WEAK_SIGNAL_NO_OFFICIAL_RULING_FOUND,
    WEAK_SIGNAL_NOTHING_PUBLISH_WORTHY_YET,
    WEAK_SIGNAL_NO_OFFICIAL_TRANSLATION,
)


def weak_signal_message(state: str) -> str:
    """Human-readable message for weak-signal state."""
    return {
        WEAK_SIGNAL_STILL_VERIFYING: "Still verifying.",
        WEAK_SIGNAL_TOO_EARLY_TO_CALL: "Too early to call.",
        WEAK_SIGNAL_NO_STRONG_META_SIGNAL: "No strong meta signal yet.",
        WEAK_SIGNAL_NO_OFFICIAL_RULING_FOUND: "No official ruling found yet.",
        WEAK_SIGNAL_NOTHING_PUBLISH_WORTHY_YET: "Nothing publish-worthy yet.",
        WEAK_SIGNAL_NO_OFFICIAL_TRANSLATION: "No official translation yet.",
    }.get(state, "Uncertain.")


# ---------------------------------------------------------------------------
# Review queue prioritization (for learner review queue)
# ---------------------------------------------------------------------------

PRIORITY_CLASS_HIGH = "high"
PRIORITY_CLASS_MEDIUM = "medium"
PRIORITY_CLASS_LOW = "low"

# Integer priority for learning_queue: higher = more urgent
PRIORITY_HIGH_INT = 100
PRIORITY_MEDIUM_INT = 50
PRIORITY_LOW_INT = 10


def priority_class_to_int(priority_class: str) -> int:
    """Map HIGH/MEDIUM/LOW to integer for queue ordering."""
    c = str(priority_class or "").strip().lower()
    if c == PRIORITY_CLASS_HIGH:
        return PRIORITY_HIGH_INT
    if c == PRIORITY_CLASS_MEDIUM:
        return PRIORITY_MEDIUM_INT
    return PRIORITY_LOW_INT


def review_priority_for_task(task_type: str, *, conflict_type: str | None = None) -> int:
    """
    Suggested queue priority for a review task.
    HIGH: legality, banlist, official rulings, blocked source/compliance conflict.
    MEDIUM: uncertain translation, image mismatch, prerelease verification.
    LOW: lore, minor enrichment, non-critical cleanup.
    """
    t = str(task_type or "").strip().lower()
    if conflict_type:
        if CONFLICT_TYPE_LEGALITY in conflict_type or CONFLICT_TYPE_RULING in conflict_type:
            return PRIORITY_HIGH_INT
        if CONFLICT_TYPE_TRANSLATION in conflict_type or CONFLICT_TYPE_IMAGE_PRINT in conflict_type:
            return PRIORITY_MEDIUM_INT
    if t in ("banlist", "legality", "ruling", "compliance", "audit"):
        return PRIORITY_HIGH_INT
    if t in ("translation", "image_verify", "prerelease_verify", "image_mismatch"):
        return PRIORITY_MEDIUM_INT
    if t in ("lore", "enrichment", "cleanup"):
        return PRIORITY_LOW_INT
    return PRIORITY_MEDIUM_INT


# ---------------------------------------------------------------------------
# Site-impact awareness (which surfaces are affected)
# ---------------------------------------------------------------------------

AFFECTED_SURFACE_LIBRARY = "library"
AFFECTED_SURFACE_MODAL_INSIGHT = "modal_insight"
AFFECTED_SURFACE_BANNED_INDICATOR = "banned_indicator"
AFFECTED_SURFACE_RULINGS_SECTION = "rulings_section"
AFFECTED_SURFACE_IMAGE = "image"
AFFECTED_SURFACE_LEADER_HUB = "leader_hub"

AFFECTED_SURFACES = (
    AFFECTED_SURFACE_LIBRARY,
    AFFECTED_SURFACE_MODAL_INSIGHT,
    AFFECTED_SURFACE_BANNED_INDICATOR,
    AFFECTED_SURFACE_RULINGS_SECTION,
    AFFECTED_SURFACE_IMAGE,
    AFFECTED_SURFACE_LEADER_HUB,
)


def affected_surfaces_for_insight(
    *,
    insight_ready: bool = False,
    publish_eligible: bool = False,
    banlist_banned: bool = False,
    has_ruling_explanations: bool = False,
    has_master_image: bool = False,
    has_leader_intel: bool = False,
) -> list[str]:
    """Compute which site surfaces are affected by this card's current state."""
    out: list[str] = []
    if publish_eligible or insight_ready:
        out.append(AFFECTED_SURFACE_LIBRARY)
        out.append(AFFECTED_SURFACE_MODAL_INSIGHT)
    if banlist_banned:
        out.append(AFFECTED_SURFACE_BANNED_INDICATOR)
    if has_ruling_explanations:
        out.append(AFFECTED_SURFACE_RULINGS_SECTION)
    if has_master_image:
        out.append(AFFECTED_SURFACE_IMAGE)
    if has_leader_intel:
        out.append(AFFECTED_SURFACE_LEADER_HUB)
    return list(dict.fromkeys(out))  # preserve order, dedupe
