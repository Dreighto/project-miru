#!/usr/bin/env python
"""Staged import of official rule notices from a JSON file into miru_official_rules.db.

No network; worktree-only. Use for Bandai-approved official notices (rules, banlist, block, rulings).
JSON shape: list of notice objects, or single object with notice_id, title, source_id, effective_at, etc.

Usage:
    python -m tools.ingest_official_rules_json data/staging/official_notices.json
    python -m tools.ingest_official_rules_json data/staging/official_notices.json --rules-db data/miru_official_rules.db
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from tools.miru_official_rules import (
    DEFAULT_RULES_DB_PATH,
    ingest_notice_json,
)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Import official rule notices from JSON into miru_official_rules.db.")
    parser.add_argument("json_path", type=Path, help="Path to JSON file (array of notices or single notice).")
    parser.add_argument(
        "--rules-db",
        type=Path,
        default=DEFAULT_RULES_DB_PATH,
        help=f"Rules DB path (default: {DEFAULT_RULES_DB_PATH}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB.")
    args = parser.parse_args()

    json_path = Path(args.json_path)
    if not json_path.is_file():
        print(f"ERROR: JSON file not found: {json_path}", file=sys.stderr)
        return 1

    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}", file=sys.stderr)
        return 1

    notices = raw if isinstance(raw, list) else [raw]
    if not notices:
        print("ERROR: No notices in JSON.", file=sys.stderr)
        return 1

    written = 0
    for i, payload in enumerate(notices):
        if not isinstance(payload, dict):
            print(f"Skip row {i}: not a dict", file=sys.stderr)
            continue
        if args.dry_run:
            written += 1
            print(f"  [dry-run] would ingest: {payload.get('notice_id') or payload.get('title', '')[:50]}")
            continue
        ok = ingest_notice_json(args.rules_db, payload)
        if ok:
            written += 1

    print(f"Notices processed: {len(notices)}")
    print(f"Written: {written}")
    if args.dry_run:
        print("(dry-run; no DB writes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
