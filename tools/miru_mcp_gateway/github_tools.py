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
import os
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
except ImportError:
    requests = None  # type: ignore


_API_BASE = "https://api.github.com"
_HTTP_TIMEOUT_S = 10
_LIMIT_HARD_CAP = 100
_FILE_HARD_CAP_BYTES = 256 * 1024
_BODY_SUMMARY_BYTES = 1500


# --- Module-level state populated at register() ------------------------

_TOKEN: str | None = None
_WRITE_TOKEN: str | None = None
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
        if p in (candidate_a, candidate_b):
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
        raise stdio_mcp.McpError("github: GITHUB_TOKEN_READ not configured", -32000)

    url = f"{_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {_TOKEN}",
        "User-Agent": "miru-mcp-gateway/0.2",
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT_S)
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
        raise stdio_mcp.McpError(f"github: 404 Not Found: {path}", -32000)
    if not (200 <= resp.status_code < 300):
        body_preview = _redact.redact(resp.text[:_BODY_SUMMARY_BYTES])
        raise stdio_mcp.McpError(
            f"github: HTTP {resp.status_code} on {path}: {body_preview}",
            -32000,
        )

    try:
        return resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError(f"github: non-JSON response on {path}", -32000) from exc


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
                "author": author_obj.get("login") or (commit.get("author") or {}).get("name", ""),
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


def github_search_repo_files(owner: str, repo: str, query: str, limit: int = 30) -> str:
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
    raw = _gh_get("/search/code", params={"q": full_q, "per_page": n})
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


def github_read_file(owner: str, repo: str, path: str, ref: str | None = None) -> str:
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
        raise stdio_mcp.McpError(f"github: path is a directory, not a file: {path}", -32000)
    encoding = obj.get("encoding")
    size = int(obj.get("size", 0))
    if size > _FILE_HARD_CAP_BYTES:
        raise stdio_mcp.McpError(
            f"github: file too large ({size} > {_FILE_HARD_CAP_BYTES} bytes): {path}",
            -32000,
        )
    if encoding != "base64":
        raise stdio_mcp.McpError(f"github: unexpected encoding {encoding!r} for {path}", -32000)
    raw = base64.b64decode(obj.get("content", "") or "", validate=False)
    text = raw.decode("utf-8", errors="replace")
    return _redact.redact(text)


# --- PRO-131: PR review surface ----------------------------------------

_GENERATED_FILENAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Gemfile.lock",
        "go.sum",
        "Cargo.lock",
        "poetry.lock",
        "composer.lock",
    }
)


def _gh_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if requests is None:
        raise stdio_mcp.McpError(
            "github: 'requests' library not installed; pip install requests",
            -32000,
        )
    if not _TOKEN:
        raise stdio_mcp.McpError("github: GITHUB_TOKEN_READ not configured", -32000)
    try:
        resp = requests.post(
            "https://api.github.com/graphql",
            json={"query": query, "variables": variables},
            headers={
                "Authorization": f"Bearer {_TOKEN}",
                "Content-Type": "application/json",
                "User-Agent": "miru-mcp-gateway/0.4",
            },
            timeout=_HTTP_TIMEOUT_S,
        )
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"github: graphql transport: {_redact.redact(str(exc))}", -32000
        ) from exc
    if resp.status_code != 200:
        raise stdio_mcp.McpError(
            f"github: graphql HTTP {resp.status_code}: {_redact.redact(resp.text[:400])}",
            -32000,
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError("github: graphql non-JSON", -32000) from exc
    if body.get("errors"):
        raise stdio_mcp.McpError(
            f"github: graphql errors: {_redact.redact(str(body.get('errors'))[:500])}",
            -32000,
        )
    return body.get("data") or {}


def _pr_review_thread_resolution(owner: str, repo: str, number: int) -> dict[str, bool]:
    """Map REST review comment id (str) -> thread isResolved (GraphQL)."""
    q = """
    query ($owner: String!, $name: String!, $pr: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $pr) {
          reviewThreads(first: 100) {
            nodes {
              isResolved
              comments(first: 50) {
                nodes { databaseId }
              }
            }
          }
        }
      }
    }
    """
    try:
        data = _gh_graphql(q, {"owner": owner, "name": repo, "pr": int(number)})
    except stdio_mcp.McpError:
        return {}
    repo_node = (data.get("repository") or {}).get("pullRequest") or {}
    threads = ((repo_node.get("reviewThreads") or {}).get("nodes")) or []
    out: dict[str, bool] = {}
    for th in threads:
        if not isinstance(th, dict):
            continue
        resolved = bool(th.get("isResolved"))
        for c in ((th.get("comments") or {}).get("nodes")) or []:
            if isinstance(c, dict) and c.get("databaseId") is not None:
                out[str(c["databaseId"])] = resolved
    return out


def _gh_get_all_pages(path: str, params_base: dict[str, Any]) -> list[Any]:
    """Paginate GitHub REST GET using per_page=100 and page=N."""
    all_items: list[Any] = []
    page = 1
    while True:
        params = {**params_base, "per_page": 100, "page": page}
        chunk = _gh_get(path, params=params)
        if not isinstance(chunk, list):
            break
        if not chunk:
            break
        all_items.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
        if page > 50:
            break
    return all_items


def _should_skip_generated_file(filename: str, file_obj: dict[str, Any]) -> bool:
    base = (filename or "").split("/")[-1].lower()
    return base in {x.lower() for x in _GENERATED_FILENAMES} or (
        file_obj.get("patch") is None and int(file_obj.get("changes", 0) or 0) > 5000
    )


def github_get_pr_diff(owner: str, repo: str, number: int, max_lines: int = 2000) -> str:
    """Structured per-file diff for a PR (GET /pulls/{n}/files).

    Respects ``max_lines`` (default 2000, hard cap 8000) across all ``patch``
    bodies after redaction. Large/generated/binary files are listed under
    ``skipped_paths`` instead of inline patches.
    """
    _assert_repo_allowed(owner, repo)
    try:
        ml = int(max_lines)
    except (TypeError, ValueError):
        ml = 2000
    ml = max(1, min(ml, 8000))

    raw_files = _gh_get_all_pages(
        f"/repos/{owner}/{repo}/pulls/{int(number)}/files",
        {},
    )
    files_out: list[dict[str, Any]] = []
    skipped: list[str] = []
    total_lines = 0
    truncated = False
    original_line_count = 0

    for f in raw_files:
        if not isinstance(f, dict):
            continue
        fn = str(f.get("filename", ""))
        if _should_skip_generated_file(fn, f):
            skipped.append(fn)
            continue
        patch = f.get("patch")
        if patch is None:
            skipped.append(fn)
            continue
        plines = patch.count("\n") + (1 if patch else 0)
        original_line_count += plines
        patch_truncated_by_github = False
        if plines >= 999:
            patch_truncated_by_github = True
        slice_patch = patch
        add_lines = slice_patch.count("\n") + (1 if slice_patch else 0)
        if total_lines + add_lines > ml:
            remaining = ml - total_lines
            if remaining <= 0:
                truncated = True
                continue
            slice_patch = "\n".join(slice_patch.splitlines()[: max(1, remaining)])
            truncated = True
            add_lines = slice_patch.count("\n") + 1
        total_lines += add_lines
        files_out.append(
            {
                "filename": fn,
                "status": f.get("status", ""),
                "additions": int(f.get("additions", 0)),
                "deletions": int(f.get("deletions", 0)),
                "changes": int(f.get("changes", 0)),
                "patch": slice_patch,
                "patch_truncated_by_github": patch_truncated_by_github,
            }
        )
        if total_lines >= ml:
            truncated = True
            break

    payload: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "number": int(number),
        "truncated": truncated,
        "original_line_count_estimate": original_line_count,
        "max_lines_budget": ml,
        "skipped_paths": skipped,
        "files": files_out,
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


def github_list_pr_reviews(owner: str, repo: str, number: int) -> str:
    """List review submissions on a PR (id, author, state, submitted_at, body_summary)."""
    _assert_repo_allowed(owner, repo)
    raw = _gh_get_all_pages(
        f"/repos/{owner}/{repo}/pulls/{int(number)}/reviews",
        {},
    )
    out: list[dict[str, Any]] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        user = r.get("user") or {}
        out.append(
            {
                "id": r.get("id"),
                "author": user.get("login", ""),
                "user_type": user.get("type", ""),
                "state": r.get("state", ""),
                "submitted_at": r.get("submitted_at", ""),
                "body_summary": _summarize_body(r.get("body")),
            }
        )
    return json.dumps(_redact.redact_dict(out), indent=2)


def github_get_pr_review_comments(
    owner: str, repo: str, number: int, review_id: int | None = None
) -> str:
    """Line-level review comments; grouped by path. Optional ``review_id`` filter.

    Includes ``in_reply_to_id``, ``is_outdated``, ``has_suggestion``, bot hint,
    and ``thread_resolved`` when GraphQL succeeds.
    """
    _assert_repo_allowed(owner, repo)
    raw = _gh_get_all_pages(
        f"/repos/{owner}/{repo}/pulls/{int(number)}/comments",
        {},
    )
    resolved_map = _pr_review_thread_resolution(owner, repo, int(number))

    comments: list[dict[str, Any]] = []
    for c in raw:
        if not isinstance(c, dict):
            continue
        if review_id is not None and c.get("pull_request_review_id") != review_id:
            continue
        body = c.get("body") or ""
        user = c.get("user") or {}
        login = str(user.get("login", ""))
        utype = str(user.get("type", ""))
        is_bot = utype == "Bot" or login.lower().endswith("[bot]") or "bot" in login.lower()
        cid = c.get("id")
        is_outdated = c.get("line") is None and c.get("original_line") is not None
        has_sug = "```suggestion" in str(body).lower()
        thr_res = resolved_map.get(str(cid)) if cid is not None else None
        comments.append(
            {
                "id": cid,
                "path": c.get("path", ""),
                "line": c.get("line"),
                "original_line": c.get("original_line"),
                "side": c.get("side", ""),
                "position": c.get("position"),
                "user": login,
                "user_type": utype,
                "is_bot_heuristic": is_bot,
                "created_at": c.get("created_at", ""),
                "updated_at": c.get("updated_at", ""),
                "in_reply_to_id": c.get("in_reply_to_id"),
                "pull_request_review_id": c.get("pull_request_review_id"),
                "is_outdated": is_outdated,
                "has_suggestion": has_sug,
                "thread_resolved": thr_res,
                "body_summary": _summarize_body(body),
            }
        )
    comments.sort(key=lambda x: (str(x.get("path", "")), str(x.get("created_at", ""))))

    by_path: dict[str, list[dict[str, Any]]] = {}
    for row in comments:
        p = str(row.get("path", "")) or "_"
        by_path.setdefault(p, []).append(row)

    return json.dumps(_redact.redact_dict({"by_path": by_path, "flat": comments}), indent=2)


def github_get_pr_check_runs(owner: str, repo: str, number: int) -> str:
    """CI / check runs for the PR head commit (REST check-runs API)."""
    _assert_repo_allowed(owner, repo)
    pr = _gh_get(f"/repos/{owner}/{repo}/pulls/{int(number)}")
    head = pr.get("head") or {}
    sha = str(head.get("sha", ""))
    if not sha:
        raise stdio_mcp.McpError("github: could not resolve PR head sha", -32000)

    raw = _gh_get(
        f"/repos/{owner}/{repo}/commits/{sha}/check-runs",
        params={"per_page": 100},
    )
    items = (raw or {}).get("check_runs") if isinstance(raw, dict) else []
    out: list[dict[str, Any]] = []
    for run in items or []:
        if not isinstance(run, dict):
            continue
        app = run.get("app") or {}
        out.append(
            {
                "id": run.get("id"),
                "name": run.get("name", ""),
                "status": run.get("status", ""),
                "conclusion": run.get("conclusion"),
                "app_slug": app.get("slug", ""),
                "app_name": app.get("name", ""),
                "details_url": run.get("details_url", ""),
                "started_at": run.get("started_at", ""),
                "completed_at": run.get("completed_at", ""),
            }
        )
    return json.dumps(_redact.redact_dict({"head_sha": sha[:12], "check_runs": out}), indent=2)


# --- PR comment (write) ------------------------------------------------


def _gh_post(path: str, json_body: dict) -> Any:
    """POST to api.github.com using the write token."""
    if requests is None:
        raise stdio_mcp.McpError(
            "github: 'requests' library not installed; pip install requests", -32000
        )
    if not _WRITE_TOKEN:
        raise stdio_mcp.McpError(
            "github: GITHUB_TOKEN_WRITE not configured; cannot post comments", -32000
        )
    url = f"{_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {_WRITE_TOKEN}",
        "User-Agent": "miru-mcp-gateway/0.4",
    }
    try:
        resp = requests.post(url, json=json_body, headers=headers, timeout=_HTTP_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(
            f"github: timeout after {_HTTP_TIMEOUT_S}s on POST {path}", -32000
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"github: transport error on POST {path}: {_redact.redact(str(exc))}", -32000
        ) from exc
    if resp.status_code == 401:
        raise stdio_mcp.McpError(
            "github: 401 Unauthorized -- GITHUB_TOKEN_WRITE may be expired", -32000
        )
    if not (200 <= resp.status_code < 300):
        body_preview = _redact.redact(resp.text[:_BODY_SUMMARY_BYTES])
        raise stdio_mcp.McpError(
            f"github: HTTP {resp.status_code} on POST {path}: {body_preview}", -32000
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError(f"github: non-JSON response on POST {path}", -32000) from exc


def github_create_pr_comment(owner: str, repo: str, number: int, body: str) -> str:
    """Post a comment on a pull request (uses GITHUB_TOKEN_WRITE).

    Returns JSON: {id, html_url, created_at}. Body is limited to 65535 chars.
    """
    _assert_repo_allowed(owner, repo)
    if not body or not body.strip():
        raise stdio_mcp.McpError("github: comment body must not be empty", -32602)
    if len(body) > 65535:
        raise stdio_mcp.McpError("github: comment body exceeds 65535 chars", -32602)
    obj = _gh_post(f"/repos/{owner}/{repo}/issues/{int(number)}/comments", {"body": body})
    payload = {
        "id": obj.get("id"),
        "html_url": obj.get("html_url", ""),
        "created_at": obj.get("created_at", ""),
    }
    return json.dumps(_redact.redact_dict(payload), indent=2)


def _gh_put(path: str, json_body: dict) -> Any:
    """PUT to api.github.com using the write token (used for PR merge)."""
    if requests is None:
        raise stdio_mcp.McpError(
            "github: 'requests' library not installed; pip install requests", -32000
        )
    if not _WRITE_TOKEN:
        raise stdio_mcp.McpError(
            "github: GITHUB_TOKEN_WRITE not configured; cannot merge PRs", -32000
        )
    url = f"{_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {_WRITE_TOKEN}",
        "User-Agent": "miru-mcp-gateway/0.4",
    }
    try:
        resp = requests.put(url, json=json_body, headers=headers, timeout=_HTTP_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(
            f"github: timeout after {_HTTP_TIMEOUT_S}s on PUT {path}", -32000
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"github: transport error on PUT {path}: {_redact.redact(str(exc))}", -32000
        ) from exc
    if resp.status_code == 401:
        raise stdio_mcp.McpError(
            "github: 401 Unauthorized -- GITHUB_TOKEN_WRITE may be expired", -32000
        )
    if resp.status_code == 405:
        raise stdio_mcp.McpError(
            f"github: 405 on PUT {path} -- PR may not be mergeable (conflicts or status checks failing)",
            -32000,
        )
    if resp.status_code == 409:
        raise stdio_mcp.McpError(
            f"github: 409 on PUT {path} -- merge conflict; resolve conflicts before merging",
            -32000,
        )
    if not (200 <= resp.status_code < 300):
        body_preview = _redact.redact(resp.text[:_BODY_SUMMARY_BYTES])
        raise stdio_mcp.McpError(
            f"github: HTTP {resp.status_code} on PUT {path}: {body_preview}", -32000
        )
    try:
        return resp.json()
    except ValueError:
        return {}


def _gh_delete(path: str) -> int:
    """DELETE from api.github.com using the write token. Returns HTTP status code."""
    if requests is None:
        raise stdio_mcp.McpError(
            "github: 'requests' library not installed; pip install requests", -32000
        )
    if not _WRITE_TOKEN:
        raise stdio_mcp.McpError(
            "github: GITHUB_TOKEN_WRITE not configured; cannot delete branches", -32000
        )
    url = f"{_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {_WRITE_TOKEN}",
        "User-Agent": "miru-mcp-gateway/0.4",
    }
    try:
        resp = requests.delete(url, headers=headers, timeout=_HTTP_TIMEOUT_S)
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(
            f"github: timeout after {_HTTP_TIMEOUT_S}s on DELETE {path}", -32000
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"github: transport error on DELETE {path}: {_redact.redact(str(exc))}", -32000
        ) from exc
    if resp.status_code == 401:
        raise stdio_mcp.McpError(
            "github: 401 Unauthorized -- GITHUB_TOKEN_WRITE may be expired", -32000
        )
    if resp.status_code == 404:
        raise stdio_mcp.McpError(f"github: 404 Not Found: {path}", -32000)
    if resp.status_code == 422:
        body_preview = _redact.redact(resp.text[:_BODY_SUMMARY_BYTES])
        raise stdio_mcp.McpError(
            f"github: 422 Unprocessable on DELETE {path}: {body_preview}", -32000
        )
    if not (200 <= resp.status_code < 300):
        body_preview = _redact.redact(resp.text[:_BODY_SUMMARY_BYTES])
        raise stdio_mcp.McpError(
            f"github: HTTP {resp.status_code} on DELETE {path}: {body_preview}", -32000
        )
    return resp.status_code


def github_merge_pr(
    owner: str,
    repo: str,
    number: int,
    merge_method: str = "squash",
    commit_title: str | None = None,
) -> str:
    """Merge a pull request (uses GITHUB_TOKEN_WRITE).

    ``merge_method``: 'squash' (default), 'merge', or 'rebase'.
    ``commit_title``: optional override for the merge commit title.
    Returns JSON: {merged, sha, message}.
    Only valid for PRs in the CC-merge column per CLAUDE.md merge policy.
    """
    _assert_repo_allowed(owner, repo)
    valid_methods = {"squash", "merge", "rebase"}
    if merge_method not in valid_methods:
        raise stdio_mcp.McpError(
            f"github: merge_method must be one of {sorted(valid_methods)}", -32602
        )
    payload: dict[str, Any] = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title.strip()
    obj = _gh_put(f"/repos/{owner}/{repo}/pulls/{int(number)}/merge", payload)
    out = {
        "merged": bool(obj.get("merged", True)),
        "sha": obj.get("sha", ""),
        "message": obj.get("message", ""),
    }
    return json.dumps(_redact.redact_dict(out), indent=2)


def github_delete_branch(owner: str, repo: str, branch: str) -> str:
    """Delete a remote branch (uses GITHUB_TOKEN_WRITE).

    Safety guard: refuses to delete 'main', 'master', or 'develop'.
    Returns JSON: {deleted, branch}.
    """
    _assert_repo_allowed(owner, repo)
    if not branch or not branch.strip():
        raise stdio_mcp.McpError("github: branch must not be empty", -32602)
    branch = branch.strip()
    protected = {"main", "master", "develop"}
    if branch.lower() in protected:
        raise stdio_mcp.McpError(f"github: refusing to delete protected branch {branch!r}", -32000)
    status = _gh_delete(f"/repos/{owner}/{repo}/git/refs/heads/{branch}")
    out = {"deleted": status == 204, "branch": branch}
    return json.dumps(_redact.redact_dict(out), indent=2)


def github_create_issue(
    owner: str,
    repo: str,
    title: str,
    body: str | None = None,
    labels: list[str] | None = None,
) -> str:
    """Create a new GitHub issue (uses GITHUB_TOKEN_WRITE).

    ``owner``/``repo`` must be in the allowlist. ``title`` max 256 chars.
    ``body`` max 65535 chars. ``labels`` items must be 1-50 chars each.
    Both title and body are scanned for known secret substrings.
    Returns JSON: {number, url, state}.
    """
    _assert_repo_allowed(owner, repo)
    if not title or not title.strip():
        raise stdio_mcp.McpError("github: title must not be empty", -32602)
    if len(title) > 256:
        raise stdio_mcp.McpError("github: title exceeds 256 chars", -32602)
    if body and len(body) > 65535:
        raise stdio_mcp.McpError("github: body exceeds 65535 chars", -32602)

    # Secret-content guard
    check_text = title + " " + (body or "")
    hits = _redact.find_named_secret_substrings(check_text)
    if hits:
        raise stdio_mcp.McpError(
            f"github: content contains known secret substring: {hits[0]}", -32000
        )

    # Validate labels
    clean_labels: list[str] = []
    for lbl in labels or []:
        lbl = str(lbl).strip()
        if not lbl or len(lbl) > 50:
            raise stdio_mcp.McpError(f"github: label must be 1-50 chars: {lbl!r}", -32602)
        clean_labels.append(lbl)

    payload: dict[str, Any] = {"title": title.strip()}
    if body:
        payload["body"] = body
    if clean_labels:
        payload["labels"] = clean_labels

    obj = _gh_post(f"/repos/{owner}/{repo}/issues", payload)
    out = {
        "number": obj.get("number"),
        "url": obj.get("html_url", ""),
        "state": obj.get("state", "open"),
    }
    return json.dumps(_redact.redact_dict(out), indent=2)


# --- Manifest + register hook ------------------------------------------

TOOL_FUNCTIONS = (
    github_get_repo_status,
    github_list_recent_commits,
    github_get_pr,
    github_list_open_prs,
    github_get_issue,
    github_search_repo_files,
    github_read_file,
    github_get_pr_diff,
    github_list_pr_reviews,
    github_get_pr_review_comments,
    github_get_pr_check_runs,
    github_create_pr_comment,
    github_create_issue,
    github_merge_pr,
    github_delete_branch,
)


def register(mcp, cfg) -> int:
    """Register github_* tools iff token + MIRU_GITHUB_READ_ENABLED (PRO-131)."""
    global _TOKEN, _ALLOWLIST

    if not getattr(cfg, "github_token", None):
        cfg.disabled_categories["github"] = "GITHUB_TOKEN_READ missing"
        return 0
    if not getattr(cfg, "github_read_enabled", False):
        cfg.disabled_categories["github"] = "MIRU_GITHUB_READ_ENABLED not set to true"
        return 0
    if requests is None:
        cfg.disabled_categories["github"] = "'requests' library not installed"
        return 0

    global _WRITE_TOKEN
    _TOKEN = cfg.github_token
    _WRITE_TOKEN = os.environ.get("GITHUB_TOKEN_WRITE", "").strip() or None
    _ALLOWLIST = tuple(cfg.github_allowlist or ())

    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
