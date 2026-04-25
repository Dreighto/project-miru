"""Read-only GitHub tools.

Disabled cleanly if GITHUB_TOKEN_READ is missing. The module never reads
GITHUB_TOKEN_WRITE -- a write token isn't needed for any of the tools below
and using it would expand the blast radius for nothing.

If MIRU_GITHUB_REPO_ALLOWLIST is set in .env, every (owner, repo) tuple is
checked against it before any HTTP call. Empty/unset means allow any repo
the token can see.

All output is passed through redact() before returning. Filenames are
checked against the same Stage 1 deny list (.env, *.key, *.pem, etc.) so a
.env in a GitHub repo cannot be returned via github_read_file even if it's
public.

Every requests.* call uses timeout=10. Tool errors surface as McpError.
"""

from __future__ import annotations

import base64
import fnmatch
import json
import sys
from pathlib import Path
from typing import Any

# stdio_mcp.McpError is the error shape Stage 1 uses.
_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import fs_tools as _fs  # noqa: E402
from miru_mcp_gateway import redact as _redact  # noqa: E402

try:
    import requests  # type: ignore
except ImportError:  # noqa: BLE001
    requests = None  # type: ignore


_API_BASE = "https://api.github.com"
_HTTP_TIMEOUT_S = 10
_LIMIT_HARD_CAP = 100
_FILE_HARD_CAP_BYTES = 256 * 1024
_BODY_SUMMARY_BYTES = 1500


# --- Module-level state populated at register() ------------------------

_TOKEN: str | None = None
_ALLOWLIST: tuple[str, ...] = ()


# --- Allowlist + path-deny helpers -------------------------------------


def _assert_repo_allowed(owner: str, repo: str) -> None:
    """Raise McpError if (owner, repo) isn't allowed by config."""
    if not _ALLOWLIST:
        return  # empty allowlist == allow any
    candidate_a = f"{owner}/{repo}".lower()
    candidate_b = f"{owner}/*".lower()
    for pattern in _ALLOWLIST:
        p = pattern.lower()
        if p == candidate_a or p == candidate_b:
            return
        # fnmatch lets the operator write things like 'Dreighto/*' or
        # 'anthropics/claude-*' if they want narrower control.
        if fnmatch.fnmatch(candidate_a, p):
            return
    raise stdio_mcp.McpError(
        f"github: repo not in allowlist: {owner}/{repo}. "
        f"Set MIRU_GITHUB_REPO_ALLOWLIST in .env (comma-separated) and restart.",
        -32000,
    )


def _assert_path_allowed(path: str) -> None:
    """Refuse if a remote path matches the same deny list as fs_tools."""
    if _fs.is_denied_path_string(path):
        raise stdio_mcp.McpError(
            f"github: path matches Stage 1 filesystem deny list: {path!r}",
            -32000,
        )


# --- Thin requests wrapper ---------------------------------------------


def _gh_get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET an api.github.com URL and return parsed JSON.

    Raises McpError on transport failure, non-2xx status, or JSON parse
    failure. The body is redacted before being included in any error text.
    """
    if requests is None:
        raise stdio_mcp.McpError(
            "github: 'requests' library not installed; pip install requests",
            -32000,
        )
    if not _TOKEN:
        raise stdio_mcp.McpError(
            "github: GITHUB_TOKEN_READ not configured", -32000
        )

    url = f"{_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {_TOKEN}",
        "User-Agent": "miru-mcp-gateway/0.2",
    }
    try:
        resp = requests.get(
            url, headers=headers, params=params, timeout=_HTTP_TIMEOUT_S
        )
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(
            f"github: timeout after {_HTTP_TIMEOUT_S}s on {path}", -32000
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"github: transport error on {path}: {_redact.redact(str(exc))}", -32000
        ) from exc

    if resp.status_code == 401:
        raise stdio_mcp.McpError(
            "github: 401 Unauthorized -- GITHUB_TOKEN_READ may be expired or revoked",
            -32000,
        )
    if resp.status_code == 403:
        # Common cause: rate limit. Surface the reset hint if present.
        reset = resp.headers.get("X-RateLimit-Reset", "")
        raise stdio_mcp.McpError(
            f"github: 403 (rate limit or forbidden); reset={reset}",
            -32000,
        )
    if resp.status_code == 404:
        raise stdio_mcp.McpError(
            f"github: 404 Not Found: {path}", -32000
        )
    if not (200 <= resp.status_code < 300):
        body_preview = _redact.redact(resp.text[:_BODY_SUMMARY_BYTES])
        raise stdio_mcp.McpError(
            f"github: HTTP {resp.status_code} on {path}: {body_preview}",
            -32000,
        )

    try:
        return resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError(
            f"github: non-JSON response on {path}", -32000
        ) from exc


def _clamp(value: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(v, _LIMIT_HARD_CAP))


def _summarize_message(text: str | None, limit: int = 240) -> str:
    if not text:
        return ""
    text = text.strip()
    first_line = text.splitlines()[0] if text else ""
    if len(first_line) > limit:
        first_line = first_line[: limit - 1] + "…"
    return first_line


def _summarize_body(text: str | None) -> str:
    if not text:
        return ""
    if len(text) > _BODY_SUMMARY_BYTES:
        return text[:_BODY_SUMMARY_BYTES] + "\n…[truncated]"
    return text


# --- Tools --------------------------------------------------------------


def github_get_repo_status(owner: str, repo: str) -> str:
    """Return a quick repo-state snapshot.

    Output JSON: {default_branch, last_commit_sha, last_commit_ts,
    last_commit_author, open_pr_count, open_issue_count}.

    Cost: 3 GitHub API calls (repo, default-branch tip commit, search for
    open PR + issue counts via the search endpoint).
    """
    _assert_repo_allowed(owner, repo)

    repo_obj = _gh_get(f"/repos/{owner}/{repo}")
    default_branch = repo_obj.get("default_branch", "main")

    commits = _gh_get(
        f"/repos/{owner}/{repo}/commits",
        params={"sha": default_branch, "per_page": 1},
    )
    last_commit_sha = ""
    last_commit_ts = ""
    last_commit_author = ""
    if commits:
        c = commits[0]
        last_commit_sha = c.get("sha", "")[:12]
        commit = c.get("commit", {}) or {}
        last_commit_ts = (commit.get("author") or {}).get("date", "")
        author_obj = c.get("author") or {}
        last_commit_author = author_obj.get("login") or (
            (commit.get("author") or {}).get("name", "")
        )

    # Count open PRs and issues via search (1 call each is fine; could
    # combine but the issues endpoint mixes PRs and issues).
    pr_search = _gh_get(
        "/search/issues",
        params={"q": f"repo:{owner}/{repo} is:pr is:open", "per_page": 1},
    )
    issue_search = _gh_get(
        "/search/issues",
        params={"q": f"repo:{owner}/{repo} is:issue is:open", "per_page": 1},
    )

    payload: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "default_branch": default_branch,
        "last_commit_sha": last_commit_sha,
        "last_commit_ts": last_commit_ts,
        "last_commit_author": last_commit_author,
        "open_pr_count": int(pr_search.get("total_count", 0)),
        "open_issue_count": int(issue_search.get("total_count", 0)),
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


def github_list_recent_commits(
    owner: str, repo: str, limit: int = 20, ref: str | None = None
) -> str:
    """List recent commits on `ref` (defaults to default branch).

    `limit` is capped at 100. Returns JSON list of
    {sha, ts, author, message_first_line}.
    """
    _assert_repo_allowed(owner, repo)
    n = _clamp(limit, 20)
    params: dict[str, Any] = {"per_page": n}
    if ref:
        params["sha"] = ref
    raw = _gh_get(f"/repos/{owner}/{repo}/commits", params=params)
    out: list[dict[str, Any]] = []
    for c in raw or []:
        commit = c.get("commit") or {}
        author_obj = c.get("author") or {}
        out.append(
            {
                "sha": c.get("sha", "")[:12],
                "ts": (commit.get("author") or {}).get("date", ""),
                "author": author_obj.get("login")
                or (commit.get("author") or {}).get("name", ""),
                "message_first_line": _summarize_message(commit.get("message")),
            }
        )
    return json.dumps(_redact.redact_dict(out), indent=2)


def github_get_pr(owner: str, repo: str, number: int) -> str:
    """Get a single pull request's metadata + body summary.

    Returns JSON: {number, title, state, author, base, head, draft,
    comments, body_summary}. Body is truncated to ~1500 chars and redacted.
    """
    _assert_repo_allowed(owner, repo)
    pr = _gh_get(f"/repos/{owner}/{repo}/pulls/{int(number)}")
    payload = {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "state": pr.get("state", ""),
        "draft": bool(pr.get("draft", False)),
        "author": (pr.get("user") or {}).get("login", ""),
        "base": (pr.get("base") or {}).get("ref", ""),
        "head": (pr.get("head") or {}).get("ref", ""),
        "comments": int(pr.get("comments", 0)),
        "review_comments": int(pr.get("review_comments", 0)),
        "additions": int(pr.get("additions", 0)),
        "deletions": int(pr.get("deletions", 0)),
        "merged": bool(pr.get("merged", False)),
        "created_at": pr.get("created_at", ""),
        "updated_at": pr.get("updated_at", ""),
        "body_summary": _summarize_body(pr.get("body")),
        "url": pr.get("html_url", ""),
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


def github_list_open_prs(owner: str, repo: str, limit: int = 20) -> str:
    """List open pull requests on a repo.

    `limit` capped at 100. Returns JSON list of
    {number, title, author, ts, draft}.
    """
    _assert_repo_allowed(owner, repo)
    n = _clamp(limit, 20)
    raw = _gh_get(
        f"/repos/{owner}/{repo}/pulls",
        params={"state": "open", "per_page": n, "sort": "updated", "direction": "desc"},
    )
    out: list[dict[str, Any]] = []
    for pr in raw or []:
        out.append(
            {
                "number": pr.get("number"),
                "title": pr.get("title", ""),
                "author": (pr.get("user") or {}).get("login", ""),
                "ts": pr.get("updated_at", ""),
                "draft": bool(pr.get("draft", False)),
                "url": pr.get("html_url", ""),
            }
        )
    return json.dumps(_redact.redact_dict(out), indent=2)


def github_get_issue(owner: str, repo: str, number: int) -> str:
    """Get a single issue's metadata + body summary.

    Note: the GitHub issues endpoint also returns PRs as issues; this tool
    refuses if the result is a PR (use github_get_pr instead).
    """
    _assert_repo_allowed(owner, repo)
    obj = _gh_get(f"/repos/{owner}/{repo}/issues/{int(number)}")
    if "pull_request" in obj:
        raise stdio_mcp.McpError(
            f"github: #{number} is a pull request -- use github_get_pr instead",
            -32000,
        )
    payload = {
        "number": obj.get("number"),
        "title": obj.get("title", ""),
        "state": obj.get("state", ""),
        "author": (obj.get("user") or {}).get("login", ""),
        "ts": obj.get("updated_at", ""),
        "labels": [lbl.get("name", "") for lbl in obj.get("labels", []) or []],
        "comments": int(obj.get("comments", 0)),
        "body_summary": _summarize_body(obj.get("body")),
        "url": obj.get("html_url", ""),
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


def github_search_repo_files(
    owner: str, repo: str, query: str, limit: int = 30
) -> str:
    """Search filenames within a single repo using GitHub code search.

    `query` is GitHub code search syntax (e.g. 'foo extension:py'). The
    repo qualifier is added automatically. Results matching the Stage 1
    filename deny list (.env, *.key, etc.) are filtered out before return.

    `limit` capped at 100. Returns JSON list of {path, score}.
    """
    _assert_repo_allowed(owner, repo)
    if not query or not query.strip():
        raise stdio_mcp.McpError("github: query must not be empty", -32602)
    n = _clamp(limit, 30)
    full_q = f"repo:{owner}/{repo} {query.strip()}"
    raw = _gh_get(
        "/search/code", params={"q": full_q, "per_page": n}
    )
    out: list[dict[str, Any]] = []
    for item in (raw or {}).get("items", []) or []:
        path = item.get("path", "")
        if not path or _fs.is_denied_path_string(path):
            continue
        out.append(
            {
                "path": path,
                "score": float(item.get("score", 0.0)),
                "url": item.get("html_url", ""),
            }
        )
    return json.dumps(_redact.redact_dict(out), indent=2)


def github_read_file(
    owner: str, repo: str, path: str, ref: str | None = None
) -> str:
    """Read a single text file from a GitHub repo.

    `path` must NOT match the Stage 1 deny list (.env, *.key, *.pem,
    secrets.*, id_rsa*, id_ed25519*, anything under .git/ or logs/).
    Files larger than 256 KB are refused. Output is redacted.
    """
    _assert_repo_allowed(owner, repo)
    _assert_path_allowed(path)
    params: dict[str, Any] = {}
    if ref:
        params["ref"] = ref
    obj = _gh_get(f"/repos/{owner}/{repo}/contents/{path}", params=params)
    if isinstance(obj, list):
        raise stdio_mcp.McpError(
            f"github: path is a directory, not a file: {path}", -32000
        )
    encoding = obj.get("encoding")
    size = int(obj.get("size", 0))
    if size > _FILE_HARD_CAP_BYTES:
        raise stdio_mcp.McpError(
            f"github: file too large ({size} > {_FILE_HARD_CAP_BYTES} bytes): {path}",
            -32000,
        )
    if encoding != "base64":
        raise stdio_mcp.McpError(
            f"github: unexpected encoding {encoding!r} for {path}", -32000
        )
    raw = base64.b64decode(obj.get("content", "") or "", validate=False)
    text = raw.decode("utf-8", errors="replace")
    return _redact.redact(text)


# --- Manifest + register hook ------------------------------------------

TOOL_FUNCTIONS = (
    github_get_repo_status,
    github_list_recent_commits,
    github_get_pr,
    github_list_open_prs,
    github_get_issue,
    github_search_repo_files,
    github_read_file,
)


def register(mcp, cfg) -> int:
    """Register github_* tools iff GITHUB_TOKEN_READ is set.

    Records a reason in cfg.disabled_categories['github'] and returns 0
    when disabled. Stage 1 still loads.
    """
    global _TOKEN, _ALLOWLIST

    if not getattr(cfg, "github_token", None):
        cfg.disabled_categories["github"] = "GITHUB_TOKEN_READ missing"
        return 0
    if requests is None:
        cfg.disabled_categories["github"] = "'requests' library not installed"
        return 0

    _TOKEN = cfg.github_token
    _ALLOWLIST = tuple(cfg.github_allowlist or ())

    for func in TOOL_FUNCTIONS:
        mcp.tool(func)
    return len(TOOL_FUNCTIONS)
