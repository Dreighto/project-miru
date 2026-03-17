"""
miru_bundle_leader_insight.py

Bundled read-only leader insight layer for Project Miru.

For a given leader_code, loads all stored intelligence from miru_deck_intel.db
(leader_card_signals, leader_cost_curves, leader_trait_signals, archetype_*)
and prints one concise bundled summary. No recomputation; no DB writes.
Optionally enriches card/leader names from miru_dossiers.db.

Confidence context (based on total decks sampled for the leader):
  low    : < 5 decks
  medium : 5-14 decks
  strong : 15+ decks

Usage
-----
    python -m tools.miru_bundle_leader_insight --leader-code OP01-001
    python -m tools.miru_bundle_leader_insight --leader-code OP02-001 --limit 3
    python -m tools.miru_bundle_leader_insight --leader-code OP01-001 --dossiers-db data/miru_dossiers.db

Limitations / TODOs
------------------
- Reads only stored tables; run compute/store pipelines if data is missing.
- format_code fixed to '' (all formats).
- Single bundled view only; no export or UI yet.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"

FORMAT_CODE = ""
COST_BUCKET_ORDER = ("0", "1", "2", "3", "4", "5", "6", "7", "8+")
BAR_WIDTH = 10
DEFAULT_ARCHETYPE_LIMIT = 3
DEFAULT_CARD_LIMIT = 10
DEFAULT_TRAIT_LIMIT = 5

# Confidence tiers (total deck count for the leader)
_CONFIDENCE_LOW_MAX = 4     # 1-4 decks  -> "low"
_CONFIDENCE_MED_MAX = 14    # 5-14 decks -> "medium"
                             # 15+        -> "strong"


def sample_confidence(n: int) -> str:
    """Return a tiered confidence label based on sample deck count."""
    if n <= _CONFIDENCE_LOW_MAX:
        return "low"
    if n <= _CONFIDENCE_MED_MAX:
        return "medium"
    return "strong"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print a bundled read-only leader insight summary from "
            "stored tables in miru_deck_intel.db."
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
        default=DEFAULT_ARCHETYPE_LIMIT,
        metavar="N",
        help=f"Max archetypes in snapshots (default: {DEFAULT_ARCHETYPE_LIMIT}).",
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
        help=f"Path to miru_dossiers.db for names (default: {DEFAULT_DOSSIERS_PATH}).",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def resolve_dossiers_path(args: argparse.Namespace) -> Path:
    return Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH


def load_leader_name(dossiers_path: Path, leader_code: str) -> str:
    if not dossiers_path.is_file():
        return ""
    try:
        conn = sqlite3.connect(f"file:{dossiers_path}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT card_name FROM cards WHERE canonical_code = ?",
            (leader_code.upper(),),
        ).fetchone()
        conn.close()
        return (row[0] or "").strip() if row else ""
    except (sqlite3.Error, TypeError):
        return ""


def load_card_names(dossiers_path: Path, card_codes: list[str]) -> dict[str, str]:
    if not card_codes or not dossiers_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{dossiers_path}?mode=ro", uri=True)
        placeholders = ",".join("?" * len(card_codes))
        rows = conn.execute(
            f"SELECT canonical_code, card_name FROM cards WHERE canonical_code IN ({placeholders})",
            card_codes,
        ).fetchall()
        conn.close()
        return {r[0]: (r[1] or "").strip() for r in rows}
    except sqlite3.Error:
        return {}


def main() -> None:
    args = build_parser().parse_args()
    db_path = resolve_db_path(args)
    if not db_path.is_file():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    leader_code = args.leader_code.upper()
    dossiers_path = resolve_dossiers_path(args)
    has_dossiers = dossiers_path.is_file()
    leader_name = load_leader_name(dossiers_path, leader_code) if has_dossiers else ""

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = 1")
    conn.row_factory = sqlite3.Row

    # Check which tables exist
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?,?,?,?,?,?)",
            (
                "decklists",
                "leader_card_signals",
                "leader_cost_curves",
                "leader_trait_signals",
                "archetype_profiles",
                "archetype_profile_cards",
                "archetype_profile_traits",
                "archetype_profile_cost_curve",
            ),
        ).fetchall()
    }

    # Leader overview
    decks_sampled = 0
    if "decklists" in tables:
        row = conn.execute(
            "SELECT COUNT(DISTINCT deck_uid) FROM decklists WHERE leader_code = ?",
            (leader_code,),
        ).fetchone()
        decks_sampled = row[0] if row else 0

    archetypes_stored = 0
    if "archetype_profiles" in tables:
        row = conn.execute(
            "SELECT COUNT(*) FROM archetype_profiles WHERE leader_code = ? AND format_code = ?",
            (leader_code, FORMAT_CODE),
        ).fetchone()
        archetypes_stored = row[0] if row else 0

    confidence = sample_confidence(decks_sampled)

    print("Leader overview")
    print(f"  leader code:     {leader_code}")
    if leader_name:
        print(f"  leader name:     {leader_name}")
    print(f"  decks sampled:   {decks_sampled}")
    print(f"  archetypes:      {archetypes_stored}")
    print(f"  confidence:      {confidence}")
    print()

    # Popular Builds (top archetypes)
    print("Popular Builds")
    if "archetype_profiles" not in tables or archetypes_stored == 0:
        print("  (no stored archetypes)")
    else:
        profiles = conn.execute(
            """
            SELECT archetype_id, deck_count, avg_similarity
            FROM archetype_profiles
            WHERE leader_code = ? AND format_code = ?
            ORDER BY deck_count DESC, avg_similarity DESC, archetype_id ASC
            LIMIT ?
            """,
            (leader_code, FORMAT_CODE, args.limit),
        ).fetchall()
        for r in profiles:
            print(f"  {r['archetype_id']}  decks={r['deck_count']}  avg_sim={r['avg_similarity']:.2f}")
    print()

    # Overall Core Cards (leader_card_signals, role_label = core)
    print("Overall Core Cards")
    if "leader_card_signals" not in tables:
        print("  (no signals table)")
    else:
        rows = conn.execute(
            """
            SELECT card_code, usage_percent, deck_count
            FROM leader_card_signals
            WHERE leader_code = ? AND format_code = ? AND role_label = 'core'
              AND card_code != ?
            ORDER BY usage_percent DESC, card_code ASC
            LIMIT ?
            """,
            (leader_code, FORMAT_CODE, leader_code, DEFAULT_CARD_LIMIT),
        ).fetchall()
        if not rows:
            print("  (none)")
        else:
            codes = [r["card_code"] for r in rows]
            names = load_card_names(dossiers_path, codes) if has_dossiers else {}
            for r in rows:
                code = r["card_code"]
                name = names.get(code, "")
                extra = f"  {name}" if name else ""
                print(f"    {code}  {r['usage_percent']:.0%}{extra}")
    print()

    # Overall Flex Cards
    print("Overall Flex Cards")
    if "leader_card_signals" not in tables:
        print("  (no signals table)")
    else:
        rows = conn.execute(
            """
            SELECT card_code, usage_percent, deck_count
            FROM leader_card_signals
            WHERE leader_code = ? AND format_code = ? AND role_label = 'flex'
              AND card_code != ?
            ORDER BY usage_percent DESC, card_code ASC
            LIMIT ?
            """,
            (leader_code, FORMAT_CODE, leader_code, DEFAULT_CARD_LIMIT),
        ).fetchall()
        if not rows:
            print("  (none)")
        else:
            codes = [r["card_code"] for r in rows]
            names = load_card_names(dossiers_path, codes) if has_dossiers else {}
            for r in rows:
                code = r["card_code"]
                name = names.get(code, "")
                extra = f"  {name}" if name else ""
                print(f"    {code}  {r['usage_percent']:.0%}{extra}")
    print()

    # Overall Cost Curve
    print("Overall Cost Curve")
    if "leader_cost_curves" not in tables:
        print("  (no cost curves table)")
    else:
        rows = conn.execute(
            """
            SELECT cost_bucket, total_copies
            FROM leader_cost_curves
            WHERE leader_code = ? AND format_code = ?
            ORDER BY cost_bucket ASC
            """,
            (leader_code, FORMAT_CODE),
        ).fetchall()
        curve = {r["cost_bucket"]: r["total_copies"] for r in rows}
        if not curve:
            print("  (none)")
        else:
            max_c = max(curve.values())
            for b in COST_BUCKET_ORDER:
                total = curve.get(b, 0)
                bar_len = round(BAR_WIDTH * total / max_c) if max_c else 0
                label = f"{b}cp" if b != "8+" else "8+"
                print(f"    {label:>3}  {'#' * bar_len}")
    print()

    # Overall Top Traits
    print("Overall Top Traits")
    if "leader_trait_signals" not in tables:
        print("  (no trait signals table)")
    else:
        rows = conn.execute(
            """
            SELECT trait_name, deck_appearances, total_copies
            FROM leader_trait_signals
            WHERE leader_code = ? AND format_code = ?
            ORDER BY deck_appearances DESC, total_copies DESC, trait_name ASC
            LIMIT ?
            """,
            (leader_code, FORMAT_CODE, DEFAULT_TRAIT_LIMIT),
        ).fetchall()
        if not rows:
            print("  (none)")
        else:
            for r in rows:
                print(f"    {r['trait_name']}  (d.app={r['deck_appearances']})")
    print()

    # Archetype Snapshots (top 1-3 from stored archetype tables)
    print("Archetype Snapshots")
    if "archetype_profiles" not in tables or archetypes_stored == 0:
        print("  (no stored archetypes)")
    else:
        profiles = conn.execute(
            """
            SELECT archetype_id, deck_count, avg_similarity
            FROM archetype_profiles
            WHERE leader_code = ? AND format_code = ?
            ORDER BY deck_count DESC, avg_similarity DESC, archetype_id ASC
            LIMIT ?
            """,
            (leader_code, FORMAT_CODE, args.limit),
        ).fetchall()

        for idx, prof in enumerate(profiles, start=1):
            aid = prof["archetype_id"]
            cluster_confidence = sample_confidence(prof["deck_count"])
            print(
                f"  Snapshot {idx}: {aid}  decks={prof['deck_count']}"
                f"  avg_sim={prof['avg_similarity']:.2f}  confidence={cluster_confidence}"
            )

            cards = conn.execute(
                """
                SELECT card_code, role_label
                FROM archetype_profile_cards
                WHERE leader_code = ? AND format_code = ? AND archetype_id = ?
                  AND card_code != ?
                ORDER BY role_label ASC, card_code ASC
                """,
                (leader_code, FORMAT_CODE, aid, leader_code),
            ).fetchall()
            all_codes = [r["card_code"] for r in cards]
            names = load_card_names(dossiers_path, all_codes) if has_dossiers else {}
            for role in ("core", "flex", "tech"):
                role_cards = [r for r in cards if r["role_label"] == role]
                if not role_cards:
                    continue
                print(f"    {role.capitalize()}: " + ", ".join(
                    r["card_code"] + (f" ({names.get(r['card_code'], '')})" if names.get(r["card_code"]) else "")
                    for r in role_cards[:5]
                ))
                if len(role_cards) > 5:
                    print(f"      ... +{len(role_cards) - 5} more")

            curve_rows = conn.execute(
                """
                SELECT cost_bucket, total_copies
                FROM archetype_profile_cost_curve
                WHERE leader_code = ? AND format_code = ? AND archetype_id = ?
                """,
                (leader_code, FORMAT_CODE, aid),
            ).fetchall()
            c_map = {r["cost_bucket"]: r["total_copies"] for r in curve_rows}
            if c_map:
                mx = max(c_map.values())
                bars = " ".join(
                    f"{b}cp:{'#' * (round(BAR_WIDTH * c_map.get(b, 0) / mx) if mx else 0)}"
                    for b in COST_BUCKET_ORDER if c_map.get(b, 0) > 0
                )
                print(f"    Cost: {bars}")

            traits = conn.execute(
                """
                SELECT trait_name
                FROM archetype_profile_traits
                WHERE leader_code = ? AND format_code = ? AND archetype_id = ?
                ORDER BY total_copies DESC
                LIMIT ?
                """,
                (leader_code, FORMAT_CODE, aid, DEFAULT_TRAIT_LIMIT),
            ).fetchall()
            if traits:
                print(f"    Traits: {', '.join(r['trait_name'] for r in traits)}")
            print()

    conn.close()


if __name__ == "__main__":
    main()
