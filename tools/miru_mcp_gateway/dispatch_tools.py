"""PRO-235: dispatch_worker — trigger CC workers via the dispatch listener.

Gated by MIRU_DISPATCH_ENABLED=1 + W4_LISTENER_HMAC_SECRET set.
Writes the prompt to data/n8n_inbox/<trace_id>.prompt.json, then POSTs to
the dispatch listener at http://127.0.0.1:19100/dispatch with HMAC-SHA256
auth (X-W4-Hmac header).  The listener reads the prompt file synchronously
before returning 202, so the file is cleaned up immediately after success.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import http.client
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import redact as _redact  # noqa: E402

_APPROVED_WORKERS = frozenset({"claude-code", "gemini", "codex"})
# "extended" is the semantic alias for --effort max; direct effort values also accepted.
_APPROVED_THINKING_LEVELS = frozenset({"extended", "none", "low", "medium", "high", "xhigh", "max"})
_DEFAULT_TIMEOUT_S = 600
_TIMEOUT_MIN = 1
_TIMEOUT_MAX = 1800
_HTTP_TIMEOUT_S = 15
_MAX_PROMPT_CHARS = 60_000

_CFG: Any = None


def _compute_hmac(secret: str, body: bytes) -> str:
    return _hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _generate_trace_id(ticket_id: str | None) -> str:
    ts = int(time.time() * 1000) & 0xFFFFFFFF
    rnd = secrets.token_hex(4)
    if ticket_id:
        slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in ticket_id)[:28]
        return f"cc-{slug}-{ts:08x}-{rnd}"
    return f"cc-{ts:08x}-{rnd}"


def _post_dispatch(base_url: str, body: bytes, hmac_hex: str) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 19100
    conn = http.client.HTTPConnection(host, port, timeout=_HTTP_TIMEOUT_S)
    try:
        conn.request(
            "POST",
            "/dispatch",
            body=body,
            headers={
                "Content-Type": "application/json",
                "X-W4-Hmac": hmac_hex,
            },
        )
        resp = conn.getresponse()
        status = resp.status
        raw = resp.read(65536)
        try:
            data: dict[str, Any] = json.loads(raw)
        except ValueError:
            data = {"raw": raw.decode("utf-8", errors="replace")[:500]}
        return status, data
    finally:
        conn.close()


_TOOL_PROFILE_RE = __import__("re").compile(r"^[a-z_]{3,30}$")


def dispatch_worker(
    worker: str,
    prompt: str,
    ticket_id: str | None = None,
    timeout_seconds: int = _DEFAULT_TIMEOUT_S,
    model: str | None = None,
    thinking_level: str | None = None,
    tool_profile: str | None = None,
    ctx: Any = None,
) -> str:
    """Trigger an approved worker via the dispatch listener (PRO-235).

    ``worker``: one of claude-code, gemini, codex.
    ``prompt``: full prompt text delivered to the worker's stdin.
    ``ticket_id``: optional Linear ticket ID (e.g. PRO-235); used in trace_id.
    ``timeout_seconds``: 1-1800 (default 600).
    ``model``: optional model override (e.g. 'claude-opus-4-7'); claude-code only.
    ``thinking_level``: 'extended' or 'none'; claude-code only (PRO-265).
    ``tool_profile``: Phase 3 subagent isolation profile (e.g. 'drift_executor').
    Returns JSON: ok, trace_id, worker, ticket_id, http_status, listener_response.
    """
    cfg = _CFG
    if cfg is None:
        raise stdio_mcp.McpError("dispatch_worker: not configured", -32000)

    w = (worker or "").strip().lower()
    if w not in _APPROVED_WORKERS:
        approved = ", ".join(sorted(_APPROVED_WORKERS))
        raise stdio_mcp.McpError(
            f"dispatch_worker: unknown worker {worker!r}. Approved: {approved}", -32602
        )

    if not isinstance(prompt, str) or not prompt.strip():
        raise stdio_mcp.McpError("dispatch_worker: prompt must be a non-empty string", -32602)
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise stdio_mcp.McpError(
            f"dispatch_worker: prompt too long ({len(prompt)} chars, max {_MAX_PROMPT_CHARS})",
            -32602,
        )

    ts = int(timeout_seconds) if isinstance(timeout_seconds, float) else timeout_seconds
    if not isinstance(ts, int) or ts < _TIMEOUT_MIN or ts > _TIMEOUT_MAX:
        raise stdio_mcp.McpError(
            f"dispatch_worker: timeout_seconds must be {_TIMEOUT_MIN}-{_TIMEOUT_MAX}", -32602
        )

    if model is not None:
        model = str(model).strip()
        if not model:
            raise stdio_mcp.McpError(
                "dispatch_worker: model must be a non-empty string if provided", -32602
            )

    if thinking_level is not None:
        thinking_level = str(thinking_level).strip().lower()
        if thinking_level not in _APPROVED_THINKING_LEVELS:
            approved = ", ".join(sorted(_APPROVED_THINKING_LEVELS))
            raise stdio_mcp.McpError(
                f"dispatch_worker: invalid thinking_level {thinking_level!r}. Approved: {approved}",
                -32602,
            )

    if tool_profile is not None:
        tool_profile = str(tool_profile).strip()
        if not _TOOL_PROFILE_RE.match(tool_profile):
            raise stdio_mcp.McpError(
                f"dispatch_worker: invalid tool_profile {tool_profile!r}. "
                "Must match /^[a-z_]{3,30}$/",
                -32602,
            )

    trace_id = _generate_trace_id(ticket_id)

    # Write prompt file — listener reads this synchronously before 202.
    inbox_dir = cfg.repo_root / "data" / "n8n_inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = inbox_dir / f"{trace_id}.prompt.json"
    prompt_file.write_text(json.dumps({"prompt": prompt}, ensure_ascii=False), encoding="utf-8")

    # Repo-relative path the listener resolves from REPO_ROOT.
    prompt_path = f"data/n8n_inbox/{trace_id}.prompt.json"

    body_dict: dict[str, Any] = {
        "trace_id": trace_id,
        "worker": w,
        "prompt_path": prompt_path,
        "timeout_seconds": ts,
    }
    if model is not None:
        body_dict["model"] = model
    if thinking_level is not None:
        body_dict["thinking_level"] = thinking_level
    if tool_profile is not None:
        body_dict["tool_profile"] = tool_profile

    body = json.dumps(body_dict, separators=(",", ":")).encode("utf-8")

    sig = _compute_hmac(cfg.dispatch_hmac_secret, body)

    try:
        status_code, resp_data = _post_dispatch(cfg.dispatch_listener_url, body, sig)
    except OSError as exc:
        prompt_file.unlink(missing_ok=True)
        raise stdio_mcp.McpError(
            f"dispatch_worker: could not reach listener at {cfg.dispatch_listener_url}: {exc}",
            -32000,
        ) from exc

    # Listener reads prompt synchronously — safe to clean up on 202.
    if status_code == 202:
        prompt_file.unlink(missing_ok=True)

    if status_code != 202:
        prompt_file.unlink(missing_ok=True)
        raise stdio_mcp.McpError(
            f"dispatch_worker: listener returned {status_code}: "
            f"{_redact.redact(str(resp_data)[:400])}",
            -32000,
        )

    return json.dumps(
        {
            "ok": True,
            "trace_id": trace_id,
            "worker": w,
            "ticket_id": ticket_id,
            "timeout_seconds": ts,
            "model": model,
            "thinking_level": thinking_level,
            "tool_profile": tool_profile,
            "http_status": status_code,
            "listener_response": resp_data,
        },
        indent=2,
    )


TOOL_FUNCTIONS = (dispatch_worker,)


def register(mcp, cfg) -> int:
    """Register dispatch_worker iff MIRU_DISPATCH_ENABLED and secret present."""
    global _CFG
    if not getattr(cfg, "dispatch_enabled", False):
        reason = (
            "W4_LISTENER_HMAC_SECRET not set"
            if not getattr(cfg, "dispatch_hmac_secret", None)
            else "MIRU_DISPATCH_ENABLED not set"
        )
        cfg.disabled_categories["dispatch"] = reason
        return 0

    _CFG = cfg

    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
