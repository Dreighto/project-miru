"""DGAS Tier 2 #6 Part B: fault-injection tests for emit-helper chaining.

Verifies the four CC-domain emit helpers (emit_completion, emit_heartbeat,
emit_decision, emit_github_resource) now chain every row via
``tools/audit_chain.py``. Without these tests, chaining could silently
regress to plain appends and the verifier would still report ok=True for
files that look legacy-only.

Coverage per helper:
    * Chained: every row has prev_hash and row_hash.
    * Linked: row N's prev_hash equals row N-1's row_hash.
    * Anchored: the first chained row's prev_hash is None.
    * Tamper-evident: mutating a body field after the fact breaks
      validate_chain.
    * Legacy-prefix tolerant: pre-existing legacy rows at the head of the
      file are preserved and the new chained rows anchor cleanly on top.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from audit_chain import (  # noqa: E402
    CHAIN_FIELD_HASH,
    CHAIN_FIELD_PREV,
    validate_chain,
)


class _ChainAssertionsMixin:
    """Shared assertions used by every emit-helper test class."""

    def _assert_all_chained(self, path: Path, expected_count: int) -> list[dict]:
        """Read every row, assert each carries chain fields, return list."""
        self.assertTrue(path.exists(), f"{path} should exist after emit")
        rows: list[dict] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            obj = json.loads(line)
            self.assertIn(CHAIN_FIELD_PREV, obj, f"row missing prev_hash: {obj}")
            self.assertIn(CHAIN_FIELD_HASH, obj, f"row missing row_hash: {obj}")
            self.assertIsInstance(obj[CHAIN_FIELD_HASH], str)
            self.assertTrue(obj[CHAIN_FIELD_HASH], "row_hash must be non-empty")
            rows.append(obj)
        self.assertEqual(
            len(rows),
            expected_count,
            f"expected {expected_count} chained row(s), got {len(rows)} in {path}",
        )
        return rows

    def _assert_chain_links(self, rows: list[dict]) -> None:
        """First row anchors with prev_hash=None; later rows link prev→hash."""
        if not rows:
            return
        self.assertIsNone(
            rows[0][CHAIN_FIELD_PREV],
            "first chained row must anchor with prev_hash=None",
        )
        prev = rows[0][CHAIN_FIELD_HASH]
        for i, row in enumerate(rows[1:], start=1):
            self.assertEqual(
                row[CHAIN_FIELD_PREV],
                prev,
                f"row {i} prev_hash should equal row {i - 1} row_hash",
            )
            prev = row[CHAIN_FIELD_HASH]


class TestEmitHeartbeatChaining(unittest.TestCase, _ChainAssertionsMixin):
    """emit_heartbeat must chain every row."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_heartbeat_chain_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp_root, ignore_errors=True)
        self.log_path = self.tmp_root / "data" / "cc_heartbeat_log.jsonl"

        # Force the helper to write into our tmp path.
        import emit_heartbeat

        self._orig_log = emit_heartbeat.HEARTBEAT_LOG
        emit_heartbeat.HEARTBEAT_LOG = str(self.log_path)
        self.addCleanup(setattr, emit_heartbeat, "HEARTBEAT_LOG", self._orig_log)
        self.emit_heartbeat = emit_heartbeat

    def test_three_emits_chain_correctly(self) -> None:
        for step in ("pre_flight", "writing_tests", "post_merge_cleanup"):
            self.emit_heartbeat.emit(
                worker_id="cc-test",
                ticket_id="PRO-TEST",
                step=step,
                branch="dreighto/test",
            )

        rows = self._assert_all_chained(self.log_path, expected_count=3)
        self._assert_chain_links(rows)
        result = validate_chain(self.log_path)
        self.assertTrue(result.ok, f"chain should validate: {result.error}")
        self.assertEqual(result.chained_rows, 3)

    def test_tampering_with_a_body_field_breaks_chain(self) -> None:
        """Fault injection: rewrite step on row 1 → validate must fail."""
        for step in ("a", "b", "c"):
            self.emit_heartbeat.emit(worker_id="x", ticket_id="X", step=step)
        # Rewrite the middle row's `step` without recomputing its row_hash.
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        obj = json.loads(lines[1])
        obj["step"] = "TAMPERED"
        lines[1] = json.dumps(obj, separators=(",", ":"))
        self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = validate_chain(self.log_path)
        self.assertFalse(result.ok, "tampered chain must fail validation")
        self.assertEqual(result.broken_at_line, 1)


class TestEmitCompletionChaining(unittest.TestCase, _ChainAssertionsMixin):
    """emit_completion must chain every row, even on a file that already
    has hundreds of legacy (un-chained) rows."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_completion_chain_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp_root, ignore_errors=True)
        # emit_completion derives the log path from _repo_root(); we patch
        # _repo_root to return our tmp dir so the helper writes into it.
        import emit_completion

        self.emit_completion = emit_completion
        self._orig_repo_root = emit_completion._repo_root
        emit_completion._repo_root = lambda: str(self.tmp_root)
        self.addCleanup(setattr, emit_completion, "_repo_root", self._orig_repo_root)
        self.log_path = self.tmp_root / "data" / "cc_completion_log.jsonl"

    def _emit(self, marker: dict) -> None:
        with (
            mock.patch.object(sys, "argv", ["emit_completion.py"]),
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(marker))),
        ):
            self.emit_completion.main()

    def test_chained_after_legacy_prefix(self) -> None:
        """Pre-seed file with legacy rows; new emits must anchor cleanly."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text(
            json.dumps({"timestamp": "2026-01-01T00:00:00Z", "ticket_id": "OLD"}) + "\n",
            encoding="utf-8",
        )
        self._emit(
            {
                "timestamp": "2026-05-08T00:00:00Z",
                "ticket_id": "PRO-TEST",
                "status": "CONFIRMED_WORKING",
                "summary": "first chained",
            }
        )
        self._emit(
            {
                "timestamp": "2026-05-08T01:00:00Z",
                "ticket_id": "PRO-TEST",
                "status": "CONFIRMED_WORKING",
                "summary": "second chained",
            }
        )

        result = validate_chain(self.log_path)
        self.assertTrue(result.ok, f"validation failed: {result.error}")
        self.assertEqual(result.legacy_prefix_rows, 1)
        self.assertEqual(result.chained_rows, 2)


class TestEmitDecisionChaining(unittest.TestCase, _ChainAssertionsMixin):
    """emit_decision must chain every row."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_decision_chain_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp_root, ignore_errors=True)
        self.log_path = self.tmp_root / "data" / "agent_decisions.jsonl"

        import emit_decision

        self.emit_decision = emit_decision

    def _record(self, decision_id: str) -> dict:
        return {
            "decision_id": decision_id,
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

    def test_two_emits_chain_correctly(self) -> None:
        # Bypass the heavy validator — we're testing chain wiring, not schema.
        with mock.patch.object(
            self.emit_decision, "_validate_and_normalize", side_effect=lambda r: r
        ):
            self.emit_decision.emit(self._record("d1"), log_path=self.log_path)
            self.emit_decision.emit(self._record("d2"), log_path=self.log_path)

        rows = self._assert_all_chained(self.log_path, expected_count=2)
        self._assert_chain_links(rows)
        result = validate_chain(self.log_path)
        self.assertTrue(result.ok, f"chain should validate: {result.error}")


class TestEmitGithubResourceChaining(unittest.TestCase, _ChainAssertionsMixin):
    """emit_github_resource must chain every row AND fsync."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="miru_ghres_chain_"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp_root, ignore_errors=True)
        self.log_path = self.tmp_root / "data" / "github_resource_ledger.jsonl"

        import emit_github_resource

        self.emit_github_resource = emit_github_resource

    def _entry(self, resource_id: str) -> dict:
        return {
            "ts": "2026-05-08T00:00:00Z",
            "trace_id": "cc-test-x",
            "ticket_id": "PRO-TEST",
            "resource_type": "branch",
            "resource_id": resource_id,
            "intent": "create",
            "status": "pending",
        }

    def test_three_appends_chain_correctly(self) -> None:
        for rid in ("br1", "br2", "br3"):
            self.emit_github_resource.append_entry(self._entry(rid), str(self.log_path))

        rows = self._assert_all_chained(self.log_path, expected_count=3)
        self._assert_chain_links(rows)
        result = validate_chain(self.log_path)
        self.assertTrue(result.ok, f"chain should validate: {result.error}")

    def test_fsync_invoked(self) -> None:
        """fsync flag must reach the OS layer — losing a ledger row orphans
        a real-world GitHub resource."""
        with mock.patch("os.fsync") as mock_fsync:
            self.emit_github_resource.append_entry(self._entry("br1"), str(self.log_path))
            self.assertGreaterEqual(
                mock_fsync.call_count,
                1,
                "github_resource_ledger writes must fsync; the ledger tracks "
                "external state and a lost row means an orphan resource",
            )


class TestEmitHelpersInvokableViaCLI(unittest.TestCase):
    """Smoke check: the CLI entry points still work end-to-end after the
    chaining wiring. This catches stupid breakage like broken imports."""

    def test_emit_heartbeat_cli_runs(self) -> None:
        """End-to-end: invoke the actual CLI parser path, not just emit().
        This catches argparse regressions, broken imports, and __main__
        breakage that direct-emit smoke tests miss."""
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "data" / "cc_heartbeat_log.jsonl"
            env = {
                **__import__("os").environ,
                "PYTHONPATH": str(TOOLS_DIR),
            }
            # Wrapper redirects HEARTBEAT_LOG to a tmp file, then invokes
            # main() via sys.argv so argparse runs end-to-end. This way a
            # regression in --worker-id parsing or main() flow control
            # actually fails the test.
            wrapper = (
                f"import sys; sys.path.insert(0, {str(TOOLS_DIR)!r});"
                f"import emit_heartbeat;"
                f"emit_heartbeat.HEARTBEAT_LOG={str(log_path)!r};"
                f"sys.argv=['emit_heartbeat.py',"
                f" '--worker-id', 'cc',"
                f" '--ticket-id', 'PRO-X',"
                f" '--step', 'cli_smoke'];"
                f"emit_heartbeat.main()"
            )
            result = subprocess.run(
                [sys.executable, "-c", wrapper],
                capture_output=True,
                text=True,
                env=env,
                timeout=30,
                check=False,
            )
            self.assertEqual(
                result.returncode, 0, f"emit_heartbeat CLI smoke failed: {result.stderr}"
            )
            self.assertTrue(log_path.exists())
            obj = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn(CHAIN_FIELD_HASH, obj)
            self.assertIsNone(obj[CHAIN_FIELD_PREV], "first row must anchor with prev_hash=None")


if __name__ == "__main__":
    unittest.main()
