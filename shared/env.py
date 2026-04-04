from __future__ import annotations

import os
from pathlib import Path
from typing import Any, MutableMapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"
PUSHOVER_REQUIRED_KEYS = (
    "PUSHOVER_USER_KEY",
    "PUSHOVER_APP_TOKEN",
)
PUSHOVER_OPTIONAL_KEYS = (
    "PUSHOVER_ENABLED",
    "PUSHOVER_DEFAULT_PRIORITY",
)


def _parse_dotenv_text(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        parsed[key] = value
    return parsed


def load_project_env(
    *,
    env_path: str | Path = DEFAULT_ENV_PATH,
    environ: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> dict[str, Any]:
    target_env = environ if environ is not None else os.environ
    path = Path(env_path)
    result = {
        "env_path": str(path),
        "exists": path.is_file(),
        "parser": "missing",
        "loaded_keys": [],
        "skipped_existing_keys": [],
        "available_keys": [],
        "override": bool(override),
    }
    if not path.is_file():
        return result

    parsed: dict[str, str]
    try:
        from dotenv import dotenv_values  # type: ignore

        raw_values = dotenv_values(path)
        parsed = {
            str(key): str(value)
            for key, value in raw_values.items()
            if key is not None and value is not None
        }
        result["parser"] = "python-dotenv"
    except Exception:
        parsed = _parse_dotenv_text(path.read_text(encoding="utf-8"))
        result["parser"] = "builtin"

    loaded_keys: list[str] = []
    skipped_existing_keys: list[str] = []
    for key, value in parsed.items():
        current = str(target_env.get(key, "") or "")
        if override or not current.strip():
            target_env[key] = value
            loaded_keys.append(key)
        elif str(value).strip():
            skipped_existing_keys.append(key)

    result["loaded_keys"] = loaded_keys
    result["skipped_existing_keys"] = skipped_existing_keys
    result["available_keys"] = sorted(key for key, value in parsed.items() if str(value).strip())
    return result


def inspect_pushover_env(
    *,
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, Any]:
    target_env = environ if environ is not None else os.environ
    enabled_text = str(target_env.get("PUSHOVER_ENABLED", "") or "").strip().lower()
    enabled = enabled_text in {"1", "true", "yes", "on"}
    missing_required = [
        key
        for key in PUSHOVER_REQUIRED_KEYS
        if not str(target_env.get(key, "") or "").strip()
    ]
    configured = enabled and not missing_required
    available_keys = [
        key
        for key in (*PUSHOVER_REQUIRED_KEYS, *PUSHOVER_OPTIONAL_KEYS)
        if str(target_env.get(key, "") or "").strip()
    ]
    return {
        "enabled": enabled,
        "configured": configured,
        "missing_required_keys": missing_required,
        "available_keys": available_keys,
        "default_priority": str(target_env.get("PUSHOVER_DEFAULT_PRIORITY", "") or "").strip(),
    }


def build_pushover_status_message(
    *,
    env_load: dict[str, Any] | None = None,
    pushover: dict[str, Any] | None = None,
) -> str:
    load_info = env_load or {}
    push_info = pushover or inspect_pushover_env()
    env_path = str(load_info.get("env_path") or DEFAULT_ENV_PATH)
    if not load_info.get("exists", False):
        return f"Pushover env file not found at {env_path}. Notifications will stay unavailable until a local .env is added."
    if not push_info.get("enabled", False):
        return f"Pushover notifications are disabled. Loaded local env from {env_path}."
    missing = list(push_info.get("missing_required_keys") or [])
    if missing:
        return (
            f"Pushover is enabled but missing required keys: {', '.join(missing)}. "
            f"Loaded local env from {env_path}."
        )
    return f"Pushover credentials loaded from {env_path} and notifications are enabled."
