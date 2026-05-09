"""Tests for windows/install_dispatch_listener_startup_shortcut.ps1 (PRO-336).

Covers:
- Shortcut path resolution: shortcut is created in the override startup folder.
- Idempotent re-run: running the installer twice does not overwrite an already-
  correct shortcut (mtime is preserved, exit code stays 0).
- Missing-script error path: installer exits non-zero when the wrapper script
  does not exist.

These tests run the PowerShell script via subprocess using -StartupFolder and
-WrapperScript override parameters (test-only), so they never touch the real
shell:startup folder and do not require elevation.

Skipped automatically on non-Windows platforms where powershell.exe is absent.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "windows" / "install_dispatch_listener_startup_shortcut.ps1"
REAL_WRAPPER = REPO_ROOT / "windows" / "start_dispatch_listener.ps1"

# Skip the entire module on platforms without PowerShell
_powershell = shutil.which("powershell.exe") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(
    _powershell is None,
    reason="powershell.exe not found -- skipping Windows-only tests",
)


def _run_installer(
    startup_folder: Path,
    wrapper_script: Path | None = None,
) -> subprocess.CompletedProcess:
    """Run the installer with override parameters for test isolation."""
    args = [
        _powershell,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(INSTALLER),
        "-StartupFolder",
        str(startup_folder),
    ]
    if wrapper_script is not None:
        args += ["-WrapperScript", str(wrapper_script)]
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# Shortcut path resolution
# ---------------------------------------------------------------------------


def test_shortcut_created_in_startup_folder():
    """Installer creates MiruDispatchListener.lnk in the override folder."""
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        result = _run_installer(startup, wrapper_script=REAL_WRAPPER)
        assert result.returncode == 0, f"installer failed:\n{result.stdout}\n{result.stderr}"
        shortcut = startup / "MiruDispatchListener.lnk"
        assert shortcut.exists(), f"shortcut not found at {shortcut}"


def test_shortcut_name_is_fixed():
    """Shortcut file name is always MiruDispatchListener.lnk."""
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        _run_installer(startup, wrapper_script=REAL_WRAPPER)
        lnk_files = list(startup.glob("*.lnk"))
        assert len(lnk_files) == 1, f"expected exactly one .lnk, got {lnk_files}"
        assert lnk_files[0].name == "MiruDispatchListener.lnk"


# ---------------------------------------------------------------------------
# Idempotent re-run
# ---------------------------------------------------------------------------


def test_idempotent_rerun_does_not_overwrite():
    """Running the installer twice leaves the shortcut mtime unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        r1 = _run_installer(startup, wrapper_script=REAL_WRAPPER)
        assert r1.returncode == 0, f"first run failed:\n{r1.stdout}\n{r1.stderr}"

        shortcut = startup / "MiruDispatchListener.lnk"
        mtime_after_first_run = shortcut.stat().st_mtime_ns

        r2 = _run_installer(startup, wrapper_script=REAL_WRAPPER)
        assert r2.returncode == 0, f"second run failed:\n{r2.stdout}\n{r2.stderr}"

        mtime_after_second_run = shortcut.stat().st_mtime_ns
        assert (
            mtime_after_first_run == mtime_after_second_run
        ), "Shortcut was overwritten on second run -- idempotency violated"


def test_idempotent_rerun_logs_skipping():
    """Second run stdout must confirm it skipped re-creation."""
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        _run_installer(startup, wrapper_script=REAL_WRAPPER)
        r2 = _run_installer(startup, wrapper_script=REAL_WRAPPER)
        combined = r2.stdout + r2.stderr
        assert (
            "skipping" in combined.lower() or "idempotent" in combined.lower()
        ), f"Expected 'skipping' or 'idempotent' in second-run output, got:\n{combined}"


# ---------------------------------------------------------------------------
# Missing-script error path
# ---------------------------------------------------------------------------


def test_missing_wrapper_script_exits_nonzero():
    """Installer exits non-zero when the wrapper script does not exist."""
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        nonexistent = Path(tmp) / "does_not_exist.ps1"
        result = _run_installer(startup, wrapper_script=nonexistent)
        assert result.returncode != 0, (
            "Expected non-zero exit when wrapper script is missing, got 0\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


def test_missing_wrapper_script_no_shortcut_created():
    """When wrapper script is missing, no .lnk file is left behind."""
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        nonexistent = Path(tmp) / "does_not_exist.ps1"
        _run_installer(startup, wrapper_script=nonexistent)
        lnk_files = list(startup.glob("*.lnk"))
        assert lnk_files == [], f"Unexpected .lnk files created on error: {lnk_files}"


def test_missing_wrapper_script_error_message():
    """Installer output must mention the missing script path."""
    with tempfile.TemporaryDirectory() as tmp:
        startup = Path(tmp)
        nonexistent = Path(tmp) / "does_not_exist.ps1"
        result = _run_installer(startup, wrapper_script=nonexistent)
        combined = result.stdout + result.stderr
        # The installer should mention the bad path or 'not found' in its output
        assert (
            "not found" in combined.lower() or "does_not_exist" in combined.lower()
        ), f"Expected error message in output, got:\n{combined}"
