"""Tests for tools/task_deps.py (PRO-311).

Uses a temp SQLite database per test method to avoid shared state.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "task_deps.py"
MIGRATION_PATH = REPO_ROOT / "tools" / "migrations" / "m006_task_dependencies.sql"


def _import_module():
    spec = importlib.util.spec_from_file_location("task_deps_under_test", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["task_deps_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _create_test_db() -> Path:
    """Create a temp db with the task_dependencies table."""
    fd, name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_path = Path(name)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MIGRATION_PATH.read_text(encoding="utf-8"))
    conn.close()
    return db_path


class ModuleImportTest(unittest.TestCase):
    def test_module_loads_from_disk(self) -> None:
        mod = _import_module()
        self.assertTrue(hasattr(mod, "register"))
        self.assertTrue(hasattr(mod, "mark_ready"))
        self.assertTrue(hasattr(mod, "all_ready"))
        self.assertTrue(hasattr(mod, "check"))
        self.assertTrue(hasattr(mod, "get_blockers"))
        self.assertTrue(hasattr(mod, "list_dependents"))
        self.assertTrue(hasattr(mod, "get_artifact"))


class RegisterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_register_creates_row(self) -> None:
        dep_id = self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.assertIsInstance(dep_id, str)
        self.assertTrue(len(dep_id) > 0)

    def test_register_idempotent(self) -> None:
        id1 = self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        id2 = self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.assertEqual(id1, id2)

    def test_register_different_deps_different_ids(self) -> None:
        id1 = self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        id2 = self.mod.register("PRO-20", depends_on="PRO-18", db_path=self.db_path)
        self.assertNotEqual(id1, id2)

    def test_register_self_dependency_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.register("PRO-20", depends_on="PRO-20", db_path=self.db_path)

    def test_register_empty_ticket_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.register("", depends_on="PRO-19", db_path=self.db_path)

    def test_register_empty_depends_on_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.register("PRO-20", depends_on="", db_path=self.db_path)

    def test_register_with_trace_id(self) -> None:
        self.mod.register(
            "PRO-20", depends_on="PRO-19", trace_id="cc-test-123", db_path=self.db_path
        )
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["trace_id"], "cc-test-123")

    def test_register_with_notes(self) -> None:
        self.mod.register(
            "PRO-20", depends_on="PRO-19", notes="needs API schema", db_path=self.db_path
        )
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["notes"], "needs API schema")


class MarkReadyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_mark_ready_transitions_pending(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        count = self.mod.mark_ready("PRO-19", db_path=self.db_path)
        self.assertEqual(count, 1)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["status"], "ready")

    def test_mark_ready_multiple_dependents(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.register("PRO-21", depends_on="PRO-19", db_path=self.db_path)
        count = self.mod.mark_ready("PRO-19", db_path=self.db_path)
        self.assertEqual(count, 2)

    def test_mark_ready_with_artifact(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        artifact = {"schema_version": "v2", "entry_point": "api.py:42"}
        self.mod.mark_ready("PRO-19", artifact=artifact, db_path=self.db_path)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["artifact"], artifact)

    def test_mark_ready_no_pending_returns_zero(self) -> None:
        count = self.mod.mark_ready("PRO-19", db_path=self.db_path)
        self.assertEqual(count, 0)

    def test_mark_ready_idempotent(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        count = self.mod.mark_ready("PRO-19", db_path=self.db_path)
        self.assertEqual(count, 0)

    def test_mark_ready_sets_resolved_at(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertIsNotNone(deps[0]["resolved_at"])

    def test_mark_ready_empty_ticket_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.mark_ready("", db_path=self.db_path)


class MarkFailedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_mark_failed_transitions_pending(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        count = self.mod.mark_failed("PRO-19", db_path=self.db_path)
        self.assertEqual(count, 1)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["status"], "failed")

    def test_mark_failed_with_notes(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_failed("PRO-19", notes="worker timed out", db_path=self.db_path)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["notes"], "worker timed out")


class CancelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_cancel_transitions(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        result = self.mod.cancel("PRO-20", "PRO-19", db_path=self.db_path)
        self.assertTrue(result)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["status"], "cancelled")

    def test_cancel_nonexistent_returns_false(self) -> None:
        result = self.mod.cancel("PRO-20", "PRO-19", db_path=self.db_path)
        self.assertFalse(result)

    def test_cancel_already_ready_returns_false(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        result = self.mod.cancel("PRO-20", "PRO-19", db_path=self.db_path)
        self.assertFalse(result)


class AllReadyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_no_deps_returns_true(self) -> None:
        self.assertTrue(self.mod.all_ready("PRO-20", db_path=self.db_path))

    def test_all_ready_when_all_resolved(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.register("PRO-20", depends_on="PRO-18", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-18", db_path=self.db_path)
        self.assertTrue(self.mod.all_ready("PRO-20", db_path=self.db_path))

    def test_not_ready_when_one_pending(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.register("PRO-20", depends_on="PRO-18", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        self.assertFalse(self.mod.all_ready("PRO-20", db_path=self.db_path))

    def test_not_ready_when_one_failed(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_failed("PRO-19", db_path=self.db_path)
        self.assertFalse(self.mod.all_ready("PRO-20", db_path=self.db_path))


class GetBlockersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_returns_pending_deps(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.register("PRO-20", depends_on="PRO-18", db_path=self.db_path)
        self.mod.mark_ready("PRO-18", db_path=self.db_path)
        blockers = self.mod.get_blockers("PRO-20", db_path=self.db_path)
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["depends_on"], "PRO-19")

    def test_returns_failed_deps(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_failed("PRO-19", db_path=self.db_path)
        blockers = self.mod.get_blockers("PRO-20", db_path=self.db_path)
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["status"], "failed")

    def test_empty_when_all_ready(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        blockers = self.mod.get_blockers("PRO-20", db_path=self.db_path)
        self.assertEqual(len(blockers), 0)

    def test_empty_when_no_deps(self) -> None:
        blockers = self.mod.get_blockers("PRO-20", db_path=self.db_path)
        self.assertEqual(len(blockers), 0)


class ListDependentsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_returns_pending_dependents(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.register("PRO-21", depends_on="PRO-19", db_path=self.db_path)
        dependents = self.mod.list_dependents("PRO-19", db_path=self.db_path)
        self.assertEqual(len(dependents), 2)
        tickets = {d["ticket_id"] for d in dependents}
        self.assertEqual(tickets, {"PRO-20", "PRO-21"})

    def test_excludes_resolved_dependents(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.register("PRO-21", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        dependents = self.mod.list_dependents("PRO-19", db_path=self.db_path)
        self.assertEqual(len(dependents), 0)

    def test_empty_when_no_dependents(self) -> None:
        dependents = self.mod.list_dependents("PRO-19", db_path=self.db_path)
        self.assertEqual(len(dependents), 0)


class GetArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_returns_artifact_after_ready(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        artifact = {"schema": "v2", "endpoints": ["/api/cards", "/api/decks"]}
        self.mod.mark_ready("PRO-19", artifact=artifact, db_path=self.db_path)
        result = self.mod.get_artifact("PRO-20", "PRO-19", db_path=self.db_path)
        self.assertEqual(result, artifact)

    def test_returns_none_when_no_artifact(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", db_path=self.db_path)
        result = self.mod.get_artifact("PRO-20", "PRO-19", db_path=self.db_path)
        self.assertIsNone(result)

    def test_returns_none_when_dep_not_found(self) -> None:
        result = self.mod.get_artifact("PRO-20", "PRO-19", db_path=self.db_path)
        self.assertIsNone(result)


class CheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def setUp(self) -> None:
        self.db_path = _create_test_db()

    def tearDown(self) -> None:
        self.db_path.unlink(missing_ok=True)

    def test_returns_all_deps_for_ticket(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.register("PRO-20", depends_on="PRO-18", db_path=self.db_path)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(len(deps), 2)

    def test_empty_for_unknown_ticket(self) -> None:
        deps = self.mod.check("PRO-999", db_path=self.db_path)
        self.assertEqual(len(deps), 0)

    def test_includes_decoded_artifact(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        self.mod.mark_ready("PRO-19", artifact={"key": "val"}, db_path=self.db_path)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertEqual(deps[0]["artifact"], {"key": "val"})

    def test_artifact_none_when_not_set(self) -> None:
        self.mod.register("PRO-20", depends_on="PRO-19", db_path=self.db_path)
        deps = self.mod.check("PRO-20", db_path=self.db_path)
        self.assertIsNone(deps[0]["artifact"])


if __name__ == "__main__":
    unittest.main()
