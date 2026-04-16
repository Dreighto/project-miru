"""
One-off: compute current OP10-001 deck_uids from data/decks op10*.json,
then remove from miru_deck_intel.db any OP10-001 deck whose deck_uid is not
in that set. Leaves other leaders untouched.

Run from repo root:
  python -m tools._clean_op10_deck_state
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.miru_fetch_decklist import (
    load_payload,
    extract_card_list,
    detect_leader,
    parse_entries,
    compute_deck_uid,
)


LEADER = "OP10-001"
DECKS_DIR = ROOT / "data" / "decks"
DB_PATH = ROOT / "data" / "miru_deck_intel.db"


def main() -> int:
    deck_files = sorted(DECKS_DIR.glob("op10*.json"))
    if not deck_files:
        print("No op10*.json files in data/decks", file=sys.stderr)
        return 1

    keep_uids: set[str] = set()
    for path in deck_files:
        try:
            payload = load_payload(path)
            card_list = extract_card_list(payload)
            leader_code = detect_leader(payload, card_list, "")
            entries = parse_entries(card_list, leader_code)
            uid = compute_deck_uid(leader_code, entries)
            keep_uids.add(uid)
        except Exception as e:
            print(f"ERROR {path.name}: {e}", file=sys.stderr)
            return 1

    print(f"Current legal OP10-001 deck files: {len(deck_files)} -> {len(keep_uids)} deck_uids")
    print("Keep uids:", sorted(keep_uids))

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.execute(
        "SELECT deck_uid FROM decklists WHERE leader_code = ? ORDER BY deck_uid",
        (LEADER,),
    )
    db_uids = [row[0] for row in cur.fetchall()]
    obsolete = [u for u in db_uids if u not in keep_uids]

    if not obsolete:
        print("No obsolete OP10-001 decks to remove.")
        conn.close()
        return 0

    print(f"Removing {len(obsolete)} obsolete deck(s): {obsolete}")
    with conn:
        for uid in obsolete:
            conn.execute("DELETE FROM deck_entries WHERE deck_uid = ?", (uid,))
        for uid in obsolete:
            conn.execute("DELETE FROM decklists WHERE deck_uid = ?", (uid,))
    conn.close()
    print("Done. Removed deck_uids:", obsolete)
    return 0


if __name__ == "__main__":
    sys.exit(main())
