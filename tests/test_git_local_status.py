"""Regression test for PRO-287: git_local_status gateway tool.

The tool returns the gateway's view of the local working tree at fs_root:
current branch, head SHA, staged/unstaged/untracked counts, and the
ahead/behind delta vs origin/<branch> (or null if no upstream).

Tests cover:
- The porcelain parser (_parse_porcelain_counts) directly with edge cases.
- The full tool with mocked _run_git for deterministic gating + happy-path.

Tests use mocked subprocess so they're fast and deterministic; the real
git invocation is exercised end-to-end when the operator runs the tool.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import miru_readonly_filesystem_mcp as stdio_mcp
from miru_mcp_gateway import git_tools


def _gitrun(args, returncode=0, stdout="", stderr=""):
    return git_tools._GitRun(["git", *args], returncode, stdout, stderr)


class ParsePorcelainCountsTests(unittest.TestCase):
    """Direct tests of _parse_porcelain_counts."""

    def test_empty(self) -> None:
        self.assertEqual(git_tools._parse_porcelain_counts(""), (0, 0, 0))

    def test_single_staged(self) -> None:
        # "M  file" - staged modification, no unstaged change
        self.assertEqual(git_tools._parse_porcelain_counts("M  foo.py\n"), (1, 0, 0))

    def test_single_unstaged(self) -> None:
        # " M file" - unstaged modification only
        self.assertEqual(git_tools._parse_porcelain_counts(" M foo.py\n"), (0, 1, 0))

    def test_single_untracked(self) -> None:
        self.assertEqual(git_tools._parse_porcelain_counts("?? foo.py\n"), (0, 0, 1))

    def test_both_staged_and_unstaged(self) -> None:
        # "MM file" - file is staged AND has further unstaged edits.
        # Same file counts in both buckets (matches `git status` semantics).
        self.assertEqual(git_tools._parse_porcelain_counts("MM foo.py\n"), (1, 1, 0))

    def test_mixed(self) -> None:
        text = (
            "M  staged_only.py\n"
            " M unstaged_only.py\n"
            "MM both.py\n"
            "A  added_staged.py\n"
            "?? untracked.py\n"
            "?? another.py\n"
        )
        self.assertEqual(git_tools._parse_porcelain_counts(text), (3, 2, 2))

    def test_ignores_blank_lines(self) -> None:
        self.assertEqual(
            git_tools._parse_porcelain_counts("\n\nM  foo.py\n\n"),
            (1, 0, 0),
        )

    def test_ignores_short_lines(self) -> None:
        # Lines under 2 chars are skipped (defensive — shouldn't happen in real output).
        self.assertEqual(git_tools._parse_porcelain_counts("X\n"), (0, 0, 0))


class GitLocalStatusTests(unittest.TestCase):
    """Tests for the full git_local_status tool with mocked _run_git."""

    def _patch_run_git(self, responses):
        remaining = list(responses)
        prev = git_tools._run_git
        self.addCleanup(lambda: setattr(git_tools, "_run_git", prev))

        def fake(args, *, timeout=git_tools._GIT_TIMEOUT_S):
            for i, (matcher, run) in enumerate(remaining):
                if list(args[: len(matcher)]) == list(matcher):
                    remaining.pop(i)
                    return run
            raise AssertionError(
                f"unexpected git call {args!r}; remaining matchers: " f"{[m for m, _ in remaining]}"
            )

        git_tools._run_git = fake
        return remaining

    def _redact_passthrough(self, payload):
        # The real _redact may strip some fields; for these tests pass through.
        return payload

    def test_clean_main_no_divergence(self) -> None:
        self._patch_run_git(
            [
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "abc123\n", ""),
                ),
                (
                    ("status", "--porcelain=v1"),
                    _gitrun(["status"], 0, "", ""),
                ),
                (
                    ("rev-parse", "--verify", "origin/main"),
                    _gitrun(["rev-parse"], 0, "abc123\n", ""),
                ),
                (
                    ("rev-list", "--left-right", "--count", "HEAD...origin/main"),
                    _gitrun(["rev-list"], 0, "0\t0\n", ""),
                ),
            ]
        )
        with patch("miru_mcp_gateway.redact.redact_dict", side_effect=self._redact_passthrough):
            parsed = json.loads(git_tools.git_local_status())
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["branch"], "main")
        self.assertEqual(parsed["head_sha"], "abc123")
        self.assertEqual(parsed["staged_count"], 0)
        self.assertEqual(parsed["unstaged_count"], 0)
        self.assertEqual(parsed["untracked_count"], 0)
        self.assertEqual(parsed["ahead_of_origin"], 0)
        self.assertEqual(parsed["behind_origin"], 0)
        self.assertTrue(parsed["clean"])

    def test_dirty_with_mixed_changes(self) -> None:
        self._patch_run_git(
            [
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "def456\n", ""),
                ),
                (
                    ("status", "--porcelain=v1"),
                    _gitrun(
                        ["status"],
                        0,
                        "M  staged.py\n M unstaged.py\n?? new.py\n?? another.py\n",
                        "",
                    ),
                ),
                (
                    ("rev-parse", "--verify", "origin/main"),
                    _gitrun(["rev-parse"], 0, "def456\n", ""),
                ),
                (
                    ("rev-list", "--left-right", "--count", "HEAD...origin/main"),
                    _gitrun(["rev-list"], 0, "0\t0\n", ""),
                ),
            ]
        )
        with patch("miru_mcp_gateway.redact.redact_dict", side_effect=self._redact_passthrough):
            parsed = json.loads(git_tools.git_local_status())
        self.assertEqual(parsed["staged_count"], 1)
        self.assertEqual(parsed["unstaged_count"], 1)
        self.assertEqual(parsed["untracked_count"], 2)
        self.assertFalse(parsed["clean"])

    def test_branch_no_upstream(self) -> None:
        """If origin/<branch> doesn't exist, ahead/behind are null."""
        self._patch_run_git(
            [
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "dreighto/local-only\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "xyz789\n", ""),
                ),
                (
                    ("status", "--porcelain=v1"),
                    _gitrun(["status"], 0, "", ""),
                ),
                (
                    ("rev-parse", "--verify", "origin/dreighto/local-only"),
                    _gitrun(["rev-parse"], 128, "", "fatal: ambiguous argument\n"),
                ),
            ]
        )
        with patch("miru_mcp_gateway.redact.redact_dict", side_effect=self._redact_passthrough):
            parsed = json.loads(git_tools.git_local_status())
        self.assertEqual(parsed["branch"], "dreighto/local-only")
        self.assertIsNone(parsed["ahead_of_origin"])
        self.assertIsNone(parsed["behind_origin"])
        self.assertTrue(parsed["clean"])

    def test_branch_ahead_of_origin(self) -> None:
        self._patch_run_git(
            [
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "ahead123\n", ""),
                ),
                (
                    ("status", "--porcelain=v1"),
                    _gitrun(["status"], 0, "", ""),
                ),
                (
                    ("rev-parse", "--verify", "origin/main"),
                    _gitrun(["rev-parse"], 0, "behind123\n", ""),
                ),
                (
                    ("rev-list", "--left-right", "--count", "HEAD...origin/main"),
                    _gitrun(["rev-list"], 0, "3\t0\n", ""),
                ),
            ]
        )
        with patch("miru_mcp_gateway.redact.redact_dict", side_effect=self._redact_passthrough):
            parsed = json.loads(git_tools.git_local_status())
        self.assertEqual(parsed["ahead_of_origin"], 3)
        self.assertEqual(parsed["behind_origin"], 0)

    def test_branch_behind_origin(self) -> None:
        """The very failure mode that bit PRO-278: local main is behind origin."""
        self._patch_run_git(
            [
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "old123\n", ""),
                ),
                (
                    ("status", "--porcelain=v1"),
                    _gitrun(["status"], 0, "", ""),
                ),
                (
                    ("rev-parse", "--verify", "origin/main"),
                    _gitrun(["rev-parse"], 0, "new456\n", ""),
                ),
                (
                    ("rev-list", "--left-right", "--count", "HEAD...origin/main"),
                    _gitrun(["rev-list"], 0, "0\t1\n", ""),
                ),
            ]
        )
        with patch("miru_mcp_gateway.redact.redact_dict", side_effect=self._redact_passthrough):
            parsed = json.loads(git_tools.git_local_status())
        self.assertEqual(parsed["ahead_of_origin"], 0)
        self.assertEqual(parsed["behind_origin"], 1)

    def test_raises_if_branch_query_fails(self) -> None:
        self._patch_run_git(
            [
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 128, "", "fatal: not a git repository\n"),
                ),
            ]
        )
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            git_tools.git_local_status()
        self.assertIn("could not read current branch", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
