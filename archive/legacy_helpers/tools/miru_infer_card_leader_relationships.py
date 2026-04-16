#!/usr/bin/env python3
"""
Phase 2.5: Text-based card-to-leader relationship inference (mechanical synergy signals).

Inserts into card_relationships with evidence_source text_analysis_2026_03_24.
Uses INSERT OR IGNORE so operator seed rows are never overwritten.

Run: python tools/miru_infer_card_leader_relationships.py
Run (read-only Event pool report): python tools/miru_infer_card_leader_relationships.py --pool-stats-only
Run (read-only color guard check): python tools/miru_infer_card_leader_relationships.py --color-guard-spot-check

Color guard: before scoring, skip pairs where the card is not color-compatible with the leader
(shared slash-delimited color token, case-insensitive, or card color empty / Colorless).

Event pool: Character/Stage use the SQL filter only. Events additionally require at least one of:
(1) a trait token outside the ultra-generic set (sole Straw Hat Crew / Supernovas-style tags),
(2) a capitalized proper-noun token in effect/trigger (excluding colors, card types, common rule words),
(3) any card_relationships row with evidence_source operator_knowledge_2026_03_24 for that card_id.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"

EVIDENCE_SOURCE = "text_analysis_2026_03_24"
OPERATOR_SEED_EVIDENCE = "operator_knowledge_2026_03_24"
REL_TYPE = "supports_leader"
ENTITY_TYPE = "leader"

# Trait tokens that are too broad alone to count as Event "specificity" for pool gating
# (broad line tags on otherwise generic Events — see OP01-029 / OP06-096 sweep).
_GENERIC_EVENT_TRAIT_TOKENS: frozenset[str] = frozenset(
    {
        "straw hat crew",
        "supernovas",
    }
)

# Capitalized tokens in effect text that are NOT treated as named-character / proper-noun signals.
_EXCLUDED_PROPER_LIKE: frozenset[str] = frozenset(
    {
        # Colors (incl. multi-word fragments)
        "red",
        "blue",
        "green",
        "black",
        "yellow",
        "purple",
        "orange",
        # Card types
        "leader",
        "character",
        "characters",
        "event",
        "stage",
        # Task-listed keywords
        "blocker",
        "rush",
        "banish",
        "trigger",
        "counter",
        "draw",
        # Common rules vocabulary (sentence-initial caps)
        "your",
        "you",
        "opponent",
        "the",
        "this",
        "that",
        "when",
        "if",
        "then",
        "at",
        "on",
        "all",
        "up",
        "to",
        "of",
        "and",
        "or",
        "for",
        "from",
        "with",
        "into",
        "until",
        "during",
        "after",
        "before",
        "once",
        "per",
        "any",
        "each",
        "both",
        "either",
        "neither",
        "activate",
        "main",
        "play",
        "attacking",
        "turn",
        "phase",
        "deck",
        "hand",
        "field",
        "life",
        "trash",
        "search",
        "cost",
        "power",
        "base",
        "battle",
        "attack",
        "target",
        "type",
        "cards",
        "card",
        "don",
        "rest",
        "active",
        "rested",
        "cannot",
        "may",
        "must",
        "add",
        "return",
        "give",
        "gets",
        "get",
        "has",
        "have",
        "had",
        "been",
        "one",
        "two",
        "more",
        "less",
        "than",
        "next",
        "same",
        "look",
        "reveal",
        "place",
        "bottom",
        "top",
        "declare",
        "choose",
        "select",
        "set",
        "also",
        "instead",
        "however",
        "even",
        "only",
        "other",
        "another",
        "such",
        "these",
        "those",
        "their",
        "they",
        "them",
        "its",
        "who",
        "which",
        "what",
        "where",
        "while",
        "without",
        "within",
        "between",
        "among",
        "about",
        "over",
        "under",
        "through",
        "against",
        "toward",
        "towards",
        "upon",
        "onto",
        "off",
        "out",
        "down",
        "back",
        "away",
        "here",
        "there",
        "now",
        "still",
        "just",
        "very",
        "too",
        "not",
        "nor",
        "but",
        "because",
        "although",
        "though",
        "unless",
        "whether",
        "either",
        "neither",
        "both",
        "either",
        "neither",
        "yes",
        "no",
        "non",
        "new",
        "old",
        "own",
        "total",
        "face",
        "faceup",
        "facedown",
        "active",
        "inactive",
        "legal",
        "illegal",
        "ignore",
        "ignored",
        "ignoring",
        "including",
        "included",
        "exclude",
        "excluded",
        "except",
        "excepted",
        "special",
        "normal",
        "original",
        "printed",
        "printedtext",
        "text",
        "effect",
        "effects",
        "ability",
        "abilities",
        "skill",
        "skills",
        "keyword",
        "keywords",
        "status",
        "stack",
        "resolve",
        "resolved",
        "resolving",
        "activate",
        "activated",
        "activating",
        "discard",
        "discarded",
        "discarding",
        "destroy",
        "destroyed",
        "destroying",
        "remove",
        "removed",
        "removing",
        "ko",
        "kos",
        "beat",
        "beats",
        "hit",
        "hits",
        "deal",
        "deals",
        "dealt",
        "take",
        "takes",
        "took",
        "taken",
        "lose",
        "loses",
        "lost",
        "losing",
        "gain",
        "gains",
        "gained",
        "gaining",
        "pay",
        "pays",
        "paid",
        "paying",
        "reduce",
        "reduces",
        "reduced",
        "reducing",
        "increase",
        "increases",
        "increased",
        "increasing",
        "becomes",
        "become",
        "becoming",
        "become",
        "count",
        "counts",
        "counted",
        "counting",
        "ignore",
        "ignores",
        "ignoring",
        "consider",
        "considers",
        "considered",
        "considering",
        "regard",
        "regards",
        "regarded",
        "regarding",
        "treat",
        "treats",
        "treated",
        "treating",
        "like",
        "likes",
        "liked",
        "liking",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "do",
        "does",
        "did",
        "done",
        "doing",
        "will",
        "would",
        "could",
        "should",
        "might",
        "must",
        "shall",
        "can",
        "cannot",
        "cant",
        "don",
        "doesn",
        "didn",
        "wasn",
        "weren",
        "isn",
        "aren",
        "haven",
        "hasn",
        "hadn",
        "won",
        "wouldn",
        "couldn",
        "shouldn",
        "east",
        "west",
        "south",
        "north",
        "sky",
        "island",
        "islands",
        "sea",
        "ocean",
        "world",
    }
)

# Capitalized word (not ALL-CAPS like DON) — likely name or place in card text.
_PROPER_NOUN_TOKEN_RE = re.compile(r"(?<![A-Za-z])([A-Z][a-z]{2,})(?![a-z])")

EARLY_TURN_PAT = re.compile(
    r"first\s+turn|1st\s+turn|turn\s*1|early\s+turn|"
    r"at\s+the\s+start\s+of|beginning\s+of\s+the\s+game|opening",
    re.I,
)


def _split_traits(raw: str | None) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    parts: list[str] = []
    for chunk in re.split(r"[,/]", str(raw)):
        t = chunk.strip()
        if t:
            parts.append(t)
    return parts


def _norm_trait_token_set(raw: str | None) -> set[str]:
    return {t.strip().lower() for t in _split_traits(raw) if t.strip()}


def _split_colors(raw: str | None) -> set[str]:
    if not raw or not str(raw).strip():
        return set()
    return {c.strip().lower() for c in str(raw).split("/") if c.strip()}


def _card_color_is_deck_colorless(card_color: str | None) -> bool:
    """True if card has no deck color restriction (legal in any-color leader deck per guard rules)."""
    if card_color is None:
        return True
    s = str(card_color).strip()
    if not s or s == "-":
        return True
    if s.lower() == "colorless":
        return True
    return False


def _colors_compatible(leader_color: str | None, card_color: str | None) -> bool:
    """
    Deck legality proxy: share at least one slash-delimited color token (case-insensitive),
    or card is colorless / empty / unknown color field.
    """
    if _card_color_is_deck_colorless(card_color):
        return True
    leader_tokens = _split_colors(leader_color)
    card_tokens = _split_colors(card_color)
    if not card_tokens:
        return True
    if not leader_tokens:
        return True
    return bool(leader_tokens & card_tokens)


def _parse_int_field(raw: object) -> int | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == "-":
        return None
    m = re.search(r"-?\d+", s)
    if not m:
        return None
    try:
        return int(m.group(0))
    except ValueError:
        return None


def _parse_leader_life(raw: str | None) -> int | None:
    return _parse_int_field(raw)


def _notes_join(evidence: list[str], max_len: int = 300) -> str:
    s = ", ".join(evidence)
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _load_operator_seeded_card_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT UPPER(TRIM(card_id)) AS cid
        FROM card_relationships
        WHERE evidence_source = ?
        """,
        (OPERATOR_SEED_EVIDENCE,),
    ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def _event_effect_has_named_proper_noun(effect_text: str | None, trigger_text: str | None) -> bool:
    """True if effect/trigger text contains a capitalized token not in the generic exclude list."""
    blob = f"{effect_text or ''}\n{trigger_text or ''}"
    for m in _PROPER_NOUN_TOKEN_RE.finditer(blob):
        word = m.group(1)
        if word.lower() not in _EXCLUDED_PROPER_LIKE:
            return True
    return False


def _event_has_specific_traits(traits: str | None) -> bool:
    """
    True if traits has at least one token that is not ultra-generic alone.
    Matches task: non-empty traits that tie the Event to a shell (not just line-wide tags).
    """
    tokens = [t.strip().lower() for t in _split_traits(traits) if t.strip()]
    if not tokens:
        return False
    return any(t not in _GENERIC_EVENT_TRAIT_TOKENS for t in tokens)


def _event_passes_pool(
    row: sqlite3.Row,
    operator_seeded: set[str],
) -> bool:
    """Event-specific pool gate: specific traits, proper-noun in text, or operator seed row."""
    cid = str(row["canonical_code"] or "").strip().upper()
    if cid in operator_seeded:
        return True
    if _event_has_specific_traits(row["traits"]):
        return True
    if _event_effect_has_named_proper_noun(row["effect_text"], row["trigger_text"]):
        return True
    return False


def _apply_event_pool_filter(
    conn: sqlite3.Connection,
    card_rows: list[sqlite3.Row],
) -> tuple[list[sqlite3.Row], dict[str, object]]:
    """
    Character and Stage rows pass through unchanged.
    Events are dropped unless traits / proper-noun text / operator seed.
    """
    operator_ids = _load_operator_seeded_card_ids(conn)
    events_before = [r for r in card_rows if str(r["card_type"] or "") == "Event"]
    out: list[sqlite3.Row] = []
    excluded_events: list[sqlite3.Row] = []
    retained_events: list[sqlite3.Row] = []
    for row in card_rows:
        ct = str(row["card_type"] or "")
        if ct != "Event":
            out.append(row)
            continue
        if _event_passes_pool(row, operator_ids):
            out.append(row)
            retained_events.append(row)
        else:
            excluded_events.append(row)
    meta: dict[str, object] = {
        "events_before": len(events_before),
        "events_after": len(retained_events),
        "excluded_events": excluded_events,
        "retained_events": retained_events,
        "operator_seeded_card_count": len(operator_ids),
    }
    return out, meta


def _print_event_pool_verification(meta: dict[str, object]) -> None:
    eb = int(meta["events_before"])
    ea = int(meta["events_after"])
    ex_list = meta["excluded_events"]
    rt_list = meta["retained_events"]
    print()
    print("=== Event pool filter (verification) ===")
    print(f"1. Events in pool before Event-specific filter: {eb}")
    print(f"2. Events in pool after Event-specific filter:  {ea}")
    print(f"   Events excluded by filter: {eb - ea}")
    print(
        f"   (Operator-seeded card_ids loaded: {meta.get('operator_seeded_card_count', 0)} "
        f"from {OPERATOR_SEED_EVIDENCE})"
    )
    print()

    def _preview(row: sqlite3.Row, n: int = 80) -> str:
        t = str(row["effect_text"] or "").replace("\n", " ").strip()
        return (t[:n] + "…") if len(t) > n else t

    print("3. Example Events EXCLUDED (up to 10) — card_id | card_name | traits | effect…")
    for row in sorted(ex_list, key=lambda r: str(r["canonical_code"]))[:10]:
        print(
            f"   {row['canonical_code']} | {row['card_name']} | {repr(row['traits'])} | {_preview(row)}"
        )

    print()
    print("4. Example Events RETAINED (up to 10) — card_id | card_name | traits | effect…")
    for row in sorted(rt_list, key=lambda r: str(r["canonical_code"]))[:10]:
        print(
            f"   {row['canonical_code']} | {row['card_name']} | {repr(row['traits'])} | {_preview(row)}"
        )

    print()
    codes_ex = {str(r["canonical_code"]).strip().upper() for r in ex_list}
    print(f"5. OP01-029 excluded: {'YES' if 'OP01-029' in codes_ex else 'NO'}")
    print(f"   OP06-096 excluded: {'YES' if 'OP06-096' in codes_ex else 'NO'}")
    print()
    print("6. Character / Stage filtering: unchanged (only Event rows gated).")
    print()


def _score_pair(
    leader: sqlite3.Row,
    card: sqlite3.Row,
) -> tuple[int, list[str]]:
    """Return (score, evidence strings)."""
    score = 0
    evidence: list[str] = []

    leader_code = str(leader["canonical_code"] or "").strip().upper()
    card_code = str(card["canonical_code"] or "").strip().upper()
    if card_code == leader_code:
        return 0, []

    leff = str(leader["effect_text"] or "")
    ltrig = str(leader["trigger_text"] or "")
    leader_full = f"{leff}\n{ltrig}"

    ceff = str(card["effect_text"] or "")
    ctrig = str(card["trigger_text"] or "")
    card_full = f"{ceff}\n{ctrig}"

    lt_set = _norm_trait_token_set(leader["traits"])
    ct_set = _norm_trait_token_set(card["traits"])
    trait_overlap = lt_set & ct_set
    if not trait_overlap:
        l_blob = str(leader["traits"] or "").lower()
        for t in _split_traits(card["traits"]):
            tl = t.strip().lower()
            if len(tl) >= 3 and tl in l_blob:
                trait_overlap = {tl}
                break
    if trait_overlap:
        display = sorted(trait_overlap)[0]
        if len(display) > 80:
            display = display[:77] + "..."
        score += 3
        evidence.append(f"trait match: {display}")

    # Color match
    lc = _split_colors(leader["color"])
    cc = _split_colors(card["color"])
    color_hit = sorted(lc & cc) if lc and cc else []
    if color_hit:
        score += 2
        evidence.append(f"color match: {color_hit[0]}")

    # {Trait} type in leader — card has trait
    for m in re.finditer(r"\{([^}]+)\}\s*type", leader_full, re.I):
        inner = m.group(1).strip()
        if not inner:
            continue
        inner_l = inner.lower()
        card_trait_blob = str(card["traits"] or "").lower()
        if inner_l and inner_l in card_trait_blob:
            score += 2
            evidence.append(f"leader requires {inner} type, card has that trait")
            break

    # Power thresholds (parse card power)
    pwr = _parse_int_field(card["power"])
    if pwr is not None:
        if re.search(r"6000\s*base\s*power\s*or\s*more", leader_full, re.I):
            if pwr >= 6000:
                score += 2
                evidence.append("leader requires 6000+ power, card meets threshold")
        if re.search(r"5000\s*base\s*power\s*or\s*more", leader_full, re.I):
            if pwr >= 5000:
                score += 1
                evidence.append("leader requires 5000+ power, card meets threshold")

    # DON!! x1 in leader + DON!! in card
    if re.search(r"DON!!\s*x\s*1", leader_full, re.I) and re.search(r"DON!!", card_full, re.I):
        score += 1
        evidence.append("leader has DON!! condition, card interacts with DON!!")

    if re.search(r"\bdraw\b", leader_full, re.I) and re.search(r"\bdraw\b", card_full, re.I):
        score += 1
        evidence.append("both leader and card have draw effects")

    if re.search(r"\bLife\b", leader_full, re.I) and re.search(r"\bLife\b", card_full, re.I):
        score += 1
        evidence.append("both leader and card interact with Life cards")

    if re.search(r"\btrash\b", leader_full, re.I) and re.search(r"\btrash\b", card_full, re.I):
        score += 1
        evidence.append("both leader and card interact with trash")

    if re.search(r"K\.O\.", leader_full) and re.search(r"K\.O\.", card_full):
        score += 1
        evidence.append("both leader and card reference K.O.")

    # Blocker + low-life leader
    llife = _parse_leader_life(leader["life"])
    if llife is not None and llife <= 3:
        if "[Blocker]" in ceff or "[Blocker]" in ctrig or "[Blocker]" in card_full:
            score += 1
            evidence.append("blocker supports low-life leader")

    # Low-cost + aggressive / early leader
    cost = _parse_int_field(card["cost"])
    if cost is not None and cost <= 2 and EARLY_TURN_PAT.search(leader_full):
        score += 1
        evidence.append("low-cost card fits aggressive leader curve")

    if str(card["trigger_text"] or "").strip() and re.search(r"\bLife\b", leader_full, re.I):
        score += 1
        evidence.append("trigger card fits life-based leader mechanic")

    return score, evidence


def _confidence_from_score(score: int) -> str | None:
    if score >= 6:
        return "high"
    if score >= 4:
        return "medium"
    if score >= 2:
        return "low"
    return None


def _run_color_guard_spot_check(conn: sqlite3.Connection) -> None:
    """Dry-run: verify color guard on known mismatch vs valid Yellow + Supernovas pair."""
    leader_code = "OP10-099"
    leader = conn.execute(
        """
        SELECT canonical_code, card_name, color, traits
        FROM cards WHERE canonical_code = ?
        """,
        (leader_code,),
    ).fetchone()
    if not leader:
        print(f"FAILED: leader {leader_code} not found in cards", file=sys.stderr)
        return
    lc = str(leader["color"] or "")
    print("=== Color guard spot check (dry-run, no scoring) ===")
    print(f"Leader {leader_code} ({leader['card_name']}): color={repr(lc)}")
    print()

    checks: list[tuple[str, str, str]] = [
        ("ST02-017", "SKIPPED", "sweep example: must not link to Yellow leader (DB color vs leader)"),
        ("ST24-002", "SKIPPED", "Green vs Yellow leader"),
        ("OP01-025", "SKIPPED", "Red vs Yellow leader"),
        ("OP10-101", "PASSED", "Yellow + Supernovas — shares Yellow with leader"),
    ]
    for card_code, expect, note in checks:
        row = conn.execute(
            "SELECT canonical_code, card_name, color FROM cards WHERE canonical_code = ?",
            (card_code,),
        ).fetchone()
        if not row:
            print(f"  {card_code}: MISSING from cards table")
            continue
        cc = str(row["color"] or "")
        ok = _colors_compatible(leader["color"], row["color"])
        # SKIPPED by color guard = incompatible = not ok
        if expect == "SKIPPED":
            verdict = "SKIPPED (color)" if not ok else "UNEXPECTED_PASS"
        else:
            verdict = "PASSED (color)" if ok else "UNEXPECTED_SKIP"
        print(
            f"  {card_code} ({row['card_name']}) card_color={repr(cc)} | "
            f"compatible={ok} | {verdict} — {note}"
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Card–leader relationship inference (Phase 2.5).")
    parser.add_argument(
        "--pool-stats-only",
        action="store_true",
        help="Read-only: print Event pool before/after filter and exit (no scoring, no DB writes).",
    )
    parser.add_argument(
        "--color-guard-spot-check",
        action="store_true",
        help="Read-only: verify color compatibility on four fixed card/leader pairs; exit.",
    )
    args = parser.parse_args()

    if not DB_PATH.is_file():
        print(f"FAILED: {DB_PATH} not found", file=sys.stderr)
        return 1

    if args.color_guard_spot_check:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        _run_color_guard_spot_check(conn)
        conn.close()
        return 0

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    leaders = conn.execute(
        """
        SELECT canonical_code, card_name, color, traits, life, effect_text, trigger_text
        FROM cards
        WHERE card_type = 'Leader'
        """
    ).fetchall()

    cards_raw = conn.execute(
        """
        SELECT canonical_code, card_name, color, traits, cost, power,
               counter, effect_text, trigger_text, card_type
        FROM cards
        WHERE card_type IN ('Character', 'Event', 'Stage')
          AND (is_variant = 0 OR is_variant IS NULL)
          AND card_name != ''
          AND TRIM(card_name) != ''
          AND UPPER(TRIM(card_name)) != UPPER(TRIM(canonical_code))
        """
    ).fetchall()

    cards, event_meta = _apply_event_pool_filter(conn, list(cards_raw))
    _print_event_pool_verification(event_meta)

    if args.pool_stats_only:
        print("7. Inference engine not run (--pool-stats-only). No database writes.")
        conn.close()
        return 0

    insert_sql = """
    INSERT OR IGNORE INTO card_relationships (
        card_id, related_entity, related_entity_type, relationship_type,
        evidence_source, confidence, status, notes, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """

    inserted = 0
    ignored_duplicate = 0
    skipped_low_score = 0
    skipped_by_color = 0
    inserted_by_conf: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

    pairs_total = 0
    pairs_scored = 0
    for leader in leaders:
        lc = str(leader["canonical_code"] or "").strip().upper()
        for card in cards:
            pairs_total += 1
            if not _colors_compatible(leader["color"], card["color"]):
                skipped_by_color += 1
                continue
            pairs_scored += 1
            sc, ev = _score_pair(leader, card)
            if sc < 2:
                skipped_low_score += 1
                continue
            conf = _confidence_from_score(sc)
            if not conf:
                skipped_low_score += 1
                continue
            notes = _notes_join(ev)
            cur = conn.execute(
                insert_sql,
                (
                    str(card["canonical_code"]).strip().upper(),
                    lc,
                    ENTITY_TYPE,
                    REL_TYPE,
                    EVIDENCE_SOURCE,
                    conf,
                    "inferred",
                    notes,
                ),
            )
            if cur.rowcount == 1:
                inserted += 1
                inserted_by_conf[conf] = inserted_by_conf.get(conf, 0) + 1
            else:
                ignored_duplicate += 1

    conn.commit()

    by_conf = {k: 0 for k in ("high", "medium", "low")}
    for (conf, n) in conn.execute(
        """
        SELECT confidence, COUNT(*) FROM card_relationships
        WHERE evidence_source = ? AND relationship_type = ?
        GROUP BY confidence
        """,
        (EVIDENCE_SOURCE, REL_TYPE),
    ).fetchall():
        if conf in by_conf:
            by_conf[conf] = int(n)

    per_card = {
        r[0]: int(r[1])
        for r in conn.execute(
            """
            SELECT card_id, COUNT(*) AS n FROM card_relationships
            WHERE evidence_source = ? AND relationship_type = ?
            GROUP BY card_id ORDER BY n DESC LIMIT 10
            """,
            (EVIDENCE_SOURCE, REL_TYPE),
        )
    }
    per_leader = {
        r[0]: int(r[1])
        for r in conn.execute(
            """
            SELECT related_entity, COUNT(*) AS n FROM card_relationships
            WHERE evidence_source = ? AND relationship_type = ?
              AND related_entity_type = ?
            GROUP BY related_entity ORDER BY n DESC LIMIT 5
            """,
            (EVIDENCE_SOURCE, REL_TYPE, ENTITY_TYPE),
        )
    }

    eb = int(event_meta["events_before"])
    ea = int(event_meta["events_after"])

    print("=== miru_infer_card_leader_relationships (Phase 2.5) ===")
    print(f"Leaders processed: {len(leaders)}")
    print(f"Cards in candidate pool (after Event filter): {len(cards)}")
    print(f"Events excluded by generic Event filter: {eb - ea}")
    print(f"Leader×card pair iterations (total): {pairs_total}")
    print(f"Pairs skipped by color incompatibility: {skipped_by_color}")
    print(f"Pairs scored (passed color guard): {pairs_scored}")
    print(f"Relationships inserted this run: {inserted}")
    print(f"Inserted this run by confidence: high={inserted_by_conf['high']} "
          f"medium={inserted_by_conf['medium']} low={inserted_by_conf['low']}")
    print(f"Skipped (score < 2, after color guard): {skipped_low_score}")
    print(f"Skipped (INSERT OR IGNORE, duplicate key): {ignored_duplicate}")
    print()
    print("Confidence breakdown (all rows, this evidence source):")
    print(f"  high:   {by_conf['high']}")
    print(f"  medium: {by_conf['medium']}")
    print(f"  low:    {by_conf['low']}")
    print()

    print("Top 10 cards by inferred leader relationship count:")
    for code, n in per_card.items():
        print(f"  {code}: {n}")

    print()
    print("Top 5 leaders by inferred card relationship count:")
    for code, n in per_leader.items():
        print(f"  {code}: {n}")

    print()
    print("--- Spot check: OP13-002 (Portgas.D.Ace) — top 10 high confidence ---")
    spot = conn.execute(
        """
        SELECT cr.card_id, c.card_name, cr.confidence,
               substr(cr.notes, 1, 220) AS notes_preview
        FROM card_relationships cr
        JOIN cards c ON c.canonical_code = cr.card_id
        WHERE cr.related_entity = 'OP13-002'
          AND cr.evidence_source = ?
          AND cr.confidence = 'high'
        ORDER BY cr.card_id
        LIMIT 10
        """,
        (EVIDENCE_SOURCE,),
    ).fetchall()
    for row in spot:
        print(
            f"  {row['card_id']} | {row['card_name']} | {row['notes_preview']}"
        )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
