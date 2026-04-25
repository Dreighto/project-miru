#!/usr/bin/env python3
"""Validate that every non-empty line in a JSONL file is valid JSON.

Used by pre-commit to catch malformed marker writes (e.g. cc_completion_log.jsonl,
routing_history.jsonl) before they land. Pure stdlib — works on any platform with
Python 3.11+ available, no jq dependency.

Usage: validate_jsonl.py <file>...
Exit 0 if every line in every file parses, 1 otherwise.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    exit_code = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"{path}: read error: {e}", file=sys.stderr)
            exit_code = 1
            continue

        for line_num, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                print(f"{path}:{line_num}: invalid JSON: {e}", file=sys.stderr)
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
