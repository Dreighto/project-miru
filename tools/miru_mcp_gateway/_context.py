"""Request-scoped ContextVars for the MCP gateway.

Separated from server.py to avoid the __main__ vs package-import
dual-module problem: when server.py runs as __main__, any module that
does ``from miru_mcp_gateway.server import current_profile`` gets a
DIFFERENT ContextVar object than the one the middleware sets.
"""

from __future__ import annotations

import contextvars

current_profile: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_profile", default="full_operator"
)
current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_trace_id", default=""
)
