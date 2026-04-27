"""Append-only JSONL audit logs for MCP Gateway write tools.

Rotation: when the active log exceeds 10 MiB, rename to ``.jsonl.1`` and shift
``.1`` → ``.5`` (drop ``.6+``), matching the operator contract for PRO-122.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROTATE_BYTES = 10 * 1024 * 1024
_MAX_ROTATED_SUFFIX = 5


def _utc_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rotate_if_needed(path: Path) -> None:
    if not path.exists():
        return
    try:
        if path.stat().st_size < _ROTATE_BYTES:
            return
    except OSError:
        return

    # Drop oldest backup (.5) so we never keep more than .1.. .5
    oldest = path.with_name(f"{path.name}.{_MAX_ROTATED_SUFFIX}")
    if oldest.exists():
        with contextlib.suppress(OSError):
            oldest.unlink()

    # Shift .4 -> .5, .3 -> .4, ... .1 -> .2
    for k in range(_MAX_ROTATED_SUFFIX - 1, 0, -1):
        src = path.with_name(f"{path.name}.{k}")
        dst = path.with_name(f"{path.name}.{k + 1}")
        if src.exists():
            with contextlib.suppress(OSError):
                if dst.exists():
                    dst.unlink()
                shutil.move(str(src), str(dst))

    # Active -> .1
    dst1 = path.with_name(f"{path.name}.1")
    with contextlib.suppress(OSError):
        if dst1.exists():
            dst1.unlink()
        shutil.move(str(path), str(dst1))


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    """Append one JSON object as a single line. Creates parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(path)
    line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def caller_from_fastmcp_context(ctx: Any | None) -> str:
    """Best-effort worker / client id from FastMCP Context (optional)."""
    if ctx is None:
        return "unknown"
    try:
        meta = None
        rc = getattr(ctx, "request_context", None)
        if rc is not None:
            meta = getattr(rc, "meta", None)
        if meta is not None:
            for attr in ("worker_id", "user_id", "client_name", "operator"):
                if hasattr(meta, attr):
                    val = getattr(meta, attr)
                    if val:
                        return str(val)
        cid = getattr(ctx, "client_id", None) or getattr(ctx, "session_id", None)
        if cid:
            return str(cid)
    except Exception:
        pass
    return "unknown"


def default_audit_paths(repo_root: Path) -> tuple[Path, Path]:
    """Standard locations under repo ``logs/``."""
    log_dir = repo_root / "logs"
    return (
        log_dir / "mcp_gateway_writes.jsonl",
        log_dir / "mcp_gateway_docs_writes.jsonl",
    )


def notify_approval_webhook(url: str | None, request_id: str) -> None:
    """Fire-and-forget POST to n8n webhook so Telegram notify workflow runs."""
    if not url or not url.strip():
        return
    try:
        import requests  # type: ignore
    except ImportError:
        return
    # Never fail the tool on notify errors; operator can poll JSONL.
    with contextlib.suppress(Exception):
        requests.post(
            url.strip(),
            json={"request_id": request_id},
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
