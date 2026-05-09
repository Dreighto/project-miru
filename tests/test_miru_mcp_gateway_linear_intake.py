"""Tests for PRO-331: Linear label + state intake tools on linear_create_issue."""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import miru_readonly_filesystem_mcp as stdio_mcp
from miru_mcp_gateway import linear_write_tools as lw


def _make_cfg(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        linear_api_key="fake-key",
        linear_team_id="team-uuid-1234",
        fs_root=root,
    )


def _issue_create_response(
    identifier: str = "PRO-99",
    state_name: str = "Backlog",
    label_names: list[str] | None = None,
) -> dict:
    return {
        "issueCreate": {
            "success": True,
            "issue": {
                "id": f"iss-{identifier}",
                "identifier": identifier,
                "title": "t",
                "url": f"https://linear.app/{identifier}",
                "state": {"name": state_name},
                "labels": {"nodes": [{"name": n} for n in (label_names or [])]},
            },
        }
    }


class LinearIntakeToolsTests(unittest.TestCase):
    HARNESS = Path(__file__).resolve().parent / "_tmp"

    def setUp(self) -> None:
        self.HARNESS.mkdir(parents=True, exist_ok=True)
        self.root = self.HARNESS / f"linear_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        lw._CFG = _make_cfg(self.root)

    # ------------------------------------------------------------------
    # label_names resolution
    # ------------------------------------------------------------------

    def test_create_issue_with_label_names_resolves_and_passes_label_ids(self) -> None:
        """label_names are resolved to IDs and sent as labelIds in create input."""
        captured_inp: dict = {}

        def fake_gql(query: str, variables: dict) -> dict:
            if "labels" in query and "issueCreate" not in query:
                return {
                    "team": {
                        "labels": {
                            "nodes": [
                                {"id": "lbl-bug", "name": "Bug"},
                                {"id": "lbl-feat", "name": "Feature"},
                            ]
                        }
                    }
                }
            if "issueCreate" in query:
                captured_inp.update(variables.get("input", {}))
                return _issue_create_response(label_names=["Bug"])
            return {}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            out = lw.linear_create_issue("t", project_id="proj-1", label_names=["Bug"])

        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["labels"], ["Bug"])
        self.assertEqual(captured_inp.get("labelIds"), ["lbl-bug"])

    def test_create_issue_with_multiple_label_names(self) -> None:
        """Multiple label names all resolve and are passed as labelIds."""
        captured_inp: dict = {}

        def fake_gql(query: str, variables: dict) -> dict:
            if "labels" in query and "issueCreate" not in query:
                return {
                    "team": {
                        "labels": {
                            "nodes": [
                                {"id": "lbl-1", "name": "Bug"},
                                {"id": "lbl-2", "name": "High Priority"},
                            ]
                        }
                    }
                }
            if "issueCreate" in query:
                captured_inp.update(variables.get("input", {}))
                return _issue_create_response(label_names=["Bug", "High Priority"])
            return {}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            out = lw.linear_create_issue(
                "t", project_id="proj-1", label_names=["Bug", "High Priority"]
            )

        payload = json.loads(out)
        self.assertEqual(sorted(payload["labels"]), ["Bug", "High Priority"])
        self.assertEqual(sorted(captured_inp.get("labelIds", [])), ["lbl-1", "lbl-2"])

    def test_create_issue_unknown_label_raises_mcp_error_with_available(self) -> None:
        """Unknown label name raises McpError and lists available labels."""

        def fake_gql(query: str, variables: dict) -> dict:
            if "labels" in query:
                return {"team": {"labels": {"nodes": [{"id": "lbl-1", "name": "Bug"}]}}}
            return {}

        with (
            patch.object(lw, "_linear_gql", side_effect=fake_gql),
            self.assertRaises(stdio_mcp.McpError) as ctx,
        ):
            lw.linear_create_issue("t", project_id="proj-1", label_names=["Nonexistent"])
        err = str(ctx.exception)
        self.assertIn("Nonexistent", err)
        self.assertIn("Bug", err)

    def test_create_issue_label_names_case_insensitive(self) -> None:
        """Label name matching is case-insensitive."""
        captured_inp: dict = {}

        def fake_gql(query: str, variables: dict) -> dict:
            if "labels" in query and "issueCreate" not in query:
                return {"team": {"labels": {"nodes": [{"id": "lbl-1", "name": "Bug"}]}}}
            if "issueCreate" in query:
                captured_inp.update(variables.get("input", {}))
                return _issue_create_response(label_names=["Bug"])
            return {}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            lw.linear_create_issue("t", project_id="proj-1", label_names=["bug"])

        self.assertEqual(captured_inp.get("labelIds"), ["lbl-1"])

    # ------------------------------------------------------------------
    # initial_state resolution
    # ------------------------------------------------------------------

    def test_create_issue_with_initial_state_sets_state_id_atomically(self) -> None:
        """initial_state is resolved to stateId and passed in the create input."""
        captured_inp: dict = {}

        def fake_gql(query: str, variables: dict) -> dict:
            if "states" in query and "issueCreate" not in query:
                return {
                    "team": {
                        "states": {
                            "nodes": [
                                {"id": "st-1", "name": "In Progress", "type": "started"},
                                {"id": "st-2", "name": "Backlog", "type": "backlog"},
                            ]
                        }
                    }
                }
            if "issueCreate" in query:
                captured_inp.update(variables.get("input", {}))
                return _issue_create_response(state_name="In Progress")
            return {}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            out = lw.linear_create_issue("t", project_id="proj-1", initial_state="In Progress")

        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "In Progress")
        self.assertEqual(captured_inp.get("stateId"), "st-1")

    def test_create_issue_unknown_state_raises_mcp_error_with_available(self) -> None:
        """Unknown state name raises McpError and lists available states."""

        def fake_gql(query: str, variables: dict) -> dict:
            if "states" in query:
                return {
                    "team": {
                        "states": {
                            "nodes": [
                                {"id": "st-1", "name": "In Progress", "type": "started"},
                            ]
                        }
                    }
                }
            return {}

        with (
            patch.object(lw, "_linear_gql", side_effect=fake_gql),
            self.assertRaises(stdio_mcp.McpError) as ctx,
        ):
            lw.linear_create_issue("t", project_id="proj-1", initial_state="Nonexistent")
        err = str(ctx.exception)
        self.assertIn("Nonexistent", err)
        self.assertIn("In Progress", err)

    def test_create_issue_initial_state_case_insensitive(self) -> None:
        """initial_state matching is case-insensitive."""
        captured_inp: dict = {}

        def fake_gql(query: str, variables: dict) -> dict:
            if "states" in query and "issueCreate" not in query:
                return {
                    "team": {
                        "states": {
                            "nodes": [
                                {"id": "st-1", "name": "In Progress", "type": "started"},
                            ]
                        }
                    }
                }
            if "issueCreate" in query:
                captured_inp.update(variables.get("input", {}))
                return _issue_create_response(state_name="In Progress")
            return {}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            lw.linear_create_issue("t", project_id="proj-1", initial_state="in progress")

        self.assertEqual(captured_inp.get("stateId"), "st-1")

    # ------------------------------------------------------------------
    # label_names + initial_state together
    # ------------------------------------------------------------------

    def test_create_issue_with_both_label_and_state(self) -> None:
        """label_names and initial_state can be combined in one call."""
        captured_inp: dict = {}

        def fake_gql(query: str, variables: dict) -> dict:
            if "labels" in query and "states" not in query and "issueCreate" not in query:
                return {"team": {"labels": {"nodes": [{"id": "lbl-1", "name": "Bug"}]}}}
            if "states" in query and "labels" not in query and "issueCreate" not in query:
                return {
                    "team": {
                        "states": {
                            "nodes": [
                                {"id": "st-1", "name": "In Progress", "type": "started"},
                            ]
                        }
                    }
                }
            if "issueCreate" in query:
                captured_inp.update(variables.get("input", {}))
                return _issue_create_response(state_name="In Progress", label_names=["Bug"])
            return {}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            out = lw.linear_create_issue(
                "t",
                project_id="proj-1",
                label_names=["Bug"],
                initial_state="In Progress",
            )

        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["state"], "In Progress")
        self.assertEqual(payload["labels"], ["Bug"])
        self.assertEqual(captured_inp.get("labelIds"), ["lbl-1"])
        self.assertEqual(captured_inp.get("stateId"), "st-1")

    # ------------------------------------------------------------------
    # Backward compatibility
    # ------------------------------------------------------------------

    def test_create_issue_no_new_params_no_label_ids_or_state_id(self) -> None:
        """Existing callers passing no new params get identical behavior."""
        captured_inp: dict = {}

        def fake_gql(query: str, variables: dict) -> dict:
            if "issueCreate" in query:
                captured_inp.update(variables.get("input", {}))
                return _issue_create_response()
            return {}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            out = lw.linear_create_issue("t", project_id="proj-1")

        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertNotIn("labelIds", captured_inp)
        self.assertNotIn("stateId", captured_inp)
        self.assertEqual(payload.get("labels"), [])

    # ------------------------------------------------------------------
    # linear_list_labels
    # ------------------------------------------------------------------

    def test_list_labels_returns_json_array_with_id_name_color(self) -> None:
        """linear_list_labels returns JSON array with id/name/color per label."""

        def fake_gql(query: str, variables: dict) -> dict:
            return {
                "team": {
                    "labels": {
                        "nodes": [
                            {"id": "lbl-1", "name": "Bug", "color": "#f00"},
                            {"id": "lbl-2", "name": "Feature", "color": "#0f0"},
                        ]
                    }
                }
            }

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            out = lw.linear_list_labels()

        labels = json.loads(out)
        self.assertEqual(len(labels), 2)
        self.assertEqual(labels[0], {"id": "lbl-1", "name": "Bug", "color": "#f00"})
        self.assertEqual(labels[1], {"id": "lbl-2", "name": "Feature", "color": "#0f0"})

    def test_list_labels_empty_team_returns_empty_array(self) -> None:
        """linear_list_labels with no labels returns empty JSON array."""

        def fake_gql(query: str, variables: dict) -> dict:
            return {"team": {"labels": {"nodes": []}}}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            out = lw.linear_list_labels()

        self.assertEqual(json.loads(out), [])

    def test_list_labels_no_team_id_raises_mcp_error(self) -> None:
        """linear_list_labels raises McpError when team_id cannot be resolved."""
        lw._CFG = SimpleNamespace(
            linear_api_key="fake-key",
            linear_team_id=None,
            fs_root=self.root,
        )
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            lw.linear_list_labels()
        self.assertIn("team_id required", str(ctx.exception))

    def test_list_labels_uses_explicit_team_id_over_config(self) -> None:
        """linear_list_labels passes explicit team_id to GraphQL, not config default."""
        seen_vars: list[dict] = []

        def fake_gql(query: str, variables: dict) -> dict:
            seen_vars.append(variables)
            return {"team": {"labels": {"nodes": []}}}

        with patch.object(lw, "_linear_gql", side_effect=fake_gql):
            lw.linear_list_labels(team_id="explicit-team-id")

        self.assertEqual(seen_vars[0]["teamId"], "explicit-team-id")

    # ------------------------------------------------------------------
    # linear_list_labels in TOOL_FUNCTIONS
    # ------------------------------------------------------------------

    def test_linear_list_labels_in_tool_functions(self) -> None:
        """linear_list_labels is registered in TOOL_FUNCTIONS."""
        self.assertIn(lw.linear_list_labels, lw.TOOL_FUNCTIONS)

    # ------------------------------------------------------------------
    # _resolve_label_ids and _resolve_team_state_id unit tests
    # ------------------------------------------------------------------

    def test_resolve_label_ids_empty_list_returns_empty(self) -> None:
        """_resolve_label_ids with empty list returns empty without an API call."""
        calls: list = []
        with patch.object(lw, "_linear_gql", side_effect=lambda q, v: calls.append(1) or {}):
            result = lw._resolve_label_ids("team-1", [])
        self.assertEqual(result, [])
        self.assertEqual(calls, [])

    def test_resolve_team_state_id_raises_on_missing_state(self) -> None:
        """_resolve_team_state_id raises McpError when state name absent."""

        def fake_gql(query: str, variables: dict) -> dict:
            return {
                "team": {"states": {"nodes": [{"id": "st-1", "name": "Done", "type": "completed"}]}}
            }

        with (
            patch.object(lw, "_linear_gql", side_effect=fake_gql),
            self.assertRaises(stdio_mcp.McpError) as ctx,
        ):
            lw._resolve_team_state_id("team-1", "Ghost")
        self.assertIn("Ghost", str(ctx.exception))
        self.assertIn("Done", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
