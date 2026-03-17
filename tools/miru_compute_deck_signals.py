"""
miru_compute_deck_signals.py

Read leader_card_stats + decklists from miru_deck_intel.db and compute
per-card usage signals for each leader.

Creates or rebuilds the leader_card_signals table:
    leader_code     TEXT
    card_code       TEXT
    format_code     TEXT
    deck_count      INTEGER
    total_copies    INTEGER
    usage_percent   REAL        -- deck_count / total_decks_for_leader
    avg_copies      REAL        -- total_copies / deck_count
    role_label      TEXT        -- core / flex / tech
    updated_at      TEXT

Role thresholds (MVP):
    usage_percent >= THRESHOLD_CORE  (0.60) → "core"
    usage_percent >= THRESHOLD_FLEX  (0.30) → "flex"
    otherwise                                → "tech"

total_decks_for_leader is the COUNT(DISTINCT deck_uid) from decklists for
that leader (and format_code, if format bucketing is active), NOT a sum of
deck_count from leader_card_stats (which would double-count shared cards).

Usage:
    python -m tools.miru_compute_deck_signals
    python -m tools.miru_compute_deck_signals --leader-code OP01-001
    python -m tools.miru_compute_deck_signals --dry-run
    python -m tools.miru_compute_deck_signals --db-path data/miru_deck_intel.db
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
# Role thresholds — adjust here only
# ---------------------------------------------------------------------------
THRESHOLD_CORE: float = 0.60   # usage_percent >= this → core
THRESHOLD_FLEX: float = 0.30   # usage_percent >= this (and < core) → flex
# below THRESHOLD_FLEX → tech

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SIGNALS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS leader_card_signals (
    leader_code   TEXT NOT NULL,
    card_code     TEXT NOT NULL,
    format_code   TEXT NOT NULL DEFAULT '',
    deck_count    INTEGER NOT NULL,
    total_copies  INTEGER NOT NULL,
    usage_percent REAL NOT NULL,
    avg_copies    REAL NOT NULL,
    role_label    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (leader_code, card_code, format_code)
);
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute leader_card_signals from leader_card_stats in miru_deck_intel.db."
    )
    parser.add_argument(
        "--db-path",
        default="",
        metavar="PATH",
        help=f"Path to miru_deck_intel.db (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--leader-code",
        default="",
        metavar="CODE",
        help="Recompute signals for this leader only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print signals without writing to the DB.",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


# ---------------------------------------------------------------------------
# Signal computation types
# ---------------------------------------------------------------------------


class SignalRow(NamedTuple):
    leader_code: str
    card_code: str
    format_code: str
    deck_count: int
    total_copies: int
    usage_percent: float
    avg_copies: float
    role_label: str


def _classify(usage_percent: float) -> str:
    if usage_percent >= THRESHOLD_CORE:
        return "core"
    if usage_percent >= THRESHOLD_FLEX:
        return "flex"
    return "tech"


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------


def compute_signals(
    conn: sqlite3.Connection,
    leader_filter: str = "",
) -> list[SignalRow]:
    """
    Derive SignalRow records from leader_card_stats + decklists.

    total_decks_for_leader is COUNT(DISTINCT deck_uid) from decklists,
    partitioned by (leader_code, format_code), so a card appearing in 2 of 3
    decks for a leader always yields usage_percent = 2/3, regardless of how
    many other cards that deck contains.
    """
    # Step 1: compute total distinct decks per (leader_code, format_code)
    #         from the decklists table — the ground truth deck count.
    if leader_filter.strip():
        totals_sql = """
            SELECT
                leader_code,
                COALESCE(NULLIF(TRIM(format_code), ''), '') AS format_code,
                COUNT(DISTINCT deck_uid) AS total_decks
            FROM decklists
            WHERE leader_code = ?
            GROUP BY leader_code, format_code
        """
        totals_rows = conn.execute(totals_sql, (leader_filter.upper(),)).fetchall()
    else:
        totals_sql = """
            SELECT
                leader_code,
                COALESCE(NULLIF(TRIM(format_code), ''), '') AS format_code,
                COUNT(DISTINCT deck_uid) AS total_decks
            FROM decklists
            GROUP BY leader_code, format_code
        """
        totals_rows = conn.execute(totals_sql).fetchall()

    # Build lookup: (leader_code, format_code) -> total_decks
    totals: dict[tuple[str, str], int] = {
        (r[0], r[1]): r[2] for r in totals_rows
    }

    if not totals:
        return []

    # Step 2: read stats from leader_card_stats
    if leader_filter.strip():
        stats_sql = """
            SELECT
                leader_code,
                card_code,
                COALESCE(NULLIF(TRIM(format_code), ''), '') AS format_code,
                deck_count,
                total_copies
            FROM leader_card_stats
            WHERE leader_code = ?
            ORDER BY leader_code, format_code, deck_count DESC, card_code ASC
        """
        stats_rows = conn.execute(stats_sql, (leader_filter.upper(),)).fetchall()
    else:
        stats_sql = """
            SELECT
                leader_code,
                card_code,
                COALESCE(NULLIF(TRIM(format_code), ''), '') AS format_code,
                deck_count,
                total_copies
            FROM leader_card_stats
            ORDER BY leader_code, format_code, deck_count DESC, card_code ASC
        """
        stats_rows = conn.execute(stats_sql).fetchall()

    # Step 3: compute signal for each row
    signals: list[SignalRow] = []
    for leader_code, card_code, format_code, deck_count, total_copies in stats_rows:
        total_decks = totals.get((leader_code, format_code), 0)
        if total_decks <= 0:
            continue  # no denominator — skip rather than divide-by-zero

        usage_percent = deck_count / total_decks
        avg_copies = total_copies / deck_count if deck_count > 0 else 0.0
        role_label = _classify(usage_percent)

        signals.append(
            SignalRow(
                leader_code=leader_code,
                card_code=card_code,
                format_code=format_code,
                deck_count=deck_count,
                total_copies=total_copies,
                usage_percent=round(usage_percent, 6),
                avg_copies=round(avg_copies, 4),
                role_label=role_label,
            )
        )

    return signals


# ---------------------------------------------------------------------------
# Schema + write
# ---------------------------------------------------------------------------


def ensure_signals_table(conn: sqlite3.Connection) -> None:
    conn.execute(_SIGNALS_TABLE_DDL)


def rebuild_signals(
    conn: sqlite3.Connection,
    signals: list[SignalRow],
    updated_at: str,
    leader_filter: str = "",
) -> None:
    """
    Replace signals inside a single transaction.

    Scoped delete: when leader_filter is set, only that leader's rows are
    removed+replaced; all other leaders are left untouched.
    """
    if leader_filter.strip():
        conn.execute(
            "DELETE FROM leader_card_signals WHERE leader_code = ?",
            (leader_filter.upper(),),
        )
    else:
        conn.execute("DELETE FROM leader_card_signals")

    conn.executemany(
        """
        INSERT INTO leader_card_signals
            (leader_code, card_code, format_code, deck_count, total_copies,
             usage_percent, avg_copies, role_label, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                s.leader_code,
                s.card_code,
                s.format_code,
                s.deck_count,
                s.total_copies,
                s.usage_percent,
                s.avg_copies,
                s.role_label,
                updated_at,
            )
            for s in signals
        ],
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_dry_run(signals: list[SignalRow]) -> None:
    """Print a human-readable preview of the computed signals."""
    if not signals:
        print("  No signals computed (no data in leader_card_stats?).")
        return

    from collections import defaultdict

    by_leader: dict[str, list[SignalRow]] = defaultdict(list)
    for s in signals:
        by_leader[s.leader_code].append(s)

    for leader in sorted(by_leader):
        rows = by_leader[leader]
        core  = [r for r in rows if r.role_label == "core"]
        flex  = [r for r in rows if r.role_label == "flex"]
        tech  = [r for r in rows if r.role_label == "tech"]

        total_decks_sample = max(r.deck_count for r in rows)  # proxy: highest deck_count
        # Determine actual total from usage_percent of the most-played card
        # (usage_percent = deck_count / total — so total = deck_count / usage_percent)
        top = max(rows, key=lambda r: r.deck_count)
        approx_total = round(top.deck_count / top.usage_percent) if top.usage_percent > 0 else "?"

        print(
            f"\n  {leader}  ({len(rows)} cards | ~{approx_total} decks | "
            f"core:{len(core)}  flex:{len(flex)}  tech:{len(tech)})"
        )
        # Print top-5 by usage_percent
        top5 = sorted(rows, key=lambda r: (-r.usage_percent, r.card_code))[:5]
        for r in top5:
            bar = "#" * int(r.usage_percent * 20)
            print(
                f"    {r.card_code:<14}  {r.role_label:<5}  "
                f"{r.usage_percent:>5.1%}  [{bar:<20}]  avg {r.avg_copies:.2f}"
            )


def print_summary(
    signals: list[SignalRow],
    leaders_processed: int,
    dry_run: bool,
    db_path: Path,
) -> None:
    if not signals:
        print("No signals written (nothing to process).")
        return

    role_counts: dict[str, int] = {"core": 0, "flex": 0, "tech": 0}
    for s in signals:
        role_counts[s.role_label] = role_counts.get(s.role_label, 0) + 1

    print(
        f"  leaders processed : {leaders_processed}\n"
        f"  cards evaluated   : {len(signals)}\n"
        f"  core / flex / tech: {role_counts['core']} / {role_counts['flex']} / {role_counts['tech']}"
    )
    if dry_run:
        print(f"\nDry-run complete. {len(signals)} signal(s) computed. No DB writes.")
    else:
        print(f"  written to        : {db_path}")


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
            "Run tools/miru_import_decklist.py and tools/miru_summarize_deck_stats.py first.",
            file=sys.stderr,
        )
        return 1

    updated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        if args.dry_run:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.OperationalError as exc:
        print(f"ERROR: Could not open DB: {exc}", file=sys.stderr)
        return 1

    try:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for required in ("decklists", "leader_card_stats"):
            if required not in tables:
                print(
                    f"ERROR: Required table '{required}' not found. "
                    f"Run miru_import_decklist.py"
                    + (" and miru_summarize_deck_stats.py" if required == "leader_card_stats" else "")
                    + " first.",
                    file=sys.stderr,
                )
                return 1

        signals = compute_signals(conn, leader_filter=args.leader_code)

        # Distinct leaders in result
        leaders_processed = len({s.leader_code for s in signals})

        filter_note = (
            f" (leader filter: {args.leader_code.upper()})" if args.leader_code else ""
        )
        print(
            f"Computed {len(signals)} signal(s) across {leaders_processed} leader(s){filter_note}."
        )

        if args.dry_run:
            print_dry_run(signals)
            print()
            print_summary(signals, leaders_processed, dry_run=True, db_path=db_path)
            return 0

        ensure_signals_table(conn)
        with conn:
            rebuild_signals(conn, signals, updated_at, leader_filter=args.leader_code)

        print_summary(signals, leaders_processed, dry_run=False, db_path=db_path)

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
