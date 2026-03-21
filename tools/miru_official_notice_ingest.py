#!/usr/bin/env python
"""Staged intake for real official Bandai rules/banlist/block-rotation notices (JSON).

Worktree-only, no scraping. Validates schema, then populates:
- official_rule_notices
- official_legality_history (from affected_cards)
- official_format_context (from format_context when present)

Schema (see README_official_notice_staging.md):
  notice_id, title, source_id (required)
  source_url?, source_reference?, region?, format_name?, notice_type?, published_at?, effective_at?, status?, summary?
  affected_cards?: [ { card_code, legality_state [, effective_at, notes ] } ]
  format_context?: { block_rotation_active?, effective_at?, notes? }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from tools.miru_official_rules import (
    DEFAULT_RULES_DB_PATH,
    NOTICE_TYPES,
    STATUS_CURRENT,
    STATUS_UPCOMING,
    STATUS_HISTORICAL,
    STATUS_SUPERSEDED,
    is_effective_now,
    insert_rule_notice,
    insert_format_context,
    ingest_legality_row,
)
from tools.miru_regulation import LEGALITY_STATES


def _normalize_legality_state(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s in LEGALITY_STATES:
        return s
    if s in ("ok", "playable", "allowed"):
        return "legal"
    if s in ("ban", "banned"):
        return "banned"
    if s in ("restrict", "limited"):
        return "restricted"
    if s in ("rotate", "rotated", "out"):
        return "rotated"
    return "unknown"


# ---------------------------------------------------------------------------
# Schema and validation
# ---------------------------------------------------------------------------

REQUIRED_TOP_LEVEL = ("notice_id", "title", "source_id")
OPTIONAL_TOP_LEVEL = (
    "source_url",
    "source_reference",
    "region",
    "format_name",
    "notice_type",
    "published_at",
    "effective_at",
    "status",
    "summary",
    "affected_cards",
    "format_context",
)
VALID_STATUSES = (STATUS_CURRENT, STATUS_UPCOMING, STATUS_HISTORICAL, STATUS_SUPERSEDED)


class NoticeValidationError(ValueError):
    """Raised when a notice JSON fails validation."""

    pass


def _validate_string(value: Any, name: str, allow_empty: bool = False) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        s = ""
    else:
        s = str(value).strip()
    if not allow_empty and not s:
        raise NoticeValidationError(f"Missing or empty required field: {name}")
    return s


def _validate_affected_card(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise NoticeValidationError(f"affected_cards[{index}] must be an object")
    card_code = _validate_string(item.get("card_code"), f"affected_cards[{index}].card_code")
    legality = _validate_string(item.get("legality_state"), f"affected_cards[{index}].legality_state")
    return {
        "card_code": card_code.upper(),
        "legality_state": _normalize_legality_state(legality),
        "effective_at": str(item.get("effective_at") or "").strip(),
        "notes": str(item.get("notes") or "").strip(),
    }


def _validate_format_context(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise NoticeValidationError("format_context must be an object")
    return {
        "block_rotation_active": bool(obj.get("block_rotation_active")),
        "effective_at": str(obj.get("effective_at") or "").strip(),
        "notes": str(obj.get("notes") or "").strip(),
    }


def validate_notice_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a notice payload. Raises NoticeValidationError on failure."""
    if not isinstance(data, dict):
        raise NoticeValidationError("Payload must be a JSON object")

    out: dict[str, Any] = {}
    for key in REQUIRED_TOP_LEVEL:
        out[key] = _validate_string(data.get(key), key)

    for key in OPTIONAL_TOP_LEVEL:
        val = data.get(key)
        if key == "notice_type":
            raw = str(val or "other").strip().lower()
            if raw not in NOTICE_TYPES:
                raise NoticeValidationError(f"notice_type must be one of {NOTICE_TYPES}; got {raw!r}")
            out[key] = raw
        elif key == "status":
            raw = str(val or "").strip().lower()
            if raw and raw not in VALID_STATUSES:
                raise NoticeValidationError(f"status must be one of {VALID_STATUSES}; got {raw!r}")
            out[key] = raw or STATUS_CURRENT
        elif key == "affected_cards":
            if val is None:
                out[key] = []
            elif isinstance(val, list):
                out[key] = [_validate_affected_card(v, i) for i, v in enumerate(val)]
            else:
                raise NoticeValidationError("affected_cards must be an array")
        elif key == "format_context":
            if val is None:
                out[key] = None
            else:
                out[key] = _validate_format_context(val)
        else:
            out[key] = str(val or "").strip() if val is not None else ""

    if "format_name" not in out or not out["format_name"]:
        out["format_name"] = "standard"
    if "region" not in out:
        out["region"] = ""
    return out


def _derive_status(effective_at: str, status: str) -> str:
    """If status is current/empty and effective_at is in the future, return upcoming."""
    if status and status != STATUS_CURRENT:
        return status
    if effective_at and not is_effective_now(effective_at):
        return STATUS_UPCOMING
    return status or STATUS_CURRENT


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

def ingest_notice_payload(
    payload: dict[str, Any],
    rules_db_path: Path | None = None,
) -> dict[str, Any]:
    """Validate and ingest one notice: notices table, affected_cards -> legality_history, format_context -> format_context.
    Returns summary dict with notice_id, notices_written, legality_written, format_context_written, errors (list).
    """
    rules_db_path = rules_db_path or DEFAULT_RULES_DB_PATH
    summary: dict[str, Any] = {
        "notice_id": "",
        "notices_written": 0,
        "legality_written": 0,
        "format_context_written": 0,
        "errors": [],
    }
    try:
        normalized = validate_notice_payload(payload)
    except NoticeValidationError as e:
        summary["errors"].append(str(e))
        return summary

    nid = normalized["notice_id"]
    summary["notice_id"] = nid
    effective_at = normalized.get("effective_at") or ""
    status = _derive_status(effective_at, normalized.get("status") or STATUS_CURRENT)

    # 1) official_rule_notices
    payload_json = json.dumps(payload)
    ok = insert_rule_notice(
        rules_db_path,
        nid,
        title=normalized["title"],
        source_id=normalized["source_id"],
        source_url=normalized.get("source_url") or "",
        source_reference=normalized.get("source_reference") or "",
        region=normalized.get("region") or "",
        format_name=normalized.get("format_name") or "standard",
        notice_type=normalized.get("notice_type") or "other",
        published_at=normalized.get("published_at") or "",
        effective_at=effective_at,
        status=status,
        summary=normalized.get("summary") or "",
        payload_json=payload_json,
    )
    if ok:
        summary["notices_written"] = 1

    # 2) affected_cards -> official_legality_history (stored only in rules DB; catalog not updated here)
    format_name = normalized.get("format_name") or "standard"
    region = normalized.get("region") or ""
    source_id = normalized["source_id"]
    source_ref = normalized.get("source_reference") or ""
    for card in normalized.get("affected_cards") or []:
        eff = card.get("effective_at") or effective_at
        ok = ingest_legality_row(
            rules_db_path,
            card["card_code"],
            format_name,
            card["legality_state"],
            region=region,
            effective_date=eff,
            source_id=source_id,
            source_reference=source_ref,
            notice_id=nid,
            notes=card.get("notes") or "",
        )
        if ok:
            summary["legality_written"] += 1

    # 3) format_context -> official_format_context
    fc = normalized.get("format_context")
    if fc:
        ok = insert_format_context(
            rules_db_path,
            region=region,
            format_name=format_name,
            block_rotation_active=1 if fc.get("block_rotation_active") else 0,
            effective_at=fc.get("effective_at") or effective_at,
            source_id=source_id,
            source_reference=source_ref,
            notice_id=nid,
            notes=fc.get("notes") or "",
        )
        if ok:
            summary["format_context_written"] = 1

    return summary


def ingest_notice_file(
    path: Path,
    rules_db_path: Path | None = None,
) -> dict[str, Any]:
    """Load JSON file, validate, ingest. Returns same summary as ingest_notice_payload."""
    path = Path(path)
    rules_db_path = rules_db_path or DEFAULT_RULES_DB_PATH
    if not path.is_file():
        return {
            "notice_id": "",
            "notices_written": 0,
            "legality_written": 0,
            "format_context_written": 0,
            "errors": [f"File not found: {path}"],
        }
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        return {
            "notice_id": "",
            "notices_written": 0,
            "legality_written": 0,
            "format_context_written": 0,
            "errors": [f"Read error: {e}"],
        }
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return {
            "notice_id": "",
            "notices_written": 0,
            "legality_written": 0,
            "format_context_written": 0,
            "errors": [f"Invalid JSON: {e}"],
        }
    return ingest_notice_payload(data, rules_db_path=rules_db_path or DEFAULT_RULES_DB_PATH)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Ingest a staged official notice JSON file into Miru official rules DB.",
    )
    parser.add_argument("notice_json_path", type=Path, help="Path to notice JSON file.")
    parser.add_argument(
        "--rules-db",
        type=Path,
        default=None,
        help="Path to miru_official_rules.db (default: data/miru_official_rules.db)",
    )
    args = parser.parse_args()
    summary = ingest_notice_file(args.notice_json_path, rules_db_path=args.rules_db or DEFAULT_RULES_DB_PATH)
    if summary["errors"]:
        for err in summary["errors"]:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(f"Ingested notice {summary['notice_id']}: notices={summary['notices_written']}, legality_rows={summary['legality_written']}, format_context={summary['format_context_written']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
