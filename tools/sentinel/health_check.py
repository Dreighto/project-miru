"""
MiruSentinel — periodic system health scanner.

Runs every 20 minutes as the MiruSentinel scheduled task.
Collects service health, log tails, DLQ delta, and recent worker activity,
then asks an AI (Ollama first, OpenAI fallback) if anything looks wrong.
Sends a Telegram alert (+ Pushover fallback) only when something needs
attention — silent otherwise.

Also runs a daily OAuth credential probe to catch expired/missing Claude
auth before it silently breaks worker dispatches.

Entry point: python tools/sentinel/health_check.py
"""

from __future__ import annotations

import contextlib
import datetime
import http.client
import json
import urllib.parse
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_FILE = _REPO_ROOT / "logs" / "sentinel.log"
_STATE_FILE = _REPO_ROOT / "logs" / "sentinel_state.json"
_DLQ_FILE = _REPO_ROOT / "data" / "dispatch_dlq.jsonl"
_HEARTBEAT_LOG = _REPO_ROOT / "data" / "cc_heartbeat_log.jsonl"
_COMPLETION_LOG = _REPO_ROOT / "data" / "cc_completion_log.jsonl"

_HTTP_TIMEOUT_S = 5
_LOG_TAIL_LINES = 20
_OAUTH_CHECK_INTERVAL_H = 24

_HEALTH_ENDPOINTS = {
    "gateway": "http://127.0.0.1:18766/health",
    "dispatch_listener": "http://127.0.0.1:19100/health",
    "pm": "http://127.0.0.1:18080/health",
    "miru_ai": "http://127.0.0.1:18765/api/health",
    "n8n": "http://127.0.0.1:15678/healthz",
}

_WATCH_LOGS = {
    "dispatch_listener_stderr": _REPO_ROOT / "logs" / "dispatch_listener_stderr.log",
    "service_watchdog": _REPO_ROOT / "logs" / "service_watchdog.log",
    "stall_recovery": _REPO_ROOT / "logs" / "stall_recovery.log",
}

# Claude stores OAuth session data under ~/.claude/
_CLAUDE_DIR = Path.home() / ".claude"
_OAUTH_SIGNAL_DIRS = ["sessions", "session-env"]


# ── Logging ───────────────────────────────────────────────────────────────────


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}\t{msg}"
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ── .env loader ───────────────────────────────────────────────────────────────


def _load_env() -> dict[str, str]:
    env_path = _REPO_ROOT / ".env"
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
            v = v[1:-1]
        if k:
            env[k] = v
    return env


# ── State ─────────────────────────────────────────────────────────────────────


def _read_state() -> dict:
    if not _STATE_FILE.exists():
        return {}
    try:
        return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(state: dict) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        _log(f"state_write_failed: {exc}")


# ── Data collection ───────────────────────────────────────────────────────────


def _check_services() -> dict[str, str]:
    results: dict[str, str] = {}
    for name, url in _HEALTH_ENDPOINTS.items():
        try:
            with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_S) as resp:
                results[name] = "up" if resp.status == 200 else f"http_{resp.status}"
        except Exception as exc:
            results[name] = f"down ({type(exc).__name__})"
    return results


def _tail_file(path: Path, n: int) -> str:
    try:
        if not path.exists():
            return "(file not found)"
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = lines[-n:] if len(lines) > n else lines
        return "\n".join(tail) if tail else "(empty)"
    except OSError:
        return "(read error)"


# Stall-recovery log emits these lines on every healthy idle cycle.
# If the entire tail consists only of these, replace with a summary so the
# AI doesn't misinterpret normal idle output as a problem.
_STALL_IDLE_TOKENS = frozenset(
    [
        "=== stall_recovery run ===",
        "stalls_found=0",
        "no_stalls_detected",
        "=== stall_recovery done ===",
    ]
)


def _tail_stall_recovery(path: Path, n: int) -> str:
    raw = _tail_file(path, n)
    lines = raw.splitlines()
    # Strip timestamps (tab-delimited prefix) to check the content tokens
    content = [ln.split("\t", 1)[-1].strip() for ln in lines if ln.strip()]
    if content and all(tok in _STALL_IDLE_TOKENS for tok in content):
        runs = sum(1 for tok in content if tok == "=== stall_recovery run ===")
        return f"(healthy: {runs} stall-check cycle(s) completed, no stalls detected)"
    return raw


def _count_dlq_lines() -> int:
    try:
        if not _DLQ_FILE.exists():
            return 0
        return sum(1 for _ in _DLQ_FILE.open(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def _tail_jsonl(path: Path, n: int) -> list[dict]:
    try:
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        rows = []
        for line in lines[-n:]:
            with contextlib.suppress(ValueError):
                rows.append(json.loads(line))
        return rows
    except OSError:
        return []


# ── OAuth credential probe ────────────────────────────────────────────────────


def _check_oauth_credentials() -> str | None:
    """Return an alert string if Claude OAuth credentials look missing, else None."""
    if not _CLAUDE_DIR.exists():
        return f"Claude credential directory missing: {_CLAUDE_DIR}"
    for subdir in _OAUTH_SIGNAL_DIRS:
        target = _CLAUDE_DIR / subdir
        if target.exists():
            try:
                if any(target.iterdir()):
                    return None  # at least one signal dir has files — looks fine
            except OSError:
                pass
    return (
        f"Claude OAuth credentials may be missing or cleared. "
        f"No files found in {_CLAUDE_DIR}/sessions or session-env. "
        f"Worker dispatches using OAuth will fail silently."
    )


def _should_run_oauth_check(state: dict) -> bool:
    last = state.get("last_oauth_check_utc")
    if not last:
        return True
    try:
        last_dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
        hours_since = (datetime.datetime.now(datetime.UTC) - last_dt).total_seconds() / 3600
        return hours_since >= _OAUTH_CHECK_INTERVAL_H
    except (ValueError, OverflowError):
        return True


# ── AI analysis ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a system health monitor for Project Miru. "
    "Respond with exactly ALL CLEAR unless you see clear evidence of a problem.\n\n"
    "Alert ONLY on these specific signals:\n"
    "- A service explicitly listed as 'down' in the SERVICES section\n"
    "- A log line containing: ERROR, FATAL, crash, panic, unhandled exception, "
    "segfault, OOM, or similar hard failure keywords\n"
    "- A DLQ new-entry count above 0\n\n"
    "Do NOT alert on:\n"
    "- Services listed as 'up'\n"
    "- Log lines showing normal cycles: 'ok', 'all_clear', 'healthy', 'poll', 'done'\n"
    "- Placeholder text like '(no recent errors)', '(file not found)', '(empty)'\n"
    "- Worker activity or completion entries\n\n"
    "When in doubt, respond ALL CLEAR. "
    "If you must alert, start with ALERT: followed by one specific sentence."
)


def _build_context(
    services: dict[str, str],
    log_tails: dict[str, str],
    dlq_new: int,
    heartbeats: list[dict],
    completions: list[dict],
) -> str:
    parts = ["=== SERVICES ==="]
    for svc, status in services.items():
        parts.append(f"  {svc}: {status}")

    parts.append("\n=== DLQ ===")
    parts.append(f"  New entries since last check: {dlq_new}")

    parts.append("\n=== RECENT WORKER ACTIVITY ===")
    if heartbeats:
        for h in heartbeats[-5:]:
            parts.append(
                f"  heartbeat worker={h.get('worker_id')} ticket={h.get('ticket_id')} "
                f"step={h.get('step')} stall={h.get('stall_signal')}"
            )
    else:
        parts.append("  (no recent heartbeats)")

    if completions:
        for c in completions[-3:]:
            parts.append(
                f"  completion ticket={c.get('ticket_id')} status={c.get('status')} "
                f"summary={str(c.get('summary',''))[:80]}"
            )
    else:
        parts.append("  (no recent completions)")

    parts.append("\n=== LOG TAILS ===")
    for log_name, tail in log_tails.items():
        parts.append(f"--- {log_name} (last {_LOG_TAIL_LINES} lines) ---")
        parts.append(tail)

    return "\n".join(parts)


def _ask_ollama(context: str) -> str | None:
    # Model preference order for health analysis: fast general models first,
    # coder models reserved for code review tasks.
    preferred = ["llama3.2:3b", "qwen2.5:7b", "llama3.2", "qwen2.5", "mistral", "phi"]

    try:
        r = urllib.request.urlopen(
            urllib.request.Request(
                "http://localhost:11434/api/tags",
                headers={"Content-Type": "application/json"},
            ),
            timeout=3,
        )
        tags = json.loads(r.read())
        models = [m["name"] for m in tags.get("models", [])]
        if not models:
            return None
        model = next(
            (pref for pref in preferred if any(m == pref or m.startswith(pref) for m in models)),
            None,
        ) or next((m for m in models if "coder" not in m), models[0])
        payload = json.dumps(
            {
                "model": model,
                "prompt": f"{_SYSTEM_PROMPT}\n\n{context}",
                "stream": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        return str(result.get("response", "")).strip() or None
    except Exception as exc:
        _log(f"ollama_failed: {exc}")
        return None


def _ask_openai(context: str, api_key: str) -> str | None:
    try:
        payload = json.dumps(
            {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                "max_tokens": 150,
                "temperature": 0,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        conn = http.client.HTTPSConnection("api.openai.com", timeout=20)
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        resp = conn.getresponse()
        body = json.loads(resp.read())
        conn.close()
        return str(body["choices"][0]["message"]["content"]).strip() or None
    except Exception as exc:
        _log(f"openai_failed: {exc}")
        return None


def _ask_ai(context: str, openai_key: str) -> str:
    answer = _ask_ollama(context)
    if answer:
        _log("ai_source=ollama")
        return answer
    if openai_key:
        answer = _ask_openai(context, openai_key)
        if answer:
            _log("ai_source=openai")
            return answer
    _log("ai_source=none — no AI available")
    return ""


# ── Telegram ──────────────────────────────────────────────────────────────────


def _send_telegram(token: str, chat_id: str, msg: str) -> bool:
    try:
        payload = json.dumps(
            {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            separators=(",", ":"),
        ).encode("utf-8")
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=10)
        conn.request(
            "POST",
            f"/bot{token}/sendMessage",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        conn.getresponse().read()
        conn.close()
        return True
    except Exception as exc:
        _log(f"telegram_failed: {exc}")
        return False


# ── Pushover ──────────────────────────────────────────────────────────────────


def _send_pushover(api_token: str, user_key: str, msg: str, title: str = "Miru Sentinel") -> bool:
    try:
        body = urllib.parse.urlencode(
            {"token": api_token, "user": user_key, "title": title, "message": msg}
        ).encode("utf-8")
        conn = http.client.HTTPSConnection("api.pushover.net", timeout=10)
        conn.request(
            "POST",
            "/1/messages.json",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        conn.getresponse().read()
        conn.close()
        return True
    except Exception as exc:
        _log(f"pushover_failed: {exc}")
        return False


def _alert(
    msg: str,
    tg_token: str,
    tg_chat: str,
    po_token: str,
    po_user: str,
    po_enabled: bool,
) -> None:
    """Send via Telegram; fall back to Pushover if Telegram fails or is unconfigured."""
    tg_ok = False
    if tg_token and tg_chat:
        tg_ok = _send_telegram(tg_token, tg_chat, msg)
    if not tg_ok and po_enabled and po_token and po_user:
        plain = (
            msg.replace("<b>", "")
            .replace("</b>", "")
            .replace("<i>", "")
            .replace("</i>", "")
            .replace("<code>", "")
            .replace("</code>", "")
        )
        _send_pushover(po_token, po_user, plain)
        _log("pushover_fallback_used")


# ── Snooze (Telegram command polling) ────────────────────────────────────────


def _parse_duration_minutes(s: str) -> int | None:
    """Parse '30m' or '2h' into minutes. Returns None if unparseable."""
    s = s.strip().lower()
    if s.endswith("h"):
        try:
            return int(s[:-1]) * 60
        except ValueError:
            return None
    if s.endswith("m"):
        try:
            return int(s[:-1])
        except ValueError:
            return None
    return None


def _poll_telegram_commands(token: str, chat_id: str, state: dict) -> dict:
    """Poll getUpdates and process /snooze and /unsnooze from the known chat."""
    if not token or not chat_id:
        return state
    offset = int(state.get("telegram_update_offset", 0))
    try:
        url = (
            f"https://api.telegram.org/bot{token}/getUpdates"
            f"?offset={offset}&limit=100&timeout=0"
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        _log(f"telegram_getUpdates_failed: {exc}")
        return state
    if not data.get("ok"):
        return state
    for update in data.get("result", []):
        state["telegram_update_offset"] = update["update_id"] + 1
        msg = update.get("message") or update.get("channel_post")
        if not msg:
            continue
        if str(msg.get("chat", {}).get("id", "")) != str(chat_id):
            continue  # ignore messages from other chats
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        parts = text.split()
        cmd = parts[0].lower().lstrip("/").split("@")[0]  # strip bot@username suffix
        if cmd == "snooze":
            duration_str = parts[1] if len(parts) > 1 else ""
            minutes = _parse_duration_minutes(duration_str)
            if minutes:
                until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=minutes)
                state["snooze_until_utc"] = until.strftime("%Y-%m-%dT%H:%M:%SZ")
                _log(f"snooze_set: until={state['snooze_until_utc']}")
                _send_telegram(
                    token,
                    chat_id,
                    f"🔕 Sentinel snoozed for {duration_str} (until {until.strftime('%H:%M UTC')}). "
                    f"Send /unsnooze to re-enable early.",
                )
            else:
                _send_telegram(token, chat_id, "Usage: /snooze 30m  or  /snooze 2h")
        elif cmd == "unsnooze":
            state.pop("snooze_until_utc", None)
            _log("snooze_cleared")
            _send_telegram(token, chat_id, "🔔 Sentinel alerts re-enabled.")
    return state


def _is_snoozed(state: dict) -> bool:
    until_str = state.get("snooze_until_utc")
    if not until_str:
        return False
    try:
        until = datetime.datetime.fromisoformat(until_str.replace("Z", "+00:00"))
        return datetime.datetime.now(datetime.UTC) < until
    except (ValueError, OverflowError):
        return False


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    _log("=== sentinel run ===")
    env = _load_env()
    tg_token = env.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = env.get("TELEGRAM_CHAT_ID", "")
    openai_key = env.get("OPENAI_API_KEY", "")
    po_token = env.get("PUSHOVER_API_TOKEN", "")
    po_user = env.get("PUSHOVER_USER_KEY", "")
    po_enabled = env.get("PUSHOVER_ENABLED", "false").lower() in {"true", "1", "yes"}

    state = _read_state()
    # Note: Telegram getUpdates polling conflicts with the n8n webhook on the same
    # bot token (HTTP 409). Snooze is set via sentinel_state.json — see PRO-248.
    dlq_count_prev = int(state.get("dlq_count", 0))

    services = _check_services()
    down_services = [s for s, st in services.items() if not st.startswith("up")]

    log_tails = {}
    for name, path in _WATCH_LOGS.items():
        if name == "stall_recovery":
            pass  # handled as a programmatic hard alert below; excluded from AI context
        elif name == "dispatch_listener_stderr" and services.get(
            "dispatch_listener", ""
        ).startswith("up"):
            # If the listener is up and the log still contains EADDRINUSE/stack-trace
            # content, the entire visible tail is a pre-fix crash-loop artifact.
            # Replace it entirely so the AI doesn't alert on historical noise.
            raw = _tail_file(path, _LOG_TAIL_LINES)
            if "EADDRINUSE" in raw or "Unhandled 'error' event" in raw:
                log_tails[name] = (
                    "(no recent errors — prior startup-race artifact; service is healthy)"
                )
            else:
                log_tails[name] = raw if raw.strip() else "(no recent errors)"
        else:
            log_tails[name] = _tail_file(path, _LOG_TAIL_LINES)

    dlq_count_now = _count_dlq_lines()
    dlq_new = max(0, dlq_count_now - dlq_count_prev)

    heartbeats = _tail_jsonl(_HEARTBEAT_LOG, 10)
    completions = _tail_jsonl(_COMPLETION_LOG, 5)

    _log(
        f"services_down={len(down_services)} dlq_new={dlq_new} "
        f"heartbeats={len(heartbeats)} completions={len(completions)}"
    )

    # Hard facts that always trigger an alert regardless of AI
    hard_alerts: list[str] = []
    if down_services:
        hard_alerts.append(f"Services down: {', '.join(down_services)}")
    if dlq_new >= 3:
        hard_alerts.append(f"{dlq_new} new failed dispatches added to the queue")

    # Programmatic stall-recovery check — excluded from AI context because small
    # models misread "stalls_found=0 / no stalls detected" as a problem.
    import re as _re

    stall_log_path = _WATCH_LOGS.get("stall_recovery")
    if stall_log_path:
        stall_raw = _tail_file(stall_log_path, _LOG_TAIL_LINES)
        stall_counts = [int(m) for m in _re.findall(r"stalls_found=(\d+)", stall_raw) if int(m) > 0]
        if stall_counts:
            hard_alerts.append(f"Stall recovery detected active stalls: counts={stall_counts}")
            _log(f"stall_hard_alert: counts={stall_counts}")

    # Daily OAuth credential probe
    if _should_run_oauth_check(state):
        oauth_problem = _check_oauth_credentials()
        if oauth_problem:
            hard_alerts.append(oauth_problem)
            _log(f"oauth_probe_failed: {oauth_problem}")
        else:
            _log("oauth_probe_ok")
        state["last_oauth_check_utc"] = datetime.datetime.now(datetime.UTC).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    context = _build_context(services, log_tails, dlq_new, heartbeats, completions)
    ai_response = _ask_ai(context, openai_key)

    _log(f"ai_response={ai_response[:120] if ai_response else '(none)'}")

    # Decide whether to alert
    ai_flagged = ai_response and ai_response.upper().startswith("ALERT")
    should_alert = bool(hard_alerts) or ai_flagged

    if should_alert:
        if _is_snoozed(state):
            _log(f"alert_suppressed: snoozed until {state.get('snooze_until_utc')}")
        else:
            lines = ["🔍 <b>Miru Sentinel</b>"]
            if hard_alerts:
                for a in hard_alerts:
                    lines.append(f"⚠️ {a}")
            if ai_flagged:
                lines.append(f"🤖 {ai_response}")
            lines.append("<i>Reply /snooze 2h to silence for a while</i>")
            _alert("\n".join(lines), tg_token, tg_chat, po_token, po_user, po_enabled)
            _log("alert_sent")
    else:
        _log("all_clear")

    state["dlq_count"] = dlq_count_now
    state["last_run_utc"] = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(state)
    _log("=== sentinel done ===")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_REPO_ROOT))
    main()
