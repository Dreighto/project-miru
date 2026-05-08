"""Tests for tools/parent_watcher.py"""

from __future__ import annotations

from unittest.mock import patch

from tools.parent_watcher import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    ParentAction,
    _evaluate_parent,
    _is_forward_transition,
    execute_actions,
    scan_parents,
)


def _make_child(identifier: str, state: str) -> dict:
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"Sub {identifier}",
        "state": {"name": state},
    }


def _make_parent(identifier: str, state: str, children: list[dict]) -> dict:
    return {
        "id": f"uuid-{identifier}",
        "identifier": identifier,
        "title": f"Parent {identifier}",
        "state": {"name": state},
        "children": {"nodes": children},
    }


TEAM_ID = "f9d6193c-4572-40a9-b834-c408439f1aa1"


class TestEvaluateParent:
    def test_all_children_done_proposes_done(self):
        parent = _make_parent(
            "PRO-100",
            "In Progress",
            [
                _make_child("PRO-101", "Done"),
                _make_child("PRO-102", "Done"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is not None
        assert action.proposed_state == "Done"
        assert "2 sub-ticket(s) completed" in action.comment

    def test_all_done_or_canceled_proposes_done(self):
        parent = _make_parent(
            "PRO-100",
            "In Progress",
            [
                _make_child("PRO-101", "Done"),
                _make_child("PRO-102", "Canceled"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is not None
        assert action.proposed_state == "Done"
        assert "PRO-102" in action.comment
        assert "canceled" in action.comment.lower()

    def test_all_canceled_no_done_returns_none(self):
        parent = _make_parent(
            "PRO-100",
            "In Progress",
            [
                _make_child("PRO-101", "Canceled"),
                _make_child("PRO-102", "Canceled"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is None

    def test_child_in_progress_moves_todo_parent(self):
        parent = _make_parent(
            "PRO-100",
            "Todo",
            [
                _make_child("PRO-101", "In Progress"),
                _make_child("PRO-102", "Todo"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is not None
        assert action.proposed_state == "In Progress"

    def test_child_in_progress_no_change_if_parent_already_in_progress(self):
        parent = _make_parent(
            "PRO-100",
            "In Progress",
            [
                _make_child("PRO-101", "In Progress"),
                _make_child("PRO-102", "Todo"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is None

    def test_partial_done_reports_progress(self):
        parent = _make_parent(
            "PRO-100",
            "In Progress",
            [
                _make_child("PRO-101", "Done"),
                _make_child("PRO-102", "Todo"),
                _make_child("PRO-103", "Todo"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is not None
        assert action.proposed_state is None
        assert "1/3" in action.comment

    def test_skips_done_parent(self):
        parent = _make_parent(
            "PRO-100",
            "Done",
            [
                _make_child("PRO-101", "Done"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is None

    def test_skips_canceled_parent(self):
        parent = _make_parent(
            "PRO-100",
            "Canceled",
            [
                _make_child("PRO-101", "Todo"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is None

    def test_no_children_returns_none(self):
        parent = _make_parent("PRO-100", "In Progress", [])
        action = _evaluate_parent(parent)
        assert action is None

    def test_backlog_parent_moves_to_in_progress(self):
        parent = _make_parent(
            "PRO-100",
            "Backlog",
            [
                _make_child("PRO-101", "In Review"),
                _make_child("PRO-102", "Todo"),
            ],
        )
        action = _evaluate_parent(parent)
        assert action is not None
        assert action.proposed_state == "In Progress"


class TestForwardTransition:
    def test_forward(self):
        assert _is_forward_transition("Todo", "In Progress") is True
        assert _is_forward_transition("In Progress", "Done") is True
        assert _is_forward_transition("Backlog", "Done") is True

    def test_backward(self):
        assert _is_forward_transition("In Progress", "Todo") is False
        assert _is_forward_transition("Done", "In Progress") is False

    def test_same(self):
        assert _is_forward_transition("Todo", "Todo") is False


class TestScanParents:
    @patch("tools.parent_watcher._linear_gql")
    def test_returns_actions_for_actionable_parents(self, mock_gql):
        mock_gql.return_value = {
            "issues": {
                "nodes": [
                    _make_parent(
                        "PRO-100",
                        "In Progress",
                        [
                            _make_child("PRO-101", "Done"),
                            _make_child("PRO-102", "Done"),
                        ],
                    ),
                    _make_parent(
                        "PRO-200",
                        "Done",
                        [
                            _make_child("PRO-201", "Done"),
                        ],
                    ),
                ]
            }
        }

        actions = scan_parents(TEAM_ID)
        assert len(actions) == 1
        assert actions[0].parent_identifier == "PRO-100"

    @patch("tools.parent_watcher._linear_gql")
    def test_empty_when_no_parents(self, mock_gql):
        mock_gql.return_value = {"issues": {"nodes": []}}
        actions = scan_parents(TEAM_ID)
        assert actions == []


class TestExecuteActions:
    @patch("tools.parent_watcher._linear_gql")
    def test_dry_run_makes_no_mutations(self, mock_gql):
        action = ParentAction(
            parent_id="uuid-100",
            parent_identifier="PRO-100",
            parent_title="Test",
            current_state="In Progress",
            proposed_state="Done",
            comment="All done.",
            children_summary={"Done": 2},
        )

        results = execute_actions(TEAM_ID, [action], dry_run=True)
        assert len(results) == 1
        assert results[0]["applied"] is False
        mock_gql.assert_not_called()

    @patch("tools.parent_watcher._linear_gql")
    def test_execute_applies_forward_transition(self, mock_gql):
        mock_gql.side_effect = [
            {
                "workflowStates": {
                    "nodes": [
                        {"id": "state-done", "name": "Done", "type": "completed"},
                    ]
                }
            },
            {"issueUpdate": {"success": True}},
            {"commentCreate": {"success": True}},
        ]

        action = ParentAction(
            parent_id="uuid-100",
            parent_identifier="PRO-100",
            parent_title="Test",
            current_state="In Progress",
            proposed_state="Done",
            comment="All done.",
            children_summary={"Done": 2},
        )

        results = execute_actions(TEAM_ID, [action], dry_run=False)
        assert results[0]["applied"] is True
        assert mock_gql.call_count == 3

    @patch("tools.parent_watcher._linear_gql")
    def test_execute_skips_backward_transition(self, mock_gql):
        mock_gql.side_effect = [
            {"commentCreate": {"success": True}},
        ]

        action = ParentAction(
            parent_id="uuid-100",
            parent_identifier="PRO-100",
            parent_title="Test",
            current_state="In Review",
            proposed_state="In Progress",
            comment="Progress update.",
            children_summary={"In Progress": 1, "Done": 1},
        )

        results = execute_actions(TEAM_ID, [action], dry_run=False)
        assert results[0]["applied"] is False

    @patch("tools.parent_watcher._linear_gql")
    def test_execute_no_state_change_still_comments(self, mock_gql):
        mock_gql.side_effect = [
            {"commentCreate": {"success": True}},
        ]

        action = ParentAction(
            parent_id="uuid-100",
            parent_identifier="PRO-100",
            parent_title="Test",
            current_state="In Progress",
            proposed_state=None,
            comment="1/3 complete.",
            children_summary={"Done": 1, "Todo": 2},
        )

        results = execute_actions(TEAM_ID, [action], dry_run=False)
        assert results[0]["applied"] is False
        assert mock_gql.call_count == 1

    @patch("tools.parent_watcher._linear_gql")
    def test_execute_handles_api_error_gracefully(self, mock_gql):
        mock_gql.side_effect = [
            RuntimeError("Linear API timeout"),
            {"commentCreate": {"success": True}},
        ]

        action = ParentAction(
            parent_id="uuid-100",
            parent_identifier="PRO-100",
            parent_title="Test",
            current_state="In Progress",
            proposed_state="Done",
            comment="All done.",
            children_summary={"Done": 2},
        )

        results = execute_actions(TEAM_ID, [action], dry_run=False)
        assert results[0]["applied"] is False
        assert "error" in results[0]


class TestParentActionDict:
    def test_to_dict(self):
        action = ParentAction(
            parent_id="uuid-100",
            parent_identifier="PRO-100",
            parent_title="Test parent",
            current_state="Todo",
            proposed_state="In Progress",
            comment="Work started.",
            children_summary={"In Progress": 1, "Todo": 1},
        )
        d = action.to_dict()
        assert d["parent_identifier"] == "PRO-100"
        assert d["proposed_state"] == "In Progress"
        assert "parent_id" not in d


class TestConstants:
    def test_active_states(self):
        assert "Todo" in ACTIVE_STATES
        assert "In Progress" in ACTIVE_STATES
        assert "Done" not in ACTIVE_STATES

    def test_terminal_states(self):
        assert "Done" in TERMINAL_STATES
        assert "Canceled" in TERMINAL_STATES
        assert "Todo" not in TERMINAL_STATES
