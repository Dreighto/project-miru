#!/usr/bin/env python
"""Validate that future-dated official legality changes are stored as upcoming, not as current conflicts.

Uses worktree-local staged fixtures only. No network.

Run from repo root:
  python -m tools.validate_official_timing

Expects:
  - data/staging/op_official_timing_test_seed_current.csv (OP01-001 = legal, past date)
  - data/staging/op_official_timing_test_future.csv (OP01-001 = banned, 2026-12-31)

Verifies:
  - Future-dated row is routed to UPCOMING_STORED
  - Written to data/miru_official_rules.db with is_upcoming=1, is_current=0
  - NOT in review_queue as conflict
  - Catalog miru_card_legality still has OP01-001 = legal (unchanged)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STAGING_DIR = DATA_DIR / "staging"
CATALOG_PATH = DATA_DIR / "card_catalog.db"
RULES_DB_PATH = DATA_DIR / "miru_official_rules.db"

SEED_CSV = STAGING_DIR / "op_official_timing_test_seed_current.csv"
FUTURE_CSV = STAGING_DIR / "op_official_timing_test_future.csv"
TEST_CARD = "OP01-001"
TEST_FORMAT = "standard"


def main() -> int:
    from tools.run_governed_batch_test import run_batch
    from tools.miru_regulation import get_legality_state
    from tools.miru_project_sync import ensure_catalog_sync_schema

    errors: list[str] = []

    # Ensure catalog schema exists
    ensure_catalog_sync_schema(CATALOG_PATH)

    # Step 1: Seed current state (OP01-001 = legal, past date) so we have a "current" to conflict with
    if not SEED_CSV.is_file():
        errors.append(f"Missing seed CSV: {SEED_CSV}")
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    report_seed = run_batch(
        csv_path=SEED_CSV,
        source_id="official",
        catalog_path=CATALOG_PATH,
        dry_run=False,
        data_dir=DATA_DIR,
    )
    # Expect auto_apply (new row, low impact or no conflict)
    current_after_seed = get_legality_state(CATALOG_PATH, TEST_CARD, TEST_FORMAT)
    if not current_after_seed:
        errors.append("After seed run: no catalog row for OP01-001 (seed may have been skipped)")
    elif (current_after_seed.get("legality_state") or "").strip().lower() != "legal":
        errors.append(f"After seed: OP01-001 should be legal in catalog, got {current_after_seed.get('legality_state')}")

    # Step 2: Run with future-dated fixture (OP01-001 = banned, 2026-12-31)
    if not FUTURE_CSV.is_file():
        errors.append(f"Missing future CSV: {FUTURE_CSV}")
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    report_future = run_batch(
        csv_path=FUTURE_CSV,
        source_id="official",
        catalog_path=CATALOG_PATH,
        dry_run=False,
        data_dir=DATA_DIR,
    )

    # Phase B checks
    if report_future.get("total_upcoming_stored", 0) < 1:
        errors.append(f"Expected at least 1 UPCOMING_STORED, got total_upcoming_stored={report_future.get('total_upcoming_stored')}")
    upcoming_list = report_future.get("upcoming_stored") or []
    test_in_upcoming = any((r.get("item_identifier") or "").strip().upper() == TEST_CARD for r in upcoming_list)
    if not test_in_upcoming:
        errors.append(f"Expected {TEST_CARD} in report upcoming_stored list, got {[r.get('item_identifier') for r in upcoming_list]}")

    review_queue = report_future.get("review_queue") or []
    conflict_for_test = [r for r in review_queue if (r.get("item_identifier") or "").strip().upper() == TEST_CARD]
    if conflict_for_test:
        errors.append(f"Future-dated change must NOT appear as review conflict; got review_queue item for {TEST_CARD}: {conflict_for_test[0].get('why_escalated')}")

    # Phase C: DB state
    if not RULES_DB_PATH.is_file():
        errors.append(f"Rules DB not created: {RULES_DB_PATH}")
    else:
        with sqlite3.connect(RULES_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT card_code, format_name, legality_state, effective_start, is_current, is_upcoming FROM official_legality_history WHERE card_code = ? AND format_name = ?",
                (TEST_CARD, TEST_FORMAT),
            ).fetchall()
        # We may have multiple rows (seed could have written one, future one). We need at least one with is_upcoming=1
        upcoming_rows = [dict(r) for r in rows if r["is_upcoming"]]
        if not upcoming_rows:
            errors.append(f"official_legality_history: expected at least one row for {TEST_CARD} with is_upcoming=1; rows={[dict(r) for r in rows]}")
        else:
            for row in upcoming_rows:
                if row.get("is_current") != 0:
                    errors.append(f"Upcoming row should have is_current=0, got {row}")

    # Catalog unchanged: should still be legal (current applied state)
    current_after_future = get_legality_state(CATALOG_PATH, TEST_CARD, TEST_FORMAT)
    if not current_after_future:
        errors.append("Catalog should still have a row for OP01-001 after future run")
    elif (current_after_future.get("legality_state") or "").strip().lower() != "legal":
        errors.append(f"Catalog must not be overwritten by future-dated change: OP01-001 should still be legal, got {current_after_future.get('legality_state')}")

    if errors:
        print("Validation FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("Validation PASSED")
    print(f"  Staged input (future): {FUTURE_CSV.name} (OP01-001 banned effective 2026-12-31)")
    print(f"  Outcome: UPCOMING_STORED (total_upcoming_stored={report_future.get('total_upcoming_stored')})")
    print(f"  official_legality_history: row(s) for {TEST_CARD} with is_upcoming=1, is_current=0")
    print(f"  Catalog: OP01-001 remains legal (unchanged)")
    print(f"  Review queue: no conflict item for {TEST_CARD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
