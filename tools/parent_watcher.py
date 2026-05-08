"""
parent_watcher.py -- Monitors sub-ticket progress and updates parent ticket state.

Phase 3 of the Job Splitter pipeline. Queries Linear for parent tickets with
children, evaluates child states, and proposes or applies state transitions
on the parent.
"""

from __future__ import annotations

import contextlib
import json
import os
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

_LINEAR_API = "https://api.linear.app/graphql"
_HTTP_TIMEOUT_S = 15

ACTIVE_STATES = frozenset({"Todo", "Backlog", "In Progress", "In Review"})
TERMINAL_STATES = frozenset({"Done", "Canceled"})
DONE_LIKE = frozenset({"Done", "Canceled"})

STATE_RANK = {
    "Backlog": 0,
    "Todo": 1,
    "In Progress": 2,
    "In Review": 3,
    "Done": 4,
    "Canceled": 5,
}


def _get_token() -> str:
    token = os.environ.get("LINEAR_API_KEY", "")
    if not token:
        raise RuntimeError("LINEAR_API_KEY environment variable not set")
    return token


def _linear_gql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    if requests is None:
        raise RuntimeError("requests library not installed; pip install requests")
    token = _get_token()
    resp = requests.post(
        _LINEAR_API,
        json={"query": query, "variables": variables},
        headers={
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "miru-parent-watcher/1.0",
        },
        timeout=_HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Linear API errors: {json.dumps(body['errors'])}")
    return body.get("data") or {}


_QUERY_PARENTS_WITH_CHILDREN = """
query($teamId: String!) {
  issues(
    filter: {
      team: { id: { eq: $teamId } }
      children: { length: { gt: 0 } }
    }
    first: 100
  ) {
    nodes {
      id
      identifier
      title
      state { name }
      children {
        nodes {
          id
          identifier
          title
          state { name }
        }
      }
    }
  }
}
"""

_QUERY_WORKFLOW_STATES = """
query($teamId: String!) {
  workflowStates(filter: { team: { id: { eq: $teamId } } }) {
    nodes { id name type }
  }
}
"""

_MUTATION_UPDATE_STATE = """
mutation($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    success
  }
}
"""

_MUTATION_COMMENT = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success
  }
}
"""


class ParentAction:
    def __init__(
        self,
        parent_id: str,
        parent_identifier: str,
        parent_title: str,
        current_state: str,
        proposed_state: str | None,
        comment: str,
        children_summary: dict[str, int],
    ):
        self.parent_id = parent_id
        self.parent_identifier = parent_identifier
        self.parent_title = parent_title
        self.current_state = current_state
        self.proposed_state = proposed_state
        self.comment = comment
        self.children_summary = children_summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "parent_identifier": self.parent_identifier,
            "parent_title": self.parent_title,
            "current_state": self.current_state,
            "proposed_state": self.proposed_state,
            "comment": self.comment,
            "children_summary": self.children_summary,
        }


def _is_forward_transition(current: str, proposed: str) -> bool:
    return STATE_RANK.get(proposed, -1) > STATE_RANK.get(current, -1)


def _evaluate_parent(parent: dict[str, Any]) -> ParentAction | None:
    parent_state = parent["state"]["name"]

    if parent_state in TERMINAL_STATES:
        return None

    children = parent["children"]["nodes"]
    if not children:
        return None

    state_counts: dict[str, int] = {}
    for child in children:
        child_state = child["state"]["name"]
        state_counts[child_state] = state_counts.get(child_state, 0) + 1

    total = len(children)
    done_count = state_counts.get("Done", 0)
    canceled_count = state_counts.get("Canceled", 0)
    in_progress_count = state_counts.get("In Progress", 0)
    in_review_count = state_counts.get("In Review", 0)

    all_done = done_count == total
    all_done_or_canceled = (done_count + canceled_count) == total and done_count > 0

    if all_done:
        return ParentAction(
            parent_id=parent["id"],
            parent_identifier=parent["identifier"],
            parent_title=parent["title"],
            current_state=parent_state,
            proposed_state="Done",
            comment=f"All {total} sub-ticket(s) completed. Closing parent.",
            children_summary=state_counts,
        )

    if all_done_or_canceled:
        canceled_ids = [c["identifier"] for c in children if c["state"]["name"] == "Canceled"]
        return ParentAction(
            parent_id=parent["id"],
            parent_identifier=parent["identifier"],
            parent_title=parent["title"],
            current_state=parent_state,
            proposed_state="Done",
            comment=(
                f"All sub-tickets resolved ({done_count} done, {canceled_count} canceled: "
                f"{', '.join(canceled_ids)}). Closing parent."
            ),
            children_summary=state_counts,
        )

    if (in_progress_count > 0 or in_review_count > 0) and parent_state in (
        "Todo",
        "Backlog",
    ):
        return ParentAction(
            parent_id=parent["id"],
            parent_identifier=parent["identifier"],
            parent_title=parent["title"],
            current_state=parent_state,
            proposed_state="In Progress",
            comment=f"Sub-ticket progress: {done_count}/{total} complete, {in_progress_count} in progress, {in_review_count} in review.",
            children_summary=state_counts,
        )

    if done_count > 0 and not all_done_or_canceled:
        return ParentAction(
            parent_id=parent["id"],
            parent_identifier=parent["identifier"],
            parent_title=parent["title"],
            current_state=parent_state,
            proposed_state=None,
            comment=f"Sub-ticket progress: {done_count}/{total} complete.",
            children_summary=state_counts,
        )

    return None


def _get_state_id(team_id: str, state_name: str) -> str:
    data = _linear_gql(_QUERY_WORKFLOW_STATES, {"teamId": team_id})
    states = data.get("workflowStates", {}).get("nodes", [])
    for state in states:
        if state["name"] == state_name:
            return state["id"]
    raise ValueError(f"Workflow state {state_name!r} not found for team {team_id}")


def scan_parents(team_id: str) -> list[ParentAction]:
    data = _linear_gql(_QUERY_PARENTS_WITH_CHILDREN, {"teamId": team_id})
    parents = data.get("issues", {}).get("nodes", [])

    actions: list[ParentAction] = []
    for parent in parents:
        action = _evaluate_parent(parent)
        if action is not None:
            actions.append(action)

    return actions


def execute_actions(
    team_id: str,
    actions: list[ParentAction],
    *,
    dry_run: bool = True,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for action in actions:
        result: dict[str, Any] = {
            "parent": action.parent_identifier,
            "current_state": action.current_state,
            "proposed_state": action.proposed_state,
            "comment": action.comment,
            "applied": False,
        }

        if dry_run:
            results.append(result)
            continue

        if action.proposed_state and _is_forward_transition(
            action.current_state, action.proposed_state
        ):
            try:
                state_id = _get_state_id(team_id, action.proposed_state)
                _linear_gql(
                    _MUTATION_UPDATE_STATE,
                    {"issueId": action.parent_id, "stateId": state_id},
                )
                result["applied"] = True
            except Exception as exc:
                result["error"] = str(exc)

        with contextlib.suppress(Exception):
            _linear_gql(
                _MUTATION_COMMENT,
                {"issueId": action.parent_id, "body": action.comment},
            )

        results.append(result)

    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Monitor sub-ticket progress and update parent ticket state."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply state changes (default: dry-run)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--team-id",
        default="f9d6193c-4572-40a9-b834-c408439f1aa1",
        help="Linear team UUID (default: Project Miru)",
    )
    args = parser.parse_args()

    actions = scan_parents(args.team_id)

    if not actions:
        if args.json_output:
            print(json.dumps({"actions": [], "dry_run": not args.execute}))
        else:
            print("No parent tickets need updates.")
        return

    results = execute_actions(args.team_id, actions, dry_run=not args.execute)

    if args.json_output:
        print(json.dumps({"actions": list(results), "dry_run": not args.execute}, indent=2))
    else:
        mode = "EXECUTE" if args.execute else "DRY-RUN"
        print(f"[{mode}] {len(results)} parent ticket(s) evaluated:\n")
        for r in results:
            state_change = (
                f"{r['current_state']} -> {r['proposed_state']}"
                if r["proposed_state"]
                else "no state change"
            )
            applied = " (APPLIED)" if r["applied"] else ""
            print(f"  {r['parent']}: {state_change}{applied}")
            print(f"    {r['comment']}")
            if r.get("error"):
                print(f"    ERROR: {r['error']}")
            print()


if __name__ == "__main__":
    main()
