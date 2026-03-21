"""
Persistent operator acknowledgement for Miru AI /dev handoff prompts (port 18765).

Self-report fields (primary limitation, blockers, etc.) can stay "urgent" after a worker
finishes a scoped task. This module stores a SHA-256 fingerprint of the actionable need;
when it matches the live fingerprint, build_operator_handoff_payload treats the handoff as
resolved until the underlying signature changes.

Read/write JSON under data/miru_operator_handoff_resolution.json (local operator state).
Does not affect Project Miru, governance, or publication rules.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESOLUTION_PATH = ROOT / "data" / "miru_operator_handoff_resolution.json"


def _round_metric(val: Any, places: int = 2) -> Any:
    if val is None:
        return None
    try:
        return round(float(val), places)
    except (TypeError, ValueError):
        return None


def compute_operator_handoff_need_fingerprint(
    operator_self_report: dict[str, Any],
    issues: dict[str, Any],
) -> str:
    """
    Stable fingerprint of inputs that drive an *urgent* handoff (must match
    build_operator_handoff_payload urgency + prompt facts).

    Returns "" when self-report is unusable (error state); callers should not treat
    resolution as authoritative in that case.
    """
    osr = operator_self_report or {}
    if osr.get("error"):
        return ""
    intel = osr.get("intelligence_surface") or {}
    met = osr.get("metrics") or {}
    miru = (issues or {}).get("miru_ai") or {}
    tone = str(miru.get("tone") or "good").strip().lower()
    parts = {
        "capability_level": str(osr.get("capability_level") or "").strip(),
        "primary_limitation_code": str(intel.get("primary_limitation_code") or "").strip(),
        "primary_limitation_human": str(intel.get("primary_limitation_human") or "").strip(),
        "top_blocker": str(osr.get("top_blocker") or "").strip(),
        "next_priority": str(osr.get("next_priority") or "").strip(),
        "recommended_next_operator_action": str(intel.get("recommended_next_operator_action") or "").strip(),
        "miru_ai_issue_tone": tone,
        "coverage_pct": _round_metric(met.get("coverage_pct")),
        "publication_cards_pending_review": met.get("publication_cards_pending_review"),
        "cards_with_any_insight": met.get("cards_with_any_insight"),
        "cards_with_strong_insight": met.get("cards_with_strong_insight"),
    }
    canonical = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_operator_handoff_resolution() -> dict[str, Any]:
    if not RESOLUTION_PATH.is_file():
        return {}
    try:
        data = json.loads(RESOLUTION_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_operator_handoff_resolution(fingerprint: str, *, note: str = "") -> dict[str, Any]:
    """Write acknowledgement for the given need fingerprint (overwrites prior file)."""
    RESOLUTION_PATH.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body: dict[str, Any] = {
        "schema_version": 1,
        "resolved_fingerprint": str(fingerprint),
        "resolved_at": now,
        "note": str(note or "")[:2000],
    }
    RESOLUTION_PATH.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return body


def clear_operator_handoff_resolution() -> None:
    if RESOLUTION_PATH.is_file():
        try:
            RESOLUTION_PATH.unlink()
        except OSError:
            pass


def is_operator_handoff_acknowledged_for_fingerprint(fingerprint: str) -> tuple[bool, dict[str, Any]]:
    if not fingerprint:
        return False, {}
    state = load_operator_handoff_resolution()
    rfp = str(state.get("resolved_fingerprint") or "")
    if rfp and rfp == fingerprint:
        return True, state
    return False, {}
