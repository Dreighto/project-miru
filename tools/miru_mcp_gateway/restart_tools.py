"""Service restart tools (PRO-225).

Gated by MIRU_RESTART_TOOLS_ENABLED. Exposes one tool per approved service.
Restart scripts are hard-coded to the repo-relative paths in CLAUDE.md.
The dispatch listener is managed via Windows Scheduled Task.
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

from miru_mcp_gateway import redact as _redact  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PS_TIMEOUT_S = 60

_APPROVED_RESTARTS: dict[str, dict[str, Any]] = {
    "pm": {
        "label": "PM Dashboard (port 18080)",
        "mode": "script",
        "script": str(_REPO_ROOT / "windows" / "restart_pm.ps1"),
    },
    "miru_ai": {
        "label": "Miru AI (port 18765)",
        "mode": "script",
        "script": str(_REPO_ROOT / "windows" / "restart_miru_ai.ps1"),
    },
    "dispatch_listener": {
        "label": "Dispatch Listener (MiruDispatchListener scheduled task)",
        "mode": "scheduled_task",
        "task_name": "MiruDispatchListener",
    },
    "mcp_gateway": {
        "label": "MCP Gateway (port 18766)",
        "mode": "script",
        "script": str(_REPO_ROOT / "windows" / "restart_mcp_gateway.ps1"),
    },
}


def _run_ps(args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["powershell", "-NonInteractive", "-ExecutionPolicy", "Bypass", *args],
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT_S,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise stdio_mcp.McpError(
            f"restart: powershell command timed out after {_PS_TIMEOUT_S}s", -32000
        ) from exc
    except FileNotFoundError as exc:
        raise stdio_mcp.McpError("restart: powershell.exe not found on PATH", -32000) from exc
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def service_restart(service: str, ctx: Any = None) -> str:
    """Restart an approved Miru service.

    ``service`` must be one of: pm, miru_ai, dispatch_listener, mcp_gateway.
    Returns JSON with ok, service, label, returncode, stdout_tail, stderr_tail.
    """
    svc = (service or "").strip().lower()
    if svc not in _APPROVED_RESTARTS:
        approved = ", ".join(sorted(_APPROVED_RESTARTS))
        raise stdio_mcp.McpError(
            f"restart: unknown service {service!r}. Approved: {approved}",
            -32602,
        )

    defn = _APPROVED_RESTARTS[svc]
    mode = defn["mode"]

    if mode == "script":
        script = defn["script"]
        rc, out, err = _run_ps(["-File", script])
    elif mode == "scheduled_task":
        task = defn["task_name"]
        rc_stop, out_stop, err_stop = _run_ps(
            ["-Command", f"Stop-ScheduledTask -TaskName '{task}'"]
        )
        rc_start, out_start, err_start = _run_ps(
            ["-Command", f"Start-ScheduledTask -TaskName '{task}'"]
        )
        rc = max(rc_stop, rc_start)
        out = f"stop: {out_stop.strip()}\nstart: {out_start.strip()}"
        err = f"stop_err: {err_stop.strip()}\nstart_err: {err_start.strip()}"
    else:
        raise stdio_mcp.McpError(f"restart: unknown mode {mode!r}", -32000)

    tail_chars = 800
    result = {
        "ok": rc == 0,
        "service": svc,
        "label": defn["label"],
        "returncode": rc,
        "stdout_tail": _redact.redact(out[-tail_chars:]),
        "stderr_tail": _redact.redact(err[-tail_chars:]),
    }
    if rc != 0:
        raise stdio_mcp.McpError(
            f"restart: {svc} restart exited {rc}. stderr: {_redact.redact(err[:400])}",
            -32000,
        )
    return json.dumps(result, indent=2)


TOOL_FUNCTIONS = (service_restart,)


def register(mcp, cfg) -> int:
    """Register restart tools iff MIRU_RESTART_TOOLS_ENABLED."""
    if not getattr(cfg, "restart_tools_enabled", False):
        cfg.disabled_categories["restart"] = "MIRU_RESTART_TOOLS_ENABLED not set"
        return 0

    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
