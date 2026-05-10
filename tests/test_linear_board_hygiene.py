"""Tests for tools/linear_board_hygiene.py (PRO-322).

Coverage:
    1. dry-run mode does not mutate (mock GraphQL calls)
    2. active+archived ticket is queued for cancel
    3. done/canceled ticket is skipped even if archived
    4. single ticket update failure does not abort the batch
"""

from __future__ import annotations

import sys
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

# ---------------------------------------------------------------------------
# Import the module under test from tools/
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import linear_board_hygiene as lbh  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_KEY = "lin_api_testkey"

_ISSUE_ACTIVE_ARCHIVED = {
    "id": "issue-001",
    "identifier": "PRO-001",
    "title": "Active and archived ticket",
    "archivedAt": "2026-05-01T00:00:00Z",
    "team": {"id": "team-aaa", "name": "Project Miru"},
    "state": {"id": "state-unstarted", "name": "Todo", "type": "unstarted"},
}

_ISSUE_DONE_ARCHIVED = {
    "id": "issue-002",
    "identifier": "PRO-002",
    "title": "Done and archived ticket",
    "archivedAt": "2026-05-01T00:00:00Z",
    "team": {"id": "team-aaa", "name": "Project Miru"},
    "state": {"id": "state-done", "name": "Done", "type": "completed"},
}

_ISSUE_CANCELED_ARCHIVED = {
    "id": "issue-003",
    "identifier": "PRO-003",
    "title": "Already canceled and archived",
    "archivedAt": "2026-05-01T00:00:00Z",
    "team": {"id": "team-aaa", "name": "Project Miru"},
    "state": {"id": "state-canceled", "name": "Canceled", "type": "canceled"},
}

_CANCELED_STATE_ID = "state-canceled-id"

# A _gql response for the archived-issues query returning one active+archived issue.
_PAGE_ONE_ACTIVE = {
    "issues": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [_ISSUE_ACTIVE_ARCHIVED],
    }
}

# A _gql response for the workflow states query (all states for team).
_CANCELED_STATE_RESP = {
    "workflowStates": {
        "nodes": [
            {"id": "state-todo-id", "name": "Todo", "type": "unstarted"},
            {"id": _CANCELED_STATE_ID, "name": "Canceled", "type": "canceled"},
        ]
    }
}

# A _gql response for a successful issueUpdate mutation.
_UPDATE_SUCCESS = {
    "issueUpdate": {
        "success": True,
        "issue": {
            "id": "issue-001",
            "identifier": "PRO-001",
            "state": {"name": "Canceled"},
        },
    }
}

# A _gql response for a failed issueUpdate mutation.
_UPDATE_FAILURE = {
    "issueUpdate": {
        "success": False,
        "issue": None,
    }
}

# A second active+archived issue on the same team (used for pagination tests).
_ISSUE_ACTIVE_P2: dict[str, Any] = {
    "id": "issue-005",
    "identifier": "PRO-005",
    "title": "Page-2 active archived ticket",
    "archivedAt": "2026-05-01T00:00:00Z",
    "team": {"id": "team-aaa", "name": "Project Miru"},
    "state": {"id": "state-started", "name": "In Progress", "type": "started"},
}


def _make_gql_side_effect(
    *responses: Mapping[str, Any],
) -> Callable[[str, str, Mapping[str, Any] | None], Any]:
    """Return a side_effect function that yields responses in order."""
    it = iter(responses)

    def _side_effect(
        _api_key: str,
        _query: str,
        _variables: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            return next(it)
        except StopIteration as exc:
            raise AssertionError("Unexpected extra GQL call — no response configured") from exc

    return _side_effect


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestDryRunNoMutations(unittest.TestCase):
    """dry-run mode must not call the issueUpdate mutation."""

    def test_dry_run_does_not_call_update(self):
        responses = [
            _PAGE_ONE_ACTIVE,  # fetch archived issues
            _CANCELED_STATE_RESP,  # fetch canceled state for team
            # no mutation should be called
        ]
        with mock.patch.object(
            lbh, "_gql", side_effect=_make_gql_side_effect(*responses)
        ) as mock_gql:
            count = lbh.run_hygiene(_FAKE_KEY, dry_run=True)

        # Exactly two GQL calls: issues query + canceled-state query.
        self.assertEqual(mock_gql.call_count, 2)
        # The third call would be the mutation — confirm it was never made.
        call_queries = [c.args[1] for c in mock_gql.call_args_list]
        self.assertFalse(
            any("issueUpdate" in q for q in call_queries),
            "issueUpdate mutation must NOT be called in dry-run mode",
        )
        # Returned count still reports what would be moved.
        self.assertEqual(count, 1)


class TestActiveArchivedTicketQueued(unittest.TestCase):
    """An archived ticket with an active state is moved to Canceled."""

    def test_active_archived_ticket_is_canceled(self):
        responses = [
            _PAGE_ONE_ACTIVE,
            _CANCELED_STATE_RESP,
            _UPDATE_SUCCESS,
        ]
        with mock.patch.object(
            lbh, "_gql", side_effect=_make_gql_side_effect(*responses)
        ) as mock_gql:
            count = lbh.run_hygiene(_FAKE_KEY, dry_run=False)

        self.assertEqual(count, 1)
        self.assertTrue(
            any("issueUpdate" in c.args[1] for c in mock_gql.call_args_list),
            "issueUpdate mutation must be called in execute mode",
        )


class TestDoneOrCanceledTicketSkipped(unittest.TestCase):
    """Tickets with done/canceled state type must be skipped even if archived."""

    def _page_with(self, *issues):
        return {
            "issues": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": list(issues),
            }
        }

    def test_done_ticket_is_skipped(self):
        page = self._page_with(_ISSUE_DONE_ARCHIVED)
        with mock.patch.object(lbh, "_gql", side_effect=_make_gql_side_effect(page)) as mock_gql:
            count = lbh.run_hygiene(_FAKE_KEY, dry_run=False)

        # The GQL filter in the query already excludes done/canceled, but the
        # Python-side double-check in _fetch_archived_active_issues is what we
        # test here: _gql is called once (the issues query), zero times for the
        # state lookup, and zero times for mutation.
        self.assertEqual(mock_gql.call_count, 1, "Only the issues list query should run")
        self.assertEqual(count, 0)

    def test_canceled_ticket_is_skipped(self):
        page = self._page_with(_ISSUE_CANCELED_ARCHIVED)
        with mock.patch.object(lbh, "_gql", side_effect=_make_gql_side_effect(page)) as mock_gql:
            count = lbh.run_hygiene(_FAKE_KEY, dry_run=False)

        self.assertEqual(mock_gql.call_count, 1)
        self.assertEqual(count, 0)

    def test_non_archived_is_not_mutated(self):
        non_archived = {
            "id": "issue-010",
            "identifier": "PRO-010",
            "title": "Active but not archived",
            "archivedAt": None,
            "team": {"id": "team-aaa", "name": "Project Miru"},
            "state": {"id": "state-unstarted", "name": "Todo", "type": "unstarted"},
        }
        page = self._page_with(non_archived)
        with mock.patch.object(lbh, "_gql", side_effect=_make_gql_side_effect(page)) as mock_gql:
            count = lbh.run_hygiene(_FAKE_KEY, dry_run=False)

        self.assertEqual(mock_gql.call_count, 1, "Only the issues list query should run")
        self.assertEqual(count, 0)


class TestSingleTicketFailureDoesNotAbortBatch(unittest.TestCase):
    """A RuntimeError on one ticket's mutation must not abort the rest."""

    _ISSUE_ACTIVE_2: ClassVar[dict] = {
        "id": "issue-004",
        "identifier": "PRO-004",
        "title": "Second active archived ticket",
        "archivedAt": "2026-05-01T00:00:00Z",
        "team": {"id": "team-aaa", "name": "Project Miru"},
        "state": {"id": "state-started", "name": "In Progress", "type": "started"},
    }

    def test_one_failure_continues_to_next_ticket(self):
        page_two_issues = {
            "issues": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [_ISSUE_ACTIVE_ARCHIVED, self._ISSUE_ACTIVE_2],
            }
        }

        call_count = {"n": 0}

        def selective_gql(
            _api_key: str,
            _query: str,
            _variables: Mapping[str, Any] | None = None,
        ) -> Any:
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                # First call: issues list
                return page_two_issues
            if n == 2:
                # Second call: canceled state for the team (shared)
                return _CANCELED_STATE_RESP
            if n == 3:
                # Third call: mutation for issue-001 — assert target then fail
                assert (
                    (_variables or {}).get("id") == "issue-001"
                ), f"Expected mutation target issue-001, got {(_variables or {}).get('id')}"
                raise RuntimeError("Simulated Linear API failure")
            if n == 4:
                # Fourth call: mutation for issue-004 — assert target then succeed
                assert (
                    (_variables or {}).get("id") == "issue-004"
                ), f"Expected mutation target issue-004, got {(_variables or {}).get('id')}"
                return _UPDATE_SUCCESS
            raise AssertionError(f"Unexpected GQL call #{n}")

        with mock.patch.object(lbh, "_gql", side_effect=selective_gql):
            count = lbh.run_hygiene(_FAKE_KEY, dry_run=False)

        # Only the second ticket succeeded; the batch continued past the error.
        self.assertEqual(count, 1)
        # All four GQL calls were made (no early abort).
        self.assertEqual(call_count["n"], 4)


class TestPaginationHandling(unittest.TestCase):
    """Multi-page cursor responses must all be fetched and processed."""

    def test_pagination_fetches_all_pages(self):
        page_1 = {
            "issues": {
                "pageInfo": {"hasNextPage": True, "endCursor": "cursor-abc"},
                "nodes": [_ISSUE_ACTIVE_ARCHIVED],
            }
        }
        page_2 = {
            "issues": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [_ISSUE_ACTIVE_P2],
            }
        }
        # 2 page fetches + 1 state lookup (cached after first) + 2 mutations
        responses = [page_1, page_2, _CANCELED_STATE_RESP, _UPDATE_SUCCESS, _UPDATE_SUCCESS]
        with mock.patch.object(
            lbh, "_gql", side_effect=_make_gql_side_effect(*responses)
        ) as mock_gql:
            count = lbh.run_hygiene(_FAKE_KEY, dry_run=False)

        self.assertEqual(count, 2, "Both pages' tickets should be processed")
        self.assertEqual(mock_gql.call_count, 5)


class TestRateLimitRetry(unittest.TestCase):
    """_gql retries once on HTTP 429 then succeeds."""

    def test_rate_limit_retry_succeeds(self) -> None:
        import sys
        import types

        mock_resp_429 = mock.MagicMock()
        mock_resp_429.ok = False
        mock_resp_429.status_code = 429

        mock_resp_200 = mock.MagicMock()
        mock_resp_200.ok = True
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = {"data": {"retried": True}}

        mock_requests = types.ModuleType("requests")
        mock_requests.post = mock.MagicMock(  # type: ignore[attr-defined]
            side_effect=[mock_resp_429, mock_resp_200]
        )

        class _RequestError(Exception):
            pass

        mock_requests.RequestException = _RequestError  # type: ignore[attr-defined]

        with (
            mock.patch.dict(sys.modules, {"requests": mock_requests}),
            mock.patch.object(lbh, "time") as mock_time,
        ):
            result = lbh._gql("fake-key", "{ q }")

        self.assertEqual(result, {"retried": True})
        mock_time.sleep.assert_called_once_with(lbh._RATE_LIMIT_SLEEP_S)


if __name__ == "__main__":
    unittest.main()
