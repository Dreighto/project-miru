"""Tests for gatekeeper.frontmatter_parser — dispatch frontmatter extraction and validation.

Covers: extract() regex anchoring, parse() full pipeline, _validate() enum checks
and business rules (worker=none pairing, ambiguous+plan_only, array field types).
Uses the synthetic corpus in tests/fixtures/gatekeeper_corpus/ for data-driven tests.

PRO-305 — Gatekeeper test coverage.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from gatekeeper.frontmatter_parser import (
    REQUIRED_FIELDS,
    VALID_MODE,
    VALID_PRIORITY,
    VALID_TOOL_PROFILE,
    VALID_WORKER,
    FrontmatterError,
    _validate,
    extract,
    parse,
)

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "gatekeeper_corpus"


def _load_corpus(filename: str) -> dict:
    return json.loads((CORPUS_DIR / filename).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# extract() — regex behavior
# ---------------------------------------------------------------------------


class TestExtract(unittest.TestCase):
    def test_valid_block_at_start(self):
        desc = "<!-- dispatch:\n  worker: claude-code\n  scope: backend\n-->\nBody text."
        result = extract(desc)
        self.assertIsNotNone(result)
        self.assertIn("worker", result)

    def test_block_not_at_start_returns_none(self):
        desc = "Some intro text.\n<!-- dispatch:\n  worker: claude-code\n  scope: backend\n-->"
        self.assertIsNone(extract(desc))

    def test_empty_description_returns_none(self):
        self.assertIsNone(extract(""))

    def test_none_description_returns_none(self):
        self.assertIsNone(extract(None))

    def test_no_dispatch_comment_returns_none(self):
        self.assertIsNone(extract("<!-- not dispatch -->\nJust a comment."))

    def test_leading_whitespace_allowed(self):
        desc = "  \n<!-- dispatch:\n  worker: gemini\n  scope: audit\n-->"
        result = extract(desc)
        self.assertIsNotNone(result)
        self.assertIn("worker", result)

    def test_dedent_preserves_mapping_structure(self):
        desc = "<!-- dispatch:\n    worker: claude-code\n    scope: backend/auth\n-->"
        result = extract(desc)
        self.assertIsNotNone(result)
        self.assertIn("worker: claude-code", result)


# ---------------------------------------------------------------------------
# parse() — full pipeline (extract + YAML + validate)
# ---------------------------------------------------------------------------


class TestParse(unittest.TestCase):
    def test_valid_standard_dispatch(self):
        vec = _load_corpus("01_valid_standard_dispatch.json")
        result = parse(vec["frontmatter_block"])
        self.assertIsNotNone(result)
        for key, expected in vec["expected_parse"].items():
            self.assertEqual(result[key], expected, f"mismatch on field {key!r}")

    def test_valid_gemini_routine(self):
        vec = _load_corpus("02_valid_gemini_routine.json")
        result = parse(vec["frontmatter_block"])
        self.assertIsNotNone(result)
        self.assertEqual(result["worker"], "gemini")
        self.assertEqual(result["expected_mode"], "routine")

    def test_valid_both_workers(self):
        vec = _load_corpus("03_valid_both_workers.json")
        result = parse(vec["frontmatter_block"])
        self.assertEqual(result["worker"], "both")

    def test_valid_blocked_none(self):
        vec = _load_corpus("04_valid_blocked_none.json")
        result = parse(vec["frontmatter_block"])
        self.assertEqual(result["worker"], "none")
        self.assertEqual(result["expected_mode"], "blocked")

    def test_valid_ambiguous_plan_only(self):
        vec = _load_corpus("05_valid_ambiguous_plan_only.json")
        result = parse(vec["frontmatter_block"])
        self.assertEqual(result["expected_mode"], "ambiguous")
        self.assertTrue(result["plan_only"])

    def test_valid_minimal_required_only(self):
        vec = _load_corpus("06_valid_minimal_required_only.json")
        result = parse(vec["frontmatter_block"])
        self.assertEqual(result["worker"], "claude-code")
        self.assertEqual(result["scope"], "backend/fix")
        self.assertNotIn("expected_mode", result)

    def test_valid_do_not_touch(self):
        vec = _load_corpus("07_valid_do_not_touch.json")
        result = parse(vec["frontmatter_block"])
        self.assertEqual(result["do_not_touch"], ["card_catalog.db", ".mcp.json"])
        self.assertEqual(result["dispatch_priority"], "urgent")

    def test_no_frontmatter_returns_none(self):
        vec = _load_corpus("19_no_frontmatter.json")
        result = parse(vec["frontmatter_block"])
        self.assertIsNone(result)


class TestParseErrors(unittest.TestCase):
    """Data-driven error tests from the corpus."""

    def _assert_corpus_error(self, filename: str):
        vec = _load_corpus(filename)
        with self.assertRaises(FrontmatterError) as ctx:
            parse(vec["frontmatter_block"])
        self.assertEqual(ctx.exception.reason, vec["expected_error"])

    def test_missing_worker(self):
        self._assert_corpus_error("08_error_missing_worker.json")

    def test_missing_scope(self):
        self._assert_corpus_error("09_error_missing_scope.json")

    def test_invalid_worker_enum(self):
        self._assert_corpus_error("10_error_invalid_worker_enum.json")

    def test_invalid_mode_enum(self):
        self._assert_corpus_error("11_error_invalid_mode_enum.json")

    def test_invalid_tool_profile(self):
        self._assert_corpus_error("12_error_invalid_tool_profile.json")

    def test_worker_none_mode_mismatch(self):
        self._assert_corpus_error("13_error_worker_none_mode_mismatch.json")

    def test_ambiguous_no_plan_only(self):
        self._assert_corpus_error("14_error_ambiguous_no_plan_only.json")

    def test_context_files_not_list(self):
        self._assert_corpus_error("15_error_context_files_not_list.json")

    def test_do_not_touch_non_string_entry(self):
        self._assert_corpus_error("16_error_do_not_touch_non_string_entry.json")

    def test_invalid_priority(self):
        self._assert_corpus_error("17_error_invalid_priority.json")

    def test_malformed_yaml(self):
        self._assert_corpus_error("18_error_malformed_yaml.json")

    def test_not_a_mapping(self):
        self._assert_corpus_error("20_error_not_a_mapping.json")


# ---------------------------------------------------------------------------
# _validate() — direct unit tests for edge cases not in corpus
# ---------------------------------------------------------------------------


class TestValidateEdgeCases(unittest.TestCase):
    def _base(self, **overrides):
        d = {"worker": "claude-code", "scope": "backend/fix"}
        d.update(overrides)
        return d

    def test_worker_empty_string_is_missing(self):
        with self.assertRaises(FrontmatterError) as ctx:
            _validate({"worker": "", "scope": "x"})
        self.assertEqual(ctx.exception.reason, "frontmatter_missing_required_field")

    def test_worker_none_value_is_missing(self):
        with self.assertRaises(FrontmatterError) as ctx:
            _validate({"worker": None, "scope": "x"})
        self.assertEqual(ctx.exception.reason, "frontmatter_missing_required_field")

    def test_scope_empty_string_is_missing(self):
        with self.assertRaises(FrontmatterError) as ctx:
            _validate({"worker": "claude-code", "scope": ""})
        self.assertEqual(ctx.exception.reason, "frontmatter_missing_required_field")

    def test_worker_none_with_no_mode_is_valid(self):
        d = self._base(worker="none")
        del d["scope"]
        d["scope"] = "blocked/waiting"
        _validate(d)

    def test_null_tool_profile_is_valid(self):
        d = self._base(expected_tool_profile=None)
        _validate(d)

    def test_context_files_null_is_valid(self):
        d = self._base(context_files=None)
        _validate(d)

    def test_do_not_touch_null_is_valid(self):
        d = self._base(do_not_touch=None)
        _validate(d)

    def test_all_valid_workers_accepted(self):
        for w in VALID_WORKER:
            d = self._base(worker=w)
            if w == "none":
                d["expected_mode"] = "blocked"
            _validate(d)

    def test_all_valid_modes_accepted(self):
        for m in VALID_MODE:
            d = self._base(expected_mode=m)
            if m == "ambiguous":
                d["plan_only"] = True
            _validate(d)

    def test_all_valid_priorities_accepted(self):
        for p in VALID_PRIORITY:
            d = self._base(dispatch_priority=p)
            _validate(d)

    def test_all_valid_tool_profiles_accepted(self):
        for tp in VALID_TOOL_PROFILE:
            d = self._base(expected_tool_profile=tp)
            _validate(d)


# ---------------------------------------------------------------------------
# FrontmatterError structure
# ---------------------------------------------------------------------------


class TestFrontmatterError(unittest.TestCase):
    def test_reason_and_detail(self):
        err = FrontmatterError("test_reason", "some detail")
        self.assertEqual(err.reason, "test_reason")
        self.assertEqual(err.detail, "some detail")
        self.assertIn("test_reason", str(err))
        self.assertIn("some detail", str(err))

    def test_reason_only(self):
        err = FrontmatterError("just_reason")
        self.assertEqual(err.reason, "just_reason")
        self.assertEqual(err.detail, "")
        self.assertEqual(str(err), "just_reason")

    def test_is_exception(self):
        self.assertTrue(issubclass(FrontmatterError, Exception))


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestConstants(unittest.TestCase):
    def test_required_fields_tuple(self):
        self.assertIn("worker", REQUIRED_FIELDS)
        self.assertIn("scope", REQUIRED_FIELDS)

    def test_valid_worker_includes_core_set(self):
        self.assertIn("claude-code", VALID_WORKER)
        self.assertIn("gemini", VALID_WORKER)
        self.assertIn("both", VALID_WORKER)
        self.assertIn("none", VALID_WORKER)

    def test_valid_mode_includes_core_set(self):
        self.assertIn("routine", VALID_MODE)
        self.assertIn("judgment", VALID_MODE)
        self.assertIn("ambiguous", VALID_MODE)
        self.assertIn("blocked", VALID_MODE)


if __name__ == "__main__":
    unittest.main()
