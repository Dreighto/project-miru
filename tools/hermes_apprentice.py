"""
hermes_apprentice.py — Differential analysis bridge for Hermes learning (PRO-312).

Joins data/routing_history.jsonl with data/pending_callbacks.jsonl to produce
structured learning cases in data/hermes_learning_cases.jsonl.

Each learning case captures:
  - W2's routing proposal (worker, confidence, risk, extracted signals)
  - The operator's decision (approve / override / triage / no_decision)
  - A learning_signal label for Hermes to train on

Usage:
    python tools/hermes_apprentice.py [OPTIONS]

Options:
    --since YYYY-MM-DD      Include only decisions on or after this date
    --ticket PRO-NNN        Include only a specific ticket
    --overrides-only        Only emit override/triage cases (exclude confirmed + no_decision)
    --dry-run               Print cases to stdout instead of appending to output file
    --output PATH           Override output path (default: data/hermes_learning_cases.jsonl)
    --routing-history PATH  Override routing_history.jsonl path
    --callbacks PATH        Override pending_callbacks.jsonl path
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Action code → worker name mapping (from pending_callbacks button labels)
# ---------------------------------------------------------------------------

_ACTION_TO_WORKER: dict[str, str | None] = {
    "a": None,  # Approve — actual worker = proposed worker
    "c": "claude-code",
    "u": "cursor",
    "x": "codex",
    "g": "gemini",
    "o": None,  # Generic Override — actual worker unknown
    "t": None,  # Triage
    "T": None,  # Triage (alternate)
    "r": None,  # Request Revision
}

_OVERRIDE_ACTIONS = frozenset({"o", "c", "u", "x", "g"})
_TRIAGE_ACTIONS = frozenset({"t", "T", "r"})
_APPROVE_ACTION = "a"


# ---------------------------------------------------------------------------
# Path resolution — mirrors emit_completion.py exactly
# ---------------------------------------------------------------------------


def _repo_root() -> str:
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


def _default_path(filename: str) -> str:
    return os.path.join(_repo_root(), "data", filename)


# ---------------------------------------------------------------------------
# Quality extraction (inline — mirrors hermes_extract_test_quality.py)
# ---------------------------------------------------------------------------

_NN_PATTERN = re.compile(r"(\d+)\s*/\s*(\d+)")


def _parse_test_evidence(raw: str) -> dict[str, Any]:
    """Extract structured quality data from test_evidence string."""
    if not raw or raw.strip().lower() in ("", "null"):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "freetext",
        }

    raw = raw.strip()
    m = _NN_PATTERN.search(raw)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        if total > 0 and passed <= total:
            return {
                "test_passed": passed,
                "test_total": total,
                "test_pass_rate": round(passed / total, 4),
                "evidence_tier": "nn_regex",
            }
        # Nonsensical ratio (passed > total, e.g. ticket ref) — fall through

    lower = raw.lower()
    if raw.startswith("ci_only:") or any(
        kw in lower
        for kw in (
            "pre-commit",
            "hygiene",
            "bugbot",
            "ci pass",
            "ci green",
            "green",
            "lint",
            "eslint",
            "ruff",
        )
    ):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "ci_binary",
        }

    if raw == "no_tests" or any(
        kw in lower
        for kw in (
            "no test",
            "no_test",
            "behavioral",
            "rule only",
            "n/a",
            "not applicable",
            "no code change",
        )
    ):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "no_tests",
        }

    return {
        "test_passed": None,
        "test_total": None,
        "test_pass_rate": None,
        "evidence_tier": "freetext",
    }


# ---------------------------------------------------------------------------
# JSONL loaders
# ---------------------------------------------------------------------------


def load_routing_history(path: str) -> dict[str, dict[str, Any]]:
    """Load routing_history.jsonl, deduplicating by task_identifier.

    When a ticket appears multiple times (W2 writes multiple rows per cycle),
    the row with the latest timestamp wins.
    """
    latest: dict[str, dict[str, Any]] = {}
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"[hermes_apprentice] warning: bad JSON on line {lineno} of {path}",
                    file=sys.stderr,
                )
                continue

            ticket = row.get("task_identifier")
            if not ticket:
                continue

            ts = row.get("timestamp", "")
            existing = latest.get(ticket)
            if existing is None or ts > existing.get("timestamp", ""):
                latest[ticket] = row

    return latest


def load_pending_callbacks(path: str) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    """Load pending_callbacks.jsonl.

    Returns:
        intent_map:  ticket_id → list of intent rows (field: issue_identifier)
        decided_map: ticket_id → latest decided row (field: task_identifier)
    """
    intent_map: dict[str, list[dict]] = {}
    decided_map: dict[str, dict] = {}

    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(
                    f"[hermes_apprentice] warning: bad JSON on line {lineno} of {path}",
                    file=sys.stderr,
                )
                continue

            kind = row.get("kind")
            if kind == "intent":
                ticket = row.get("issue_identifier") or row.get("task_identifier")
                if ticket:
                    intent_map.setdefault(ticket, []).append(row)
            elif kind == "decided":
                ticket = row.get("task_identifier") or row.get("issue_identifier")
                if ticket:
                    # Keep the latest decided row per ticket
                    ts = row.get("decided_at", "")
                    existing = decided_map.get(ticket)
                    if existing is None or ts > existing.get("decided_at", ""):
                        decided_map[ticket] = row

    return intent_map, decided_map


def load_completion_log(path: str) -> dict[str, dict[str, Any]]:
    """Load cc_completion_log.jsonl, keyed by ticket_id (last entry wins)."""
    latest: dict[str, dict[str, Any]] = {}
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ticket = row.get("ticket_id")
            if not ticket:
                continue
            ts = row.get("timestamp", "")
            existing = latest.get(ticket)
            if existing is None or ts > existing.get("timestamp", ""):
                latest[ticket] = row
    return latest


def load_vp_ops_supervision(path: str) -> dict[str, dict[str, Any]]:
    """Load vp_ops_supervision.jsonl, keyed by ticket_id (last entry wins)."""
    latest: dict[str, dict[str, Any]] = {}
    if not os.path.exists(path):
        return latest
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ticket = row.get("ticket_id")
            if not ticket:
                continue
            ts = row.get("ts", "")
            existing = latest.get(ticket)
            if existing is None or ts > existing.get("ts", ""):
                latest[ticket] = row
    return latest


# ---------------------------------------------------------------------------
# Learning case construction
# ---------------------------------------------------------------------------


def _classify_signal(decided_row: dict | None, rh_row: dict) -> str:
    """Return a learning_signal label for this case."""
    if decided_row is None:
        return "no_decision"
    action = decided_row.get("action", "")
    if action == _APPROVE_ACTION:
        return "confirmed"
    if action in _OVERRIDE_ACTIONS:
        return "override"
    if action in _TRIAGE_ACTIONS:
        return "triage"
    return "no_decision"


def _actual_worker(decided_row: dict | None, proposed_worker: str | None) -> str | None:
    """Derive the worker that was ultimately dispatched."""
    if decided_row is None:
        return None
    action = decided_row.get("action", "")
    if action == _APPROVE_ACTION:
        return proposed_worker
    mapped = _ACTION_TO_WORKER.get(action)
    return mapped  # None for generic override / triage (worker unknown)


def _case_id(ticket: str, trace_id: str | None) -> str:
    seed = f"{ticket}:{trace_id or ''}"
    digest = hashlib.sha1(seed.encode()).hexdigest()[:8]
    return f"hlc-{ticket}-{digest}"


def build_case(
    rh_row: dict,
    intent_rows: list[dict],
    decided_row: dict | None,
    ticket_description: str | None = None,
    completion_row: dict | None = None,
    vp_ops_row: dict | None = None,
) -> dict:
    """Build one learning case dict from joined rows."""
    ticket = rh_row.get("task_identifier") or rh_row.get("task_id") or "unknown"
    proposed_worker = rh_row.get("chosen_worker")
    signals = rh_row.get("extracted_signals") or {}
    trace_id = rh_row.get("trace_id")

    signal = _classify_signal(decided_row, rh_row)
    actual = _actual_worker(decided_row, proposed_worker)

    operator_decision: dict | None = None
    if decided_row is not None:
        operator_decision = {
            "action": decided_row.get("action"),
            "action_label": decided_row.get("action_label"),
            "decided_at": decided_row.get("decided_at"),
            "actual_worker": actual,
        }

    # Intent row with matching trace_id is the most precise proposal context.
    # Fall back to any intent row for the ticket.
    matching_intent: dict | None = None
    for ir in intent_rows:
        if ir.get("trace_id") == trace_id:
            matching_intent = ir
            break
    if matching_intent is None and intent_rows:
        matching_intent = intent_rows[-1]  # latest by insertion order

    # Work outcome — from cc_completion_log.jsonl
    work_outcome: dict | None = None
    if completion_row is not None:
        te_raw = completion_row.get("test_evidence", "")
        te_parsed = _parse_test_evidence(te_raw)
        work_outcome = {
            "status": completion_row.get("status"),
            "timestamp": completion_row.get("timestamp"),
            "test_passed": te_parsed["test_passed"],
            "test_total": te_parsed["test_total"],
            "test_pass_rate": te_parsed["test_pass_rate"],
            "evidence_tier": te_parsed["evidence_tier"],
            "pr_number": completion_row.get("pr_number"),
            "files_touched": completion_row.get("files_touched") or [],
        }

    # VP Ops verdict — from vp_ops_supervision.jsonl
    verification: dict | None = None
    if vp_ops_row is not None:
        verification = {
            "verdict": vp_ops_row.get("verdict"),
            "ts": vp_ops_row.get("ts"),
            "flags": vp_ops_row.get("flags") or [],
            "test_pass_rate": (vp_ops_row.get("checks") or {}).get("test_pass_rate"),
            "test_evidence_tier": (vp_ops_row.get("checks") or {}).get("test_evidence_tier"),
        }

    return {
        "case_id": _case_id(ticket, trace_id),
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "ticket_id": ticket,
        "routing_proposal": {
            "trace_id": trace_id,
            "timestamp": rh_row.get("timestamp"),
            "chosen_worker": proposed_worker,
            "confidence": rh_row.get("confidence"),
            "risk": rh_row.get("risk"),
            "task_type": signals.get("task_type"),
            "surface_keywords": signals.get("surface_keywords") or [],
            "ranked_candidates": rh_row.get("ranked_candidates") or [],
            "proposed_model_version": rh_row.get("proposed_model_version"),
            "w2_workflow_version": rh_row.get("w2_workflow_version"),
            "operator_override_flag": rh_row.get("operator_override_flag", False),
        },
        "operator_decision": operator_decision,
        "work_outcome": work_outcome,
        "verification": verification,
        "learning_signal": signal,
        "delta": {
            "was_override": signal == "override",
            "proposed_worker": proposed_worker,
            "actual_worker": actual,
            "confidence_at_decision": rh_row.get("confidence"),
        },
        "ticket_description": ticket_description,
    }


# ---------------------------------------------------------------------------
# Linear ticket description fetch (optional — requires LINEAR_API_KEY)
# ---------------------------------------------------------------------------


def _fetch_ticket_description(ticket_id: str, api_key: str) -> str | None:
    try:
        import urllib.request

        query = json.dumps(
            {
                "query": """
                query($id: String!) {
                  issue(id: $id) { description }
                }
                """,
                "variables": {"id": ticket_id},
            }
        )
        req = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=query.encode(),
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
        desc = body.get("data", {}).get("issue", {}).get("description")
        return desc[:2000] if desc else None
    except Exception as exc:
        print(
            f"[hermes_apprentice] warning: could not fetch {ticket_id} from Linear — {exc}",
            file=sys.stderr,
        )
        return None


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


def run(
    routing_history_path: str,
    callbacks_path: str,
    output_path: str,
    since: str | None = None,
    ticket_filter: str | None = None,
    overrides_only: bool = False,
    dry_run: bool = False,
    linear_api_key: str | None = None,
    completion_log_path: str | None = None,
    supervision_path: str | None = None,
) -> int:
    """Run the differential analysis and write learning cases.

    Returns the number of cases emitted.
    """
    rh_map = load_routing_history(routing_history_path)
    intent_map, decided_map = load_pending_callbacks(callbacks_path)

    # Load work-outcome and verification data (new joins)
    cl_path = completion_log_path or _default_path("cc_completion_log.jsonl")
    sv_path = supervision_path or _default_path("vp_ops_supervision.jsonl")
    completion_map = load_completion_log(cl_path)
    vp_ops_map = load_vp_ops_supervision(sv_path)

    cases_written = 0
    output_lines: list[str] = []

    for ticket, rh_row in sorted(rh_map.items()):
        if ticket_filter and ticket != ticket_filter:
            continue

        ts = rh_row.get("timestamp", "")
        if since and ts and ts[:10] < since:
            continue

        intent_rows = intent_map.get(ticket, [])
        decided_row = decided_map.get(ticket)
        completion_row = completion_map.get(ticket)
        vp_ops_row = vp_ops_map.get(ticket)

        case = build_case(
            rh_row,
            intent_rows,
            decided_row,
            completion_row=completion_row,
            vp_ops_row=vp_ops_row,
        )
        signal = case["learning_signal"]

        if overrides_only and signal not in ("override", "triage"):
            continue

        if linear_api_key and ticket:
            case["ticket_description"] = _fetch_ticket_description(ticket, linear_api_key)

        line = json.dumps(case, separators=(",", ":"), ensure_ascii=False)
        output_lines.append(line)
        cases_written += 1

    if dry_run:
        # Use binary stdout to avoid Windows cp1252 encoding errors on non-ASCII
        stdout_bin = getattr(sys.stdout, "buffer", None)
        for line in output_lines:
            if stdout_bin is not None:
                stdout_bin.write((line + "\n").encode("utf-8"))
            else:
                print(line)
    else:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "a", encoding="utf-8") as fh:
            for line in output_lines:
                fh.write(line + "\n")
        print(
            f"[hermes_apprentice] appended {cases_written} learning cases to {output_path}",
            file=sys.stderr,
        )

    return cases_written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Hermes apprentice bridge — differential analysis of routing decisions."
    )
    p.add_argument(
        "--since", metavar="YYYY-MM-DD", help="Only include decisions on or after this date"
    )
    p.add_argument("--ticket", metavar="PRO-NNN", help="Only include a specific ticket")
    p.add_argument("--overrides-only", action="store_true", help="Only emit override/triage cases")
    p.add_argument("--dry-run", action="store_true", help="Print to stdout instead of appending")
    p.add_argument("--output", metavar="PATH", help="Override output file path")
    p.add_argument("--routing-history", metavar="PATH", help="Override routing_history.jsonl path")
    p.add_argument("--callbacks", metavar="PATH", help="Override pending_callbacks.jsonl path")
    p.add_argument("--completion-log", metavar="PATH", help="Override cc_completion_log.jsonl path")
    p.add_argument("--supervision", metavar="PATH", help="Override vp_ops_supervision.jsonl path")
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    routing_path = args.routing_history or _default_path("routing_history.jsonl")
    callbacks_path = args.callbacks or _default_path("pending_callbacks.jsonl")
    output_path = args.output or _default_path("hermes_learning_cases.jsonl")
    api_key = os.environ.get("LINEAR_API_KEY", "").strip() or None

    for path, label in [(routing_path, "routing_history"), (callbacks_path, "callbacks")]:
        if not os.path.exists(path):
            print(f"[hermes_apprentice] error: {label} file not found: {path}", file=sys.stderr)
            sys.exit(1)

    count = run(
        routing_history_path=routing_path,
        callbacks_path=callbacks_path,
        output_path=output_path,
        since=args.since,
        ticket_filter=args.ticket,
        overrides_only=args.overrides_only,
        dry_run=args.dry_run,
        linear_api_key=api_key,
        completion_log_path=args.completion_log,
        supervision_path=args.supervision,
    )

    if count == 0:
        print("[hermes_apprentice] no matching cases found", file=sys.stderr)


if __name__ == "__main__":
    main()
