"""DGAS Tier 2 #10: governance metric writer.

Appends a single chained row to ``data/governance_metrics.jsonl`` recording
when a governance gate fires. The file is the data source for the weekly
"gates with 0 fires" canary report — a gate that never blocks anything is
either perfect or theatre, and we want the operator to see which.

Schema:
    {
        "ts":       ISO-8601 UTC,
        "gate":     short stable identifier (see _GATE_REGISTRY below),
        "outcome":  one of "fired" | "blocked" | "passed" | "bypass_attempted",
        "actor":    who triggered the gate (worker id, github username, "ci")
        "subject":  what the gate examined (filename, sql snippet, branch ref)
        "context":  optional dict for gate-specific extra fields (truncated)
    }

Outcome semantics:
    fired             — the gate ran (always emit when a gate runs in CI/CLI)
    blocked           — the gate refused the attempt (the bad thing was caught)
    passed            — the gate ran and the input was clean
    bypass_attempted  — the gate detected an explicit bypass attempt
                        (e.g. --no-verify on a hygiene-required commit)

Why a closed outcome enum? Because the rollup report counts
``blocked / fired`` per gate. New outcome strings would silently change
the math.

Usage as a library:
    from emit_governance_metric import emit
    emit(gate="safe_git_push", outcome="blocked", actor="cc",
         subject="refs/heads/main", context={"reason": "force_push_attempt"})

CLI usage:
    python tools/emit_governance_metric.py \\
        --gate safe_git_push --outcome blocked \\
        --actor cc --subject refs/heads/main \\
        --context-json '{"reason": "force_push_attempt"}'

Exit codes:
    0 — row appended
    1 — invalid input
    2 — script error
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Import the chain library from the same directory.
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
from audit_chain import append_chained  # noqa: E402

METRICS_LOG_REL = "data/governance_metrics.jsonl"

VALID_OUTCOMES: frozenset[str] = frozenset({"fired", "blocked", "passed", "bypass_attempted"})

# Stable gate identifiers used across emit calls and rollup reports. New
# gates must be added here AND must have a fault-injection test registered
# in tests/test_governance_gates.py — the meta-test enforces both.
_GATE_REGISTRY: frozenset[str] = frozenset(
    {
        "secret_scanner",  # gitleaks pre-commit (PR #126)
        "db_write_deny",  # gateway memory_tools deny-check (PR #127)
        "audit_chain",  # row_hash + prev_hash on append-only logs (PR #128)
        "safe_git_push",  # pre-push protection on main/master/release (PR #129)
        "governance_change",  # tools/check_governance_change.py (PR #130)
        "audit_anchor",  # tools/emit_audit_anchor.py (PR #133)
        # Future gates (placeholders; meta-test allows uncommented entries):
        # "localhost_bind",      # Codex's WIP localhost bind
        # "git_execution_wrap",  # Tier 2 #8 follow-up (force-push CLI wrap)
    }
)

_MAX_CONTEXT_BYTES = 4 * 1024  # truncate verbose context to keep rows small


def _utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repo_root() -> Path:
    """Active worktree root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(_THIS_DIR),
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except Exception:
        pass
    return _THIS_DIR.parent


def _truncate_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Cap context so a single rogue caller can't blow up the metrics file.

    Truncates the JSON serialisation to ``_MAX_CONTEXT_BYTES``. The
    resulting context is still parseable; if truncation occurred we replace
    the whole context with a sentinel so downstream readers don't see a
    half-parsed object.
    """
    if not context:
        return None
    serialised = json.dumps(context, separators=(",", ":"))
    if len(serialised.encode("utf-8")) <= _MAX_CONTEXT_BYTES:
        return context
    return {
        "_truncated": True,
        "_original_byte_len": len(serialised.encode("utf-8")),
        "_max_byte_len": _MAX_CONTEXT_BYTES,
    }


def emit(
    gate: str,
    outcome: str,
    *,
    actor: str | None = None,
    subject: str | None = None,
    context: dict[str, Any] | None = None,
    log_path: Path | None = None,
) -> str:
    """Validate and append one metric row. Returns the chained row_hash."""
    if gate not in _GATE_REGISTRY:
        raise ValueError(
            f"unknown gate identifier {gate!r}. Add to _GATE_REGISTRY in "
            f"tools/emit_governance_metric.py and register a fault-injection "
            f"test in tests/test_governance_gates.py."
        )
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome {outcome!r}. Must be one of {sorted(VALID_OUTCOMES)}.")

    row = {
        "ts": _utc_iso(),
        "gate": gate,
        "outcome": outcome,
        "actor": actor,
        "subject": subject,
        "context": _truncate_context(context),
    }
    env_trace = os.environ.get("MIRU_TRACE_ID", "").strip()
    if env_trace:
        row["trace_id"] = env_trace

    target = log_path or (_repo_root() / METRICS_LOG_REL)
    return append_chained(target, row)


def _parse_context_arg(raw: str | None) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--context-json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--context-json must be a JSON object")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Append one governance metric row.")
    parser.add_argument("--gate", required=True, help=f"one of {sorted(_GATE_REGISTRY)}")
    parser.add_argument("--outcome", required=True, help=f"one of {sorted(VALID_OUTCOMES)}")
    parser.add_argument("--actor", default=None)
    parser.add_argument("--subject", default=None)
    parser.add_argument(
        "--context-json",
        default=None,
        help="optional JSON object with gate-specific extra fields",
    )
    args = parser.parse_args()

    try:
        context = _parse_context_arg(args.context_json)
        row_hash = emit(
            gate=args.gate,
            outcome=args.outcome,
            actor=args.actor,
            subject=args.subject,
            context=context,
        )
    except ValueError as exc:
        print(f"[governance_metric] error: {exc}", file=sys.stderr)
        return 1

    print(
        f"[governance_metric] {args.gate} {args.outcome} (row_hash={row_hash[:12]}…)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[governance_metric] script error: {exc}", file=sys.stderr)
        sys.exit(2)
