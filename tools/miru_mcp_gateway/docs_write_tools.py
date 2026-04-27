"""Docs write tools (PRO-123) — markdown / rules / README only.

Env-gated by MIRU_DOCS_WRITE_ENABLED. Positive path allowlist + hard deny list.
"""

from __future__ import annotations

import fnmatch
import json
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import audit as gw_audit  # noqa: E402
from miru_mcp_gateway import fs_tools as _fs  # noqa: E402
from miru_mcp_gateway import redact as _redact  # noqa: E402

_DOCS_MAX_BYTES = 256 * 1024

_CONFIG_SUFFIXES_DENY = (
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
)
_CODE_SUFFIXES_DENY = (".py", ".js", ".ts", ".jsx", ".tsx", ".ps1", ".sh", ".bat")
_LOCK_NAMES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pipfile.lock",
        "poetry.lock",
    }
)

_CFG: Any = None


def _repo_rel_posix(path: Path) -> str:
    return path.resolve().relative_to(stdio_mcp.ROOT).as_posix()


def _is_denied_docs_path(rel_posix: str) -> bool:
    """PRO-123 deny list takes precedence over allowlist."""
    lower = rel_posix.replace("\\", "/").lower()
    if _fs.is_denied_path_string(lower):
        return True
    parts = lower.split("/")
    name = parts[-1] if parts else lower
    if name in _LOCK_NAMES or name.endswith(".lock"):
        return True
    if lower.startswith("docker/n8n/workflows/") and name.endswith(".json"):
        return True
    deny_suffixes = _CONFIG_SUFFIXES_DENY + _CODE_SUFFIXES_DENY
    return any(name.endswith(suf) for suf in deny_suffixes)


def _matches_allowlist(rel_posix: str, patterns: tuple[str, ...]) -> bool:
    rel = rel_posix.replace("\\", "/").strip("/")
    if not rel:
        return False
    for pat in patterns:
        p = pat.replace("\\", "/").strip()
        if not p:
            continue
        if p == "*.md":
            if "/" not in rel and fnmatch.fnmatch(rel.split("/")[-1].lower(), "*.md"):
                return True
            continue
        if fnmatch.fnmatch(rel.lower(), p.lower()):
            return True
    return False


def _audit_docs(
    *,
    cfg: Any,
    tool: str,
    caller: str,
    rel_path: str,
    operation: str,
    bytes_before: int,
    bytes_after: int | None,
    result: str,
    error: str | None,
) -> None:
    _, docs_log = gw_audit.default_audit_paths(cfg.fs_root)
    row = {
        "ts": gw_audit._utc_iso(),
        "tool": tool,
        "caller": caller,
        "path": rel_path,
        "operation": operation,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "result": result,
        "error": error,
    }
    gw_audit.append_jsonl(docs_log, _redact.redact_dict(row))


def _assert_docs_path_allowed(cfg: Any, raw_path: str) -> tuple[Path, str]:
    path = stdio_mcp._resolve_path(raw_path)
    rel = _repo_rel_posix(path)
    if _is_denied_docs_path(rel):
        raise stdio_mcp.McpError(f"docs: path rejected by deny list: {raw_path!r}", -32000)
    if not _matches_allowlist(rel, cfg.docs_write_path_allowlist):
        raise stdio_mcp.McpError(f"docs: path not in write allowlist: {raw_path!r}", -32000)
    return path, rel


def _reject_if_secrets_in_content(text: str) -> None:
    hits = _redact.find_named_secret_substrings(text)
    if hits:
        raise stdio_mcp.McpError(
            "refused: content contains substring matching known secret " + hits[0],
            -32000,
        )


def docs_write_file(path: str, content: str, ctx: Any = None) -> str:
    """Create or overwrite an allowlisted markdown/text doc (UTF-8)."""
    cfg = _CFG
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    rel = path
    try:
        resolved, rel = _assert_docs_path_allowed(cfg, path)
        _reject_if_secrets_in_content(content)
        before = resolved.stat().st_size if resolved.is_file() else 0
        if len(content.encode("utf-8")) > _DOCS_MAX_BYTES:
            _audit_docs(
                cfg=cfg,
                tool="docs_write_file",
                caller=caller,
                rel_path=rel,
                operation="write",
                bytes_before=before,
                bytes_after=None,
                result="rejected_by_size_cap",
                error=f"content > {_DOCS_MAX_BYTES} bytes",
            )
            raise stdio_mcp.McpError(f"docs: content exceeds {_DOCS_MAX_BYTES} bytes", -32000)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        after = resolved.stat().st_size
        _audit_docs(
            cfg=cfg,
            tool="docs_write_file",
            caller=caller,
            rel_path=rel,
            operation="write",
            bytes_before=before,
            bytes_after=after,
            result="success",
            error=None,
        )
        return json.dumps(
            _redact.redact_dict({"ok": True, "path": rel, "bytes_written": after}),
            indent=2,
        )
    except stdio_mcp.McpError as exc:
        res = "failure"
        if "allowlist" in str(exc):
            res = "rejected_by_allowlist"
        elif "deny" in str(exc).lower():
            res = "rejected_by_denylist"
        elif "exceed" in str(exc).lower() or "size" in str(exc).lower():
            res = "rejected_by_size_cap"
        elif "secret" in str(exc).lower():
            res = "failure"
        _audit_docs(
            cfg=cfg,
            tool="docs_write_file",
            caller=caller,
            rel_path=rel,
            operation="write",
            bytes_before=0,
            bytes_after=None,
            result=res,
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_docs(
            cfg=cfg,
            tool="docs_write_file",
            caller=caller,
            rel_path=rel,
            operation="write",
            bytes_before=0,
            bytes_after=None,
            result="failure",
            error=str(exc),
        )
        raise stdio_mcp.McpError(f"docs: {exc!r}", -32000) from exc


def docs_append_file(path: str, content: str, ctx: Any = None) -> str:
    """Append UTF-8 text to end of an allowlisted doc file."""
    cfg = _CFG
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    rel = path
    try:
        resolved, rel = _assert_docs_path_allowed(cfg, path)
        _reject_if_secrets_in_content(content)
        before = resolved.stat().st_size if resolved.is_file() else 0
        add_bytes = len(content.encode("utf-8"))
        if before + add_bytes > _DOCS_MAX_BYTES:
            _audit_docs(
                cfg=cfg,
                tool="docs_append_file",
                caller=caller,
                rel_path=rel,
                operation="append",
                bytes_before=before,
                bytes_after=None,
                result="rejected_by_size_cap",
                error="would exceed cap",
            )
            raise stdio_mcp.McpError(f"docs: append would exceed {_DOCS_MAX_BYTES} bytes", -32000)
        with resolved.open("a", encoding="utf-8") as fh:
            fh.write(content)
        after = resolved.stat().st_size
        _audit_docs(
            cfg=cfg,
            tool="docs_append_file",
            caller=caller,
            rel_path=rel,
            operation="append",
            bytes_before=before,
            bytes_after=after,
            result="success",
            error=None,
        )
        return json.dumps(
            _redact.redact_dict({"ok": True, "path": rel, "bytes_total": after}),
            indent=2,
        )
    except stdio_mcp.McpError as exc:
        res = "failure"
        if "allowlist" in str(exc):
            res = "rejected_by_allowlist"
        elif "deny" in str(exc).lower():
            res = "rejected_by_denylist"
        elif "exceed" in str(exc).lower():
            res = "rejected_by_size_cap"
        _audit_docs(
            cfg=cfg,
            tool="docs_append_file",
            caller=caller,
            rel_path=rel,
            operation="append",
            bytes_before=0,
            bytes_after=None,
            result=res,
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_docs(
            cfg=cfg,
            tool="docs_append_file",
            caller=caller,
            rel_path=rel,
            operation="append",
            bytes_before=0,
            bytes_after=None,
            result="failure",
            error=str(exc),
        )
        raise stdio_mcp.McpError(f"docs: {exc!r}", -32000) from exc


def docs_patch_file(path: str, old_str: str, new_str: str, ctx: Any = None) -> str:
    """Replace exactly one occurrence of old_str with new_str (UTF-8 text)."""
    cfg = _CFG
    caller = gw_audit.caller_from_fastmcp_context(ctx)
    rel = path
    try:
        resolved, rel = _assert_docs_path_allowed(cfg, path)
        _reject_if_secrets_in_content(new_str)
        if not old_str:
            raise stdio_mcp.McpError("docs: old_str must not be empty", -32602)
        if not resolved.is_file():
            raise stdio_mcp.McpError(f"docs: file not found: {path}", -32000)
        text = resolved.read_text(encoding="utf-8", errors="strict")
        before = len(text.encode("utf-8"))
        count = text.count(old_str)
        if count == 0:
            raise stdio_mcp.McpError("docs: old_str not found in file", -32602)
        if count > 1:
            raise stdio_mcp.McpError(f"docs: old_str must be unique (found {count} times)", -32602)
        new_text = text.replace(old_str, new_str, 1)
        after_b = len(new_text.encode("utf-8"))
        if after_b > _DOCS_MAX_BYTES:
            _audit_docs(
                cfg=cfg,
                tool="docs_patch_file",
                caller=caller,
                rel_path=rel,
                operation="patch",
                bytes_before=before,
                bytes_after=None,
                result="rejected_by_size_cap",
                error="result too large",
            )
            raise stdio_mcp.McpError(
                f"docs: patched file would exceed {_DOCS_MAX_BYTES} bytes", -32000
            )
        resolved.write_text(new_text, encoding="utf-8")
        after = len(new_text.encode("utf-8"))
        _audit_docs(
            cfg=cfg,
            tool="docs_patch_file",
            caller=caller,
            rel_path=rel,
            operation="patch",
            bytes_before=before,
            bytes_after=after,
            result="success",
            error=None,
        )
        return json.dumps(
            _redact.redact_dict({"ok": True, "path": rel, "bytes_total": after}),
            indent=2,
        )
    except stdio_mcp.McpError as exc:
        res = "failure"
        if "allowlist" in str(exc):
            res = "rejected_by_allowlist"
        elif "deny" in str(exc).lower():
            res = "rejected_by_denylist"
        elif "exceed" in str(exc).lower():
            res = "rejected_by_size_cap"
        _audit_docs(
            cfg=cfg,
            tool="docs_patch_file",
            caller=caller,
            rel_path=rel,
            operation="patch",
            bytes_before=0,
            bytes_after=None,
            result=res,
            error=str(exc),
        )
        raise
    except Exception as exc:
        _audit_docs(
            cfg=cfg,
            tool="docs_patch_file",
            caller=caller,
            rel_path=rel,
            operation="patch",
            bytes_before=0,
            bytes_after=None,
            result="failure",
            error=str(exc),
        )
        raise stdio_mcp.McpError(f"docs: {exc!r}", -32000) from exc


TOOL_FUNCTIONS = (
    docs_write_file,
    docs_patch_file,
    docs_append_file,
)


def register(mcp, cfg) -> int:
    """Register docs_* write tools iff MIRU_DOCS_WRITE_ENABLED."""
    global _CFG
    if not getattr(cfg, "docs_write_enabled", False):
        cfg.disabled_categories["docs_write"] = "MIRU_DOCS_WRITE_ENABLED not set"
        return 0
    _CFG = cfg
    for func in TOOL_FUNCTIONS:
        mcp.tool(func)
    return len(TOOL_FUNCTIONS)
