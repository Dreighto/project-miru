"""
miru_archetype_preview.py

Read-only Leader Hub archetype preview for one leader.

Reads stored archetype tables from miru_deck_intel.db (archetype_profiles,
archetype_profile_cards, archetype_profile_traits, archetype_profile_cost_curve)
and prints a concise human-readable summary. No clustering or skeleton
computation; no DB writes.

Optionally enriches card codes with names from miru_dossiers.db.

Confidence context (based on total decks sampled for the leader):
  low    : < 5 decks
  medium : 5-14 decks
  strong : 15+ decks

Usage
-----
    python -m tools.miru_archetype_preview --leader-code OP01-001
    python -m tools.miru_archetype_preview --leader-code OP02-001 --limit 3
    python -m tools.miru_archetype_preview --leader-code OP01-001 --dossiers-db data/miru_dossiers.db

Limitations / TODOs
------------------
- Only reads persisted profiles; run miru_store_archetype_profiles first if empty.
- format_code filter not exposed; uses stored '' (all formats).
- No per-archetype drill-down or export.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"

FORMAT_CODE_DEFAULT = ""
COST_BUCKET_ORDER = ("0", "1", "2", "3", "4", "5", "6", "7", "8+")
BAR_WIDTH = 10
TOP_TRAITS = 5

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
            "Print a read-only archetype preview for one leader from "
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
        default=5,
        metavar="N",
        help="Maximum number of archetypes to show (default: 5).",
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
        help=f"Path to miru_dossiers.db for card names (default: {DEFAULT_DOSSIERS_PATH}).",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def resolve_dossiers_path(args: argparse.Namespace) -> Path:
    return Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH


def load_card_names(dossiers_path: Path, card_codes: list[str]) -> dict[str, str]:
    """Return {card_code: card_name}. Returns {} if path missing or on error."""
    if not card_codes or not dossiers_path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"file:{dossiers_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return {}
    out: dict[str, str] = {}
    try:
        placeholders = ",".join("?" * len(card_codes))
        for row in conn.execute(
            f"SELECT canonical_code, card_name FROM cards WHERE canonical_code IN ({placeholders})",
            card_codes,
        ).fetchall():
            out[row[0]] = (row[1] or "").strip()
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return out


def main() -> None:
    args = build_parser().parse_args()
    db_path = resolve_db_path(args)
    if not db_path.is_file():
        print(f"DB not found: {db_path}", file=sys.stderr)
        sys.exit(1)

    leader_code = args.leader_code.upper()
    format_code = FORMAT_CODE_DEFAULT

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = 1")
    try:
        profiles = conn.execute(
            """
            SELECT archetype_id, deck_count, avg_similarity
            FROM archetype_profiles
            WHERE leader_code = ? AND format_code = ?
            ORDER BY deck_count DESC, avg_similarity DESC, archetype_id ASC
            """,
            (leader_code, format_code),
        ).fetchall()

        # Total decks for confidence context
        row = conn.execute(
            "SELECT COUNT(DISTINCT deck_uid) FROM decklists WHERE leader_code = ?",
            (leader_code,),
        ).fetchone()
        total_decks = row[0] if row else 0
    except sqlite3.OperationalError:
        total_decks = 0
        profiles = []
    finally:
        conn.close()

    confidence = sample_confidence(total_decks)

    if not profiles:
        print(f"Leader archetype preview for {leader_code}\n")
        print("No stored archetype profiles for this leader.")
        print("Run: python -m tools.miru_store_archetype_profiles --leader-code ...")
        return

    dossiers_path = resolve_dossiers_path(args)
    has_dossiers = dossiers_path.is_file()

    print(f"Leader archetype preview for {leader_code}")
    print(f"  {total_decks} deck(s) sampled  |  confidence: {confidence}\n")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = 1")
    try:
        for idx, (archetype_id, deck_count, avg_similarity) in enumerate(
            profiles[: args.limit], start=1
        ):
            cluster_confidence = sample_confidence(deck_count)
            print(f"Cluster {idx}  [{archetype_id}]")
            print(f"  decks: {deck_count}  |  avg similarity: {avg_similarity:.2f}  |  confidence: {cluster_confidence}\n")

            cards = conn.execute(
                """
                SELECT card_code, role_label, deck_count, total_copies
                FROM archetype_profile_cards
                WHERE leader_code = ? AND format_code = ? AND archetype_id = ?
                ORDER BY role_label ASC, deck_count DESC, card_code ASC
                """,
                (leader_code, format_code, archetype_id),
            ).fetchall()

            # Filter leader card from display (defensive)
            cards = [r for r in cards if r[0] != leader_code]

            all_codes = [r[0] for r in cards]
            names_map = load_card_names(dossiers_path, all_codes) if has_dossiers else {}

            def line_for(code: str) -> str:
                name = names_map.get(code, "")
                if name:
                    return f"    {code}  {name}"
                return f"    {code}"

            for role in ("core", "flex", "tech"):
                role_cards = [r for r in cards if r[1] == role]
                print(f"  {role.capitalize()} cards")
                if not role_cards:
                    print("    (none)")
                else:
                    for r in role_cards:
                        print(line_for(r[0]))
                print()

            curve = conn.execute(
                """
                SELECT cost_bucket, total_copies
                FROM archetype_profile_cost_curve
                WHERE leader_code = ? AND format_code = ? AND archetype_id = ?
                """,
                (leader_code, format_code, archetype_id),
            ).fetchall()
            curve_map = {r[0]: r[1] for r in curve}
            max_copies = max(curve_map.values()) if curve_map else 1
            print("  Cost curve")
            if not curve_map:
                print("    (none)")
            else:
                for b in COST_BUCKET_ORDER:
                    total = curve_map.get(b, 0)
                    bar_len = round(BAR_WIDTH * total / max_copies)
                    bar = "#" * bar_len
                    label = f"{b}cp" if b != "8+" else "8+"
                    print(f"    {label:>3}  {bar}")
            print()

            traits = conn.execute(
                """
                SELECT trait_name, total_copies
                FROM archetype_profile_traits
                WHERE leader_code = ? AND format_code = ? AND archetype_id = ?
                ORDER BY total_copies DESC, trait_name ASC
                LIMIT ?
                """,
                (leader_code, format_code, archetype_id, TOP_TRAITS),
            ).fetchall()
            print("  Top traits")
            if not traits:
                print("    (none)")
            else:
                for name, _ in traits:
                    print(f"    {name}")
            print()
    finally:
        conn.close()

    if len(profiles) > args.limit:
        print(f"(... {len(profiles) - args.limit} more archetype(s); use --limit to show more)")


if __name__ == "__main__":
    main()
