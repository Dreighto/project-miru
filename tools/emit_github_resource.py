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


def append_entry(data: dict, ledger_path: str) -> None:
    validate(data)
    os.makedirs(os.path.dirname(ledger_path), exist_ok=True)
    line = json.dumps(data, separators=(",", ":"))
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


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

    ledger_path = os.path.join(_repo_root(), "data", "github_resource_ledger.jsonl")

    try:
        append_entry(data, ledger_path)
    except ValueError as e:
        print(f"[emit_github_resource] error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[emit_github_resource] written to {ledger_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
