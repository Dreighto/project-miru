"""Linear write tools for orchestrator-scoped issue management (PRO-226)."""

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

_LINEAR_API = "https://api.linear.app/graphql"
_HTTP_TIMEOUT_S = 15
_BODY_MAX_CHARS = 65536

_CFG: Any = None


def _cfg() -> Any:
    if _CFG is None:
        raise RuntimeError("linear_write_tools not configured")
    return _CFG


# --- GraphQL transport ---------------------------------------------------


def _linear_gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if requests is None:
        raise stdio_mcp.McpError(
            "linear_write: 'requests' library not installed; pip install requests", -32000
        )
    token = getattr(_cfg(), "linear_api_key", None)
    if not token:
        raise stdio_mcp.McpError("linear_write: LINEAR_API_KEY not configured", -32000)
    try:
        resp = requests.post(
            _LINEAR_API,
            json={"query": query, "variables": variables},
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
                "User-Agent": "miru-mcp-gateway/0.4",
            },
            timeout=_HTTP_TIMEOUT_S,
        )
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(f"linear_write: timeout after {_HTTP_TIMEOUT_S}s", -32000) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"linear_write: transport error: {_redact.redact(str(exc))}", -32000
        ) from exc
    if resp.status_code == 401:
        raise stdio_mcp.McpError(
            "linear_write: 401 Unauthorized — LINEAR_API_KEY may be invalid or revoked",
            -32000,
        )
    if not (200 <= resp.status_code < 300):
        raise stdio_mcp.McpError(
            f"linear_write: HTTP {resp.status_code}: {_redact.redact(resp.text[:400])}",
            -32000,
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError("linear_write: non-JSON response", -32000) from exc
    if body.get("errors"):
        msgs = [str(e.get("message", e)) for e in body["errors"]]
        raise stdio_mcp.McpError(
            f"linear_write: GraphQL error: {_redact.redact('; '.join(msgs)[:500])}",
            -32000,
        )
    return body.get("data") or {}


# --- Resolution helpers -------------------------------------------------


def _resolve_label_ids(team_id: str, label_names: list[str]) -> list[str]:
    """Resolve label names to Linear label UUIDs for a team (case-insensitive)."""
    if not label_names:
        return []
    query = """
    query($teamId: String!) {
      team(id: $teamId) {
        labels { nodes { id name } }
      }
    }
    """
    data = _linear_gql(query, {"teamId": team_id})
    nodes = (((data.get("team") or {}).get("labels") or {}).get("nodes")) or []
    name_to_id = {n.get("name", "").lower(): n.get("id", "") for n in nodes if n.get("id")}
    result: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for name in label_names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        lid = name_to_id.get(key)
        if lid:
            result.append(lid)
        else:
            missing.append(name)
    if missing:
        available = [n.get("name", "") for n in nodes]
        raise stdio_mcp.McpError(
            f"linear_write: label(s) not found: {missing}; available: {available}", -32602
        )
    return result


def _resolve_team_state_id(team_id: str, state_name: str) -> str:
    """Resolve a state name to a Linear state UUID for a team (case-insensitive)."""
    query = """
    query($teamId: String!) {
      team(id: $teamId) {
        states { nodes { id name type } }
      }
    }
    """
    data = _linear_gql(query, {"teamId": team_id})
    nodes = (((data.get("team") or {}).get("states") or {}).get("nodes")) or []
    lower = state_name.strip().lower()
    matched = [s for s in nodes if s.get("name", "").lower() == lower]
    if not matched:
        available = [s.get("name", "") for s in nodes]
        raise stdio_mcp.McpError(
            f"linear_write: state {state_name!r} not found; available: {available}", -32602
        )
    return str(matched[0]["id"])


def _resolve_issue_id(identifier: str) -> str:
    """Resolve a PRO-XXX identifier or pass-through UUID to Linear internal ID.

    Bug fix (PRO-232): ``issueByIdentifier`` does not exist in Linear's GraphQL
    schema and returns HTTP 400. Use ``issue(id:)`` — the canonical singular
    lookup — which accepts both UUIDs and human-readable identifiers (e.g.
    PRO-232). UUID pass-through is unchanged; existence is verified downstream.
    """
    clean = identifier.strip()
    if len(clean) == 36 and clean.count("-") == 4:
        return clean
    query = """
    query($identifier: String!) {
      issue(id: $identifier) {
        id
      }
    }
    """
    data = _linear_gql(query, {"identifier": clean})
    node = data.get("issue") or {}
    internal_id = node.get("id")
    if not internal_id:
        raise stdio_mcp.McpError(f"linear_write: issue not found: {clean!r}", -32000)
    return str(internal_id)


def _resolve_user_id(name_or_id: str) -> str:
    """Resolve display name / email to Linear user UUID. Pass-through if UUID."""
    clean = name_or_id.strip()
    if len(clean) == 36 and clean.count("-") == 4:
        return clean
    team_id = getattr(_cfg(), "linear_team_id", None)
    if not team_id:
        raise stdio_mcp.McpError(
            "linear_write: MIRU_LINEAR_TEAM_ID required to resolve user name", -32602
        )
    query = """
    query($teamId: String!) {
      team(id: $teamId) {
        members { nodes { id name displayName email } }
      }
    }
    """
    data = _linear_gql(query, {"teamId": team_id})
    members = (((data.get("team") or {}).get("members") or {}).get("nodes")) or []
    lower = clean.lower()
    for m in members:
        if (
            str(m.get("displayName", "")).lower() == lower
            or str(m.get("name", "")).lower() == lower
            or str(m.get("email", "")).lower() == lower
        ):
            return str(m["id"])
    raise stdio_mcp.McpError(f"linear_write: user not found: {clean!r}", -32602)


# --- Security -----------------------------------------------------------


def _reject_if_secrets(text: str) -> None:
    hits = _redact.find_named_secret_substrings(text)
    if hits:
        raise stdio_mcp.McpError(
            f"linear_write: content contains known secret substring: {hits[0]}", -32000
        )


# --- Audit --------------------------------------------------------------


def _audit_linear(
    *,
    tool: str,
    caller: str,
    issue_id: str,
    operation: str,
    params: dict[str, Any],
    result: str,
    error: str | None,
) -> None:
    writes_log, _, _ = gw_audit.default_audit_paths(_cfg().fs_root)
    row = {
        "ts": gw_audit._utc_iso(),
        "tool": tool,
        "category": "linear_write",
        "caller": caller,
        "issue_id": issue_id,
        "operation": operation,
        "params": _redact.redact_dict(params),
        "result": result,
        "error": error,
    }
    gw_audit.append_jsonl_chained(writes_log, row)


# --- Tools --------------------------------------------------------------


def linear_update_issue_state(issue_id: str, state: str, ctx: Any = None) -> str:
    """Update the workflow state of a Linear issue.

    ``issue_id`` accepts the ticket identifier (e.g. 'PRO-226') or internal UUID.
    ``state`` is the exact state name (e.g. 'In Progress', 'Done', 'In Review').
    """
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    if not issue_id or not issue_id.strip():
        raise stdio_mcp.McpError("linear_write: issue_id is required", -32602)
    if not state or not state.strip():
        raise stdio_mcp.McpError("linear_write: state is required", -32602)
    try:
        internal_id = _resolve_issue_id(issue_id.strip())
        q_states = """
        query($id: String!) {
          issue(id: $id) {
            team {
              states { nodes { id name type } }
            }
          }
        }
        """
        data = _linear_gql(q_states, {"id": internal_id})
        issue_obj = data.get("issue") or {}
        state_nodes = (((issue_obj.get("team") or {}).get("states") or {}).get("nodes")) or []
        matched = [s for s in state_nodes if s.get("name", "").lower() == state.strip().lower()]
        if not matched:
            available = [s.get("name", "") for s in state_nodes]
            raise stdio_mcp.McpError(
                f"linear_write: state {state!r} not found; available: {available}", -32602
            )
        state_id = matched[0]["id"]
        mutation = """
        mutation($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) {
            success
            issue { id identifier title state { name type } }
          }
        }
        """
        data2 = _linear_gql(mutation, {"id": internal_id, "stateId": state_id})
        update = data2.get("issueUpdate") or {}
        if not update.get("success"):
            raise stdio_mcp.McpError("linear_write: issueUpdate returned success=false", -32000)
        updated = update.get("issue") or {}
        payload = {
            "ok": True,
            "identifier": updated.get("identifier", issue_id),
            "title": updated.get("title", ""),
            "state": (updated.get("state") or {}).get("name", state),
        }
        _audit_linear(
            tool="linear_update_issue_state",
            caller=caller,
            issue_id=issue_id,
            operation="update_state",
            params={"state": state},
            result="success",
            error=None,
        )
        return json.dumps(_redact.redact_dict(payload), indent=2)
    except stdio_mcp.McpError as exc:
        _audit_linear(
            tool="linear_update_issue_state",
            caller=caller,
            issue_id=issue_id,
            operation="update_state",
            params={"state": state},
            result="failure",
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_linear(
            tool="linear_update_issue_state",
            caller=caller,
            issue_id=issue_id,
            operation="update_state",
            params={"state": state},
            result="failure",
            error=repr(exc),
        )
        raise stdio_mcp.McpError(f"linear_write: {exc!r}", -32000) from exc


def linear_create_issue(
    title: str,
    description: str | None = None,
    team_id: str | None = None,
    project_id: str | None = None,
    priority: int | None = None,
    parent_id: str | None = None,
    label_names: list[str] | None = None,
    initial_state: str | None = None,
    ctx: Any = None,
) -> str:
    """Create a new Linear issue.

    ``title`` is required. ``team_id`` defaults to MIRU_LINEAR_TEAM_ID.
    ``project_id`` is required — every ticket must belong to a project per
    the CLAUDE.md hard rule (tickets without a project are invisible to the
    project-based workflow). See CLAUDE.md for the canonical project ID table.
    ``priority``: 0=None, 1=Urgent, 2=High, 3=Normal, 4=Low.
    ``parent_id``: identifier (e.g. 'PRO-226') or UUID of a parent issue.
    ``label_names``: list of label names to attach (e.g. ``['Bug', 'High Priority']``).
    ``initial_state``: workflow state name to set at creation (e.g. 'In Progress').
    """
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    if not title or not title.strip():
        raise stdio_mcp.McpError("linear_write: title is required", -32602)
    if not project_id or not project_id.strip():
        raise stdio_mcp.McpError(
            "linear_write: project_id is required — every ticket must belong to a project "
            "(CLAUDE.md hard rule). See CLAUDE.md for the canonical project ID table.",
            -32602,
        )
    resolved_team_id = (team_id or "").strip() or getattr(_cfg(), "linear_team_id", None)
    if not resolved_team_id:
        raise stdio_mcp.McpError(
            "linear_write: team_id required (or set MIRU_LINEAR_TEAM_ID)", -32602
        )
    _reject_if_secrets(title + " " + (description or ""))
    inp: dict[str, Any] = {
        "teamId": resolved_team_id,
        "projectId": project_id.strip(),
        "title": title.strip(),
    }
    if description:
        inp["description"] = description
    if priority is not None:
        try:
            p = int(priority)
        except (TypeError, ValueError):
            p = 0
        inp["priority"] = max(0, min(4, p))
    if parent_id:
        try:
            inp["parentId"] = _resolve_issue_id(parent_id.strip())
        except stdio_mcp.McpError as exc:
            raise stdio_mcp.McpError(
                f"linear_write: parent_id {parent_id!r} could not be resolved: {exc}", -32602
            ) from exc
    mutation = """
    mutation($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id identifier title url state { name } labels { nodes { name } } }
      }
    }
    """
    try:
        if label_names is not None:
            if not isinstance(label_names, list) or any(
                not isinstance(n, str) or not n.strip() for n in label_names
            ):
                raise stdio_mcp.McpError(
                    "linear_write: label_names must be a list of non-empty strings", -32602
                )
            cleaned_labels = [n.strip() for n in label_names]
            if cleaned_labels:
                inp["labelIds"] = _resolve_label_ids(resolved_team_id, cleaned_labels)
        if initial_state is not None:
            if not isinstance(initial_state, str) or not initial_state.strip():
                raise stdio_mcp.McpError(
                    "linear_write: initial_state must be a non-empty string", -32602
                )
            inp["stateId"] = _resolve_team_state_id(resolved_team_id, initial_state.strip())
        data = _linear_gql(mutation, {"input": inp})
        created = data.get("issueCreate") or {}
        if not created.get("success"):
            raise stdio_mcp.McpError("linear_write: issueCreate returned success=false", -32000)
        issue = created.get("issue") or {}
        label_names_out = [
            lbl.get("name", "")
            for lbl in ((issue.get("labels") or {}).get("nodes") or [])
            if isinstance(lbl, dict)
        ]
        payload = {
            "ok": True,
            "identifier": issue.get("identifier", ""),
            "id": issue.get("id", ""),
            "title": issue.get("title", title),
            "url": issue.get("url", ""),
            "state": (issue.get("state") or {}).get("name", ""),
            "labels": label_names_out,
        }
        _audit_linear(
            tool="linear_create_issue",
            caller=caller,
            issue_id=issue.get("identifier", ""),
            operation="create",
            params={
                "title": title,
                "team_id": resolved_team_id,
                "priority": priority,
                "label_names": label_names,
                "initial_state": initial_state,
            },
            result="success",
            error=None,
        )
        return json.dumps(_redact.redact_dict(payload), indent=2)
    except stdio_mcp.McpError as exc:
        _audit_linear(
            tool="linear_create_issue",
            caller=caller,
            issue_id="",
            operation="create",
            params={"title": title, "team_id": resolved_team_id},
            result="failure",
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_linear(
            tool="linear_create_issue",
            caller=caller,
            issue_id="",
            operation="create",
            params={"title": title},
            result="failure",
            error=repr(exc),
        )
        raise stdio_mcp.McpError(f"linear_write: {exc!r}", -32000) from exc


def linear_add_comment(issue_id: str, body: str, ctx: Any = None) -> str:
    """Add a comment to a Linear issue.

    ``issue_id`` accepts identifier (e.g. 'PRO-226') or UUID.
    ``body`` is plain text or Markdown; max 65536 chars.
    """
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    if not issue_id or not issue_id.strip():
        raise stdio_mcp.McpError("linear_write: issue_id is required", -32602)
    if not body or not body.strip():
        raise stdio_mcp.McpError("linear_write: body is required", -32602)
    if len(body) > _BODY_MAX_CHARS:
        raise stdio_mcp.McpError(f"linear_write: body exceeds {_BODY_MAX_CHARS} chars", -32602)
    _reject_if_secrets(body)
    try:
        internal_id = _resolve_issue_id(issue_id.strip())
        mutation = """
        mutation($issueId: String!, $body: String!) {
          commentCreate(input: { issueId: $issueId, body: $body }) {
            success
            comment { id createdAt }
          }
        }
        """
        data = _linear_gql(mutation, {"issueId": internal_id, "body": body})
        created = data.get("commentCreate") or {}
        if not created.get("success"):
            raise stdio_mcp.McpError("linear_write: commentCreate returned success=false", -32000)
        comment = created.get("comment") or {}
        payload = {
            "ok": True,
            "issue_id": issue_id,
            "comment_id": comment.get("id", ""),
            "created_at": comment.get("createdAt", ""),
        }
        _audit_linear(
            tool="linear_add_comment",
            caller=caller,
            issue_id=issue_id,
            operation="comment",
            params={"body_length": len(body)},
            result="success",
            error=None,
        )
        return json.dumps(_redact.redact_dict(payload), indent=2)
    except stdio_mcp.McpError as exc:
        _audit_linear(
            tool="linear_add_comment",
            caller=caller,
            issue_id=issue_id,
            operation="comment",
            params={"body_length": len(body)},
            result="failure",
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_linear(
            tool="linear_add_comment",
            caller=caller,
            issue_id=issue_id,
            operation="comment",
            params={},
            result="failure",
            error=repr(exc),
        )
        raise stdio_mcp.McpError(f"linear_write: {exc!r}", -32000) from exc


def linear_assign_issue(issue_id: str, assignee_id: str, ctx: Any = None) -> str:
    """Assign a Linear issue to a user.

    ``issue_id``: identifier (e.g. 'PRO-226') or UUID.
    ``assignee_id``: Linear user UUID, display name, or email.
    """
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    if not issue_id or not issue_id.strip():
        raise stdio_mcp.McpError("linear_write: issue_id is required", -32602)
    if not assignee_id or not assignee_id.strip():
        raise stdio_mcp.McpError("linear_write: assignee_id is required", -32602)
    try:
        internal_id = _resolve_issue_id(issue_id.strip())
        resolved_assignee = _resolve_user_id(assignee_id.strip())
        mutation = """
        mutation($id: String!, $assigneeId: String!) {
          issueUpdate(id: $id, input: { assigneeId: $assigneeId }) {
            success
            issue { id identifier assignee { id name displayName } }
          }
        }
        """
        data = _linear_gql(mutation, {"id": internal_id, "assigneeId": resolved_assignee})
        update = data.get("issueUpdate") or {}
        if not update.get("success"):
            raise stdio_mcp.McpError("linear_write: issueUpdate returned success=false", -32000)
        issue = update.get("issue") or {}
        assignee = issue.get("assignee") or {}
        payload = {
            "ok": True,
            "identifier": issue.get("identifier", issue_id),
            "assignee": assignee.get("displayName") or assignee.get("name", assignee_id),
            "assignee_id": assignee.get("id", resolved_assignee),
        }
        _audit_linear(
            tool="linear_assign_issue",
            caller=caller,
            issue_id=issue_id,
            operation="assign",
            params={"assignee_id": assignee_id},
            result="success",
            error=None,
        )
        return json.dumps(_redact.redact_dict(payload), indent=2)
    except stdio_mcp.McpError as exc:
        _audit_linear(
            tool="linear_assign_issue",
            caller=caller,
            issue_id=issue_id,
            operation="assign",
            params={"assignee_id": assignee_id},
            result="failure",
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_linear(
            tool="linear_assign_issue",
            caller=caller,
            issue_id=issue_id,
            operation="assign",
            params={"assignee_id": assignee_id},
            result="failure",
            error=repr(exc),
        )
        raise stdio_mcp.McpError(f"linear_write: {exc!r}", -32000) from exc


def linear_list_labels(team_id: str | None = None, ctx: Any = None) -> str:
    """List all issue labels for a Linear team.

    ``team_id`` defaults to MIRU_LINEAR_TEAM_ID. Returns a JSON array where
    each element has ``id``, ``name``, and ``color``. Use the ``name`` values
    from this response as ``label_names`` inputs to ``linear_create_issue``.
    """
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    resolved_team_id = (team_id or "").strip() or getattr(_cfg(), "linear_team_id", None)
    if not resolved_team_id:
        raise stdio_mcp.McpError(
            "linear_write: team_id required (or set MIRU_LINEAR_TEAM_ID)", -32602
        )
    try:
        query = """
        query($teamId: String!) {
          team(id: $teamId) {
            labels { nodes { id name color } }
          }
        }
        """
        data = _linear_gql(query, {"teamId": resolved_team_id})
        nodes = (((data.get("team") or {}).get("labels") or {}).get("nodes")) or []
        payload = [
            {"id": n.get("id", ""), "name": n.get("name", ""), "color": n.get("color", "")}
            for n in nodes
        ]
        _audit_linear(
            tool="linear_list_labels",
            caller=caller,
            issue_id="",
            operation="list_labels",
            params={"team_id": resolved_team_id},
            result="success",
            error=None,
        )
        return json.dumps(payload, indent=2)
    except stdio_mcp.McpError as exc:
        _audit_linear(
            tool="linear_list_labels",
            caller=caller,
            issue_id="",
            operation="list_labels",
            params={"team_id": resolved_team_id},
            result="failure",
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_linear(
            tool="linear_list_labels",
            caller=caller,
            issue_id="",
            operation="list_labels",
            params={},
            result="failure",
            error=repr(exc),
        )
        raise stdio_mcp.McpError(f"linear_write: {exc!r}", -32000) from exc


_DESC_TRUNCATE = 500


def linear_get_issue(issue_id: str, ctx: Any = None) -> str:
    """Return full metadata for a Linear issue (PRO-232).

    ``issue_id`` accepts a human-readable identifier (e.g. 'PRO-232') or the
    internal UUID. Returns JSON with id, identifier, title, description
    (truncated to 500 chars), state, labels, assignee, priority, and url.
    """
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    if not issue_id or not issue_id.strip():
        raise stdio_mcp.McpError("linear_write: issue_id is required", -32602)
    try:
        internal_id = _resolve_issue_id(issue_id.strip())
        query = """
        query get_issue($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            description
            state { name type }
            labels { nodes { name } }
            assignee { id name displayName }
            priority
            url
          }
        }
        """
        data = _linear_gql(query, {"id": internal_id})
        node = data.get("issue") or {}
        if not node:
            raise stdio_mcp.McpError(f"linear_write: issue not found for id: {internal_id}", -32000)

        desc = node.get("description") or ""
        if len(desc) > _DESC_TRUNCATE:
            desc = desc[:_DESC_TRUNCATE] + "\u2026"

        label_names = [
            lbl.get("name", "")
            for lbl in ((node.get("labels") or {}).get("nodes") or [])
            if isinstance(lbl, dict)
        ]

        payload: dict[str, Any] = {
            "id": node.get("id"),
            "identifier": node.get("identifier"),
            "title": node.get("title"),
            "description": desc,
            "state": node.get("state") or {},
            "labels": label_names,
            "assignee": node.get("assignee") or None,
            "priority": node.get("priority"),
            "url": node.get("url"),
        }
        _audit_linear(
            tool="linear_get_issue",
            caller=caller,
            issue_id=issue_id,
            operation="get",
            params={"issue_id": issue_id},
            result="success",
            error=None,
        )
        return json.dumps(_redact.redact_dict(payload), indent=2)
    except stdio_mcp.McpError as exc:
        _audit_linear(
            tool="linear_get_issue",
            caller=caller,
            issue_id=issue_id,
            operation="get",
            params={"issue_id": issue_id},
            result="failure",
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_linear(
            tool="linear_get_issue",
            caller=caller,
            issue_id=issue_id,
            operation="get",
            params={"issue_id": issue_id},
            result="failure",
            error=repr(exc),
        )
        raise stdio_mcp.McpError(f"linear_write: {exc!r}", -32000) from exc


TOOL_FUNCTIONS = (
    linear_update_issue_state,
    linear_create_issue,
    linear_add_comment,
    linear_assign_issue,
    linear_get_issue,
    linear_list_labels,
)


def register(mcp, cfg) -> int:
    """Register linear_write_* tools iff MIRU_LINEAR_WRITE_ENABLED."""
    global _CFG
    if not getattr(cfg, "linear_write_enabled", False):
        cfg.disabled_categories["linear_write"] = "MIRU_LINEAR_WRITE_ENABLED not set"
        return 0
    if not getattr(cfg, "linear_api_key", None):
        cfg.disabled_categories["linear_write"] = "LINEAR_API_KEY missing"
        return 0
    if requests is None:
        cfg.disabled_categories["linear_write"] = "'requests' library not installed"
        return 0
    _CFG = cfg
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
