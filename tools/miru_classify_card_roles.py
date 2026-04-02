#!/usr/bin/env python3
"""
Phase 1: Card role classification from verified card text only (card_catalog.db).

Run: python tools/miru_classify_card_roles.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"

CREATE_CARD_ROLES_SQL = """
CREATE TABLE IF NOT EXISTS card_roles (
    card_id TEXT NOT NULL,
    role TEXT NOT NULL,
    role_confidence TEXT NOT NULL CHECK(
        role_confidence IN ('low', 'medium', 'high', 'verified')
    ),
    evidence TEXT NOT NULL,
    classification_source TEXT NOT NULL DEFAULT 'text_analysis',
    status TEXT NOT NULL DEFAULT 'inferred' CHECK(
        status IN ('inferred', 'corroborated', 'verified', 'rejected')
    ),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (card_id, role)
);
"""

CONF_RANK = {"low": 1, "medium": 2, "high": 3, "verified": 4}
ALL_ROLES = (
    "searcher",
    "blocker",
    "removal",
    "finisher",
    "extender",
    "recursion_piece",
    "stabilizer",
    "value_engine",
    "tempo_piece",
    "life_manipulation",
    "hand_control",
    "don_acceleration",
    "don_reduction",
)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _parse_cost(cost: object) -> int | None:
    if cost is None:
        return None
    try:
        return int(cost)
    except (TypeError, ValueError):
        return None


def _full_text(effect_text: str, trigger_text: str) -> str:
    e = (effect_text or "").strip()
    tr = (trigger_text or "").strip()
    if e and tr:
        return e + "\n" + tr
    return e or tr


@dataclass(frozen=True)
class RoleHit:
    role: str
    confidence: str
    evidence: str


def _detect_blocker(effect_text: str) -> RoleHit | None:
    """Blocker role only when effect_text matches the [Blocker] keyword (not block_icon — that is counter value)."""
    et = effect_text or ""
    if not et.strip():
        return None
    et_lo = et.lower()
    if "[blocker]" in et_lo or "blocker" in et_lo:
        return RoleHit("blocker", "high", "effect_text contains [Blocker] keyword")
    return None


def _detect_searcher(t: str) -> RoleHit | None:
    has_look_top = "look at the top" in t
    has_add_up = "add up to" in t
    has_search = re.search(r"\bsearch\b", t) is not None
    has_reveal = "reveal" in t
    if not (has_look_top or has_add_up or has_search or has_reveal):
        return None
    adds_to_hand_or_field = (
        "hand" in t
        or "field" in t
        or "play" in t
        or "character" in t
        or "stage" in t
        or "event" in t
        or "leader" in t
    )
    if has_look_top or has_add_up:
        return RoleHit("searcher", "high", "deck/top access pattern (look at the top / add up to)")
    if has_search and adds_to_hand_or_field:
        return RoleHit("searcher", "high", "effect_text contains search and adds to hand/field context")
    if has_reveal and adds_to_hand_or_field:
        return RoleHit("searcher", "medium", "effect_text contains reveal with add/play/hand/field context")
    if has_search or has_reveal:
        return RoleHit("searcher", "medium", "effect_text contains search or reveal (interpreted as card access)")
    return None


def _opponent_context(t: str) -> bool:
    return bool(
        re.search(r"opponent|your opponent|opposing|rival", t)
        or "opponent's" in t
        or "opponents" in t
    )


def _detect_removal(t: str) -> RoleHit | None:
    opp = _opponent_context(t)
    ko = re.search(r"k\.?\s*o\.?", t) is not None or "ko " in t
    rtn = "return" in t and opp
    trash = "trash" in t and opp
    if (ko or rtn or trash) and opp:
        return RoleHit("removal", "high", "K.O./return/trash pattern targeting opponent context")
    if re.search(r"-\s*\d{3,4}\s*(power|pow)?", t) and opp:
        return RoleHit("removal", "medium", "power reduction on opponent character")
    if ko and "character" in t:
        return RoleHit("removal", "medium", "K.O. on character (opponent implied)")
    return None


def _detect_finisher(t: str, cost: int | None) -> RoleHit | None:
    c = cost if cost is not None else -1
    if c >= 8:
        return RoleHit("finisher", "low", f"cost >= 8 (cost={c})")
    if "rush" in t and c >= 6:
        return RoleHit("finisher", "medium", f"effect_text contains Rush and cost >= 6 (cost={c})")
    if "cannot be k.o" in t or "cannot be removed" in t:
        return RoleHit("finisher", "high", "effect_text contains cannot be K.O.'d / cannot be removed")
    return None


def _detect_extender(t: str) -> RoleHit | None:
    p1 = "play up to 1" in t or "play up to one" in t
    p2 = "put into play" in t
    if not (p1 or p2):
        return None
    from_ht = "hand" in t or "trash" in t
    cost_ge_3 = bool(re.search(r"cost\s*[4-9]|cost\s*\d{2,}", t)) or bool(re.search(r"\b[3-9]\s*cost", t))
    if from_ht and cost_ge_3:
        return RoleHit("extender", "medium", "play up to / put into play from hand or trash with meaningful cost cue")
    if from_ht:
        return RoleHit("extender", "low", "play up to / put into play from hand or trash")
    return RoleHit("extender", "low", "play up to / put into play pattern")


def _detect_recursion(t: str) -> RoleHit | None:
    if "from your trash" not in t and "from the trash" not in t:
        return None
    if "hand" in t or "field" in t or "play" in t or "return" in t:
        return RoleHit("recursion_piece", "high", "from trash to hand/field/play pattern")
    return RoleHit("recursion_piece", "medium", "trash recursion text present")


def _detect_stabilizer(t: str) -> RoleHit | None:
    if "life" not in t:
        return None
    if "add" in t and "life" in t:
        return RoleHit("stabilizer", "medium", "add to Life / Life cards (defensive stabilization)")
    if "your life cards" in t:
        return RoleHit("stabilizer", "medium", "references your Life cards defensively")
    return None


def _detect_value_engine(t: str) -> RoleHit | None:
    if "draw" not in t:
        return None
    ongoing = bool(
        re.search(
            r"(when|whenever|at the start of|during your turn|once per turn|end of your turn|beginning of)",
            t,
        )
    )
    draws = len(re.findall(r"\bdraw\b", t))
    if ongoing:
        return RoleHit("value_engine", "high", "draw effect with repeatable/when/at trigger")
    if draws >= 2:
        return RoleHit("value_engine", "medium", "multiple draw references (repeatable value)")
    return None


def _detect_tempo_piece(t: str) -> RoleHit | None:
    if "active don" in t or "as active" in t and "don" in t:
        return RoleHit("tempo_piece", "high", "active DON!! / set DON!! as active")
    if "rested don" in t and ("leader" in t or "character" in t):
        return RoleHit("tempo_piece", "high", "rested DON!! to Leader or Character")
    if "don!!" in t and ("active" in t or "rest" in t):
        return RoleHit("tempo_piece", "medium", "DON!! tempo manipulation")
    return None


def _detect_life_manipulation(t: str) -> RoleHit | None:
    if "life card" not in t and "life cards" not in t:
        return None
    move = bool(re.search(r"add|remove|place|swap|send|return", t))
    if move:
        return RoleHit("life_manipulation", "high", "Life card(s) move/add/remove pattern")
    return RoleHit("life_manipulation", "medium", "Life card text with manipulation context")


def _detect_hand_control(t: str) -> RoleHit | None:
    if "opponent's hand" in t or "opponents hand" in t:
        return RoleHit("hand_control", "high", "opponent's hand referenced")
    if "discard" in t and "opponent" in t:
        return RoleHit("hand_control", "medium", "discard from opponent context")
    if "trash" in t and "hand" in t and "opponent" in t:
        return RoleHit("hand_control", "high", "trash from opponent hand context")
    return None


def _detect_don_acceleration(t: str) -> RoleHit | None:
    if "don!! deck" in t and ("add" in t or "place" in t):
        return RoleHit("don_acceleration", "high", "DON!! deck adds DON!!")
    if "add" in t and "don!!" in t and ("field" in t or "character" in t or "leader" in t):
        return RoleHit("don_acceleration", "medium", "add DON!! to field/Leader/Character")
    return None


def _detect_don_reduction(t: str) -> RoleHit | None:
    if re.search(r"don!!\s*-\s*\d", t) or re.search(r"don!!\s*-\s*\(", t):
        return RoleHit("don_reduction", "high", "DON!! - cost reduction on activate")
    if "return" in t and "don!!" in t and ("deck" in t or "don!! deck" in t):
        return RoleHit("don_reduction", "medium", "return DON!! to DON!! deck")
    return None


def classify_card(
    *,
    effect_text: str,
    trigger_text: str,
    cost: object,
) -> list[RoleHit]:
    raw = _full_text(effect_text, trigger_text)
    t = _norm(raw)
    if not t.strip():
        return []

    hits: list[RoleHit] = []
    cost_i = _parse_cost(cost)

    for fn in (
        lambda: _detect_blocker(effect_text),
        lambda: _detect_searcher(t),
        lambda: _detect_removal(t),
        lambda: _detect_finisher(t, cost_i),
        lambda: _detect_extender(t),
        lambda: _detect_recursion(t),
        lambda: _detect_stabilizer(t),
        lambda: _detect_value_engine(t),
        lambda: _detect_tempo_piece(t),
        lambda: _detect_life_manipulation(t),
        lambda: _detect_hand_control(t),
        lambda: _detect_don_acceleration(t),
        lambda: _detect_don_reduction(t),
    ):
        h = fn()
        if h:
            hits.append(h)

    seen: set[str] = set()
    out: list[RoleHit] = []
    for h in hits:
        if h.role not in seen:
            seen.add(h.role)
            out.append(h)
    return out


def upsert_role(
    conn: sqlite3.Connection,
    card_id: str,
    hit: RoleHit,
) -> str:
    """Returns 'insert', 'update', or 'skip'."""
    row = conn.execute(
        "SELECT role_confidence FROM card_roles WHERE card_id = ? AND role = ?",
        (card_id, hit.role),
    ).fetchone()
    new_r = CONF_RANK[hit.confidence]
    now = conn.execute("SELECT datetime('now')").fetchone()[0]
    if row is None:
        conn.execute(
            """
            INSERT INTO card_roles (
                card_id, role, role_confidence, evidence,
                classification_source, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'text_analysis', 'inferred', ?, ?)
            """,
            (card_id, hit.role, hit.confidence, hit.evidence, now, now),
        )
        return "insert"
    old_r = CONF_RANK.get(str(row["role_confidence"]), 0)
    if new_r > old_r:
        conn.execute(
            """
            UPDATE card_roles SET
                role_confidence = ?,
                evidence = ?,
                classification_source = 'text_analysis',
                status = 'inferred',
                updated_at = ?
            WHERE card_id = ? AND role = ?
            """,
            (hit.confidence, hit.evidence, now, card_id, hit.role),
        )
        return "update"
    return "skip"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: database not found: {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(CREATE_CARD_ROLES_SQL)
    conn.commit()

    rows = conn.execute(
        """
        SELECT canonical_code, card_name, effect_text, trigger_text, card_type,
               cost, power, counter
        FROM cards
        ORDER BY canonical_code
        """
    ).fetchall()

    processed = 0
    inserts = 0
    updates = 0
    skips = 0
    assignments_this_run = 0
    zero_role_cards: list[str] = []

    for row in rows:
        processed += 1
        cid = str(row["canonical_code"] or "").strip()
        hits = classify_card(
            effect_text=str(row["effect_text"] or ""),
            trigger_text=str(row["trigger_text"] or ""),
            cost=row["cost"],
        )
        if not hits:
            zero_role_cards.append(cid)
        for h in hits:
            action = upsert_role(conn, cid, h)
            if action == "insert":
                inserts += 1
                assignments_this_run += 1
            elif action == "update":
                updates += 1
                assignments_this_run += 1
            else:
                skips += 1

    conn.commit()

    total_assignments = conn.execute("SELECT COUNT(*) AS n FROM card_roles").fetchone()["n"]
    by_role = Counter(
        str(r["role"])
        for r in conn.execute("SELECT role FROM card_roles").fetchall()
    )
    top5 = conn.execute(
        """
        SELECT cr.card_id, MAX(c.card_name) AS card_name, COUNT(*) AS cnt
        FROM card_roles cr
        JOIN cards c ON c.canonical_code = cr.card_id
        GROUP BY cr.card_id
        ORDER BY cnt DESC, cr.card_id
        LIMIT 5
        """
    ).fetchall()

    samples = conn.execute(
        """
        SELECT cr.card_id, c.card_name, cr.role, cr.role_confidence, cr.evidence
        FROM card_roles cr
        JOIN cards c ON c.canonical_code = cr.card_id
        ORDER BY cr.card_id, cr.role
        LIMIT 5
        """
    ).fetchall()

    print("=== miru_classify_card_roles.py ===")
    print(f"Total cards processed: {processed}")
    print(f"Role assignments written this run (insert + update): {assignments_this_run}")
    print(f"This run — inserts: {inserts}, updates: {updates}, upsert-skips (same/lower conf): {skips}")
    print(f"Total role rows in card_roles (after run): {total_assignments}")
    print()
    print("Breakdown by role (card count per role):")
    for role in ALL_ROLES:
        print(f"  {role}: {by_role.get(role, 0)}")
    print()
    print("Top 5 cards by number of roles:")
    for r in top5:
        nm = (r["card_name"] or "")[:48]
        print(f"  {r['card_id']} — {nm}: {r['cnt']} roles")
    print()
    print(f"Cards with zero roles assigned: {len(zero_role_cards)}")
    if zero_role_cards and len(zero_role_cards) <= 30:
        print("  (sample):", ", ".join(zero_role_cards[:30]))
    elif zero_role_cards:
        print("  (first 20):", ", ".join(zero_role_cards[:20]), "...")
    print()
    print("Sample role assignments (5 rows):")
    for s in samples:
        print(
            f"  {s['card_id']} | {s['card_name'][:40]!s} | {s['role']} | "
            f"{s['role_confidence']} | {s['evidence'][:80]}"
        )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
