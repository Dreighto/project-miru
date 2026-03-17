from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.miru_learning_engine import MiruLearningEngine
from tools.miru_project_sync import (
    MiruProjectDbSync,
    ensure_catalog_sync_schema,
    list_validation_audit_insights,
    load_card_validation_audit,
)
from tools.miru_source_adapters import NormalizedSourceRecord


def build_record(
    card_code: str,
    *,
    card_name: str = "Monkey D. Luffy",
    set_code: str = "OP01",
    set_name: str = "Romance Dawn",
    rarity: str = "L",
    color: str = "Red",
    card_type: str = "Leader",
    cost: str = "5",
    power: str = "5000",
    counter: str = "",
    attribute: str = "Strike",
    traits: list[str] | None = None,
    life: str = "5",
    effect_text: str = "[DON!! x1] This Leader gains +1000 power.",
    trigger_text: str = "Test trigger.",
    source_id: str = "official-cardlist",
    source_url: str = "https://asia-en.onepiece-cardgame.com/cardlist/",
    source_reference: str = "",
    fetched_at: str = "2026-03-10 12:00:00",
) -> NormalizedSourceRecord:
    return NormalizedSourceRecord(
        card_code=card_code,
        card_name=card_name,
        set_code=set_code,
        set_name=set_name,
        rarity=rarity,
        color=color,
        card_type=card_type,
        cost=cost,
        power=power,
        counter=counter,
        attribute=attribute,
        traits=traits or ["Supernovas", "Straw Hat Crew"],
        life=life,
        effect_text=effect_text,
        trigger_text=trigger_text,
        source_id=source_id,
        source_url=source_url,
        source_reference=source_reference or f"official-{card_code.lower()}",
        fetched_at=fetched_at,
    )


class MiruProjectSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="miru-project-sync-")
        root = Path(self.temp_dir.name)
        self.project_db = root / "card_catalog.db"
        self.logs: list[dict[str, str]] = []

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def log_event(self, **kwargs) -> None:
        self.logs.append({key: str(value) for key, value in kwargs.items()})

    def make_sync(self, *, batch_size: int = 3, sync_immediate: bool = True) -> MiruProjectDbSync:
        return MiruProjectDbSync(
            project_db_path=self.project_db,
            batch_size=batch_size,
            sync_immediate=sync_immediate,
            logger=self.log_event,
        )

    def test_insert_new_card_syncs_into_project_db(self) -> None:
        sync = self.make_sync()
        sync.queue_validated_record(build_record("OP01-001"))

        conn = sqlite3.connect(self.project_db)
        try:
            conn.row_factory = sqlite3.Row
            card = conn.execute("SELECT * FROM cards WHERE canonical_code = 'OP01-001'").fetchone()
            validation = conn.execute("SELECT * FROM miru_validations WHERE card_code = 'OP01-001'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(card)
        self.assertIsNotNone(validation)
        assert card is not None
        assert validation is not None
        self.assertEqual(card["card_name"], "Monkey D. Luffy")
        self.assertEqual(card["set_name"], "Romance Dawn")
        self.assertEqual(card["cost"], 5)
        self.assertEqual(card["attribute"], "Strike")
        self.assertEqual(card["effect_text"], "[DON!! x1] This Leader gains +1000 power.")
        self.assertEqual(card["trigger_text"], "Test trigger.")
        self.assertEqual(json.loads(validation["validated_fields_json"])[0], "card_name")
        self.assertEqual(json.loads(validation["sources_json"])[0]["source_id"], "official-cardlist")
        self.assertEqual(json.loads(validation["winning_source_json"])["source_id"], "official-cardlist")
        self.assertIn("Official source evidence", validation["confidence_reason"])

    def test_update_existing_card_only_overwrites_validated_non_empty_fields(self) -> None:
        sync = self.make_sync()
        sync.queue_validated_record(build_record("OP01-001", effect_text="Old effect.", trigger_text="Old trigger."))
        sync.queue_validated_record(build_record("OP01-001", effect_text="New effect.", trigger_text=""))

        conn = sqlite3.connect(self.project_db)
        try:
            row = conn.execute(
                "SELECT effect_text, trigger_text FROM cards WHERE canonical_code = 'OP01-001'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "New effect.")
        self.assertEqual(row[1], "Old trigger.")

    def test_lower_trust_conflicting_data_does_not_override_official_value(self) -> None:
        sync = self.make_sync()
        sync.queue_validated_record(build_record("OP01-001", color="Red", source_id="official-cardlist"))
        result = sync.queue_validated_record(
            build_record(
                "OP01-001",
                color="Blue",
                source_id="reputable-card-db",
                source_url="https://example.invalid/reputable/op01-001",
                source_reference="reputable-op01-001",
            )
        )

        self.assertEqual(result["failed"], 0)
        conn = sqlite3.connect(self.project_db)
        try:
            conn.row_factory = sqlite3.Row
            card = conn.execute("SELECT color FROM cards WHERE canonical_code = 'OP01-001'").fetchone()
            validation = conn.execute("SELECT * FROM miru_validations WHERE card_code = 'OP01-001'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(card)
        self.assertIsNotNone(validation)
        assert card is not None
        assert validation is not None
        self.assertEqual(card["color"], "Red")
        conflict_summary = json.loads(validation["conflict_summary_json"])
        rejected_sources = json.loads(validation["rejected_sources_json"])
        self.assertEqual(conflict_summary["rule"], "prefer-existing-higher-trust")
        self.assertIn("color", conflict_summary["rejected_fields"])
        self.assertEqual(rejected_sources[0]["source_id"], "reputable-card-db")

    def test_single_weak_source_is_skipped_below_sync_threshold(self) -> None:
        sync = self.make_sync()
        result = sync.queue_validated_record(
            build_record(
                "OP01-001",
                source_id="community-market",
                source_url="https://example.invalid/community/op01-001",
                source_reference="community-op01-001",
            )
        )

        self.assertEqual(result["failed"], 1)
        self.assertTrue(any(item.get("event_type") == "card_sync_failed" for item in self.logs))
        conn = sqlite3.connect(self.project_db)
        try:
            total_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(total_cards, 0)

    def test_duplicate_sync_is_idempotent_and_does_not_duplicate_rows(self) -> None:
        sync = self.make_sync()
        record = build_record("OP01-001")
        sync.queue_validated_record(record)
        sync.queue_validated_record(record)

        conn = sqlite3.connect(self.project_db)
        try:
            cards_count = conn.execute("SELECT COUNT(*) FROM cards WHERE canonical_code = 'OP01-001'").fetchone()[0]
            validations_count = conn.execute(
                """
                SELECT COUNT(*) FROM miru_validations
                WHERE card_code = 'OP01-001'
                """
            ).fetchone()[0]
            sets_count = conn.execute(
                """
                SELECT COUNT(*) FROM sets
                WHERE set_code = 'OP01'
                """
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(cards_count, 1)
        self.assertEqual(validations_count, 1)
        self.assertEqual(sets_count, 1)

    def test_batch_flush_syncs_after_threshold_when_immediate_mode_is_disabled(self) -> None:
        sync = self.make_sync(batch_size=2, sync_immediate=False)
        sync.queue_validated_record(build_record("OP01-001"))
        conn = sqlite3.connect(self.project_db)
        try:
            total_cards_before = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(total_cards_before, 0)

        sync.queue_validated_record(build_record("OP01-002", card_name="Trafalgar Law", rarity="SR", color="Green"))

        conn = sqlite3.connect(self.project_db)
        try:
            total_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(total_cards, 2)

    def test_failed_sync_is_logged_and_does_not_corrupt_db(self) -> None:
        sync = self.make_sync()
        with patch.object(sync, "_sync_payload", side_effect=RuntimeError("boom")):
            result = sync.queue_validated_record(build_record("OP01-001"))

        self.assertEqual(result["failed"], 1)
        self.assertEqual(len(sync._pending), 1)
        self.assertTrue(any(item.get("event_type") == "card_sync_failed" for item in self.logs))
        if self.project_db.exists():
            conn = sqlite3.connect(self.project_db)
            try:
                total_cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(total_cards, 0)

    def test_sync_creates_meta_intelligence_foundation_tables(self) -> None:
        ensure_catalog_sync_schema(self.project_db)
        conn = sqlite3.connect(self.project_db)
        try:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        finally:
            conn.close()
        table_names = {row[0] for row in rows}
        self.assertIn("miru_validations", table_names)
        self.assertIn("miru_card_usage", table_names)
        self.assertIn("miru_deck_archetypes", table_names)
        self.assertIn("miru_meta_events", table_names)

    def test_validation_provenance_records_winning_source_and_confidence_summary(self) -> None:
        sync = self.make_sync()
        sync.queue_validated_record(
            build_record(
                "OP01-001",
                source_id="reputable-card-db",
                source_url="https://example.invalid/reputable/op01-001",
                source_reference="reputable-op01-001",
            )
        )

        conn = sqlite3.connect(self.project_db)
        try:
            conn.row_factory = sqlite3.Row
            validation = conn.execute("SELECT * FROM miru_validations WHERE card_code = 'OP01-001'").fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(validation)
        assert validation is not None
        winning_source = json.loads(validation["winning_source_json"])
        conflict_summary = json.loads(validation["conflict_summary_json"])
        self.assertEqual(winning_source["source_id"], "reputable-card-db")
        self.assertEqual(winning_source["trust_tier"], 2)
        self.assertEqual(conflict_summary["rule"], "no-conflict")
        self.assertIn("high-confidence community source", validation["confidence_reason"])

    def test_load_card_validation_audit_returns_canonical_values_and_reasoning(self) -> None:
        sync = self.make_sync()
        sync.queue_validated_record(build_record("OP01-001"))

        audit = load_card_validation_audit("OP01-001", project_db_path=self.project_db)

        self.assertIsNotNone(audit)
        assert audit is not None
        self.assertEqual(audit["card_code"], "OP01-001")
        self.assertEqual(audit["canonical_values"]["card_name"], "Monkey D. Luffy")
        self.assertEqual(audit["winning_source"]["source_id"], "official-cardlist")
        self.assertIn("Official source evidence", audit["confidence_reason"])
        self.assertTrue(audit["sync_boundary"]["owns_canonical_upsert"])

    def test_validation_audit_insights_surface_conflicts_and_recent_cards(self) -> None:
        sync = self.make_sync()
        sync.queue_validated_record(build_record("OP01-001", source_id="official-cardlist"))
        sync.queue_validated_record(
            build_record(
                "OP01-001",
                color="Blue",
                source_id="reputable-card-db",
                source_url="https://example.invalid/reputable/op01-001",
                source_reference="reputable-op01-001",
            )
        )
        sync.queue_validated_record(
            build_record(
                "OP01-060",
                card_name="Dracule Mihawk",
                color="Blue",
                rarity="SR",
                card_type="Character",
                cost="9",
                power="9000",
                attribute="Slash",
                life="",
                trigger_text="",
                source_id="reputable-card-db",
                source_url="https://example.invalid/reputable/op01-060",
                source_reference="reputable-op01-060",
            )
        )

        insights = list_validation_audit_insights(project_db_path=self.project_db, limit=5)

        self.assertTrue(any(item["card_code"] == "OP01-001" for item in insights["recent_conflicts"]))
        self.assertTrue(any(item["card_code"] == "OP01-060" for item in insights["lowest_confidence"]))
        self.assertTrue(any(item["card_code"] == "OP01-001" for item in insights["rejected_evidence"]))
        self.assertGreaterEqual(len(insights["recently_validated"]), 1)


class MiruLearningEngineProjectSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="miru-learning-engine-sync-")
        root = Path(self.temp_dir.name)
        self.queue_db = root / "miru_learning_queue.db"
        self.status_db = root / "miru_learning_log.db"
        self.learning_dossier_db = root / "miru_learning_dossiers.db"
        self.project_db = root / "card_catalog.db"
        self.catalog_db = root / "card_catalog.db"
        self.knowledge_cache = root / "miru_ai_onepiece_knowledge.json"
        self.official_fixture = Path("tests/fixtures/miru_official_cardlist_sample.json")
        self.knowledge_cache.write_text(
            json.dumps(
                {
                    "sets": {"OP01": {"set_name": "Romance Dawn", "series_code_display": "OP01", "series_id": "OP01", "sources": ["fixture"]}},
                    "cards": {
                        "OP01-001": {
                            "canonical_code": "OP01-001",
                            "set_code": "OP01",
                            "set_name": "Romance Dawn",
                            "card_name": "Monkey D. Luffy",
                            "rarity": "L",
                            "color": "Red",
                            "card_type": "Leader",
                            "cost": 5,
                            "power": "5000",
                            "counter": "",
                            "attribute": "Strike",
                            "traits": "Supernovas/Straw Hat Crew",
                            "life": "5",
                            "effect_text": "[DON!! x1] This Leader gains +1000 power.",
                            "trigger_text": "",
                            "aliases": [],
                            "prints": [],
                            "field_sources": {},
                            "discrepancies": [],
                            "sources": ["fixture"],
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.engine = MiruLearningEngine(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.learning_dossier_db,
            project_db_path=self.project_db,
            catalog_db_path=self.catalog_db,
            knowledge_cache_path=self.knowledge_cache,
            sleep_seconds=0.1,
            sync_batch_size=2,
            sync_immediate=True,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_verify_official_fields_syncs_validated_card_into_project_db(self) -> None:
        self.engine.ensure_datastores()
        self.assertTrue(
            self.engine.enqueue_task(
                card_code="OP01-001",
                task_type="verify_official_fields",
                source_id="official-cardlist",
                priority=40,
                task_payload={"snapshot_path": str(self.official_fixture)},
            )
        )

        result = self.engine.process_one()

        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        conn = sqlite3.connect(self.project_db)
        try:
            row = conn.execute(
                "SELECT card_name, effect_text FROM cards WHERE canonical_code = 'OP01-001'"
            ).fetchone()
            validation = conn.execute(
                "SELECT confidence FROM miru_validations WHERE card_code = 'OP01-001'"
            ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertIsNotNone(validation)
        self.assertEqual(row[0], "Monkey D. Luffy")
        self.assertEqual(row[1], "[DON!! x1] This Leader gains +1000 power.")
        self.assertGreaterEqual(float(validation[0]), 0.9)


if __name__ == "__main__":
    unittest.main()
