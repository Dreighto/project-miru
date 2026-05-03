"""Regression test for PRO-284: git_pull_main gateway tool.

The tool fetches origin/main and fast-forwards local main when:
- Working tree is clean (no staged or unstaged changes)
- Current branch is main
- Fast-forward succeeds (no diverged history)

Without this tool, when CH self-merges a PR via github_merge_pr the merged
commit lands on origin/main but the local working tree at the gateway's
fs_root has no automatic pull. Services restarted after the merge re-load
the pre-merge code (the failure mode that hit PRO-278 on 2026-05-03).

Tests use mocked subprocess so they're fast and deterministic; the real
git invocation is exercised end-to-end when the operator runs the tool
in production.
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

import miru_readonly_filesystem_mcp as stdio_mcp
from miru_mcp_gateway import git_tools


def _gitrun(args, returncode=0, stdout="", stderr=""):
    return git_tools._GitRun(["git", *args], returncode, stdout, stderr)


class GitPullMainTests(unittest.TestCase):
    HARNESS = Path(__file__).resolve().parent / "_tmp"

    def setUp(self) -> None:
        self.HARNESS.mkdir(parents=True, exist_ok=True)
        self.root = self.HARNESS / f"git_pull_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

        # _CFG must be set so _audit_git_pull's _cfg() call doesn't raise.
        cfg = SimpleNamespace(fs_root=self.root)
        self._prev_cfg = git_tools._CFG
        git_tools._CFG = cfg
        self.addCleanup(self._restore_cfg)

        # Capture audit rows instead of writing to disk.
        self.audit_rows: list[dict] = []
        self._prev_audit = git_tools._audit_git_pull
        git_tools._audit_git_pull = self._capture_audit
        self.addCleanup(self._restore_audit)

    def _restore_cfg(self) -> None:
        git_tools._CFG = self._prev_cfg

    def _restore_audit(self) -> None:
        git_tools._audit_git_pull = self._prev_audit

    def _capture_audit(self, **kwargs) -> None:
        self.audit_rows.append(kwargs)

    def _patch_run_git(self, responses):
        """Patch _run_git to return canned responses in order based on the args.

        ``responses`` is a list of ``(matcher, _GitRun)`` pairs. The matcher is
        a tuple-prefix matched against the args list. First match wins, and the
        matched entry is consumed (so each git call is asserted to happen exactly
        once unless the matcher is intentionally reused via duplicates).
        """
        # Make a mutable copy so the test can re-add common branches.
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

    def test_refuses_on_dirty_working_tree(self) -> None:
        self._patch_run_git(
            [
                (
                    ("status", "--porcelain=v1"),
                    _gitrun(["status"], 0, " M tools/emit_completion.py\n", ""),
                ),
            ]
        )
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            git_tools.git_pull_main()
        self.assertIn("working tree is dirty", str(ctx.exception))
        self.assertIn("emit_completion.py", str(ctx.exception))
        # Audit row records the failure.
        self.assertEqual(len(self.audit_rows), 1)
        self.assertEqual(self.audit_rows[0]["result"], "failure")
        self.assertFalse(self.audit_rows[0]["fetched"])

    def test_refuses_on_non_main_branch(self) -> None:
        self._patch_run_git(
            [
                (("status", "--porcelain=v1"), _gitrun(["status"], 0, "", "")),
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "dreighto/some-branch\n", ""),
                ),
            ]
        )
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            git_tools.git_pull_main()
        self.assertIn("not 'main'", str(ctx.exception))
        self.assertIn("dreighto/some-branch", str(ctx.exception))
        self.assertEqual(len(self.audit_rows), 1)
        self.assertEqual(self.audit_rows[0]["result"], "failure")

    def test_happy_path_no_commits_pulled(self) -> None:
        self._patch_run_git(
            [
                (("status", "--porcelain=v1"), _gitrun(["status"], 0, "", "")),
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "abc123\n", ""),
                ),
                (
                    ("fetch", "origin", "main"),
                    _gitrun(["fetch"], 0, "", "From github.com:Dreighto/project-miru\n"),
                ),
                (
                    ("pull", "--ff-only", "origin", "main"),
                    _gitrun(["pull"], 0, "Already up to date.\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "abc123\n", ""),
                ),
            ]
        )
        result = git_tools.git_pull_main()
        import json

        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["branch"], "main")
        self.assertEqual(parsed["before_sha"], "abc123")
        self.assertEqual(parsed["after_sha"], "abc123")
        self.assertFalse(parsed["commits_pulled"])
        self.assertTrue(parsed["fetched"])
        self.assertEqual(len(self.audit_rows), 1)
        self.assertEqual(self.audit_rows[0]["result"], "success")

    def test_happy_path_commits_pulled(self) -> None:
        self._patch_run_git(
            [
                (("status", "--porcelain=v1"), _gitrun(["status"], 0, "", "")),
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "before_sha_aaa\n", ""),
                ),
                (
                    ("fetch", "origin", "main"),
                    _gitrun(["fetch"], 0, "", "From github\n   abc..def main -> origin/main\n"),
                ),
                (
                    ("pull", "--ff-only", "origin", "main"),
                    _gitrun(
                        ["pull"],
                        0,
                        "Updating abc..def\nFast-forward\n 1 file changed, 5 insertions(+)\n",
                        "",
                    ),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "after_sha_bbb\n", ""),
                ),
            ]
        )
        result = git_tools.git_pull_main()
        import json

        parsed = json.loads(result)
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["before_sha"], "before_sha_aaa")
        self.assertEqual(parsed["after_sha"], "after_sha_bbb")
        self.assertTrue(parsed["commits_pulled"])
        self.assertIn("Fast-forward", parsed["pull_output"])
        self.assertEqual(self.audit_rows[0]["result"], "success")
        self.assertEqual(self.audit_rows[0]["before_sha"], "before_sha_aaa")
        self.assertEqual(self.audit_rows[0]["after_sha"], "after_sha_bbb")

    def test_refuses_on_fetch_failure(self) -> None:
        self._patch_run_git(
            [
                (("status", "--porcelain=v1"), _gitrun(["status"], 0, "", "")),
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "abc\n", ""),
                ),
                (
                    ("fetch", "origin", "main"),
                    _gitrun(["fetch"], 128, "", "fatal: unable to access remote: timeout\n"),
                ),
            ]
        )
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            git_tools.git_pull_main()
        self.assertIn("fetch failed", str(ctx.exception))
        self.assertIn("timeout", str(ctx.exception))
        self.assertEqual(self.audit_rows[0]["result"], "failure")
        self.assertFalse(self.audit_rows[0]["fetched"])

    def test_refuses_on_ff_only_pull_failure(self) -> None:
        """If history has diverged, --ff-only refuses. Tool must surface that
        rather than silently merging or overwriting."""
        self._patch_run_git(
            [
                (("status", "--porcelain=v1"), _gitrun(["status"], 0, "", "")),
                (
                    ("rev-parse", "--abbrev-ref", "HEAD"),
                    _gitrun(["rev-parse"], 0, "main\n", ""),
                ),
                (
                    ("rev-parse", "HEAD"),
                    _gitrun(["rev-parse"], 0, "abc\n", ""),
                ),
                (
                    ("fetch", "origin", "main"),
                    _gitrun(["fetch"], 0, "", ""),
                ),
                (
                    ("pull", "--ff-only", "origin", "main"),
                    _gitrun(
                        ["pull"],
                        128,
                        "",
                        "fatal: Not possible to fast-forward, aborting.\n",
                    ),
                ),
            ]
        )
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            git_tools.git_pull_main()
        self.assertIn("pull --ff-only failed", str(ctx.exception))
        self.assertIn("diverged history", str(ctx.exception))
        self.assertEqual(self.audit_rows[0]["result"], "failure")
        # Fetch did succeed before the pull failed, so fetched=True.
        self.assertTrue(self.audit_rows[0]["fetched"])


if __name__ == "__main__":
    unittest.main()
