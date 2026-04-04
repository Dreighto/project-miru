#!/usr/bin/env python
"""Enforceable ethics and provenance gates for Miru. Observable in code and structured status.

Gates:
- No legality claims without official-source-backed record
- No publish/promotion if provenance missing or source not approved
- No automatic live fetch from snapshot-only or manual-only sources
- No high-confidence insight if evidence/confidence below threshold
- No cross-worktree/main promotion (worktree-only pass)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_PATH = PROJECT_ROOT / "data" / "miru_ethics_audit.json"

# Last blocked gate for audit (in-memory; optionally persisted).
_last_blocked: dict[str, Any] = {}


def _record_block(gate_id: str, reason: str, context: dict[str, Any] | None = None) -> None:
    global _last_blocked
    _last_blocked["gate_id"] = gate_id
    _last_blocked["reason"] = reason
    _last_blocked["context"] = context or {}
    try:
        DEFAULT_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_AUDIT_PATH.write_text(
            json.dumps({"last_blocked": _last_blocked}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_last_gate_block() -> dict[str, Any]:
    """Return last blocked gate info for audit/debug. Empty if none."""
    return dict(_last_blocked)


def load_last_gate_block_from_disk() -> dict[str, Any]:
    """Load last block from audit file if present."""
    if not DEFAULT_AUDIT_PATH.is_file():
        return {}
    try:
        data = json.loads(DEFAULT_AUDIT_PATH.read_text(encoding="utf-8"))
        return dict(data.get("last_blocked") or {})
    except Exception:
        return {}


def can_claim_legality(
    has_official_source_backed_record: bool,
    *,
    card_code: str = "",
    format_name: str = "",
) -> tuple[bool, str]:
    """
    Gate: no legality claims without official-source-backed record.
    Returns (allowed, reason). If not allowed, records block for audit.
    """
    if has_official_source_backed_record:
        return True, ""
    _record_block(
        "can_claim_legality",
        "No official-source-backed legality record; claim refused.",
        {"card_code": card_code, "format": format_name},
    )
    return False, "No official-source-backed legality record; claim refused."


def can_publish(
    has_provenance: bool,
    source_approved: bool,
    *,
    card_code: str = "",
    source_id: str = "",
) -> tuple[bool, str]:
    """
    Gate: no publish/promotion if provenance missing or source not approved.
    Returns (allowed, reason). If not allowed, records block for audit.
    """
    if not has_provenance:
        _record_block(
            "can_publish",
            "Publish refused: provenance missing.",
            {"card_code": card_code, "source_id": source_id},
        )
        return False, "Publish refused: provenance missing."
    if not source_approved:
        _record_block(
            "can_publish",
            "Publish refused: source not approved.",
            {"card_code": card_code, "source_id": source_id},
        )
        return False, "Publish refused: source not approved."
    return True, ""


def can_auto_fetch(
    fetch_mode: str,
    allowed_access: str,
    *,
    source_id: str = "",
    interaction: str = "live",
) -> tuple[bool, str]:
    """
    Gate: no automatic **live HTTP** fetch from snapshot-only or manual-only sources.

    Reading an existing on-disk snapshot file is **not** a live auto-fetch; callers that are
    about to perform only a local file read must pass ``interaction="local_file"`` (or
    ``local_snapshot``) so this gate does not record a spurious block.

    ``fetch_mode`` values ``snapshot`` and ``manual_only`` (exact) block automated URL fetches.
    Registry ``snapshot-json`` lanes are not the same as ``snapshot`` and remain eligible for
    governed URL fetch paths unless ``allowed_access`` is ``manual_only``.
    Returns (allowed, reason). If not allowed, records block for audit.
    """
    inter = (interaction or "").strip().lower()
    if inter in {"local_file", "local_snapshot", "local_disk"}:
        return True, ""
    if (fetch_mode or "").strip().lower() in ("snapshot", "manual_only"):
        _record_block(
            "can_auto_fetch",
            "Auto fetch refused: source is snapshot-only or manual-only.",
            {"source_id": source_id, "fetch_mode": fetch_mode, "interaction": interaction},
        )
        return False, "Auto fetch refused: source is snapshot-only or manual-only."
    if (allowed_access or "").strip().lower() == "manual_only":
        _record_block(
            "can_auto_fetch",
            "Auto fetch refused: source is manual-only.",
            {"source_id": source_id, "allowed_access": allowed_access, "interaction": interaction},
        )
        return False, "Auto fetch refused: source is manual-only."
    return True, ""


def can_auto_intake_discovered_source(
    *,
    source_id: str = "",
    registry_matched: bool,
    gate_action: str,
    execution_outcome: str,
    manual_approval_required: bool,
    permission_status: str = "",
) -> tuple[bool, str]:
    """
    Gate: no autonomous intake for newly discovered sources unless they already map to an
    existing governed registry lane and policy explicitly allows the execution path.
    """
    if not registry_matched:
        _record_block(
            "can_auto_intake_discovered_source",
            "Autonomous intake refused: discovered source is not already governed in Miru's registry.",
            {"source_id": source_id, "permission_status": permission_status},
        )
        return False, "Autonomous intake refused: discovered source is not already governed in Miru's registry."
    if manual_approval_required or str(gate_action or "").strip().lower() == "manual-review":
        _record_block(
            "can_auto_intake_discovered_source",
            "Autonomous intake refused: source still requires manual approval.",
            {"source_id": source_id, "gate_action": gate_action, "permission_status": permission_status},
        )
        return False, "Autonomous intake refused: source still requires manual approval."
    if str(execution_outcome or "").strip().lower() not in {"allow-learning", "allow-reference-only"}:
        _record_block(
            "can_auto_intake_discovered_source",
            "Autonomous intake refused: execution path is not policy-approved for automatic use.",
            {"source_id": source_id, "gate_action": gate_action, "execution_outcome": execution_outcome},
        )
        return False, "Autonomous intake refused: execution path is not policy-approved for automatic use."
    return True, ""


def insight_confidence_gate(
    confidence: float,
    threshold: float,
    *,
    card_id: str = "",
    insight_type: str = "",
) -> tuple[bool, str]:
    """
    Gate: no high-confidence insight if evidence/confidence below threshold.
    Returns (allowed, reason). If not allowed, records block for audit.
    """
    if confidence >= threshold:
        return True, ""
    _record_block(
        "insight_confidence_gate",
        f"Insight rejected: confidence {confidence:.2f} below threshold {threshold:.2f}.",
        {"card_id": card_id, "insight_type": insight_type, "confidence": confidence, "threshold": threshold},
    )
    return False, f"Confidence {confidence:.2f} below threshold {threshold:.2f}; insight rejected."


def no_cross_worktree_promotion(
    is_worktree_context: bool = True,
) -> tuple[bool, str]:
    """
    Gate: no cross-worktree/main promotion in this pass. Always allow when in worktree-only context.
    Returns (allowed, reason). Block only if caller explicitly indicates cross-promotion attempt.
    """
    if is_worktree_context:
        return True, ""
    _record_block("no_cross_worktree_promotion", "Cross-worktree/main promotion refused.")
    return False, "Cross-worktree/main promotion refused."


def check_publish_gate(has_provenance: bool, source_approved: bool, card_code: str = "", source_id: str = "") -> tuple[bool, str]:
    """Convenience: run can_publish and return (allowed, reason)."""
    return can_publish(has_provenance, source_approved, card_code=card_code, source_id=source_id)


def check_insight_confidence_gate(confidence: float, threshold: float, card_id: str = "", insight_type: str = "") -> tuple[bool, str]:
    """Convenience: run insight_confidence_gate and return (allowed, reason)."""
    return insight_confidence_gate(confidence, threshold, card_id=card_id, insight_type=insight_type)
