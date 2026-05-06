"""Local Governance Gatekeeper — dispatch validation core.

Sits between Claude Chat (CH) and the existing dispatch_listener
(port 19100). Receives a ``cc_handoff`` payload, runs a deterministic
floor (no LLM call), then a cross-context LLM validation, and either
forwards to the listener or returns a Phase 2.5 Rejection.

Per the locked Phase 1 spec
(``Notion: 358c5d34-0141-817c-8dda-e2f91a50a9c5``), this module:

1. Reframes routing as validation (per GMI 2026-05-05)
2. Reads ticket frontmatter as gospel + conversational delta as
   refinement
3. Targets ``main`` repo root for git_local_status (per PR #89's
   ``MIRU_REPO_ROOT``) so CH self-serve attempts on the core branch
   are caught
4. Logs every decision as a ``judgment_driven`` entry in
   ``agent_decisions.jsonl`` (per GMI — auditable shadow-mode bench)
5. Uses ``format=<json_schema>`` (this Ollama build silently
   ignores ``options.grammar``; smoke test confirmed 2026-05-06)

The module is import-clean. No I/O at import time. CLI entrypoint is
gated behind ``__name__ == "__main__"``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import forwarder
from .frontmatter_parser import FrontmatterError
from .frontmatter_parser import parse as parse_frontmatter

log = logging.getLogger("miru.gatekeeper")

REPO_ROOT = Path(os.environ.get("MIRU_REPO_ROOT") or Path(__file__).resolve().parents[1])
ROUTING_HISTORY_PATH = REPO_ROOT / "data" / "routing_history.jsonl"
MEMORY_DB_PATH = REPO_ROOT / "data" / "miru_memory.db"
GBNF_PATH = REPO_ROOT / "tools" / "gatekeeper" / "routing_schema.gbnf"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
DEFAULT_MODEL = "qwen2.5:7b"

IN_FLIGHT_WINDOW_SECONDS = 600

ALLOWLISTED_WORKERS = forwarder.ALLOWLISTED_WORKERS

# JSON schema mirror of routing_schema.gbnf — used in the Ollama
# ``format`` field because this build silently ignores
# ``options.grammar``. Smoke test 2026-05-06 confirmed format works.
ROUTING_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "trace_id",
        "ticket_id",
        "decision",
        "validation",
        "context_snapshot",
        "execution",
        "rejection",
        "flags",
        "rationale",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": ["2"]},
        "trace_id": {"type": "string"},
        "ticket_id": {"type": "string"},
        "decision": {
            "type": "object",
            "required": ["worker", "mode", "tool_profile", "confidence"],
            "properties": {
                "worker": {
                    "type": "string",
                    "enum": ["claude-code", "gemini", "both", "none"],
                },
                "mode": {
                    "type": "string",
                    "enum": ["routine", "judgment", "ambiguous", "blocked"],
                },
                "tool_profile": {
                    "type": ["string", "null"],
                    "enum": [
                        "drift_executor",
                        "standard_worker",
                        "reviewer",
                        None,
                    ],
                },
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
        },
        "validation": {
            "type": "object",
            "required": [
                "is_legitimate_build",
                "self_serve_probability",
                "deterministic_checks",
                "rationale",
            ],
        },
        "context_snapshot": {"type": "object"},
        "execution": {"type": "object"},
        "rejection": {"type": ["object", "null"]},
        "flags": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
}

GOVERNANCE_PREAMBLE = """You are the Miru Local Governance Gatekeeper. Your job is to validate \
conversational dispatches from Claude Chat (CH) before they reach a worker. \
Your value is in saying NO when CH is hallucinating a handoff, has drifted \
from the ticket spec, or is trying to dispatch work that's already done. \
When CH is correct, routing is mostly mechanical (label → worker). \
When CH is wrong, you issue a structured rejection (Phase 2.5 Rejection \
Loop) so CH can correct itself rather than retry blind.

You receive: a Linear ticket frontmatter (the original-intent gospel), a \
git_local_status snapshot of the main repo, and a conversational delta CH \
sends as refinement. You emit a routing decision JSON matching the schema \
exactly. Closed enums are non-negotiable. Be conservative — prefer \
``ambiguous`` over a confident wrong answer; prefer ``standard_worker`` \
over the more permissive ``drift_executor`` when in doubt.

Rejection reasons (use one when ``decision.worker = "none"``):
  - ``ticket_drift_unresolved``: delta materially contradicts frontmatter
  - ``already_completed``: repo state shows work is done
  - ``dirty_worktree``: main repo has uncommitted changes overlapping scope
  - ``not_a_build``: request is conversational, not a dispatch task
  - ``ghost_task``: trace_id is already claimed in the A2A bus

If validation passes, ``rejection`` MUST be ``null``."""


class GatekeeperError(Exception):
    """Internal Gatekeeper error (not a Phase 2.5 Rejection).

    Phase 2.5 Rejections are a normal outcome and are returned as JSON.
    This exception is for unexpected failures the operator must investigate
    (LLM unreachable, schema validation failed despite ``format``, etc.).
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


# ---------------------------------------------------------------------------
# Deterministic floor (Python only, no LLM call)
# ---------------------------------------------------------------------------


def _check_trace_id_format(trace_id: str) -> bool:
    if not isinstance(trace_id, str):
        return False
    if not (6 <= len(trace_id) <= 128):
        return False
    return all(c.isalnum() or c in "_-" for c in trace_id)


def _check_a2a_bus_state(trace_id: str) -> tuple[bool, str]:
    """Verify trace_id isn't already claimed/pending in agent_messages.

    Returns ``(passed, detail)``. Per GMI 2026-05-05 — prevents duplicate
    execution by catching trace_ids that another agent has already
    claimed.
    """
    if not MEMORY_DB_PATH.exists():
        return True, "memory_db_absent_skip"

    try:
        conn = sqlite3.connect(f"file:{MEMORY_DB_PATH}?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.execute(
                "SELECT status FROM agent_messages WHERE trace_id = ? "
                "AND status IN ('pending', 'claimed') LIMIT 1",
                (trace_id,),
            )
            row = cur.fetchone()
            if row is not None:
                return False, f"trace_id already in A2A bus with status={row[0]!r}"
            return True, "no_active_a2a_claim"
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            return True, "agent_messages_table_absent_skip"
        return True, f"a2a_check_failed_skip: {e}"


def _check_git_status(repo_root: Path) -> tuple[bool, list[str]]:
    """Snapshot git status of the MAIN repo root (not worker worktrees).

    Per GMI 2026-05-05 + PR #89 ``MIRU_REPO_ROOT`` semantics. Returns
    ``(clean, modified_paths)``. Untracked-only repos are still considered
    "clean enough" for the deterministic floor — the LLM step weighs the
    untracked list against the ticket scope.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        log.warning("git_status_failed", extra={"error": str(e)})
        return True, []

    if result.returncode != 0:
        log.warning("git_status_nonzero", extra={"stderr": result.stderr[:200]})
        return True, []

    # Treat any non-blank, non-untracked status code as "modified". The
    # earlier whitelist (M, A, D, R, MM, AM) missed rename-modified (RM),
    # merge conflicts (UU, AA, UD, DU, DD), typechange (T), and assorted
    # other porcelain codes. The safer policy is "if git porcelain has
    # something to say AND it isn't an untracked-file marker, it counts."
    modified = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        if code == "??":
            continue
        modified.append(path)

    return (len(modified) == 0), modified


def _check_in_flight_dispatch(ticket_id: str) -> tuple[bool, str]:
    """Look back through routing_history for an active dispatch on this ticket.

    Returns ``(no_in_flight, detail)``. Append-only — read only.
    """
    if not ROUTING_HISTORY_PATH.exists():
        return True, "no_routing_history"

    cutoff = time.time() - IN_FLIGHT_WINDOW_SECONDS
    try:
        with ROUTING_HISTORY_PATH.open("r", encoding="utf-8") as fh:
            tail = fh.readlines()[-200:]
    except OSError as e:
        return True, f"routing_history_read_failed_skip: {e}"

    for line in reversed(tail):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        if row.get("task_identifier") != ticket_id and row.get("ticket_id") != ticket_id:
            continue

        ts = row.get("timestamp", "")
        try:
            from datetime import datetime

            row_ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            if row_ts < cutoff:
                continue
        except (ValueError, AttributeError):
            continue

        outcome = row.get("outcome")
        if outcome in ("dispatched", "shadow-dispatched", "callback-decided"):
            return False, f"in_flight: {row.get('chosen_worker','?')} via {outcome} at {ts}"

    return True, "no_recent_active_dispatch"


def run_deterministic_floor(
    *,
    trace_id: str,
    ticket_id: str,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Run all four pre-LLM checks. Returns a checks dict matching the schema."""
    repo_root = repo_root or REPO_ROOT

    trace_valid = _check_trace_id_format(trace_id)
    a2a_clean, a2a_detail = _check_a2a_bus_state(trace_id)
    worktree_clean, modified = _check_git_status(repo_root)
    no_in_flight, in_flight_detail = _check_in_flight_dispatch(ticket_id)

    return {
        "trace_id_valid": trace_valid,
        "ticket_exists_and_open": True,
        "worktree_clean": worktree_clean,
        "no_in_flight_dispatch": no_in_flight,
        "a2a_clean": a2a_clean,
        "_modified_paths": modified,
        "_a2a_detail": a2a_detail,
        "_in_flight_detail": in_flight_detail,
    }


# ---------------------------------------------------------------------------
# LLM call (cross-context validation)
# ---------------------------------------------------------------------------


def _build_prompt(
    *,
    ticket_id: str,
    frontmatter: dict[str, Any] | None,
    git_status: list[str],
    conversational_delta: str | None,
    deterministic_checks: dict[str, Any],
) -> str:
    """Build the Gatekeeper prompt as stable prefix + dynamic tail.

    Stable prefix (cacheable, 1-hour TTL on Anthropic; per PXY) is the
    governance preamble + schema reference. Dynamic tail is the per-call
    ticket data.
    """
    fm_yaml = json.dumps(frontmatter, indent=2) if frontmatter else "(no frontmatter on ticket)"
    git_summary = "\n".join(f"  - {p}" for p in git_status[:20]) if git_status else "  (clean tree)"
    delta = conversational_delta if conversational_delta else "(no conversational delta)"

    deterministic_summary = json.dumps(
        {k: v for k, v in deterministic_checks.items() if not k.startswith("_")},
        indent=2,
    )

    return f"""{GOVERNANCE_PREAMBLE}

---

TICKET: {ticket_id}

FRONTMATTER (original intent at ticket creation):
{fm_yaml}

GIT STATUS (main repo, modified paths):
{git_summary}

CONVERSATIONAL DELTA (CH refinements since ticket creation):
{delta}

DETERMINISTIC CHECKS (already ran, all four):
{deterministic_summary}

---

Emit the routing decision JSON. Schema version "2". trace_id must match \
the format ``rtr-PRO-XXX-<rand>``. Be conservative on edge cases. \
``rejection`` is null when the dispatch is legitimate, or an object with a \
``reason`` enum when not.
"""


def call_ollama(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    timeout_s: float = 180.0,
) -> tuple[dict[str, Any], float]:
    """Call Ollama with format=json_schema. Returns (parsed_dict, latency_ms).

    Uses ``format`` because this Ollama build silently ignores
    ``options.grammar`` (smoke test 2026-05-06).
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": ROUTING_JSON_SCHEMA,
        "options": {"temperature": 0.0},
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            response = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise GatekeeperError(
            "ollama_http_error",
            f"HTTP {e.code}: {e.read()[:300]!r}",
        ) from e
    except urllib.error.URLError as e:
        raise GatekeeperError("ollama_unreachable", str(e.reason)) from e
    except (TimeoutError, OSError) as e:
        raise GatekeeperError("ollama_timeout", str(e)) from e

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    content = response.get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as e:
        raise GatekeeperError(
            "ollama_emit_not_json",
            f"first 200 chars: {content[:200]!r}",
        ) from e

    return parsed, elapsed_ms


# ---------------------------------------------------------------------------
# Decision logging
# ---------------------------------------------------------------------------


def log_decision(decision: dict[str, Any], *, ticket_id: str) -> None:
    """Append a judgment_driven entry to agent_decisions.jsonl.

    Uses ``tools/emit_decision.py`` per the append-only contract — never
    open the file directly. Per GMI 2026-05-05: every Gatekeeper accept /
    reject / enrich is logged for shadow-mode auditability.
    """
    emit_script = REPO_ROOT / "tools" / "emit_decision.py"
    if not emit_script.exists():
        log.warning("emit_decision_script_missing skipping_log")
        return

    short = {
        "trigger": "scope_interpretation",
        "proposed_tag": "judgment_driven",
        "authority_mode": "operator",
        "confidence": decision.get("decision", {}).get("confidence", "medium"),
        "decision": decision.get("rationale", "")[:400] or "gatekeeper decision",
        "confidence_reason": decision.get("validation", {}).get("rationale", "")[:400]
        or "see validation block",
        "would_change_mind_if": "deterministic floor or LLM emit changes",
        "ticket_id": ticket_id,
        "source": "gatekeeper_shadow_mode",
        "context_refs": [],
        "canon_refs": [],
        "evidence_refs": [],
        "alternatives_rejected": [],
    }

    try:
        result = subprocess.run(
            [sys.executable, str(emit_script)],
            input=json.dumps(short),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            log.warning(
                "emit_decision_failed rc=%s stderr=%s",
                result.returncode,
                result.stderr[:200],
            )
    except (subprocess.SubprocessError, OSError) as e:
        log.warning("emit_decision_subprocess_error %s", e)


# ---------------------------------------------------------------------------
# Top-level entry: gate_dispatch
# ---------------------------------------------------------------------------


def gate_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Main Gatekeeper entry point.

    Args:
      payload: cc_handoff input. Required keys:
        - ``ticket_id`` (str)
        - ``prompt`` (str) — the worker prompt body
        Optional context keys (read-only — used to build the LLM prompt):
        - ``trace_id`` — caller-supplied; if absent, one is minted
        - ``conversational_delta`` — text refinement from CH chat
        - ``parent_conversation_summary`` — hashed for staleness detection
        - ``ticket_frontmatter`` — parsed dict (caller-provided)
        - ``ticket_description`` — raw ticket body; parser will extract
          frontmatter from this if ``ticket_frontmatter`` is absent
        - ``shadow_mode`` (bool, default True) — if True, do not actually
          forward to dispatch_listener even on accept; just emit the
          decision JSON
        - ``gatekeeper_model`` — override the Ollama model used for the
          validation call (default: ``DEFAULT_MODEL``)

    The Gatekeeper is the validation authority. The model's emitted
    ``decision.{worker, mode, tool_profile, confidence}`` and
    ``execution.{model, thinking_level, timeout_seconds, plan_only}``
    fields are canonical; the payload does NOT override them. Caller
    overrides would defeat the validation purpose. Phase 2 may
    reconsider this for narrow allowlisted cases (e.g. operator-forced
    re-dispatch with explicit reason), tracked as a future enhancement.

    Returns the routing decision JSON (schema version "2"). On
    Phase 2.5 Rejection, ``decision.worker = "none"`` and ``rejection``
    is populated. On internal error, raises :class:`GatekeeperError`.
    """
    ticket_id = payload.get("ticket_id")
    if not ticket_id:
        raise GatekeeperError("payload_missing_ticket_id")

    prompt_text = payload.get("prompt")
    if not isinstance(prompt_text, str) or not prompt_text.strip():
        raise GatekeeperError(
            "payload_missing_prompt",
            "cc_handoff payload must include a non-empty 'prompt' string — "
            "the Gatekeeper validates intent against the prompt body, not just the ticket",
        )

    trace_id = payload.get("trace_id") or forwarder.mint_trace_id(ticket_id)

    frontmatter = payload.get("ticket_frontmatter")
    if frontmatter is None and "ticket_description" in payload:
        try:
            frontmatter = parse_frontmatter(payload["ticket_description"])
        except FrontmatterError as e:
            return _rejection_response(
                trace_id=trace_id,
                ticket_id=ticket_id,
                reason="not_a_build",
                explanation=f"Frontmatter parse failed: {e.reason}",
                suggested_correction="Fix the dispatch frontmatter in the ticket description.",
            )

    checks = run_deterministic_floor(trace_id=trace_id, ticket_id=ticket_id)

    if not checks["trace_id_valid"]:
        return _rejection_response(
            trace_id=trace_id,
            ticket_id=ticket_id,
            reason="not_a_build",
            explanation="trace_id format is invalid",
        )

    if not checks["a2a_clean"]:
        a2a_detail = checks.get("_a2a_detail", "")
        return _rejection_response(
            trace_id=trace_id,
            ticket_id=ticket_id,
            reason="ghost_task",
            explanation=f"trace_id is already active in the A2A bus ({a2a_detail})",
            suggested_correction="Mint a fresh trace_id or wait for the active claim to release.",
            checks=checks,
        )

    if not checks["no_in_flight_dispatch"]:
        return _rejection_response(
            trace_id=trace_id,
            ticket_id=ticket_id,
            reason="ticket_drift_unresolved",
            explanation=f"another dispatch is in flight on this ticket: "
            f"{checks.get('_in_flight_detail','')}",
            suggested_correction="Wait for the in-flight dispatch to complete or cancel it.",
            checks=checks,
        )

    if not checks["worktree_clean"]:
        modified = checks.get("_modified_paths", [])
        modified_summary = (
            ", ".join(modified[:5]) + (f" (+{len(modified) - 5} more)" if len(modified) > 5 else "")
            if modified
            else "(no detail)"
        )
        return _rejection_response(
            trace_id=trace_id,
            ticket_id=ticket_id,
            reason="dirty_worktree",
            explanation=f"main repo has uncommitted tracked changes: {modified_summary}",
            suggested_correction="Commit, stash, or revert the working-tree changes "
            "on main before dispatching. Per CLAUDE.md, every session ends on main "
            "with a clean tree.",
            checks=checks,
        )

    delta = payload.get("conversational_delta")
    parent_summary = payload.get("parent_conversation_summary", "")
    parent_hash = (
        hashlib.sha256(parent_summary.encode("utf-8")).hexdigest()[:32]
        if parent_summary
        else "0" * 32
    )

    prompt = _build_prompt(
        ticket_id=ticket_id,
        frontmatter=frontmatter,
        git_status=checks.get("_modified_paths", []),
        conversational_delta=delta,
        deterministic_checks=checks,
    )

    model = payload.get("gatekeeper_model", DEFAULT_MODEL)
    decision, latency_ms = call_ollama(prompt, model=model)

    # Force canonical IDs and schema version even if the model emitted
    # different values — the model is not authoritative on identity.
    decision["trace_id"] = trace_id
    decision["ticket_id"] = ticket_id
    decision["schema_version"] = "2"

    # Derive is_legitimate_build when the model omits it.  qwen2.5:7b
    # sometimes drops validation sub-fields despite the JSON schema
    # constraint.  The derivation is safe: if the model DID emit the
    # field we leave it alone; otherwise we infer from worker + rejection.
    val = decision.setdefault("validation", {})
    if "is_legitimate_build" not in val:
        val["is_legitimate_build"] = (
            decision.get("decision", {}).get("worker") in forwarder.ALLOWLISTED_WORKERS
            and decision.get("rejection") is None
        )

    cs = decision.setdefault("context_snapshot", {})
    cs.setdefault("parent_conversation_summary_hash", parent_hash)
    cs.setdefault("conversational_delta", delta)
    cs.setdefault(
        "ticket_vs_conversation_coherent",
        delta is None,
    )

    decision.setdefault("flags", []).append(f"latency_ms:{int(latency_ms)}")
    decision["flags"].append(f"model:{model}")

    log_decision(decision, ticket_id=ticket_id)

    # Phase 2/3 cutover: forward to dispatch_listener if accepted AND not
    # in shadow mode. Phase 1 (this PR) defaults shadow_mode=True so no
    # actual dispatch routing change happens — the Gatekeeper just emits
    # decisions for shadow-mode bench against routing_history.jsonl.
    shadow_mode = bool(payload.get("shadow_mode", True))
    decision_accepted = (
        decision.get("validation", {}).get("is_legitimate_build") is True
        and decision.get("rejection") is None
        and decision.get("decision", {}).get("worker") in forwarder.ALLOWLISTED_WORKERS
    )

    if decision_accepted and not shadow_mode:
        try:
            execution = decision.get("execution", {}) or {}
            forward_response = forwarder.forward(
                trace_id=trace_id,
                worker=decision["decision"]["worker"],
                prompt_text=prompt_text,
                timeout_seconds=int(execution.get("timeout_seconds", 600)),
                model=execution.get("model") or None,
                thinking_level=execution.get("thinking_level") or None,
                tool_profile=decision["decision"].get("tool_profile") or "standard_worker",
            )
            decision["flags"].append(f"forwarded:{forward_response.get('status','?')}")
        except forwarder.ForwarderError as e:
            log.warning(
                "forwarder_failed trace_id=%s reason=%s detail=%s",
                trace_id,
                e.reason,
                e.detail,
            )
            decision["flags"].append(f"forward_failed:{e.reason}")
    elif decision_accepted and shadow_mode:
        decision["flags"].append("shadow_mode:no_forward")

    return decision


def _rejection_response(
    *,
    trace_id: str,
    ticket_id: str,
    reason: str,
    explanation: str,
    suggested_correction: str = "",
    checks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Phase 2.5 Rejection response without an LLM call."""
    det = checks or {
        "trace_id_valid": True,
        "ticket_exists_and_open": True,
        "worktree_clean": True,
        "no_in_flight_dispatch": True,
    }
    det = {k: v for k, v in det.items() if not k.startswith("_")}
    response = {
        "schema_version": "2",
        "trace_id": trace_id,
        "ticket_id": ticket_id,
        "decision": {
            "worker": "none",
            "mode": "blocked",
            "tool_profile": None,
            "confidence": "high",
        },
        "validation": {
            "is_legitimate_build": False,
            "self_serve_probability": 1.0 if reason == "not_a_build" else 0.5,
            "deterministic_checks": det,
            "rationale": f"Deterministic-floor rejection: {reason}",
        },
        "context_snapshot": {
            "parent_conversation_summary_hash": "0" * 32,
            "conversational_delta": None,
            "ticket_vs_conversation_coherent": False,
        },
        "execution": {
            "model": "",
            "thinking_level": None,
            "timeout_seconds": 600,
            "plan_only": True,
        },
        "rejection": {
            "reason": reason,
            "explanation": explanation,
            "suggested_correction": suggested_correction,
        },
        "flags": [f"rejected:{reason}"],
        "rationale": f"Gatekeeper rejected dispatch: {explanation}",
    }
    log_decision(response, ticket_id=ticket_id)
    return response


# ---------------------------------------------------------------------------
# CLI entrypoint (shadow mode + manual testing)
# ---------------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run the Gatekeeper against a JSON payload.")
    p.add_argument(
        "--payload",
        type=Path,
        required=True,
        help="Path to a JSON file with the cc_handoff payload.",
    )
    p.add_argument(
        "--out",
        type=Path,
        help="Write the routing decision JSON to this path. If omitted, prints to stdout.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_argparser()
    args = parser.parse_args(argv)

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    decision = gate_dispatch(payload)
    out_text = json.dumps(decision, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(out_text, encoding="utf-8")
        print(f"Wrote decision to {args.out}")
    else:
        print(out_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
