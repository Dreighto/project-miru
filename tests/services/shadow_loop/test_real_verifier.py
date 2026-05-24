"""Tests for the RealVerifier (PR-B)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.shadow_loop.bandai_source import BandaiSource
from services.shadow_loop.real_verifier import RealVerifier


class _CannedJudge:
    """Semantic judge that returns a pre-programmed verdict per soft-field call."""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.calls: list[str] = []

    def ask_json(self, user_prompt: str) -> dict[str, Any]:
        self.calls.append(user_prompt)
        return {"verdict": self.verdict}


class _ExplodingJudge:
    def ask_json(self, user_prompt: str) -> dict[str, Any]:
        raise RuntimeError("boom")


@pytest.fixture
def bandai_with_zoro(tmp_path: Path) -> BandaiSource:
    crawl = tmp_path / "bandai_op01_crawl.json"
    crawl.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ticket": "test",
                "source": "test",
                "queried_at": "2026-05-17",
                "card_numbers_queried": ["OP01-003"],
                "printings": [
                    {
                        "card_number": "OP01-003",
                        "print_id": "base",
                        "full_id": "OP01-003",
                        "name": "Roronoa Zoro",
                        "rarity": "SR",
                        "card_set": "OP01",
                        "image_url": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return BandaiSource(crawl)


def _zoro_card() -> dict[str, Any]:
    return {
        "canonical_code": "OP01-003",
        "print_id": "OP01-003",
        "card_name": "Roronoa Zoro",
        "card_type": "Character",
        "color": "Green",
        "rarity": "SR",
        "cost": 4,
        "power": "6000",
        "counter": "2000",
        "attribute": "Slash",
        "life": "",
        "effect_text": "On Play: KO an opponent Character with 3000 or less.",
        "trigger_text": "",
        "traits": "Supernovas / Straw Hat Crew",
    }


def test_hard_field_match_against_catalog_is_verified_correct(bandai_with_zoro: BandaiSource):
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    primary = {"card_name": "Roronoa Zoro", "rarity": "SR", "cost": 4, "power": "6000"}
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["card_name"]["outcome"] == "verified-correct"
    assert result["field_outcomes"]["rarity"]["outcome"] == "verified-correct"
    assert result["field_outcomes"]["cost"]["outcome"] == "verified-correct"
    assert result["field_outcomes"]["power"]["outcome"] == "verified-correct"


def test_hard_field_mismatch_is_verified_wrong(bandai_with_zoro: BandaiSource):
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    primary = {"cost": 99, "power": "9999"}
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["cost"]["outcome"] == "verified-wrong"
    assert result["field_outcomes"]["power"]["outcome"] == "verified-wrong"


def test_missing_primary_answer_is_inconclusive(bandai_with_zoro: BandaiSource):
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    primary: dict[str, Any] = {}
    result = verifier.score(card, primary)
    for field in ("card_name", "cost", "power", "color", "card_type", "rarity"):
        assert result["field_outcomes"][field]["outcome"] == "inconclusive"


def test_two_source_disagreement_marks_inconclusive(tmp_path: Path):
    """Catalog says rarity=SR but Bandai says rarity=L → inconclusive even if primary matches catalog."""
    crawl = tmp_path / "bandai_op01_crawl.json"
    crawl.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ticket": "test",
                "source": "test",
                "queried_at": "2026-05-17",
                "card_numbers_queried": ["OP01-003"],
                "printings": [
                    {
                        "card_number": "OP01-003",
                        "print_id": "base",
                        "full_id": "OP01-003",
                        "name": "Roronoa Zoro",
                        "rarity": "L",  # contradicts catalog
                        "card_set": "OP01",
                        "image_url": "",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    verifier = RealVerifier(bandai=BandaiSource(crawl))
    card = _zoro_card()
    primary = {"rarity": "SR"}  # matches catalog
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["rarity"]["outcome"] == "inconclusive"
    assert "two-source disagree" in result["field_outcomes"]["rarity"]["reason"]


def test_soft_field_exact_match_short_circuits_judge(bandai_with_zoro: BandaiSource):
    judge = _CannedJudge(verdict="no-match")  # would say no-match if called
    verifier = RealVerifier(bandai=bandai_with_zoro, judge=judge)
    card = _zoro_card()
    primary = {"effect_text": card["effect_text"]}  # exact match
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["effect_text"]["outcome"] == "verified-correct"
    # Judge should not be called for exact match.
    assert judge.calls == []


def test_soft_field_uses_judge_when_strings_differ(bandai_with_zoro: BandaiSource):
    judge = _CannedJudge(verdict="match")
    verifier = RealVerifier(bandai=bandai_with_zoro, judge=judge)
    card = _zoro_card()
    primary = {"effect_text": "When played, knock out an opponent Character of 3000 power or less."}
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["effect_text"]["outcome"] == "verified-correct"
    assert len(judge.calls) == 1


def test_soft_field_judge_says_no_match_is_verified_wrong(bandai_with_zoro: BandaiSource):
    judge = _CannedJudge(verdict="no-match")
    verifier = RealVerifier(bandai=bandai_with_zoro, judge=judge)
    card = _zoro_card()
    primary = {"effect_text": "Draw 3 cards and gain 7 life."}
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["effect_text"]["outcome"] == "verified-wrong"


def test_soft_field_no_judge_falls_back_to_inconclusive(bandai_with_zoro: BandaiSource):
    verifier = RealVerifier(bandai=bandai_with_zoro, judge=None)
    card = _zoro_card()
    primary = {"effect_text": "different wording"}
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["effect_text"]["outcome"] == "inconclusive"
    assert "no semantic judge" in result["field_outcomes"]["effect_text"]["reason"]


def test_judge_crash_marks_inconclusive_not_crash(bandai_with_zoro: BandaiSource):
    verifier = RealVerifier(bandai=bandai_with_zoro, judge=_ExplodingJudge())
    card = _zoro_card()
    primary = {"effect_text": "different wording"}
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["effect_text"]["outcome"] == "inconclusive"
    assert "semantic judge failed" in result["field_outcomes"]["effect_text"]["reason"]


def test_confidence_score_excludes_inconclusive(bandai_with_zoro: BandaiSource):
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    # 2 correct hard fields, 1 wrong, 1 unanswered (inconclusive)
    primary = {
        "card_name": "Roronoa Zoro",  # correct
        "rarity": "SR",  # correct
        "cost": 99,  # wrong
        # no "power" → inconclusive
    }
    result = verifier.score(card, primary)
    # Score should be 2 correct / (2 correct + 1 wrong) = 0.6667
    assert result["confidence_score"] == pytest.approx(0.6667, abs=0.001)


def test_all_hard_verified_correct_flag(bandai_with_zoro: BandaiSource):
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    # Answer every HARD field correctly. life="" matches catalog="" (field
    # doesn't apply to Characters).
    primary = {
        "card_name": "Roronoa Zoro",
        "card_type": "Character",
        "color": "Green",
        "rarity": "SR",
        "cost": 4,
        "power": "6000",
        "counter": "2000",
        "attribute": "Slash",
        "life": "",
    }
    result = verifier.score(card, primary)
    assert result["all_hard_verified_correct"] is True


def test_explicit_empty_against_non_empty_catalog_is_wrong(bandai_with_zoro: BandaiSource):
    """Primary saying '' when catalog has a value → verified-wrong, not inconclusive."""
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    primary = {"card_name": ""}  # explicitly empty against "Roronoa Zoro"
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["card_name"]["outcome"] == "verified-wrong"


def test_normalization_handles_format_drift(bandai_with_zoro: BandaiSource):
    """'5,000' / '5000' / 5000 should all be treated as the same value."""
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    card["power"] = "6,000"  # catalog phrased with comma
    primary = {"power": 6000}  # primary answered as int
    result = verifier.score(card, primary)
    assert result["field_outcomes"]["power"]["outcome"] == "verified-correct"


def test_inferred_field_is_always_inconclusive(bandai_with_zoro: BandaiSource):
    """Unknown / inferred fields can never auto-promote."""
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    card["archetype_tag"] = "main_character"  # not in FIELD_TIERS
    primary = {"archetype_tag": "main_character"}
    result = verifier.score(card, primary)
    # archetype_tag isn't in FIELD_TIERS so it's not in field_outcomes at all
    assert "archetype_tag" not in result["field_outcomes"]


def test_validator_answer_recorded_for_all_fields_not_just_bandai(
    bandai_with_zoro: BandaiSource,
):
    """validator_answer + agree must be recorded for every tiered field, not
    only Bandai-tracked ones — so the Review UI sees the validator's answer on
    color, cost, power, effect_text, etc., not just card_name + rarity."""
    judge = _CannedJudge(verdict="match")
    verifier = RealVerifier(bandai=bandai_with_zoro, judge=judge)
    card = _zoro_card()
    primary = {
        "card_name": "Roronoa Zoro",
        "color": "Green",
        "cost": 4,
        "effect_text": card["effect_text"],
    }
    validator = {
        "card_name": "Roronoa Zoro",
        "color": "Green",
        "cost": 4,
        "effect_text": card["effect_text"],
    }
    result = verifier.score(card, primary, validator_answer=validator)

    # Bandai-tracked field (card_name): validator_answer present.
    assert result["field_outcomes"]["card_name"]["validator_answer"] == "Roronoa Zoro"
    assert result["field_outcomes"]["card_name"]["agree"] is True

    # Hard field WITHOUT Bandai signal (color, cost): validator_answer present.
    assert result["field_outcomes"]["color"]["validator_answer"] == "Green"
    assert result["field_outcomes"]["color"]["agree"] is True
    assert result["field_outcomes"]["cost"]["validator_answer"] == 4
    assert result["field_outcomes"]["cost"]["agree"] is True

    # Soft field (effect_text): validator_answer present.
    assert result["field_outcomes"]["effect_text"]["validator_answer"] == card["effect_text"]
    assert result["field_outcomes"]["effect_text"]["agree"] is True


def test_agree_false_when_primary_and_validator_disagree(bandai_with_zoro: BandaiSource):
    """agree must distinguish primary↔validator agreement on non-Bandai fields too."""
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    primary = {"cost": 4}
    validator = {"cost": 7}
    result = verifier.score(card, primary, validator_answer=validator)
    assert result["field_outcomes"]["cost"]["validator_answer"] == 7
    assert result["field_outcomes"]["cost"]["agree"] is False


def test_validator_answer_none_when_validator_did_not_answer_field(
    bandai_with_zoro: BandaiSource,
):
    """If validator_answer dict omits a field, validator_answer is None and agree is None."""
    verifier = RealVerifier(bandai=bandai_with_zoro)
    card = _zoro_card()
    primary = {"cost": 4, "color": "Green"}
    validator = {"cost": 4}  # omits color
    result = verifier.score(card, primary, validator_answer=validator)
    assert result["field_outcomes"]["cost"]["validator_answer"] == 4
    assert result["field_outcomes"]["color"]["validator_answer"] is None
    assert result["field_outcomes"]["color"]["agree"] is None
