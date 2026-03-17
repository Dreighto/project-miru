"""
tools/miru_migrate_db.py — Safe, idempotent migration runner for Project Miru databases.

Usage
-----
    python -m tools.miru_migrate_db --target user_decks
    python -m tools.miru_migrate_db --target deck_intel
    python -m tools.miru_migrate_db --target catalog
    python -m tools.miru_migrate_db --target all

Targets
-------
    user_decks   Apply m001_user_decks.sql  → data/miru_user_decks.db  (created if missing)
    deck_intel   Apply m002_leader_hub.sql  → data/miru_deck_intel.db   (must exist)
    catalog      Apply m003_catalog_extensions.sql → data/card_catalog.db (must exist)
    all          Run all three targets in order: user_decks → deck_intel → catalog

Each migration file uses CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
throughout, so running any target twice is fully safe.

Column additions to miru_card_insights (source_ref, leader_code, generated_at)
are NOT applied here — they are handled by miru_project_sync.py via
_ensure_column(). See docs/miru_db_schema.md §8 for the full migration sequence.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# ─────────────────────────────────────────────────────────────────────────────
# Migration manifest
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: (target_name, sql_file, db_filename, must_exist)
#   must_exist=True  → abort if the database file is missing (it should have
#                       been created by another tool before this migration runs)
#   must_exist=False → create the database file if it does not exist yet

MIGRATIONS: list[tuple[str, str, str, bool]] = [
    ("user_decks", "m001_user_decks.sql", "miru_user_decks.db", False),
    ("deck_intel", "m002_leader_hub.sql", "miru_deck_intel.db", True),
    ("catalog",    "m003_catalog_extensions.sql", "card_catalog.db", True),
]

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("miru_migrate_db")

# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────


def _load_sql(sql_file: str) -> str:
    """Read and return the contents of a migration SQL file."""
    path = MIGRATIONS_DIR / sql_file
    if not path.exists():
        raise FileNotFoundError(f"Migration file not found: {path}")
    return path.read_text(encoding="utf-8")


def _run_migration(target: str, sql_file: str, db_filename: str, must_exist: bool) -> None:
    """
    Apply a single migration to the target database.

    Parameters
    ----------
    target      : short name used in log messages
    sql_file    : filename inside tools/migrations/
    db_filename : database filename inside data/
    must_exist  : if True, abort when the database file does not exist yet
    """
    db_path = DATA_DIR / db_filename
    sql_path = MIGRATIONS_DIR / sql_file

    log.info("─────────────────────────────────────────")
    log.info("Target   : %s", target)
    log.info("SQL file : %s", sql_path.relative_to(PROJECT_ROOT))
    log.info("Database : %s", db_path.relative_to(PROJECT_ROOT))

    # Pre-flight: check that the database exists when required
    if must_exist and not db_path.exists():
        log.error(
            "%s does not exist. Run the tool that creates it first, then re-run this migration.",
            db_path,
        )
        raise FileNotFoundError(f"Required database missing: {db_path}")

    # Load SQL
    sql = _load_sql(sql_file)

    # Connect — this creates the file if must_exist=False
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA foreign_keys = ON")

        # Run the entire migration script in one executescript() call.
        # executescript() implicitly commits any pending transaction first,
        # then executes all statements. It is appropriate here because our
        # migration files contain DDL (CREATE TABLE / CREATE INDEX) which
        # cannot be rolled back in SQLite anyway.
        conn.executescript(sql)

        conn.commit()
        log.info("OK       : migration applied successfully.")
    except Exception:
        log.exception("FAILED   : migration raised an exception.")
        conn.close()
        raise
    else:
        conn.close()

    # Verify: report every table that now exists in the database
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        tables = [r[0] for r in rows]
        log.info("Tables   : %s", ", ".join(tables) if tables else "(none)")
    finally:
        conn.close()


def run(targets: list[str]) -> None:
    """Run migrations for the requested targets in canonical order."""
    manifest = {entry[0]: entry for entry in MIGRATIONS}

    # Validate all requested targets before running any migration
    for t in targets:
        if t not in manifest:
            valid = ", ".join(e[0] for e in MIGRATIONS)
            raise ValueError(f"Unknown target '{t}'. Valid targets: {valid}")

    # Preserve canonical ordering regardless of argument order
    ordered_targets = [e[0] for e in MIGRATIONS if e[0] in targets]

    for target_name in ordered_targets:
        _, sql_file, db_filename, must_exist = manifest[target_name]
        _run_migration(target_name, sql_file, db_filename, must_exist)

    log.info("─────────────────────────────────────────")
    log.info("All requested migrations completed.")


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    valid_targets = [e[0] for e in MIGRATIONS] + ["all"]
    parser = argparse.ArgumentParser(
        prog="python -m tools.miru_migrate_db",
        description=(
            "Apply Project Miru database migrations. "
            "All migrations are idempotent — safe to run multiple times."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Targets
  user_decks   Apply m001_user_decks.sql → data/miru_user_decks.db
  deck_intel   Apply m002_leader_hub.sql → data/miru_deck_intel.db
  catalog      Apply m003_catalog_extensions.sql → data/card_catalog.db
  all          Run all three in order

Example — full migration sequence:
  python -m tools.miru_migrate_db --target all
  python -m tools.miru_project_sync   # adds source_ref / leader_code / generated_at columns
""",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=valid_targets,
        metavar="TARGET",
        help=f"Migration target: {', '.join(valid_targets)}",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        metavar="PATH",
        help=(
            "Override the data directory (default: <repo_root>/data). "
            "Useful for tests or non-standard deployments."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    global DATA_DIR  # allow --data-dir override

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.data_dir is not None:
        DATA_DIR = Path(args.data_dir).resolve()
        if not DATA_DIR.is_dir():
            parser.error(f"--data-dir does not exist or is not a directory: {DATA_DIR}")
        log.info("Data dir override: %s", DATA_DIR)

    if not DATA_DIR.is_dir():
        parser.error(
            f"Data directory does not exist: {DATA_DIR}\n"
            "Run the main pipeline first, or pass --data-dir to specify a different path."
        )

    if args.target == "all":
        targets = [e[0] for e in MIGRATIONS]
    else:
        targets = [args.target]

    try:
        run(targets)
    except (FileNotFoundError, ValueError) as exc:
        log.error("%s", exc)
        sys.exit(1)
    except Exception:
        log.exception("Unexpected error during migration.")
        sys.exit(2)


if __name__ == "__main__":
    main()
