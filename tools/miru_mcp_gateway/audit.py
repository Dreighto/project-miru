"""Append-only JSONL audit logs for MCP Gateway write tools.

Rotation: when the active log exceeds 10 MiB, rename to ``.jsonl.1`` and shift
``.1`` → ``.5`` (drop ``.6+``), matching the operator contract for PRO-122.
"""

from __future__ import annotations

import contextlib
import hashlib
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
    """Append one JSON object as a single line. Creates parent dirs.

    Prefer :func:`append_jsonl_chained` for gateway audit logs (PRO-135).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(path)
    line = json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def _read_last_json_object_for_chain(path: Path) -> dict[str, Any] | None:
    """Return the last JSON object on ``path`` if parseable, else None."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        size = path.stat().st_size
        chunk = min(size, 65536)
        with path.open("rb") as fh:
            fh.seek(size - chunk)
            data = fh.read()
        text = data.decode("utf-8", errors="replace")
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except ValueError:
                continue
    except OSError:
        return None
    return None


def append_jsonl_chained(path: Path, row: dict[str, Any]) -> None:
    """Append one audit row with SHA256 hash chain (PRO-135).

    ``row`` must not contain ``prev_hash`` or ``row_hash``; they are added
    here. Legacy tail rows without ``row_hash`` start a fresh chain link
    (``prev_hash`` is null).
    """
    body = {k: v for k, v in row.items() if k not in ("prev_hash", "row_hash")}
    last = _read_last_json_object_for_chain(path)
    prev_hash: str | None = None
    if last and isinstance(last.get("row_hash"), str) and last["row_hash"]:
        prev_hash = str(last["row_hash"])
    body["prev_hash"] = prev_hash
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    row_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    final_row = {**body, "row_hash": row_hash}
    append_jsonl(path, final_row)


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


def default_audit_paths(repo_root: Path) -> tuple[Path, Path, Path]:
    """Standard locations under repo ``logs/``.

    Returns ``(writes_log, docs_writes_log, reads_log)``.
    """
    log_dir = repo_root / "logs"
    return (
        log_dir / "mcp_gateway_writes.jsonl",
        log_dir / "mcp_gateway_docs_writes.jsonl",
        log_dir / "mcp_gateway_reads.jsonl",
    )


def append_read_audit(repo_root: Path, row: dict[str, Any]) -> None:
    """Append one row to the read-audit JSONL (PRO-131 / PRO-132) with hash chain."""
    *_, reads_log = default_audit_paths(repo_root)
    append_jsonl_chained(reads_log, row)


def validate_audit_chain_slice(rows: list[dict[str, Any]]) -> tuple[bool, int | None]:
    """Verify per-row ``row_hash`` and intra-slice ``prev_hash`` links (PRO-135).

    The first row in the slice that carries ``row_hash`` is not required to
    chain to a predecessor outside the slice (tail-read of a longer log).
    """
    prev_row_hash: str | None = None
    first_hashed = True
    for i, row in enumerate(rows):
        rh = row.get("row_hash")
        if not rh:
            continue
        body = {k: v for k, v in row.items() if k != "row_hash"}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expect = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if expect != rh:
            return False, i
        if not first_hashed and body.get("prev_hash") != prev_row_hash:
            return False, i
        first_hashed = False
        prev_row_hash = rh
    return True, None


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
