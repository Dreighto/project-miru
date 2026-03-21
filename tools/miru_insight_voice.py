"""
Player-facing voice for Miru card insights: short, readable, non-technical.

Used by MiruDossierStore insight generation and miru_project_sync strict candidates.
Does not alter schemas, thresholds, or governance — text shaping only.
"""

from __future__ import annotations

import re
from typing import Any

_MAX_SENTENCES = 2
_MAX_CHARS = 340


def clamp_sentences(text: str, *, max_sentences: int = _MAX_SENTENCES, max_chars: int = _MAX_CHARS) -> str:
    """Keep at most max_sentences sentences and a hard char cap."""
    text = (text or "").strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    parts = [p.strip() for p in parts if p.strip()]
    out = " ".join(parts[:max_sentences]).strip()
    if len(out) > max_chars:
        out = out[: max_chars - 1].rstrip() + "…"
    return out


def _clean(s: Any) -> str:
    return str(s or "").strip()


def _role_phrase(role_label: str, gameplay_role: str) -> str:
    rl = (role_label or "").strip().lower()
    gr = (gameplay_role or "").strip().lower()
    pick = rl or gr
    if pick == "core":
        return "a core piece"
    if pick == "tech":
        return "a tech option"
    if pick in {"staple", "engine"}:
        return f"a {pick}"
    if pick:
        return f"a {pick} piece"
    return "a role player"


def voice_usage_line(dossier: dict[str, Any]) -> str | None:
    """Deck / leader backed — one or two short sentences, no pipeline jargon."""
    name = _clean(dossier.get("name")) or _clean(dossier.get("card_id"))
    tls = list(dossier.get("top_leaders_used_in") or [])
    gr = _clean(dossier.get("gameplay_role")).lower()

    if not tls:
        return None

    best = tls[0]
    lc = _clean(best.get("leader_code")).upper()
    if not lc:
        return None

    pct = float(best.get("usage_percent") or 0.0)
    copies = float(best.get("avg_copies") or 0.0)
    rl = _clean(best.get("role_label")).lower()
    role_what = _role_phrase(rl, gr)

    # Copy / prevalence phrasing (avoid raw percentages in copy)
    if pct >= 0.98:
        copies_bit = " It’s almost always played at four copies."
    elif pct >= 0.7:
        copies_bit = f" Lists often run about {round(copies, 1)} copies."
    elif pct >= 0.35:
        copies_bit = " You’ll see it in a solid chunk of those lists."
    elif pct > 0.08:
        copies_bit = " It shows up, but not in every list."
    else:
        copies_bit = " It’s a niche include — not something you see everywhere."

    s1 = f"{name} is {role_what} in {lc} lists.{copies_bit}"
    return clamp_sentences(s1)


def voice_meta_line(dossier: dict[str, Any]) -> str | None:
    """When deck rows are thin but a meta signal exists — no 'stored score' wording."""
    name = _clean(dossier.get("name")) or _clean(dossier.get("card_id"))
    tls = list(dossier.get("top_leaders_used_in") or [])
    mrs = dossier.get("meta_relevance_score")
    if mrs in (None, ""):
        return None
    try:
        mrf = float(mrs)
    except (TypeError, ValueError):
        return None
    if mrf <= 0:
        return None
    if mrf < 0.25:
        return None
    lc = _clean(tls[0].get("leader_code")).upper() if tls else ""
    if lc:
        return clamp_sentences(
            f"{name} lines up with how {lc} lists are built right now — it’s pulling real weight in that lane."
        )
    return clamp_sentences(f"{name} is showing up often enough that it’s worth respecting in deckbuilding.")


def voice_ruling_line(dossier: dict[str, Any]) -> str | None:
    raw = _clean(dossier.get("rulings_summary"))
    if not raw:
        return None
    # Drop common upstream prefixes for a cleaner read
    for prefix in (
        "Official rulings currently note:",
        "Official rulings note:",
        "Official ruling:",
    ):
        if raw.lower().startswith(prefix.lower()):
            raw = raw[len(prefix) :].strip()
    out = clamp_sentences(raw, max_chars=280)
    if not out:
        return None
    # Prefer leading with the card-specific hook if present
    if not out[0].isupper():
        out = out[0].upper() + out[1:] if len(out) > 1 else out.upper()
    return out


def voice_legality_line(dossier: dict[str, Any]) -> str | None:
    note = _clean(dossier.get("legality_note"))
    if not note:
        return None
    low = note.lower()
    if "banned" in low:
        return clamp_sentences("Banned for official Standard play right now — double-check your event’s format.")
    if "restricted" in low:
        return clamp_sentences("Restricted — check the latest official list before you register.")
    return clamp_sentences(note, max_chars=260)


def voice_price_line(dossier: dict[str, Any]) -> str | None:
    name = _clean(dossier.get("name")) or _clean(dossier.get("card_id"))
    pl = dossier.get("price_low")
    if pl in (None, ""):
        return None
    try:
        v = float(pl)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return clamp_sentences(f"Last watch price for {name} was about ${v:.2f} — treat it as a snapshot, not a quote.")


def build_insight_display_list(ranked_sections: Any, voice_context: dict[str, Any]) -> list[Any]:
    """Phase 16 optional hook — dossier summary path; return [] to use defaults."""
    return []


def _synthetic_tls_from_deck_summary(dossier: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Infer leader usage from deck_usage_summary when top_leaders_used_in is empty."""
    du = _clean(dossier.get("deck_usage_summary"))
    if len(du) < 12:
        return None
    codes = re.findall(r"\b([A-Z]{2,4}\d{2}-\d{3})\b", du)
    if not codes:
        return None
    role = "core"
    if "tech" in du.lower():
        role = "tech"
    um = re.search(r"(\d+)%\s*inclusion", du, re.I)
    pct = min(float(um.group(1)) / 100.0, 1.0) if um else 0.55
    am = re.search(r"(\d+\.?\d*)\s*avg copies", du, re.I)
    avg = float(am.group(1)) if am else 3.0
    return [{"leader_code": codes[0], "usage_percent": pct, "avg_copies": avg, "role_label": role}]


def dominant_insight_type(used_sections: list[str]) -> str:
    """Map section tags to storefront insight_type (priority handled upstream)."""
    s = set(used_sections or [])
    if s & {"usage_meta", "top_leaders"}:
        return "usage"
    if "usage_meta" in s:
        return "meta"
    if "gameplay_role" in s:
        return "strength"
    if "rulings" in s:
        return "ruling"
    if "legality" in s:
        return "ruling"
    if "market" in s:
        return "price"
    return "meta"


def build_single_voice_insight(dossier: dict[str, Any]) -> tuple[str, list[str]] | None:
    """
    Pick one dominant insight (voice-only shaping).

    Priority:
    1) Deck / leader usage
    2) Official ruling / errata text (before abstract meta so errata-first cards read clearly)
    3) Meta signal without deck rows
    4) Format legality
    5) Price snapshot
    6) Role-only (strength-style)

    Returns (text, used_sections) or None if nothing to say (caller may fail-close).
    """
    d = dict(dossier)
    if not d.get("top_leaders_used_in") and d.get("deck_usage_summary"):
        syn = _synthetic_tls_from_deck_summary(d)
        if syn:
            d["top_leaders_used_in"] = syn

    tls = list(d.get("top_leaders_used_in") or [])
    du = _clean(d.get("deck_usage_summary"))
    rs = _clean(d.get("rulings_summary"))

    # 1) Usage / deck
    if tls or (du and len(du) > 12):
        line = voice_usage_line(d)
        if line:
            sections = ["usage_meta"]
            if tls:
                sections.append("top_leaders")
            return line, list(dict.fromkeys(sections))

    # 2) Rulings / errata (short, player-readable)
    if rs:
        rul = voice_ruling_line(d)
        if rul:
            return rul, ["rulings"]

    # 3) Meta (thin deck context)
    meta = voice_meta_line(d)
    if meta:
        return meta, ["usage_meta"]

    # 4) Legality (short)
    leg = voice_legality_line(d)
    if leg:
        return leg, ["legality"]

    # 5) Price
    pr = voice_price_line(d)
    if pr:
        return pr, ["market"]

    # 6) Role-only fallback (strength-style)
    gr = _clean(d.get("gameplay_role")).lower()
    name = _clean(d.get("name")) or _clean(d.get("card_id"))
    if gr and name:
        return clamp_sentences(
            f"{name} reads as {gr} in the lists we’ve got on file — build with that plan in mind."
        ), ["gameplay_role"]

    return None
