"""PRO-322: Linear board hygiene — auto-cancel archived tickets in active states.

Finds tickets where archivedAt != null AND state.type in
[unstarted, started, triage], then moves them to Canceled.

Usage:
    python tools/linear_board_hygiene.py [--dry-run] [--team "Project Miru"]

Options:
    --dry-run   Print what would be canceled; no mutations.
    --team NAME Filter to a single team by name (default: all teams).

Output:
    Table: ticket ID | title | old state -> Canceled
    Summary: "N tickets moved to Canceled"

Exit codes:
    0  Success (or dry-run pass)
    1  Fatal error (bad API key, unrecoverable HTTP error)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ACTIVE_STATE_TYPES = {"unstarted", "started", "triage"}

_QUERY_ARCHIVED_ISSUES = """
query ArchivedActiveIssues($after: String) {
  issues(
    filter: {
      archivedAt: { null: false }
    }
    first: 100
    after: $after
    includeArchived: true
  ) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      identifier
      title
      archivedAt
      team {
        id
        name
      }
      state {
        id
        name
        type
      }
    }
  }
}
"""

_QUERY_WORKFLOW_STATES = """
query WorkflowStates($teamId: ID!) {
  workflowStates(
    filter: {
      team: { id: { eq: $teamId } }
    }
    first: 50
  ) {
    nodes {
      id
      name
      type
    }
  }
}
"""

_MUTATION_UPDATE_ISSUE = """
mutation CancelIssue($id: String!, $stateId: String!) {
  issueUpdate(id: $id, input: { stateId: $stateId }) {
    success
    issue {
      id
      identifier
      state {
        name
      }
    }
  }
}
"""

_LINEAR_API_URL = "https://api.linear.app/graphql"
_RATE_LIMIT_SLEEP_S = 60
_RATE_LIMIT_RETRY = 1


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _gql(api_key: str, query: str, variables: dict[str, Any] | None = None) -> dict:
    """Execute one GraphQL request; return the 'data' dict.

    Raises RuntimeError on HTTP error or GraphQL top-level errors.
    Handles 429 with one retry after _RATE_LIMIT_SLEEP_S seconds.
    """
    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    for attempt in range(_RATE_LIMIT_RETRY + 1):
        resp = requests.post(_LINEAR_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 429:
            if attempt < _RATE_LIMIT_RETRY:
                print(
                    f"[hygiene] rate-limited (429); sleeping {_RATE_LIMIT_SLEEP_S}s then retrying…",
                    file=sys.stderr,
                )
                time.sleep(_RATE_LIMIT_SLEEP_S)
                continue
            raise RuntimeError("Rate-limited by Linear API and retry exhausted.")
        if not resp.ok:
            raise RuntimeError(f"Linear API HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json()
        errors = body.get("errors")
        if errors:
            raise RuntimeError(f"Linear GraphQL errors: {errors}")
        return body.get("data", {})

    raise RuntimeError("Unexpected exit from retry loop.")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _fetch_archived_active_issues(api_key: str, team_filter: str | None) -> list[dict]:
    """Return all issues that are archived but still in an active state."""
    issues: list[dict] = []
    cursor: str | None = None

    while True:
        variables: dict[str, Any] = {}
        if cursor:
            variables["after"] = cursor

        data = _gql(api_key, _QUERY_ARCHIVED_ISSUES, variables or None)
        page = data.get("issues", {})
        nodes = page.get("nodes", [])

        for node in nodes:
            team_name = (node.get("team") or {}).get("name", "")
            if team_filter and team_name.lower() != team_filter.lower():
                continue
            # Double-check state type (belt-and-suspenders; the GQL filter
            # already restricts to active types, but guard against API drift).
            state_type = (node.get("state") or {}).get("type", "")
            if state_type not in _ACTIVE_STATE_TYPES:
                continue
            issues.append(node)

        page_info = page.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")

    return issues


def _get_canceled_state_id(api_key: str, team_id: str) -> str | None:
    """Return the ID of the first Canceled workflow state for the given team."""
    data = _gql(api_key, _QUERY_WORKFLOW_STATES, {"teamId": team_id})
    nodes = data.get("workflowStates", {}).get("nodes", [])
    for node in nodes:
        if node.get("type") == "canceled":
            return node["id"]
    return None


def _cancel_issue(api_key: str, issue_id: str, state_id: str) -> bool:
    """Mutate the issue to canceled. Returns True on success."""
    data = _gql(api_key, _MUTATION_UPDATE_ISSUE, {"id": issue_id, "stateId": state_id})
    return bool(data.get("issueUpdate", {}).get("success"))


# ---------------------------------------------------------------------------
# Public entry point (also used by tests)
# ---------------------------------------------------------------------------


def run_hygiene(
    api_key: str,
    team_filter: str | None = None,
    dry_run: bool = False,
) -> int:
    """Main logic; returns count of tickets moved (or that would be moved).

    Errors on individual tickets are logged but do not abort the batch.
    Fatal errors (bad key, HTTP) raise RuntimeError.
    """
    issues = _fetch_archived_active_issues(api_key, team_filter)

    if not issues:
        print("No archived tickets in active states found.")
        return 0

    # Cache canceled-state IDs per team so we don't re-fetch per ticket.
    canceled_state_cache: dict[str, str | None] = {}

    moved = 0
    rows: list[str] = []

    for issue in issues:
        identifier = issue.get("identifier", issue["id"])
        title = issue.get("title", "(no title)")
        old_state = (issue.get("state") or {}).get("name", "?")
        team_id = (issue.get("team") or {}).get("id", "")

        if team_id not in canceled_state_cache:
            canceled_state_cache[team_id] = _get_canceled_state_id(api_key, team_id)

        canceled_id = canceled_state_cache[team_id]
        if canceled_id is None:
            rows.append(f"  {identifier:<12} SKIP  — no Canceled state found for team")
            continue

        if dry_run:
            rows.append(f"  {identifier:<12} {old_state} -> Canceled  [{title[:60]}]")
            moved += 1
            continue

        try:
            ok = _cancel_issue(api_key, issue["id"], canceled_id)
            if ok:
                rows.append(f"  {identifier:<12} {old_state} -> Canceled  [{title[:60]}]")
                moved += 1
            else:
                rows.append(f"  {identifier:<12} WARN — issueUpdate returned success=false")
        except Exception as exc:
            rows.append(f"  {identifier:<12} ERROR — {exc}")

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"\n{prefix}Tickets processed:\n")
    for row in rows:
        print(row)

    verb = "would be moved" if dry_run else "moved"
    print(f"\n{prefix}{moved} ticket(s) {verb} to Canceled.")
    return moved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_api_key() -> str:
    """Load LINEAR_API_KEY from environment (after loading .env)."""
    this_dir = Path(__file__).resolve().parent
    repo_root = this_dir.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key:
        raise RuntimeError("LINEAR_API_KEY is not set. Add it to .env or the environment.")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cancel Linear tickets that are archived but still in active states."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be canceled; make no changes.",
    )
    parser.add_argument(
        "--team",
        default=None,
        metavar="NAME",
        help='Filter to a single team by name (default: all teams). E.g. "Project Miru".',
    )
    args = parser.parse_args()

    try:
        api_key = _load_api_key()
    except RuntimeError as exc:
        print(f"[hygiene] fatal: {exc}", file=sys.stderr)
        return 1

    try:
        run_hygiene(api_key, team_filter=args.team, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"[hygiene] fatal: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
