#!/usr/bin/env python
"""Import a regulation/banlist staging CSV into miru_card_legality (worktree catalog only).

Reads CSV from the existing staging path (e.g. output of miru_fetch_banlist).
Requires --source-id to be one of OFFICIAL_LEGALITY_SOURCE_IDS. No network; worktree DB only.

Usage:
    python -m tools.miru_import_legality_csv data/op_format_banlist_intake.csv --source-id official
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = PROJECT_ROOT / "data" / "card_catalog.db"

from tools.miru_regulation import (
    OFFICIAL_LEGALITY_SOURCE_IDS,
    LEGALITY_LEGAL,
    LEGALITY_BANNED,
    LEGALITY_RESTRICTED,
    LEGALITY_ROTATED,
    LEGALITY_UNKNOWN,
    LEGALITY_STATES,
    save_legality_state,
)


def _normalize_legality_state(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in LEGALITY_STATES:
        return s
    if s in ("ok", "playable", "allowed"):
        return LEGALITY_LEGAL
    if s in ("ban", "banned"):
        return LEGALITY_BANNED
    if s in ("restrict", "limited"):
        return LEGALITY_RESTRICTED
    if s in ("rotate", "rotated", "out"):
        return LEGALITY_ROTATED
    return LEGALITY_UNKNOWN


def read_staging_csv(path: Path) -> list[dict[str, str]]:
    """Read staging CSV; return list of row dicts with normalized keys."""
    rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return rows


def main() -> int:
    import argparse
    from tools.miru_project_sync import ensure_catalog_sync_schema

    parser = argparse.ArgumentParser(
        description="Import regulation/banlist staging CSV into miru_card_legality (worktree catalog)."
    )
    parser.add_argument("csv_path", type=Path, help="Path to staging CSV (e.g. from miru_fetch_banlist).")
    parser.add_argument(
        "--source-id",
        required=True,
        metavar="ID",
        help="Source identifier; must be one of: " + ", ".join(sorted(OFFICIAL_LEGALITY_SOURCE_IDS)),
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help=f"Catalog DB path (default: {DEFAULT_CATALOG}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB.")
    args = parser.parse_args()

    sid = (args.source_id or "").strip()
    if sid not in OFFICIAL_LEGALITY_SOURCE_IDS:
        print(f"ERROR: --source-id must be one of {sorted(OFFICIAL_LEGALITY_SOURCE_IDS)}.", file=sys.stderr)
        return 1

    csv_path = Path(args.csv_path)
    if not csv_path.is_file():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1

    rows = read_staging_csv(csv_path)
    if not rows:
        print("ERROR: CSV contains no data rows.", file=sys.stderr)
        return 1

    ensure_catalog_sync_schema(args.catalog)
    written = 0
    skipped = 0
    skip_reasons: dict[str, int] = {}

    for i, row in enumerate(rows):
        card_code = (row.get("card_code") or "").strip().upper()
        if not card_code:
            skipped += 1
            skip_reasons["empty card_code"] = skip_reasons.get("empty card_code", 0) + 1
            continue
        ban_status = row.get("ban_status") or row.get("legality_state") or ""
        state = _normalize_legality_state(ban_status)
        format_name = (row.get("format_code") or row.get("format") or "standard").strip().lower() or "standard"
        effective_date = (row.get("effective_date") or "").strip()
        notes = (row.get("notes") or "").strip()
        source_reference = (row.get("source_reference") or row.get("source_url") or "").strip()

        if args.dry_run:
            written += 1
            continue
        ok = save_legality_state(
            args.catalog,
            card_code,
            format_name,
            state,
            effective_date=effective_date,
            source_id=sid,
            source_reference=source_reference,
            notes=notes,
        )
        if ok:
            written += 1
        else:
            skipped += 1
            skip_reasons["save_legality_state refused"] = skip_reasons.get("save_legality_state refused", 0) + 1

    print(f"Rows read: {len(rows)}")
    print(f"Rows written: {written}")
    print(f"Rows skipped: {skipped}")
    if skip_reasons:
        print("Skip reasons:", dict(skip_reasons))
    if args.dry_run:
        print("(dry-run; no DB writes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
