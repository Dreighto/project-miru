"""Tests for tools/emit_github_resource.py and tools/reap_github_resources.py."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EMIT_PATH = REPO_ROOT / "tools" / "emit_github_resource.py"
REAP_PATH = REPO_ROOT / "tools" / "reap_github_resources.py"


def _import_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_emit():
    return _import_module(EMIT_PATH, "emit_github_resource_under_test")


def _load_reap():
    return _import_module(REAP_PATH, "reap_github_resources_under_test")


def _valid_entry(**overrides) -> dict:
    base = {
        "ts": "2026-05-07T04:00:00Z",
        "trace_id": "cc-PRO-320-abc123",
        "ticket_id": "PRO-320",
        "resource_type": "branch",
        "resource_id": "dreighto/pro-320-feature",
        "intent": "create",
        "status": "pending",
        "compensation": "delete_branch",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# emit_github_resource tests
# ---------------------------------------------------------------------------


class EmitModuleImportTest(unittest.TestCase):
    def test_module_loads(self) -> None:
        mod = _load_emit()
        self.assertTrue(hasattr(mod, "validate"))
        self.assertTrue(hasattr(mod, "append_entry"))
        self.assertTrue(hasattr(mod, "main"))


class EmitValidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_emit()

    def test_valid_entry_passes(self) -> None:
        self.mod.validate(_valid_entry())  # should not raise

    def test_missing_required_field_raises(self) -> None:
        entry = _valid_entry()
        del entry["trace_id"]
        with self.assertRaises(ValueError) as ctx:
            self.mod.validate(entry)
        self.assertIn("trace_id", str(ctx.exception))

    def test_missing_multiple_fields_raises(self) -> None:
        entry = _valid_entry()
        del entry["ts"]
        del entry["resource_id"]
        with self.assertRaises(ValueError) as ctx:
            self.mod.validate(entry)
        msg = str(ctx.exception)
        self.assertIn("resource_id", msg)
        self.assertIn("ts", msg)

    def test_invalid_resource_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.validate(_valid_entry(resource_type="commit"))

    def test_invalid_intent_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.validate(_valid_entry(intent="open"))

    def test_invalid_status_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.validate(_valid_entry(status="done"))

    def test_invalid_compensation_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.mod.validate(_valid_entry(compensation="revert"))

    def test_null_compensation_valid(self) -> None:
        self.mod.validate(_valid_entry(compensation=None))

    def test_pr_resource_type_valid(self) -> None:
        self.mod.validate(_valid_entry(resource_type="pr", compensation="close_pr"))

    def test_all_valid_statuses(self) -> None:
        for status in ("pending", "committed", "compensated", "failed"):
            self.mod.validate(_valid_entry(status=status))

    def test_all_valid_intents(self) -> None:
        for intent in ("create", "close", "delete"):
            self.mod.validate(_valid_entry(intent=intent))


class EmitAppendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_emit()

    def setUp(self) -> None:
        fd, self.ledger_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)

    def tearDown(self) -> None:
        if os.path.exists(self.ledger_path):
            os.unlink(self.ledger_path)

    def _read_lines(self) -> list[dict]:
        with open(self.ledger_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_append_single_entry(self) -> None:
        entry = _valid_entry()
        self.mod.append_entry(entry, self.ledger_path)
        lines = self._read_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["ticket_id"], "PRO-320")

    def test_append_multiple_entries(self) -> None:
        for i in range(3):
            entry = _valid_entry(resource_id=f"dreighto/pro-{i}-feature")
            self.mod.append_entry(entry, self.ledger_path)
        lines = self._read_lines()
        self.assertEqual(len(lines), 3)

    def test_append_preserves_order(self) -> None:
        ids = ["dreighto/pro-1", "dreighto/pro-2", "dreighto/pro-3"]
        for rid in ids:
            self.mod.append_entry(_valid_entry(resource_id=rid), self.ledger_path)
        lines = self._read_lines()
        self.assertEqual([ln["resource_id"] for ln in lines], ids)

    def test_each_line_is_valid_json(self) -> None:
        self.mod.append_entry(_valid_entry(), self.ledger_path)
        with open(self.ledger_path, encoding="utf-8") as fh:
            content = fh.read()
        self.assertTrue(content.endswith("\n"), "file must end with newline")
        for line in content.strip().splitlines():
            json.loads(line)  # must not raise

    def test_reject_invalid_entry_does_not_write(self) -> None:
        entry = _valid_entry()
        del entry["trace_id"]
        with self.assertRaises(ValueError):
            self.mod.append_entry(entry, self.ledger_path)
        lines = self._read_lines()
        self.assertEqual(len(lines), 0)


# ---------------------------------------------------------------------------
# reap_github_resources tests
# ---------------------------------------------------------------------------


class ReapModuleImportTest(unittest.TestCase):
    def test_module_loads(self) -> None:
        mod = _load_reap()
        self.assertTrue(hasattr(mod, "reap"))
        self.assertTrue(hasattr(mod, "find_stale_pending"))
        self.assertTrue(hasattr(mod, "load_ledger"))


class ReapLoadLedgerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_reap()

    def setUp(self) -> None:
        fd, self.ledger_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)

    def tearDown(self) -> None:
        if os.path.exists(self.ledger_path):
            os.unlink(self.ledger_path)

    def _write_lines(self, entries: list[dict]) -> None:
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def test_load_empty_file(self) -> None:
        result = self.mod.load_ledger(self.ledger_path)
        self.assertEqual(result, [])

    def test_load_missing_file(self) -> None:
        result = self.mod.load_ledger("/nonexistent/path/ledger.jsonl")
        self.assertEqual(result, [])

    def test_load_valid_entries(self) -> None:
        entries = [_valid_entry(resource_id=f"r-{i}") for i in range(3)]
        self._write_lines(entries)
        loaded = self.mod.load_ledger(self.ledger_path)
        self.assertEqual(len(loaded), 3)

    def test_skip_malformed_lines(self) -> None:
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_valid_entry()) + "\n")
            fh.write("not-valid-json\n")
            fh.write(json.dumps(_valid_entry(resource_id="r-2")) + "\n")
        loaded = self.mod.load_ledger(self.ledger_path)
        self.assertEqual(len(loaded), 2)


class ReapFindStaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_reap()

    def _now(self) -> datetime:
        return datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)

    def _ts(self, hours_ago: float) -> str:
        dt = self._now() - timedelta(hours=hours_ago)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_identifies_stale_pending(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(3), status="pending", trace_id="stale-op"),  # stale
            _valid_entry(ts=self._ts(1), status="pending", trace_id="fresh-op"),  # fresh
            _valid_entry(ts=self._ts(3), status="compensated", trace_id="done-op"),  # not pending
        ]
        stale = self.mod.find_stale_pending(entries, ttl_seconds=7200, now=self._now())
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["trace_id"], "stale-op")

    def test_compensated_entry_suppresses_stale_pending(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(3), status="pending"),
            _valid_entry(ts=self._ts(0.5), status="compensated"),
        ]
        stale = self.mod.find_stale_pending(entries, ttl_seconds=7200, now=self._now())
        self.assertEqual(len(stale), 0)

    def test_failed_entry_allows_retry(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(3), status="pending"),
            _valid_entry(ts=self._ts(2.5), status="failed"),
        ]
        stale = self.mod.find_stale_pending(entries, ttl_seconds=7200, now=self._now())
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["status"], "failed")

    def test_exactly_at_ttl_not_stale(self) -> None:
        entries = [_valid_entry(ts=self._ts(2.0), status="pending")]
        stale = self.mod.find_stale_pending(entries, ttl_seconds=7200, now=self._now())
        self.assertEqual(len(stale), 0)

    def test_just_over_ttl_is_stale(self) -> None:
        entries = [_valid_entry(ts=self._ts(2.001), status="pending")]
        stale = self.mod.find_stale_pending(entries, ttl_seconds=7200, now=self._now())
        self.assertEqual(len(stale), 1)

    def test_empty_ledger(self) -> None:
        stale = self.mod.find_stale_pending([], ttl_seconds=7200, now=self._now())
        self.assertEqual(stale, [])

    def test_terminal_statuses_not_retried(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(5), status="committed", trace_id="t1"),
            _valid_entry(ts=self._ts(5), status="compensated", trace_id="t2"),
        ]
        stale = self.mod.find_stale_pending(entries, ttl_seconds=7200, now=self._now())
        self.assertEqual(stale, [])

    def test_custom_ttl_respected(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(0.6), status="pending"),  # 36 min old
        ]
        # TTL = 30 min → stale
        stale_30 = self.mod.find_stale_pending(entries, ttl_seconds=1800, now=self._now())
        self.assertEqual(len(stale_30), 1)
        # TTL = 2 hours → fresh
        stale_2h = self.mod.find_stale_pending(entries, ttl_seconds=7200, now=self._now())
        self.assertEqual(len(stale_2h), 0)


class ReapDryRunTest(unittest.TestCase):
    """Dry-run mode must not write to ledger or call git/gh."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_reap()

    def setUp(self) -> None:
        fd, self.ledger_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)

    def tearDown(self) -> None:
        if os.path.exists(self.ledger_path):
            os.unlink(self.ledger_path)

    def _now(self) -> datetime:
        return datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC)

    def _ts(self, hours_ago: float) -> str:
        dt = self._now() - timedelta(hours=hours_ago)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _seed_ledger(self, entries: list[dict]) -> None:
        with open(self.ledger_path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def _line_count(self) -> int:
        with open(self.ledger_path, encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def test_dry_run_no_new_rows_written(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(3), status="pending"),
            _valid_entry(
                ts=self._ts(3), status="pending", resource_type="pr", compensation="close_pr"
            ),
        ]
        self._seed_ledger(entries)
        initial_count = self._line_count()

        stale = self.mod.reap(
            self.ledger_path,
            ttl_hours=2.0,
            dry_run=True,
            now=self._now(),
        )

        self.assertEqual(len(stale), 2)
        self.assertEqual(self._line_count(), initial_count, "dry-run must not append rows")

    def test_dry_run_returns_stale_entries(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(5), status="pending", resource_id="r-old"),
            _valid_entry(ts=self._ts(1), status="pending", resource_id="r-fresh"),
        ]
        self._seed_ledger(entries)

        stale = self.mod.reap(
            self.ledger_path,
            ttl_hours=2.0,
            dry_run=True,
            now=self._now(),
        )

        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["resource_id"], "r-old")

    def test_dry_run_on_empty_ledger(self) -> None:
        stale = self.mod.reap(
            self.ledger_path,
            ttl_hours=2.0,
            dry_run=True,
            now=self._now(),
        )
        self.assertEqual(stale, [])

    def test_ttl_hours_parameter_respected(self) -> None:
        entries = [
            _valid_entry(ts=self._ts(0.5), status="pending"),  # 30 min old
        ]
        self._seed_ledger(entries)

        # 1-hour TTL: not stale
        stale_1h = self.mod.reap(self.ledger_path, ttl_hours=1.0, dry_run=True, now=self._now())
        self.assertEqual(len(stale_1h), 0)

        # 0.25-hour (15 min) TTL: stale
        stale_15m = self.mod.reap(self.ledger_path, ttl_hours=0.25, dry_run=True, now=self._now())
        self.assertEqual(len(stale_15m), 1)


if __name__ == "__main__":
    unittest.main()
