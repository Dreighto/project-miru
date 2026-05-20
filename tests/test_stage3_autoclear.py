"""Tests for the Stage 3 auto-clear predicate (PRO-927).

Seven cases as specified in the ticket:
  1. Both agree + same trace → advance=True
  2. Both agree but URL mismatch → advance=False
  3. Both agree but field mismatch → advance=False
  4. Answer disagreement → advance=False
  5. One side missing trace → advance=False
  6. Field-tier exemption (non-Bandai field) → advance=True even if trace absent
  7. Empty verifier result → advance=False (defensive)
"""

from __future__ import annotations

from services.shadow_loop.stage3_autoclear import stage3_autoclear

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TRACE_A = {
    "url": "https://en.onepiece-cardgame.com/cardlist/?search=true&freewords=OP01-001",
    "field": "rarity_text",
    "fetched_at": "2026-05-17T16:57:23Z",
}
_TRACE_B = {
    "url": "https://en.onepiece-cardgame.com/cardlist/?search=true&freewords=OP01-002",
    "field": "rarity_text",
    "fetched_at": "2026-05-17T16:57:23Z",
}
_TRACE_ALT_FIELD = {
    "url": "https://en.onepiece-cardgame.com/cardlist/?search=true&freewords=OP01-001",
    "field": "name",  # different page-section
    "fetched_at": "2026-05-17T16:57:23Z",
}


def _bandai_field_outcome(
    field: str,
    primary_answer: str | None,
    validator_answer: str | None,
    primary_trace,
    validator_trace,
    outcome: str = "verified-correct",
) -> dict:
    return {
        "outcome": outcome,
        "reason": "test",
        "tier": "hard",
        "catalog_value": primary_answer,
        "bandai_value": primary_answer,
        "model_value": primary_answer,
        "primary_answer": primary_answer,
        "validator_answer": validator_answer,
        "agree": primary_answer == validator_answer,
        "primary_source_trace": primary_trace,
        "validator_source_trace": validator_trace,
    }


def _soft_field_outcome(field: str, primary_answer: str | None = "some text") -> dict:
    """Non-Bandai field — source traces not present (exempt from gate)."""
    return {
        "outcome": "verified-correct",
        "reason": "test",
        "tier": "soft",
        "catalog_value": primary_answer,
        "bandai_value": None,
        "model_value": primary_answer,
        "primary_answer": primary_answer,
        # No primary_source_trace / validator_source_trace keys at all —
        # the real verifier only adds them for has_bandai_source() fields.
    }


# ---------------------------------------------------------------------------
# Test 1: Both agree + same trace → advance=True
# ---------------------------------------------------------------------------


def test_both_agree_same_trace_advances():
    result = {
        "field_outcomes": {
            "rarity": _bandai_field_outcome("rarity", "SR", "SR", _TRACE_A, _TRACE_A),
            "card_name": _bandai_field_outcome(
                "card_name", "Roronoa Zoro", "Roronoa Zoro", _TRACE_A, _TRACE_A
            ),
            "effect_text": _soft_field_outcome("effect_text"),
        }
    }
    advance, reason = stage3_autoclear(result)
    assert advance is True
    assert reason is None


# ---------------------------------------------------------------------------
# Test 2: Both agree but URL mismatch → advance=False
# ---------------------------------------------------------------------------


def test_url_mismatch_blocks():
    result = {
        "field_outcomes": {
            "rarity": _bandai_field_outcome(
                "rarity",
                "SR",
                "SR",
                _TRACE_A,  # primary URL = OP01-001
                _TRACE_B,  # validator URL = OP01-002 — mismatch
            ),
        }
    }
    advance, reason = stage3_autoclear(result)
    assert advance is False
    assert reason is not None
    assert "url_mismatch" in reason


# ---------------------------------------------------------------------------
# Test 3: Both agree but field mismatch → advance=False
# ---------------------------------------------------------------------------


def test_field_section_mismatch_blocks():
    result = {
        "field_outcomes": {
            "rarity": _bandai_field_outcome(
                "rarity",
                "SR",
                "SR",
                _TRACE_A,  # field = "rarity_text"
                _TRACE_ALT_FIELD,  # field = "name" — mismatch
            ),
        }
    }
    advance, reason = stage3_autoclear(result)
    assert advance is False
    assert reason is not None
    assert "field_mismatch" in reason


# ---------------------------------------------------------------------------
# Test 4: Answer disagreement → advance=False
# ---------------------------------------------------------------------------


def test_answer_disagreement_blocks():
    result = {
        "field_outcomes": {
            "rarity": _bandai_field_outcome(
                "rarity",
                primary_answer="SR",
                validator_answer="R",  # different!
                primary_trace=_TRACE_A,
                validator_trace=_TRACE_A,
            ),
        }
    }
    advance, reason = stage3_autoclear(result)
    assert advance is False
    assert reason is not None
    assert "answer_disagreement" in reason


# ---------------------------------------------------------------------------
# Test 5: One side missing trace → advance=False
# ---------------------------------------------------------------------------


def test_missing_source_trace_blocks():
    # Primary has trace, validator doesn't.
    result_primary_only = {
        "field_outcomes": {
            "rarity": _bandai_field_outcome(
                "rarity",
                "SR",
                "SR",
                primary_trace=_TRACE_A,
                validator_trace=None,  # validator couldn't anchor to Bandai
            ),
        }
    }
    advance, reason = stage3_autoclear(result_primary_only)
    assert advance is False
    assert reason is not None
    assert "missing_source_trace" in reason

    # Validator has trace, primary doesn't.
    result_validator_only = {
        "field_outcomes": {
            "rarity": _bandai_field_outcome(
                "rarity",
                "SR",
                "SR",
                primary_trace=None,
                validator_trace=_TRACE_A,
            ),
        }
    }
    advance2, reason2 = stage3_autoclear(result_validator_only)
    assert advance2 is False
    assert reason2 is not None
    assert "missing_source_trace" in reason2


# ---------------------------------------------------------------------------
# Test 6: Field-tier exemption — non-Bandai field exempt from gate
# ---------------------------------------------------------------------------


def test_non_bandai_field_exempt():
    """SOFT fields (effect_text, traits, etc.) have no source-trace requirement.

    Even if their trace keys are absent, the predicate should not fail on them.
    The gate only applies to fields where has_bandai_source() is True.
    """
    result = {
        "field_outcomes": {
            # Only soft/non-Bandai fields — no Bandai fields at all.
            "effect_text": _soft_field_outcome("effect_text", "some ability text"),
            "traits": _soft_field_outcome("traits", "Straw Hat Crew"),
            "cost": {  # HARD but NOT in BANDAI_FIELDS
                "outcome": "verified-correct",
                "reason": "test",
                "tier": "hard",
                "catalog_value": "3",
                "bandai_value": None,
                "model_value": "3",
                "primary_answer": "3",
                "validator_answer": "3",
                "agree": True,
            },
        }
    }
    advance, reason = stage3_autoclear(result)
    # No Bandai-tracked fields → all required-trace checks are vacuously satisfied.
    assert advance is True
    assert reason is None


# ---------------------------------------------------------------------------
# Test 7: Empty verifier result → advance=False (defensive)
# ---------------------------------------------------------------------------


def test_empty_verifier_result_blocked():
    advance, reason = stage3_autoclear({})
    assert advance is False
    assert reason == "empty_verifier_result"

    # Also with explicit empty field_outcomes.
    advance2, reason2 = stage3_autoclear({"field_outcomes": {}})
    assert advance2 is False
    assert reason2 == "empty_verifier_result"
