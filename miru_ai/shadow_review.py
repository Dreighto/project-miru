"""Shadow-review API layer (PRO-909 PR-A).

Backend for the dev-page review queue. Reads from miru_learning_pool.db
(PR-A of PRO-908, schema PR-907) and writes operator verdicts to the
append-only JSONL that override_metric.py watches.

Endpoints called by miru_ai/dev_review_hub_ui/src/lib/api/shadow-review.ts:

  GET  /api/shadow-review/queue?limit=N
  GET  /api/shadow-review/item/<canonical_code>/<print_id>?contributing_model=<m>
  POST /api/shadow-review/verdict

The Flask routes live in server.py and call thin wrappers here.

Discipline:
  - miru_learning_pool.db: opened READ-ONLY for queue + item fetches.
  - approval_state / promotion_state updates happen on verdict commit —
    UPDATE only, never INSERT.
  - data/shadow_loop_verifier_overrides.jsonl is APPEND-ONLY (the file
    services/shadow_loop/override_metric.py reads).

PRO-928 — ported from the single `promotion_status` column to the three-axis
state model (readiness_state / approval_state / promotion_state). This is a
minimal keep-it-working port of the existing correct/wrong/defer verdict flow;
the full three-door verdict model is Ticket 3b.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_POOL_DB = REPO_ROOT / "data" / "miru_learning_pool.db"
DEFAULT_OVERRIDES_JSONL = REPO_ROOT / "data" / "shadow_loop_verifier_overrides.jsonl"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bandai_url(canonical_code: str) -> str | None:
    """Bandai deep-link from a canonical_code like OP01-001."""
    if not canonical_code or "-" not in canonical_code:
        return None
    return f"https://en.onepiece-cardgame.com/cardlist/" f"?search=true&freewords={canonical_code}"


def _tcgplayer_url(canonical_code: str) -> str | None:
    """TCGPlayer search URL for the canonical_code."""
    if not canonical_code:
        return None
    return f"https://www.tcgplayer.com/search/one-piece-card-game/product?q={canonical_code}"


def _parse_field_outcomes(row_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse the `validator_agreement` JSON blob into the FieldOutcome[] shape.

    Older rows may not have a JSON blob — return [] in that case so the UI
    can render an empty evidence panel rather than crash.
    """
    raw = row_dict.get("validator_agreement") or ""
    if not raw:
        return []
    try:
        blob = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(blob, dict):
        return []
    outcomes: list[dict[str, Any]] = []
    for field, payload in blob.items():
        if not isinstance(payload, dict):
            continue
        outcomes.append(
            {
                "field": field,
                "tier": payload.get("tier", "inferred"),
                "outcome": payload.get("outcome", "inconclusive"),
                "reason": payload.get("reason", ""),
                "primary_value": payload.get("model_value"),
                "validator_value": payload.get("validator_value"),
                "catalog_value": payload.get("catalog_value"),
                "bandai_value": payload.get("bandai_value"),
            }
        )
    return outcomes


def _inconclusive_field_count(row_dict: dict[str, Any]) -> int:
    outcomes = _parse_field_outcomes(row_dict)
    return sum(1 for o in outcomes if o["outcome"] == "inconclusive")


def fetch_queue(
    pool_db: Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Return queue items + total count.

    The queue is rows awaiting an operator verdict (`approval_state =
    'pending_review'`). Rows blocked by a guardrail surface first; a clean
    `ready_for_review` row with no inconclusive fields is not review work and
    is filtered out.
    """
    pool_db = Path(pool_db) if pool_db is not None else DEFAULT_POOL_DB
    if not pool_db.exists():
        return {"items": [], "total": 0}
    conn = sqlite3.connect(f"file:{pool_db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT canonical_code, print_id, contributing_model,
                   readiness_state, approval_state, promotion_state,
                   confidence_score, validator_agreement, created_at, last_verified
            FROM learned_cards
            WHERE approval_state = 'pending_review'
            ORDER BY
              CASE readiness_state WHEN 'blocked_by_guardrail' THEN 0 ELSE 1 END,
              last_verified DESC NULLS LAST,
              created_at DESC
            """,
        ).fetchall()
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    for row in rows:
        row_dict = dict(row)
        inconclusive = _inconclusive_field_count(row_dict)
        # A clean `ready_for_review` row (Stage 3 passed, no inconclusive
        # fields) is not review work — only surface rows blocked by a
        # guardrail or carrying at least one inconclusive field.
        if row_dict["readiness_state"] != "blocked_by_guardrail" and inconclusive == 0:
            continue
        items.append(
            {
                "canonical_code": row_dict["canonical_code"],
                "print_id": row_dict["print_id"],
                "contributing_model": row_dict.get("contributing_model") or "",
                "readiness_state": row_dict["readiness_state"],
                "approval_state": row_dict["approval_state"],
                "promotion_state": row_dict["promotion_state"],
                "confidence_score": float(row_dict.get("confidence_score") or 0.0),
                "inconclusive_field_count": inconclusive,
                "created_at": row_dict.get("created_at") or "",
                "last_verified": row_dict.get("last_verified"),
            }
        )

    total = len(items)
    if limit and limit > 0:
        items = items[:limit]
    return {"items": items, "total": total}


def fetch_item(
    canonical_code: str,
    print_id: str,
    contributing_model: str,
    pool_db: Path | None = None,
) -> dict[str, Any] | None:
    """Return full evidence for one (card, model) row, or None if not found."""
    pool_db = Path(pool_db) if pool_db is not None else DEFAULT_POOL_DB
    if not pool_db.exists():
        return None
    conn = sqlite3.connect(f"file:{pool_db}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT canonical_code, print_id, contributing_model,
                   readiness_state, approval_state, promotion_state,
                   confidence_score, validator_agreement
            FROM learned_cards
            WHERE canonical_code = ? AND print_id = ? AND contributing_model = ?
            LIMIT 1
            """,
            (canonical_code, print_id, contributing_model),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    row_dict = dict(row)
    return {
        "canonical_code": row_dict["canonical_code"],
        "print_id": row_dict["print_id"],
        "contributing_model": row_dict.get("contributing_model") or "",
        "readiness_state": row_dict["readiness_state"],
        "approval_state": row_dict["approval_state"],
        "promotion_state": row_dict["promotion_state"],
        "confidence_score": float(row_dict.get("confidence_score") or 0.0),
        "field_outcomes": _parse_field_outcomes(row_dict),
        "bandai_url": _bandai_url(canonical_code),
        "tcgplayer_url": _tcgplayer_url(canonical_code),
    }


# State transitions on verdict commit, as (approval_state, promotion_state).
# Defer is intentionally absent — defer does NOT change the row's state, it
# only appends to JSONL; the card stays `pending_review`, held for the
# operator. Minimal keep-it-working port of the correct/wrong/defer verdict
# model; the full three-door verdict model is Ticket 3b.
_VERDICT_TO_STATE: dict[str, tuple[str, str]] = {
    "correct": ("approved_for_candidate", "review_approved_candidate"),
    "wrong": ("rejected", "blocked_from_promotion"),
}


def submit_verdict(
    canonical_code: str,
    print_id: str,
    contributing_model: str,
    verdict: str,
    sources_checked: list[str],
    pool_db: Path | None = None,
    overrides_jsonl: Path | None = None,
    operator: str = "operator",
) -> dict[str, Any]:
    """Append the operator's verdict event to JSONL and (if applicable) update
    promotion_status.

    Returns the response envelope the UI expects:
        { ok: bool, new_approval_state: str, event_logged: bool }

    Raises ValueError on invalid input (bubbled to a 400 by the route).
    """
    if verdict not in {"correct", "wrong", "defer"}:
        raise ValueError(f"verdict must be one of correct|wrong|defer, got {verdict!r}")
    if verdict in {"correct", "wrong"} and not sources_checked:
        raise ValueError(
            "sources_checked is required (at least one entry) when verdict is correct/wrong"
        )

    pool_db = Path(pool_db) if pool_db is not None else DEFAULT_POOL_DB
    overrides_jsonl = (
        Path(overrides_jsonl) if overrides_jsonl is not None else DEFAULT_OVERRIDES_JSONL
    )

    # Read the current verifier outcome so we can record what the operator
    # was reacting to — useful for the verifier-error-rate metric.
    current_approval = "pending_review"
    current_outcome = "inconclusive"
    if pool_db.exists():
        conn_ro = sqlite3.connect(f"file:{pool_db}?mode=ro", uri=True)
        try:
            row = conn_ro.execute(
                "SELECT approval_state, confidence_score FROM learned_cards "
                "WHERE canonical_code = ? AND print_id = ? AND contributing_model = ? LIMIT 1",
                (canonical_code, print_id, contributing_model),
            ).fetchone()
        finally:
            conn_ro.close()
        if row is not None:
            current_approval = row[0] or "pending_review"
            score = row[1] or 0.0
            if score >= 1.0:
                current_outcome = "verified-correct"

    # Append to the JSONL (override_metric.py reads this).
    event = {
        "ts": _utc_now_iso(),
        "canonical_code": canonical_code,
        "print_id": print_id,
        "contributing_model": contributing_model,
        "verdict": "override"
        if verdict == "wrong"
        else ("agree" if verdict == "correct" else "defer"),
        "operator_verdict": verdict,
        "verifier_outcome": current_outcome,
        "sources_checked": list(sources_checked),
        "operator": operator,
    }
    overrides_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with overrides_jsonl.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")

    # Update approval_state + promotion_state only on correct/wrong (defer
    # leaves the row pending_review — it stays held for the operator).
    new_approval = current_approval
    if verdict in _VERDICT_TO_STATE and pool_db.exists():
        new_approval, new_promotion = _VERDICT_TO_STATE[verdict]
        conn_rw = sqlite3.connect(pool_db)
        try:
            conn_rw.execute(
                "UPDATE learned_cards SET approval_state = ?, promotion_state = ? "
                "WHERE canonical_code = ? AND print_id = ? AND contributing_model = ?",
                (new_approval, new_promotion, canonical_code, print_id, contributing_model),
            )
            conn_rw.commit()
        finally:
            conn_rw.close()

    return {
        "ok": True,
        "new_approval_state": new_approval,
        "event_logged": True,
    }
