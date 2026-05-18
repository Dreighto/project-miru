"""Stale-row re-queue helper (PR-C, PRO-912).

Returns (canonical_code, print_id) pairs from learned_cards where
`last_verified` is older than MAX_AGE_HOURS.  Callers add these back to the
priority queue so the card is re-evaluated on the next available tick.

Without this guard a long-running loop would permanently ignore cards it scored
in earlier passes, even if the verifier was later improved or the catalog was
corrected.  Stale requeue ensures continuous coverage without manual intervention.

PRO-908 PR-C.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger(__name__)

MAX_AGE_HOURS: int = 24  # Re-evaluate cards not verified within this window


def stale_rows(
    pool_db: Path | None = None,
    max_age_hours: int = MAX_AGE_HOURS,
) -> list[tuple[str, str]]:
    """Return DISTINCT (canonical_code, print_id) pairs whose last_verified is stale.

    Rows are ordered oldest-first so the most-neglected cards surface first.
    Returns an empty list when the DB does not exist or contains no stale rows.

    `pool_db` defaults to the path from config when not supplied (production path).
    """
    if pool_db is None:
        from .config import load as _load_config

        pool_db = _load_config().learning_pool_db

    pool_db = Path(pool_db)
    if not pool_db.exists():
        return []

    conn = sqlite3.connect(f"file:{pool_db}?mode=ro", uri=True)
    try:
        age_modifier = f"-{max_age_hours} hours"
        rows = conn.execute(
            "SELECT DISTINCT canonical_code, print_id FROM learned_cards "
            "WHERE last_verified < datetime('now', ?) "
            "ORDER BY last_verified ASC",
            (age_modifier,),
        ).fetchall()
    finally:
        conn.close()

    if rows:
        log.info("stale_requeue: %d (canonical_code, print_id) pairs need re-evaluation", len(rows))
    return [(r[0], r[1]) for r in rows]
