"""
miru_strategy_preview.py

Read-only strategy preview for a single leader.

Reads leader_card_signals, leader_cost_curves, and leader_trait_signals
from miru_deck_intel.db and prints a concise, human-readable summary.
Optionally enriches card codes with names, types, and costs from
miru_dossiers.db (read-only).

No DB writes.  No network access.  Safe to run at any time.

Usage
-----
    python -m tools.miru_strategy_preview --leader-code OP01-001
    python -m tools.miru_strategy_preview --leader-code OP01-001 --limit 10
    python -m tools.miru_strategy_preview --leader-code OP01-001 --format-code OP-FORMAT
    python -m tools.miru_strategy_preview \\
        --leader-code OP01-001 \\
        --db-path data/miru_deck_intel.db \\
        --dossiers-db data/miru_dossiers.db
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH     = ROOT / "data" / "miru_deck_intel.db"
DEFAULT_DOSSIERS_PATH = ROOT / "data" / "miru_dossiers.db"

# Display constants
_BAR_WIDTH   = 20   # width of ASCII bar charts
_NAME_WIDTH  = 26   # truncated card name column
_CODE_WIDTH  = 14   # card code column
_TYPE_WIDTH  = 10   # card type abbreviation

# Map full card-type strings to short display labels
_TYPE_ABBREV: dict[str, str] = {
    "character": "CHAR",
    "event":     "EVENT",
    "stage":     "STAGE",
    "leader":    "LEADER",
    "don":       "DON",
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Print a read-only strategy preview for a single leader from "
            "miru_deck_intel.db."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--leader-code",
        required=True,
        metavar="CODE",
        help="Leader card code to preview (e.g. OP01-001).",
    )
    parser.add_argument(
        "--format-code",
        default="",
        metavar="CODE",
        help="Restrict to a specific format bucket (default: all formats combined).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Cap the number of cards shown per role section (0 = no limit).",
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
        help=(
            f"Path to miru_dossiers.db for card-name enrichment "
            f"(default: {DEFAULT_DOSSIERS_PATH}).  Skipped gracefully if absent."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SignalRow(NamedTuple):
    card_code:     str
    deck_count:    int
    total_copies:  int
    usage_percent: float
    avg_copies:    float
    role_label:    str


class CurveRow(NamedTuple):
    cost_bucket:     str
    card_count:      int
    deck_appearances: int
    total_copies:    int


class TraitRow(NamedTuple):
    trait_name:      str
    card_count:      int
    deck_appearances: int
    total_copies:    int


@dataclass
class CardInfo:
    name: str = ""
    card_type: str = ""
    cost: str = ""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _open_ro(path: Path) -> sqlite3.Connection | None:
    """Open a SQLite DB read-only; return None if it cannot be opened."""
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.OperationalError:
        return None


def load_signals(
    conn: sqlite3.Connection,
    leader_code: str,
    format_code: str,
) -> list[SignalRow]:
    """Return all leader_card_signals rows for the given leader (+ optional format)."""
    if format_code.strip():
        rows = conn.execute(
            """
            SELECT card_code, deck_count, total_copies,
                   usage_percent, avg_copies, role_label
            FROM leader_card_signals
            WHERE leader_code = ? AND format_code = ?
            ORDER BY usage_percent DESC, card_code ASC
            """,
            (leader_code.upper(), format_code),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT card_code, deck_count, total_copies,
                   usage_percent, avg_copies, role_label
            FROM leader_card_signals
            WHERE leader_code = ?
            ORDER BY usage_percent DESC, card_code ASC
            """,
            (leader_code.upper(),),
        ).fetchall()
    return [
        SignalRow(
            card_code=r["card_code"],
            deck_count=r["deck_count"],
            total_copies=r["total_copies"],
            usage_percent=float(r["usage_percent"]),
            avg_copies=float(r["avg_copies"]),
            role_label=r["role_label"],
        )
        for r in rows
    ]


def load_curves(
    conn: sqlite3.Connection,
    leader_code: str,
    format_code: str,
) -> list[CurveRow]:
    """Return leader_cost_curves rows ordered by cost bucket."""
    if format_code.strip():
        rows = conn.execute(
            """
            SELECT cost_bucket, card_count, deck_appearances, total_copies
            FROM leader_cost_curves
            WHERE leader_code = ? AND format_code = ?
            ORDER BY cost_bucket ASC
            """,
            (leader_code.upper(), format_code),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT cost_bucket, card_count, deck_appearances, total_copies
            FROM leader_cost_curves
            WHERE leader_code = ?
            ORDER BY cost_bucket ASC
            """,
            (leader_code.upper(),),
        ).fetchall()
    return [
        CurveRow(
            cost_bucket=r["cost_bucket"],
            card_count=r["card_count"],
            deck_appearances=r["deck_appearances"],
            total_copies=r["total_copies"],
        )
        for r in rows
    ]


def load_traits(
    conn: sqlite3.Connection,
    leader_code: str,
    format_code: str,
) -> list[TraitRow]:
    """Return leader_trait_signals rows ordered by deck_appearances DESC."""
    if format_code.strip():
        rows = conn.execute(
            """
            SELECT trait_name, card_count, deck_appearances, total_copies
            FROM leader_trait_signals
            WHERE leader_code = ? AND format_code = ?
            ORDER BY deck_appearances DESC, trait_name ASC
            """,
            (leader_code.upper(), format_code),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT trait_name, card_count, deck_appearances, total_copies
            FROM leader_trait_signals
            WHERE leader_code = ?
            ORDER BY deck_appearances DESC, trait_name ASC
            """,
            (leader_code.upper(),),
        ).fetchall()
    return [
        TraitRow(
            trait_name=r["trait_name"],
            card_count=r["card_count"],
            deck_appearances=r["deck_appearances"],
            total_copies=r["total_copies"],
        )
        for r in rows
    ]


def load_deck_count(
    conn: sqlite3.Connection,
    leader_code: str,
    format_code: str,
) -> int:
    if format_code.strip():
        return conn.execute(
            "SELECT COUNT(DISTINCT deck_uid) FROM decklists WHERE leader_code=? AND format_code=?",
            (leader_code.upper(), format_code),
        ).fetchone()[0]
    return conn.execute(
        "SELECT COUNT(DISTINCT deck_uid) FROM decklists WHERE leader_code=?",
        (leader_code.upper(),),
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Dossier enrichment
# ---------------------------------------------------------------------------


def load_enrichment(
    dossiers_path: Path,
    card_codes: list[str],
    leader_code: str,
) -> dict[str, CardInfo]:
    """
    Return {card_code: CardInfo} for the given codes.

    Loads card_name and card_type from cards table; cost from card_facts
    (verified-first).  Leader info also loaded if available.
    Returns {} on any error so the caller can degrade gracefully.
    """
    conn = _open_ro(dossiers_path)
    if conn is None:
        return {}

    all_codes = list({c.upper() for c in card_codes} | {leader_code.upper()})
    result: dict[str, CardInfo] = {}

    try:
        placeholders = ",".join("?" * len(all_codes))
        # Names + types from cards table
        for row in conn.execute(
            f"SELECT canonical_code, card_name, card_type FROM cards "
            f"WHERE canonical_code IN ({placeholders})",
            all_codes,
        ).fetchall():
            code = row["canonical_code"]
            result[code] = CardInfo(
                name=row["card_name"] or "",
                card_type=row["card_type"] or "",
            )

        # Costs from card_facts (verified preferred)
        for row in conn.execute(
            f"""
            SELECT c.canonical_code, cf.value_text
            FROM card_facts cf
            JOIN cards c ON c.id = cf.card_id
            WHERE cf.field_name = 'cost'
              AND c.canonical_code IN ({placeholders})
            ORDER BY
                CASE cf.verification_state WHEN 'verified' THEN 0 ELSE 1 END,
                cf.updated_at DESC
            """,
            all_codes,
        ).fetchall():
            code = row["canonical_code"]
            if code in result and not result[code].cost:
                result[code].cost = row["value_text"] or ""

    except sqlite3.Error:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _bar(value: int | float, max_value: int | float, width: int = _BAR_WIDTH) -> str:
    if max_value <= 0:
        return " " * width
    filled = round(width * value / max_value)
    return "#" * filled + " " * (width - filled)


def _trunc(s: str, width: int) -> str:
    if len(s) <= width:
        return s.ljust(width)
    return s[: width - 1] + "~"


def _type_abbrev(raw: str) -> str:
    return _TYPE_ABBREV.get(raw.strip().lower(), raw[:_TYPE_WIDTH] if raw else "")


def _cost_label(cost: str) -> str:
    if not cost:
        return "  -"
    try:
        return f"{int(cost):>3}cp"
    except ValueError:
        return f" {cost[:3]}"


# ---------------------------------------------------------------------------
# Print sections
# ---------------------------------------------------------------------------


def _print_header(
    leader_code: str,
    leader_info: CardInfo | None,
    total_decks: int,
    total_signals: int,
    format_code: str,
) -> None:
    name = (leader_info.name if leader_info else "") or leader_code
    type_str = (
        _type_abbrev(leader_info.card_type) if leader_info else ""
    )
    fmt_note = f"  format: {format_code}" if format_code else ""
    print()
    print("=" * 66)
    print(f"  {leader_code}  {name}  [{type_str}]")
    print(f"  {total_decks} deck(s){fmt_note}  |  {total_signals} card(s) tracked")
    print("=" * 66)


def _print_role_section(
    title: str,
    signals: list[SignalRow],
    enrichment: dict[str, CardInfo],
    limit: int,
) -> None:
    if not signals:
        return
    shown = signals[:limit] if limit > 0 else signals
    truncated = len(signals) - len(shown)

    print(f"\n{title}  --  {len(signals)} card(s)")
    for s in shown:
        info = enrichment.get(s.card_code, CardInfo())
        name = _trunc(info.name or s.card_code, _NAME_WIDTH)
        tp   = _type_abbrev(info.card_type).ljust(5)
        cost = _cost_label(info.cost)
        bar  = _bar(s.usage_percent, 1.0)
        print(
            f"  {s.card_code:<{_CODE_WIDTH}} {name}  {tp} {cost}  "
            f"{s.usage_percent:>5.1%}  [{bar}]  avg {s.avg_copies:.1f}x"
        )
    if truncated:
        print(f"  ... ({truncated} more, use --limit 0 to see all)")


def _print_cost_curve(curves: list[CurveRow]) -> None:
    if not curves:
        return
    print("\nCOST CURVE")
    max_da = max((c.deck_appearances for c in curves), default=0)
    for c in curves:
        bar = _bar(c.deck_appearances, max_da, width=12)
        print(
            f"  {c.cost_bucket:>3}cp  [{bar}]  "
            f"{c.card_count:>2} card(s)  "
            f"{c.deck_appearances:>3} deck-app(s)  "
            f"({c.total_copies:>3} copies)"
        )


def _print_traits(traits: list[TraitRow], limit: int) -> None:
    if not traits:
        return
    shown = traits[:limit] if limit > 0 else traits
    max_da = max((t.deck_appearances for t in shown), default=0)
    print(f"\nTOP TRAITS  ({len(shown)} of {len(traits)})")
    for t in shown:
        bar = _bar(t.deck_appearances, max_da, width=16)
        print(
            f"  {t.trait_name:<14}  "
            f"{t.card_count:>2} card(s)  "
            f"{t.deck_appearances:>3} deck-app(s)  "
            f"[{bar}]"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    leader_code = args.leader_code.strip().upper()
    format_code = (args.format_code or "").strip()
    limit       = max(0, args.limit)

    db_path       = Path(args.db_path)       if args.db_path       else DEFAULT_DB_PATH
    dossiers_path = Path(args.dossiers_db) if args.dossiers_db else DEFAULT_DOSSIERS_PATH

    # Open intel DB (read-only)
    conn = _open_ro(db_path)
    if conn is None:
        print(f"ERROR: deck intel DB not found: {db_path}", file=sys.stderr)
        print(
            "Run miru_import_decklist + miru_summarize_deck_stats + "
            "miru_compute_deck_signals first (or use miru_run_sandbox_cycle).",
            file=sys.stderr,
        )
        return 1

    try:
        # Check required tables exist
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for required in ("decklists", "leader_card_signals",
                         "leader_cost_curves", "leader_trait_signals"):
            if required not in tables:
                print(
                    f"ERROR: table '{required}' not found in {db_path}. "
                    "Run the full sandbox cycle first.",
                    file=sys.stderr,
                )
                return 1

        total_decks  = load_deck_count(conn, leader_code, format_code)
        signals      = load_signals(conn, leader_code, format_code)
        curves       = load_curves(conn, leader_code, format_code)
        traits       = load_traits(conn, leader_code, format_code)

        if not signals and total_decks == 0:
            print(
                f"ERROR: no data found for leader '{leader_code}'"
                + (f" / format '{format_code}'" if format_code else "")
                + f" in {db_path}.",
                file=sys.stderr,
            )
            return 1

    except sqlite3.Error as exc:
        print(f"ERROR: SQLite error reading {db_path}: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Enrichment (graceful degradation if dossiers DB absent)
    all_codes = [s.card_code for s in signals]
    enrichment: dict[str, CardInfo] = {}
    if dossiers_path.exists():
        enrichment = load_enrichment(dossiers_path, all_codes, leader_code)
    else:
        print(
            f"  note: dossiers DB not found at {dossiers_path} -- "
            "card names/types/costs will not be shown.",
            file=sys.stderr,
        )

    # Split signals by role
    core = [s for s in signals if s.role_label == "core"]
    flex = [s for s in signals if s.role_label == "flex"]
    tech = [s for s in signals if s.role_label == "tech"]

    # --- Output ---
    leader_info = enrichment.get(leader_code)
    _print_header(leader_code, leader_info, total_decks, len(signals), format_code)

    _print_role_section(
        "CORE  (>= 60% usage)",
        core, enrichment, limit,
    )
    _print_role_section(
        "FLEX  (30-59% usage)",
        flex, enrichment, limit,
    )
    _print_role_section(
        "TECH  (< 30% usage)",
        tech, enrichment, limit,
    )

    _print_cost_curve(curves)
    _print_traits(traits, limit if limit > 0 else len(traits))

    print()
    fmt_note = f" / format '{format_code}'" if format_code else ""
    print(
        f"  Preview complete.  Leader: {leader_code}{fmt_note}  "
        f"|  {total_decks} deck(s) sampled  |  read-only"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
