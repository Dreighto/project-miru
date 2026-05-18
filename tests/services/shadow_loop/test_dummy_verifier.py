"""Tests for the dummy verifier (PR-A scaffold; PR-B replaces with real logic)."""

from __future__ import annotations

from services.shadow_loop.dummy_verifier import TRACKED_FIELDS, DummyVerifier


def test_every_field_outcome_is_inconclusive():
    verifier = DummyVerifier()
    card = {f: f"catalog-{f}" for f in TRACKED_FIELDS}
    primary_answer = {f: f"primary-{f}" for f in TRACKED_FIELDS}
    result = verifier.score(card, primary_answer)
    assert result["confidence_score"] == 0.0
    assert result["all_hard_verified_correct"] is False
    for field in TRACKED_FIELDS:
        outcome = result["field_outcomes"][field]
        assert outcome["outcome"] == "inconclusive"
        assert outcome["catalog_value"] == f"catalog-{field}"
        assert outcome["model_value"] == f"primary-{field}"


def test_missing_card_or_primary_values_are_recorded_as_none():
    verifier = DummyVerifier()
    result = verifier.score({}, {})
    for field in TRACKED_FIELDS:
        outcome = result["field_outcomes"][field]
        assert outcome["catalog_value"] is None
        assert outcome["model_value"] is None
