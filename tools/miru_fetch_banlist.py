"""
miru_fetch_banlist.py

Convert a saved banlist-style JSON response on disk into a Miru legality intake CSV.

This first version is sandbox-only and local-file only:
  - no DB writes
  - no snapshot refresh
  - no queueing
  - no live network fetching

NOTE: The output fields (ban_status, restriction_count, format_code, effective_date)
are NOT yet wired as stored dossier facts in the Miru pipeline. This CSV is an
inspectable staging output only. Full legality storage is a separate subsequent step.

Usage:
    python -m tools.miru_fetch_banlist saved_banlist.json --format-code OP-FORMAT
    python -m tools.miru_fetch_banlist --input-json data/banlist/op_format.json --format-code OP-FORMAT -o data/op_format_banlist_intake.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

OUTPUT_COLUMNS = [
    "card_code",
    "ban_status",
    "restriction_count",
    "format_code",
    "effective_date",
    "notes",
]

ROW_LIST_KEYS = ("cards", "data", "results", "items", "records", "banlist", "list", "entries")
WRAPPER_KEYS = ("card", "attributes", "fields", "details", "restriction")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a saved banlist-style JSON response into a Miru legality intake CSV."
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help="Path to the saved JSON response.",
    )
    parser.add_argument(
        "--input-json",
        default="",
        metavar="PATH",
        help="Path to the saved JSON response (alternative to positional input path).",
    )
    parser.add_argument(
        "--format-code",
        default="",
        metavar="CODE",
        help="Format/ruleset code to write into every output row (e.g. OP-FORMAT). "
             "Overrides any format_code found in the JSON.",
    )
    parser.add_argument(
        "--effective-date",
        default="",
        metavar="DATE",
        help="Effective date override for every output row (e.g. 2025-06-01). "
             "Overrides any effective_date found in the JSON.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        metavar="PATH",
        help="Output CSV path (default: data/<format_code>_banlist_intake.csv).",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=1,
        metavar="N",
        help="Fail if fewer than N rows are parsed (default: 1).",
    )
    return parser


def resolve_input_path(args: argparse.Namespace) -> Path:
    candidates = [value for value in (args.input_path, args.input_json) if str(value or "").strip()]
    if len(candidates) != 1:
        raise ValueError("Provide exactly one JSON input via the positional path or --input-json.")
    path = Path(candidates[0])
    if not path.exists():
        raise ValueError(f"Input JSON not found: {path}")
    return path


def default_output_path(format_code: str) -> Path:
    slug = re.sub(r"[^a-z0-9_-]+", "_", format_code.strip().lower()).strip("_") or "banlist"
    return ROOT / "data" / f"{slug}_banlist_intake.csv"


def load_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse JSON from {path}: {exc}") from exc


def extract_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON payload must be a dict or list.")

    for key in ROW_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for inner_key in ROW_LIST_KEYS:
                inner = value.get(inner_key)
                if isinstance(inner, list):
                    return [item for item in inner if isinstance(item, dict)]

    if any(key in payload for key in ("card_code", "cardCode", "code", "ban_status", "status")):
        return [payload]

    raise ValueError(
        "Could not find a banlist record list. Expected a top-level list or a dict with one of: "
        f"{', '.join(ROW_LIST_KEYS)}."
    )


def candidate_maps(record: dict[str, Any]) -> list[dict[str, Any]]:
    maps = [record]
    for key in WRAPPER_KEYS:
        value = record.get(key)
        if isinstance(value, dict):
            maps.append(value)
    return maps


def pick_value(record: dict[str, Any], *aliases: str) -> Any:
    for mapping in candidate_maps(record):
        for alias in aliases:
            if alias in mapping and mapping[alias] not in (None, ""):
                return mapping[alias]
    return None


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def infer_format_code(payload: Any, row_record: dict[str, Any], override: str) -> str:
    if override.strip():
        return override.strip()
    direct = clean_text(pick_value(row_record, "format_code", "formatCode", "format", "ruleset"))
    if direct:
        return direct
    if isinstance(payload, dict):
        for container in (payload, payload.get("meta"), payload.get("format")):
            if isinstance(container, dict):
                for key in ("format_code", "formatCode", "format", "ruleset", "name", "title"):
                    value = clean_text(container.get(key))
                    if value:
                        return value
    return ""


def infer_effective_date(payload: Any, row_record: dict[str, Any], override: str) -> str:
    if override.strip():
        return override.strip()
    direct = clean_text(
        pick_value(row_record, "effective_date", "effectiveDate", "date", "updated_at", "updatedAt")
    )
    if direct:
        return direct
    if isinstance(payload, dict):
        for container in (payload, payload.get("meta")):
            if isinstance(container, dict):
                for key in ("effective_date", "effectiveDate", "date", "updated_at", "updatedAt"):
                    value = clean_text(container.get(key))
                    if value:
                        return value
    return ""


def build_row(
    record: dict[str, Any],
    *,
    payload: Any,
    format_code_override: str,
    effective_date_override: str,
    row_number: int,
) -> dict[str, str]:
    card_code = clean_text(
        pick_value(record, "card_code", "cardCode", "code", "number", "card_number")
    )
    if not card_code:
        raise ValueError(
            f"Row {row_number} is missing card_code/code. "
            f"Available top-level keys: {sorted(record.keys())}"
        )

    return {
        "card_code": card_code,
        "ban_status": clean_text(
            pick_value(
                record,
                "ban_status",
                "banStatus",
                "status",
                "legality_status",
                "legalityStatus",
                "restriction_status",
                "restrictionStatus",
                "legality",
            )
        ),
        "restriction_count": clean_text(
            pick_value(
                record,
                "restriction_count",
                "restrictionCount",
                "limit",
                "copies_allowed",
                "copiesAllowed",
                "max_copies",
                "maxCopies",
                "count",
            )
        ),
        "format_code": infer_format_code(payload, record, format_code_override),
        "effective_date": infer_effective_date(payload, record, effective_date_override),
        "notes": clean_text(
            pick_value(record, "notes", "note", "reason", "comment", "remarks")
        ),
    }


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        input_path = resolve_input_path(args)
        payload = load_payload(input_path)
        records = extract_records(payload)
        rows = [
            build_row(
                record,
                payload=payload,
                format_code_override=args.format_code,
                effective_date_override=args.effective_date,
                row_number=index,
            )
            for index, record in enumerate(records, start=1)
        ]
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if len(rows) < max(args.min_rows, 1):
        print(
            f"ERROR: parsed only {len(rows)} row(s) from {input_path}; "
            f"this is below --min-rows={max(args.min_rows, 1)} and looks suspicious.",
            file=sys.stderr,
        )
        return 1

    output_path = (
        Path(args.output)
        if args.output
        else default_output_path(args.format_code or "banlist")
    )

    try:
        write_csv(rows, output_path)
    except OSError as exc:
        print(f"ERROR: could not write CSV to {output_path}: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {len(rows)} row(s) to {output_path}")
    print(
        "NOTE: ban_status, restriction_count, format_code, and effective_date are staging fields "
        "only. They are not yet wired as stored dossier facts in the Miru pipeline."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
