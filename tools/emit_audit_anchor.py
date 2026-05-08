"""DGAS Tier 2 #7: daily audit anchor.

Snapshots the state of every canonical append-only data/*.jsonl file into
a single chained row in ``data/audit_anchors.jsonl``. The anchor records:

    * file size in bytes
    * SHA-256 of the entire file contents at anchor time
    * total / chained / legacy prefix row counts
    * row_hash of the last chained row (or None if the file is still
      legacy-only)

Why both file_sha256 AND last_chained_row_hash? Because chain protection
only applies to rows that were written via ``append_chained``. The 9
canonical files all contain hundreds of legacy rows from before chaining
existed (PR #128). Sealing each daily anchor with file_sha256 retroactively
locks the legacy prefix as well: tampering with any byte of a legacy row
breaks the file_sha256 of every anchor written after that day, and the
anchors themselves are chained, so an attacker cannot rewrite the anchor
file to cover the tracks.

The anchor row is itself written via ``append_chained``, so the audit
anchor file becomes the second-order audit trail.

Usage:
    python tools/emit_audit_anchor.py            # writes today's anchor
    python tools/emit_audit_anchor.py --dry-run  # print, do not write

Schedule: invoke once per day via Windows scheduled task or cron. Idempotent
in the sense that re-running on the same day appends a second anchor row;
that's intentional. Multiple anchors per day are useful (one per CI run,
one per pre-push, etc.).

Exit codes:
    0 — anchor written (or, with --dry-run, computed) successfully
    1 — at least one target file failed to read; anchor still emitted with
        the per-file error captured. Use exit 1 to surface CI alarms.
    2 — script error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Make the audit_chain library importable regardless of how this tool is
# invoked.
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))
from audit_chain import CHAIN_FIELD_HASH, append_chained, validate_chain  # noqa: E402

# Canonical list mirrors CLAUDE.md "Append-only data files" section. Order
# matters for stable diffing across days.
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

ANCHOR_LOG_REL = "data/audit_anchors.jsonl"

_HASH_CHUNK = 65536


def _repo_root() -> Path:
    """Return the active worktree root (matches verify_audit_chain.py)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(_THIS_DIR),
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except Exception:
        pass
    return _THIS_DIR.parent


def _file_sha256(path: Path) -> str:
    """Stream the file in chunks; never load it all in memory."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_HASH_CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _last_chained_row_hash(path: Path) -> str | None:
    """Return the row_hash of the last chained row in ``path``, or None.

    Reads in reverse from the end of the file via line scan. Sufficient for
    the canonical audit logs which use one JSON object per line. If the file
    is legacy-only this returns None — that's the expected state on day 1.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    last_chained: str | None = None
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            rh = obj.get(CHAIN_FIELD_HASH)
            if isinstance(rh, str) and rh:
                last_chained = rh
    return last_chained


def _resolve_within_repo(rel_path: str, repo_root: Path) -> Path:
    """Resolve ``rel_path`` against ``repo_root`` and refuse to leave the repo.

    With ``--files``, callers can pass arbitrary path strings. Unrestricted,
    an absolute path or a ``..`` segment would let the anchor snapshot files
    outside the repo (and write their sha256 into the audit ledger), which
    is a small but real information-disclosure surface.

    The check normalises both sides via ``Path.resolve`` so symlinks and
    case-folded segments still resolve to a single canonical form on
    Windows and POSIX.
    """
    repo_root_abs = repo_root.resolve()
    candidate = (repo_root_abs / rel_path).resolve()
    try:
        candidate.relative_to(repo_root_abs)
    except ValueError as exc:
        raise ValueError(
            f"path {rel_path!r} resolves outside repo root {repo_root_abs!s}; "
            f"--files arguments must stay within the repo"
        ) from exc
    return candidate


def snapshot_file(rel_path: str, repo_root: Path) -> dict[str, Any]:
    """Return the per-file dict for one audit log."""
    try:
        full = _resolve_within_repo(rel_path, repo_root)
    except ValueError as exc:
        return {
            "path": rel_path,
            "exists": False,
            "file_size": 0,
            "file_sha256": None,
            "total_rows": 0,
            "chained_rows": 0,
            "legacy_prefix_rows": 0,
            "last_chained_row_hash": None,
            "chain_ok": False,
            "error": f"path_traversal_rejected: {exc}",
        }
    if not full.exists():
        return {
            "path": rel_path,
            "exists": False,
            "file_size": 0,
            "file_sha256": None,
            "total_rows": 0,
            "chained_rows": 0,
            "legacy_prefix_rows": 0,
            "last_chained_row_hash": None,
            "chain_ok": True,  # vacuously true: nothing to validate
            "error": None,
        }
    try:
        size = full.stat().st_size
        sha = _file_sha256(full)
        chain = validate_chain(full)
        last_hash = _last_chained_row_hash(full) if chain.ok else None
        return {
            "path": rel_path,
            "exists": True,
            "file_size": size,
            "file_sha256": sha,
            "total_rows": chain.total_rows,
            "chained_rows": chain.chained_rows,
            "legacy_prefix_rows": chain.legacy_prefix_rows,
            "last_chained_row_hash": last_hash,
            "chain_ok": chain.ok,
            "error": chain.error,
        }
    except OSError as exc:
        return {
            "path": rel_path,
            "exists": True,
            "file_size": None,
            "file_sha256": None,
            "total_rows": 0,
            "chained_rows": 0,
            "legacy_prefix_rows": 0,
            "last_chained_row_hash": None,
            "chain_ok": False,
            "error": f"os_error: {exc}",
        }


def build_anchor_row(repo_root: Path, files: tuple[str, ...] = AUDIT_FILES) -> dict[str, Any]:
    """Build the anchor row body (without prev_hash/row_hash — append_chained
    fills those)."""
    now = datetime.now(UTC)
    snapshots = [snapshot_file(f, repo_root) for f in files]
    return {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "anchor_for_date": now.strftime("%Y-%m-%d"),
        "schema_version": 1,
        "files": snapshots,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit a daily audit anchor row.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute the anchor and print it; do not append",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        default=None,
        help="override the canonical list (paths repo-relative)",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    file_list = tuple(args.files) if args.files else AUDIT_FILES

    row = build_anchor_row(repo_root, file_list)

    # Surface errors to the caller without aborting the anchor write —
    # recording the error in the anchor itself is more useful than skipping.
    any_error = any(s.get("error") for s in row["files"])

    if args.dry_run:
        print(json.dumps(row, indent=2, sort_keys=True))
        return 1 if any_error else 0

    anchor_path = repo_root / ANCHOR_LOG_REL
    row_hash = append_chained(anchor_path, row)
    print(
        f"[audit_anchor] wrote anchor for {row['anchor_for_date']} "
        f"({len(row['files'])} files), row_hash={row_hash[:12]}…",
        file=sys.stderr,
    )
    return 1 if any_error else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[audit_anchor] script error: {exc}", file=sys.stderr)
        sys.exit(2)
