"""Tests for tools/hermes_apprentice.py (PRO-312).

Follows the boundary-crossing test pattern from the PRO-189 adopted lesson:
all tests load the module from disk via importlib so they catch import-time
errors and test the module as-delivered.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "hermes_apprentice.py"


def _import_module():
    spec = importlib.util.spec_from_file_location("hermes_apprentice_under_test", str(MODULE_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hermes_apprentice_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_RH_ROW_APPROVED = {
    "timestamp": "2026-05-01T10:00:00.000Z",
    "trace_id": "cc-PRO-100-aaa-bbb",
    "task_id": "uuid-100",
    "task_identifier": "PRO-100",
    "extracted_signals": {
        "task_type": "bug",
        "surface_keywords": ["crash", "regression"],
    },
    "ranked_candidates": [
        {"worker": "claude-code", "score": 0.9, "reasoning": "best for bugs"},
    ],
    "chosen_worker": "claude-code",
    "confidence": 0.9,
    "risk": "low",
    "operator_override_flag": False,
    "outcome": "success",
    "proposed_model_version": "claude-opus-4-7",
    "w2_workflow_version": "phase-1-v2",
}

_RH_ROW_OVERRIDE = {
    "timestamp": "2026-05-01T11:00:00.000Z",
    "trace_id": "cc-PRO-101-ccc-ddd",
    "task_id": "uuid-101",
    "task_identifier": "PRO-101",
    "extracted_signals": {
        "task_type": "feature",
        "surface_keywords": ["ui", "frontend"],
    },
    "ranked_candidates": [
        {"worker": "claude-code", "score": 0.7, "reasoning": "frontend work"},
    ],
    "chosen_worker": "claude-code",
    "confidence": 0.7,
    "risk": "medium",
    "operator_override_flag": True,
    "outcome": "success",
    "proposed_model_version": "claude-opus-4-7",
    "w2_workflow_version": "phase-1-v2",
}

_INTENT_PRO100 = {
    "kind": "intent",
    "token": "tok-100",
    "intent_written_at": "2026-05-01T10:00:01.000Z",
    "trace_id": "cc-PRO-100-aaa-bbb",
    "issue_id": "uuid-100",
    "issue_identifier": "PRO-100",
    "chosen_worker": "claude-code",
    "confidence": 0.9,
    "risk": "low",
}

_DECIDED_PRO100_APPROVE = {
    "kind": "decided",
    "token": "tok-100",
    "action": "a",
    "action_label": "Approve",
    "decided_at": "2026-05-01T10:01:00.000Z",
    "trace_id": "cc-PRO-100-aaa-bbb",
    "task_id": "uuid-100",
    "task_identifier": "PRO-100",
}

_INTENT_PRO101 = {
    "kind": "intent",
    "token": "tok-101",
    "intent_written_at": "2026-05-01T11:00:01.000Z",
    "trace_id": "cc-PRO-101-ccc-ddd",
    "issue_id": "uuid-101",
    "issue_identifier": "PRO-101",
    "chosen_worker": "claude-code",
    "confidence": 0.7,
    "risk": "medium",
}

_DECIDED_PRO101_OVERRIDE = {
    "kind": "decided",
    "token": "tok-101",
    "action": "u",
    "action_label": "Cursor",
    "decided_at": "2026-05-01T11:01:00.000Z",
    "trace_id": "cc-PRO-101-ccc-ddd",
    "task_id": "uuid-101",
    "task_identifier": "PRO-101",
}


def _write_jsonl(path: str, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# ---------------------------------------------------------------------------
# Module import test (boundary crossing)
# ---------------------------------------------------------------------------


class ModuleImportTest(unittest.TestCase):
    def test_module_loads_from_disk(self) -> None:
        mod = _import_module()
        self.assertTrue(hasattr(mod, "load_routing_history"))
        self.assertTrue(hasattr(mod, "load_pending_callbacks"))
        self.assertTrue(hasattr(mod, "build_case"))
        self.assertTrue(hasattr(mod, "run"))


# ---------------------------------------------------------------------------
# load_routing_history — deduplication
# ---------------------------------------------------------------------------


class LoadRoutingHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def test_deduplication_latest_wins(self) -> None:
        rows = [
            {**_RH_ROW_APPROVED, "timestamp": "2026-05-01T09:00:00.000Z", "confidence": 0.5},
            {**_RH_ROW_APPROVED, "timestamp": "2026-05-01T10:00:00.000Z", "confidence": 0.9},
        ]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            path = fh.name
        try:
            result = self.mod.load_routing_history(path)
            self.assertEqual(len(result), 1)
            self.assertEqual(result["PRO-100"]["confidence"], 0.9)
        finally:
            os.unlink(path)

    def test_rows_without_task_identifier_skipped(self) -> None:
        row = {**_RH_ROW_APPROVED}
        del row["task_identifier"]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(row) + "\n")
            path = fh.name
        try:
            result = self.mod.load_routing_history(path)
            self.assertEqual(len(result), 0)
        finally:
            os.unlink(path)

    def test_bad_json_lines_skipped(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as fh:
            fh.write("{bad json}\n")
            fh.write(json.dumps(_RH_ROW_APPROVED) + "\n")
            path = fh.name
        try:
            result = self.mod.load_routing_history(path)
            self.assertEqual(len(result), 1)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# load_pending_callbacks — intent/decided separation
# ---------------------------------------------------------------------------


class LoadPendingCallbacksTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def test_intent_indexed_by_issue_identifier(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(_INTENT_PRO100) + "\n")
            path = fh.name
        try:
            intent_map, decided_map = self.mod.load_pending_callbacks(path)
            self.assertIn("PRO-100", intent_map)
            self.assertEqual(len(intent_map["PRO-100"]), 1)
            self.assertEqual(decided_map, {})
        finally:
            os.unlink(path)

    def test_decided_indexed_by_task_identifier(self) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(_DECIDED_PRO100_APPROVE) + "\n")
            path = fh.name
        try:
            intent_map, decided_map = self.mod.load_pending_callbacks(path)
            self.assertIn("PRO-100", decided_map)
            self.assertEqual(decided_map["PRO-100"]["action"], "a")
        finally:
            os.unlink(path)

    def test_latest_decided_wins_per_ticket(self) -> None:
        early = {**_DECIDED_PRO100_APPROVE, "decided_at": "2026-05-01T10:00:00Z", "action": "a"}
        late = {**_DECIDED_PRO100_APPROVE, "decided_at": "2026-05-01T11:00:00Z", "action": "o"}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(early) + "\n")
            fh.write(json.dumps(late) + "\n")
            path = fh.name
        try:
            _, decided_map = self.mod.load_pending_callbacks(path)
            self.assertEqual(decided_map["PRO-100"]["action"], "o")
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# build_case — learning signal classification
# ---------------------------------------------------------------------------


class BuildCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def test_approved_case_confirmed_signal(self) -> None:
        case = self.mod.build_case(_RH_ROW_APPROVED, [_INTENT_PRO100], _DECIDED_PRO100_APPROVE)
        self.assertEqual(case["learning_signal"], "confirmed")
        self.assertFalse(case["delta"]["was_override"])
        self.assertEqual(case["delta"]["actual_worker"], "claude-code")
        self.assertEqual(case["ticket_id"], "PRO-100")

    def test_override_worker_button_signal(self) -> None:
        case = self.mod.build_case(_RH_ROW_OVERRIDE, [_INTENT_PRO101], _DECIDED_PRO101_OVERRIDE)
        self.assertEqual(case["learning_signal"], "override")
        self.assertTrue(case["delta"]["was_override"])
        self.assertEqual(case["delta"]["proposed_worker"], "claude-code")
        self.assertEqual(case["delta"]["actual_worker"], "cursor")

    def test_no_decision_when_decided_missing(self) -> None:
        case = self.mod.build_case(_RH_ROW_APPROVED, [], None)
        self.assertEqual(case["learning_signal"], "no_decision")
        self.assertIsNone(case["operator_decision"])
        self.assertIsNone(case["delta"]["actual_worker"])

    def test_triage_action_classified(self) -> None:
        decided_triage = {**_DECIDED_PRO100_APPROVE, "action": "t", "action_label": "Triage"}
        case = self.mod.build_case(_RH_ROW_APPROVED, [_INTENT_PRO100], decided_triage)
        self.assertEqual(case["learning_signal"], "triage")
        self.assertFalse(case["delta"]["was_override"])

    def test_generic_override_actual_worker_none(self) -> None:
        decided_o = {**_DECIDED_PRO100_APPROVE, "action": "o", "action_label": "Override"}
        case = self.mod.build_case(_RH_ROW_APPROVED, [_INTENT_PRO100], decided_o)
        self.assertEqual(case["learning_signal"], "override")
        self.assertIsNone(case["delta"]["actual_worker"])

    def test_case_id_deterministic(self) -> None:
        case1 = self.mod.build_case(_RH_ROW_APPROVED, [], None)
        case2 = self.mod.build_case(_RH_ROW_APPROVED, [], None)
        self.assertEqual(case1["case_id"], case2["case_id"])

    def test_case_id_differs_per_ticket(self) -> None:
        case1 = self.mod.build_case(_RH_ROW_APPROVED, [], None)
        case2 = self.mod.build_case(_RH_ROW_OVERRIDE, [], None)
        self.assertNotEqual(case1["case_id"], case2["case_id"])

    def test_operator_decision_structure(self) -> None:
        case = self.mod.build_case(_RH_ROW_APPROVED, [_INTENT_PRO100], _DECIDED_PRO100_APPROVE)
        od = case["operator_decision"]
        self.assertIsNotNone(od)
        self.assertIn("action", od)
        self.assertIn("action_label", od)
        self.assertIn("decided_at", od)
        self.assertIn("actual_worker", od)

    def test_routing_proposal_fields_present(self) -> None:
        case = self.mod.build_case(_RH_ROW_APPROVED, [], None)
        rp = case["routing_proposal"]
        for field in (
            "trace_id",
            "timestamp",
            "chosen_worker",
            "confidence",
            "risk",
            "task_type",
            "surface_keywords",
            "ranked_candidates",
            "proposed_model_version",
            "w2_workflow_version",
        ):
            self.assertIn(field, rp, f"Missing field: {field}")

    def test_ticket_description_defaults_null(self) -> None:
        case = self.mod.build_case(_RH_ROW_APPROVED, [], None)
        self.assertIsNone(case["ticket_description"])


# ---------------------------------------------------------------------------
# run() — integration over temp JSONL files
# ---------------------------------------------------------------------------


class RunIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _import_module()

    def _make_files(self, rh_rows: list, cb_rows: list) -> tuple[str, str]:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as rh_file:
            for r in rh_rows:
                rh_file.write(json.dumps(r) + "\n")
            rh_path = rh_file.name

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as cb_file:
            for r in cb_rows:
                cb_file.write(json.dumps(r) + "\n")
            cb_path = cb_file.name

        return rh_path, cb_path

    def test_dry_run_writes_to_stdout(self) -> None:
        rh_path, cb_path = self._make_files(
            [_RH_ROW_APPROVED],
            [_INTENT_PRO100, _DECIDED_PRO100_APPROVE],
        )
        try:
            with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as out:
                out_path = out.name

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                count = self.mod.run(
                    routing_history_path=rh_path,
                    callbacks_path=cb_path,
                    output_path=out_path,
                    dry_run=True,
                )

            self.assertEqual(count, 1)
            printed = buf.getvalue().strip()
            case = json.loads(printed)
            self.assertEqual(case["ticket_id"], "PRO-100")
            # dry_run must NOT write to file
            self.assertEqual(os.path.getsize(out_path), 0)
        finally:
            os.unlink(rh_path)
            os.unlink(cb_path)
            os.unlink(out_path)

    def test_appends_to_output_file(self) -> None:
        rh_path, cb_path = self._make_files(
            [_RH_ROW_APPROVED, _RH_ROW_OVERRIDE],
            [_INTENT_PRO100, _DECIDED_PRO100_APPROVE, _INTENT_PRO101, _DECIDED_PRO101_OVERRIDE],
        )
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
            ) as out:
                out_path = out.name

            count = self.mod.run(
                routing_history_path=rh_path,
                callbacks_path=cb_path,
                output_path=out_path,
            )
            self.assertEqual(count, 2)
            with open(out_path, encoding="utf-8") as fh:
                lines = [ln.strip() for ln in fh if ln.strip()]
            self.assertEqual(len(lines), 2)
            parsed = [json.loads(ln) for ln in lines]
            tickets = {c["ticket_id"] for c in parsed}
            self.assertEqual(tickets, {"PRO-100", "PRO-101"})
        finally:
            os.unlink(rh_path)
            os.unlink(cb_path)
            os.unlink(out_path)

    def test_overrides_only_filter(self) -> None:
        rh_path, cb_path = self._make_files(
            [_RH_ROW_APPROVED, _RH_ROW_OVERRIDE],
            [_INTENT_PRO100, _DECIDED_PRO100_APPROVE, _INTENT_PRO101, _DECIDED_PRO101_OVERRIDE],
        )
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
            ) as out:
                out_path = out.name

            count = self.mod.run(
                routing_history_path=rh_path,
                callbacks_path=cb_path,
                output_path=out_path,
                overrides_only=True,
                dry_run=True,
            )
            self.assertEqual(count, 1)
        finally:
            os.unlink(rh_path)
            os.unlink(cb_path)
            os.unlink(out_path)

    def test_since_filter_excludes_older_rows(self) -> None:
        rh_old = {**_RH_ROW_APPROVED, "timestamp": "2026-04-01T10:00:00.000Z"}
        rh_new = {**_RH_ROW_OVERRIDE, "timestamp": "2026-05-01T10:00:00.000Z"}
        rh_path, cb_path = self._make_files([rh_old, rh_new], [])
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
            ) as out:
                out_path = out.name

            count = self.mod.run(
                routing_history_path=rh_path,
                callbacks_path=cb_path,
                output_path=out_path,
                since="2026-05-01",
                dry_run=True,
            )
            self.assertEqual(count, 1)
        finally:
            os.unlink(rh_path)
            os.unlink(cb_path)
            os.unlink(out_path)

    def test_ticket_filter(self) -> None:
        rh_path, cb_path = self._make_files(
            [_RH_ROW_APPROVED, _RH_ROW_OVERRIDE],
            [],
        )
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
            ) as out:
                out_path = out.name

            count = self.mod.run(
                routing_history_path=rh_path,
                callbacks_path=cb_path,
                output_path=out_path,
                ticket_filter="PRO-101",
                dry_run=True,
            )
            self.assertEqual(count, 1)
        finally:
            os.unlink(rh_path)
            os.unlink(cb_path)
            os.unlink(out_path)

    def test_output_rows_are_valid_json(self) -> None:
        rh_path, cb_path = self._make_files(
            [_RH_ROW_APPROVED],
            [_INTENT_PRO100, _DECIDED_PRO100_APPROVE],
        )
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
            ) as out:
                out_path = out.name

            self.mod.run(
                routing_history_path=rh_path,
                callbacks_path=cb_path,
                output_path=out_path,
            )
            with open(out_path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        parsed = json.loads(line)
                        self.assertIsInstance(parsed, dict)
        finally:
            os.unlink(rh_path)
            os.unlink(cb_path)
            os.unlink(out_path)

    def test_empty_callbacks_produces_no_decision_cases(self) -> None:
        rh_path, cb_path = self._make_files([_RH_ROW_APPROVED], [])
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
            ) as out:
                out_path = out.name

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                count = self.mod.run(
                    routing_history_path=rh_path,
                    callbacks_path=cb_path,
                    output_path=out_path,
                    dry_run=True,
                )

            self.assertEqual(count, 1)
            case = json.loads(buf.getvalue().strip())
            self.assertEqual(case["learning_signal"], "no_decision")
        finally:
            os.unlink(rh_path)
            os.unlink(cb_path)
            os.unlink(out_path)


if __name__ == "__main__":
    unittest.main()
