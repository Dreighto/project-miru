from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path

from dashboard.miru_dossier_queries import get_conflict_summary, get_fact_answer, get_source_summary
from dashboard.miru_intel_adapters import OfficialCardListSnapshotAdapter, StaticJsonAdapter
from dashboard.miru_intel_db import MiruIntelRepository
from dashboard.miru_intel_pipeline import MiruEnrichmentRunner
from dashboard.miru_snapshot_refresh import OfficialSnapshotRefresher, normalize_official_export_path


class MiruSnapshotRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = Path(__file__).resolve().parent / "_tmp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.temp_dir / f"miru_snapshot_refresh_{uuid.uuid4().hex}.db"
        self.snapshot_output_path = self.temp_dir / f"miru_snapshot_normalized_{uuid.uuid4().hex}.json"
        self.initial_snapshot = Path("tests/fixtures/miru_official_cardlist_sample.json")
        self.refresh_input = Path("tests/fixtures/miru_official_export_refresh_input.json")
        self.repository = MiruIntelRepository(str(self.db_path))
        seed_adapter = OfficialCardListSnapshotAdapter.from_path(self.initial_snapshot)
        MiruEnrichmentRunner(self.repository, [seed_adapter]).run_batch(["OP01-001", "OP01-060"], run_id="seed-run")

    def tearDown(self) -> None:
        if self.db_path.exists():
            self.db_path.unlink()
        if self.snapshot_output_path.exists():
            self.snapshot_output_path.unlink()

    def test_normalize_official_export_path_writes_snapshot_shape(self) -> None:
        snapshot = normalize_official_export_path(self.refresh_input, self.snapshot_output_path)
        self.assertTrue(self.snapshot_output_path.exists())
        self.assertEqual(snapshot["source"]["source_key"], "official-cardlist")
        self.assertEqual(snapshot["source"]["format"], "official-cardlist-snapshot")
        self.assertEqual(snapshot["source"]["source_export_format"], "official-cardlist-export")
        cards = {item["card_code"]: item for item in snapshot["cards"]}
        self.assertIn("OP01-060", cards)
        self.assertEqual(cards["OP01-060"]["counter"], "1000")
        self.assertNotIn("power", cards["OP01-060"])
        self.assertIn("counter", cards["OP01-060"]["present_fields"])
        self.assertNotIn("power", cards["OP01-060"]["present_fields"])

    def test_refresh_updates_dossier_answers_and_records_change_categories(self) -> None:
        before = self.repository.build_card_dossier("OP01-060")
        before_counter = get_fact_answer(before, "counter")
        before_power = get_fact_answer(before, "power")
        refresher = OfficialSnapshotRefresher(self.repository)
        result = refresher.refresh_from_export_path(
            self.refresh_input,
            snapshot_output_path=self.snapshot_output_path,
            run_id="refresh-run-1",
            notes="official refresh test",
        )
        after = self.repository.build_card_dossier("OP01-060")
        counter = get_fact_answer(after, "counter")
        power = get_fact_answer(after, "power")
        effect = get_fact_answer(after, "effect_text")
        source = get_source_summary(after, "counter")

        self.assertEqual(before_counter["verification_state"], "missing")
        self.assertEqual(before_power["value"], "9000")
        self.assertEqual(result["run"]["mode"], "official-snapshot-refresh")
        self.assertEqual(counter["value"], "1000")
        self.assertEqual(counter["verification_state"], "verified")
        # power was NOT in the refresh input (present_fields), but the fallback
        # adapter preserves the previously-verified value instead of demoting it.
        self.assertEqual(power["verification_state"], "verified")
        self.assertEqual(power["value"], "9000")
        self.assertIn("Return up to 1 Character", effect["value"])
        self.assertEqual(source["selected_sources"][0]["source_key"], "official-cardlist")

        reports = self.repository.list_refresh_reports(run_id="refresh-run-1", canonical_code="OP01-060")
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]["overall_category"], "added")
        report = json.loads(reports[0]["report_json"])
        categories = {item["field_name"]: item["category"] for item in report["field_changes"]}
        self.assertEqual(categories["counter"], "added")
        # power was absent from the refresh input; fallback preserved it → unchanged
        # (unchanged fields are omitted from field_changes, so key is absent)
        self.assertEqual(categories.get("power", "unchanged"), "unchanged")
        self.assertEqual(categories["effect_text"], "added")

    def test_unchanged_identity_answers_remain_stable_after_refresh(self) -> None:
        before = self.repository.build_card_dossier("OP01-001")
        before_name = get_fact_answer(before, "card_name")
        refresher = OfficialSnapshotRefresher(self.repository)
        refresher.refresh_from_export_path(self.refresh_input, run_id="refresh-run-2")
        after = self.repository.build_card_dossier("OP01-001")
        after_name = get_fact_answer(after, "card_name")
        after_availability = get_fact_answer(after, "availability")

        self.assertEqual(before_name["value"], after_name["value"])
        self.assertEqual(after_name["verification_state"], "verified")
        self.assertEqual(after_availability["value"], "featured")
        reports = self.repository.list_refresh_reports(run_id="refresh-run-2", canonical_code="OP01-001")
        report = json.loads(reports[0]["report_json"])
        self.assertGreater(report["counts"].get("unchanged", 0), 0)
        self.assertEqual(report["overall_category"], "updated")

    def test_refresh_preserves_conflicts_when_lower_tier_source_disagrees(self) -> None:
        secondary = StaticJsonAdapter(
            {
                "cards": {
                    "OP01-001": [
                        {
                            "source_key": "community-market",
                            "source_url": "https://example.com/community/op01-001",
                            "source_title": "Community Listing",
                            "source_card_ref": "community-op01-001",
                            "observed_at": "2026-03-08 10:00:00",
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
        refresher = OfficialSnapshotRefresher(self.repository, extra_adapters=[secondary])
        refresher.refresh_from_export_path(self.refresh_input, run_id="refresh-run-3")
        dossier = self.repository.build_card_dossier("OP01-001")
        conflict = get_conflict_summary(dossier, "color")
        self.assertIn("Red", conflict["candidates"])
        self.assertIn("Blue", conflict["candidates"])
        reports = self.repository.list_refresh_reports(run_id="refresh-run-3", canonical_code="OP01-001")
        self.assertEqual(reports[0]["overall_category"], "conflict")

    def test_refresh_resume_records_skipped_report(self) -> None:
        refresher = OfficialSnapshotRefresher(self.repository)
        refresher.refresh_from_export_path(self.refresh_input, run_id="refresh-resume")
        second = refresher.refresh_from_export_path(self.refresh_input, run_id="refresh-resume", resume=True)
        self.assertEqual(second["results"][0]["status"], "skipped")
        reports = self.repository.list_refresh_reports(run_id="refresh-resume", canonical_code="OP01-001")
        self.assertEqual(reports[-1]["overall_category"], "skipped")


if __name__ == "__main__":
    unittest.main()
