"""
check_worktree_clean.py — verify the current worktree is clean before starting a dispatch.

Checks that:
  1. The current branch is main (or a clean feature branch with no uncommitted tracked changes).
  2. No staged or unstaged changes to tracked files exist.
  3. No untracked files in the repo root (files in data/, logs/, tests/_tmp/ are excluded).

Works from any git worktree (miru-w1, miru-w2, etc.).

Exit code 0 = clean, clear to proceed.
Exit code 1 = dirty worktree, stop and report.

Usage in pre-flight:
    python tools/check_worktree_clean.py || exit 1
"""

import os
import subprocess
import sys


def _cwd() -> str:
    """Return the current working directory (the worktree root when invoked via 'python tools/...')."""
    return os.getcwd()


def _run_git(*args, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=10,
    )


def main() -> None:
    cwd = _cwd()

    # Check for staged or unstaged changes to tracked files
    status = _run_git("status", "--porcelain", cwd=cwd)
    if status.returncode != 0:
        print(f"DIRTY: git status failed: {status.stderr.strip()}", file=sys.stderr)
        sys.exit(1)

    dirty_lines = []
    for line in status.stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        # Ignore untracked files in known-safe dirs (data/, logs/, tests/_tmp/)
        if code.strip() == "??" and (
            path.startswith("data/") or path.startswith("logs/") or path.startswith("tests/_tmp/")
        ):
            continue
        dirty_lines.append(line)

    if dirty_lines:
        print("DIRTY: uncommitted changes in worktree:", file=sys.stderr)
        for line in dirty_lines:
            print(f"  {line}", file=sys.stderr)
        print(
            "Action required: stash or commit in-progress work, then run"
            " 'git checkout main' before next dispatch.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("CLEAN", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
