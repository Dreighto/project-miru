"""
miru_import_card_csv.py

Converts a CSV or TSV card-list file into the official-export JSON envelope
expected by miru_refresh_official_snapshot.py / normalize_official_export().

Usage:
    python -m tools.miru_import_card_csv <input.csv> [options]

Options:
    -o / --output         Output JSON path (default: <input_stem>_snapshot.json)
    --tsv                 Treat input as tab-separated (auto-detected for .tsv)
    --set-code CODE       Override / fill setCode for all rows
    --set-name NAME       Override / fill setName for all rows
    --snapshot-date DATE  snapshotTakenAt value (default: today YYYY-MM-DD 00:00:00)
    --source-key KEY      sourceKey in export_meta (default: official-cardlist)
    --dry-run             Validate rows and print a report without writing output
    --db-path PATH        Path to miru_dossiers.db for comparison against stored facts

Accepted CSV column names (case-insensitive):
    card_code / code / cardCode             -> cardCode
    name / card_name / cardName             -> name
    set_code / setCode                      -> setCode
    set_name / setName                      -> setName
    rarity                                  -> rarity
    color                                   -> color
    type / card_type / cardType             -> cardType
    cost                                    -> cost
    power                                   -> power
    counter                                 -> counter
    attribute                               -> attribute
    life                                    -> life
    effect / effect_text / effectText       -> effectText
    trigger / trigger_text / triggerText    -> triggerText
    image / image_url / imageUrl            -> imageUrl
    updated_at / updatedAt                  -> updatedAt

Columns not present in the CSV are silently omitted from each row.
The normalizer treats absent fields as "missing", which is correct behaviour.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Column alias map  (lowercase CSV header -> output JSON key)
# ---------------------------------------------------------------------------
_COLUMN_ALIASES: dict[str, str] = {
    "card_code": "cardCode",
    "cardcode": "cardCode",
    "code": "cardCode",
    "name": "name",
    "card_name": "name",
    "cardname": "name",
    "set_code": "setCode",
    "setcode": "setCode",
    "set_name": "setName",
    "setname": "setName",
    "rarity": "rarity",
    "color": "color",
    "colour": "color",
    "type": "cardType",
    "card_type": "cardType",
    "cardtype": "cardType",
    "cost": "cost",
    "power": "power",
    "counter": "counter",
    "attribute": "attribute",
    "life": "life",
    "effect": "effectText",
    "effect_text": "effectText",
    "effecttext": "effectText",
    "trigger": "triggerText",
    "trigger_text": "triggerText",
    "triggertext": "triggerText",
    "image": "imageUrl",
    "image_url": "imageUrl",
    "imageurl": "imageUrl",
    "updated_at": "updatedAt",
    "updatedat": "updatedAt",
    # traits / type affiliations
    "traits": "traits",
    "type_tags": "traits",
    "typetags": "traits",
    "affiliations": "traits",
    "affiliation": "traits",
    "type_affiliations": "traits",
    "typeaffiliations": "traits",
    # block number (format legality / banlist support)
    "block_number": "blockNumber",
    "blocknumber": "blockNumber",
    "block_no": "blockNumber",
    "blockno": "blockNumber",
    "block": "blockNumber",
}

# Card types that are expected to carry effect text
_EFFECT_EXPECTED_TYPES = {"character", "event", "stage"}

# JSON keys whose CSV values are pipe-separated lists and should be
# written as JSON arrays rather than plain strings.
_LIST_COLUMNS = {"traits"}

# Thresholds for effect-text length comparison against stored verified value
_BLOCK_RATIO = 0.25   # < 25% of stored length -> BLOCK
_WARN_RATIO  = 0.75   # < 75% of stored length -> WARN (but >= 25%)


def _map_header(raw: str) -> str | None:
    """Return the canonical JSON key for a CSV column header, or None if unknown."""
    return _COLUMN_ALIASES.get(raw.strip().lower())


# ---------------------------------------------------------------------------
# Validation dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RowIssue:
    card_code: str
    severity: str   # "BLOCK" | "WARN"
    field: str
    message: str


@dataclass
class ValidationReport:
    issues: list[RowIssue] = field(default_factory=list)

    @property
    def block_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "BLOCK")

    @property
    def warn_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == "WARN")

    @property
    def has_blocks(self) -> bool:
        return self.block_count > 0


# ---------------------------------------------------------------------------
# Core converter
# ---------------------------------------------------------------------------

def convert(
    input_path: Path,
    *,
    delimiter: str = ",",
    set_code_override: str = "",
    set_name_override: str = "",
    snapshot_date: str = "",
    source_key: str = "official-cardlist",
) -> dict:
    """Read a CSV/TSV file and return the export JSON payload as a dict."""
    if not snapshot_date:
        snapshot_date = f"{date.today()} 00:00:00"
    if not source_key:
        source_key = "official-cardlist"

    rows: list[dict] = []

    with input_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)

        if reader.fieldnames is None:
            raise ValueError(f"Input file appears to be empty: {input_path}")

        # Build a mapping from raw fieldname -> canonical JSON key (skip unknowns)
        col_map: dict[str, str] = {}
        for raw in reader.fieldnames:
            canonical = _map_header(raw)
            if canonical:
                col_map[raw] = canonical

        if not col_map:
            known = sorted(_COLUMN_ALIASES.keys())
            raise ValueError(
                f"No recognised columns found in {input_path}.\n"
                f"Headers found: {list(reader.fieldnames)}\n"
                f"Recognised aliases: {known}"
            )

        for raw_row in reader:
            row: dict[str, str] = {}
            for raw_col, json_key in col_map.items():
                value = (raw_row.get(raw_col) or "").strip()
                if value:
                    if json_key in _LIST_COLUMNS:
                        # Split pipe-separated values into a JSON array so the
                        # normalizer receives a proper list (e.g. "A|B" -> ["A","B"])
                        row[json_key] = [p.strip() for p in value.split("|") if p.strip()]
                    else:
                        row[json_key] = value

            # Apply CLI overrides (only if not already present in the row)
            if set_code_override and not row.get("setCode"):
                row["setCode"] = set_code_override
            if set_name_override and not row.get("setName"):
                row["setName"] = set_name_override

            # Skip entirely blank rows (e.g. trailing newlines)
            if not any(row.values()):
                continue

            rows.append(row)

    return {
        "export_meta": {
            "sourceKey": source_key,
            "snapshotTakenAt": snapshot_date,
        },
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _get_stored_facts(db_path: Path, card_code: str) -> dict[str, str]:
    """
    Return a dict of {field_name: value_text} for all *verified* facts
    stored for the given card in miru_dossiers.db.

    Uses a direct read-only SQLite query against the cards + card_facts
    tables — consistent with how miru_compute_trait_signals and
    miru_compute_cost_curves access the dossiers DB.

    Returns {} if the card has no entry, the DB is inaccessible, or any
    error occurs.
    """
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return {}

    result: dict[str, str] = {}
    try:
        rows = conn.execute(
            """
            SELECT cf.field_name, cf.value_text
            FROM card_facts cf
            JOIN cards c ON c.id = cf.card_id
            WHERE c.canonical_code = ?
              AND cf.verification_state = 'verified'
            ORDER BY cf.updated_at DESC
            """,
            (card_code.strip().upper(),),
        ).fetchall()
        seen: set[str] = set()
        for row in rows:
            fn = row["field_name"]
            vt = row["value_text"] or ""
            if fn and vt and fn not in seen:
                result[fn] = vt
                seen.add(fn)
    except sqlite3.Error:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return result


def validate_rows(
    rows: list[dict],
    db_path: Optional[Path] = None,
) -> ValidationReport:
    """
    Validate a list of converted rows and return a ValidationReport.

    BLOCK conditions (exit 1 in --dry-run):
      - cardCode absent
      - name absent AND stored dossier has a verified card_name
      - effectText present but < 25% of stored verified effect_text length

    WARN conditions (exit 0, printed as warnings):
      - name absent (no stored comparison available)
      - effectText absent for CHARACTER / EVENT / STAGE card types
      - effectText present but 25-75% of stored verified effect_text length
      - color absent
      - rarity absent
    """
    report = ValidationReport()

    for row in rows:
        code = row.get("cardCode", "").strip()

        # --- BLOCK: no card code ---
        if not code:
            report.issues.append(RowIssue(
                card_code="(unknown)",
                severity="BLOCK",
                field="cardCode",
                message="cardCode absent - row cannot be ingested",
            ))
            continue

        # Optional DB lookup
        stored: dict[str, str] = {}
        if db_path is not None:
            stored = _get_stored_facts(db_path, code)

        incoming_name   = row.get("name", "").strip()
        incoming_effect = row.get("effectText", "").strip()
        incoming_color  = row.get("color", "").strip()
        incoming_rarity = row.get("rarity", "").strip()
        incoming_type   = row.get("cardType", "").strip().lower()

        stored_name   = stored.get("card_name", "")
        stored_effect = stored.get("effect_text", "")

        # --- name checks ---
        if not incoming_name:
            if stored_name:
                report.issues.append(RowIssue(
                    card_code=code,
                    severity="BLOCK",
                    field="name",
                    message=(
                        f"name absent but stored dossier has verified name "
                        f"({repr(stored_name[:60])}{'...' if len(stored_name) > 60 else ''})"
                    ),
                ))
            else:
                report.issues.append(RowIssue(
                    card_code=code,
                    severity="WARN",
                    field="name",
                    message="name absent (no stored comparison available)",
                ))

        # --- effectText checks ---
        if not incoming_effect:
            if incoming_type in _EFFECT_EXPECTED_TYPES:
                report.issues.append(RowIssue(
                    card_code=code,
                    severity="WARN",
                    field="effectText",
                    message=f"effectText absent for {incoming_type.upper()} card",
                ))
        else:
            if stored_effect:
                ratio = len(incoming_effect) / max(len(stored_effect), 1)
                if ratio < _BLOCK_RATIO:
                    pct = int(ratio * 100)
                    report.issues.append(RowIssue(
                        card_code=code,
                        severity="BLOCK",
                        field="effectText",
                        message=(
                            f"incoming ({len(incoming_effect)} chars) is {pct}% of "
                            f"stored verified value ({len(stored_effect)} chars) - "
                            f"likely truncated"
                        ),
                    ))
                elif ratio < _WARN_RATIO:
                    pct = int(ratio * 100)
                    report.issues.append(RowIssue(
                        card_code=code,
                        severity="WARN",
                        field="effectText",
                        message=(
                            f"incoming ({len(incoming_effect)} chars) is {pct}% of "
                            f"stored verified value ({len(stored_effect)} chars) - "
                            f"verify for truncation"
                        ),
                    ))

        # --- color / rarity ---
        if not incoming_color:
            report.issues.append(RowIssue(
                card_code=code, severity="WARN", field="color",
                message="color absent",
            ))
        if not incoming_rarity:
            report.issues.append(RowIssue(
                card_code=code, severity="WARN", field="rarity",
                message="rarity absent",
            ))

    return report


def _print_report(report: ValidationReport, *, total_rows: int) -> None:
    """Print the validation report to stdout."""
    if not report.issues:
        print(f"Validation OK: {total_rows} row(s), no issues found.")
        return

    print(f"Validating {total_rows} row(s)...\n")
    for issue in report.issues:
        print(f"  {issue.card_code:<12} {issue.severity:<5}  {issue.field:<14}: {issue.message}")

    print()
    print(
        f"Summary: {report.warn_count} warning(s), {report.block_count} block(s)"
    )
    if report.has_blocks:
        print("ERROR: blocking issue(s) found - fix the CSV and re-run.", file=sys.stderr)
    else:
        print("Warnings present - review before proceeding.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert a CSV/TSV card list into a Miru official-export JSON snapshot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_path", help="Path to the CSV or TSV input file.")
    parser.add_argument(
        "-o", "--output",
        help="Output JSON path (default: <input_stem>_snapshot.json next to the input file).",
    )
    parser.add_argument(
        "--tsv",
        action="store_true",
        help="Treat input as tab-separated regardless of file extension.",
    )
    parser.add_argument(
        "--set-code",
        default="",
        metavar="CODE",
        help="Override / fill setCode for all rows (e.g. EB04).",
    )
    parser.add_argument(
        "--set-name",
        default="",
        metavar="NAME",
        help="Override / fill setName for all rows.",
    )
    parser.add_argument(
        "--snapshot-date",
        default="",
        metavar="DATE",
        help="snapshotTakenAt value (default: today, YYYY-MM-DD 00:00:00).",
    )
    parser.add_argument(
        "--source-key",
        default="official-cardlist",
        metavar="KEY",
        help="sourceKey written into export_meta (default: official-cardlist).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate rows and print a report without writing JSON output. "
            "Exits with code 1 if any BLOCK-level issue is found."
        ),
    )
    parser.add_argument(
        "--db-path",
        default="",
        metavar="PATH",
        help=(
            "Path to miru_dossiers.db. When provided, stored verified facts are "
            "compared against incoming data to detect overwrites and truncations."
        ),
    )

    args = parser.parse_args(argv)

    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    db_path: Optional[Path] = None
    if args.db_path:
        db_path = Path(args.db_path)
        if not db_path.exists():
            print(f"ERROR: --db-path not found: {db_path}", file=sys.stderr)
            return 1

    # Auto-detect delimiter
    delimiter = "\t" if (args.tsv or input_path.suffix.lower() == ".tsv") else ","

    # Resolve output path (not used in --dry-run, but computed for consistency)
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.with_name(input_path.stem + "_snapshot.json")

    try:
        payload = convert(
            input_path,
            delimiter=delimiter,
            set_code_override=args.set_code,
            set_name_override=args.set_name,
            snapshot_date=args.snapshot_date,
            source_key=args.source_key,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    rows = payload["rows"]

    # --- Validation (runs when --dry-run or --db-path is provided) ---
    if args.dry_run or db_path is not None:
        report = validate_rows(rows, db_path=db_path)
        _print_report(report, total_rows=len(rows))

        if args.dry_run:
            # In dry-run mode: never write output; exit code reflects blocks
            return 1 if report.has_blocks else 0

        # Normal mode with --db-path: print warnings but still write output
        # unless there are blocks (safety gate)
        if report.has_blocks:
            print(
                "ERROR: blocking issue(s) found - output not written. "
                "Use --dry-run for full details or fix the CSV.",
                file=sys.stderr,
            )
            return 1

    # --- Write output ---
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    card_count = len(rows)
    print(f"OK: {card_count} row(s) written to {output_path}")
    if card_count == 0:
        print(
            "WARNING: no card rows were produced - check that your CSV has "
            "data rows and recognised column headers.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
