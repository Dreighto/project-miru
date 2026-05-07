"""Tests for tools/salvage_worktree.py (PRO-317)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "salvage_worktree.py"


def _import_module():
    spec = importlib.util.spec_from_file_location("salvage_worktree_under_test", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["salvage_worktree_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _init_git_repo(path: str) -> None:
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, capture_output=True)
    gitignore = os.path.join(path, ".gitignore")
    with open(gitignore, "w") as f:
        f.write("*.pyc\n__pycache__/\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"],
        cwd=path,
        capture_output=True,
        check=True,
    )


class ModuleImportTest(unittest.TestCase):
    def test_module_loads(self) -> None:
        mod = _import_module()
        self.assertTrue(hasattr(mod, "scan"))
        self.assertTrue(hasattr(mod, "RECOMMENDATIONS"))


class ScanTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        _init_git_repo(self.tmpdir)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_clean_worktree_no_work_product(self) -> None:
        report = self.mod.scan(self.tmpdir, "PRO-999")
        self.assertEqual(report["salvage_recommendation"], "NO_WORK_PRODUCT")
        self.assertFalse(report["has_commits"])
        self.assertFalse(report["has_uncommitted_changes"])

    def test_nonexistent_path(self) -> None:
        report = self.mod.scan("/nonexistent/path", "PRO-999")
        self.assertEqual(report["salvage_recommendation"], "NO_WORK_PRODUCT")
        self.assertIn("error", report)

    def test_new_source_file_without_tests_is_partial(self) -> None:
        Path(os.path.join(self.tmpdir, "tools")).mkdir(exist_ok=True)
        Path(os.path.join(self.tmpdir, "tools", "new_module.py")).write_text("print('hello')")
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertEqual(report["salvage_recommendation"], "CODE_PARTIAL")
        self.assertTrue(report["has_uncommitted_changes"])

    def test_source_and_passing_tests_is_complete(self) -> None:
        Path(os.path.join(self.tmpdir, "tools")).mkdir(exist_ok=True)
        Path(os.path.join(self.tmpdir, "tools", "module.py")).write_text(
            "def add(a, b): return a + b\n"
        )
        Path(os.path.join(self.tmpdir, "tests")).mkdir(exist_ok=True)
        Path(os.path.join(self.tmpdir, "tests", "test_module.py")).write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(1+1, 2)\n\n"
            "if __name__ == '__main__': unittest.main()\n"
        )
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertEqual(report["salvage_recommendation"], "CODE_COMPLETE_TESTS_PASS")
        self.assertTrue(report["test_result"]["passed"])

    def test_source_and_failing_tests_is_tests_fail(self) -> None:
        Path(os.path.join(self.tmpdir, "tools")).mkdir(exist_ok=True)
        Path(os.path.join(self.tmpdir, "tools", "module.py")).write_text("x = 1\n")
        Path(os.path.join(self.tmpdir, "tests")).mkdir(exist_ok=True)
        Path(os.path.join(self.tmpdir, "tests", "test_module.py")).write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_fail(self):\n        self.fail('intentional')\n\n"
            "if __name__ == '__main__': unittest.main()\n"
        )
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertEqual(report["salvage_recommendation"], "CODE_COMPLETE_TESTS_FAIL")
        self.assertFalse(report["test_result"]["passed"])

    def test_committed_work_is_already_committed(self) -> None:
        Path(os.path.join(self.tmpdir, "tools")).mkdir(exist_ok=True)
        Path(os.path.join(self.tmpdir, "tools", "feature.py")).write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=self.tmpdir, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "feat: PRO-100 new feature", "--no-verify"],
            cwd=self.tmpdir,
            capture_output=True,
        )
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertEqual(report["salvage_recommendation"], "ALREADY_COMMITTED")
        self.assertTrue(report["has_commits"])

    def test_only_non_source_files_is_no_work_product(self) -> None:
        Path(os.path.join(self.tmpdir, "notes.txt")).write_text("some notes")
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertEqual(report["salvage_recommendation"], "NO_WORK_PRODUCT")

    def test_report_has_required_fields(self) -> None:
        report = self.mod.scan(self.tmpdir, "PRO-100")
        for field in (
            "ticket_id",
            "worktree",
            "branch",
            "has_commits",
            "has_uncommitted_changes",
            "new_files",
            "modified_files",
            "tests_found",
            "test_result",
            "completion_marker_found",
            "salvage_recommendation",
            "recommendation_description",
        ):
            self.assertIn(field, report, f"Missing field: {field}")

    def test_report_ticket_id_matches(self) -> None:
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertEqual(report["ticket_id"], "PRO-100")

    def test_new_files_listed(self) -> None:
        Path(os.path.join(self.tmpdir, "tools")).mkdir(exist_ok=True)
        Path(os.path.join(self.tmpdir, "tools", "new.py")).write_text("x = 1")
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertTrue(any("new.py" in f for f in report["new_files"]))

    def test_modified_files_listed(self) -> None:
        tracked = os.path.join(self.tmpdir, ".gitignore")
        with open(tracked, "a") as f:
            f.write("\n*.log\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.tmpdir, capture_output=True)
        report = self.mod.scan(self.tmpdir, "PRO-100")
        self.assertFalse(report["has_commits"])


class ClassifyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def test_no_changes_no_commits(self) -> None:
        result = self.mod._classify(
            has_commits=False,
            has_changes=False,
            new_files=[],
            modified_files=[],
            test_result={"ran": False},
            has_marker=False,
        )
        self.assertEqual(result, "NO_WORK_PRODUCT")

    def test_marker_found(self) -> None:
        result = self.mod._classify(
            has_commits=True,
            has_changes=False,
            new_files=[],
            modified_files=[],
            test_result={"ran": False},
            has_marker=True,
        )
        self.assertEqual(result, "ALREADY_COMMITTED")

    def test_commits_no_changes(self) -> None:
        result = self.mod._classify(
            has_commits=True,
            has_changes=False,
            new_files=[],
            modified_files=[],
            test_result={"ran": False},
            has_marker=False,
        )
        self.assertEqual(result, "ALREADY_COMMITTED")

    def test_source_with_passing_tests(self) -> None:
        result = self.mod._classify(
            has_commits=False,
            has_changes=True,
            new_files=["tools/foo.py", "tests/test_foo.py"],
            modified_files=[],
            test_result={"ran": True, "passed": True},
            has_marker=False,
        )
        self.assertEqual(result, "CODE_COMPLETE_TESTS_PASS")

    def test_source_with_failing_tests(self) -> None:
        result = self.mod._classify(
            has_commits=False,
            has_changes=True,
            new_files=["tools/foo.py", "tests/test_foo.py"],
            modified_files=[],
            test_result={"ran": True, "passed": False},
            has_marker=False,
        )
        self.assertEqual(result, "CODE_COMPLETE_TESTS_FAIL")

    def test_source_without_tests(self) -> None:
        result = self.mod._classify(
            has_commits=False,
            has_changes=True,
            new_files=["tools/foo.py"],
            modified_files=[],
            test_result={"ran": False},
            has_marker=False,
        )
        self.assertEqual(result, "CODE_PARTIAL")


if __name__ == "__main__":
    unittest.main()
