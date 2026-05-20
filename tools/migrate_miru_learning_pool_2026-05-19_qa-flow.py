"""Idempotent schema migration for miru_learning_pool.db — QA flow additions.

PRO-926 (2026-05-19) — adds three things needed by the QA verification flow
(Ticket 3) and Voyage rebuild (Ticket 6):

  a. learned_cards.source_trace_json  (TEXT, nullable)
     Per-field Bandai-source pointers for Stage 3 auto-clear / model-collusion
     fix.

  b. New table: door_b_overrides  (append-only)
     Operator-approved rule waivers (Door B verdicts) with snapshot-hash
     invalidation.

  c. learned_cards.derived_from_json  (TEXT, default '[]')
     Parent print_id list for derived-card attenuation.

  d. New table: score_transitions  (append-only)
     Score-change event log (assigned / attenuated / corrected / bounced).

Idempotency: detects whether the migration has already run by checking for
the presence of `source_trace_json` on `learned_cards`. Exits 0 with
"no-op" output if already applied.

Drift refusal: if `learned_cards` does not match the expected 72-column
baseline, exits non-zero with a clear error before touching anything.

Transaction: all DDL is wrapped in a single BEGIN/COMMIT — all or nothing.

Do NOT run against data/miru_learning_pool.db directly during this ticket.
Smoke against /tmp/pool_smoke.db (a copy). Live execution is CC's post-merge
step.

Usage:
    python tools/migrate_miru_learning_pool_2026-05-19_qa-flow.py [--db PATH] [--dry-run]
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

# Exact set of column names present in learned_cards before this migration.
# Built from PRAGMA table_info output on the live pool (PRO-925 confirmed: 72
# columns, schema clean). If the live schema gains or loses columns without a
# corresponding migration ticket, the drift check will catch it.
BASELINE_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "created_at",
        "canonical_code",
        "set_code",
        "card_number",
        "set_name",
        "card_name",
        "rarity",
        "color",
        "card_type",
        "cost",
        "power",
        "counter",
        "attribute",
        "traits",
        "life",
        "block_icon",
        "effect_text",
        "trigger_text",
        "aliases_json",
        "sources_json",
        "base_card_id",
        "is_variant",
        "variant_category",
        "variant_subtype",
        "stamp_type",
        "stamp_event_name",
        "stamp_placement",
        "distribution_source",
        "distribution_event",
        "is_serialized",
        "serial_number",
        "print_run",
        "is_premium_variant",
        "variant_meta_json",
        "don_activated_cost",
        "card_id",
        "variant_key",
        "variant_label",
        "print_id",
        "release_set_code",
        "release_set_name",
        "image_path",
        "image_url",
        "source",
        "is_base",
        "is_alt",
        "is_sp",
        "has_variant_evidence",
        "is_tr",
        "is_manga_rare",
        "is_golden_manga_rare",
        "is_promo",
        "variant_is_serialized",
        "is_illustration_rare",
        "official_provenance",
        "distribution_product_key",
        "updated_at",
        "tcgplayer_product_id",
        "tcgplayer_market_price",
        "tcgplayer_mid_price",
        "tcgplayer_low_price",
        "tcgplayer_price_updated_at",
        "variant_block_icon",
        "art_variant_index",
        "illustrator",
        "confidence_score",
        "learned_from",
        "last_verified",
        "promotion_status",
        "validator_agreement",
        "contributing_model",
    }
)

EXPECTED_BASELINE_COUNT: int = 72

# Columns added by this migration — excluded from the "unexpected columns"
# drift check so that a re-run on an already-migrated DB doesn't falsely
# report drift (though in practice the idempotency check exits first).
NEW_COLUMNS: frozenset[str] = frozenset({"source_trace_json", "derived_from_json"})

# Full DDL applied in one transaction.  executescript() bypasses
# isolation_level, so BEGIN/COMMIT in the SQL string gives us true atomicity.
_MIGRATION_SQL = """\
BEGIN;

ALTER TABLE learned_cards ADD COLUMN source_trace_json TEXT DEFAULT NULL;
ALTER TABLE learned_cards ADD COLUMN derived_from_json TEXT DEFAULT '[]';

CREATE TABLE door_b_overrides (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  print_id      TEXT NOT NULL,
  rule_id       TEXT NOT NULL,
  operator      TEXT NOT NULL,
  approved_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  reason        TEXT,
  snapshot_hash TEXT NOT NULL
);
CREATE INDEX idx_door_b_overrides_print_rule ON door_b_overrides(print_id, rule_id);
CREATE INDEX idx_door_b_overrides_approved_at ON door_b_overrides(approved_at);

CREATE TABLE score_transitions (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  print_id       TEXT NOT NULL,
  prior_score    INTEGER,
  new_score      INTEGER NOT NULL,
  cause          TEXT NOT NULL CHECK (cause IN ('assigned','attenuated','corrected','bounced')),
  actor          TEXT NOT NULL,
  reason         TEXT,
  transition_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX idx_score_transitions_print_id ON score_transitions(print_id, transition_at);

COMMIT;
"""


def get_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def check_already_applied(conn: sqlite3.Connection) -> bool:
    """Return True if the migration has already been applied."""
    return "source_trace_json" in get_columns(conn, "learned_cards")


def check_drift(conn: sqlite3.Connection) -> list[str]:
    """Return a list of error strings describing schema drift; empty = no drift."""
    errors: list[str] = []
    actual = set(get_columns(conn, "learned_cards"))
    unexpected = actual - BASELINE_COLUMNS - NEW_COLUMNS
    missing = BASELINE_COLUMNS - actual
    if len(actual) != EXPECTED_BASELINE_COUNT:
        errors.append(f"Expected {EXPECTED_BASELINE_COUNT} columns, found {len(actual)}.")
    if unexpected:
        errors.append(f"Unexpected columns: {sorted(unexpected)}")
    if missing:
        errors.append(f"Missing baseline columns: {sorted(missing)}")
    return errors


def apply_migration(conn: sqlite3.Connection) -> None:
    """Apply all DDL in one atomic transaction."""
    conn.executescript(_MIGRATION_SQL)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Idempotent schema migration for miru_learning_pool.db (PRO-926)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_POOL_DB,
        help="Path to the pool DB (default: data/miru_learning_pool.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done; make no changes.",
    )
    args = parser.parse_args()

    db_path: Path = args.db if args.db.is_absolute() else Path.cwd() / args.db

    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))

    if check_already_applied(conn):
        print(f"no-op: migration already applied to {db_path}")
        conn.close()
        return 0

    drift_errors = check_drift(conn)
    if drift_errors:
        for err in drift_errors:
            print(f"DRIFT ERROR: {err}", file=sys.stderr)
        print(
            "Refusing to apply migration against an unexpected schema. "
            "Investigate the drift or open a new migration ticket.",
            file=sys.stderr,
        )
        conn.close()
        return 1

    if args.dry_run:
        print("dry-run: would apply the following changes to", db_path)
        print("  ALTER TABLE learned_cards ADD COLUMN source_trace_json TEXT DEFAULT NULL")
        print("  ALTER TABLE learned_cards ADD COLUMN derived_from_json TEXT DEFAULT '[]'")
        print("  CREATE TABLE door_b_overrides (7 columns + 2 indexes)")
        print("  CREATE TABLE score_transitions (8 columns + 1 index)")
        conn.close()
        return 0

    conn.close()

    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H%M%SZ")
    backup_path = Path(str(db_path) + f".bak.{ts}")
    shutil.copy2(str(db_path), str(backup_path))
    print(f"Backup written to {backup_path}")

    conn = sqlite3.connect(str(db_path))
    apply_migration(conn)
    conn.close()

    print(f"Migration applied successfully to {db_path}")
    print(
        "  learned_cards: +source_trace_json (TEXT nullable), +derived_from_json (TEXT default '[]')"
    )
    print("  Created: door_b_overrides (append-only event log, 7 cols, 2 indexes)")
    print("  Created: score_transitions (append-only event log, 8 cols, 1 index)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
