from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from dashboard.miru_dossier_queries import (
    get_conflict_summary,
    get_fact_answer,
    get_identity_summary,
    get_source_summary,
    get_variant_answer,
)
from dashboard.miru_intel_adapters import OfficialCardListSnapshotAdapter, StaticJsonAdapter
from dashboard.miru_intel_db import MiruIntelRepository
from dashboard.miru_intel_pipeline import MiruEnrichmentRunner


class MiruDossierQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / "_tmp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / f"miru_dossier_query_{uuid.uuid4().hex}.db"
        self.repository = MiruIntelRepository(str(self.db_path))
        self.adapter = OfficialCardListSnapshotAdapter.from_path(
            "tests/fixtures/miru_official_cardlist_sample.json"
        )
        self.runner = MiruEnrichmentRunner(self.repository, [self.adapter])
        self.runner.enrich_card("OP01-001", run_id="query-run-1")
        self.runner.enrich_card("OP01-060", run_id="query-run-2")

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def test_identity_question_what_card_is_op01001(self) -> None:
        dossier = self.repository.build_card_dossier("OP01-001")
        answer = get_identity_summary(dossier)
        self.assertEqual(answer["card_name"], "Monkey D. Luffy")
        self.assertIn("OP01-001 is Monkey D. Luffy", answer["answer"])

    def test_set_question_what_set_is_this_card_from(self) -> None:
        dossier = self.repository.build_card_dossier("OP01-001")
        answer = get_fact_answer(dossier, "set_name")
        self.assertEqual(answer["value"], "Romance Dawn")
        self.assertEqual(answer["verification_state"], "verified")

    def test_attribute_questions_return_color_type_and_stats(self) -> None:
        dossier = self.repository.build_card_dossier("OP01-001")
        color = get_fact_answer(dossier, "color")
        card_type = get_fact_answer(dossier, "card_type")
        power = get_fact_answer(dossier, "power")
        life = get_fact_answer(dossier, "life")
        cost = get_fact_answer(dossier, "cost")
        self.assertEqual(color["value"], "Red")
        self.assertEqual(card_type["value"], "Leader")
        self.assertEqual(power["value"], "5000")
        self.assertEqual(life["value"], "5")
        self.assertEqual(cost["verification_state"], "missing")

    def test_text_questions_return_effect_and_missing_trigger_honestly(self) -> None:
        dossier = self.repository.build_card_dossier("OP01-001")
        effect = get_fact_answer(dossier, "effect_text")
        trigger = get_fact_answer(dossier, "trigger_text")
        self.assertEqual(effect["value"], "[DON!! x1] This Leader gains +1000 power.")
        self.assertEqual(trigger["verification_state"], "missing")
        self.assertIn("does not currently provide", trigger["answer"])

    def test_variant_question_reports_variant_and_official_image_records(self) -> None:
        dossier = self.repository.build_card_dossier("OP01-001")
        answer = get_variant_answer(dossier)
        self.assertTrue(answer["has_variant"])
        self.assertIn("Alt Art", answer["variant_labels"])
        self.assertTrue(any("op01-001-alt.png" in value for value in answer["image_identities"]))

    def test_source_question_explains_why_a_fact_is_verified(self) -> None:
        dossier = self.repository.build_card_dossier("OP01-001")
        answer = get_source_summary(dossier, "card_name")
        self.assertEqual(answer["verification_state"], "verified")
        self.assertEqual(answer["selected_sources"][0]["source_key"], "official-cardlist")
        self.assertIn("Official One Piece Card List", answer["answer"])

    def test_missing_data_question_reports_absent_field_cleanly(self) -> None:
        dossier = self.repository.build_card_dossier("OP01-060")
        answer = get_fact_answer(dossier, "trigger_text")
        self.assertEqual(answer["verification_state"], "missing")
        self.assertIn("does not currently provide trigger_text", answer["answer"])

    def test_conflict_question_reports_lower_tier_disagreement(self) -> None:
        secondary = StaticJsonAdapter(
            {
                "cards": {
                    "OP01-001": [
                        {
                            "source_key": "community-market",
                            "source_url": "https://example.com/community/op01-001",
                            "source_title": "Community Listing",
                            "source_card_ref": "community-op01-001",
                            "observed_at": "2026-03-07 19:00:00",
                            "card_name": "Monkey D. Luffy",
                            "set_code": "OP01",
                            "set_name": "Romance Dawn",
                            "rarity": "L",
                            "color": "Blue",
                            "card_type": "Leader"
                        }
                    ]
                }
            }
        )
        conflict_db = self.temp_dir / f"miru_dossier_conflict_{uuid.uuid4().hex}.db"
        conflict_repository = MiruIntelRepository(str(conflict_db))
        conflict_runner = MiruEnrichmentRunner(conflict_repository, [self.adapter, secondary])
        conflict_runner.enrich_card("OP01-001", run_id="query-conflict")
        dossier = conflict_repository.build_card_dossier("OP01-001")
        answer = get_conflict_summary(dossier, "color")
        self.assertIn("did not choose a winner", answer["answer"])
        self.assertIn("Red", answer["candidates"])
        self.assertIn("Blue", answer["candidates"])
        if conflict_db.exists():
            conflict_db.unlink()


if __name__ == "__main__":
    unittest.main()
