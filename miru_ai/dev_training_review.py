"""Dev / training review queue + persistence (Miru 18765).

Queue rows are grounded in ``card_catalog.db`` + ``image_assets.local_path`` under
``D:\\Miru_Assets``. Images are exposed only via ``/img/<relpath>`` when the file exists
(fail-closed). Review submissions append to ``data/miru_dev_training_reviews.db``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from typing import Any

# Resolved in configure(); default PROJECT_ROOT = repo root (parent of miru_ai/)
_PROJECT_ROOT: Path | None = None
MIRU_ASSETS_ROOT = Path(r"D:\Miru_Assets")
_DB_PATH: Path | None = None
_LOCK = Lock()


def configure(project_root: Path, miru_assets_root: Path | None = None) -> None:
    global _PROJECT_ROOT, _DB_PATH, MIRU_ASSETS_ROOT
    _PROJECT_ROOT = Path(project_root)
    _DB_PATH = _PROJECT_ROOT / "data" / "miru_dev_training_reviews.db"
    if miru_assets_root is not None:
        MIRU_ASSETS_ROOT = Path(miru_assets_root)


def _catalog_db_path() -> Path:
    root = _PROJECT_ROOT or Path(__file__).resolve().parent.parent
    return root / "data" / "card_catalog.db"


def _reviews_db_path() -> Path:
    if _DB_PATH is not None:
        return _DB_PATH
    root = _PROJECT_ROOT or Path(__file__).resolve().parent.parent
    return root / "data" / "miru_dev_training_reviews.db"


def _assets_root() -> Path:
    return MIRU_ASSETS_ROOT.resolve()


def _img_url_if_file(rel: str) -> str | None:
    text = str(rel or "").strip().replace("\\", "/")
    if not text or any(p == ".." for p in text.split("/")):
        return None
    root = _assets_root()
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return "/img/" + text
    return None


def _ensure_reviews_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dev_training_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            card_code TEXT NOT NULL,
            printing_id INTEGER,
            variant_key TEXT,
            miru_image_relpath TEXT,
            verdict TEXT NOT NULL,
            issues_json TEXT NOT NULL DEFAULT '[]',
            because_note TEXT NOT NULL DEFAULT '',
            source_note TEXT NOT NULL DEFAULT '',
            missing_image_source_url TEXT NOT NULL DEFAULT '',
            missing_image_upload_name TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            client_payload_json TEXT NOT NULL DEFAULT '{}',
            correction_detail_json TEXT NOT NULL DEFAULT '[]'
        )
        """
    )
    # Migrate existing tables that lack correction_detail_json.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(dev_training_reviews)")}
    if "correction_detail_json" not in cols:
        conn.execute(
            "ALTER TABLE dev_training_reviews "
            "ADD COLUMN correction_detail_json TEXT NOT NULL DEFAULT '[]'"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_training_reviews_card "
        "ON dev_training_reviews(card_code)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dev_training_reviews_created "
        "ON dev_training_reviews(created_at)"
    )


def _fetch_miru_history_for_card_codes(
    card_codes: list[str],
) -> dict[str, list[dict[str, str]]]:
    """Load prior ``dev_training_reviews`` rows for queue items (newest first per card).

    Shapes ``miruHistory`` entries for the operator / training drawer: ``id``, ``title``, ``body``.
    """
    out: dict[str, list[dict[str, str]]] = {}
    if not card_codes:
        return out
    db_path = _reviews_db_path()
    if not db_path.is_file():
        return out
    for cc in card_codes:
        out[str(cc).strip().upper()] = []
    try:
        with closing(sqlite3.connect(str(db_path), timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_reviews_schema(conn)
            ph = ",".join("?" * len(card_codes))
            params = [str(c).strip().upper() for c in card_codes]
            rows = conn.execute(
                f"""
                SELECT id, created_at, card_code, variant_key, verdict, action,
                       because_note, issues_json
                FROM dev_training_reviews
                WHERE card_code IN ({ph})
                ORDER BY card_code, id DESC
                """,
                params,
            ).fetchall()
    except sqlite3.Error:
        return out
    for r in rows:
        code = str(r["card_code"] or "").strip().upper()
        if code not in out:
            continue
        if len(out[code]) >= 25:
            continue
        created = str(r["created_at"] or "")[:19].replace("T", " ")
        action = str(r["action"] or "").strip()
        verdict = str(r["verdict"] or "").strip()
        vk = str(r["variant_key"] or "").strip()
        title = f"{created} · {action} · {verdict}"
        if vk:
            title = f"{title} · {vk}"
        try:
            issues = json.loads(r["issues_json"] or "[]")
        except json.JSONDecodeError:
            issues = []
        because = str(r["because_note"] or "").strip()
        parts: list[str] = []
        if isinstance(issues, list) and issues:
            parts.append("Issues: " + ", ".join(str(x) for x in issues))
        if because:
            parts.append("Note: " + because)
        body = "\n".join(parts) if parts else "No additional note recorded."
        created_iso = str(r["created_at"] or "").strip()
        out[code].append(
            {
                "id": f"rv-{int(r['id'])}",
                "title": title,
                "body": body,
                "createdAtIso": created_iso,
                "action": action,
                "verdict": verdict,
                "variantKey": vk,
            }
        )
    return out


def training_reviews_count() -> int:
    path = _reviews_db_path()
    if not path.is_file():
        return 0
    try:
        with closing(sqlite3.connect(str(path), timeout=10)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            _ensure_reviews_schema(conn)
            row = conn.execute(
                "SELECT COUNT(*) FROM dev_training_reviews"
            ).fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def op01_throughput_stats() -> dict[str, Any]:
    """Return OP01-scoped throughput metrics for the mission control surface."""
    path = _reviews_db_path()
    out: dict[str, Any] = {
        "total_reviews": 0,
        "today_reviews": 0,
        "distinct_cards_reviewed": 0,
        "op01_total_cards": 0,
    }
    if not path.is_file():
        return out
    try:
        with closing(sqlite3.connect(str(path), timeout=10)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            _ensure_reviews_schema(conn)
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM dev_training_reviews WHERE card_code LIKE 'OP01-%'"
            ).fetchone()
            out["total_reviews"] = int(row["cnt"]) if row else 0
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM dev_training_reviews "
                "WHERE card_code LIKE 'OP01-%' AND DATE(created_at) = DATE('now')"
            ).fetchone()
            out["today_reviews"] = int(row["cnt"]) if row else 0
            row = conn.execute(
                "SELECT COUNT(DISTINCT card_code) AS cnt FROM dev_training_reviews WHERE card_code LIKE 'OP01-%'"
            ).fetchone()
            out["distinct_cards_reviewed"] = int(row["cnt"]) if row else 0
    except sqlite3.Error:
        pass
    # OP01 catalog count from card_catalog.db
    cat_path = _catalog_db_path()
    if cat_path.is_file():
        try:
            uri = f"file:{cat_path}?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as con:
                row = con.execute(
                    "SELECT COUNT(*) AS cnt FROM cards WHERE UPPER(set_code) = 'OP01'"
                ).fetchone()
                out["op01_total_cards"] = int(row[0]) if row else 0
        except sqlite3.Error:
            pass
    return out


def persist_training_review_row(record: dict[str, Any]) -> tuple[bool, str, int | None]:
    """Insert one review row. Returns (ok, message, row_id).

    Retries once on ``database is locked`` to handle contention with
    background evidence-collection threads.
    """
    import time as _time

    path = _reviews_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    last_err = ""
    for attempt in range(3):
        with _LOCK:
            try:
                with closing(sqlite3.connect(str(path), timeout=15)) as conn:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA busy_timeout=10000")
                    _ensure_reviews_schema(conn)
                    cur = conn.execute(
                        """
                        INSERT INTO dev_training_reviews (
                            created_at, card_code, printing_id, variant_key,
                            miru_image_relpath, verdict, issues_json, because_note,
                            source_note, missing_image_source_url,
                            missing_image_upload_name, action, client_payload_json,
                            correction_detail_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            now,
                            record.get("card_code") or "",
                            record.get("printing_id"),
                            record.get("variant_key") or "",
                            record.get("miru_image_relpath") or "",
                            record.get("verdict") or "",
                            json.dumps(record.get("issues") or []),
                            record.get("because") or "",
                            record.get("source") or "",
                            record.get("missing_image_source_url") or "",
                            record.get("missing_image_upload_name") or "",
                            record.get("action") or "",
                            json.dumps(record.get("client_payload") or {}),
                            json.dumps(
                                record.get("correction_detail") or [],
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    conn.commit()
                    return True, "stored", cur.lastrowid
            except sqlite3.OperationalError as e:
                last_err = str(e)
                if "locked" in last_err and attempt < 2:
                    _time.sleep(0.5 * (attempt + 1))
                    continue
                return False, last_err, None
            except sqlite3.Error as e:
                return False, str(e), None
    return False, last_err or "max retries", None


def build_training_review_queue_payload(
    *, limit: int = 28, offset: int = 0, set_code_filter: str = "",
) -> dict[str, Any]:
    """Load catalog-backed queue + stats for the Dev training UI.

    When *set_code_filter* is non-empty (e.g. ``"OP01"``), only cards
    whose ``set_code`` matches are returned.  *offset* supports
    paginated replenishment without re-fetching earlier items.
    """
    out: dict[str, Any] = {
        "items": [],
        "stats": {
            "reviewedCount": training_reviews_count(),
            "queueTotal": 0,
        },
        "hasMore": False,
        "error": "",
    }
    db_path = _catalog_db_path()
    if not db_path.is_file():
        out["error"] = "card_catalog.db not found."
        return out

    lim = max(1, min(int(limit or 28), 80))
    off = max(0, int(offset or 0))
    sc_filter = str(set_code_filter or "").strip().upper()
    try:
        uri = f"file:{db_path}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        where_clause = "WHERE EXISTS (SELECT 1 FROM card_variants cv WHERE cv.card_id = c.id)"
        params: list[Any] = []
        if sc_filter:
            where_clause += " AND UPPER(c.set_code) = ?"
            params.append(sc_filter)
        card_rows = con.execute(
            f"""
            SELECT c.id AS card_id, c.canonical_code, c.card_name, c.set_code, c.card_number
            FROM cards c
            {where_clause}
            ORDER BY c.canonical_code
            LIMIT {lim} OFFSET {off}
            """,
            params,
        ).fetchall()
        items: list[dict[str, Any]] = []
        for cr in card_rows:
            cid = cr["card_id"]
            code = str(cr["canonical_code"] or "").strip().upper()
            vrows = con.execute(
                """
                SELECT
                    cv.id AS printing_id,
                    cv.variant_key,
                    cv.variant_label,
                    cv.image_path AS cv_image_path,
                    ia.local_path AS ia_local_path
                FROM card_variants cv
                LEFT JOIN image_assets ia
                  ON ia.printing_id = cv.id AND ia.is_primary = 1
                WHERE cv.card_id = ?
                ORDER BY cv.variant_key, cv.id
                """,
                (cid,),
            ).fetchall()
            variants: list[dict[str, Any]] = []
            for vr in vrows:
                pid = int(vr["printing_id"])
                vk = str(vr["variant_key"] or "").strip()
                label = str(vr["variant_label"] or "").strip() or vk
                rel = str(vr["ia_local_path"] or "").strip()
                if not rel:
                    rel = str(vr["cv_image_path"] or "").strip().replace("\\", "/")
                url = _img_url_if_file(rel)
                rel_out = rel if url else (rel if rel else None)
                variants.append(
                    {
                        "id": str(pid),
                        "label": label,
                        "variantKey": vk,
                        "imageUrl": url,
                        "miruAssetsRelPath": rel if rel else None,
                    }
                )
            if not variants:
                continue
            version = variants[0]["label"]
            segs = [
                "pending",
                "empty",
                "empty",
                "empty",
                "empty",
                "empty",
            ]  # type: ignore
            thumb = None
            for v in variants:
                if v.get("imageUrl"):
                    thumb = v["imageUrl"]
                    break
            items.append(
                {
                    "id": code,
                    "cardCode": code,
                    "name": str(cr["card_name"] or "").strip() or code,
                    "setCode": str(cr["set_code"] or "").strip(),
                    "cardNumber": str(cr["card_number"] or "").strip(),
                    "version": version,
                    "segments": segs,
                    "thumbUrl": thumb,
                    "variants": variants,
                    "miruHistory": [],
                }
            )
        codes = [str(it["cardCode"]) for it in items]
        hist_map = _fetch_miru_history_for_card_codes(codes)
        for it in items:
            cc = str(it.get("cardCode") or "").strip().upper()
            it["miruHistory"] = hist_map.get(cc, [])
        # Determine hasMore: peek one row beyond the batch.
        peek = con.execute(
            f"""
            SELECT 1 FROM cards c
            {where_clause}
            ORDER BY c.canonical_code
            LIMIT 1 OFFSET {off + len(items)}
            """,
            params,
        ).fetchone()
        con.close()

        # Enrich items with state/issue/context fields for operator console.
        items = _enrich_queue_items(items)

        out["items"] = items
        out["hasMore"] = peek is not None
        out["stats"]["queueTotal"] = len(items)
    except Exception as exc:
        out["error"] = f"Queue load failed: {exc}"
    return out


# ---------------------------------------------------------------------------
# Queue enrichment — adds state, issues, contextSentence for operator console
# ---------------------------------------------------------------------------

# Cooling window: cards with a recent reject/fix_it/hold are suppressed
# from the active queue front for this many minutes.
_COOLING_WINDOW_MINUTES = 30


def _enrich_queue_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add operator-console fields to raw queue items.

    Reads evidence reconciliation and review history to derive state,
    issue badges, cooling status, and a human-readable context sentence.
    Cards within the cooling window after a reject/fix_it/hold are moved
    to the end of the queue rather than excluded entirely (so the operator
    can still scroll to them if needed).
    """
    reviews_db = _reviews_db_path()
    review_data: dict[str, dict[str, Any]] = {}
    if reviews_db.is_file():
        try:
            with closing(sqlite3.connect(str(reviews_db), timeout=10)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT card_code, COUNT(*) AS review_count, "
                    "MAX(created_at) AS last_reviewed_at, "
                    "GROUP_CONCAT(DISTINCT issues_json) AS all_issues "
                    "FROM dev_training_reviews GROUP BY card_code"
                ).fetchall()
                # Get the actual last action per card (from the most recent review).
                last_actions: dict[str, str] = {}
                la_rows = conn.execute(
                    "SELECT card_code, action FROM dev_training_reviews "
                    "WHERE id IN ("
                    "  SELECT MAX(id) FROM dev_training_reviews GROUP BY card_code"
                    ")"
                ).fetchall()
                for la in la_rows:
                    last_actions[la["card_code"]] = la["action"]
                for r in rows:
                    review_data[r["card_code"]] = {
                        "review_count": r["review_count"],
                        "last_action": last_actions.get(r["card_code"], ""),
                        "last_reviewed_at": r["last_reviewed_at"],
                        "all_issues": r["all_issues"] or "",
                    }
                # Fetch latest reconciliation status per card_code.
                recon_rows = conn.execute(
                    "SELECT dtr.card_code, er.reconciliation_status "
                    "FROM dev_training_reviews dtr "
                    "JOIN evidence_reconciliation er ON er.review_id = dtr.id "
                    "ORDER BY er.reconciled_at DESC"
                ).fetchall()
                for rr in recon_rows:
                    cc = rr["card_code"]
                    if cc in review_data and "recon" not in review_data[cc]:
                        review_data[cc]["recon"] = rr["reconciliation_status"]
        except Exception:
            pass

    now = datetime.now(timezone.utc)
    active_items: list[dict[str, Any]] = []
    cooling_items: list[dict[str, Any]] = []

    for item in items:
        code = item.get("cardCode", "")
        rd = review_data.get(code, {})
        has_image = item.get("thumbUrl") is not None
        has_reviews = rd.get("review_count", 0) > 0
        recon = rd.get("recon", "")
        last_action = rd.get("last_action", "")
        last_reviewed_at = rd.get("last_reviewed_at", "")

        # state: "staged" if we have a local image, "live" otherwise
        item["state"] = "staged" if has_image else "live"

        # issues: derive from review history and image availability
        issues: list[str] = []
        if not has_image:
            issues.append("missing_art")
        if not has_reviews:
            issues.append("new_card")
        if recon == "CONTRADICTED":
            issues.append("stat_mismatch")
        # Parse historical issues from reviews for name/parallel detection.
        all_iss = rd.get("all_issues", "")
        if "name" in all_iss.lower() or "metadata" in all_iss.lower():
            issues.append("name_mismatch")
        if "variant_code" in all_iss.lower():
            issues.append("false_parallel")
        item["issues"] = issues

        # reconciliationStatus
        item["reconciliationStatus"] = recon if recon else None

        # Cooling window: recently rejected/held cards go to the back.
        is_cooling = False
        if last_action in ("fix_it", "hold") and last_reviewed_at:
            try:
                reviewed_dt = datetime.fromisoformat(last_reviewed_at)
                if reviewed_dt.tzinfo is None:
                    reviewed_dt = reviewed_dt.replace(tzinfo=timezone.utc)
                elapsed = (now - reviewed_dt).total_seconds() / 60
                if elapsed < _COOLING_WINDOW_MINUTES:
                    is_cooling = True
                    item["coolingUntil"] = (
                        reviewed_dt.replace(microsecond=0)
                        + timedelta(minutes=_COOLING_WINDOW_MINUTES)
                    ).isoformat()
            except (ValueError, TypeError):
                pass

        # contextSentence
        if is_cooling:
            mins_left = max(1, int(_COOLING_WINDOW_MINUTES - elapsed))
            item["contextSentence"] = (
                f"Recently handled ({last_action}) — cooling for ~{mins_left} min."
            )
        else:
            item["contextSentence"] = _build_context_sentence(
                code, issues, recon, has_reviews,
            )

        if is_cooling:
            cooling_items.append(item)
        else:
            active_items.append(item)

    # Active items first, cooling items at the back.
    return active_items + cooling_items


def _build_context_sentence(
    code: str, issues: list[str], recon: str, has_reviews: bool,
) -> str:
    """Build a single plain-English context sentence for a queue card."""
    if recon == "CONTRADICTED":
        return "Evidence contradiction detected — review before approving."
    if "missing_art" in issues and "new_card" in issues:
        return "New card with no local artwork — needs initial verification."
    if "missing_art" in issues:
        return "Missing local artwork — image evidence may be needed."
    if "new_card" in issues:
        return "First time in review queue — no prior operator decisions."
    if "name_mismatch" in issues:
        return "Possible name discrepancy flagged from prior reviews."
    if "false_parallel" in issues:
        return "Variant code issue flagged — possible false parallel."
    if "stat_mismatch" in issues:
        return "Stat mismatch flagged by evidence cross-check."
    if recon == "SUPPORTED":
        return "Evidence supports prior operator decision."
    if has_reviews:
        return "Previously reviewed — ready for re-evaluation."
    return "Awaiting initial operator review."


# ---------------------------------------------------------------------------
# Verify-action pre-flight (governance gate)
# ---------------------------------------------------------------------------

def verify_action_preflight(
    card_id: str, variant_id: str, action: str,
) -> dict[str, Any]:
    """Check whether an operator action is permissible before commit.

    Returns ``{"ok": True}`` or ``{"ok": False, "conflict": ..., "banner": ...}``.
    """
    card_code = str(card_id or "").strip().upper()
    if not card_code:
        return {"ok": False, "conflict": "missing_card", "banner": "Card ID is required."}
    if action not in ("approve", "fix_it", "hold"):
        return {"ok": False, "conflict": "invalid_action", "banner": f"Unknown action: {action}"}

    reviews_db = _reviews_db_path()
    if not reviews_db.is_file():
        # No review DB yet — action is permissible (first review ever).
        return {"ok": True}

    try:
        with closing(sqlite3.connect(str(reviews_db), timeout=10)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row

            # Check 1: Active CONTRADICTED reconciliation blocks approval.
            if action == "approve":
                row = conn.execute(
                    "SELECT er.reconciliation_status, er.review_id "
                    "FROM dev_training_reviews dtr "
                    "JOIN evidence_reconciliation er ON er.review_id = dtr.id "
                    "WHERE dtr.card_code = ? "
                    "ORDER BY er.reconciled_at DESC LIMIT 1",
                    (card_code,),
                ).fetchone()
                if row and row["reconciliation_status"] == "CONTRADICTED":
                    return {
                        "ok": False,
                        "conflict": "contradicted_evidence",
                        "banner": "This card has an active evidence contradiction. Review evidence or use Fix It before approving.",
                    }

            # Check 2: Stale evidence requiring recollection.
            stale_row = conn.execute(
                "SELECT er.reconciliation_status "
                "FROM dev_training_reviews dtr "
                "JOIN evidence_reconciliation er ON er.review_id = dtr.id "
                "WHERE dtr.card_code = ? AND er.reconciliation_status = 'ERROR' "
                "ORDER BY er.reconciled_at DESC LIMIT 1",
                (card_code,),
            ).fetchone()
            if stale_row and action == "approve":
                return {
                    "ok": False,
                    "conflict": "stale_evidence",
                    "banner": "Evidence collection failed for this card. Review or re-submit before approving.",
                }

            # Check 3: Card already promoted (superseded).
            try:
                promoted = conn.execute(
                    "SELECT candidate_status FROM correction_candidates "
                    "WHERE card_code = ? AND candidate_status IN ('PROMOTED', 'SUPERSEDED') "
                    "LIMIT 1",
                    (card_code,),
                ).fetchone()
                if promoted:
                    return {
                        "ok": False,
                        "conflict": "already_promoted",
                        "banner": f"This card has already been {promoted['candidate_status'].lower()}. No further action needed.",
                    }
            except sqlite3.OperationalError:
                pass  # correction_candidates table may not exist yet

    except sqlite3.Error:
        pass  # Fail open — if DB is unavailable, allow the action.

    return {"ok": True}
