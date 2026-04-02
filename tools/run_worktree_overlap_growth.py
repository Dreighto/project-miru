#!/usr/bin/env python
"""Targeted dossier growth for cards that already have meta-bearing catalog enrichment.

Finds the intersection of: (1) card codes in card_intelligence, (2) card codes in
the available snapshot(s). Runs dossier growth only for that overlap (both sources),
then rebuilds insights so meta can appear for those cards.

Uses shared overlap logic from tools.miru_worktree_overlap (single source of truth).

Usage:
  python -m tools.run_worktree_overlap_growth [--snapshot PATH]
  --snapshot  Card-list JSON (default: data/snapshots/onepiece_cardgame_dev.json)
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

SOURCES = ("onepiece-cardgame-dev", "official-cardlist")
DEFAULT_SNAPSHOT = SNAPSHOTS / "onepiece_cardgame_dev.json"


def _write_result_file(
    preflight: dict,
    report: dict,
    *,
    overlap_meta: dict | None = None,
) -> None:
    """Persist full operator-visible output for audit (UTF-8)."""
    try:
        oc_full = list((overlap_meta or {}).get("overlap_codes") or [])
        overlap_compact = dict(overlap_meta or {})
        if "overlap_codes" in overlap_compact:
            overlap_compact["overlap_codes_total"] = len(oc_full)
            overlap_compact["overlap_codes_sample"] = oc_full[:50]
            del overlap_compact["overlap_codes"]

        payload = {
            "preflight": preflight,
            "overlap_compute": overlap_compact,
            "result": report,
        }
        (DATA / "overlap_growth_result.txt").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _catalog_insight_stats() -> dict:
    """Best-effort insight row counts and coverage vs catalog cards (for before/after reporting)."""
    out: dict = {
        "catalog_cards_total": 0,
        "distinct_cards_with_insights": 0,
        "insight_rows_total": 0,
        "insight_coverage_pct": 0.0,
    }
    if not CATALOG_DB.is_file():
        return out
    try:
        conn = sqlite3.connect(CATALOG_DB)
        row = conn.execute("SELECT COUNT(*) FROM cards").fetchone()
        out["catalog_cards_total"] = int(row[0] or 0) if row else 0
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='miru_card_insights'"
        ).fetchone()
        if not row or row[0] == 0:
            conn.close()
            return out
        row = conn.execute("SELECT COUNT(*) FROM miru_card_insights").fetchone()
        out["insight_rows_total"] = int(row[0] or 0) if row else 0
        row = conn.execute("SELECT COUNT(DISTINCT card_id) FROM miru_card_insights").fetchone()
        out["distinct_cards_with_insights"] = int(row[0] or 0) if row else 0
        conn.close()
        total = out["catalog_cards_total"]
        if total > 0:
            out["insight_coverage_pct"] = round(100.0 * float(out["distinct_cards_with_insights"]) / float(total), 4)
    except Exception:
        pass
    return out


def run_overlap_growth_and_sync(snapshot_path: Path, overlap_result: dict, *, max_cards: int = 0) -> dict:
    """Run dossier growth for overlap codes (both sources), then rebuild insight sync.

    overlap_result comes from miru_worktree_overlap.compute_overlap(...).
    Returns a report dict (tasks_ok, dossiers_created_refreshed, insight_count_after, etc.).
    """
    overlap_codes = list(overlap_result.get("overlap_codes") or [])
    if max_cards > 0:
        overlap_codes = overlap_codes[:max_cards]
    if not overlap_codes:
        return {
            "meta_bearing_count": overlap_result.get("meta_bearing_count", 0),
            "snapshot_card_count": overlap_result.get("snapshot_card_count", 0),
            "overlap_count": 0,
            "tasks_ok": 0,
            "dossiers_created_refreshed": 0,
            "insight_stats_before": _catalog_insight_stats(),
            "insight_stats_after": _catalog_insight_stats(),
        }

    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.miru_learning_engine import (
        LearningTask,
        build_parser,
        build_engine_from_args,
        handle_verify_official_fields,
    )
    from tools.miru_project_sync import run_worktree_card_insight_sync

    queue_db = DATA / "miru_learning_queue.db"
    status_db = DATA / "miru_learning_log.db"
    parser = build_parser()
    parse_args = parser.parse_args([])
    parse_args.queue_db = queue_db
    parse_args.status_db = status_db
    parse_args.dossier_db = DOSSIER_DB
    engine = build_engine_from_args(parse_args)
    insight_stats_before = _catalog_insight_stats()
    payload = {"snapshot_path": str(snapshot_path)}

    def _inline_verify_task(card_code: str, source_id: str) -> LearningTask:
        ts = "1970-01-01 00:00:00"
        return LearningTask(
            id=0,
            card_code=str(card_code or "").strip().upper(),
            variant_key="",
            task_type="verify_official_fields",
            source_id=str(source_id or "").strip().lower(),
            priority=100,
            status="running",
            attempts=0,
            last_error="",
            task_payload=dict(payload),
            created_at=ts,
            updated_at=ts,
        )

    tasks_ok = 0
    engine.ensure_datastores()
    # Direct handler calls (no queue): do not require the single-instance worker lock; operators
    # should still avoid running this alongside a continuous learning worker on the same DBs.
    try:
        for card_code in overlap_codes:
            for source_id in SOURCES:
                try:
                    result = handle_verify_official_fields(engine, _inline_verify_task(card_code, source_id))
                    msg = str(result.get("message") or "")
                    if str(result.get("task_type") or "") == "verify_official_fields" and (
                        "Verified source-backed fields" in msg
                        or "Stored source record" in msg
                        or bool(result.get("fallback_used"))
                    ):
                        tasks_ok += 1
                except Exception:
                    pass
    finally:
        engine.flush_pending_project_sync(reason="overlap-growth")

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

    sync_report = run_worktree_card_insight_sync(
        rebuild=False,
        limit=None,
        only_card_codes=overlap_codes,
    )
    report["insight_sync_mode"] = "overlap_allowlist_incremental"
    sync = sync_report.get("sync_result") or {}
    by_type = sync.get("by_type") or (sync.get("status") or {}).get("by_type") or {}
    report["insight_count_after"] = sync_report.get("insight_count_after", 0)
    report["by_type"] = by_type
    report["meta_insights"] = by_type.get("meta", 0)
    report["insight_stats_before"] = insight_stats_before
    report["insight_stats_after"] = _catalog_insight_stats()
    report["source_ids_used"] = list(SOURCES)

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
    ap.add_argument(
        "--max-cards",
        type=int,
        default=0,
        help="If >0, only process the first N overlap card codes (debug / smoke test). Default: all overlap.",
    )
    args = ap.parse_args()
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = PROJECT_ROOT / snapshot_path

    preflight = {
        "snapshot": str(snapshot_path),
        "sources": list(SOURCES),
        "insight_stats_before": _catalog_insight_stats(),
    }
    print("=== overlap_growth preflight ===", flush=True)
    print(json.dumps(preflight, indent=2), flush=True)

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
        _write_result_file(preflight, report, overlap_meta=overlap_result)
        return 0

    report = run_overlap_growth_and_sync(snapshot_path, overlap_result, max_cards=max(0, int(args.max_cards or 0)))
    print(json.dumps(report, indent=2))
    _write_result_file(preflight, report, overlap_meta=overlap_result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
