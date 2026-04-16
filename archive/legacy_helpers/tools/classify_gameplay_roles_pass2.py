"""classify_gameplay_roles — PASS 2 edge case classifier.

Pass 1 (tools/classify_gameplay_roles.py) covered the cards with clear,
text-derived signals. This pass 2 handles the remaining edge cases:
promo cards, older-set characters with minimal or no effect text, and
cards whose roles must be inferred from anatomy (power, cost, counter,
card_type) rather than explicit wording.

Pass 2 rule set (permissive — prefers 'low' confidence and ends with a
card-type fallback so every remaining card gets at least one role):

    1. Any card with "[Counter]" keyword in effect_text -> stabilizer (high).
    2. +1000/+2000/+3000 power boost to own cards -> stabilizer
       (medium if early in text, low if later in text).
    3. Characters with power >= 8000 and no/minimal effect -> finisher (low).
    4. Characters with power <= 2000 and a counter value -> stabilizer (low).
    5. Characters with cost 0 or 1 and any effect -> tempo_piece (low);
       if the effect reduces cost or plays other cards -> extender (low).
    6. Characters or Events mentioning "opponent's turn" -> stabilizer (medium).
    7. Events (without [Counter]) that affect board state get the closest
       matching role at medium confidence.
    8. Promo cards (P-###) with empty effect_text -> card-type-based low
       with promo-specific evidence.
    9. Starter Deck (ST###) cards share the generic rule set — already
       handled by rules 1-7 with low confidence thresholds.
   10. Leaders with no classifiable effect -> finisher (low).
   11. Fallback by card_type for any card still unclassified:
       Character/Event -> stabilizer, Stage -> value_engine, Leader -> finisher.
       All at low confidence.

DB writes go through the same batched INSERT OR IGNORE pattern as pass 1:
one transaction per batch of 100, rollback on error, continue to next batch.
"""

import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Tuple

DB_PATH = r"D:\dev\tcg-watcher-worktree\data\card_catalog.db"
BATCH_SIZE = 100
MAX_ROLES_PER_CARD = 4

ALLOWED_ROLES = {
    "removal",
    "tempo_piece",
    "searcher",
    "blocker",
    "life_manipulation",
    "extender",
    "don_acceleration",
    "stabilizer",
    "finisher",
    "hand_control",
    "don_reduction",
    "recursion_piece",
    "value_engine",
}

CONFIDENCE_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass
class RoleAssignment:
    role: str
    confidence: str
    evidence: str


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    # Some effect_text values are the literal "-" meaning "no effect".
    if s == "-":
        return ""
    return s


def parse_int_from_text(text: str) -> int:
    if not text:
        return 0
    digits = re.findall(r"\d+", text.replace(",", ""))
    if not digits:
        return 0
    try:
        return int(digits[0])
    except ValueError:
        return 0


def parse_cost(cost_value: object) -> int:
    if cost_value is None:
        return -1
    if isinstance(cost_value, int):
        return cost_value
    try:
        return int(str(cost_value).strip())
    except (ValueError, TypeError):
        return -1


def add_role(
    assignments: Dict[str, RoleAssignment],
    role: str,
    confidence: str,
    evidence: str,
) -> None:
    if role not in ALLOWED_ROLES:
        return
    if len(evidence) > 100:
        evidence = evidence[:97] + "..."
    existing = assignments.get(role)
    new_rank = CONFIDENCE_ORDER.get(confidence, 0)
    if existing is None or new_rank > CONFIDENCE_ORDER.get(existing.confidence, 0):
        assignments[role] = RoleAssignment(role=role, confidence=confidence, evidence=evidence)


POWER_BOOST_TOKENS = ("+1000", "+2000", "+3000")
ANY_POWER_BOOST_TOKENS = ("+1000", "+2000", "+3000", "+4000", "+5000", "+6000", "+7000", "+8000")


def classify_card_pass2(card: sqlite3.Row) -> List[RoleAssignment]:
    code = normalize_text(card["canonical_code"])
    card_type = normalize_text(card["card_type"])
    effect_text = normalize_text(card["effect_text"])
    power_text = normalize_text(card["power"])
    counter_text = normalize_text(card["counter"])
    cost = parse_cost(card["cost"])
    power_value = parse_int_from_text(power_text)
    counter_value = parse_int_from_text(counter_text)
    effect_l = effect_text.lower()

    assignments: Dict[str, RoleAssignment] = {}
    is_promo = code.startswith("P-")

    # -- Rule 1: [Counter] keyword anywhere -> stabilizer high
    if "[counter]" in effect_l:
        add_role(
            assignments,
            "stabilizer",
            "high",
            "[Counter] keyword - defensive hand resource",
        )

    # -- Rule 2: power boost to own cards
    if effect_l and "your" in effect_l and any(t in effect_l for t in POWER_BOOST_TOKENS):
        first_boost_pos = min(
            (effect_l.find(t) for t in POWER_BOOST_TOKENS if t in effect_l),
            default=-1,
        )
        if 0 <= first_boost_pos <= 80:
            add_role(
                assignments,
                "stabilizer",
                "medium",
                "Own-side +1000/+2000/+3000 power boost (primary)",
            )
        else:
            add_role(
                assignments,
                "stabilizer",
                "low",
                "Own-side +1000/+2000/+3000 power boost (secondary)",
            )

    # -- Rule 6: reactive on opponent's turn (Characters and Events only)
    if card_type in ("Character", "Event") and (
        "opponent's turn" in effect_l or "[opponent's turn]" in effect_l
    ):
        add_role(
            assignments,
            "stabilizer",
            "medium",
            "Reactive effect on opponent's turn - defensive tool",
        )

    # -- Rule 7: Events (without [Counter]) that affect board state
    if card_type == "Event" and "[counter]" not in effect_l and effect_l:
        matched_event_role = False
        if "return" in effect_l and "opponent" in effect_l:
            add_role(
                assignments,
                "removal",
                "medium",
                "Event returns opponent card to hand",
            )
            matched_event_role = True
        if ("place" in effect_l and ("bottom" in effect_l or "top" in effect_l) and "deck" in effect_l):
            add_role(
                assignments,
                "removal",
                "medium",
                "Event places character on top/bottom of deck",
            )
            matched_event_role = True
        if "k.o." in effect_l and "opponent" in effect_l:
            add_role(
                assignments,
                "removal",
                "medium",
                "Event KOs opponent character",
            )
            matched_event_role = True
        if ("opponent" in effect_l and "power" in effect_l and re.search(r"-\d{3,}", effect_l)):
            add_role(
                assignments,
                "removal",
                "medium",
                "Event debuffs opponent power (soft removal)",
            )
            matched_event_role = True
        if "rest" in effect_l and "opponent" in effect_l:
            add_role(
                assignments,
                "tempo_piece",
                "medium",
                "Event rests opponent characters",
            )
            matched_event_role = True
        if "active" in effect_l and "your" in effect_l and "set" in effect_l:
            add_role(
                assignments,
                "tempo_piece",
                "medium",
                "Event refreshes own characters/leader",
            )
            matched_event_role = True
        if any(t in effect_l for t in ANY_POWER_BOOST_TOKENS) and "your" in effect_l:
            add_role(
                assignments,
                "stabilizer",
                "medium",
                "Event gives own-side power boost",
            )
            matched_event_role = True
        if "look at" in effect_l or "draw" in effect_l or "reveal" in effect_l:
            add_role(
                assignments,
                "searcher",
                "medium",
                "Event provides look/draw/reveal card advantage",
            )
            matched_event_role = True
        if "don!!" in effect_l and ("your" in effect_l or "active" in effect_l):
            add_role(
                assignments,
                "don_acceleration",
                "medium",
                "Event manipulates DON!! cards",
            )
            matched_event_role = True
        if "from your trash" in effect_l:
            add_role(
                assignments,
                "recursion_piece",
                "medium",
                "Event recovers cards from trash",
            )
            matched_event_role = True
        if not matched_event_role:
            # Event had an effect but no specific pattern matched.
            # Land on stabilizer medium as the closest generic defensive shape.
            add_role(
                assignments,
                "stabilizer",
                "medium",
                "Event affects board state - generic classification",
            )

    # -- Character-specific rules
    if card_type == "Character":
        # Rule 4: low-power character with counter value -> defensive fodder
        if 0 < power_value <= 2000 and counter_value > 0:
            add_role(
                assignments,
                "stabilizer",
                "low",
                "Low-power Character with counter value - defensive fodder",
            )

        # Rule 3: high-power character with little or no effect -> raw threat
        if power_value >= 8000 and (not effect_l or len(effect_l) < 30):
            add_role(
                assignments,
                "finisher",
                "low",
                "High-power Character (>=8000) with minimal effect - raw threat",
            )

        # Rule 5: low-cost character with effect -> tempo / extender
        if cost in (0, 1) and effect_l:
            add_role(
                assignments,
                "tempo_piece",
                "low",
                "Low-cost (0/1) Character with effect - tempo play",
            )
            reduces_cost = (
                "cost" in effect_l
                and (
                    re.search(r"-\d+\s*cost", effect_l) is not None
                    or "cost -" in effect_l
                    or "reduce" in effect_l
                )
            )
            plays_other = (
                "play" in effect_l
                and (
                    "from your hand" in effect_l
                    or "from the top" in effect_l
                    or "play this card" in effect_l
                )
            )
            if reduces_cost or plays_other:
                add_role(
                    assignments,
                    "extender",
                    "low",
                    "Low-cost Character reduces costs or enables extra plays",
                )

    # -- Rule 8: promo cards with no effect_text -> card-type default
    if is_promo and not effect_l and not assignments:
        if card_type == "Leader":
            add_role(
                assignments,
                "finisher",
                "low",
                "Promo card - role inferred from card type",
            )
        elif card_type == "Character":
            add_role(
                assignments,
                "stabilizer",
                "low",
                "Promo card - role inferred from card type",
            )
        elif card_type == "Event":
            add_role(
                assignments,
                "stabilizer",
                "low",
                "Promo card - role inferred from card type",
            )
        elif card_type == "Stage":
            add_role(
                assignments,
                "value_engine",
                "low",
                "Promo card - role inferred from card type",
            )

    # -- Rule 10: Leaders with no classifiable effect yet -> finisher
    if card_type == "Leader" and not assignments:
        add_role(
            assignments,
            "finisher",
            "low",
            "Leader - default finisher role",
        )

    # -- Rule 11: Ultimate fallback by card_type
    if not assignments:
        if card_type == "Character":
            add_role(
                assignments,
                "stabilizer",
                "low",
                "Fallback classification by card type",
            )
        elif card_type == "Event":
            add_role(
                assignments,
                "stabilizer",
                "low",
                "Fallback classification by card type",
            )
        elif card_type == "Stage":
            add_role(
                assignments,
                "value_engine",
                "low",
                "Fallback classification by card type",
            )
        elif card_type == "Leader":
            add_role(
                assignments,
                "finisher",
                "low",
                "Fallback classification by card type",
            )

    result = list(assignments.values())
    if len(result) > MAX_ROLES_PER_CARD:
        result = sorted(
            result,
            key=lambda r: (CONFIDENCE_ORDER.get(r.confidence, 0), r.role),
            reverse=True,
        )[:MAX_ROLES_PER_CARD]
    return result


def run() -> int:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute(
        """
        SELECT c.canonical_code, c.card_name, c.card_type,
               c.color, c.cost, c.power, c.effect_text,
               c.counter, c.traits
        FROM cards c
        WHERE c.canonical_code NOT IN (
            SELECT DISTINCT card_id FROM card_roles
        )
        ORDER BY c.canonical_code
        """
    )
    unclassified_cards = cur.fetchall()

    total_cards = len(unclassified_cards)
    rows_inserted = 0
    cards_classified = 0
    cards_without_roles: List[str] = []
    batch_errors: List[str] = []

    for offset in range(0, total_cards, BATCH_SIZE):
        batch = unclassified_cards[offset : offset + BATCH_SIZE]
        pending_rows: List[Tuple[str, str, str, str]] = []

        for card in batch:
            roles = classify_card_pass2(card)
            if not roles:
                cards_without_roles.append(card["canonical_code"])
                continue
            cards_classified += 1
            for role in roles:
                pending_rows.append(
                    (
                        card["canonical_code"],
                        role.role,
                        role.confidence,
                        role.evidence,
                    )
                )

        if not pending_rows:
            continue

        try:
            cur.execute("BEGIN;")
            for row in pending_rows:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO card_roles
                      (card_id, role, role_confidence, evidence,
                       classification_source, status)
                    VALUES (?, ?, ?, ?, 'text_analysis', 'inferred');
                    """,
                    row,
                )
                if cur.rowcount == 1:
                    rows_inserted += 1
            cur.execute("COMMIT;")
        except Exception as exc:
            cur.execute("ROLLBACK;")
            batch_errors.append(
                f"Batch {offset // BATCH_SIZE + 1} "
                f"({offset}-{offset + len(batch) - 1}) error: {exc}"
            )

    # -- Verification queries --
    cur.execute("SELECT COUNT(DISTINCT card_id) FROM card_roles;")
    covered_cards = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM card_roles;")
    total_role_rows = cur.fetchone()[0]

    cur.execute(
        """
        SELECT role, COUNT(*) AS count
        FROM card_roles
        GROUP BY role
        ORDER BY count DESC;
        """
    )
    role_breakdown = cur.fetchall()

    cur.execute(
        """
        SELECT role_confidence, COUNT(*) AS count
        FROM card_roles
        GROUP BY role_confidence
        ORDER BY count DESC;
        """
    )
    confidence_breakdown = cur.fetchall()

    cur.execute(
        """
        SELECT COUNT(*) FROM cards c
        WHERE c.canonical_code NOT IN (
            SELECT DISTINCT card_id FROM card_roles
        );
        """
    )
    still_unclassified_count = cur.fetchone()[0]

    cur.execute(
        """
        SELECT c.canonical_code
        FROM cards c
        WHERE c.canonical_code NOT IN (
            SELECT DISTINCT card_id FROM card_roles
        )
        ORDER BY c.canonical_code;
        """
    )
    still_unclassified_codes = [r[0] for r in cur.fetchall()]

    con.close()

    print("=== classify_gameplay_roles PASS 2 (edge case fill) ===")
    print(f"Unclassified cards loaded: {total_cards}")
    print(f"Cards classified in pass 2: {cards_classified}")
    print(f"Rows inserted in pass 2: {rows_inserted}")
    print(f"Cards with NO inferred role during pass 2: {len(cards_without_roles)}")
    if cards_without_roles:
        print("No-role sample (pass 2):")
        for code in cards_without_roles[:20]:
            print(code)

    print("\nPOST-RUN VERIFICATION")
    print(f"COUNT(DISTINCT card_id) FROM card_roles: {covered_cards}")
    print(f"COUNT(*) FROM card_roles: {total_role_rows}")

    print("\nRole breakdown:")
    for row in role_breakdown:
        print(f"{row['role']}|{row['count']}")

    print("\nConfidence breakdown:")
    for row in confidence_breakdown:
        print(f"{row['role_confidence']}|{row['count']}")

    print(f"\nCards still with no role: {still_unclassified_count}")
    if still_unclassified_codes:
        print("Still unclassified canonical_codes:")
        for code in still_unclassified_codes:
            print(code)

    if batch_errors:
        print("\nBatch errors (rolled back):")
        for err in batch_errors:
            print(err)
    else:
        print("\nBatch errors (rolled back): none")

    return 0


if __name__ == "__main__":
    raise SystemExit(run())
