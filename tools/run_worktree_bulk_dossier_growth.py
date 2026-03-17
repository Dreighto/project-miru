#!/usr/bin/env python
"""Bulk dossier growth from approved snapshot sources (worktree-only, snapshot-only).

Processes each card in the snapshot through both approved card-list sources so cards
gain two source rows in learning_dossier_sources, then runs insight sync (enrich + rebuild).

Usage:
  python -m tools.run_worktree_bulk_dossier_growth [--snapshot PATH] [--limit N]
  --snapshot  Path to card-list JSON (default: data/snapshots/community_cardlist.json)
  --limit     Max cards to process (default: all in snapshot)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from tools.miru_worktree_overlap import meta_bearing_codes

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent
DATA = PROJECT_ROOT / "data"
SNAPSHOTS = DATA / "snapshots"
DOSSIER_DB = DATA / "miru_learning_dossiers.db"
CATALOG_DB = DATA / "card_catalog.db"

SOURCES = ("community-cardlist", "official-cardlist")
DEFAULT_SNAPSHOT = SNAPSHOTS / "community_cardlist.json"


def _card_codes_from_snapshot(snapshot_path: Path, limit: int | None) -> list[str]:
    with open(snapshot_path, encoding="utf-8") as f:
        data = json.load(f)
    cards = data.get("cards") or []
    codes = [str(c.get("card_code") or "").strip().upper() for c in cards if c.get("card_code")]
    codes = [c for c in codes if c]
    if limit is not None and limit > 0:
        codes = codes[:limit]
    return codes


def _run_growth(snapshot_path: Path, limit: int | None) -> dict:
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.miru_learning_engine import build_parser, build_engine_from_args

    queue_db = DATA / "miru_learning_queue.db"
    status_db = DATA / "miru_learning_log.db"
    codes = _card_codes_from_snapshot(snapshot_path, limit)
    meta_codes = meta_bearing_codes(CATALOG_DB)
    # Prefer cards that have meta in catalog so insight can surface meta
    ordered = sorted(codes, key=lambda c: (0 if c in meta_codes else 1, c))

    parser = build_parser()
    args = parser.parse_args([])
    args.queue_db = queue_db
    args.status_db = status_db
    args.dossier_db = DOSSIER_DB
    engine = build_engine_from_args(args)
    payload = {"snapshot_path": str(snapshot_path)}

    processed = 0
    ok_count = 0
    for card_code in ordered:
        for source_id in SOURCES:
            processed += 1
            try:
                result = engine.run_once(
                    card_code=card_code,
                    task_type="verify_official_fields",
                    source_id=source_id,
                    task_payload=payload,
                )
                if result.get("ok") is True:
                    ok_count += 1
            except Exception as e:
                pass  # skip on error (e.g. card not in snapshot for this source)

    # Count dossiers and two-source cards
    dossier_count = 0
    two_source_count = 0
    if DOSSIER_DB.is_file():
        conn = sqlite3.connect(DOSSIER_DB)
        dossier_count = conn.execute(
            "SELECT COUNT(*) FROM learning_dossiers WHERE verification_state IN ('verified','source-backed')"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT card_code, COUNT(DISTINCT source_id) AS n FROM learning_dossier_sources GROUP BY card_code"
        ).fetchall()
        two_source_count = sum(1 for r in rows if r[1] >= 2)
        conn.close()

    return {
        "cards_in_batch": len(ordered),
        "tasks_processed": processed,
        "tasks_ok": ok_count,
        "dossier_count_after": dossier_count,
        "two_source_cards": two_source_count,
        "meta_bearing_in_batch": len(meta_codes & set(ordered)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Bulk dossier growth from approved snapshots (worktree-only).")
    ap.add_argument("--snapshot", default=str(DEFAULT_SNAPSHOT), help="Path to card-list JSON snapshot")
    ap.add_argument("--limit", type=int, default=None, help="Max cards to process (default: all)")
    args = ap.parse_args()
    snapshot_path = Path(args.snapshot)
    if not snapshot_path.is_absolute():
        snapshot_path = PROJECT_ROOT / snapshot_path
    if not snapshot_path.is_file():
        print(f"Snapshot not found: {snapshot_path}", file=sys.stderr)
        return 1

    growth = _run_growth(snapshot_path, args.limit)
    print("Bulk dossier growth:", json.dumps(growth, indent=2))

    from tools.miru_project_sync import run_worktree_card_insight_sync
    report = run_worktree_card_insight_sync(rebuild=True, limit=None)
    sync = report.get("sync_result") or {}
    by_type = sync.get("by_type") or {}
    enr = report.get("enrichment") or {}
    growth["enrichment_cards"] = enr.get("cards_enriched", 0)
    growth["insight_count_after"] = report.get("insight_count_after", 0)
    growth["by_type"] = by_type
    growth["meta_insights"] = by_type.get("meta", 0)
    # Overlap: dossiers that also have meta in catalog
    meta_codes = meta_bearing_codes(CATALOG_DB)
    if DOSSIER_DB.is_file():
        conn = sqlite3.connect(DOSSIER_DB)
        d_codes = set(r[0] for r in conn.execute("SELECT card_code FROM learning_dossiers WHERE verification_state IN ('verified','source-backed')").fetchall())
        conn.close()
        growth["dossiers_with_meta_overlap"] = len(d_codes & meta_codes)
    print("Bulk dossier growth (final):", json.dumps(growth, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
