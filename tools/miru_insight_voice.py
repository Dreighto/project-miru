"""
Phase 16 – Miru voice, insight style, and player guidance.
Phase 16.5 – Price signal insight category.

Defines how Miru phrases insights: first person, knowledgeable regular at locals,
OPTCG player terminology, short and clear. Supports price_signal category for
player-language price insights (staple, spike, drop, cheap, reprint). No UI
changes; only how insight text is produced or selected. Compliance and
publication gate unchanged.
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Insight categories (Core Insight Types)
# ---------------------------------------------------------------------------

CATEGORY_META_RELEVANCE = "meta_relevance"
CATEGORY_DECK_USAGE = "deck_usage"
CATEGORY_STANDOUT = "standout_characteristics"
CATEGORY_PRICE_SIGNAL = "price_signal"
CATEGORY_RULING_CLARIFICATION = "ruling_clarification"
CATEGORY_LORE_TRIVIA = "lore_trivia"

INSIGHT_TYPE_TO_CATEGORY: dict[str, str] = {
    "usage": CATEGORY_META_RELEVANCE,
    "strategy": CATEGORY_DECK_USAGE,
    "meta": CATEGORY_META_RELEVANCE,
    "ruling": CATEGORY_RULING_CLARIFICATION,
    "synergy": CATEGORY_DECK_USAGE,
    "price": CATEGORY_PRICE_SIGNAL,
}

# Widely recognized nicknames (clarity first; use only when safe)
LEADER_NICKNAMES: dict[str, str] = {
    "donquixote doflamingo": "Doffy",
    "donquixote rosinante": "Rosi",
    "monkey d. luffy": "Luffy",
    "trafalgar law": "Law",
}


def _leader_display_name(leader_name: str) -> str:
    """Return display name for a leader; use nickname if widely recognized."""
    key = str(leader_name or "").strip().lower()
    return LEADER_NICKNAMES.get(key, str(leader_name or "").strip() or "this leader")


# ---------------------------------------------------------------------------
# Filler detection – do not surface generic/obvious/no-value insight text
# ---------------------------------------------------------------------------

FILLER_PATTERNS = (
    r"^Meta trend:\s*unknown\.?$",
    r"^Strategy role:\s*\.?\s*$",
    r"^Ruling evidence exists for this card\.?$",
    r"^Synergy pattern evidence exists for this card\.?$",
    r"^Partial strategy evidence is available for this card\.?$",
    r"^Verified usage data exists for this card\.?$",
    r"^Frequently pairs with:\s*\.?$",
    r"^Ruling available:\s*\.?$",
    r"^Price data exists for this card\.?$",
)
FILLER_PATTERNS_COMPILED = [re.compile(p, re.IGNORECASE) for p in FILLER_PATTERNS]

MIN_MEANINGFUL_LENGTH = 12  # only filter very short/empty; rely on patterns for phrase filler


def is_filler_insight(summary: str, insight_type: str) -> bool:
    """Return True if the insight text is generic, obvious, or adds no value.

    Quality over quantity: do not surface filler insights.
    """
    text = str(summary or "").strip()
    if not text or len(text) < MIN_MEANINGFUL_LENGTH:
        return True
    for pat in FILLER_PATTERNS_COMPILED:
        if pat.match(text):
            return True
    # "Strategy role: core." with no role_purpose is weak but not always filler
    if insight_type == "strategy" and text.lower().startswith("strategy role:") and "—" not in text and len(text) < 40:
        return True
    return False


# ---------------------------------------------------------------------------
# Shaping: structured section + context -> Miru-voice display (1–3 sentences)
# ---------------------------------------------------------------------------

def _snippet(text: str, max_len: int = 120) -> str:
    """Short snippet for ruling or long text; avoid cutting mid-word."""
    t = str(text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 1].rsplit(" ", 1)[0] + ("…" if len(t) > max_len else "")


def shape_section_display(
    insight_type: str,
    section_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Produce Miru-voice display for one insight section.

    Returns dict with:
      - primary_text: 1–3 sentences, first person, player language
      - follow_up_text: optional short follow-up (or None)
      - source_note: optional evidence note (or None)
      - category: one of meta_relevance, deck_usage, etc.

    Miru sounds like a knowledgeable regular at locals: calm, casual,
    confident but not arrogant, first person, no corporate/robotic phrasing.
    """
    category = INSIGHT_TYPE_TO_CATEGORY.get(insight_type, CATEGORY_DECK_USAGE)
    evidence_posture = str(section_data.get("evidence_posture") or "").lower()
    summary = str(section_data.get("summary") or "").strip()

    # If we would output filler, return minimal display
    if is_filler_insight(summary, insight_type):
        return {
            "primary_text": "",
            "follow_up_text": None,
            "source_note": None,
            "category": category,
            "is_filler": True,
        }

    leader_name = str(context.get("leader_name") or context.get("usage_leader") or "").strip()
    archetype = str(context.get("archetype_label") or context.get("usage_archetype") or "").strip()
    role_label = str(context.get("role_label") or "").strip()
    role_purpose = str(context.get("role_purpose") or "").strip()
    trend_label = str(context.get("trend_label") or "").strip()
    ruling_snippet = str(context.get("first_ruling_text") or "").strip()
    related_cards = list(context.get("top_partners") or [])

    primary_text = ""
    follow_up_text: str | None = None
    source_note: str | None = None

    # Weak evidence phrasing
    if evidence_posture.startswith("incomplete_") or evidence_posture.startswith("stale_"):
        weak_prefix = "I'm starting to see "
    elif evidence_posture.startswith("partial_"):
        weak_prefix = "I usually see "
    else:
        weak_prefix = "I mostly see "

    if insight_type == "usage":
        leader_display = _leader_display_name(leader_name)
        if archetype and leader_display:
            primary_text = f"{weak_prefix}this card in {archetype} lists with {leader_display}."
        elif leader_display:
            primary_text = f"{weak_prefix}this card in {leader_display} lists."
        elif archetype:
            primary_text = f"{weak_prefix}this card in {archetype} builds."
        else:
            primary_text = f"{weak_prefix}this card in competitive lists."
        source_note = "Based on tournament decklists and usage data."

    elif insight_type == "strategy":
        if role_purpose:
            primary_text = f"Players often use this as a {role_label or 'flex'} piece: {role_purpose}"
            if not primary_text.endswith("."):
                primary_text += "."
        elif role_label:
            primary_text = f"This card usually fills a {role_label} role in deck builds."
        else:
            primary_text = "This card shows up in lists that want extra value or pressure."
        if archetype:
            follow_up_text = f"It fits well in {archetype} builds."

    elif insight_type == "meta":
        if trend_label and trend_label.lower() not in ("unknown", ""):
            if trend_label.lower() == "rising":
                primary_text = "This card tends to rise in the meta right now."
            elif trend_label.lower() == "stable":
                primary_text = "This card tends to stay stable in the meta right now."
            elif trend_label.lower() == "stale":
                primary_text = "This card has seen less play lately in competitive lists."
            else:
                primary_text = f"I see this card {trend_label} in current lists."
        else:
            primary_text = "This card shows up in some competitive lists."
        source_note = "Based on usage and decklist data."

    elif insight_type == "ruling":
        if ruling_snippet:
            primary_text = f"I checked the official ruling: {_snippet(ruling_snippet, 100)}"
            if not primary_text.endswith("."):
                primary_text += "."
        elif context.get("no_ruling_found"):
            primary_text = "I couldn't find an official ruling for this interaction yet."
        else:
            primary_text = "There's a relevant ruling for how this card interacts."
        source_note = "From official rulings and judge answers." if ruling_snippet else None

    elif insight_type == "synergy":
        if related_cards:
            partners = ", ".join(related_cards[:3])
            primary_text = f"This card often pairs with {partners} in competitive lists."
        else:
            primary_text = "This card tends to be run alongside other pieces in the same package."
        if archetype:
            follow_up_text = f"Common in {archetype} packages."

    elif insight_type == "price":
        # Player-language price signals (no market-analysis jargon)
        price_signal_type = str(context.get("price_signal_type") or section_data.get("price_signal_type") or "").strip().lower()
        if price_signal_type == "staple" or "staple" in summary.lower() or "playset" in summary.lower():
            primary_text = "This card tends to stay expensive because competitive lists usually run the full playset."
        elif price_signal_type == "spike" or "spike" in summary.lower() or "winning" in summary.lower():
            primary_text = "I usually see this spike when the deck it's used in starts winning tournaments."
        elif price_signal_type == "drop" or "drop" in summary.lower() or "falls out" in summary.lower():
            primary_text = "If the deck using this falls out of the meta, the price usually drops pretty quickly."
        elif price_signal_type == "cheap" or "cheap" in summary.lower() or "not seeing much play" in summary.lower():
            primary_text = "This card is cheap right now because it's not seeing much play."
        elif price_signal_type == "reprint" or "reprint" in summary.lower():
            primary_text = "Reprints usually bring the price down."
        elif summary and len(summary) >= MIN_MEANINGFUL_LENGTH:
            primary_text = _snippet(summary, 200)
            if not primary_text.endswith("."):
                primary_text += "."
        else:
            primary_text = "This card's price tends to follow how much play it sees in competitive lists."
        source_note = "Based on play and demand."

    else:
        primary_text = summary[:200] + ("…" if len(summary) > 200 else "") if summary else ""

    # Enforce max length for primary (1–3 sentences; rarely more than 4)
    if len(primary_text) > 280:
        primary_text = _snippet(primary_text, 260) + "…"
    if follow_up_text and len(follow_up_text) > 120:
        follow_up_text = _snippet(follow_up_text, 100) + "…"

    return {
        "primary_text": primary_text.strip(),
        "follow_up_text": follow_up_text.strip() if follow_up_text else None,
        "source_note": source_note.strip() if source_note else None,
        "category": category,
        "is_filler": False,
    }


def build_insight_display_list(
    sections: list[dict[str, Any]],
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build a list of Miru-voice display dicts for the given sections.

    Filters out filler; returns only displays with primary_text.
    """
    out: list[dict[str, Any]] = []
    for section in sections:
        itype = str(section.get("insight_type") or "").strip()
        if not itype:
            continue
        display = shape_section_display(itype, section, context)
        if display.get("is_filler") or not display.get("primary_text"):
            continue
        out.append({k: v for k, v in display.items() if k != "is_filler"})
    return out
