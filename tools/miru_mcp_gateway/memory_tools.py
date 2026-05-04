"""PRO-163: memory SQLite tools surfaced in the gateway.

Read/write access to SQLite memory databases for Claude Chat sessions.
Supports multiple databases via the optional db_path parameter.
Gated by MIRU_MEMORY_ENABLED=1.

Security model:
  read_query  — SELECT only (no DML, no DDL, no PRAGMA writes)
  write_query — INSERT / UPDATE / DELETE / CREATE TABLE IF NOT EXISTS only
                (no DROP, no ALTER, no ATTACH/DETACH)
  list_tables — sqlite_master query, read-only
  describe_table — PRAGMA table_info, read-only

Writes are audited to logs/mcp_gateway_writes.jsonl with hash chain.
Reads are audited through the standard gateway_security read-audit path.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import audit as gw_audit  # noqa: E402

_CFG: Any = None
_DB_PATH: Path | None = None

_MAX_ROWS = 500
_MAX_SQL_LEN = 4096

_DENY_TOKENS: frozenset[str] = frozenset(
    {
        "attach",
        "detach",
        "vacuum",
        "reindex",
        "analyze",
        "savepoint",
        "release",
        "checkpoint",
        "drop",
        "alter",
    }
)

_WRITE_ALLOWED_LEAD: frozenset[str] = frozenset({"insert", "update", "delete", "create"})

# Only CREATE TABLE IF NOT EXISTS is permitted — bare CREATE TABLE and all other
# CREATE variants (VIEW, INDEX, TRIGGER, etc.) are blocked.
_CREATE_SAFE_RE = re.compile(r"^\s*create\s+table\s+if\s+not\s+exists\s+", re.IGNORECASE)

_READ_ALLOWED_LEAD: frozenset[str] = frozenset({"select"})


def _first_token(sql: str) -> str:
    """Return the first alphabetic token from sql, lowercased."""
    m = re.match(r"\s*([a-zA-Z_]+)", sql)
    return m.group(1).lower() if m else ""


def _strip_comments(sql: str) -> str:
    """Remove -- line comments and /* */ block comments."""
    sql = re.sub(r"--[^\n]*", " ", sql)
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return sql.strip()


def _check_deny_tokens(sql_lower: str) -> None:
    """Raise McpError if a denied token appears as a word."""
    for tok in _DENY_TOKENS:
        if re.search(r"\b" + tok + r"\b", sql_lower):
            raise stdio_mcp.McpError(
                json.dumps({"error": "forbidden_keyword", "keyword": tok}), -32600
            )


def _resolve_db_path(db_path: str | None, *, create: bool = False) -> Path:
    """Resolve which database to use. If db_path is given, validate it
    against the filesystem root. Otherwise use the default.

    create=True is only passed by write_query — read operations raise
    McpError on a missing path rather than silently creating the file.
    """
    if db_path is None:
        if _DB_PATH is None:
            raise stdio_mcp.McpError("memory_tools: not configured", -32000)
        return _DB_PATH

    p = Path(db_path).resolve()
    if not p.suffix == ".db":
        raise stdio_mcp.McpError(
            json.dumps({"error": "invalid_db_path", "reason": "must end with .db"}), -32602
        )
    fs_root = stdio_mcp.ROOT
    try:
        common = os.path.commonpath([str(fs_root), str(p)])
    except ValueError:
        raise stdio_mcp.McpError(
            json.dumps({"error": "invalid_db_path", "reason": "outside allowed root"}), -32602
        ) from None
    if common != str(fs_root):
        raise stdio_mcp.McpError(
            json.dumps({"error": "invalid_db_path", "reason": "outside allowed root"}), -32602
        )
    if not p.exists():
        if not create:
            raise stdio_mcp.McpError(json.dumps({"error": "db_not_found", "path": str(p)}), -32000)
        p.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(p), timeout=5)
        conn.close()
    return p


def _connect(db: Path, write: bool = False) -> sqlite3.Connection:
    if not db.exists():
        raise stdio_mcp.McpError(json.dumps({"error": "db_not_found", "path": str(db)}), -32000)
    uri = db.as_uri()
    if not write:
        uri += "?mode=ro"
    flags = sqlite3.PARSE_DECLTYPES
    conn = sqlite3.connect(uri, uri=True, detect_types=flags, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_write(tool: str, sql: str, rows_affected: int, error: str | None) -> None:
    if _CFG is None:
        return
    try:
        writes_log, _, _ = gw_audit.default_audit_paths(_CFG.fs_root)
        gw_audit.append_jsonl_chained(
            writes_log,
            {
                "ts": _utc_now(),
                "tool": tool,
                "category": "memory_write",
                "sql_preview": sql[:200],
                "rows_affected": rows_affected,
                "error": error,
            },
        )
    except Exception:
        pass


def list_tables(db_path: str | None = None) -> str:
    """List all user tables in a memory database.

    db_path: absolute path to a .db file under the allowed filesystem root.
    Omit to use the default database.
    """
    db = _resolve_db_path(db_path)
    with _connect(db, write=False) as conn:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master " "WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
    return json.dumps([dict(r) for r in rows], indent=2)


def describe_table(table_name: str, db_path: str | None = None) -> str:
    """Return column schema for a table in a memory database.

    table_name must be alphanumeric + underscores.
    db_path: absolute path to a .db file under the allowed filesystem root.
    Omit to use the default database.
    """
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", table_name):
        raise stdio_mcp.McpError(
            json.dumps({"error": "invalid_table_name", "name": table_name[:64]}), -32602
        )
    db = _resolve_db_path(db_path)
    with _connect(db, write=False) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        raise stdio_mcp.McpError(
            json.dumps({"error": "table_not_found", "name": table_name}), -32602
        )
    return json.dumps([dict(r) for r in rows], indent=2)


def read_query(sql: str, params: list[Any] | None = None, db_path: str | None = None) -> str:
    """Run a SELECT query against a memory database.

    Only SELECT statements are allowed. Returns up to 500 rows as JSON.
    Use params (positional ? placeholders) to avoid SQL injection.
    db_path: absolute path to a .db file under the allowed filesystem root.
    Omit to use the default database.
    """
    if len(sql) > _MAX_SQL_LEN:
        raise stdio_mcp.McpError(json.dumps({"error": "sql_too_long", "max": _MAX_SQL_LEN}), -32602)
    clean = _strip_comments(sql)
    if not clean:
        raise stdio_mcp.McpError(json.dumps({"error": "empty_sql"}), -32602)
    lead = _first_token(clean)
    if lead not in _READ_ALLOWED_LEAD:
        raise stdio_mcp.McpError(json.dumps({"error": "read_only", "lead_token": lead}), -32600)
    _check_deny_tokens(clean.lower())
    db = _resolve_db_path(db_path)
    bind = params or []
    with _connect(db, write=False) as conn:
        cur = conn.execute(clean, bind)
        rows = cur.fetchmany(_MAX_ROWS)
        columns = [d[0] for d in cur.description] if cur.description else []
    result = [dict(zip(columns, row, strict=False)) for row in rows]
    return json.dumps(result, indent=2, default=str)


def write_query(sql: str, params: list[Any] | None = None, db_path: str | None = None) -> str:
    """Run INSERT, UPDATE, DELETE, or CREATE TABLE against a memory database.

    DROP, ALTER, ATTACH, DETACH, and VACUUM are blocked.
    Returns rows_affected count as JSON.
    db_path: absolute path to a .db file under the allowed filesystem root.
    Omit to use the default database.
    """
    if len(sql) > _MAX_SQL_LEN:
        raise stdio_mcp.McpError(json.dumps({"error": "sql_too_long", "max": _MAX_SQL_LEN}), -32602)
    clean = _strip_comments(sql)
    if not clean:
        raise stdio_mcp.McpError(json.dumps({"error": "empty_sql"}), -32602)
    lead = _first_token(clean)
    if lead not in _WRITE_ALLOWED_LEAD:
        raise stdio_mcp.McpError(
            json.dumps(
                {"error": "dml_only", "lead_token": lead, "allowed": sorted(_WRITE_ALLOWED_LEAD)}
            ),
            -32600,
        )
    if lead == "create" and not _CREATE_SAFE_RE.match(clean):
        raise stdio_mcp.McpError(
            json.dumps(
                {
                    "error": "dml_only",
                    "detail": "only CREATE TABLE IF NOT EXISTS is permitted; "
                    "bare CREATE TABLE and other CREATE variants are blocked",
                }
            ),
            -32600,
        )
    _check_deny_tokens(clean.lower())
    db = _resolve_db_path(db_path, create=True)
    bind = params or []
    error_str: str | None = None
    rows_affected = 0
    try:
        with _connect(db, write=True) as conn:
            cur = conn.execute(clean, bind)
            rows_affected = cur.rowcount if cur.rowcount >= 0 else 0
            conn.commit()
    except sqlite3.Error as exc:
        error_str = str(exc)
        _audit_write("write_query", clean, 0, error_str)
        raise stdio_mcp.McpError(
            json.dumps({"error": "sqlite_error", "detail": error_str}), -32000
        ) from exc
    _audit_write("write_query", clean, rows_affected, None)
    return json.dumps({"ok": True, "rows_affected": rows_affected})


TOOL_FUNCTIONS = (list_tables, describe_table, read_query, write_query)


def register(mcp, cfg) -> int:
    global _CFG, _DB_PATH
    if not getattr(cfg, "memory_enabled", False):
        cfg.disabled_categories["memory"] = "MIRU_MEMORY_ENABLED not set"
        return 0
    db_path = getattr(cfg, "memory_db_path", None)
    if db_path is None:
        cfg.disabled_categories["memory"] = "memory_db_path not configured"
        return 0
    if not db_path.exists():
        cfg.disabled_categories["memory"] = f"db not found: {db_path}"
        return 0
    _CFG = cfg
    _DB_PATH = db_path
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
