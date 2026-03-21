#!/usr/bin/env python
"""Staged intake for official OPTCG rulings / Q&A (JSON).

Worktree-only, no scraping. Validates schema then writes to official_card_rulings.
Supports card-specific and general rulings; source-backed references for future UI.

Schema: see data/staging/README_official_rulings_staging.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.miru_official_rules import (
    DEFAULT_RULES_DB_PATH,
    RULING_SOURCE_TYPES,
    RULING_SOURCE_OTHER_OFFICIAL,
    STATUS_CURRENT,
    STATUS_HISTORICAL,
    STATUS_SUPERSEDED,
    is_effective_now,
    insert_card_ruling,
)


class RulingValidationError(ValueError):
    """Raised when a ruling payload fails validation."""

    pass


def _str(v: Any, name: str, allow_empty: bool = False) -> str:
    if v is None or (isinstance(v, str) and not v.strip()):
        s = ""
    else:
        s = str(v).strip()
    if not allow_empty and not s:
        raise RulingValidationError(f"Missing or empty required field: {name}")
    return s


def _validate_one_ruling(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise RulingValidationError(f"rulings[{index}] must be an object")
    ruling_id = _str(item.get("ruling_id"), f"rulings[{index}].ruling_id")
    ruling_text = _str(item.get("ruling_text") or item.get("answer_text"), f"rulings[{index}].ruling_text (or answer_text)")
    source_id = _str(item.get("source_id"), f"rulings[{index}].source_id")
    source_type = (item.get("source_type") or "").strip().lower() or RULING_SOURCE_OTHER_OFFICIAL
    if source_type not in RULING_SOURCE_TYPES:
        source_type = RULING_SOURCE_OTHER_OFFICIAL
    status = (item.get("status") or "").strip().lower() or STATUS_CURRENT
    if status not in (STATUS_CURRENT, STATUS_HISTORICAL, STATUS_SUPERSEDED):
        status = STATUS_CURRENT
    return {
        "ruling_id": ruling_id,
        "card_code": (item.get("card_code") or "").strip().upper() or None,
        "topic_key": (item.get("topic_key") or "").strip(),
        "question_text": (item.get("question_text") or "").strip(),
        "ruling_text": ruling_text,
        "normalized_summary": (item.get("normalized_summary") or "").strip(),
        "source_id": source_id,
        "source_type": source_type,
        "source_title": (item.get("source_title") or "").strip(),
        "source_url": (item.get("source_url") or "").strip(),
        "source_reference": (item.get("source_reference") or "").strip(),
        "source_anchor": (item.get("source_anchor") or "").strip(),
        "published_at": (item.get("published_at") or "").strip(),
        "effective_at": (item.get("effective_at") or "").strip(),
        "status": status,
        "tags": (item.get("tags") or "").strip(),
    }


def validate_rulings_payload(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate and normalize a rulings payload. Expects { rulings: [ {...}, ... ] } or a single ruling object."""
    if not isinstance(data, dict):
        raise RulingValidationError("Payload must be a JSON object")
    rulings = data.get("rulings")
    if rulings is None:
        # Single ruling as top-level object
        return [_validate_one_ruling(data, 0)]
    if not isinstance(rulings, list):
        raise RulingValidationError("rulings must be an array")
    return [_validate_one_ruling(r, i) for i, r in enumerate(rulings)]


def _derive_status(effective_at: str, status: str) -> str:
    if status and status != STATUS_CURRENT:
        return status
    if effective_at and not is_effective_now(effective_at):
        return STATUS_HISTORICAL  # future-dated treat as historical until effective
    return status or STATUS_CURRENT


def ingest_rulings_payload(
    payload: dict[str, Any],
    rules_db_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and ingest rulings into official_card_rulings. Returns summary with counts and errors."""
    rules_db_path = rules_db_path or DEFAULT_RULES_DB_PATH
    summary: dict[str, Any] = {"written": 0, "errors": []}
    try:
        normalized_list = validate_rulings_payload(payload)
    except RulingValidationError as e:
        summary["errors"].append(str(e))
        return summary
    for n in normalized_list:
        status = _derive_status(n.get("effective_at") or "", n.get("status") or STATUS_CURRENT)
        ok = insert_card_ruling(
            rules_db_path,
            ruling_id=n["ruling_id"],
            card_code=n.get("card_code") or "",
            topic_key=n.get("topic_key") or "",
            question_text=n.get("question_text") or "",
            ruling_text=n.get("ruling_text") or "",
            normalized_summary=n.get("normalized_summary") or "",
            source_id=n.get("source_id") or "",
            source_type=n.get("source_type") or RULING_SOURCE_OTHER_OFFICIAL,
            source_title=n.get("source_title") or "",
            source_url=n.get("source_url") or "",
            source_reference=n.get("source_reference") or "",
            source_anchor=n.get("source_anchor") or "",
            published_at=n.get("published_at") or "",
            effective_at=n.get("effective_at") or "",
            status=status,
            tags=n.get("tags") or "",
        )
        if ok:
            summary["written"] += 1
    return summary


def ingest_rulings_file(
    path: Path,
    rules_db_path: Path | None = None,
) -> dict[str, Any]:
    """Load JSON file, validate, ingest. Returns same summary as ingest_rulings_payload."""
    path = Path(path)
    rules_db_path = rules_db_path or DEFAULT_RULES_DB_PATH
    if not path.is_file():
        return {"written": 0, "errors": [f"File not found: {path}"]}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {"written": 0, "errors": [f"Read error: {e}"]}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return {"written": 0, "errors": [f"Invalid JSON: {e}"]}
    return ingest_rulings_payload(data, rules_db_path=rules_db_path)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Ingest staged official rulings JSON into Miru official rules DB.",
    )
    parser.add_argument("rulings_json_path", type=Path, help="Path to rulings JSON file.")
    parser.add_argument(
        "--rules-db",
        type=Path,
        default=None,
        help="Path to miru_official_rules.db",
    )
    args = parser.parse_args()
    summary = ingest_rulings_file(args.rulings_json_path, rules_db_path=args.rules_db)
    if summary["errors"]:
        for err in summary["errors"]:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"Ingested {summary['written']} ruling(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
