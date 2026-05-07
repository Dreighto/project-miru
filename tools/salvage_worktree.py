"""
salvage_worktree.py — Inspect a worktree after a worker timeout/crash (PRO-317).

When a worker dies mid-task, this script inspects the worktree to determine
what work product exists and whether it can be automatically recovered.

Usage:
    python tools/salvage_worktree.py <worktree-path> <ticket-id>

Exit codes:
    0 = salvage report generated (check recommendation field)
    1 = error generating report
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

RECOMMENDATIONS = {
    "CODE_COMPLETE_TESTS_PASS": "Worker finished, tests green. Auto-salvage candidate.",
    "CODE_COMPLETE_TESTS_FAIL": "Worker finished but tests fail. Needs review.",
    "CODE_PARTIAL": "Worker started but didn't finish. Manual review.",
    "ALREADY_COMMITTED": "Worker committed but didn't push or complete lifecycle.",
    "NO_WORK_PRODUCT": "Worktree is clean or unchanged. Nothing to salvage.",
}


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=15,
    )


def _get_branch(cwd: str) -> str | None:
    result = _run_git(["branch", "--show-current"], cwd)
    return result.stdout.strip() if result.returncode == 0 else None


def _get_new_files(cwd: str) -> list[str]:
    """Get untracked + staged-new files individually (no directory grouping)."""
    files = []
    untracked = _run_git(["ls-files", "--others", "--exclude-standard"], cwd)
    if untracked.returncode == 0:
        files.extend(f.strip() for f in untracked.stdout.splitlines() if f.strip())
    staged = _run_git(["diff", "--cached", "--name-only", "--diff-filter=A"], cwd)
    if staged.returncode == 0:
        files.extend(f.strip() for f in staged.stdout.splitlines() if f.strip())
    return sorted(set(files))


def _get_modified_files(cwd: str) -> list[str]:
    """Get modified tracked files (staged + unstaged)."""
    files = []
    unstaged = _run_git(["diff", "--name-only"], cwd)
    if unstaged.returncode == 0:
        files.extend(f.strip() for f in unstaged.stdout.splitlines() if f.strip())
    staged = _run_git(["diff", "--cached", "--name-only", "--diff-filter=M"], cwd)
    if staged.returncode == 0:
        files.extend(f.strip() for f in staged.stdout.splitlines() if f.strip())
    return sorted(set(files))


def _has_commits_for_ticket(cwd: str, ticket_id: str) -> bool:
    result = _run_git(["log", "--oneline", f"--grep={ticket_id}"], cwd)
    return result.returncode == 0 and bool(result.stdout.strip())


def _has_uncommitted_changes(cwd: str) -> bool:
    result = _run_git(["status", "--porcelain"], cwd)
    if result.returncode != 0:
        return False
    return any(line.strip() for line in result.stdout.splitlines())


def _find_test_files(cwd: str, new_files: list[str], modified_files: list[str]) -> list[str]:
    """Find test files among the worker's changes."""
    all_files = new_files + modified_files
    return [f for f in all_files if f.startswith("tests/") and f.endswith(".py")]


def _run_tests(cwd: str, test_files: list[str]) -> dict:
    """Run pytest on the given test files. Returns summary dict."""
    if not test_files:
        return {"ran": False, "passed": False, "summary": "no test files found"}

    try:
        result = subprocess.run(
            ["python", "-m", "pytest", *test_files, "--tb=short", "-q"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=120,
        )
        output = result.stdout + result.stderr
        passed = result.returncode == 0
        last_line = ""
        for line in output.strip().splitlines():
            if line.strip():
                last_line = line.strip()

        return {
            "ran": True,
            "passed": passed,
            "exit_code": result.returncode,
            "summary": last_line,
        }
    except subprocess.TimeoutExpired:
        return {"ran": True, "passed": False, "summary": "pytest timed out (120s)"}
    except Exception as exc:
        return {"ran": True, "passed": False, "summary": f"pytest error: {exc}"}


def _check_completion_marker(cwd: str, ticket_id: str) -> bool:
    """Check if a completion marker exists for this ticket."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        if result.returncode == 0:
            common_dir = (Path(cwd) / result.stdout.strip()).resolve()
            repo_root = common_dir.parent
        else:
            repo_root = Path(cwd)

        log_path = repo_root / "data" / "cc_completion_log.jsonl"
        if not log_path.exists():
            return False

        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ticket_id") == ticket_id:
                        return True
                except json.JSONDecodeError:
                    continue
        return False
    except Exception:
        return False


def _classify(
    has_commits: bool,
    has_changes: bool,
    new_files: list[str],
    modified_files: list[str],
    test_result: dict,
    has_marker: bool,
) -> str:
    if has_marker:
        return "ALREADY_COMMITTED"

    if not has_changes and not has_commits:
        return "NO_WORK_PRODUCT"

    if has_commits and not has_changes:
        return "ALREADY_COMMITTED"

    all_changed = new_files + modified_files
    has_source = any(
        f.endswith(".py") or f.endswith(".js") or f.endswith(".sql") for f in all_changed
    )
    has_tests = any(f.startswith("tests/") for f in all_changed)

    if not has_source:
        return "NO_WORK_PRODUCT"

    if has_tests and test_result.get("ran") and test_result.get("passed"):
        return "CODE_COMPLETE_TESTS_PASS"

    if has_tests and test_result.get("ran") and not test_result.get("passed"):
        return "CODE_COMPLETE_TESTS_FAIL"

    return "CODE_PARTIAL"


def scan(worktree_path: str, ticket_id: str) -> dict:
    """Scan a worktree and produce a salvage report."""
    cwd = os.path.abspath(worktree_path)

    if not os.path.isdir(cwd):
        return {
            "ticket_id": ticket_id,
            "worktree": cwd,
            "error": f"directory not found: {cwd}",
            "salvage_recommendation": "NO_WORK_PRODUCT",
        }

    branch = _get_branch(cwd)
    new_files = _get_new_files(cwd)
    modified_files = _get_modified_files(cwd)
    has_commits = _has_commits_for_ticket(cwd, ticket_id)
    has_changes = _has_uncommitted_changes(cwd)
    has_marker = _check_completion_marker(cwd, ticket_id)
    test_files = _find_test_files(cwd, new_files, modified_files)
    test_result = _run_tests(cwd, test_files)

    recommendation = _classify(
        has_commits=has_commits,
        has_changes=has_changes,
        new_files=new_files,
        modified_files=modified_files,
        test_result=test_result,
        has_marker=has_marker,
    )

    return {
        "scanned_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ticket_id": ticket_id,
        "worktree": cwd,
        "branch": branch,
        "has_commits": has_commits,
        "has_uncommitted_changes": has_changes,
        "new_files": new_files,
        "modified_files": modified_files,
        "tests_found": test_files,
        "test_result": test_result,
        "completion_marker_found": has_marker,
        "salvage_recommendation": recommendation,
        "recommendation_description": RECOMMENDATIONS.get(recommendation, ""),
    }


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: python tools/salvage_worktree.py <worktree-path> <ticket-id>",
            file=sys.stderr,
        )
        sys.exit(1)

    worktree_path = sys.argv[1]
    ticket_id = sys.argv[2].upper()

    try:
        report = scan(worktree_path, ticket_id)
    except Exception as exc:
        print(json.dumps({"error": str(exc), "ticket_id": ticket_id}))
        sys.exit(1)

    print(json.dumps(report, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
