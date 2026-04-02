#!/usr/bin/env python
"""Enrich Miru learning dossiers from data/snapshots/limitless.json (verbatim fields only).

Reads overlap via tools.miru_worktree_overlap.compute_overlap (same 60 codes as overlap growth).
Writes to data/miru_learning_dossiers.db only (learning_dossier_sources + basic_facts_json merge).

Then runs run_worktree_card_insight_sync(..., only_card_codes=overlap) and logs JSON to
data/limitless_enrichment_sync_result_v1.txt.

Does not modify card_catalog except via normal insight sync; does not touch miru_fetch_limitless.py.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_ROOT.parent
DATA = PROJECT_ROOT / "data"
SNAPSHOT_PATH = DATA / "snapshots" / "limitless.json"
CATALOG_DB = DATA / "card_catalog.db"
DOSSIER_DB = DATA / "miru_learning_dossiers.db"
RESULT_PATH = DATA / "limitless_enrichment_sync_result_v1.txt"
LOG_DB = DATA / "miru_learning_log.db"

SOURCE_ID = "limitless"
META_REF = "meta_summary"


def _utc_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def _norm_code(raw: Any) -> str:
    return str(raw or "").strip().upper()


def _insight_row_total(catalog: Path) -> int:
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


def _insights_snapshot_for_codes(catalog: Path, codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    """card_id -> list of {insight_type, confidence} for threshold-crossing diff."""
    out: dict[str, list[dict[str, Any]]] = {}
    if not catalog.is_file() or not codes:
        return out
    try:
        conn = sqlite3.connect(catalog)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT card_id, insight_type, confidence
            FROM miru_card_insights
            WHERE card_id IN ({})
            """.format(",".join("?" * len(codes))),
            codes,
        ).fetchall()
        conn.close()
        for r in rows:
            cid = str(r["card_id"] or "").strip().upper()
            out.setdefault(cid, []).append(
                {"insight_type": str(r["insight_type"] or ""), "confidence": float(r["confidence"] or 0.0)}
            )
    except Exception:
        pass
    return out


def _review_queue_count(log_db: Path) -> int:
    if not log_db.is_file():
        return 0
    try:
        conn = sqlite3.connect(log_db)
        row = conn.execute("SELECT COUNT(*) FROM learner_review_queue").fetchone()
        conn.close()
        return int(row[0] or 0) if row else 0
    except Exception:
        return 0


def _load_snapshot(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _leader_usage_for_snapshot(data: dict[str, Any], code: str) -> dict[str, Any] | None:
    meta = data.get("meta_summary")
    if not isinstance(meta, dict):
        return None
    lu = meta.get("leader_usage")
    if not isinstance(lu, dict):
        return None
    raw = lu.get(code)
    if not isinstance(raw, dict):
        return None
    # Only include keys present in snapshot for this leader (verbatim).
    out: dict[str, Any] = {}
    for k, v in raw.items():
        out[str(k)] = v
    return out or None


def _collect_tournament_rows(
    data: dict[str, Any], overlap: set[str]
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """card_code -> list of (source_reference, field_payload)."""
    by_card: dict[str, list[tuple[str, dict[str, Any]]]] = {c: [] for c in overlap}
    tournaments = data.get("tournaments")
    if not isinstance(tournaments, list):
        return by_card
    for t in tournaments:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("tournament_id") or "").strip()
        tname = str(t.get("name") or "").strip()
        tdate = str(t.get("date") or "").strip()
        results = t.get("results") or []
        if not isinstance(results, list):
            continue
        for idx, row in enumerate(results):
            if not isinstance(row, dict):
                continue
            lc = _norm_code(row.get("leader_code"))
            if not lc or lc not in overlap:
                continue
            ref = f"tournament:{tid}:result:{idx}"
            payload: dict[str, Any] = {}
            if tid:
                payload["tournament_id"] = tid
            if tname:
                payload["tournament_name"] = tname
            if tdate:
                payload["tournament_date"] = tdate
            if "placing" in row:
                payload["placing"] = row["placing"]
            pl = str(row.get("player") or "").strip()
            if pl:
                payload["player"] = pl
            rec = row.get("record")
            if isinstance(rec, dict) and rec:
                payload["record"] = {str(k): rec[k] for k in rec.keys()}
            if "deck" in row and row["deck"] is not None:
                deck = row["deck"]
                if isinstance(deck, dict):
                    payload["deck"] = {str(k): deck[k] for k in deck.keys()}
                else:
                    payload["deck"] = deck
            if payload:
                by_card.setdefault(lc, []).append((ref, payload))
    return by_card


def _merge_basic_facts_json(existing: str, fetched_at: str, leader_usage: dict[str, Any] | None) -> str:
    try:
        base = json.loads(existing or "{}")
    except json.JSONDecodeError:
        base = {}
    if not isinstance(base, dict):
        base = {}
    if fetched_at:
        base["limitless_fetched_at"] = fetched_at
    if leader_usage is not None:
        base["limitless_leader_usage"] = leader_usage
    return json.dumps(base, ensure_ascii=False, sort_keys=True)


def enrich_dossiers(
    *,
    snapshot: dict[str, Any],
    overlap_codes: list[str],
    dossier_db: Path,
) -> dict[str, Any]:
    fetched_at = str(snapshot.get("fetched_at") or "").strip()
    tournament_by_card = _collect_tournament_rows(snapshot, set(overlap_codes))
    now = _utc_now()
    rows_written = 0
    basic_facts_updated = 0

    with sqlite3.connect(dossier_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        for code in overlap_codes:
            leader_usage = _leader_usage_for_snapshot(snapshot, code)
            meta_payload: dict[str, Any] = {}
            if fetched_at:
                meta_payload["snapshot_fetched_at"] = fetched_at
            if leader_usage is not None:
                meta_payload["leader_usage"] = leader_usage

            if meta_payload:
                conn.execute(
                    """
                    INSERT INTO learning_dossier_sources (
                        card_code, source_id, source_reference, field_payload_json,
                        verification_state, fetched_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'source-fetched', ?, ?)
                    ON CONFLICT(card_code, source_id, source_reference) DO UPDATE SET
                        field_payload_json = excluded.field_payload_json,
                        fetched_at = excluded.fetched_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        code,
                        SOURCE_ID,
                        META_REF,
                        json.dumps(meta_payload, ensure_ascii=False, sort_keys=True),
                        fetched_at or now,
                        now,
                    ),
                )
                rows_written += 1

            # Replace tournament rows for this card so snapshot is authoritative.
            conn.execute(
                """
                DELETE FROM learning_dossier_sources
                WHERE card_code = ? AND source_id = ? AND source_reference LIKE 'tournament:%'
                """,
                (code, SOURCE_ID),
            )
            for ref, payload in tournament_by_card.get(code, []):
                conn.execute(
                    """
                    INSERT INTO learning_dossier_sources (
                        card_code, source_id, source_reference, field_payload_json,
                        verification_state, fetched_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'source-fetched', ?, ?)
                    """,
                    (
                        code,
                        SOURCE_ID,
                        ref,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        fetched_at or now,
                        now,
                    ),
                )
                rows_written += 1

            # Merge verbatim limitless summary into basic_facts_json when we have a dossier row.
            row = conn.execute(
                "SELECT basic_facts_json FROM learning_dossiers WHERE card_code = ?",
                (code,),
            ).fetchone()
            if row is not None:
                merged = _merge_basic_facts_json(str(row[0] or "{}"), fetched_at, leader_usage)
                conn.execute(
                    """
                    UPDATE learning_dossiers
                    SET basic_facts_json = ?, updated_at = ?
                    WHERE card_code = ?
                    """,
                    (merged, now, code),
                )
                basic_facts_updated += 1

        conn.commit()

    return {
        "overlap_codes": len(overlap_codes),
        "learning_dossier_sources_upserts": rows_written,
        "learning_dossiers_basic_facts_merged": basic_facts_updated,
        "tournament_source_rows_total": sum(len(tournament_by_card.get(c, [])) for c in overlap_codes),
    }


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))
    from tools.miru_learner_config import get_learner_mode, LEARNER_MODE_REVIEW_REQUIRED
    from tools.miru_project_sync import run_worktree_card_insight_sync
    from tools.miru_worktree_overlap import compute_overlap
    from tools.miru_learning_engine_worktree_overlay import MiruLearningEngine

    if not SNAPSHOT_PATH.is_file():
        print(json.dumps({"ok": False, "error": f"missing snapshot {SNAPSHOT_PATH}"}))
        return 1
    if not CATALOG_DB.is_file():
        print(json.dumps({"ok": False, "error": f"missing catalog {CATALOG_DB}"}))
        return 1

    overlap_result = compute_overlap(SNAPSHOT_PATH, CATALOG_DB)
    overlap_codes = list(overlap_result.get("overlap_codes") or [])
    if not overlap_codes:
        report = {
            "ok": False,
            "error": "no overlap codes; check snapshot vs catalog",
            "overlap_result": overlap_result,
        }
        RESULT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return 1

    snapshot = _load_snapshot(SNAPSHOT_PATH)
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _need_schema = True
    if DOSSIER_DB.is_file():
        try:
            _c = sqlite3.connect(DOSSIER_DB)
            _r = _c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_dossier_sources'"
            ).fetchone()
            _c.close()
            _need_schema = _r is None
        except Exception:
            _need_schema = True
    if _need_schema:
        MiruLearningEngine(dossier_db_path=DOSSIER_DB, catalog_db_path=CATALOG_DB).ensure_datastores()

    insight_before = _insight_row_total(CATALOG_DB)
    insights_before = _insights_snapshot_for_codes(CATALOG_DB, overlap_codes)
    review_before = _review_queue_count(LOG_DB)

    enrichment = enrich_dossiers(snapshot=snapshot, overlap_codes=overlap_codes, dossier_db=DOSSIER_DB)

    sync_report = run_worktree_card_insight_sync(
        rebuild=False,
        limit=None,
        only_card_codes=overlap_codes,
    )
    sync_result = sync_report.get("sync_result") or {}
    by_type = sync_result.get("by_type") or {}
    if not by_type and isinstance(sync_result.get("status"), dict):
        by_type = (sync_result.get("status") or {}).get("by_type") or {}

    insight_after = _insight_row_total(CATALOG_DB)
    insights_after = _insights_snapshot_for_codes(CATALOG_DB, overlap_codes)
    review_after = _review_queue_count(LOG_DB)

    # Cards that newly have at least one insight row (crossed into having catalog insights).
    crossed_into_insights: list[str] = []
    for code in overlap_codes:
        if not insights_before.get(code) and insights_after.get(code):
            crossed_into_insights.append(code)

    learner_mode = get_learner_mode()
    sample_op13 = None
    try:
        conn = sqlite3.connect(DOSSIER_DB)
        conn.row_factory = sqlite3.Row
        r = conn.execute(
            """
            SELECT card_code, basic_facts_json,
            (SELECT field_payload_json FROM learning_dossier_sources
             WHERE card_code = 'OP13-002' AND source_id = ? AND source_reference = ? LIMIT 1) AS meta_payload
            FROM learning_dossiers WHERE card_code = 'OP13-002'
            """,
            (SOURCE_ID, META_REF),
        ).fetchone()
        conn.close()
        if r:
            bf = {}
            try:
                bf = json.loads(r["basic_facts_json"] or "{}")
            except json.JSONDecodeError:
                pass
            mp = {}
            try:
                mp = json.loads(r["meta_payload"] or "{}")
            except (json.JSONDecodeError, TypeError):
                pass
            sample_op13 = {
                "card_code": r["card_code"],
                "basic_facts_limitless_keys": {
                    k: bf[k] for k in ("limitless_fetched_at", "limitless_leader_usage") if k in bf
                },
                "learning_dossier_sources_meta_payload": mp,
            }
    except Exception as e:
        sample_op13 = {"error": str(e)}

    report: dict[str, Any] = {
        "ok": True,
        "snapshot_path": str(SNAPSHOT_PATH),
        "overlap_count": len(overlap_codes),
        "overlap_codes": overlap_codes,
        "enrichment": enrichment,
        "insight_sync_mode": "overlap_allowlist_incremental",
        "sync_report": sync_report,
        "sync_result": sync_result,
        "meta_insights": int(by_type.get("meta", 0) or 0),
        "by_type": dict(by_type),
        "insight_count_after": int(sync_report.get("insight_count_after") or 0),
        "miru_card_insights_row_count_before": insight_before,
        "miru_card_insights_row_count_after": insight_after,
        "insight_stats_delta": {
            "row_delta": insight_after - insight_before,
        },
        "cards_newly_with_insights": crossed_into_insights,
        "learner_mode": learner_mode,
        "learner_mode_review_required": learner_mode == LEARNER_MODE_REVIEW_REQUIRED,
        "learner_review_queue_count_before": review_before,
        "learner_review_queue_count_after": review_after,
        "sample_op13_002": sample_op13,
    }

    RESULT_PATH.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "wrote": str(RESULT_PATH), "meta_insights": report["meta_insights"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
