"""DGAS Tier 2 #10: meta-test enforcing fault-injection coverage per gate.

A governance gate is only real if a test deliberately tries to do the bad
thing the gate prevents AND the gate stops it. Without that, the gate is
theatre — see synthesis item #7. This file is the registry of "every known
gate -> the test file proving it works." Adding a new gate without a
matching fault-injection test makes this meta-test fail.

Two layers of enforcement:
    1. The ``_GATE_FAULT_INJECTION_TESTS`` registry below maps every gate
       in ``tools/emit_governance_metric._GATE_REGISTRY`` to at least one
       test file that fault-injects against it. Test files must exist on
       disk and contain at least one assertion that calls into the gate's
       deny path.
    2. Cross-check: every gate registered in the metric writer has an entry
       here, and every entry here points at a real test.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
TESTS_DIR = REPO_ROOT / "tests"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from emit_governance_metric import _GATE_REGISTRY, VALID_OUTCOMES, emit  # noqa: E402

# Gate-id -> (test_file, must-contain-substring). The substring check is a
# cheap proof-of-life: the test file must mention the gate's specific
# failure path so a renamed-but-empty test file doesn't pass this meta-test.
_GATE_FAULT_INJECTION_TESTS: dict[str, tuple[str, str]] = {
    "secret_scanner": (
        "tests/test_secret_scanner_smoke.py",
        # Planted secret + assertion that gitleaks fires
        "test_planted_aws_key_triggers_gate",
    ),
    "db_write_deny": (
        "tests/test_db_write_protections.py",
        # Bare DROP TABLE must be rejected
        "test_rejects_drop_table",
    ),
    "audit_chain": (
        "tests/test_audit_chain.py",
        # Tampering with a row breaks validation
        "test_modifying_row_hash_without_body_breaks_chain",
    ),
    "safe_git_push": (
        "tests/test_safe_git_push.py",
        # Force-push to protected branch must be refused
        "force_push",
    ),
    "governance_change": (
        "tests/test_governance_change.py",
        # Touching a registry file without the opt-in token must fail
        "test_governance_change_without_token_fails",
    ),
    "audit_anchor": (
        "tests/test_emit_audit_anchor.py",
        # Tampering with a legacy row breaks file_sha256 in subsequent anchors
        "test_post_anchor_tamper_breaks_file_sha256",
    ),
    "localhost_bind": (
        "tests/test_full_operator_localhost_bind.py",
        # full_operator from non-local IPv4 must be rejected with 403
        "test_full_operator_header_from_remote_ipv4_rejected",
    ),
}


class TestGateFaultInjectionRegistry(unittest.TestCase):
    """The single source of truth that ties gates to their fault-injection tests."""

    def test_every_registered_gate_has_a_fault_injection_test(self) -> None:
        for gate in _GATE_REGISTRY:
            self.assertIn(
                gate,
                _GATE_FAULT_INJECTION_TESTS,
                f"Gate {gate!r} is registered in tools/emit_governance_metric.py "
                f"but has no fault-injection test mapped here. Add one to "
                f"tests/test_governance_gates._GATE_FAULT_INJECTION_TESTS or "
                f"the gate is theatre.",
            )

    def test_every_mapped_test_file_exists_on_disk(self) -> None:
        for gate, (rel_path, _marker) in _GATE_FAULT_INJECTION_TESTS.items():
            full = REPO_ROOT / rel_path
            self.assertTrue(
                full.exists(),
                f"Gate {gate!r} maps to {rel_path} which doesn't exist. "
                f"Either add the file or update the registry.",
            )

    def test_every_mapped_test_contains_its_marker(self) -> None:
        for gate, (rel_path, marker) in _GATE_FAULT_INJECTION_TESTS.items():
            full = REPO_ROOT / rel_path
            content = full.read_text(encoding="utf-8")
            self.assertIn(
                marker,
                content,
                f"Gate {gate!r}: test file {rel_path} no longer contains "
                f"the marker {marker!r}. The fault-injection assertion was "
                f"renamed or deleted; pick a new marker that proves the gate "
                f"still has deliberate fault-injection coverage.",
            )

    def test_no_orphan_mappings(self) -> None:
        """A test mapping for a non-registered gate is dead weight; either
        register the gate or drop the mapping."""
        for gate in _GATE_FAULT_INJECTION_TESTS:
            self.assertIn(
                gate,
                _GATE_REGISTRY,
                f"Gate {gate!r} has a fault-injection test mapped but isn't "
                f"in tools/emit_governance_metric._GATE_REGISTRY. Add it to "
                f"the registry so production-side metric writes can reference it.",
            )


class TestEmitGovernanceMetric(unittest.TestCase):
    """The metric writer must reject unknown gates, reject unknown outcomes,
    and chain every accepted row."""

    def setUp(self) -> None:
        import shutil
        import tempfile

        self.tmp = Path(tempfile.mkdtemp(prefix="miru_govmetric_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log_path = self.tmp / "data" / "governance_metrics.jsonl"

    def test_unknown_gate_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            emit(gate="totally_made_up_gate", outcome="fired", log_path=self.log_path)
        self.assertIn("unknown gate", str(ctx.exception))

    def test_unknown_outcome_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            emit(gate="audit_chain", outcome="exploded", log_path=self.log_path)
        self.assertIn("invalid outcome", str(ctx.exception))

    def test_valid_emit_appends_chained_row(self) -> None:
        import json

        emit(
            gate="safe_git_push",
            outcome="blocked",
            actor="cc",
            subject="refs/heads/main",
            log_path=self.log_path,
        )
        emit(
            gate="audit_chain",
            outcome="passed",
            actor="ci",
            subject="data/cc_completion_log.jsonl",
            log_path=self.log_path,
        )

        from audit_chain import validate_chain

        result = validate_chain(self.log_path)
        self.assertTrue(result.ok, f"chain failed: {result.error}")
        self.assertEqual(result.chained_rows, 2)

        rows = [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(rows[0]["gate"], "safe_git_push")
        self.assertEqual(rows[0]["outcome"], "blocked")
        self.assertEqual(rows[1]["gate"], "audit_chain")

    def test_oversized_context_is_truncated(self) -> None:
        """A rogue caller can't blow up the metrics file with a 1MB context blob."""
        import json

        big_context = {"payload": "x" * (10 * 1024)}  # 10 KiB exceeds the 4 KiB cap
        emit(
            gate="db_write_deny",
            outcome="blocked",
            context=big_context,
            log_path=self.log_path,
        )
        row = json.loads(self.log_path.read_text(encoding="utf-8").splitlines()[0])
        self.assertTrue(row["context"]["_truncated"], "oversized context must be replaced")
        self.assertGreater(row["context"]["_original_byte_len"], 4 * 1024)

    def test_outcome_enum_is_closed(self) -> None:
        """The rollup math depends on exactly 4 outcome values. If this test
        fails, also update the rollup formula in any consumer that reads
        governance_metrics.jsonl."""
        self.assertEqual(VALID_OUTCOMES, {"fired", "blocked", "passed", "bypass_attempted"})


if __name__ == "__main__":
    unittest.main()
