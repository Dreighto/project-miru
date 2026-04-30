"""Telegram direct send tool — decouples notifications from n8n (PRO-227)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import audit as gw_audit  # noqa: E402
from miru_mcp_gateway import redact as _redact  # noqa: E402

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore

_TELEGRAM_API = "https://api.telegram.org"
_HTTP_TIMEOUT_S = 10
_MESSAGE_MAX_CHARS = 4000
_VALID_PARSE_MODES = frozenset({"Markdown", "MarkdownV2", "HTML"})

_CFG: Any = None


def _cfg() -> Any:
    if _CFG is None:
        raise RuntimeError("telegram_tools not configured")
    return _CFG


def _reject_if_secrets(text: str) -> None:
    hits = _redact.find_named_secret_substrings(text)
    if hits:
        raise stdio_mcp.McpError(
            f"telegram: content contains known secret substring: {hits[0]}", -32000
        )


def _audit_telegram(
    *,
    caller: str,
    chat_id: str,
    text_length: int,
    parse_mode: str,
    result: str,
    error: str | None,
) -> None:
    writes_log, _, _ = gw_audit.default_audit_paths(_cfg().fs_root)
    row = {
        "ts": gw_audit._utc_iso(),
        "tool": "telegram_send_message",
        "category": "telegram",
        "caller": caller,
        "chat_id": chat_id,
        "text_length": text_length,
        "parse_mode": parse_mode,
        "result": result,
        "error": error,
    }
    gw_audit.append_jsonl_chained(writes_log, row)


def telegram_send_message(
    text: str,
    chat_id: str | None = None,
    parse_mode: str = "Markdown",
    ctx: Any = None,
) -> str:
    """Send a Telegram message directly via the Bot API (bypasses n8n).

    ``text``: message body — plain text or Markdown/HTML depending on ``parse_mode``.
    ``chat_id``: numeric chat ID or @username. Defaults to TELEGRAM_CHAT_ID from config.
    ``parse_mode``: "Markdown" (default), "MarkdownV2", or "HTML".

    Messages longer than 4000 chars are truncated with a trailing notice.
    """
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    cfg = _cfg()

    if not text or not text.strip():
        raise stdio_mcp.McpError("telegram: text is required", -32602)

    resolved_chat_id = (chat_id or "").strip() or getattr(cfg, "telegram_default_chat_id", None)
    if not resolved_chat_id:
        raise stdio_mcp.McpError(
            "telegram: chat_id required (or set TELEGRAM_CHAT_ID in .env)", -32602
        )

    if parse_mode not in _VALID_PARSE_MODES:
        raise stdio_mcp.McpError(
            f"telegram: parse_mode must be one of {sorted(_VALID_PARSE_MODES)}", -32602
        )

    # Truncate if over Telegram's limit
    if len(text) > _MESSAGE_MAX_CHARS:
        text = text[: _MESSAGE_MAX_CHARS - 40] + "\n\n…[message truncated by gateway]"

    _reject_if_secrets(text)

    token = getattr(cfg, "telegram_bot_token", None)
    if not token:
        raise stdio_mcp.McpError("telegram: TELEGRAM_BOT_TOKEN not configured", -32000)

    if requests is None:
        raise stdio_mcp.McpError(
            "telegram: 'requests' library not installed; pip install requests", -32000
        )

    url = f"{_TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": resolved_chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }

    try:
        resp = requests.post(url, json=payload, timeout=_HTTP_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        err = f"telegram: timeout after {_HTTP_TIMEOUT_S}s"
        _audit_telegram(
            caller=caller,
            chat_id=resolved_chat_id,
            text_length=len(text),
            parse_mode=parse_mode,
            result="failure",
            error=err,
        )
        raise stdio_mcp.McpError(err, -32000) from exc
    except requests.exceptions.RequestException as exc:
        err = f"telegram: transport error: {_redact.redact(str(exc))}"
        _audit_telegram(
            caller=caller,
            chat_id=resolved_chat_id,
            text_length=len(text),
            parse_mode=parse_mode,
            result="failure",
            error=err,
        )
        raise stdio_mcp.McpError(err, -32000) from exc

    if resp.status_code == 401:
        err = "telegram: 401 Unauthorized — TELEGRAM_BOT_TOKEN may be invalid"
        _audit_telegram(
            caller=caller,
            chat_id=resolved_chat_id,
            text_length=len(text),
            parse_mode=parse_mode,
            result="failure",
            error=err,
        )
        raise stdio_mcp.McpError(err, -32000)

    if not (200 <= resp.status_code < 300):
        try:
            body = resp.json()
            description = body.get("description", resp.text[:200])
        except ValueError:
            description = resp.text[:200]
        err = f"telegram: HTTP {resp.status_code}: {_redact.redact(str(description))}"
        _audit_telegram(
            caller=caller,
            chat_id=resolved_chat_id,
            text_length=len(text),
            parse_mode=parse_mode,
            result="failure",
            error=err,
        )
        raise stdio_mcp.McpError(err, -32000)

    try:
        body = resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError("telegram: non-JSON response", -32000) from exc

    msg = body.get("result") or {}
    out = {
        "ok": True,
        "message_id": msg.get("message_id"),
        "chat_id": resolved_chat_id,
        "date": msg.get("date"),
        "text_length": len(text),
    }
    _audit_telegram(
        caller=caller,
        chat_id=resolved_chat_id,
        text_length=len(text),
        parse_mode=parse_mode,
        result="success",
        error=None,
    )
    return json.dumps(out, indent=2)


TOOL_FUNCTIONS = (telegram_send_message,)


def register(mcp, cfg) -> int:
    """Register telegram_send_message iff TELEGRAM_BOT_TOKEN is present."""
    global _CFG
    if not getattr(cfg, "telegram_bot_token", None):
        cfg.disabled_categories["telegram"] = "TELEGRAM_BOT_TOKEN missing"
        return 0
    if requests is None:
        cfg.disabled_categories["telegram"] = "'requests' library not installed"
        return 0
    _CFG = cfg
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
