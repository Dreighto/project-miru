"""Tests for gatekeeper.core — deterministic floor, rejection responses, gate_dispatch flow.

Covers: _check_trace_id_format(), _check_a2a_bus_state() (with in-memory SQLite),
_check_git_status() (mocked subprocess), _check_in_flight_dispatch() (temp file),
run_deterministic_floor() integration, _rejection_response() structure,
gate_dispatch() payload validation and deterministic rejections.

LLM call (call_ollama) is mocked — deterministic floor is the primary test target.

PRO-305 — Gatekeeper test coverage.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from gatekeeper.core import (
    DEFAULT_MODEL,
    GOVERNANCE_PREAMBLE,
    IN_FLIGHT_WINDOW_SECONDS,
    ROUTING_JSON_SCHEMA,
    GatekeeperError,
    _build_prompt,
    _check_a2a_bus_state,
    _check_git_status,
    _check_in_flight_dispatch,
    _check_trace_id_format,
    _rejection_response,
    gate_dispatch,
    run_deterministic_floor,
)

# ---------------------------------------------------------------------------
# _check_trace_id_format()
# ---------------------------------------------------------------------------


class TestCheckTraceIdFormat(unittest.TestCase):
    def test_valid_standard(self):
        self.assertTrue(_check_trace_id_format("rtr-PRO-305-abcdef0123456789"))

    def test_valid_minimum_length(self):
        self.assertTrue(_check_trace_id_format("abcdef"))

    def test_valid_maximum_length(self):
        self.assertTrue(_check_trace_id_format("a" * 128))

    def test_valid_with_underscore_and_dash(self):
        self.assertTrue(_check_trace_id_format("trace_id-with-both"))

    def test_invalid_too_short(self):
        self.assertFalse(_check_trace_id_format("abc"))

    def test_invalid_too_long(self):
        self.assertFalse(_check_trace_id_format("a" * 129))

    def test_invalid_with_slashes(self):
        self.assertFalse(_check_trace_id_format("rtr/PRO/305"))

    def test_invalid_with_dots(self):
        self.assertFalse(_check_trace_id_format("rtr.PRO.305"))

    def test_invalid_with_spaces(self):
        self.assertFalse(_check_trace_id_format("rtr PRO 305"))

    def test_invalid_not_string(self):
        self.assertFalse(_check_trace_id_format(12345))
        self.assertFalse(_check_trace_id_format(None))

    def test_invalid_empty(self):
        self.assertFalse(_check_trace_id_format(""))


# ---------------------------------------------------------------------------
# _check_a2a_bus_state() — in-memory SQLite
# ---------------------------------------------------------------------------


class TestCheckA2ABusState(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="gk_a2a_")
        self._db_path = Path(self._tmpdir) / "miru_memory.db"
        self._patcher = patch("gatekeeper.core.MEMORY_DB_PATH", self._db_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_db_passes(self):
        passed, detail = _check_a2a_bus_state("rtr-test-abc123")
        self.assertTrue(passed)
        self.assertEqual(detail, "memory_db_absent_skip")

    def test_missing_table_passes(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("CREATE TABLE other_table (id TEXT)")
        conn.close()
        passed, detail = _check_a2a_bus_state("rtr-test-abc123")
        self.assertTrue(passed)
        self.assertIn("absent_skip", detail)

    def test_no_active_claim_passes(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("CREATE TABLE agent_messages (trace_id TEXT, status TEXT)")
        conn.execute(
            "INSERT INTO agent_messages VALUES (?, ?)",
            ("rtr-other-trace", "completed"),
        )
        conn.commit()
        conn.close()
        passed, detail = _check_a2a_bus_state("rtr-test-abc123")
        self.assertTrue(passed)
        self.assertEqual(detail, "no_active_a2a_claim")

    def test_pending_claim_fails(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("CREATE TABLE agent_messages (trace_id TEXT, status TEXT)")
        conn.execute(
            "INSERT INTO agent_messages VALUES (?, ?)",
            ("rtr-test-abc123", "pending"),
        )
        conn.commit()
        conn.close()
        passed, detail = _check_a2a_bus_state("rtr-test-abc123")
        self.assertFalse(passed)
        self.assertIn("pending", detail)

    def test_claimed_status_fails(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("CREATE TABLE agent_messages (trace_id TEXT, status TEXT)")
        conn.execute(
            "INSERT INTO agent_messages VALUES (?, ?)",
            ("rtr-test-claimed", "claimed"),
        )
        conn.commit()
        conn.close()
        passed, detail = _check_a2a_bus_state("rtr-test-claimed")
        self.assertFalse(passed)
        self.assertIn("claimed", detail)

    def test_completed_claim_passes(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("CREATE TABLE agent_messages (trace_id TEXT, status TEXT)")
        conn.execute(
            "INSERT INTO agent_messages VALUES (?, ?)",
            ("rtr-test-done", "completed"),
        )
        conn.commit()
        conn.close()
        passed, detail = _check_a2a_bus_state("rtr-test-done")
        self.assertTrue(passed)


# ---------------------------------------------------------------------------
# _check_git_status() — mocked subprocess
# ---------------------------------------------------------------------------


class TestCheckGitStatus(unittest.TestCase):
    @patch("gatekeeper.core.subprocess.run")
    def test_clean_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        clean, modified = _check_git_status(Path("/fake/repo"))
        self.assertTrue(clean)
        self.assertEqual(modified, [])

    @patch("gatekeeper.core.subprocess.run")
    def test_untracked_only_is_clean(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="?? data/new_file.json\n?? logs/debug.log\n",
            stderr="",
        )
        clean, modified = _check_git_status(Path("/fake/repo"))
        self.assertTrue(clean)
        self.assertEqual(modified, [])

    @patch("gatekeeper.core.subprocess.run")
    def test_modified_file_detected(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M src/main.py\n",
            stderr="",
        )
        clean, modified = _check_git_status(Path("/fake/repo"))
        self.assertFalse(clean)
        self.assertEqual(modified, ["src/main.py"])

    @patch("gatekeeper.core.subprocess.run")
    def test_multiple_statuses(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M src/a.py\nA  src/b.py\n?? logs/x.log\nMM src/c.py\n",
            stderr="",
        )
        clean, modified = _check_git_status(Path("/fake/repo"))
        self.assertFalse(clean)
        self.assertEqual(len(modified), 3)
        self.assertNotIn("logs/x.log", modified)

    @patch("gatekeeper.core.subprocess.run")
    def test_git_failure_returns_clean(self, mock_run):
        mock_run.return_value = MagicMock(returncode=128, stdout="", stderr="fatal: not a git repo")
        clean, modified = _check_git_status(Path("/not/a/repo"))
        self.assertTrue(clean)

    @patch("gatekeeper.core.subprocess.run")
    def test_subprocess_error_returns_clean(self, mock_run):
        mock_run.side_effect = FileNotFoundError("git not found")
        clean, modified = _check_git_status(Path("/fake"))
        self.assertTrue(clean)


# ---------------------------------------------------------------------------
# _check_in_flight_dispatch() — temp routing history file
# ---------------------------------------------------------------------------


class TestCheckInFlightDispatch(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="gk_inflight_")
        self._rh_path = Path(self._tmpdir) / "routing_history.jsonl"
        self._patcher = patch("gatekeeper.core.ROUTING_HISTORY_PATH", self._rh_path)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_no_history_file_passes(self):
        passed, detail = _check_in_flight_dispatch("PRO-999")
        self.assertTrue(passed)
        self.assertEqual(detail, "no_routing_history")

    def test_empty_history_passes(self):
        self._rh_path.write_text("", encoding="utf-8")
        passed, detail = _check_in_flight_dispatch("PRO-999")
        self.assertTrue(passed)

    def test_old_dispatch_passes(self):
        old_ts = datetime.fromtimestamp(
            time.time() - IN_FLIGHT_WINDOW_SECONDS - 100, tz=UTC
        ).isoformat()
        row = {
            "task_identifier": "PRO-100",
            "timestamp": old_ts,
            "outcome": "dispatched",
            "chosen_worker": "claude-code",
        }
        self._rh_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        passed, detail = _check_in_flight_dispatch("PRO-100")
        self.assertTrue(passed)
        self.assertEqual(detail, "no_recent_active_dispatch")

    def test_recent_dispatch_fails(self):
        recent_ts = datetime.now(UTC).isoformat()
        row = {
            "task_identifier": "PRO-200",
            "timestamp": recent_ts,
            "outcome": "dispatched",
            "chosen_worker": "gemini",
        }
        self._rh_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        passed, detail = _check_in_flight_dispatch("PRO-200")
        self.assertFalse(passed)
        self.assertIn("in_flight", detail)

    def test_different_ticket_passes(self):
        recent_ts = datetime.now(UTC).isoformat()
        row = {
            "task_identifier": "PRO-300",
            "timestamp": recent_ts,
            "outcome": "dispatched",
        }
        self._rh_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        passed, detail = _check_in_flight_dispatch("PRO-999")
        self.assertTrue(passed)

    def test_non_active_outcome_passes(self):
        recent_ts = datetime.now(UTC).isoformat()
        row = {
            "task_identifier": "PRO-400",
            "timestamp": recent_ts,
            "outcome": "success",
        }
        self._rh_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        passed, detail = _check_in_flight_dispatch("PRO-400")
        self.assertTrue(passed)

    def test_ticket_id_field_also_matches(self):
        recent_ts = datetime.now(UTC).isoformat()
        row = {
            "ticket_id": "PRO-500",
            "timestamp": recent_ts,
            "outcome": "shadow-dispatched",
        }
        self._rh_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        passed, detail = _check_in_flight_dispatch("PRO-500")
        self.assertFalse(passed)

    def test_malformed_json_lines_skipped(self):
        recent_ts = datetime.now(UTC).isoformat()
        lines = [
            "not json at all\n",
            json.dumps(
                {
                    "task_identifier": "PRO-600",
                    "timestamp": recent_ts,
                    "outcome": "dispatched",
                }
            )
            + "\n",
        ]
        self._rh_path.write_text("".join(lines), encoding="utf-8")
        passed, detail = _check_in_flight_dispatch("PRO-600")
        self.assertFalse(passed)


# ---------------------------------------------------------------------------
# run_deterministic_floor() — integration
# ---------------------------------------------------------------------------


class TestRunDeterministicFloor(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="gk_floor_")
        self._db_path = Path(self._tmpdir) / "miru_memory.db"
        self._rh_path = Path(self._tmpdir) / "routing_history.jsonl"
        self._patchers = [
            patch("gatekeeper.core.MEMORY_DB_PATH", self._db_path),
            patch("gatekeeper.core.ROUTING_HISTORY_PATH", self._rh_path),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @patch("gatekeeper.core.subprocess.run")
    def test_all_green(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        checks = run_deterministic_floor(
            trace_id="rtr-PRO-305-abcdef0123456789",
            ticket_id="PRO-305",
            repo_root=Path(self._tmpdir),
        )
        self.assertTrue(checks["trace_id_valid"])
        self.assertTrue(checks["worktree_clean"])
        self.assertTrue(checks["no_in_flight_dispatch"])
        self.assertTrue(checks["a2a_clean"])

    @patch("gatekeeper.core.subprocess.run")
    def test_invalid_trace_id_detected(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        checks = run_deterministic_floor(
            trace_id="bad",
            ticket_id="PRO-305",
            repo_root=Path(self._tmpdir),
        )
        self.assertFalse(checks["trace_id_valid"])

    @patch("gatekeeper.core.subprocess.run")
    def test_dirty_worktree_detected(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M gatekeeper/core.py\n",
            stderr="",
        )
        checks = run_deterministic_floor(
            trace_id="rtr-PRO-305-abcdef0123456789",
            ticket_id="PRO-305",
            repo_root=Path(self._tmpdir),
        )
        self.assertFalse(checks["worktree_clean"])
        self.assertIn("gatekeeper/core.py", checks["_modified_paths"])


# ---------------------------------------------------------------------------
# _rejection_response() — structure
# ---------------------------------------------------------------------------


class TestRejectionResponse(unittest.TestCase):
    @patch("gatekeeper.core.log_decision")
    def test_structure(self, mock_log):
        resp = _rejection_response(
            trace_id="rtr-PRO-1-abc",
            ticket_id="PRO-1",
            reason="dirty_worktree",
            explanation="main has uncommitted changes",
            suggested_correction="Commit or stash changes.",
        )
        self.assertEqual(resp["schema_version"], "2")
        self.assertEqual(resp["trace_id"], "rtr-PRO-1-abc")
        self.assertEqual(resp["ticket_id"], "PRO-1")
        self.assertEqual(resp["decision"]["worker"], "none")
        self.assertEqual(resp["decision"]["mode"], "blocked")
        self.assertIsNone(resp["decision"]["tool_profile"])
        self.assertFalse(resp["validation"]["is_legitimate_build"])
        self.assertIsNotNone(resp["rejection"])
        self.assertEqual(resp["rejection"]["reason"], "dirty_worktree")
        self.assertIn("dirty_worktree", resp["flags"][0])

    @patch("gatekeeper.core.log_decision")
    def test_not_a_build_has_high_self_serve_probability(self, mock_log):
        resp = _rejection_response(
            trace_id="rtr-PRO-2-def",
            ticket_id="PRO-2",
            reason="not_a_build",
            explanation="conversational request",
        )
        self.assertEqual(resp["validation"]["self_serve_probability"], 1.0)

    @patch("gatekeeper.core.log_decision")
    def test_other_reason_has_lower_self_serve_probability(self, mock_log):
        resp = _rejection_response(
            trace_id="rtr-PRO-3-ghi",
            ticket_id="PRO-3",
            reason="ghost_task",
            explanation="trace_id already claimed",
        )
        self.assertEqual(resp["validation"]["self_serve_probability"], 0.5)

    @patch("gatekeeper.core.log_decision")
    def test_custom_checks_passed_through(self, mock_log):
        checks = {
            "trace_id_valid": True,
            "ticket_exists_and_open": True,
            "worktree_clean": False,
            "no_in_flight_dispatch": True,
            "_modified_paths": ["src/a.py"],
        }
        resp = _rejection_response(
            trace_id="rtr-PRO-4-jkl",
            ticket_id="PRO-4",
            reason="dirty_worktree",
            explanation="dirty",
            checks=checks,
        )
        det = resp["validation"]["deterministic_checks"]
        self.assertFalse(det["worktree_clean"])
        self.assertNotIn("_modified_paths", det)


# ---------------------------------------------------------------------------
# gate_dispatch() — payload validation + deterministic rejections
# ---------------------------------------------------------------------------


class TestGateDispatch(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="gk_gate_")
        self._db_path = Path(self._tmpdir) / "miru_memory.db"
        self._rh_path = Path(self._tmpdir) / "routing_history.jsonl"
        self._emit_script = Path(self._tmpdir) / "tools" / "emit_decision.py"
        self._patchers = [
            patch("gatekeeper.core.MEMORY_DB_PATH", self._db_path),
            patch("gatekeeper.core.ROUTING_HISTORY_PATH", self._rh_path),
            patch("gatekeeper.core.REPO_ROOT", Path(self._tmpdir)),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_missing_ticket_id_raises(self):
        with self.assertRaises(GatekeeperError) as ctx:
            gate_dispatch({"prompt": "do something"})
        self.assertEqual(ctx.exception.reason, "payload_missing_ticket_id")

    def test_missing_prompt_raises(self):
        with self.assertRaises(GatekeeperError) as ctx:
            gate_dispatch({"ticket_id": "PRO-1"})
        self.assertEqual(ctx.exception.reason, "payload_missing_prompt")

    def test_empty_prompt_raises(self):
        with self.assertRaises(GatekeeperError) as ctx:
            gate_dispatch({"ticket_id": "PRO-1", "prompt": "   "})
        self.assertEqual(ctx.exception.reason, "payload_missing_prompt")

    def test_non_string_prompt_raises(self):
        with self.assertRaises(GatekeeperError) as ctx:
            gate_dispatch({"ticket_id": "PRO-1", "prompt": 42})
        self.assertEqual(ctx.exception.reason, "payload_missing_prompt")

    @patch("gatekeeper.core.subprocess.run")
    def test_invalid_trace_id_rejected(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = gate_dispatch(
            {
                "ticket_id": "PRO-1",
                "prompt": "Do the work",
                "trace_id": "bad",
            }
        )
        self.assertEqual(result["decision"]["worker"], "none")
        self.assertEqual(result["rejection"]["reason"], "not_a_build")

    @patch("gatekeeper.core.subprocess.run")
    def test_dirty_worktree_rejected(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=" M src/main.py\n",
            stderr="",
        )
        result = gate_dispatch(
            {
                "ticket_id": "PRO-1",
                "prompt": "Fix the bug",
                "trace_id": "rtr-PRO-1-abcdef0123456789",
            }
        )
        self.assertEqual(result["rejection"]["reason"], "dirty_worktree")

    @patch("gatekeeper.core.subprocess.run")
    def test_in_flight_dispatch_rejected(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        recent_ts = datetime.now(UTC).isoformat()
        row = {
            "task_identifier": "PRO-10",
            "timestamp": recent_ts,
            "outcome": "dispatched",
        }
        self._rh_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

        result = gate_dispatch(
            {
                "ticket_id": "PRO-10",
                "prompt": "Redo the work",
                "trace_id": "rtr-PRO-10-abcdef0123456789",
            }
        )
        self.assertEqual(result["rejection"]["reason"], "ticket_drift_unresolved")

    @patch("gatekeeper.core.subprocess.run")
    def test_a2a_bus_claim_rejected(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("CREATE TABLE agent_messages (trace_id TEXT, status TEXT)")
        conn.execute(
            "INSERT INTO agent_messages VALUES (?, ?)",
            ("rtr-PRO-20-abcdef0123456789", "pending"),
        )
        conn.commit()
        conn.close()

        result = gate_dispatch(
            {
                "ticket_id": "PRO-20",
                "prompt": "Build feature",
                "trace_id": "rtr-PRO-20-abcdef0123456789",
            }
        )
        self.assertEqual(result["rejection"]["reason"], "ghost_task")

    @patch("gatekeeper.core.subprocess.run")
    def test_bad_frontmatter_in_description_rejected(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = gate_dispatch(
            {
                "ticket_id": "PRO-30",
                "prompt": "Do the task",
                "trace_id": "rtr-PRO-30-abcdef0123456789",
                "ticket_description": "<!-- dispatch:\n  worker: invalid-worker\n  scope: test\n-->",
            }
        )
        self.assertEqual(result["rejection"]["reason"], "not_a_build")
        self.assertIn("Frontmatter parse failed", result["rejection"]["explanation"])


# ---------------------------------------------------------------------------
# _build_prompt()
# ---------------------------------------------------------------------------


class TestBuildPrompt(unittest.TestCase):
    def test_contains_governance_preamble(self):
        prompt = _build_prompt(
            ticket_id="PRO-1",
            frontmatter={"worker": "claude-code", "scope": "test"},
            git_status=[],
            conversational_delta=None,
            deterministic_checks={"trace_id_valid": True},
        )
        self.assertIn("Local Governance Gatekeeper", prompt)

    def test_includes_ticket_id(self):
        prompt = _build_prompt(
            ticket_id="PRO-305",
            frontmatter=None,
            git_status=[],
            conversational_delta=None,
            deterministic_checks={},
        )
        self.assertIn("PRO-305", prompt)

    def test_includes_modified_paths(self):
        prompt = _build_prompt(
            ticket_id="PRO-1",
            frontmatter=None,
            git_status=["src/main.py", "tests/test.py"],
            conversational_delta=None,
            deterministic_checks={},
        )
        self.assertIn("src/main.py", prompt)
        self.assertIn("tests/test.py", prompt)

    def test_no_frontmatter_placeholder(self):
        prompt = _build_prompt(
            ticket_id="PRO-1",
            frontmatter=None,
            git_status=[],
            conversational_delta=None,
            deterministic_checks={},
        )
        self.assertIn("no frontmatter", prompt)

    def test_includes_conversational_delta(self):
        prompt = _build_prompt(
            ticket_id="PRO-1",
            frontmatter=None,
            git_status=[],
            conversational_delta="Operator says: add logging to auth module",
            deterministic_checks={},
        )
        self.assertIn("add logging to auth module", prompt)

    def test_strips_private_keys_from_checks(self):
        prompt = _build_prompt(
            ticket_id="PRO-1",
            frontmatter=None,
            git_status=[],
            conversational_delta=None,
            deterministic_checks={
                "trace_id_valid": True,
                "_modified_paths": ["secret.py"],
            },
        )
        self.assertNotIn("_modified_paths", prompt)
        self.assertIn("trace_id_valid", prompt)


# ---------------------------------------------------------------------------
# GatekeeperError structure
# ---------------------------------------------------------------------------


class TestGatekeeperError(unittest.TestCase):
    def test_reason_and_detail(self):
        err = GatekeeperError("ollama_timeout", "30s exceeded")
        self.assertEqual(err.reason, "ollama_timeout")
        self.assertEqual(err.detail, "30s exceeded")
        self.assertIn("ollama_timeout", str(err))

    def test_reason_only(self):
        err = GatekeeperError("payload_missing_ticket_id")
        self.assertEqual(str(err), "payload_missing_ticket_id")

    def test_is_exception(self):
        self.assertTrue(issubclass(GatekeeperError, Exception))


# ---------------------------------------------------------------------------
# Constants / schema sanity
# ---------------------------------------------------------------------------


class TestSchemaConstants(unittest.TestCase):
    def test_routing_json_schema_has_required_fields(self):
        required = ROUTING_JSON_SCHEMA["required"]
        for field in [
            "schema_version",
            "trace_id",
            "ticket_id",
            "decision",
            "validation",
            "rejection",
            "flags",
            "rationale",
        ]:
            self.assertIn(field, required)

    def test_decision_worker_enum(self):
        worker_enum = ROUTING_JSON_SCHEMA["properties"]["decision"]["properties"]["worker"]["enum"]
        self.assertIn("claude-code", worker_enum)
        self.assertIn("gemini", worker_enum)
        self.assertIn("both", worker_enum)
        self.assertIn("none", worker_enum)

    def test_governance_preamble_nonempty(self):
        self.assertGreater(len(GOVERNANCE_PREAMBLE), 100)
        self.assertIn("Gatekeeper", GOVERNANCE_PREAMBLE)

    def test_default_model_set(self):
        self.assertTrue(len(DEFAULT_MODEL) > 0)

    def test_in_flight_window_positive(self):
        self.assertGreater(IN_FLIGHT_WINDOW_SECONDS, 0)


if __name__ == "__main__":
    unittest.main()
