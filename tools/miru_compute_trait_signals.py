"""
miru_compute_trait_signals.py

Read leader_card_stats from miru_deck_intel.db, join attribute/trait facts
from miru_dossiers.db, and build a summarized leader trait signal table.

Traits are stored in miru_dossiers.db as the 'attribute' fact field with
values like "Slash", "Ranged", or "Slash/Special" (slash-separated when a
card carries two attributes).  Each value is split on '/' to produce
individual trait names.  Empty, '-', and '?' values are discarded.

Creates or rebuilds the leader_trait_signals table in miru_deck_intel.db:
    leader_code      TEXT
    format_code      TEXT
    trait_name       TEXT   -- normalised title-cased trait string
    card_count       INTEGER -- unique cards carrying this trait
    deck_appearances INTEGER -- sum of deck_count for cards with this trait
    total_copies     INTEGER -- sum of total_copies for cards with this trait
    updated_at       TEXT
    PRIMARY KEY (leader_code, format_code, trait_name)

Usage:
    python -m tools.miru_compute_trait_signals
    python -m tools.miru_compute_trait_signals --leader-code OP01-001
    python -m tools.miru_compute_trait_signals --dry-run
    python -m tools.miru_compute_trait_signals \\
        --db-path data/miru_deck_intel.db --dossiers-db data/miru_dossiers.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"

# Dossier field name that holds trait/attribute data
_TRAIT_FIELD = "attribute"

# Raw values to discard (after stripping whitespace)
_SKIP_VALUES: frozenset[str] = frozenset({"", "-", "?", "n/a", "none", "unknown"})

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SIGNALS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS leader_trait_signals (
    leader_code      TEXT NOT NULL,
    format_code      TEXT NOT NULL DEFAULT '',
    trait_name       TEXT NOT NULL,
    card_count       INTEGER NOT NULL,
    deck_appearances INTEGER NOT NULL,
    total_copies     INTEGER NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (leader_code, format_code, trait_name)
);
"""

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compute leader trait signals from leader_card_stats + miru_dossiers.db "
            "and write to leader_trait_signals in miru_deck_intel.db."
        )
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
        help=f"Path to miru_dossiers.db (default: {DEFAULT_DOSSIERS_PATH}).",
    )
    parser.add_argument(
        "--leader-code",
        default="",
        metavar="CODE",
        help="Recompute trait signals for this leader only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print signals without writing to the DB.",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def resolve_dossiers_path(args: argparse.Namespace) -> Path:
    return Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH


# ---------------------------------------------------------------------------
# Trait loading from dossiers
# ---------------------------------------------------------------------------


def _parse_traits(raw: str) -> list[str]:
    """
    Split a raw attribute string into individual normalised trait names.

    "Slash/Special" → ["Slash", "Special"]
    "Ranged"        → ["Ranged"]
    "-"             → []
    ""              → []
    """
    traits: list[str] = []
    for part in raw.split("/"):
        t = part.strip().title()
        if t.lower() not in _SKIP_VALUES:
            traits.append(t)
    return traits


def load_trait_map(
    dossiers_path: Path,
    card_codes: list[str],
) -> dict[str, list[str]]:
    """
    Return {canonical_code: [trait, ...]} for the given card codes.

    Prefers verified rows for the 'attribute' field; falls back to any
    stored value if no verified row exists.  Cards with no usable trait
    value are absent from the result.
    """
    if not card_codes or not dossiers_path.exists():
        return {}

    try:
        dconn = sqlite3.connect(f"file:{dossiers_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return {}

    trait_map: dict[str, list[str]] = {}
    try:
        placeholders = ",".join("?" * len(card_codes))
        rows = dconn.execute(
            f"""
            SELECT c.canonical_code, cf.value_text, cf.verification_state
            FROM card_facts cf
            JOIN cards c ON c.id = cf.card_id
            WHERE cf.field_name = '{_TRAIT_FIELD}'
              AND c.canonical_code IN ({placeholders})
            ORDER BY
                CASE cf.verification_state WHEN 'verified' THEN 0 ELSE 1 END,
                cf.updated_at DESC
            """,
            card_codes,
        ).fetchall()

        for code, value, _state in rows:
            if code in trait_map:
                continue  # first row wins (verified first)
            traits = _parse_traits(value or "")
            if traits:
                trait_map[code] = traits

    except sqlite3.Error:
        pass
    finally:
        dconn.close()

    return trait_map


# ---------------------------------------------------------------------------
# Signal row type + aggregation
# ---------------------------------------------------------------------------


class TraitSignalRow(NamedTuple):
    leader_code: str
    format_code: str
    trait_name: str
    card_count: int
    deck_appearances: int
    total_copies: int


def compute_trait_signals(
    intel_conn: sqlite3.Connection,
    trait_map: dict[str, list[str]],
    leader_filter: str = "",
) -> list[TraitSignalRow]:
    """
    Aggregate leader_card_stats into per-(leader, format, trait_name) rows.

    A card with two traits (e.g. "Slash/Special") contributes to both the
    "Slash" and "Special" rows independently.  deck_count and total_copies
    are counted once per card per trait (not double-counted within a trait).

    Cards absent from trait_map are skipped.
    """
    if leader_filter.strip():
        stats_rows = intel_conn.execute(
            """
            SELECT
                leader_code,
                card_code,
                COALESCE(NULLIF(TRIM(format_code), ''), '') AS format_code,
                deck_count,
                total_copies
            FROM leader_card_stats
            WHERE leader_code = ?
            """,
            (leader_filter.upper(),),
        ).fetchall()
    else:
        stats_rows = intel_conn.execute(
            """
            SELECT
                leader_code,
                card_code,
                COALESCE(NULLIF(TRIM(format_code), ''), '') AS format_code,
                deck_count,
                total_copies
            FROM leader_card_stats
            """
        ).fetchall()

    # Accumulator: (leader, format, trait) -> [card_count, deck_appearances, total_copies]
    agg: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])

    for leader_code, card_code, format_code, deck_count, total_copies in stats_rows:
        traits = trait_map.get(card_code)
        if not traits:
            continue
        for trait in traits:
            key = (leader_code, format_code, trait)
            agg[key][0] += 1             # card_count
            agg[key][1] += deck_count    # deck_appearances
            agg[key][2] += total_copies  # total_copies

    return [
        TraitSignalRow(
            leader_code=leader,
            format_code=fmt,
            trait_name=trait,
            card_count=vals[0],
            deck_appearances=vals[1],
            total_copies=vals[2],
        )
        for (leader, fmt, trait), vals in sorted(agg.items())
    ]


# ---------------------------------------------------------------------------
# Schema + write
# ---------------------------------------------------------------------------


def ensure_signals_table(conn: sqlite3.Connection) -> None:
    conn.execute(_SIGNALS_TABLE_DDL)


def rebuild_signals(
    conn: sqlite3.Connection,
    signals: list[TraitSignalRow],
    updated_at: str,
    leader_filter: str = "",
) -> None:
    if leader_filter.strip():
        conn.execute(
            "DELETE FROM leader_trait_signals WHERE leader_code = ?",
            (leader_filter.upper(),),
        )
    else:
        conn.execute("DELETE FROM leader_trait_signals")

    conn.executemany(
        """
        INSERT INTO leader_trait_signals
            (leader_code, format_code, trait_name, card_count,
             deck_appearances, total_copies, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (s.leader_code, s.format_code, s.trait_name,
             s.card_count, s.deck_appearances, s.total_copies, updated_at)
            for s in signals
        ],
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _bar(value: int, max_value: int, width: int = 16) -> str:
    if max_value <= 0:
        return " " * width
    filled = round(width * value / max_value)
    return "#" * filled + " " * (width - filled)


def print_dry_run(signals: list[TraitSignalRow], skipped: int) -> None:
    if not signals:
        print("  No trait signals computed (no attribute data available?).")
        if skipped:
            print(f"  {skipped} card(s) skipped: no trait data in dossiers DB.")
        return

    by_leader: dict[str, dict[str, list[TraitSignalRow]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for s in signals:
        by_leader[s.leader_code][s.format_code].append(s)

    for leader in sorted(by_leader):
        for fmt in sorted(by_leader[leader]):
            rows = sorted(
                by_leader[leader][fmt],
                key=lambda r: (-r.deck_appearances, -r.card_count, r.trait_name),
            )
            fmt_label = f"  [{fmt}]" if fmt else ""
            max_da = max(r.deck_appearances for r in rows)
            print(f"\n  {leader}{fmt_label}")
            print(
                f"  {'trait':<14}  {'cards':>5}  {'d.app':>6}  "
                f"{'copies':>7}  distribution"
            )
            print("  " + "-" * 58)
            for r in rows:
                bar = _bar(r.deck_appearances, max_da)
                print(
                    f"  {r.trait_name:<14}  {r.card_count:>5}  {r.deck_appearances:>6}  "
                    f"{r.total_copies:>7}  [{bar}]"
                )

    if skipped:
        print(f"\n  ({skipped} card(s) skipped: no trait data in dossiers DB)")


def print_summary(
    signals: list[TraitSignalRow],
    skipped: int,
    dry_run: bool,
    db_path: Path,
) -> None:
    combos = len({(s.leader_code, s.format_code) for s in signals})
    unique_traits = len({s.trait_name for s in signals})
    print(
        f"  leader/format combos  : {combos}\n"
        f"  trait rows written    : {len(signals)}\n"
        f"  distinct trait names  : {unique_traits}\n"
        f"  cards skipped (no trait): {skipped}"
    )
    if dry_run:
        print(f"\nDry-run complete. {len(signals)} signal row(s) computed. No DB writes.")
    else:
        print(f"  written to            : {db_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = resolve_db_path(args)
    dossiers_path = resolve_dossiers_path(args)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        print(
            "Run miru_import_decklist.py and miru_summarize_deck_stats.py first.",
            file=sys.stderr,
        )
        return 1

    if not dossiers_path.exists():
        print(
            f"WARNING: Dossiers DB not found at {dossiers_path}. "
            "Trait enrichment will be skipped; no signals can be computed.",
            file=sys.stderr,
        )

    updated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        if args.dry_run:
            intel_conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            intel_conn = sqlite3.connect(str(db_path))
            intel_conn.execute("PRAGMA journal_mode=WAL")
            intel_conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.OperationalError as exc:
        print(f"ERROR: Could not open DB: {exc}", file=sys.stderr)
        return 1

    try:
        tables = {
            r[0]
            for r in intel_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "leader_card_stats" not in tables:
            print(
                "ERROR: Table 'leader_card_stats' not found. "
                "Run miru_summarize_deck_stats.py first.",
                file=sys.stderr,
            )
            return 1

        # Collect all card codes we'll need traits for
        if args.leader_code.strip():
            code_rows = intel_conn.execute(
                "SELECT DISTINCT card_code FROM leader_card_stats WHERE leader_code = ?",
                (args.leader_code.upper(),),
            ).fetchall()
        else:
            code_rows = intel_conn.execute(
                "SELECT DISTINCT card_code FROM leader_card_stats"
            ).fetchall()
        all_codes = [r[0] for r in code_rows]

        trait_map = load_trait_map(dossiers_path, all_codes)
        skipped = len(all_codes) - len(trait_map)

        filter_note = (
            f" (leader filter: {args.leader_code.upper()})" if args.leader_code else ""
        )
        print(
            f"Loaded traits for {len(trait_map)}/{len(all_codes)} card(s){filter_note}. "
            f"{skipped} skipped (no trait data)."
        )

        signals = compute_trait_signals(intel_conn, trait_map, leader_filter=args.leader_code)

        if args.dry_run:
            print_dry_run(signals, skipped)
            print()
            print_summary(signals, skipped, dry_run=True, db_path=db_path)
            return 0

        ensure_signals_table(intel_conn)
        with intel_conn:
            rebuild_signals(intel_conn, signals, updated_at, leader_filter=args.leader_code)

        print_summary(signals, skipped, dry_run=False, db_path=db_path)

    except sqlite3.Error as exc:
        print(f"ERROR: SQLite error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            intel_conn.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
