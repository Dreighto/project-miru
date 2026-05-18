"""Tests for the JSONL-based operator-override-rate metric."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.shadow_loop.override_metric import current_override_rate, should_halt_loop


def _now() -> datetime:
    return datetime.now(UTC)


def _write_events(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _event(verdict: str, ts: datetime) -> dict:
    return {
        "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "canonical_code": "OP01-001",
        "print_id": "OP01-001",
        "contributing_model": "qwen2.5:7b",
        "verdict": verdict,
        "verifier_outcome": "verified-correct",
    }


def test_no_file_means_rate_zero_and_no_halt(tmp_path: Path):
    missing = tmp_path / "nope.jsonl"
    assert current_override_rate(jsonl_path=missing) == 0.0
    assert should_halt_loop(jsonl_path=missing) is False


def test_empty_file_means_rate_zero(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.write_text("", encoding="utf-8")
    assert current_override_rate(jsonl_path=path) == 0.0
    assert should_halt_loop(jsonl_path=path) is False


def test_rate_computed_from_in_window_events(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    now = _now()
    _write_events(
        path,
        [
            _event("agree", now - timedelta(hours=1)),
            _event("agree", now - timedelta(hours=2)),
            _event("override", now - timedelta(hours=3)),
            _event("agree", now - timedelta(hours=4)),
        ],
    )
    assert current_override_rate(window_hours=24, jsonl_path=path) == pytest.approx(0.25)


def test_old_events_outside_window_excluded(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    now = _now()
    _write_events(
        path,
        [
            _event("override", now - timedelta(hours=50)),  # outside 24h window
            _event("override", now - timedelta(hours=72)),  # outside
            _event("agree", now - timedelta(hours=1)),  # in window
        ],
    )
    # Only 1 in-window event, 0 overrides → 0.0
    assert current_override_rate(window_hours=24, jsonl_path=path) == 0.0


def test_halt_when_rate_meets_threshold(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    now = _now()
    # 3 overrides, 7 agreements = 30% > 5% default
    events = [_event("override", now - timedelta(hours=1)) for _ in range(3)] + [
        _event("agree", now - timedelta(hours=2)) for _ in range(7)
    ]
    _write_events(path, events)
    assert should_halt_loop(threshold=0.05, jsonl_path=path) is True


def test_no_halt_when_rate_below_threshold(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    now = _now()
    # 1 override, 99 agreements = 1% < 5%
    events = [_event("override", now - timedelta(hours=1))] + [
        _event("agree", now - timedelta(hours=2)) for _ in range(99)
    ]
    _write_events(path, events)
    assert should_halt_loop(threshold=0.05, jsonl_path=path) is False


def test_halt_respects_custom_threshold(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    now = _now()
    # 1 override, 9 agreements = 10%
    events = [_event("override", now - timedelta(hours=1))] + [
        _event("agree", now - timedelta(hours=2)) for _ in range(9)
    ]
    _write_events(path, events)
    # At 5% threshold: halt (10% > 5%)
    assert should_halt_loop(threshold=0.05, jsonl_path=path) is True
    # At 25% threshold: no halt (10% < 25%)
    assert should_halt_loop(threshold=0.25, jsonl_path=path) is False


def test_malformed_lines_are_skipped(tmp_path: Path):
    """A garbage line in the JSONL must not crash the metric."""
    path = tmp_path / "events.jsonl"
    now = _now()
    lines = [
        json.dumps(_event("agree", now - timedelta(hours=1))),
        "{not valid json",
        json.dumps(_event("override", now - timedelta(hours=2))),
        "",
        "  ",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    # Valid: 1 agree + 1 override = 50% rate
    assert current_override_rate(window_hours=24, jsonl_path=path) == 0.5


def test_unparseable_timestamps_are_skipped(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    now = _now()
    bad = _event("override", now)
    bad["ts"] = "not-a-timestamp"
    _write_events(
        path,
        [bad, _event("agree", now - timedelta(hours=1))],
    )
    # Only the valid one counts → 0% override rate
    assert current_override_rate(window_hours=24, jsonl_path=path) == 0.0
