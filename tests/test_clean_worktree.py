"""Tests for tools/clean_worktree.py (PRO-316)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "clean_worktree.py"


def _import_module():
    spec = importlib.util.spec_from_file_location("clean_worktree_under_test", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["clean_worktree_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _init_git_repo(path: str) -> None:
    """Initialize a git repo with a .gitignore that covers our known-safe dirs."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    gitignore = os.path.join(path, ".gitignore")
    with open(gitignore, "w") as f:
        f.write("test-results/\n")
        f.write("playwright-report/\n")
        f.write(".pytest_cache/\n")
        f.write("__pycache__/\n")
        f.write("node_modules/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"],
        cwd=path,
        capture_output=True,
        check=True,
    )


class ModuleImportTest(unittest.TestCase):
    def test_module_loads(self) -> None:
        mod = _import_module()
        self.assertTrue(hasattr(mod, "clean"))
        self.assertTrue(hasattr(mod, "KNOWN_SAFE_DIRS"))


class CleanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        _init_git_repo(self.tmpdir)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_removes_gitignored_test_results(self) -> None:
        target = os.path.join(self.tmpdir, "test-results")
        os.makedirs(target)
        Path(os.path.join(target, "output.xml")).write_text("data")
        result = self.mod.clean(self.tmpdir)
        self.assertIn("test-results", result["cleaned"])
        self.assertFalse(os.path.exists(target))

    def test_removes_playwright_report(self) -> None:
        target = os.path.join(self.tmpdir, "playwright-report")
        os.makedirs(target)
        Path(os.path.join(target, "index.html")).write_text("<html>")
        result = self.mod.clean(self.tmpdir)
        self.assertIn("playwright-report", result["cleaned"])
        self.assertFalse(os.path.exists(target))

    def test_removes_pytest_cache(self) -> None:
        target = os.path.join(self.tmpdir, ".pytest_cache")
        os.makedirs(os.path.join(target, "v"))
        Path(os.path.join(target, "v", "cache")).write_text("")
        result = self.mod.clean(self.tmpdir)
        self.assertIn(".pytest_cache", result["cleaned"])

    def test_removes_nested_pycache(self) -> None:
        nested = os.path.join(self.tmpdir, "tools", "__pycache__")
        os.makedirs(nested)
        Path(os.path.join(nested, "module.cpython-314.pyc")).write_text("")
        result = self.mod.clean(self.tmpdir)
        cleaned_paths = result["cleaned"]
        self.assertTrue(
            any("__pycache__" in p for p in cleaned_paths),
            f"Expected __pycache__ in cleaned, got {cleaned_paths}",
        )
        self.assertFalse(os.path.exists(nested))

    def test_skips_non_gitignored_directory(self) -> None:
        target = os.path.join(self.tmpdir, "test-results")
        os.makedirs(target)
        gitignore = os.path.join(self.tmpdir, ".gitignore")
        with open(gitignore, "w") as f:
            f.write("")
        subprocess.run(
            ["git", "add", ".gitignore"], cwd=self.tmpdir, capture_output=True, check=True
        )
        subprocess.run(
            ["git", "commit", "-m", "remove ignores", "--no-verify"],
            cwd=self.tmpdir,
            capture_output=True,
            check=True,
        )
        result = self.mod.clean(self.tmpdir)
        self.assertIn("test-results", result["skipped"])
        self.assertTrue(os.path.exists(target))

    def test_nothing_to_clean(self) -> None:
        result = self.mod.clean(self.tmpdir)
        self.assertEqual(result["cleaned"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["errors"], [])

    def test_multiple_artifacts_cleaned(self) -> None:
        for dirname in ["test-results", "playwright-report", ".pytest_cache"]:
            os.makedirs(os.path.join(self.tmpdir, dirname))
            Path(os.path.join(self.tmpdir, dirname, "f.txt")).write_text("x")
        result = self.mod.clean(self.tmpdir)
        self.assertEqual(len(result["cleaned"]), 3)

    def test_does_not_touch_tracked_files(self) -> None:
        tracked = os.path.join(self.tmpdir, "important.py")
        Path(tracked).write_text("keep me")
        subprocess.run(["git", "add", "important.py"], cwd=self.tmpdir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add file", "--no-verify"],
            cwd=self.tmpdir,
            capture_output=True,
        )
        Path(tracked).write_text("modified")
        self.mod.clean(self.tmpdir)
        self.assertTrue(os.path.exists(tracked))
        self.assertEqual(Path(tracked).read_text(), "modified")

    def test_node_modules_cache_cleaned(self) -> None:
        target = os.path.join(self.tmpdir, "node_modules", ".cache")
        os.makedirs(target)
        Path(os.path.join(target, "babel.json")).write_text("{}")
        result = self.mod.clean(self.tmpdir)
        cleaned_str = " ".join(result["cleaned"])
        self.assertIn("node_modules", cleaned_str)
        self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
