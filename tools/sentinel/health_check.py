"""
MiruSentinel — periodic system health scanner.

Runs every 20 minutes as the MiruSentinel scheduled task.
Collects service health, log tails, DLQ delta, and recent worker activity,
then asks an AI (Ollama first, OpenAI fallback) if anything looks wrong.
Sends a Telegram alert only when something needs attention — silent otherwise.

Entry point: python tools/sentinel/health_check.py
"""

from __future__ import annotations

import contextlib
import datetime
import http.client
import json
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

_HEALTH_ENDPOINTS = {
    "gateway": "http://127.0.0.1:18766/health",
    "dispatch_listener": "http://127.0.0.1:19100/health",
    "pm": "http://127.0.0.1:18080/health",
    "miru_ai": "http://127.0.0.1:18765/health",
}

_WATCH_LOGS = {
    "dispatch_listener_stderr": _REPO_ROOT / "logs" / "dispatch_listener_stderr.log",
    "service_watchdog": _REPO_ROOT / "logs" / "service_watchdog.log",
    "stall_recovery": _REPO_ROOT / "logs" / "stall_recovery.log",
}


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


# ── AI analysis ───────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a system health monitor for Project Miru, an autonomous AI worker dispatch system. "
    "Review the snapshot below and flag anything that looks wrong or unusual. "
    "Be specific and brief. If everything looks normal, respond with exactly: ALL CLEAR\n"
    "If something needs attention, start your response with ALERT: followed by 1-2 sentences."
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


# ── Telegram ─────────────────────────────────────────────────────────────────


def _send_telegram(token: str, chat_id: str, msg: str) -> None:
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
    except Exception as exc:
        _log(f"telegram_failed: {exc}")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    _log("=== sentinel run ===")
    env = _load_env()
    tg_token = env.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = env.get("TELEGRAM_CHAT_ID", "")
    openai_key = env.get("OPENAI_API_KEY", "")

    state = _read_state()
    dlq_count_prev = int(state.get("dlq_count", 0))

    services = _check_services()
    down_services = [s for s, st in services.items() if not st.startswith("up")]

    log_tails = {name: _tail_file(path, _LOG_TAIL_LINES) for name, path in _WATCH_LOGS.items()}

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

    context = _build_context(services, log_tails, dlq_new, heartbeats, completions)
    ai_response = _ask_ai(context, openai_key)

    _log(f"ai_response={ai_response[:120] if ai_response else '(none)'}")

    # Decide whether to alert
    ai_flagged = ai_response and ai_response.upper().startswith("ALERT")
    should_alert = bool(hard_alerts) or ai_flagged

    if should_alert and tg_token and tg_chat:
        lines = ["🔍 <b>Miru Sentinel</b>"]
        if hard_alerts:
            for a in hard_alerts:
                lines.append(f"⚠️ {a}")
        if ai_flagged:
            lines.append(f"🤖 {ai_response}")
        _send_telegram(tg_token, tg_chat, "\n".join(lines))
        _log("telegram_alert_sent")
    elif not should_alert:
        _log("all_clear")

    state["dlq_count"] = dlq_count_now
    state["last_run_utc"] = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_state(state)
    _log("=== sentinel done ===")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(_REPO_ROOT))
    main()
