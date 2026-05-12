"""DGAS Tier 2 #9: fault-injection tests for the governance change gate.

Verifies the gate fires for every governance file pattern and stays silent
for non-governance changes. Without these, the gate is theatre — see
synthesis item #7.

Coverage:
    * Each registry pattern (overlays, reference, profiles.py, pre-commit
      config, check_*.py, validate_*.py, w2 profile rules, gatekeeper, etc.)
      matches as expected.
    * Non-registry paths do NOT match (no false positives).
    * PR body opt-in detection: missing token, present token, missing
      explanation heading, empty explanation body.
    * end-to-end main(): governance hit + missing opt-in => exit 1;
      governance hit + full opt-in => exit 0; non-governance change => exit 0.
"""

from __future__ import annotations

import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import check_governance_change as gov  # noqa: E402

APPROVAL_BLOCK = """\
## Summary

Some prose describing what changes.

GOVERNANCE_CHANGE_APPROVED=true

## What does this allow that wasn't allowed before?

This change adds X to the deny-list, which means workers can no longer Y.
The trust surface shrinks; nothing new is permitted.
"""


class TestRegistryMatching(unittest.TestCase):
    def test_overlays_match(self) -> None:
        for path in (
            ".miru/overlays/workflow-git.md",
            ".miru/overlays/domain-ui.md",
            ".miru/overlays/nested/deep.md",
        ):
            self.assertIsNotNone(gov.matches_registry(path), f"should match: {path}")

    def test_reference_match(self) -> None:
        for path in (
            ".miru/reference/linear-projects.md",
            ".miru/reference/file-placement.md",
        ):
            self.assertIsNotNone(gov.matches_registry(path))

    def test_profiles_py_match(self) -> None:
        self.assertIsNotNone(gov.matches_registry("tools/miru_mcp_gateway/profiles.py"))

    def test_precommit_config_match(self) -> None:
        self.assertIsNotNone(gov.matches_registry(".pre-commit-config.yaml"))

    def test_check_and_validate_scripts_match(self) -> None:
        for path in (
            "tools/check_kill_switch.py",
            "tools/check_worktree_clean.py",
            "tools/check_safe_git_push.py",
            "tools/validate_jsonl.py",
            "tools/validate_n8n_workflow.py",
            "tools/validate_instruction_migration.py",
        ):
            self.assertIsNotNone(gov.matches_registry(path), f"should match: {path}")

    def test_w2_profile_rules_match(self) -> None:
        self.assertIsNotNone(gov.matches_registry("data/config/w2_profile_rules.json"))

    def test_gatekeeper_match(self) -> None:
        self.assertIsNotNone(gov.matches_registry("gatekeeper/anything.py"))
        self.assertIsNotNone(gov.matches_registry("gatekeeper/dispatch/forwarder.js"))

    def test_codeowners_and_workflow_match(self) -> None:
        self.assertIsNotNone(gov.matches_registry(".github/CODEOWNERS"))
        self.assertIsNotNone(gov.matches_registry(".github/workflows/governance-check.yml"))

    def test_check_governance_change_self_match(self) -> None:
        """Recursive protection: changes to the gate script itself are gated."""
        self.assertIsNotNone(gov.matches_registry("tools/check_governance_change.py"))

    def test_miru_context_match(self) -> None:
        """team-charter.md and other miru-context/ files are read at every
        dispatch (per CLAUDE.md sec. Repo Boundary). Modifying them silently
        propagates to every worker session — same trust surface as the
        overlay/reference files. Added 2026-05-12 after the LOS-34/35
        untie-from-miru sweep flagged the gap.
        """
        for path in (
            "miru-context/team-charter.md",
            "miru-context/role-briefs/cc-vp-ops.md",
            "miru-context/role-briefs/gemini-frontend.md",
            "miru-context/linear-triage-framework.md",
            "miru-context/nested/deep/file.md",
        ):
            self.assertIsNotNone(
                gov.matches_registry(path), f"should match miru-context/**: {path}"
            )

    def test_non_governance_paths_do_not_match(self) -> None:
        for path in (
            "tools/emit_completion.py",
            "tools/audit_chain.py",
            "tests/test_anything.py",
            "pm/app.py",
            "miru_ai/workers/foo.py",
            "docs/architecture/overview.md",
            "README.md",
            "data/cc_completion_log.jsonl",
            ".github/workflows/hygiene.yml",
            "tools/orchestrator/stall_detector.py",
        ):
            self.assertIsNone(gov.matches_registry(path), f"should NOT match: {path}")

    def test_windows_path_separators_normalized(self) -> None:
        self.assertIsNotNone(gov.matches_registry(".miru\\overlays\\workflow-git.md"))


class TestPrBodyValidation(unittest.TestCase):
    def test_full_approval_passes(self) -> None:
        errors = gov.check_pr_body(APPROVAL_BLOCK)
        self.assertEqual(errors, [])

    def test_missing_approval_token_fails(self) -> None:
        body = APPROVAL_BLOCK.replace("GOVERNANCE_CHANGE_APPROVED=true", "")
        errors = gov.check_pr_body(body)
        self.assertTrue(any("GOVERNANCE_CHANGE_APPROVED=true" in e for e in errors))

    def test_missing_explanation_heading_fails(self) -> None:
        body = APPROVAL_BLOCK.replace(
            "## What does this allow that wasn't allowed before?",
            "## Some other heading",
        )
        errors = gov.check_pr_body(body)
        self.assertTrue(any("section titled" in e for e in errors))

    def test_empty_explanation_section_fails(self) -> None:
        body = (
            "GOVERNANCE_CHANGE_APPROVED=true\n\n"
            "## What does this allow that wasn't allowed before?\n\n\n"
        )
        errors = gov.check_pr_body(body)
        self.assertTrue(any("empty" in e for e in errors))

    def test_curly_apostrophe_is_accepted(self) -> None:
        """GitHub Markdown often smart-quotes apostrophes. The regex must
        match the curly form too so legitimate PR bodies don't get rejected."""
        body = (
            "GOVERNANCE_CHANGE_APPROVED=true\n\n"
            "## What does this allow that wasn’t allowed before?\n\n"  # noqa: RUF001
            "Adds a new MCP tool category with a corresponding deny-list entry.\n"
        )
        errors = gov.check_pr_body(body)
        self.assertEqual(errors, [])


class TestMain(unittest.TestCase):
    def _run(self, changed: list[str], pr_body: str) -> int:
        # Pass via env vars so we don't have to write a temp file each run.
        env = {
            "MIRU_GOV_CHANGED_FILES": "\n".join(changed),
            "MIRU_GOV_PR_BODY": pr_body,
        }
        with (
            mock.patch.dict(os.environ, env, clear=False),
            mock.patch.object(sys, "argv", ["check_governance_change.py"]),
            mock.patch.object(sys, "stdin", io.StringIO("")),
        ):
            return gov.main()

    def test_no_governance_change_passes_with_empty_body(self) -> None:
        self.assertEqual(self._run(["pm/app.py", "tools/emit_completion.py"], ""), 0)

    def test_governance_change_with_full_approval_passes(self) -> None:
        self.assertEqual(
            self._run([".miru/overlays/workflow-git.md"], APPROVAL_BLOCK),
            0,
        )

    def test_governance_change_without_token_fails(self) -> None:
        body = APPROVAL_BLOCK.replace("GOVERNANCE_CHANGE_APPROVED=true", "")
        self.assertEqual(
            self._run([".pre-commit-config.yaml"], body),
            1,
        )

    def test_governance_change_without_explanation_fails(self) -> None:
        body = "GOVERNANCE_CHANGE_APPROVED=true\n\n(no explanation section)\n"
        self.assertEqual(
            self._run(["tools/miru_mcp_gateway/profiles.py"], body),
            1,
        )

    def test_mixed_changes_governance_dominates(self) -> None:
        """Touching one governance file in a 100-file PR still triggers the gate."""
        non_gov_files = [f"pm/app{i}.py" for i in range(50)]
        self.assertEqual(
            self._run([*non_gov_files, ".miru/reference/ports-and-services.md"], ""),
            1,
        )


if __name__ == "__main__":
    unittest.main()
