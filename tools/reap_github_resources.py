import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime


def _repo_root() -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=script_dir,
            timeout=5,
        )
        if result.returncode == 0:
            common_dir = os.path.normpath(os.path.join(script_dir, result.stdout.strip()))
            return os.path.dirname(common_dir)
    except Exception:
        pass
    return os.path.dirname(script_dir)


def load_ledger(ledger_path: str) -> list[dict]:
    if not os.path.exists(ledger_path):
        return []
    entries = []
    with open(ledger_path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"[reap] warning: skipping malformed line {lineno}: {e}", file=sys.stderr)
    return entries


def _operation_key(entry: dict) -> tuple[str, str, str, str]:
    return (
        str(entry.get("trace_id", "")),
        str(entry.get("resource_type", "")),
        str(entry.get("resource_id", "")),
        str(entry.get("intent", "")),
    )


def find_stale_pending(
    entries: list[dict], ttl_seconds: float, now: datetime | None = None
) -> list[dict]:
    """Deduplicates by operation key; returns pending/failed entries past TTL.

    Compensated rows suppress re-processing. Failed entries are retryable.
    """
    if now is None:
        now = datetime.now(UTC)

    latest_by_op: dict[tuple[str, str, str, str], dict] = {}
    for entry in entries:
        key = _operation_key(entry)
        latest_by_op[key] = entry

    stale = []
    for entry in latest_by_op.values():
        if entry.get("status") not in ("pending", "failed"):
            continue
        ts_str = entry.get("ts")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            print(f"[reap] warning: unparseable ts '{ts_str}', skipping", file=sys.stderr)
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age = (now - ts).total_seconds()
        if age > ttl_seconds:
            stale.append(entry)
    return stale


def _branch_exists_remote(branch_name: str, cwd: str | None = None) -> bool | None:
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch_name],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _pr_is_open(resource_id: str, cwd: str | None = None) -> bool | None:
    result = subprocess.run(
        ["gh", "pr", "view", resource_id, "--json", "state", "--jq", ".state"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip().upper() == "OPEN"


def _delete_branch(branch_name: str, cwd: str | None = None) -> bool:
    result = subprocess.run(
        ["git", "push", "origin", "--delete", branch_name],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def _close_pr(resource_id: str, cwd: str | None = None) -> bool:
    result = subprocess.run(
        [
            "gh",
            "pr",
            "close",
            resource_id,
            "--comment",
            "Auto-closed by reap_github_resources (stale pending entry)",
        ],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=15,
        check=False,
    )
    return result.returncode == 0


def _append_compensation_row(ledger_path: str, original: dict, outcome_status: str) -> None:
    row = dict(original)
    row["status"] = outcome_status
    row["ts"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = json.dumps(row, separators=(",", ":"))
    with open(ledger_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def reap(
    ledger_path: str,
    ttl_hours: float = 2.0,
    dry_run: bool = True,
    now: datetime | None = None,
) -> tuple[list[dict], bool]:
    """Returns (stale_entries, had_failure)."""
    entries = load_ledger(ledger_path)
    ttl_seconds = ttl_hours * 3600
    stale = find_stale_pending(entries, ttl_seconds, now=now)

    if not stale:
        print("[reap] no stale pending entries found", file=sys.stderr)
        return [], False

    repo_root = _repo_root()
    had_failure = False

    for entry in stale:
        resource_type = entry.get("resource_type")
        resource_id = entry.get("resource_id", "")
        trace_id = entry.get("trace_id", "?")

        if dry_run:
            print(
                f"[reap] DRY-RUN: would compensate {resource_type} '{resource_id}' "
                f"(trace={trace_id})",
                file=sys.stderr,
            )
            continue

        # Execute mode
        if resource_type == "branch":
            exists = _branch_exists_remote(resource_id, cwd=repo_root)
            if exists is None:
                print(
                    f"[reap] could not verify branch '{resource_id}' (command failed), skipping",
                    file=sys.stderr,
                )
                had_failure = True
                continue
            if not exists:
                print(
                    f"[reap] branch '{resource_id}' already gone, marking compensated",
                    file=sys.stderr,
                )
                _append_compensation_row(ledger_path, entry, "compensated")
                continue
            success = _delete_branch(resource_id, cwd=repo_root)
            outcome = "compensated" if success else "failed"
            if not success:
                had_failure = True
            print(
                f"[reap] delete branch '{resource_id}': {'OK' if success else 'FAILED'}",
                file=sys.stderr,
            )
            _append_compensation_row(ledger_path, entry, outcome)

        elif resource_type == "pr":
            is_open = _pr_is_open(resource_id, cwd=repo_root)
            if is_open is None:
                print(
                    f"[reap] could not verify PR '{resource_id}' (command failed), skipping",
                    file=sys.stderr,
                )
                had_failure = True
                continue
            if not is_open:
                print(
                    f"[reap] PR '{resource_id}' already closed, marking compensated",
                    file=sys.stderr,
                )
                _append_compensation_row(ledger_path, entry, "compensated")
                continue
            success = _close_pr(resource_id, cwd=repo_root)
            outcome = "compensated" if success else "failed"
            if not success:
                had_failure = True
            print(
                f"[reap] close PR '{resource_id}': {'OK' if success else 'FAILED'}",
                file=sys.stderr,
            )
            _append_compensation_row(ledger_path, entry, outcome)

        else:
            print(f"[reap] unknown resource_type '{resource_type}', skipping", file=sys.stderr)

    return stale, had_failure


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reap stale pending entries from the GitHub resource ledger."
    )
    parser.add_argument(
        "--ttl-hours", type=float, default=2.0, help="Stale threshold in hours (default: 2)"
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Report stale entries without taking action (default)",
    )
    mode_group.add_argument(
        "--execute",
        dest="dry_run",
        action="store_false",
        help="Execute compensations (delete branches, close PRs)",
    )

    args = parser.parse_args()

    ledger_path = os.path.join(_repo_root(), "data", "github_resource_ledger.jsonl")

    mode_label = "DRY-RUN" if args.dry_run else "EXECUTE"
    print(f"[reap] mode={mode_label} ttl={args.ttl_hours}h ledger={ledger_path}", file=sys.stderr)

    stale, had_failure = reap(ledger_path, ttl_hours=args.ttl_hours, dry_run=args.dry_run)

    if stale:
        print(f"[reap] {len(stale)} stale entries found", file=sys.stderr)
        for e in stale:
            print(
                f"  {e.get('resource_type')} {e.get('resource_id')} ts={e.get('ts')}",
                file=sys.stderr,
            )
    sys.exit(1 if had_failure else 0)


if __name__ == "__main__":
    main()
