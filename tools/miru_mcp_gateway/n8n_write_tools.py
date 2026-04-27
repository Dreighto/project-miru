"""n8n write tools (PRO-122) — lifecycle, execution, modification, archive, etc.

Gated by MIRU_N8N_WRITE_ENABLED + N8N_API_KEY. Workflow allowlist optional.
Approval-gated create/update append to data/mcp_gateway_pending_writes.jsonl
and optionally POST MIRU_N8N_WRITE_APPROVAL_NOTIFY_URL.
"""

from __future__ import annotations

import json
import sys
import uuid
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
_LIMIT_CAP = 100

_API_KEY: str | None = None
_BASE_URL: str = ""
_WORKFLOW_ALLOWLIST: frozenset[str] = frozenset()
_CFG: Any = None


def _cfg() -> Any:
    if _CFG is None:
        raise RuntimeError("n8n_write_tools not configured")
    return _CFG


def _audit_n8n(
    *,
    tool: str,
    caller: str,
    params: dict[str, Any],
    target_id: str,
    result: str,
    error: str | None,
) -> None:
    writes_log, _, _ = gw_audit.default_audit_paths(_cfg().fs_root)
    row = {
        "ts": gw_audit._utc_iso(),
        "tool": tool,
        "caller": caller,
        "params": _redact.redact_dict(params),
        "target_id": target_id,
        "result": result,
        "error": error,
    }
    gw_audit.append_jsonl_chained(writes_log, row)


def _assert_workflow_allowed(workflow_id: str) -> None:
    if not _WORKFLOW_ALLOWLIST:
        return
    if workflow_id not in _WORKFLOW_ALLOWLIST:
        raise stdio_mcp.McpError(
            f"n8n_write: workflow_id not in MIRU_N8N_WRITE_WORKFLOW_ALLOWLIST: " f"{workflow_id!r}",
            -32000,
        )


def _n8n_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: Any | None = None,
    include_response_metadata: bool = False,
) -> Any:
    if requests is None:
        raise stdio_mcp.McpError("n8n_write: 'requests' library not installed", -32000)
    if not _API_KEY or not _BASE_URL:
        raise stdio_mcp.McpError("n8n_write: not configured", -32000)
    url = f"{_BASE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-N8N-API-KEY": _API_KEY,
        "User-Agent": "miru-mcp-gateway/0.3",
    }
    try:
        resp = requests.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=_HTTP_TIMEOUT_S,
        )
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(
            f"n8n_write: timeout after {_HTTP_TIMEOUT_S}s on {path}", -32000
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"n8n_write: transport error on {path}: {_redact.redact(str(exc))}",
            -32000,
        ) from exc

    if resp.status_code == 401:
        raise stdio_mcp.McpError(
            "n8n_write: 401 Unauthorized -- N8N_API_KEY may be invalid", -32000
        )
    if not (200 <= resp.status_code < 300):
        body = _redact.redact(resp.text[:800])
        raise stdio_mcp.McpError(f"n8n_write: HTTP {resp.status_code} on {path}: {body}", -32000)
    if include_response_metadata:
        return {
            "http_status": resp.status_code,
            "body_preview": _redact.redact(resp.text[:4000]),
        }
    if not resp.content.strip():
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"_raw": resp.text[:500]}


def _json_response(payload: Any) -> str:
    return json.dumps(_redact.redact_dict(payload), indent=2)


def _caller(ctx: Any) -> str:
    return gw_audit.caller_from_fastmcp_context(ctx)


def _append_pending_write_intent(
    *,
    operation: str,
    workflow_id: str | None,
    workflow_json: dict[str, Any] | None,
    request_id: str,
    execution_id: str | None = None,
) -> None:
    cfg = _cfg()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "kind": "intent",
        "request_id": request_id,
        "intent_written_at": gw_audit._utc_iso(),
        "operation": operation,
        "workflow_id": workflow_id,
        "workflow_json": workflow_json,
    }
    if execution_id:
        row["execution_id"] = execution_id
    with cfg.mcp_gateway_pending_writes_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n")
    gw_audit.notify_approval_webhook(
        getattr(cfg, "n8n_write_approval_notify_url", None), request_id
    )


def n8n_activate_workflow(workflow_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id}
    try:
        _assert_workflow_allowed(workflow_id)
        out = _n8n_request("POST", f"/api/v1/workflows/{workflow_id}/activate")
        _audit_n8n(
            tool="n8n_activate_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_activate_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def n8n_deactivate_workflow(workflow_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id}
    try:
        _assert_workflow_allowed(workflow_id)
        out = _n8n_request("POST", f"/api/v1/workflows/{workflow_id}/deactivate")
        _audit_n8n(
            tool="n8n_deactivate_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_deactivate_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def n8n_execute_workflow(
    workflow_id: str, input_data: dict[str, Any] | None = None, ctx: Any = None
) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id, "input_data": input_data or {}}
    try:
        _assert_workflow_allowed(workflow_id)
        body: dict[str, Any] = {}
        if input_data:
            body["inputData"] = input_data
        # Prefer newer public execute endpoint; fall back to /run.
        try:
            out = _n8n_request("POST", f"/api/v1/workflows/{workflow_id}/execute", json_body=body)
        except stdio_mcp.McpError as exc:
            if "HTTP 404" not in str(exc):
                raise
            out = _n8n_request("POST", f"/api/v1/workflows/{workflow_id}/run", json_body=body or {})
        _audit_n8n(
            tool="n8n_execute_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_execute_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def n8n_trigger_webhook(
    webhook_path: str, payload: dict[str, Any] | None = None, ctx: Any = None
) -> str:
    caller = _caller(ctx)
    params = {"webhook_path": webhook_path, "payload": payload or {}}
    raw = (webhook_path or "").strip()
    if ".." in raw or raw.startswith("http"):
        raise stdio_mcp.McpError("n8n_write: webhook_path must be a relative path fragment", -32602)
    path = raw if raw.startswith("/") else f"/{raw}"
    if "/webhook/" not in path and not path.startswith("/webhook"):
        path = "/webhook/" + raw.lstrip("/")
    try:
        out = _n8n_request(
            "POST",
            path,
            json_body=payload or {},
            include_response_metadata=True,
        )
        _audit_n8n(
            tool="n8n_trigger_webhook",
            caller=caller,
            params=params,
            target_id=path,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_trigger_webhook",
            caller=caller,
            params=params,
            target_id=path,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_stop_execution(execution_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"execution_id": execution_id}
    try:
        if not execution_id:
            raise stdio_mcp.McpError("n8n_write: execution_id required", -32602)
        out = _n8n_request("POST", f"/api/v1/executions/{execution_id}/stop")
        _audit_n8n(
            tool="n8n_stop_execution",
            caller=caller,
            params=params,
            target_id=execution_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_stop_execution",
            caller=caller,
            params=params,
            target_id=execution_id,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_retry_execution(execution_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"execution_id": execution_id}
    try:
        if not execution_id:
            raise stdio_mcp.McpError("n8n_write: execution_id required", -32602)
        out = _n8n_request(
            "POST",
            f"/api/v1/executions/{execution_id}/retry",
            json_body={"loadWorkflow": True},
        )
        _audit_n8n(
            tool="n8n_retry_execution",
            caller=caller,
            params=params,
            target_id=execution_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_retry_execution",
            caller=caller,
            params=params,
            target_id=execution_id,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_create_workflow(workflow_json: dict[str, Any], ctx: Any = None) -> str:
    """Queue create behind Telegram approval (no direct n8n API call)."""
    caller = _caller(ctx)
    rid = str(uuid.uuid4())
    params: dict[str, Any] = {"workflow_json": workflow_json}
    try:
        if not isinstance(workflow_json, dict):
            raise stdio_mcp.McpError("n8n_write: workflow_json must be an object", -32602)
        _append_pending_write_intent(
            operation="create_workflow",
            workflow_id=None,
            workflow_json=workflow_json,
            request_id=rid,
        )
        summary = {
            "name": workflow_json.get("name", ""),
            "node_count": len(workflow_json.get("nodes") or []),
        }
        payload = {
            "ok": True,
            "status": "pending_approval",
            "approval": {
                "request_id": rid,
                "kind": "n8n_create_workflow",
                "workflow_id": None,
                "submitted_at": gw_audit._utc_iso(),
                "summary": summary,
                "proposed_payload": _redact.redact_dict(workflow_json),
            },
        }
        _audit_n8n(
            tool="n8n_create_workflow",
            caller=caller,
            params=params,
            target_id=rid,
            result="pending_approval",
            error=None,
        )
        return _json_response(payload)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_create_workflow",
            caller=caller,
            params=params,
            target_id=rid,
            result="failure",
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_n8n(
            tool="n8n_create_workflow",
            caller=caller,
            params=params,
            target_id=rid,
            result="failure",
            error=str(exc),
        )
        raise stdio_mcp.McpError(f"n8n_write: {exc!r}", -32000) from exc


def n8n_update_workflow(workflow_id: str, workflow_json: dict[str, Any], ctx: Any = None) -> str:
    """Queue full workflow PUT behind Telegram approval."""
    caller = _caller(ctx)
    rid = str(uuid.uuid4())
    params: dict[str, Any] = {
        "workflow_id": workflow_id,
        "workflow_json": workflow_json,
    }
    try:
        _assert_workflow_allowed(workflow_id)
        if not isinstance(workflow_json, dict):
            raise stdio_mcp.McpError("n8n_write: workflow_json must be an object", -32602)
        _append_pending_write_intent(
            operation="update_workflow",
            workflow_id=workflow_id,
            workflow_json=workflow_json,
            request_id=rid,
        )
        summary = {
            "name": workflow_json.get("name", ""),
            "node_count": len(workflow_json.get("nodes") or []),
        }
        payload = {
            "ok": True,
            "status": "pending_approval",
            "approval": {
                "request_id": rid,
                "kind": "n8n_update_workflow",
                "workflow_id": workflow_id,
                "submitted_at": gw_audit._utc_iso(),
                "summary": summary,
                "proposed_payload": _redact.redact_dict(workflow_json),
            },
        }
        _audit_n8n(
            tool="n8n_update_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="pending_approval",
            error=None,
        )
        return _json_response(payload)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_update_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_n8n(
            tool="n8n_update_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="failure",
            error=str(exc),
        )
        raise stdio_mcp.McpError(f"n8n_write: {exc!r}", -32000) from exc


def n8n_update_workflow_settings(
    workflow_id: str, settings: dict[str, Any], ctx: Any = None
) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id, "settings": settings}
    try:
        _assert_workflow_allowed(workflow_id)
        wf = _n8n_request("GET", f"/api/v1/workflows/{workflow_id}")
        if not isinstance(wf, dict):
            raise stdio_mcp.McpError("n8n_write: unexpected workflow shape", -32000)
        merged = dict(wf)
        merged["settings"] = {**(wf.get("settings") or {}), **(settings or {})}
        out = _n8n_request("PUT", f"/api/v1/workflows/{workflow_id}", json_body=merged)
        _audit_n8n(
            tool="n8n_update_workflow_settings",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_update_workflow_settings",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def _tag_name_to_id(name: str) -> str:
    raw = _n8n_request("GET", "/api/v1/tags", params={"limit": 200})
    data = raw.get("data") if isinstance(raw, dict) else raw
    for t in data or []:
        if isinstance(t, dict) and t.get("name") == name:
            tid = t.get("id")
            if tid is not None:
                return str(tid)
    raise stdio_mcp.McpError(f"n8n_write: tag not found: {name!r}", -32000)


def n8n_update_workflow_tags(workflow_id: str, tags: list[str], ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id, "tags": tags}
    try:
        _assert_workflow_allowed(workflow_id)
        ids: list[str] = []
        for name in tags or []:
            ids.append(_tag_name_to_id(str(name)))
        out = _n8n_request(
            "PUT",
            f"/api/v1/workflows/{workflow_id}/tags",
            json_body=[{"id": i} for i in ids],
        )
        _audit_n8n(
            tool="n8n_update_workflow_tags",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_update_workflow_tags",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def n8n_rename_workflow(workflow_id: str, new_name: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id, "new_name": new_name}
    try:
        _assert_workflow_allowed(workflow_id)
        wf = _n8n_request("GET", f"/api/v1/workflows/{workflow_id}")
        if not isinstance(wf, dict):
            raise stdio_mcp.McpError("n8n_write: unexpected workflow shape", -32000)
        merged = dict(wf)
        merged["name"] = new_name
        out = _n8n_request("PUT", f"/api/v1/workflows/{workflow_id}", json_body=merged)
        _audit_n8n(
            tool="n8n_rename_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_rename_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def n8n_archive_workflow(workflow_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id}
    try:
        _assert_workflow_allowed(workflow_id)
        out = _n8n_request("POST", f"/api/v1/workflows/{workflow_id}/archive")
        _audit_n8n(
            tool="n8n_archive_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        if "HTTP 404" in str(exc):
            msg = (
                "n8n_archive_workflow: POST /archive returned 404 — this n8n build "
                "may not expose soft-archive. Use n8n_deactivate_workflow or upgrade "
                "n8n; do not substitute silent activate/deactivate as archive."
            )
            _audit_n8n(
                tool="n8n_archive_workflow",
                caller=caller,
                params=params,
                target_id=workflow_id,
                result="failure",
                error=msg,
            )
            raise stdio_mcp.McpError(msg, -32000) from exc
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_archive_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def n8n_unarchive_workflow(workflow_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"workflow_id": workflow_id}
    try:
        _assert_workflow_allowed(workflow_id)
        out = _n8n_request("POST", f"/api/v1/workflows/{workflow_id}/unarchive")
        _audit_n8n(
            tool="n8n_unarchive_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        if "HTTP 404" in str(exc):
            msg = (
                "n8n_unarchive_workflow: POST /unarchive returned 404 — unsupported "
                "on this n8n build."
            )
            _audit_n8n(
                tool="n8n_unarchive_workflow",
                caller=caller,
                params=params,
                target_id=workflow_id,
                result="failure",
                error=msg,
            )
            raise stdio_mcp.McpError(msg, -32000) from exc
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_unarchive_workflow",
            caller=caller,
            params=params,
            target_id=workflow_id,
            result=res,
            error=str(exc),
        )
        raise


def n8n_delete_execution(execution_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"execution_id": execution_id}
    try:
        if not execution_id:
            raise stdio_mcp.McpError("n8n_write: execution_id required", -32602)
        out = _n8n_request("DELETE", f"/api/v1/executions/{execution_id}")
        _audit_n8n(
            tool="n8n_delete_execution",
            caller=caller,
            params=params,
            target_id=execution_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_delete_execution",
            caller=caller,
            params=params,
            target_id=execution_id,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_bulk_delete_executions(filter: dict[str, Any], ctx: Any = None) -> str:
    """Delete up to 100 executions matching filter (>=1 filter key required)."""
    caller = _caller(ctx)
    params = {"filter": filter}
    wf_id = (filter or {}).get("workflow_id") or (filter or {}).get("workflowId")
    status = (filter or {}).get("status")
    before_iso = (filter or {}).get("before_date") or (filter or {}).get("beforeDate")
    if not filter or not any([wf_id, status, before_iso]):
        raise stdio_mcp.McpError(
            "n8n_write: filter must include at least one of " "workflow_id, status, before_date",
            -32602,
        )
    if wf_id:
        _assert_workflow_allowed(str(wf_id))
    try:
        qparams: dict[str, Any] = {"limit": 101}
        if wf_id:
            qparams["workflowId"] = str(wf_id)
        if status:
            qparams["status"] = str(status)
        if before_iso:
            # Supported on many n8n builds; if rejected, surface error.
            qparams["startedBefore"] = str(before_iso)
        raw = _n8n_request("GET", "/api/v1/executions", params=qparams)
        items = raw.get("data") if isinstance(raw, dict) else raw
        ids: list[str] = []
        for ex in items or []:
            if isinstance(ex, dict) and ex.get("id") is not None:
                ids.append(str(ex["id"]))
        if len(ids) > _LIMIT_CAP:
            preview = ids[:_LIMIT_CAP]
            out = {
                "ok": False,
                "would_delete_count": len(ids),
                "preview_execution_ids": preview,
                "note": "More than 100 matches; narrow filter or call multiple times.",
            }
            _audit_n8n(
                tool="n8n_bulk_delete_executions",
                caller=caller,
                params=params,
                target_id=",".join(ids[:5]) + ("..." if len(ids) > 5 else ""),
                result="failure",
                error=">100 executions matched; refusing delete",
            )
            return _json_response(out)
        deleted: list[str] = []
        for eid in ids:
            _n8n_request("DELETE", f"/api/v1/executions/{eid}")
            deleted.append(eid)
        out = {"ok": True, "deleted_count": len(deleted), "execution_ids": deleted}
        _audit_n8n(
            tool="n8n_bulk_delete_executions",
            caller=caller,
            params=params,
            target_id=",".join(deleted[:5]) if deleted else "",
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        res = "rejected_by_allowlist" if "WORKFLOW_ALLOWLIST" in str(exc) else "failure"
        _audit_n8n(
            tool="n8n_bulk_delete_executions",
            caller=caller,
            params=params,
            target_id="",
            result=res,
            error=str(exc),
        )
        raise


def _variable_id_for_key(key: str) -> str:
    raw = _n8n_request("GET", "/api/v1/variables", params={"limit": 200})
    data = raw.get("data") if isinstance(raw, dict) else raw
    for v in data or []:
        if isinstance(v, dict) and v.get("key") == key:
            vid = v.get("id")
            if vid is not None:
                return str(vid)
    raise stdio_mcp.McpError(f"n8n_write: variable not found: {key!r}", -32000)


def n8n_create_variable(key: str, value: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"key": key, "value": value}
    try:
        out = _n8n_request("POST", "/api/v1/variables", json_body={"key": key, "value": value})
        _audit_n8n(
            tool="n8n_create_variable",
            caller=caller,
            params=params,
            target_id=key,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_create_variable",
            caller=caller,
            params=params,
            target_id=key,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_update_variable(key: str, value: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"key": key, "value": value}
    try:
        vid = _variable_id_for_key(key)
        _n8n_request(
            "PUT",
            f"/api/v1/variables/{vid}",
            json_body={"key": key, "value": value},
        )
        _audit_n8n(
            tool="n8n_update_variable",
            caller=caller,
            params=params,
            target_id=key,
            result="success",
            error=None,
        )
        return _json_response({"ok": True, "id": vid, "key": key})
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_update_variable",
            caller=caller,
            params=params,
            target_id=key,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_delete_variable(key: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"key": key}
    try:
        vid = _variable_id_for_key(key)
        _n8n_request("DELETE", f"/api/v1/variables/{vid}")
        _audit_n8n(
            tool="n8n_delete_variable",
            caller=caller,
            params=params,
            target_id=key,
            result="success",
            error=None,
        )
        return _json_response({"ok": True, "key": key})
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_delete_variable",
            caller=caller,
            params=params,
            target_id=key,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_create_tag(name: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"name": name}
    try:
        out = _n8n_request("POST", "/api/v1/tags", json_body={"name": name})
        _audit_n8n(
            tool="n8n_create_tag",
            caller=caller,
            params=params,
            target_id=name,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_create_tag",
            caller=caller,
            params=params,
            target_id=name,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_update_tag(tag_id: str, name: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"tag_id": tag_id, "name": name}
    try:
        out = _n8n_request("PUT", f"/api/v1/tags/{tag_id}", json_body={"name": name})
        _audit_n8n(
            tool="n8n_update_tag",
            caller=caller,
            params=params,
            target_id=tag_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_update_tag",
            caller=caller,
            params=params,
            target_id=tag_id,
            result="failure",
            error=str(exc),
        )
        raise


def n8n_delete_tag(tag_id: str, ctx: Any = None) -> str:
    caller = _caller(ctx)
    params = {"tag_id": tag_id}
    try:
        out = _n8n_request("DELETE", f"/api/v1/tags/{tag_id}")
        _audit_n8n(
            tool="n8n_delete_tag",
            caller=caller,
            params=params,
            target_id=tag_id,
            result="success",
            error=None,
        )
        return _json_response(out)
    except stdio_mcp.McpError as exc:
        _audit_n8n(
            tool="n8n_delete_tag",
            caller=caller,
            params=params,
            target_id=tag_id,
            result="failure",
            error=str(exc),
        )
        raise


TOOL_FUNCTIONS = (
    n8n_activate_workflow,
    n8n_deactivate_workflow,
    n8n_execute_workflow,
    n8n_trigger_webhook,
    n8n_stop_execution,
    n8n_retry_execution,
    n8n_create_workflow,
    n8n_update_workflow,
    n8n_update_workflow_settings,
    n8n_update_workflow_tags,
    n8n_rename_workflow,
    n8n_archive_workflow,
    n8n_unarchive_workflow,
    n8n_delete_execution,
    n8n_bulk_delete_executions,
    n8n_create_variable,
    n8n_update_variable,
    n8n_delete_variable,
    n8n_create_tag,
    n8n_update_tag,
    n8n_delete_tag,
)


def register(mcp, cfg) -> int:
    global _API_KEY, _BASE_URL, _WORKFLOW_ALLOWLIST, _CFG
    if not getattr(cfg, "n8n_api_key", None):
        cfg.disabled_categories["n8n_write"] = "N8N_API_KEY missing"
        return 0
    if not getattr(cfg, "n8n_write_enabled", False):
        cfg.disabled_categories["n8n_write"] = "MIRU_N8N_WRITE_ENABLED not set"
        return 0
    if requests is None:
        cfg.disabled_categories["n8n_write"] = "'requests' library not installed"
        return 0

    _CFG = cfg
    _API_KEY = cfg.n8n_api_key
    _BASE_URL = (cfg.n8n_base_url or "").rstrip("/")
    allow = tuple(getattr(cfg, "n8n_write_workflow_allowlist", ()) or ())
    _WORKFLOW_ALLOWLIST = frozenset(allow) if allow else frozenset()

    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
