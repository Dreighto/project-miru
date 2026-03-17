#!/usr/bin/env python
"""Worktree worker: one-shot operator cycle (overlap, bulk, sync_only).

Stage 3: Overlap-aware stop, no-new-work short-circuit, structured reporting, --log-run (miru_worker_last_run.json + miru_worker_runs.jsonl).
Run_once-based; no queue seeding or daemon. Safe to call from Task Scheduler.

Usage:
  python -m tools.run_worktree_worker --mode overlap [--snapshot PATH] [--log-run]
  python -m tools.run_worktree_worker --mode bulk [--snapshot PATH] [--limit N] [--log-run]
  python -m tools.run_worktree_worker --mode sync_only [--log-run]
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent
DATA = PROJECT_ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
DEFAULT_SNAPSHOT = SNAPSHOTS / "community_cardlist.json"
CATALOG_DB = DATA / "card_catalog.db"
DOSSIER_DB = DATA / "miru_learning_dossiers.db"
WORKER_STATE_PATH = DATA / "miru_worker_state.json"
WORKER_LAST_RUN_PATH = DATA / "miru_worker_last_run.json"
WORKER_RUNS_JSONL_PATH = DATA / "miru_worker_runs.jsonl"


def _write_run_log(report: dict) -> None:
    """Persist this run to worktree-local log (latest JSON + append JSONL). Scheduler visibility."""
    record = dict(report)
    record["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    DATA.mkdir(parents=True, exist_ok=True)
    with open(WORKER_LAST_RUN_PATH, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    with open(WORKER_RUNS_JSONL_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _resolve_snapshot(path: Path) -> Path:
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _read_worker_state() -> dict:
    """Load worker state from worktree data dir. Returns {} if missing or invalid."""
    if not WORKER_STATE_PATH.is_file():
        return {}
    try:
        with open(WORKER_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_worker_state(state: dict) -> None:
    """Persist worker state under worktree data dir."""
    WORKER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(WORKER_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def _overlap_signature(overlap_codes: list[str]) -> str:
    """Stable hash of overlap set for no-new-work check."""
    return hashlib.sha256("|".join(sorted(overlap_codes)).encode()).hexdigest()


def _check_no_new_work(snapshot_path: Path, overlap_result: dict) -> tuple[bool, str | None, dict | None]:
    """If same snapshot and same overlap were already processed, return (True, reason, state). Else (False, None, None)."""
    state = _read_worker_state()
    overlap_state = state.get("overlap") or {}
    try:
        mtime = snapshot_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    path_str = str(snapshot_path.resolve())
    count = overlap_result.get("overlap_count", 0)
    codes = overlap_result.get("overlap_codes") or []
    sig = _overlap_signature(codes) if codes else ""

    if (
        path_str == overlap_state.get("snapshot_path")
        and mtime == overlap_state.get("snapshot_mtime")
        and count == overlap_state.get("overlap_count")
        and sig == overlap_state.get("overlap_signature")
    ):
        reason = (
            "Same snapshot and overlap set already processed; snapshot mtime and overlap unchanged."
        )
        return True, reason, overlap_state
    return False, None, None


def _write_overlap_state(snapshot_path: Path, overlap_result: dict) -> None:
    """Record successful overlap run so next run can short-circuit if unchanged."""
    try:
        mtime = snapshot_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    state = _read_worker_state()
    state["overlap"] = {
        "snapshot_path": str(snapshot_path.resolve()),
        "snapshot_mtime": mtime,
        "overlap_count": overlap_result.get("overlap_count", 0),
        "overlap_signature": _overlap_signature(overlap_result.get("overlap_codes") or []),
        "last_run": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    _write_worker_state(state)


def _run_overlap_mode(snapshot_path: Path) -> dict:
    from tools.miru_worktree_overlap import compute_overlap, good_next_learning_targets
    from tools.run_worktree_overlap_growth import run_overlap_growth_and_sync

    overlap_result = compute_overlap(snapshot_path, CATALOG_DB)
    if overlap_result["overlap_count"] == 0:
        targets = good_next_learning_targets(CATALOG_DB, DOSSIER_DB, limit=10)
        return _report(
            mode="overlap",
            action="overlap_blocked",
            overlap_count=0,
            blocker=overlap_result["blocker"],
            meta_bearing_count=overlap_result["meta_bearing_count"],
            snapshot_card_count=overlap_result["snapshot_card_count"],
            sample_meta_bearing_codes=overlap_result.get("sample_meta_bearing_codes", []),
            exact_snapshot_needed=overlap_result.get("exact_snapshot_needed"),
            next_learning_targets=targets,
        )
    no_work, reason, prev_state = _check_no_new_work(snapshot_path, overlap_result)
    if no_work and reason:
        return _report(
            mode="overlap",
            action="no_new_work",
            overlap_count=overlap_result["overlap_count"],
            blocker=None,
            no_new_work_reason=reason,
            snapshot_path=str(snapshot_path.resolve()),
            last_run=prev_state.get("last_run") if prev_state else None,
        )
    report = run_overlap_growth_and_sync(snapshot_path, overlap_result)
    _write_overlap_state(snapshot_path, overlap_result)
    return _report(
        mode="overlap",
        action="overlap_growth_and_sync",
        overlap_count=report.get("overlap_count", 0),
        blocker=None,
        cards_attempted=report.get("cards_attempted"),
        tasks_ok=report.get("tasks_ok"),
        tasks_failed=report.get("tasks_failed"),
        dossiers_created_refreshed=report.get("dossiers_created_refreshed"),
        two_source_count=report.get("two_source_count") or report.get("overlap_cards_with_two_source"),
        insight_count_after=report.get("insight_count_after"),
        by_type=report.get("by_type"),
        snapshot_path=report.get("snapshot_path"),
        meta_insights=report.get("meta_insights"),
        meta_insight_examples=report.get("meta_insight_examples"),
    )


def _report(
    mode: str,
    action: str,
    *,
    overlap_count: int | None = None,
    blocker: str | None = None,
    no_new_work_reason: str | None = None,
    cards_attempted: int | None = None,
    tasks_ok: int | None = None,
    tasks_failed: int | None = None,
    dossiers_created_refreshed: int | None = None,
    two_source_count: int | None = None,
    insight_count_after: int | None = None,
    by_type: dict | None = None,
    snapshot_path: str | None = None,
    last_run: str | None = None,
    meta_bearing_count: int | None = None,
    snapshot_card_count: int | None = None,
    sample_meta_bearing_codes: list | None = None,
    exact_snapshot_needed: str | None = None,
    meta_insights: int | None = None,
    meta_insight_examples: list | None = None,
    **extra: object,
) -> dict:
    """Build a structured worker report. Omit optional keys when None to keep output clean."""
    out = {"mode": mode, "action": action}
    if overlap_count is not None:
        out["overlap_count"] = overlap_count
    out["blocker"] = blocker
    if no_new_work_reason is not None:
        out["no_new_work_reason"] = no_new_work_reason
    if cards_attempted is not None:
        out["cards_attempted"] = cards_attempted
    if tasks_ok is not None:
        out["tasks_ok"] = tasks_ok
    if tasks_failed is not None:
        out["tasks_failed"] = tasks_failed
    if dossiers_created_refreshed is not None:
        out["dossiers_created_refreshed"] = dossiers_created_refreshed
    if two_source_count is not None:
        out["two_source_count"] = two_source_count
    if insight_count_after is not None:
        out["insight_count_after"] = insight_count_after
    if by_type is not None:
        out["by_type"] = by_type
    if snapshot_path is not None:
        out["snapshot_path"] = snapshot_path
    if last_run is not None:
        out["last_run"] = last_run
    if meta_bearing_count is not None:
        out["meta_bearing_count"] = meta_bearing_count
    if snapshot_card_count is not None:
        out["snapshot_card_count"] = snapshot_card_count
    if sample_meta_bearing_codes is not None:
        out["sample_meta_bearing_codes"] = sample_meta_bearing_codes
    if exact_snapshot_needed is not None:
        out["exact_snapshot_needed"] = exact_snapshot_needed
    if meta_insights is not None:
        out["meta_insights"] = meta_insights
    if meta_insight_examples is not None:
        out["meta_insight_examples"] = meta_insight_examples
    for k, v in extra.items():
        if v is not None:
            out[k] = v
    return out


def _run_bulk_mode(snapshot_path: Path, limit: int | None) -> dict:
    from tools.run_worktree_bulk_dossier_growth import _run_growth
    from tools.miru_project_sync import run_worktree_card_insight_sync

    growth = _run_growth(snapshot_path, limit)
    sync_report = run_worktree_card_insight_sync(rebuild=True, limit=None)
    sync = sync_report.get("sync_result") or {}
    by_type = sync.get("by_type") or {}
    tasks_processed = growth.get("tasks_processed", 0)
    tasks_ok = growth.get("tasks_ok", 0)
    return _report(
        mode="bulk",
        action="bulk_growth_and_sync",
        overlap_count=None,
        blocker=None,
        cards_attempted=growth.get("cards_in_batch"),
        tasks_ok=tasks_ok,
        tasks_failed=(tasks_processed - tasks_ok) if tasks_processed is not None else None,
        dossiers_created_refreshed=tasks_ok,
        two_source_count=growth.get("two_source_cards"),
        insight_count_after=sync_report.get("insight_count_after", 0),
        by_type=by_type,
        snapshot_path=str(snapshot_path.resolve()),
        dossier_count_after=growth.get("dossier_count_after"),
        meta_insights=by_type.get("meta", 0),
    )


def _run_sync_only_mode() -> dict:
    from tools.miru_project_sync import run_worktree_card_insight_sync

    report = run_worktree_card_insight_sync(rebuild=False, limit=None)
    sync = report.get("sync_result") or {}
    status = sync.get("status") or {}
    return _report(
        mode="sync_only",
        action="sync_only",
        blocker=None,
        insight_count_after=report.get("insight_count_after"),
        by_type=sync.get("by_type"),
        sync_ok=sync.get("ok"),
        insight_count=status.get("insight_count"),
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Worktree worker: one-shot operator cycle (overlap, bulk, sync_only). Stage 3; scheduler-safe output."
    )
    ap.add_argument(
        "--mode",
        choices=("overlap", "bulk", "sync_only"),
        required=True,
        help="overlap=default for scheduling (overlap-aware growth+sync or blocker); bulk=manual-only; sync_only=sync only",
    )
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT), help="Path to card-list JSON (overlap/bulk)")
    ap.add_argument("--limit", type=int, default=None, help="Max cards for bulk mode (default: all)")
    ap.add_argument("--log-run", action="store_true", help="Write run result to data/miru_worker_last_run.json and append to miru_worker_runs.jsonl")
    args = ap.parse_args()

    from tools.miru_learner_config import get_learner_mode, is_sandbox_or_dry_run
    if is_sandbox_or_dry_run() and args.mode != "sync_only":
        print(json.dumps({
            "warning": "DRY_RUN active but worker will still write insights",
            "mode": get_learner_mode(),
            "note": "rebuild=True sync is mode-agnostic"
        }), file=sys.stderr)

    if args.mode == "sync_only":
        report = _run_sync_only_mode()
        print(json.dumps(report, indent=2))
        if args.log_run:
            _write_run_log(report)
        return 0

    snapshot_path = _resolve_snapshot(Path(args.snapshot))
    if not snapshot_path.is_file():
        report = _report(
            mode=args.mode,
            action="error",
            blocker=f"Snapshot not found: {snapshot_path}",
            snapshot_path=str(snapshot_path),
        )
        print(json.dumps(report, indent=2), file=sys.stderr)
        if args.log_run:
            _write_run_log(report)
        return 1

    if args.mode == "overlap":
        report = _run_overlap_mode(snapshot_path)
        print(json.dumps(report, indent=2))
        if args.log_run:
            _write_run_log(report)
        return 0

    if args.mode == "bulk":
        report = _run_bulk_mode(snapshot_path, args.limit)
        print(json.dumps(report, indent=2))
        if args.log_run:
            _write_run_log(report)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
