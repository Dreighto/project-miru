"""Write learned-card rows into miru_learning_pool.db.

The shadow loop produces one row per (canonical_code, print_id, contributing_model)
on each pass. PR-A writes rows with:

  * The primary's claimed values for each field (in the mirrored card columns).
  * The verifier's per-field outcomes in `validator_agreement` JSON.
  * `confidence_score` and `promotion_status` from the verifier's verdict.
  * `last_verified` timestamp.
  * `contributing_model` set to the primary model's identifier.
  * `learned_from` set to a stable reason string for traceability.

Each call replaces any prior row for the same
(canonical_code, print_id, contributing_model) tuple — the pool tracks current
state per (card, model), not history. History lives in `validator_agreement`
across passes is left to PR-B's per-field state machine.

PRO-908 PR-A.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Card-level columns mirrored from catalog (see PRO-907 schema).
CARD_COLUMNS: list[str] = [
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
    "effect_text",
    "trigger_text",
]


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert_learned_card(
    pool_db: Path,
    canonical_code: str,
    print_id: str,
    contributing_model: str,
    primary_answer: dict[str, Any],
    verifier_result: dict[str, Any],
    learned_from: str,
) -> int:
    """Insert or replace the learned_cards row for this (card, model).

    Returns the row id.
    """
    conn = sqlite3.connect(pool_db)
    try:
        # Always strip any existing row for the same (canonical_code, print_id,
        # contributing_model). Replace, don't accumulate — PR-B's per-field
        # state machine will encode the consecutive-correct counter in
        # validator_agreement instead of via duplicate rows.
        conn.execute(
            "DELETE FROM learned_cards "
            "WHERE canonical_code = ? AND print_id = ? AND contributing_model = ?",
            (canonical_code, print_id, contributing_model),
        )

        columns: list[str] = ["canonical_code", "print_id", "contributing_model"]
        values: list[Any] = [canonical_code, print_id, contributing_model]

        for field in CARD_COLUMNS:
            if field in primary_answer:
                columns.append(field)
                raw = primary_answer[field]
                # Models sometimes return list/dict for fields like `traits` or
                # `aliases_json`. SQLite can't bind those — serialize to JSON
                # text. The catalog stores these columns as TEXT anyway.
                if isinstance(raw, list | dict):
                    values.append(json.dumps(raw))
                else:
                    values.append(raw)

        columns.append("confidence_score")
        values.append(verifier_result.get("confidence_score", 0.0))

        columns.append("validator_agreement")
        values.append(json.dumps(verifier_result.get("field_outcomes", {})))

        columns.append("last_verified")
        values.append(_utc_now_iso())

        columns.append("learned_from")
        values.append(learned_from)

        # PR-A always writes promotion_status='experimental'; PR-C's promotion
        # gate is what graduates rows past that.
        columns.append("promotion_status")
        values.append("experimental")

        placeholders = ",".join("?" for _ in columns)
        sql = f"INSERT INTO learned_cards ({','.join(columns)}) VALUES ({placeholders})"
        cursor = conn.execute(sql, values)
        conn.commit()
        return cursor.lastrowid or 0
    finally:
        conn.close()
