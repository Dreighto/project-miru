"""Read-only system-status tools: port check, health probe, safe log tail.

All three operate against a fixed allowlist (ports, endpoints, log filenames).
There is no user-supplied path or port. Anything not on the list raises
McpError. Outputs run through redact() to scrub any credential that may have
leaked into a service log.

Approved scope (matches CLAUDE.md):
    Ports         15678 18080 18765 18766 19000
    Endpoints     mcp_gateway, pm, miru_ai, dispatcher, n8n
    Log files     stdout/stderr/restart logs for the four Miru services + startup.log
"""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import Any

# Make the stdio module importable for McpError reuse.
_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import redact as _redact  # noqa: E402

# psutil is optional in this repo (already used optionally in miru_ai). If
# present, we get pid + process name; otherwise we fall back to a simple
# socket connect-test which only tells us "is something listening".
try:
    import psutil  # type: ignore
    _HAS_PSUTIL = True
except ImportError:  # noqa: BLE001
    _HAS_PSUTIL = False


# --- Allowlists ---------------------------------------------------------

# (port, label)
APPROVED_PORTS: tuple[tuple[int, str], ...] = (
    (15678, "n8n"),
    (18080, "pm"),
    (18765, "miru_ai"),
    (18766, "mcp_gateway"),
    (19000, "dispatcher"),
)

# (service, primary_url, fallback_url_or_None)
# "Probe both, prefer rich" -- per operator choice in Stage 2 plan.
APPROVED_HEALTH_ENDPOINTS: tuple[tuple[str, str, str | None], ...] = (
    ("mcp_gateway", "http://127.0.0.1:18766/health", None),
    ("pm", "http://127.0.0.1:18080/__pm_health", "http://127.0.0.1:18080/"),
    ("miru_ai", "http://127.0.0.1:18765/api/health", None),
    ("dispatcher", "http://127.0.0.1:19000/api/health", "http://127.0.0.1:19000/health"),
    ("n8n", "http://localhost:15678/", None),
)

# Resolve log root once at import time. Matches windows/start_mcp_gateway.ps1.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOG_DIR = _REPO_ROOT / "logs"

APPROVED_LOG_FILES: dict[str, Path] = {
    "mcp_gateway_stdout": _LOG_DIR / "mcp_gateway_18766_stdout.log",
    "mcp_gateway_stderr": _LOG_DIR / "mcp_gateway_18766_stderr.log",
    "mcp_gateway_restart": _LOG_DIR / "mcp_gateway_restart.log",
    "pm_stdout": _LOG_DIR / "pm_stdout.log",
    "pm_stderr": _LOG_DIR / "pm_stderr.log",
    "pm_restart": _LOG_DIR / "pm_restart.log",
    "miru_ai_stdout": _LOG_DIR / "miru_ai_stdout.log",
    "miru_ai_stderr": _LOG_DIR / "miru_ai_stderr.log",
    "miru_ai_restart": _LOG_DIR / "miru_ai_restart.log",
    "dispatcher_stdout": _LOG_DIR / "dispatcher_stdout.log",
    "dispatcher_stderr": _LOG_DIR / "dispatcher_stderr.log",
    "startup": _LOG_DIR / "startup.log",
}

_HTTP_TIMEOUT_S = 5
_MAX_LOG_LINES = 500
_MAX_LOG_BYTES = 256 * 1024
_BODY_PREVIEW_BYTES = 240

# The gateway listens on this port. Calling our own /health from inside a
# tool handler would deadlock (the outer request waits on the inner one,
# but uvicorn's sync executor is full). Special-case it to ok=true since
# the tool running at all proves the gateway is up.
_SELF_PORT = 18766


# --- Tools --------------------------------------------------------------


def system_check_ports() -> str:
    """Check whether each approved Miru service port is listening on loopback.

    Approved ports: 15678 (n8n), 18080 (pm), 18765 (miru_ai),
    18766 (mcp_gateway), 19000 (dispatcher).

    Returns JSON string: list of {port, label, listening, pid, process_name}.
    pid/process_name are populated only if psutil is available.
    """
    results: list[dict[str, Any]] = []
    psutil_listeners: dict[int, tuple[int, str]] = {}
    if _HAS_PSUTIL:
        try:
            for conn in psutil.net_connections(kind="tcp"):
                if conn.status != psutil.CONN_LISTEN:
                    continue
                if conn.laddr is None:
                    continue
                lport = getattr(conn.laddr, "port", None)
                if not isinstance(lport, int):
                    continue
                pid = conn.pid or 0
                pname = ""
                if pid:
                    try:
                        pname = psutil.Process(pid).name()
                    except Exception:  # noqa: BLE001
                        pname = ""
                psutil_listeners.setdefault(lport, (pid, pname))
        except Exception:  # noqa: BLE001 -- fall back to socket probe
            psutil_listeners = {}

    for port, label in APPROVED_PORTS:
        if port in psutil_listeners:
            pid, pname = psutil_listeners[port]
            results.append(
                {
                    "port": port,
                    "label": label,
                    "listening": True,
                    "pid": pid or None,
                    "process_name": pname or None,
                }
            )
            continue
        # Socket fallback: connect_ex returns 0 if something is listening.
        listening = _socket_listening("127.0.0.1", port)
        results.append(
            {
                "port": port,
                "label": label,
                "listening": listening,
                "pid": None,
                "process_name": None,
            }
        )

    return json.dumps(_redact.redact_dict(results), indent=2)


def system_check_health_endpoints() -> str:
    """Probe each approved Miru service's health endpoint.

    For services that have both a rich path and a bare-root fallback (PM,
    Dispatcher), we probe the rich path first. If it 404s or errors, we
    fall back to '/' and report which path actually responded.

    Returns JSON string: list of {service, url_probed, status_code, ok,
    body_preview}. body_preview is truncated and redacted.
    """
    import urllib.request
    import urllib.error

    results: list[dict[str, Any]] = []
    for service, primary, fallback in APPROVED_HEALTH_ENDPOINTS:
        # Special-case: calling our own port deadlocks uvicorn's executor.
        # If the URL points at us, short-circuit to ok=true.
        if f":{_SELF_PORT}/" in primary or primary.endswith(f":{_SELF_PORT}"):
            results.append(
                {
                    "service": service,
                    "url_probed": primary,
                    "status_code": 200,
                    "ok": True,
                    "body_preview": "[self-probe skipped: tool is running, gateway is up]",
                    "error": None,
                }
            )
            continue
        urls = [primary] + ([fallback] if fallback else [])
        last: dict[str, Any] = {
            "service": service,
            "url_probed": primary,
            "status_code": None,
            "ok": False,
            "body_preview": "",
            "error": None,
        }
        for url in urls:
            try:
                req = urllib.request.Request(
                    url, method="GET", headers={"User-Agent": "miru-mcp-gateway/0.2"}
                )
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
                    status = resp.status
                    body = resp.read(_BODY_PREVIEW_BYTES)
                last = {
                    "service": service,
                    "url_probed": url,
                    "status_code": status,
                    "ok": 200 <= status < 400,
                    "body_preview": _safe_text(body),
                    "error": None,
                }
                if last["ok"]:
                    break  # primary worked, no need for fallback
            except urllib.error.HTTPError as e:
                last = {
                    "service": service,
                    "url_probed": url,
                    "status_code": e.code,
                    "ok": False,
                    "body_preview": "",
                    "error": f"HTTP {e.code}",
                }
                # Only fall through to fallback for 404/410.
                if e.code not in (404, 410):
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = {
                    "service": service,
                    "url_probed": url,
                    "status_code": None,
                    "ok": False,
                    "body_preview": "",
                    "error": _redact.redact(str(e)),
                }
                break
        results.append(last)

    return json.dumps(_redact.redact_dict(results), indent=2)


def system_tail_safe_log(name: str, lines: int = 100) -> str:
    """Return the last N lines of an approved log file, with redaction.

    `name` must be a key from the APPROVED_LOG_FILES allowlist (e.g.
    'mcp_gateway_stdout', 'dispatcher_stderr'). Raw paths are not accepted.
    `lines` is capped at 500. Total output is capped at 256 KB.

    Use system_tail_safe_log('') to get the list of approved log names.
    """
    if not name:
        return json.dumps(
            {"approved_logs": sorted(APPROVED_LOG_FILES.keys())}, indent=2
        )
    if name not in APPROVED_LOG_FILES:
        raise stdio_mcp.McpError(
            f"system: log not approved: {name!r}. "
            f"Use system_tail_safe_log('') to list approved names.",
            -32000,
        )

    lines = max(1, min(int(lines), _MAX_LOG_LINES))
    log_path = APPROVED_LOG_FILES[name]
    if not log_path.exists():
        return _redact.redact(
            f"[log not found: {name} -> {log_path}]\n"
            f"(the service may not have started yet, or the log was rotated)"
        )

    try:
        text = _tail_text(log_path, lines, _MAX_LOG_BYTES)
    except Exception as exc:  # noqa: BLE001
        raise stdio_mcp.McpError(
            f"system: failed to read log {name}: {exc!r}", -32000
        ) from exc

    return _redact.redact(text)


# --- Helpers ------------------------------------------------------------


def _socket_listening(host: str, port: int) -> bool:
    """Try to connect; if it succeeds quickly, something is listening."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def _safe_text(b: bytes) -> str:
    return b.decode("utf-8", errors="replace")


def _tail_text(path: Path, lines: int, max_bytes: int) -> str:
    """Read up to `max_bytes` from the END of `path` and return the last
    `lines` lines. Robust to large files (we never read the whole thing).
    """
    size = path.stat().st_size
    read_amount = min(size, max_bytes)
    with path.open("rb") as fh:
        if size > read_amount:
            fh.seek(size - read_amount)
        data = fh.read(read_amount)
    text = data.decode("utf-8", errors="replace")
    chunk_lines = text.splitlines()
    return "\n".join(chunk_lines[-lines:])


# --- Manifest + register hook ------------------------------------------

TOOL_FUNCTIONS = (
    system_check_ports,
    system_check_health_endpoints,
    system_tail_safe_log,
)


def register(mcp, cfg) -> int:  # noqa: ARG001
    """Register system_* tools. Always enabled (no env requirement).

    Returns the count registered.
    """
    for func in TOOL_FUNCTIONS:
        mcp.tool(func)
    return len(TOOL_FUNCTIONS)
