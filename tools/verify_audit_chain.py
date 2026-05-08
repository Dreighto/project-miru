"""DGAS Tier 2 #6: walk the 9 append-only data/*.jsonl files and report chain integrity.

Usage:
    python tools/verify_audit_chain.py            # human-readable summary
    python tools/verify_audit_chain.py --json     # machine-readable
    python tools/verify_audit_chain.py --strict   # exit 1 if any chained file is broken

Exit codes:
    0 — every file with chained rows verifies; legacy prefixes are tolerated.
    1 — at least one file has a broken chain (only when --strict is set, or
        when a chained row fails verification — silent legacy-only files
        always exit 0 because they predate the chain).
    2 — script error (path resolution failed, etc).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Audit files that are subject to DGAS Tier 2 #6 hash chaining. Order matches
# the canonical list in CLAUDE.md "Append-only data files" section.
AUDIT_FILES: tuple[str, ...] = (
    "data/cc_completion_log.jsonl",
    "data/routing_history.jsonl",
    "data/pending_callbacks.jsonl",
    "data/dispatch_dlq.jsonl",
    "data/cc_heartbeat_log.jsonl",
    "data/vp_ops_supervision.jsonl",
    "data/drift_scanner_log.jsonl",
    "data/agent_decisions.jsonl",
    "data/github_resource_ledger.jsonl",
)


def _repo_root() -> Path:
    """Return the main repo root, works from any linked worktree."""
    import subprocess

    here = Path(__file__).resolve().parent
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=str(here),
            timeout=5,
        )
        if result.returncode == 0:
            common_dir = (here / result.stdout.strip()).resolve()
            return common_dir.parent
    except Exception:
        pass
    return here.parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify hash-chained audit log integrity.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 even when a file has only legacy rows (no chain established yet)",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="override the default list of files (paths are repo-relative)",
    )
    args = parser.parse_args()

    # Imported here so the script can be run before audit_chain is on
    # sys.path in unusual environments.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from audit_chain import validate_chain

    root = _repo_root()
    targets = [root / p for p in (args.files or AUDIT_FILES)]

    summaries: list[dict[str, object]] = []
    overall_ok = True
    any_chained_broken = False
    any_legacy_only = False

    for path in targets:
        rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
        if not path.exists():
            summaries.append(
                {
                    "path": rel,
                    "exists": False,
                    "ok": True,
                    "total_rows": 0,
                    "chained_rows": 0,
                    "legacy_prefix_rows": 0,
                    "note": "file does not exist (gitignored or not yet created)",
                }
            )
            # A missing file is treated as legacy-only for --strict purposes —
            # the gate isn't established yet for this stream.
            any_legacy_only = True
            continue

        result = validate_chain(path)
        chained_broken = bool(result.chained_rows and not result.ok)
        if result.chained_rows == 0:
            any_legacy_only = True
        if chained_broken:
            any_chained_broken = True
        if not result.ok:
            overall_ok = False

        summaries.append(
            {
                "path": rel,
                "exists": True,
                "ok": result.ok,
                "total_rows": result.total_rows,
                "chained_rows": result.chained_rows,
                "legacy_prefix_rows": result.legacy_prefix_rows,
                "broken_at_line": result.broken_at_line,
                "error": result.error,
                "parse_error_count": len(result.parse_errors),
            }
        )

    if args.json:
        print(
            json.dumps(
                {
                    "ok": overall_ok,
                    "any_chained_broken": any_chained_broken,
                    "files": summaries,
                },
                indent=2,
            )
        )
    else:
        print(f"Audit chain verification — {len(summaries)} file(s)")
        for s in summaries:
            mark = "OK" if s["ok"] else "BROKEN"
            note = ""
            if not s["exists"]:
                note = " (missing)"
            elif s["chained_rows"] == 0:
                note = " (no chained rows yet — legacy)"
            line = (
                f"  [{mark}] {s['path']:<48s} "
                f"total={s['total_rows']} chained={s['chained_rows']} "
                f"legacy_prefix={s['legacy_prefix_rows']}{note}"
            )
            print(line)
            if not s["ok"]:
                print(f"         line {s['broken_at_line']}: {s['error']}")
            if s.get("parse_error_count"):
                print(f"         parse_error_count={s['parse_error_count']}")
        print(f"\nOverall: {'OK' if overall_ok else 'BROKEN'}")
        if any_chained_broken:
            print("WARNING: at least one chained row failed verification.")

    if any_chained_broken:
        return 1
    if args.strict and (not overall_ok or any_legacy_only):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"verify_audit_chain: error — {exc}", file=sys.stderr)
        sys.exit(2)
