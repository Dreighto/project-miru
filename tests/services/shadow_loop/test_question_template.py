"""Tests for the per-card question template."""

from __future__ import annotations

from services.shadow_loop.dummy_verifier import TRACKED_FIELDS
from services.shadow_loop.question_template import build_question


def test_question_contains_card_identity():
    q = build_question("OP01-001", "OP01-001_p1")
    assert "OP01-001" in q
    assert "OP01-001_p1" in q


def test_question_lists_every_tracked_field():
    q = build_question("OP01-001", "OP01-001")
    for field in TRACKED_FIELDS:
        assert field in q


def test_question_instructs_json_only():
    q = build_question("OP01-001", "OP01-001")
    assert "JSON" in q
    assert "null" in q
