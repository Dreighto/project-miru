"""PRO-137: per-category rate limits + parameter validation for MCP tools."""

from __future__ import annotations

import contextlib
import functools
import json
import os
import re
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import audit as gw_audit  # noqa: E402
from miru_mcp_gateway import redact as _redact  # noqa: E402

_MODULE_CATEGORY: dict[str, str] = {
    "miru_mcp_gateway.github_tools": "github_read",
    "miru_mcp_gateway.n8n_tools": "n8n_read",
    "miru_mcp_gateway.n8n_write_tools": "n8n_write",
    "miru_mcp_gateway.fs_tools": "filesystem_read",
    "miru_mcp_gateway.system_tools": "system_logs",
    "miru_mcp_gateway.docs_write_tools": "docs_write",
    "miru_mcp_gateway.activity_tools": "aggregator",
    "miru_mcp_gateway.audit_read_tools": "audit_read",
    "miru_mcp_gateway.worker_tools": "worker_read",
}

_PARAM_REGEX: dict[str, re.Pattern[str]] = {
    "owner": re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,38})$"),
    "repo": re.compile(r"^[a-zA-Z0-9._-]{1,100}$"),
    "path": re.compile(r"^[a-zA-Z0-9_./\\-]+$"),
    "workflow_id": re.compile(r"^[a-zA-Z0-9_-]{1,128}$"),
    "execution_id": re.compile(r"^[a-zA-Z0-9_-]{1,64}$"),
    "workflow_id_param": re.compile(r"^[a-zA-Z0-9_-]{1,128}$"),
    "ticket_id": re.compile(r"^[A-Z]+-[0-9]+$"),
    "pr_number": re.compile(r"^[0-9]{1,9}$"),
    "number": re.compile(r"^[0-9]{1,9}$"),
    "page_id": re.compile(r"^[a-f0-9-]{36}$"),
    "name": re.compile(r"^[a-zA-Z0-9_./-]{1,120}$"),
    "approval_request_id": re.compile(
        r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$"
    ),
    "review_id": re.compile(r"^[0-9]{1,18}$"),
    "worker_name": re.compile(r"^[a-zA-Z0-9_-]{1,40}$"),
    "log_kind": re.compile(r"^(writes|reads|docs)$"),
}

_rate_events: dict[str, list[float]] = defaultdict(list)


def _tool_category(func: Callable[..., Any]) -> str:
    mod = getattr(func, "__module__", "") or ""
    return _MODULE_CATEGORY.get(mod, "default")


def _limit_for_category(cfg: Any, category: str) -> int:
    m = getattr(cfg, "rate_limit_by_category", None) or {}
    return int(m.get(category) or m.get("default", 30))


def _rate_allow(category: str, limit: int) -> tuple[bool, int]:
    now = time.monotonic()
    ev = _rate_events[category]
    cutoff = now - 60.0
    ev[:] = [t for t in ev if t > cutoff]
    if len(ev) >= limit:
        retry_after = max(1, int(61 - (now - ev[0]))) if ev else 30
        return False, retry_after
    ev.append(now)
    return True, 0


def _validate_string_param(key: str, value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str):
        return
    pat = _PARAM_REGEX.get(key)
    if pat is None:
        return
    if not pat.match(value):
        raise stdio_mcp.McpError(
            json.dumps(
                {
                    "error": "invalid_param",
                    "param": key,
                    "value": _redact.redact(value[:80]),
                }
            ),
            -32602,
        )
    if key in ("path", "repo", "name") and ".." in value:
        raise stdio_mcp.McpError(
            json.dumps({"error": "invalid_param", "param": key, "reason": "path_traversal"}),
            -32602,
        )


def _validate_bound_kwargs(kwargs: dict[str, Any]) -> None:
    """Apply regex validation to known string parameters (defense in depth)."""
    alias = {k: v for k, v in kwargs.items() if k != "ctx"}
    if isinstance(alias.get("number"), int):
        alias = {**alias, "number": str(alias["number"])}
    if isinstance(alias.get("review_id"), int):
        alias = {**alias, "review_id": str(alias["review_id"])}
    for key, val in alias.items():
        if isinstance(val, str):
            _validate_string_param(key, val)
    owner, repo = alias.get("owner"), alias.get("repo")
    if isinstance(owner, str) and isinstance(repo, str):
        combined = f"{owner}/{repo}"
        if not re.match(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,38})/[a-zA-Z0-9._-]{1,100}$", combined):
            raise stdio_mcp.McpError(
                json.dumps({"error": "invalid_param", "param": "owner/repo", "value": combined}),
                -32602,
            )


def _should_audit_read_log(func: Callable[..., Any]) -> bool:
    cat = _tool_category(func)
    if cat != "filesystem_read":
        return True
    return os.environ.get("MIRU_READ_AUDIT_FS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _append_read_audit_row(cfg: Any, row: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        gw_audit.append_read_audit(cfg.fs_root, row)


def wrap_tool_entry(func: Callable[..., Any], cfg: Any) -> Callable[..., Any]:
    """Rate-limit, validate params, optional read-audit (PRO-137)."""
    category = _tool_category(func)
    audit_reads = _should_audit_read_log(func)

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        lim = _limit_for_category(cfg, category)
        ok, retry_after = _rate_allow(category, lim)
        if not ok:
            if audit_reads:
                _append_read_audit_row(
                    cfg,
                    {
                        "ts": gw_audit._utc_iso(),
                        "tool": func.__name__,
                        "category": category,
                        "caller": gw_audit.caller_from_fastmcp_context(kwargs.get("ctx")),
                        "params": _redact.redact_dict(
                            {k: v for k, v in kwargs.items() if k != "ctx"}
                        ),
                        "result": "rate_limit_rejected",
                        "duration_ms": 0,
                        "error": None,
                        "retry_after_seconds": retry_after,
                    },
                )
            raise stdio_mcp.McpError(
                json.dumps(
                    {
                        "error": "rate_limit_exceeded",
                        "category": category,
                        "retry_after_seconds": retry_after,
                    }
                ),
                -32000,
            )
        try:
            _validate_bound_kwargs(kwargs)
        except stdio_mcp.McpError as exc:
            if audit_reads:
                _append_read_audit_row(
                    cfg,
                    {
                        "ts": gw_audit._utc_iso(),
                        "tool": func.__name__,
                        "category": category,
                        "caller": gw_audit.caller_from_fastmcp_context(kwargs.get("ctx")),
                        "params": _redact.redact_dict(
                            {k: v for k, v in kwargs.items() if k != "ctx"}
                        ),
                        "result": "param_validation_rejected",
                        "duration_ms": 0,
                        "error": str(exc),
                    },
                )
            raise
        t0 = time.monotonic()
        caller = gw_audit.caller_from_fastmcp_context(kwargs.get("ctx"))
        try:
            out = func(*args, **kwargs)
            ms = int((time.monotonic() - t0) * 1000)
            if audit_reads:
                _append_read_audit_row(
                    cfg,
                    {
                        "ts": gw_audit._utc_iso(),
                        "tool": func.__name__,
                        "category": category,
                        "caller": caller,
                        "params": _redact.redact_dict(
                            {k: v for k, v in kwargs.items() if k != "ctx"}
                        ),
                        "result": "success",
                        "duration_ms": ms,
                        "error": None,
                    },
                )
            return out
        except stdio_mcp.McpError as exc:
            ms = int((time.monotonic() - t0) * 1000)
            if audit_reads:
                _append_read_audit_row(
                    cfg,
                    {
                        "ts": gw_audit._utc_iso(),
                        "tool": func.__name__,
                        "category": category,
                        "caller": caller,
                        "params": _redact.redact_dict(
                            {k: v for k, v in kwargs.items() if k != "ctx"}
                        ),
                        "result": "failure",
                        "duration_ms": ms,
                        "error": str(exc)[:500],
                    },
                )
            raise
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            if audit_reads:
                _append_read_audit_row(
                    cfg,
                    {
                        "ts": gw_audit._utc_iso(),
                        "tool": func.__name__,
                        "category": category,
                        "caller": caller,
                        "params": _redact.redact_dict(
                            {k: v for k, v in kwargs.items() if k != "ctx"}
                        ),
                        "result": "failure",
                        "duration_ms": ms,
                        "error": repr(exc)[:500],
                    },
                )
            raise

    return wrapper


def register_wrapped_tools(mcp: Any, cfg: Any, functions: tuple[Callable[..., Any], ...]) -> int:
    """Register each function behind PRO-137 wrapper."""
    for func in functions:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(functions)
