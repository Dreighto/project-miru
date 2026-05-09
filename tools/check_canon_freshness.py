"""Canon freshness check (PRO-337).

Walks the worker-read canon file list and verifies each file's last-reviewed
date stamp is within the freshness threshold. Stale canon means the next
session reads the wrong truth — this gate catches drift before workers
operate on stale assumptions.

Trigger: 2026-05-09 incident where GPT (operator-relayed) still believed CH
was the Router/Orchestrator three days after that role transitioned to CC +
Hermes. Stale canon was the root cause. Codified in
`feedback_canon_refresh_cadence` memory: refresh every 3 days OR after any
major ship — whichever comes first.

Usage:
    python tools/check_canon_freshness.py
    python tools/check_canon_freshness.py --threshold 7
    python tools/check_canon_freshness.py --warn-threshold 5
    python tools/check_canon_freshness.py --json   # machine-readable output

Environment:
    CANON_FRESHNESS_DAYS         — overrides --threshold (default 7)
    CANON_FRESHNESS_WARN_DAYS    — overrides --warn-threshold (default 5)

Exit codes:
    0 — all canon files are fresh (within threshold) OR in the warn zone
    1 — one or more canon files are stale (beyond threshold) OR has a missing
        or malformed date stamp
    2 — script error (unparseable input, IO failure)

Recognized date-field names (case-insensitive, first match wins):
    Last reviewed, Last synced, Last updated, Effective

Files checked:
    CLAUDE.md
    AGENTS.md
    miru-context/team-charter.md
    .miru/overlays/*.md
    .miru/reference/*.md

Files explicitly excluded:
    data/peer_reviews/*       (local research bundles, not canon)
    .miru/instruction_manifest.json   (machine-readable index, no stamp)
    Worker-specific rule files (GEMINI.md, CURSOR.md, CODEX.md) — separate
    follow-up if needed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

DEFAULT_THRESHOLD_DAYS = 7
DEFAULT_WARN_DAYS = 5

# Canon files (repo-relative paths). Globs supported.
CANON_FILES: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "miru-context/team-charter.md",
    ".miru/overlays/*.md",
    ".miru/reference/*.md",
)

# Recognized date-field names. First match wins. Case-insensitive.
# Format: <field-name>: <YYYY-MM-DD>[ <optional trailing text>]
DATE_FIELD_NAMES: tuple[str, ...] = (
    "Last reviewed",
    "Last synced",
    "Last updated",
    "Effective",
)

# Build the regex once. Matches: "Last reviewed: 2026-05-09" or
# "Last reviewed: 2026-05-09 (verified...)" — captures only the date.
DATE_FIELD_RE = re.compile(
    r"^\s*(?P<field>"
    + "|".join(re.escape(n) for n in DATE_FIELD_NAMES)
    + r")\s*:\s*(?P<date>\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE | re.MULTILINE,
)


# ----------------------------------------------------------------------------
# Data classes
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FileResult:
    path: str
    status: str  # "fresh" | "warn" | "stale" | "missing_stamp" | "bad_date"
    field_name: str | None
    stamp_date: date | None
    days_old: int | None
    detail: str  # Human-readable explanation


# ----------------------------------------------------------------------------
# Core logic
# ----------------------------------------------------------------------------


def _today() -> date:
    """Today's date in UTC. Indirection for testability."""
    return datetime.now(UTC).date()


def _resolve_canon_files(repo_root: Path) -> list[Path]:
    """Expand globs in CANON_FILES into a sorted list of actual files.

    Files that don't exist are silently skipped — a glob like
    .miru/overlays/*.md won't fail just because no overlays exist.
    """
    resolved: list[Path] = []
    for pattern in CANON_FILES:
        if "*" in pattern:
            resolved.extend(sorted(repo_root.glob(pattern)))
        else:
            target = repo_root / pattern
            if target.is_file():
                resolved.append(target)
    return resolved


def _check_file(path: Path, today: date, threshold: int, warn_threshold: int) -> FileResult:
    """Inspect one canon file and produce a FileResult."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return FileResult(
            path=str(path),
            status="bad_date",
            field_name=None,
            stamp_date=None,
            days_old=None,
            detail=f"could not read file: {exc}",
        )

    # Search only the first 30 lines (front-matter zone). The stamp belongs
    # at the top; scanning the whole file invites false positives from
    # body content quoting dates.
    head = "\n".join(text.splitlines()[:30])
    match = DATE_FIELD_RE.search(head)
    if not match:
        return FileResult(
            path=str(path),
            status="missing_stamp",
            field_name=None,
            stamp_date=None,
            days_old=None,
            detail=(
                "no recognized date field in first 30 lines; expected one of: "
                + ", ".join(DATE_FIELD_NAMES)
            ),
        )

    field_name = match.group("field")
    raw_date = match.group("date")
    try:
        stamp = date.fromisoformat(raw_date)
    except ValueError as exc:
        return FileResult(
            path=str(path),
            status="bad_date",
            field_name=field_name,
            stamp_date=None,
            days_old=None,
            detail=f"unparseable date {raw_date!r}: {exc}",
        )

    days_old = (today - stamp).days
    if days_old < 0:
        # Future-dated stamp — suspicious but not a failure. Treat as fresh,
        # but flag in the detail.
        return FileResult(
            path=str(path),
            status="fresh",
            field_name=field_name,
            stamp_date=stamp,
            days_old=days_old,
            detail=f"future-dated ({raw_date}); treating as fresh",
        )
    if days_old > threshold:
        return FileResult(
            path=str(path),
            status="stale",
            field_name=field_name,
            stamp_date=stamp,
            days_old=days_old,
            detail=f"{days_old} days old (threshold {threshold})",
        )
    if days_old >= warn_threshold:
        return FileResult(
            path=str(path),
            status="warn",
            field_name=field_name,
            stamp_date=stamp,
            days_old=days_old,
            detail=f"{days_old} days old (warn at {warn_threshold}, fail at {threshold})",
        )
    return FileResult(
        path=str(path),
        status="fresh",
        field_name=field_name,
        stamp_date=stamp,
        days_old=days_old,
        detail=f"{days_old} days old",
    )


def check_canon_freshness(
    repo_root: Path,
    threshold: int = DEFAULT_THRESHOLD_DAYS,
    warn_threshold: int = DEFAULT_WARN_DAYS,
    today: date | None = None,
) -> list[FileResult]:
    """Scan all canon files and return a list of FileResult.

    Pure function — no side effects, no exit. The caller decides what to do
    with the results (CLI prints + exits, tests assert on the list).
    """
    today = today or _today()
    files = _resolve_canon_files(repo_root)
    return [_check_file(p, today, threshold, warn_threshold) for p in files]


# ----------------------------------------------------------------------------
# CLI rendering
# ----------------------------------------------------------------------------


def _render_text(results: list[FileResult], threshold: int, warn_threshold: int) -> str:
    """Human-readable report. Sorted: stale/missing first, then warn, then fresh."""
    order = {"stale": 0, "missing_stamp": 1, "bad_date": 2, "warn": 3, "fresh": 4}
    results = sorted(results, key=lambda r: (order.get(r.status, 99), -(r.days_old or 0)))

    lines: list[str] = []
    lines.append(
        f"canon-freshness: {len(results)} file(s) checked "
        f"(fail at >{threshold} days, warn at >={warn_threshold} days)"
    )
    lines.append("")
    for r in results:
        marker = {
            "stale": "FAIL",
            "missing_stamp": "FAIL",
            "bad_date": "FAIL",
            "warn": "WARN",
            "fresh": "OK  ",
        }[r.status]
        date_part = (
            f"  ({r.field_name}: {r.stamp_date.isoformat()})" if r.stamp_date else f"  ({r.detail})"
        )
        lines.append(f"  [{marker}] {r.path}{date_part}")
    return "\n".join(lines) + "\n"


def _render_json(results: list[FileResult]) -> str:
    payload = [
        {
            "path": r.path,
            "status": r.status,
            "field_name": r.field_name,
            "stamp_date": r.stamp_date.isoformat() if r.stamp_date else None,
            "days_old": r.days_old,
            "detail": r.detail,
        }
        for r in results
    ]
    return json.dumps(payload, indent=2) + "\n"


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------


def _resolve_repo_root() -> Path:
    """Find the repo root. Prefer git, fall back to walking up from this file."""
    here = Path(__file__).resolve()
    # tools/check_canon_freshness.py → repo root is two parents up
    candidate = here.parent.parent
    if (candidate / "CLAUDE.md").is_file():
        return candidate
    # Last-resort fallback: cwd
    return Path.cwd()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that worker-read canon files have recent date stamps."
    )
    default_threshold = int(os.environ.get("CANON_FRESHNESS_DAYS", DEFAULT_THRESHOLD_DAYS))
    default_warn = int(os.environ.get("CANON_FRESHNESS_WARN_DAYS", DEFAULT_WARN_DAYS))
    parser.add_argument(
        "--threshold",
        type=int,
        default=default_threshold,
        help=f"Days after which a stamp is stale (default {DEFAULT_THRESHOLD_DAYS}).",
    )
    parser.add_argument(
        "--warn-threshold",
        type=int,
        default=default_warn,
        help=f"Days after which a stamp warns but does not fail (default {DEFAULT_WARN_DAYS}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Override the repo root path (default: auto-detect).",
    )
    args = parser.parse_args(argv)

    if args.warn_threshold > args.threshold:
        print(
            f"error: --warn-threshold ({args.warn_threshold}) cannot exceed "
            f"--threshold ({args.threshold})",
            file=sys.stderr,
        )
        return 2

    repo_root = args.repo_root or _resolve_repo_root()
    if not repo_root.is_dir():
        print(f"error: repo root not found: {repo_root}", file=sys.stderr)
        return 2

    results = check_canon_freshness(repo_root, args.threshold, args.warn_threshold)
    if not results:
        print("error: no canon files found — check CANON_FILES list", file=sys.stderr)
        return 2

    if args.json:
        sys.stdout.write(_render_json(results))
    else:
        sys.stdout.write(_render_text(results, args.threshold, args.warn_threshold))

    failure_states = {"stale", "missing_stamp", "bad_date"}
    if any(r.status in failure_states for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
