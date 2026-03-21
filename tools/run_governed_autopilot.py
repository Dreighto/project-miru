#!/usr/bin/env python
"""Governed autopilot: safe scheduled loop that runs the governed batch ingestion in the worktree.

Reuses run_governed_batch_test only. Lock prevents overlapping runs. Tracks surfaced review items
so repeated identical items don't spam the rollup. Writes a daily plain-English rollup for operators.

Usage:
  python -m tools.run_governed_autopilot --csv data/staging/op_format_banlist_intake.csv --source-id official --interval 3600
  python -m tools.run_governed_autopilot --csv data/staging/op_format_banlist_intake.csv --source-id official --once
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_CSV = DATA_DIR / "staging" / "op_format_banlist_intake.csv"
LOCK_PATH = DATA_DIR / "governed_autopilot.lock"
SURFACED_PATH = DATA_DIR / "governed_autopilot_surfaced.json"
ROLLUP_JSON_PREFIX = "governed_autopilot_rollup_"
ROLLUP_TXT_PREFIX = "governed_autopilot_rollup_"
DEFAULT_INTERVAL_SEC = 3600
MAX_SURFACED_AGE_DAYS = 7


def _review_sig(item: dict[str, Any]) -> str:
    """Stable signature for dedupe: same card + state + reason = same item."""
    ident = (item.get("item_identifier") or "").strip()
    state = (item.get("proposed_state") or item.get("why_escalated") or "").strip()
    why = (item.get("why_escalated") or item.get("decision_reason") or "").strip()
    return hashlib.sha256(f"{ident}|{state}|{why}".encode()).hexdigest()


def _load_surfaced() -> list[dict[str, Any]]:
    if not SURFACED_PATH.is_file():
        return []
    try:
        data = json.loads(SURFACED_PATH.read_text(encoding="utf-8"))
        return list(data.get("items") or [])
    except Exception:
        return []


def _save_surfaced(items: list[dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Prune old entries
    cutoff = datetime.now(timezone.utc).timestamp() - (MAX_SURFACED_AGE_DAYS * 86400)
    kept = [x for x in items if (x.get("first_seen_ts") or 0) > cutoff]
    SURFACED_PATH.write_text(
        json.dumps({"items": kept, "updated_at": datetime.now(timezone.utc).isoformat()}, indent=2),
        encoding="utf-8",
    )


def _count_newly_surfaced(review_items: list[dict[str, Any]]) -> tuple[int, list[dict[str, Any]]]:
    """Return (new_count, updated_surfaced_list). New = sig not seen before."""
    seen = _load_surfaced()
    seen_sigs = {s["sig"] for s in seen}
    now_ts = datetime.now(timezone.utc).timestamp()
    now_iso = datetime.now(timezone.utc).isoformat()
    new_count = 0
    for r in review_items:
        sig = _review_sig(r)
        if sig not in seen_sigs:
            new_count += 1
            seen_sigs.add(sig)
            seen.append({
                "sig": sig,
                "item_identifier": r.get("item_identifier", ""),
                "proposed_state": r.get("proposed_state", ""),
                "why_escalated": r.get("why_escalated", ""),
                "first_seen": now_iso,
                "first_seen_ts": now_ts,
            })
    return new_count, seen


def _acquire_lock() -> bool:
    """Create lock file with PID. Returns True if we got the lock."""
    if LOCK_PATH.is_file():
        try:
            data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            pid = int(data.get("pid") or 0)
            if pid and _pid_alive(pid):
                return False
        except Exception:
            pass
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        LOCK_PATH.write_text(
            json.dumps({
                "pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).isoformat(),
            }, indent=0),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def _release_lock() -> None:
    try:
        if LOCK_PATH.is_file():
            LOCK_PATH.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _update_rollup(
    report: dict[str, Any],
    newly_surfaced: int,
    run_skipped_overlap: bool,
    rollup: dict[str, Any],
) -> dict[str, Any]:
    """Merge this run into the day's rollup. Returns updated rollup."""
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if rollup.get("date") != date_key:
        rollup = {"date": date_key, "runs": 0, "runs_skipped_overlap": 0, "total_auto_applied": 0, "newly_surfaced_review": 0, "total_skipped": 0, "total_upcoming_stored": 0, "failure_count": 0, "partial_count": 0, "open_review_items": [], "last_run_ts": "", "last_summary_path": ""}
    rollup["runs"] = rollup.get("runs", 0) + (0 if run_skipped_overlap else 1)
    if run_skipped_overlap:
        rollup["runs_skipped_overlap"] = rollup.get("runs_skipped_overlap", 0) + 1
    else:
        rollup["total_auto_applied"] = rollup.get("total_auto_applied", 0) + report.get("total_auto_applied", 0)
        rollup["newly_surfaced_review"] = rollup.get("newly_surfaced_review", 0) + newly_surfaced
        rollup["total_skipped"] = rollup.get("total_skipped", 0) + report.get("total_skipped", 0)
        rollup["total_upcoming_stored"] = rollup.get("total_upcoming_stored", 0) + report.get("total_upcoming_stored", 0)
        status = report.get("graduation_status", "")
        if status == "failed":
            rollup["failure_count"] = rollup.get("failure_count", 0) + 1
        elif status == "partial_success":
            rollup["partial_count"] = rollup.get("partial_count", 0) + 1
        rollup["last_run_ts"] = report.get("batch_run_ts", "")
        rollup["last_summary_path"] = report.get("summary_path", "")
        for r in report.get("review_queue") or []:
            ident = r.get("item_identifier", "")
            state = r.get("proposed_state", "")
            if ident and not any(x.get("item_identifier") == ident and x.get("proposed_state") == state for x in rollup.get("open_review_items", [])):
                rollup.setdefault("open_review_items", []).append({
                    "item_identifier": ident,
                    "proposed_state": state,
                    "simple_title": r.get("simple_title", "Item for review"),
                    "simple_reason": r.get("simple_reason", ""),
                    "why_it_matters": r.get("why_it_matters", ""),
                    "confidence_note": r.get("confidence_note", ""),
                    "suggested_action": r.get("suggested_action", ""),
                    "why_escalated": r.get("why_escalated", ""),
                    "source_id": r.get("source_id", ""),
                })
    return rollup


def _load_rollup() -> dict[str, Any]:
    date_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = DATA_DIR / f"{ROLLUP_JSON_PREFIX}{date_key}.json"
    if not path.is_file():
        return {"date": date_key, "runs": 0, "runs_skipped_overlap": 0, "total_auto_applied": 0, "newly_surfaced_review": 0, "total_skipped": 0, "total_upcoming_stored": 0, "failure_count": 0, "partial_count": 0, "open_review_items": [], "last_run_ts": "", "last_summary_path": ""}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"date": date_key, "runs": 0, "runs_skipped_overlap": 0, "total_auto_applied": 0, "newly_surfaced_review": 0, "total_skipped": 0, "total_upcoming_stored": 0, "failure_count": 0, "partial_count": 0, "open_review_items": [], "last_run_ts": "", "last_summary_path": ""}


def _write_rollup(rollup: dict[str, Any]) -> None:
    date_key = rollup.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    jpath = DATA_DIR / f"{ROLLUP_JSON_PREFIX}{date_key}.json"
    tpath = DATA_DIR / f"{ROLLUP_TXT_PREFIX}{date_key}.txt"
    jpath.write_text(json.dumps(rollup, indent=2), encoding="utf-8")
    lines = [
        f"Governed autopilot rollup for {date_key}",
        "",
        "Is Miru still operating safely in the worktree?",
        "Yes. All writes stay in this worktree only. Ethics and provenance rules stay on.",
        "",
        f"Governed runs: {rollup.get('runs', 0)} (runs skipped because another was already in progress: {rollup.get('runs_skipped_overlap', 0)})",
        f"Safe updates applied today: {rollup.get('total_auto_applied', 0)}",
        f"New items that need your review (first time surfaced today): {rollup.get('newly_surfaced_review', 0)}",
        f"Duplicates skipped (quiet, not spammed): {rollup.get('total_skipped', 0)}",
        "",
    ]
    fc = rollup.get("failure_count", 0)
    pc = rollup.get("partial_count", 0)
    if fc:
        lines.append("Something failed or was blocked in at least one run. Check the latest report and summary for that run.")
    elif pc:
        lines.append("At least one run was partial (e.g. nothing to apply and nothing to review). That is normal when everything is already up to date.")
    else:
        lines.append("No failures. Partial runs (all skipped) are normal when there is nothing new to apply.")
    lines.append("")
    open_items = rollup.get("open_review_items") or []
    if open_items:
        lines.append("What still needs your attention:")
        for x in open_items:
            lines.append(f"  - {x.get('item_identifier', '')} ({x.get('proposed_state', '')}): {x.get('simple_title', '')}")
    else:
        lines.append("What still needs your attention: Nothing right now.")
    lines.append("")
    if rollup.get("last_summary_path"):
        lines.append(f"Latest run summary: {rollup['last_summary_path']}")
    tpath.write_text("\n".join(lines), encoding="utf-8")


def run_one(
    csv_path: Path,
    source_id: str,
    catalog_path: Path,
    data_dir: Path,
    dry_run: bool,
) -> tuple[dict[str, Any] | None, bool]:
    """Run governed batch once. Returns (report, run_skipped_overlap). report is None if skipped."""
    if not _acquire_lock():
        return None, True
    try:
        from tools.run_governed_batch_test import run_batch
        from tools.miru_regulation import OFFICIAL_LEGALITY_SOURCE_IDS

        if source_id.strip() not in OFFICIAL_LEGALITY_SOURCE_IDS:
            return None, False
        report = run_batch(
            csv_path=csv_path,
            source_id=source_id,
            catalog_path=catalog_path,
            dry_run=dry_run,
            data_dir=data_dir,
        )
        review_items = report.get("review_queue") or []
        new_count, updated_surfaced = _count_newly_surfaced(review_items)
        _save_surfaced(updated_surfaced)
        rollup = _load_rollup()
        rollup = _update_rollup(report, new_count, False, rollup)
        _write_rollup(rollup)
        return report, False
    finally:
        _release_lock()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Governed autopilot: scheduled governed batch in the worktree.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="Staging CSV path.")
    parser.add_argument("--source-id", default="official", help="Source ID (must be in official legality allowlist).")
    parser.add_argument("--catalog", type=Path, default=PROJECT_ROOT / "data" / "card_catalog.db", help="Catalog DB.")
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR, help="Data dir for reports and rollup.")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC, help="Seconds between runs (loop).")
    parser.add_argument("--once", action="store_true", help="Run once and exit (no loop).")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to catalog.")
    args = parser.parse_args()

    from tools.miru_regulation import OFFICIAL_LEGALITY_SOURCE_IDS

    sid = (args.source_id or "").strip()
    if sid not in OFFICIAL_LEGALITY_SOURCE_IDS:
        print(f"ERROR: --source-id must be one of {sorted(OFFICIAL_LEGALITY_SOURCE_IDS)}.", file=sys.stderr)
        return 1

    csv_path = Path(args.csv)
    if not csv_path.is_file():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 1

    def _record_skip() -> None:
        rollup = _load_rollup()
        rollup["runs_skipped_overlap"] = rollup.get("runs_skipped_overlap", 0) + 1
        _write_rollup(rollup)

    if args.once:
        report, skipped = run_one(
            csv_path=csv_path,
            source_id=sid,
            catalog_path=Path(args.catalog),
            data_dir=Path(args.data_dir),
            dry_run=args.dry_run,
        )
        if skipped:
            print("Governed autopilot: run skipped (another governed batch was already in progress).", file=sys.stderr)
            _record_skip()
            return 0
        if report:
            print("Governed autopilot: one run completed.", file=sys.stderr)
            print("  report :", report.get("report_path", ""), file=sys.stderr)
            print("  summary:", report.get("summary_path", ""), file=sys.stderr)
        return 0

    # Loop
    interval = max(60.0, float(args.interval))
    print(f"Governed autopilot: starting loop (interval={interval}s). Worktree-only. Ctrl+C to stop.", file=sys.stderr)
    try:
        while True:
            report, skipped = run_one(
                csv_path=csv_path,
                source_id=sid,
                catalog_path=Path(args.catalog),
                data_dir=Path(args.data_dir),
                dry_run=args.dry_run,
            )
            if skipped:
                _record_skip()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("Governed autopilot: stopped.", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
