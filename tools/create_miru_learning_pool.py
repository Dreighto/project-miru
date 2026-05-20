"""Create data/miru_learning_pool.db with a `learned_cards` table.

PRO-907 — shadow-loop learning database.

This is the working memory for the OP01 shadow-evaluation loop (PRO-908).
It mirrors card_catalog.db's card/variant structure so a promoted row slots
back into card_catalog with no transformation, PLUS ten learning-only
metadata columns (see METADATA_COLUMNS).

This script builds the CURRENT schema directly — fresh DBs land already at
the latest shape, no migration chain needed. The historical migrations
(PRO-926 qa-flow, PRO-928 state-model) only exist to upgrade DBs created by
an older version of this script.

Design notes:
  * The mirror is built live from card_catalog.db via PRAGMA table_info — it
    cannot drift from the source schema. If you re-run this script after the
    catalog schema changes, it will refuse to clobber an existing table with
    a different shape (run the migration ticket instead).
  * The own PK (`id`) lives on the learning pool. Source PKs from card_catalog
    are intentionally omitted — a row's identity in the pool is
    (canonical_code, print_id).
  * Column-name collisions between `cards` and `card_variants` are resolved by
    prefixing the card_variants copy with `variant_`. Today's collisions:
    `is_serialized`, `block_icon`.
  * card_catalog.db is opened READ-ONLY here. This script writes only to
    data/miru_learning_pool.db (a brand-new file). card_catalog.db is never
    modified — the ticket forbids it and the schema-discipline skill
    forbids schema writes on the catalog without operator approval.

Usage:
    python tools/create_miru_learning_pool.py [--db PATH]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG_DB = ROOT / "data" / "card_catalog.db"
DEFAULT_POOL_DB = ROOT / "data" / "miru_learning_pool.db"

# Ordered to match the post-migration column layout exactly: an older DB
# upgraded through PRO-926 (qa-flow) + PRO-928 (state-model) ends up with this
# same column order, so a fresh build and a migrated build are byte-identical
# in shape. Do not reorder without re-checking both migration scripts.
METADATA_COLUMNS: list[tuple[str, str, str]] = [
    # (column, type, ddl-tail)
    ("confidence_score", "REAL", ""),
    ("learned_from", "TEXT", ""),
    ("last_verified", "TEXT", ""),
    ("validator_agreement", "TEXT", ""),
    ("contributing_model", "TEXT", ""),
    # PRO-926 (qa-flow) additions.
    ("source_trace_json", "TEXT", "DEFAULT NULL"),
    ("derived_from_json", "TEXT", "DEFAULT '[]'"),
    # PRO-928 — three-axis state model (BORROW decision). Replaces the old
    # single `promotion_status` column. Each DEFAULT is a valid vocabulary
    # value; '' on promotion_state is the real pre-promotion state, not absence.
    (
        "readiness_state",
        "TEXT",
        "NOT NULL DEFAULT 'ready_for_review' "
        "CHECK (readiness_state IN "
        "('not_ready','ready_for_review','blocked_by_guardrail','ready_for_publish_candidate'))",
    ),
    (
        "approval_state",
        "TEXT",
        "NOT NULL DEFAULT 'pending_review' "
        "CHECK (approval_state IN "
        "('pending_review','approved_for_candidate','rejected','deferred'))",
    ),
    (
        "promotion_state",
        "TEXT",
        "NOT NULL DEFAULT '' "
        "CHECK (promotion_state IN "
        "('', 'review_approved_candidate', 'blocked_from_promotion', 'deferred'))",
    ),
]


def introspect(conn: sqlite3.Connection, table: str) -> list[dict]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [
        {
            "cid": r[0],
            "name": r[1],
            "type": r[2],
            "notnull": r[3],
            "dflt_value": r[4],
            "pk": r[5],
        }
        for r in rows
    ]


def column_ddl(col: dict, *, force_nullable: bool = False) -> str:
    parts = [col["name"], col["type"] or "TEXT"]
    if col["notnull"] and not force_nullable:
        parts.append("NOT NULL")
    if col["dflt_value"] is not None:
        parts.append(f"DEFAULT {col['dflt_value']}")
    return " ".join(parts)


def build_create_table(cards_cols: list[dict], variants_cols: list[dict]) -> tuple[str, dict]:
    """Return (CREATE TABLE SQL, column-source map for documentation)."""
    used_names: set[str] = {"id", "created_at"}
    lines: list[str] = [
        "CREATE TABLE learned_cards (",
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,",
        "  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),",
    ]
    source_map: dict[str, str] = {
        "id": "learning_pool_own",
        "created_at": "learning_pool_own",
    }

    # Mirror cards columns (skip source PK).
    for col in cards_cols:
        if col["name"] == "id":
            continue
        # Source-table NOT-NULL constraints are too strict for the learning
        # pool (a learner row may know name but not power yet). Drop NOT NULL.
        ddl = column_ddl(col, force_nullable=True)
        lines.append(f"  {ddl},")
        used_names.add(col["name"])
        source_map[col["name"]] = "cards"

    # Mirror card_variants columns (skip source PK + own card_id FK semantics).
    # Collisions get the `variant_` prefix.
    for col in variants_cols:
        if col["name"] == "id":
            continue
        target_name = col["name"]
        if target_name in used_names:
            target_name = f"variant_{col['name']}"
        renamed = dict(col)
        renamed["name"] = target_name
        ddl = column_ddl(renamed, force_nullable=True)
        lines.append(f"  {ddl},")
        used_names.add(target_name)
        source_map[target_name] = f"card_variants.{col['name']}"

    # Metadata columns last.
    for name, typ, tail in METADATA_COLUMNS:
        ddl = f"{name} {typ}"
        if tail:
            ddl = f"{ddl} {tail}"
        lines.append(f"  {ddl},")
        used_names.add(name)
        source_map[name] = "learning_metadata"

    # Remove trailing comma on the last column.
    lines[-1] = lines[-1].rstrip(",")
    lines.append(")")
    return "\n".join(lines), source_map


INDEXES: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_learned_cards_identity "
    "ON learned_cards(canonical_code, print_id)",
    "CREATE INDEX IF NOT EXISTS idx_learned_cards_readiness_state "
    "ON learned_cards(readiness_state)",
    "CREATE INDEX IF NOT EXISTS idx_learned_cards_contributing_model "
    "ON learned_cards(contributing_model)",
    "CREATE INDEX IF NOT EXISTS idx_learned_cards_last_verified " "ON learned_cards(last_verified)",
]


def existing_table_columns(conn: sqlite3.Connection, table: str) -> list[str] | None:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchall()
    if not rows:
        return None
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        default=str(DEFAULT_POOL_DB),
        help="learning pool db path (default: data/miru_learning_pool.db)",
    )
    args = parser.parse_args()
    pool_path = Path(args.db)

    if not CATALOG_DB.exists():
        print(f"FAIL: card_catalog.db missing at {CATALOG_DB}", file=sys.stderr)
        return 2

    # Introspect catalog schema READ-ONLY.
    catalog = sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True)
    try:
        cards_cols = introspect(catalog, "cards")
        variants_cols = introspect(catalog, "card_variants")
    finally:
        catalog.close()

    create_sql, source_map = build_create_table(cards_cols, variants_cols)

    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool = sqlite3.connect(pool_path)
    try:
        existing = existing_table_columns(pool, "learned_cards")
        if existing is not None:
            # Idempotency guard: confirm the existing shape matches what we'd build.
            expected_names = [
                line.strip().split()[0].rstrip(",") for line in create_sql.splitlines()[1:-1]
            ]
            if existing == expected_names:
                print(
                    f"learned_cards already exists with matching shape ({len(existing)} columns). No-op."
                )
                return 0
            print(
                "FAIL: learned_cards exists with DIFFERENT shape — refusing to clobber.\n"
                f"  existing: {existing}\n  expected: {expected_names}\n"
                "Run a migration ticket instead.",
                file=sys.stderr,
            )
            return 3

        pool.executescript(create_sql + ";")
        for idx in INDEXES:
            pool.execute(idx)
        pool.commit()
    finally:
        pool.close()

    total_cols = len(source_map)
    n_cards = sum(1 for v in source_map.values() if v == "cards")
    n_variants = sum(1 for v in source_map.values() if v.startswith("card_variants."))
    n_meta = sum(1 for v in source_map.values() if v == "learning_metadata")
    n_own = sum(1 for v in source_map.values() if v == "learning_pool_own")
    print(f"Created {pool_path}")
    print(
        f"  learned_cards: {total_cols} columns "
        f"({n_own} own + {n_cards} mirrored from cards + {n_variants} mirrored from card_variants + {n_meta} learning metadata)"
    )
    print(f"  indexes: {len(INDEXES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
