"""Tests for the bootstrap-test runner."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from services.shadow_loop.bandai_source import BandaiSource
from services.shadow_loop.bootstrap_test import _evaluate_case, run_bootstrap


@pytest.fixture
def fake_catalog(tmp_path: Path) -> Path:
    """A tiny catalog DB with one card so _fetch_card_row finds something."""
    db = tmp_path / "catalog.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            canonical_code TEXT NOT NULL,
            set_code TEXT DEFAULT '',
            card_number TEXT DEFAULT '',
            set_name TEXT DEFAULT '',
            card_name TEXT DEFAULT '',
            rarity TEXT DEFAULT '',
            color TEXT DEFAULT '',
            card_type TEXT DEFAULT '',
            cost INTEGER,
            power TEXT DEFAULT '',
            counter TEXT DEFAULT '',
            attribute TEXT DEFAULT '',
            traits TEXT DEFAULT '',
            life TEXT DEFAULT '',
            block_icon TEXT DEFAULT '',
            effect_text TEXT DEFAULT '',
            trigger_text TEXT DEFAULT ''
        );
        CREATE TABLE card_variants (
            id INTEGER PRIMARY KEY,
            card_id INTEGER NOT NULL,
            print_id TEXT NOT NULL,
            image_path TEXT,
            image_url TEXT
        );
        INSERT INTO cards (
            id, canonical_code, card_name, card_type, color, rarity,
            cost, power, counter, attribute, life, effect_text, trigger_text, traits
        ) VALUES (
            1, 'OP01-X01', 'Test Card', 'Character', 'Red', 'C',
            3, '4000', '1000', 'Slash', '', 'On Play: Draw 1.', '', 'Pirate'
        );
        INSERT INTO card_variants (id, card_id, print_id) VALUES (1, 1, 'OP01-X01');
        """
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def empty_bandai(tmp_path: Path) -> BandaiSource:
    return BandaiSource(tmp_path / "no_such_crawl.json")


def test_case_with_correct_primary_answer_passes(fake_catalog: Path, empty_bandai: BandaiSource):
    case: dict[str, Any] = {
        "name": "correct answer",
        "canonical_code": "OP01-X01",
        "print_id": "OP01-X01",
        "primary_answer": {
            "card_name": "Test Card",
            "card_type": "Character",
            "color": "Red",
            "rarity": "C",
            "cost": 3,
            "power": "4000",
        },
        "expected": {
            "card_name": "verified-correct",
            "card_type": "verified-correct",
            "color": "verified-correct",
            "rarity": "verified-correct",
            "cost": "verified-correct",
            "power": "verified-correct",
        },
    }
    passed, mismatches = _evaluate_case(case, fake_catalog, empty_bandai)
    assert passed is True, f"mismatches: {mismatches}"


def test_case_detects_wrong_answer(fake_catalog: Path, empty_bandai: BandaiSource):
    case: dict[str, Any] = {
        "name": "wrong cost",
        "canonical_code": "OP01-X01",
        "print_id": "OP01-X01",
        "primary_answer": {"cost": 99},
        "expected": {"cost": "verified-wrong"},
    }
    passed, mismatches = _evaluate_case(case, fake_catalog, empty_bandai)
    assert passed is True


def test_case_detects_normalization_edge_case(fake_catalog: Path, empty_bandai: BandaiSource):
    """4000 vs '4,000' should normalize to equal."""
    case: dict[str, Any] = {
        "name": "comma-separated power",
        "canonical_code": "OP01-X01",
        "print_id": "OP01-X01",
        "primary_answer": {"power": "4,000"},
        "expected": {"power": "verified-correct"},
    }
    passed, mismatches = _evaluate_case(case, fake_catalog, empty_bandai)
    assert passed is True, f"mismatches: {mismatches}"


def test_case_missing_card_fails(fake_catalog: Path, empty_bandai: BandaiSource):
    case: dict[str, Any] = {
        "name": "no such card",
        "canonical_code": "OP01-NEVER",
        "print_id": "OP01-NEVER",
        "primary_answer": {},
        "expected": {},
    }
    passed, mismatches = _evaluate_case(case, fake_catalog, empty_bandai)
    assert passed is False
    assert any("not found in catalog" in m for m in mismatches)


def test_case_expected_mismatch_is_reported(fake_catalog: Path, empty_bandai: BandaiSource):
    """If expected says 'verified-correct' but verifier says 'verified-wrong', report it."""
    case: dict[str, Any] = {
        "name": "expectation does not match actual",
        "canonical_code": "OP01-X01",
        "print_id": "OP01-X01",
        "primary_answer": {"cost": 99},
        "expected": {"cost": "verified-correct"},  # but actual will be verified-wrong
    }
    passed, mismatches = _evaluate_case(case, fake_catalog, empty_bandai)
    assert passed is False
    assert any(
        "cost" in m and "verified-correct" in m and "verified-wrong" in m for m in mismatches
    )


def test_run_bootstrap_passes_with_clean_fixtures(fake_catalog: Path, tmp_path: Path):
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "name": "ok",
                        "canonical_code": "OP01-X01",
                        "print_id": "OP01-X01",
                        "primary_answer": {"card_name": "Test Card", "cost": 3},
                        "expected": {
                            "card_name": "verified-correct",
                            "cost": "verified-correct",
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    rc = run_bootstrap(
        fixtures_path=fixtures,
        catalog_db=fake_catalog,
        bandai_crawl_path=tmp_path / "missing.json",
    )
    assert rc == 0


def test_run_bootstrap_returns_non_zero_on_mismatch(fake_catalog: Path, tmp_path: Path):
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "expectation wrong",
                        "canonical_code": "OP01-X01",
                        "print_id": "OP01-X01",
                        "primary_answer": {"cost": 99},
                        "expected": {"cost": "verified-correct"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    rc = run_bootstrap(
        fixtures_path=fixtures,
        catalog_db=fake_catalog,
        bandai_crawl_path=tmp_path / "missing.json",
    )
    assert rc == 1


def test_run_bootstrap_missing_fixtures_file(tmp_path: Path):
    rc = run_bootstrap(fixtures_path=tmp_path / "nope.json")
    assert rc != 0


def test_run_bootstrap_empty_cases(fake_catalog: Path, tmp_path: Path):
    fixtures = tmp_path / "fixtures.json"
    fixtures.write_text(json.dumps({"cases": []}), encoding="utf-8")
    rc = run_bootstrap(
        fixtures_path=fixtures,
        catalog_db=fake_catalog,
        bandai_crawl_path=tmp_path / "missing.json",
    )
    assert rc != 0
