#!/usr/bin/env python
"""Targeted card insight sync for Limitless overlap allowlist (engine path only).

Uses tools.miru_project_sync.run_worktree_card_insight_sync(only_card_codes=...) which calls
sync_miru_card_insights → MiruDossierStore.generate_card_insight.

Overlap codes are taken from tools.miru_worktree_overlap.compute_overlap (full list; v2 txt may truncate).

Logs JSON to data/insight_generation_overlap_v2.txt.
Does not modify run_limitless_dossier_enrichment_sync.py.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent
DATA = PROJECT_ROOT / "data"
SNAPSHOT = DATA / "snapshots" / "limitless.json"
CATALOG_DB = DATA / "card_catalog.db"
RESULT_PATH = DATA / "insight_generation_overlap_v2.txt"
# learner_review_queue lives on status / log DB in this worktree layout
STATUS_DB = DATA / "miru_learning_log.db"


def _count_insights(catalog: Path) -> int:
    if not catalog.is_file():
        return 0
    try:
        conn = sqlite3.connect(catalog)
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='miru_card_insights'"
        ).fetchone()
        if not row or row[0] == 0:
            conn.close()
            return 0
        row = conn.execute("SELECT COUNT(*) FROM miru_card_insights").fetchone()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _review_queue_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        conn = sqlite3.connect(path)
        row = conn.execute("SELECT COUNT(*) FROM learner_review_queue").fetchone()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.miru_learner_config import get_learner_mode, LEARNER_MODE_REVIEW_REQUIRED
    from tools.miru_project_sync import run_worktree_card_insight_sync, sync_miru_card_insights
    from tools.miru_worktree_overlap import compute_overlap

    recon: dict = {
        "engine_entry_points": {
            "primary_sync": {
                "file": "tools/miru_project_sync.py",
                "functions": [
                    "run_worktree_card_insight_sync(limit, rebuild, only_card_codes)",
                    "sync_miru_card_insights(..., force_rebuild, only_card_codes)",
                ],
                "description": (
                    "Catalog insight projection + strict insight candidates; writes miru_card_insights "
                    "via connect_catalog_db (engine-mediated, not ad-hoc SQL)."
                ),
            },
            "dossier_generation": {
                "file": "tools/miru_dossier_store.py",
                "functions": [
                    "MiruDossierStore.generate_card_insight(...)",
                    "MiruDossierStore._generate_card_insight_from_dossier(dossier)",
                ],
                "description": "Builds single-voice insight text from merged dossier; used inside sync_miru_card_insights.",
            },
            "strict_candidates": {
                "file": "tools/miru_project_sync.py",
                "functions": [
                    "_build_strict_dossier_insight_candidates(dossier, generated)",
                    "_is_supported_dossier_insight(...)",
                ],
            },
        },
        "targeted_card_list_support": {
            "exists": True,
            "parameter": "only_card_codes",
            "when_active": "When force_rebuild is False and only_card_codes is not None, "
            "sync_miru_card_insights selects only those codes (sync_mode publish_eligible_allowlist).",
            "docstring_ref": "run_worktree_card_insight_sync docstring ~L3933",
        },
        "force_rebuild_interaction": {
            "only_card_codes_honored": False,
            "note": "If force_rebuild is True, the first branch builds candidates from all runtime dossiers "
            "(and catalog order); only_card_codes is not applied. Subset rebuild would require a small "
            "extension to sync_miru_card_insights (not implemented in this script).",
        },
    }

    if not SNAPSHOT.is_file() or not CATALOG_DB.is_file():
        err = {"ok": False, "error": "missing snapshot or catalog", "reconnaissance": recon}
        RESULT_PATH.write_text(json.dumps(err, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return 1

    overlap = compute_overlap(SNAPSHOT, CATALOG_DB)
    codes = list(overlap.get("overlap_codes") or [])
    if not codes:
        err = {"ok": False, "error": "empty overlap", "overlap": overlap, "reconnaissance": recon}
        RESULT_PATH.write_text(json.dumps(err, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return 1

    insight_before = _count_insights(CATALOG_DB)
    review_before = _review_queue_count(STATUS_DB)
    learner_before = get_learner_mode()

    # Engine-only targeted pass (same as overlap growth insight step).
    report = run_worktree_card_insight_sync(
        rebuild=False,
        limit=None,
        only_card_codes=codes,
    )
    sr = report.get("sync_result") or {}

    insight_after = _count_insights(CATALOG_DB)
    review_after = _review_queue_count(STATUS_DB)
    learner_after = get_learner_mode()

    attempted = int(sr.get("selected_card_count") or len(codes))
    inserted = int(sr.get("inserted_insights") or 0)
    replaced = int(sr.get("replaced_insights") or 0)
    synced = int(sr.get("synced_cards") or 0)
    skipped = int(sr.get("skipped_cards") or 0)

    sample_code = codes[0] if codes else ""
    sample_rows: list[dict[str, Any]] = []
    if sample_code and CATALOG_DB.is_file():
        try:
            qconn = sqlite3.connect(CATALOG_DB)
            for row in qconn.execute(
                "SELECT insight_type, insight_text, used_sections_json FROM miru_card_insights "
                "WHERE card_id = ? ORDER BY updated_at DESC LIMIT 3",
                (sample_code,),
            ).fetchall():
                sample_rows.append(
                    {
                        "insight_type": row[0],
                        "insight_text_preview": (row[1] or "")[:120],
                        "used_sections_json": row[2],
                    }
                )
            qconn.close()
        except Exception:
            sample_rows = []
    sample_outcome = {
        "card_code": sample_code,
        "sync_mode": sr.get("sync_mode"),
        "miru_card_insights_rows_for_card": sample_rows,
        "outcome_summary": (
            "Rows listed = current catalog insights for sample card after sync; "
            "empty list means no miru_card_insights row for that card_id."
        ),
    }

    out: dict = {
        "ok": True,
        "reconnaissance": recon,
        "overlap_count": len(codes),
        "overlap_codes_sample": codes[:5],
        "generation_attempted_card_count": attempted,
        "sync_result_summary": {
            "sync_mode": sr.get("sync_mode"),
            "selected_card_count": sr.get("selected_card_count"),
            "synced_cards": synced,
            "skipped_cards": skipped,
            "inserted_insights": inserted,
            "replaced_insights": replaced,
            "preserved_insights": sr.get("preserved_insights"),
            "written_insights": sr.get("written_insights"),
            "projected_cards": sr.get("projected_cards"),
            "by_type": sr.get("by_type") or {},
        },
        "new_miru_card_insights_rows": inserted,
        "upgraded_replaced_insights": replaced,
        "miru_card_insights_row_count_before": insight_before,
        "miru_card_insights_row_count_after": insight_after,
        "miru_card_insights_row_delta": insight_after - insight_before,
        "learner_review_queue_count_before": review_before,
        "learner_review_queue_count_after": review_after,
        "learner_review_queue_delta": review_after - review_before,
        "learner_mode_before": learner_before,
        "learner_mode_after": learner_after,
        "learner_mode_review_required": learner_after == LEARNER_MODE_REVIEW_REQUIRED,
        "run_worktree_card_insight_sync_report": report,
        "sample_generation_attempt": sample_outcome,
    }

    RESULT_PATH.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "wrote": str(RESULT_PATH), "inserted_insights": inserted}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
