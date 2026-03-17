"""
miru_deck_similarity.py

Read-only deck similarity for a single leader.

Loads decklists and deck_entries from miru_deck_intel.db and reports pairwise
similarity scores. Two modes:

  weighted  (default): weighted Jaccard using card quantities
                       sum(min(a_i, b_i)) / sum(max(a_i, b_i))
  unweighted          : classic Jaccard over unique card-code sets
                       |intersection| / |union|

No DB writes. No network access. Deterministic output.

Usage
-----
    python -m tools.miru_deck_similarity --leader-code OP01-001
    python -m tools.miru_deck_similarity --leader-code OP02-001 --limit 10
    python -m tools.miru_deck_similarity --leader-code OP03-001 --sim-mode unweighted

Limitations / TODOs
------------------
- No format_code filter; all decks for the leader are included.
- Output is pairs only; no clustering or aggregate stats.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"

REQUIRED_TABLES = ("decklists", "deck_entries")

SIM_MODE_WEIGHTED = "weighted"
SIM_MODE_UNWEIGHTED = "unweighted"
SIM_MODE_DEFAULT = SIM_MODE_WEIGHTED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compute pairwise deck similarity for a leader from "
            "miru_deck_intel.db (read-only)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--leader-code",
        required=True,
        metavar="CODE",
        help="Leader card code (e.g. OP01-001).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of deck pairs to output (default: 20).",
    )
    parser.add_argument(
        "--sim-mode",
        choices=(SIM_MODE_WEIGHTED, SIM_MODE_UNWEIGHTED),
        default=SIM_MODE_DEFAULT,
        metavar="MODE",
        help=(
            f"Similarity mode: '{SIM_MODE_WEIGHTED}' (default, quantity-aware) "
            f"or '{SIM_MODE_UNWEIGHTED}' (classic set Jaccard)."
        ),
    )
    parser.add_argument(
        "--db-path",
        default="",
        metavar="PATH",
        help=f"Path to miru_deck_intel.db (default: {DEFAULT_DB_PATH}).",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def ensure_tables(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)",
        REQUIRED_TABLES,
    )
    found = {row[0] for row in cur.fetchall()}
    missing = set(REQUIRED_TABLES) - found
    if missing:
        raise SystemExit(f"Missing tables: {sorted(missing)}. Need {REQUIRED_TABLES}.")


def load_deck_uids(conn: sqlite3.Connection, leader_code: str) -> list[str]:
    cur = conn.execute(
        "SELECT deck_uid FROM decklists WHERE leader_code = ? ORDER BY deck_uid",
        (leader_code,),
    )
    return [row[0] for row in cur.fetchall()]


def load_deck_card_sets(
    conn: sqlite3.Connection, deck_uids: list[str]
) -> dict[str, set[str]]:
    """Return {deck_uid: set[card_code]}. Quantities are discarded."""
    if not deck_uids:
        return {}
    placeholders = ",".join("?" * len(deck_uids))
    cur = conn.execute(
        f"SELECT deck_uid, card_code FROM deck_entries WHERE deck_uid IN ({placeholders})",
        deck_uids,
    )
    out: dict[str, set[str]] = {uid: set() for uid in deck_uids}
    for deck_uid, card_code in cur.fetchall():
        out[deck_uid].add(card_code)
    return out


def load_deck_card_dicts(
    conn: sqlite3.Connection, deck_uids: list[str]
) -> dict[str, dict[str, int]]:
    """Return {deck_uid: {card_code: quantity}}."""
    if not deck_uids:
        return {}
    placeholders = ",".join("?" * len(deck_uids))
    cur = conn.execute(
        f"SELECT deck_uid, card_code, quantity FROM deck_entries WHERE deck_uid IN ({placeholders})",
        deck_uids,
    )
    out: dict[str, dict[str, int]] = {uid: {} for uid in deck_uids}
    for deck_uid, card_code, qty in cur.fetchall():
        out[deck_uid][card_code] = int(qty or 0)
    return out


def jaccard(a: set[str], b: set[str]) -> float:
    """Classic Jaccard similarity over unique card-code sets."""
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def weighted_jaccard(a: dict[str, int], b: dict[str, int]) -> float:
    """Weighted Jaccard similarity using card quantities.

    sum(min(a_i, b_i)) / sum(max(a_i, b_i))
    """
    all_codes = set(a) | set(b)
    if not all_codes:
        return 0.0
    numerator = sum(min(a.get(c, 0), b.get(c, 0)) for c in all_codes)
    denominator = sum(max(a.get(c, 0), b.get(c, 0)) for c in all_codes)
    return numerator / denominator if denominator > 0 else 0.0


def main() -> None:
    args = build_parser().parse_args()
    db_path = resolve_db_path(args)
    if not db_path.is_file():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    use_weighted = args.sim_mode == SIM_MODE_WEIGHTED

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = 1")
    try:
        ensure_tables(conn)
        deck_uids = load_deck_uids(conn, args.leader_code)
        if len(deck_uids) < 2:
            print(f"Deck similarity for leader {args.leader_code}  [{args.sim_mode}]\n")
            print("Need at least 2 decks for this leader.")
            return
        if use_weighted:
            deck_data = load_deck_card_dicts(conn, deck_uids)
        else:
            deck_data = load_deck_card_sets(conn, deck_uids)
    finally:
        conn.close()

    sim_fn = weighted_jaccard if use_weighted else jaccard

    pairs: list[tuple[str, str, float]] = []
    n = len(deck_uids)
    for i in range(n):
        for j in range(i + 1, n):
            uid_a, uid_b = deck_uids[i], deck_uids[j]
            sim = sim_fn(deck_data[uid_a], deck_data[uid_b])
            pairs.append((uid_a, uid_b, sim))

    # Sort by similarity descending, then deck_a, deck_b for determinism
    pairs.sort(key=lambda x: (-x[2], x[0], x[1]))
    capped = pairs[: args.limit]

    print(f"Deck similarity for leader {args.leader_code}  [{args.sim_mode}]\n")
    col_a = "deck_a"
    col_b = "deck_b"
    col_sim = "similarity"
    width_a = max(len(col_a), max(len(p[0]) for p in capped)) if capped else len(col_a)
    width_b = max(len(col_b), max(len(p[1]) for p in capped)) if capped else len(col_b)
    sep = "-" * (width_a + 1 + width_b + 1 + 4)
    print(f"{col_a:<{width_a}} {col_b:<{width_b}} {col_sim}")
    print(sep)
    for uid_a, uid_b, sim in capped:
        print(f"{uid_a:<{width_a}} {uid_b:<{width_b}} {sim:.4f}")


if __name__ == "__main__":
    main()
