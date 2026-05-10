"""
clean_worktree.py — Pre-flight auto-clean for gitignored worktree artifacts (PRO-316).

Removes known-safe gitignored directories that block the worktree cleanliness
gate. Runs BEFORE check_worktree_clean.py in the pre-flight sequence.

Only removes directories that git confirms are ignored. Never touches tracked
files or staged changes.

Usage:
    python tools/clean_worktree.py            # operates on current working dir
    python tools/clean_worktree.py --cwd PATH # operates on PATH

The --cwd flag (PRO-338, 2026-05-10) lets the dispatch_listener invoke this
script from project-miru's REPO_ROOT while pointing it at any worker worktree
— including non-miru repos like LogueOS-Console that don't have tools/ on their
own filesystem. Without --cwd, the dispatcher would have to chdir into each
worktree (and hope tools/ exists there), which it doesn't for multi-repo dispatch.

Exit codes:
    0 = cleanup succeeded (or nothing to clean)
    1 = unexpected error during cleanup
    2 = invalid --cwd argument (path doesn't exist or isn't a directory)
"""

from __future__ import annotations

import argparse
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
    except Exception:
        return False
    else:
        return result.returncode == 0


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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Auto-clean gitignored worktree artifacts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cwd",
        default=None,
        help=(
            "Operate on this directory instead of the current working dir. "
            "Required when the script is invoked from outside the target worktree "
            "(e.g. dispatch_listener pointing at a multi-repo worker slot)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    cwd = args.cwd if args.cwd is not None else os.getcwd()
    if not os.path.isdir(cwd):
        # Don't proceed against a non-existent path — the script's git-check-ignore
        # call would silently fail, returning nothing to clean and masking the bug.
        print(f"CLEAN_ERROR: --cwd path is not a directory: {cwd}", file=sys.stderr)
        sys.exit(2)

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
