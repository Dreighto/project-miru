from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Lock
from typing import Any, MutableMapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from tools.miru_env import PROJECT_ROOT, inspect_pushover_env


PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"
OPERATOR_NOTIFICATION_STATE_PATH = PROJECT_ROOT / "data" / "miru_operator_notifications.json"
OPERATOR_NOTIFICATION_LOCK = Lock()
DEFAULT_OPERATOR_EVENT_COOLDOWNS = {
    "learning_worker_started": 60,
    "learning_worker_stopped": 60,
    "learning_worker_restarted": 30,
    "miru_ai_restarted": 30,
    "learning_milestone": 1800,
    "critical_learning_failure": 300,
    "test": 0,
}


def send_pushover_notification(
    *,
    title: str,
    message: str,
    priority: int | None = None,
    timeout: float = 10.0,
    environ: MutableMapping[str, str] | None = None,
    logger: Any | None = None,
) -> dict[str, Any]:
    target_env = environ if environ is not None else os.environ
    status = inspect_pushover_env(environ=target_env)
    result: dict[str, Any] = {
        "ok": False,
        "enabled": status["enabled"],
        "configured": status["configured"],
        "missing_required_keys": list(status["missing_required_keys"]),
        "endpoint": PUSHOVER_API_URL,
        "status_code": None,
        "response_json": None,
        "response_text": "",
        "error": "",
    }

    if not status["enabled"]:
        result["error"] = "Pushover notifications are disabled."
        return result

    if not status["configured"]:
        result["error"] = (
            "Pushover is enabled but missing required keys: "
            + ", ".join(result["missing_required_keys"])
        )
        return result

    priority_value = priority
    if priority_value is None:
        default_priority = str(target_env.get("PUSHOVER_DEFAULT_PRIORITY", "") or "").strip()
        try:
            priority_value = int(default_priority) if default_priority else 0
        except ValueError:
            priority_value = 0

    payload = {
        "token": str(target_env.get("PUSHOVER_APP_TOKEN", "") or "").strip(),
        "user": str(target_env.get("PUSHOVER_USER_KEY", "") or "").strip(),
        "title": title.strip() or "Miru AI",
        "message": message.strip() or "Miru AI notification",
        "priority": str(priority_value),
    }
    encoded_payload = urlencode(payload).encode("utf-8")
    request = Request(
        PUSHOVER_API_URL,
        data=encoded_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    if logger is not None:
        logger.info(
            "Pushover send attempt: title=%r priority=%s endpoint=%s",
            payload["title"],
            payload["priority"],
            PUSHOVER_API_URL,
        )

    try:
        with urlopen(request, timeout=timeout) as response:
            body_bytes = response.read()
            body_text = body_bytes.decode("utf-8", "replace")
            result["status_code"] = getattr(response, "status", None) or response.getcode()
            result["response_text"] = body_text
            try:
                result["response_json"] = json.loads(body_text)
            except json.JSONDecodeError:
                result["response_json"] = None
            result["ok"] = bool(
                result["status_code"] == 200
                and isinstance(result["response_json"], dict)
                and result["response_json"].get("status") == 1
            )
    except HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace")
        result["status_code"] = exc.code
        result["response_text"] = body_text
        result["error"] = f"HTTPError: {exc.code} {exc.reason}"
        try:
            result["response_json"] = json.loads(body_text)
        except json.JSONDecodeError:
            result["response_json"] = None
    except URLError as exc:
        result["error"] = f"URLError: {exc.reason}"
    except Exception as exc:  # pragma: no cover - defensive fallback
        result["error"] = f"{exc.__class__.__name__}: {exc}"

    if logger is not None:
        if result["ok"]:
            logger.info(
                "Pushover send succeeded: status_code=%s response=%s",
                result["status_code"],
                result["response_json"] if result["response_json"] is not None else result["response_text"][:300],
            )
        else:
            logger.warning(
                "Pushover send failed: status_code=%s error=%s response=%s",
                result["status_code"],
                result["error"],
                result["response_json"] if result["response_json"] is not None else result["response_text"][:300],
            )

    return result


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


def send_operator_notification(
    event_type: str,
    message: str,
    *,
    title: str = "Miru Operator",
    priority: int | None = None,
    cooldown_seconds: int | None = None,
    timeout: float = 10.0,
    environ: MutableMapping[str, str] | None = None,
    logger: Any | None = None,
    state_path: Path = OPERATOR_NOTIFICATION_STATE_PATH,
) -> dict[str, Any]:
    event_key = str(event_type or "unknown").strip().lower().replace(" ", "_")
    cooldown = int(
        cooldown_seconds
        if cooldown_seconds is not None
        else DEFAULT_OPERATOR_EVENT_COOLDOWNS.get(event_key, 300)
    )
    now = int(time.time())

    with OPERATOR_NOTIFICATION_LOCK:
        state = _load_operator_state(state_path)
        notifications = state.get("notifications")
        if not isinstance(notifications, dict):
            notifications = {}
        last_sent = notifications.get(event_key)
        if isinstance(last_sent, dict):
            last_sent_at = int(last_sent.get("sent_at") or 0)
        else:
            last_sent_at = 0

        age_seconds = now - last_sent_at if last_sent_at > 0 else None
        if cooldown > 0 and age_seconds is not None and age_seconds < cooldown:
            return {
                "ok": False,
                "suppressed": True,
                "event_type": event_key,
                "cooldown_seconds": cooldown,
                "age_seconds": age_seconds,
                "error": f"Suppressed duplicate operator notification for {event_key}.",
            }

        result = send_pushover_notification(
            title=title.strip() or "Miru Operator",
            message=message,
            priority=priority,
            timeout=timeout,
            environ=environ,
            logger=logger,
        )
        result["suppressed"] = False
        result["event_type"] = event_key
        result["cooldown_seconds"] = cooldown

        if result.get("ok"):
            notifications[event_key] = {
                "sent_at": now,
                "message_preview": str(message or "")[:200],
            }
            state["notifications"] = notifications
            try:
                _save_operator_state(state_path, state)
            except OSError:
                if logger is not None:
                    logger.warning("Unable to persist operator notification state for %s.", event_key)
        return result
