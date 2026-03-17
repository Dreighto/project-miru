#!/usr/bin/env python
"""
One-off worktree learner recovery: stop all duplicate learner processes,
clear PID file, then report. Run from worktree root. Does not start server or learner.
Use after running: restart Miru AI server (--port 18765) and start learner via Dev.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Run from repo root so tools and data paths resolve
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.miru_ai_server import (
    _list_worktree_learner_process_ids,
    _stop_worktree_learner_process,
    _read_worktree_learner_pid,
    _is_worktree_runtime,
)

def main() -> int:
    if not _is_worktree_runtime():
        print("Not worktree runtime (expected when run as script). Proceeding with worktree data paths.")
    before = _list_worktree_learner_process_ids()
    print(f"BEFORE: learner process count = {len(before)}, PIDs = {before}")
    ok, msg = _stop_worktree_learner_process()
    print(f"STOP: ok={ok}, message={msg}")
    after = _list_worktree_learner_process_ids()
    print(f"AFTER: learner process count = {len(after)}, PIDs = {after}")
    record = _read_worktree_learner_pid()
    print(f"PID file after stop: {record}")
    return 0 if len(after) == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
