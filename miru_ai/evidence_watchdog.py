"""Post-review evidence infrastructure — schema + watchdog (Phase A).

Owns the three evidence tables inside ``data/miru_dev_training_reviews.db``:
  - evidence_source_weights
  - post_review_evidence
  - evidence_reconciliation

The watchdog tick flips PENDING reconciliation rows whose deadline has passed
to ERROR.  It is idempotent and safe to call on any cadence.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_PROJECT_ROOT: Path | None = None
_DB_PATH: Path | None = None


def configure(project_root: Path) -> None:
    global _PROJECT_ROOT, _DB_PATH
    _PROJECT_ROOT = Path(project_root)
    _DB_PATH = _PROJECT_ROOT / "data" / "miru_dev_training_reviews.db"


def _reviews_db_path() -> Path:
    if _DB_PATH is not None:
        return _DB_PATH
    root = _PROJECT_ROOT or Path(__file__).resolve().parent.parent
    return root / "data" / "miru_dev_training_reviews.db"


# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evidence_source_weights (
    source                  TEXT PRIMARY KEY,
    weight                  REAL NOT NULL,
    can_contradict_identity INTEGER NOT NULL DEFAULT 0,
    can_contradict_market   INTEGER NOT NULL DEFAULT 0,
    staleness_days          INTEGER NOT NULL DEFAULT 30,
    active                  INTEGER NOT NULL DEFAULT 1,
    notes                   TEXT,
    last_updated            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS post_review_evidence (
    id                      INTEGER PRIMARY KEY,
    review_id               INTEGER NOT NULL REFERENCES dev_training_reviews(id),
    card_code               TEXT NOT NULL,
    variant_key             TEXT NOT NULL,
    evidence_source         TEXT NOT NULL REFERENCES evidence_source_weights(source),
    evidence_type           TEXT NOT NULL CHECK(evidence_type IN (
        'CARD_EXISTENCE',
        'IMAGE_REFERENCE',
        'VARIANT_EXISTENCE',
        'MARKET_IDENTITY',
        'INTERNAL_CONSISTENCY',
        'PROMO_REVEAL'
    )),
    raw_query               TEXT,
    raw_result_summary      TEXT,
    raw_result_json         TEXT,
    alignment               TEXT NOT NULL CHECK(alignment IN (
        'SUPPORTS_OPERATOR',
        'CONTRADICTS_OPERATOR',
        'INCONCLUSIVE',
        'NOT_APPLICABLE'
    )),
    confidence_contribution REAL NOT NULL CHECK(confidence_contribution BETWEEN -1.0 AND 1.0),
    source_url              TEXT,
    fetched_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    evidence_status         TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(evidence_status IN (
        'ACTIVE', 'SUPERSEDED', 'STALE', 'ERROR'
    )),
    error_detail            TEXT
);

CREATE TABLE IF NOT EXISTS evidence_reconciliation (
    id                       INTEGER PRIMARY KEY,
    review_id                INTEGER NOT NULL UNIQUE REFERENCES dev_training_reviews(id),
    operator_verdict         TEXT NOT NULL,
    evidence_count           INTEGER NOT NULL DEFAULT 0,
    supporting_count         INTEGER NOT NULL DEFAULT 0,
    contradicting_count      INTEGER NOT NULL DEFAULT 0,
    inconclusive_count       INTEGER NOT NULL DEFAULT 0,
    composite_confidence     REAL NOT NULL DEFAULT 0.0,
    reconciliation_status    TEXT NOT NULL DEFAULT 'PENDING' CHECK(reconciliation_status IN (
        'PENDING', 'SUPPORTED', 'CONTRADICTED', 'INCONCLUSIVE', 'ERROR'
    )),
    requires_elevated_review INTEGER NOT NULL DEFAULT 0,
    contradiction_sources    TEXT,
    reconciled_at            TIMESTAMP,
    watchdog_deadline        TIMESTAMP,
    notes                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_evidence_review_id      ON post_review_evidence(review_id);
CREATE INDEX IF NOT EXISTS idx_evidence_card_variant   ON post_review_evidence(card_code, variant_key);
CREATE INDEX IF NOT EXISTS idx_evidence_alignment      ON post_review_evidence(alignment);
CREATE INDEX IF NOT EXISTS idx_evidence_status         ON post_review_evidence(evidence_status);
CREATE INDEX IF NOT EXISTS idx_reconciliation_status   ON evidence_reconciliation(reconciliation_status);
CREATE INDEX IF NOT EXISTS idx_reconciliation_review   ON evidence_reconciliation(review_id);
"""

# Partial index — SQLite supports WHERE on CREATE INDEX but not with IF NOT EXISTS
# in all versions, so we guard it manually.
_PARTIAL_INDEX_SQL = """
CREATE INDEX idx_reconciliation_watchdog ON evidence_reconciliation(watchdog_deadline)
    WHERE reconciliation_status = 'PENDING';
"""

_SEED_SQL = """
INSERT OR IGNORE INTO evidence_source_weights
    (source, weight, can_contradict_identity, can_contradict_market, staleness_days, active, notes, last_updated)
VALUES
    ('BANDAI_CDN_CHECK',     0.25, 1, 0,  7, 1, 'Official Bandai EN CDN. HEAD check only. Only non-internal source that can contradict identity.', CURRENT_TIMESTAMP),
    ('INTERNAL_ASSET_CHECK', 0.25, 0, 0,  1, 1, 'Local disk check. File exists, size >= 100KB, PNG header valid.', CURRENT_TIMESTAMP),
    ('PM_PARITY_CHECK',      0.20, 0, 0,  1, 1, 'HTTP GET to PM /img/ path. Read-only. No PM side effects.', CURRENT_TIMESTAMP),
    ('JUSTTCG_CONSTRAINED',  0.15, 0, 1,  7, 1, 'Constrained lookup by tcgplayerId or variantId only. Only source that can contradict market identity.', CURRENT_TIMESTAMP),
    ('OPTCGAPI_CROSS_CHECK', 0.08, 0, 0, 14, 1, 'Community scraper. NOT_FOUND = NOT_APPLICABLE, never CONTRADICTS.', CURRENT_TIMESTAMP),
    ('OPERATOR_URL',         0.15, 0, 0, 30, 1, 'Must contain card code in visible page content to count as SUPPORTS.', CURRENT_TIMESTAMP),
    ('PERPLEXITY',           0.05, 0, 0, 30, 1, 'Cannot produce CONTRADICTS_OPERATOR for any field. INCONCLUSIVE at worst.', CURRENT_TIMESTAMP),
    ('YOUTUBE',              0.03, 0, 0, 60, 1, 'Promo reveal / alt art corroboration only. Cannot produce CONTRADICTS_OPERATOR.', CURRENT_TIMESTAMP);
"""


def ensure_evidence_schema(conn: sqlite3.Connection) -> None:
    """Create evidence tables, indexes, and seed weights. Idempotent."""
    conn.executescript(_SCHEMA_SQL)
    # Partial index: guard against duplicate creation
    try:
        conn.execute(_PARTIAL_INDEX_SQL)
    except sqlite3.OperationalError:
        pass  # already exists
    conn.executescript(_SEED_SQL)


def init_evidence_schema() -> None:
    """Standalone entry-point: open the reviews DB and ensure schema."""
    db = _reviews_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(db))) as conn:
        ensure_evidence_schema(conn)
        conn.commit()
    log.info("Evidence schema initialised in %s", db)


# ── Watchdog ─────────────────────────────────────────────────────────────────

def watchdog_tick() -> int:
    """Flip overdue PENDING reconciliation rows to ERROR.

    Returns the number of rows flipped.  Safe to call repeatedly.
    """
    db = _reviews_db_path()
    if not db.is_file():
        return 0
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(str(db))) as conn:
        cur = conn.execute(
            """
            UPDATE evidence_reconciliation
               SET reconciliation_status = 'ERROR',
                   notes = COALESCE(notes || ' | ', '')
                           || 'watchdog_timeout at ' || ?,
                   reconciled_at = ?
             WHERE reconciliation_status = 'PENDING'
               AND watchdog_deadline IS NOT NULL
               AND watchdog_deadline < ?
            """,
            (now, now, now),
        )
        conn.commit()
        flipped = cur.rowcount
    if flipped:
        log.warning("Evidence watchdog flipped %d overdue row(s) to ERROR.", flipped)
    return flipped
