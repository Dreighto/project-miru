"""
Recovery router — processes StallEvents and auto-recovers or escalates.

Budget: MAX_AUTO_RETRIES per (worker_id, ticket_id) pair.
  - Budget remaining + listener reachable → re-dispatch a recovery prompt
  - Budget exhausted or dispatch fails → write data/dispatch_dlq.jsonl + Telegram alert

State: logs/stall_recovery_state.json
  Tracks retry_count, first_stall_utc, last_retry_utc per worker::ticket key.
  Entries are cleared when a ticket appears in the completion log (budget resets).

Entry point: python -m tools.orchestrator.recovery_router
  (or called by the MiruStallRecovery scheduled task via startup_all.ps1)
"""

from __future__ import annotations

import datetime
import hashlib
import hmac as _hmac
import http.client
import json
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_STATE_FILE = _REPO_ROOT / "logs" / "stall_recovery_state.json"
_DLQ_FILE = _REPO_ROOT / "data" / "dispatch_dlq.jsonl"
_LOG_FILE = _REPO_ROOT / "logs" / "stall_recovery.log"

MAX_AUTO_RETRIES = 1  # auto-retries per (worker_id, ticket_id)
MIN_RETRY_GAP_S = 300  # minimum seconds between retries for the same key
_HTTP_TIMEOUT_S = 10


# ── Logging ───────────────────────────────────────────────────────────────────


def _log(msg: str) -> None:
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    line = f"{ts}\t{msg}"
    try:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    print(f"[stall_recovery] {msg}")


# ── .env loader ───────────────────────────────────────────────────────────────


def _load_env() -> dict[str, str]:
    env_path = _REPO_ROOT / ".env"
    env: dict[str, str] = {}
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in {'"', "'"}:
            v = v[1:-1]
        if k:
            env[k] = v
    return env


# ── State management ─────────────────────────────────────────────────────────


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


def _clean_completed_keys(state: dict, completed_tickets: set[str]) -> dict:
    """Remove state entries whose ticket has completed — resets budget for future runs."""
    return {k: v for k, v in state.items() if not any(tid in k for tid in completed_tickets)}


# ── Telegram ─────────────────────────────────────────────────────────────────


def _send_telegram(token: str, chat_id: str, msg: str) -> None:
    try:
        body = json.dumps(
            {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            separators=(",", ":"),
        ).encode("utf-8")
        conn = http.client.HTTPSConnection("api.telegram.org", timeout=_HTTP_TIMEOUT_S)
        conn.request(
            "POST",
            f"/bot{token}/sendMessage",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        conn.getresponse().read()
        conn.close()
    except Exception as exc:
        _log(f"telegram_send_failed: {exc}")


# ── DLQ append ───────────────────────────────────────────────────────────────


def _append_dlq(stall, reason: str, recovery_trace_id: str | None = None) -> None:
    row = {
        "ts": datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "worker_id": stall.worker_id,
        "ticket_id": stall.ticket_id,
        "step": stall.step,
        "branch": stall.branch,
        "last_heartbeat_ts": stall.last_heartbeat_ts,
        "stall_age_seconds": int(stall.stall_age_seconds),
        "reason": reason,
        # Lineage: the original dispatch that stalled, plus (when present) the
        # recovery dispatch that also failed. Lets the DLQ watcher tell humans
        # whether this is a first-attempt failure or an exhausted retry chain.
        "parent_trace_id": getattr(stall, "trace_id", None),
        "recovery_trace_id": recovery_trace_id,
    }
    line = json.dumps(row, separators=(",", ":"))
    try:
        with open(_DLQ_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        _log(f"dlq_write_failed: {exc}")


# ── Dispatch ─────────────────────────────────────────────────────────────────


def _compute_hmac(secret: str, body: bytes) -> str:
    return _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _build_recovery_prompt(stall, trace_id: str) -> str:
    tid = stall.ticket_id or "unknown"
    return (
        f"═══════════════════════════════════════════════════════\n"
        f"MIRU WORKER RECOVERY DISPATCH — {tid}\n"
        f"═══════════════════════════════════════════════════════\n"
        f"\n"
        f"TICKET: {tid}\n"
        f"WORKER: {stall.worker_id}\n"
        f"TRACE_ID: {trace_id}\n"
        f"WORKTREE: cut a new branch from origin/main\n"
        f"\n"
        f"FIRST ACTION — emit session-start heartbeat before reading any files:\n"
        f"    python tools/emit_heartbeat.py --worker-id {stall.worker_id} "
        f"--ticket-id {tid} --step pre_flight --branch main\n"
        f"\n"
        f"---\n"
        f"\n"
        f"RECOVERY CONTEXT\n"
        f"This is an automatic re-dispatch. Worker {stall.worker_id} stalled at "
        f"step={stall.step or 'unknown'} on branch={stall.branch or 'unknown'}.\n"
        f"\n"
        f"TASK\n"
        f"Read Linear ticket {tid} for full requirements and acceptance criteria.\n"
        f"Check data/cc_completion_log.jsonl to see what completed in prior runs.\n"
        f"Check data/cc_heartbeat_log.jsonl to see where the previous run stalled.\n"
        f"Resume from the last confirmed checkpoint, or re-run from the beginning "
        f"if no checkpoint exists.\n"
        f"Follow docs/dispatch_contract.md — authority tiers, heartbeat protocol, "
        f"and completion contract all apply.\n"
        f"\n"
        f"═══════════════════════════════════════════════════════"
    )


def _dispatch_recovery(worker_type: str, stall, hmac_secret: str, listener_url: str) -> bool:
    """Write prompt file and POST to dispatch listener. Returns True on 202."""
    ts = int(time.time() * 1000) & 0xFFFFFFFF
    rnd = secrets.token_hex(4)
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in (stall.ticket_id or "unknown"))[
        :28
    ]
    trace_id = f"recovery-{slug}-{ts:08x}-{rnd}"

    inbox_dir = _REPO_ROOT / "data" / "n8n_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = inbox_dir / f"{trace_id}.prompt.json"
    prompt_file.write_text(
        json.dumps({"prompt": _build_recovery_prompt(stall, trace_id)}, ensure_ascii=False),
        encoding="utf-8",
    )

    body = json.dumps(
        {
            "trace_id": trace_id,
            "worker": worker_type,
            "prompt_path": f"data/n8n_inbox/{trace_id}.prompt.json",
            "timeout_seconds": 1200,
            "use_api_key": True,
        },
        separators=(",", ":"),
    ).encode("utf-8")

    sig = _compute_hmac(hmac_secret, body)

    try:
        parsed = urlparse(listener_url)
        conn = http.client.HTTPConnection(
            parsed.hostname or "127.0.0.1",
            parsed.port or 19100,
            timeout=_HTTP_TIMEOUT_S,
        )
        conn.request(
            "POST",
            "/dispatch",
            body=body,
            headers={"Content-Type": "application/json", "X-W4-Hmac": sig},
        )
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
        prompt_file.unlink(missing_ok=True)
        if status == 202:
            return True
        _log(f"dispatch_rejected status={status} worker={stall.worker_id}")
        return False
    except Exception as exc:
        _log(f"dispatch_failed worker={stall.worker_id}: {exc}")
        prompt_file.unlink(missing_ok=True)
        return False


# ── Main routing logic ────────────────────────────────────────────────────────


def route(stalls: list) -> None:
    if not stalls:
        _log("no_stalls_detected")
        return

    env = _load_env()
    hmac_secret = env.get("W4_LISTENER_HMAC_SECRET", "")
    listener_url = env.get("MIRU_DISPATCH_LISTENER_URL", "http://127.0.0.1:19100")
    tg_token = env.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = env.get("TELEGRAM_CHAT_ID", "")

    # Load completed tickets for budget cleanup
    from tools.orchestrator.stall_detector import (
        _COMPLETION_LOG,
        _COMPLETION_READ_LINES,
        _read_last_jsonl,
    )

    cp_rows = _read_last_jsonl(_COMPLETION_LOG, _COMPLETION_READ_LINES)
    completed_tickets: set[str] = set()
    for row in cp_rows:
        tid = str(row.get("ticket_id", "")).strip()
        if tid and tid.lower() != "null":
            completed_tickets.add(tid)

    state = _read_state()
    state = _clean_completed_keys(state, completed_tickets)

    now_utc = datetime.datetime.now(datetime.UTC)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    for stall in stalls:
        key = f"{stall.worker_id}::{stall.ticket_id or 'none'}"
        entry = state.get(
            key, {"retry_count": 0, "first_stall_utc": now_iso, "last_retry_utc": None}
        )
        retry_count = int(entry.get("retry_count", 0))
        last_retry = entry.get("last_retry_utc")

        _log(
            f"stall worker={stall.worker_id} ticket={stall.ticket_id} "
            f"step={stall.step} age_s={int(stall.stall_age_seconds)} retries={retry_count}"
        )

        # Minimum gap between retries (exponential backoff, first tier)
        if last_retry:
            try:
                last_retry_ts = datetime.datetime.fromisoformat(last_retry.replace("Z", "+00:00"))
                gap_s = (now_utc - last_retry_ts).total_seconds()
                if gap_s < MIN_RETRY_GAP_S:
                    _log(
                        f"backoff_skip worker={stall.worker_id} "
                        f"gap_s={int(gap_s)} min={MIN_RETRY_GAP_S}"
                    )
                    continue
            except (ValueError, OverflowError):
                pass

        if not entry.get("first_stall_utc"):
            entry["first_stall_utc"] = now_iso

        tid_str = stall.ticket_id or "unknown"
        # Strip trailing -N suffix to get the dispatch worker type
        worker_type = re.sub(r"-\d+$", "", stall.worker_id)

        # Already escalated this stall — skip until the ticket completes.
        if entry.get("escalated_utc"):
            _log(f"already_escalated worker={stall.worker_id} ticket={tid_str} -- skipping")
            state[key] = entry
            continue

        if retry_count < MAX_AUTO_RETRIES and hmac_secret:
            success = _dispatch_recovery(worker_type, stall, hmac_secret, listener_url)
            if success:
                entry["retry_count"] = retry_count + 1
                entry["last_retry_utc"] = now_iso
                state[key] = entry
                _log(
                    f"auto_redispatch_ok worker={stall.worker_id} ticket={tid_str} retry={retry_count + 1}"
                )
                if tg_token and tg_chat:
                    _send_telegram(
                        tg_token,
                        tg_chat,
                        f"🔄 <b>Worker auto-recovery</b>\n"
                        f"Worker <code>{stall.worker_id}</code> stalled on <b>{tid_str}</b> "
                        f"at step <code>{stall.step or 'unknown'}</code>.\n"
                        f"Auto-redispatched (retry {retry_count + 1}/{MAX_AUTO_RETRIES}).",
                    )
            else:
                _append_dlq(stall, "dispatch_failed")
                state[key] = entry
                _log(f"auto_redispatch_failed worker={stall.worker_id} ticket={tid_str}")
                if tg_token and tg_chat:
                    _send_telegram(
                        tg_token,
                        tg_chat,
                        f"⚠️ <b>Worker stalled — dispatch failed</b>\n"
                        f"Worker <code>{stall.worker_id}</code> stalled on <b>{tid_str}</b>.\n"
                        f"Auto-redispatch failed (listener down?). Added to DLQ.",
                    )
        else:
            reason = "budget_exhausted" if retry_count >= MAX_AUTO_RETRIES else "no_hmac_secret"
            _append_dlq(stall, reason)
            entry["escalated_utc"] = now_iso
            state[key] = entry
            _log(f"escalate worker={stall.worker_id} ticket={tid_str} reason={reason}")
            if tg_token and tg_chat:
                _send_telegram(
                    tg_token,
                    tg_chat,
                    f"🚨 <b>Worker stall — needs your input</b>\n"
                    f"Worker <code>{stall.worker_id}</code> stalled on <b>{tid_str}</b> "
                    f"at step <code>{stall.step or 'unknown'}</code>.\n"
                    f"Auto-retry budget exhausted ({retry_count}/{MAX_AUTO_RETRIES}). Added to DLQ.\n"
                    f"Last heartbeat: <code>{stall.last_heartbeat_ts}</code>",
                )

    _write_state(state)


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    from tools.orchestrator.stall_detector import detect_stalls

    _log("=== stall_recovery run ===")
    stalls = detect_stalls()
    _log(f"stalls_found={len(stalls)}")
    route(stalls)
    _log("=== stall_recovery done ===")


if __name__ == "__main__":
    # Allow running as: python tools/orchestrator/recovery_router.py
    sys.path.insert(0, str(_REPO_ROOT))
    main()
