from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import Lock
from typing import Any

from tools.miru_ai_onepiece import inspect_fallback_catalog_db


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG_DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
DEFAULT_VERIFIED_DOSSIER_DB_PATH = PROJECT_ROOT / "data" / "miru_dossiers.db"
DEFAULT_OPERATOR_STATE_PATH = PROJECT_ROOT / "data" / "miru_operator_notifications.json"
_LEARNING_NOTIFICATION_STATE_LOCK = Lock()
VERIFIED_LIBRARY_STATUSES = (
    "verified",
    "high-confidence",
    "verified_with_image_confirmation",
    "verified-with-image-confirmation",
)


def format_compact_number(value: int | float) -> str:
    number = float(value or 0)
    sign = "-" if number < 0 else ""
    absolute = abs(number)
    thresholds = (
        (1_000_000_000, "B"),
        (1_000_000, "M"),
        (1_000, "K"),
    )
    for threshold, suffix in thresholds:
        if absolute >= threshold:
            compact = absolute / threshold
            decimals = 2 if compact < 10 and suffix in {"M", "B"} else 1
            return f"{sign}{compact:.{decimals}f}".rstrip("0").rstrip(".") + suffix
    if absolute.is_integer():
        return f"{sign}{int(absolute)}"
    return f"{sign}{absolute:.1f}".rstrip("0").rstrip(".")


def _load_operator_state(state_path: Path) -> dict[str, Any]:
    if not state_path.is_file():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_operator_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def load_learning_notification_baseline(
    state_path: Path = DEFAULT_OPERATOR_STATE_PATH,
) -> dict[str, Any] | None:
    with _LEARNING_NOTIFICATION_STATE_LOCK:
        state = _load_operator_state(state_path)
    baseline = state.get("learning_summary_baseline")
    return baseline if isinstance(baseline, dict) else None


def save_learning_notification_baseline(
    snapshot: dict[str, Any],
    state_path: Path = DEFAULT_OPERATOR_STATE_PATH,
) -> None:
    with _LEARNING_NOTIFICATION_STATE_LOCK:
        state = _load_operator_state(state_path)
        state["learning_summary_baseline"] = dict(snapshot)
        _save_operator_state(state_path, state)


def load_learning_batch_state(
    state_path: Path = DEFAULT_OPERATOR_STATE_PATH,
) -> dict[str, Any]:
    with _LEARNING_NOTIFICATION_STATE_LOCK:
        state = _load_operator_state(state_path)
    payload = state.get("learning_batch_progress")
    return dict(payload) if isinstance(payload, dict) else {}


def save_learning_batch_state(
    payload: dict[str, Any],
    state_path: Path = DEFAULT_OPERATOR_STATE_PATH,
) -> None:
    with _LEARNING_NOTIFICATION_STATE_LOCK:
        state = _load_operator_state(state_path)
        state["learning_batch_progress"] = dict(payload)
        _save_operator_state(state_path, state)


def load_notified_completed_sets(
    state_path: Path = DEFAULT_OPERATOR_STATE_PATH,
) -> set[str]:
    with _LEARNING_NOTIFICATION_STATE_LOCK:
        state = _load_operator_state(state_path)
    payload = state.get("learning_completed_sets")
    if isinstance(payload, list):
        return {str(item or "").strip().upper() for item in payload if str(item or "").strip()}
    return set()


def save_notified_completed_sets(
    set_codes: set[str],
    state_path: Path = DEFAULT_OPERATOR_STATE_PATH,
) -> None:
    with _LEARNING_NOTIFICATION_STATE_LOCK:
        state = _load_operator_state(state_path)
        state["learning_completed_sets"] = sorted(
            {str(code or "").strip().upper() for code in set_codes if str(code or "").strip()}
        )
        _save_operator_state(state_path, state)


def learning_batch_threshold(verified_count: int) -> int:
    current = max(int(verified_count or 0), 0)
    if current < 250:
        return 10
    if current < 1000:
        return 25
    return 50


def _catalog_verified_learning_totals(catalog_db_path: Path) -> dict[str, Any]:
    totals = {
        "total_cards": 0,
        "verified_dossiers": 0,
        "completed_sets": {},
    }
    if not Path(catalog_db_path).is_file():
        return totals
    try:
        with closing(sqlite3.connect(catalog_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                str(row["name"] or "")
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "cards" not in tables:
                return totals
            columns = {
                str(row["name"] or "")
                for row in conn.execute("PRAGMA table_info(cards)").fetchall()
            }
            totals["total_cards"] = int(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
            if "verification_status" in columns:
                placeholders = ", ".join("?" for _ in VERIFIED_LIBRARY_STATUSES)
                totals["verified_dossiers"] = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM cards
                        WHERE lower(trim(coalesce(verification_status, ''))) IN ({placeholders})
                        """,
                        tuple(VERIFIED_LIBRARY_STATUSES),
                    ).fetchone()[0]
                )
                rows = conn.execute(
                    f"""
                    SELECT
                        set_code,
                        COUNT(*) AS total_cards,
                        SUM(
                            CASE
                                WHEN lower(trim(coalesce(verification_status, ''))) IN ({placeholders})
                                THEN 1
                                ELSE 0
                            END
                        ) AS verified_cards
                    FROM cards
                    WHERE trim(coalesce(set_code, '')) != ''
                    GROUP BY set_code
                    """,
                    tuple(VERIFIED_LIBRARY_STATUSES),
                ).fetchall()
                totals["completed_sets"] = {
                    str(row["set_code"] or "").strip().upper(): {
                        "total_cards": int(row["total_cards"] or 0),
                        "verified_cards": int(row["verified_cards"] or 0),
                    }
                    for row in rows
                    if str(row["set_code"] or "").strip()
                    and int(row["total_cards"] or 0) > 0
                    and int(row["verified_cards"] or 0) >= int(row["total_cards"] or 0)
                }
    except sqlite3.Error:
        return totals
    return totals


def load_verified_learning_totals(
    *,
    catalog_db_path: Path = DEFAULT_CATALOG_DB_PATH,
    dossier_db_path: Path = DEFAULT_VERIFIED_DOSSIER_DB_PATH,
) -> dict[str, int | float]:
    catalog_status = inspect_fallback_catalog_db(catalog_db_path)
    catalog_totals = _catalog_verified_learning_totals(catalog_db_path)
    total_cards = int(catalog_totals.get("total_cards") or 0)
    if total_cards <= 0 and catalog_status.get("usable"):
        total_cards = int(catalog_status.get("cards") or 0)
    dossiers_created = 0
    verified_dossiers = 0

    if Path(dossier_db_path).is_file():
        try:
            with closing(sqlite3.connect(dossier_db_path)) as conn:
                conn.row_factory = sqlite3.Row
                tables = {
                    str(row["name"] or "")
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "cards" in tables:
                    columns = {
                        str(row["name"] or "")
                        for row in conn.execute("PRAGMA table_info(cards)").fetchall()
                    }
                    dossiers_created = int(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
                    if "overall_state" in columns:
                        verified_dossiers = int(
                            conn.execute(
                                "SELECT COUNT(*) FROM cards WHERE lower(coalesce(overall_state, '')) = 'verified'"
                            ).fetchone()[0]
                        )
                    elif "verification_state" in columns:
                        verified_dossiers = int(
                            conn.execute(
                                "SELECT COUNT(*) FROM cards WHERE lower(coalesce(verification_state, '')) = 'verified'"
                            ).fetchone()[0]
                        )
                    elif "confidence_records" in tables:
                        verified_dossiers = int(
                            conn.execute(
                                """
                                SELECT COUNT(DISTINCT card_id)
                                FROM confidence_records
                                WHERE lower(coalesce(verification_state, '')) = 'verified'
                                  AND (
                                        lower(coalesce(scope, '')) = 'card'
                                     OR lower(coalesce(scope_key, '')) = 'overall'
                                  )
                                """
                            ).fetchone()[0]
                        )
                elif "dossiers" in tables:
                    columns = {
                        str(row["name"] or "")
                        for row in conn.execute("PRAGMA table_info(dossiers)").fetchall()
                    }
                    dossiers_created = int(conn.execute("SELECT COUNT(*) FROM dossiers").fetchone()[0])
                    for candidate in ("overall_state", "verification_state", "status"):
                        if candidate in columns:
                            verified_dossiers = int(
                                conn.execute(
                                    f"SELECT COUNT(*) FROM dossiers WHERE lower(coalesce({candidate}, '')) = 'verified'"
                                ).fetchone()[0]
                            )
                            break
        except sqlite3.Error:
            dossiers_created = 0
            verified_dossiers = 0

    if total_cards > 0:
        dossiers_created = total_cards
    verified_dossiers = max(int(catalog_totals.get("verified_dossiers") or 0), verified_dossiers)
    if total_cards > 0:
        dossiers_created = min(dossiers_created, total_cards)
        verified_dossiers = min(verified_dossiers, dossiers_created)
    coverage_percent = round((verified_dossiers / total_cards) * 100.0, 1) if total_cards else 0.0
    return {
        "total_cards": total_cards,
        "dossiers_created": dossiers_created,
        "verified_dossiers": verified_dossiers,
        "verified_coverage_percent": coverage_percent,
    }


def load_completed_verified_sets(
    *,
    catalog_db_path: Path = DEFAULT_CATALOG_DB_PATH,
) -> dict[str, dict[str, int]]:
    completed_sets = _catalog_verified_learning_totals(catalog_db_path).get("completed_sets") or {}
    return {
        str(code or "").strip().upper(): {
            "total_cards": int((payload or {}).get("total_cards") or 0),
            "verified_cards": int((payload or {}).get("verified_cards") or 0),
        }
        for code, payload in completed_sets.items()
        if str(code or "").strip()
    }


def _classify_engine_state(snapshot: dict[str, Any]) -> str:
    current_state = str(snapshot.get("current_state") or "").strip().lower()
    task_type = str(snapshot.get("current_task_type") or "").strip().lower()
    queue_length = int(snapshot.get("queue_length") or 0)

    if current_state == "error" or snapshot.get("last_error"):
        return "stuck"
    if current_state in {"processing", "starting"}:
        if "image" in task_type:
            return "checking images"
        if "source" in task_type or task_type.startswith("verify_") or task_type.startswith("discover_"):
            return "searching"
        return "learning"
    if queue_length > 0:
        return "queued"
    if current_state in {"sleeping", "idle"}:
        return "idle"
    return "idle"


def _describe_task(snapshot: dict[str, Any]) -> str:
    task_label = str(snapshot.get("current_task_label") or "").strip()
    if task_label:
        return task_label
    task_type = str(snapshot.get("current_task_type") or "").strip().lower().replace("_", " ")
    current_card = str(snapshot.get("current_card_code") or "").strip().upper()
    parts = [part for part in (task_type, current_card) if part]
    return " ".join(parts).strip()


def _describe_queue(snapshot: dict[str, Any]) -> str:
    queue_length = int(snapshot.get("queue_length") or 0)
    running_count = int(snapshot.get("running_count") or 0)
    if queue_length <= 0 and running_count <= 0:
        return "Queue is clear."
    if queue_length > 0 and running_count > 0:
        return f"Queue is {format_compact_number(queue_length)} waiting and {format_compact_number(running_count)} running."
    if queue_length > 0:
        return f"Queue is {format_compact_number(queue_length)} waiting."
    return f"Queue has {format_compact_number(running_count)} running."


def build_learning_notification(
    *,
    learning_status: dict[str, Any],
    catalog_db_path: Path = DEFAULT_CATALOG_DB_PATH,
    dossier_db_path: Path = DEFAULT_VERIFIED_DOSSIER_DB_PATH,
    state_path: Path = DEFAULT_OPERATOR_STATE_PATH,
) -> dict[str, Any]:
    totals = load_verified_learning_totals(
        catalog_db_path=catalog_db_path,
        dossier_db_path=dossier_db_path,
    )
    baseline = load_learning_notification_baseline(state_path=state_path)
    snapshot = {
        "dossiers_created": int(totals["dossiers_created"]),
        "learning_dossier_count": int(learning_status.get("dossier_count") or 0),
        "verified_dossiers": int(totals["verified_dossiers"]),
        "verified_coverage_percent": float(totals["verified_coverage_percent"]),
        "queue_length": int(learning_status.get("queue_length") or 0),
        "running_count": int(learning_status.get("running_count") or 0),
        "failed_count": int(learning_status.get("failed_count") or 0),
        "current_state": str(learning_status.get("current_state") or "").strip().lower(),
        "current_task_type": str(learning_status.get("current_task_type") or "").strip().lower(),
        "current_task_label": str(learning_status.get("current_task_label") or "").strip(),
        "current_card_code": str(learning_status.get("current_card_code") or "").strip().upper(),
        "last_error": str(learning_status.get("last_error") or "").strip(),
        "last_completed_task": str(learning_status.get("last_completed_task") or "").strip().lower(),
        "last_completed_card": str(learning_status.get("last_completed_card") or "").strip().upper(),
    }

    baseline_verified = int(baseline.get("verified_dossiers") or 0) if baseline else 0
    baseline_coverage = float(baseline.get("verified_coverage_percent") or 0.0) if baseline else 0.0
    baseline_failed = int(baseline.get("failed_count") or 0) if baseline else 0
    verified_delta = snapshot["verified_dossiers"] - baseline_verified if baseline else 0
    coverage_delta = round(snapshot["verified_coverage_percent"] - baseline_coverage, 1) if baseline else 0.0
    failed_delta = snapshot["failed_count"] - baseline_failed if baseline else 0
    meaningful_gain = bool(baseline) and (verified_delta > 0 or coverage_delta > 0.0)
    engine_state = _classify_engine_state(snapshot)
    task_text = _describe_task(snapshot)
    queue_text = _describe_queue(snapshot)

    if meaningful_gain:
        title = "Miru learning improved"
        if verified_delta > 0:
            first = (
                f"Miru verified +{format_compact_number(verified_delta)} new card dossier"
                f"{'' if verified_delta == 1 else 's'} since the last report."
            )
        else:
            first = "Miru improved verified learning coverage since the last report."
        second = (
            f"Coverage rose from {baseline_coverage:.1f}% to {snapshot['verified_coverage_percent']:.1f}%."
        )
    elif not baseline:
        title = "Miru learning snapshot"
        if snapshot["verified_dossiers"] > 0:
            first = (
                f"Miru currently has {format_compact_number(snapshot['verified_dossiers'])} verified card dossiers."
            )
        elif snapshot["dossiers_created"] > 0:
            first = (
                f"Miru currently has {format_compact_number(snapshot['dossiers_created'])} learning dossiers, "
                "but none are verified yet."
            )
        elif snapshot["learning_dossier_count"] > 0:
            first = (
                f"Miru currently has {format_compact_number(snapshot['learning_dossier_count'])} learning dossiers, "
                "but none are verified yet."
            )
        else:
            first = "Miru does not have verified card dossiers yet."
        second = f"Coverage is {snapshot['verified_coverage_percent']:.1f}%."
    elif snapshot["last_error"] or engine_state == "stuck" or failed_delta > 0:
        title = "Miru may be stuck"
        first = "Miru retried work but no new verified learning was added."
        second = f"Coverage is unchanged at {snapshot['verified_coverage_percent']:.1f}%."
    elif engine_state == "idle" and snapshot["queue_length"] <= 0:
        title = "Miru is idle"
        first = "Miru is online but idle. No queued learning work right now."
        second = f"Coverage is {snapshot['verified_coverage_percent']:.1f}%."
    elif engine_state == "searching":
        title = "Miru is searching"
        first = "Miru scanned sources but did not add new verified learning this cycle."
        second = f"Coverage unchanged at {snapshot['verified_coverage_percent']:.1f}%."
    elif engine_state == "checking images":
        title = "Miru is checking images"
        first = "Miru processed image work but did not add new verified learning this cycle."
        second = f"Coverage unchanged at {snapshot['verified_coverage_percent']:.1f}%."
    else:
        title = "Miru is working"
        first = "Miru is active but has not added new verified learning yet."
        second = f"Coverage unchanged at {snapshot['verified_coverage_percent']:.1f}%."

    engine_sentence = f"Engine is {engine_state}."
    if task_text and engine_state not in {"idle", "stuck"}:
        engine_sentence = f"Engine is {engine_state} on {task_text}."
    elif engine_state == "stuck" and snapshot["last_error"]:
        engine_sentence = f"Engine is stuck. {snapshot['last_error'][:120]}."

    message = " ".join(part for part in (first, second, queue_text, engine_sentence) if part)
    return {
        "title": title,
        "message": message.strip(),
        "meaningful_gain": meaningful_gain,
        "verified_delta": verified_delta if baseline else None,
        "coverage_delta": coverage_delta if baseline else None,
        "engine_state": engine_state,
        "snapshot": snapshot,
    }


def build_batch_progress_notification(
    *,
    learning_status: dict[str, Any],
    verified_delta: int,
    current_verified: int,
    coverage_percent: float,
) -> dict[str, Any]:
    snapshot = {
        "queue_length": int(learning_status.get("queue_length") or 0),
        "running_count": int(learning_status.get("running_count") or 0),
        "current_state": str(learning_status.get("current_state") or "").strip().lower(),
        "current_task_type": str(learning_status.get("current_task_type") or "").strip().lower(),
        "current_task_label": str(learning_status.get("current_task_label") or "").strip(),
        "current_card_code": str(learning_status.get("current_card_code") or "").strip().upper(),
        "last_error": str(learning_status.get("last_error") or "").strip(),
    }
    engine_state = _classify_engine_state(snapshot)
    queue_text = _describe_queue(snapshot)
    if engine_state in {"learning", "searching", "checking images", "queued"}:
        engine_text = "Engine is active."
    elif engine_state == "idle":
        engine_text = "Engine is idle."
    elif engine_state == "stuck":
        engine_text = "Engine may be stuck."
    else:
        engine_text = f"Engine is {engine_state}."
    message = " ".join(
        part
        for part in (
            f"Miru verified {format_compact_number(verified_delta)} more card dossiers.",
            f"Coverage is now {coverage_percent:.1f}%.",
            queue_text,
            engine_text,
        )
        if part
    )
    return {
        "title": "Miru batch progress",
        "message": message.strip(),
        "snapshot": {
            "verified_dossiers": int(current_verified or 0),
            "verified_coverage_percent": float(coverage_percent or 0.0),
        },
    }


def build_set_completion_notification(
    *,
    set_code: str,
) -> dict[str, str]:
    normalized = str(set_code or "").strip().upper()
    return {
        "title": "Set completed",
        "message": f"Miru finished verified coverage for {normalized}. The set is now marked complete.",
    }
