"""
miru_store_archetype_profiles.py

Detect archetype clusters for a leader, compute skeleton summaries, and persist
them into miru_deck_intel.db (archetype_profiles, archetype_profile_cards,
archetype_profile_traits, archetype_profile_cost_curve).

Uses the same clustering as miru_detect_archetypes and the same skeleton
logic as miru_cluster_skeletons. Only clusters with size >= 2 are stored.
If dossiers DB is missing, card roles are still stored; traits and cost curve
are skipped for that run.

The leader card itself is always excluded from stored card rows.

Two similarity modes are supported (default: weighted):
  weighted   : sum(min(a_i, b_i)) / sum(max(a_i, b_i))
  unweighted : classic Jaccard over unique card-code sets

Usage
-----
    python -m tools.miru_store_archetype_profiles --leader-code OP01-001
    python -m tools.miru_store_archetype_profiles --leader-code OP02-001 --threshold 0.75
    python -m tools.miru_store_archetype_profiles --leader-code OP01-001 --dry-run

Limitations / TODOs
------------------
- format_code is stored as '' (all formats combined); no per-format split yet.
- Full rebuild per leader only; no incremental update.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.miru_cluster_skeletons import (
    COST_BUCKETS,
    CORE_PCT,
    FLEX_PCT,
    load_cluster_card_stats,
    load_dossiers_enrichment,
)
from tools.miru_detect_archetypes import (
    SIM_MODE_DEFAULT,
    SIM_MODE_UNWEIGHTED,
    SIM_MODE_WEIGHTED,
    THRESHOLD_SIMILARITY,
    connected_components,
    ensure_tables as ensure_intel_tables,
    jaccard,
    load_deck_card_dicts,
    load_deck_card_sets,
    load_deck_uids,
    weighted_jaccard,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"

FORMAT_CODE_DEFAULT = ""

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archetype_profiles (
    leader_code   TEXT NOT NULL,
    format_code   TEXT NOT NULL DEFAULT '',
    archetype_id  TEXT NOT NULL,
    deck_count    INTEGER NOT NULL,
    avg_similarity REAL NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (leader_code, format_code, archetype_id)
);

CREATE TABLE IF NOT EXISTS archetype_profile_cards (
    leader_code   TEXT NOT NULL,
    format_code   TEXT NOT NULL DEFAULT '',
    archetype_id  TEXT NOT NULL,
    card_code     TEXT NOT NULL,
    role_label    TEXT NOT NULL,
    deck_count    INTEGER NOT NULL,
    total_copies  INTEGER NOT NULL,
    PRIMARY KEY (leader_code, format_code, archetype_id, card_code)
);

CREATE TABLE IF NOT EXISTS archetype_profile_traits (
    leader_code   TEXT NOT NULL,
    format_code   TEXT NOT NULL DEFAULT '',
    archetype_id  TEXT NOT NULL,
    trait_name    TEXT NOT NULL,
    total_copies  INTEGER NOT NULL,
    PRIMARY KEY (leader_code, format_code, archetype_id, trait_name)
);

CREATE TABLE IF NOT EXISTS archetype_profile_cost_curve (
    leader_code   TEXT NOT NULL,
    format_code   TEXT NOT NULL DEFAULT '',
    archetype_id  TEXT NOT NULL,
    cost_bucket   TEXT NOT NULL,
    total_copies  INTEGER NOT NULL,
    PRIMARY KEY (leader_code, format_code, archetype_id, cost_bucket)
);
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Detect archetype clusters for a leader and persist profile "
            "summaries into miru_deck_intel.db."
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
        help=f"Path to miru_dossiers.db for traits/cost (default: {DEFAULT_DOSSIERS_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print summary only; do not write to the DB.",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def resolve_dossiers_path(args: argparse.Namespace) -> Path:
    return Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH


def ensure_archetype_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def main() -> None:
    args = build_parser().parse_args()
    db_path = resolve_db_path(args)
    if not db_path.is_file():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    dossiers_path = resolve_dossiers_path(args)
    has_dossiers = dossiers_path.is_file()
    leader_code = args.leader_code.upper()
    use_weighted = args.sim_mode == SIM_MODE_WEIGHTED

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = 0")
    try:
        ensure_intel_tables(conn)
        ensure_archetype_tables(conn)

        deck_uids = load_deck_uids(conn, leader_code)
        if len(deck_uids) < 2:
            print("Need at least 2 decks for this leader.")
            return
        if use_weighted:
            deck_data = load_deck_card_dicts(conn, deck_uids)
        else:
            deck_data = load_deck_card_sets(conn, deck_uids)
    finally:
        conn.close()

    sim_fn = weighted_jaccard if use_weighted else jaccard

    # Clustering (same as miru_detect_archetypes)
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

    format_code = FORMAT_CODE_DEFAULT
    updated_at = datetime.now(timezone.utc).isoformat()

    profile_rows: list[tuple[str, str, str, int, float, str]] = []
    card_rows: list[tuple[str, str, str, str, str, int, int]] = []
    trait_rows: list[tuple[str, str, str, str, int]] = []
    curve_rows: list[tuple[str, str, str, str, int]] = []

    conn = sqlite3.connect(str(db_path))
    try:
        for idx, (comp, avg_sim) in enumerate(clusters_with_avg, start=1):
            archetype_id = f"cluster_{idx:03d}"
            cluster_decks = sorted(comp)
            n_decks = len(cluster_decks)
            stats = load_cluster_card_stats(conn, cluster_decks)
            if not stats:
                continue

            # Filter the leader card from skeleton cards
            stats = {code: v for code, v in stats.items() if code != leader_code}

            profile_rows.append(
                (leader_code, format_code, archetype_id, n_decks, round(avg_sim, 6), updated_at)
            )

            pct_per_deck = 100.0 / n_decks
            all_codes = list(stats.keys())
            names_map, cost_map, trait_map = (
                load_dossiers_enrichment(dossiers_path, all_codes) if has_dossiers else ({}, {}, {})
            )

            for code, (deck_count, total_copies) in stats.items():
                pct = deck_count * pct_per_deck
                if pct >= CORE_PCT:
                    role = "core"
                elif pct >= FLEX_PCT:
                    role = "flex"
                else:
                    role = "tech"
                card_rows.append(
                    (leader_code, format_code, archetype_id, code, role, deck_count, total_copies)
                )

            if has_dossiers:
                for b in COST_BUCKETS:
                    total = sum(
                        stats[code][1]
                        for code in all_codes
                        if cost_map.get(code) == b
                    )
                    if total > 0:
                        curve_rows.append(
                            (leader_code, format_code, archetype_id, b, total)
                        )
                trait_totals: dict[str, int] = {}
                for code, (_dc, total) in stats.items():
                    for trait in trait_map.get(code, []):
                        trait_totals[trait] = trait_totals.get(trait, 0) + total
                for trait, total in trait_totals.items():
                    trait_rows.append(
                        (leader_code, format_code, archetype_id, trait, total)
                    )
    finally:
        conn.close()

    if args.dry_run:
        print(f"Dry run: leader {leader_code}, threshold {args.threshold:.2f}, sim-mode {args.sim_mode}")
        print(f"  Clusters: {len(profile_rows)}")
        print(f"  Profile rows: {len(profile_rows)}")
        print(f"  Card rows: {len(card_rows)}")
        print(f"  Trait rows: {len(trait_rows)} (dossiers: {has_dossiers})")
        print(f"  Cost curve rows: {len(curve_rows)} (dossiers: {has_dossiers})")
        print("  No writes performed.")
        return

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM archetype_profiles WHERE leader_code = ?", (leader_code,))
        conn.execute("DELETE FROM archetype_profile_cards WHERE leader_code = ?", (leader_code,))
        conn.execute("DELETE FROM archetype_profile_traits WHERE leader_code = ?", (leader_code,))
        conn.execute("DELETE FROM archetype_profile_cost_curve WHERE leader_code = ?", (leader_code,))

        if profile_rows:
            conn.executemany(
                "INSERT INTO archetype_profiles (leader_code, format_code, archetype_id, deck_count, avg_similarity, updated_at) VALUES (?,?,?,?,?,?)",
                profile_rows,
            )
            conn.executemany(
                "INSERT INTO archetype_profile_cards (leader_code, format_code, archetype_id, card_code, role_label, deck_count, total_copies) VALUES (?,?,?,?,?,?,?)",
                card_rows,
            )
            if trait_rows:
                conn.executemany(
                    "INSERT INTO archetype_profile_traits (leader_code, format_code, archetype_id, trait_name, total_copies) VALUES (?,?,?,?,?)",
                    trait_rows,
                )
            if curve_rows:
                conn.executemany(
                    "INSERT INTO archetype_profile_cost_curve (leader_code, format_code, archetype_id, cost_bucket, total_copies) VALUES (?,?,?,?,?)",
                    curve_rows,
                )
        conn.commit()
        print(
            f"Stored {len(profile_rows)} archetype profile(s) for leader {leader_code} "
            f"(sim-mode: {args.sim_mode})."
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
