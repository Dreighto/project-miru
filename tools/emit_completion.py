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
      "notes": ""
    }
    EOF

Schema defined in CLAUDE.md — Completion Contract section.
The file is append-only. Never truncate, sort, or deduplicate it.
"""

import json
import os
import subprocess
import sys


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

    log_path = os.path.join(_repo_root(), "data", "cc_completion_log.jsonl")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    line = json.dumps(data, separators=(",", ":"))
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")

    print(f"[emit_completion] written to {log_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
