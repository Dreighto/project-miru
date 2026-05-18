"""Tests for the field-tier classification."""

from __future__ import annotations

from services.shadow_loop.field_tiers import (
    BANDAI_FIELDS,
    FIELD_TIERS,
    Tier,
    has_bandai_source,
    tier,
)


def test_hard_fields_are_classified_correctly():
    for field in (
        "card_name",
        "card_type",
        "color",
        "rarity",
        "cost",
        "power",
        "counter",
        "attribute",
        "life",
    ):
        assert tier(field) is Tier.HARD, f"{field} should be HARD"


def test_soft_fields_are_classified_correctly():
    for field in ("effect_text", "trigger_text", "traits"):
        assert tier(field) is Tier.SOFT, f"{field} should be SOFT"


def test_unknown_field_defaults_to_inferred():
    assert tier("some_made_up_field") is Tier.INFERRED


def test_bandai_fields_are_only_name_and_rarity():
    """Bandai crawl only provides name + rarity; other hard fields are catalog-only."""
    assert {"card_name", "rarity"} == BANDAI_FIELDS
    assert has_bandai_source("card_name") is True
    assert has_bandai_source("rarity") is True
    assert has_bandai_source("cost") is False
    assert has_bandai_source("power") is False


def test_every_tracked_field_has_a_tier():
    """Sanity: no SOFT/HARD/INFERRED field is missing from FIELD_TIERS."""
    assert len(FIELD_TIERS) == 12  # 9 hard + 3 soft
