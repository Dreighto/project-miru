"""
check_kill_switch.py — check if data/system_halt exists in the main repo.

Works from any git worktree (miru-w1, miru-w2, etc.) — always checks the
main repo's data/ directory, not the worktree-local one.

Exit code 0 = clear to proceed.
Exit code 1 = kill switch active, stop immediately.

Usage in pre-flight:
    python tools/check_kill_switch.py || exit 1
"""

import os
import subprocess
import sys


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


def main() -> None:
    halt_path = os.path.join(_repo_root(), "data", "system_halt")
    if os.path.exists(halt_path):
        print("KILL_SWITCH_ACTIVE", file=sys.stderr)
        sys.exit(1)
    else:
        print("CLEAR", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
