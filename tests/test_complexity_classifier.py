"""Tests for tools/complexity_classifier.py (PRO-314).

Each fixture covers a distinct signal path. Fixtures are realistic ticket
shapes drawn from the Project Miru ticket history pattern.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_ORIG_SYS_PATH = list(sys.path)
try:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from complexity_classifier import classify_ticket
finally:
    sys.path[:] = _ORIG_SYS_PATH

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Simple single-concern bug fix -- no signals expected.
_FX_SIMPLE = {
    "title": "Fix null pointer exception in card lookup",
    "description": (
        "The card lookup throws a NullPointerException when card_id is None. "
        "Add a guard clause before the DB call."
    ),
}

# Touches miru_ai AND pm/storefront -- multi-service boundary.
_FX_MULTI_SERVICE = {
    "title": "Add sentiment scoring to miru_ai and display on pm dashboard frontend",
    "description": (
        "Wire a new sentiment scorer into miru ai (port 18765). "
        "Update the pm dashboard storefront to render the score on the card tile."
    ),
}

# Mixes .py Python files with n8n workflow JSON.
_FX_FILE_TYPE_MIX = {
    "title": "Update routing logic in n8n workflow JSON and fix Python dispatch handler",
    "description": (
        "Edit the n8n workflow .json to add a routing node. "
        "Fix the corresponding .py handler in miru_ai that processes the routed payload."
    ),
}

# Description has 4 distinct bullet-point tasks.
_FX_MULTI_TASK = {
    "title": "Sprint cleanup",
    "description": (
        "* Fix the broken test in test_miru_ai_server.py\n"
        "* Update the routing history schema documentation\n"
        "* Add pagination to the card grid component\n"
        "* Remove dead code from miru_brain.py"
    ),
}

# Single conjunction keyword -- weak signal only, should NOT split.
_FX_CONJUNCTION_ONLY = {
    "title": "Fix routing bug and update the docs",
    "description": "Fix the routing bug and also update the README section.",
}

# Two large-scope keywords -- fires scope_breadth signal (weight=1), stays medium alone.
_FX_DUAL_SCOPE = {
    "title": "Refactor and overhaul the miru_ai routing subsystem",
    "description": (
        "We need to refactor the routing subsystem from scratch and overhaul "
        "the associated test suite. This is a comprehensive rework."
    ),
}

# Conjunction (weight=1) + scope_breadth (weight=1) = 2 total -- triggers "high".
# Needs 2 scope keywords ("refactor" + "overhaul") to fire scope_breadth,
# plus a conjunction phrase ("and also") to fire conjunction_keywords.
_FX_CONJUNCTION_PLUS_SCOPE = {
    "title": "Refactor the card scoring module and also overhaul the ranking algorithm",
    "description": "Refactor the card scoring and also overhaul the ranking algorithm.",
}

# Both multi-service and multiple tasks -- all signals fire.
_FX_ALL_SIGNALS = {
    "title": "Redesign miru_ai pipeline and rebuild pm storefront card grid",
    "description": (
        "* Rewrite the miru_ai inference pipeline for the new model\n"
        "* Update the pm dashboard storefront card grid for SVELTE\n"
        "* Migrate the n8n workflow json to use the new routing node\n"
        "* Overhaul the test suite and refactor shared utilities"
    ),
}

# Two bullets only (below threshold) -- should NOT trigger multiple_discrete_tasks.
_FX_TWO_BULLETS = {
    "title": "Update two things",
    "description": "* Fix the bug in card lookup\n* Update the README",
}

# Empty description -- should be graceful.
_FX_EMPTY_DESC = {
    "title": "Add logging",
    "description": "",
}


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestSimpleLowComplexity(unittest.TestCase):
    def setUp(self):
        self.result = classify_ticket(**_FX_SIMPLE)

    def test_should_not_split(self):
        self.assertFalse(self.result["should_split"])

    def test_complexity_is_low(self):
        self.assertEqual(self.result["complexity"], "low")

    def test_no_signals(self):
        self.assertEqual(self.result["signals"], [])

    def test_no_suggested_splits(self):
        self.assertEqual(self.result["suggested_splits"], [])


class TestMultiServiceBoundary(unittest.TestCase):
    def setUp(self):
        self.result = classify_ticket(**_FX_MULTI_SERVICE)

    def test_should_split(self):
        self.assertTrue(self.result["should_split"])

    def test_complexity_is_high(self):
        self.assertEqual(self.result["complexity"], "high")

    def test_multi_service_signal_present(self):
        signal_text = " ".join(self.result["signals"])
        self.assertIn("service", signal_text.lower())

    def test_suggested_splits_are_populated(self):
        self.assertGreater(len(self.result["suggested_splits"]), 0)

    def test_suggested_splits_have_required_keys(self):
        for split in self.result["suggested_splits"]:
            self.assertIn("label", split)
            self.assertIn("scope", split)
            self.assertIn("service_dirs", split)

    def test_at_most_three_splits(self):
        self.assertLessEqual(len(self.result["suggested_splits"]), 3)


class TestFileTypeMixing(unittest.TestCase):
    def setUp(self):
        self.result = classify_ticket(**_FX_FILE_TYPE_MIX)

    def test_should_split(self):
        self.assertTrue(self.result["should_split"])

    def test_complexity_is_high(self):
        self.assertEqual(self.result["complexity"], "high")

    def test_file_type_signal_present(self):
        signal_text = " ".join(self.result["signals"])
        self.assertIn("file-type", signal_text.lower())

    def test_suggested_splits_populated(self):
        self.assertGreater(len(self.result["suggested_splits"]), 0)


class TestMultipleDiscreteTasks(unittest.TestCase):
    def setUp(self):
        self.result = classify_ticket(**_FX_MULTI_TASK)

    def test_should_split(self):
        self.assertTrue(self.result["should_split"])

    def test_complexity_is_high(self):
        self.assertEqual(self.result["complexity"], "high")

    def test_discrete_tasks_signal_present(self):
        signal_text = " ".join(self.result["signals"])
        self.assertIn("discrete task", signal_text.lower())

    def test_suggested_splits_are_two_groups(self):
        self.assertEqual(len(self.result["suggested_splits"]), 2)
        labels = {s["label"] for s in self.result["suggested_splits"]}
        self.assertIn("Task group A", labels)
        self.assertIn("Task group B", labels)


class TestConjunctionKeywordOnly(unittest.TestCase):
    """A single conjunction keyword is a weak signal -- should NOT trigger split."""

    def setUp(self):
        self.result = classify_ticket(**_FX_CONJUNCTION_ONLY)

    def test_should_not_split(self):
        self.assertFalse(self.result["should_split"])

    def test_complexity_is_medium(self):
        self.assertEqual(self.result["complexity"], "medium")

    def test_conjunction_signal_present(self):
        signal_text = " ".join(self.result["signals"])
        self.assertIn("conjunction", signal_text.lower())

    def test_no_suggested_splits(self):
        self.assertEqual(self.result["suggested_splits"], [])


class TestDualScopeKeywords(unittest.TestCase):
    """scope_breadth fires (weight=1) but stays medium without a second signal."""

    def setUp(self):
        self.result = classify_ticket(**_FX_DUAL_SCOPE)

    def test_should_not_split(self):
        self.assertFalse(self.result["should_split"])

    def test_complexity_is_medium(self):
        self.assertEqual(self.result["complexity"], "medium")

    def test_scope_signal_present(self):
        signal_text = " ".join(self.result["signals"])
        self.assertIn("scope", signal_text.lower())


class TestConjunctionPlusScopeKeyword(unittest.TestCase):
    """Conjunction (weight 1) + scope (weight 1) = 2 total -- should trigger high."""

    def setUp(self):
        self.result = classify_ticket(**_FX_CONJUNCTION_PLUS_SCOPE)

    def test_should_split(self):
        self.assertTrue(self.result["should_split"])

    def test_complexity_is_high(self):
        self.assertEqual(self.result["complexity"], "high")


class TestAllSignals(unittest.TestCase):
    def setUp(self):
        self.result = classify_ticket(**_FX_ALL_SIGNALS)

    def test_should_split(self):
        self.assertTrue(self.result["should_split"])

    def test_complexity_is_high(self):
        self.assertEqual(self.result["complexity"], "high")

    def test_multiple_signals(self):
        self.assertGreater(len(self.result["signals"]), 1)

    def test_suggested_splits_populated(self):
        self.assertGreater(len(self.result["suggested_splits"]), 0)


class TestTwoBulletsNoSplit(unittest.TestCase):
    """Two bullet points is below the 3-item threshold."""

    def setUp(self):
        self.result = classify_ticket(**_FX_TWO_BULLETS)

    def test_no_discrete_tasks_signal(self):
        signal_text = " ".join(self.result["signals"])
        self.assertNotIn("discrete task", signal_text.lower())


class TestEmptyDescription(unittest.TestCase):
    def test_empty_description_does_not_raise(self):
        result = classify_ticket(title="Add logging", description="")
        self.assertIn("should_split", result)
        self.assertIn("complexity", result)
        self.assertIn("signals", result)
        self.assertIn("suggested_splits", result)

    def test_empty_description_is_low(self):
        result = classify_ticket(title="Add logging", description="")
        self.assertEqual(result["complexity"], "low")

    def test_description_defaults_to_empty(self):
        result = classify_ticket(title="Add logging")
        self.assertIn("should_split", result)


class TestReturnShape(unittest.TestCase):
    """Every result must have exactly the four documented keys."""

    def _check_shape(self, title, description="") -> None:
        result = classify_ticket(title=title, description=description)
        self.assertEqual(
            set(result.keys()),
            {"should_split", "complexity", "signals", "suggested_splits"},
        )
        self.assertIsInstance(result["should_split"], bool)
        self.assertIn(result["complexity"], ("low", "medium", "high"))
        self.assertIsInstance(result["signals"], list)
        self.assertIsInstance(result["suggested_splits"], list)
        for split in result["suggested_splits"]:
            self.assertIsInstance(split["label"], str)
            self.assertIsInstance(split["scope"], str)
            self.assertIsInstance(split["service_dirs"], list)

    def test_simple_ticket_shape(self):
        self._check_shape(**_FX_SIMPLE)

    def test_multi_service_shape(self):
        self._check_shape(**_FX_MULTI_SERVICE)

    def test_multi_task_shape(self):
        self._check_shape(**_FX_MULTI_TASK)

    def test_empty_desc_shape(self):
        self._check_shape(title="Do something")

    def test_splits_absent_when_no_split(self):
        result = classify_ticket(**_FX_SIMPLE)
        self.assertFalse(result["should_split"])
        self.assertEqual(result["suggested_splits"], [])

    def test_none_description_does_not_crash(self):
        result = classify_ticket(title="Fix something", description=None)
        self.assertIn(result["complexity"], ("low", "medium", "high"))


if __name__ == "__main__":
    unittest.main()
