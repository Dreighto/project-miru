"""PRO-306: cc_handoff -- Gatekeeper-validated dispatch via MCP.

Routes dispatch proposals through the Local Governance Gatekeeper before
forwarding to the dispatch_listener.  Unlike ``dispatch_worker`` (which
POSTs directly to the listener), ``cc_handoff`` runs the deterministic
floor + LLM cross-context validation first.

Flow::

    CH -> cc_handoff MCP tool
       -> gatekeeper.core.gate_dispatch()
         -> deterministic floor (trace_id, a2a_bus, git, in-flight)
         -> Ollama LLM validation
         -> gatekeeper.forwarder.forward() (if accepted + not shadow)
       <- routing decision JSON (schema v2)

Gated by the same env as dispatch_worker: MIRU_DISPATCH_ENABLED=1 +
W4_LISTENER_HMAC_SECRET set.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _TOOLS_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

_CFG: Any = None

_MAX_PROMPT_CHARS = 60_000
_MAX_DESCRIPTION_CHARS = 30_000


def cc_handoff(
    ticket_id: str,
    prompt: str,
    ticket_description: str | None = None,
    conversational_delta: str | None = None,
    shadow_mode: bool = True,
    gatekeeper_model: str | None = None,
    ctx: Any = None,
) -> str:
    """Route a dispatch proposal through the Gatekeeper before forwarding.

    ``ticket_id``: Linear ticket identifier (e.g. PRO-305). Required.
    ``prompt``: full prompt text for the worker. Required.
    ``ticket_description``: raw ticket body; frontmatter is extracted from this.
    ``conversational_delta``: CH refinement text layered on top of ticket.
    ``shadow_mode``: if True, validate only -- do not forward to listener
        (default True). Set False for live dispatch.
    ``gatekeeper_model``: override the Ollama model for LLM validation.

    Returns JSON: the Gatekeeper's routing decision (schema version 2).
    On acceptance with shadow_mode=False, the worker is dispatched and
    the decision includes a ``forwarded:spawned`` flag.
    On rejection, ``decision.worker`` is ``"none"`` and ``rejection``
    describes why.
    """
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        raise stdio_mcp.McpError(
            "cc_handoff: ticket_id must be a non-empty string",
            -32602,
        )
    if not isinstance(prompt, str) or not prompt.strip():
        raise stdio_mcp.McpError(
            "cc_handoff: prompt must be a non-empty string",
            -32602,
        )
    if len(prompt) > _MAX_PROMPT_CHARS:
        raise stdio_mcp.McpError(
            f"cc_handoff: prompt too long ({len(prompt)} chars, max {_MAX_PROMPT_CHARS})",
            -32602,
        )
    if ticket_description is not None and len(ticket_description) > _MAX_DESCRIPTION_CHARS:
        raise stdio_mcp.McpError(
            f"cc_handoff: ticket_description too long "
            f"({len(ticket_description)} chars, max {_MAX_DESCRIPTION_CHARS})",
            -32602,
        )

    from gatekeeper.core import GatekeeperError, gate_dispatch

    payload: dict[str, Any] = {
        "ticket_id": ticket_id.strip(),
        "prompt": prompt,
        "shadow_mode": bool(shadow_mode),
    }
    if ticket_description is not None:
        payload["ticket_description"] = ticket_description
    if conversational_delta is not None:
        payload["conversational_delta"] = conversational_delta
    if gatekeeper_model is not None:
        payload["gatekeeper_model"] = gatekeeper_model

    try:
        decision = gate_dispatch(payload)
    except GatekeeperError as e:
        raise stdio_mcp.McpError(
            f"cc_handoff: gatekeeper error -- {e.reason}: {e.detail}"
            if hasattr(e, "detail") and e.detail
            else f"cc_handoff: gatekeeper error -- {e}",
            -32000,
        ) from e

    return json.dumps(decision, indent=2)


TOOL_FUNCTIONS = (cc_handoff,)


def register(mcp, cfg) -> int:
    """Register cc_handoff iff dispatch is enabled and HMAC secret present."""
    global _CFG
    if not getattr(cfg, "dispatch_enabled", False):
        reason = (
            "W4_LISTENER_HMAC_SECRET not set"
            if not getattr(cfg, "dispatch_hmac_secret", None)
            else "MIRU_DISPATCH_ENABLED not set"
        )
        cfg.disabled_categories["gatekeeper"] = reason
        return 0

    _CFG = cfg

    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
