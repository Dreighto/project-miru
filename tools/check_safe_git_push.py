"""DGAS Tier 2 #8: pre-push hook that refuses force-push and branch-delete on protected branches.

Defense-in-depth alongside the GitHub branch protection ruleset on `main`:

* GitHub branch protection rejects force-push at the SERVER side.
* This hook rejects force-push at the LOCAL side, BEFORE the network call,
  so the operator gets fast feedback and never accidentally tries to
  force-push from a misconfigured remote (a fork, a Tailscale-tunneled
  test instance, etc).

The hook reads git's standard pre-push stdin format, one line per ref pair:

    <local-ref> SP <local-sha> SP <remote-ref> SP <remote-sha> LF

For each ref pair:

* If the remote ref matches a protected branch pattern (`main` / `master`
  / `release/*`), and either:
    - the local-sha is all zeros (delete), OR
    - the local-sha is NOT a descendant of remote-sha (force-push),
  the hook exits 1 and prints the reason.
* Otherwise allows the push.

Bypass via `git push --no-verify` is intentionally available for emergency
hotfixes — same policy as the rest of the pre-commit hygiene gates per
CLAUDE.md. Use logged in the commit message.

Run as:
    python tools/check_safe_git_push.py <remote-name> <remote-url>

stdin: the standard git pre-push ref pairs.
exit:  0 = allow push, 1 = refuse, 2 = script error.
"""

from __future__ import annotations

import re
import subprocess
import sys

# 40-char zero hash that git uses to mean "no such ref."
NULL_SHA = "0" * 40

# Protected branches — exact names or simple `prefix/*` glob form.
# main + master cover the canonical default-branch names. release/* covers
# release branches in case the project ever ships them.
_PROTECTED_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^refs/heads/main$"),
    re.compile(r"^refs/heads/master$"),
    re.compile(r"^refs/heads/release/.+$"),
)


def _is_protected(remote_ref: str) -> bool:
    return any(p.match(remote_ref) for p in _PROTECTED_PATTERNS)


def is_descendant(local_sha: str, remote_sha: str) -> bool:
    """True if local_sha is a fast-forward (or equal) of remote_sha.

    `git merge-base remote local` returns remote_sha iff remote_sha is an
    ancestor of local_sha (i.e. local is a descendant). Anything else means
    a force-push is required.
    """
    try:
        result = subprocess.run(
            ["git", "merge-base", remote_sha, local_sha],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    if result.returncode != 0:
        return False
    base = result.stdout.strip()
    return base == remote_sha


def is_protected(remote_ref: str) -> bool:
    """Public alias for the protected-branch check (kept for testability)."""
    return _is_protected(remote_ref)


def check_ref_pair(local_ref: str, local_sha: str, remote_ref: str, remote_sha: str) -> str | None:
    """Return None if the push is safe, or a refusal message if it should be blocked."""
    if not is_protected(remote_ref):
        return None

    # Delete: local sha is all zeros and remote is a real sha.
    if local_sha == NULL_SHA and remote_sha != NULL_SHA:
        return (
            f"refused: cannot delete protected branch {remote_ref!r}. "
            f"Use --no-verify only for emergency hotfixes (and log the bypass)."
        )

    # New branch (no remote): allow.
    if remote_sha == NULL_SHA:
        return None

    # Force-push: local is not a descendant of remote.
    if not is_descendant(local_sha, remote_sha):
        return (
            f"refused: force-push to protected branch {remote_ref!r} "
            f"(local {local_sha[:10]} is not a descendant of remote {remote_sha[:10]}). "
            f"Use --no-verify only for emergency hotfixes (and log the bypass)."
        )

    return None


def main(argv: list[str]) -> int:
    # Pre-commit's pre-push stage forwards (remote-name, remote-url) as
    # argv. We don't actually use them for the check — we only care about
    # ref pairs on stdin — but we accept them so the script is a drop-in.
    _ = argv

    # If pre-commit invokes us with no stdin (manual `pre-commit run` against
    # files), there's nothing to validate. Exit 0 cleanly so it doesn't block
    # ad-hoc runs.
    if sys.stdin.isatty():
        return 0

    refusals: list[str] = []
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            # Malformed input — refuse safely so we don't silently allow
            # a push we couldn't parse.
            refusals.append(f"refused: malformed pre-push input: {line!r}")
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts
        result = check_ref_pair(local_ref, local_sha, remote_ref, remote_sha)
        if result is not None:
            refusals.append(result)

    if refusals:
        for msg in refusals:
            print(f"[safe-git-push] {msg}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"[safe-git-push] script error: {exc}", file=sys.stderr)
        sys.exit(2)


# Re-export for tests and external tools.
PROTECTED_PATTERNS = _PROTECTED_PATTERNS
__all__ = [
    "NULL_SHA",
    "PROTECTED_PATTERNS",
    "check_ref_pair",
    "main",
]
