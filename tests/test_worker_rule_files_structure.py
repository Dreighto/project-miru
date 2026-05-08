"""Structural validation tests for CLAUDE.md and AGENTS.md.

This PR replaced the MIRU-INSTRUCTIONS-v2 slim architecture (where content
was spread across .miru/overlays/ and .miru/reference/ files) with a single
expanded CLAUDE.md and AGENTS.md containing all rules inline.

These tests verify:
  1. CLAUDE.md has the correct new title and does NOT carry the old
     MIRU-INSTRUCTIONS-v2 architecture version stamp.
  2. Critical sections that were previously in overlays are now present
     in CLAUDE.md (fail-closed directive, repo boundary, kill switch,
     completion marker schema, merge policy, linear projects table, etc.)
  3. AGENTS.md contains the new sections added in this PR:
     - Automated PR Review Completion Sequence (moved from overlay)
     - gh CLI Auth Bootstrap (moved from overlay)
     - Return-to-main Hard Rule (moved from overlay)
     - Code Craft and Self-Review Instinct (new, set 2026-05-08)
     - WIP Commit Checkpoints (moved from overlay)
  4. AGENTS.md does NOT contain the old MIRU-INSTRUCTIONS-v2 stamp.
  5. Key identifiers (project IDs, port numbers, tool names) referenced in
     both files remain consistent across both files.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class ClaudeMdExistsTests(unittest.TestCase):
    def test_claude_md_exists(self):
        self.assertTrue(CLAUDE_MD.exists(), f"CLAUDE.md missing at {CLAUDE_MD}")

    def test_agents_md_exists(self):
        self.assertTrue(AGENTS_MD.exists(), f"AGENTS.md missing at {AGENTS_MD}")


class ClaudeMdTitleAndArchitectureTests(unittest.TestCase):
    """CLAUDE.md should have expanded title and no MIRU-INSTRUCTIONS-v2 stamp."""

    def setUp(self):
        self.text = _read(CLAUDE_MD)

    def test_title_is_expanded_form(self):
        """After PR, CLAUDE.md title is 'Claude Chat + Claude Code — Project Miru'."""
        self.assertIn("Claude Chat + Claude Code", self.text)

    def test_no_miru_instructions_v2_stamp(self):
        """MIRU-INSTRUCTIONS-v2 was the slim overlay architecture that was removed."""
        self.assertNotIn(
            "MIRU-INSTRUCTIONS-v2",
            self.text,
            "MIRU-INSTRUCTIONS-v2 stamp found — slim overlay architecture was removed in this PR",
        )

    def test_no_discovery_index_pointer_to_miru_overlays(self):
        """After the rewrite, rules live inline — no need for .miru/overlays/ discovery routing."""
        self.assertNotIn(
            ".miru/overlays/",
            self.text,
            ".miru/overlays/ reference found — overlay architecture was removed in this PR",
        )


class ClaudeMdCriticalSectionsTests(unittest.TestCase):
    """All critical rule sections must be present inline in CLAUDE.md."""

    def setUp(self):
        self.text = _read(CLAUDE_MD)

    def test_copy_paste_hard_rule_present(self):
        self.assertIn("Copy-paste content for manual routing", self.text)

    def test_copy_paste_rule_requires_fenced_code_block(self):
        self.assertIn("fenced code block", self.text)

    def test_repo_boundary_section_present(self):
        self.assertIn("Repo Boundary", self.text)

    def test_repo_boundary_references_canonical_repo(self):
        self.assertIn("Dreighto/project-miru", self.text)

    def test_kill_switch_section_present(self):
        self.assertIn("Kill Switch", self.text)

    def test_kill_switch_references_check_script(self):
        self.assertIn("check_kill_switch.py", self.text)

    def test_kill_switch_escalate_response_present(self):
        self.assertIn("ESCALATE: HUMAN-REQUIRED", self.text)

    def test_worktree_cleanliness_gate_present(self):
        self.assertIn("Worktree Cleanliness Gate", self.text)

    def test_worktree_cleanliness_references_check_script(self):
        self.assertIn("check_worktree_clean.py", self.text)

    def test_no_overlap_rule_present(self):
        self.assertIn("No Overlap", self.text)

    def test_linear_ticket_routing_hard_rule_present(self):
        self.assertIn("Linear", self.text)
        self.assertIn("projectId", self.text)

    def test_linear_projects_table_present(self):
        """The inline projects table must list the key Project Miru projects."""
        self.assertIn("PM Storefront", self.text)
        self.assertIn("Miru Orchestration / Autonomy", self.text)
        self.assertIn("Tooling / MCP Gateway", self.text)
        self.assertIn("Automation / Integrations", self.text)
        self.assertIn("Memory / Context System", self.text)
        self.assertIn("Docs / Canon / Process", self.text)

    def test_legacy_project_id_warned_against(self):
        """The old catch-all projectId must be marked as never-use."""
        self.assertIn("7c2b40d5-058a-457d-84c7-d57d6bf3f281", self.text)
        self.assertIn("Never use", self.text)

    def test_notion_read_write_rules_present(self):
        self.assertIn("Notion", self.text)
        self.assertIn("Read/Write", self.text)

    def test_adopted_lessons_section_present(self):
        self.assertIn("Adopted Lessons", self.text)

    def test_js_workflow_json_test_rule_present(self):
        """PRO-189 lesson: test JS as it lives in workflow JSON."""
        self.assertIn("jsCode", self.text)
        self.assertIn("fs.readFileSync", self.text)

    def test_pr_merge_policy_section_present(self):
        self.assertIn("PR Merge Policy", self.text)

    def test_merge_policy_has_three_tiers(self):
        """Merge policy has direct-to-main, CC-merges, operator-merges."""
        self.assertIn("direct to main", self.text.lower())
        self.assertIn("CC merges", self.text)
        self.assertIn("Operator merges", self.text)

    def test_append_only_data_files_rule_present(self):
        self.assertIn("append-only", self.text.lower())
        self.assertIn("cc_completion_log.jsonl", self.text)

    def test_completion_marker_schema_present(self):
        self.assertIn("completion-marker", self.text.lower().replace(" ", "-"))

    def test_test_evidence_format_rules_present(self):
        """The machine-parseable test_evidence format rules must be present."""
        self.assertIn("test_evidence", self.text)
        self.assertIn("ci_only:", self.text)
        self.assertIn("no_tests", self.text)

    def test_test_evidence_regex_hint_present(self):
        """CLAUDE.md must document the regex that parsers use."""
        self.assertIn(r"(\d+)\s*/\s*(\d+)", self.text)

    def test_ports_reference_table_present(self):
        self.assertIn("18080", self.text)
        self.assertIn("18765", self.text)
        self.assertIn("8765", self.text)

    def test_fail_closed_or_stop_ask_rule_present(self):
        """Fail-closed or 'STOP and ask' directive must appear."""
        has_fail_closed = "Fail-Closed" in self.text or "fail-closed" in self.text.lower()
        has_stop_ask = "STOP" in self.text and "ask" in self.text.lower()
        self.assertTrue(
            has_fail_closed or has_stop_ask,
            "Neither 'Fail-Closed' nor 'STOP and ask' found in CLAUDE.md",
        )


class ClaudeMdReturnToMainTests(unittest.TestCase):
    """Return-to-main rule must be present (moved from AGENTS.md overlay)."""

    def setUp(self):
        self.claude_text = _read(CLAUDE_MD)
        self.agents_text = _read(AGENTS_MD)

    def test_return_to_main_rule_present_in_agents(self):
        self.assertIn("Return-to-main", self.agents_text)

    def test_return_to_main_rule_mentions_git_checkout_main(self):
        self.assertIn("git checkout main", self.agents_text)


class AgentsMdTitleAndArchitectureTests(unittest.TestCase):
    """AGENTS.md should have updated header and no MIRU-INSTRUCTIONS-v2 stamp."""

    def setUp(self):
        self.text = _read(AGENTS_MD)

    def test_agents_md_has_worker_baseline_title(self):
        self.assertIn("Worker Baseline", self.text)

    def test_no_miru_instructions_v2_stamp(self):
        self.assertNotIn(
            "MIRU-INSTRUCTIONS-v2",
            self.text,
            "MIRU-INSTRUCTIONS-v2 stamp found — slim overlay architecture was removed in this PR",
        )

    def test_team_charter_read_directive_present(self):
        self.assertIn("miru-context/team-charter.md", self.text)


class AgentsMdNewSectionsTests(unittest.TestCase):
    """New sections added by this PR must be present in AGENTS.md."""

    def setUp(self):
        self.text = _read(AGENTS_MD)

    def test_operator_communication_standard_present(self):
        self.assertIn("Operator Communication Standard", self.text)

    def test_operator_comm_standard_has_required_format_block(self):
        """The mandatory plain-English summary format must be present."""
        self.assertIn("What happened:", self.text)
        self.assertIn("Does it work:", self.text)
        self.assertIn("What you need to do:", self.text)

    def test_automated_pr_review_completion_sequence_present(self):
        """Moved from .miru/overlays/workflow-git.md in this PR."""
        self.assertIn("Automated PR Review Completion Sequence", self.text)

    def test_pr_review_sequence_references_coderabbit(self):
        self.assertIn("CodeRabbit", self.text)

    def test_pr_review_sequence_references_bugbot(self):
        self.assertIn("Bugbot", self.text)

    def test_pr_review_sequence_has_5_steps(self):
        """The sequence requires exactly 5 steps."""
        # Look for Step 1 through Step 5
        for step_num in range(1, 6):
            self.assertIn(
                f"Step {step_num}",
                self.text,
                f"PR Review Completion Sequence is missing Step {step_num}",
            )

    def test_pr_review_sequence_10_minute_timeout_rule(self):
        self.assertIn("10 minutes", self.text)

    def test_gh_cli_auth_bootstrap_section_present(self):
        """Moved from .miru/overlays/workflow-git.md in this PR."""
        self.assertIn("gh CLI Auth Bootstrap", self.text)

    def test_gh_cli_auth_bootstrap_has_command(self):
        self.assertIn("gh auth login", self.text)

    def test_gh_cli_auth_bootstrap_references_room_token(self):
        self.assertIn("ROOM_TOKEN_OPERATOR", self.text)

    def test_return_to_main_hard_rule_present(self):
        """Moved from .miru/overlays/workflow-git.md in this PR."""
        self.assertIn("Return-to-main", self.text)

    def test_return_to_main_has_confirmed_working_branch(self):
        """After CONFIRMED_WORKING, workers must checkout main."""
        self.assertIn("CONFIRMED_WORKING", self.text)
        self.assertIn("git checkout main", self.text)

    def test_return_to_main_has_inconclusive_branch(self):
        """After INCONCLUSIVE/FAILED, workers must stash and checkout main."""
        self.assertIn("INCONCLUSIVE", self.text)
        self.assertIn("git stash", self.text)

    def test_try_harder_discipline_present(self):
        """Pre-existing section that must remain after the rewrite."""
        self.assertIn("Try Harder Discipline", self.text)

    def test_code_craft_self_review_section_present(self):
        """New section added in this PR (set 2026-05-08)."""
        self.assertIn("Code Craft and Self-Review Instinct", self.text)

    def test_code_craft_section_date_stamp(self):
        """The Code Craft section is stamped 2026-05-08 (the date of this PR)."""
        self.assertIn("2026-05-08", self.text)

    def test_code_craft_has_six_step_loop(self):
        """The self-review loop requires 6 steps (numbered 1–6)."""
        # Each step is listed as a numbered item: "1." through "6."
        for step_num in range(1, 7):
            # Look for the step number in the Code Craft section
            # The steps appear as "1. **Understand" etc.
            pattern = rf"\b{step_num}\.\s+\*\*"
            self.assertTrue(
                re.search(pattern, self.text),
                f"Code Craft self-review loop step {step_num} not found",
            )

    def test_code_craft_step1_understand_contract(self):
        self.assertIn("Understand the contract", self.text)

    def test_code_craft_step2_clean_implementation(self):
        self.assertIn("Choose the clean implementation", self.text)

    def test_code_craft_step3_test_behavior(self):
        self.assertIn("Test the behavior that matters", self.text)

    def test_code_craft_step4_run_checks_locally(self):
        self.assertIn("Run the right checks locally", self.text)

    def test_code_craft_step5_self_review_diff(self):
        self.assertIn("Self-review the diff", self.text)

    def test_code_craft_step6_fix_what_review_finds(self):
        self.assertIn("Fix what the review finds", self.text)

    def test_code_craft_discipline_violation_language(self):
        """Skipping the loop is explicitly a discipline violation."""
        self.assertIn("discipline violation", self.text)

    def test_wip_commit_checkpoints_section_present(self):
        """Moved from .miru/overlays/workflow-git.md in this PR."""
        self.assertIn("WIP Commit Checkpoints", self.text)

    def test_wip_commit_format_present(self):
        """The WIP commit message format must be documented."""
        self.assertIn("WIP:", self.text)

    def test_wip_commit_squash_before_pr_rule(self):
        """WIP commits must be squashed before opening a PR."""
        self.assertIn("Squash before PR", self.text)
        self.assertIn("WIP prefix must not appear", self.text)

    def test_wip_commit_checkpoints_references_pro318(self):
        self.assertIn("PRO-318", self.text)


class AgentsMdConsistencyWithClaudeMdTests(unittest.TestCase):
    """Cross-file consistency: key identifiers referenced in AGENTS.md
    should appear consistently with CLAUDE.md where they overlap."""

    def setUp(self):
        self.claude = _read(CLAUDE_MD)
        self.agents = _read(AGENTS_MD)

    def test_both_files_reference_confirmed_working_status(self):
        self.assertIn("CONFIRMED_WORKING", self.claude)
        self.assertIn("CONFIRMED_WORKING", self.agents)

    def test_both_files_reference_cc_completion_log(self):
        self.assertIn("cc_completion_log.jsonl", self.claude)
        self.assertIn("cc_completion_log.jsonl", self.agents)

    def test_both_files_reference_pre_commit_hygiene(self):
        has_claude = "pre-commit" in self.claude.lower()
        has_agents = "pre-commit" in self.agents.lower()
        self.assertTrue(has_claude, "pre-commit not referenced in CLAUDE.md")
        self.assertTrue(has_agents, "pre-commit not referenced in AGENTS.md")

    def test_team_charter_referenced_in_agents(self):
        self.assertIn("team-charter.md", self.agents)

    def test_agents_references_worker_framework(self):
        """AGENTS.md header declares its framework source after the rewrite."""
        self.assertIn("worker-framework", self.agents)


class ClaudeMdMiruInstructionsV2RemovedTests(unittest.TestCase):
    """The .miru/ overlay/reference system was removed in this PR.
    These tests confirm the removed infrastructure is gone."""

    def setUp(self):
        self.claude = _read(CLAUDE_MD)
        self.agents = _read(AGENTS_MD)

    def test_claude_md_has_no_overlay_load_when_directive(self):
        """Overlay 'Load when:' directives were in .miru/overlays/ files, now removed."""
        self.assertNotIn("Load when:", self.claude)

    def test_agents_md_has_no_overlay_load_when_directive(self):
        self.assertNotIn("Load when:", self.agents)

    def test_claude_md_has_no_discovery_index_header(self):
        """The 'Discovery Index' was the routing table in the slim CLAUDE.md; it's gone."""
        self.assertNotIn("Discovery Index", self.claude)

    def test_claude_md_does_not_reference_instruction_manifest_json(self):
        self.assertNotIn("instruction_manifest.json", self.claude)

    def test_agents_md_does_not_reference_instruction_manifest_json(self):
        self.assertNotIn("instruction_manifest.json", self.agents)


if __name__ == "__main__":
    unittest.main()
