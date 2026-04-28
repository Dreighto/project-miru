from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import miru_readonly_filesystem_mcp as stdio_mcp
from miru_mcp_gateway import git_tools


class MiruMcpGatewayGitWriteTests(unittest.TestCase):
    HARNESS = Path(__file__).resolve().parent / "_tmp"

    def setUp(self) -> None:
        self.HARNESS.mkdir(parents=True, exist_ok=True)
        self.root = self.HARNESS / f"git_write_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_resolve_allowed_paths_rejects_append_only_jsonl(self) -> None:
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            git_tools._resolve_allowed_paths(["data/cc_completion_log.jsonl"])
        self.assertIn("append-only", str(ctx.exception))

    def test_resolve_allowed_paths_rejects_worker_rule_file_other_than_claude(self) -> None:
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            git_tools._resolve_allowed_paths(["CURSOR.md"])
        self.assertIn("worker rule file denied", str(ctx.exception))

    def test_resolve_allowed_paths_accepts_claude_and_docs(self) -> None:
        docs_file = Path("docs") / "n8n" / "WORKFLOW_MAP.md"
        rels = git_tools._resolve_allowed_paths(["CLAUDE.md", str(docs_file)])
        self.assertIn("CLAUDE.md", rels)
        self.assertIn("docs/n8n/WORKFLOW_MAP.md", rels)

    def test_git_commit_and_push_audits_hygiene_failure_without_commit(self) -> None:
        cfg = SimpleNamespace(fs_root=self.root)
        git_tools._CFG = cfg
        calls: list[list[str]] = []

        def fake_git(args: list[str], *, timeout: int = git_tools._GIT_TIMEOUT_S):
            calls.append(args)
            if args[:3] == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return git_tools._GitRun(["git", *args], 0, "main\n", "")
            if args[:2] == ["rev-parse", "--verify"]:
                return git_tools._GitRun(["git", *args], 0, "abc\n", "")
            if args[:3] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return git_tools._GitRun(["git", *args], 0, "origin/main\n", "")
            if args[:2] == ["status", "--porcelain=v1"]:
                return git_tools._GitRun(["git", *args], 0, " M CLAUDE.md\n", "")
            if args[:2] == ["add", "--"]:
                return git_tools._GitRun(["git", *args], 0, "", "")
            raise AssertionError(f"unexpected git call: {args}")

        def fake_pre_commit(paths: list[str]):
            return git_tools._GitRun(
                ["pre-commit", "run", "--files", *paths],
                1,
                "ruff failed",
                "",
            )

        with (
            patch.object(git_tools, "_run_git", side_effect=fake_git),
            patch.object(git_tools, "_run_pre_commit", side_effect=fake_pre_commit),
            self.assertRaises(stdio_mcp.McpError),
        ):
            git_tools.git_commit_and_push(["CLAUDE.md"], "docs: test", "main")

        self.assertNotIn(["commit", "-m", "docs: test"], calls)
        writes_log = self.root / "logs" / "mcp_gateway_writes.jsonl"
        row = json.loads(writes_log.read_text(encoding="utf-8").strip())
        self.assertEqual(row["tool"], "git_commit_and_push")
        self.assertEqual(row["category"], "git_write")
        self.assertEqual(row["result"], "hygiene_failed")
        self.assertEqual(row["paths"], ["CLAUDE.md"])


if __name__ == "__main__":
    unittest.main()
