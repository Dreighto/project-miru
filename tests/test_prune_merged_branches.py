"""Tests for tools/prune_merged_branches.py."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import prune_merged_branches as mod  # noqa: E402

# --- Helpers ---

SAMPLE_BRANCH_VV = """\
+ _parking_w1                   65cbeef (D:/dev/miru-w1) chore: old
  _parking_w2                   ca6a9f9 docs(agents): old
  dreighto/pro-200-old-feature  7230b64 [origin/dreighto/pro-200-old-feature: gone] feat: old
  dreighto/pro-201-another      69daa92 [origin/dreighto/pro-201-another: gone] feat: another
+ dreighto/pro-216-in-worktree  e314aba (D:/dev/miru-cursor) [origin/dreighto/pro-216-in-worktree: gone] feat: wt
* main                          5ce79f2 [origin/main] latest
  dreighto/active-remote        bef8640 [origin/dreighto/active-remote] feat: active
  dreighto/stale-no-pr          aaaaaaa [origin/dreighto/stale-no-pr: gone] feat: orphan
"""


def _mock_run_branch_vv(cmd, **kwargs):
    result = MagicMock()
    result.returncode = 0
    result.stdout = SAMPLE_BRANCH_VV
    return result


# --- Tests ---


class TestIsProtected(unittest.TestCase):
    def test_main_protected(self):
        self.assertTrue(mod._is_protected("main"))

    def test_develop_protected(self):
        self.assertTrue(mod._is_protected("develop"))

    def test_parking_protected(self):
        self.assertTrue(mod._is_protected("_parking_w1"))
        self.assertTrue(mod._is_protected("_parking_w2"))

    def test_feature_not_protected(self):
        self.assertFalse(mod._is_protected("dreighto/pro-200-feature"))

    def test_main_substring_not_protected(self):
        self.assertFalse(mod._is_protected("fix-main-page"))


class TestListLocalBranches(unittest.TestCase):
    @patch("prune_merged_branches.subprocess.run", side_effect=_mock_run_branch_vv)
    def test_parses_branches(self, mock_run):
        branches = mod.list_local_branches("/fake")
        names = [b["name"] for b in branches]

        self.assertIn("main", names)
        self.assertIn("dreighto/pro-200-old-feature", names)
        self.assertIn("dreighto/pro-216-in-worktree", names)

    @patch("prune_merged_branches.subprocess.run", side_effect=_mock_run_branch_vv)
    def test_detects_remote_gone(self, mock_run):
        branches = mod.list_local_branches("/fake")
        by_name = {b["name"]: b for b in branches}

        self.assertTrue(by_name["dreighto/pro-200-old-feature"]["remote_gone"])
        self.assertTrue(by_name["dreighto/pro-201-another"]["remote_gone"])
        self.assertFalse(by_name["dreighto/active-remote"]["remote_gone"])
        self.assertFalse(by_name["main"]["remote_gone"])

    @patch("prune_merged_branches.subprocess.run", side_effect=_mock_run_branch_vv)
    def test_detects_worktree(self, mock_run):
        branches = mod.list_local_branches("/fake")
        by_name = {b["name"]: b for b in branches}

        self.assertTrue(by_name["_parking_w1"]["in_worktree"])
        self.assertTrue(by_name["dreighto/pro-216-in-worktree"]["in_worktree"])
        self.assertFalse(by_name["dreighto/pro-200-old-feature"]["in_worktree"])

    @patch("prune_merged_branches.subprocess.run", side_effect=_mock_run_branch_vv)
    def test_detects_current(self, mock_run):
        branches = mod.list_local_branches("/fake")
        by_name = {b["name"]: b for b in branches}

        self.assertTrue(by_name["main"]["is_current"])
        self.assertFalse(by_name["dreighto/pro-200-old-feature"]["is_current"])


class TestFindCandidates(unittest.TestCase):
    @patch("prune_merged_branches.subprocess.run", side_effect=_mock_run_branch_vv)
    def test_finds_correct_candidates(self, mock_run):
        branches = mod.list_local_branches("/fake")
        candidates = mod.find_candidates(branches)
        names = [c["name"] for c in candidates]

        # Should include: remote gone + not in worktree + not current + not protected
        self.assertIn("dreighto/pro-200-old-feature", names)
        self.assertIn("dreighto/pro-201-another", names)
        self.assertIn("dreighto/stale-no-pr", names)

        # Should exclude:
        self.assertNotIn("main", names)  # current + protected
        self.assertNotIn("_parking_w1", names)  # in worktree + protected
        self.assertNotIn("_parking_w2", names)  # protected
        self.assertNotIn("dreighto/pro-216-in-worktree", names)  # in worktree
        self.assertNotIn("dreighto/active-remote", names)  # remote not gone


class TestVerifyPrMerged(unittest.TestCase):
    def test_returns_pr_info_when_merged(self):
        pr_data = [{"number": 42, "title": "Fix stuff", "mergedAt": "2026-05-07T00:00:00Z"}]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(pr_data)

        with patch("prune_merged_branches.subprocess.run", return_value=mock_result):
            result = mod.verify_pr_merged("dreighto/pro-200-feature", "Dreighto/project-miru")

        self.assertIsNotNone(result)
        self.assertEqual(result["number"], 42)

    def test_returns_none_when_no_pr(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "[]"

        with patch("prune_merged_branches.subprocess.run", return_value=mock_result):
            result = mod.verify_pr_merged("dreighto/orphan", "Dreighto/project-miru")

        self.assertIsNone(result)

    def test_returns_none_on_command_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("prune_merged_branches.subprocess.run", return_value=mock_result):
            result = mod.verify_pr_merged("dreighto/orphan", "Dreighto/project-miru")

        self.assertIsNone(result)


class TestPruneDryRun(unittest.TestCase):
    def _make_run_side_effect(self):
        tip_sha = "abc1234567890"
        pr_data = [
            {
                "number": 99,
                "title": "Test PR",
                "mergedAt": "2026-05-07T00:00:00Z",
                "headRefOid": tip_sha,
            }
        ]

        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "git" and cmd[1] == "fetch":
                result.returncode = 0
                return result
            if cmd[0] == "git" and cmd[1] == "rev-parse":
                result.returncode = 0
                result.stdout = tip_sha
                return result
            if cmd[0] == "git" and cmd[1] == "branch":
                result.returncode = 0
                result.stdout = SAMPLE_BRANCH_VV
                return result
            if cmd[0] == "gh":
                result.returncode = 0
                branch_arg = cmd[cmd.index("--head") + 1]
                if branch_arg == "dreighto/stale-no-pr":
                    result.stdout = "[]"
                else:
                    result.stdout = json.dumps(pr_data)
                return result
            result.returncode = 1
            return result

        return side_effect

    @patch("prune_merged_branches.subprocess.run")
    def test_dry_run_reports_would_delete(self, mock_run):
        mock_run.side_effect = self._make_run_side_effect()

        actions = mod.prune(dry_run=True, cwd="/fake")

        would_delete = [a for a in actions if a["action"] == "would_delete"]
        skipped = [a for a in actions if a["action"] == "skipped"]

        self.assertEqual(len(would_delete), 2)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["branch"], "dreighto/stale-no-pr")

    @patch("prune_merged_branches.subprocess.run")
    def test_dry_run_never_calls_force_delete(self, mock_run):
        mock_run.side_effect = self._make_run_side_effect()

        mod.prune(dry_run=True, cwd="/fake")

        for call in mock_run.call_args_list:
            cmd = call[0][0] if call[0] else call[1].get("cmd", [])
            if isinstance(cmd, list) and "git" in cmd and "-D" in cmd:
                self.fail("dry_run should never call git branch -D")


class TestPruneExecute(unittest.TestCase):
    def _make_run_side_effect(self):
        tip_sha = "def4567890abc"
        pr_data = [
            {
                "number": 50,
                "title": "Merged PR",
                "mergedAt": "2026-05-07T00:00:00Z",
                "headRefOid": tip_sha,
            }
        ]

        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "git" and cmd[1] == "fetch":
                result.returncode = 0
                return result
            if cmd[0] == "git" and cmd[1] == "rev-parse":
                result.returncode = 0
                result.stdout = tip_sha
                return result
            if cmd[0] == "git" and cmd[1] == "branch" and "-D" in cmd:
                result.returncode = 0
                return result
            if cmd[0] == "git" and cmd[1] == "branch":
                result.returncode = 0
                result.stdout = SAMPLE_BRANCH_VV
                return result
            if cmd[0] == "gh":
                result.returncode = 0
                branch_arg = cmd[cmd.index("--head") + 1]
                if branch_arg == "dreighto/stale-no-pr":
                    result.stdout = "[]"
                else:
                    result.stdout = json.dumps(pr_data)
                return result
            result.returncode = 1
            return result

        return side_effect

    @patch("prune_merged_branches.subprocess.run")
    def test_execute_deletes_verified_branches(self, mock_run):
        mock_run.side_effect = self._make_run_side_effect()

        actions = mod.prune(dry_run=False, cwd="/fake")

        deleted = [a for a in actions if a["action"] == "deleted"]
        skipped = [a for a in actions if a["action"] == "skipped"]

        self.assertEqual(len(deleted), 2)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(deleted[0]["pr_number"], 50)

    @patch("prune_merged_branches.subprocess.run")
    def test_execute_calls_force_delete_for_verified_only(self, mock_run):
        mock_run.side_effect = self._make_run_side_effect()

        mod.prune(dry_run=False, cwd="/fake")

        delete_calls = []
        for call in mock_run.call_args_list:
            cmd = call[0][0]
            if (
                isinstance(cmd, list)
                and len(cmd) >= 3
                and cmd[:2] == ["git", "branch"]
                and "-D" in cmd
            ):
                delete_calls.append(cmd[3])

        self.assertIn("dreighto/pro-200-old-feature", delete_calls)
        self.assertIn("dreighto/pro-201-another", delete_calls)
        self.assertNotIn("dreighto/stale-no-pr", delete_calls)


class TestPruneDivergedTip(unittest.TestCase):
    def _make_run_side_effect(self):
        pr_data = [
            {
                "number": 70,
                "title": "Old PR",
                "mergedAt": "2026-05-07T00:00:00Z",
                "headRefOid": "oldsha1234",
            }
        ]

        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "git" and cmd[1] == "fetch":
                result.returncode = 0
                return result
            if cmd[0] == "git" and cmd[1] == "rev-parse":
                result.returncode = 0
                result.stdout = "newsha5678"
                return result
            if cmd[0] == "git" and cmd[1] == "branch":
                result.returncode = 0
                result.stdout = (
                    "  dreighto/reused-branch  aaa1111 "
                    "[origin/dreighto/reused-branch: gone] feat: reused\n"
                    "* main  5ce79f2 [origin/main] latest\n"
                )
                return result
            if cmd[0] == "gh":
                result.returncode = 0
                result.stdout = json.dumps(pr_data)
                return result
            result.returncode = 1
            return result

        return side_effect

    @patch("prune_merged_branches.subprocess.run")
    def test_skips_branch_with_diverged_tip(self, mock_run):
        mock_run.side_effect = self._make_run_side_effect()

        actions = mod.prune(dry_run=False, cwd="/fake")

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "skipped")
        self.assertIn("diverged", actions[0]["reason"])


class TestVerifyPrMergedGhMissing(unittest.TestCase):
    def test_returns_none_when_gh_not_installed(self):
        with patch("prune_merged_branches.subprocess.run", side_effect=FileNotFoundError("gh")):
            result = mod.verify_pr_merged("dreighto/some-branch", "Dreighto/project-miru")

        self.assertIsNone(result)


class TestPruneBranchListFailure(unittest.TestCase):
    @patch("prune_merged_branches.subprocess.run")
    def test_aborts_when_branch_list_fails(self, mock_run):
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "git" and cmd[1] == "fetch":
                result.returncode = 0
                return result
            if cmd[0] == "git" and cmd[1] == "branch":
                result.returncode = 128
                return result
            result.returncode = 0
            return result

        mock_run.side_effect = side_effect

        actions = mod.prune(dry_run=False, cwd="/fake")

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "failed")
        self.assertIn("branch", actions[0]["reason"])


class TestPruneFetchFailure(unittest.TestCase):
    @patch("prune_merged_branches.subprocess.run")
    def test_aborts_when_fetch_prune_fails(self, mock_run):
        def side_effect(cmd, **kwargs):
            result = MagicMock()
            if cmd[0] == "git" and cmd[1] == "fetch":
                result.returncode = 1
                return result
            result.returncode = 0
            return result

        mock_run.side_effect = side_effect

        actions = mod.prune(dry_run=False, cwd="/fake")

        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "failed")
        self.assertIn("fetch", actions[0]["reason"])


class TestPruneNoCandidates(unittest.TestCase):
    @patch("prune_merged_branches.subprocess.run")
    def test_returns_empty_when_no_candidates(self, mock_run):
        clean_output = "* main  5ce79f2 [origin/main] latest\n"

        def side_effect(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if cmd[0] == "git" and cmd[1] == "branch":
                result.stdout = clean_output
            return result

        mock_run.side_effect = side_effect

        actions = mod.prune(dry_run=True, cwd="/fake")
        self.assertEqual(actions, [])


if __name__ == "__main__":
    unittest.main()
