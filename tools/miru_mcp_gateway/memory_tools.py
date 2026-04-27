"""PRO-163: miru_memory SQLite tools surfaced in the gateway.

Read/write access to data/miru_memory.db for Claude Chat sessions.
Gated by MIRU_MEMORY_ENABLED=1.

Security model:
  read_query  — SELECT only (no DML, no DDL, no PRAGMA writes)
  write_query — INSERT / UPDATE / DELETE only (no DDL, no ATTACH/DETACH)
  list_tables — sqlite_master query, read-only
  describe_table — PRAGMA table_info, read-only

Writes are audited to logs/mcp_gateway_writes.jsonl with hash chain.
Reads are audited through the standard gateway_security read-audit path.
"""

from __future__ import annotations

import json
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

# Tokens that are never allowed in any query (DML or read)
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
    }
)

# Only these lead-tokens are allowed for write_query
_WRITE_ALLOWED_LEAD: frozenset[str] = frozenset({"insert", "update", "delete"})

# Only this lead-token is allowed for read_query (after list/describe are handled separately)
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


def _connect(write: bool = False) -> sqlite3.Connection:
    if _DB_PATH is None:
        raise stdio_mcp.McpError("memory_tools: not configured", -32000)
    if not _DB_PATH.exists():
        raise stdio_mcp.McpError(
            json.dumps({"error": "db_not_found", "path": str(_DB_PATH)}), -32000
        )
    uri = _DB_PATH.as_uri()
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


def list_tables() -> str:
    """List all user tables in miru_memory.db."""
    with _connect(write=False) as conn:
        rows = conn.execute(
            "SELECT name, type FROM sqlite_master " "WHERE type IN ('table','view') ORDER BY name"
        ).fetchall()
    return json.dumps([dict(r) for r in rows], indent=2)


def describe_table(table_name: str) -> str:
    """Return column schema for a table in miru_memory.db.

    Uses PRAGMA table_info(). table_name must be alphanumeric + underscores.
    """
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}", table_name):
        raise stdio_mcp.McpError(
            json.dumps({"error": "invalid_table_name", "name": table_name[:64]}), -32602
        )
    with _connect(write=False) as conn:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        raise stdio_mcp.McpError(
            json.dumps({"error": "table_not_found", "name": table_name}), -32602
        )
    return json.dumps([dict(r) for r in rows], indent=2)


def read_query(sql: str, params: list[Any] | None = None) -> str:
    """Run a SELECT query against miru_memory.db.

    Only SELECT statements are allowed. Returns up to 500 rows as a JSON array.
    Use params (positional ? placeholders) to avoid SQL injection.
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
    bind = params or []
    with _connect(write=False) as conn:
        cur = conn.execute(clean, bind)
        rows = cur.fetchmany(_MAX_ROWS)
        columns = [d[0] for d in cur.description] if cur.description else []
    result = [dict(zip(columns, row, strict=False)) for row in rows]
    return json.dumps(result, indent=2, default=str)


def write_query(sql: str, params: list[Any] | None = None) -> str:
    """Run an INSERT, UPDATE, or DELETE against miru_memory.db.

    DDL (CREATE, DROP, ALTER), ATTACH, DETACH, and VACUUM are blocked.
    Returns rows_affected count as JSON.
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
    _check_deny_tokens(clean.lower())
    bind = params or []
    error_str: str | None = None
    rows_affected = 0
    try:
        with _connect(write=True) as conn:
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
