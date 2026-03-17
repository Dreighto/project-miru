"""
Minimal source-agreement computation for Miru (compute-on-read only).

Compares field_payload_json across learning_dossier_sources for a card
and returns agreement level and counts. No schema changes; worktree-only.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from tools.miru_ai_onepiece import clean_display_text


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_learning_dossiers.db"

# Same comparable fields as project sync merge (design doc §5).
COMPARABLE_FIELDS = (
    "card_name",
    "set_code",
    "set_name",
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
)


def _normalize_value(field_name: str, value: Any) -> str | int | None:
    """Normalize a field value for comparison (aligned with project sync)."""
    if value is None:
        return None
    if field_name == "cost":
        try:
            v = int(value)
            return v
        except (TypeError, ValueError):
            return None
    if field_name == "traits":
        if isinstance(value, (list, tuple)):
            parts = [clean_display_text(str(x)) for x in value if clean_display_text(str(x))]
            return " / ".join(sorted(parts)) if parts else ""
        text = clean_display_text(str(value or ""))
        if "/" in text:
            parts = [clean_display_text(p) for p in text.split("/") if clean_display_text(p)]
            return " / ".join(sorted(parts)) if parts else ""
        return text
    text = clean_display_text(str(value or ""))
    return text if text else None


def _is_blank(normalized: str | int | None) -> bool:
    if normalized is None:
        return True
    if isinstance(normalized, str):
        return not normalized.strip()
    return False


def _compute_field_outcome(values: list[str | int | None]) -> str:
    """
    Given normalized values from all sources for one field:
    - 'missing': fewer than 2 non-blank values
    - 'agree': at least 2 non-blank and all equal
    - 'conflict': at least 2 distinct non-blank values
    """
    non_blank = [v for v in values if not _is_blank(v)]
    if len(non_blank) < 2:
        return "missing"
    distinct = set()
    for v in non_blank:
        if isinstance(v, str):
            distinct.add(v.strip())
        else:
            distinct.add(v)
    if len(distinct) == 1:
        return "agree"
    return "conflict"


def compute_card_source_agreement(
    card_code: str,
    dossier_db_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Load all learning_dossier_sources rows for the card, compare comparable fields,
    and return agreement level and counts. Compute-on-read; no schema change.

    Returns:
        card_code, source_count, agreement_level, agree_count, conflict_count, checked_fields
    """
    path = Path(dossier_db_path) if dossier_db_path is not None else DEFAULT_DOSSIER_DB_PATH
    card_code = str(card_code or "").strip().upper()
    if not card_code:
        return {
            "card_code": "",
            "source_count": 0,
            "agreement_level": "single_source",
            "agree_count": 0,
            "conflict_count": 0,
            "checked_fields": list(COMPARABLE_FIELDS),
        }

    if not path.is_file():
        return {
            "card_code": card_code,
            "source_count": 0,
            "agreement_level": "single_source",
            "agree_count": 0,
            "conflict_count": 0,
            "checked_fields": list(COMPARABLE_FIELDS),
        }

    rows: list[dict[str, Any]] = []
    try:
        with closing(sqlite3.connect(path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT card_code, source_id, source_reference, field_payload_json
                FROM learning_dossier_sources
                WHERE card_code = ?
                """,
                (card_code,),
            )
            for row in cursor:
                payload_str = str(row["field_payload_json"] or "{}")
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    payload = {}
                rows.append({
                    "source_id": str(row["source_id"] or ""),
                    "source_reference": str(row["source_reference"] or ""),
                    "payload": payload if isinstance(payload, dict) else {},
                })
    except sqlite3.OperationalError:
        return {
            "card_code": card_code,
            "source_count": 0,
            "agreement_level": "single_source",
            "agree_count": 0,
            "conflict_count": 0,
            "checked_fields": list(COMPARABLE_FIELDS),
        }

    source_count = len(rows)
    if source_count < 2:
        return {
            "card_code": card_code,
            "source_count": source_count,
            "agreement_level": "single_source",
            "agree_count": 0,
            "conflict_count": 0,
            "checked_fields": list(COMPARABLE_FIELDS),
        }

    agree_count = 0
    conflict_count = 0
    for field_name in COMPARABLE_FIELDS:
        values = [
            _normalize_value(field_name, s["payload"].get(field_name))
            for s in rows
        ]
        outcome = _compute_field_outcome(values)
        if outcome == "agree":
            agree_count += 1
        elif outcome == "conflict":
            conflict_count += 1

    total_compared = agree_count + conflict_count
    if conflict_count > 0:
        agreement_level = "conflict"
    elif total_compared > 0 and agree_count == total_compared:
        agreement_level = "full"
    else:
        agreement_level = "partial"

    return {
        "card_code": card_code,
        "source_count": source_count,
        "agreement_level": agreement_level,
        "agree_count": agree_count,
        "conflict_count": conflict_count,
        "checked_fields": list(COMPARABLE_FIELDS),
    }
