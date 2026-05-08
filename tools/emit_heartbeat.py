"""
emit_heartbeat.py — append a heartbeat row to data/cc_heartbeat_log.jsonl.

Works from any git worktree (miru-w1, miru-w2, etc.) — always writes to the
main repo's data/ directory, not the worktree-local one.

Usage:
    python tools/emit_heartbeat.py \
        --worker-id miru-w1 \
        --ticket-id PRO-XXX \
        --step running_pre_commit \
        [--branch dreighto/pro-xxx-...] \
        [--last-file tests/test_x.py] \
        [--stall-signal "awaiting_external: bugbot"] \
        [--outputs path/a path/b]

The file is append-only.  Never truncate, sort, or deduplicate it.
Schema: PRO-180 (locked 2026-04-28, PROVISIONAL).
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Hash-chain library lives next to this script in tools/. Make it importable
# regardless of invocation mode.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_chain import append_chained


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
            # resolve relative to the subprocess cwd, not Python's cwd
            common_dir = os.path.normpath(os.path.join(script_dir, result.stdout.strip()))
            return os.path.dirname(common_dir)
    except Exception:
        pass
    # fallback: two levels up from this file (works from main worktree)
    return os.path.dirname(script_dir)


HEARTBEAT_LOG = os.path.join(_repo_root(), "data", "cc_heartbeat_log.jsonl")


def emit(
    worker_id: str,
    ticket_id: str,
    step: str,
    branch: str | None = None,
    last_file_written: str | None = None,
    stall_signal: str | None = None,
    outputs: list[str] | None = None,
) -> None:
    row = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "worker_id": worker_id,
        "ticket_id": ticket_id,
        "status": "IN_PROGRESS",
        "step": step,
        "branch": branch,
        "last_file_written": last_file_written,
        "stall_signal": stall_signal,
        "outputs": outputs or [],
    }
    env_trace = os.environ.get("MIRU_TRACE_ID", "").strip()
    if env_trace:
        row["trace_id"] = env_trace
    # DGAS Tier 2 #6 Part B: chain every heartbeat row.
    append_chained(Path(HEARTBEAT_LOG), row)
    print(f"[heartbeat] {json.dumps(row, separators=(',', ':'))}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Emit a worker heartbeat row.")
    parser.add_argument("--worker-id", required=True, help="Stable worker identifier")
    parser.add_argument("--ticket-id", required=True, help="Linear ticket ID")
    parser.add_argument("--step", required=True, help="Current task phase label")
    parser.add_argument("--branch", default=None, help="Current git branch")
    parser.add_argument(
        "--last-file", default=None, dest="last_file", help="Most recently written file"
    )
    parser.add_argument(
        "--stall-signal", default=None, dest="stall_signal", help="Stall signal string or omit"
    )
    parser.add_argument("--outputs", nargs="*", default=[], help="Artifact paths produced so far")
    args = parser.parse_args()

    emit(
        worker_id=args.worker_id,
        ticket_id=args.ticket_id,
        step=args.step,
        branch=args.branch,
        last_file_written=args.last_file,
        stall_signal=args.stall_signal,
        outputs=args.outputs,
    )


if __name__ == "__main__":
    main()
