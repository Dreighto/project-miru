"""
miru_fetch_optcgdb.py

Convert a saved OptCGDB-style JSON response on disk into a Miru intake CSV.

This first version is sandbox-only and local-file only:
  - no DB writes
  - no queueing
  - no snapshot refresh
  - no live network fetching

Usage:
    python -m tools.miru_fetch_optcgdb saved_response.json --set-code OP09
    python -m tools.miru_fetch_optcgdb --input-json data/optcgdb/op09.json --set-code OP09 -o data/op09_optcgdb_intake.csv
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
    "name",
    "set_code",
    "set_name",
    "rarity",
    "color",
    "card_type",
    "cost",
    "attribute",
    "power",
    "counter",
    "life",
    "block_number",
    "traits",
    "effect_text",
    "trigger_text",
]

ROW_LIST_KEYS = ("cards", "data", "results", "items", "records")
WRAPPER_KEYS = ("card", "attributes", "fields", "details")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a saved OptCGDB-style JSON response into a Miru intake CSV."
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
        "--set-code",
        required=True,
        metavar="CODE",
        help="Set code to write into every output row (for example: OP09).",
    )
    parser.add_argument(
        "--set-name",
        default="",
        metavar="NAME",
        help="Optional set-name override for every output row.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        metavar="PATH",
        help="Output CSV path (default: data/<set_code>_optcgdb_intake.csv).",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=3,
        metavar="N",
        help="Fail if fewer than N rows are parsed (default: 3).",
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


def default_output_path(set_code: str) -> Path:
    slug = re.sub(r"[^a-z0-9_-]+", "_", set_code.strip().lower()).strip("_") or "set"
    return ROOT / "data" / f"{slug}_optcgdb_intake.csv"


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

    if any(key in payload for key in ("card_code", "cardCode", "code", "name")):
        return [payload]

    raise ValueError(
        "Could not find a card record list. Expected a top-level list or a dict with one of: "
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


def normalize_traits(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [clean_text(item) for item in value]
        return "|".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("names", "values", "items", "traits"):
            nested = value.get(key)
            if nested is not None:
                return normalize_traits(nested)
        return ""
    text = clean_text(value)
    if not text:
        return ""
    parts = [part.strip() for part in re.split(r"\s*\|\s*|\s*,\s*|\s*/\s*", text) if part.strip()]
    return "|".join(parts)


def infer_set_name(payload: Any, row_record: dict[str, Any], override: str) -> str:
    if override.strip():
        return override.strip()
    direct = clean_text(pick_value(row_record, "set_name", "setName", "set"))
    if direct:
        return direct
    if isinstance(payload, dict):
        for container in (payload, payload.get("meta"), payload.get("set")):
            if isinstance(container, dict):
                for key in ("set_name", "setName", "name", "title"):
                    value = clean_text(container.get(key))
                    if value:
                        return value
    return ""


def build_row(record: dict[str, Any], *, payload: Any, set_code: str, set_name_override: str, row_number: int) -> dict[str, str]:
    card_code = clean_text(
        pick_value(record, "card_code", "cardCode", "code", "number", "card_number")
    )
    if not card_code:
        raise ValueError(
            f"Row {row_number} is missing card_code/code. Available top-level keys: {sorted(record.keys())}"
        )

    effect_value = pick_value(
        record,
        "effect_text",
        "effectText",
        "effect",
        "text",
        "main_effect",
        "card_text",
    )
    trigger_value = pick_value(record, "trigger_text", "triggerText", "trigger")

    return {
        "card_code": card_code,
        "name": clean_text(pick_value(record, "name", "card_name", "cardName")),
        "set_code": set_code.strip(),
        "set_name": infer_set_name(payload, record, set_name_override),
        "rarity": clean_text(pick_value(record, "rarity", "rarity_name", "rarityName")),
        "color": clean_text(pick_value(record, "color", "colour")),
        "card_type": clean_text(pick_value(record, "card_type", "cardType", "type")),
        "cost": clean_text(pick_value(record, "cost")),
        "attribute": clean_text(pick_value(record, "attribute")),
        "power": clean_text(pick_value(record, "power")),
        "counter": clean_text(pick_value(record, "counter")),
        "life": clean_text(pick_value(record, "life")),
        "block_number": clean_text(
            pick_value(record, "block_number", "blockNumber", "block", "block_no", "blockNo")
        ),
        "traits": normalize_traits(
            pick_value(
                record,
                "traits",
                "trait",
                "type_tags",
                "typeTags",
                "affiliations",
                "affiliation",
            )
        ),
        "effect_text": clean_text(effect_value),
        "trigger_text": clean_text(trigger_value),
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
                set_code=args.set_code,
                set_name_override=args.set_name,
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

    output_path = Path(args.output) if args.output else default_output_path(args.set_code)

    try:
        write_csv(rows, output_path)
    except OSError as exc:
        print(f"ERROR: could not write CSV to {output_path}: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {len(rows)} row(s) to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
