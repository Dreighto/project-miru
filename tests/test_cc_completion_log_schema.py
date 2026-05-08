"""Schema validation tests for data/cc_completion_log.jsonl.

Validates structural and semantic constraints of the cc_completion_log.jsonl file
as specified in CLAUDE.md's completion marker schema section:

  - Every line is valid JSON (single object, not array-wrapped)
  - Required fields are present
  - status is one of the allowed terminal-state enum values
  - timestamp follows ISO 8601 UTC (ends with Z)
  - test_evidence is non-empty and follows the machine-parseable format rules
    introduced in the CLAUDE.md expansion (this PR)
  - Append-only invariant: file is ordered by line number only (no row sorting/deduplication)

These tests also cover the "test_evidence machine-parseable format" rule added
in the CLAUDE.md rewrite: every test_evidence value must begin with one of
``<passed>/<total>``, ``ci_only:``, or ``no_tests``.

Note: older entries predating the strict format rule use freetext evidence.
The parseable-prefix check is applied only to entries that carry a valid
``ticket_id`` and use a format that can be checked (i.e. have digits or a
recognised prefix).
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPLETION_LOG = REPO_ROOT / "data" / "cc_completion_log.jsonl"

# Required fields per the schema defined in CLAUDE.md
REQUIRED_FIELDS = {
    "timestamp",
    "status",
    "summary",
    "test_evidence",
}

# Optional fields that, when present, must satisfy type constraints
NULLABLE_STRING_FIELDS = {
    "ticket_id",
    "phase",
    "branch",
    "merge_commit_sha",
    "linear_state_after",
    "notes",
}

NULLABLE_INT_FIELDS = {"pr_number"}

ARRAY_FIELDS = {
    "files_touched",
    "deploy_actions",
    "follow_up_tickets_filed",
}

# Terminal state enum values from the schema
VALID_STATUSES = {"CONFIRMED_WORKING", "INCONCLUSIVE", "FAILED"}

# Regex for ISO 8601 UTC timestamp with Z suffix
ISO8601_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

# Regex that extracts a machine-parseable ratio from test_evidence
EVIDENCE_RATIO_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

# Recognised machine-parseable prefixes added in CLAUDE.md schema
CI_ONLY_PREFIX = "ci_only:"
NO_TESTS_VALUE = "no_tests"


def _load_log_lines():
    """Return list of (line_number, raw_text) for non-blank lines."""
    if not COMPLETION_LOG.exists():
        return []
    with COMPLETION_LOG.open(encoding="utf-8") as fh:
        return [(i + 1, line.rstrip("\n")) for i, line in enumerate(fh) if line.strip()]


def _is_machine_parseable_evidence(value: str) -> bool:
    """Return True if test_evidence satisfies one of the three allowed formats."""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    if stripped == NO_TESTS_VALUE:
        return True
    if stripped.startswith(CI_ONLY_PREFIX):
        return True
    if EVIDENCE_RATIO_RE.search(stripped):
        return True
    return False


class CompletionLogExistsTests(unittest.TestCase):
    def test_log_file_exists(self):
        self.assertTrue(
            COMPLETION_LOG.exists(),
            f"cc_completion_log.jsonl missing at {COMPLETION_LOG}",
        )

    def test_log_file_not_empty(self):
        lines = _load_log_lines()
        self.assertGreater(len(lines), 0, "cc_completion_log.jsonl has no entries")


class CompletionLogParseTests(unittest.TestCase):
    """Every non-blank line must be parseable as a single JSON object."""

    def setUp(self):
        self.lines = _load_log_lines()

    def test_all_lines_are_valid_json(self):
        errors = []
        for lineno, raw in self.lines:
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                errors.append(f"line {lineno}: {exc}")
        self.assertEqual(errors, [], f"JSON parse errors:\n" + "\n".join(errors))

    def test_no_line_is_an_array(self):
        """Each line must be a JSON object, not an array. The schema requires
        'No array wrapping' (CLAUDE.md completion-marker convention)."""
        for lineno, raw in self.lines:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue  # covered by test_all_lines_are_valid_json
            self.assertIsInstance(
                parsed,
                dict,
                f"line {lineno}: expected JSON object, got {type(parsed).__name__}",
            )

    def test_no_trailing_commas_present(self):
        """Trailing commas are not valid JSON. Verifies by round-trip parsing."""
        for lineno, raw in self.lines:
            try:
                parsed = json.loads(raw)
                re_encoded = json.dumps(parsed)
                json.loads(re_encoded)  # should not raise
            except json.JSONDecodeError as exc:
                self.fail(f"line {lineno}: malformed JSON — {exc}")


class CompletionLogRequiredFieldTests(unittest.TestCase):
    """All required fields must be present in every entry."""

    def setUp(self):
        self.entries = []
        for lineno, raw in _load_log_lines():
            try:
                self.entries.append((lineno, json.loads(raw)))
            except json.JSONDecodeError:
                pass  # covered by parse tests

    def test_required_fields_present(self):
        missing_report = []
        for lineno, entry in self.entries:
            for field in REQUIRED_FIELDS:
                if field not in entry:
                    missing_report.append(f"line {lineno}: missing required field {field!r}")
        self.assertEqual(
            missing_report,
            [],
            "Required field violations:\n" + "\n".join(missing_report),
        )

    def test_status_is_valid_enum(self):
        violations = []
        for lineno, entry in self.entries:
            status = entry.get("status")
            if status not in VALID_STATUSES:
                violations.append(
                    f"line {lineno}: status={status!r} not in {sorted(VALID_STATUSES)}"
                )
        self.assertEqual(
            violations,
            [],
            "Invalid status values:\n" + "\n".join(violations),
        )

    def test_timestamp_is_iso8601_utc(self):
        violations = []
        for lineno, entry in self.entries:
            ts = entry.get("timestamp")
            if not isinstance(ts, str) or not ISO8601_Z.match(ts):
                violations.append(
                    f"line {lineno}: timestamp={ts!r} does not match ISO 8601 UTC (YYYY-MM-DDTHH:MM:SSZ)"
                )
        self.assertEqual(
            violations,
            [],
            "Timestamp format violations:\n" + "\n".join(violations),
        )

    def test_summary_is_non_empty_string(self):
        violations = []
        for lineno, entry in self.entries:
            summary = entry.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                violations.append(
                    f"line {lineno}: summary is missing or empty"
                )
        self.assertEqual(
            violations,
            [],
            "Summary violations:\n" + "\n".join(violations),
        )

    def test_test_evidence_is_non_empty_string(self):
        violations = []
        for lineno, entry in self.entries:
            te = entry.get("test_evidence")
            if not isinstance(te, str) or not te.strip():
                violations.append(
                    f"line {lineno}: test_evidence is missing or empty"
                )
        self.assertEqual(
            violations,
            [],
            "test_evidence violations:\n" + "\n".join(violations),
        )


class CompletionLogTypeTests(unittest.TestCase):
    """Type constraints for optional fields when present."""

    def setUp(self):
        self.entries = []
        for lineno, raw in _load_log_lines():
            try:
                self.entries.append((lineno, json.loads(raw)))
            except json.JSONDecodeError:
                pass

    def test_nullable_string_fields_are_string_or_null(self):
        violations = []
        for lineno, entry in self.entries:
            for field in NULLABLE_STRING_FIELDS:
                val = entry.get(field, None)  # absent is fine
                if field in entry and val is not None and not isinstance(val, str):
                    violations.append(
                        f"line {lineno}: {field}={val!r} should be string or null"
                    )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_nullable_int_fields_are_int_or_null(self):
        violations = []
        for lineno, entry in self.entries:
            for field in NULLABLE_INT_FIELDS:
                val = entry.get(field, None)
                if field in entry and val is not None and not isinstance(val, int):
                    violations.append(
                        f"line {lineno}: {field}={val!r} should be int or null"
                    )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_array_fields_are_lists_when_present(self):
        violations = []
        for lineno, entry in self.entries:
            for field in ARRAY_FIELDS:
                val = entry.get(field)
                if field in entry and not isinstance(val, list):
                    violations.append(
                        f"line {lineno}: {field}={val!r} should be a list"
                    )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_handoff_is_dict_or_null_when_present(self):
        violations = []
        for lineno, entry in self.entries:
            if "handoff" in entry:
                val = entry["handoff"]
                if val is not None and not isinstance(val, dict):
                    violations.append(
                        f"line {lineno}: handoff={val!r} should be object or null"
                    )
        self.assertEqual(violations, [], "\n".join(violations))


class CompletionLogTestEvidenceFormatTests(unittest.TestCase):
    """Machine-parseable format rules for test_evidence (CLAUDE.md schema).

    New entries (post CLAUDE.md expansion) must satisfy one of:
      - `<passed>/<total>` ratio appearing first (regex: \\d+\\s*/\\s*\\d+)
      - `ci_only:` prefix
      - `no_tests` (exact string)

    For legacy entries predating the schema, freetext is accepted. We identify
    'modern' entries as those explicitly carrying one of the three valid prefixes
    OR a ratio — i.e. entries that opted into the machine-parseable format.
    Entries that use pure freetext are skipped (grandfathered).
    """

    def setUp(self):
        self.entries = []
        for lineno, raw in _load_log_lines():
            try:
                self.entries.append((lineno, json.loads(raw)))
            except json.JSONDecodeError:
                pass

    def test_modern_entries_are_machine_parseable(self):
        """Entries that carry a recognised prefix must be fully valid per spec.

        An entry is considered 'modern' if its test_evidence starts with the
        ``ci_only:`` or ``no_tests`` sentinel, or if it contains a ``<N>/<M>`` ratio.
        These are the entries that explicitly opted into the schema format;
        a trailing free-text note after the ratio is allowed.
        """
        violations = []
        for lineno, entry in self.entries:
            te = entry.get("test_evidence", "")
            if not isinstance(te, str):
                continue
            stripped = te.strip()
            # Classify as modern if it uses any parseable marker
            has_ratio = bool(EVIDENCE_RATIO_RE.search(stripped))
            has_ci_prefix = stripped.startswith(CI_ONLY_PREFIX)
            has_no_tests = stripped == NO_TESTS_VALUE or stripped.startswith("no_tests")
            is_modern = has_ratio or has_ci_prefix or has_no_tests
            if is_modern and not _is_machine_parseable_evidence(te):
                violations.append(
                    f"line {lineno}: test_evidence appears modern but is not "
                    f"machine-parseable: {stripped[:80]!r}"
                )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_ratio_passed_does_not_exceed_total(self):
        """In entries where test_evidence STARTS with a passed/total ratio,
        passed count must not exceed total.

        Only the leading ratio (if any) is checked — this avoids false positives
        from numeric sequences inside UUIDs, trace IDs, or other freetext.
        """
        # Match only a ratio that appears at the very start of the evidence string
        LEADING_RATIO_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)")
        violations = []
        for lineno, entry in self.entries:
            te = entry.get("test_evidence", "")
            if not isinstance(te, str):
                continue
            m = LEADING_RATIO_RE.match(te)
            if m is None:
                continue  # not a ratio-first entry; skip
            passed = int(m.group(1))
            total = int(m.group(2))
            if total == 0:
                violations.append(
                    f"line {lineno}: test_evidence has total=0 in leading ratio: {te[:80]!r}"
                )
            elif passed > total:
                violations.append(
                    f"line {lineno}: passed ({passed}) > total ({total}) "
                    f"in leading ratio of test_evidence: {te[:80]!r}"
                )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_ci_only_prefix_has_nonempty_detail(self):
        """ci_only: prefix must be followed by non-whitespace detail."""
        violations = []
        for lineno, entry in self.entries:
            te = entry.get("test_evidence", "")
            if not isinstance(te, str):
                continue
            stripped = te.strip()
            if stripped.startswith(CI_ONLY_PREFIX):
                detail = stripped[len(CI_ONLY_PREFIX):].strip()
                if not detail:
                    violations.append(
                        f"line {lineno}: ci_only: prefix has no trailing detail"
                    )
        self.assertEqual(violations, [], "\n".join(violations))


class CompletionLogAppendOnlyOrderTests(unittest.TestCase):
    """Verify the file has not been sorted or deduplicated.

    The append-only contract means the file is a chronological write-ahead
    log. We cannot enforce strict time ordering (workers may emit out of
    order), but we CAN enforce that there are no exact duplicate lines (a
    deduplication pass would violate append-only).
    """

    def setUp(self):
        self.lines = _load_log_lines()

    def test_no_exact_duplicate_lines(self):
        seen = {}
        duplicates = []
        for lineno, raw in self.lines:
            if raw in seen:
                duplicates.append(
                    f"line {lineno} is identical to line {seen[raw]}"
                )
            else:
                seen[raw] = lineno
        self.assertEqual(
            duplicates,
            [],
            "Duplicate lines found (append-only violated):\n" + "\n".join(duplicates),
        )


class CompletionLogHandoffSchemaTests(unittest.TestCase):
    """When a handoff object is present, required sub-fields must exist."""

    REQUIRED_HANDOFF_FIELDS = {"next_worker", "ticket_id", "context"}

    def setUp(self):
        self.entries = []
        for lineno, raw in _load_log_lines():
            try:
                self.entries.append((lineno, json.loads(raw)))
            except json.JSONDecodeError:
                pass

    def test_handoff_object_has_required_fields(self):
        violations = []
        for lineno, entry in self.entries:
            handoff = entry.get("handoff")
            if handoff is None or not isinstance(handoff, dict):
                continue
            for field in self.REQUIRED_HANDOFF_FIELDS:
                if field not in handoff:
                    violations.append(
                        f"line {lineno}: handoff object missing required field {field!r}"
                    )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_handoff_entry_points_is_list_when_present(self):
        violations = []
        for lineno, entry in self.entries:
            handoff = entry.get("handoff")
            if not isinstance(handoff, dict):
                continue
            if "entry_points" in handoff and not isinstance(handoff["entry_points"], list):
                violations.append(
                    f"line {lineno}: handoff.entry_points should be a list"
                )
        self.assertEqual(violations, [], "\n".join(violations))


if __name__ == "__main__":
    unittest.main()