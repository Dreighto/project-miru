"""
emit_github_resource.py — append a GitHub resource intent entry to
data/github_resource_ledger.jsonl.

Works from any git worktree (miru-w1, miru-w2, etc.) — always writes to the
main repo's data/ directory.

Usage: pipe a single JSON object to stdin.

    python tools/emit_github_resource.py <<'EOF'
    {
      "ts": "2026-05-07T04:00:00Z",
      "trace_id": "cc-PRO-320-abc123",
      "ticket_id": "PRO-320",
      "resource_type": "branch",
      "resource_id": "dreighto/pro-320-feature",
      "intent": "create",
      "status": "pending",
      "compensation": "delete_branch"
    }
    EOF

Required fields: ts, trace_id, resource_type, resource_id, intent, status.
Optional: ticket_id, compensation.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Hash-chain library lives next to this script in tools/. Make it importable
# regardless of invocation mode.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_chain import append_chained
from data_paths import data_path as _data_path

REQUIRED_FIELDS = {"ts", "trace_id", "resource_type", "resource_id", "intent", "status"}
VALID_RESOURCE_TYPES = {"branch", "pr"}
VALID_INTENTS = {"create", "close", "delete"}
VALID_STATUSES = {"pending", "committed", "compensated", "failed"}
VALID_COMPENSATIONS = {"delete_branch", "close_pr", None}


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


def validate(data: dict) -> None:
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    missing = REQUIRED_FIELDS - data.keys()
    if missing:
        raise ValueError(f"missing required fields: {sorted(missing)}")

    if data["resource_type"] not in VALID_RESOURCE_TYPES:
        raise ValueError(f"resource_type must be one of {VALID_RESOURCE_TYPES}")

    if data["intent"] not in VALID_INTENTS:
        raise ValueError(f"intent must be one of {VALID_INTENTS}")

    if data["status"] not in VALID_STATUSES:
        raise ValueError(f"status must be one of {VALID_STATUSES}")

    compensation = data.get("compensation")
    if compensation not in VALID_COMPENSATIONS:
        raise ValueError(f"compensation must be one of {VALID_COMPENSATIONS}")


def _stamp_project_id_from_env(data: dict) -> None:
    """LOS-28: fill project_id from worker env if the caller didn't provide one.

    The dispatch listener sets ``LOGUEOS_PROJECT_ID`` at spawn time (see
    services/dispatch_listener/src/projects.js). Calls from outside a
    dispatch context (manual ops scripts, tests) simply skip this — the
    row stays as the caller wrote it.
    """
    env_project_id = os.environ.get("LOGUEOS_PROJECT_ID", "").strip()
    if env_project_id and not data.get("project_id"):
        data["project_id"] = env_project_id


def append_entry(data: dict, ledger_path: str) -> None:
    validate(data)
    _stamp_project_id_from_env(data)
    # DGAS Tier 2 #6 Part B: chain every ledger row. fsync=True because this
    # ledger tracks external GitHub-side resources — losing a row means an
    # orphan branch/PR with no compensation record.
    append_chained(Path(ledger_path), data, fsync=True)


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        print("[emit_github_resource] error: no JSON received on stdin", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[emit_github_resource] error: invalid JSON — {e}", file=sys.stderr)
        sys.exit(1)

    ledger_path = str(_data_path("github_resource_ledger.jsonl", repo_root_fn=_repo_root))

    try:
        append_entry(data, ledger_path)
    except ValueError as e:
        print(f"[emit_github_resource] error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[emit_github_resource] written to {ledger_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
