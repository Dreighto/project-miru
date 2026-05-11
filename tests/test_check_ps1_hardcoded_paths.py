"""Tests for tools/check_ps1_hardcoded_paths.py — the pre-commit hook that
rejects hardcoded absolute paths inside PowerShell -Description args.

Background: CodeRabbit caught a regression on 2026-05-11 where
`register_restart_tasks.ps1` embedded a literal `D:\\dev\\...` path into
a scheduled-task Description. The hook here is the structural guard that
prevents the same class of bug from recurring.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "check_ps1_hardcoded_paths.py"


def _run(*ps1_files: Path) -> subprocess.CompletedProcess[str]:
    """Run the detector against the given files, capture exit + stderr."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), *[str(f) for f in ps1_files]],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_description_passes(tmp_path: Path) -> None:
    """A -Description that references a $variable should pass."""
    f = tmp_path / "clean.ps1"
    f.write_text(
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by $startupScript"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 0, f"stderr: {result.stderr}"


def test_windows_absolute_path_in_description_fails(tmp_path: Path) -> None:
    """The exact pattern CR caught: a `D:\\dev\\...` path inside -Description."""
    f = tmp_path / "bad_win.ps1"
    f.write_text(
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by D:\\dev\\miru\\windows\\startup_all.ps1"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1
    assert "hardcoded absolute path" in result.stderr
    # Stderr contains the offending path via Python's repr() — double-escaped
    # backslashes are expected. Check the trailing filename which is unambiguous.
    assert "startup_all.ps1" in result.stderr


def test_posix_absolute_path_in_description_fails(tmp_path: Path) -> None:
    """Linux/macOS analog — `/home/<user>/...` paths inside -Description."""
    f = tmp_path / "bad_posix.ps1"
    f.write_text(
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by /home/dreighto/dev/miru/windows/startup_all.ps1"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1
    assert "hardcoded absolute path" in result.stderr


def test_absolute_path_outside_description_ignored(tmp_path: Path) -> None:
    """Test-Path against a literal absolute path is NOT a -Description; the hook
    must NOT flag it. Avoids false-positive churn on legitimate file-existence
    checks, examples in help text, etc."""
    f = tmp_path / "elsewhere.ps1"
    f.write_text(
        'if (Test-Path "D:\\dev\\miru\\windows\\startup_all.ps1") {\n'
        '    Write-Host "found"\n'
        '}\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 0, f"stderr: {result.stderr}"


def test_multiple_findings_all_reported(tmp_path: Path) -> None:
    """Two offending Descriptions in the same file → both reported, single
    non-zero exit."""
    f = tmp_path / "multi.ps1"
    f.write_text(
        'Register-ScheduledTask -TaskName "A" '
        '-Description "Managed by D:\\dev\\miru\\windows\\one.ps1"\n'
        'Register-ScheduledTask -TaskName "B" '
        '-Description "Managed by D:\\dev\\miru\\windows\\two.ps1"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1
    assert "one.ps1" in result.stderr
    assert "two.ps1" in result.stderr


def test_non_ps1_files_skipped(tmp_path: Path) -> None:
    """The hook is filtered by .pre-commit-config.yaml to .ps1 files only, but
    the script must also short-circuit non-.ps1 inputs defensively (in case it's
    invoked manually with a wildcard)."""
    f = tmp_path / "bad_pattern_but_wrong_ext.txt"
    f.write_text(
        'Register-ScheduledTask -Description "Managed by D:\\dev\\miru\\foo"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 0


def test_no_input_files_passes(tmp_path: Path) -> None:
    """pre-commit may invoke the hook with no matching files. Hook must exit 0."""
    result = _run()
    assert result.returncode == 0


def test_case_insensitive_description_match(tmp_path: Path) -> None:
    """PowerShell parameter names are case-insensitive. The hook must catch
    both `-Description` and `-description`."""
    f = tmp_path / "case.ps1"
    f.write_text(
        'Register-ScheduledTask -description "Managed by D:\\dev\\miru\\x.ps1"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1
