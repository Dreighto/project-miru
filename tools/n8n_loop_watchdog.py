#!/usr/bin/env python3
"""n8n loop watchdog — monitors n8n workflows for failures, silence, and recurring errors.

Runs every 15 minutes via Windows Task Scheduler (register_watchdog_task.ps1).
Independent of n8n: reads the n8n REST API directly, stores state in miru_memory.db.

Config:  data/config/watchdog_registry.json
State:   data/miru_memory.db  (watchdog_state table)
Log:     logs/n8n_loop_watchdog.log
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "data" / "config" / "watchdog_registry.json"
DB_PATH = REPO_ROOT / "data" / "miru_memory.db"
LOG_DIR = REPO_ROOT / "logs"
LOG_PATH = LOG_DIR / "n8n_loop_watchdog.log"
ENV_PATH = REPO_ROOT / ".env"

DEFAULT_N8N_BASE_URL = "http://localhost:15678"
N8N_HTTP_TIMEOUT = 10
TELEGRAM_HTTP_TIMEOUT = 15
HC_HTTP_TIMEOUT = 10

_FAIL_STATUSES = {"error", "crashed"}


# ── Bootstrap ─────────────────────────────────────────────────────────────────
def _load_env() -> None:
    if not ENV_PATH.is_file():
        return
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$", raw.strip())
        if not m:
            continue
        val = m.group(2).strip().strip('"').strip("'")
        if not os.environ.get(m.group(1)):
            os.environ[m.group(1)] = val


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s\t%(levelname)s\t%(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return logging.getLogger("watchdog")


# ── Database ──────────────────────────────────────────────────────────────────
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS watchdog_state (
    workflow_id          TEXT PRIMARY KEY,
    workflow_name        TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'healthy',
    last_checked_at      TEXT,
    last_status_change_at TEXT,
    last_execution_id    TEXT,
    last_execution_at    TEXT,
    last_execution_status TEXT,
    failure_count_24h    INTEGER DEFAULT 0,
    recurring_pattern    TEXT,
    last_alert_sent_at   TEXT,
    pending_alert        TEXT
)
"""


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


def _get_state(conn: sqlite3.Connection, wf_id: str) -> dict:
    row = conn.execute("SELECT * FROM watchdog_state WHERE workflow_id = ?", (wf_id,)).fetchone()
    if row:
        return dict(row)
    return {
        "workflow_id": wf_id,
        "workflow_name": "",
        "status": "healthy",
        "last_checked_at": None,
        "last_status_change_at": None,
        "last_execution_id": None,
        "last_execution_at": None,
        "last_execution_status": None,
        "failure_count_24h": 0,
        "recurring_pattern": None,
        "last_alert_sent_at": None,
        "pending_alert": None,
    }


def _upsert_state(conn: sqlite3.Connection, state: dict) -> None:
    conn.execute(
        """
        INSERT INTO watchdog_state (
            workflow_id, workflow_name, status, last_checked_at,
            last_status_change_at, last_execution_id, last_execution_at,
            last_execution_status, failure_count_24h, recurring_pattern,
            last_alert_sent_at, pending_alert
        ) VALUES (
            :workflow_id, :workflow_name, :status, :last_checked_at,
            :last_status_change_at, :last_execution_id, :last_execution_at,
            :last_execution_status, :failure_count_24h, :recurring_pattern,
            :last_alert_sent_at, :pending_alert
        )
        ON CONFLICT(workflow_id) DO UPDATE SET
            workflow_name         = excluded.workflow_name,
            status                = excluded.status,
            last_checked_at       = excluded.last_checked_at,
            last_status_change_at = excluded.last_status_change_at,
            last_execution_id     = excluded.last_execution_id,
            last_execution_at     = excluded.last_execution_at,
            last_execution_status = excluded.last_execution_status,
            failure_count_24h     = excluded.failure_count_24h,
            recurring_pattern     = excluded.recurring_pattern,
            last_alert_sent_at    = excluded.last_alert_sent_at,
            pending_alert         = excluded.pending_alert
        """,
        state,
    )
    conn.commit()


# ── n8n API ────────────────────────────────────────────────────────────────────
def _n8n_get(
    base_url: str, api_key: str, path: str, params: dict | None = None
) -> dict | list | None:
    url = base_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"X-N8N-API-KEY": api_key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=N8N_HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"n8n HTTP {exc.code} on {path}") from exc
    except Exception as exc:
        raise RuntimeError(f"n8n request failed on {path}: {exc}") from exc


def _fetch_executions(base_url: str, api_key: str, wf_id: str, limit: int = 10) -> list[dict]:
    raw = _n8n_get(base_url, api_key, "/api/v1/executions", {"workflowId": wf_id, "limit": limit})
    if isinstance(raw, dict):
        return raw.get("data") or []
    return raw or []


# ── Telegram ───────────────────────────────────────────────────────────────────
def _send_telegram(token: str, chat_id: str, text: str, log: logging.Logger) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TELEGRAM_HTTP_TIMEOUT) as resp:
            result = json.loads(resp.read().decode())
            log.info("telegram_sent message_id=%s", result.get("result", {}).get("message_id"))
            return True
    except Exception as exc:
        log.warning("telegram_failed: %s", exc)
        return False


# ── Healthchecks.io ────────────────────────────────────────────────────────────
def _ping_healthchecks(url: str, log: logging.Logger) -> None:
    if not url:
        return
    try:
        with urllib.request.urlopen(url, timeout=HC_HTTP_TIMEOUT) as resp:
            log.info("healthchecks_ping status=%s", resp.status)
    except Exception as exc:
        log.warning("healthchecks_ping_failed: %s", exc)


# ── Helpers ────────────────────────────────────────────────────────────────────
def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=UTC)
        except ValueError:
            pass
    return None


def _fingerprint(error_msg: str) -> str:
    """Normalize error message to a fingerprint for recurring-pattern detection."""
    s = (error_msg or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    return s[:120]


def _alert_on_cooldown(state: dict, cooldown_minutes: int) -> bool:
    last = _parse_utc(state.get("last_alert_sent_at"))
    if not last:
        return False
    return (datetime.now(UTC) - last) < timedelta(minutes=cooldown_minutes)


def _status_emoji(status: str) -> str:
    return {
        "failing": "🔴",
        "unstable": "🟡",
        "silent": "🟡",
        "recovered": "🟢",
        "healthy": "🟢",
    }.get(status, "⚪")


# ── Check passes ──────────────────────────────────────────────────────────────
def _pass_a_failing(executions: list[dict]) -> tuple[str, str | None, str | None, int]:
    """Return (new_status, last_exec_id, last_exec_ts, failure_count_24h)."""
    if not executions:
        return "healthy", None, None, 0

    last = executions[0]
    last_id = str(last.get("id") or "")
    last_ts = last.get("startedAt") or last.get("stoppedAt") or ""

    # Count failures in last 24 hours
    cutoff = datetime.now(UTC) - timedelta(hours=24)
    fail_24h = 0
    for ex in executions:
        ex_ts = _parse_utc(ex.get("startedAt") or ex.get("stoppedAt"))
        if ex_ts and ex_ts > cutoff and ex.get("status") in _FAIL_STATUSES:
            fail_24h += 1

    recent = executions[:5]
    fail_count = sum(1 for ex in recent if ex.get("status") in _FAIL_STATUSES)
    last_3 = executions[:3]
    last_3_all_fail = len(last_3) >= 3 and all(ex.get("status") in _FAIL_STATUSES for ex in last_3)

    if last_3_all_fail:
        return "failing", last_id, last_ts, fail_24h
    if len(recent) >= 3 and fail_count / len(recent) > 0.5:
        return "unstable", last_id, last_ts, fail_24h
    return "healthy", last_id, last_ts, fail_24h


def _pass_b_silence(wf_config: dict, executions: list[dict], now: datetime) -> bool:
    """Return True if this periodic workflow is silent (no recent execution)."""
    if wf_config.get("class") != "periodic":
        return False
    interval = wf_config.get("expected_interval_seconds", 3600)
    multiplier = wf_config.get("silence_threshold_multiplier", 4)
    threshold_s = interval * multiplier

    if not executions:
        return True
    last_ts_str = executions[0].get("startedAt") or executions[0].get("stoppedAt")
    last_ts = _parse_utc(last_ts_str)
    if not last_ts:
        return True
    return (now - last_ts).total_seconds() > threshold_s


def _pass_c_recurring(executions: list[dict], cutoff_hours: int = 24) -> str | None:
    """Return recurring error fingerprint if same error fired 3+ times in cutoff window."""
    cutoff = datetime.now(UTC) - timedelta(hours=cutoff_hours)
    counts: dict[str, int] = {}
    for ex in executions:
        if ex.get("status") not in _FAIL_STATUSES:
            continue
        ex_ts = _parse_utc(ex.get("startedAt") or ex.get("stoppedAt"))
        if ex_ts and ex_ts < cutoff:
            continue
        err = ex.get("data", {}).get("resultData", {}).get("error", {}).get("message") or ""
        if not err:
            err = str(ex.get("status") or "error")
        fp = _fingerprint(err)
        counts[fp] = counts.get(fp, 0) + 1

    for fp, cnt in counts.items():
        if cnt >= 3:
            return fp
    return None


# ── Alert builder ─────────────────────────────────────────────────────────────
def _build_alert(name: str, new_status: str, old_status: str, state: dict) -> str:
    emoji = _status_emoji(new_status)
    lines = [f"{emoji} *{name}* → {new_status.upper()}"]

    if new_status == "failing":
        lines.append("Last 3 executions all errored.")
    elif new_status == "unstable":
        lines.append("More than half of recent executions failed.")
    elif new_status == "silent":
        lines.append("No recent execution — possible stuck trigger.")
    elif new_status == "recovered":
        changed_at = state.get("last_status_change_at") or ""
        lines.append(f"Was {old_status} since {changed_at or 'unknown'}. Now healthy.")

    if state.get("recurring_pattern"):
        fp = state["recurring_pattern"]
        lines.append(f"Recurring error: _{fp[:80]}_")

    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    _load_env()
    log = _setup_logging()
    log.info("=== watchdog BEGIN ===")

    # Config
    n8n_api_key = os.environ.get("N8N_API_KEY", "").strip()
    n8n_base_url = os.environ.get("MIRU_N8N_BASE_URL", DEFAULT_N8N_BASE_URL).rstrip("/")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not n8n_api_key:
        log.error("N8N_API_KEY not set — cannot query n8n. Exiting.")
        return 1

    # Registry
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("Failed to read registry: %s", exc)
        return 1

    hc_ping_url: str = registry.get("healthchecks_ping_url", "")
    cooldown: int = int(registry.get("alert_cooldown_minutes", 60))
    workflows: dict = registry.get("workflows", {})

    conn = _open_db()
    now = datetime.now(UTC)
    alerts: list[str] = []

    for wf_id, wf_config in workflows.items():
        wf_name = wf_config.get("name", wf_id)
        log.info("checking workflow=%s name=%s", wf_id, wf_name)

        # Fetch executions
        try:
            executions = _fetch_executions(n8n_base_url, n8n_api_key, wf_id, limit=10)
        except RuntimeError as exc:
            log.warning("n8n_fetch_failed workflow=%s: %s", wf_id, exc)
            executions = []

        state = _get_state(conn, wf_id)
        state["workflow_name"] = wf_name
        old_status = state["status"]

        # Pass A — failure detection
        new_status, last_id, last_ts, fail_24h = _pass_a_failing(executions)
        if last_id:
            state["last_execution_id"] = last_id
        if last_ts:
            state["last_execution_at"] = last_ts
        if executions:
            state["last_execution_status"] = executions[0].get("status")
        state["failure_count_24h"] = fail_24h

        # Pass B — silence detection (overrides healthy → silent)
        if new_status == "healthy" and _pass_b_silence(wf_config, executions, now):
            new_status = "silent"

        # Pass C — recurring error patterns
        if executions:
            state["recurring_pattern"] = _pass_c_recurring(executions)

        # Recovery: was bad, now healthy
        if old_status in ("failing", "unstable", "silent") and new_status == "healthy":
            new_status = "recovered"

        state["last_checked_at"] = _now_utc()
        if new_status != old_status:
            state["last_status_change_at"] = _now_utc()

        state["status"] = new_status

        # Alert on state change (with cooldown)
        transitioned = new_status != old_status
        if transitioned and new_status != "healthy":
            if not _alert_on_cooldown(state, cooldown):
                alert_text = _build_alert(wf_name, new_status, old_status, state)
                alerts.append(alert_text)
                state["last_alert_sent_at"] = _now_utc()
                state["pending_alert"] = None
            else:
                log.info("alert_cooldown workflow=%s status=%s", wf_id, new_status)

        # Retry pending alert from previous cycle
        elif state.get("pending_alert") and not _alert_on_cooldown(state, cooldown):
            alerts.append(state["pending_alert"])
            state["last_alert_sent_at"] = _now_utc()
            state["pending_alert"] = None

        log.info(
            "workflow=%s old=%s new=%s fail_24h=%d recurring=%s",
            wf_id,
            old_status,
            new_status,
            fail_24h,
            bool(state.get("recurring_pattern")),
        )
        _upsert_state(conn, state)

    conn.close()

    # Send alerts
    if alerts:
        combined = "\n\n".join(alerts)
        header = f"🛠 *n8n Watchdog* — {now.strftime('%b %d %H:%M')} UTC\n\n"
        full_msg = header + combined
        if tg_token and tg_chat:
            sent = _send_telegram(tg_token, tg_chat, full_msg, log)
            if not sent:
                log.warning("alerts_pending count=%d — will retry next cycle", len(alerts))
        else:
            log.warning("telegram_not_configured — alerts suppressed: %d", len(alerts))
            log.info("alerts:\n%s", combined)
    else:
        log.info("no_alerts — all workflows nominal")

    # Ping Healthchecks.io
    _ping_healthchecks(hc_ping_url, log)

    log.info("=== watchdog DONE ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
