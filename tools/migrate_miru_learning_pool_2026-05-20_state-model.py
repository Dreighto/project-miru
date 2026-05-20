"""Migrate miru_learning_pool.db learned_cards to the three-axis state model (PRO-928).

BORROW architecture decision (operator + CH, 2026-05-20): adopt card_catalog.db's
proven readiness/approval/promotion three-axis state model — the shape validated
by 172 rows of real use in `miru_review_queue` — instead of the single
`promotion_status` column and PRO-926's parallel `door_b_overrides` /
`score_transitions` tables.

What this migration does, in one transaction:
  * ADD three CHECK-constrained columns to `learned_cards`:
      readiness_state, approval_state, promotion_state
    Each column's DEFAULT is a valid vocabulary value, so the ADD COLUMN
    satisfies the CHECK against all existing rows and migrates them in place
    to the "pending" combination (ready_for_review / pending_review / '').
  * DROP the `promotion_status` column + its index idx_learned_cards_promotion_status.
  * DROP the `door_b_overrides` and `score_transitions` tables (PRO-926;
    0 rows, 0 readers — superseded by the BORROW decision).
  * ADD index idx_learned_cards_readiness_state (shadow_review.py queries by it).

What it KEEPS: `source_trace_json` (live — PRO-927 Stage 3) and
`derived_from_json` (column retained for the Ticket 3c re-scope).

Idempotent: re-running on an already-migrated DB is a no-op (exit 0).
Drift-refusing: refuses if `learned_cards` is not the expected pre-migration
shape (must have `promotion_status` + the two PRO-926 tables).

Usage:
    python tools/migrate_miru_learning_pool_2026-05-20_state-model.py [--db PATH] [--dry-run]
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POOL_DB = ROOT / "data" / "miru_learning_pool.db"

# The three new columns. DEFAULT is a valid vocab value so ADD COLUMN both
# satisfies the CHECK against existing rows AND performs the in-place data
# migration to the "pending" combination.
NEW_COLUMNS: list[tuple[str, str]] = [
    (
        "readiness_state",
        "TEXT NOT NULL DEFAULT 'ready_for_review' "
        "CHECK (readiness_state IN "
        "('not_ready','ready_for_review','blocked_by_guardrail','ready_for_publish_candidate'))",
    ),
    (
        "approval_state",
        "TEXT NOT NULL DEFAULT 'pending_review' "
        "CHECK (approval_state IN "
        "('pending_review','approved_for_candidate','rejected','deferred'))",
    ),
    (
        "promotion_state",
        # '' is a real state — the pre-promotion state — NOT absence.
        "TEXT NOT NULL DEFAULT '' "
        "CHECK (promotion_state IN "
        "('', 'review_approved_candidate', 'blocked_from_promotion', 'deferred'))",
    ),
]

DROPPED_TABLES = ("door_b_overrides", "score_transitions")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _index_exists(conn: sqlite3.Connection, index: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (index,)
        ).fetchone()
        is not None
    )


def is_already_migrated(conn: sqlite3.Connection) -> bool:
    """True iff the migration has already been applied."""
    return "readiness_state" in _columns(conn, "learned_cards")


def check_drift(conn: sqlite3.Connection) -> list[str]:
    """Return a list of drift problems. Empty list means the DB is the
    expected pre-migration shape and safe to migrate."""
    problems: list[str] = []
    if not _table_exists(conn, "learned_cards"):
        problems.append("learned_cards table is missing")
        return problems
    cols = _columns(conn, "learned_cards")
    if "promotion_status" not in cols:
        problems.append(
            "learned_cards has no `promotion_status` column — not the expected "
            "pre-migration shape"
        )
    for t in DROPPED_TABLES:
        if not _table_exists(conn, t):
            problems.append(f"expected PRO-926 table `{t}` is missing")
    return problems


def apply_migration(conn: sqlite3.Connection) -> None:
    """Apply the migration inside a single transaction."""
    with conn:  # transaction — commits on success, rolls back on exception
        # 1. Add the three new state columns (DEFAULTs migrate existing rows).
        for name, ddl_tail in NEW_COLUMNS:
            conn.execute(f"ALTER TABLE learned_cards ADD COLUMN {name} {ddl_tail}")
        # 2. Drop the old single-axis column + its index.
        if _index_exists(conn, "idx_learned_cards_promotion_status"):
            conn.execute("DROP INDEX idx_learned_cards_promotion_status")
        conn.execute("ALTER TABLE learned_cards DROP COLUMN promotion_status")
        # 3. Index the new review-state column.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learned_cards_readiness_state "
            "ON learned_cards(readiness_state)"
        )
        # 4. Drop the superseded PRO-926 tables.
        for t in DROPPED_TABLES:
            conn.execute(f"DROP TABLE IF EXISTS {t}")


def verify(conn: sqlite3.Connection, expected_rows: int) -> list[str]:
    """Post-migration verification. Returns list of failures (empty = OK)."""
    failures: list[str] = []
    cols = _columns(conn, "learned_cards")
    for name, _ in NEW_COLUMNS:
        if name not in cols:
            failures.append(f"expected column `{name}` not present after migration")
    if "promotion_status" in cols:
        failures.append("`promotion_status` still present after migration")
    for t in DROPPED_TABLES:
        if _table_exists(conn, t):
            failures.append(f"table `{t}` still present after migration")
    if not _index_exists(conn, "idx_learned_cards_readiness_state"):
        failures.append("idx_learned_cards_readiness_state not created")
    actual_rows = conn.execute("SELECT COUNT(*) FROM learned_cards").fetchone()[0]
    if actual_rows != expected_rows:
        failures.append(f"row count changed: {expected_rows} -> {actual_rows}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=str(DEFAULT_POOL_DB),
        help="learning pool db path (default: data/miru_learning_pool.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would happen; take no action",
    )
    args = parser.parse_args()
    pool_path = Path(args.db)

    if not pool_path.exists():
        print(f"FAIL: pool DB missing at {pool_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(pool_path)
    try:
        if is_already_migrated(conn):
            print(f"no-op: migration already applied to {pool_path}")
            return 0

        drift = check_drift(conn)
        if drift:
            print("FAIL: schema drift — refusing to migrate:", file=sys.stderr)
            for p in drift:
                print(f"  - {p}", file=sys.stderr)
            print("Investigate the pool DB before migrating.", file=sys.stderr)
            return 3

        row_count = conn.execute("SELECT COUNT(*) FROM learned_cards").fetchone()[0]

        if args.dry_run:
            print(f"DRY RUN -- would migrate {pool_path} ({row_count} rows):")
            print("  + ADD readiness_state / approval_state / promotion_state (CHECK-constrained)")
            print("  + all rows -> ready_for_review / pending_review / '' (via column DEFAULTs)")
            print("  - DROP promotion_status + idx_learned_cards_promotion_status")
            print(f"  - DROP tables {', '.join(DROPPED_TABLES)}")
            print("  + CREATE idx_learned_cards_readiness_state")
            print("No changes made.")
            return 0

        # Backup before any write (db-schema-discipline).
        ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
        backup = pool_path.with_name(f"{pool_path.name}.bak.{ts}")
        shutil.copy2(pool_path, backup)
        print(f"Backup written to {backup}")

        apply_migration(conn)

        failures = verify(conn, row_count)
        if failures:
            print("FAIL: post-migration verification failed:", file=sys.stderr)
            for f in failures:
                print(f"  - {f}", file=sys.stderr)
            print(f"The pre-migration backup is at {backup}", file=sys.stderr)
            return 4

        print(f"Migration applied successfully to {pool_path}")
        print(f"  learned_cards: {row_count} rows preserved")
        print("  + readiness_state, approval_state, promotion_state (CHECK-constrained)")
        print("  - promotion_status (dropped)")
        print(f"  - tables dropped: {', '.join(DROPPED_TABLES)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
