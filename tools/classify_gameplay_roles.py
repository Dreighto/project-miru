import re
import sqlite3
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

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


@dataclass
class RoleAssignment:
    role: str
    confidence: str
    evidence: str


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_power(power_text: str) -> int:
    if not power_text:
        return 0
    digits = re.findall(r"\d+", power_text.replace(",", ""))
    if not digits:
        return 0
    try:
        return int(digits[0])
    except ValueError:
        return 0


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
    order = {"low": 1, "medium": 2, "high": 3}
    existing = assignments.get(role)
    if existing is None or order.get(confidence, 0) > order.get(existing.confidence, 0):
        assignments[role] = RoleAssignment(role=role, confidence=confidence, evidence=evidence)


def classify_from_effect_text(effect_text: str, assignments: Dict[str, RoleAssignment]) -> None:
    t = effect_text.lower()
    if not t:
        return

    if "k.o." in t or "trash" in t or "return" in t:
        if "your opponent" in t and ("character" in t or "card" in t):
            add_role(assignments, "removal", "high", "Effect KOs/trashes/returns opponent card")
        elif "return" in t and "opponent" in t:
            add_role(assignments, "removal", "high", "Returns opponent cards to hand/deck")

    if "rest up to" in t or "cannot attack" in t or "cannot be attacked" in t or "rest this card" in t:
        add_role(assignments, "tempo_piece", "high", "Effect controls attack timing or board tempo")

    if "look at" in t and "add up to" in t:
        add_role(assignments, "searcher", "high", "Looks at cards and adds card(s) to hand")
    elif "draw" in t or "add up to 1" in t:
        add_role(assignments, "searcher", "medium", "Effect provides draw/search value")

    if "life" in t:
        if "add 1 card from the top of your deck to the top of your life" in t:
            add_role(assignments, "life_manipulation", "high", "Adds card to life from deck")
        elif "life" in t and ("add" in t or "take" in t or "top of your life" in t):
            add_role(assignments, "life_manipulation", "medium", "Effect manipulates life cards")

    if "play up to" in t or "if your leader has the" in t and "play this card" in t:
        add_role(assignments, "extender", "high", "Plays extra body or cheats timing")
    elif "reduce the cost" in t or "cost -" in t:
        add_role(assignments, "extender", "high", "Reduces costs enabling additional plays")

    if "add up to 1 don!!" in t or "set up to 1 of your don!! cards as active" in t:
        add_role(assignments, "don_acceleration", "high", "Adds/restores DON!! cards")
    elif "don!!" in t and ("active" in t or "add" in t):
        add_role(assignments, "don_acceleration", "medium", "DON!! acceleration signal in effect")

    if "your characters gain" in t or "cannot be k.o." in t or "cannot be removed" in t:
        add_role(assignments, "stabilizer", "high", "Boosts/protects own board state")
    elif "+1000" in t or "+2000" in t:
        add_role(assignments, "stabilizer", "medium", "Power boost effect improves board stability")

    if "double attack" in t or "direct attack" in t:
        add_role(assignments, "finisher", "high", "Direct Attack/Double Attack finishing pressure")

    if "your opponent trashes" in t or "trash 1 card from your opponent" in t:
        add_role(assignments, "hand_control", "high", "Forces opponent hand discard")

    if "return up to" in t and "don!! cards" in t:
        add_role(assignments, "don_reduction", "high", "Returns opponent DON!! to deck")

    if "from your trash" in t or "trash to your hand" in t:
        add_role(assignments, "recursion_piece", "high", "Recovers cards from trash")

    if "at the end of your turn" in t or "once per turn" in t:
        if "draw" in t or "add up to 1 card" in t:
            add_role(assignments, "value_engine", "high", "Recurring per-turn card advantage")
        else:
            add_role(assignments, "value_engine", "medium", "Ongoing per-turn effect value")


def classify_card(card: sqlite3.Row) -> List[RoleAssignment]:
    card_type = normalize_text(card["card_type"])
    effect_text = normalize_text(card["effect_text"])
    traits = normalize_text(card["traits"])
    power_text = normalize_text(card["power"])
    power_value = parse_power(power_text)
    effect_l = effect_text.lower()
    traits_l = traits.lower()

    assignments: Dict[str, RoleAssignment] = {}

    if card_type == "Character":
        if "[blocker]" in effect_l or "blocker" in traits_l:
            add_role(assignments, "blocker", "high", "Blocker keyword - redirects opponent attacks")

        if "[double attack]" in effect_l or "double attack" in traits_l:
            add_role(assignments, "finisher", "high", "Double Attack keyword creates finishing pressure")

        if "[rush]" in effect_l or "rush" in traits_l:
            if power_value >= 6000:
                add_role(assignments, "finisher", "medium", "Rush with strong power pressures lethal quickly")
            else:
                add_role(assignments, "tempo_piece", "medium", "Rush enables immediate tempo swing")

    if card_type == "Event":
        if "[counter]" in effect_l:
            add_role(assignments, "stabilizer", "high", "Counter event stabilizes board/defense")

    classify_from_effect_text(effect_text, assignments)

    if card_type == "Stage":
        t = effect_l
        if "once per turn" in t and ("draw" in t or "add up to" in t):
            add_role(assignments, "value_engine", "high", "Stage gives recurring per-turn card advantage")
        if "your characters gain" in t or "+1000" in t:
            add_role(assignments, "stabilizer", "medium", "Stage provides ongoing power support")
        if "don!!" in t and ("active" in t or "add" in t):
            add_role(assignments, "don_acceleration", "medium", "Stage supports ongoing DON!! acceleration")

    if card_type == "Leader":
        classify_from_effect_text(effect_text, assignments)

    if not assignments and not effect_text:
        if card_type == "Character":
            if power_value >= 7000:
                add_role(assignments, "finisher", "low", "No effect text - role inferred from power/type")
            elif 0 < power_value <= 3000:
                add_role(assignments, "stabilizer", "low", "No effect text - role inferred from power/type")
        elif card_type == "Leader":
            add_role(assignments, "finisher", "low", "No effect text - role inferred from power/type")

    result = list(assignments.values())
    if len(result) > MAX_ROLES_PER_CARD:
        priority = {
            "high": 3,
            "medium": 2,
            "low": 1,
        }
        result = sorted(
            result,
            key=lambda r: (priority.get(r.confidence, 0), r.role),
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
            roles = classify_card(card)
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
                f"Batch {offset // BATCH_SIZE + 1} ({offset}-{offset + len(batch) - 1}) error: {exc}"
            )

    # Verification queries
    cur.execute("SELECT COUNT(DISTINCT card_id) FROM card_roles;")
    covered_cards = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM card_roles;")
    total_role_rows = cur.fetchone()[0]

    cur.execute(
        """
        SELECT role, COUNT(*) as count
        FROM card_roles
        GROUP BY role
        ORDER BY count DESC;
        """
    )
    role_breakdown = cur.fetchall()

    cur.execute(
        """
        SELECT role_confidence, COUNT(*) as count
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

    print("=== classify_gameplay_roles gap-fill pass ===")
    print(f"Unclassified cards loaded: {total_cards}")
    print(f"Cards classified: {cards_classified}")
    print(f"Rows inserted: {rows_inserted}")
    print(f"Cards with no inferred role during classification: {len(cards_without_roles)}")
    if cards_without_roles:
        print("No-role (during classification) sample:")
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
