"""Tests for tools/sub_ticket_creator.py"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.sub_ticket_creator import (
    ALL_WORKER_LABELS,
    LOOP_WORKER_LABELS,
    _build_sub_description,
    _filter_labels,
    create_sub_tickets,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PARENT_ISSUE = {
    "id": "uuid-parent-123",
    "identifier": "PRO-100",
    "title": "Overhaul the dispatch pipeline and add retry logic",
    "description": "Full redesign of the dispatch flow.\n\n- Step 1: refactor spawn\n- Step 2: add retry\n- Step 3: update tests",
    "priority": 2,
    "team": {"id": "team-uuid-1"},
    "project": {"id": "project-uuid-1"},
    "labels": {
        "nodes": [
            {"id": "label-feature", "name": "Feature"},
            {"id": "label-cc", "name": "claude-code"},
            {"id": "label-bug", "name": "Bug"},
            {"id": "label-gemini", "name": "gemini"},
        ]
    },
}

CLASSIFICATION_SPLIT = {
    "should_split": True,
    "complexity": "high",
    "signals": ["Touches 2 service boundaries: dispatch_listener, tools"],
    "suggested_splits": [
        {
            "label": "dispatch_listener work",
            "scope": "Changes isolated to services/dispatch_listener/",
            "service_dirs": ["services/dispatch_listener/"],
        },
        {
            "label": "tools work",
            "scope": "Changes isolated to tools/",
            "service_dirs": ["tools/"],
        },
    ],
}

CLASSIFICATION_NO_SPLIT = {
    "should_split": False,
    "complexity": "low",
    "signals": [],
    "suggested_splits": [],
}

CREATED_ISSUE_A = {
    "issueCreate": {
        "success": True,
        "issue": {
            "id": "uuid-sub-a",
            "identifier": "PRO-101",
            "title": "Overhaul the dispatch pipeline and add retry logic — dispatch_listener work",
            "url": "https://linear.app/project-miru/issue/PRO-101",
        },
    }
}

CREATED_ISSUE_B = {
    "issueCreate": {
        "success": True,
        "issue": {
            "id": "uuid-sub-b",
            "identifier": "PRO-102",
            "title": "Overhaul the dispatch pipeline and add retry logic — tools work",
            "url": "https://linear.app/project-miru/issue/PRO-102",
        },
    }
}

COMMENT_SUCCESS = {"commentCreate": {"success": True}}


# ---------------------------------------------------------------------------
# Unit tests -- label filtering
# ---------------------------------------------------------------------------


class TestFilterLabels:
    def test_strips_all_worker_labels(self):
        nodes = [
            {"id": "1", "name": "Feature"},
            {"id": "2", "name": "claude-code"},
            {"id": "3", "name": "gemini"},
            {"id": "4", "name": "codex"},
            {"id": "5", "name": "cursor"},
            {"id": "6", "name": "operator"},
            {"id": "7", "name": "Bug"},
        ]
        result = _filter_labels(nodes)
        assert result == ["1", "7"]

    def test_keeps_non_worker_labels(self):
        nodes = [
            {"id": "1", "name": "Feature"},
            {"id": "2", "name": "Improvement"},
            {"id": "3", "name": "chore"},
        ]
        result = _filter_labels(nodes)
        assert result == ["1", "2", "3"]

    def test_empty_labels(self):
        assert _filter_labels([]) == []

    def test_case_insensitive(self):
        nodes = [{"id": "1", "name": "Claude-Code"}, {"id": "2", "name": "GEMINI"}]
        result = _filter_labels(nodes)
        assert result == []


# ---------------------------------------------------------------------------
# Unit tests -- description builder
# ---------------------------------------------------------------------------


class TestBuildSubDescription:
    def test_basic_split(self):
        split = {"label": "Part 1", "scope": "First half", "service_dirs": []}
        result = _build_sub_description(split, "Parent context here")
        assert "## Scope" in result
        assert "First half" in result
        assert "Parent context here" in result

    def test_with_service_dirs(self):
        split = {
            "label": "backend",
            "scope": "Backend changes",
            "service_dirs": ["miru_ai/", "tools/"],
        }
        result = _build_sub_description(split, None)
        assert "`miru_ai/`" in result
        assert "`tools/`" in result
        assert "Target directories" in result

    def test_truncates_long_parent_desc(self):
        long_desc = "x" * 3000
        split = {"label": "Part 1", "scope": "Test", "service_dirs": []}
        result = _build_sub_description(split, long_desc)
        assert "truncated" in result
        assert len(result) < 3500

    def test_no_parent_desc(self):
        split = {"label": "Part 1", "scope": "Test scope", "service_dirs": []}
        result = _build_sub_description(split, None)
        assert "## Scope" in result
        assert "Parent context" not in result


# ---------------------------------------------------------------------------
# Integration tests -- create_sub_tickets (mocked Linear API)
# ---------------------------------------------------------------------------


class TestCreateSubTickets:
    @patch("tools.sub_ticket_creator._linear_gql")
    def test_creates_two_sub_tickets(self, mock_gql):
        mock_gql.side_effect = [
            {"issue": PARENT_ISSUE},
            CREATED_ISSUE_A,
            CREATED_ISSUE_B,
            COMMENT_SUCCESS,
        ]

        result = create_sub_tickets("PRO-100", CLASSIFICATION_SPLIT)

        assert result == ["PRO-101", "PRO-102"]
        assert mock_gql.call_count == 4

        create_call_1 = mock_gql.call_args_list[1]
        inp = create_call_1[0][1]["input"]
        assert inp["parentId"] == "uuid-parent-123"
        assert inp["teamId"] == "team-uuid-1"
        assert inp["projectId"] == "project-uuid-1"
        assert inp["priority"] == 2
        assert "label-cc" not in inp.get("labelIds", [])
        assert "label-gemini" not in inp.get("labelIds", [])
        assert "label-feature" in inp["labelIds"]
        assert "label-bug" in inp["labelIds"]

    @patch("tools.sub_ticket_creator._linear_gql")
    def test_empty_splits_returns_empty(self, mock_gql):
        result = create_sub_tickets("PRO-100", CLASSIFICATION_NO_SPLIT)
        assert result == []
        mock_gql.assert_not_called()

    @patch("tools.sub_ticket_creator._linear_gql")
    def test_parent_not_found_raises(self, mock_gql):
        mock_gql.return_value = {"issue": None}

        with pytest.raises(ValueError, match="not found"):
            create_sub_tickets("PRO-999", CLASSIFICATION_SPLIT)

    @patch("tools.sub_ticket_creator._linear_gql")
    def test_partial_failure_continues(self, mock_gql):
        mock_gql.side_effect = [
            {"issue": PARENT_ISSUE},
            RuntimeError("Linear API timeout"),
            CREATED_ISSUE_B,
            COMMENT_SUCCESS,
        ]

        result = create_sub_tickets("PRO-100", CLASSIFICATION_SPLIT)
        assert result == ["PRO-102"]

    @patch("tools.sub_ticket_creator._linear_gql")
    def test_title_truncation(self, mock_gql):
        long_parent = dict(PARENT_ISSUE)
        long_parent["title"] = "A" * 200

        mock_gql.side_effect = [
            {"issue": long_parent},
            CREATED_ISSUE_A,
            COMMENT_SUCCESS,
        ]

        one_split = dict(CLASSIFICATION_SPLIT)
        one_split["suggested_splits"] = [CLASSIFICATION_SPLIT["suggested_splits"][0]]

        create_sub_tickets("PRO-100", one_split)

        create_call = mock_gql.call_args_list[1]
        title = create_call[0][1]["input"]["title"]
        assert len(title) <= 200

    @patch("tools.sub_ticket_creator._linear_gql")
    def test_no_project_on_parent(self, mock_gql):
        parent_no_project = dict(PARENT_ISSUE)
        parent_no_project["project"] = None

        mock_gql.side_effect = [
            {"issue": parent_no_project},
            CREATED_ISSUE_A,
            CREATED_ISSUE_B,
            COMMENT_SUCCESS,
        ]

        result = create_sub_tickets("PRO-100", CLASSIFICATION_SPLIT)
        assert result == ["PRO-101", "PRO-102"]

        create_call = mock_gql.call_args_list[1]
        assert "projectId" not in create_call[0][1]["input"]

    @patch("tools.sub_ticket_creator._linear_gql")
    def test_comment_posted_on_parent(self, mock_gql):
        mock_gql.side_effect = [
            {"issue": PARENT_ISSUE},
            CREATED_ISSUE_A,
            CREATED_ISSUE_B,
            COMMENT_SUCCESS,
        ]

        create_sub_tickets("PRO-100", CLASSIFICATION_SPLIT)

        comment_call = mock_gql.call_args_list[3]
        body = comment_call[0][1]["body"]
        assert "PRO-101" in body
        assert "PRO-102" in body
        assert "2 sub-ticket" in body


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestConstants:
    def test_loop_workers_subset_of_all(self):
        assert LOOP_WORKER_LABELS.issubset(ALL_WORKER_LABELS)

    def test_loop_workers_are_claude_and_gemini(self):
        assert {"claude-code", "gemini"} == LOOP_WORKER_LABELS

    def test_all_workers_includes_benched(self):
        assert "codex" in ALL_WORKER_LABELS
        assert "cursor" in ALL_WORKER_LABELS
