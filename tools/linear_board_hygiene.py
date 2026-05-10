"""PRO-322: Linear board hygiene — auto-cancel archived tickets in active states.

Finds tickets where archivedAt != null AND state.type in
[unstarted, started, triage], then moves them to Canceled.

Usage:
    python tools/linear_board_hygiene.py [--execute] [--json] [--team "Project Miru"]

Options:
    --execute   Apply cancellations (default: dry-run / preview only).
    --dry-run   Preview only; make no changes (explicit form of the default).
    --json      Emit machine-readable JSON instead of text output.
    --team NAME Filter to a single team by name (default: all teams).

Output (text, default):
    Table: ticket ID | title | old state -> Canceled
    Summary: "N tickets moved to Canceled"

Output (--json):
    {"dry_run": bool, "tickets": [...], "summary": {"moved": N, "skipped": N, "errors": N}}

Exit codes:
    0  Success (or dry-run pass)
    1  Fatal error (bad API key, unrecoverable HTTP error)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# requests and load_dotenv are imported inside the functions that use them
# so pytest can collect this module without requiring these packages installed.

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
    try:
        import requests  # deferred so pytest collection works without the package
    except ModuleNotFoundError as exc:
        raise RuntimeError("requests is required: pip install requests") from exc

    headers = {
        "Authorization": api_key,
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"query": query}
    if variables:
        payload["variables"] = variables

    for attempt in range(_RATE_LIMIT_RETRY + 1):
        try:
            resp = requests.post(_LINEAR_API_URL, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise RuntimeError(f"Linear API request failed: {exc}") from exc
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
        try:
            body = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"Linear API returned non-JSON response: {exc}") from exc
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
            if not (node.get("archivedAt") or ""):
                continue
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
        if not cursor:
            raise RuntimeError(
                "Linear pagination error: hasNextPage=true but endCursor is missing."
            )

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
    dry_run: bool = True,
    json_output: bool = False,
) -> int:
    """Main logic; returns count of tickets moved (or that would be moved).

    Errors on individual tickets are logged but do not abort the batch.
    Fatal errors (bad key, HTTP) raise RuntimeError.
    """
    issues = _fetch_archived_active_issues(api_key, team_filter)

    if not issues:
        if json_output:
            print(
                json.dumps(
                    {
                        "dry_run": dry_run,
                        "tickets": [],
                        "summary": {"moved": 0, "skipped": 0, "errors": 0},
                    },
                    sort_keys=True,
                )
            )
        else:
            print("No archived tickets in active states found.")
        return 0

    # Cache canceled-state IDs per team so we don't re-fetch per ticket.
    canceled_state_cache: dict[str, str | None] = {}

    moved = 0
    skipped = 0
    errors = 0
    rows: list[str] = []
    ticket_records: list[dict[str, Any]] = []

    for issue in issues:
        identifier = issue.get("identifier", issue["id"])
        title = issue.get("title", "(no title)")
        old_state = (issue.get("state") or {}).get("name", "?")
        team_id = (issue.get("team") or {}).get("id", "")

        if team_id not in canceled_state_cache:
            try:
                canceled_state_cache[team_id] = _get_canceled_state_id(api_key, team_id)
            except RuntimeError as exc:
                canceled_state_cache[team_id] = None
                rows.append(f"  {identifier:<12} ERROR — canceled state lookup failed: {exc}")
                errors += 1
                ticket_records.append(
                    {
                        "id": identifier,
                        "title": title,
                        "from_state": old_state,
                        "action": "error",
                        "reason": "canceled_state_lookup_failed",
                    }
                )
                continue

        canceled_id = canceled_state_cache[team_id]
        if canceled_id is None:
            rows.append(f"  {identifier:<12} SKIP  — no Canceled state found for team")
            skipped += 1
            ticket_records.append(
                {
                    "id": identifier,
                    "title": title,
                    "from_state": old_state,
                    "action": "skipped",
                    "reason": "no_canceled_state",
                }
            )
            continue

        if dry_run:
            rows.append(f"  {identifier:<12} {old_state} -> Canceled  [{title[:60]}]")
            moved += 1
            ticket_records.append(
                {
                    "id": identifier,
                    "title": title,
                    "from_state": old_state,
                    "action": "would_cancel",
                }
            )
            continue

        try:
            ok = _cancel_issue(api_key, issue["id"], canceled_id)
            if ok:
                rows.append(f"  {identifier:<12} {old_state} -> Canceled  [{title[:60]}]")
                moved += 1
                ticket_records.append(
                    {
                        "id": identifier,
                        "title": title,
                        "from_state": old_state,
                        "action": "canceled",
                    }
                )
            else:
                rows.append(f"  {identifier:<12} WARN — issueUpdate returned success=false")
                errors += 1
                ticket_records.append(
                    {
                        "id": identifier,
                        "title": title,
                        "from_state": old_state,
                        "action": "error",
                        "reason": "success_false",
                    }
                )
        except Exception as exc:
            rows.append(f"  {identifier:<12} ERROR — {exc}")
            errors += 1
            ticket_records.append(
                {
                    "id": identifier,
                    "title": title,
                    "from_state": old_state,
                    "action": "error",
                    "reason": str(exc)[:200],
                }
            )

    if json_output:
        print(
            json.dumps(
                {
                    "dry_run": dry_run,
                    "tickets": ticket_records,
                    "summary": {"moved": moved, "skipped": skipped, "errors": errors},
                },
                sort_keys=True,
            )
        )
    else:
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
    """Load LINEAR_API_KEY from environment, optionally augmenting from .env."""
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if key:
        return key

    this_dir = Path(__file__).resolve().parent
    repo_root = this_dir.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv  # deferred; only needed when .env exists
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "python-dotenv is required to load .env files: pip install python-dotenv"
            ) from exc
        load_dotenv(env_path)
        key = os.environ.get("LINEAR_API_KEY", "").strip()

    if not key:
        raise RuntimeError("LINEAR_API_KEY is not set. Add it to .env or the environment.")
    return key


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cancel Linear tickets that are archived but still in active states."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute",
        action="store_true",
        help="Apply cancellations. Without this flag the script runs in dry-run (preview) mode.",
    )
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview only; make no changes (default behavior, kept for explicitness).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable JSON output instead of text.",
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
        run_hygiene(
            api_key,
            team_filter=args.team,
            dry_run=not args.execute,
            json_output=args.json_output,
        )
    except RuntimeError as exc:
        print(f"[hygiene] fatal: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
