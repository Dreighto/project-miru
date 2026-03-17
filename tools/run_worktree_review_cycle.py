#!/usr/bin/env python
"""Run one learner cycle that enqueues a verify_official_fields task and processes it.
In REVIEW_REQUIRED mode this adds one item to the learner_review_queue for approval on the Dev page.
Uses worktree data paths and an approved card-list snapshot (community-cardlist or official-cardlist).

Operator workflow (review-first):
  1. Start stack:  .\\windows\\start_op_miru_worktree.ps1 -Native
  2. Seed review:  python tools/run_worktree_review_cycle.py [CARD_CODE] [SOURCE_ID] [SNAPSHOT_PATH]
  3. Open Dev:     http://127.0.0.1:18765/dev  -> Pending Approvals -> Approve/Reject
  4. Sync:         Use "Sync Insights" on Dev or stop learner to run post-stop sync
  5. Inspect:      http://127.0.0.1:18080/  (worktree dashboard)

Usage: python tools/run_worktree_review_cycle.py [CARD_CODE] [SOURCE_ID] [SNAPSHOT_PATH_OVERRIDE]
  Default CARD_CODE: OP01-001
  Default SOURCE_ID: community-cardlist (use official-cardlist for second source / two-source baseline)
  Optional SNAPSHOT_PATH_OVERRIDE: e.g. data/snapshots/community_cardlist.json to use one file for both sources when testing
Examples:
  python tools/run_worktree_review_cycle.py OP01-001
  python tools/run_worktree_review_cycle.py OP01-001 official-cardlist
  python tools/run_worktree_review_cycle.py EB04-001 official-cardlist data/snapshots/community_cardlist.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent
DATA = PROJECT_ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
QUEUE_DB = DATA / "miru_learning_queue.db"
STATUS_DB = DATA / "miru_learning_log.db"
DOSSIER_DB = DATA / "miru_learning_dossiers.db"

# Default snapshot path per approved source_id (worktree convention).
DEFAULT_SNAPSHOT_PATHS: dict[str, Path] = {
    "community-cardlist": SNAPSHOTS / "community_cardlist.json",
    "official-cardlist": SNAPSHOTS / "official_cardlist.json",
}


def main() -> int:
    card = (sys.argv[1] if len(sys.argv) > 1 else "OP01-001").strip().upper()
    source_id = (sys.argv[2] if len(sys.argv) > 2 else "community-cardlist").strip().lower()
    path_override = sys.argv[3].strip() if len(sys.argv) > 3 else None

    if not card:
        print("Usage: python tools/run_worktree_review_cycle.py [CARD_CODE] [SOURCE_ID] [SNAPSHOT_PATH_OVERRIDE]", file=sys.stderr)
        return 1

    snapshot_path: Path
    if path_override:
        snapshot_path = (PROJECT_ROOT / path_override) if not Path(path_override).is_absolute() else Path(path_override)
    else:
        snapshot_path = DEFAULT_SNAPSHOT_PATHS.get(source_id) or SNAPSHOTS / "community_cardlist.json"

    if not snapshot_path.is_file():
        print(f"Snapshot not found: {snapshot_path}", file=sys.stderr)
        print(f"  Source: {source_id}. Use optional third arg to point to an existing snapshot for testing.", file=sys.stderr)
        return 1

    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.miru_learning_engine import build_parser
    from tools.miru_learning_engine import build_engine_from_args

    parser = build_parser()
    args = parser.parse_args([])
    args.queue_db = QUEUE_DB
    args.status_db = STATUS_DB
    args.dossier_db = DOSSIER_DB
    engine = build_engine_from_args(args)
    result = engine.run_once(
        card_code=card,
        task_type="verify_official_fields",
        source_id=source_id,
        task_payload={"snapshot_path": str(snapshot_path)},
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    sys.exit(main())
