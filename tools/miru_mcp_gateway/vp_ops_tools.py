"""VP Ops supervisory tools — vp_ops_verify_ticket.

Exposes vp_ops_verify.py as a gateway tool so Claude Chat can trigger a
verification pass on a completed ticket without dispatching a full CC session.

Enabled unconditionally (no env gate needed — uses only local filesystem + git).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

_VERIFY_SCRIPT = _TOOLS_DIR / "vp_ops_verify.py"

TOOL_FUNCTIONS: tuple[Any, ...] = ()


def register(mcp: Any, cfg: Any) -> int:
    @mcp.tool()
    async def vp_ops_verify_ticket(ticket_id: str) -> dict:
        """Run VP Ops verification for a completed ticket.

        Checks the completion marker, git commits, file claims, PR state, and
        handoff entry points. Returns a verdict (VERIFIED or FLAGGED) plus a
        list of specific flags if anything is wrong.

        Args:
            ticket_id: Linear ticket identifier, e.g. "PRO-271".
        """
        ticket_id = ticket_id.strip().upper()
        if not ticket_id:
            raise stdio_mcp.McpError(-32602, "ticket_id is required")

        try:
            result = subprocess.run(
                [sys.executable, str(_VERIFY_SCRIPT), ticket_id],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired as exc:
            raise stdio_mcp.McpError(-32603, f"vp_ops_verify timed out for {ticket_id}") from exc
        except Exception as exc:
            raise stdio_mcp.McpError(-32603, f"vp_ops_verify failed: {exc}") from exc

        raw = result.stdout.strip()
        if not raw:
            raise stdio_mcp.McpError(
                -32603,
                f"vp_ops_verify produced no output (exit {result.returncode}): {result.stderr[:200]}",
            )

        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise stdio_mcp.McpError(
                -32603, f"vp_ops_verify returned non-JSON: {raw[:200]}"
            ) from exc

        return record

    return 1
