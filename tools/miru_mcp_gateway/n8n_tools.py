"""Read-only n8n tools.

Disabled cleanly if N8N_API_KEY is missing. The base URL defaults to
http://localhost:15678 and can be overridden with MIRU_N8N_BASE_URL.

Hard rules:
- Never return raw `nodes` / `connections` arrays. They contain hardcoded
  webhook URLs, credential references, and parameter values that may
  include API tokens. Workflow inspection returns counts + a node-type
  histogram only.
- Never return credential names or values.
- Every output passes through redact() (env-substring scrub + pattern
  scrub for Bearer/JWT/n8n webhook URLs/Telegram bot URLs).
- HTTP timeout = 10s on every call.
"""

from __future__ import annotations

import contextlib
import json
import sys
import uuid
from collections import Counter
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


_HTTP_TIMEOUT_S = 10
_LIMIT_HARD_CAP_LIST = 200
_LIMIT_HARD_CAP_EXEC = 100
_LIMIT_HARD_CAP_HISTORY = 500
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ROUTING_HISTORY_JSONL = _REPO_ROOT / "data" / "spike_ntfy_log.jsonl"


# --- Module-level state populated at register() ------------------------

_API_KEY: str | None = None
_BASE_URL: str = ""
_CFG: Any = None


# --- Thin requests wrapper ---------------------------------------------


def _n8n_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET an n8n REST URL and return parsed JSON. Raises McpError on failure."""
    if requests is None:
        raise stdio_mcp.McpError("n8n: 'requests' library not installed", -32000)
    if not _API_KEY:
        raise stdio_mcp.McpError("n8n: N8N_API_KEY not configured", -32000)
    if not _BASE_URL:
        raise stdio_mcp.McpError("n8n: base URL not configured", -32000)

    url = f"{_BASE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "X-N8N-API-KEY": _API_KEY,
        "User-Agent": "miru-mcp-gateway/0.2",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(
            f"n8n: timeout after {_HTTP_TIMEOUT_S}s on {path}", -32000
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"n8n: transport error on {path}: {_redact.redact(str(exc))}", -32000
        ) from exc

    if resp.status_code == 401:
        raise stdio_mcp.McpError("n8n: 401 Unauthorized -- N8N_API_KEY may be invalid", -32000)
    if resp.status_code == 404:
        raise stdio_mcp.McpError(f"n8n: 404 Not Found: {path}", -32000)
    if not (200 <= resp.status_code < 300):
        body = _redact.redact(resp.text[:500])
        raise stdio_mcp.McpError(f"n8n: HTTP {resp.status_code} on {path}: {body}", -32000)

    try:
        return resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError(f"n8n: non-JSON response on {path}", -32000) from exc


def _clamp(value: int, default: int, cap: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, cap))


def _summarize_workflow(raw: dict[str, Any]) -> dict[str, Any]:
    """Build a safe summary -- never includes raw nodes or connections."""
    nodes = raw.get("nodes") or []
    type_counter: Counter[str] = Counter()
    for n in nodes:
        if isinstance(n, dict):
            type_counter[str(n.get("type", "unknown"))] += 1
    return {
        "id": raw.get("id"),
        "name": raw.get("name", ""),
        "active": bool(raw.get("active", False)),
        "node_count": len(nodes),
        "node_types": dict(type_counter),
        "tags": [t.get("name") for t in (raw.get("tags") or []) if isinstance(t, dict)],
        "created_at": raw.get("createdAt", ""),
        "updated_at": raw.get("updatedAt", ""),
    }


# --- Tools --------------------------------------------------------------


def n8n_list_workflows(active_only: bool = False, limit: int = 50) -> str:
    """List n8n workflows.

    Returns JSON list of {id, name, active, updated_at, tags}. Workflow
    nodes/connections are NEVER returned by this tool. Use
    n8n_get_workflow_summary to inspect a single workflow's structure.

    `limit` capped at 200.
    """
    n = _clamp(limit, 50, _LIMIT_HARD_CAP_LIST)
    params: dict[str, Any] = {"limit": n}
    if active_only:
        params["active"] = "true"
    raw = _n8n_get("/api/v1/workflows", params=params)
    items = raw.get("data") if isinstance(raw, dict) else raw
    out: list[dict[str, Any]] = []
    for wf in items or []:
        if not isinstance(wf, dict):
            continue
        out.append(
            {
                "id": wf.get("id"),
                "name": wf.get("name", ""),
                "active": bool(wf.get("active", False)),
                "updated_at": wf.get("updatedAt", ""),
                "tags": [t.get("name") for t in (wf.get("tags") or []) if isinstance(t, dict)],
            }
        )
    return json.dumps(_redact.redact_dict(out), indent=2)


def n8n_get_workflow_summary(workflow_id: str) -> str:
    """Return a node-shape summary for a single workflow.

    Output JSON: {id, name, active, node_count, node_types, tags,
    created_at, updated_at}. Node parameters are NOT returned (they may
    contain webhook URLs and credential references).
    """
    if not workflow_id:
        raise stdio_mcp.McpError("n8n: workflow_id required", -32602)
    raw = _n8n_get(f"/api/v1/workflows/{workflow_id}")
    if not isinstance(raw, dict):
        raise stdio_mcp.McpError(
            f"n8n: unexpected response shape for workflow {workflow_id}", -32000
        )
    summary = _summarize_workflow(raw)
    return json.dumps(_redact.redact_dict(summary), indent=2)


def n8n_list_recent_executions(workflow_id: str | None = None, limit: int = 20) -> str:
    """List recent n8n executions.

    Returns JSON list of {id, workflow_id, status, mode, started_at,
    finished_at}. `limit` capped at 100.
    """
    n = _clamp(limit, 20, _LIMIT_HARD_CAP_EXEC)
    params: dict[str, Any] = {"limit": n}
    if workflow_id:
        params["workflowId"] = workflow_id
    raw = _n8n_get("/api/v1/executions", params=params)
    items = raw.get("data") if isinstance(raw, dict) else raw
    out: list[dict[str, Any]] = []
    for ex in items or []:
        if not isinstance(ex, dict):
            continue
        out.append(
            {
                "id": ex.get("id"),
                "workflow_id": ex.get("workflowId"),
                "status": ex.get("status", (ex.get("finished") and "success") or "running"),
                "mode": ex.get("mode", ""),
                "started_at": ex.get("startedAt", ""),
                "finished_at": ex.get("stoppedAt", ""),
            }
        )
    return json.dumps(_redact.redact_dict(out), indent=2)


def n8n_get_execution_summary(execution_id: str) -> str:
    """Return a redacted summary of a single execution.

    Output JSON: {id, workflow_id, status, mode, started_at, finished_at,
    error_summary, node_run_count}. The execution's full node-by-node data
    is never returned -- it can contain credentials and request bodies.
    """
    if not execution_id:
        raise stdio_mcp.McpError("n8n: execution_id required", -32602)
    raw = _n8n_get(f"/api/v1/executions/{execution_id}")
    if not isinstance(raw, dict):
        raise stdio_mcp.McpError(
            f"n8n: unexpected response shape for execution {execution_id}", -32000
        )
    data = raw.get("data") or {}
    result_data = data.get("resultData") or {} if isinstance(data, dict) else {}
    error = result_data.get("error") or {} if isinstance(result_data, dict) else {}
    error_summary = ""
    if isinstance(error, dict) and error:
        msg = str(error.get("message") or error.get("name") or "error")
        error_summary = msg[:240]
    run_data = result_data.get("runData") if isinstance(result_data, dict) else None
    node_run_count = len(run_data) if isinstance(run_data, dict) else 0
    payload = {
        "id": raw.get("id"),
        "workflow_id": raw.get("workflowId"),
        "status": raw.get("status", ""),
        "mode": raw.get("mode", ""),
        "started_at": raw.get("startedAt", ""),
        "finished_at": raw.get("stoppedAt", ""),
        "error_summary": error_summary,
        "node_run_count": node_run_count,
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


def n8n_read_routing_history(limit: int = 50) -> str:
    """Read recent approval-routing history.

    Primary source: data/spike_ntfy_log.jsonl (n8n approval webhook log).
    Each line is one routing event: {ts, button, test, method, query,
    body_keys}.

    Fallback: if the JSONL file is missing or empty, calls
    /api/v1/executions and returns a short execution-summary list.

    `limit` capped at 500.
    """
    n = _clamp(limit, 50, _LIMIT_HARD_CAP_HISTORY)

    if _ROUTING_HISTORY_JSONL.exists() and _ROUTING_HISTORY_JSONL.stat().st_size > 0:
        rows: list[dict[str, Any]] = []
        try:
            with _ROUTING_HISTORY_JSONL.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        # Malformed line -- include it as raw so the operator can see.
                        rows.append({"_unparsed": line[:200]})
        except OSError as exc:
            raise stdio_mcp.McpError(
                f"n8n: failed to read routing history: {exc!r}", -32000
            ) from exc
        tail = rows[-n:]
        return json.dumps(
            _redact.redact_dict(
                {"source": "jsonl", "path": str(_ROUTING_HISTORY_JSONL), "rows": tail}
            ),
            indent=2,
        )

    # Fallback to n8n API.
    raw = _n8n_get(
        "/api/v1/executions",
        params={"limit": min(n, _LIMIT_HARD_CAP_EXEC)},
    )
    items = raw.get("data") if isinstance(raw, dict) else raw
    rows = []
    for ex in items or []:
        if not isinstance(ex, dict):
            continue
        rows.append(
            {
                "id": ex.get("id"),
                "workflow_id": ex.get("workflowId"),
                "status": ex.get("status", ""),
                "mode": ex.get("mode", ""),
                "started_at": ex.get("startedAt", ""),
                "finished_at": ex.get("stoppedAt", ""),
            }
        )
    return json.dumps(
        _redact.redact_dict({"source": "n8n_api", "rows": rows}),
        indent=2,
    )


# --- PRO-132 execution diagnosis ----------------------------------------


def _workflow_id_to_name_map() -> dict[str, str]:
    raw = _n8n_get("/api/v1/workflows", params={"limit": 200})
    items = raw.get("data") if isinstance(raw, dict) else raw
    m: dict[str, str] = {}
    for wf in items or []:
        if isinstance(wf, dict) and wf.get("id"):
            m[str(wf["id"])] = str(wf.get("name", ""))
    return m


def _append_execution_read_intent(cfg: Any, request_id: str, execution_id: str) -> None:
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "kind": "intent",
        "request_id": request_id,
        "intent_written_at": gw_audit._utc_iso(),
        "operation": "read_execution_include_data",
        "workflow_id": None,
        "workflow_json": None,
        "execution_id": execution_id,
    }
    with cfg.mcp_gateway_pending_writes_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    gw_audit.notify_approval_webhook(
        getattr(cfg, "n8n_write_approval_notify_url", None), request_id
    )


def _item_shape(item: Any) -> Any:
    if isinstance(item, dict):
        j = item.get("json")
        if isinstance(j, dict):
            return sorted(j.keys())[:80]
        return "_non_object_"
    return "_scalar_"


def _branch_shapes(branch: Any) -> list[Any]:
    if not isinstance(branch, list):
        return []
    out: list[Any] = []
    for wire in branch[:20]:
        if isinstance(wire, list):
            out.append([_item_shape(x) for x in wire[:30]])
        else:
            out.append("_bad_wire_")
    return out


def _execution_node_summaries(
    raw: dict[str, Any], *, include_values: bool
) -> tuple[list[dict[str, Any]], str, str, str]:
    """Return (nodes, error_summary, error_node, error_stack)."""
    data = raw.get("data") or {}
    result_data = data.get("resultData") or {} if isinstance(data, dict) else {}
    err = result_data.get("error") if isinstance(result_data, dict) else None
    error_summary = ""
    error_node = ""
    error_stack = ""
    if isinstance(err, dict) and err:
        error_summary = str(err.get("message") or err.get("name") or "error")[:500]
        error_node = str(err.get("node", "") or err.get("nodeName", "") or "")
        tb = err.get("stack")
        if isinstance(tb, str):
            error_stack = tb[:2000]
    run_data = result_data.get("runData") if isinstance(result_data, dict) else None
    nodes_out: list[dict[str, Any]] = []
    if not isinstance(run_data, dict):
        return nodes_out, error_summary, error_node, error_stack
    for node_name, runs in run_data.items():
        if not isinstance(runs, list) or not runs:
            continue
        last = runs[-1] if isinstance(runs[-1], dict) else {}
        exec_ms = int(last.get("executionTime", 0) or 0)
        status = "success"
        if last.get("error"):
            status = "error"
        err_msg = ""
        if isinstance(last.get("error"), dict):
            err_msg = str(last["error"].get("message", ""))[:400]
        main = (last.get("data") or {}).get("main")
        item_in = item_out = 0
        in_shape: Any = []
        out_shape: Any = []
        if isinstance(main, list):
            for branch in main:
                if isinstance(branch, list):
                    item_in += sum(len(w) for w in branch if isinstance(w, list))
                    in_shape = _branch_shapes(branch)
        out_shape = (
            main if include_values and isinstance(main, list) else in_shape
        )  # full structure when include_values; else shapes only
        nodes_out.append(
            {
                "name": str(node_name),
                "type": str(last.get("nodeType", "") or last.get("type", "") or ""),
                "status": status,
                "execution_time_ms": exec_ms,
                "item_count_in": item_in,
                "item_count_out": item_out,
                "error_message": err_msg,
                "input_shape": in_shape,
                "output_shape": out_shape,
            }
        )
    return nodes_out, error_summary, error_node, error_stack


def n8n_list_executions(
    status: str | None = None,
    workflow_id: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> str:
    """List executions with optional filters (PRO-132)."""
    n = _clamp(limit, 20, 100)
    params: dict[str, Any] = {"limit": n}
    if workflow_id:
        params["workflowId"] = workflow_id
    if status:
        params["status"] = status
    raw = _n8n_get("/api/v1/executions", params=params)
    items = raw.get("data") if isinstance(raw, dict) else raw
    names = _workflow_id_to_name_map()
    out: list[dict[str, Any]] = []
    for ex in items or []:
        if not isinstance(ex, dict):
            continue
        started = str(ex.get("startedAt", ""))
        if since and started < since:
            continue
        wid = str(ex.get("workflowId", "") or "")
        err_msg = ""
        data = ex.get("data") or {}
        if isinstance(data, dict):
            rd = (
                (data.get("resultData") or {}).get("error")
                if isinstance(data.get("resultData"), dict)
                else None
            )
            if isinstance(rd, dict):
                err_msg = str(rd.get("message", ""))[:400]
        out.append(
            {
                "id": ex.get("id"),
                "workflow_id": wid,
                "workflow_name": names.get(wid, ""),
                "status": ex.get("status", ""),
                "mode": ex.get("mode", ""),
                "started_at": started,
                "finished_at": ex.get("stoppedAt", ""),
                "error_message": err_msg,
            }
        )
    return json.dumps(_redact.redact_dict(out), indent=2)


def n8n_get_execution(
    execution_id: str,
    include_node_data: bool = False,
    approval_request_id: str | None = None,
    ctx: Any = None,
) -> str:
    """Per-node execution diagnosis (PRO-132). ``include_node_data=True`` is
    Telegram-approval-gated unless ``MIRU_N8N_EXECUTION_DATA_SKIP_APPROVAL`` or
    ``approval_request_id`` matches a cache file dropped after operator/W7 approval.
    """
    if not execution_id:
        raise stdio_mcp.McpError("n8n: execution_id required", -32602)
    cfg = _CFG
    if cfg is None:
        raise stdio_mcp.McpError("n8n: gateway not configured", -32000)

    cache_dir = cfg.mcp_gateway_execution_cache_dir
    if approval_request_id:
        cache_file = cache_dir / f"{approval_request_id.strip()}.json"
        if not cache_file.is_file():
            raise stdio_mcp.McpError(
                "n8n: no approved execution cache for this request_id yet", -32000
            )
        try:
            text = cache_file.read_text(encoding="utf-8", errors="replace")
            data = json.loads(text)
        except (OSError, ValueError) as exc:
            raise stdio_mcp.McpError(f"n8n: bad execution cache file: {exc!r}", -32000) from exc
        with contextlib.suppress(OSError):
            cache_file.unlink()
        return json.dumps(_redact.redact_dict(data), indent=2)

    if include_node_data and getattr(cfg, "n8n_execution_data_skip_approval", False):
        raw = _n8n_get(
            f"/api/v1/executions/{execution_id}",
            params={"includeData": "true"},
        )
        if not isinstance(raw, dict):
            raise stdio_mcp.McpError("n8n: unexpected execution response", -32000)
        nodes, esum, enode, estack = _execution_node_summaries(raw, include_values=True)
        wf_name = ""
        wid = str(raw.get("workflowId", "") or "")
        if wid:
            names = _workflow_id_to_name_map()
            wf_name = names.get(wid, "")
        started = str(raw.get("startedAt", ""))
        finished = str(raw.get("stoppedAt", ""))
        duration_ms = 0
        try:
            if started and finished:
                from datetime import datetime

                a = datetime.fromisoformat(started.replace("Z", "+00:00"))
                b = datetime.fromisoformat(finished.replace("Z", "+00:00"))
                duration_ms = int((b - a).total_seconds() * 1000)
        except Exception:
            duration_ms = 0
        payload = {
            "id": raw.get("id"),
            "workflow_id": wid,
            "workflow_name": wf_name,
            "status": raw.get("status", ""),
            "mode": raw.get("mode", ""),
            "started_at": started,
            "finished_at": finished,
            "duration_ms": duration_ms,
            "error_summary": esum,
            "error_node": enode,
            "error_stack": estack,
            "nodes": nodes,
            "note": "include_node_data bypass via MIRU_N8N_EXECUTION_DATA_SKIP_APPROVAL",
        }
        return json.dumps(_redact.redact_dict(payload), indent=2)

    if include_node_data:
        rid = str(uuid.uuid4())
        _append_execution_read_intent(cfg, rid, execution_id)
        return json.dumps(
            _redact.redact_dict(
                {
                    "ok": True,
                    "status": "pending_approval",
                    "approval": {
                        "request_id": rid,
                        "kind": "n8n_execution_include_data",
                        "execution_id": execution_id,
                        "submitted_at": gw_audit._utc_iso(),
                        "hint": "After Telegram approval, W7 (or operator) should "
                        "write execution JSON to "
                        f"data/mcp_gateway_execution_cache/{rid}.json then call "
                        "n8n_get_execution again with approval_request_id.",
                    },
                }
            ),
            indent=2,
        )

    raw = _n8n_get(
        f"/api/v1/executions/{execution_id}",
        params={"includeData": "true"},
    )
    if not isinstance(raw, dict):
        raise stdio_mcp.McpError(
            f"n8n: unexpected response shape for execution {execution_id}", -32000
        )
    nodes, esum, enode, estack = _execution_node_summaries(raw, include_values=False)
    wid = str(raw.get("workflowId", "") or "")
    names = _workflow_id_to_name_map()
    wf_name = names.get(wid, "")
    started = str(raw.get("startedAt", ""))
    finished = str(raw.get("stoppedAt", ""))
    duration_ms = 0
    try:
        if started and finished:
            from datetime import datetime

            a = datetime.fromisoformat(started.replace("Z", "+00:00"))
            b = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            duration_ms = int((b - a).total_seconds() * 1000)
    except Exception:
        duration_ms = 0
    payload = {
        "id": raw.get("id"),
        "workflow_id": wid,
        "workflow_name": wf_name,
        "status": raw.get("status", ""),
        "mode": raw.get("mode", ""),
        "started_at": started,
        "finished_at": finished,
        "duration_ms": duration_ms,
        "error_summary": esum,
        "error_node": enode,
        "error_stack": estack,
        "nodes": nodes,
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


# --- Manifest + register hook ------------------------------------------

TOOL_FUNCTIONS = (
    n8n_list_workflows,
    n8n_get_workflow_summary,
    n8n_list_recent_executions,
    n8n_get_execution_summary,
    n8n_read_routing_history,
    n8n_list_executions,
    n8n_get_execution,
)


def register(mcp, cfg) -> int:
    """Register n8n_* tools iff N8N_API_KEY + MIRU_N8N_READ_ENABLED (PRO-132)."""
    global _API_KEY, _BASE_URL, _CFG

    if not getattr(cfg, "n8n_api_key", None):
        cfg.disabled_categories["n8n"] = "N8N_API_KEY missing"
        return 0
    if not getattr(cfg, "n8n_read_enabled", False):
        cfg.disabled_categories["n8n"] = "MIRU_N8N_READ_ENABLED not set to true"
        return 0
    if requests is None:
        cfg.disabled_categories["n8n"] = "'requests' library not installed"
        return 0

    _CFG = cfg
    _API_KEY = cfg.n8n_api_key
    _BASE_URL = cfg.n8n_base_url or ""

    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
