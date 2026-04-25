#!/usr/bin/env python3
"""Read-only MCP filesystem server for Project Miru Stage 1 access."""

from __future__ import annotations

import base64
import fnmatch
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "2024-11-05"
DENIED_SUFFIXES = (".db", ".sqlite", ".sqlite3")
DENIED_NAMES = {"card_catalog.db"}


def _root() -> Path:
    configured = os.environ.get("MIRU_FS_ALLOW_ROOT") or (
        sys.argv[1] if len(sys.argv) > 1 else r"D:\dev\miru"
    )
    return Path(configured).resolve()


ROOT = _root()


class McpError(Exception):
    def __init__(self, message: str, code: int = -32000) -> None:
        super().__init__(message)
        self.code = code


def _is_denied(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return bool(parts & DENIED_NAMES) or path.name.lower().endswith(DENIED_SUFFIXES)


def _resolve_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate

    resolved = candidate.resolve()
    try:
        os.path.commonpath([str(ROOT), str(resolved)])
    except ValueError as exc:
        raise McpError(f"Path is outside allowed root: {raw_path}") from exc

    if os.path.commonpath([str(ROOT), str(resolved)]) != str(ROOT):
        raise McpError(f"Path is outside allowed root: {raw_path}")
    if _is_denied(resolved):
        raise McpError(f"Path is denied by Project Miru MCP policy: {raw_path}")
    return resolved


def _read_text_file(args: dict[str, Any]) -> str:
    path = _resolve_path(str(args.get("path", "")))
    head = args.get("head")
    tail = args.get("tail")
    if head is not None and tail is not None:
        raise McpError("Use either head or tail, not both", -32602)

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if head is not None:
        lines = lines[: int(head)]
    elif tail is not None:
        lines = lines[-int(tail) :]
    return "\n".join(lines)


def _read_media_file(args: dict[str, Any]) -> dict[str, Any]:
    path = _resolve_path(str(args.get("path", "")))
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return {
        "mimeType": mime_type,
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def _read_multiple_files(args: dict[str, Any]) -> str:
    results: list[str] = []
    for raw_path in args.get("paths", []):
        try:
            text = _read_text_file({"path": raw_path})
            results.append(f"{raw_path}:\n{text}")
        except Exception as exc:  # noqa: BLE001 - per-file errors are part of the tool contract.
            results.append(f"{raw_path}: ERROR: {exc}")
    return "\n\n".join(results)


def _visible_entries(path: Path) -> list[Path]:
    entries: list[Path] = []
    for entry in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if not _is_denied(entry.resolve()):
            entries.append(entry)
    return entries


def _list_directory(args: dict[str, Any]) -> str:
    path = _resolve_path(str(args.get("path", "")))
    if not path.is_dir():
        raise McpError(f"Path is not a directory: {path}")
    rows = []
    for entry in _visible_entries(path):
        marker = "DIR" if entry.is_dir() else "FILE"
        rows.append(f"[{marker}] {entry.name}")
    return "\n".join(rows)


def _list_directory_with_sizes(args: dict[str, Any]) -> str:
    path = _resolve_path(str(args.get("path", "")))
    if not path.is_dir():
        raise McpError(f"Path is not a directory: {path}")
    entries = _visible_entries(path)
    if args.get("sortBy") == "size":
        entries.sort(key=lambda item: item.stat().st_size if item.is_file() else 0, reverse=True)
    rows = []
    for entry in entries:
        marker = "DIR" if entry.is_dir() else "FILE"
        size = entry.stat().st_size if entry.is_file() else 0
        rows.append(f"[{marker}] {entry.name}\t{size} bytes")
    return "\n".join(rows)


def _matches_exclude(path: Path, excludes: list[str]) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return any(fnmatch.fnmatch(rel, pattern) for pattern in excludes)


def _directory_tree(args: dict[str, Any]) -> str:
    excludes = [str(pattern) for pattern in args.get("excludePatterns", [])]

    def build(path: Path) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        for entry in _visible_entries(path):
            resolved = entry.resolve()
            if _matches_exclude(resolved, excludes):
                continue
            node: dict[str, Any] = {
                "name": entry.name,
                "type": "directory" if entry.is_dir() else "file",
            }
            if entry.is_dir():
                node["children"] = build(resolved)
            children.append(node)
        return children

    path = _resolve_path(str(args.get("path", "")))
    if not path.is_dir():
        raise McpError(f"Path is not a directory: {path}")
    return json.dumps(build(path), indent=2)


def _search_files(args: dict[str, Any]) -> str:
    path = _resolve_path(str(args.get("path", "")))
    if not path.is_dir():
        raise McpError(f"Path is not a directory: {path}")
    pattern = str(args.get("pattern", "*"))
    excludes = [str(item) for item in args.get("excludePatterns", [])]
    matches: list[str] = []
    for current_root, dir_names, file_names in os.walk(path):
        root_path = Path(current_root).resolve()
        dir_names[:] = [
            name
            for name in dir_names
            if not _is_denied((root_path / name).resolve())
            and not _matches_exclude((root_path / name).resolve(), excludes)
        ]
        for name in [*dir_names, *file_names]:
            candidate = (root_path / name).resolve()
            if _is_denied(candidate) or _matches_exclude(candidate, excludes):
                continue
            relative = candidate.relative_to(ROOT).as_posix()
            if fnmatch.fnmatch(name.lower(), pattern.lower()) or fnmatch.fnmatch(
                relative.lower(), pattern.lower()
            ):
                matches.append(str(candidate))
    return "\n".join(matches) if matches else "No matches found"


def _get_file_info(args: dict[str, Any]) -> str:
    path = _resolve_path(str(args.get("path", "")))
    stat = path.stat()
    return "\n".join(
        [
            f"Path: {path}",
            f"Type: {'directory' if path.is_dir() else 'file'}",
            f"Size: {stat.st_size}",
            f"Created: {stat.st_ctime}",
            f"Modified: {stat.st_mtime}",
            f"Accessed: {stat.st_atime}",
        ]
    )


TOOLS: dict[str, dict[str, Any]] = {
    "read_text_file": {
        "description": "Read a UTF-8 text file under D:\\dev\\miru. Database files are denied.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "head": {"type": "number"},
                "tail": {"type": "number"},
            },
            "required": ["path"],
        },
        "handler": _read_text_file,
    },
    "read_media_file": {
        "description": "Read an image or audio file under D:\\dev\\miru as base64. Database files are denied.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "handler": _read_media_file,
    },
    "read_multiple_files": {
        "description": "Read multiple UTF-8 text files. Database files are denied per path.",
        "inputSchema": {
            "type": "object",
            "properties": {"paths": {"type": "array", "items": {"type": "string"}}},
            "required": ["paths"],
        },
        "handler": _read_multiple_files,
    },
    "list_directory": {
        "description": "List a directory under D:\\dev\\miru, omitting denied database files.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "handler": _list_directory,
    },
    "list_directory_with_sizes": {
        "description": "List a directory with file sizes, omitting denied database files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "sortBy": {"type": "string", "enum": ["name", "size"]},
            },
            "required": ["path"],
        },
        "handler": _list_directory_with_sizes,
    },
    "directory_tree": {
        "description": "Return a recursive JSON directory tree, omitting denied database files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "excludePatterns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path"],
        },
        "handler": _directory_tree,
    },
    "search_files": {
        "description": "Search filenames under D:\\dev\\miru, omitting denied database files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "pattern": {"type": "string"},
                "excludePatterns": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["path", "pattern"],
        },
        "handler": _search_files,
    },
    "get_file_info": {
        "description": "Return metadata for an allowed path. Database files are denied.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "handler": _get_file_info,
    },
    "list_allowed_directories": {
        "description": "List the single Project Miru root exposed by this server.",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": lambda _args: f"Allowed directories:\n{ROOT}",
    },
}


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
            "annotations": {"readOnlyHint": True},
        }
        for name, spec in TOOLS.items()
    ]


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _handle(request: dict[str, Any]) -> dict[str, Any] | None:
    if "id" not in request:
        return None

    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})
    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "miru-readonly-filesystem",
                        "version": "1.0.0",
                    },
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": _tool_defs()})
        if method == "tools/call":
            name = params.get("name")
            if name not in TOOLS:
                raise McpError(f"Unknown tool: {name}", -32602)
            value = TOOLS[name]["handler"](params.get("arguments", {}))
            if isinstance(value, dict) and "data" in value and "mimeType" in value:
                content = [{"type": "image", "data": value["data"], "mimeType": value["mimeType"]}]
            else:
                content = [{"type": "text", "text": str(value)}]
            return _result(request_id, {"content": content, "isError": False})
        if method == "ping":
            return _result(request_id, {})
        return _error(request_id, -32601, f"Method not found: {method}")
    except McpError as exc:
        return _result(
            request_id,
            {"content": [{"type": "text", "text": str(exc)}], "isError": True},
        )
    except Exception as exc:  # noqa: BLE001 - convert server failures to JSON-RPC errors.
        return _error(request_id, -32000, str(exc))


def main() -> None:
    if not ROOT.exists():
        raise SystemExit(f"Allowed root does not exist: {ROOT}")

    for line in sys.stdin:
        if not line.strip():
            continue
        response = _handle(json.loads(line))
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
