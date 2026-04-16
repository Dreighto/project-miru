"""Populate card_keywords by scanning cards.effect_text for known keyword patterns."""

import sqlite3
import sys

DB_PATH = r"D:\dev\tcg-watcher-worktree\data\card_catalog.db"

# keyword_id -> (keyword_name, match_pattern, match_mode, card_type_filter)
# match_mode: 'exact' = exact substring, 'prefix' = prefix match
# card_type_filter: None = all types, 'Event' = Events only
KEYWORD_PATTERNS = [
    (1, "Rush", "[Rush]", "exact", None),
    (2, "Blocker", "[Blocker]", "exact", None),
    (3, "Banish", "[Banish]", "exact", None),
    (4, "Double Attack", "[Double Attack]", "exact", None),
    (5, "On Play", "[On Play]", "exact", None),
    (6, "When Attacking", "[When Attacking]", "exact", None),
    (7, "On K.O.", "[On K.O.]", "exact", None),
    (8, "Trigger", "[Trigger]", "exact", None),
    (9, "Counter", "[Counter]", "exact", None),
    (10, "Activate: Main", "[Activate: Main]", "exact", None),
    (11, "Your Turn", "[Your Turn]", "exact", None),
    (12, "Opponent's Turn", "[Opponent's Turn]", "exact", None),
    (13, "Once Per Turn", "[Once Per Turn]", "exact", None),
    (14, "DON!! x", "[DON!! x", "prefix", None),
    (15, "DON!! \u2212", "DON!! \u2212", "prefix", None),
    (16, "Rush: Character", "[Rush: Character]", "exact", None),
    (17, "Unblockable", "[Unblockable]", "exact", None),
    (18, "On Block", "[On Block]", "exact", None),
    (19, "End of Your Turn", "[End of Your Turn]", "exact", None),
    (20, "On Your Opponent's Attack", "[On Your Opponent's Attack]", "exact", None),
    (21, "Main", "[Main]", "exact", "Event"),
]

CONFIDENCE_MAP = {
    "exact": "verified",
    "prefix": "high",
}


def run() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    cur = con.cursor()

    cur.execute("SELECT id, canonical_code, effect_text, card_type FROM cards")
    cards = cur.fetchall()

    inserted = 0
    skipped_empty = 0
    matches_by_keyword: dict[str, int] = {}
    ignored_inserts = 0

    for card_id, code, effect_text, card_type in cards:
        if not effect_text or not effect_text.strip():
            skipped_empty += 1
            continue

        for kw_id, kw_name, pattern, mode, type_filter in KEYWORD_PATTERNS:
            if type_filter and card_type != type_filter:
                continue

            matched = False
            if mode == "exact":
                matched = pattern in effect_text
            elif mode == "prefix":
                matched = pattern in effect_text

            if matched:
                confidence = CONFIDENCE_MAP[mode]
                try:
                    cur.execute(
                        """INSERT OR IGNORE INTO card_keywords
                           (card_id, keyword_id, source_key, confidence)
                           VALUES (?, ?, 'bandai_official', ?)""",
                        (card_id, kw_id, confidence),
                    )
                    if cur.rowcount == 1:
                        inserted += 1
                        matches_by_keyword[kw_name] = matches_by_keyword.get(kw_name, 0) + 1
                    else:
                        ignored_inserts += 1
                except Exception as e:
                    print(f"ERROR on card {code} keyword {kw_name}: {e}", file=sys.stderr)

    con.commit()
    con.close()

    print("\n=== card_keywords population complete ===")
    print(f"Cards processed: {len(cards)}")
    print(f"Cards skipped (empty effect_text): {skipped_empty}")
    print(f"Rows inserted (new): {inserted}")
    print(f"INSERT OR IGNORE no-ops (duplicates): {ignored_inserts}")
    print("\nBreakdown by keyword (new inserts only):")
    for kw_name, count in sorted(
        matches_by_keyword.items(),
        key=lambda x: -x[1],
    ):
        print(f"  {kw_name}: {count}")


if __name__ == "__main__":
    run()
