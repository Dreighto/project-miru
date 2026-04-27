"""PRO-134: cross-system ``activity_since`` aggregator."""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import redact as _redact  # noqa: E402

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore

_CFG: Any = None
_HTTP_TIMEOUT = 5.0
_SKIP_FS_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        "dist",
        "build",
        ".venv",
        "venv",
        ".ruff_cache",
    }
)


def _since_ts(minutes: int) -> datetime:
    cap = max(1, min(int(minutes or 30), 1440))
    return datetime.now(UTC) - timedelta(minutes=cap)


def _linear_events(since: datetime) -> list[dict[str, Any]]:
    cfg = _CFG
    if not cfg or not getattr(cfg, "linear_api_key", None):
        return []
    team = getattr(cfg, "linear_team_id", None)
    if not team:
        return []
    q = """
    query ($team: ID!, $after: DateTime!) {
      issues(
        filter: {
          team: { id: { eq: $team } }
          updatedAt: { gt: $after }
        }
        first: 50
      ) {
        nodes {
          identifier
          title
          updatedAt
          url
          state { name type }
        }
      }
    }
    """
    variables = {
        "team": team,
        "after": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    try:
        if requests is None:
            return []
        resp = requests.post(
            "https://api.linear.app/graphql",
            json={"query": q, "variables": variables},
            headers={
                "Authorization": str(cfg.linear_api_key),
                "Content-Type": "application/json",
            },
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        body = resp.json()
        if body.get("errors"):
            return []
        issues = (body.get("data") or {}).get("issues") or {}
        nodes = (issues.get("nodes")) or []
        out: list[dict[str, Any]] = []
        for n in nodes:
            if not isinstance(n, dict):
                continue
            st = n.get("state") or {}
            out.append(
                {
                    "ts": str(n.get("updatedAt", "")).replace("+00:00", "Z"),
                    "source": "linear",
                    "kind": "issue_updated",
                    "ref": n.get("identifier", ""),
                    "summary": _redact.redact(
                        f"{n.get('title', '')[:120]} — state: {st.get('name', '')}"
                    ),
                    "url": n.get("url", ""),
                }
            )
        return out
    except Exception:
        return []


def _github_allowed(owner: str, repo: str) -> bool:
    cfg = _CFG
    allow = tuple(getattr(cfg, "github_allowlist", ()) or ())
    if not allow:
        return True
    candidate = f"{owner}/{repo}".lower()
    for pattern in allow:
        p = pattern.lower()
        if p == candidate or p == f"{owner.lower()}/*":
            return True
        if fnmatch.fnmatch(candidate, p):
            return True
    return False


def _github_events(since: datetime) -> list[dict[str, Any]]:
    cfg = _CFG
    tok = getattr(cfg, "github_token", None)
    if not tok or requests is None:
        return []
    out: list[dict[str, Any]] = []
    raw_allow = os.environ.get("MIRU_ACTIVITY_GITHUB_REPOS", "").strip()
    repos: list[tuple[str, str]] = []
    if raw_allow:
        for piece in raw_allow.split(","):
            piece = piece.strip()
            if "/" in piece:
                o, r = piece.split("/", 1)
                if _github_allowed(o, r):
                    repos.append((o, r))
    else:
        for p in getattr(cfg, "github_allowlist", ()) or ():
            if "/" in p and not p.endswith("/*"):
                o, r = p.split("/", 1)
                repos.append((o, r))
    if not repos:
        return []
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {tok}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    since_s = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    for owner, repo in repos[:5]:
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{owner}/{repo}/commits",
                headers=headers,
                params={"per_page": 30},
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                continue
            for c in resp.json() or []:
                if not isinstance(c, dict):
                    continue
                commit = c.get("commit") or {}
                ts = (commit.get("author") or {}).get("date", "")
                if ts < since_s:
                    continue
                sha = str(c.get("sha", ""))[:12]
                msg = (commit.get("message") or "").splitlines()[0][:160]
                out.append(
                    {
                        "ts": ts,
                        "source": "github",
                        "kind": "commit",
                        "ref": sha,
                        "summary": _redact.redact(msg),
                        "url": c.get("html_url", ""),
                    }
                )
        except Exception:
            continue
    return out


def _n8n_events(since: datetime) -> list[dict[str, Any]]:
    cfg = _CFG
    key = getattr(cfg, "n8n_api_key", None)
    base = (getattr(cfg, "n8n_base_url", None) or "").rstrip("/")
    if not key or not base or requests is None:
        return []
    try:
        resp = requests.get(
            f"{base}/api/v1/executions",
            headers={"Accept": "application/json", "X-N8N-API-KEY": key},
            params={"limit": 80},
            timeout=_HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        raw = resp.json()
        items = raw.get("data") if isinstance(raw, dict) else raw
        out: list[dict[str, Any]] = []
        for ex in items or []:
            if not isinstance(ex, dict):
                continue
            st = str(ex.get("startedAt", ""))
            try:
                dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
                if dt < since:
                    continue
            except ValueError:
                continue
            status = str(ex.get("status", ""))
            kind = (
                "execution_error"
                if status.lower() in ("error", "crashed", "failed")
                else "execution_completed"
            )
            out.append(
                {
                    "ts": st.replace("+00:00", "Z") if "+" in st else st,
                    "source": "n8n",
                    "kind": kind,
                    "ref": str(ex.get("id", "")),
                    "summary": _redact.redact(f"workflow {ex.get('workflowId', '')} — {status}"),
                    "url": None,
                }
            )
        return out
    except Exception:
        return []


def _fs_events(since: datetime, root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        root = root.resolve()
    except OSError:
        return []
    since_ts = since.timestamp()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        parts = Path(dirpath).parts
        if any(x in _SKIP_FS_DIRS for x in parts):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _SKIP_FS_DIRS]
        for fn in filenames:
            if count >= 120:
                return out
            fp = Path(dirpath) / fn
            try:
                m = fp.stat().st_mtime
            except OSError:
                continue
            if m < since_ts:
                continue
            try:
                rel = str(fp.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(fp)
            out.append(
                {
                    "ts": datetime.fromtimestamp(m, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "source": "filesystem",
                    "kind": "file_modified",
                    "ref": rel[:500],
                    "summary": "Modified",
                    "url": None,
                }
            )
            count += 1
    return out


def activity_since(
    minutes: int = 30,
    sources: list[str] | None = None,
) -> str:
    """Unified cross-system timeline (PRO-134)."""
    cfg = _CFG
    if cfg is None:
        raise stdio_mcp.McpError("activity_since: not configured", -32000)
    since = _since_ts(minutes)
    until = datetime.now(UTC)
    srcs = sources or ["linear", "github", "n8n", "filesystem"]
    srcs = [s.strip().lower() for s in srcs if s]

    partial = False
    events: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs: list[tuple[str, Any]] = []
        if "linear" in srcs:
            futs.append(("linear", pool.submit(_linear_events, since)))
        if "github" in srcs:
            futs.append(("github", pool.submit(_github_events, since)))
        if "n8n" in srcs:
            futs.append(("n8n", pool.submit(_n8n_events, since)))
        if "filesystem" in srcs:
            futs.append(("fs", pool.submit(_fs_events, since, cfg.fs_root)))
        for _name, fut in futs:
            try:
                events.extend(fut.result(timeout=5.0))
            except FuturesTimeout:
                partial = True

    events.sort(key=lambda e: str(e.get("ts", "")))
    truncated = False
    if len(events) > 200:
        truncated = True
        events = events[-200:]

    counts = Counter(str(e.get("source", "")) for e in events)

    payload = {
        "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "until": until.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "events": events,
        "counts": dict(counts),
        "partial": partial,
        "truncated": truncated,
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


TOOL_FUNCTIONS = (activity_since,)


def register(mcp, cfg) -> int:
    global _CFG
    if not getattr(cfg, "aggregator_enabled", False):
        cfg.disabled_categories["activity"] = "MIRU_AGGREGATOR_ENABLED not set"
        return 0
    _CFG = cfg
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
