"""
miru_detect_archetypes.py

Read-only archetype clustering for a single leader.

Loads decklists and deck_entries from miru_deck_intel.db, builds per-deck
card data, and clusters decks by similarity: an edge exists between two
decks if similarity >= threshold; archetypes are connected components.

Two similarity modes are supported:
  weighted  (default): weighted Jaccard using card quantities
                       sum(min(a_i, b_i)) / sum(max(a_i, b_i))
  unweighted          : classic Jaccard over unique card-code sets
                       |intersection| / |union|

No DB writes. No network access. Deterministic output.

Usage
-----
    python -m tools.miru_detect_archetypes --leader-code OP01-001
    python -m tools.miru_detect_archetypes --leader-code OP02-001 --threshold 0.75
    python -m tools.miru_detect_archetypes --leader-code OP03-001 --sim-mode unweighted

Limitations / TODOs
------------------
- No format_code filter; all decks for the leader are included.
- No DB persistence of clusters; human-readable summary only.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
THRESHOLD_SIMILARITY = 0.70

REQUIRED_TABLES = ("decklists", "deck_entries")

SIM_MODE_WEIGHTED = "weighted"
SIM_MODE_UNWEIGHTED = "unweighted"
SIM_MODE_DEFAULT = SIM_MODE_WEIGHTED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Cluster decks for a leader into archetypes by similarity "
            "threshold (read-only, miru_deck_intel.db)."
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
        help="Maximum number of clusters to show (default: 20). Singletons always listed.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=THRESHOLD_SIMILARITY,
        metavar="FLOAT",
        help=f"Minimum similarity to link two decks (default: {THRESHOLD_SIMILARITY}).",
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


def connected_components(neighbors: dict[str, set[str]], nodes: list[str]) -> list[set[str]]:
    """Return list of connected components (each a set of node ids). Order deterministic by min node."""
    remaining = set(nodes)
    components: list[set[str]] = []
    while remaining:
        start = min(remaining)
        comp: set[str] = set()
        q: deque[str] = deque([start])
        while q:
            v = q.popleft()
            if v not in remaining:
                continue
            remaining.discard(v)
            comp.add(v)
            for w in neighbors.get(v, set()):
                if w in remaining:
                    q.append(w)
        components.append(comp)
    return components


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
        if len(deck_uids) == 0:
            print(f"Archetype clusters for leader {args.leader_code}\n")
            print("No decks found for this leader.")
            return
        if len(deck_uids) == 1:
            print(f"Archetype clusters for leader {args.leader_code}\n")
            print("Only one deck for this leader; no clustering.")
            return
        if use_weighted:
            deck_data = load_deck_card_dicts(conn, deck_uids)
        else:
            deck_data = load_deck_card_sets(conn, deck_uids)
    finally:
        conn.close()

    sim_fn = weighted_jaccard if use_weighted else jaccard

    # Pairwise similarities and lookup for later
    pairs: list[tuple[str, str, float]] = []
    sim_lookup: dict[tuple[str, str], float] = {}
    n = len(deck_uids)
    for i in range(n):
        for j in range(i + 1, n):
            uid_a, uid_b = deck_uids[i], deck_uids[j]
            sim = sim_fn(deck_data[uid_a], deck_data[uid_b])
            pairs.append((uid_a, uid_b, sim))
            sim_lookup[(uid_a, uid_b)] = sim

    # Graph: edge if similarity >= threshold
    neighbors: dict[str, set[str]] = {uid: set() for uid in deck_uids}
    for uid_a, uid_b, sim in pairs:
        if sim >= args.threshold:
            neighbors[uid_a].add(uid_b)
            neighbors[uid_b].add(uid_a)

    components = connected_components(neighbors, deck_uids)
    clusters = [c for c in components if len(c) >= 2]
    singletons = [c for c in components if len(c) == 1]

    def avg_internal_similarity(comp: set[str]) -> float:
        uids = sorted(comp)
        total = 0.0
        count = 0
        for i in range(len(uids)):
            for j in range(i + 1, len(uids)):
                a, b = uids[i], uids[j]
                total += sim_lookup[(a, b)]
                count += 1
        return total / count if count else 0.0

    # Sort clusters: larger first, then by avg internal similarity desc, then by min deck_uid asc
    clusters_with_avg = [(c, avg_internal_similarity(c)) for c in clusters]
    clusters_with_avg.sort(
        key=lambda x: (-len(x[0]), -x[1], min(x[0]))
    )
    singleton_uids = sorted(s for c in singletons for s in c)

    # Output
    print(f"Archetype clusters for leader {args.leader_code}")
    print(f"threshold: {args.threshold:.2f}  sim-mode: {args.sim_mode}\n")

    for idx, (comp, avg_sim) in enumerate(clusters_with_avg[: args.limit], start=1):
        sorted_decks = sorted(comp)
        print(f"Cluster {idx}  ({len(comp)} deck(s))")
        print(f"  avg internal similarity: {avg_sim:.2f}")
        print("  decks:")
        for uid in sorted_decks:
            print(f"    {uid}")
        print()

    if singleton_uids:
        print("Unclustered / singleton decks:")
        for uid in singleton_uids:
            print(f"  {uid}")

    if len(clusters_with_avg) > args.limit:
        print(f"\n(... {len(clusters_with_avg) - args.limit} more cluster(s) omitted; use --limit to show more)")


if __name__ == "__main__":
    main()
