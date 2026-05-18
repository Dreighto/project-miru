"""Formulate the per-card question the primary + validator answer.

The shadow loop's job is to learn card facts. For each (canonical_code,
print_id) the loop pulls, it asks both models the same structured question
and gets back JSON. The verifier compares the response to the catalog.

PRO-908 PR-A.
"""

from __future__ import annotations

from .dummy_verifier import TRACKED_FIELDS


def build_question(canonical_code: str, print_id: str) -> str:
    """Build the user-side prompt asking for a card's canonical fields."""
    field_list = "\n  - ".join(TRACKED_FIELDS)
    return (
        f"What are the canonical fields for One Piece TCG card "
        f"{canonical_code} (printing id: {print_id})?\n\n"
        f"Reply with a JSON object containing these keys:\n  - {field_list}\n\n"
        f"Use lowercase keys exactly as listed. If you don't know a field, set it to null. "
        f"Do not include any prose, only the JSON object."
    )
