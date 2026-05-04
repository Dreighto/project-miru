"""
emit_decision.py — append a Phase 2 Judgment Trail decision record to
data/agent_decisions.jsonl.

Works from any git worktree (miru-w1, miru-w2, etc.) — always writes to the
main repo's data/ directory, not the worktree-local one.

Usage (CLI):
    python tools/emit_decision.py --input path/to/decision.json
    type decision.json | python tools/emit_decision.py

Usage (Python):
    from emit_decision import emit, DecisionValidationError
    record = emit(record_dict, log_path=Path("..."))  # log_path optional

Schema is canon. See Notion: "Project Miru Autonomy Overhaul — 4-Phase
Implementation Plan", Phase 2 section. Top-level fields are flat; concept
blocks are documented in the canon page.

The file is append-only. Never truncate, sort, or deduplicate it.

This is a standalone Phase 2 foundation. NOT wired into W2, gateway, VP Ops,
or worker dispatch paths. A future explicitly approved grading/wiring pass
will integrate this with runtime.

Authority discipline: tool_profile (free string) and authority_mode (enum)
are SEPARATE gates. tool_profile == 'full_operator' does NOT imply
authority_mode == 'canon_write_authorized'. The authority_mode enum
deliberately excludes 'full_operator' to prevent that conflation. The
validator does not cross-check the two — canon-write authority comes from
explicit operator/canon grant outside the JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution (worktree-safe; mirrors tools/emit_completion.py:74-90).
# ---------------------------------------------------------------------------


def _repo_root() -> str:
    """Return the main repo root, works from any linked worktree."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=script_dir,
            timeout=5,
        )
        if result.returncode == 0:
            common_dir = os.path.normpath(os.path.join(script_dir, result.stdout.strip()))
            return os.path.dirname(common_dir)
    except Exception:
        pass
    return os.path.dirname(script_dir)


# Trace_id format from dispatch listener spawn.js:
#   {worker}-{ticket_id}-{uuid}-{uuid}, e.g. cc-PRO-276-eaa0a242-326360d3
# (mirrors tools/emit_completion.py:50-56)
_TRACE_ID_TICKET_RE = re.compile(r"(?:^|-)([A-Z]+-\d+)(?:-|$)")


def _ticket_id_from_trace(trace_id):
    if not trace_id:
        return None
    match = _TRACE_ID_TICKET_RE.search(trace_id)
    if match:
        return match.group(1)
    return None


def _utc_iso() -> str:
    """Seconds-precision UTC ISO with Z suffix (matches production rows)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_log_path() -> Path:
    """Canonical log path. MIRU_DECISIONS_LOG_PATH env var overrides for tests."""
    override = os.environ.get("MIRU_DECISIONS_LOG_PATH", "").strip()
    if override:
        return Path(override)
    return Path(_repo_root()) / "data" / "agent_decisions.jsonl"


# ---------------------------------------------------------------------------
# Enums (closed sets — no "other" fallback).
# ---------------------------------------------------------------------------

TRIGGERS = frozenset(
    {
        "worker_selection",
        "scope_interpretation",
        "canon_interpretation",
        "risk_classification",
        "alternative_rejected",
        "confidence_claim",
        "fallback_or_retry",
        "escalation_or_non_escalation",
        "verification_interpretation",
    }
)

PROPOSED_TAGS = frozenset({"canon_mandated", "judgment_driven", "hybrid", "unknown"})

# 'full_operator' is deliberately NOT in authority_mode. tool_profile may carry
# that string (free field), but tool access and canon authority are separate
# gates. canon_write_authorized requires explicit operator/canon grant for
# the task. operator_authorized_exception is for one-off explicit operator
# approval. See module docstring.
AUTHORITY_MODES = frozenset(
    {
        "read_only",
        "limited_write",
        "operator_authorized_exception",
        "canon_write_authorized",
        "unknown",
    }
)

CONFIDENCE_LEVELS = frozenset({"low", "medium", "high", "blocked_by_missing_evidence"})

# Only 'pending_outcome' is allowed at creation. Other states are set later
# by a future approved grading pass.
GRADING_STATES = frozenset(
    {
        "pending_outcome",
        "outcome_observed",
        "graded_correct",
        "graded_partial",
        "graded_wrong",
        "ungradable",
        "superseded",
    }
)

# Anti-gaming: option strings that are content-free filler (case-insensitive).
_BLACKLIST_OPTIONS = frozenset({"do nothing", "ignore the task", "n/a", "none", ""})

# Cheap calibration filter; real quality check happens at grading time.
_MIN_MEANINGFUL_LENGTH = 20


# ---------------------------------------------------------------------------
# Validation error type — carries every collected error.
# ---------------------------------------------------------------------------


class DecisionValidationError(ValueError):
    """Raised when a decision record fails validation. .errors is the full list."""

    def __init__(self, errors):
        self.errors = list(errors)
        formatted = "\n".join(f"[decision-validation] {e}" for e in self.errors)
        super().__init__(formatted)


# ---------------------------------------------------------------------------
# Required schema (used by the structural pass).
# ---------------------------------------------------------------------------

_REQUIRED_STRING_FIELDS = (
    "decision_id",
    "trace_id",
    "ticket_id",
    "worker",
    "created_at",
    "proposed_tag",
    "tool_profile",
    "authority_mode",
    "trigger",
    "decision_type",
    "decision",
    "confidence",
    "confidence_reason",
    "would_change_mind_if",
    "expected_outcome",
)

_REQUIRED_LIST_FIELDS = (
    "classification_history",
    "canon_refs",
    "evidence_refs",
    "context_used",
    "alternatives_considered",
    "assumptions",
    "constraints",
    "known_uncertainties",
    "outcome_evidence_refs",
    "verification_limitations",
)

_NULL_AT_CREATION_FIELDS = (
    "final_tag",
    "actual_outcome",
    "outcome_status",
    "graded_by",
    "graded_at",
)


# ---------------------------------------------------------------------------
# Identity auto-fill and structural defaults.
# ---------------------------------------------------------------------------


def _autofill_identity(record):
    """Fill identity defaults from env / generators."""
    record = dict(record)

    if not record.get("decision_id"):
        record["decision_id"] = uuid.uuid4().hex
    if not record.get("created_at"):
        record["created_at"] = _utc_iso()

    env_trace = os.environ.get("MIRU_TRACE_ID", "").strip()
    if env_trace and not record.get("trace_id"):
        record["trace_id"] = env_trace

    if not record.get("ticket_id"):
        inferred = _ticket_id_from_trace(record.get("trace_id"))
        if inferred:
            record["ticket_id"] = inferred

    return record


def _normalize_defaults(record):
    """Fill structural defaults: empty lists, null-at-creation fields, grading_state."""
    record = dict(record)

    for field in _REQUIRED_LIST_FIELDS:
        if field not in record or record[field] is None:
            record[field] = []

    for field in _NULL_AT_CREATION_FIELDS:
        if field not in record:
            record[field] = None

    if not record.get("grading_state"):
        record["grading_state"] = "pending_outcome"

    return record


# ---------------------------------------------------------------------------
# Pass 1: structural validation. If errors here, semantic pass is skipped —
# no point checking enum values when the field is the wrong type.
# ---------------------------------------------------------------------------


def _validate_structural(record):
    errors = []

    if not isinstance(record, dict):
        return ["record must be a JSON object"]

    for field in _REQUIRED_STRING_FIELDS:
        if field not in record:
            errors.append(f"missing required field: {field!r}")
        elif record[field] is None:
            errors.append(f"required field {field!r} is null; provide a value")
        elif not isinstance(record[field], str):
            errors.append(f"field {field!r} must be a string, got {type(record[field]).__name__}")

    for field in _REQUIRED_LIST_FIELDS:
        if field in record and record[field] is not None and not isinstance(record[field], list):
            errors.append(f"field {field!r} must be a list, got {type(record[field]).__name__}")

    if isinstance(record.get("alternatives_considered"), list):
        for i, item in enumerate(record["alternatives_considered"]):
            if not isinstance(item, dict):
                errors.append(
                    f"alternatives_considered[{i}] must be an object, " f"got {type(item).__name__}"
                )

    return errors


# ---------------------------------------------------------------------------
# Pass 2: semantic validators. Each is a pure function (record) -> list[str].
# Add new rules by appending to _SEMANTIC_VALIDATORS.
# ---------------------------------------------------------------------------


def _validate_classification(record):
    errors = []

    proposed = record.get("proposed_tag")
    if proposed not in PROPOSED_TAGS:
        errors.append(f"proposed_tag must be one of {sorted(PROPOSED_TAGS)}, got {proposed!r}")

    if record.get("final_tag") is not None:
        errors.append(
            "final_tag must be null at creation time; only W2/gateway/VP Ops/"
            "reviewer/operator may finalize classification after outcome verification"
        )

    history = record.get("classification_history", [])
    if isinstance(history, list) and len(history) > 0:
        errors.append(
            "classification_history must be empty ([]) at creation time; "
            "history is appended only when classification is updated post-creation"
        )

    return errors


def _validate_authority(record):
    errors = []

    mode = record.get("authority_mode")
    if mode not in AUTHORITY_MODES:
        errors.append(
            f"authority_mode must be one of {sorted(AUTHORITY_MODES)}, got {mode!r}. "
            "'full_operator' is deliberately NOT a valid authority_mode — tool "
            "access and canon authority are separate gates. Use canon_write_authorized "
            "for explicit canon-write grants, operator_authorized_exception for one-off."
        )

    profile = record.get("tool_profile")
    if not isinstance(profile, str) or not profile.strip():
        errors.append(
            "tool_profile must be a non-empty string (free field, e.g. "
            "'standard_worker', 'drift_executor', 'full_operator')"
        )

    if mode == "unknown":
        confidence = record.get("confidence")
        if confidence not in {"low", "blocked_by_missing_evidence"}:
            errors.append(
                "authority_mode 'unknown' is allowed only when confidence is 'low' "
                "or 'blocked_by_missing_evidence'; if you do not know your authority, "
                "you do not yet have grounds for a confident decision"
            )

    return errors


def _validate_decision_summary(record):
    errors = []

    trigger = record.get("trigger")
    if trigger not in TRIGGERS:
        errors.append(
            f"trigger must be one of {sorted(TRIGGERS)}, got {trigger!r}. "
            "There is no 'other' fallback in Phase 2 — pick the closest match or "
            "escalate to operator if none fits."
        )

    decision = record.get("decision", "")
    if isinstance(decision, str) and not decision.strip():
        errors.append("decision must be a non-empty narrative describing what was decided")

    return errors


def _validate_calibration(record):
    errors = []

    confidence = record.get("confidence")
    if confidence not in CONFIDENCE_LEVELS:
        errors.append(f"confidence must be one of {sorted(CONFIDENCE_LEVELS)}, got {confidence!r}")

    reason = record.get("confidence_reason", "")
    if isinstance(reason, str) and not reason.strip():
        errors.append(
            "confidence_reason must be a non-empty explanation; confidence "
            "without reason is not calibration data"
        )

    wcmf = record.get("would_change_mind_if", "")
    if isinstance(wcmf, str):
        stripped = wcmf.strip()
        if not stripped:
            errors.append("would_change_mind_if must be a non-empty meaningful condition")
        elif len(stripped) < _MIN_MEANINGFUL_LENGTH:
            errors.append(
                f"would_change_mind_if is too short ({len(stripped)} chars, need "
                f">= {_MIN_MEANINGFUL_LENGTH}); state a specific, falsifiable "
                "condition. Real calibration quality is graded post-outcome; this "
                "length floor is a deliberately permissive cheap filter."
            )

    if confidence == "high" and (not isinstance(wcmf, str) or not wcmf.strip()):
        errors.append(
            "high confidence requires would_change_mind_if; confidence is "
            "calibration metadata, not proof, and high-confidence claims must "
            "name the condition that would falsify them"
        )

    return errors


def _validate_alternatives(record):
    errors = []

    alts = record.get("alternatives_considered", [])
    if not isinstance(alts, list):
        return errors

    trigger = record.get("trigger")
    if trigger == "alternative_rejected" and len(alts) == 0:
        errors.append(
            "trigger 'alternative_rejected' requires at least one entry in "
            "alternatives_considered describing the alternative that was rejected"
        )

    for i, alt in enumerate(alts):
        if not isinstance(alt, dict):
            continue

        for sub in ("option", "why_plausible", "rejected_because"):
            value = alt.get(sub)
            if not isinstance(value, str):
                errors.append(f"alternatives_considered[{i}].{sub} must be a non-empty string")
                continue
            stripped = value.strip()
            if not stripped:
                errors.append(f"alternatives_considered[{i}].{sub} is empty; provide a real value")
                continue
            if (
                sub in ("why_plausible", "rejected_because")
                and len(stripped) < _MIN_MEANINGFUL_LENGTH
            ):
                errors.append(
                    f"alternatives_considered[{i}].{sub} is too short "
                    f"({len(stripped)} chars, need >= {_MIN_MEANINGFUL_LENGTH}); "
                    "explain genuinely, not in placeholder language"
                )

        option = alt.get("option")
        if isinstance(option, str) and option.strip().lower() in _BLACKLIST_OPTIONS:
            errors.append(
                f"alternatives_considered[{i}].option {option!r} is content-free "
                "filler; provide a real alternative that was actually plausible, "
                "or remove this entry"
            )

    return errors


def _validate_outcome(record):
    errors = []

    expected = record.get("expected_outcome", "")
    if isinstance(expected, str) and not expected.strip():
        errors.append("expected_outcome must be a non-empty description of what success looks like")

    return errors


def _validate_grading(record):
    """Reject any attempt to set final grading at creation time."""
    errors = []

    for field in ("actual_outcome", "outcome_status", "graded_by", "graded_at"):
        if record.get(field) is not None:
            errors.append(
                f"{field} must be null at creation time; final grading is forbidden "
                "until outcome evidence is verified by a future approved grading pass"
            )

    state = record.get("grading_state")
    if state is None:
        errors.append("grading_state must be 'pending_outcome' at creation time")
    elif state != "pending_outcome":
        if state in GRADING_STATES:
            errors.append(
                f"grading_state must be 'pending_outcome' at creation time; "
                f"got {state!r}. Other states are set later by a future approved "
                "grading pass."
            )
        else:
            errors.append(f"grading_state must be one of {sorted(GRADING_STATES)}, got {state!r}")

    return errors


def _validate_conditional_rules(record):
    """Cross-block conditional rules from the Phase 2 spec."""
    errors = []

    proposed = record.get("proposed_tag")
    canon_refs = record.get("canon_refs") or []

    if proposed in {"canon_mandated", "hybrid"} and len(canon_refs) == 0:
        errors.append(
            f"proposed_tag {proposed!r} requires canon_refs to be non-empty; "
            "if a decision is canon-mandated or hybrid, name the canon source(s) "
            "that mandate it"
        )

    if proposed == "judgment_driven":
        assumptions = record.get("assumptions") or []
        uncertainties = record.get("known_uncertainties") or []
        if len(assumptions) == 0 and len(uncertainties) == 0:
            errors.append(
                "proposed_tag 'judgment_driven' requires at least one of "
                "assumptions or known_uncertainties to be non-empty; judgment "
                "without stated assumptions or uncertainties is just an assertion"
            )

    return errors


_SEMANTIC_VALIDATORS = (
    _validate_classification,
    _validate_authority,
    _validate_decision_summary,
    _validate_calibration,
    _validate_alternatives,
    _validate_outcome,
    _validate_grading,
    _validate_conditional_rules,
)


# ---------------------------------------------------------------------------
# Public emit() — validate, normalize, append.
# ---------------------------------------------------------------------------


def _validate_and_normalize(record):
    """Two-pass validation + normalization. Raises DecisionValidationError."""
    if not isinstance(record, dict):
        raise DecisionValidationError(["record must be a JSON object"])

    record = _autofill_identity(record)
    record = _normalize_defaults(record)

    structural_errors = _validate_structural(record)
    if structural_errors:
        raise DecisionValidationError(structural_errors)

    semantic_errors = []
    for validator in _SEMANTIC_VALIDATORS:
        semantic_errors.extend(validator(record))
    if semantic_errors:
        raise DecisionValidationError(semantic_errors)

    return record


def emit(record, *, log_path=None):
    """Validate, normalize, and append a decision record. Returns canonicalized record."""
    record = _validate_and_normalize(record)

    if log_path is None:
        log_path = _default_log_path()
    log_path = Path(log_path)

    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    return record


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Append a Phase 2 Judgment Trail decision record.")
    parser.add_argument(
        "--input",
        dest="input_path",
        default=None,
        help="Path to a JSON file containing the decision record. If omitted, reads stdin.",
    )
    args = parser.parse_args()

    if args.input_path:
        try:
            raw = Path(args.input_path).read_text(encoding="utf-8")
        except OSError as e:
            print(f"[decision-validation] could not read input file: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        raw = sys.stdin.read()

    raw = raw.strip()
    if not raw:
        print("[decision-validation] no JSON received on stdin", file=sys.stderr)
        sys.exit(2)

    try:
        record = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[decision-validation] invalid JSON: {e}", file=sys.stderr)
        sys.exit(2)

    try:
        normalized = emit(record)
    except DecisionValidationError as e:
        for line in str(e).splitlines():
            print(line, file=sys.stderr)
        sys.exit(1)

    log_path = _default_log_path()
    print(f"[emit_decision] written to {log_path}", file=sys.stderr)
    print(normalized["decision_id"])


if __name__ == "__main__":
    main()
