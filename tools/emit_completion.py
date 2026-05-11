"""
emit_completion.py — append a completion marker to data/cc_completion_log.jsonl.

Works from any git worktree (miru-w1, miru-w2, etc.) — always writes to the
main repo's data/ directory, not the worktree-local one.

Usage: pipe a single JSON object to stdin.

    python tools/emit_completion.py <<'EOF'
    {
      "timestamp": "2026-05-02T03:00:00Z",
      "ticket_id": "PRO-XXX",
      "phase": null,
      "status": "CONFIRMED_WORKING",
      "summary": "...",
      "branch": "dreighto/pro-xxx-...",
      "pr_number": null,
      "merge_commit_sha": null,
      "files_touched": [],
      "linear_state_after": "Done",
      "deploy_actions": [],
      "test_evidence": "...",
      "follow_up_tickets_filed": [],
      "notes": "",
      "handoff": null
    }
    EOF

Include a handoff object when another worker must continue the work:

    "handoff": {
      "next_worker": "cursor",
      "ticket_id": "PRO-YYY",
      "context": "Plain English paragraph for the receiving worker.",
      "entry_points": ["pm/templates/foo.html:42"],
      "watch_out_for": ["specific gotcha the next worker needs to know"],
      "blocked_on": null
    }

Schema defined in CLAUDE.md — Completion Contract section.
The file is append-only. Never truncate, sort, or deduplicate it.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Hash-chain library lives next to this script in tools/. Make it importable
# regardless of how this helper is invoked (python tools/emit_completion.py,
# python -m tools.emit_completion, etc.).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_chain import append_chained

# Trace_id format from dispatch listener spawn.js:
#   {worker}-{ticket_id}-{uuid}-{uuid}, e.g. cc-PRO-276-eaa0a242-326360d3
# The ticket id segment matches Linear's identifier shape (TEAM-NNN). Anchored
# between hyphens or boundaries so worker prefix length doesn't matter; the
# first matching segment is the ticket id by construction (later uuid segments
# are lowercase hex only and cannot match [A-Z]+).
_TRACE_ID_TICKET_RE = re.compile(r"(?:^|-)([A-Z]+-\d+)(?:-|$)")

# canon_snapshot_id must be a 64-char lowercase hex SHA-256 digest. Mirrors
# the format check in services/dispatch_listener/src/canon_probe.js. CodeRabbit
# R2 finding on PR #181: if LOGUEOS_CANON_SNAPSHOT_ID is set in the worker env
# but contains a malformed value, refuse to write it onto the marker (don't
# silently corrupt the audit row with bad provenance). Emit a stderr warning
# so the operator sees the discrepancy.
_CANON_SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def _is_valid_canon_snapshot_id(snap_id: str) -> bool:
    """Return True iff snap_id is a 64-char lowercase hex SHA-256 digest."""
    return bool(_CANON_SNAPSHOT_ID_RE.fullmatch(snap_id))


def _ticket_id_from_trace(trace_id):
    """Extract a Linear ticket identifier from a worker trace_id, if encoded.

    Returns the ticket id string (e.g. "PRO-276") or None when the trace_id
    is empty or doesn't carry a ticket id. The auto-fill is best-effort —
    callers should treat None as "leave whatever the marker submitted in place".
    """
    if not trace_id:
        return None
    match = _TRACE_ID_TICKET_RE.search(trace_id)
    if match:
        return match.group(1)
    return None


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


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print("[emit_completion] error: no JSON received on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[emit_completion] error: invalid JSON — {e}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(data, dict):
        print(
            f"[emit_completion] error: top-level JSON must be an object, "
            f"got {type(data).__name__}",
            file=sys.stderr,
        )
        sys.exit(1)

    # If MIRU_TRACE_ID is set in the worker env (set by dispatch_listener spawn.js):
    # 1. Fill the marker's `trace_id` field if missing — bridges marker → dispatch.
    # 2. Auto-fill `ticket_id` if the marker submitted null/missing AND the trace
    #    encodes a Linear ticket id (e.g. cc-PRO-276-uuid-uuid → "PRO-276").
    #    This closes PRO-285: orphan completion markers leave Linear stuck In
    #    Progress because the daily drift scanner can't link a null ticket_id
    #    back to its issue. The trace_id is reliably set by the dispatch listener,
    #    so it's the right inference source.
    env_trace = os.environ.get("MIRU_TRACE_ID", "").strip()
    if env_trace:
        if "trace_id" not in data:
            data["trace_id"] = env_trace
        if not data.get("ticket_id"):
            inferred = _ticket_id_from_trace(env_trace)
            if inferred:
                data["ticket_id"] = inferred

    # LOS-10 Step 2 / LOS-13: stamp canon_snapshot_id from env.
    # The dispatch listener probes /canon-manifest before spawn and passes
    # LOGUEOS_CANON_SNAPSHOT_ID into the worker's env. Recording it on every
    # marker makes the canon-that-was-in-force deterministically queryable
    # for any historical row.
    #
    # CodeRabbit R1: env value is AUTHORITATIVE. The dispatch listener
    # observed the canon at spawn; if a worker submits a different value in
    # its marker, that's almost certainly a bug or tampering. Env always
    # wins. If the marker had a different value, emit a stderr warning so
    # audits can see the discrepancy.
    #
    # CodeRabbit R2: validate env_canon format BEFORE accepting. If the env
    # value is malformed (not 64-char lowercase hex), refuse to write it
    # onto the marker — corrupting an audit row's provenance is worse than
    # leaving the prior value (which at least was caller-supplied). Emit
    # a stderr warning so the operator sees the misconfiguration.
    #
    # Naming: LOGUEOS_CANON_SNAPSHOT_ID uses the FUTURE post-rename style
    # per Step 6 rename map.
    env_canon = os.environ.get("LOGUEOS_CANON_SNAPSHOT_ID", "").strip()
    if env_canon:
        if not _is_valid_canon_snapshot_id(env_canon):
            print(
                f"[emit_completion] WARN: env LOGUEOS_CANON_SNAPSHOT_ID={env_canon!r} "
                f"is not a 64-char lowercase hex SHA-256 digest; ignoring (marker keeps "
                f"its existing canon_snapshot_id field if any). Fix the dispatch listener's "
                f"canon probe to emit only valid digests.",
                file=sys.stderr,
            )
        else:
            prior = data.get("canon_snapshot_id")
            if prior and prior != env_canon:
                print(
                    f"[emit_completion] WARN: marker submitted canon_snapshot_id={prior!r} "
                    f"but env LOGUEOS_CANON_SNAPSHOT_ID={env_canon!r}; env wins (dispatch-"
                    f"listener-observed value at spawn time, treated as authoritative).",
                    file=sys.stderr,
                )
            data["canon_snapshot_id"] = env_canon

    log_path = Path(_repo_root()) / "data" / "cc_completion_log.jsonl"
    # DGAS Tier 2 #6 Part B: chain every new row. Existing legacy rows at the
    # head of the file remain untouched; the first chained row anchors with
    # prev_hash=None and every subsequent row links back. See tools/audit_chain.py.
    row_hash = append_chained(log_path, data)

    print(f"[emit_completion] written to {log_path} (row_hash={row_hash[:12]}…)", file=sys.stderr)


if __name__ == "__main__":
    main()
