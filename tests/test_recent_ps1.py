"""Smoke tests for tools/scripts/recent.ps1.

Boundary-crossing tests per PRO-189 lesson: invokes the actual .ps1 file via
pwsh subprocess against a controlled fixture log. Catches script-level bugs
that wouldn't surface in a pure unit test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "scripts" / "recent.ps1"


def _pwsh_available() -> bool:
    return shutil.which("pwsh") is not None


pytestmark = pytest.mark.skipif(
    not _pwsh_available(),
    reason="pwsh (PowerShell 7+) not installed in test environment",
)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = ["pwsh", "-NoProfile", "-File", str(SCRIPT_PATH), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        timeout=30,
        check=False,
    )


def _write_fixture(rows: list[dict]) -> Path:
    fd, path_str = tempfile.mkstemp(suffix=".jsonl", text=True)
    os.close(fd)
    path = Path(path_str)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return path


def test_script_exists():
    assert SCRIPT_PATH.exists(), f"recent.ps1 missing at {SCRIPT_PATH}"


def test_empty_log_produces_friendly_message():
    fixture = _write_fixture([])
    try:
        result = _run(["-LogPath", str(fixture)])
        assert result.returncode == 0
        assert "No completed tasks yet." in result.stdout
    finally:
        fixture.unlink()


def test_renders_basic_entry():
    rows = [
        {
            "timestamp": "2026-05-05T10:00:00Z",
            "ticket_id": "TEST-001",
            "status": "CONFIRMED_WORKING",
            "summary": "Test entry one",
            "branch": "test/branch",
            "pr_number": 999,
            "files_touched": ["foo/bar.py"],
            "test_evidence": "smoke",
        }
    ]
    fixture = _write_fixture(rows)
    try:
        result = _run(["-LogPath", str(fixture)])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "TEST-001" in result.stdout
        assert "CONFIRMED_WORKING" in result.stdout
        assert "Test entry one" in result.stdout
        assert "PR #999" in result.stdout
    finally:
        fixture.unlink()


def test_count_arg_limits_output():
    rows = [
        {
            "timestamp": f"2026-05-05T10:0{i}:00Z",
            "ticket_id": f"TEST-{i:03d}",
            "status": "CONFIRMED_WORKING",
            "summary": f"Entry {i}",
        }
        for i in range(5)
    ]
    fixture = _write_fixture(rows)
    try:
        result = _run(["-Count", "2", "-LogPath", str(fixture)])
        assert result.returncode == 0
        # Count=2 should include the last two entries (TEST-003, TEST-004)
        # and exclude the older ones
        assert "TEST-004" in result.stdout
        assert "TEST-003" in result.stdout
        assert "TEST-000" not in result.stdout
        assert "TEST-001" not in result.stdout
    finally:
        fixture.unlink()


def test_mojibake_repair_em_dash():
    # cc_completion_log.jsonl historically contains rows where UTF-8 em-dashes
    # were stored as the cp1252 mis-decoding "â€”". Verify the script
    # repairs these so users see clean text.
    rows = [
        {
            "timestamp": "2026-05-05T10:00:00Z",
            "ticket_id": "TEST-MOJI",
            "status": "CONFIRMED_WORKING",
            # This is the cp1252-mojibake form of em-dash (—)
            "summary": "Phase 4 â€” Ingress Classifier shipped",
        }
    ]
    fixture = _write_fixture(rows)
    try:
        result = _run(["-LogPath", str(fixture)])
        assert result.returncode == 0
        # After repair, the em-dash should be present and the mojibake gone
        assert "—" in result.stdout
        assert "â" not in result.stdout
    finally:
        fixture.unlink()


def test_malformed_json_skipped_with_warning():
    fixture = _write_fixture([])
    try:
        with fixture.open("w", encoding="utf-8") as f:
            f.write(
                '{"timestamp": "2026-05-05T10:00:00Z", "ticket_id": "GOOD-1", "status": "CONFIRMED_WORKING", "summary": "good"}\n'
            )
            f.write("not valid json at all\n")
            f.write(
                '{"timestamp": "2026-05-05T11:00:00Z", "ticket_id": "GOOD-2", "status": "CONFIRMED_WORKING", "summary": "good"}\n'
            )
        result = _run(["-LogPath", str(fixture)])
        assert result.returncode == 0
        assert "GOOD-1" in result.stdout
        assert "GOOD-2" in result.stdout
        assert "skipped" in result.stdout.lower()
    finally:
        fixture.unlink()


def test_runs_from_outside_repo_via_psscriptroot_fallback():
    # When invoked with an absolute path from a non-git directory (e.g.
    # operator running `pwsh D:\path\to\recent.ps1` from their home dir),
    # `git rev-parse` fails. Script must fall back to PSScriptRoot to
    # locate the repo. This was a real regression.
    cwd_outside = Path(tempfile.mkdtemp())
    try:
        cmd = ["pwsh", "-NoProfile", "-File", str(SCRIPT_PATH), "-Count", "1"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=cwd_outside,
            timeout=30,
            check=False,
        )
        # Either it finds the repo via PSScriptRoot fallback (preferred) or it
        # errors cleanly. It must NOT crash with a null-method-call exception.
        assert result.returncode == 0 or "Could not resolve repo root" in result.stderr
        assert "null-valued expression" not in result.stderr
    finally:
        cwd_outside.rmdir()


def test_count_clamped_to_minimum_one():
    rows = [
        {
            "timestamp": "2026-05-05T10:00:00Z",
            "ticket_id": "TEST-CLAMP",
            "status": "CONFIRMED_WORKING",
            "summary": "clamp test",
        }
    ]
    fixture = _write_fixture(rows)
    try:
        # Count of 0 or negative should clamp to 1, not crash or return empty
        result = _run(["-Count", "0", "-LogPath", str(fixture)])
        assert result.returncode == 0
        assert "TEST-CLAMP" in result.stdout
    finally:
        fixture.unlink()
