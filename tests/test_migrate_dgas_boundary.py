"""LOS-10 Step 4: writer-side migration test suite.

Counterpart to tests/test_verify_dgas_boundary.py (reader). Covers:

- append_v2_chained: empty file with anchor, subsequent rows, ValueError
  when first-row anchor missing.
- migrate(): happy-path dry-run, happy-path real run, refuses on broken
  chain, refuses on empty log, refuses on overwrite without --force,
  rejects bad canon_snapshot_id.
- Round-trip: writer output verifies cleanly against verify_dgas_boundary.

These tests do NOT exercise ssh-keygen (signature) or git tag paths —
those depend on external state and are covered by manual operator
runbook + the verifier's signature test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import audit_chain  # noqa: E402
import migrate_dgas_boundary as migrate_mod  # noqa: E402

VALID_CANON_ID = "a" * 64


class TestAppendV2Chained(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_v2_chain_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "v2.jsonl"

    def test_first_row_requires_anchor(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            audit_chain.append_v2_chained(self.path, {"event": "first"})
        self.assertIn("anchor_prev_hash", str(ctx.exception))

    def test_first_row_with_anchor_records_correctly(self) -> None:
        anchor_hash = "b" * 64
        rh, bi = audit_chain.append_v2_chained(
            self.path,
            {"event": "first"},
            anchor_prev_hash=anchor_hash,
            anchor_block_index=5,
        )
        self.assertIsInstance(rh, str)
        self.assertEqual(len(rh), 64)
        self.assertEqual(bi, 5)

        line = json.loads(self.path.read_text(encoding="utf-8").strip())
        self.assertEqual(line["prev_hash"], anchor_hash)
        self.assertEqual(line["block_index"], 5)
        self.assertEqual(line["row_hash"], rh)
        self.assertEqual(line["event"], "first")

    def test_subsequent_rows_increment_block_index_and_chain(self) -> None:
        anchor_hash = "c" * 64
        h1, b1 = audit_chain.append_v2_chained(
            self.path, {"event": "a"}, anchor_prev_hash=anchor_hash, anchor_block_index=10
        )
        h2, b2 = audit_chain.append_v2_chained(self.path, {"event": "b"})
        h3, b3 = audit_chain.append_v2_chained(self.path, {"event": "c"})

        self.assertEqual(b1, 10)
        self.assertEqual(b2, 11)
        self.assertEqual(b3, 12)

        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["prev_hash"], anchor_hash)
        self.assertEqual(rows[1]["prev_hash"], h1)
        self.assertEqual(rows[2]["prev_hash"], h2)
        self.assertEqual(rows[2]["row_hash"], h3)

    def test_read_last_v2_state_whole_file_fallback(self) -> None:
        """CR R3 (PR #182 combined): _read_last_v2_state must fall back to
        whole-file read if the 64KiB tail window doesn't contain a complete
        parseable line. Mirrors v1's _read_last_chained behavior. Without
        the fallback, a >64KiB last row would return (None, None) and force
        append_v2_chained to incorrectly demand anchor params."""
        # Construct a v2 chain where the last row's payload exceeds 64KiB.
        anchor_hash = "e" * 64
        # First row: normal size.
        audit_chain.append_v2_chained(
            self.path,
            {"event": "first", "small": True},
            anchor_prev_hash=anchor_hash,
            anchor_block_index=1,
        )
        # Second row: payload > 64KiB. The serialized line will exceed the
        # tail window, forcing the whole-file fallback path.
        big_payload = {"event": "huge", "padding": "x" * 80000}
        audit_chain.append_v2_chained(self.path, big_payload)

        # If the fallback works, a subsequent append should succeed (no
        # anchor params needed; it reads the prior row's hash + block_index
        # from the whole-file fallback).
        rh3, bi3 = audit_chain.append_v2_chained(self.path, {"event": "third"})
        self.assertEqual(bi3, 3)
        # And the row chains from the huge row, not from anchor.
        rows = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[2]["prev_hash"], rows[1]["row_hash"])

    def test_append_v2_refuses_on_corrupt_tail(self) -> None:
        """CR R4 (PR #182 combined): if the file has content but the tail
        row is missing row_hash or block_index, append_v2_chained MUST
        refuse to append. Recovering silently would fork the chain at
        the point of existing corruption."""
        # Write a single line that is parseable JSON but missing both fields.
        self.path.write_text(
            json.dumps({"event": "looks-like-row-but-no-chain-fields"}) + "\n",
            encoding="utf-8",
        )
        # Even with anchor params provided, the corrupt-tail check fires
        # before the empty-file path is considered.
        with self.assertRaises(ValueError) as ctx:
            audit_chain.append_v2_chained(
                self.path,
                {"event": "new"},
                anchor_prev_hash="f" * 64,
                anchor_block_index=1,
            )
        self.assertIn("corrupt", str(ctx.exception).lower())

    def test_append_v2_refuses_on_unparseable_tail(self) -> None:
        """Companion: garbage content (no parseable JSON anywhere) must
        also be treated as corrupt rather than 'empty file with junk'."""
        self.path.write_text("not-json{{{\nmore-garbage\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            audit_chain.append_v2_chained(
                self.path,
                {"event": "new"},
                anchor_prev_hash="f" * 64,
                anchor_block_index=1,
            )
        self.assertIn("corrupt", str(ctx.exception).lower())

    def test_hash_formula_matches_verifier(self) -> None:
        # The writer's formula must produce a hash that the verifier
        # accepts. Recompute the expected hash by hand using the exact
        # formula from tools/verify_dgas_boundary.py.
        import hashlib

        anchor_hash = "d" * 64
        block_index = 7
        payload = {"event": "verify-me", "ticket_id": "PRO-999"}

        # Writer's path:
        rh, bi = audit_chain.append_v2_chained(
            self.path,
            payload,
            anchor_prev_hash=anchor_hash,
            anchor_block_index=block_index,
        )

        # Recomputed by hand:
        canonical_payload = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        payload_inner = hashlib.sha256(canonical_payload).digest()
        combined = (
            b"DGASv1"
            + str(block_index).encode("ascii")
            + anchor_hash.encode("ascii")
            + payload_inner
        )
        expected = hashlib.sha256(combined).hexdigest()

        self.assertEqual(rh, expected)
        self.assertEqual(bi, block_index)


class TestMigrate(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_migrate_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.legacy = self.tmp / "legacy.jsonl"
        self.outdir = self.tmp / "out"

    def _seed_legacy(self, n_rows: int = 3) -> list[str]:
        """Append n chained rows to self.legacy. Returns row_hashes."""
        hashes = []
        for i in range(n_rows):
            h = audit_chain.append_chained(self.legacy, {"event": f"row-{i}", "i": i})
            hashes.append(h)
        return hashes

    def test_dry_run_does_not_write_anything(self) -> None:
        self._seed_legacy(3)
        result = migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            dry_run=True,
        )
        self.assertTrue(result["dry_run"])
        # Outdir should not exist; no artifacts created.
        self.assertFalse(self.outdir.exists())
        self.assertEqual(result["v1_summary"]["row_count"], 3)

    def test_happy_path_creates_all_artifacts(self) -> None:
        hashes = self._seed_legacy(5)
        result = migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            created_at_utc="2026-05-10T12:34:56Z",
        )
        frozen_path = Path(result["frozen_log_output"])
        manifest_path = Path(result["manifest_output"])
        v2_path = Path(result["v2_log_output"])

        self.assertTrue(frozen_path.exists())
        self.assertTrue(manifest_path.exists())
        self.assertTrue(v2_path.exists())
        self.assertEqual(v2_path.stat().st_size, 0)  # empty placeholder

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["terminal_hash"], hashes[-1])
        self.assertEqual(manifest["row_count"], 5)
        self.assertEqual(manifest["terminal_block"], 5)
        self.assertEqual(manifest["new_chain_starts_at"], 6)
        self.assertEqual(manifest["hash_algorithm"], "sha256")
        self.assertEqual(manifest["new_chain_format_version"], "DGAS_V2")
        self.assertEqual(manifest["canon_snapshot_id_at_cutover"], VALID_CANON_ID)
        self.assertEqual(manifest["created_at_utc"], "2026-05-10T12:34:56Z")
        self.assertEqual(manifest["legacy_log_path"], frozen_path.name)
        self.assertEqual(manifest["byte_length"], frozen_path.stat().st_size)

    def test_refuses_overwrite_without_force(self) -> None:
        self._seed_legacy(2)
        # First run succeeds
        migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            created_at_utc="2026-05-10T12:00:00Z",
        )
        # Second run with same timestamp must refuse
        with self.assertRaises(ValueError) as ctx:
            migrate_mod.migrate(
                legacy_log=self.legacy,
                output_dir=self.outdir,
                canon_snapshot_id=VALID_CANON_ID,
                created_at_utc="2026-05-10T12:00:00Z",
            )
        self.assertIn("refusing to overwrite", str(ctx.exception))

    def test_force_allows_overwrite(self) -> None:
        self._seed_legacy(2)
        migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            created_at_utc="2026-05-10T12:00:00Z",
        )
        # Re-run with --force should succeed
        result = migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            created_at_utc="2026-05-10T12:00:00Z",
            force=True,
        )
        self.assertEqual(result["v1_summary"]["row_count"], 2)

    def test_rejects_bad_canon_snapshot_id(self) -> None:
        self._seed_legacy(1)
        for bad in ["", "short", "A" * 64, "g" * 64, "a" * 63, "a" * 65]:
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                migrate_mod.migrate(
                    legacy_log=self.legacy,
                    output_dir=self.outdir / bad,
                    canon_snapshot_id=bad,
                    dry_run=True,
                )

    def test_rejects_malformed_created_at_utc(self) -> None:
        """CR R4 CRITICAL: created_at_utc gets embedded into frozen_name,
        so it must be validated as a strict ISO 8601 UTC string before
        use. Inputs containing path separators or `..` segments would
        otherwise escape output_dir."""
        self._seed_legacy(1)
        bad_timestamps = [
            "2026-01-01T00:00:00Z/../escape",  # path traversal attempt
            "../../../../tmp/evil",  # raw path
            "not-a-timestamp",
            "2026-01-01 00:00:00",  # space instead of T
            "2026-13-01T00:00:00Z",  # invalid month
            "2026-02-30T00:00:00Z",  # invalid day
            "",
            "2026-01-01T00:00:00",  # missing Z
        ]
        for bad in bad_timestamps:
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                migrate_mod.migrate(
                    legacy_log=self.legacy,
                    output_dir=self.outdir,
                    canon_snapshot_id=VALID_CANON_ID,
                    created_at_utc=bad,
                    dry_run=True,
                )

    def test_accepts_canonical_iso_utc(self) -> None:
        """Sanity: the canonical form must still pass."""
        self._seed_legacy(1)
        result = migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            created_at_utc="2026-05-10T12:34:56Z",
            dry_run=True,
        )
        self.assertEqual(result["created_at_utc"], "2026-05-10T12:34:56Z")

    def test_refuses_empty_legacy_log(self) -> None:
        self.legacy.write_text("", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            migrate_mod.migrate(
                legacy_log=self.legacy,
                output_dir=self.outdir,
                canon_snapshot_id=VALID_CANON_ID,
                dry_run=True,
            )
        self.assertIn("no chained rows", str(ctx.exception))

    def test_refuses_tampered_chain(self) -> None:
        self._seed_legacy(3)
        # Tamper: overwrite the second row's event field, leaving the
        # row_hash stale. The strict walk should detect the mismatch.
        lines = self.legacy.read_text(encoding="utf-8").splitlines()
        row1 = json.loads(lines[1])
        row1["event"] = "TAMPERED"
        lines[1] = json.dumps(row1, separators=(",", ":"), ensure_ascii=False)
        self.legacy.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            migrate_mod.migrate(
                legacy_log=self.legacy,
                output_dir=self.outdir,
                canon_snapshot_id=VALID_CANON_ID,
                dry_run=True,
            )
        self.assertIn("row_hash mismatch", str(ctx.exception))

    def test_tolerates_legacy_prefix_rows(self) -> None:
        # Pre-DGAS rows have no row_hash. They MUST be tolerated at the
        # head of the file because the live cc_completion_log.jsonl has
        # exactly this shape.
        legacy_row = {"event": "old-pre-chain", "timestamp": "2025-01-01T00:00:00Z"}
        with self.legacy.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(legacy_row, separators=(",", ":")) + "\n")
        self._seed_legacy(2)
        result = migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            dry_run=True,
        )
        self.assertEqual(result["v1_summary"]["row_count"], 2)
        self.assertEqual(result["v1_summary"]["legacy_prefix_rows"], 1)


class TestRoundTripWithVerifier(unittest.TestCase):
    """Run the writer, then invoke verify_dgas_boundary.py via subprocess.

    The verifier is an independent Python script. The whole point of the
    boundary protocol is that the writer + verifier agree, so the round-
    trip is the canonical end-to-end test.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_roundtrip_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.legacy = self.tmp / "legacy.jsonl"
        self.outdir = self.tmp / "out"

    def _seed_legacy(self, n: int) -> list[str]:
        hashes = []
        for i in range(n):
            h = audit_chain.append_chained(self.legacy, {"event": f"row-{i}", "i": i})
            hashes.append(h)
        return hashes

    def test_writer_output_verifies(self) -> None:
        self._seed_legacy(4)
        migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            created_at_utc="2026-05-10T15:00:00Z",
        )
        manifest_path = self.outdir / migrate_mod.MANIFEST_NAME
        # frozen filename includes the timestamp; find it from manifest
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frozen_path = self.outdir / manifest["legacy_log_path"]
        v2_path = self.outdir / migrate_mod.V2_LOG_NAME

        verifier = REPO_ROOT / "tools" / "verify_dgas_boundary.py"
        result = subprocess.run(
            [
                sys.executable,
                str(verifier),
                "--legacy-log",
                str(frozen_path),
                "--manifest",
                str(manifest_path),
                "--new-log",
                str(v2_path),
                "--verbose",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"verifier failed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )

    def test_writer_then_v2_appends_then_verifier(self) -> None:
        # Real end-to-end: seed legacy, run writer, append a few v2 rows,
        # invoke the verifier. The verifier should accept the full chain.
        self._seed_legacy(3)
        migrate_mod.migrate(
            legacy_log=self.legacy,
            output_dir=self.outdir,
            canon_snapshot_id=VALID_CANON_ID,
            created_at_utc="2026-05-10T16:00:00Z",
        )
        manifest_path = self.outdir / migrate_mod.MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frozen_path = self.outdir / manifest["legacy_log_path"]
        v2_path = self.outdir / migrate_mod.V2_LOG_NAME

        # Append v2 rows
        audit_chain.append_v2_chained(
            v2_path,
            {"event": "v2-first", "ticket_id": "LOS-10"},
            anchor_prev_hash=manifest["terminal_hash"],
            anchor_block_index=manifest["new_chain_starts_at"],
        )
        audit_chain.append_v2_chained(v2_path, {"event": "v2-second"})
        audit_chain.append_v2_chained(v2_path, {"event": "v2-third"})

        verifier = REPO_ROOT / "tools" / "verify_dgas_boundary.py"
        result = subprocess.run(
            [
                sys.executable,
                str(verifier),
                "--legacy-log",
                str(frozen_path),
                "--manifest",
                str(manifest_path),
                "--new-log",
                str(v2_path),
                "--verbose",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"verifier failed:\nstdout={result.stdout}\nstderr={result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
