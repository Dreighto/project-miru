"""PRO-136: per-worker git worktree status snapshot.
PRO-228: worker_availability — idle/busy state from heartbeat log.
"""

from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

_CFG: Any = None
_WORKERS: dict[str, Path] = {}
_TICKET_RE = re.compile(r"\b([A-Z]+-\d+)\b")


def _load_workers_yaml(path: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return out
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return out
    for item in data.get("workers") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        wp = item.get("worktree_path")
        if name and wp:
            out[name] = Path(str(wp))
    return out


def _parse_workers_env(raw: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece or ":" not in piece:
            continue
        name, pth = piece.split(":", 1)
        name = name.strip()
        pth = pth.strip().strip('"').strip("'")
        if name and pth:
            out[name] = Path(pth)
    return out


def _allowed_roots(cfg: Any) -> list[Path]:
    roots = [cfg.fs_root.resolve()]
    for pfx in getattr(cfg, "worker_path_allow_prefixes", ()) or ():
        if pfx:
            try:
                roots.append(Path(pfx).resolve())
            except OSError:
                continue
    return roots


def _path_under_allowed_roots(cfg: Any, path: Path) -> bool:
    try:
        p = path.resolve()
    except OSError:
        return False
    for r in _allowed_roots(cfg):
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _git_output(cwd: Path, args: list[str], timeout: float) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _one_worker(name: str, cwd: Path, timeout: float) -> dict[str, Any]:
    git_dir = cwd / ".git"
    if not (git_dir.exists()):
        return {"error": "worktree not found or not a git repo"}

    def g(args: list[str]) -> str:
        return _git_output(cwd, args, timeout)

    branch = g(["rev-parse", "--abbrev-ref", "HEAD"]) or "unknown"
    ticket = None
    m = _TICKET_RE.search(branch)
    if m:
        ticket = m.group(1)

    porcelain = g(["status", "--porcelain"])
    clean = porcelain == ""
    uncommitted: list[str] = []
    untracked: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < 4:
            continue
        code, fn = line[:2], line[3:].strip()
        if "?" in code:
            untracked.append(fn)
        else:
            uncommitted.append(fn)

    sha = g(["log", "-1", "--format=%h"]) or ""
    msg = g(["log", "-1", "--format=%s"]) or ""
    ts = g(["log", "-1", "--format=%cI"]) or ""

    upstream = g(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    ahead = behind = None
    if upstream:
        ac = g(["rev-list", "--count", f"{upstream}..HEAD"])
        bc = g(["rev-list", "--count", f"HEAD..{upstream}"])
        try:
            ahead = int(ac) if ac else 0
            behind = int(bc) if bc else 0
        except ValueError:
            ahead = behind = None
    else:
        ahead = behind = None

    stash_raw = g(["stash", "list"])
    stash_count = len([x for x in stash_raw.splitlines() if x.strip()]) if stash_raw else 0

    return {
        "worktree_path": str(cwd),
        "branch": branch,
        "branch_age_minutes": None,
        "clean": clean,
        "uncommitted_files": uncommitted[:200],
        "untracked_files": untracked[:200],
        "last_commit": {"sha": sha, "message_first_line": msg, "ts": ts},
        "ticket": ticket,
        "ahead_origin": ahead,
        "behind_origin": behind,
        "stash_count": stash_count,
    }


def worker_status(worker_name: str | None = None) -> str:
    """Return git/branch snapshot for configured workers (PRO-136)."""
    cfg = _CFG
    if cfg is None:
        raise stdio_mcp.McpError("worker_status: not configured", -32000)
    workers = _WORKERS
    if not workers:
        raise stdio_mcp.McpError("worker_status: no workers configured", -32000)
    if worker_name:
        wn = worker_name.strip()
        if wn not in workers:
            raise stdio_mcp.McpError(f"worker_status: unknown worker {wn!r}", -32602)
        targets = {wn: workers[wn]}
    else:
        targets = dict(workers)

    timeout = 2.0

    def job(item: tuple[str, Path]) -> tuple[str, dict[str, Any]]:
        n, cwd = item
        if not _path_under_allowed_roots(cfg, cwd):
            return n, {"error": f"path not under allowed roots: {cwd}"}
        try:
            return n, _one_worker(n, cwd, timeout)
        except OSError as exc:
            return n, {"error": repr(exc)}

    out: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        futs = [pool.submit(job, (n, p)) for n, p in targets.items()]
        for fut in futs:
            n, payload = fut.result()
            out[n] = payload

    return json.dumps(out, indent=2)


_IDLE_THRESHOLD_S = 300  # 5 minutes — no heartbeat in this window → idle
_HEARTBEAT_READ_LINES = 500
_COMPLETION_READ_LINES = 1000


def _read_last_jsonl(path: Path, n: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
        except ValueError:
            continue
    return rows[-n:]


def _match_heartbeat(worker_name: str, latest: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Find the latest heartbeat row for a configured worker name.

    Tries exact match first, then prefix match to handle suffixed IDs like
    'claude-code-1' matching configured name 'claude-code'.
    """
    if worker_name in latest:
        return latest[worker_name]
    for wid, row in latest.items():
        if wid.startswith(worker_name):
            return row
    return None


def worker_availability(worker_name: str | None = None) -> str:
    """Return idle/busy state per configured worker slot (PRO-228).

    Reads ``data/cc_heartbeat_log.jsonl`` (last 500 rows) to find each
    worker's most recent heartbeat. A worker is idle if:
      - No heartbeat found, OR
      - Last heartbeat is older than 5 minutes, OR
      - The heartbeat's ticket_id has a completion marker in
        ``data/cc_completion_log.jsonl``.

    ``worker_name``: optional filter; if omitted returns all configured slots.
    """
    cfg = _CFG
    if cfg is None:
        raise stdio_mcp.McpError("worker_availability: not configured", -32000)
    workers = _WORKERS
    if not workers:
        raise stdio_mcp.McpError("worker_availability: no workers configured", -32000)
    if worker_name:
        wn = worker_name.strip()
        if wn not in workers:
            raise stdio_mcp.McpError(f"worker_availability: unknown worker {wn!r}", -32602)
        targets = [wn]
    else:
        targets = sorted(workers.keys())

    # Build latest-heartbeat-per-worker_id map
    hb_rows = _read_last_jsonl(
        cfg.repo_root / "data" / "cc_heartbeat_log.jsonl", _HEARTBEAT_READ_LINES
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in hb_rows:
        wid = str(row.get("worker_id", "")).strip()
        if not wid:
            continue
        existing = latest.get(wid)
        if existing is None or str(row.get("ts", "")) > str(existing.get("ts", "")):
            latest[wid] = row

    # Collect completed ticket IDs from completion log
    cp_rows = _read_last_jsonl(
        cfg.repo_root / "data" / "cc_completion_log.jsonl", _COMPLETION_READ_LINES
    )
    completed_tickets: set[str] = set()
    for row in cp_rows:
        tid = str(row.get("ticket_id", "")).strip()
        if tid and tid.lower() != "null":
            completed_tickets.add(tid)

    now_utc = datetime.datetime.now(datetime.UTC)

    out: dict[str, Any] = {}
    for wid in targets:
        row = _match_heartbeat(wid, latest)
        if row is None:
            out[wid] = {
                "is_idle": True,
                "current_ticket": None,
                "current_step": None,
                "last_heartbeat_ts": None,
                "branch": str(workers[wid]) if wid in workers else None,
                "stall_signal": None,
            }
            continue

        ts_str = str(row.get("ts", "")).strip()
        ticket = str(row.get("ticket_id", "")).strip() or None

        is_idle = True
        if ts_str:
            try:
                hb_ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                is_idle = (now_utc - hb_ts).total_seconds() > _IDLE_THRESHOLD_S
            except (ValueError, OverflowError):
                is_idle = True

        # Completion marker overrides active heartbeat
        if ticket and ticket in completed_tickets:
            is_idle = True

        out[wid] = {
            "is_idle": is_idle,
            "current_ticket": ticket if not is_idle else None,
            "current_step": (str(row.get("step", "")).strip() or None) if not is_idle else None,
            "last_heartbeat_ts": ts_str or None,
            "branch": str(row.get("branch", "")).strip() or None,
            "stall_signal": (str(row.get("stall_signal", "")).strip() or None)
            if not is_idle
            else None,
        }

    return json.dumps(out, indent=2)


TOOL_FUNCTIONS = (worker_status, worker_availability)


def register(mcp, cfg) -> int:
    global _CFG, _WORKERS
    if not getattr(cfg, "worker_status_enabled", False):
        cfg.disabled_categories["worker_status"] = "MIRU_WORKER_STATUS_ENABLED not set"
        return 0
    _CFG = cfg
    merged: dict[str, Path] = {}
    if cfg.workers_yaml_path and cfg.workers_yaml_path.exists():
        merged.update(_load_workers_yaml(cfg.workers_yaml_path))
    merged.update(_parse_workers_env(cfg.workers_config_raw))
    _WORKERS = {}
    for name, pth in merged.items():
        if _path_under_allowed_roots(cfg, pth):
            _WORKERS[name] = pth.resolve()
    if not _WORKERS:
        cfg.disabled_categories["worker_status"] = "no valid worker paths after allowlist check"
        return 0
    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
