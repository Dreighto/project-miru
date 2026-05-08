"""DGAS Tier 1 #4 — fault-injection smoke test for the gitleaks pre-commit hook.

Verifies that the secret scanner gate actually fires when a known-bad token is
planted, and does not fire when only allowlisted content is present. Without
this test the gate is theatre — see synthesis doc item #7.

Skips cleanly when gitleaks is not on PATH (e.g. CI runners that have not
hydrated the pre-commit golang environment yet). The pre-commit framework is
the canonical install path; this test is a verification harness, not a
deployment dependency.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"

# Synthetic AWS access key that matches the default Gitleaks rule for
# AWS Access Key IDs (AKIA + 16 uppercase alphanumerics). NOT a real
# credential — these are deterministic placeholder characters.
PLANTED_AWS_KEY = "AKIA" + "ABCDEFGHIJKLMNOP"

# Allowlisted example value (AWS docs canonical example, 20 chars).
ALLOWLISTED_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _find_gitleaks() -> str | None:
    """Return the path to a gitleaks binary, or None if not available.

    Looks first on PATH, then in pre-commit's golang environment cache.
    Once `pre-commit install` has been run, gitleaks is reachable via the
    cache even if the binary is not on the user's global PATH.
    """
    on_path = shutil.which("gitleaks")
    if on_path:
        return on_path

    # pre-commit caches its golang environments under one of these roots,
    # depending on the OS and pre-commit version.
    cache_roots = [
        Path.home() / ".cache" / "pre-commit",
        Path(os.environ.get("LOCALAPPDATA", "")) / "pre-commit",
        Path(os.environ.get("PRE_COMMIT_HOME", "")),
    ]
    for root in cache_roots:
        if not root or not root.exists():
            continue
        for candidate in root.rglob("gitleaks*"):
            if candidate.is_file() and candidate.name in ("gitleaks", "gitleaks.exe"):
                return str(candidate)
    return None


def _gitleaks_available() -> bool:
    return _find_gitleaks() is not None


def _run_gitleaks(target_dir: Path) -> int:
    """Run gitleaks against a directory. Returns the exit code.

    Exit code 0 = no leaks. Exit code 1 = leaks found.
    """
    binary = _find_gitleaks()
    if binary is None:
        raise unittest.SkipTest("gitleaks binary not available")
    config_args: list[str] = []
    if GITLEAKS_CONFIG.exists():
        config_args = ["--config", str(GITLEAKS_CONFIG)]
    proc = subprocess.run(
        [binary, "detect", *config_args, "--source", str(target_dir), "--no-git"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode


@unittest.skipUnless(_gitleaks_available(), "gitleaks binary not on PATH")
class TestGitleaksGate(unittest.TestCase):
    """The gate must fire on a planted secret and stay silent on allowlisted content."""

    def setUp(self) -> None:
        # Use an OS-level temp dir, NOT tests/_tmp — the latter is in the
        # allowlist (.gitleaks.toml) so planted secrets there would be
        # silently ignored, masking gate failures.
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_gitleaks_smoke_"))
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)

    def test_planted_aws_key_triggers_gate(self) -> None:
        """Fault injection: a real-shaped AWS key in a normal file MUST be caught."""
        bad_file = self.tmp_root / "leaked.py"
        bad_file.write_text(
            f'AWS_ACCESS_KEY_ID = "{PLANTED_AWS_KEY}"\n'
            f'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCY{os.urandom(4).hex()}"\n',
            encoding="utf-8",
        )
        self.assertEqual(
            _run_gitleaks(self.tmp_root),
            1,
            "gitleaks did NOT fire on a planted AWS key — gate is broken",
        )

    def test_allowlisted_aws_example_does_not_trigger_gate(self) -> None:
        """Happy path: AWS canonical example token must not produce a finding."""
        ok_file = self.tmp_root / "docs_example.py"
        ok_file.write_text(
            f'# AWS docs canonical example\nAWS_ACCESS_KEY_ID = "{ALLOWLISTED_AWS_KEY}"\n',
            encoding="utf-8",
        )
        self.assertEqual(
            _run_gitleaks(self.tmp_root),
            0,
            "gitleaks fired on the allowlisted AWS example — allowlist is broken",
        )

    def test_clean_directory_does_not_trigger_gate(self) -> None:
        """Sanity: an empty directory produces no findings."""
        self.assertEqual(_run_gitleaks(self.tmp_root), 0)


class TestGitleaksConfigPresent(unittest.TestCase):
    """The .gitleaks.toml config must be checked in. Validate without running gitleaks."""

    def test_config_file_exists(self) -> None:
        self.assertTrue(GITLEAKS_CONFIG.exists(), ".gitleaks.toml missing from repo root")

    def test_config_extends_default_rules(self) -> None:
        content = GITLEAKS_CONFIG.read_text(encoding="utf-8")
        self.assertIn("[extend]", content)
        self.assertIn("useDefault = true", content)

    def test_config_has_allowlist_for_research_dumps(self) -> None:
        content = GITLEAKS_CONFIG.read_text(encoding="utf-8")
        self.assertIn("data/peer_reviews/", content)
        self.assertIn("docs/archive/", content)


class TestPreCommitConfigHasGitleaks(unittest.TestCase):
    """The .pre-commit-config.yaml must register the gitleaks hook."""

    def test_pre_commit_config_references_gitleaks(self) -> None:
        config = REPO_ROOT / ".pre-commit-config.yaml"
        self.assertTrue(config.exists())
        content = config.read_text(encoding="utf-8")
        self.assertIn("gitleaks/gitleaks", content)
        self.assertIn("id: gitleaks", content)


if __name__ == "__main__":
    unittest.main()
