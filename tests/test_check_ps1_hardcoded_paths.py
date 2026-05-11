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
        'Register-ScheduledTask -TaskName "Foo" -Description "Managed by $startupScript"\n',
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
        'if (Test-Path "D:\\dev\\miru\\windows\\startup_all.ps1") {\n    Write-Host "found"\n}\n',
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


def test_no_input_files_passes() -> None:
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


def test_utf16_ps1_still_scanned(tmp_path: Path) -> None:
    """CR R2 on PR #3: PowerShell scripts on Windows are commonly saved as
    UTF-16 (default for `Out-File`). The previous read used encoding="utf-8"
    and caught UnicodeDecodeError, silently bypassing the hook on UTF-16
    files. Verify the robust read tries UTF-16 and still detects the same
    bad pattern."""
    f = tmp_path / "bad_utf16.ps1"
    content = (
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by D:\\dev\\miru\\windows\\startup_all.ps1"\n'
    )
    # UTF-16 with BOM — what `Out-File` produces on Windows by default.
    f.write_text(content, encoding="utf-16")
    result = _run(f)
    assert result.returncode == 1, f"UTF-16 file should still be scanned; stderr: {result.stderr}"
    assert "startup_all.ps1" in result.stderr


def test_windows_forward_slash_path_in_description_fails(tmp_path: Path) -> None:
    """CR R2 on PR #3: `D:/dev/...` is a legal Windows path notation (PowerShell
    accepts both `\\` and `/` as separators). The previous regex matched only
    backslashes, so forward-slash variants were a silent bypass."""
    f = tmp_path / "bad_win_slash.ps1"
    f.write_text(
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by D:/dev/miru/windows/startup_all.ps1"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1
    assert "hardcoded absolute path" in result.stderr
    assert "startup_all.ps1" in result.stderr


def test_single_quoted_description_fails(tmp_path: Path) -> None:
    """CR R3 on PR #3: PowerShell accepts both 'literal' and \"interpolated\"
    string forms. The previous regex matched only double quotes — a worker
    could have bypassed the hook by writing the offending Description as a
    single-quoted PowerShell literal."""
    f = tmp_path / "bad_single_quote.ps1"
    f.write_text(
        "Register-ScheduledTask -TaskName 'Foo' "
        "-Description 'Managed by D:\\dev\\miru\\windows\\startup_all.ps1'\n",
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1
    assert "hardcoded absolute path" in result.stderr
    assert "startup_all.ps1" in result.stderr


def test_mixed_quote_styles_not_matched_as_one_description(tmp_path: Path) -> None:
    """The regex captures the closing quote via backreference, so a stray
    `-Description "...` followed later by `'...'` is NOT collapsed into one
    multi-line description body. Defense against the backreference being
    accidentally relaxed."""
    f = tmp_path / "mixed.ps1"
    f.write_text(
        'Register-ScheduledTask -TaskName "First" '
        "-Description 'safe single-quoted'\n"
        'Register-ScheduledTask -TaskName "Second" '
        '-Description "safe double-quoted"\n',
        encoding="utf-8",
    )
    result = _run(f)
    # Two clean descriptions, no offending paths → exit 0.
    assert result.returncode == 0, f"unexpected stderr: {result.stderr}"


def test_multiline_description_with_backtick_continuation(tmp_path: Path) -> None:
    """PowerShell supports multi-line string literals; DOTALL coverage means
    a path embedded across newlines should still be caught."""
    f = tmp_path / "multiline.ps1"
    f.write_text(
        "Register-ScheduledTask -TaskName 'Foo' `\n"
        '    -Description "Step 1: do thing.\n'
        "Step 2: managed by D:\\dev\\miru\\windows\\startup_all.ps1\n"
        'Step 3: done."\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1
    assert "startup_all.ps1" in result.stderr


def test_utf16_le_no_bom_still_scanned(tmp_path: Path) -> None:
    """CR R4 on PR #193: UTF-16 LE without a BOM is what `Out-File` produces
    in certain PowerShell modes. The robust read must try utf-16-le even when
    no BOM is present, otherwise the file decodes as garbage under utf-8 and
    silently bypasses detection. Write raw bytes via .encode('utf-16-le')
    which omits the BOM (.encode('utf-16') would include one)."""
    f = tmp_path / "bad_utf16le_nobom.ps1"
    line = (
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by D:\\dev\\miru\\windows\\startup_all.ps1"\n'
    )
    f.write_bytes(line.encode("utf-16-le"))
    result = _run(f)
    assert result.returncode == 1, f"UTF-16-LE no-BOM should be scanned; stderr: {result.stderr}"
    assert "startup_all.ps1" in result.stderr


def test_utf16_be_no_bom_still_scanned(tmp_path: Path) -> None:
    """CR R4 on PR #193: same as the LE case for UTF-16 big-endian."""
    f = tmp_path / "bad_utf16be_nobom.ps1"
    line = (
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by D:\\dev\\miru\\windows\\startup_all.ps1"\n'
    )
    f.write_bytes(line.encode("utf-16-be"))
    result = _run(f)
    assert result.returncode == 1, f"UTF-16-BE no-BOM should be scanned; stderr: {result.stderr}"
    assert "startup_all.ps1" in result.stderr


def test_uppercase_ps1_extension_still_scanned(tmp_path: Path) -> None:
    """CR R4 on PR #3: Windows file systems are case-insensitive, so `.PS1`
    and `.Ps1` are the same file as `.ps1`. The hook must trigger on all
    casings — the .pre-commit-config.yaml regex now uses `(?i)` and the
    Python suffix check uses `.lower()`."""
    f = tmp_path / "bad_uppercase.PS1"
    f.write_text(
        'Register-ScheduledTask -TaskName "Foo" '
        '-Description "Managed by D:\\dev\\miru\\windows\\startup_all.ps1"\n',
        encoding="utf-8",
    )
    result = _run(f)
    assert result.returncode == 1, f"uppercase .PS1 should be scanned; stderr: {result.stderr}"
    assert "startup_all.ps1" in result.stderr
