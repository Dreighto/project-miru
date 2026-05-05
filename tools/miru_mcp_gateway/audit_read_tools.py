"""PRO-135: read gateway audit JSONL tails with optional hash-chain verification."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import audit as gw_audit  # noqa: E402

_CFG: Any = None

_LOG_KINDS = ("writes", "reads", "docs")


def _parse_since(s: str | None) -> datetime | None:
    if not s or not str(s).strip():
        return None
    raw = str(s).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _tail_jsonl_lines(path: Path, max_lines: int) -> list[str]:
    if not path.exists():
        return []
    size = path.stat().st_size
    chunk = min(size, 2 * 1024 * 1024)
    with path.open("rb") as fh:
        if size > chunk:
            fh.seek(size - chunk)
        data = fh.read()
    text = data.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return lines[-max_lines:]


def gateway_audit_tail(
    log_kind: str = "writes",
    category: str | None = None,
    since: str | None = None,
    limit: int = 50,
    summary: bool = True,
) -> str:
    """Return recent gateway audit JSONL entries (PRO-135).

    ``log_kind``: ``writes`` | ``reads`` | ``docs`` (docs write audit).
    ``category``: optional substring filter on a ``category`` or ``tool`` field.
    ``since``: optional ISO UTC timestamp filter.
    ``summary``: when true, return compact rows; when false, full parsed dicts.
    """
    cfg = _CFG
    if cfg is None:
        raise stdio_mcp.McpError("audit_read: not configured", -32000)
    lk = (log_kind or "writes").strip().lower()
    if lk not in _LOG_KINDS:
        raise stdio_mcp.McpError(f"audit_read: log_kind must be one of {list(_LOG_KINDS)}", -32602)
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 50
    lim = max(1, min(lim, 500))

    writes_p, docs_p, reads_p = gw_audit.default_audit_paths(cfg.repo_root)
    path = {"writes": writes_p, "reads": reads_p, "docs": docs_p}[lk]

    raw_lines = _tail_jsonl_lines(path, lim * 3)
    since_dt = _parse_since(since)
    entries: list[dict[str, Any]] = []
    parsed_rows: list[dict[str, Any]] = []
    for line in raw_lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        ts = str(row.get("ts", ""))
        if since_dt:
            try:
                rts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if rts < since_dt:
                    continue
            except ValueError:
                pass
        if category:
            cat = str(row.get("category", row.get("tool", "")))
            if category.lower() not in cat.lower():
                continue
        parsed_rows.append(row)
        if len(parsed_rows) > lim:
            parsed_rows.pop(0)

    chain_intact, break_idx = gw_audit.validate_audit_chain_slice(parsed_rows)

    for row in parsed_rows[-lim:]:
        if summary:
            prm = row.get("params", "")
            psum = json.dumps(prm, default=str)[:240] if isinstance(prm, dict) else str(prm)[:240]
            entries.append(
                {
                    "ts": row.get("ts"),
                    "tool": row.get("tool"),
                    "category": row.get("category", ""),
                    "caller": row.get("caller", ""),
                    "params_summary": psum,
                    "result": row.get("result", row.get("status", "")),
                    "duration_ms": row.get("duration_ms"),
                    "error": (row.get("error") or "")[:200] if row.get("error") else None,
                    "unverified": not bool(row.get("row_hash")),
                }
            )
        else:
            entries.append(dict(row))

    by_tool = Counter(str(e.get("tool", "")) for e in entries)
    by_result = Counter(str(e.get("result", "")) for e in entries)

    payload = {
        "log_kind": lk,
        "path": str(path),
        "since_filter": since,
        "until": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries": entries,
        "counts": {"by_tool": dict(by_tool), "by_result": dict(by_result)},
        "chain_intact": chain_intact,
        "chain_break_index": break_idx,
    }
    return json.dumps(payload, indent=2)


TOOL_FUNCTIONS = (gateway_audit_tail,)


def register(mcp, cfg) -> int:
    global _CFG
    if not getattr(cfg, "audit_read_enabled", False):
        cfg.disabled_categories["audit_read"] = "MIRU_AUDIT_READ_ENABLED not set"
        return 0
    _CFG = cfg
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
