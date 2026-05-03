"""Regression test for PRO-159: pre-commit hooks must not rewrite append-only
JSONL files under data/.

The CC Completion Ping watcher (workflow `UCM67hqZR74Fz8US`) treats
`data/cc_completion_log.jsonl` as strictly append-only and fires a Telegram
alert if rows ever decrease. On 2026-04-27 the guard fired with
`rows now=22, last_seen=24` during PRO-156 work — a read-modify-write of the
file (e.g. by `trailing-whitespace` or `end-of-file-fixer`) lost two
uncommitted appends.

This test enforces the structural invariant: any pre-commit hook that
read-modify-writes file content must exclude `^data/.*\\.jsonl$`. If a future
edit weakens or removes the exclude, this test fails loudly.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRECOMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"

# Hooks that operate by read-modify-write of whole-file content. These MUST
# exclude the append-only data/*.jsonl files. validate_jsonl is read-only and
# is intentionally NOT in this list.
WHOLE_FILE_REWRITE_HOOKS = ("trailing-whitespace", "end-of-file-fixer")

# The append-only files. cc_completion_log.jsonl is currently the only tracked
# one (the others are gitignored), but the exclude pattern applies to all of
# them so future tracking inherits the protection.
APPEND_ONLY_FILES = (
    "data/cc_completion_log.jsonl",
    "data/routing_history.jsonl",
    "data/pending_callbacks.jsonl",
    "data/dispatch_dlq.jsonl",
    "data/cc_heartbeat_log.jsonl",
    "data/vp_ops_supervision.jsonl",
    # Ticket B7 — daily Linear↔completion-marker drift scan results.
    "data/drift_scanner_log.jsonl",
)


def _parse_hook_blocks(yaml_text: str) -> dict[str, str]:
    """Return {hook_id: full_block_text}. Hand-rolled to avoid a YAML dep."""
    blocks: dict[str, str] = {}
    lines = yaml_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\s*)- id:\s+([\w-]+)", line)
        if m:
            indent = len(m.group(1))
            hook_id = m.group(2)
            block_lines = [line]
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip():
                    block_lines.append(nxt)
                    j += 1
                    continue
                # Stop at a sibling list item or shallower content.
                stripped_indent = len(nxt) - len(nxt.lstrip(" "))
                if stripped_indent <= indent and nxt.lstrip().startswith(("- ", "-")):
                    break
                if stripped_indent < indent:
                    break
                block_lines.append(nxt)
                j += 1
            blocks[hook_id] = "\n".join(block_lines)
            i = j
            continue
        i += 1
    return blocks


class JsonlAppendOnlyInvariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertTrue(
            PRECOMMIT_CONFIG.exists(),
            f".pre-commit-config.yaml missing at {PRECOMMIT_CONFIG}",
        )
        self.text = PRECOMMIT_CONFIG.read_text(encoding="utf-8")
        self.blocks = _parse_hook_blocks(self.text)

    def test_rewrite_hooks_present(self) -> None:
        for hook in WHOLE_FILE_REWRITE_HOOKS:
            self.assertIn(
                hook,
                self.blocks,
                f"hook {hook!r} not found in .pre-commit-config.yaml — "
                "test stale or hook removed",
            )

    def test_rewrite_hooks_exclude_append_only_jsonl(self) -> None:
        for hook in WHOLE_FILE_REWRITE_HOOKS:
            block = self.blocks[hook]
            m = re.search(r"exclude:\s*(\S+)", block)
            self.assertIsNotNone(
                m,
                f"hook {hook!r} has no `exclude:` line — append-only data/*.jsonl "
                "would be subject to read-modify-write rewrite (PRO-159 regression).",
            )
            pattern = m.group(1).strip("'\"")
            compiled = re.compile(pattern)
            for path in APPEND_ONLY_FILES:
                self.assertIsNotNone(
                    compiled.search(path),
                    f"hook {hook!r} exclude pattern {pattern!r} does not match "
                    f"{path!r}. The append-only invariant guard will fire if any "
                    "rewrite-style hook touches this file. See PRO-159.",
                )

    def test_validate_jsonl_hook_is_not_excluded(self) -> None:
        """validate_jsonl is read-only — it must keep applying to all jsonl files
        so malformed appends are caught. If someone accidentally adds an
        exclude here while patching the rewrite hooks, this guards against it.
        """
        block = self.blocks.get("jsonl-line-validation")
        self.assertIsNotNone(block, "jsonl-line-validation hook missing")
        self.assertNotIn(
            "exclude:",
            block,
            "validate_jsonl is read-only and must NOT exclude data/*.jsonl. "
            "Adding an exclude here would mask malformed appends.",
        )


if __name__ == "__main__":
    unittest.main()
