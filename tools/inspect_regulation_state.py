#!/usr/bin/env python
"""Lightweight audit: regulation/legality coverage and ethics gate blocks.

Run from worktree root. Reports:
- Which cards have official legality state
- Which remain unknown (no record)
- Last ethics gate block if any
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = PROJECT_ROOT / "data" / "card_catalog.db"


def main() -> int:
    from tools.miru_regulation import (
        list_legality_records,
        OFFICIAL_LEGALITY_SOURCE_IDS,
        LEGALITY_LEGAL,
        LEGALITY_BANNED,
        LEGALITY_RESTRICTED,
        LEGALITY_ROTATED,
        LEGALITY_UNKNOWN,
    )
    from tools.miru_ethics_gates import get_last_gate_block, load_last_gate_block_from_disk

    print("Regulation / legality audit (worktree)")
    print("  catalog:", CATALOG_PATH)
    print()

    if not CATALOG_PATH.is_file():
        print("  catalog not found; no legality records.")
        print("  Cards with official legality state: 0")
        print("  Cards unknown (no record): (catalog missing)")
        print()
        block = load_last_gate_block_from_disk()
        if block:
            print("  Last ethics gate block:", block.get("gate_id"), "-", block.get("reason"))
            if block.get("context"):
                print("  context:", block.get("context"))
        else:
            print("  Last ethics gate block: none")
        return 0

    all_records = list_legality_records(CATALOG_PATH)
    official_records = [r for r in all_records if (r.get("source_id") or "").strip() in OFFICIAL_LEGALITY_SOURCE_IDS]
    by_state: dict[str, list[str]] = {}
    for rec in official_records:
        state = (rec.get("legality_state") or LEGALITY_UNKNOWN).strip().lower()
        code = (rec.get("card_code") or "").strip().upper()
        if code:
            by_state.setdefault(state, []).append(code)

    print("  Cards with official legality state:", len(official_records))
    for state in (LEGALITY_LEGAL, LEGALITY_BANNED, LEGALITY_RESTRICTED, LEGALITY_ROTATED, LEGALITY_UNKNOWN):
        codes = sorted(by_state.get(state, []))
        if codes:
            print(f"    {state}: {len(codes)} — sample: {codes[:5]}{' ...' if len(codes) > 5 else ''}")
    print("  Cards unknown (no record): (all other catalog cards; count not computed here)")
    print()

    block = get_last_gate_block() or load_last_gate_block_from_disk()
    if block:
        print("  Last ethics gate block:")
        print("    gate_id:", block.get("gate_id"))
        print("    reason:", block.get("reason"))
        if block.get("context"):
            print("    context:", block.get("context"))
    else:
        print("  Last ethics gate block: none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
