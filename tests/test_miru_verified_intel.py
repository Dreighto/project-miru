from __future__ import annotations

import sqlite3
import unittest
import uuid
from pathlib import Path

from dashboard.miru_intel_adapters import MiruKnowledgeCacheAdapter, OfficialCardListSnapshotAdapter, StaticJsonAdapter
from dashboard.miru_intel_db import MiruIntelRepository, init_miru_intel_schema
from dashboard.miru_intel_pipeline import MiruEnrichmentRunner
from dashboard.miru_intel_trust import build_source_registry, get_source_profile


class MiruVerifiedIntelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / "_tmp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / f"miru_verified_intel_{uuid.uuid4().hex}.db"
        self.fixture_path = Path("tests/fixtures/miru_official_cardlist_sample.json")
        self.repository = MiruIntelRepository(str(self.db_path))
        self.adapter = OfficialCardListSnapshotAdapter.from_path(self.fixture_path)
        self.runner = MiruEnrichmentRunner(self.repository, [self.adapter])

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()

    def test_schema_initialization_creates_expected_tables(self) -> None:
        init_miru_intel_schema(str(self.db_path))
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        finally:
            conn.close()
        table_names = {row[0] for row in rows}
        self.assertTrue({
            "cards",
            "card_variants",
            "card_relationships",
            "card_facts",
            "fact_sources",
            "confidence_records",
            "enrichment_runs",
            "enrichment_run_cards",
            "source_registry",
            "refresh_reports",
        }.issubset(table_names))

    def test_source_trust_registry_marks_official_path_as_tier_one(self) -> None:
        registry = build_source_registry()
        official = get_source_profile("official-cardlist", registry)
        self.assertEqual(official.trust_tier, 1)
        self.assertEqual(official.trust_label, "official")
        self.assertEqual(official.default_weight, 1.0)

    def test_official_adapter_parses_richer_snapshot_fields(self) -> None:
        records = self.adapter.fetch_card_records("OP01-001")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.source_key, "official-cardlist")
        self.assertEqual(record.source_card_ref, "official-op01-001")
        self.assertEqual(record.card_name, "Monkey D. Luffy")
        self.assertEqual(record.set_name, "Romance Dawn")
        self.assertEqual(record.power, "5000")
        self.assertEqual(record.life, "5")
        self.assertEqual(record.attribute, "Strike")
        self.assertEqual(record.traits, ("Supernovas", "Straw Hat Crew"))
        self.assertEqual(record.series_name, "One Piece Card Game")
        self.assertEqual(record.availability, "listed")
        self.assertEqual(len(record.variants), 2)

    def test_official_snapshot_builds_verified_dossier_with_richer_details(self) -> None:
        result = self.runner.enrich_card("OP01-001", run_id="official-run-1")
        dossier = self.repository.build_card_dossier("OP01-001")

        self.assertEqual(result["summary"]["overall_state"], "verified")
        self.assertIsNotNone(dossier)
        self.assertEqual(dossier.identity["card_name"], "Monkey D. Luffy")
        self.assertEqual(dossier.set_info["set_name"], "Romance Dawn")
        self.assertEqual(dossier.set_info["series_name"], "One Piece Card Game")
        self.assertEqual(dossier.official_details["power"], "5000")
        self.assertEqual(dossier.official_details["life"], "5")
        self.assertEqual(dossier.official_details["attribute"], "Strike")
        self.assertEqual(dossier.official_details["traits"], ["Supernovas", "Straw Hat Crew"])
        self.assertEqual(dossier.official_details["effect_text"], "[DON!! x1] This Leader gains +1000 power.")
        self.assertIn("card_name", dossier.confidence_summary.verified_fields)
        power_fact = next(item for item in dossier.facts if item.field_name == "power")
        self.assertEqual(power_fact.verification_state, "verified")
        self.assertTrue(any(citation.source_key == "official-cardlist" for citation in power_fact.citations))

    def test_knowledge_cache_adapter_splits_official_fields_from_local_variants(self) -> None:
        adapter = MiruKnowledgeCacheAdapter.from_path("data/miru_ai_onepiece_knowledge.json")
        records = adapter.fetch_card_records("EB01-001")

        self.assertEqual([record.source_key for record in records], ["official-cardlist", "local-catalog"])
        official = records[0]
        local = records[1]
        self.assertEqual(official.card_name, "Kouzuki Oden")
        self.assertEqual(official.set_name, "Memorial Collection")
        self.assertTrue(any(variant.image_identity.startswith("https://") for variant in official.variants))
        self.assertEqual(local.card_name, "")
        self.assertTrue(any(variant.image_identity.endswith(".webp") for variant in local.variants))

    def test_knowledge_cache_adapter_bootstraps_verified_dossier(self) -> None:
        adapter = MiruKnowledgeCacheAdapter.from_path("data/miru_ai_onepiece_knowledge.json")
        runner = MiruEnrichmentRunner(self.repository, [adapter])

        result = runner.enrich_card("EB01-001", run_id="knowledge-cache-run")
        dossier = self.repository.build_card_dossier("EB01-001")

        self.assertEqual(result["summary"]["overall_state"], "verified")
        self.assertIsNotNone(dossier)
        self.assertEqual(dossier.identity["card_name"], "Kouzuki Oden")
        self.assertEqual(dossier.set_info["set_name"], "Memorial Collection")
        self.assertIn("card_name", dossier.confidence_summary.verified_fields)
        image_fact = next(item for item in dossier.facts if item.field_name == "image_identity")
        self.assertEqual(image_fact.verification_state, "verified")
        self.assertTrue(any(citation.source_key == "official-cardlist" for citation in image_fact.citations))
        self.assertTrue(any(variant.variant_key == "parallel 1" for variant in dossier.variants))

    def test_missing_official_fields_remain_missing(self) -> None:
        self.runner.enrich_card("OP01-060", run_id="official-run-2")
        dossier = self.repository.build_card_dossier("OP01-060")
        self.assertIsNotNone(dossier)
        counter_fact = next(item for item in dossier.facts if item.field_name == "counter")
        trigger_fact = next(item for item in dossier.facts if item.field_name == "trigger_text")
        self.assertEqual(counter_fact.verification_state, "missing")
        self.assertEqual(trigger_fact.verification_state, "missing")
        self.assertEqual(dossier.official_details["cost"], "9")
        self.assertEqual(dossier.official_details["power"], "9000")
        self.assertEqual(dossier.official_details["traits"], ["The Seven Warlords of the Sea"])

    def test_conflicts_are_recorded_when_official_and_secondary_disagree(self) -> None:
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
        runner = MiruEnrichmentRunner(self.repository, [self.adapter, secondary])
        runner.enrich_card("OP01-001", run_id="official-run-3")
        dossier = self.repository.build_card_dossier("OP01-001")
        self.assertIsNotNone(dossier)
        color_fact = next(item for item in dossier.facts if item.field_name == "color")
        self.assertEqual(color_fact.verification_state, "conflict")
        self.assertTrue(any(citation.is_conflicting for citation in color_fact.citations))

    def test_run_batch_supports_resume_without_reprocessing_completed_cards(self) -> None:
        first = self.runner.run_batch(["OP01-001"], run_id="resume-run", notes="initial")
        second = self.runner.run_batch(["OP01-001"], run_id="resume-run", resume=True, notes="resume")
        run_rows = self.repository.list_run_cards("resume-run")

        self.assertEqual(first["results"][0]["status"], "completed")
        self.assertEqual(second["results"][0]["status"], "skipped")
        self.assertEqual(run_rows[0]["status"], "completed")
        run_summary = self.repository.load_run("resume-run")
        self.assertEqual(run_summary["completed_cards"], 1)
        self.assertEqual(run_summary["failed_cards"], 0)


if __name__ == "__main__":
    unittest.main()
