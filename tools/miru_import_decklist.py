"""
miru_import_decklist.py

Read a staging CSV produced by miru_fetch_decklist.py and import it into the Miru
deck-intelligence SQLite database.

Schema (created on first run if absent):
    deck_sources  -- provenance of each ingestion batch
    decklists     -- one row per unique deck (keyed by deck_uid)
    deck_entries  -- one row per card per deck (quantity included)

Duplicate deck_uid: skipped by default (conservative / non-destructive).
Re-running the same file is always safe.

Usage:
    python -m tools.miru_import_decklist staging.csv
    python -m tools.miru_import_decklist staging.csv --dry-run
    python -m tools.miru_import_decklist staging.csv --db-path data/miru_deck_intel.db
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "miru_deck_intel.db"

# Expected columns from miru_fetch_decklist.py output
EXPECTED_COLUMNS = {
    "deck_uid", "leader_code", "card_code", "quantity",
    "format_code", "source_reference", "placement", "notes",
}

_LEGAL_MAX_QTY = 4
_DECK_MIN_TOTAL = 10   # total copies across all entries (suspiciously low)
_DECK_MAX_TOTAL = 80   # total copies (suspiciously high for a 50-card format)


# ---------------------------------------------------------------------------
# Guardrail dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DeckIssue:
    ref: str        # deck_uid or card_code identifying context
    severity: str   # "BLOCK" | "WARN"
    field: str
    message: str


@dataclass
class ValidationReport:
    issues: list[DeckIssue] = field(default_factory=list)

    @property
    def block_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "BLOCK")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARN")

    @property
    def has_blocks(self) -> bool:
        return self.block_count > 0

    def add_block(self, ref: str, field_name: str, message: str) -> None:
        self.issues.append(DeckIssue(ref=ref, severity="BLOCK", field=field_name, message=message))

    def add_warn(self, ref: str, field_name: str, message: str) -> None:
        self.issues.append(DeckIssue(ref=ref, severity="WARN", field=field_name, message=message))


# ---------------------------------------------------------------------------
# CSV reading
# ---------------------------------------------------------------------------

@dataclass
class DeckRow:
    deck_uid: str
    leader_code: str
    card_code: str
    quantity: int
    format_code: str
    source_reference: str
    placement: str
    notes: str


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def read_staging_csv(input_path: Path) -> list[DeckRow]:
    """Read and lightly normalise a staging CSV from miru_fetch_decklist.py."""
    rows: list[DeckRow] = []
    with input_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"CSV appears to be empty: {input_path}")
        headers = {h.lower().strip() for h in reader.fieldnames}
        missing = EXPECTED_COLUMNS - headers
        if missing:
            raise ValueError(
                f"CSV is missing expected column(s): {', '.join(sorted(missing))}. "
                "Was this file produced by miru_fetch_decklist.py?"
            )
        for i, raw in enumerate(reader, start=2):  # row 1 is header
            qty_raw = _clean(raw.get("quantity") or "1")
            try:
                qty = max(int(qty_raw), 1)
            except (ValueError, TypeError):
                qty = 1
            rows.append(
                DeckRow(
                    deck_uid=_clean(raw.get("deck_uid")),
                    leader_code=_clean(raw.get("leader_code")).upper(),
                    card_code=_clean(raw.get("card_code")).upper(),
                    quantity=qty,
                    format_code=_clean(raw.get("format_code")),
                    source_reference=_clean(raw.get("source_reference")),
                    placement=_clean(raw.get("placement")),
                    notes=_clean(raw.get("notes")),
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Validation / guardrails
# ---------------------------------------------------------------------------

def validate_rows(rows: list[DeckRow]) -> ValidationReport:
    report = ValidationReport()

    if not rows:
        report.add_block("(file)", "rows", "CSV contains no data rows.")
        return report

    # Collect deck-level aggregates
    deck_uids: set[str] = set()
    leader_codes: set[str] = set()
    total_qty = 0
    qty_by_card: dict[str, int] = {}

    for i, row in enumerate(rows, start=1):
        # --- BLOCK: missing identifiers ---
        if not row.deck_uid:
            report.add_block(f"row {i}", "deck_uid", "deck_uid is absent or empty.")
        if not row.leader_code:
            report.add_block(f"row {i}", "leader_code", "leader_code is absent or empty.")
        if not row.card_code:
            report.add_block(f"row {i}", "card_code", "card_code is absent or empty.")
            continue  # skip further checks for this row if code is missing

        deck_uids.add(row.deck_uid)
        leader_codes.add(row.leader_code)
        total_qty += row.quantity
        qty_by_card[row.card_code] = qty_by_card.get(row.card_code, 0) + row.quantity

    # --- WARN: per-card quantity violations ---
    for card_code, qty in qty_by_card.items():
        if qty > _LEGAL_MAX_QTY:
            report.add_warn(
                card_code, "quantity",
                f"{qty} copies exceeds the legal {_LEGAL_MAX_QTY}-copy limit.",
            )

    # --- WARN: suspicious total deck size ---
    if total_qty < _DECK_MIN_TOTAL:
        report.add_warn(
            "(deck)", "total_qty",
            f"Total card quantity ({total_qty}) is suspiciously low for a complete deck.",
        )
    elif total_qty > _DECK_MAX_TOTAL:
        report.add_warn(
            "(deck)", "total_qty",
            f"Total card quantity ({total_qty}) is suspiciously high.",
        )

    # --- WARN: multiple distinct deck_uids (multi-deck file is unexpected for MVP) ---
    if len(deck_uids) > 1:
        report.add_warn(
            "(file)", "deck_uid",
            f"File contains {len(deck_uids)} distinct deck_uids. "
            "MVP expects one deck per file; only the first uid will be imported.",
        )

    # --- WARN: multiple distinct leader codes (probably a fetch error) ---
    if len(leader_codes) > 1:
        report.add_warn(
            "(file)", "leader_code",
            f"File contains {len(leader_codes)} distinct leader codes: "
            f"{', '.join(sorted(leader_codes))}.",
        )

    return report


# ---------------------------------------------------------------------------
# Source key derivation
# ---------------------------------------------------------------------------

def _derive_source_key(source_reference: str) -> str:
    """Derive a short stable key from a URL or free-text reference."""
    ref = source_reference.strip()
    if not ref:
        return "user-manual"
    try:
        parsed = urlparse(ref)
        if parsed.netloc:
            # e.g. "limitless-tcg" from "limitless-tcg.com"
            host = parsed.netloc.lower().lstrip("www.")
            key = re.sub(r"[^a-z0-9]+", "-", host.split(".")[0]).strip("-")
            return key or "web-source"
    except Exception:
        pass
    slug = re.sub(r"[^a-z0-9]+", "-", ref.lower())[:32].strip("-")
    return slug or "user-manual"


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deck_sources (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key       TEXT    NOT NULL DEFAULT 'user-manual',
    source_reference TEXT    NOT NULL DEFAULT '',
    format_code      TEXT    NOT NULL DEFAULT '',
    created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS decklists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid    TEXT    UNIQUE NOT NULL,
    leader_code TEXT    NOT NULL,
    format_code TEXT    NOT NULL DEFAULT '',
    source_id   INTEGER REFERENCES deck_sources(id),
    placement   TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS deck_entries (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_uid  TEXT    NOT NULL REFERENCES decklists(deck_uid),
    card_code TEXT    NOT NULL,
    quantity  INTEGER NOT NULL DEFAULT 1,
    UNIQUE(deck_uid, card_code)
);

CREATE INDEX IF NOT EXISTS idx_decklists_leader ON decklists(leader_code);
CREATE INDEX IF NOT EXISTS idx_deck_entries_card  ON deck_entries(card_code);
CREATE INDEX IF NOT EXISTS idx_deck_entries_deck  ON deck_entries(deck_uid);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Import logic
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _find_or_create_source(
    conn: sqlite3.Connection,
    source_key: str,
    source_reference: str,
    format_code: str,
    now: str,
) -> int:
    """Return id of an existing matching source row, or insert and return new id."""
    row = conn.execute(
        "SELECT id FROM deck_sources WHERE source_key = ? AND source_reference = ? AND format_code = ?",
        (source_key, source_reference, format_code),
    ).fetchone()
    if row:
        return int(row[0])
    cursor = conn.execute(
        "INSERT INTO deck_sources (source_key, source_reference, format_code, created_at) VALUES (?, ?, ?, ?)",
        (source_key, source_reference, format_code, now),
    )
    return cursor.lastrowid


def import_deck(
    conn: sqlite3.Connection,
    rows: list[DeckRow],
    now: str,
) -> dict:
    """
    Import a single deck's rows into the database.

    Returns a summary dict with keys: deck_uid, leader_code, skipped (bool),
    entries_written, entries_skipped.
    """
    # Use the first row's metadata (all rows should share the same deck-level fields)
    first = rows[0]
    deck_uid = first.deck_uid
    leader_code = first.leader_code
    format_code = first.format_code
    placement = first.placement
    source_reference = first.source_reference

    # Check for existing deck — skip if already present
    existing = conn.execute(
        "SELECT id FROM decklists WHERE deck_uid = ?", (deck_uid,)
    ).fetchone()
    if existing:
        return {
            "deck_uid": deck_uid,
            "leader_code": leader_code,
            "skipped": True,
            "entries_written": 0,
            "entries_skipped": len(rows),
        }

    source_key = _derive_source_key(source_reference)
    source_id = _find_or_create_source(conn, source_key, source_reference, format_code, now)

    conn.execute(
        "INSERT INTO decklists (deck_uid, leader_code, format_code, source_id, placement, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (deck_uid, leader_code, format_code, source_id, placement, now),
    )

    entries_written = 0
    entries_skipped = 0
    for row in rows:
        if not row.card_code:
            entries_skipped += 1
            continue
        cursor = conn.execute(
            "INSERT OR IGNORE INTO deck_entries (deck_uid, card_code, quantity) VALUES (?, ?, ?)",
            (deck_uid, row.card_code, row.quantity),
        )
        if cursor.rowcount:
            entries_written += 1
        else:
            entries_skipped += 1

    return {
        "deck_uid": deck_uid,
        "leader_code": leader_code,
        "skipped": False,
        "entries_written": entries_written,
        "entries_skipped": entries_skipped,
    }


# ---------------------------------------------------------------------------
# Report printing
# ---------------------------------------------------------------------------

def print_report(report: ValidationReport, *, total_rows: int) -> None:
    if not report.issues:
        print(f"Validation OK: {total_rows} row(s) - no issues found.")
        return

    print(f"Validating {total_rows} row(s)...\n")
    for issue in report.issues:
        print(f"  {issue.ref:<16} {issue.severity:<5}  {issue.field:<14}: {issue.message}")

    print()
    print(f"Summary: {report.warn_count} warning(s), {report.block_count} block(s)")
    if report.has_blocks:
        print("ERROR: blocking issue(s) found - fix the staging CSV and re-run.", file=sys.stderr)
    else:
        print("Warnings present - review before proceeding.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a miru_fetch_decklist.py staging CSV into the deck-intelligence DB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_path", help="Path to the staging CSV file.")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        metavar="PATH",
        help=f"Deck intelligence DB path (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only. No DB writes. Exits with code 1 if any BLOCK-level issue is found.",
    )
    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    # --- Read CSV ---
    try:
        rows = read_staging_csv(input_path)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    total_rows = len(rows)
    if total_rows == 0:
        print("ERROR: staging CSV contains no data rows.", file=sys.stderr)
        return 1

    # --- Validate ---
    report = validate_rows(rows)
    if report.issues:
        print_report(report, total_rows=total_rows)
    else:
        print(f"Validation OK: {total_rows} row(s) - no issues found.")

    if report.has_blocks:
        print(f"\nIMPORT STOPPED: {report.block_count} blocking issue(s). Fix CSV and re-run.", file=sys.stderr)
        return 1

    # --- Dry-run exits here ---
    if args.dry_run:
        print(
            f"\nDry-run complete. {total_rows} row(s) validated "
            f"({report.warn_count} warning(s), 0 blocks). No DB writes."
        )
        return 0

    # --- Group rows by deck_uid (MVP: typically one uid per file) ---
    decks: dict[str, list[DeckRow]] = {}
    for row in rows:
        if row.deck_uid:
            decks.setdefault(row.deck_uid, []).append(row)

    if not decks:
        print("ERROR: No rows with a valid deck_uid found.", file=sys.stderr)
        return 1

    # --- Open / create DB ---
    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_schema(conn)
    except sqlite3.Error as exc:
        print(f"ERROR: could not open/create DB at {db_path}: {exc}", file=sys.stderr)
        return 1

    # --- Import ---
    now = _utcnow()
    results = []
    try:
        with conn:  # transaction
            for deck_uid, deck_rows in decks.items():
                result = import_deck(conn, deck_rows, now)
                results.append(result)
    except sqlite3.Error as exc:
        print(f"ERROR: DB write failed: {exc}", file=sys.stderr)
        conn.close()
        return 1
    finally:
        conn.close()

    # --- Summary ---
    imported = [r for r in results if not r["skipped"]]
    skipped  = [r for r in results if r["skipped"]]
    total_entries_written = sum(r["entries_written"] for r in imported)

    print()
    for r in imported:
        print(
            f"  Imported  deck {r['deck_uid']}  leader {r['leader_code']}"
            f"  ({r['entries_written']} entries)"
        )
    for r in skipped:
        print(
            f"  Skipped   deck {r['deck_uid']}  (already in DB)"
        )

    print(
        f"\nDone. {len(imported)} deck(s) imported, {len(skipped)} skipped, "
        f"{total_entries_written} entries written to {db_path}"
    )
    if report.warn_count:
        print(f"Note: {report.warn_count} warning(s) accepted. Review with --dry-run if needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
