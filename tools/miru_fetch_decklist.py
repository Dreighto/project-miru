"""
miru_fetch_decklist.py

Convert a saved structured decklist JSON file on disk into a Miru deck intelligence
staging CSV.

This first version is sandbox-only and local-file only:
  - no DB writes
  - no snapshot refresh
  - no queueing
  - no live network fetching

deck_uid is a content-addressable 12-hex-character SHA-256 digest computed from the
normalized deck contents (leader_code + sorted card_code:quantity pairs). Two files
with identical cards and quantities for the same leader always produce the same uid,
regardless of filename or metadata. This makes duplicate detection trivial at import
time.

Usage:
    python -m tools.miru_fetch_decklist saved_deck.json --leader-code OP01-001
    python -m tools.miru_fetch_decklist --input-json data/decks/op01_luffy.json \\
        --leader-code OP01-001 --format-code OP-FORMAT --placement "1st" \\
        -o data/decks/op01_luffy_staging.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

OUTPUT_COLUMNS = [
    "deck_uid",
    "leader_code",
    "card_code",
    "quantity",
    "format_code",
    "source_reference",
    "placement",
    "notes",
]

# Keys that may wrap the card list
CARD_LIST_KEYS = ("cards", "decklist", "main", "maindeck", "deck", "entries", "data", "results")

# Keys where a leader card or leader code may be stored at the top level
LEADER_KEYS = ("leader", "leader_card", "leaderCard", "leader_code", "leaderCode")

# Keys at the item level that identify a card as the leader
LEADER_TYPE_VALUES = frozenset({"leader"})

# Card code field aliases (evaluated in order; first non-empty wins)
CODE_ALIASES = ("card_code", "cardCode", "code", "id", "card_id", "cardId", "number")

# Quantity field aliases
QTY_ALIASES = ("quantity", "qty", "count", "copies", "amount", "num")

# card_type field aliases
TYPE_ALIASES = ("card_type", "cardType", "type", "category")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a saved structured decklist JSON file into a Miru deck staging CSV."
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        help="Path to the saved JSON decklist.",
    )
    parser.add_argument(
        "--input-json",
        default="",
        metavar="PATH",
        help="Path to the saved JSON decklist (alternative to positional input path).",
    )
    parser.add_argument(
        "--leader-code",
        default="",
        metavar="CODE",
        help="Leader card code (e.g. OP01-001). Required if the JSON does not include leader info.",
    )
    parser.add_argument(
        "--format-code",
        default="",
        metavar="CODE",
        help="Format / ruleset code for every output row (e.g. OP-FORMAT).",
    )
    parser.add_argument(
        "--source-reference",
        default="",
        metavar="REF",
        help="URL or label identifying the original source of this decklist.",
    )
    parser.add_argument(
        "--placement",
        default="",
        metavar="TEXT",
        help="Optional tournament placement (e.g. '1st', 'top8', 'winner').",
    )
    parser.add_argument(
        "--notes",
        default="",
        metavar="TEXT",
        help="Optional free-text notes for every output row.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="",
        metavar="PATH",
        help="Output CSV path (default: data/deck_<uid>_staging.csv).",
    )
    parser.add_argument(
        "--min-entries",
        type=int,
        default=10,
        metavar="N",
        help=(
            "Fail if total card quantity (sum of all copies) is below N "
            "(default: 10). A complete deck has 50 cards."
        ),
    )
    return parser


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------

def resolve_input_path(args: argparse.Namespace) -> Path:
    candidates = [v for v in (args.input_path, args.input_json) if str(v or "").strip()]
    if len(candidates) != 1:
        raise ValueError("Provide exactly one JSON input via the positional path or --input-json.")
    path = Path(candidates[0])
    if not path.exists():
        raise ValueError(f"Input JSON not found: {path}")
    return path


def load_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse JSON from {path}: {exc}") from exc


# ---------------------------------------------------------------------------
# Card list extraction
# ---------------------------------------------------------------------------

def _extract_list_from_value(value: Any) -> list[dict[str, Any]] | None:
    """Return a flat list of card dicts from a value, or None if not usable."""
    if isinstance(value, list):
        dicts = [item for item in value if isinstance(item, dict)]
        return dicts if dicts else None
    return None


def extract_card_list(payload: Any) -> list[dict[str, Any]]:
    """Pull the card list out of any common wrapper shape."""
    if isinstance(payload, list):
        dicts = [item for item in payload if isinstance(item, dict)]
        if dicts:
            return dicts
        raise ValueError("Top-level JSON is an empty list or contains no card objects.")

    if not isinstance(payload, dict):
        raise ValueError("Top-level JSON must be a list or object.")

    # Try known card-list wrapper keys
    for key in CARD_LIST_KEYS:
        value = payload.get(key)
        result = _extract_list_from_value(value)
        if result is not None:
            return result

    # Single-card object (rare but handle gracefully)
    if any(alias in payload for alias in CODE_ALIASES):
        return [payload]

    raise ValueError(
        "Could not locate a card list. Expected a top-level list or a dict with one of: "
        f"{', '.join(CARD_LIST_KEYS)}."
    )


# ---------------------------------------------------------------------------
# Leader detection
# ---------------------------------------------------------------------------

def _clean_code(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", "", str(value)).strip().upper()


def _pick_card_code(item: dict[str, Any]) -> str:
    for alias in CODE_ALIASES:
        v = item.get(alias)
        if v not in (None, ""):
            return _clean_code(v)
    return ""


def _pick_card_type(item: dict[str, Any]) -> str:
    for alias in TYPE_ALIASES:
        v = item.get(alias)
        if v not in (None, ""):
            return str(v).strip().lower()
    return ""


def detect_leader(payload: Any, card_list: list[dict[str, Any]], override: str) -> str:
    """Return a normalized leader code, or raise ValueError if it cannot be determined."""
    # 1. Explicit CLI override always wins
    if override.strip():
        return _clean_code(override.strip())

    # 2. Top-level leader key in the payload dict
    if isinstance(payload, dict):
        for key in LEADER_KEYS:
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str) and value.strip():
                return _clean_code(value)
            if isinstance(value, dict):
                code = _pick_card_code(value)
                if code:
                    return code

    # 3. Card in the list flagged as leader by card_type
    leader_candidates = []
    for item in card_list:
        if _pick_card_type(item) in LEADER_TYPE_VALUES:
            code = _pick_card_code(item)
            if code:
                leader_candidates.append(code)

    if len(leader_candidates) == 1:
        return leader_candidates[0]
    if len(leader_candidates) > 1:
        raise ValueError(
            f"Multiple leader-type cards found: {leader_candidates}. "
            "Use --leader-code to specify which is the deck leader."
        )

    raise ValueError(
        "Leader code could not be determined from the input. "
        "Add a 'leader' key to the JSON or pass --leader-code CODE."
    )


# ---------------------------------------------------------------------------
# Entry parsing
# ---------------------------------------------------------------------------

def _pick_quantity(item: dict[str, Any]) -> int:
    for alias in QTY_ALIASES:
        v = item.get(alias)
        if v is not None:
            try:
                qty = int(v)
                if qty > 0:
                    return qty
            except (ValueError, TypeError):
                pass
    return 1  # default to 1 copy if quantity not specified


def parse_entries(
    card_list: list[dict[str, Any]],
    leader_code: str,
    row_number_offset: int = 1,
) -> list[dict[str, int | str]]:
    """
    Convert card_list dicts into {card_code, quantity} entries.

    Leader cards (card_type == leader) are included in the entry list so the
    full deck contents are captured; the deck_uid hash includes the leader
    through the leader_code field, not through the entry list.

    Raises ValueError if any item is missing a card_code.
    """
    entries: list[dict[str, int | str]] = []
    seen: dict[str, int] = {}

    for i, item in enumerate(card_list, start=row_number_offset):
        code = _pick_card_code(item)
        if not code:
            raise ValueError(
                f"Card at position {i} is missing a card code. "
                f"Available keys: {sorted(item.keys())}"
            )
        qty = _pick_quantity(item)
        # Merge duplicate rows (some exporters emit one row per copy)
        if code in seen:
            seen[code] += qty
        else:
            seen[code] = qty

    for code, qty in seen.items():
        entries.append({"card_code": code, "quantity": qty})

    return entries


# ---------------------------------------------------------------------------
# deck_uid
# ---------------------------------------------------------------------------

def compute_deck_uid(leader_code: str, entries: list[dict[str, int | str]]) -> str:
    """
    Stable 12-hex content-addressable identifier for this deck.

    Hash input: JSON-serialized object containing:
      - "leader": normalized leader code
      - "cards":  list of [card_code, quantity] pairs, sorted by card_code

    Two decks with the same leader and identical card/quantity combinations
    always produce the same uid, regardless of file metadata or field ordering.
    """
    sorted_cards = sorted(
        ([e["card_code"], e["quantity"]] for e in entries),
        key=lambda pair: pair[0],
    )
    payload = json.dumps(
        {"leader": leader_code.upper(), "cards": sorted_cards},
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Row building + CSV output
# ---------------------------------------------------------------------------

def build_rows(
    entries: list[dict[str, int | str]],
    *,
    deck_uid: str,
    leader_code: str,
    format_code: str,
    source_reference: str,
    placement: str,
    notes: str,
) -> list[dict[str, str]]:
    return [
        {
            "deck_uid": deck_uid,
            "leader_code": leader_code,
            "card_code": str(e["card_code"]),
            "quantity": str(e["quantity"]),
            "format_code": format_code.strip(),
            "source_reference": source_reference.strip(),
            "placement": placement.strip(),
            "notes": notes.strip(),
        }
        for e in entries
    ]


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def default_output_path(deck_uid: str) -> Path:
    return ROOT / "data" / f"deck_{deck_uid}_staging.csv"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        input_path = resolve_input_path(args)
        payload = load_payload(input_path)
        card_list = extract_card_list(payload)
        leader_code = detect_leader(payload, card_list, args.leader_code)
        entries = parse_entries(card_list, leader_code)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Guard: suspiciously low total card count
    total_qty = sum(int(e["quantity"]) for e in entries)
    min_entries = max(args.min_entries, 1)
    if total_qty < min_entries:
        print(
            f"ERROR: total card quantity ({total_qty}) is below --min-entries={min_entries}. "
            "This looks like a truncated or empty decklist.",
            file=sys.stderr,
        )
        return 1

    # Warn on suspicious quantities (>4 copies of any single non-leader card)
    warnings: list[str] = []
    for e in entries:
        qty = int(e["quantity"])
        if _pick_card_type({}) in LEADER_TYPE_VALUES:
            continue
        if qty > 4:
            warnings.append(
                f"  WARN  {e['card_code']}: quantity {qty} exceeds the legal 4-copy limit"
            )

    for w in warnings:
        print(w, file=sys.stderr)

    deck_uid = compute_deck_uid(leader_code, entries)
    rows = build_rows(
        entries,
        deck_uid=deck_uid,
        leader_code=leader_code,
        format_code=args.format_code,
        source_reference=args.source_reference,
        placement=args.placement,
        notes=args.notes,
    )

    output_path = Path(args.output) if args.output else default_output_path(deck_uid)

    try:
        write_csv(rows, output_path)
    except OSError as exc:
        print(f"ERROR: could not write CSV to {output_path}: {exc}", file=sys.stderr)
        return 1

    unique_cards = len(entries)
    print(
        f"Wrote {unique_cards} card line(s) ({total_qty} total copies) "
        f"for leader {leader_code} to {output_path}"
    )
    print(f"deck_uid: {deck_uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
