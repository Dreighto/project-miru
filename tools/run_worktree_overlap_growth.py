#!/usr/bin/env python
"""Targeted dossier growth for cards that already have meta-bearing catalog enrichment.

Finds the intersection of: (1) card codes in card_intelligence, (2) card codes in
the available snapshot(s). Runs dossier growth only for that overlap (both sources),
then rebuilds insights so meta can appear for those cards.

Uses shared overlap logic from tools.miru_worktree_overlap (single source of truth).

Usage:
  python -m tools.run_worktree_overlap_growth [--snapshot PATH]
  --snapshot  Card-list JSON (default: data/snapshots/community_cardlist.json)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent
DATA = PROJECT_ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
DOSSIER_DB = DATA / "miru_learning_dossiers.db"
CATALOG_DB = DATA / "card_catalog.db"

SOURCES = ("community-cardlist", "official-cardlist")
DEFAULT_SNAPSHOT = SNAPSHOTS / "community_cardlist.json"


def run_overlap_growth_and_sync(snapshot_path: Path, overlap_result: dict) -> dict:
    """Run dossier growth for overlap codes (both sources), then rebuild insight sync.

    overlap_result comes from miru_worktree_overlap.compute_overlap(...).
    Returns a report dict (tasks_ok, dossiers_created_refreshed, insight_count_after, etc.).
    """
    overlap_codes = overlap_result.get("overlap_codes") or []
    if not overlap_codes:
        return {
            "meta_bearing_count": overlap_result.get("meta_bearing_count", 0),
            "snapshot_card_count": overlap_result.get("snapshot_card_count", 0),
            "overlap_count": 0,
            "tasks_ok": 0,
            "dossiers_created_refreshed": 0,
        }

    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.miru_learning_engine import build_parser, build_engine_from_args
    from tools.miru_project_sync import run_worktree_card_insight_sync

    queue_db = DATA / "miru_learning_queue.db"
    status_db = DATA / "miru_learning_log.db"
    parser = build_parser()
    parse_args = parser.parse_args([])
    parse_args.queue_db = queue_db
    parse_args.status_db = status_db
    parse_args.dossier_db = DOSSIER_DB
    engine = build_engine_from_args(parse_args)
    payload = {"snapshot_path": str(snapshot_path)}

    tasks_ok = 0
    for card_code in overlap_codes:
        for source_id in SOURCES:
            try:
                result = engine.run_once(
                    card_code=card_code,
                    task_type="verify_official_fields",
                    source_id=source_id,
                    task_payload=payload,
                )
                if result.get("ok") is True:
                    tasks_ok += 1
            except Exception:
                pass

    tasks_attempted = len(overlap_codes) * len(SOURCES)
    report = {
        "meta_bearing_count": overlap_result.get("meta_bearing_count", 0),
        "snapshot_card_count": overlap_result.get("snapshot_card_count", 0),
        "overlap_count": len(overlap_codes),
        "overlap_codes": overlap_codes[:20],
        "snapshot_path": overlap_result.get("snapshot_path", str(snapshot_path)),
        "cards_attempted": len(overlap_codes),
        "tasks_attempted": tasks_attempted,
        "tasks_ok": tasks_ok,
        "tasks_failed": tasks_attempted - tasks_ok,
        "dossiers_created_refreshed": tasks_ok,
    }

    two_source = 0
    if DOSSIER_DB.is_file():
        conn = sqlite3.connect(DOSSIER_DB)
        for code in overlap_codes:
            n = conn.execute(
                "SELECT COUNT(DISTINCT source_id) FROM learning_dossier_sources WHERE card_code = ?",
                (code,),
            ).fetchone()[0]
            if n >= 2:
                two_source += 1
        conn.close()
    report["overlap_cards_with_two_source"] = two_source
    report["two_source_count"] = two_source

    sync_report = run_worktree_card_insight_sync(rebuild=True, limit=None)
    sync = sync_report.get("sync_result") or {}
    by_type = sync.get("by_type") or {}
    report["insight_count_after"] = sync_report.get("insight_count_after", 0)
    report["by_type"] = by_type
    report["meta_insights"] = by_type.get("meta", 0)

    meta_examples = []
    if report["meta_insights"] and CATALOG_DB.is_file():
        conn = sqlite3.connect(CATALOG_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT card_id, insight_type, substr(insight_text,1,80) AS txt FROM miru_card_insights WHERE insight_type = 'meta' LIMIT 5"
        ).fetchall()
        for r in rows:
            meta_examples.append({"card_id": r["card_id"], "preview": (r["txt"] or "")[:80]})
        conn.close()
    report["meta_insight_examples"] = meta_examples

    return report


def main() -> int:
    from tools.miru_worktree_overlap import compute_overlap

    ap = argparse.ArgumentParser(description="Targeted overlap growth: dossiers for meta-bearing cards in snapshot.")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT), help="Path to card-list JSON")
    args = ap.parse_args()
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = PROJECT_ROOT / snapshot_path

    overlap_result = compute_overlap(snapshot_path, CATALOG_DB)
    if overlap_result["overlap_count"] == 0:
        report = {
            "meta_bearing_count": overlap_result["meta_bearing_count"],
            "snapshot_card_count": overlap_result["snapshot_card_count"],
            "overlap_count": 0,
            "overlap_codes": [],
            "snapshot_path": overlap_result["snapshot_path"],
            "blocker": overlap_result["blocker"],
            "snapshot_gap": (
                "The current snapshot has only the cards listed in snapshot_card_count. "
                "The meta-bearing codes (from card_intelligence / deck-intel enrichment) are not in this file."
            ),
            "sample_meta_bearing_codes": overlap_result.get("sample_meta_bearing_codes", []),
            "exact_snapshot_needed": overlap_result.get("exact_snapshot_needed"),
            "next_step": "Add or replace snapshot with one that contains meta-bearing codes; re-run overlap growth.",
        }
        print(json.dumps(report, indent=2))
        return 0

    report = run_overlap_growth_and_sync(snapshot_path, overlap_result)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
