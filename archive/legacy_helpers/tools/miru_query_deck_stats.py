"""
miru_query_deck_stats.py

Read-only CLI query tool for leader_card_stats in miru_deck_intel.db.

Two query modes (exactly one required):
    --leader-code CODE   Top cards for a specific leader
    --card-code CODE     Leaders using a specific card

Pass --enrich to join card names, types, and costs from miru_dossiers.db.

Usage:
    python -m tools.miru_query_deck_stats --leader-code OP01-001
    python -m tools.miru_query_deck_stats --leader-code OP01-001 --limit 20
    python -m tools.miru_query_deck_stats --leader-code OP01-001 --format-code OP-FORMAT
    python -m tools.miru_query_deck_stats --leader-code OP01-001 --enrich
    python -m tools.miru_query_deck_stats --card-code OP01-002
    python -m tools.miru_query_deck_stats --card-code OP01-002 --enrich
    python -m tools.miru_query_deck_stats --card-code OP01-002 --format-code OP-FORMAT
    python -m tools.miru_query_deck_stats --card-code OP01-002 --db-path data/miru_deck_intel.db
    python -m tools.miru_query_deck_stats --leader-code OP01-001 --enrich \\
        --dossiers-db data/miru_dossiers.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"
DEFAULT_LIMIT = 10

_TRUNCATE_NAME = 22   # max chars for name column before adding "…"
_TRUNCATE_TYPE = 12   # max chars for card_type column


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query leader_card_stats from miru_deck_intel.db (read-only)."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--leader-code",
        metavar="CODE",
        help="Show top cards for this leader (e.g. OP01-001).",
    )
    mode.add_argument(
        "--card-code",
        metavar="CODE",
        help="Show which leaders use this card (e.g. OP01-002).",
    )
    parser.add_argument(
        "--format-code",
        default="",
        metavar="CODE",
        help="Filter results to a specific format code (e.g. OP-FORMAT).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        metavar="N",
        help=f"Maximum rows to display (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--db-path",
        default="",
        metavar="PATH",
        help=f"Path to miru_deck_intel.db (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Join card names, types, and costs from miru_dossiers.db.",
    )
    parser.add_argument(
        "--dossiers-db",
        default="",
        metavar="PATH",
        help=f"Path to miru_dossiers.db for --enrich (default: {DEFAULT_DOSSIERS_PATH}).",
    )
    return parser


def resolve_db_path(args: argparse.Namespace) -> Path:
    return Path(args.db_path) if args.db_path else DEFAULT_DB_PATH


def resolve_dossiers_path(args: argparse.Namespace) -> Path:
    return Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class LeaderRow(NamedTuple):
    card_code: str
    format_code: str
    deck_count: int
    total_copies: int
    avg_copies: float


class CardRow(NamedTuple):
    leader_code: str
    format_code: str
    deck_count: int
    total_copies: int


class CardInfo(NamedTuple):
    """Enrichment data from miru_dossiers.db for one card code."""
    name: str       # card_name from cards table
    card_type: str  # card_type from cards table
    cost: str       # cost from card_facts (verified preferred, falls back to any)


# ---------------------------------------------------------------------------
# Enrichment loader
# ---------------------------------------------------------------------------


def load_enrichment(
    codes: list[str],
    dossiers_path: Path,
) -> dict[str, CardInfo] | None:
    """
    Bulk-load card name, type, and cost for the given card codes.

    Returns a dict {canonical_code: CardInfo}, or None if the dossiers DB
    is unavailable (caller should warn and continue without enrichment).

    Sources:
      - card_name, card_type: cards.card_name / cards.card_type (denormalized)
      - cost: card_facts WHERE field_name='cost' (verified preferred)
    """
    if not dossiers_path.exists():
        return None

    try:
        dconn = sqlite3.connect(f"file:{dossiers_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None

    enrichment: dict[str, CardInfo] = {}
    try:
        if not codes:
            return enrichment

        placeholders = ",".join("?" * len(codes))

        # --- name + type from cards table ---
        name_type: dict[str, tuple[str, str]] = {}
        for row in dconn.execute(
            f"SELECT canonical_code, COALESCE(card_name,''), COALESCE(card_type,'')"
            f" FROM cards WHERE canonical_code IN ({placeholders})",
            codes,
        ).fetchall():
            name_type[row[0]] = (row[1], row[2])

        # --- cost from card_facts; prefer verified, fall back to any ---
        cost_map: dict[str, str] = {}
        for row in dconn.execute(
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
            codes,
        ).fetchall():
            code = row[0]
            if code not in cost_map:  # first row wins (verified first due to ORDER BY)
                cost_map[code] = str(row[1] or "")

        for code in codes:
            nt = name_type.get(code, ("", ""))
            enrichment[code] = CardInfo(
                name=nt[0],
                card_type=nt[1],
                cost=cost_map.get(code, ""),
            )
    except sqlite3.Error:
        return None
    finally:
        dconn.close()

    return enrichment


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def query_leader(
    conn: sqlite3.Connection,
    leader_code: str,
    format_code: str = "",
    limit: int = DEFAULT_LIMIT,
) -> list[LeaderRow]:
    """Top cards for a specific leader, sorted by deck_count desc."""
    base = """
        SELECT
            card_code,
            format_code,
            deck_count,
            total_copies,
            ROUND(CAST(total_copies AS REAL) / NULLIF(deck_count, 0), 2) AS avg_copies
        FROM leader_card_stats
        WHERE leader_code = ?
        {fmt_filter}
        ORDER BY deck_count DESC, total_copies DESC, card_code ASC
        LIMIT ?
    """
    if format_code.strip():
        sql = base.format(fmt_filter="AND format_code = ?")
        rows = conn.execute(sql, (leader_code.upper(), format_code.strip(), limit)).fetchall()
    else:
        sql = base.format(fmt_filter="")
        rows = conn.execute(sql, (leader_code.upper(), limit)).fetchall()

    return [
        LeaderRow(
            card_code=r[0],
            format_code=r[1],
            deck_count=r[2],
            total_copies=r[3],
            avg_copies=float(r[4] or 0.0),
        )
        for r in rows
    ]


def query_card(
    conn: sqlite3.Connection,
    card_code: str,
    format_code: str = "",
    limit: int = DEFAULT_LIMIT,
) -> list[CardRow]:
    """Leaders that use a specific card, sorted by deck_count desc."""
    base = """
        SELECT
            leader_code,
            format_code,
            deck_count,
            total_copies
        FROM leader_card_stats
        WHERE card_code = ?
        {fmt_filter}
        ORDER BY deck_count DESC, total_copies DESC, leader_code ASC
        LIMIT ?
    """
    if format_code.strip():
        sql = base.format(fmt_filter="AND format_code = ?")
        rows = conn.execute(sql, (card_code.upper(), format_code.strip(), limit)).fetchall()
    else:
        sql = base.format(fmt_filter="")
        rows = conn.execute(sql, (card_code.upper(), limit)).fetchall()

    return [
        CardRow(
            leader_code=r[0],
            format_code=r[1],
            deck_count=r[2],
            total_copies=r[3],
        )
        for r in rows
    ]


def total_deck_count(conn: sqlite3.Connection) -> int:
    """Total distinct decks in decklists (for context in output)."""
    try:
        return conn.execute("SELECT COUNT(*) FROM decklists").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

_COL = {
    "card":    14,
    "leader":  14,
    "name":    _TRUNCATE_NAME,
    "type":    _TRUNCATE_TYPE,
    "cost":     4,
    "fmt":     12,
    "dc":       7,
    "tc":       8,
    "avg":      6,
}


def _trunc(value: str, width: int) -> str:
    """Truncate a string to width, appending '…' if clipped."""
    if len(value) <= width:
        return value
    return value[: width - 1] + "\u2026"


def _pct(deck_count: int, total: int) -> str:
    if total <= 0:
        return ""
    return f"  ({100 * deck_count / total:.0f}%)"


def print_leader_results(
    rows: list[LeaderRow],
    leader_code: str,
    format_filter: str,
    total_decks: int,
    limit: int,
    enrichment: dict[str, CardInfo] | None = None,
) -> None:
    fmt_note = f"  format: {format_filter}" if format_filter else ""
    enrich_note = "  [enriched]" if enrichment is not None else ""
    print(f"\nTop cards for leader {leader_code}{fmt_note}{enrich_note}")
    print(f"(showing up to {limit}; {total_decks} total deck(s) in DB)\n")

    if not rows:
        print("  No results. Has miru_summarize_deck_stats been run?")
        return

    if enrichment is not None:
        # Enriched header: code | name | type | cost | decks | copies | avg
        hdr = (
            f"  {'card_code':<{_COL['card']}}  "
            f"{'name':<{_COL['name']}}  "
            f"{'type':<{_COL['type']}}  "
            f"{'cost':>{_COL['cost']}}  "
            f"{'decks':>{_COL['dc']}}  "
            f"{'copies':>{_COL['tc']}}  "
            f"{'avg':>{_COL['avg']}}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in rows:
            info = enrichment.get(r.card_code, CardInfo("", "", ""))
            pct = _pct(r.deck_count, total_decks)
            print(
                f"  {r.card_code:<{_COL['card']}}  "
                f"{_trunc(info.name, _COL['name']):<{_COL['name']}}  "
                f"{_trunc(info.card_type.title(), _COL['type']):<{_COL['type']}}  "
                f"{info.cost:>{_COL['cost']}}  "
                f"{r.deck_count:>{_COL['dc']}}{pct:<8}  "
                f"{r.total_copies:>{_COL['tc']}}  "
                f"{r.avg_copies:>{_COL['avg']}.2f}"
            )
    else:
        # Plain header: code | format | decks | copies | avg
        hdr = (
            f"  {'card_code':<{_COL['card']}}  "
            f"{'format':<{_COL['fmt']}}  "
            f"{'decks':>{_COL['dc']}}  "
            f"{'copies':>{_COL['tc']}}  "
            f"{'avg':>{_COL['avg']}}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in rows:
            fmt = r.format_code or "(none)"
            pct = _pct(r.deck_count, total_decks)
            print(
                f"  {r.card_code:<{_COL['card']}}  "
                f"{fmt:<{_COL['fmt']}}  "
                f"{r.deck_count:>{_COL['dc']}}{pct:<8}  "
                f"{r.total_copies:>{_COL['tc']}}  "
                f"{r.avg_copies:>{_COL['avg']}.2f}"
            )

    print(f"\n  {len(rows)} row(s) returned.")


def print_card_results(
    rows: list[CardRow],
    card_code: str,
    format_filter: str,
    total_decks: int,
    limit: int,
    enrichment: dict[str, CardInfo] | None = None,
) -> None:
    fmt_note = f"  format: {format_filter}" if format_filter else ""
    enrich_note = "  [enriched]" if enrichment is not None else ""
    print(f"\nLeaders using card {card_code}{fmt_note}{enrich_note}")
    print(f"(showing up to {limit}; {total_decks} total deck(s) in DB)\n")

    if not rows:
        print("  No results. Has miru_summarize_deck_stats been run?")
        return

    if enrichment is not None:
        # Enriched header: leader_code | name (of leader) | type | cost | decks | copies
        hdr = (
            f"  {'leader_code':<{_COL['leader']}}  "
            f"{'name':<{_COL['name']}}  "
            f"{'type':<{_COL['type']}}  "
            f"{'cost':>{_COL['cost']}}  "
            f"{'decks':>{_COL['dc']}}  "
            f"{'copies':>{_COL['tc']}}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in rows:
            info = enrichment.get(r.leader_code, CardInfo("", "", ""))
            pct = _pct(r.deck_count, total_decks)
            print(
                f"  {r.leader_code:<{_COL['leader']}}  "
                f"{_trunc(info.name, _COL['name']):<{_COL['name']}}  "
                f"{_trunc(info.card_type.title(), _COL['type']):<{_COL['type']}}  "
                f"{info.cost:>{_COL['cost']}}  "
                f"{r.deck_count:>{_COL['dc']}}{pct:<8}  "
                f"{r.total_copies:>{_COL['tc']}}"
            )
    else:
        hdr = (
            f"  {'leader_code':<{_COL['leader']}}  "
            f"{'format':<{_COL['fmt']}}  "
            f"{'decks':>{_COL['dc']}}  "
            f"{'copies':>{_COL['tc']}}"
        )
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for r in rows:
            fmt = r.format_code or "(none)"
            pct = _pct(r.deck_count, total_decks)
            print(
                f"  {r.leader_code:<{_COL['leader']}}  "
                f"{fmt:<{_COL['fmt']}}  "
                f"{r.deck_count:>{_COL['dc']}}{pct:<8}  "
                f"{r.total_copies:>{_COL['tc']}}"
            )

    print(f"\n  {len(rows)} row(s) returned.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = resolve_db_path(args)
    limit = max(args.limit, 1)

    if not db_path.exists():
        print(f"ERROR: DB not found: {db_path}", file=sys.stderr)
        print(
            "Run tools/miru_import_decklist.py then tools/miru_summarize_deck_stats.py first.",
            file=sys.stderr,
        )
        return 1

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        print(f"ERROR: Could not open DB: {exc}", file=sys.stderr)
        return 1

    try:
        # Confirm leader_card_stats exists
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "leader_card_stats" not in tables:
            print(
                "ERROR: Table 'leader_card_stats' not found. "
                "Run tools/miru_summarize_deck_stats.py first.",
                file=sys.stderr,
            )
            return 1

        n_total = total_deck_count(conn)

        if args.leader_code:
            rows = query_leader(conn, args.leader_code, args.format_code, limit)
            enrichment = _maybe_enrich(
                args, [r.card_code for r in rows], mode="leader"
            )
            print_leader_results(
                rows, args.leader_code.upper(), args.format_code,
                n_total, limit, enrichment=enrichment,
            )
        else:
            rows = query_card(conn, args.card_code, args.format_code, limit)
            enrichment = _maybe_enrich(
                args, [r.leader_code for r in rows], mode="card"
            )
            print_card_results(
                rows, args.card_code.upper(), args.format_code,
                n_total, limit, enrichment=enrichment,
            )

    except sqlite3.Error as exc:
        print(f"ERROR: SQLite error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


def _maybe_enrich(
    args: argparse.Namespace,
    codes: list[str],
    mode: str,
) -> dict[str, CardInfo] | None:
    """Load enrichment if --enrich was requested; warn and return None on failure."""
    if not args.enrich:
        return None

    dossiers_path = resolve_dossiers_path(args)
    enrichment = load_enrichment(codes, dossiers_path)

    if enrichment is None:
        print(
            f"WARNING: --enrich requested but dossiers DB not available at {dossiers_path}. "
            "Continuing without enrichment.",
            file=sys.stderr,
        )

    return enrichment


if __name__ == "__main__":
    raise SystemExit(main())
