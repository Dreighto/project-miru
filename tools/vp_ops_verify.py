"""
vp_ops_verify.py — VP Ops supervisory verification pass for a completed ticket.

After a worker reports completion, VP Ops (Claude Code) runs this script to verify
the actual repo state matches what the worker claimed. Produces a VERIFIED or FLAGGED
verdict and appends one record to data/vp_ops_supervision.jsonl.

Usage:
    python tools/vp_ops_verify.py PRO-XXX

Works from any git worktree — always reads/writes to the main repo's data/ directory.

Exit codes:
    0 = VERIFIED
    1 = FLAGGED (flags printed to stdout as JSON)
    2 = ERROR (could not run verification)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_NN_PATTERN = re.compile(r"(\d+)\s*/\s*(\d+)")

# ---------------------------------------------------------------------------
# Repo root resolution (same pattern as emit_completion.py)
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    script_dir = Path(__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=script_dir,
            timeout=5,
        )
        if result.returncode == 0:
            common_dir = (script_dir / result.stdout.strip()).resolve()
            return common_dir.parent
    except Exception:
        pass
    return script_dir.parent


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_completion_marker(data_dir: Path, ticket_id: str) -> tuple[bool, dict | None]:
    log_path = data_dir / "cc_completion_log.jsonl"
    if not log_path.exists():
        return False, None
    marker = None
    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("ticket_id") == ticket_id:
                    marker = entry  # last match wins
            except json.JSONDecodeError:
                continue
    return marker is not None, marker


def _check_git_commits(repo_root: Path, ticket_id: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "--all", f"--grep={ticket_id}"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except Exception:
        return False


def _check_files_match(
    repo_root: Path, ticket_id: str, claimed_files: list[str]
) -> tuple[bool, list[str]]:
    if not claimed_files:
        return True, []
    try:
        result = subprocess.run(
            ["git", "log", "--name-only", "--pretty=format:", "--all", f"--grep={ticket_id}"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )
        if result.returncode != 0:
            return True, []  # can't check — don't penalise
        touched_in_git = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        flags = []
        for f in claimed_files:
            if f not in touched_in_git:
                flags.append(f"claimed file not in git commits: {f}")
        return len(flags) == 0, flags
    except Exception:
        return True, []  # non-fatal


def _check_pr_state(marker: dict) -> tuple[str | None, list[str]]:
    pr_number = marker.get("pr_number")
    if not pr_number:
        return None, []
    try:
        import urllib.request

        token = (
            (os.environ.get("ROOM_TOKEN_OPERATOR") or "").strip()
            or (os.environ.get("ROOM_TOKEN_WORKER") or "").strip()
            or (os.environ.get("GITHUB_TOKEN_READ") or "").strip()
            or (os.environ.get("GITHUB_TOKEN_WRITE") or "").strip()
            or None
        )
        if not token:
            return "unknown", []
        req = urllib.request.Request(
            f"https://api.github.com/repos/Dreighto/project-miru/pulls/{pr_number}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
            state = (data.get("merged") and "merged") or data.get("state", "unknown")
            flags = []
            if state not in ("merged", "closed"):
                flags.append(f"PR #{pr_number} is not merged (state: {state})")
            return state, flags
    except Exception:
        return "unknown", []  # non-fatal — network may be unavailable


def _check_handoff_entry_points(repo_root: Path, marker: dict) -> tuple[bool, list[str]]:
    handoff = marker.get("handoff")
    if not handoff:
        return True, []
    entry_points = handoff.get("entry_points") or []
    flags = []
    for ep in entry_points:
        # strip line number suffix (e.g. "foo.py:42" -> "foo.py")
        file_path = ep.split(":")[0]
        if not (repo_root / file_path).exists():
            flags.append(f"handoff entry_point file not found: {file_path}")
    return len(flags) == 0, flags


def _check_test_evidence(marker: dict) -> tuple[float | None, str, list[str]]:
    """Parse test_evidence and return (pass_rate, tier, flags).

    Thresholds (from brief):
      >= 0.90 → green (no flag)
      0.50-0.89 → soft warning
      < 0.50 → FLAGGED
    """
    te = (marker.get("test_evidence") or "").strip()
    if not te:
        return None, "missing", []

    # N/N regex — highest confidence, but validate passed <= total to reject
    # false matches like ticket refs "PRO-117/112" which parse as 117/112.
    m = _NN_PATTERN.search(te)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        if total > 0 and passed <= total:
            rate = passed / total
            flags = []
            if rate < 0.50:
                flags.append(f"test pass rate critically low: {passed}/{total} ({rate:.0%})")
            elif rate < 0.90:
                flags.append(
                    f"test pass rate below threshold: {passed}/{total} ({rate:.0%}) — review recommended"
                )
            return round(rate, 4), "nn_regex", flags
        # Nonsensical ratio (passed > total) — fall through to other tiers

    # ci_only or legacy CI keywords
    lower = te.lower()
    if te.startswith("ci_only:") or any(
        kw in lower for kw in ("pre-commit", "hygiene", "ci pass", "ci green", "bugbot")
    ):
        return None, "ci_binary", []

    # no_tests
    if te == "no_tests" or any(
        kw in lower for kw in ("no test", "no_test", "behavioral", "rule only")
    ):
        return None, "no_tests", []

    # Freetext — not parseable, soft warning
    return None, "freetext", [f"test_evidence is freetext (not machine-parseable): {te[:80]}"]


# ---------------------------------------------------------------------------
# Main verification logic
# ---------------------------------------------------------------------------


def verify(ticket_id: str) -> dict:
    repo_root = _repo_root()
    data_dir = repo_root / "data"

    flags: list[str] = []
    checks: dict[str, object] = {}

    # 1. Completion marker
    marker_found, marker = _check_completion_marker(data_dir, ticket_id)
    checks["completion_marker_found"] = marker_found
    if not marker_found:
        flags.append(f"no completion marker found for {ticket_id}")

    worker = marker.get("worker", None) if marker else None
    completion_status = marker.get("status") if marker else None

    # 2. Git commits
    commits_found = _check_git_commits(repo_root, ticket_id)
    checks["git_commits_found"] = commits_found
    if not commits_found:
        flags.append(f"no git commits found mentioning {ticket_id}")

    # 3. Files match claims
    claimed_files = (marker or {}).get("files_touched") or []
    files_match, file_flags = _check_files_match(repo_root, ticket_id, claimed_files)
    checks["files_match_claims"] = files_match
    flags.extend(file_flags)

    # 4. PR state (non-fatal if unavailable)
    pr_state, pr_flags = _check_pr_state(marker or {})
    checks["pr_state"] = pr_state
    flags.extend(pr_flags)

    # 5. Handoff entry points
    handoff_ok, handoff_flags = _check_handoff_entry_points(repo_root, marker or {})
    checks["handoff_entry_points_exist"] = handoff_ok
    flags.extend(handoff_flags)

    # 6. Test evidence quality (PRO-312 — Hermes quality signal)
    test_pass_rate, test_tier, test_flags = _check_test_evidence(marker or {})
    checks["test_pass_rate"] = test_pass_rate
    checks["test_evidence_tier"] = test_tier
    flags.extend(test_flags)

    verdict = "VERIFIED" if not flags else "FLAGGED"

    record = {
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticket_id": ticket_id,
        "worker": worker or "unknown",
        "completion_status": completion_status,
        "verdict": verdict,
        "checks": checks,
        "flags": flags,
        "notes": "",
    }

    # Append to supervision log
    log_path = data_dir / "vp_ops_supervision.jsonl"
    data_dir.mkdir(exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")

    return record


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tools/vp_ops_verify.py <ticket_id>", file=sys.stderr)
        sys.exit(2)

    ticket_id = sys.argv[1].upper()
    try:
        record = verify(ticket_id)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "ticket_id": ticket_id}))
        sys.exit(2)

    print(json.dumps(record, indent=2))
    sys.exit(0 if record["verdict"] == "VERIFIED" else 1)


if __name__ == "__main__":
    main()
