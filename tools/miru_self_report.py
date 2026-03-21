"""
miru_self_report.py

Read-only operator self-report for Miru: coverage, pipeline/signal health,
insight distribution, queue/sync/source snapshots. Writes JSON to
data/miru_self_report.json. No governance or publication rule changes.

All database reads are defensive: missing files/tables surface as alerts and
null metrics rather than raising (fail-closed visibility).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = ROOT / "data" / "miru_self_report.json"
CATALOG_DB = ROOT / "data" / "card_catalog.db"
RUNTIME_DOSSIER_DB = ROOT / "data" / "miru_learning_dossiers.db"
DECK_INTEL_DB = ROOT / "data" / "miru_deck_intel.db"
APPROVED_SOURCES_PATH = ROOT / "config" / "miru_approved_sources.json"

# Stale queue threshold (days) for queue_stale metric
_STALE_QUEUE_DAYS = 14
# Snapshot file age warning (days)
_STALE_SNAPSHOT_DAYS = 30


def _utc_now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_connect(path: Path) -> sqlite3.Connection | None:
    if not path.is_file():
        return None
    try:
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except Exception:
        try:
            return sqlite3.connect(str(path))
        except Exception:
            return None


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception:
        return False
    for r in rows:
        if len(r) > 1 and str(r[1]).strip().lower() == column.strip().lower():
            return True
    return False


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _load_approved_sources() -> tuple[list[dict[str, Any]], str | None]:
    if not APPROVED_SOURCES_PATH.is_file():
        return [], f"missing:{APPROVED_SOURCES_PATH.relative_to(ROOT)}"
    try:
        raw = json.loads(APPROVED_SOURCES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return [], f"invalid_json:{exc}"
    items = raw.get("approved_sources") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return [], "approved_sources_not_a_list"
    out: list[dict[str, Any]] = [x for x in items if isinstance(x, dict)]
    return out, None


def _source_has_live_adapter(src: dict[str, Any]) -> bool:
    """Heuristic: enabled source with execution_adapter or API requirement."""
    if not bool(src.get("enabled", True)):
        return False
    if bool(src.get("requires_api")):
        return True
    if str(src.get("execution_adapter") or "").strip():
        return True
    return False


def _snapshot_freshness_warnings(project_root: Path, sources: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    checked: list[dict[str, Any]] = []
    paths_seen: set[str] = set()
    now = datetime.now(tz=timezone.utc).timestamp()

    for src in sources:
        sid = str(src.get("source_id") or "")
        candidates = src.get("snapshot_candidates")
        if not isinstance(candidates, list):
            continue
        for rel in candidates:
            p = Path(str(rel or "").strip())
            if not p.parts:
                continue
            abs_path = p if p.is_absolute() else (project_root / p)
            key = str(abs_path.resolve())
            if key in paths_seen:
                continue
            paths_seen.add(key)
            entry: dict[str, Any] = {
                "path": str(p.as_posix()),
                "exists": abs_path.is_file(),
                "mtime_iso": None,
                "age_days": None,
            }
            if abs_path.is_file():
                try:
                    mtime = abs_path.stat().st_mtime
                    entry["mtime_iso"] = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    entry["age_days"] = round((now - mtime) / 86400.0, 2)
                    if (now - mtime) > _STALE_SNAPSHOT_DAYS * 86400:
                        warnings.append(
                            f"snapshot_stale:{sid or 'unknown'}:{p.as_posix()} "
                            f"(>{_STALE_SNAPSHOT_DAYS}d)"
                        )
                except OSError:
                    warnings.append(f"snapshot_stat_error:{p.as_posix()}")
            else:
                warnings.append(f"snapshot_missing:{sid or 'unknown'}:{p.as_posix()}")
            checked.append(entry)

    return {"warnings": warnings, "snapshots": checked}


def build_self_report(project_root: Path | None = None) -> dict[str, Any]:
    """
    Assemble the full self-report dict (read-only). Does not write files.
    """
    project_root = project_root or ROOT
    alerts: list[str] = []

    metrics: dict[str, Any] = {
        "cards_total": None,
        "cards_with_any_insight": None,
        "coverage_pct": None,
        "coverage_by_type": None,
        "cards_with_dossier": None,
        "dossier_to_insight_gap": None,
        "avg_confidence_overall": None,
        "signal_cards_covered": None,
        "signal_leaders_covered": None,
        "archetype_clusters_active": None,
        "archetype_cards_mapped": None,
        "queue_pending": None,
        "queue_stale": None,
        "promotion_ready": None,
        "last_sync_run_at": None,
        "sync_never_run": None,
        "sources_active": None,
        "sources_with_live_adapter": None,
        "insight_sync_remaining_candidates": None,
        "cards_with_strong_insight": None,
        "cards_with_low_confidence_insight": None,
        "cards_without_insight": None,
        "publication_cards_pending_review": None,
        "avg_confidence_by_insight_type": None,
    }

    pipeline_health: dict[str, Any] = {
        "card_catalog_db_ok": CATALOG_DB.is_file(),
        "runtime_dossier_db_ok": RUNTIME_DOSSIER_DB.is_file(),
        "deck_intel_db_ok": DECK_INTEL_DB.is_file(),
        "decklist_deck_count": None,
        "decklist_table_present": None,
    }

    signal_health: dict[str, Any] = {
        "leader_card_signals_rows": None,
        "signals_nonempty": None,
    }

    insight_distribution: dict[str, Any] = {
        "by_insight_type": None,
        "total_insight_rows": None,
    }

    # --- Catalog + insights + queue + archetypes + sync metadata ---
    sync_payload: dict[str, Any] = {}
    sync_row_updated_at = ""

    if not CATALOG_DB.is_file():
        alerts.append(f"missing_catalog_db:{CATALOG_DB.relative_to(project_root)}")
    else:
        conn = _safe_connect(CATALOG_DB)
        if conn is None:
            alerts.append("catalog_db_open_failed")
        else:
            with closing(conn):
                try:
                    metrics["cards_total"] = int(
                        _scalar(conn, "SELECT COUNT(*) FROM cards") or 0
                    )
                except Exception as exc:
                    alerts.append(f"cards_total:{exc}")
                    metrics["cards_total"] = None

                if _table_exists(conn, "miru_card_insights"):
                    try:
                        metrics["cards_with_any_insight"] = int(
                            _scalar(
                                conn,
                                "SELECT COUNT(DISTINCT card_id) FROM miru_card_insights",
                            )
                            or 0,
                        )
                        insight_distribution["total_insight_rows"] = int(
                            _scalar(conn, "SELECT COUNT(*) FROM miru_card_insights") or 0
                        )
                        rows = conn.execute(
                            "SELECT insight_type, COUNT(*) AS n FROM miru_card_insights "
                            "GROUP BY insight_type ORDER BY n DESC"
                        ).fetchall()
                        insight_distribution["by_insight_type"] = {
                            str(r[0]): int(r[1]) for r in rows
                        }
                        metrics["coverage_by_type"] = dict(
                            insight_distribution["by_insight_type"]
                        )
                        avg = _scalar(conn, "SELECT AVG(confidence) FROM miru_card_insights")
                        metrics["avg_confidence_overall"] = (
                            round(float(avg), 6) if avg is not None else None
                        )
                        try:
                            metrics["cards_with_strong_insight"] = int(
                                _scalar(
                                    conn,
                                    """
                                    SELECT COUNT(*) FROM (
                                        SELECT card_id FROM miru_card_insights
                                        GROUP BY card_id
                                        HAVING MAX(confidence) >= 0.72
                                    )
                                    """,
                                )
                                or 0
                            )
                            metrics["cards_with_low_confidence_insight"] = int(
                                _scalar(
                                    conn,
                                    """
                                    SELECT COUNT(*) FROM (
                                        SELECT card_id FROM miru_card_insights
                                        GROUP BY card_id
                                        HAVING MAX(confidence) < 0.60 AND MAX(confidence) >= 0.50
                                    )
                                    """,
                                )
                                or 0
                            )
                        except Exception as exc:
                            alerts.append(f"miru_card_insights_tiers:{exc}")
                        try:
                            rows_avg = conn.execute(
                                """
                                SELECT insight_type, AVG(confidence) AS a, COUNT(*) AS n
                                FROM miru_card_insights
                                GROUP BY insight_type
                                """
                            ).fetchall()
                            metrics["avg_confidence_by_insight_type"] = {
                                str(r[0]): {
                                    "avg": round(float(r[1]), 4) if r[1] is not None else None,
                                    "rows": int(r[2] or 0),
                                }
                                for r in rows_avg
                            }
                        except Exception as exc:
                            alerts.append(f"miru_card_insights_by_type:{exc}")
                    except Exception as exc:
                        alerts.append(f"miru_card_insights:{exc}")

                    ct = metrics["cards_total"] or 0
                    cwi = metrics["cards_with_any_insight"]
                    if ct > 0 and cwi is not None:
                        metrics["coverage_pct"] = round(100.0 * float(cwi) / float(ct), 4)
                        try:
                            metrics["cards_without_insight"] = max(0, int(ct) - int(cwi))
                        except Exception:
                            metrics["cards_without_insight"] = None
                else:
                    alerts.append("table_missing:miru_card_insights")

                if _table_exists(conn, "card_intelligence") and _column_exists(
                    conn, "card_intelligence", "publish_status"
                ):
                    try:
                        metrics["publication_cards_pending_review"] = int(
                            _scalar(
                                conn,
                                """
                                SELECT COUNT(*) FROM card_intelligence ci
                                JOIN cards c ON c.id = ci.card_id
                                WHERE lower(trim(coalesce(ci.publish_status, ''))) IN (
                                    'publish_requires_review', 'publish_deferred'
                                )
                                """,
                            )
                            or 0
                        )
                    except Exception as exc:
                        alerts.append(f"card_intelligence_publish:{exc}")

                if _table_exists(conn, "miru_deck_archetypes"):
                    try:
                        metrics["archetype_clusters_active"] = int(
                            _scalar(conn, "SELECT COUNT(*) FROM miru_deck_archetypes") or 0
                        )
                    except Exception as exc:
                        alerts.append(f"miru_deck_archetypes:{exc}")

                if _table_exists(conn, "miru_card_usage"):
                    try:
                        metrics["archetype_cards_mapped"] = int(
                            _scalar(
                                conn,
                                "SELECT COUNT(DISTINCT card_code) FROM miru_card_usage",
                            )
                            or 0
                        )
                    except Exception as exc:
                        alerts.append(f"miru_card_usage:{exc}")

                if _table_exists(conn, "miru_review_queue"):
                    try:
                        metrics["queue_pending"] = int(
                            _scalar(
                                conn,
                                "SELECT COUNT(*) FROM miru_review_queue WHERE status = 'pending'",
                            )
                            or 0,
                        )
                        metrics["queue_stale"] = int(
                            _scalar(
                                conn,
                                """
                                SELECT COUNT(*) FROM miru_review_queue
                                WHERE status = 'pending'
                                  AND updated_at != ''
                                  AND datetime(updated_at) < datetime('now', ?)
                                """,
                                (f"-{_STALE_QUEUE_DAYS} days",),
                            )
                            or 0,
                        )
                    except Exception as exc:
                        alerts.append(f"miru_review_queue:{exc}")

                if _table_exists(conn, "miru_publication_stage"):
                    try:
                        metrics["promotion_ready"] = int(
                            _scalar(
                                conn,
                                """
                                SELECT COUNT(*) FROM miru_publication_stage
                                WHERE (removed_at IS NULL OR removed_at = '')
                                  AND stage_state IN ('staged_candidate', 'staged_batch_member')
                                """,
                            )
                            or 0,
                        )
                    except Exception as exc:
                        alerts.append(f"miru_publication_stage:{exc}")

                if _table_exists(conn, "miru_sync_metadata"):
                    try:
                        row = conn.execute(
                            """
                            SELECT updated_at, payload_json
                            FROM miru_sync_metadata
                            WHERE sync_key = 'miru_card_insights'
                            LIMIT 1
                            """
                        ).fetchone()
                        if row:
                            sync_row_updated_at = str(row[0] or "")
                            raw_p = row[1]
                            try:
                                sync_payload = json.loads(str(raw_p or "{}"))
                            except Exception:
                                sync_payload = {}
                            metrics["last_sync_run_at"] = sync_row_updated_at or str(
                                sync_payload.get("updated_at") or ""
                            )
                            metrics["insight_sync_remaining_candidates"] = int(
                                sync_payload.get("remaining_count") or 0
                            )
                        if _table_exists(conn, "miru_card_insights"):
                            insight_rows = int(
                                _scalar(conn, "SELECT COUNT(*) FROM miru_card_insights") or 0
                            )
                        else:
                            insight_rows = 0
                        if row is None and insight_rows == 0:
                            metrics["sync_never_run"] = True
                        else:
                            metrics["sync_never_run"] = False
                    except Exception as exc:
                        alerts.append(f"miru_sync_metadata:{exc}")

    # --- Learning dossiers (runtime) ---
    dossier_codes: set[str] = set()
    if not RUNTIME_DOSSIER_DB.is_file():
        alerts.append(f"missing_runtime_dossier_db:{RUNTIME_DOSSIER_DB.relative_to(project_root)}")
    else:
        rconn = _safe_connect(RUNTIME_DOSSIER_DB)
        if rconn is None:
            alerts.append("runtime_dossier_db_open_failed")
        else:
            with closing(rconn):
                if _table_exists(rconn, "learning_dossiers"):
                    try:
                        metrics["cards_with_dossier"] = int(
                            _scalar(rconn, "SELECT COUNT(*) FROM learning_dossiers") or 0
                        )
                        rows = rconn.execute("SELECT card_code FROM learning_dossiers").fetchall()
                        dossier_codes = {str(r[0]).strip().upper() for r in rows if r and r[0]}
                    except Exception as exc:
                        alerts.append(f"learning_dossiers:{exc}")
                else:
                    alerts.append("table_missing:learning_dossiers")

    # Dossier → insight gap (cross-store, in Python)
    insight_codes: set[str] = set()
    if CATALOG_DB.is_file():
        c2 = _safe_connect(CATALOG_DB)
        if c2:
            with closing(c2):
                if _table_exists(c2, "miru_card_insights"):
                    try:
                        rows = c2.execute(
                            "SELECT DISTINCT card_id FROM miru_card_insights"
                        ).fetchall()
                        insight_codes = {str(r[0]).strip().upper() for r in rows if r and r[0]}
                    except Exception:
                        pass
    if dossier_codes:
        metrics["dossier_to_insight_gap"] = len(dossier_codes - insight_codes)
    elif metrics["cards_with_dossier"] is not None and metrics["cards_with_dossier"] == 0:
        metrics["dossier_to_insight_gap"] = 0
    else:
        metrics["dossier_to_insight_gap"] = None

    # --- Deck intel / signals ---
    if DECK_INTEL_DB.is_file():
        dconn = _safe_connect(DECK_INTEL_DB)
        if dconn:
            with closing(dconn):
                if _table_exists(dconn, "decklists"):
                    pipeline_health["decklist_table_present"] = True
                    try:
                        pipeline_health["decklist_deck_count"] = int(
                            _scalar(dconn, "SELECT COUNT(DISTINCT deck_uid) FROM decklists") or 0
                        )
                    except Exception as exc:
                        alerts.append(f"decklists_count:{exc}")
                else:
                    pipeline_health["decklist_table_present"] = False

                if _table_exists(dconn, "leader_card_signals"):
                    try:
                        nrows = int(
                            _scalar(dconn, "SELECT COUNT(*) FROM leader_card_signals") or 0
                        )
                        signal_health["leader_card_signals_rows"] = nrows
                        signal_health["signals_nonempty"] = nrows > 0
                        metrics["signal_cards_covered"] = int(
                            _scalar(
                                dconn,
                                "SELECT COUNT(DISTINCT card_code) FROM leader_card_signals",
                            )
                            or 0
                        )
                        metrics["signal_leaders_covered"] = int(
                            _scalar(
                                dconn,
                                "SELECT COUNT(DISTINCT leader_code) FROM leader_card_signals",
                            )
                            or 0
                        )
                    except Exception as exc:
                        alerts.append(f"leader_card_signals:{exc}")
                else:
                    alerts.append("table_missing:leader_card_signals")
    else:
        alerts.append(f"missing_deck_intel_db:{DECK_INTEL_DB.relative_to(project_root)}")
        pipeline_health["deck_intel_db_ok"] = False

    # --- Approved sources config ---
    src_list, src_err = _load_approved_sources()
    if src_err:
        alerts.append(f"approved_sources:{src_err}")
    enabled = [s for s in src_list if bool(s.get("enabled", True))]
    metrics["sources_active"] = len(enabled)
    metrics["sources_with_live_adapter"] = sum(1 for s in enabled if _source_has_live_adapter(s))

    source_freshness = _snapshot_freshness_warnings(project_root, src_list)
    for w in source_freshness.get("warnings") or []:
        if isinstance(w, str):
            alerts.append(f"snapshot:{w}")

    # Capability + blockers
    capability_level = _derive_capability_level(metrics, pipeline_health, alerts)
    top_blocker, next_priority = _derive_blocker_and_next(
        metrics, alerts, sync_payload, source_freshness.get("warnings") or []
    )
    intelligence_surface = _build_intelligence_surface(
        metrics, insight_distribution, alerts, sync_payload
    )
    next_derived = next_priority
    if str(intelligence_surface.get("primary_limitation_code") or "").strip() != "balanced":
        ih = str(intelligence_surface.get("primary_limitation_human") or "").strip()
        if ih:
            top_blocker = ih
        next_priority = intelligence_surface.get("recommended_next_operator_action") or next_derived
    else:
        next_priority = next_derived

    # Slim summary only (avoid multi-KB candidate lists in the operator file)
    sync_insight_summary: dict[str, Any] | None = None
    if sync_payload:
        sync_insight_summary = {
            "updated_at": str(sync_payload.get("updated_at") or sync_row_updated_at or ""),
            "sync_mode": str(sync_payload.get("sync_mode") or ""),
            "remaining_count": int(sync_payload.get("remaining_count") or 0),
            "candidate_count": int(sync_payload.get("candidate_count") or 0),
            "inserted_insights": int(sync_payload.get("inserted_insights") or 0),
            "synced_cards": int(sync_payload.get("synced_cards") or 0),
            "skipped_cards": int(sync_payload.get("skipped_cards") or 0),
        }

    return {
        "generated_at": _utc_now_iso(),
        "schema_version": 2,
        "capability_level": capability_level,
        "metrics": metrics,
        "pipeline_health": pipeline_health,
        "signal_health": signal_health,
        "insight_distribution": insight_distribution,
        "source_freshness": source_freshness,
        "sync_insight_summary": sync_insight_summary,
        "intelligence_surface": intelligence_surface,
        "alerts": alerts,
        "top_blocker": top_blocker,
        "next_priority": next_priority,
    }


def _build_intelligence_surface(
    metrics: dict[str, Any],
    insight_distribution: dict[str, Any],
    alerts: list[str],
    sync_payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Operator-facing summary: coverage shape, distribution, weakest lane, limitation code, next action.
    Read-only; does not change governance or thresholds.
    """
    by_type = dict(insight_distribution.get("by_insight_type") or {})
    avg_by = dict(metrics.get("avg_confidence_by_insight_type") or {})
    type_labels = {
        "usage": "usage",
        "meta": "meta",
        "strength": "strategy",
        "ruling": "ruling",
        "price": "price",
        "synergy": "synergy",
        "lore": "lore",
    }
    weakest: str | None = None
    weakest_avg: float | None = None
    weakest_rows = 0
    for itype, payload in avg_by.items():
        if not isinstance(payload, dict):
            continue
        av = payload.get("avg")
        n = int(payload.get("rows") or 0)
        if av is None or n < 1:
            continue
        if weakest_avg is None or float(av) < float(weakest_avg):
            weakest_avg = float(av)
            weakest = str(itype)
            weakest_rows = n

    rem = int(metrics.get("insight_sync_remaining_candidates") or 0)
    pub_rev = int(metrics.get("publication_cards_pending_review") or 0)
    cov = metrics.get("coverage_pct")
    snap_n = sum(1 for a in alerts if str(a).startswith("snapshot:"))
    gap = int(metrics.get("dossier_to_insight_gap") or 0)
    ct = int(metrics.get("cards_total") or 0)

    primary_code = "balanced"
    primary_human = "Published insights look broadly aligned with the last catalog sync run."

    if rem > 100:
        primary_code = "sync_backlog"
        primary_human = f"Insight sync backlog: {rem} candidate card(s) still queued — biggest limit on coverage."
    elif pub_rev > 25:
        primary_code = "publication_backlog"
        primary_human = f"Publication review backlog: {pub_rev} card intelligence row(s) are not publish-ready yet."
    elif cov is not None and float(cov) < 8.0 and ct > 100:
        primary_code = "weak_signal_coverage"
        primary_human = "Most catalog cards still lack a published insight line — coverage is the main gap."
    elif snap_n > 2:
        primary_code = "source_freshness"
        primary_human = "Several approved-source snapshots are stale or missing — freshness is the bottleneck."
    elif gap > 150:
        primary_code = "dossier_insight_gap"
        primary_human = f"Learning dossiers are ahead of catalog insights by ~{gap} card(s) — sync hasn’t caught up."
    elif weakest and weakest_avg is not None and weakest_avg < 0.58 and weakest_rows >= 5:
        label = type_labels.get(weakest, weakest)
        primary_code = "low_confidence_lane"
        primary_human = (
            f'"{label}" insights average {weakest_avg:.2f} confidence — weakest published lane right now.'
        )

    rec: str | None = None
    if rem > 0:
        rec = "Run bounded insight sync until remaining_candidate_count trends down."
    elif pub_rev > 10:
        rec = "Work the publication review queue on Dev — unblock publish_requires_review rows you trust."
    elif cov is not None and float(cov) < 12:
        rec = "Enrich dossiers + deck signals, then re-run insight sync to lift coverage %."
    elif weakest == "price" and weakest_avg is not None and weakest_avg < 0.62:
        rec = "Refresh watch prices and re-sync so price insights aren’t anchored to stale snapshots."
    elif snap_n > 0:
        rec = "Refresh stale source snapshots (see self-report alerts) before the next insight sync."
    else:
        rec = "Keep scheduled learning cycles; re-run self-report after major ingest."

    return {
        "type_display_labels": type_labels,
        "weakest_insight_category": weakest,
        "weakest_avg_confidence": round(weakest_avg, 4) if weakest_avg is not None else None,
        "weakest_row_count": weakest_rows,
        "primary_limitation_code": primary_code,
        "primary_limitation_human": primary_human,
        "recommended_next_operator_action": rec,
        "distribution_by_type": by_type,
        "avg_confidence_by_insight_type": avg_by,
    }


def _derive_capability_level(
    metrics: dict[str, Any],
    pipeline_health: dict[str, Any],
    alerts: list[str],
) -> str:
    if not pipeline_health.get("card_catalog_db_ok"):
        return "blocked"
    if any(a.startswith("missing_catalog") for a in alerts):
        return "blocked"
    cov = metrics.get("coverage_pct")
    sig_cards = metrics.get("signal_cards_covered") or 0
    if cov is None:
        return "unknown"
    if cov >= 20.0 and sig_cards > 0:
        return "operational"
    if cov >= 5.0 or sig_cards > 0:
        return "degraded"
    return "minimal"


def _derive_blocker_and_next(
    metrics: dict[str, Any],
    alerts: list[str],
    sync_payload: dict[str, Any],
    snap_warnings: list[str],
) -> tuple[str | None, str | None]:
    """Pick a single top blocker string and a recommended next step (read-only heuristics)."""
    blockers: list[tuple[int, str]] = []

    if not (Path(CATALOG_DB).is_file()):
        blockers.append((100, "Card catalog database is missing; ingest cards before insights."))
    if any("missing_deck_intel" in a for a in alerts):
        blockers.append((85, "Deck intel database missing; import decklists for signal coverage."))
    if metrics.get("sync_never_run"):
        blockers.append((80, "Insight sync has not completed; run worktree card insight sync."))
    rem = int(metrics.get("insight_sync_remaining_candidates") or 0)
    if rem > 0:
        blockers.append((55, f"Insight sync backlog: {rem} candidate card(s) remaining."))

    qp = int(metrics.get("queue_pending") or 0)
    if qp > 50:
        blockers.append((50, f"Publication review queue backlog: {qp} pending item(s)."))
    elif qp > 10:
        blockers.append((35, f"Review queue attention: {qp} pending item(s)."))

    qs = int(metrics.get("queue_stale") or 0)
    if qs > 0:
        blockers.append((45, f"Stale pending reviews: {qs} item(s) older than {_STALE_QUEUE_DAYS} days."))

    cov = metrics.get("coverage_pct")
    if cov is not None and cov < 5.0 and int(metrics.get("cards_total") or 0) > 50:
        blockers.append((40, f"Low catalog insight coverage ({cov}%); expand sync or dossier depth."))

    if snap_warnings:
        blockers.append((25, f"Snapshot freshness: {len(snap_warnings)} warning(s); check data/snapshots."))

    gap = metrics.get("dossier_to_insight_gap")
    if gap is not None and gap > 100:
        blockers.append((30, f"Dossier→insight gap: {gap} dossier card(s) without catalog insights."))

    top: str | None = None
    if blockers:
        blockers.sort(key=lambda x: -x[0])
        top = blockers[0][1]

    # Next priority (complement top blocker)
    nxt: str | None = None
    if rem > 0:
        nxt = "Run bounded insight sync until remaining_candidate_count trends down."
    elif qp > 5:
        nxt = "Triage publication review queue (pending approvals)."
    elif cov is not None and cov < 15.0:
        nxt = "Increase insight coverage via sandbox cycle + dossier enrichment."
    elif not Path(DECK_INTEL_DB).is_file():
        nxt = "Import decklists and recompute deck signals."
    else:
        nxt = "Continue scheduled sandbox cycles; monitor alerts and snapshot freshness."

    return top, nxt


def write_self_report(
    project_root: Path | None = None,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Build the self-report and atomically write JSON to data/miru_self_report.json.
    Returns the report dict plus output_path.
    """
    project_root = project_root or ROOT
    report = build_self_report(project_root)
    out = output_path or (project_root / "data" / "miru_self_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    payload = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(out)
    report["output_path"] = str(out.relative_to(project_root))
    return report


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Write Miru operator self-report JSON (read-only inputs).")
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Print JSON to stdout instead of only writing the file.",
    )
    args = p.parse_args(argv)
    r = write_self_report()
    if args.stdout:
        print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(f"Wrote {r.get('output_path')} capability={r.get('capability_level')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
