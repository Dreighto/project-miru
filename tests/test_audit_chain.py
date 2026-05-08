"""DGAS Tier 2 #6: fault-injection tests for the hash-chain library.

Verifies the gate works end-to-end:
    * append_chained writes prev_hash + row_hash correctly
    * validate_chain accepts a clean chain
    * validate_chain detects every documented tampering pattern
    * legacy prefix rows (no row_hash) are tolerated at the head
    * legacy rows interleaved AFTER chained rows are rejected
    * parse errors are reported, not raised
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_chain  # noqa: E402


class TestAppendAndValidate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_audit_chain_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "test.jsonl"

    def test_empty_file_validates_ok(self) -> None:
        # Validate a brand-new empty file. No rows, no error.
        self.path.write_text("", encoding="utf-8")
        result = audit_chain.validate_chain(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.total_rows, 0)
        self.assertEqual(result.chained_rows, 0)

    def test_missing_file_returns_error(self) -> None:
        result = audit_chain.validate_chain(self.tmp / "nope.jsonl")
        self.assertFalse(result.ok)
        self.assertIn("file not found", result.error or "")

    def test_single_appended_row_validates(self) -> None:
        h1 = audit_chain.append_chained(self.path, {"event": "first"})
        result = audit_chain.validate_chain(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.total_rows, 1)
        self.assertEqual(result.chained_rows, 1)
        self.assertEqual(result.legacy_prefix_rows, 0)
        # The first chained row's prev_hash must be null because the file
        # was empty before the append.
        loaded = json.loads(self.path.read_text(encoding="utf-8").strip())
        self.assertIsNone(loaded["prev_hash"])
        self.assertEqual(loaded["row_hash"], h1)

    def test_multiple_appended_rows_chain_correctly(self) -> None:
        h1 = audit_chain.append_chained(self.path, {"event": "a"})
        h2 = audit_chain.append_chained(self.path, {"event": "b"})
        h3 = audit_chain.append_chained(self.path, {"event": "c"})
        result = audit_chain.validate_chain(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.chained_rows, 3)

        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertIsNone(rows[0]["prev_hash"])
        self.assertEqual(rows[1]["prev_hash"], h1)
        self.assertEqual(rows[2]["prev_hash"], h2)
        self.assertEqual(rows[2]["row_hash"], h3)


class TestTamperDetection(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_audit_chain_tamper_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "test.jsonl"
        for i in range(3):
            audit_chain.append_chained(self.path, {"event": f"row-{i}"})

    def _rewrite_lines(self, mutator) -> None:
        lines = self.path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines]
        rows = mutator(rows)
        self.path.write_text(
            "\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n",
            encoding="utf-8",
        )

    def test_modifying_an_old_row_breaks_the_chain(self) -> None:
        """Fault injection: edit row 0's body without recomputing its hash."""

        def mutate(rows):
            rows[0]["event"] = "TAMPERED"
            return rows

        self._rewrite_lines(mutate)
        result = audit_chain.validate_chain(self.path)
        self.assertFalse(result.ok)
        self.assertEqual(result.broken_at_line, 0)
        self.assertIn("row_hash mismatch", result.error or "")

    def test_modifying_row_hash_without_body_breaks_chain(self) -> None:
        """Fault injection: rewrite row_hash to a different value."""

        def mutate(rows):
            rows[1]["row_hash"] = "0" * 64
            return rows

        self._rewrite_lines(mutate)
        result = audit_chain.validate_chain(self.path)
        self.assertFalse(result.ok)
        self.assertEqual(result.broken_at_line, 1)

    def test_breaking_prev_hash_link_breaks_chain(self) -> None:
        """Fault injection: delete a row from the middle (the next row's
        prev_hash now points at a deleted predecessor)."""

        def mutate(rows):
            return [rows[0], rows[2]]  # row 1 removed

        self._rewrite_lines(mutate)
        result = audit_chain.validate_chain(self.path)
        self.assertFalse(result.ok)
        self.assertIn("prev_hash mismatch", result.error or "")

    def test_swapping_two_rows_breaks_chain(self) -> None:
        """Fault injection: reorder rows. prev_hash links no longer line up."""

        def mutate(rows):
            return [rows[1], rows[0], rows[2]]

        self._rewrite_lines(mutate)
        result = audit_chain.validate_chain(self.path)
        self.assertFalse(result.ok)

    def test_deleting_head_row_breaks_chain(self) -> None:
        """Fault injection: delete the FIRST chained row. The new head row's
        prev_hash points at the deleted predecessor (non-None), which now
        violates the 'first chained row must anchor to None' invariant."""

        def mutate(rows):
            return [rows[1], rows[2]]  # head row removed

        self._rewrite_lines(mutate)
        result = audit_chain.validate_chain(self.path)
        self.assertFalse(result.ok)
        self.assertEqual(result.broken_at_line, 0)
        self.assertIn("first chained row must declare prev_hash=None", result.error or "")


class TestLegacyPrefix(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_audit_chain_legacy_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "test.jsonl"

    def test_legacy_only_file_is_tolerated(self) -> None:
        """A file with no row_hash anywhere is valid — chain hasn't started."""
        self.path.write_text(
            '{"event":"old1"}\n{"event":"old2"}\n{"event":"old3"}\n', encoding="utf-8"
        )
        result = audit_chain.validate_chain(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.total_rows, 3)
        self.assertEqual(result.chained_rows, 0)
        self.assertEqual(result.legacy_prefix_rows, 3)

    def test_legacy_prefix_then_chained_rows_validates(self) -> None:
        """Pre-chain rows at the head are tolerated; chain starts mid-file."""
        self.path.write_text('{"event":"old"}\n{"event":"older"}\n', encoding="utf-8")
        audit_chain.append_chained(self.path, {"event": "first-chained"})
        audit_chain.append_chained(self.path, {"event": "second-chained"})
        result = audit_chain.validate_chain(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.legacy_prefix_rows, 2)
        self.assertEqual(result.chained_rows, 2)

    def test_legacy_row_after_chain_started_is_rejected(self) -> None:
        """Fault injection: insert a legacy row in the middle of a chain.
        That would let an attacker drop a row but blame the chain on
        "legacy prefix tolerance." Rejected."""
        audit_chain.append_chained(self.path, {"event": "a"})
        audit_chain.append_chained(self.path, {"event": "b"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write('{"event":"sneaky-legacy"}\n')
        result = audit_chain.validate_chain(self.path)
        self.assertFalse(result.ok)
        self.assertIn("legacy row appears after chained rows began", result.error or "")


class TestParseTolerance(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_audit_chain_parse_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "test.jsonl"

    def test_blank_lines_are_skipped(self) -> None:
        audit_chain.append_chained(self.path, {"event": "a"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("\n\n")
        audit_chain.append_chained(self.path, {"event": "b"})
        result = audit_chain.validate_chain(self.path)
        self.assertTrue(result.ok)
        self.assertEqual(result.chained_rows, 2)

    def test_unparseable_line_is_recorded_not_raised(self) -> None:
        """A garbage line records a parse error and continues. The chain
        rows around it must still verify."""
        audit_chain.append_chained(self.path, {"event": "a"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("not-json{broken\n")
        # Continuing the chain after a corrupt line is a real concern. We do
        # not auto-skip; the chained verifier reads valid rows and the next
        # append_chained call will base prev_hash on the last parseable
        # chained row in the tail — which is correct behaviour.
        audit_chain.append_chained(self.path, {"event": "c"})
        result = audit_chain.validate_chain(self.path)
        self.assertEqual(len(result.parse_errors), 1)
        self.assertEqual(result.chained_rows, 2)
        self.assertTrue(result.ok)

    def test_non_object_json_line_is_recorded_not_raised(self) -> None:
        """A parseable JSON line that is NOT an object (a bare array, scalar,
        or boolean) is recorded as a parse error and skipped. Without the
        isinstance(obj, dict) guard, calling .get() on these would crash."""
        audit_chain.append_chained(self.path, {"event": "a"})
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("[]\n")  # parseable JSON, but not an object
            fh.write('"just a string"\n')
            fh.write("42\n")
            fh.write("true\n")
        audit_chain.append_chained(self.path, {"event": "b"})
        result = audit_chain.validate_chain(self.path)
        # Four non-object lines recorded as parse errors.
        self.assertEqual(len(result.parse_errors), 4)
        # Two real chained rows still verify.
        self.assertEqual(result.chained_rows, 2)
        self.assertTrue(result.ok)


class TestAppendUsesLatestTail(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_audit_chain_tail_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "test.jsonl"

    def test_append_after_legacy_only_starts_fresh_chain(self) -> None:
        """If the file is all legacy, the next append starts a new chain
        link with prev_hash=None. Operator can choose to retire the legacy
        prefix later if needed."""
        self.path.write_text('{"event":"legacy"}\n', encoding="utf-8")
        audit_chain.append_chained(self.path, {"event": "first-chained"})
        rows = [json.loads(line) for line in self.path.read_text().splitlines()]
        self.assertEqual(rows[0], {"event": "legacy"})
        self.assertIsNone(rows[1]["prev_hash"])
        self.assertIsInstance(rows[1]["row_hash"], str)


if __name__ == "__main__":
    unittest.main()
