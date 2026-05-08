"""
sub_ticket_creator.py -- Creates scoped sub-tickets in Linear from complexity classifier output.

Phase 2 of the Job Splitter pipeline. Takes a parent ticket identifier and a
ClassificationResult from complexity_classifier.py, creates linked sub-tickets,
and posts a summary comment on the parent.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from tools.complexity_classifier import ClassificationResult, SuggestedSplit

_LINEAR_API = "https://api.linear.app/graphql"
_HTTP_TIMEOUT_S = 15

# Labels that indicate worker assignment — strip from sub-tickets so they get fresh routing.
# Only claude-code and gemini run in the dispatch loop. Others are manual or benched.
LOOP_WORKER_LABELS = frozenset({"claude-code", "gemini"})
ALL_WORKER_LABELS = frozenset({"claude-code", "gemini", "codex", "cursor", "operator"})


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
            "User-Agent": "miru-sub-ticket-creator/1.0",
        },
        timeout=_HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError(f"Linear API errors: {json.dumps(body['errors'])}")
    return body.get("data") or {}


_QUERY_PARENT = """
query($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    priority
    team { id }
    project { id }
    labels { nodes { id name } }
  }
}
"""

_MUTATION_CREATE = """
mutation($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier title url }
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


def _fetch_parent(ticket_id: str) -> dict[str, Any]:
    data = _linear_gql(_QUERY_PARENT, {"id": ticket_id})
    issue = data.get("issue")
    if not issue:
        raise ValueError(f"Parent ticket {ticket_id!r} not found in Linear")
    return issue


def _build_sub_description(split: SuggestedSplit, parent_desc: str | None) -> str:
    parts = [f"## Scope\n\n{split['scope']}"]
    if split.get("service_dirs"):
        dirs = ", ".join(f"`{d}`" for d in split["service_dirs"])
        parts.append(f"\n**Target directories:** {dirs}")
    if parent_desc:
        trimmed = parent_desc[:2000]
        if len(parent_desc) > 2000:
            trimmed += "\n\n_(parent description truncated)_"
        parts.append(f"\n---\n\n## Parent context\n\n{trimmed}")
    return "\n".join(parts)


def _filter_labels(label_nodes: list[dict[str, str]]) -> list[str]:
    return [
        node["id"] for node in label_nodes if node.get("name", "").lower() not in ALL_WORKER_LABELS
    ]


def create_sub_tickets(
    parent_ticket_id: str,
    classification: ClassificationResult,
) -> list[str]:
    """Create scoped sub-tickets in Linear from classifier output.

    Args:
        parent_ticket_id: Linear ticket identifier (e.g. "PRO-321") or UUID.
        classification: Output from complexity_classifier.classify_ticket().

    Returns:
        List of created sub-ticket identifiers (e.g. ["PRO-325", "PRO-326"]).
    """
    splits = classification.get("suggested_splits", [])
    if not splits:
        return []

    parent = _fetch_parent(parent_ticket_id)
    parent_uuid = parent["id"]
    parent_title = parent["title"]
    parent_desc = parent.get("description") or ""
    team_id = parent["team"]["id"]
    project_id = parent.get("project", {}).get("id") if parent.get("project") else None
    if not project_id:
        raise ValueError(
            f"Parent ticket {parent_ticket_id!r} has no project; sub-tickets require a projectId"
        )
    priority = parent.get("priority")
    label_ids = _filter_labels(parent.get("labels", {}).get("nodes", []))

    created: list[str] = []
    errors: list[str] = []

    for split in splits:
        separator = " — "
        label = split["label"]
        max_parent = 200 - len(separator) - len(label) - 3
        if max_parent < 10:
            title = f"{parent_title[:50]}...{separator}{label}"[:200]
        elif len(parent_title) > max_parent:
            title = f"{parent_title[:max_parent]}...{separator}{label}"
        else:
            title = f"{parent_title}{separator}{label}"

        sub_desc = _build_sub_description(split, parent_desc)

        inp: dict[str, Any] = {
            "title": title,
            "description": sub_desc,
            "teamId": team_id,
            "parentId": parent_uuid,
        }
        inp["projectId"] = project_id
        if priority is not None:
            inp["priority"] = priority
        if label_ids:
            inp["labelIds"] = label_ids

        try:
            data = _linear_gql(_MUTATION_CREATE, {"input": inp})
            result = data.get("issueCreate", {})
            if result.get("success"):
                identifier = result["issue"]["identifier"]
                created.append(identifier)
            else:
                errors.append(f"issueCreate returned success=false for {split['label']!r}")
        except Exception as exc:
            errors.append(f"Failed to create sub-ticket {split['label']!r}: {exc}")

    if created:
        ticket_list = ", ".join(created)
        comment = f"Split into {len(created)} sub-ticket(s): {ticket_list}"
        with contextlib.suppress(Exception):
            _linear_gql(_MUTATION_COMMENT, {"issueId": parent_uuid, "body": comment})

    if errors:
        print(f"[sub_ticket_creator] warnings: {'; '.join(errors)}", file=sys.stderr)

    return created


def main() -> None:
    """CLI entry point: python -m tools.sub_ticket_creator <ticket_id> <classification_json>"""
    if len(sys.argv) < 3:
        print(
            "Usage: python -m tools.sub_ticket_creator <ticket_id> <classification_json>",
            file=sys.stderr,
        )
        sys.exit(1)

    ticket_id = sys.argv[1]
    classification: ClassificationResult = json.loads(sys.argv[2])
    result = create_sub_tickets(ticket_id, classification)
    print(json.dumps({"created": result}, indent=2))


if __name__ == "__main__":
    main()
