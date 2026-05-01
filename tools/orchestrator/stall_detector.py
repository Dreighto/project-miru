"""
Stall detector — reads heartbeat and completion logs to find stalled workers.

Mirrors the logic in worker_tools.py:worker_availability but runs standalone
(no MCP gateway context required) so the recovery scheduler can call it directly.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_HEARTBEAT_LOG = _REPO_ROOT / "data" / "cc_heartbeat_log.jsonl"
_COMPLETION_LOG = _REPO_ROOT / "data" / "cc_completion_log.jsonl"

STALL_THRESHOLD_S = 300  # 5 min — matches worker_tools._IDLE_THRESHOLD_S
_HEARTBEAT_READ_LINES = 500
_COMPLETION_READ_LINES = 1000


@dataclass
class StallEvent:
    worker_id: str
    ticket_id: str | None
    step: str | None
    branch: str | None
    last_heartbeat_ts: str
    stall_age_seconds: float
    stall_signal: str | None = None


def _read_last_jsonl(path: Path, n: int) -> list[dict]:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = []
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


def detect_stalls() -> list[StallEvent]:
    """Return a StallEvent for every worker whose last heartbeat is older than
    STALL_THRESHOLD_S and has no matching completion marker."""
    hb_rows = _read_last_jsonl(_HEARTBEAT_LOG, _HEARTBEAT_READ_LINES)

    # Latest heartbeat per worker_id
    latest: dict[str, dict] = {}
    for row in hb_rows:
        wid = str(row.get("worker_id", "")).strip()
        if not wid:
            continue
        existing = latest.get(wid)
        if existing is None or str(row.get("ts", "")) > str(existing.get("ts", "")):
            latest[wid] = row

    # Completed ticket IDs
    cp_rows = _read_last_jsonl(_COMPLETION_LOG, _COMPLETION_READ_LINES)
    completed_tickets: set[str] = set()
    for row in cp_rows:
        tid = str(row.get("ticket_id", "")).strip()
        if tid and tid.lower() != "null":
            completed_tickets.add(tid)

    now_utc = datetime.datetime.now(datetime.UTC)
    stalls: list[StallEvent] = []

    for wid, row in latest.items():
        ts_str = str(row.get("ts", "")).strip()
        if not ts_str:
            continue
        try:
            hb_ts = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, OverflowError):
            continue

        age_s = (now_utc - hb_ts).total_seconds()
        if age_s <= STALL_THRESHOLD_S:
            continue

        ticket = str(row.get("ticket_id", "")).strip() or None
        if ticket and ticket in completed_tickets:
            continue

        stalls.append(
            StallEvent(
                worker_id=wid,
                ticket_id=ticket,
                step=str(row.get("step", "")).strip() or None,
                branch=str(row.get("branch", "")).strip() or None,
                last_heartbeat_ts=ts_str,
                stall_age_seconds=age_s,
                stall_signal=str(row.get("stall_signal", "")).strip() or None,
            )
        )

    return stalls
