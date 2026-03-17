"""Unit tests for source-agreement computation (compute-on-read only)."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.miru_source_agreement import (
    COMPARABLE_FIELDS,
    compute_card_source_agreement,
)


def _create_dossier_db(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS learning_dossier_sources (
                card_code TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_reference TEXT NOT NULL DEFAULT '',
                field_payload_json TEXT NOT NULL DEFAULT '{}',
                verification_state TEXT NOT NULL DEFAULT 'source-fetched',
                fetched_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL,
                UNIQUE(card_code, source_id, source_reference)
            )
            """
        )
        conn.commit()


def _insert_source(path: Path, card_code: str, source_id: str, source_reference: str, payload: dict) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO learning_dossier_sources (
                card_code, source_id, source_reference, field_payload_json,
                verification_state, fetched_at, updated_at
            ) VALUES (?, ?, ?, ?, 'source-fetched', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
            """,
            (card_code, source_id, source_reference, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        conn.commit()


def _minimal_payload(
    card_code: str = "OP01-001",
    card_name: str = "Monkey D. Luffy",
    set_code: str = "OP01",
    set_name: str = "Romance Dawn",
    effect_text: str = "[DON!! x1] This Leader gains +1000 power.",
    **kwargs: str | int | list[str],
) -> dict:
    p: dict = {
        "card_code": card_code,
        "card_name": card_name,
        "set_code": set_code,
        "set_name": set_name,
        "rarity": "L",
        "color": "Red",
        "card_type": "Leader",
        "cost": "",
        "power": "5000",
        "counter": "",
        "attribute": "Strike",
        "traits": ["Supernovas", "Straw Hat Crew"],
        "life": "5",
        "effect_text": effect_text,
        "trigger_text": "",
        "source_id": "official-cardlist",
        "source_url": "",
        "source_reference": "official-op01-001",
        "fetched_at": "2026-01-01 00:00:00",
    }
    p.update(kwargs)
    return p


class MiruSourceAgreementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="miru-source-agreement-")
        self.dossier_db = Path(self.temp_dir.name) / "miru_learning_dossiers.db"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_return_structure_has_required_keys(self) -> None:
        _create_dossier_db(self.dossier_db)
        result = compute_card_source_agreement("OP01-001", dossier_db_path=self.dossier_db)
        for key in ("card_code", "source_count", "agreement_level", "agree_count", "conflict_count", "checked_fields"):
            self.assertIn(key, result)
        self.assertEqual(result["card_code"], "OP01-001")
        self.assertEqual(result["checked_fields"], list(COMPARABLE_FIELDS))

    def test_single_source_returns_single_source_level(self) -> None:
        _create_dossier_db(self.dossier_db)
        _insert_source(
            self.dossier_db,
            "OP01-001",
            "official-cardlist",
            "official-op01-001",
            _minimal_payload(),
        )
        result = compute_card_source_agreement("OP01-001", dossier_db_path=self.dossier_db)
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["agreement_level"], "single_source")
        self.assertEqual(result["agree_count"], 0)
        self.assertEqual(result["conflict_count"], 0)

    def test_two_sources_same_values_returns_full(self) -> None:
        _create_dossier_db(self.dossier_db)
        payload = _minimal_payload()
        _insert_source(self.dossier_db, "OP01-001", "official-cardlist", "ref1", payload)
        _insert_source(self.dossier_db, "OP01-001", "community-cardlist", "ref2", dict(payload, source_id="community-cardlist", source_reference="ref2"))
        result = compute_card_source_agreement("OP01-001", dossier_db_path=self.dossier_db)
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["agreement_level"], "full")
        self.assertGreater(result["agree_count"], 0)
        self.assertEqual(result["conflict_count"], 0)

    def test_two_sources_different_effect_text_returns_conflict(self) -> None:
        _create_dossier_db(self.dossier_db)
        _insert_source(
            self.dossier_db,
            "OP01-001",
            "official-cardlist",
            "ref1",
            _minimal_payload(effect_text="Effect A"),
        )
        _insert_source(
            self.dossier_db,
            "OP01-001",
            "community-cardlist",
            "ref2",
            _minimal_payload(effect_text="Effect B", source_id="community-cardlist", source_reference="ref2"),
        )
        result = compute_card_source_agreement("OP01-001", dossier_db_path=self.dossier_db)
        self.assertEqual(result["source_count"], 2)
        self.assertEqual(result["agreement_level"], "conflict")
        self.assertGreater(result["conflict_count"], 0)

    def test_two_sources_one_field_agree_rest_missing_returns_partial(self) -> None:
        _create_dossier_db(self.dossier_db)
        # Only card_name and set_code present in both; same values
        p1 = _minimal_payload(card_name="Luffy", set_code="OP01")
        for k in ("effect_text", "trigger_text", "traits", "life", "power", "counter", "attribute", "rarity", "color", "card_type", "cost", "set_name"):
            p1[k] = "" if k != "traits" else []
        p2 = dict(p1, source_id="community-cardlist", source_reference="ref2")
        _insert_source(self.dossier_db, "OP01-001", "official-cardlist", "ref1", p1)
        _insert_source(self.dossier_db, "OP01-001", "community-cardlist", "ref2", p2)
        result = compute_card_source_agreement("OP01-001", dossier_db_path=self.dossier_db)
        self.assertEqual(result["source_count"], 2)
        self.assertIn(result["agreement_level"], ("full", "partial"))
        self.assertGreaterEqual(result["agree_count"], 1)
        self.assertEqual(result["conflict_count"], 0)

    def test_missing_db_returns_single_source(self) -> None:
        result = compute_card_source_agreement("OP01-001", dossier_db_path=Path(self.temp_dir.name) / "nonexistent.db")
        self.assertEqual(result["card_code"], "OP01-001")
        self.assertEqual(result["source_count"], 0)
        self.assertEqual(result["agreement_level"], "single_source")

    def test_empty_card_code_returns_single_source(self) -> None:
        result = compute_card_source_agreement("", dossier_db_path=self.dossier_db)
        self.assertEqual(result["card_code"], "")
        self.assertEqual(result["source_count"], 0)
        self.assertEqual(result["agreement_level"], "single_source")


if __name__ == "__main__":
    unittest.main()
