"""Filesystem tool surface for the Miru MCP Gateway.

Reuses the resolver and handler logic from the existing stdio MCP server at
[tools/miru_readonly_filesystem_mcp.py]. Adds an expanded deny list with both
filename patterns (.env, *.key, etc.) and path-segment denies (.git, logs).

The expansion is applied by monkey-patching the imported module's `_is_denied`
function so all of its code paths -- _resolve_path, _visible_entries, listing,
search, tree -- see the stricter policy without having to duplicate handlers.
"""

from __future__ import annotations

import fnmatch
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The stdio MCP file is a sibling under tools/. Make sure the parent of this
# package (i.e. tools/) is importable before we try to pull it in.
_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

# ---------------------------------------------------------------------------
# Expanded deny policy (Stage 1 mandatory expansion -- see plan §5)
# ---------------------------------------------------------------------------

DENIED_FILENAME_PATTERNS: tuple[str, ...] = (
    ".env",
    "*.env",
    "*.env.*",
    ".env.example",
    "secrets.*",
    "tokens.*",
    "*.key",
    "*.pem",
    "*.ppk",
    "id_rsa*",
    "id_ed25519*",
)

# Mandatory path-segment denies: any path with one of these as a part is denied.
DENIED_PATH_SEGMENTS_MANDATORY: frozenset[str] = frozenset({".git", "logs"})

# Recommended (on by default): heavy/cache directories with no read value.
DENIED_PATH_SEGMENTS_OPTIONAL: frozenset[str] = frozenset(
    {"node_modules", "__pycache__", ".venv", "venv"}
)

ALL_DENIED_SEGMENTS: frozenset[str] = DENIED_PATH_SEGMENTS_MANDATORY | DENIED_PATH_SEGMENTS_OPTIONAL


def _matches_filename_deny(name: str) -> bool:
    lower = name.lower()
    return any(fnmatch.fnmatch(lower, pattern.lower()) for pattern in DENIED_FILENAME_PATTERNS)


def _has_denied_segment(path: Path) -> bool:
    try:
        rel = path.relative_to(stdio_mcp.ROOT)
    except ValueError:
        # Outside ROOT -- the original resolver will catch this separately.
        return False
    return any(part.lower() in ALL_DENIED_SEGMENTS for part in rel.parts)


_original_is_denied: Callable[[Path], bool] = stdio_mcp._is_denied


def _is_denied_extended(path: Path) -> bool:
    return (
        _original_is_denied(path) or _matches_filename_deny(path.name) or _has_denied_segment(path)
    )


# Apply the patch. From now on, every code path inside stdio_mcp that calls
# `_is_denied(...)` (resolver, _visible_entries, search_files, directory_tree)
# uses the extended policy. We do NOT mutate the original file -- this is a
# runtime-only override that lasts for the lifetime of the gateway process.
stdio_mcp._is_denied = _is_denied_extended


def is_denied_path_string(path: str) -> bool:
    """Stage 2 reuse: check a forward-or-backslash path string against the
    same filename + path-segment deny rules used for the local filesystem.

    Used by github_tools.github_read_file / github_search_repo_files so a
    `.env` or `*.key` from a GitHub repo never reaches the caller, regardless
    of whether the local repo would have allowed it.

    Pure string check -- no I/O, no resolution. The path may be any case.
    """
    if not path:
        return False
    # Normalize separators so we can check segments uniformly.
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    parts = normalized.split("/")
    name = parts[-1] if parts else ""
    if name and _matches_filename_deny(name):
        return True
    return any(part.lower() in ALL_DENIED_SEGMENTS for part in parts)


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------
#
# Each wrapper translates between the FastMCP `@mcp.tool` argument shape (named
# kwargs, typed) and the existing handler shape (single dict argument). The
# handler raises stdio_mcp.McpError on policy violations; FastMCP converts
# raised exceptions into MCP error responses automatically.


def fs_read_text_file(path: str, head: int | None = None, tail: int | None = None) -> str:
    """Read a UTF-8 text file under D:\\dev\\miru. Database, secret, log, and
    .git files are denied. Use head OR tail to truncate, not both.
    """
    args: dict[str, Any] = {"path": path}
    if head is not None:
        args["head"] = head
    if tail is not None:
        args["tail"] = tail
    return stdio_mcp._read_text_file(args)


def fs_read_media_file(path: str) -> dict[str, Any]:
    """Read an image or audio file under D:\\dev\\miru as base64. Returns a
    dict with `mimeType` and `data` keys.
    """
    return stdio_mcp._read_media_file({"path": path})


def fs_read_multiple_files(paths: list[str]) -> str:
    """Read multiple UTF-8 text files. Each file's errors are reported per-path
    rather than aborting the whole batch.
    """
    return stdio_mcp._read_multiple_files({"paths": list(paths)})


def fs_list_directory(path: str) -> str:
    """List a directory under D:\\dev\\miru. Denied entries are omitted."""
    return stdio_mcp._list_directory({"path": path})


def fs_list_directory_with_sizes(
    path: str,
    sortBy: str = "name",  # noqa: N803
) -> str:
    """List a directory with file sizes in bytes. sortBy must be 'name' or 'size'."""
    if sortBy not in ("name", "size"):
        raise stdio_mcp.McpError("sortBy must be 'name' or 'size'", -32602)
    return stdio_mcp._list_directory_with_sizes({"path": path, "sortBy": sortBy})


def fs_directory_tree(
    path: str,
    excludePatterns: list[str] | None = None,  # noqa: N803
) -> str:
    """Return a recursive JSON tree. Denied entries are omitted."""
    return stdio_mcp._directory_tree({"path": path, "excludePatterns": list(excludePatterns or [])})


def fs_search_files(
    path: str,
    pattern: str,
    excludePatterns: list[str] | None = None,  # noqa: N803
) -> str:
    """Search filenames matching `pattern` (fnmatch syntax) under `path`.
    Denied paths are skipped.
    """
    return stdio_mcp._search_files(
        {
            "path": path,
            "pattern": pattern,
            "excludePatterns": list(excludePatterns or []),
        }
    )


def fs_get_file_info(path: str) -> str:
    """Return metadata (size, type, timestamps) for an allowed path."""
    return stdio_mcp._get_file_info({"path": path})


def fs_list_allowed_directories() -> str:
    """List the single Project Miru root exposed by this gateway."""
    return f"Allowed directories:\n{stdio_mcp.ROOT}"


# Manifest used by server.py to register tools with FastMCP.
TOOL_FUNCTIONS = (
    fs_read_text_file,
    fs_read_media_file,
    fs_read_multiple_files,
    fs_list_directory,
    fs_list_directory_with_sizes,
    fs_directory_tree,
    fs_search_files,
    fs_get_file_info,
    fs_list_allowed_directories,
)


def register(mcp, cfg) -> int:
    """Register all fs_* tools with the given FastMCP instance.

    Filesystem is the always-on category. Returns the count registered.
    """
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
