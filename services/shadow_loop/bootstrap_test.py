"""Bootstrap test runner for the shadow-loop verifier (PR-C, PRO-908).

Runs BEFORE the loop is allowed to start. Feeds hand-curated mock primary
answers through `RealVerifier.score()` and asserts the verifier produces
the expected per-field outcome on each case. If the rubric is broken
(catalog mis-parse, normalization regression, Bandai-source drift,
schema mismatch), the loop refuses to start.

Why this matters: if the verifier is broken, models get punished for
correct answers and the feedback loop poisons the pool toward the
rubric's bugs (Goodhart's law). The bootstrap test is the first guard
against that — verify the rubric on known-correct cards before the loop
goes near unverified data.

Fixture format (`data/shadow_loop/bootstrap_fixtures.json`):

    {
        "schema_version": 1,
        "description": "...",
        "cases": [
            {
                "name": "OP01-001 — primary answers everything correctly",
                "canonical_code": "OP01-001",
                "print_id": "OP01-001",
                "primary_answer": {"card_name": "Monkey D. Luffy", "cost": null, ...},
                "expected": {
                    "card_name": "verified-correct",
                    "cost": "verified-correct",
                    ...
                }
            },
            ...
        ]
    }

Usage:
    python services/shadow_loop/bootstrap_test.py
    # exit 0 if all pass, non-zero on any failure.

Call from launch.py before the loop runs; refuse to start on non-zero.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .bandai_source import BandaiSource
from .config import load as load_config
from .real_verifier import RealVerifier

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_FIXTURES = REPO_ROOT / "data" / "shadow_loop" / "bootstrap_fixtures.json"


def _fetch_card_row(catalog_db: Path, canonical_code: str, print_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT c.*, cv.print_id, cv.image_path, cv.image_url
            FROM card_variants cv JOIN cards c ON cv.card_id = c.id
            WHERE c.canonical_code = ? AND cv.print_id = ?
            LIMIT 1
            """,
            (canonical_code, print_id),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _evaluate_case(
    case: dict[str, Any],
    catalog_db: Path,
    bandai: BandaiSource,
) -> tuple[bool, list[str]]:
    """Run one bootstrap case. Returns (passed, list_of_mismatch_descriptions)."""
    name = case.get("name", "<unnamed>")
    canonical_code = case.get("canonical_code", "")
    print_id = case.get("print_id", "")
    expected: dict[str, str] = case.get("expected", {})

    card = _fetch_card_row(catalog_db, canonical_code, print_id)
    if card is None:
        return (
            False,
            [f"case {name!r}: card not found in catalog ({canonical_code} / {print_id})"],
        )

    verifier = RealVerifier(bandai=bandai)
    result = verifier.score(card, case.get("primary_answer", {}))
    field_outcomes = result.get("field_outcomes", {})

    mismatches: list[str] = []
    for field, expected_outcome in expected.items():
        actual = field_outcomes.get(field, {}).get("outcome", "<missing>")
        if actual != expected_outcome:
            reason = field_outcomes.get(field, {}).get("reason", "")
            mismatches.append(
                f"case {name!r} field {field!r}: expected {expected_outcome!r}, "
                f"got {actual!r} ({reason})"
            )
    return (not mismatches, mismatches)


def run_bootstrap(
    fixtures_path: Path | None = None,
    catalog_db: Path | None = None,
    bandai_crawl_path: Path | None = None,
) -> int:
    """Run all bootstrap cases. Returns exit code (0 on success, non-zero on failure)."""
    if fixtures_path is None:
        fixtures_path = DEFAULT_FIXTURES
    if not fixtures_path.exists():
        print(f"FAIL: bootstrap fixtures missing at {fixtures_path}", file=sys.stderr)
        return 2

    cfg = load_config()
    catalog_db = catalog_db or cfg.catalog_db
    if not catalog_db.exists():
        print(f"FAIL: catalog DB missing at {catalog_db}", file=sys.stderr)
        return 3

    bandai_crawl_path = bandai_crawl_path or (
        cfg.learning_pool_db.parent / "bandai_op01_crawl.json"
    )
    bandai = BandaiSource(bandai_crawl_path)

    payload = json.loads(fixtures_path.read_text(encoding="utf-8"))
    cases: list[dict] = payload.get("cases", [])
    if not cases:
        print(f"FAIL: bootstrap fixtures contain no cases at {fixtures_path}", file=sys.stderr)
        return 4

    pass_count = 0
    fail_count = 0
    all_mismatches: list[str] = []

    for case in cases:
        name = case.get("name", "<unnamed>")
        passed, mismatches = _evaluate_case(case, catalog_db, bandai)
        if passed:
            print(f"PASS  {name}")
            pass_count += 1
        else:
            print(f"FAIL  {name}")
            for m in mismatches:
                print(f"      {m}")
            fail_count += 1
            all_mismatches.extend(mismatches)

    print(f"\nBootstrap result: {pass_count} passed, {fail_count} failed of {len(cases)} cases")
    if fail_count:
        print(
            "Bootstrap test failed. The verifier is producing unexpected outcomes "
            "on hand-curated cases. Refusing to start the shadow loop until the "
            "rubric is fixed."
        )
        return 1
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return run_bootstrap()


if __name__ == "__main__":
    sys.exit(main())
