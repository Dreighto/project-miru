"""
clean_worktree.py — Pre-flight auto-clean for gitignored worktree artifacts (PRO-316).

Removes known-safe gitignored directories that block the worktree cleanliness
gate. Runs BEFORE check_worktree_clean.py in the pre-flight sequence.

Only removes directories that git confirms are ignored. Never touches tracked
files or staged changes.

Usage:
    python tools/clean_worktree.py

Exit codes:
    0 = cleanup succeeded (or nothing to clean)
    1 = unexpected error during cleanup
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

KNOWN_SAFE_DIRS = [
    "test-results",
    "playwright-report",
    ".pytest_cache",
    "__pycache__",
    "node_modules/.cache",
]


def _find_pycache_dirs(root: str) -> list[str]:
    """Find all __pycache__ directories recursively."""
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        if "__pycache__" in dirnames:
            found.append(os.path.join(dirpath, "__pycache__"))
    return found


def _is_gitignored(path: str, cwd: str) -> bool:
    """Check if a path is gitignored via git check-ignore."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", path],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def clean(cwd: str | None = None) -> dict[str, list[str]]:
    """Remove known-safe gitignored directories from the worktree.

    Returns {"cleaned": [...], "skipped": [...], "errors": [...]}.
    """
    cwd = cwd or os.getcwd()
    cleaned: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    candidates: list[str] = []
    for dirname in KNOWN_SAFE_DIRS:
        if dirname == "__pycache__":
            candidates.extend(_find_pycache_dirs(cwd))
        else:
            full = os.path.join(cwd, dirname)
            if os.path.isdir(full):
                candidates.append(full)

    for full_path in candidates:
        rel = os.path.relpath(full_path, cwd)
        if not _is_gitignored(rel, cwd):
            skipped.append(rel)
            continue
        try:
            shutil.rmtree(full_path)
            cleaned.append(rel)
        except Exception as exc:
            errors.append(f"{rel}: {exc}")

    return {"cleaned": cleaned, "skipped": skipped, "errors": errors}


def main() -> None:
    cwd = os.getcwd()
    result = clean(cwd)

    print(json.dumps(result, indent=2), file=sys.stderr)

    if result["errors"]:
        print(f"CLEAN_ERRORS: {len(result['errors'])} errors during cleanup", file=sys.stderr)
        sys.exit(1)

    if result["cleaned"]:
        print(f"CLEANED: removed {len(result['cleaned'])} gitignored artifacts", file=sys.stderr)
    else:
        print("CLEAN: nothing to remove", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
