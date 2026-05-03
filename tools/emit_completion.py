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

# Trace_id format from dispatch listener spawn.js:
#   {worker}-{ticket_id}-{uuid}-{uuid}, e.g. cc-PRO-276-eaa0a242-326360d3
# The ticket id segment matches Linear's identifier shape (TEAM-NNN). Anchored
# between hyphens or boundaries so worker prefix length doesn't matter; the
# first matching segment is the ticket id by construction (later uuid segments
# are lowercase hex only and cannot match [A-Z]+).
_TRACE_ID_TICKET_RE = re.compile(r"(?:^|-)([A-Z]+-\d+)(?:-|$)")


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

    log_path = os.path.join(_repo_root(), "data", "cc_completion_log.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    line = json.dumps(data, separators=(",", ":"))
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    print(f"[emit_completion] written to {log_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
