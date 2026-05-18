"""Tests for services/shadow_loop/sentinel.py (PR-C, PRO-912)."""

from __future__ import annotations

from typing import Any

from services.shadow_loop.sentinel import (
    SENTINEL_PERIOD,
    build_sentinel_primary_from_card,
    run_sentinel_check,
    should_check_sentinel,
)

# ---------------------------------------------------------------------------
# should_check_sentinel
# ---------------------------------------------------------------------------


def test_should_check_sentinel_fires_on_multiples_of_period():
    assert should_check_sentinel(0) is True
    assert should_check_sentinel(SENTINEL_PERIOD) is True
    assert should_check_sentinel(SENTINEL_PERIOD * 2) is True
    assert should_check_sentinel(SENTINEL_PERIOD * 10) is True


def test_should_check_sentinel_off_period_is_false():
    assert should_check_sentinel(1) is False
    assert should_check_sentinel(SENTINEL_PERIOD - 1) is False
    assert should_check_sentinel(SENTINEL_PERIOD + 1) is False


# ---------------------------------------------------------------------------
# build_sentinel_primary_from_card
# ---------------------------------------------------------------------------

_SAMPLE_CARD: dict[str, Any] = {
    "canonical_code": "OP01-001",
    "print_id": "OP01-001",
    "card_name": "Monkey D. Luffy",
    "card_type": "Leader",
    "color": "Red",
    "rarity": "SEC",
    "cost": "5",
    "power": "4000",
    "counter": None,
    "attribute": "Strike",
    "life": "5",
    # SOFT / non-HARD fields that should NOT appear in the primary answer
    "effect_text": "[ DON!! x1 ] ...",
    "trigger_text": None,
    "traits": "[Straw Hat Crew]",
}


def test_build_sentinel_primary_contains_only_hard_fields():
    from services.shadow_loop.field_tiers import FIELD_TIERS, Tier

    primary = build_sentinel_primary_from_card(_SAMPLE_CARD)
    hard_fields = {f for f, t in FIELD_TIERS.items() if t is Tier.HARD}
    soft_fields = {f for f, t in FIELD_TIERS.items() if t is not Tier.HARD}

    assert set(primary.keys()) == hard_fields
    for soft in soft_fields:
        assert soft not in primary


def test_build_sentinel_primary_preserves_values():
    primary = build_sentinel_primary_from_card(_SAMPLE_CARD)
    assert primary["card_name"] == "Monkey D. Luffy"
    assert primary["color"] == "Red"
    assert primary["cost"] == "5"
    assert primary["counter"] is None


# ---------------------------------------------------------------------------
# run_sentinel_check
# ---------------------------------------------------------------------------


class _AllCorrectVerifier:
    """Verifier that marks every hard field as verified-correct."""

    def score(self, card: dict[str, Any], primary_answer: dict[str, Any]) -> dict[str, Any]:
        from services.shadow_loop.field_tiers import FIELD_TIERS, Tier

        field_outcomes = {}
        for field, t in FIELD_TIERS.items():
            if t is Tier.HARD:
                field_outcomes[field] = {
                    "outcome": "verified-correct",
                    "reason": "fake",
                    "tier": t.value,
                    "catalog_value": card.get(field),
                    "bandai_value": None,
                    "model_value": primary_answer.get(field),
                }
            else:
                field_outcomes[field] = {
                    "outcome": "inconclusive",
                    "reason": "soft/inferred",
                    "tier": t.value,
                    "catalog_value": None,
                    "bandai_value": None,
                    "model_value": None,
                }
        return {
            "field_outcomes": field_outcomes,
            "confidence_score": 1.0,
            "all_hard_verified_correct": True,
        }


class _OneWrongVerifier:
    """Verifier that marks card_name as verified-wrong, everything else correct."""

    def score(self, card: dict[str, Any], primary_answer: dict[str, Any]) -> dict[str, Any]:
        from services.shadow_loop.field_tiers import FIELD_TIERS, Tier

        field_outcomes = {}
        for field, t in FIELD_TIERS.items():
            if t is Tier.HARD:
                outcome = "verified-wrong" if field == "card_name" else "verified-correct"
                field_outcomes[field] = {
                    "outcome": outcome,
                    "reason": "fake",
                    "tier": t.value,
                    "catalog_value": card.get(field),
                    "bandai_value": None,
                    "model_value": primary_answer.get(field),
                }
            else:
                field_outcomes[field] = {
                    "outcome": "inconclusive",
                    "reason": "soft",
                    "tier": t.value,
                    "catalog_value": None,
                    "bandai_value": None,
                    "model_value": None,
                }
        return {
            "field_outcomes": field_outcomes,
            "confidence_score": 0.5,
            "all_hard_verified_correct": False,
        }


class _AllInconclusiveVerifier:
    """Verifier that marks every field inconclusive."""

    def score(self, card: dict[str, Any], primary_answer: dict[str, Any]) -> dict[str, Any]:
        from services.shadow_loop.field_tiers import FIELD_TIERS

        field_outcomes = {
            field: {
                "outcome": "inconclusive",
                "reason": "no answer",
                "tier": t.value,
                "catalog_value": None,
                "bandai_value": None,
                "model_value": None,
            }
            for field, t in FIELD_TIERS.items()
        }
        return {
            "field_outcomes": field_outcomes,
            "confidence_score": 0.0,
            "all_hard_verified_correct": False,
        }


_PRIMARY = build_sentinel_primary_from_card(_SAMPLE_CARD)


def test_run_sentinel_check_passes_when_all_hard_correct():
    result = run_sentinel_check(_AllCorrectVerifier(), _SAMPLE_CARD, _PRIMARY)
    assert result["sentinel_passed"] is True
    assert result["sentinel_canonical_code"] == "OP01-001"


def test_run_sentinel_check_fails_when_any_hard_wrong():
    result = run_sentinel_check(_OneWrongVerifier(), _SAMPLE_CARD, _PRIMARY)
    assert result["sentinel_passed"] is False


def test_run_sentinel_check_fails_when_no_hard_outcomes():
    """All-inconclusive → sentinel_passed False (no hard fields scored means nothing confirmed)."""
    result = run_sentinel_check(_AllInconclusiveVerifier(), _SAMPLE_CARD, _PRIMARY)
    assert result["sentinel_passed"] is False


def test_run_sentinel_check_returns_full_verifier_result():
    """Result must include the original verifier keys plus sentinel extension keys."""
    result = run_sentinel_check(_AllCorrectVerifier(), _SAMPLE_CARD, _PRIMARY)
    assert "field_outcomes" in result
    assert "confidence_score" in result
    assert "all_hard_verified_correct" in result
    assert "sentinel_passed" in result
    assert "sentinel_canonical_code" in result
