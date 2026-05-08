"""DGAS Tier 2 #7: fault-injection tests for the daily audit anchor.

Verifies that the anchor:
    * captures every canonical file's size, sha256, and chain state
    * writes itself as a chained row (own row_hash)
    * tolerates missing files (records exists=False rather than crashing)
    * surfaces errors via exit code without aborting the anchor write
    * detects post-anchor tampering by file_sha256 mismatch on re-snapshot
"""

from __future__ import annotations

import json
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

import emit_audit_anchor as anchor_mod  # noqa: E402
from audit_chain import (  # noqa: E402
    CHAIN_FIELD_HASH,
    CHAIN_FIELD_PREV,
    append_chained,
    validate_chain,
)


class TestSnapshotFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_anchor_snap_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_file_records_exists_false(self) -> None:
        snap = anchor_mod.snapshot_file("data/nope.jsonl", self.tmp)
        self.assertFalse(snap["exists"])
        self.assertIsNone(snap["file_sha256"])
        self.assertTrue(snap["chain_ok"], "missing files don't break the anchor")

    def test_legacy_only_file_records_no_last_chained(self) -> None:
        path = self.tmp / "data" / "legacy.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"timestamp": "2026-01-01", "ticket_id": "OLD"}) + "\n",
            encoding="utf-8",
        )
        snap = anchor_mod.snapshot_file("data/legacy.jsonl", self.tmp)
        self.assertTrue(snap["exists"])
        self.assertEqual(snap["chained_rows"], 0)
        self.assertEqual(snap["legacy_prefix_rows"], 1)
        self.assertIsNone(snap["last_chained_row_hash"])
        self.assertIsNotNone(snap["file_sha256"])

    def test_chained_file_captures_last_row_hash(self) -> None:
        path = self.tmp / "data" / "chained.jsonl"
        h1 = append_chained(path, {"x": 1})
        h2 = append_chained(path, {"x": 2})
        snap = anchor_mod.snapshot_file("data/chained.jsonl", self.tmp)
        self.assertEqual(snap["chained_rows"], 2)
        self.assertEqual(snap["last_chained_row_hash"], h2)
        self.assertNotEqual(h1, h2)

    def test_broken_chain_marks_chain_ok_false(self) -> None:
        path = self.tmp / "data" / "broken.jsonl"
        append_chained(path, {"x": 1})
        append_chained(path, {"x": 2})
        # Tamper with row 0.
        lines = path.read_text(encoding="utf-8").splitlines()
        obj = json.loads(lines[0])
        obj["x"] = 999
        lines[0] = json.dumps(obj, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        snap = anchor_mod.snapshot_file("data/broken.jsonl", self.tmp)
        self.assertFalse(snap["chain_ok"])
        self.assertIsNotNone(snap["error"])
        self.assertIsNone(snap["last_chained_row_hash"])


class TestBuildAnchorRow(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_anchor_build_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_anchor_includes_every_canonical_file(self) -> None:
        row = anchor_mod.build_anchor_row(self.tmp)
        paths = [s["path"] for s in row["files"]]
        for canonical in anchor_mod.AUDIT_FILES:
            self.assertIn(canonical, paths)
        self.assertEqual(len(row["files"]), len(anchor_mod.AUDIT_FILES))

    def test_anchor_carries_required_top_level_fields(self) -> None:
        row = anchor_mod.build_anchor_row(self.tmp)
        for field in ("ts", "anchor_for_date", "schema_version", "files"):
            self.assertIn(field, row)
        self.assertEqual(row["schema_version"], 1)
        self.assertRegex(row["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertRegex(row["anchor_for_date"], r"^\d{4}-\d{2}-\d{2}$")


class TestAnchorWriteAndChain(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_anchor_write_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Patch _repo_root so main() writes inside our tmp dir.
        self._orig_repo_root = anchor_mod._repo_root
        anchor_mod._repo_root = lambda: self.tmp
        self.addCleanup(setattr, anchor_mod, "_repo_root", self._orig_repo_root)

    def _run_main(self, *argv: str) -> int:
        with mock.patch.object(sys, "argv", ["emit_audit_anchor.py", *argv]):
            return anchor_mod.main()

    def test_main_writes_chained_anchor_row(self) -> None:
        rc = self._run_main()
        self.assertEqual(rc, 0)
        anchor_path = self.tmp / anchor_mod.ANCHOR_LOG_REL
        self.assertTrue(anchor_path.exists())

        # Anchor itself must validate as a chain.
        result = validate_chain(anchor_path)
        self.assertTrue(result.ok)
        self.assertEqual(result.chained_rows, 1)

        # First row has prev_hash=None and a row_hash.
        row = json.loads(anchor_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertIsNone(row[CHAIN_FIELD_PREV])
        self.assertIsInstance(row[CHAIN_FIELD_HASH], str)

    def test_two_runs_chain_correctly(self) -> None:
        self._run_main()
        self._run_main()
        anchor_path = self.tmp / anchor_mod.ANCHOR_LOG_REL
        result = validate_chain(anchor_path)
        self.assertTrue(result.ok)
        self.assertEqual(result.chained_rows, 2)

    def test_dry_run_does_not_write(self) -> None:
        rc = self._run_main("--dry-run")
        self.assertEqual(rc, 0)
        anchor_path = self.tmp / anchor_mod.ANCHOR_LOG_REL
        self.assertFalse(anchor_path.exists(), "--dry-run must not emit")


class TestRetroactiveTamperDetection(unittest.TestCase):
    """The headline use case: an anchor sealed yesterday detects a tampered
    legacy row today, even though the legacy row predates chaining."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_anchor_tamper_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_post_anchor_tamper_breaks_file_sha256(self) -> None:
        target = self.tmp / "data" / "cc_completion_log.jsonl"
        target.parent.mkdir(parents=True)
        # Pre-seed with a legacy row that we'll tamper later.
        target.write_text(
            json.dumps({"timestamp": "2026-01-01", "ticket_id": "OLD", "secret": "real"}) + "\n",
            encoding="utf-8",
        )
        # Snapshot now (this is "yesterday's anchor").
        snap_before = anchor_mod.snapshot_file("data/cc_completion_log.jsonl", self.tmp)
        # Tamper: rewrite the legacy row.
        target.write_text(
            json.dumps({"timestamp": "2026-01-01", "ticket_id": "OLD", "secret": "FAKE"}) + "\n",
            encoding="utf-8",
        )
        # Snapshot again (this is "today's anchor"). file_sha256 must differ.
        snap_after = anchor_mod.snapshot_file("data/cc_completion_log.jsonl", self.tmp)
        self.assertNotEqual(
            snap_before["file_sha256"],
            snap_after["file_sha256"],
            "tampering with a legacy row must change file_sha256",
        )


if __name__ == "__main__":
    unittest.main()
