"""
emit_completion.py — append a completion marker to data/cc_completion_log.jsonl.

Works from any git worktree (`<repo>-w1`, `<repo>-w2`, etc.) — always writes to the
main repo's data/ directory, not the worktree-local one.

Three ways to pass the marker (preferred → fallback):

    # 1. Inline JSON via CLI (preferred for sandboxed callers — never blocks).
    python tools/emit_completion.py --marker-json '{"status":"CONFIRMED_WORKING",...}'

    # 2. JSON file via CLI (preferred for large markers or when shell quoting is hostile).
    python tools/emit_completion.py --marker-file /tmp/marker.json

    # 3. Pipe to stdin (legacy; works from interactive shells with proper EOF handling).
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

Why the CLI args exist (LOS-36, 2026-05-12): gemini-cli's sandboxed
`run_shell_command` tool can invoke this script with a stdin pipe that
never receives EOF. `sys.stdin.read()` then blocks indefinitely and the
worker times out without writing a completion marker. The CLI flags
sidestep stdin entirely — same parsing path, just a different input
channel. When stdin IS a TTY (i.e. someone ran the script interactively
without a redirect or flag), we now fail fast with a clear error
instead of hanging.

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

import argparse
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
from data_paths import data_path as _data_path

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


def _read_marker_raw() -> str:
    """Read the marker JSON from CLI args or stdin.

    Precedence: --marker-json > --marker-file > stdin.

    The CLI flags are sandbox-safe — they never block. The stdin fallback
    preserves the original heredoc-pipe ergonomics for interactive callers,
    but fails fast (rather than hanging) when stdin is a TTY with no
    redirect, which is what happens inside gemini-cli's sandboxed
    `run_shell_command` and any similar non-TTY-but-not-piped environment.
    """
    parser = argparse.ArgumentParser(
        description="Append a completion marker to data/cc_completion_log.jsonl.",
        add_help=True,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--marker-json",
        metavar="JSON",
        help="Inline JSON string for the marker. Preferred for sandboxed callers.",
    )
    group.add_argument(
        "--marker-file",
        metavar="PATH",
        help="Path to a UTF-8 JSON file containing the marker. Preferred for large markers.",
    )
    args = parser.parse_args()

    if args.marker_json is not None:
        return args.marker_json
    if args.marker_file is not None:
        path = Path(args.marker_file)
        if not path.is_file():
            print(
                f"[emit_completion] error: --marker-file not found: {path}",
                file=sys.stderr,
            )
            sys.exit(1)
        return path.read_text(encoding="utf-8")
    # Stdin fallback. Refuse to block on a TTY — a caller that ran
    # `python tools/emit_completion.py` interactively without piping
    # JSON is almost certainly making a mistake.
    if sys.stdin.isatty():
        print(
            "[emit_completion] error: stdin is a TTY and no --marker-json / "
            "--marker-file was given. Pipe JSON to stdin OR pass one of the "
            "CLI flags. See the module docstring for usage examples.",
            file=sys.stderr,
        )
        sys.exit(1)
    return sys.stdin.read()


def main() -> None:
    raw = _read_marker_raw().strip()
    if not raw:
        print("[emit_completion] error: empty marker payload", file=sys.stderr)
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

    # If LOGUEOS_TRACE_ID is set in the worker env (set by dispatch_listener spawn.js):
    # 1. Fill the marker's `trace_id` field if missing — bridges marker → dispatch.
    # 2. Auto-fill `ticket_id` if the marker submitted null/missing AND the trace
    #    encodes a Linear ticket id (e.g. cc-PRO-276-uuid-uuid → "PRO-276").
    #    This closes PRO-285: orphan completion markers leave Linear stuck In
    #    Progress because the daily drift scanner can't link a null ticket_id
    #    back to its issue. The trace_id is reliably set by the dispatch listener,
    #    so it's the right inference source.
    env_trace = os.environ.get("LOGUEOS_TRACE_ID", "").strip()
    if env_trace:
        if "trace_id" not in data:
            data["trace_id"] = env_trace
        if not data.get("ticket_id"):
            inferred = _ticket_id_from_trace(env_trace)
            if inferred:
                data["ticket_id"] = inferred

    # LOS-28: stamp project_id from the worker's env if the marker submitted
    # nothing. The dispatch listener sets LOGUEOS_PROJECT_ID at spawn time
    # via tools/logueos_mcp_gateway/dispatch_tools.py + services/dispatch_
    # listener/src/projects.js (OpenTelemetry Resource Attribute pattern).
    # Markers emitted outside a dispatch context simply skip this — the row
    # stays as the caller wrote it.
    env_project_id = os.environ.get("LOGUEOS_PROJECT_ID", "").strip()
    if env_project_id and not data.get("project_id"):
        data["project_id"] = env_project_id

    # LOS-32 Phase 0: optional `observations` block on the completion marker.
    # If present, pop it off the marker dict (keep the marker schema clean),
    # then iterate and write each observation to data/agent_decisions.jsonl
    # via the emit_observation emitter. Malformed observations are warned
    # about but DO NOT block the marker write — the worker's task completion
    # signal takes priority over observation telemetry.
    observations = data.pop("observations", None)

    log_path = _data_path("cc_completion_log.jsonl", repo_root_fn=_repo_root)
    # DGAS Tier 2 #6 Part B: chain every new row. Existing legacy rows at the
    # head of the file remain untouched; the first chained row anchors with
    # prev_hash=None and every subsequent row links back. See tools/audit_chain.py.
    row_hash = append_chained(log_path, data)

    print(f"[emit_completion] written to {log_path} (row_hash={row_hash[:12]}…)", file=sys.stderr)

    # LOS-32 Phase 0: relay any observations to emit_observation.
    if observations is not None:
        _emit_marker_observations(observations, marker_data=data)


def _emit_marker_observations(observations, marker_data: dict) -> None:
    """LOS-32: relay marker `observations` block to the Tier 0 emitter.

    Best-effort: per-observation errors are logged but do not raise.
    The marker has already been written at this point; observations are
    secondary telemetry.

    Pre-fills trace_id, ticket_id, and project_id on each observation
    from the parent marker so the worker doesn't have to repeat them.
    """
    if not isinstance(observations, list):
        print(
            f"[emit_completion] observations: expected a list, got "
            f"{type(observations).__name__} — skipping",
            file=sys.stderr,
        )
        return
    if not observations:
        return

    # Lazy import: emit_observation lives in the same tools/ directory and
    # uses the same audit_chain helper. Importing here keeps emit_completion
    # usable when emit_observation isn't on the path (e.g., legacy markers
    # that never emit observations).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import emit_observation
    except ImportError as exc:
        print(
            f"[emit_completion] observations: emit_observation unavailable ({exc}) — "
            f"skipping {len(observations)} observation(s)",
            file=sys.stderr,
        )
        return

    inherited = {
        "trace_id": marker_data.get("trace_id"),
        "ticket_id": marker_data.get("ticket_id"),
        "project_id": marker_data.get("project_id"),
        "task_shape": marker_data.get("task_shape"),
    }

    written = 0
    failed = 0
    for i, obs in enumerate(observations):
        if not isinstance(obs, dict):
            print(
                f"[emit_completion] observations[{i}]: expected a dict, got "
                f"{type(obs).__name__} — skipping",
                file=sys.stderr,
            )
            failed += 1
            continue
        # Inherit parent marker context where the observation doesn't override.
        record = {k: v for k, v in inherited.items() if v is not None}
        record.update(obs)
        try:
            emit_observation.emit(record)
            written += 1
        except emit_observation.ObservationValidationError as exc:
            print(
                f"[emit_completion] observations[{i}]: validation failed — {exc}",
                file=sys.stderr,
            )
            failed += 1
        except Exception as exc:
            print(
                f"[emit_completion] observations[{i}]: unexpected error — {exc}",
                file=sys.stderr,
            )
            failed += 1

    print(
        f"[emit_completion] observations: {written} written, {failed} failed "
        f"(out of {len(observations)})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
