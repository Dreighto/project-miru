"""
miru_compute_cost_curves.py

Read leader_card_stats from miru_deck_intel.db, join cost facts from
miru_dossiers.db, and build a summarized cost-curve table.

Creates or rebuilds the leader_cost_curves table in miru_deck_intel.db:
    leader_code       TEXT
    format_code       TEXT
    cost_bucket       TEXT   -- "0","1","2","3","4","5","6","7","8+"
    card_count        INTEGER -- unique cards contributing to this bucket
    deck_appearances  INTEGER -- sum of deck_count for cards in this bucket
    total_copies      INTEGER -- sum of total_copies for cards in this bucket
    updated_at        TEXT
    PRIMARY KEY (leader_code, format_code, cost_bucket)

Cost facts are loaded from miru_dossiers.db (read-only):
  - Prefers verification_state = 'verified'
  - Falls back to any stored value if no verified row exists
  - Cards with absent or non-numeric cost are skipped (not bucketed)

Usage:
    python -m tools.miru_compute_cost_curves
    python -m tools.miru_compute_cost_curves --leader-code OP01-001
    python -m tools.miru_compute_cost_curves --dry-run
    python -m tools.miru_compute_cost_curves --db-path data/miru_deck_intel.db \\
        --dossiers-db data/miru_dossiers.db
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

# Cost buckets in display order
COST_BUCKETS = ["0", "1", "2", "3", "4", "5", "6", "7", "8+"]
MAX_EXACT_COST = 7   # costs > MAX_EXACT_COST → "8+"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CURVES_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS leader_cost_curves (
    leader_code      TEXT NOT NULL,
    format_code      TEXT NOT NULL DEFAULT '',
    cost_bucket      TEXT NOT NULL,
    card_count       INTEGER NOT NULL,
    deck_appearances INTEGER NOT NULL,
    total_copies     INTEGER NOT NULL,
    updated_at       TEXT NOT NULL,
    PRIMARY KEY (leader_code, format_code, cost_bucket)
);
"""

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compute leader cost curves from leader_card_stats + miru_dossiers.db "
            "and write to leader_cost_curves in miru_deck_intel.db."
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
        help="Recompute cost curves for this leader only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print curves without writing to the DB.",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def resolve_dossiers_path(args: argparse.Namespace) -> Path:
    return Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH


# ---------------------------------------------------------------------------
# Cost loading from dossiers
# ---------------------------------------------------------------------------


def _to_bucket(raw_cost: str) -> str | None:
    """
    Parse a raw cost string to a bucket label, or return None if unparseable.
    Non-negative integers > MAX_EXACT_COST map to '8+'.
    """
    try:
        val = int(str(raw_cost).strip())
    except (ValueError, TypeError):
        return None
    if val < 0:
        return None
    if val > MAX_EXACT_COST:
        return "8+"
    return str(val)


def load_cost_map(
    dossiers_path: Path,
    card_codes: list[str],
) -> dict[str, str]:
    """
    Return {canonical_code: cost_bucket} for the given card codes.

    Prefers verified rows; falls back to any stored cost row for the code.
    Cards with no cost fact, or a non-numeric cost, are absent from the result.
    """
    if not card_codes or not dossiers_path.exists():
        return {}

    try:
        dconn = sqlite3.connect(f"file:{dossiers_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return {}

    cost_map: dict[str, str] = {}
    try:
        placeholders = ",".join("?" * len(card_codes))
        rows = dconn.execute(
            f"""
            SELECT c.canonical_code, cf.value_text, cf.verification_state
            FROM card_facts cf
            JOIN cards c ON c.id = cf.card_id
            WHERE cf.field_name = 'cost'
              AND c.canonical_code IN ({placeholders})
            ORDER BY
                CASE cf.verification_state WHEN 'verified' THEN 0 ELSE 1 END,
                cf.updated_at DESC
            """,
            card_codes,
        ).fetchall()
        for code, value, _state in rows:
            if code in cost_map:
                continue  # first row wins (verified first)
            bucket = _to_bucket(value)
            if bucket is not None:
                cost_map[code] = bucket
    except sqlite3.Error:
        pass
    finally:
        dconn.close()

    return cost_map


# ---------------------------------------------------------------------------
# Curve row type + aggregation
# ---------------------------------------------------------------------------


class CurveRow(NamedTuple):
    leader_code: str
    format_code: str
    cost_bucket: str
    card_count: int
    deck_appearances: int
    total_copies: int


def compute_curves(
    intel_conn: sqlite3.Connection,
    cost_map: dict[str, str],
    leader_filter: str = "",
) -> list[CurveRow]:
    """
    Aggregate leader_card_stats into per-(leader, format, cost_bucket) rows.

    Cards absent from cost_map (missing or non-numeric cost) are skipped.
    Only buckets that have at least one card are emitted.
    """
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
        """
        stats_rows = intel_conn.execute(stats_sql, (leader_filter.upper(),)).fetchall()
    else:
        stats_sql = """
            SELECT
                leader_code,
                card_code,
                COALESCE(NULLIF(TRIM(format_code), ''), '') AS format_code,
                deck_count,
                total_copies
            FROM leader_card_stats
        """
        stats_rows = intel_conn.execute(stats_sql).fetchall()

    # Accumulator: (leader, format, bucket) -> [card_count, deck_appearances, total_copies]
    agg: dict[tuple[str, str, str], list[int]] = defaultdict(lambda: [0, 0, 0])

    for leader_code, card_code, format_code, deck_count, total_copies in stats_rows:
        bucket = cost_map.get(card_code)
        if bucket is None:
            continue  # no cost data → skip
        key = (leader_code, format_code, bucket)
        agg[key][0] += 1            # card_count
        agg[key][1] += deck_count   # deck_appearances
        agg[key][2] += total_copies  # total_copies

    return [
        CurveRow(
            leader_code=leader,
            format_code=fmt,
            cost_bucket=bucket,
            card_count=vals[0],
            deck_appearances=vals[1],
            total_copies=vals[2],
        )
        for (leader, fmt, bucket), vals in sorted(agg.items())
    ]


# ---------------------------------------------------------------------------
# Schema + write
# ---------------------------------------------------------------------------


def ensure_curves_table(conn: sqlite3.Connection) -> None:
    conn.execute(_CURVES_TABLE_DDL)


def rebuild_curves(
    conn: sqlite3.Connection,
    curves: list[CurveRow],
    updated_at: str,
    leader_filter: str = "",
) -> None:
    if leader_filter.strip():
        conn.execute(
            "DELETE FROM leader_cost_curves WHERE leader_code = ?",
            (leader_filter.upper(),),
        )
    else:
        conn.execute("DELETE FROM leader_cost_curves")

    conn.executemany(
        """
        INSERT INTO leader_cost_curves
            (leader_code, format_code, cost_bucket, card_count,
             deck_appearances, total_copies, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (r.leader_code, r.format_code, r.cost_bucket,
             r.card_count, r.deck_appearances, r.total_copies, updated_at)
            for r in curves
        ],
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _bar(value: int, max_value: int, width: int = 20) -> str:
    if max_value <= 0:
        return " " * width
    filled = round(width * value / max_value)
    return "#" * filled + " " * (width - filled)


def print_dry_run(curves: list[CurveRow], cost_map: dict[str, str], skipped: int) -> None:
    if not curves:
        print("  No curves computed (no cost data available for these cards?).")
        if skipped:
            print(f"  {skipped} card(s) skipped due to missing/non-numeric cost.")
        return

    from collections import defaultdict as dd

    by_leader: dict[str, dict[str, dict[str, CurveRow]]] = dd(lambda: dd(dict))
    for r in curves:
        by_leader[r.leader_code][r.format_code][r.cost_bucket] = r

    for leader in sorted(by_leader):
        for fmt in sorted(by_leader[leader]):
            bucket_map = by_leader[leader][fmt]
            fmt_label = f"  [{fmt}]" if fmt else ""
            total_da = sum(r.deck_appearances for r in bucket_map.values())
            max_da = max((r.deck_appearances for r in bucket_map.values()), default=0)
            print(f"\n  {leader}{fmt_label}  (deck_appearances total: {total_da})")
            print(f"  {'cost':<6}  {'cards':>5}  {'d.app':>6}  {'copies':>7}  distribution")
            print("  " + "-" * 56)
            for bucket in COST_BUCKETS:
                r = bucket_map.get(bucket)
                if r is None:
                    continue
                bar = _bar(r.deck_appearances, max_da)
                print(
                    f"  {bucket:<6}  {r.card_count:>5}  {r.deck_appearances:>6}  "
                    f"{r.total_copies:>7}  [{bar}]"
                )

    if skipped:
        print(f"\n  ({skipped} card(s) skipped: no cost data in dossiers DB)")


def print_summary(
    curves: list[CurveRow],
    skipped: int,
    dry_run: bool,
    db_path: Path,
) -> None:
    leaders = len({(r.leader_code, r.format_code) for r in curves})
    print(
        f"  leader/format combos : {leaders}\n"
        f"  curve rows written   : {len(curves)}\n"
        f"  cards skipped (no cost): {skipped}"
    )
    if dry_run:
        print(f"\nDry-run complete. {len(curves)} curve row(s) computed. No DB writes.")
    else:
        print(f"  written to           : {db_path}")


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
            "Cost enrichment will be skipped; no curves can be computed.",
            file=sys.stderr,
        )
        # Proceed — compute_curves will produce empty output gracefully

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

        # Collect all card codes we'll need costs for
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

        cost_map = load_cost_map(dossiers_path, all_codes)
        skipped = len(all_codes) - len(cost_map)

        filter_note = (
            f" (leader filter: {args.leader_code.upper()})" if args.leader_code else ""
        )
        print(
            f"Loaded costs for {len(cost_map)}/{len(all_codes)} card(s){filter_note}. "
            f"{skipped} skipped (no cost data)."
        )

        curves = compute_curves(intel_conn, cost_map, leader_filter=args.leader_code)

        if args.dry_run:
            print_dry_run(curves, cost_map, skipped)
            print()
            print_summary(curves, skipped, dry_run=True, db_path=db_path)
            return 0

        ensure_curves_table(intel_conn)
        with intel_conn:
            rebuild_curves(intel_conn, curves, updated_at, leader_filter=args.leader_code)

        print_summary(curves, skipped, dry_run=False, db_path=db_path)

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
