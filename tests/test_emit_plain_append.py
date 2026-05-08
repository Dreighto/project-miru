"""Tests for the PR that removes hash-chain wiring from emit helpers.

The PR removes ``append_chained`` calls from:
    emit_completion.py, emit_decision.py, emit_github_resource.py,
    emit_heartbeat.py

and also removes the ``fsync`` keyword parameter from ``audit_chain.append_chained``.

These tests verify:
    * append_chained() no longer accepts a ``fsync`` kwarg (regression guard).
    * Each emit helper writes plain JSON rows (no ``row_hash`` / ``prev_hash``
      fields) after the change.
    * emit_completion.py: the removed ``isinstance(data, dict)`` guard means
      non-dict JSON is now accepted rather than rejected with sys.exit(1).
    * emit_github_resource.py: ``os.fsync`` is still called directly on every
      ``append_entry`` call even though it is no longer routed through
      ``append_chained``.
    * emit_heartbeat.py: ``os.makedirs`` is called to create the log directory.
    * emit_decision.py: parent directory is created when it does not exist.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_chain  # noqa: E402
import emit_completion  # noqa: E402
import emit_decision  # noqa: E402
import emit_github_resource  # noqa: E402
import emit_heartbeat  # noqa: E402

CHAIN_FIELD_PREV = audit_chain.CHAIN_FIELD_PREV
CHAIN_FIELD_HASH = audit_chain.CHAIN_FIELD_HASH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    """Return all non-empty JSON objects in a JSONL file."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _valid_completion_marker(**overrides) -> dict:
    base = {
        "timestamp": "2026-05-08T00:00:00Z",
        "ticket_id": "PRO-TEST",
        "status": "CONFIRMED_WORKING",
        "summary": "plain append test",
    }
    base.update(overrides)
    return base


def _valid_github_entry(**overrides) -> dict:
    base = {
        "ts": "2026-05-08T00:00:00Z",
        "trace_id": "cc-PRO-TEST-abc123",
        "ticket_id": "PRO-TEST",
        "resource_type": "branch",
        "resource_id": "dreighto/pro-test-branch",
        "intent": "create",
        "status": "pending",
    }
    base.update(overrides)
    return base


def _valid_decision_record(**overrides) -> dict:
    base = {
        "decision_id": "test-decision-001",
        "trace_id": "cc-PRO-TEST-x-y",
        "ticket_id": "PRO-TEST",
        "worker": "cc",
        "created_at": "2026-05-08T00:00:00Z",
        "proposed_tag": "test_tag",
        "tool_profile": "cc_full_operator",
        "authority_mode": "execute",
        "trigger": "worker_selection",
        "decision_type": "select_worker",
        "decision": "selected cc",
        "confidence": "medium",
        "confidence_reason": "single available worker",
        "would_change_mind_if": "another worker comes online",
        "expected_outcome": "task completes",
        "classification_history": [],
        "canon_refs": [],
        "evidence_refs": [],
        "context_used": [],
        "alternatives_considered": [],
        "assumptions": [],
        "constraints": [],
        "known_uncertainties": [],
        "outcome_evidence_refs": [],
        "verification_limitations": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. audit_chain.append_chained — fsync param removed
# ---------------------------------------------------------------------------


class TestAuditChainFsyncRemoved(unittest.TestCase):
    """The ``fsync`` keyword argument was removed from ``append_chained`` in
    this PR. Callers that pass ``fsync=True`` must now receive a TypeError so
    they notice the API change rather than silently doing the wrong thing."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_fsync_removed_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "test.jsonl"

    def test_append_chained_rejects_fsync_kwarg(self) -> None:
        """Calling append_chained(..., fsync=True) must raise TypeError after
        the parameter was removed."""
        with self.assertRaises(TypeError):
            audit_chain.append_chained(self.path, {"event": "x"}, fsync=True)

    def test_append_chained_rejects_fsync_false_kwarg(self) -> None:
        """Even the default value of the old API (fsync=False) is no longer a
        valid parameter and must raise TypeError."""
        with self.assertRaises(TypeError):
            audit_chain.append_chained(self.path, {"event": "x"}, fsync=False)

    def test_append_chained_still_works_without_fsync(self) -> None:
        """After removing fsync, the core chaining functionality must remain
        intact; this is a regression guard against over-removal."""
        row_hash = audit_chain.append_chained(self.path, {"event": "ok"})
        self.assertIsInstance(row_hash, str)
        self.assertEqual(len(row_hash), 64)  # SHA-256 hex digest
        rows = _read_jsonl(self.path)
        self.assertEqual(len(rows), 1)
        self.assertIn(CHAIN_FIELD_HASH, rows[0])
        self.assertIsNone(rows[0][CHAIN_FIELD_PREV])

    def test_append_chained_chain_links_intact(self) -> None:
        """Multiple appends still produce correct prev_hash links."""
        h1 = audit_chain.append_chained(self.path, {"seq": 1})
        h2 = audit_chain.append_chained(self.path, {"seq": 2})
        rows = _read_jsonl(self.path)
        self.assertEqual(rows[0][CHAIN_FIELD_HASH], h1)
        self.assertIsNone(rows[0][CHAIN_FIELD_PREV])
        self.assertEqual(rows[1][CHAIN_FIELD_HASH], h2)
        self.assertEqual(rows[1][CHAIN_FIELD_PREV], h1)


# ---------------------------------------------------------------------------
# 2. emit_completion — plain JSON append, isinstance guard removed
# ---------------------------------------------------------------------------


class TestEmitCompletionPlainAppend(unittest.TestCase):
    """emit_completion.py no longer chains rows; it writes plain JSON."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_completion_plain_"))
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        self.log_path = self.tmp_root / "data" / "cc_completion_log.jsonl"
        self._orig_repo_root = emit_completion._repo_root
        emit_completion._repo_root = lambda: str(self.tmp_root)
        self.addCleanup(setattr, emit_completion, "_repo_root", self._orig_repo_root)

    def _run_main(self, payload) -> None:
        json_str = json.dumps(payload) if not isinstance(payload, str) else payload
        with (
            mock.patch.object(sys, "argv", ["emit_completion.py"]),
            mock.patch.object(sys, "stdin", io.StringIO(json_str)),
        ):
            emit_completion.main()

    def test_single_row_has_no_chain_fields(self) -> None:
        """After removing append_chained, written rows must not carry
        prev_hash or row_hash."""
        self._run_main(_valid_completion_marker())
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 1)
        self.assertNotIn(CHAIN_FIELD_PREV, rows[0])
        self.assertNotIn(CHAIN_FIELD_HASH, rows[0])

    def test_multiple_rows_no_chain_fields(self) -> None:
        """Three sequential emits must all produce plain JSON rows."""
        for i in range(3):
            self._run_main(_valid_completion_marker(summary=f"run {i}"))
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertNotIn(CHAIN_FIELD_PREV, row, "row should not carry prev_hash")
            self.assertNotIn(CHAIN_FIELD_HASH, row, "row should not carry row_hash")

    def test_row_data_is_preserved(self) -> None:
        """Payload fields must survive the plain append round-trip unchanged."""
        marker = _valid_completion_marker(ticket_id="PRO-999", summary="check roundtrip")
        self._run_main(marker)
        rows = _read_jsonl(self.log_path)
        self.assertEqual(rows[0]["ticket_id"], "PRO-999")
        self.assertEqual(rows[0]["summary"], "check roundtrip")

    def test_non_dict_json_list_is_written_without_error(self) -> None:
        """The isinstance(data, dict) guard was REMOVED in this PR. A JSON
        list on stdin no longer causes sys.exit(1); it is written as-is.
        This is a regression test that documents the intentional removal."""
        self._run_main("[1, 2, 3]")
        # The file should exist and contain the list as a line.
        self.assertTrue(self.log_path.exists())
        content = self.log_path.read_text(encoding="utf-8").strip()
        self.assertEqual(json.loads(content), [1, 2, 3])

    def test_empty_stdin_exits_nonzero(self) -> None:
        """Empty stdin must still exit with code 1 (unchanged from before)."""
        with (
            mock.patch.object(sys, "argv", ["emit_completion.py"]),
            mock.patch.object(sys, "stdin", io.StringIO("")),
            self.assertRaises(SystemExit) as ctx,
        ):
            emit_completion.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_invalid_json_exits_nonzero(self) -> None:
        """Malformed JSON must still exit with code 1."""
        with (
            mock.patch.object(sys, "argv", ["emit_completion.py"]),
            mock.patch.object(sys, "stdin", io.StringIO("{bad json")),
            self.assertRaises(SystemExit) as ctx,
        ):
            emit_completion.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_log_directory_created_automatically(self) -> None:
        """os.makedirs must create the data/ directory if it doesn't exist."""
        self.assertFalse(self.log_path.parent.exists())
        self._run_main(_valid_completion_marker())
        self.assertTrue(self.log_path.parent.exists())
        self.assertTrue(self.log_path.exists())

    def test_row_ends_with_newline(self) -> None:
        """Each appended line must end with '\\n' for valid JSONL."""
        self._run_main(_valid_completion_marker())
        content = self.log_path.read_text(encoding="utf-8")
        self.assertTrue(content.endswith("\n"))


# ---------------------------------------------------------------------------
# 3. emit_decision — plain JSON append
# ---------------------------------------------------------------------------


class TestEmitDecisionPlainAppend(unittest.TestCase):
    """emit_decision.emit() no longer chains rows; it writes plain JSON."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_decision_plain_"))
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        self.log_path = self.tmp_root / "data" / "agent_decisions.jsonl"

    def _emit(self, record: dict) -> dict:
        # Bypass the heavy schema validator — we're testing storage behavior,
        # not validation rules (which have their own dedicated test file).
        with mock.patch.object(
            emit_decision, "_validate_and_normalize", side_effect=lambda r: r
        ):
            return emit_decision.emit(record, log_path=self.log_path)

    def test_single_row_has_no_chain_fields(self) -> None:
        self._emit(_valid_decision_record())
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 1)
        self.assertNotIn(CHAIN_FIELD_PREV, rows[0])
        self.assertNotIn(CHAIN_FIELD_HASH, rows[0])

    def test_multiple_rows_no_chain_fields(self) -> None:
        for i in range(3):
            self._emit(_valid_decision_record(decision_id=f"d{i}"))
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertNotIn(CHAIN_FIELD_PREV, row)
            self.assertNotIn(CHAIN_FIELD_HASH, row)

    def test_parent_directory_created_if_missing(self) -> None:
        """emit() must create the parent directory via mkdir(parents=True)."""
        deep_log = self.tmp_root / "nested" / "subdir" / "decisions.jsonl"
        self.assertFalse(deep_log.parent.exists())
        self._emit(_valid_decision_record())
        # Use a fresh log_path for the deep test.
        with mock.patch.object(
            emit_decision, "_validate_and_normalize", side_effect=lambda r: r
        ):
            emit_decision.emit(_valid_decision_record(), log_path=deep_log)
        self.assertTrue(deep_log.exists())

    def test_row_data_preserved_in_plain_append(self) -> None:
        """Decision fields survive the plain JSON round-trip."""
        record = _valid_decision_record(decision_id="unique-id-xyz")
        self._emit(record)
        rows = _read_jsonl(self.log_path)
        self.assertEqual(rows[0]["decision_id"], "unique-id-xyz")

    def test_two_emits_produce_two_rows(self) -> None:
        self._emit(_valid_decision_record(decision_id="d1"))
        self._emit(_valid_decision_record(decision_id="d2"))
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["decision_id"], "d1")
        self.assertEqual(rows[1]["decision_id"], "d2")

    def test_row_ends_with_newline(self) -> None:
        self._emit(_valid_decision_record())
        content = self.log_path.read_text(encoding="utf-8")
        self.assertTrue(content.endswith("\n"))


# ---------------------------------------------------------------------------
# 4. emit_github_resource — plain JSON append + direct fsync
# ---------------------------------------------------------------------------


class TestEmitGithubResourcePlainAppend(unittest.TestCase):
    """emit_github_resource.append_entry() no longer chains rows.
    It writes plain JSON and still calls os.fsync directly."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_ghres_plain_"))
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        self.log_path = self.tmp_root / "data" / "github_resource_ledger.jsonl"

    def test_single_entry_has_no_chain_fields(self) -> None:
        emit_github_resource.append_entry(_valid_github_entry(), str(self.log_path))
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 1)
        self.assertNotIn(CHAIN_FIELD_PREV, rows[0])
        self.assertNotIn(CHAIN_FIELD_HASH, rows[0])

    def test_multiple_entries_no_chain_fields(self) -> None:
        for i in range(3):
            emit_github_resource.append_entry(
                _valid_github_entry(resource_id=f"branch-{i}"), str(self.log_path)
            )
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertNotIn(CHAIN_FIELD_PREV, row)
            self.assertNotIn(CHAIN_FIELD_HASH, row)

    def test_fsync_still_called_on_each_append(self) -> None:
        """fsync is now called directly rather than via append_chained(fsync=True).
        The behaviour must remain: each append syncs to disk, because a lost
        ledger row means an orphan GitHub resource."""
        with mock.patch("os.fsync") as mock_fsync:
            emit_github_resource.append_entry(_valid_github_entry(), str(self.log_path))
            self.assertGreaterEqual(
                mock_fsync.call_count,
                1,
                "os.fsync must be called for every github_resource_ledger write",
            )

    def test_fsync_called_once_per_append(self) -> None:
        """Three separate calls to append_entry must each trigger at least one
        os.fsync call."""
        fsync_counts = []
        original_fsync = os.fsync

        def counting_fsync(fd):
            fsync_counts.append(fd)
            return original_fsync(fd)

        with mock.patch("os.fsync", side_effect=counting_fsync):
            for i in range(3):
                emit_github_resource.append_entry(
                    _valid_github_entry(resource_id=f"br-{i}"), str(self.log_path)
                )
        self.assertEqual(len(fsync_counts), 3, "each append must call os.fsync exactly once")

    def test_entry_data_preserved(self) -> None:
        entry = _valid_github_entry(resource_id="dreighto/special-branch")
        emit_github_resource.append_entry(entry, str(self.log_path))
        rows = _read_jsonl(self.log_path)
        self.assertEqual(rows[0]["resource_id"], "dreighto/special-branch")

    def test_parent_directory_created_automatically(self) -> None:
        """os.makedirs must create missing parent directories."""
        self.assertFalse(self.log_path.parent.exists())
        emit_github_resource.append_entry(_valid_github_entry(), str(self.log_path))
        self.assertTrue(self.log_path.parent.exists())
        self.assertTrue(self.log_path.exists())

    def test_row_ends_with_newline(self) -> None:
        emit_github_resource.append_entry(_valid_github_entry(), str(self.log_path))
        content = self.log_path.read_text(encoding="utf-8")
        self.assertTrue(content.endswith("\n"))


# ---------------------------------------------------------------------------
# 5. emit_heartbeat — plain JSON append
# ---------------------------------------------------------------------------


class TestEmitHeartbeatPlainAppend(unittest.TestCase):
    """emit_heartbeat.emit() no longer chains rows; it writes plain JSON."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_heartbeat_plain_"))
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)
        self.log_path = self.tmp_root / "data" / "cc_heartbeat_log.jsonl"
        # Redirect HEARTBEAT_LOG to our temp path.
        self._orig_log = emit_heartbeat.HEARTBEAT_LOG
        emit_heartbeat.HEARTBEAT_LOG = str(self.log_path)
        self.addCleanup(setattr, emit_heartbeat, "HEARTBEAT_LOG", self._orig_log)

    def _emit(self, step: str = "test_step") -> None:
        emit_heartbeat.emit(worker_id="cc-test", ticket_id="PRO-TEST", step=step)

    def test_single_row_has_no_chain_fields(self) -> None:
        self._emit()
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 1)
        self.assertNotIn(CHAIN_FIELD_PREV, rows[0])
        self.assertNotIn(CHAIN_FIELD_HASH, rows[0])

    def test_multiple_rows_no_chain_fields(self) -> None:
        for step in ("start", "middle", "end"):
            self._emit(step=step)
        rows = _read_jsonl(self.log_path)
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertNotIn(CHAIN_FIELD_PREV, row)
            self.assertNotIn(CHAIN_FIELD_HASH, row)

    def test_row_data_preserved(self) -> None:
        """Standard heartbeat fields must survive the plain JSON round-trip."""
        self._emit(step="preflight")
        rows = _read_jsonl(self.log_path)
        self.assertEqual(rows[0]["worker_id"], "cc-test")
        self.assertEqual(rows[0]["ticket_id"], "PRO-TEST")
        self.assertEqual(rows[0]["step"], "preflight")
        self.assertEqual(rows[0]["status"], "IN_PROGRESS")

    def test_makedirs_creates_missing_parent_dir(self) -> None:
        """os.makedirs in emit() must create the data/ directory if absent."""
        self.assertFalse(self.log_path.parent.exists())
        self._emit()
        self.assertTrue(self.log_path.parent.exists())
        self.assertTrue(self.log_path.exists())

    def test_row_ends_with_newline(self) -> None:
        self._emit()
        content = self.log_path.read_text(encoding="utf-8")
        self.assertTrue(content.endswith("\n"))

    def test_branch_field_written_when_provided(self) -> None:
        emit_heartbeat.emit(
            worker_id="cc",
            ticket_id="PRO-1",
            step="s",
            branch="dreighto/pro-1-feature",
        )
        rows = _read_jsonl(self.log_path)
        self.assertEqual(rows[0]["branch"], "dreighto/pro-1-feature")

    def test_trace_id_injected_from_env(self) -> None:
        """If MIRU_TRACE_ID is set, it must appear on the row."""
        with mock.patch.dict(os.environ, {"MIRU_TRACE_ID": "cc-PRO-TEST-abc-def"}):
            self._emit()
        rows = _read_jsonl(self.log_path)
        self.assertEqual(rows[0].get("trace_id"), "cc-PRO-TEST-abc-def")

    def test_outputs_default_to_empty_list(self) -> None:
        self._emit()
        rows = _read_jsonl(self.log_path)
        self.assertEqual(rows[0]["outputs"], [])


# ---------------------------------------------------------------------------
# 6. Boundary / negative cases
# ---------------------------------------------------------------------------


class TestPlainAppendBoundaryCases(unittest.TestCase):
    """Additional boundary and regression cases that cut across helpers."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_boundary_"))
        self.addCleanup(shutil.rmtree, self.tmp_root, ignore_errors=True)

    def test_completion_compact_json_no_whitespace(self) -> None:
        """emit_completion must write compact JSON (separators=(',', ':'))."""
        log_path = self.tmp_root / "data" / "cc_completion_log.jsonl"
        marker = _valid_completion_marker()
        with mock.patch.object(emit_completion, "_repo_root", return_value=str(self.tmp_root)):
            with (
                mock.patch.object(sys, "argv", ["emit_completion.py"]),
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(marker))),
            ):
                emit_completion.main()
        raw_line = log_path.read_text(encoding="utf-8").strip()
        # Compact JSON has no spaces around : or ,
        self.assertNotIn(": ", raw_line)
        self.assertNotIn(", ", raw_line)

    def test_github_resource_compact_json_no_whitespace(self) -> None:
        """append_entry must write compact JSON."""
        log_path = self.tmp_root / "data" / "ledger.jsonl"
        emit_github_resource.append_entry(_valid_github_entry(), str(log_path))
        raw_line = log_path.read_text(encoding="utf-8").strip()
        self.assertNotIn(": ", raw_line)
        self.assertNotIn(", ", raw_line)

    def test_heartbeat_compact_json_no_whitespace(self) -> None:
        """emit_heartbeat must write compact JSON (separators=(',', ':'))."""
        log_path = self.tmp_root / "data" / "hb.jsonl"
        orig = emit_heartbeat.HEARTBEAT_LOG
        try:
            emit_heartbeat.HEARTBEAT_LOG = str(log_path)
            emit_heartbeat.emit(worker_id="cc", ticket_id="PRO-1", step="x")
        finally:
            emit_heartbeat.HEARTBEAT_LOG = orig
        raw_line = log_path.read_text(encoding="utf-8").strip()
        self.assertNotIn(": ", raw_line)
        self.assertNotIn(", ", raw_line)

    def test_completion_appends_not_overwrites(self) -> None:
        """Calling main() twice must produce two rows, not one overwritten row."""
        log_path = self.tmp_root / "data" / "cc_completion_log.jsonl"
        for i in range(2):
            with mock.patch.object(emit_completion, "_repo_root", return_value=str(self.tmp_root)):
                with (
                    mock.patch.object(sys, "argv", ["emit_completion.py"]),
                    mock.patch.object(
                        sys, "stdin", io.StringIO(json.dumps(_valid_completion_marker(summary=f"r{i}")))
                    ),
                ):
                    emit_completion.main()
        rows = _read_jsonl(log_path)
        self.assertEqual(len(rows), 2)

    def test_decision_emit_appends_not_overwrites(self) -> None:
        """Repeated emit() calls must append, not overwrite."""
        log_path = self.tmp_root / "data" / "decisions.jsonl"
        with mock.patch.object(
            emit_decision, "_validate_and_normalize", side_effect=lambda r: r
        ):
            emit_decision.emit(_valid_decision_record(decision_id="d1"), log_path=log_path)
            emit_decision.emit(_valid_decision_record(decision_id="d2"), log_path=log_path)
        rows = _read_jsonl(log_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["decision_id"], "d2")

    def test_heartbeat_appends_not_overwrites(self) -> None:
        log_path = self.tmp_root / "data" / "hb.jsonl"
        orig = emit_heartbeat.HEARTBEAT_LOG
        try:
            emit_heartbeat.HEARTBEAT_LOG = str(log_path)
            emit_heartbeat.emit(worker_id="cc", ticket_id="T1", step="a")
            emit_heartbeat.emit(worker_id="cc", ticket_id="T1", step="b")
        finally:
            emit_heartbeat.HEARTBEAT_LOG = orig
        rows = _read_jsonl(log_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["step"], "a")
        self.assertEqual(rows[1]["step"], "b")

    def test_github_resource_appends_not_overwrites(self) -> None:
        log_path = self.tmp_root / "data" / "ledger.jsonl"
        emit_github_resource.append_entry(
            _valid_github_entry(resource_id="br-1"), str(log_path)
        )
        emit_github_resource.append_entry(
            _valid_github_entry(resource_id="br-2"), str(log_path)
        )
        rows = _read_jsonl(log_path)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["resource_id"], "br-1")
        self.assertEqual(rows[1]["resource_id"], "br-2")

    def test_audit_chain_still_validates_after_fsync_removal(self) -> None:
        """Removing fsync from append_chained must not break validate_chain.
        This is a defence-in-depth regression guard for the audit_chain
        library itself."""
        path = self.tmp_root / "chain.jsonl"
        audit_chain.append_chained(path, {"seq": 1})
        audit_chain.append_chained(path, {"seq": 2})
        audit_chain.append_chained(path, {"seq": 3})
        result = audit_chain.validate_chain(path)
        self.assertTrue(result.ok, f"chain validation failed: {result.error}")
        self.assertEqual(result.chained_rows, 3)


if __name__ == "__main__":
    unittest.main()
