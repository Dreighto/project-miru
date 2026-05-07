"""Prune local branches whose PRs have been merged on GitHub.

Safe force-delete for squash-merged branches: verifies via `gh pr list`
that a merged PR exists before deleting. Skips branches checked out in
worktrees and protected branch patterns.
"""

import argparse
import json
import os
import re
import subprocess
import sys

PROTECTED_PATTERNS = [
    r"^main$",
    r"^develop$",
    r"^_parking_",
]

DEFAULT_REPO = "Dreighto/project-miru"


def _repo_root() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        cwd=script_dir,
        timeout=5,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    return os.path.dirname(script_dir)


def _is_protected(branch: str) -> bool:
    return any(re.search(pat, branch) for pat in PROTECTED_PATTERNS)


def _git_fetch_prune(cwd: str) -> bool:
    result = subprocess.run(
        ["git", "fetch", "--prune", "origin"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
        check=False,
    )
    return result.returncode == 0


def list_local_branches(cwd: str) -> list[dict] | None:
    """Return local branches with tracking status, or None on failure.

    Each entry: {name, in_worktree, remote_gone, tracking}
    """
    result = subprocess.run(
        ["git", "branch", "-vv", "--no-color"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return None

    branches = []
    for line in result.stdout.splitlines():
        line = line.rstrip()
        if not line:
            continue

        in_worktree = line.startswith("+")
        is_current = line.startswith("*")

        name = line[2:].split()[0] if len(line) > 2 else ""
        if not name:
            continue

        remote_gone = ": gone]" in line

        tracking_match = re.search(r"\[([^\]]+)\]", line)
        tracking = tracking_match.group(1) if tracking_match else None

        branches.append(
            {
                "name": name,
                "in_worktree": in_worktree,
                "is_current": is_current,
                "remote_gone": remote_gone,
                "tracking": tracking,
            }
        )

    return branches


def verify_pr_merged(branch: str, repo: str) -> tuple[dict | None, str | None]:
    """Check GitHub for a merged PR from this branch.

    Returns (pr_info, error). On success: (dict, None) or (None, None).
    On command failure: (None, error_message).
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                branch,
                "--state",
                "merged",
                "--json",
                "number,title,mergedAt,headRefOid",
                "--limit",
                "1",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            return None, f"gh pr list failed (exit {result.returncode})"
        prs = json.loads(result.stdout)
        if prs:
            return prs[0], None
    except FileNotFoundError:
        return None, "gh CLI not found"
    except subprocess.TimeoutExpired:
        return None, "gh pr list timed out"
    except json.JSONDecodeError:
        return None, "gh pr list returned invalid JSON"
    return None, None


def _local_branch_tip(branch: str, cwd: str) -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", branch],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def delete_local_branch(branch: str, cwd: str) -> bool:
    result = subprocess.run(
        ["git", "branch", "-D", branch],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=10,
        check=False,
    )
    return result.returncode == 0


def find_candidates(branches: list[dict]) -> list[dict]:
    """Filter branches to deletion candidates.

    A candidate is a branch where:
    - Remote tracking ref is gone (GitHub deleted it after merge)
    - Not checked out in any worktree
    - Not the current branch
    - Not matching a protected pattern
    """
    candidates = []
    for b in branches:
        if b["is_current"]:
            continue
        if b["in_worktree"]:
            continue
        if _is_protected(b["name"]):
            continue
        if b["remote_gone"]:
            candidates.append(b)
    return candidates


def prune(
    repo: str = DEFAULT_REPO,
    dry_run: bool = True,
    include_untracked: bool = False,
    cwd: str | None = None,
) -> list[dict]:
    """Main prune logic.

    Returns list of action records:
      {branch, action, pr_number, pr_title, reason}
    """
    if cwd is None:
        cwd = _repo_root()

    print("[prune] fetching and pruning remote refs...", file=sys.stderr)
    if not _git_fetch_prune(cwd):
        print(
            "[prune] git fetch --prune failed; aborting to avoid stale state",
            file=sys.stderr,
        )
        return [
            {
                "branch": None,
                "action": "failed",
                "pr_number": None,
                "pr_title": None,
                "reason": "git fetch --prune origin failed",
            }
        ]

    branches = list_local_branches(cwd)
    if branches is None:
        print(
            "[prune] git branch -vv failed; aborting",
            file=sys.stderr,
        )
        return [
            {
                "branch": None,
                "action": "failed",
                "pr_number": None,
                "pr_title": None,
                "reason": "git branch -vv failed",
            }
        ]

    candidates = find_candidates(branches)

    if not candidates:
        print("[prune] no candidates found", file=sys.stderr)
        return []

    print(
        f"[prune] found {len(candidates)} candidate(s) with remote gone",
        file=sys.stderr,
    )

    actions = []
    for b in candidates:
        branch = b["name"]

        pr, err = verify_pr_merged(branch, repo)
        if err is not None:
            actions.append(
                {
                    "branch": branch,
                    "action": "failed",
                    "pr_number": None,
                    "pr_title": None,
                    "reason": err,
                }
            )
            print(
                f"  FAIL  {branch} — {err}",
                file=sys.stderr,
            )
            continue
        if pr is None:
            actions.append(
                {
                    "branch": branch,
                    "action": "skipped",
                    "pr_number": None,
                    "pr_title": None,
                    "reason": "no merged PR found on GitHub",
                }
            )
            print(
                f"  SKIP  {branch} — no merged PR found",
                file=sys.stderr,
            )
            continue

        pr_head = pr.get("headRefOid")
        local_tip = _local_branch_tip(branch, cwd)
        if pr_head and local_tip and local_tip != pr_head:
            actions.append(
                {
                    "branch": branch,
                    "action": "skipped",
                    "pr_number": pr["number"],
                    "pr_title": pr["title"],
                    "reason": "local branch diverged from merged PR head",
                }
            )
            print(
                f"  SKIP  {branch} — local tip {local_tip[:8]} != PR head {pr_head[:8]}",
                file=sys.stderr,
            )
            continue

        if dry_run:
            actions.append(
                {
                    "branch": branch,
                    "action": "would_delete",
                    "pr_number": pr["number"],
                    "pr_title": pr["title"],
                    "reason": f"merged PR #{pr['number']}",
                }
            )
            print(
                f"  DRY   {branch} — would delete (PR #{pr['number']})",
                file=sys.stderr,
            )
        else:
            success = delete_local_branch(branch, cwd)
            action = "deleted" if success else "failed"
            actions.append(
                {
                    "branch": branch,
                    "action": action,
                    "pr_number": pr["number"],
                    "pr_title": pr["title"],
                    "reason": f"merged PR #{pr['number']}",
                }
            )
            label = "DEL " if success else "FAIL"
            print(
                f"  {label}  {branch} — PR #{pr['number']}",
                file=sys.stderr,
            )

    return actions


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune local branches whose PRs have been merged.")
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help="GitHub repo (owner/name). Default: %(default)s",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="List what would be deleted without deleting.",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        default=False,
        help="Print results as JSON to stdout.",
    )
    args = parser.parse_args()

    actions = prune(repo=args.repo, dry_run=args.dry_run)

    deleted = [a for a in actions if a["action"] == "deleted"]
    skipped = [a for a in actions if a["action"] == "skipped"]
    failed = [a for a in actions if a["action"] == "failed"]
    dry = [a for a in actions if a["action"] == "would_delete"]

    if args.json_output:
        print(json.dumps(actions, indent=2))

    print(file=sys.stderr)
    if args.dry_run:
        print(f"[prune] {len(dry)} would be deleted, {len(skipped)} skipped", file=sys.stderr)
    else:
        print(
            f"[prune] {len(deleted)} deleted, {len(skipped)} skipped, {len(failed)} failed",
            file=sys.stderr,
        )

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
