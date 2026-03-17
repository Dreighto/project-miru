"""
miru_cluster_skeletons.py

Read-only cluster skeleton extraction for a single leader.

Uses the same clustering as miru_detect_archetypes (similarity threshold,
connected components). For each cluster (size >= 2), computes:
  - Core / flex / tech cards by share of cluster decks (>=80%, 40-80%, <40%)
  - Cost curve from dossiers (buckets 0-7, 8+)
  - Top traits from dossiers attribute field (same parsing as trait signals)

The leader card itself is always excluded from core/flex/tech card lists.

Two similarity modes are supported (default: weighted):
  weighted   : sum(min(a_i, b_i)) / sum(max(a_i, b_i))
  unweighted : classic Jaccard over unique card-code sets

No DB writes. No schema changes. Deterministic output.
Dossiers DB optional; cost/trait/name enrichment skipped gracefully if missing.

Usage
-----
    python -m tools.miru_cluster_skeletons --leader-code OP01-001
    python -m tools.miru_cluster_skeletons --leader-code OP02-001 --threshold 0.75
    python -m tools.miru_cluster_skeletons --leader-code OP03-001 --sim-mode unweighted

Limitations / TODOs
------------------
- Uses same clustering as miru_detect_archetypes (no format_code filter).
- Cost curve and traits require miru_dossiers.db; missing DB skips enrichment.
- No DB persistence of skeletons; human-readable summary only.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import deque
from pathlib import Path

from tools.miru_detect_archetypes import (
    SIM_MODE_DEFAULT,
    SIM_MODE_UNWEIGHTED,
    SIM_MODE_WEIGHTED,
    load_deck_card_dicts,
    weighted_jaccard,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"
THRESHOLD_SIMILARITY = 0.70

REQUIRED_TABLES = ("decklists", "deck_entries")

# Role thresholds (share of cluster decks that play the card)
CORE_PCT = 80.0
FLEX_PCT = 40.0

# Cost buckets (same as cost curves)
COST_BUCKETS = ["0", "1", "2", "3", "4", "5", "6", "7", "8+"]
MAX_EXACT_COST = 7

# Trait parsing (same as miru_compute_trait_signals)
_TRAIT_FIELD = "attribute"
_SKIP_VALUES = frozenset({"", "-", "?", "n/a", "none", "unknown"})

_BAR_WIDTH = 10
_TOP_TRAITS = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract cluster skeletons for a leader: core/flex/tech cards, "
            "cost curve, and top traits (read-only)."
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
        "--threshold",
        type=float,
        default=THRESHOLD_SIMILARITY,
        metavar="FLOAT",
        help=f"Minimum similarity to link decks (default: {THRESHOLD_SIMILARITY}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of clusters to show (default: 20).",
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
    parser.add_argument(
        "--dossiers-db",
        default="",
        metavar="PATH",
        help=f"Path to miru_dossiers.db for cost/trait/name (default: {DEFAULT_DOSSIERS_PATH}).",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def resolve_dossiers_path(args: argparse.Namespace) -> Path:
    return Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH


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


def load_cluster_card_stats(
    conn: sqlite3.Connection, deck_uids: list[str]
) -> dict[str, tuple[int, int]]:
    """Return {card_code: (deck_count, total_copies)} for cards in the given decks."""
    if not deck_uids:
        return {}
    placeholders = ",".join("?" * len(deck_uids))
    cur = conn.execute(
        f"SELECT deck_uid, card_code, quantity FROM deck_entries WHERE deck_uid IN ({placeholders})",
        deck_uids,
    )
    decks_per_card: dict[str, set[str]] = {}
    total_copies: dict[str, int] = {}
    for deck_uid, card_code, qty in cur.fetchall():
        total_copies[card_code] = total_copies.get(card_code, 0) + qty
        if card_code not in decks_per_card:
            decks_per_card[card_code] = set()
        decks_per_card[card_code].add(deck_uid)
    return {code: (len(decks_per_card[code]), total_copies[code]) for code in decks_per_card}


def jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def connected_components(neighbors: dict[str, set[str]], nodes: list[str]) -> list[set[str]]:
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


def _to_bucket(raw_cost: str) -> str | None:
    try:
        val = int(str(raw_cost).strip())
    except (ValueError, TypeError):
        return None
    if val < 0:
        return None
    if val > MAX_EXACT_COST:
        return "8+"
    return str(val)


def _parse_traits(raw: str) -> list[str]:
    traits: list[str] = []
    for part in raw.split("/"):
        t = part.strip().title()
        if t.lower() not in _SKIP_VALUES:
            traits.append(t)
    return traits


def load_dossiers_enrichment(
    dossiers_path: Path,
    card_codes: list[str],
) -> tuple[dict[str, str], dict[str, str], dict[str, list[str]]]:
    """
    Return (name_map, cost_bucket_map, trait_map) for card codes.
    Missing or invalid path returns empty dicts.
    """
    names: dict[str, str] = {}
    cost_buckets: dict[str, str] = {}
    trait_map: dict[str, list[str]] = {}
    if not card_codes or not dossiers_path.is_file():
        return (names, cost_buckets, trait_map)

    try:
        dconn = sqlite3.connect(f"file:{dossiers_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return (names, cost_buckets, trait_map)

    try:
        dconn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(card_codes))
        # Names from cards
        for row in dconn.execute(
            f"SELECT canonical_code, card_name FROM cards WHERE canonical_code IN ({placeholders})",
            card_codes,
        ).fetchall():
            names[row["canonical_code"]] = (row["card_name"] or "").strip()

        # Cost from card_facts
        for row in dconn.execute(
            f"""
            SELECT c.canonical_code, cf.value_text
            FROM card_facts cf
            JOIN cards c ON c.id = cf.card_id
            WHERE cf.field_name = 'cost' AND c.canonical_code IN ({placeholders})
            ORDER BY CASE cf.verification_state WHEN 'verified' THEN 0 ELSE 1 END, cf.updated_at DESC
            """,
            card_codes,
        ).fetchall():
            code = row["canonical_code"]
            if code not in cost_buckets:
                bucket = _to_bucket(row["value_text"] or "")
                if bucket is not None:
                    cost_buckets[code] = bucket

        # Traits from card_facts (attribute)
        for row in dconn.execute(
            f"""
            SELECT c.canonical_code, cf.value_text
            FROM card_facts cf
            JOIN cards c ON c.id = cf.card_id
            WHERE cf.field_name = '{_TRAIT_FIELD}' AND c.canonical_code IN ({placeholders})
            ORDER BY CASE cf.verification_state WHEN 'verified' THEN 0 ELSE 1 END, cf.updated_at DESC
            """,
            card_codes,
        ).fetchall():
            code = row["canonical_code"]
            if code not in trait_map:
                traits = _parse_traits(row["value_text"] or "")
                if traits:
                    trait_map[code] = traits
    except sqlite3.Error:
        pass
    finally:
        dconn.close()

    return (names, cost_buckets, trait_map)


def main() -> None:
    args = build_parser().parse_args()
    db_path = resolve_db_path(args)
    if not db_path.is_file():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    leader_code = args.leader_code.upper()
    use_weighted = args.sim_mode == SIM_MODE_WEIGHTED

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = 1")
    try:
        ensure_tables(conn)
        deck_uids = load_deck_uids(conn, leader_code)
        if len(deck_uids) < 2:
            print(f"Cluster skeletons for leader {leader_code}\n")
            print("Need at least 2 decks for this leader.")
            return
        if use_weighted:
            deck_data = load_deck_card_dicts(conn, deck_uids)
        else:
            deck_data = load_deck_card_sets(conn, deck_uids)
    finally:
        conn.close()

    sim_fn = weighted_jaccard if use_weighted else jaccard

    # Same clustering as miru_detect_archetypes
    pairs: list[tuple[str, str, float]] = []
    sim_lookup: dict[tuple[str, str], float] = {}
    n = len(deck_uids)
    for i in range(n):
        for j in range(i + 1, n):
            uid_a, uid_b = deck_uids[i], deck_uids[j]
            sim = sim_fn(deck_data[uid_a], deck_data[uid_b])
            pairs.append((uid_a, uid_b, sim))
            sim_lookup[(uid_a, uid_b)] = sim

    neighbors: dict[str, set[str]] = {uid: set() for uid in deck_uids}
    for uid_a, uid_b, sim in pairs:
        if sim >= args.threshold:
            neighbors[uid_a].add(uid_b)
            neighbors[uid_b].add(uid_a)

    components = connected_components(neighbors, deck_uids)
    clusters = [c for c in components if len(c) >= 2]

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

    clusters_with_avg = [(c, avg_internal_similarity(c)) for c in clusters]
    clusters_with_avg.sort(key=lambda x: (-len(x[0]), -x[1], min(x[0])))
    clusters_to_show = clusters_with_avg[: args.limit]

    dossiers_path = resolve_dossiers_path(args)
    has_dossiers = dossiers_path.is_file()

    print(f"Cluster skeletons for leader {leader_code}")
    print(f"threshold: {args.threshold:.2f}  sim-mode: {args.sim_mode}\n")

    if not clusters_to_show:
        print("No clusters (all decks are singletons at this threshold).")
        return

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = 1")
    try:
        for idx, (comp, _avg_sim) in enumerate(clusters_to_show, start=1):
            cluster_decks = sorted(comp)
            n_decks = len(cluster_decks)
            stats = load_cluster_card_stats(conn, cluster_decks)
            if not stats:
                continue

            # Filter the leader card from skeleton cards
            stats = {code: v for code, v in stats.items() if code != leader_code}

            pct_per_deck = 100.0 / n_decks
            core: list[str] = []
            flex: list[str] = []
            tech: list[str] = []
            for code, (deck_count, _total) in stats.items():
                pct = deck_count * pct_per_deck
                if pct >= CORE_PCT:
                    core.append(code)
                elif pct >= FLEX_PCT:
                    flex.append(code)
                else:
                    tech.append(code)

            # Stable sort by deck_count desc, then code
            def sort_key(c: str) -> tuple[int, str]:
                return (-stats[c][0], c)

            core.sort(key=sort_key)
            flex.sort(key=sort_key)
            tech.sort(key=sort_key)

            all_codes = list(stats.keys())
            names_map, cost_map, trait_map = load_dossiers_enrichment(dossiers_path, all_codes)

            def line_for(code: str) -> str:
                name = names_map.get(code, "")
                if name:
                    return f"  {code}  {name}"
                return f"  {code}"

            print(f"Cluster {idx} ({n_decks} deck(s))\n")
            print("Core cards")
            for code in core:
                print(line_for(code))
            if not core:
                print("  (none)")
            print("Flex cards")
            for code in flex:
                print(line_for(code))
            if not flex:
                print("  (none)")
            print("Tech cards")
            for code in tech:
                print(line_for(code))
            if not tech:
                print("  (none)")

            if has_dossiers:
                # Cost curve: bucket -> total_copies in cluster
                bucket_totals: dict[str, int] = {b: 0 for b in COST_BUCKETS}
                for code, (_dc, total) in stats.items():
                    b = cost_map.get(code)
                    if b and b in bucket_totals:
                        bucket_totals[b] += total
                max_copies = max(bucket_totals.values()) or 1
                print("Cost curve")
                for b in COST_BUCKETS:
                    bar_len = round(_BAR_WIDTH * bucket_totals[b] / max_copies)
                    bar = "#" * bar_len
                    label = f"{b}cp" if b != "8+" else "8+"
                    print(f"  {label:>3}  {bar}")
                # Top traits: trait -> sum of total_copies for cards with that trait
                trait_totals: dict[str, int] = {}
                for code, (_dc, total) in stats.items():
                    for trait in trait_map.get(code, []):
                        trait_totals[trait] = trait_totals.get(trait, 0) + total
                top_trait_list = sorted(
                    trait_totals.items(), key=lambda x: (-x[1], x[0])
                )[:_TOP_TRAITS]
                print("Top traits")
                for trait, _ in top_trait_list:
                    print(f"  {trait}")
                if not top_trait_list:
                    print("  (none)")
            else:
                print("Cost curve")
                print("  (dossiers DB not available)")
                print("Top traits")
                print("  (dossiers DB not available)")
            print()
    finally:
        conn.close()

    if len(clusters_with_avg) > args.limit:
        print(f"(... {len(clusters_with_avg) - args.limit} more cluster(s) omitted; use --limit to show more)")


if __name__ == "__main__":
    main()
