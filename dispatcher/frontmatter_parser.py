"""Frontmatter parser for the Local Governance Gatekeeper.

Extracts the HTML-comment YAML block from a Linear ticket description and
validates it against the closed-enum schema defined in
``docs/dispatch/ticket_frontmatter_schema.md``.

Format the parser accepts (must be the first content in the description):

    <!-- dispatch:
      worker: claude-code
      scope: backend/auth
      context_files:
        - src/middleware/auth.py
      expected_mode: judgment
      expected_tool_profile: standard_worker
      plan_only: false
    -->

The Gatekeeper reads the parsed dict as the original-intent gospel for the
ticket and compares it to the conversational delta CH sends in the
``cc_handoff`` payload. Contradictions trigger Phase 2.5 Rejection.
"""

from __future__ import annotations

import re
import textwrap
from typing import Any

import yaml


class FrontmatterError(Exception):
    """Structured error from frontmatter parse / validation.

    The Gatekeeper surfaces ``reason`` to CH in the Phase 2.5 Rejection JSON
    so the operator gets actionable feedback ("frontmatter missing required
    field 'worker'") instead of a generic parse failure.
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


VALID_WORKER = {"claude-code", "gemini", "both", "none"}
VALID_MODE = {"routine", "judgment", "ambiguous", "blocked"}
VALID_TOOL_PROFILE = {"drift_executor", "standard_worker", "reviewer", None}
VALID_PRIORITY = {"urgent", "normal", "low"}

REQUIRED_FIELDS = ("worker", "scope")

_FRONTMATTER_RE = re.compile(
    r"<!--\s*dispatch:\s*\n(.*?)\s*-->",
    re.DOTALL,
)


def extract(description: str) -> str | None:
    """Pull the YAML block out of a ticket description.

    Returns the inner YAML text (between ``dispatch:`` and ``-->``), or
    ``None`` if no frontmatter block is present. Returning ``None`` is
    valid — the Gatekeeper treats absent frontmatter as "default to
    ``standard_worker`` / ``judgment`` and dispatch conservatively".

    The captured text is dedented via ``textwrap.dedent`` so the YAML
    parser sees a consistently-indented mapping. Operators typically
    indent the body with 2 spaces under ``dispatch:`` for readability.
    """
    if not description:
        return None
    m = _FRONTMATTER_RE.search(description)
    if not m:
        return None
    return textwrap.dedent(m.group(1)).strip()


def parse(description: str) -> dict[str, Any] | None:
    """Extract + parse + validate the dispatch frontmatter.

    Returns the parsed dict on success, ``None`` if no frontmatter block is
    present, or raises :class:`FrontmatterError` if the block is malformed
    or violates the closed-enum schema.
    """
    yaml_text = extract(description)
    if yaml_text is None:
        return None

    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise FrontmatterError("frontmatter_yaml_invalid", str(e)) from e

    if not isinstance(parsed, dict):
        raise FrontmatterError(
            "frontmatter_not_a_mapping",
            f"expected dict, got {type(parsed).__name__}",
        )

    _validate(parsed)
    return parsed


def _validate(d: dict[str, Any]) -> None:
    for required in REQUIRED_FIELDS:
        if required not in d or d[required] in (None, ""):
            raise FrontmatterError("frontmatter_missing_required_field", required)

    worker = d["worker"]
    if worker not in VALID_WORKER:
        raise FrontmatterError(
            "frontmatter_invalid_worker_enum",
            f"got {worker!r}, expected one of {sorted(VALID_WORKER)}",
        )

    if "expected_mode" in d:
        mode = d["expected_mode"]
        if mode not in VALID_MODE:
            raise FrontmatterError(
                "frontmatter_invalid_mode_enum",
                f"got {mode!r}, expected one of {sorted(VALID_MODE)}",
            )

    if "expected_tool_profile" in d:
        profile = d["expected_tool_profile"]
        if profile not in VALID_TOOL_PROFILE:
            raise FrontmatterError(
                "frontmatter_invalid_tool_profile_enum",
                f"got {profile!r}, expected one of {sorted(p for p in VALID_TOOL_PROFILE if p)} or null",
            )

    if "dispatch_priority" in d:
        prio = d["dispatch_priority"]
        if prio not in VALID_PRIORITY:
            raise FrontmatterError(
                "frontmatter_invalid_priority_enum",
                f"got {prio!r}, expected one of {sorted(VALID_PRIORITY)}",
            )

    if d["worker"] == "none":
        mode = d.get("expected_mode")
        if mode is not None and mode != "blocked":
            raise FrontmatterError(
                "frontmatter_inconsistent_worker_none",
                f"worker=none requires expected_mode=blocked or omitted, got {mode!r}",
            )

    if d.get("expected_mode") == "ambiguous" and d.get("plan_only") is False:
        raise FrontmatterError(
            "frontmatter_ambiguous_requires_plan_only",
            "expected_mode=ambiguous tasks must have plan_only=true",
        )

    for arr_field in ("context_files", "do_not_touch"):
        if arr_field in d and d[arr_field] is not None:
            if not isinstance(d[arr_field], list):
                raise FrontmatterError(
                    "frontmatter_field_must_be_list",
                    f"{arr_field} got {type(d[arr_field]).__name__}",
                )
            for entry in d[arr_field]:
                if not isinstance(entry, str):
                    raise FrontmatterError(
                        "frontmatter_array_entry_not_string",
                        f"{arr_field} entry: {entry!r}",
                    )
