"""CLI behavior tests for tools/verify_audit_chain.py."""

from __future__ import annotations

import contextlib
import io
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

import audit_chain  # noqa: E402
import verify_audit_chain  # noqa: E402


class TestVerifyAuditChainCli(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="miru_verify_audit_chain_cli_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "data").mkdir()

    def _run(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["verify_audit_chain.py", *args]),
            mock.patch.object(verify_audit_chain, "_repo_root", return_value=self.tmp),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                code = verify_audit_chain.main()
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, stdout.getvalue(), stderr.getvalue()

    def _write_legacy(self, rel: str) -> Path:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"event":"legacy"}\n{"event":"older"}\n', encoding="utf-8")
        return path

    def _write_chained(self, rel: str, count: int = 2) -> Path:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        for idx in range(count):
            audit_chain.append_chained(path, {"event": f"row-{idx}"})
        return path

    def test_ok_target_exits_zero(self) -> None:
        self._write_chained("data/ok.jsonl")

        code, stdout, stderr = self._run("--files", "data/ok.jsonl")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Overall: OK", stdout)

    def test_first_chained_row_break_exits_one(self) -> None:
        path = self.tmp / "data" / "broken-head.jsonl"
        body = {"event": "bad-head", "prev_hash": "0" * 64}
        body["row_hash"] = "1" * 64
        path.write_text(json.dumps(body, separators=(",", ":")) + "\n", encoding="utf-8")

        code, stdout, stderr = self._run("--files", "data/broken-head.jsonl")

        self.assertEqual(code, 1, stderr)
        self.assertIn("Overall: BROKEN", stdout)

    def test_mid_file_chained_break_exits_one_with_warning(self) -> None:
        path = self._write_chained("data/broken-mid.jsonl", count=3)
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[1]["event"] = "tampered"
        path.write_text(
            "\n".join(json.dumps(row, separators=(",", ":")) for row in rows) + "\n",
            encoding="utf-8",
        )

        code, stdout, stderr = self._run("--files", "data/broken-mid.jsonl")

        self.assertEqual(code, 1, stderr)
        self.assertIn("Overall: BROKEN", stdout)
        self.assertIn("WARNING: at least one chained row failed verification.", stdout)

    def test_legacy_only_default_zero_strict_one(self) -> None:
        self._write_legacy("data/legacy.jsonl")

        code_default, stdout_default, _ = self._run("--files", "data/legacy.jsonl")
        code_strict, stdout_strict, _ = self._run("--strict", "--files", "data/legacy.jsonl")

        self.assertEqual(code_default, 0)
        self.assertIn("Overall: OK", stdout_default)
        self.assertEqual(code_strict, 1)
        self.assertIn("Overall: BROKEN", stdout_strict)

    def test_missing_default_zero_strict_one(self) -> None:
        code_default, stdout_default, _ = self._run("--files", "data/missing.jsonl")
        code_strict, stdout_strict, _ = self._run("--strict", "--files", "data/missing.jsonl")

        self.assertEqual(code_default, 0)
        self.assertIn("Overall: OK", stdout_default)
        self.assertEqual(code_strict, 1)
        self.assertIn("Overall: BROKEN", stdout_strict)

    def test_files_without_values_is_argparse_error(self) -> None:
        code, _stdout, stderr = self._run("--files")

        self.assertEqual(code, 2)
        self.assertIn("usage:", stderr)
        self.assertIn("expected at least one argument", stderr)

    def test_explicit_file_does_not_fall_back_to_audit_files(self) -> None:
        self._write_chained("data/only-this.jsonl")
        self._write_legacy("data/cc_completion_log.jsonl")

        code, stdout, stderr = self._run("--strict", "--files", "data/only-this.jsonl")
        normalized_stdout = stdout.replace("\\", "/")

        self.assertEqual(code, 0, stderr)
        self.assertIn("Audit chain verification — 1 file(s)", stdout)
        self.assertIn("data/only-this.jsonl", normalized_stdout)
        self.assertNotIn("data/cc_completion_log.jsonl", normalized_stdout)


if __name__ == "__main__":
    unittest.main()
