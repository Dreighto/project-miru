"""DGAS Tier 2 #9: governance file registry — PR description gate.

A "governance file" is one that, if changed, alters what workers (or future
workers) are permitted to do. The registry pattern (see synthesis item #6)
treats those changes differently from code changes:

    1. They cannot be self-merged by a worker.
    2. The PR description must explicitly opt in via
       ``GOVERNANCE_CHANGE_APPROVED=true``.
    3. The PR description must include a section titled
       ``What does this allow that wasn't allowed before?`` with non-empty
       prose explaining the trust-surface delta.

Without those, the change is a "rules about rules" delta the operator never
explicitly accepted. The fail-closed default protects the trust surface.

This script is the deterministic check. It runs as a GitHub Action step
(see ``.github/workflows/governance-check.yml``) and exits non-zero when the
registry is touched but the PR description doesn't carry the explicit opt-in.

Usage:
    python tools/check_governance_change.py \\
        --changed-files <newline-separated paths> \\
        --pr-body <PR description as a single string>

For local testing or CLI exploration, both can come from environment
variables:
    MIRU_GOV_CHANGED_FILES   newline-separated list
    MIRU_GOV_PR_BODY         PR description text

Exit codes:
    0 — no governance file touched, OR all opt-ins present
    1 — governance file touched but the PR body is missing required fields
    2 — script error (unparseable input)
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# The canonical governance file registry. Each entry is matched against the
# repo-relative path of every file in the PR diff. Patterns supporting `*`
# globs are interpreted as fnmatch, while exact paths are matched literally.
#
# Source: synthesis item #6 (data/peer_reviews/2026-05-08_dgas_three_way_synthesis.md)
GOVERNANCE_PATTERNS: tuple[str, ...] = (
    # Gateway profile + dispatch validation
    "tools/miru_mcp_gateway/profiles.py",
    "gatekeeper/**",
    # Instruction architecture overlays + reference data
    ".miru/overlays/**",
    ".miru/reference/**",
    ".miru/instruction_manifest.json",
    # Worker rule canon read on every dispatch (CLAUDE.md sec. Repo Boundary):
    # team-charter.md, lane charter, role briefs. Modifying these silently
    # would propagate to every worker session — same trust surface as the
    # overlay/reference files above. Added 2026-05-12 after the LOS-34/35
    # untie-from-miru sweep flagged the gap.
    "miru-context/**",
    # Pre-commit configuration (hygiene gate definition)
    ".pre-commit-config.yaml",
    # Validator + check scripts (the gates themselves)
    "tools/check_*.py",
    "tools/validate_*.py",
    # Ingress classifier rule set (drives worker profile assignment)
    "data/config/w2_profile_rules.json",
    # The governance check itself — recursive protection
    "tools/check_governance_change.py",
    ".github/workflows/governance-check.yml",
    ".github/CODEOWNERS",
)

# Required PR body fields. Both must be present (case-insensitive) AND the
# explanation section must have non-whitespace content under it.
APPROVAL_TOKEN = "GOVERNANCE_CHANGE_APPROVED=true"
# Tolerate CRLF line endings on the trailing `$`. GitHub stores PR-body
# newlines as CRLF; when the `${{ github.event.pull_request.body }}` YAML
# context expands into env, the CRLFs survive. Python's `re.MULTILINE` $
# matches before `\n` but does NOT consume a preceding `\r`, so the strict
# anchor wouldn't match. `\r?$` is the cheap fix — equivalent to the
# original on LF input, tolerant of CRLF, and zero false-positive surface.
EXPLANATION_HEADING_RE = re.compile(
    r"(?im)^##\s*what\s+does\s+this\s+allow\s+that\s+wasn['’]t\s+allowed\s+before\??\r?$"  # noqa: RUF001
)


def _normalize_path(p: str) -> str:
    """Repo-relative path with forward slashes.

    Strips a leading ``./`` if present (e.g., ``./tools/foo.py`` → ``tools/foo.py``)
    but preserves leading dots in dotfile paths like ``.pre-commit-config.yaml``
    or ``.github/CODEOWNERS``.
    """
    norm = p.strip().replace("\\", "/")
    if norm.startswith("./"):
        norm = norm[2:]
    return norm


def matches_registry(path: str, patterns: tuple[str, ...] = GOVERNANCE_PATTERNS) -> str | None:
    """Return the matching pattern if ``path`` is in the governance registry.

    Pattern semantics:
        * Exact path (no glob chars) — literal match against the normalized path.
        * ``prefix/**`` — matches any path equal to ``prefix`` or starting with
          ``prefix/`` at any nested depth.
        * Single-segment glob like ``tools/check_*.py`` — matches files in that
          one directory whose name matches the wildcard.
    """
    import fnmatch

    norm = _normalize_path(path)
    for pattern in patterns:
        # Recursive prefix glob: "foo/**" matches foo, foo/x, foo/x/y, ...
        if pattern.endswith("/**"):
            prefix = pattern[:-3]
            if norm == prefix or norm.startswith(prefix + "/"):
                return pattern
            continue
        # Single-segment glob: "tools/check_*.py" matches one path level
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatchcase(norm, pattern):
                return pattern
            continue
        # Exact path
        if norm == pattern:
            return pattern
    return None


def find_governance_files(changed_files: list[str]) -> list[tuple[str, str]]:
    """Return [(path, matching_pattern), ...] for every governance hit."""
    hits: list[tuple[str, str]] = []
    for f in changed_files:
        match = matches_registry(f)
        if match:
            hits.append((_normalize_path(f), match))
    return hits


def check_pr_body(pr_body: str) -> list[str]:
    """Return a list of validation errors. Empty list = OK."""
    errors: list[str] = []
    if APPROVAL_TOKEN not in pr_body:
        errors.append(f"PR body must contain the literal string {APPROVAL_TOKEN!r}")
    heading_match = EXPLANATION_HEADING_RE.search(pr_body)
    if heading_match is None:
        errors.append(
            "PR body must contain a section titled "
            "'## What does this allow that wasn’t allowed before?'"  # noqa: RUF001
        )
    else:
        # The section must have non-whitespace content beneath it (before the
        # next heading or end of file).
        start = heading_match.end()
        rest = pr_body[start:]
        next_heading = re.search(r"^##\s", rest, flags=re.MULTILINE)
        section_body = rest[: next_heading.start()] if next_heading else rest
        if not section_body.strip():
            errors.append(
                "The 'What does this allow...' section is empty. "
                "Explain the trust-surface delta in plain English."
            )
    return errors


def _read_changed_files_arg(arg_value: str | None) -> list[str]:
    if arg_value is None:
        return []
    if arg_value.startswith("@"):
        # Read from a file, one path per line. Convenient for CI where the
        # diff is large.
        path = Path(arg_value[1:])
        if not path.is_file():
            raise ValueError(f"--changed-files path not found: {path}")
        return [
            line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]
    return [line.strip() for line in arg_value.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Governance change registry gate.")
    parser.add_argument(
        "--changed-files",
        default=None,
        help=(
            "Newline-separated repo-relative paths. Prefix with @ to read from a "
            "file. Defaults to MIRU_GOV_CHANGED_FILES env var."
        ),
    )
    parser.add_argument(
        "--pr-body",
        default=None,
        help="PR description text. Defaults to MIRU_GOV_PR_BODY env var.",
    )
    args = parser.parse_args()

    raw_changed = args.changed_files
    if raw_changed is None:
        raw_changed = os.environ.get("MIRU_GOV_CHANGED_FILES", "")

    pr_body = args.pr_body
    if pr_body is None:
        pr_body = os.environ.get("MIRU_GOV_PR_BODY", "")

    try:
        changed = _read_changed_files_arg(raw_changed)
    except ValueError as exc:
        print(f"governance-check: error — {exc}", file=sys.stderr)
        return 2

    hits = find_governance_files(changed)
    if not hits:
        print("governance-check: no governance files touched. OK.", file=sys.stderr)
        return 0

    print(
        f"governance-check: {len(hits)} governance file(s) touched:",
        file=sys.stderr,
    )
    for path, pattern in hits:
        print(f"  - {path} (matched {pattern})", file=sys.stderr)

    errors = check_pr_body(pr_body)
    if errors:
        print("governance-check: PR description is missing required fields:", file=sys.stderr)
        for err in errors:
            print(f"  ! {err}", file=sys.stderr)
        print(
            "\nWhen a governance file changes, the PR body must explicitly opt in.\n"
            f"Add the literal token {APPROVAL_TOKEN!r} (operator-side acknowledgment)\n"
            "and a markdown section explaining what the change permits that wasn't\n"
            "permitted before. See synthesis item #6 for context.",
            file=sys.stderr,
        )
        # Paste-ready PR body addition. Concrete file paths inlined so the
        # operator (or the next worker) doesn't have to re-derive what was
        # touched — they can copy the block, fill the two <placeholders>,
        # and re-push the PR body via `gh pr edit <N> --body ...`.
        files_list = "\n".join(f"- `{path}`" for path, _pattern in hits)
        print(
            "\n---\nPaste this into your PR body (replace each <placeholder> with"
            " concrete prose, then `gh pr edit <PR_NUMBER> --body \"$(cat <<'EOF'"
            ' ... EOF)"`):\n'
            "\n"
            "================================================================\n"
            f"{APPROVAL_TOKEN}\n"
            "\n"
            "## What does this allow that wasn't allowed before?\n"
            "\n"
            "The changes to:\n"
            f"{files_list}\n"
            "permit <what new behavior, trust expansion, or rule loosening this enables>.\n"
            "Before this PR the rule was <prior state>; after this PR <new state>.\n"
            "This is necessary because <why the operator needs this expansion>.\n"
            "================================================================\n"
            "\n"
            "Note: if the change is a pure documentation / canon expansion that\n"
            "doesn't loosen any rule, say so explicitly — that still counts as a\n"
            "valid explanation and satisfies the gate.",
            file=sys.stderr,
        )
        return 1

    print(
        "governance-check: PR description carries explicit governance approval. OK.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"governance-check: script error — {exc}", file=sys.stderr)
        sys.exit(2)
