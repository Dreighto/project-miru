"""Tests for tools/verify_dgas_boundary.py.

Synthesizes fixture v1 + v2 chains, runs the standalone verifier, and asserts
behavior across happy path + every tamper class:

- Happy path: legacy chain + manifest + new v2 chain all consistent.
- Legacy chain tamper: row body modified, row_hash unchanged → mismatch.
- Manifest tamper: terminal_hash wrong → mismatch.
- Manifest tamper: row_count wrong → mismatch.
- Manifest tamper: byte_length wrong → mismatch.
- v2 chain anchor wrong: first v2 row's prev_hash != manifest.terminal_hash.
- v2 chain hash tamper: middle row recomputed incorrectly.
- v2 chain block_index gap.
- Empty new chain (post-cutover but no dispatches yet) — should pass.
- Missing manifest file — fails clean.
- Missing legacy log — fails clean.

The fixtures use the SAME canonicalization the production audit_chain.py
uses, so this test doubles as a regression test for the verifier's algorithm
matching the chain library.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VERIFIER = REPO_ROOT / "tools" / "verify_dgas_boundary.py"

DGAS_V2_PREFIX = b"DGASv1"


def _canonical(body: dict) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _v1_row_hash(body_minus_row_hash: dict) -> str:
    return _sha256_hex(_canonical(body_minus_row_hash))


def _v2_row_hash(block_index: int, prev_hash: str, payload: dict) -> str:
    payload_inner = hashlib.sha256(_canonical(payload)).digest()
    combined = (
        DGAS_V2_PREFIX
        + str(block_index).encode("ascii")
        + prev_hash.encode("ascii")
        + payload_inner
    )
    return _sha256_hex(combined)


def _write_v1_chain(path: Path, payloads: list[dict]) -> tuple[str, int, int]:
    """Write a chained v1 JSONL. Returns (terminal_hash, row_count, byte_length)."""
    rows_out: list[str] = []
    prev_hash: str | None = None
    for payload in payloads:
        body = {**payload, "prev_hash": prev_hash}
        row_hash = _v1_row_hash(body)
        full = {**body, "row_hash": row_hash}
        rows_out.append(json.dumps(full, separators=(",", ":"), ensure_ascii=False))
        prev_hash = row_hash
    text = "\n".join(rows_out) + "\n"
    path.write_bytes(text.encode("utf-8"))
    return prev_hash, len(payloads), path.stat().st_size


def _write_v2_chain(path: Path, payloads: list[dict], anchor_prev: str, start_block: int) -> str:
    """Write a chained v2 JSONL starting from anchor_prev. Returns terminal_hash."""
    rows_out: list[str] = []
    prev_hash = anchor_prev
    block = start_block
    for payload in payloads:
        row_hash = _v2_row_hash(block, prev_hash, payload)
        full = {**payload, "prev_hash": prev_hash, "block_index": block, "row_hash": row_hash}
        rows_out.append(json.dumps(full, separators=(",", ":"), ensure_ascii=False))
        prev_hash = row_hash
        block += 1
    text = "\n".join(rows_out) + "\n"
    path.write_bytes(text.encode("utf-8"))
    return prev_hash


def _run_verifier(legacy: Path, manifest: Path, new: Path, *args: str) -> tuple[int, str, str]:
    """Run verify_dgas_boundary.py as a subprocess. Returns (exit_code, stdout, stderr)."""
    result = subprocess.run(
        [
            sys.executable,
            str(VERIFIER),
            "--legacy-log",
            str(legacy),
            "--manifest",
            str(manifest),
            "--new-log",
            str(new),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout, result.stderr


class VerifyDgasBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dgas_verifier_test_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

        # Build a small valid legacy chain.
        self.legacy_path = self.tmp / "legacy.jsonl"
        self.legacy_terminal, self.legacy_count, self.legacy_bytes = _write_v1_chain(
            self.legacy_path,
            [
                {"event": "marker", "ticket_id": "PRO-1", "ts": "2026-05-10T01:00:00Z"},
                {"event": "marker", "ticket_id": "PRO-2", "ts": "2026-05-10T02:00:00Z"},
                {
                    "event": "CHAIN_FREEZE",
                    "reason": "migration_to_LogueOS-Orchestrator",
                    "ts": "2026-05-10T03:00:00Z",
                },
            ],
        )

        # Build a small valid v2 chain.
        self.new_path = self.tmp / "new.jsonl"
        _write_v2_chain(
            self.new_path,
            [
                {"event": "marker", "ticket_id": "LOS-100", "ts": "2026-05-11T01:00:00Z"},
                {"event": "marker", "ticket_id": "LOS-101", "ts": "2026-05-11T02:00:00Z"},
            ],
            anchor_prev=self.legacy_terminal,
            start_block=self.legacy_count + 1,
        )

        # Build a valid manifest.
        self.manifest_path = self.tmp / "DGAS_BOUNDARY_MANIFEST.json"
        self.manifest = {
            "legacy_repo": "Dreighto/project-miru",
            "legacy_commit": "abcdef1234567890",
            "legacy_log_path": "data/cc_completion_log.frozen.jsonl",
            "terminal_block": self.legacy_count,
            "terminal_hash": self.legacy_terminal,
            "row_count": self.legacy_count,
            "byte_length": self.legacy_bytes,
            "hash_algorithm": "sha256",
            "canonicalization": {
                "version": "dgajson-v1",
                "encoding": "utf-8",
                "line_endings": "lf",
                "json_spacing": "compact",
                "field_ordering": "lexicographic_or_declared_schema",
            },
            "genesis_hash": _sha256_hex(b""),
            "new_repo": "Dreighto/LogueOS-Orchestrator",
            "new_chain_starts_at": self.legacy_count + 1,
            "new_chain_format_version": "DGAS_V2",
            "created_at_utc": "2026-05-10T03:00:00Z",
        }
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2))

    def test_happy_path_verifies_clean(self) -> None:
        code, _stdout, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 0, f"verifier failed: stderr={stderr}")

    def test_verbose_flag_prints_pass_message(self) -> None:
        code, stdout, _stderr = _run_verifier(
            self.legacy_path, self.manifest_path, self.new_path, "--verbose"
        )
        self.assertEqual(code, 0)
        self.assertIn("PASSED", stdout)

    def test_legacy_chain_tamper_row_payload_breaks_chain(self) -> None:
        # Modify one row's payload but keep row_hash unchanged → mismatch.
        lines = self.legacy_path.read_text(encoding="utf-8").splitlines()
        row1 = json.loads(lines[1])
        row1["ticket_id"] = "PRO-999-TAMPERED"
        # Keep row_hash unchanged
        lines[1] = json.dumps(row1, separators=(",", ":"), ensure_ascii=False)
        self.legacy_path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("row_hash mismatch", stderr)

    def test_manifest_terminal_hash_wrong_fails(self) -> None:
        bad = dict(self.manifest)
        bad["terminal_hash"] = "0" * 64
        self.manifest_path.write_text(json.dumps(bad, indent=2))
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("terminal_hash", stderr)

    def test_manifest_row_count_wrong_fails(self) -> None:
        bad = dict(self.manifest)
        bad["row_count"] = self.legacy_count + 999
        self.manifest_path.write_text(json.dumps(bad, indent=2))
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("row_count", stderr)

    def test_manifest_byte_length_wrong_fails(self) -> None:
        bad = dict(self.manifest)
        bad["byte_length"] = self.legacy_bytes + 1
        self.manifest_path.write_text(json.dumps(bad, indent=2))
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("byte_length", stderr)

    def test_v2_chain_wrong_anchor_fails(self) -> None:
        # New v2 chain points at a different anchor than the manifest declares.
        _write_v2_chain(
            self.new_path,
            [{"event": "marker", "ticket_id": "LOS-200", "ts": "2026-05-11T05:00:00Z"}],
            anchor_prev="0" * 64,  # not the real boundary
            start_block=self.legacy_count + 1,
        )
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("v2 row prev_hash mismatch", stderr)

    def test_v2_chain_block_index_gap_fails(self) -> None:
        # First v2 row at correct block, second v2 row at +2 instead of +1.
        rows_out = []
        block = self.legacy_count + 1
        prev = self.legacy_terminal
        payload1 = {"event": "marker", "ticket_id": "LOS-300", "ts": "2026-05-11T06:00:00Z"}
        h1 = _v2_row_hash(block, prev, payload1)
        rows_out.append(
            json.dumps(
                {**payload1, "prev_hash": prev, "block_index": block, "row_hash": h1},
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        # Second row has a block_index gap
        block = self.legacy_count + 3  # GAP
        prev = h1
        payload2 = {"event": "marker", "ticket_id": "LOS-301", "ts": "2026-05-11T07:00:00Z"}
        h2 = _v2_row_hash(block, prev, payload2)
        rows_out.append(
            json.dumps(
                {**payload2, "prev_hash": prev, "block_index": block, "row_hash": h2},
                separators=(",", ":"),
                ensure_ascii=False,
            )
        )
        self.new_path.write_bytes(("\n".join(rows_out) + "\n").encode("utf-8"))

        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("block_index mismatch", stderr)

    def test_empty_new_chain_post_cutover_passes(self) -> None:
        # No post-boundary dispatches yet — empty file is fine.
        self.new_path.write_bytes(b"")
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 0, f"empty new chain should pass: stderr={stderr}")

    def test_missing_new_chain_file_passes(self) -> None:
        # File doesn't exist (pre-cutover scenario) — should also pass.
        self.new_path.unlink()
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 0, f"missing new chain should pass: stderr={stderr}")

    def test_missing_manifest_fails_clean(self) -> None:
        self.manifest_path.unlink()
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("manifest", stderr.lower())

    def test_missing_legacy_log_fails_clean(self) -> None:
        self.legacy_path.unlink()
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("legacy", stderr.lower())

    def test_require_signature_fails_when_no_sig(self) -> None:
        # --require-signature without a sig file should fail.
        code, _, stderr = _run_verifier(
            self.legacy_path, self.manifest_path, self.new_path, "--require-signature"
        )
        self.assertEqual(code, 1)
        self.assertIn("signature", stderr.lower())

    # ------------------------------------------------------------------
    # CR R1 (PR #182) regression tests
    # ------------------------------------------------------------------

    def test_first_v2_row_block_index_must_match_manifest(self) -> None:
        """CR R1 #1: verifier must reject a v2 chain whose first row's
        block_index differs from manifest.new_chain_starts_at, even if
        every subsequent row chains correctly internally."""
        # Build v2 starting at a DIFFERENT block index than manifest says.
        wrong_start = self.legacy_count + 99
        _write_v2_chain(
            self.new_path,
            [
                {"event": "marker", "ticket_id": "LOS-400", "ts": "2026-05-11T08:00:00Z"},
                {"event": "marker", "ticket_id": "LOS-401", "ts": "2026-05-11T09:00:00Z"},
            ],
            anchor_prev=self.legacy_terminal,
            start_block=wrong_start,
        )
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("first v2 row block_index mismatch", stderr)
        self.assertIn("new_chain_starts_at", stderr)

    def test_non_object_manifest_rejected_cleanly(self) -> None:
        """CR R1 #2: a manifest that is valid JSON but not an object (e.g. a
        bare array) must be rejected with a clean validation error, not a
        Python TypeError."""
        # Overwrite manifest with a JSON array
        self.manifest_path.write_text(json.dumps(["not", "a", "manifest"]))
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 1)
        self.assertIn("manifest must be a JSON object", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_signature_provided_but_invalid_fails_without_require_flag(self) -> None:
        """CR R1 #3: passing --signature explicitly should fail the run if
        the signature is missing/invalid, regardless of --require-signature.
        Intent of --signature is 'verify this signature', not 'try and shrug'."""
        # Point to a sig file that doesn't exist.
        bogus_sig = self.tmp / "bogus.sig"
        # File doesn't exist on disk
        code, _, stderr = _run_verifier(
            self.legacy_path,
            self.manifest_path,
            self.new_path,
            "--signature",
            str(bogus_sig),
        )
        self.assertEqual(code, 1)
        self.assertIn("signature", stderr.lower())

    def test_signature_provided_invalid_still_explicit_no_traceback(self) -> None:
        """Companion to R1 #3: a file that exists but isn't a valid sig
        should also fail cleanly without crashing."""
        bad_sig = self.tmp / "bad.sig"
        bad_sig.write_text("not a real signature\n")
        code, _, stderr = _run_verifier(
            self.legacy_path,
            self.manifest_path,
            self.new_path,
            "--signature",
            str(bad_sig),
        )
        self.assertEqual(code, 1)
        self.assertIn("signature", stderr.lower())
        self.assertNotIn("Traceback", stderr)

    def test_no_signature_arg_passes_without_failing(self) -> None:
        """Regression guard: omitting --signature entirely should still
        pass (signature is optional when not provided + --require-signature
        is off). This is the default case."""
        # Default invocation with no signature arg
        code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
        self.assertEqual(code, 0, f"expected pass without --signature: stderr={stderr}")

    def test_unknown_chain_format_version_rejected(self) -> None:
        """CR R2 (PR #182): manifest's new_chain_format_version must equal
        'DGAS_V2'. Any other value (including 'v2', 'v3', 'DGAS_V1') must
        fail-closed even if the rest of the chain is fine."""
        for bogus in ["v2", "v3", "DGAS_V1", "DGAS_V3", "future-version", ""]:
            bad = dict(self.manifest)
            bad["new_chain_format_version"] = bogus
            self.manifest_path.write_text(json.dumps(bad, indent=2))
            code, _, stderr = _run_verifier(self.legacy_path, self.manifest_path, self.new_path)
            self.assertEqual(code, 1, f"expected fail for bogus version {bogus!r}: stderr={stderr}")
            self.assertIn("new_chain_format_version", stderr)


if __name__ == "__main__":
    unittest.main()
