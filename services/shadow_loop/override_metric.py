"""Operator-override-rate halt guard (PR-C, PRO-908).

Reads append-only JSONL at `data/shadow_loop_verifier_overrides.jsonl` —
written by PRO-909's dev page each time the operator overrides a verifier
verdict. Each event is one override.

`current_override_rate(window_hours)` returns the fraction of operator
reviews in the look-back window that disagreed with the verifier. The
denominator is total reviews in the window (override + agree events both
land in the JSONL). `should_halt_loop(threshold)` returns True when that
rate exceeds the threshold — the loop runner halts gracefully on True.

Why JSONL not DB: operator overrides are *events*, not state. A row's
`approval_state` can flip multiple times; we want to count every
override, not the latest state. JSONL is also what PRO-909 already writes
to (per the design conversation).

Expected event schema (append-only):

    {
        "ts": "2026-05-18T03:46:23Z",      # ISO 8601 UTC
        "canonical_code": "OP01-001",
        "print_id": "OP01-001",
        "contributing_model": "qwen2.5:7b",
        "verdict": "agree" | "override",   # operator's call
        "verifier_outcome": "verified-correct" | "verified-wrong" | "inconclusive",
        "operator": "<who>"                  # optional
    }
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_WINDOW_HOURS: int = 24
DEFAULT_HALT_THRESHOLD: float = 0.05


def _default_jsonl_path() -> Path:
    from .config import load as _load_config

    return _load_config().learning_pool_db.parent / "shadow_loop_verifier_overrides.jsonl"


def _parse_ts(ts: str) -> datetime | None:
    try:
        # Accept "...Z" or "+00:00" suffix; fall back to ISO without tz.
        cleaned = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(cleaned)
    except (ValueError, TypeError):
        return None
    else:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt


def _load_events_in_window(jsonl_path: Path, window_hours: int) -> tuple[int, int]:
    """Return (total_reviews_in_window, override_count_in_window)."""
    if not jsonl_path.exists():
        return (0, 0)
    cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
    total = 0
    overrides = 0
    with jsonl_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed JSONL line in %s", jsonl_path)
                continue
            ts = _parse_ts(event.get("ts", ""))
            if ts is None or ts < cutoff:
                continue
            total += 1
            if event.get("verdict") == "override":
                overrides += 1
    return (total, overrides)


def current_override_rate(
    window_hours: int = DEFAULT_WINDOW_HOURS,
    jsonl_path: Path | None = None,
) -> float:
    """Return fraction of reviews in `window_hours` that were operator overrides.

    Returns 0.0 if the JSONL is missing or no reviews fall in the window.
    """
    if jsonl_path is None:
        jsonl_path = _default_jsonl_path()
    total, overrides = _load_events_in_window(jsonl_path, window_hours)
    if total == 0:
        return 0.0
    return overrides / total


def should_halt_loop(
    threshold: float | None = None,
    window_hours: int | None = None,
    jsonl_path: Path | None = None,
) -> bool:
    """Return True when override rate ≥ threshold over the look-back window.

    Defaults pulled from env vars (SHADOW_LOOP_OVERRIDE_HALT_THRESHOLD,
    SHADOW_LOOP_OVERRIDE_WINDOW_HOURS) then fall back to module constants.
    """
    if threshold is None:
        threshold = float(
            os.environ.get("SHADOW_LOOP_OVERRIDE_HALT_THRESHOLD", DEFAULT_HALT_THRESHOLD)
        )
    if window_hours is None:
        window_hours = int(
            os.environ.get("SHADOW_LOOP_OVERRIDE_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)
        )
    if jsonl_path is None:
        jsonl_path = _default_jsonl_path()
    total, overrides = _load_events_in_window(jsonl_path, window_hours)
    if total == 0:
        return False
    rate = overrides / total
    if rate >= threshold:
        log.warning(
            "override-rate halt: %d/%d reviews in last %dh are overrides "
            "(%.0f%% >= threshold %.0f%%) — halting loop",
            overrides,
            total,
            window_hours,
            rate * 100,
            threshold * 100,
        )
        return True
    return False
