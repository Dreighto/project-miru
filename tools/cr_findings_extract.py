#!/usr/bin/env python3
"""cr_findings_extract.py — extract CodeRabbit findings from a GitHub PR.

CodeRabbit posts findings as inline comments on PRs. The comment body
embeds severity (Major/Minor/Critical/Trivial), the affected file/line,
a description, and often a suggested diff. Parsing this from `gh api`
JSON via shell+jq is error-prone — this script does the parsing once
and emits clean JSON suitable for inspection or piping into other tools.

USAGE:

    # All findings on a PR, latest first
    python tools/cr_findings_extract.py 182

    # Only findings on a specific file
    python tools/cr_findings_extract.py 182 --path tools/los_10_filter_repo.sh

    # Only findings above a severity threshold (major/critical only)
    python tools/cr_findings_extract.py 182 --min-severity major

    # Only findings posted after a specific timestamp
    python tools/cr_findings_extract.py 182 --since 2026-05-11T01:00:00Z

    # Compact one-line-per-finding summary
    python tools/cr_findings_extract.py 182 --summary

OUTPUT (default JSON):

    [
      {
        "pr": 182,
        "comment_id": 12345,
        "path": "tools/los_10_filter_repo.sh",
        "line": 243,
        "severity": "MAJOR",
        "title": "Use absolute path for REPLACE_TEXT_FILE",
        "submitted_at": "2026-05-11T01:25:29Z",
        "url": "https://github.com/.../pull/182#discussion_r12345",
        "suggested_diff": "diff --git a/...\\n@@ ...\\n+absolute_path = ...",
        "body_excerpt": "REPLACE_TEXT_FILE was previously a relative path under $OUTPUT_DIR..."
      },
      ...
    ]

Severity normalization:
    "_⚠️ Potential issue_ | _🔴 Critical_" → CRITICAL
    "_⚠️ Potential issue_ | _🟠 Major_"    → MAJOR
    "_⚠️ Potential issue_ | _🟡 Minor_"    → MINOR
    "_🧹 Nitpick_ | _🔵 Trivial_"          → TRIVIAL

Exit codes:
    0 — findings printed (may be zero findings, that's fine)
    1 — error fetching from GitHub API
    2 — usage error
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any

# Force stdout to UTF-8 on Windows so curly quotes / emoji in CR's
# titles render correctly instead of as � (cp1252 fallback).
if sys.platform == "win32" and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SEVERITY_ORDER = ["TRIVIAL", "MINOR", "MAJOR", "CRITICAL"]
SEVERITY_RE = re.compile(
    r"\|\s*_[^_]*?(Critical|Major|Minor|Trivial)_\s*\|",
    re.IGNORECASE,
)

# CR's suggested-diff blocks are wrapped in <details>...</details> with a
# ```diff or ```suggestion fenced code block inside. Capture either.
DIFF_RE = re.compile(
    r"```(?:diff|suggestion)\n(.*?)```",
    re.DOTALL,
)

# The "title" of a finding is the first **bold-wrapped** line after the
# severity tag. Example: "**Use absolute path for REPLACE_TEXT_FILE.**"
TITLE_RE = re.compile(r"\*\*([^*\n]+)\*\*")


def _gh_api(endpoint: str) -> Any:
    """Run `gh api <endpoint>` and parse JSON. Raises SystemExit on failure.

    Uses bytes mode + explicit UTF-8 decode because gh's output can contain
    emoji / non-ASCII chars (CR uses emoji in severity tags) that crash
    Python's default cp1252 decoder on Windows.
    """
    try:
        result = subprocess.run(
            ["gh", "api", endpoint, "--paginate"],
            capture_output=True,
            text=False,  # bytes mode — we'll decode explicitly
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"[cr_findings_extract] gh api failed: {exc}", file=sys.stderr)
        sys.exit(1)
    if result.returncode != 0:
        stderr_text = (result.stderr or b"").decode("utf-8", errors="replace").strip()
        print(
            f"[cr_findings_extract] gh api {endpoint} returned {result.returncode}: "
            f"{stderr_text}",
            file=sys.stderr,
        )
        sys.exit(1)
    stdout_bytes = result.stdout or b""
    out = stdout_bytes.decode("utf-8", errors="replace").strip()
    if not out:
        return []
    # Split paginated arrays. `gh api --paginate` concatenates them like
    # "[...][...]". CR R1 finding on PR #188: the previous bracket-counter
    # approach treated `[` and `]` inside JSON strings as structural,
    # which corrupts the split when comment bodies contain markdown
    # link syntax or code-fenced bracket characters. The pagination of
    # CR's own comments has plenty of those, so the splitter silently
    # produced zero findings on certain PRs.
    #
    # Fix: use json.JSONDecoder.raw_decode to peel successive arrays off
    # the buffer. raw_decode parses one JSON value from a string and
    # returns (value, end_index). Iteratively skip whitespace, decode
    # one array, advance past it, repeat. Quote-aware by virtue of being
    # the real JSON parser.
    decoder = json.JSONDecoder()
    combined: list[Any] = []
    idx = 0
    while idx < len(out):
        # Skip whitespace between concatenated arrays
        while idx < len(out) and out[idx].isspace():
            idx += 1
        if idx >= len(out):
            break
        try:
            value, end = decoder.raw_decode(out, idx)
        except json.JSONDecodeError as exc:
            print(f"[cr_findings_extract] pagination decode failed: {exc}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(value, list):
            print(
                f"[cr_findings_extract] unexpected non-array JSON value at offset {idx}",
                file=sys.stderr,
            )
            sys.exit(1)
        combined.extend(value)
        idx = end
    return combined


def _parse_severity(body: str) -> str | None:
    """Extract severity from a CR comment body. Returns one of
    CRITICAL/MAJOR/MINOR/TRIVIAL, or None if not a CR finding."""
    m = SEVERITY_RE.search(body)
    if not m:
        return None
    return m.group(1).upper()


def _parse_title(body: str) -> str:
    """Best-effort title extraction. First **bold** line after the severity tag."""
    m = TITLE_RE.search(body)
    if m:
        return m.group(1).strip().rstrip(".")
    # Fallback: first non-empty line that isn't a markdown table or detail tag.
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith(("_", "<", "|", "```", "---")):
            return line[:120]
    return "(no title parseable)"


def _parse_suggested_diff(body: str) -> str | None:
    """Extract the first ```diff or ```suggestion block from a CR comment."""
    m = DIFF_RE.search(body)
    if m:
        return m.group(1).strip()
    return None


def _parse_iso8601_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Accepts the trailing-Z form GitHub returns (``2026-05-11T01:25:29Z``)
    as well as explicit offsets like ``2026-05-11T01:25:29+00:00``. Raises
    ``ValueError`` on anything else. CR R3 finding on PR #188 — the old
    code compared raw strings, which silently dropped findings whenever
    the two sides had different offset suffixes (e.g. ``...Z`` vs
    ``...+00:00``) and gave the lexicographic illusion of a working filter.
    """
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        # Bare local-time strings are ambiguous; refuse them so the caller
        # can't accidentally drop findings whose timestamps are UTC just
        # because the operator forgot to write the offset.
        raise ValueError(f"timestamp lacks timezone: {value!r}")
    return dt.astimezone(UTC)


def fetch_findings(
    pr: int,
    repo: str,
    *,
    path_filter: str | None = None,
    min_severity: str | None = None,
    since_dt: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch all CodeRabbit findings on a PR. Filters applied post-fetch.

    ``since_dt`` is a parsed timezone-aware datetime (validated upstream
    by ``_parse_iso8601_utc``). Comparing parsed datetimes rather than
    raw ISO strings avoids the offset-suffix mismatch bug. CR R3 finding.
    """
    comments = _gh_api(f"repos/{repo}/pulls/{pr}/comments")
    findings: list[dict[str, Any]] = []
    min_sev_idx = SEVERITY_ORDER.index(min_severity.upper()) if min_severity else -1

    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") != "coderabbitai[bot]":
            continue

        body = comment.get("body") or ""
        severity = _parse_severity(body)
        if severity is None:
            # Not a finding-shaped comment (could be a status summary, etc.)
            continue

        if min_sev_idx >= 0:
            try:
                if SEVERITY_ORDER.index(severity) < min_sev_idx:
                    continue
            except ValueError:
                continue  # unknown severity, skip

        if since_dt is not None:
            created_raw = comment.get("created_at") or ""
            if not created_raw:
                continue  # no timestamp; conservatively exclude
            try:
                created_dt = _parse_iso8601_utc(created_raw)
            except ValueError:
                continue  # malformed timestamp on the comment side
            if created_dt < since_dt:
                continue

        path = comment.get("path") or ""
        if path_filter and path != path_filter:
            continue

        findings.append(
            {
                "pr": pr,
                "comment_id": comment.get("id"),
                "path": path,
                "line": comment.get("line"),
                "severity": severity,
                "title": _parse_title(body),
                "submitted_at": comment.get("created_at"),
                "url": comment.get("html_url"),
                "suggested_diff": _parse_suggested_diff(body),
                "body_excerpt": (body[:500] + "…") if len(body) > 500 else body,
            }
        )

    # Newest first
    findings.sort(key=lambda f: f["submitted_at"] or "", reverse=True)
    return findings


def _print_summary(findings: list[dict[str, Any]]) -> None:
    """One-line-per-finding summary suitable for human eyeball review."""
    if not findings:
        print("(no CR findings)")
        return
    for f in findings:
        line = f["line"] if f["line"] is not None else "?"
        print(
            f"[{f['severity']:8s}] {f['path']}:{line}  {f['title']}  " f"({f['submitted_at'][:16]})"
        )


class RepoAutodetectError(Exception):
    """Raised when `gh repo view` cannot determine the current repo.

    The previous behavior was to silently return a hardcoded
    "Dreighto/project-miru" fallback, which made the script appear to
    work but actually queried the wrong repo whenever it was run from
    outside Miru. Failing fast forces the operator to pass --repo
    explicitly instead of getting confusing "no findings" output on a
    repo they didn't realize was being substituted. CR R3 finding on PR #188.
    """


def _detect_repo() -> str:
    """Read the current repo's GitHub owner/name from `gh repo view`.

    Raises RepoAutodetectError if gh is missing, the call errors, or the
    output is empty. The caller (main) catches and instructs the
    operator to pass --repo. No silent fallback — that previously hid
    "wrong repo" bugs that produced confusing zero-finding output.
    """
    try:
        result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise RepoAutodetectError(f"gh invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise RepoAutodetectError(
            f"gh repo view returned {result.returncode}: "
            f"{(result.stderr or '').strip() or '(no stderr)'}"
        )
    repo = (result.stdout or "").strip()
    if not repo:
        raise RepoAutodetectError("gh repo view returned empty output")
    return repo


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "CR findings extractor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("pr", type=int, help="PR number (e.g. 182)")
    p.add_argument(
        "--repo",
        default=None,
        help="GitHub owner/name (default: auto-detect from current repo)",
    )
    p.add_argument(
        "--path",
        default=None,
        help="Only findings on this file path",
    )
    p.add_argument(
        "--min-severity",
        choices=[s.lower() for s in SEVERITY_ORDER],
        default=None,
        help="Only findings at or above this severity",
    )
    p.add_argument(
        "--since",
        default=None,
        help="Only findings submitted after this ISO timestamp (e.g. 2026-05-11T01:00:00Z)",
    )
    p.add_argument(
        "--summary",
        action="store_true",
        help="One-line-per-finding summary instead of full JSON",
    )

    args = p.parse_args(argv)

    # Repo resolution: explicit --repo wins; otherwise autodetect, and
    # fail loud on autodetect failure rather than silently substituting
    # a default (which previously produced confusing "no findings" output
    # when the script was run outside the intended repo). CR R3.
    if args.repo:
        repo = args.repo
    else:
        try:
            repo = _detect_repo()
        except RepoAutodetectError as exc:
            print(
                f"[cr_findings_extract] could not autodetect repo: {exc}\n"
                f"[cr_findings_extract] pass --repo OWNER/NAME explicitly.",
                file=sys.stderr,
            )
            return 2

    # --since parsing: validate once, up front, in main() so a bad value
    # exits with a clear usage error rather than silently filtering
    # nothing. CR R3 finding — string-comparing ISO timestamps with
    # different offsets (`...Z` vs `...+00:00`) gave the lexicographic
    # illusion of filtering while quietly dropping matching findings.
    since_dt = None
    if args.since:
        try:
            since_dt = _parse_iso8601_utc(args.since)
        except ValueError as exc:
            print(
                f"[cr_findings_extract] invalid --since timestamp {args.since!r}: {exc}\n"
                f"[cr_findings_extract] expected ISO-8601 with timezone, e.g. "
                f"2026-05-11T01:00:00Z or 2026-05-11T01:00:00+00:00",
                file=sys.stderr,
            )
            return 2

    findings = fetch_findings(
        args.pr,
        repo,
        path_filter=args.path,
        min_severity=args.min_severity,
        since_dt=since_dt,
    )

    if args.summary:
        _print_summary(findings)
    else:
        print(json.dumps(findings, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
