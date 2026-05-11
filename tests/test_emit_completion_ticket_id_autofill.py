"""Regression test for PRO-285: tools/emit_completion.py must auto-fill
`ticket_id` from MIRU_TRACE_ID when the marker submits null/missing.

Failure mode without this fix: workers occasionally write completion markers
with `ticket_id: null` even when the dispatch trace_id encodes the ticket.
The Linear ticket then sits in In Progress forever because the daily drift
scanner can't link a null-id marker back to its issue (PRO-276 and PRO-278
both hit this on 2026-05-02 evening; PRO-289 / PRO-285 ship the structural
fix together).

The fix uses MIRU_TRACE_ID (set by dispatch_listener spawn.js, see
`services/dispatch_listener/src/spawn.js` line where `childEnv.MIRU_TRACE_ID =
traceId;`) as the inference source. Trace ids carry the ticket id by
construction, e.g. `cc-PRO-276-eaa0a242-326360d3`.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
EMIT_PATH = REPO_ROOT / "tools" / "emit_completion.py"


def _import_emit_completion():
    spec = importlib.util.spec_from_file_location("emit_completion_under_test", str(EMIT_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules["emit_completion_under_test"] = module
    spec.loader.exec_module(module)
    return module


class TraceIdParseTests(unittest.TestCase):
    """Direct tests of `_ticket_id_from_trace`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _import_emit_completion()
        cls.fn = staticmethod(cls.module._ticket_id_from_trace)

    def test_standard_cc_format(self) -> None:
        # Format used by dispatch listener spawn.js: <worker>-<ticket>-<uuid>-<uuid>
        self.assertEqual(self.fn("cc-PRO-276-eaa0a242-326360d3"), "PRO-276")

    def test_codex_worker(self) -> None:
        self.assertEqual(self.fn("codex-PRO-200-abc123-def456"), "PRO-200")

    def test_gemini_worker(self) -> None:
        self.assertEqual(self.fn("gemini-PRO-150-xyz789-uvw012"), "PRO-150")

    def test_short_trace_id_no_uuid_suffix(self) -> None:
        self.assertEqual(self.fn("cc-PRO-276"), "PRO-276")

    def test_multi_digit_ticket(self) -> None:
        self.assertEqual(self.fn("cc-PRO-12345-uuid"), "PRO-12345")

    def test_team_other_than_pro(self) -> None:
        # Regex matches any uppercase team prefix, not just PRO. Future-proofs
        # against new Linear teams (e.g. OPS-NN, INF-NN).
        self.assertEqual(self.fn("cc-OPS-42-uuid-uuid"), "OPS-42")

    def test_empty_trace(self) -> None:
        self.assertIsNone(self.fn(""))

    def test_none_trace(self) -> None:
        self.assertIsNone(self.fn(None))

    def test_no_ticket_in_trace(self) -> None:
        # Trace without a ticket id - return None, do not raise.
        self.assertIsNone(self.fn("cc-just-some-uuid-string"))

    def test_lowercase_team_rejected(self) -> None:
        # Linear identifiers are uppercase. Lowercase must not match.
        self.assertIsNone(self.fn("cc-pro-276-uuid"))

    def test_no_digits_rejected(self) -> None:
        # No numeric ticket suffix - not a valid Linear identifier.
        self.assertIsNone(self.fn("cc-PROJECT-uuid"))


class MainAutofillTests(unittest.TestCase):
    """End-to-end: call main() with controlled env + stdin + temp log path,
    confirm the appended marker has ticket_id auto-filled (or preserved) per
    spec."""

    def setUp(self) -> None:
        self.module = _import_emit_completion()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_root = Path(self._tmp.name)
        (self.tmp_root / "data").mkdir()

    def _run_main(self, marker_dict, env_overrides=None, exclude_keys=()):
        marker_json = json.dumps(marker_dict)
        log_path = self.tmp_root / "data" / "cc_completion_log.jsonl"

        test_env = {k: v for k, v in os.environ.items() if k not in exclude_keys}
        if env_overrides:
            test_env.update(env_overrides)

        original_stdin = sys.stdin
        try:
            with (
                patch.object(self.module, "_repo_root", return_value=str(self.tmp_root)),
                patch.dict(os.environ, test_env, clear=True),
            ):
                sys.stdin = io.StringIO(marker_json)
                self.module.main()
        finally:
            sys.stdin = original_stdin

        text = log_path.read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line.strip()]
        return json.loads(lines[-1])

    def test_null_ticket_id_filled_from_trace_env(self) -> None:
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "test",
        }
        result = self._run_main(marker, env_overrides={"MIRU_TRACE_ID": "cc-PRO-276-aa-bb"})
        self.assertEqual(result["ticket_id"], "PRO-276")
        # trace_id is also captured (existing behaviour preserved).
        self.assertEqual(result["trace_id"], "cc-PRO-276-aa-bb")

    def test_missing_ticket_id_field_filled_from_trace_env(self) -> None:
        # Marker with ticket_id field entirely absent — same auto-fill must fire.
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "status": "CONFIRMED_WORKING",
            "summary": "test",
        }
        result = self._run_main(marker, env_overrides={"MIRU_TRACE_ID": "cc-PRO-276-aa-bb"})
        self.assertEqual(result["ticket_id"], "PRO-276")

    def test_existing_ticket_id_not_overwritten(self) -> None:
        """If the marker already carries a ticket_id, env trace must not replace
        it. Auto-fill is a fallback, not an override."""
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": "PRO-100",
            "status": "CONFIRMED_WORKING",
            "summary": "test",
        }
        result = self._run_main(marker, env_overrides={"MIRU_TRACE_ID": "cc-PRO-276-aa-bb"})
        self.assertEqual(result["ticket_id"], "PRO-100")
        # trace_id is still populated (it has its own value as a correlation tag).
        self.assertEqual(result["trace_id"], "cc-PRO-276-aa-bb")

    def test_existing_trace_id_in_marker_not_overwritten(self) -> None:
        """If the marker explicitly supplied a trace_id, env value must not
        clobber it."""
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "test",
            "trace_id": "operator-supplied-trace",
        }
        result = self._run_main(marker, env_overrides={"MIRU_TRACE_ID": "cc-PRO-276-aa-bb"})
        # trace_id is preserved.
        self.assertEqual(result["trace_id"], "operator-supplied-trace")
        # ticket_id is still auto-filled from MIRU_TRACE_ID since the marker
        # itself didn't supply one.
        self.assertEqual(result["ticket_id"], "PRO-276")

    def test_no_env_trace_no_change(self) -> None:
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "test",
        }
        result = self._run_main(marker, exclude_keys=("MIRU_TRACE_ID",))
        self.assertIsNone(result["ticket_id"])
        self.assertNotIn("trace_id", result)

    def test_non_inferable_trace_keeps_ticket_null(self) -> None:
        """Trace without a parseable ticket id leaves ticket_id null but still
        captures trace_id for correlation."""
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "test",
        }
        result = self._run_main(marker, env_overrides={"MIRU_TRACE_ID": "no-ticket-here"})
        self.assertIsNone(result["ticket_id"])
        self.assertEqual(result["trace_id"], "no-ticket-here")

    # ------------------------------------------------------------------
    # CR R2 (PR #181): canon_snapshot_id validation tests
    # ------------------------------------------------------------------

    def test_valid_env_canon_snapshot_id_is_authoritative(self) -> None:
        """Env LOGUEOS_CANON_SNAPSHOT_ID with valid format wins over marker."""
        marker = {
            "timestamp": "2026-05-10T15:00:00Z",
            "ticket_id": "LOS-10",
            "status": "CONFIRMED_WORKING",
            "summary": "test",
            "canon_snapshot_id": "f" * 64,  # marker says one thing
        }
        result = self._run_main(
            marker,
            env_overrides={"LOGUEOS_CANON_SNAPSHOT_ID": "a" * 64},  # env says another
        )
        # Env value wins (it's authoritative).
        self.assertEqual(result["canon_snapshot_id"], "a" * 64)

    def test_malformed_env_canon_snapshot_id_is_rejected(self) -> None:
        """CR R2: malformed env value must NOT be written onto the marker.
        Marker's existing canon_snapshot_id (if any) is preserved instead."""
        marker = {
            "timestamp": "2026-05-10T15:00:00Z",
            "ticket_id": "LOS-10",
            "status": "CONFIRMED_WORKING",
            "summary": "test",
            "canon_snapshot_id": "f" * 64,
        }
        # env_canon is malformed (too short, non-hex chars).
        result = self._run_main(
            marker, env_overrides={"LOGUEOS_CANON_SNAPSHOT_ID": "bad-id-not-hex"}
        )
        # Marker's value is preserved; malformed env didn't clobber it.
        self.assertEqual(result["canon_snapshot_id"], "f" * 64)

    def test_malformed_env_canon_snapshot_id_no_marker_value_leaves_none(self) -> None:
        """If env is malformed AND marker doesn't carry one, the field stays unset."""
        marker = {
            "timestamp": "2026-05-10T15:00:00Z",
            "ticket_id": "LOS-10",
            "status": "CONFIRMED_WORKING",
            "summary": "test",
        }
        result = self._run_main(
            marker, env_overrides={"LOGUEOS_CANON_SNAPSHOT_ID": "uppercase-fail-ABCDEF"}
        )
        # No canon_snapshot_id was set (marker didn't have one, env was rejected).
        self.assertNotIn("canon_snapshot_id", result)

    def test_no_env_canon_snapshot_id_marker_value_preserved(self) -> None:
        """No env → marker's canon_snapshot_id passes through unchanged."""
        marker = {
            "timestamp": "2026-05-10T15:00:00Z",
            "ticket_id": "LOS-10",
            "status": "CONFIRMED_WORKING",
            "summary": "test",
            "canon_snapshot_id": "b" * 64,
        }
        result = self._run_main(marker, exclude_keys=("LOGUEOS_CANON_SNAPSHOT_ID",))
        self.assertEqual(result["canon_snapshot_id"], "b" * 64)

    def test_canon_snapshot_id_format_validator_table(self) -> None:
        """Direct unit test on the validator helper. Cheaper than e2e."""
        module = _import_emit_completion()
        valid_cases = [
            "a" * 64,
            "0" * 64,
            "9" * 64,
            "abcdef0123456789" * 4,
        ]
        invalid_cases = [
            "",
            "a" * 63,
            "a" * 65,
            "A" * 64,  # uppercase
            "g" * 64,  # non-hex
            "bad-id",
            "12345",
        ]
        for ok in valid_cases:
            self.assertTrue(module._is_valid_canon_snapshot_id(ok), f"expected valid: {ok!r}")
        for bad in invalid_cases:
            self.assertFalse(module._is_valid_canon_snapshot_id(bad), f"expected invalid: {bad!r}")


if __name__ == "__main__":
    unittest.main()
