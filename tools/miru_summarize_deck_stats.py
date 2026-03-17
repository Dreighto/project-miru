"""
miru_summarize_deck_stats.py

Read deck intelligence data from miru_deck_intel.db and rebuild the
leader_card_stats summary table.

One row per (leader_code, card_code, format_code) triple.  Re-running is always
safe: the table is rebuilt from scratch each time via DELETE + INSERT inside a
single transaction, so partial runs leave no residue.

Schema created here:
    leader_card_stats (
        leader_code    TEXT NOT NULL,
        card_code      TEXT NOT NULL,
        format_code    TEXT NOT NULL DEFAULT '',
        deck_count     INTEGER NOT NULL DEFAULT 0,
        total_copies   INTEGER NOT NULL DEFAULT 0,
        last_seen      TEXT NOT NULL DEFAULT '',
        updated_at     TEXT NOT NULL,
        PRIMARY KEY (leader_code, card_code, format_code)
    )

last_seen  -- latest decklists.created_at value among matching decks
updated_at -- UTC timestamp of this summarize run

Usage:
    python -m tools.miru_summarize_deck_stats
    python -m tools.miru_summarize_deck_stats --db-path data/miru_deck_intel.db
    python -m tools.miru_summarize_deck_stats --dry-run
    python -m tools.miru_summarize_deck_stats --leader-code OP01-001
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_STATS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS leader_card_stats (
    leader_code  TEXT NOT NULL,
    card_code    TEXT NOT NULL,
    format_code  TEXT NOT NULL DEFAULT '',
    deck_count   INTEGER NOT NULL DEFAULT 0,
    total_copies INTEGER NOT NULL DEFAULT 0,
    last_seen    TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (leader_code, card_code, format_code)
);
"""

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild leader_card_stats summary table from miru_deck_intel.db."
    )
    parser.add_argument(
        "db_path_positional",
        nargs="?",
        metavar="DB_PATH",
        help="Path to miru_deck_intel.db (positional; overridden by --db-path if both given).",
    )
    parser.add_argument(
        "--db-path",
        default="",
        metavar="PATH",
        help=f"Path to miru_deck_intel.db (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute stats and print the summary without writing to the DB.",
    )
    parser.add_argument(
        "--leader-code",
        default="",
        metavar="CODE",
        help="Restrict computation to a single leader code (useful for spot-checks).",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    if args.db_path:
        return Path(args.db_path)
    if args.db_path_positional:
        return Path(args.db_path_positional)
    return DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# Source-data query
# ---------------------------------------------------------------------------


class StatRow(NamedTuple):
    leader_code: str
    card_code: str
    format_code: str
    deck_count: int
    total_copies: int
    last_seen: str


def compute_stats(conn: sqlite3.Connection, leader_filter: str = "") -> list[StatRow]:
    """
    Aggregate deck_entries + decklists into one row per
    (leader_code, card_code, format_code).

    leader_filter: if non-empty, restrict to that leader_code only.
    """
    base_sql = """
        SELECT
            d.leader_code,
            de.card_code,
            COALESCE(NULLIF(TRIM(d.format_code), ''), '')  AS format_code,
            COUNT(DISTINCT de.deck_uid)                    AS deck_count,
            SUM(de.quantity)                               AS total_copies,
            MAX(COALESCE(d.created_at, ''))                AS last_seen
        FROM deck_entries de
        JOIN decklists d ON d.deck_uid = de.deck_uid
        {where}
        GROUP BY d.leader_code, de.card_code, format_code
        ORDER BY d.leader_code, deck_count DESC, de.card_code
    """
    if leader_filter.strip():
        sql = base_sql.format(where="WHERE d.leader_code = ?")
        cursor = conn.execute(sql, (leader_filter.strip().upper(),))
    else:
        sql = base_sql.format(where="")
        cursor = conn.execute(sql)

    return [
        StatRow(
            leader_code=row[0],
            card_code=row[1],
            format_code=row[2],
            deck_count=row[3],
            total_copies=row[4],
            last_seen=row[5] or "",
        )
        for row in cursor.fetchall()
    ]


# ---------------------------------------------------------------------------
# Schema + write
# ---------------------------------------------------------------------------


def ensure_stats_table(conn: sqlite3.Connection) -> None:
    conn.execute(_STATS_TABLE_DDL)


def rebuild_stats(
    conn: sqlite3.Connection,
    stats: list[StatRow],
    updated_at: str,
    leader_filter: str = "",
) -> None:
    """
    Replace stats inside a single transaction.

    When leader_filter is set, only rows for that leader are removed+replaced,
    leaving other leaders untouched.  When leader_filter is empty, the entire
    table is cleared.
    """
    if leader_filter.strip():
        conn.execute(
            "DELETE FROM leader_card_stats WHERE leader_code = ?",
            (leader_filter.strip().upper(),),
        )
    else:
        conn.execute("DELETE FROM leader_card_stats")

    conn.executemany(
        """
        INSERT INTO leader_card_stats
            (leader_code, card_code, format_code, deck_count, total_copies, last_seen, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row.leader_code,
                row.card_code,
                row.format_code,
                row.deck_count,
                row.total_copies,
                row.last_seen,
                updated_at,
            )
            for row in stats
        ],
    )


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def print_summary(stats: list[StatRow], dry_run: bool, db_path: Path) -> None:
    """Print a concise human-readable summary of the computed stats."""
    if not stats:
        print("No stats to report (no deck entries found).")
        return

    leaders: set[str] = set()
    formats: set[str] = set()
    for row in stats:
        leaders.add(row.leader_code)
        if row.format_code:
            formats.add(row.format_code)

    total_decks_approx = sum(r.deck_count for r in stats) // max(len(stats) // len(leaders), 1)
    _ = total_decks_approx  # not printed; kept for future use

    print(f"  unique (leader, card, format) pairs : {len(stats)}")
    print(f"  leaders covered                     : {len(leaders)}")
    print(f"  formats covered                     : {len(formats) or '(none stored)'}")

    if dry_run:
        print()
        # Show top-5 most common cards per leader as a preview
        _print_top_cards(stats, n=5)
        print(f"Dry-run complete. {len(stats)} stat row(s) computed. No DB writes.")
    else:
        print(f"  written to                          : {db_path}")


def _print_top_cards(stats: list[StatRow], n: int = 5) -> None:
    """Print the top-N most-used cards per leader (dry-run preview)."""
    from collections import defaultdict

    by_leader: dict[str, list[StatRow]] = defaultdict(list)
    for row in stats:
        by_leader[row.leader_code].append(row)

    for leader in sorted(by_leader):
        top = sorted(by_leader[leader], key=lambda r: (-r.deck_count, -r.total_copies))[:n]
        print(f"  {leader}  (top {min(n, len(top))} cards):")
        for r in top:
            fmt = f" [{r.format_code}]" if r.format_code else ""
            print(f"    {r.card_code:<14}  {r.deck_count} deck(s)  {r.total_copies} copies{fmt}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = resolve_db_path(args)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        print(
            "Run tools/miru_import_decklist.py first to create the database.",
            file=sys.stderr,
        )
        return 1

    updated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Confirm source tables exist
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for required in ("decklists", "deck_entries"):
            if required not in tables:
                print(
                    f"ERROR: Required table '{required}' not found in {db_path}. "
                    "Was the DB created by miru_import_decklist.py?",
                    file=sys.stderr,
                )
                conn.close()
                return 1

        stats = compute_stats(conn, leader_filter=args.leader_code)

        decks_scanned = (
            conn.execute("SELECT COUNT(*) FROM decklists").fetchone()[0]
        )

        print(
            f"Scanned {decks_scanned} deck(s) in {db_path.name}"
            + (f" (leader filter: {args.leader_code.upper()})" if args.leader_code else "")
        )

        if args.dry_run:
            print_summary(stats, dry_run=True, db_path=db_path)
            conn.close()
            return 0

        ensure_stats_table(conn)
        with conn:
            rebuild_stats(conn, stats, updated_at, leader_filter=args.leader_code)

        print_summary(stats, dry_run=False, db_path=db_path)

    except sqlite3.Error as exc:
        print(f"ERROR: SQLite error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
