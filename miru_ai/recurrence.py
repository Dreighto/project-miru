"""Recurrence aggregation and candidate preparation (Pass 1–3).

Tracks recurring review patterns across (card_code, variant_key, issue_type,
proposed_correction) groups.  Seeded from existing ``dev_training_reviews``
history, refreshed after each evidence reconciliation transition, and
generates safe candidate-preparation rows when eligibility thresholds are met.

All writes stay inside ``data/miru_dev_training_reviews.db``.
"""

from __future__ import annotations

import json
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


# ── Normalization ────────────────────────────────────────────────────────────


def normalize_correction(value: object) -> str:
    """Deterministic JSON serialization for proposed_correction comparison.

    Accepts a dict, list, or string.  Returns a canonical JSON string with
    sorted keys and no extraneous whitespace so that grouping by exact text
    match is reliable.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value.strip()
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS recurrence_aggregates (
    id                  INTEGER PRIMARY KEY,
    card_code           TEXT NOT NULL,
    variant_key         TEXT NOT NULL,
    issue_type          TEXT NOT NULL,
    proposed_correction TEXT NOT NULL,
    decision_count      INTEGER NOT NULL DEFAULT 0,
    approve_count       INTEGER NOT NULL DEFAULT 0,
    reject_count        INTEGER NOT NULL DEFAULT 0,
    approval_rate       REAL NOT NULL DEFAULT 0.0,
    latest_review_id    INTEGER REFERENCES dev_training_reviews(id),
    latest_decided_at   TIMESTAMP,
    earliest_decided_at TIMESTAMP,
    has_contradiction   INTEGER NOT NULL DEFAULT 0,
    contradiction_ever  INTEGER NOT NULL DEFAULT 0,
    suppressed          INTEGER NOT NULL DEFAULT 0,
    last_refreshed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(card_code, variant_key, issue_type, proposed_correction)
);

CREATE INDEX IF NOT EXISTS idx_recurrence_card_variant
    ON recurrence_aggregates(card_code, variant_key);
CREATE INDEX IF NOT EXISTS idx_recurrence_issue
    ON recurrence_aggregates(issue_type);
CREATE INDEX IF NOT EXISTS idx_recurrence_eligible
    ON recurrence_aggregates(approve_count, approval_rate, has_contradiction, suppressed);

CREATE TABLE IF NOT EXISTS recurrence_review_links (
    id           INTEGER PRIMARY KEY,
    aggregate_id INTEGER NOT NULL REFERENCES recurrence_aggregates(id),
    review_id    INTEGER NOT NULL REFERENCES dev_training_reviews(id),
    verdict      TEXT NOT NULL,
    decided_at   TIMESTAMP NOT NULL,
    UNIQUE(aggregate_id, review_id)
);

CREATE INDEX IF NOT EXISTS idx_link_aggregate ON recurrence_review_links(aggregate_id);
CREATE INDEX IF NOT EXISTS idx_link_review    ON recurrence_review_links(review_id);

CREATE TABLE IF NOT EXISTS correction_candidates (
    id                        INTEGER PRIMARY KEY,
    aggregate_id              INTEGER NOT NULL REFERENCES recurrence_aggregates(id),
    card_code                 TEXT NOT NULL,
    variant_key               TEXT NOT NULL,
    issue_type                TEXT NOT NULL,
    proposed_correction       TEXT NOT NULL,
    source_review_ids         TEXT NOT NULL,
    decision_count            INTEGER NOT NULL,
    approve_count             INTEGER NOT NULL,
    approval_rate             REAL NOT NULL,
    composite_confidence_avg  REAL NOT NULL,
    eligibility_path          TEXT NOT NULL CHECK(eligibility_path IN ('SUPPORTED', 'INCONCLUSIVE')),
    candidate_status          TEXT NOT NULL DEFAULT 'PENDING_REVIEW' CHECK(candidate_status IN (
        'PENDING_REVIEW',
        'ELEVATED_REVIEW_REQUIRED',
        'APPROVED',
        'REJECTED',
        'PROMOTED',
        'SUPERSEDED',
        'STALE'
    )),
    contradiction_flag        INTEGER NOT NULL DEFAULT 0,
    elevation_reason          TEXT,
    elevation_acknowledged_at TIMESTAMP,
    operator_approved_at      TIMESTAMP,
    operator_approved_by      TEXT DEFAULT 'operator',
    promoted_at               TIMESTAMP,
    promotion_target_table    TEXT,
    promotion_target_column   TEXT,
    promotion_target_row_id   INTEGER,
    superseded_by             INTEGER REFERENCES correction_candidates(id),
    stale_after               TIMESTAMP,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_candidates_status    ON correction_candidates(candidate_status);
CREATE INDEX IF NOT EXISTS idx_candidates_card      ON correction_candidates(card_code, variant_key);
CREATE INDEX IF NOT EXISTS idx_candidates_aggregate ON correction_candidates(aggregate_id);
"""


def ensure_recurrence_schema(conn: sqlite3.Connection) -> None:
    """Create recurrence tables and indexes.  Idempotent."""
    conn.executescript(_SCHEMA_SQL)


def init_recurrence_schema() -> None:
    """Standalone entry-point: open the reviews DB and ensure schema."""
    db = _reviews_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(str(db))) as conn:
        ensure_recurrence_schema(conn)
        conn.commit()
    log.info("Recurrence schema initialised in %s", db)


# ── Seed / refresh ───────────────────────────────────────────────────────────


def _verdict_to_decision(action: str) -> str | None:
    """Map review action to a recurrence verdict.

    Returns ``'approve'`` when the operator wants the correction applied,
    ``'reject'`` when they explicitly hold, or ``None`` for actions that
    produce no recurrence row (e.g. ``'approve'`` with no issues means the
    card looks correct — nothing to track).
    """
    action = (action or "").strip().lower()
    if action == "fix_it":
        return "approve"
    if action == "hold":
        return "reject"
    return None


def seed_recurrence_from_history() -> dict[str, int]:
    """Populate recurrence tables from existing review history.

    Processes every review in ``dev_training_reviews`` that has a non-empty
    ``issues_json`` and a trackable action (``fix_it`` or ``hold``).

    Returns a summary dict with counts of rows created / updated.
    """
    db = _reviews_db_path()
    if not db.is_file():
        return {"reviews_scanned": 0, "aggregates_upserted": 0, "links_created": 0}

    stats = {"reviews_scanned": 0, "aggregates_upserted": 0, "links_created": 0}
    now = datetime.now(timezone.utc).isoformat()

    with closing(sqlite3.connect(str(db))) as conn:
        conn.row_factory = sqlite3.Row
        ensure_recurrence_schema(conn)

        # Ensure correction_detail_json column exists (migration compat).
        _existing = {row[1] for row in conn.execute("PRAGMA table_info(dev_training_reviews)")}
        if "correction_detail_json" not in _existing:
            conn.execute(
                "ALTER TABLE dev_training_reviews "
                "ADD COLUMN correction_detail_json TEXT NOT NULL DEFAULT '[]'"
            )

        reviews = conn.execute(
            """
            SELECT r.id, r.card_code, r.variant_key, r.action,
                   r.issues_json, r.created_at,
                   COALESCE(r.correction_detail_json, '[]') AS correction_detail_json,
                   COALESCE(er.reconciliation_status, 'NONE') AS recon_status,
                   COALESCE(er.requires_elevated_review, 0) AS elevated
            FROM dev_training_reviews r
            LEFT JOIN evidence_reconciliation er ON er.review_id = r.id
            ORDER BY r.id
            """
        ).fetchall()

        for rev in reviews:
            stats["reviews_scanned"] += 1
            decision = _verdict_to_decision(rev["action"])
            if decision is None:
                continue

            try:
                issues = json.loads(rev["issues_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                issues = []
            if not isinstance(issues, list) or not issues:
                continue

            card_code = (rev["card_code"] or "").strip().upper()
            variant_key = (rev["variant_key"] or "").strip()
            decided_at = rev["created_at"]
            review_id = rev["id"]
            is_contradicted = rev["recon_status"] == "CONTRADICTED" or rev["elevated"] == 1

            # Build a lookup from issue→structured correction detail so we
            # can use enriched payloads when available.
            try:
                _raw_details = json.loads(rev["correction_detail_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                _raw_details = []
            detail_by_issue: dict[str, dict] = {}
            if isinstance(_raw_details, list):
                for _d in _raw_details:
                    if isinstance(_d, dict) and "issue" in _d:
                        _key = str(_d["issue"]).strip().lower()
                        if _key:
                            detail_by_issue[_key] = _d

            for raw_issue in issues:
                issue_type = str(raw_issue).strip().lower()
                if not issue_type:
                    continue

                # proposed_correction captures WHAT is being corrected, not
                # the operator's decision.  The decision (fix_it → approve,
                # hold → reject) is tracked by approve_count / reject_count.
                # When structured correction detail is available from an
                # enriched review payload, use it directly.  Otherwise fall
                # back to the coarse issue-only dict.
                if issue_type in detail_by_issue:
                    proposed = normalize_correction(detail_by_issue[issue_type])
                else:
                    proposed = normalize_correction({"issue": issue_type})

                # Upsert aggregate row
                conn.execute(
                    """
                    INSERT INTO recurrence_aggregates
                        (card_code, variant_key, issue_type, proposed_correction,
                         decision_count, approve_count, reject_count,
                         approval_rate,
                         latest_review_id, latest_decided_at, earliest_decided_at,
                         has_contradiction, contradiction_ever,
                         suppressed, last_refreshed_at)
                    VALUES (?, ?, ?, ?,  0, 0, 0,  0.0,
                            NULL, NULL, NULL,  0, 0,  0, ?)
                    ON CONFLICT(card_code, variant_key, issue_type, proposed_correction)
                    DO NOTHING
                    """,
                    (card_code, variant_key, issue_type, proposed, now),
                )

                agg_row = conn.execute(
                    """
                    SELECT id FROM recurrence_aggregates
                    WHERE card_code = ? AND variant_key = ? AND issue_type = ?
                      AND proposed_correction = ?
                    """,
                    (card_code, variant_key, issue_type, proposed),
                ).fetchone()
                if agg_row is None:
                    continue
                agg_id = agg_row["id"]

                # Insert link (skip if already linked)
                try:
                    conn.execute(
                        """
                        INSERT INTO recurrence_review_links
                            (aggregate_id, review_id, verdict, decided_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (agg_id, review_id, decision, decided_at),
                    )
                    stats["links_created"] += 1
                except sqlite3.IntegrityError:
                    pass  # already linked

                _recompute_aggregate(conn, agg_id, now)
                stats["aggregates_upserted"] += 1

        conn.commit()
    log.info("Recurrence seed complete: %s", stats)
    return stats


# ── Single-review refresh (called after reconciliation transition) ────────


def _recompute_aggregate(conn: sqlite3.Connection, agg_id: int, now: str) -> None:
    """Recompute counts, rates, contradiction, and suppression for one aggregate.

    Derives everything from the current set of linked reviews so the result
    is identical regardless of how many times this is called (idempotent).
    """
    counts = conn.execute(
        """
        SELECT COUNT(*)                                           AS decision_count,
               SUM(CASE WHEN verdict = 'approve' THEN 1 ELSE 0 END) AS approve_count,
               SUM(CASE WHEN verdict = 'reject'  THEN 1 ELSE 0 END) AS reject_count,
               MIN(decided_at)                                    AS earliest,
               MAX(decided_at)                                    AS latest
        FROM recurrence_review_links
        WHERE aggregate_id = ?
        """,
        (agg_id,),
    ).fetchone()

    dc = counts["decision_count"] or 0
    ac = counts["approve_count"] or 0
    rc = counts["reject_count"] or 0
    rate = (ac / dc) if dc > 0 else 0.0
    suppressed = 1 if rc >= 2 else 0

    contradiction_now = conn.execute(
        """
        SELECT EXISTS(
            SELECT 1 FROM recurrence_review_links rl
            JOIN evidence_reconciliation er ON er.review_id = rl.review_id
            WHERE rl.aggregate_id = ?
              AND (er.reconciliation_status = 'CONTRADICTED'
                   OR er.requires_elevated_review = 1)
        ) AS has_it
        """,
        (agg_id,),
    ).fetchone()["has_it"]

    latest_row = conn.execute(
        """
        SELECT review_id FROM recurrence_review_links
        WHERE aggregate_id = ? ORDER BY decided_at DESC LIMIT 1
        """,
        (agg_id,),
    ).fetchone()
    latest_rid = latest_row["review_id"] if latest_row else None

    conn.execute(
        """
        UPDATE recurrence_aggregates
        SET decision_count      = ?,
            approve_count       = ?,
            reject_count        = ?,
            approval_rate       = ?,
            latest_review_id    = ?,
            latest_decided_at   = ?,
            earliest_decided_at = ?,
            has_contradiction   = ?,
            contradiction_ever  = MAX(contradiction_ever, ?),
            suppressed          = ?,
            last_refreshed_at   = ?
        WHERE id = ?
        """,
        (
            dc, ac, rc, rate,
            latest_rid,
            counts["latest"], counts["earliest"],
            contradiction_now, contradiction_now,
            suppressed,
            now,
            agg_id,
        ),
    )

    # Evaluate candidate eligibility after aggregate is up-to-date.
    try:
        evaluate_candidate_for_aggregate(conn, agg_id)
    except Exception:
        log.exception("Candidate evaluation failed for aggregate %d (non-fatal)", agg_id)


def refresh_recurrence_for_review(review_id: int) -> dict[str, int]:
    """Create or update recurrence aggregates for a single review.

    Called after evidence reconciliation transitions out of PENDING so that
    the aggregate reflects the final contradiction state.  Idempotent — safe
    to call multiple times for the same review_id.

    Returns a summary dict with counts.
    """
    db = _reviews_db_path()
    if not db.is_file():
        return {"aggregates_touched": 0, "links_created": 0}

    stats = {"aggregates_touched": 0, "links_created": 0}
    now = datetime.now(timezone.utc).isoformat()

    with closing(sqlite3.connect(str(db))) as conn:
        conn.row_factory = sqlite3.Row
        ensure_recurrence_schema(conn)

        rev = conn.execute(
            """
            SELECT r.id, r.card_code, r.variant_key, r.action,
                   r.issues_json, r.created_at
            FROM dev_training_reviews r
            WHERE r.id = ?
            """,
            (review_id,),
        ).fetchone()
        if rev is None:
            return stats

        decision = _verdict_to_decision(rev["action"])
        if decision is None:
            return stats

        try:
            issues = json.loads(rev["issues_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            issues = []
        if not isinstance(issues, list) or not issues:
            return stats

        card_code = (rev["card_code"] or "").strip().upper()
        variant_key = (rev["variant_key"] or "").strip()
        decided_at = rev["created_at"]

        for raw_issue in issues:
            issue_type = str(raw_issue).strip().lower()
            if not issue_type:
                continue

            proposed = normalize_correction({"issue": issue_type})

            # Ensure aggregate row exists
            conn.execute(
                """
                INSERT INTO recurrence_aggregates
                    (card_code, variant_key, issue_type, proposed_correction,
                     decision_count, approve_count, reject_count,
                     approval_rate,
                     latest_review_id, latest_decided_at, earliest_decided_at,
                     has_contradiction, contradiction_ever,
                     suppressed, last_refreshed_at)
                VALUES (?, ?, ?, ?,  0, 0, 0,  0.0,
                        NULL, NULL, NULL,  0, 0,  0, ?)
                ON CONFLICT(card_code, variant_key, issue_type, proposed_correction)
                DO NOTHING
                """,
                (card_code, variant_key, issue_type, proposed, now),
            )

            agg_row = conn.execute(
                """
                SELECT id FROM recurrence_aggregates
                WHERE card_code = ? AND variant_key = ? AND issue_type = ?
                  AND proposed_correction = ?
                """,
                (card_code, variant_key, issue_type, proposed),
            ).fetchone()
            if agg_row is None:
                continue
            agg_id = agg_row["id"]

            # Insert link (idempotent via UNIQUE constraint)
            try:
                conn.execute(
                    """
                    INSERT INTO recurrence_review_links
                        (aggregate_id, review_id, verdict, decided_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (agg_id, review_id, decision, decided_at),
                )
                stats["links_created"] += 1
            except sqlite3.IntegrityError:
                pass  # already linked — idempotent

            _recompute_aggregate(conn, agg_id, now)
            stats["aggregates_touched"] += 1

        conn.commit()
    log.info("Recurrence refresh for review %d: %s", review_id, stats)
    return stats


# ── Coarse correction guardrail (Path B) ─────────────────────────────────


# The review UI currently stores only issue-type tags (e.g.
# "thumb_mismatch") without structured correction detail, so
# proposed_correction is coarse: {"issue":"<type>"}.  This flag blocks
# any future candidate-generation pass from consuming aggregates whose
# proposed_correction lacks structured detail.  When the review payload
# gains richer correction fields, the check below should be updated to
# recognise them.

_COARSE_CORRECTION_KEYS = frozenset({"issue"})


def is_correction_coarse(proposed_correction: str) -> bool:
    """Return True if the proposed_correction lacks structured detail.

    Coarse corrections must NOT be promoted to live governed candidates.
    """
    try:
        obj = json.loads(proposed_correction)
    except (json.JSONDecodeError, TypeError):
        return True  # unparseable → coarse
    if not isinstance(obj, dict):
        return True
    return set(obj.keys()) <= _COARSE_CORRECTION_KEYS


# ── Candidate preparation (Pass 3) ──────────────────────────────────────

# Eligibility thresholds.
_MIN_APPROVE_SUPPORTED = 2
_MIN_APPROVE_INCONCLUSIVE = 3
_MIN_APPROVAL_RATE = 0.90
_MIN_CONFIDENCE_AVG = 0.70

# Active (non-terminal) candidate statuses — candidates that should be
# superseded when the aggregate state changes.
_ACTIVE_CANDIDATE_STATUSES = (
    "PENDING_REVIEW",
    "ELEVATED_REVIEW_REQUIRED",
)


def _compute_eligibility(
    conn: sqlite3.Connection, agg: sqlite3.Row
) -> dict | None:
    """Check whether an aggregate qualifies for candidate preparation.

    Returns an eligibility dict if qualified, or ``None`` if not.
    """
    # Gate: coarse correction → blocked
    if is_correction_coarse(agg["proposed_correction"]):
        return None

    # Gate: suppressed → blocked
    if agg["suppressed"]:
        return None

    # Gate: approval_rate below threshold
    if agg["approval_rate"] < _MIN_APPROVAL_RATE:
        return None

    # All contributing reviews must have left PENDING reconciliation.
    pending_count = conn.execute(
        """
        SELECT COUNT(*) FROM recurrence_review_links rl
        JOIN evidence_reconciliation er ON er.review_id = rl.review_id
        WHERE rl.aggregate_id = ?
          AND er.reconciliation_status = 'PENDING'
        """,
        (agg["id"],),
    ).fetchone()[0]
    if pending_count > 0:
        return None

    # Compute average composite confidence across contributing reviews.
    conf_row = conn.execute(
        """
        SELECT AVG(er.composite_confidence) AS avg_conf
        FROM recurrence_review_links rl
        JOIN evidence_reconciliation er ON er.review_id = rl.review_id
        WHERE rl.aggregate_id = ?
        """,
        (agg["id"],),
    ).fetchone()
    avg_conf = conf_row["avg_conf"] if conf_row["avg_conf"] is not None else 0.0

    if avg_conf < _MIN_CONFIDENCE_AVG:
        return None

    # Determine eligibility path.
    # SUPPORTED: all contributing reconciliations are SUPPORTED.
    # INCONCLUSIVE: at least one is INCONCLUSIVE (stricter threshold).
    has_inconclusive = conn.execute(
        """
        SELECT EXISTS(
            SELECT 1 FROM recurrence_review_links rl
            JOIN evidence_reconciliation er ON er.review_id = rl.review_id
            WHERE rl.aggregate_id = ?
              AND er.reconciliation_status = 'INCONCLUSIVE'
        ) AS has_it
        """,
        (agg["id"],),
    ).fetchone()["has_it"]

    if has_inconclusive:
        path = "INCONCLUSIVE"
        min_approve = _MIN_APPROVE_INCONCLUSIVE
    else:
        path = "SUPPORTED"
        min_approve = _MIN_APPROVE_SUPPORTED

    if agg["approve_count"] < min_approve:
        return None

    # Collect source review IDs.
    review_rows = conn.execute(
        """
        SELECT review_id FROM recurrence_review_links
        WHERE aggregate_id = ? ORDER BY review_id
        """,
        (agg["id"],),
    ).fetchall()
    source_ids = [r["review_id"] for r in review_rows]

    # Determine contradiction / elevation.
    needs_elevation = bool(agg["contradiction_ever"])
    if not needs_elevation:
        needs_elevation = bool(conn.execute(
            """
            SELECT EXISTS(
                SELECT 1 FROM recurrence_review_links rl
                JOIN evidence_reconciliation er ON er.review_id = rl.review_id
                WHERE rl.aggregate_id = ?
                  AND er.requires_elevated_review = 1
            ) AS has_it
            """,
            (agg["id"],),
        ).fetchone()["has_it"])

    elevation_reason = None
    if needs_elevation:
        reasons = []
        if agg["contradiction_ever"]:
            reasons.append("contradiction_ever on aggregate")
        # Collect specific contradicting sources.
        contra_rows = conn.execute(
            """
            SELECT DISTINCT er.contradiction_sources
            FROM recurrence_review_links rl
            JOIN evidence_reconciliation er ON er.review_id = rl.review_id
            WHERE rl.aggregate_id = ?
              AND er.requires_elevated_review = 1
              AND er.contradiction_sources IS NOT NULL
            """,
            (agg["id"],),
        ).fetchall()
        for cr in contra_rows:
            reasons.append("elevated: " + cr["contradiction_sources"])
        elevation_reason = "; ".join(reasons) if reasons else "elevated_review_required"

    return {
        "path": path,
        "avg_conf": round(avg_conf, 4),
        "source_ids": source_ids,
        "needs_elevation": needs_elevation,
        "elevation_reason": elevation_reason,
    }


def evaluate_candidate_for_aggregate(
    conn: sqlite3.Connection, agg_id: int
) -> dict[str, str]:
    """Evaluate and create/update/stale a candidate for one aggregate.

    Returns a status dict: ``{"action": "created"|"updated"|"staled"|"skipped"|"blocked_coarse", ...}``.
    """
    agg = conn.execute(
        "SELECT * FROM recurrence_aggregates WHERE id = ?", (agg_id,)
    ).fetchone()
    if agg is None:
        return {"action": "skipped", "reason": "aggregate_not_found"}

    eligibility = _compute_eligibility(conn, agg)

    # Find any active candidate for this aggregate.
    active_candidate = conn.execute(
        """
        SELECT id, candidate_status FROM correction_candidates
        WHERE aggregate_id = ? AND candidate_status IN (?, ?)
        ORDER BY created_at DESC LIMIT 1
        """,
        (agg_id, *_ACTIVE_CANDIDATE_STATUSES),
    ).fetchone()

    if eligibility is None:
        # Not eligible.  If an active candidate exists, mark it STALE.
        if active_candidate is not None:
            conn.execute(
                """
                UPDATE correction_candidates
                SET candidate_status = 'STALE', stale_after = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (active_candidate["id"],),
            )
            reason = "coarse" if is_correction_coarse(agg["proposed_correction"]) else "below_threshold"
            log.info(
                "Candidate %d staled for aggregate %d (%s)",
                active_candidate["id"], agg_id, reason,
            )
            return {"action": "staled", "candidate_id": active_candidate["id"], "reason": reason}
        # Log coarse blocks explicitly as expected behavior.
        if is_correction_coarse(agg["proposed_correction"]):
            return {"action": "blocked_coarse", "aggregate_id": agg_id}
        return {"action": "skipped", "reason": "not_eligible"}

    # Eligible — build candidate data.
    status = (
        "ELEVATED_REVIEW_REQUIRED" if eligibility["needs_elevation"]
        else "PENDING_REVIEW"
    )
    source_ids_json = json.dumps(eligibility["source_ids"])

    if active_candidate is not None:
        # Update existing active candidate in-place.
        conn.execute(
            """
            UPDATE correction_candidates
            SET source_review_ids        = ?,
                decision_count           = ?,
                approve_count            = ?,
                approval_rate            = ?,
                composite_confidence_avg = ?,
                eligibility_path         = ?,
                candidate_status         = ?,
                contradiction_flag       = ?,
                elevation_reason         = ?
            WHERE id = ?
            """,
            (
                source_ids_json,
                agg["decision_count"], agg["approve_count"],
                agg["approval_rate"], eligibility["avg_conf"],
                eligibility["path"], status,
                1 if eligibility["needs_elevation"] else 0,
                eligibility["elevation_reason"],
                active_candidate["id"],
            ),
        )
        return {
            "action": "updated",
            "candidate_id": active_candidate["id"],
            "status": status,
        }

    # Create new candidate.
    cur = conn.execute(
        """
        INSERT INTO correction_candidates (
            aggregate_id, card_code, variant_key, issue_type,
            proposed_correction, source_review_ids,
            decision_count, approve_count, approval_rate,
            composite_confidence_avg, eligibility_path,
            candidate_status, contradiction_flag, elevation_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            agg_id, agg["card_code"], agg["variant_key"],
            agg["issue_type"], agg["proposed_correction"],
            source_ids_json,
            agg["decision_count"], agg["approve_count"],
            agg["approval_rate"], eligibility["avg_conf"],
            eligibility["path"], status,
            1 if eligibility["needs_elevation"] else 0,
            eligibility["elevation_reason"],
        ),
    )
    log.info(
        "Candidate %d created for aggregate %d (%s, %s)",
        cur.lastrowid, agg_id, eligibility["path"], status,
    )
    return {
        "action": "created",
        "candidate_id": cur.lastrowid,
        "status": status,
    }


# ── Candidate queue surface (Pass 4) ─────────────────────────────────────

_CANDIDATE_QUEUE_COLS = (
    "id", "aggregate_id", "card_code", "variant_key", "issue_type",
    "proposed_correction", "source_review_ids", "decision_count",
    "approve_count", "approval_rate", "composite_confidence_avg",
    "eligibility_path", "candidate_status", "contradiction_flag",
    "elevation_reason", "superseded_by", "stale_after", "created_at",
)

_CANDIDATE_SELECT = """
    SELECT id, aggregate_id, card_code, variant_key, issue_type,
           proposed_correction, source_review_ids, decision_count,
           approve_count, approval_rate, composite_confidence_avg,
           eligibility_path, candidate_status, contradiction_flag,
           elevation_reason, superseded_by, stale_after, created_at
    FROM correction_candidates
"""


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = {}
    for col in _CANDIDATE_QUEUE_COLS:
        d[col] = row[col]
    # Parse JSON fields for display.
    for jf in ("proposed_correction", "source_review_ids"):
        raw = d.get(jf) or ""
        try:
            d[jf + "_parsed"] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            d[jf + "_parsed"] = raw
    return d


def build_candidate_queue_payload(*, card_code_prefix: str = "") -> dict:
    """Build the candidate queue/history surface payload.

    Returns candidates grouped into four buckets:
    standard (PENDING_REVIEW), elevated (ELEVATED_REVIEW_REQUIRED),
    stale (STALE), and superseded (SUPERSEDED).
    All data comes from ``correction_candidates`` inside
    ``miru_dev_training_reviews.db``.

    When *card_code_prefix* is non-empty (e.g. ``"OP01-"``), only
    candidates whose ``card_code`` starts with that prefix are returned.
    """
    db = _reviews_db_path()
    empty = {
        "standard": [], "elevated": [], "stale": [], "superseded": [],
        "counts": {"standard": 0, "elevated": 0, "stale": 0, "superseded": 0},
    }
    if not db.is_file():
        return empty

    prefix = str(card_code_prefix or "").strip().upper()
    prefix_clause = ""
    prefix_params: list[str] = []
    if prefix:
        prefix_clause = " AND card_code LIKE ?"
        prefix_params = [prefix + "%"]

    try:
        with closing(sqlite3.connect(str(db))) as conn:
            conn.row_factory = sqlite3.Row
            ensure_recurrence_schema(conn)

            standard = [
                _row_to_dict(r) for r in conn.execute(
                    _CANDIDATE_SELECT + " WHERE candidate_status = 'PENDING_REVIEW'" + prefix_clause + " ORDER BY created_at DESC",
                    prefix_params,
                ).fetchall()
            ]
            elevated = [
                _row_to_dict(r) for r in conn.execute(
                    _CANDIDATE_SELECT + " WHERE candidate_status = 'ELEVATED_REVIEW_REQUIRED'" + prefix_clause + " ORDER BY created_at DESC",
                    prefix_params,
                ).fetchall()
            ]
            stale = [
                _row_to_dict(r) for r in conn.execute(
                    _CANDIDATE_SELECT + " WHERE candidate_status = 'STALE'" + prefix_clause + " ORDER BY stale_after DESC",
                    prefix_params,
                ).fetchall()
            ]
            superseded = [
                _row_to_dict(r) for r in conn.execute(
                    _CANDIDATE_SELECT + " WHERE candidate_status = 'SUPERSEDED'" + prefix_clause + " ORDER BY created_at DESC",
                    prefix_params,
                ).fetchall()
            ]

            # For elevated candidates, attach contributing review context.
            for cand in elevated:
                agg_id = cand.get("aggregate_id")
                if agg_id is not None:
                    links = conn.execute(
                        "SELECT rl.review_id, rl.verdict, rl.decided_at "
                        "FROM recurrence_review_links rl WHERE rl.aggregate_id = ? "
                        "ORDER BY rl.decided_at DESC",
                        (agg_id,),
                    ).fetchall()
                    cand["contributing_reviews"] = [
                        {"review_id": lk["review_id"], "verdict": lk["verdict"], "decided_at": lk["decided_at"]}
                        for lk in links
                    ]

            return {
                "standard": standard,
                "elevated": elevated,
                "stale": stale,
                "superseded": superseded,
                "counts": {
                    "standard": len(standard),
                    "elevated": len(elevated),
                    "stale": len(stale),
                    "superseded": len(superseded),
                },
            }
    except sqlite3.Error as exc:
        log.warning("candidate queue load failed: %s", exc)
        return empty
