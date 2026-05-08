"""DGAS Tier 2 #8: fault-injection tests for the pre-push hook.

Verifies the gate fires for every prohibited operation and stays silent for
every legitimate one. Without these, the hook is theatre — see synthesis #7.

Coverage:
    * Fast-forward push to main: allowed
    * Force-push to main: refused
    * Branch-delete on main: refused
    * Push to master: refused (force-push)
    * Push to release/<x>: refused (force-push)
    * New protected branch (remote_sha == zero): allowed
    * Force-push to a non-protected branch: allowed (we don't gate dev branches)
    * Malformed stdin: refused (fail-closed)
    * Multiple ref pairs in one push: refuses on any single bad pair
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import check_safe_git_push as hook  # noqa: E402

NULL = hook.NULL_SHA
ABCDEF = "abc1234567" + "0" * 30  # 40 chars, not all zero
DEF456 = "def4567890" + "0" * 30


class TestProtectedBranchDetection(unittest.TestCase):
    """The protected-pattern set must match exactly the canonical refs."""

    def test_main_is_protected(self) -> None:
        self.assertTrue(hook.is_protected("refs/heads/main"))

    def test_master_is_protected(self) -> None:
        self.assertTrue(hook.is_protected("refs/heads/master"))

    def test_release_branches_are_protected(self) -> None:
        self.assertTrue(hook.is_protected("refs/heads/release/2026.05"))
        self.assertTrue(hook.is_protected("refs/heads/release/v1"))

    def test_feature_branches_are_not_protected(self) -> None:
        self.assertFalse(hook.is_protected("refs/heads/dreighto/dgas-foo"))
        self.assertFalse(hook.is_protected("refs/heads/feature/anything"))

    def test_a_branch_named_with_main_inside_is_not_protected(self) -> None:
        # Only an exact match for main/master is protected; a branch
        # incidentally containing the word does not get the gate applied.
        self.assertFalse(hook.is_protected("refs/heads/feature/main-thing"))


class TestCheckRefPair(unittest.TestCase):
    """Decision matrix for single ref pairs against `check_ref_pair`."""

    def test_fast_forward_to_main_is_allowed(self) -> None:
        with mock.patch.object(hook, "is_descendant", return_value=True):
            result = hook.check_ref_pair("refs/heads/main", ABCDEF, "refs/heads/main", DEF456)
        self.assertIsNone(result)

    def test_force_push_to_main_is_refused(self) -> None:
        with mock.patch.object(hook, "is_descendant", return_value=False):
            result = hook.check_ref_pair("refs/heads/main", ABCDEF, "refs/heads/main", DEF456)
        self.assertIsNotNone(result)
        self.assertIn("force-push", result or "")
        self.assertIn("'refs/heads/main'", result or "")

    def test_branch_delete_on_main_is_refused(self) -> None:
        result = hook.check_ref_pair("(deleted)", NULL, "refs/heads/main", DEF456)
        self.assertIsNotNone(result)
        self.assertIn("delete", (result or "").lower())

    def test_force_push_to_master_is_refused(self) -> None:
        with mock.patch.object(hook, "is_descendant", return_value=False):
            result = hook.check_ref_pair("refs/heads/master", ABCDEF, "refs/heads/master", DEF456)
        self.assertIsNotNone(result)

    def test_force_push_to_release_branch_is_refused(self) -> None:
        with mock.patch.object(hook, "is_descendant", return_value=False):
            result = hook.check_ref_pair(
                "refs/heads/release/v1", ABCDEF, "refs/heads/release/v1", DEF456
            )
        self.assertIsNotNone(result)

    def test_new_protected_branch_is_allowed(self) -> None:
        """remote_sha == zero means the branch doesn't exist remotely yet —
        nothing to force over, so the push is legitimate."""
        result = hook.check_ref_pair("refs/heads/release/v2", ABCDEF, "refs/heads/release/v2", NULL)
        self.assertIsNone(result)

    def test_force_push_to_feature_branch_is_allowed(self) -> None:
        """We don't gate dev branches — workers rebase feature branches and
        force-push to the same branch all the time. Only protected branches
        get the gate."""
        with mock.patch.object(hook, "is_descendant", return_value=False):
            result = hook.check_ref_pair(
                "refs/heads/dreighto/foo", ABCDEF, "refs/heads/dreighto/foo", DEF456
            )
        self.assertIsNone(result)


class TestMain(unittest.TestCase):
    """End-to-end main() test using mocked stdin and the descendant probe."""

    def _run(self, stdin_text: str, descendant: bool = True) -> int:
        with (
            mock.patch.object(sys, "stdin", io.StringIO(stdin_text)),
            mock.patch.object(hook, "is_descendant", return_value=descendant),
        ):
            # When stdin is a StringIO, isatty() returns False — exactly
            # what main() checks for.
            return hook.main(["origin", "https://github.com/foo/bar.git"])

    def test_empty_stdin_passes(self) -> None:
        self.assertEqual(self._run(""), 0)

    def test_fast_forward_passes(self) -> None:
        line = f"refs/heads/main {ABCDEF} refs/heads/main {DEF456}\n"
        self.assertEqual(self._run(line, descendant=True), 0)

    def test_force_push_blocks(self) -> None:
        line = f"refs/heads/main {ABCDEF} refs/heads/main {DEF456}\n"
        self.assertEqual(self._run(line, descendant=False), 1)

    def test_delete_main_blocks(self) -> None:
        line = f"(deleted) {NULL} refs/heads/main {DEF456}\n"
        self.assertEqual(self._run(line), 1)

    def test_multiple_ref_pairs_one_bad_blocks(self) -> None:
        good = f"refs/heads/dreighto/foo {ABCDEF} refs/heads/dreighto/foo {DEF456}\n"
        bad = f"(deleted) {NULL} refs/heads/main {DEF456}\n"
        self.assertEqual(self._run(good + bad), 1)

    def test_malformed_input_is_refused(self) -> None:
        """Fail closed on input we can't parse rather than silently allowing."""
        self.assertEqual(self._run("garbage line with too few fields\n"), 1)


class TestNullSha(unittest.TestCase):
    """Sanity: the NULL_SHA constant is what git actually uses."""

    def test_null_sha_is_40_zeros(self) -> None:
        self.assertEqual(hook.NULL_SHA, "0" * 40)
        self.assertEqual(len(hook.NULL_SHA), 40)


if __name__ == "__main__":
    unittest.main()
