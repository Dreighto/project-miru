"""Git write tools for orchestrator-scoped commits (PRO-187)."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import audit as gw_audit  # noqa: E402
from miru_mcp_gateway import fs_tools as _fs  # noqa: E402
from miru_mcp_gateway import redact as _redact  # noqa: E402

_CFG: Any = None
_GIT_TIMEOUT_S = 120
_OUTPUT_PREVIEW_CHARS = 6000

_ALLOWED_EXACT = frozenset(
    {
        "CLAUDE.md",
        "PROJECT_MIRU_INSTRUCTIONS.md",
        "GEMINI.md",
        "AGENTS.md",
        "CURSOR.md",
        "CODEX.md",
        "COPILOT.md",
    }
)
_ALLOWED_PREFIXES = ("docs/", "skills/")
_ALLOWED_SERVICE_MD_PREFIXES = (
    "tools/",
    "services/",
    "pm/",
    "miru_ai/",
    "dispatcher/",
    "docker/",
    "windows/",
    ".claude/",
    ".cursor/",
)
_DENIED_EXACT = frozenset(
    {
        "data/cc_completion_log.jsonl",
        "data/routing_history.jsonl",
        "data/pending_callbacks.jsonl",
        "data/dispatch_dlq.jsonl",
        "data/cc_heartbeat_log.jsonl",
    }
)
_DENIED_WORKER_RULE_FILES: frozenset[str] = frozenset()
_DENIED_DB_NAMES = frozenset({"card_catalog.db", "miru_memory.db"})
_GLOB_CHARS = frozenset("*?[")


@dataclass(frozen=True)
class _GitRun:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def combined(self) -> str:
        text = "\n".join(part for part in (self.stdout, self.stderr) if part)
        return text[-_OUTPUT_PREVIEW_CHARS:]


def _cfg() -> Any:
    if _CFG is None:
        raise RuntimeError("git_tools not configured")
    return _CFG


def _repo_rel_posix(path: Path) -> str:
    return path.resolve().relative_to(stdio_mcp.ROOT).as_posix()


def _json_response(payload: dict[str, Any]) -> str:
    return json.dumps(_redact.redact_dict(payload), indent=2)


def _caller(ctx: Any) -> str:
    return gw_audit.caller_from_fastmcp_context(ctx)


def _audit_git(
    *,
    tool: str,
    caller: str,
    paths: list[str],
    message: str,
    branch: str,
    hygiene: dict[str, Any] | None,
    commit_sha: str | None,
    push: dict[str, Any] | None,
    result: str,
    error: str | None,
) -> None:
    writes_log, _, _ = gw_audit.default_audit_paths(_cfg().fs_root)
    row = {
        "ts": gw_audit._utc_iso(),
        "tool": tool,
        "category": "git_write",
        "caller": caller,
        "paths": paths,
        "message": message,
        "branch": branch,
        "hygiene": hygiene,
        "commit_sha": commit_sha,
        "push": push,
        "result": result,
        "error": error,
    }
    gw_audit.append_jsonl_chained(writes_log, _redact.redact_dict(row))


def _run_git(args: list[str], *, timeout: int = _GIT_TIMEOUT_S) -> _GitRun:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(stdio_mcp.ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise stdio_mcp.McpError(f"git_write: git {' '.join(args)} timed out", -32000) from exc
    return _GitRun(
        args=["git", *args],
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def _run_pre_commit(paths: list[str]) -> _GitRun:
    try:
        proc = subprocess.run(
            ["pre-commit", "run", "--files", *paths],
            cwd=str(stdio_mcp.ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError as exc:
        raise stdio_mcp.McpError("git_write: pre-commit executable not found", -32000) from exc
    except subprocess.TimeoutExpired as exc:
        raise stdio_mcp.McpError("git_write: pre-commit timed out", -32000) from exc
    return _GitRun(
        args=["pre-commit", "run", "--files", *paths],
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def _reject_path_shape(raw_path: str) -> None:
    if not raw_path or not raw_path.strip():
        raise stdio_mcp.McpError("git_write: paths must not contain blanks", -32602)
    raw = raw_path.strip()
    if raw in (".", "./", "*") or raw.startswith("-") or any(ch in raw for ch in _GLOB_CHARS):
        raise stdio_mcp.McpError(f"git_write: path must be explicit, not a glob: {raw}", -32602)


def _assert_rel_allowed(rel: str) -> None:
    lower = rel.lower()
    name = rel.rsplit("/", 1)[-1]
    if _fs.is_denied_path_string(rel):
        raise stdio_mcp.McpError(f"git_write: path rejected by filesystem deny list: {rel}", -32000)
    if rel in _DENIED_EXACT:
        raise stdio_mcp.McpError(f"git_write: append-only data file denied: {rel}", -32000)
    if name in _DENIED_WORKER_RULE_FILES:
        raise stdio_mcp.McpError(f"git_write: worker rule file denied: {rel}", -32000)
    if (
        name in _DENIED_DB_NAMES
        or lower.endswith("/card_catalog.db")
        or lower.endswith("/miru_memory.db")
    ):
        raise stdio_mcp.McpError(f"git_write: database path denied: {rel}", -32000)
    if lower.startswith("docker/n8n/workflows/"):
        raise stdio_mcp.McpError(f"git_write: workflow JSON path denied: {rel}", -32000)
    if rel in _ALLOWED_EXACT:
        return
    if any(rel.startswith(prefix) for prefix in _ALLOWED_PREFIXES):
        return
    if rel.startswith("data/config/"):
        return
    if lower.endswith(".md") and any(rel.startswith(p) for p in _ALLOWED_SERVICE_MD_PREFIXES):
        return
    raise stdio_mcp.McpError(f"git_write: path not in orchestrator allowlist: {rel}", -32000)


def _resolve_allowed_paths(paths: list[str]) -> list[str]:
    if not isinstance(paths, list) or not paths:
        raise stdio_mcp.McpError("git_write: paths must be a non-empty array", -32602)
    rels: list[str] = []
    for raw in paths:
        if not isinstance(raw, str):
            raise stdio_mcp.McpError("git_write: every path must be a string", -32602)
        _reject_path_shape(raw)
        resolved = stdio_mcp._resolve_path(raw.strip())
        rel = _repo_rel_posix(resolved)
        _assert_rel_allowed(rel)
        rels.append(rel)
    deduped = sorted(dict.fromkeys(rels))
    if not deduped:
        raise stdio_mcp.McpError("git_write: no paths after normalization", -32602)
    return deduped


def _dirty_paths_for(paths: list[str]) -> set[str]:
    status = _run_git(["status", "--porcelain=v1", "--", *paths])
    if status.returncode != 0:
        raise stdio_mcp.McpError(f"git_write: status failed: {status.combined}", -32000)
    dirty: set[str] = set()
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        path = path.replace("\\", "/")
        if path:
            dirty.add(path)
    return dirty


def _assert_branch_ready(branch: str) -> None:
    if not branch or not branch.strip():
        raise stdio_mcp.McpError("git_write: branch is required", -32602)
    current = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if current.returncode != 0:
        raise stdio_mcp.McpError(
            f"git_write: could not read current branch: {current.combined}",
            -32000,
        )
    if current.stdout.strip() != branch:
        raise stdio_mcp.McpError(
            f"git_write: current branch is {current.stdout.strip()!r}, not requested {branch!r}",
            -32000,
        )
    exists = _run_git(["rev-parse", "--verify", f"refs/heads/{branch}"])
    if exists.returncode != 0:
        raise stdio_mcp.McpError(f"git_write: local branch does not exist: {branch}", -32000)
    upstream = _run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream.returncode != 0 or not upstream.stdout.strip():
        raise stdio_mcp.McpError(
            "git_write: branch has no upstream tracking branch; refusing push",
            -32000,
        )


def git_commit_and_push(paths: list[str], message: str, branch: str, ctx: Any = None) -> str:
    """Stage explicit allowlisted paths, run hygiene, commit, and push."""
    caller = _caller(ctx)
    rel_paths: list[str] = []
    audit_paths = [str(path) for path in paths] if isinstance(paths, list) else []
    hygiene: dict[str, Any] | None = None
    push_result: dict[str, Any] | None = None
    commit_sha: str | None = None
    audited = False
    msg = str(message or "").strip()
    br = str(branch or "").strip()
    try:
        if not msg:
            raise stdio_mcp.McpError("git_write: message is required", -32602)
        rel_paths = _resolve_allowed_paths(paths)
        _assert_branch_ready(br)
        dirty = _dirty_paths_for(rel_paths)
        missing = [path for path in rel_paths if path not in dirty]
        if missing:
            raise stdio_mcp.McpError(
                "git_write: listed path is not dirty/staged: " + ", ".join(missing),
                -32000,
            )

        add = _run_git(["add", "--", *rel_paths])
        if add.returncode != 0:
            raise stdio_mcp.McpError(f"git_write: git add failed: {add.combined}", -32000)

        pre = _run_pre_commit(rel_paths)
        hygiene = {
            "command": " ".join(pre.args),
            "returncode": pre.returncode,
            "output": pre.combined,
        }
        if pre.returncode != 0:
            _audit_git(
                tool="git_commit_and_push",
                caller=caller,
                paths=rel_paths,
                message=msg,
                branch=br,
                hygiene=hygiene,
                commit_sha=None,
                push=None,
                result="hygiene_failed",
                error=pre.combined,
            )
            audited = True
            raise stdio_mcp.McpError(f"git_write: hygiene failed:\n{pre.combined}", -32000)

        commit = _run_git(["commit", "-m", msg])
        if commit.returncode != 0:
            raise stdio_mcp.McpError(f"git_write: commit failed: {commit.combined}", -32000)
        rev = _run_git(["rev-parse", "HEAD"])
        if rev.returncode != 0:
            raise stdio_mcp.McpError(
                f"git_write: could not read commit SHA: {rev.combined}",
                -32000,
            )
        commit_sha = rev.stdout.strip()

        push = _run_git(["push"])
        push_result = {
            "command": " ".join(push.args),
            "returncode": push.returncode,
            "output": push.combined,
        }
        if push.returncode != 0:
            _audit_git(
                tool="git_commit_and_push",
                caller=caller,
                paths=rel_paths,
                message=msg,
                branch=br,
                hygiene=hygiene,
                commit_sha=commit_sha,
                push=push_result,
                result="push_failed",
                error=push.combined,
            )
            audited = True
            raise stdio_mcp.McpError(
                f"git_write: push failed; commit remains local ({commit_sha}):\n{push.combined}",
                -32000,
            )

        _audit_git(
            tool="git_commit_and_push",
            caller=caller,
            paths=rel_paths,
            message=msg,
            branch=br,
            hygiene=hygiene,
            commit_sha=commit_sha,
            push=push_result,
            result="success",
            error=None,
        )
        audited = True
        return _json_response(
            {
                "ok": True,
                "branch": br,
                "commit_sha": commit_sha,
                "files_committed": rel_paths,
                "hygiene": hygiene,
                "push": push_result,
            }
        )
    except stdio_mcp.McpError as exc:
        if not audited:
            _audit_git(
                tool="git_commit_and_push",
                caller=caller,
                paths=rel_paths or audit_paths,
                message=msg,
                branch=br,
                hygiene=None,
                commit_sha=commit_sha,
                push=push_result,
                result="failure",
                error=str(exc),
            )
        raise
    except Exception as exc:
        _audit_git(
            tool="git_commit_and_push",
            caller=caller,
            paths=rel_paths or audit_paths,
            message=msg,
            branch=br,
            hygiene=hygiene,
            commit_sha=commit_sha,
            push=push_result,
            result="failure",
            error=repr(exc),
        )
        raise stdio_mcp.McpError(f"git_write: {exc!r}", -32000) from exc


TOOL_FUNCTIONS = (git_commit_and_push,)


def register(mcp, cfg) -> int:
    """Register git write tools iff MIRU_GIT_WRITE_ENABLED."""
    global _CFG
    if not getattr(cfg, "git_write_enabled", False):
        cfg.disabled_categories["git_write"] = "MIRU_GIT_WRITE_ENABLED not set"
        return 0
    _CFG = cfg
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
