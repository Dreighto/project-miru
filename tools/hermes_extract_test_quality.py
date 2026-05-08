"""
hermes_extract_test_quality.py — Retroactive quality extraction from cc_completion_log.jsonl.

Reads test_evidence fields and extracts structured quality labels for Hermes training.
Outputs to data/hermes_quality_labels.jsonl (append-only).

Each row carries:
  - ticket_id, timestamp (from completion log)
  - test_passed, test_total (ints or null)
  - test_pass_rate (float 0.0-1.0 or null)
  - evidence_tier: "nn_regex" | "ci_binary" | "no_tests" | "freetext"
  - raw_test_evidence (original string for audit)

Usage:
    python tools/hermes_extract_test_quality.py [--dry-run] [--since YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime

_NN_PATTERN = re.compile(r"(\d+)\s*/\s*(\d+)")
_CI_PATTERN = re.compile(r"^ci_only:", re.IGNORECASE)
_NO_TESTS = re.compile(r"^no_tests$", re.IGNORECASE)

# Legacy patterns that predate the enforced format
_LEGACY_CI_KEYWORDS = frozenset(
    {
        "pre-commit",
        "hygiene",
        "bugbot",
        "ci pass",
        "ci green",
        "green",
        "lint",
        "eslint",
        "ruff",
    }
)
_LEGACY_NO_TEST_KEYWORDS = frozenset(
    {
        "no test",
        "no_test",
        "behavioral",
        "rule only",
        "n/a",
        "not applicable",
        "no code change",
    }
)


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


def classify_test_evidence(raw: str) -> dict:
    """Parse a test_evidence string into structured quality data."""
    if not raw or raw.strip().lower() in ("", "null"):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "freetext",
        }

    raw_stripped = raw.strip()

    # Tier 1: N/N regex — highest confidence
    m = _NN_PATTERN.search(raw_stripped)
    if m:
        passed, total = int(m.group(1)), int(m.group(2))
        rate = passed / total if total > 0 else 0.0
        return {
            "test_passed": passed,
            "test_total": total,
            "test_pass_rate": round(rate, 4),
            "evidence_tier": "nn_regex",
        }

    # Tier 2: ci_only (new format) or legacy CI keywords
    if _CI_PATTERN.match(raw_stripped):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "ci_binary",
        }
    lower = raw_stripped.lower()
    if any(kw in lower for kw in _LEGACY_CI_KEYWORDS):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "ci_binary",
        }

    # Tier 3: no_tests (new format) or legacy no-test keywords
    if _NO_TESTS.match(raw_stripped):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "no_tests",
        }
    if any(kw in lower for kw in _LEGACY_NO_TEST_KEYWORDS):
        return {
            "test_passed": None,
            "test_total": None,
            "test_pass_rate": None,
            "evidence_tier": "no_tests",
        }

    # Tier 4: freetext — lowest confidence
    return {
        "test_passed": None,
        "test_total": None,
        "test_pass_rate": None,
        "evidence_tier": "freetext",
    }


def run(since: str | None = None, dry_run: bool = False) -> int:
    root = _repo_root()
    log_path = os.path.join(root, "data", "cc_completion_log.jsonl")
    out_path = os.path.join(root, "data", "hermes_quality_labels.jsonl")

    if not os.path.exists(log_path):
        print(f"[hermes_quality] error: {log_path} not found", file=sys.stderr)
        return 0

    rows: list[str] = []
    tier_counts: dict[str, int] = {}

    with open(log_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = entry.get("timestamp", "")
            if since and ts and ts[:10] < since:
                continue

            ticket = entry.get("ticket_id")
            raw_te = entry.get("test_evidence", "")
            status = entry.get("status", "")

            quality = classify_test_evidence(raw_te)
            tier_counts[quality["evidence_tier"]] = tier_counts.get(quality["evidence_tier"], 0) + 1

            row = {
                "extracted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "ticket_id": ticket,
                "completion_timestamp": ts,
                "completion_status": status,
                "test_passed": quality["test_passed"],
                "test_total": quality["test_total"],
                "test_pass_rate": quality["test_pass_rate"],
                "evidence_tier": quality["evidence_tier"],
                "raw_test_evidence": raw_te,
            }
            rows.append(json.dumps(row, separators=(",", ":"), ensure_ascii=False))

    if dry_run:
        stdout_bin = getattr(sys.stdout, "buffer", None)
        for r in rows:
            if stdout_bin is not None:
                stdout_bin.write((r + "\n").encode("utf-8"))
            else:
                print(r)
    else:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(r + "\n")

    print(
        f"[hermes_quality] {len(rows)} entries processed. Tiers: {json.dumps(tier_counts)}",
        file=sys.stderr,
    )
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Extract quality labels from completion log for Hermes."
    )
    p.add_argument(
        "--since", metavar="YYYY-MM-DD", help="Only include entries on or after this date"
    )
    p.add_argument("--dry-run", action="store_true", help="Print to stdout instead of appending")
    args = p.parse_args()
    run(since=args.since, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
