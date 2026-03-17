from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tools.miru_learning_engine import (
    MiruLearningEngine,
    load_learning_engine_status,
)


FIXTURE_PAYLOAD = {
    "sets": {
        "OP01": {
            "set_name": "Romance Dawn",
            "series_code_display": "OP01",
            "series_id": "OP01",
            "sources": ["fixture"],
        }
    },
    "cards": {
        "OP01-001": {
            "canonical_code": "OP01-001",
            "set_code": "OP01",
            "set_name": "Romance Dawn",
            "card_name": "Roronoa Zoro",
            "rarity": "L",
            "color": "Red",
            "card_type": "Leader",
            "cost": 5,
            "power": "5000",
            "counter": "",
            "attribute": "Slash",
            "traits": "Straw Hat Crew",
            "life": "5",
            "block_icon": "1",
            "effect_text": "On Play: Test effect.",
            "trigger_text": "",
            "aliases": [],
            "prints": [
                {
                    "variant_label": "Base",
                    "variant_key": "base",
                    "print_id": "OP01-001_base",
                    "release_set_code": "OP01",
                    "release_set_name": "Romance Dawn",
                    "image_path": "",
                    "image_url": "",
                }
            ],
            "field_sources": {},
            "discrepancies": [],
            "sources": ["fixture"],
        },
        "OP01-002": {
            "canonical_code": "OP01-002",
            "set_code": "OP01",
            "set_name": "Romance Dawn",
            "card_name": "Trafalgar Law",
            "rarity": "SR",
            "color": "Green",
            "card_type": "Character",
            "cost": 3,
            "power": "4000",
            "counter": "1000",
            "attribute": "Slash",
            "traits": "Heart Pirates",
            "life": "",
            "block_icon": "1",
            "effect_text": "",
            "trigger_text": "",
            "aliases": [],
            "prints": [
                {
                    "variant_label": "Parallel",
                    "variant_key": "parallel",
                    "print_id": "OP01-002_p1",
                    "release_set_code": "OP01",
                    "release_set_name": "Romance Dawn",
                    "image_path": "cards/op01-002.png",
                    "image_url": "https://example.invalid/op01-002.png",
                }
            ],
            "field_sources": {},
            "discrepancies": [],
            "sources": ["fixture"],
        },
    },
}


class MiruLearningEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="miru-learning-engine-")
        root = Path(self.temp_dir.name)
        self.queue_db = root / "miru_learning_queue.db"
        self.status_db = root / "miru_learning_log.db"
        self.dossier_db = root / "miru_learning_dossiers.db"
        self.catalog_db = root / "card_catalog.db"
        self.knowledge_cache = root / "miru_ai_onepiece_knowledge.json"
        self.official_fixture = Path("tests/fixtures/miru_official_cardlist_sample.json")
        self.knowledge_cache.write_text(json.dumps(FIXTURE_PAYLOAD, ensure_ascii=False), encoding="utf-8")
        self.engine = MiruLearningEngine(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
            catalog_db_path=self.catalog_db,
            knowledge_cache_path=self.knowledge_cache,
            sleep_seconds=0.1,
            max_attempts=2,
            seed_batch_size=2,
            max_parallel_validations=2,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_once_bootstraps_dossier_and_updates_status(self) -> None:
        result = self.engine.run_once(card_code="OP01-001", task_type="bootstrap_dossier")

        self.assertTrue(result["ok"])
        self.assertTrue(self.queue_db.is_file())
        self.assertTrue(self.status_db.is_file())
        self.assertTrue(self.dossier_db.is_file())

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
        )
        self.assertEqual(snapshot["processed_count"], 1)
        self.assertEqual(snapshot["success_count"], 1)
        self.assertEqual(snapshot["error_count"], 0)
        self.assertEqual(snapshot["last_completed_card"], "OP01-001")
        self.assertEqual(snapshot["last_completed_task"], "bootstrap_dossier")
        self.assertEqual(snapshot["queue_length"], 0)
        self.assertEqual(snapshot["dossier_count"], 1)

        with closing(sqlite3.connect(self.dossier_db)) as conn:
            row = conn.execute(
                "SELECT card_name, set_code, rarity, source_summary, basic_facts_json FROM learning_dossiers WHERE card_code = ?",
                ("OP01-001",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Roronoa Zoro")
        self.assertEqual(row[1], "OP01")
        self.assertEqual(row[2], "L")
        self.assertIn("knowledge-cache", row[3])
        basic_facts = json.loads(row[4])
        self.assertEqual(basic_facts["card_type"], "Leader")

    def test_image_inspection_and_refresh_tasks_write_engine_state(self) -> None:
        self.engine.ensure_datastores()
        self.assertTrue(self.engine.enqueue_task(card_code="OP01-002", task_type="inspect_missing_image", priority=20))
        self.assertTrue(self.engine.enqueue_task(task_type="refresh_progress", priority=10))

        first = self.engine.process_one()
        second = self.engine.process_one()

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
        )
        self.assertEqual(snapshot["processed_count"], 2)
        self.assertEqual(snapshot["success_count"], 2)
        self.assertEqual(snapshot["queue_length"], 0)
        self.assertEqual(snapshot["dossier_count"], 1)

        dossier = self.engine.fetch_dossier("OP01-002")
        self.assertIsNotNone(dossier)
        self.assertTrue(dossier["basic_facts"]["has_local_image"])
        self.assertEqual(dossier["basic_facts"]["image_variant_count"], 1)

    def test_source_tasks_store_official_fields_and_update_source_status(self) -> None:
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

        self.assertTrue(result["ok"])
        self.assertEqual(result["source_id"], "official-cardlist")
        self.assertEqual(result["source_reference"], "official-op01-001")

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
        )
        self.assertEqual(snapshot["processed_count"], 1)
        self.assertEqual(snapshot["source_success_count"], 1)
        self.assertEqual(snapshot["source_error_count"], 0)
        self.assertEqual(snapshot["last_source_id"], "official-cardlist")
        self.assertEqual(snapshot["last_source_reference"], "official-op01-001")

        with closing(sqlite3.connect(self.dossier_db)) as conn:
            source_row = conn.execute(
                """
                SELECT source_id, source_reference, verification_state
                FROM learning_dossier_sources
                WHERE card_code = ?
                """,
                ("OP01-001",),
            ).fetchone()
        self.assertIsNotNone(source_row)
        self.assertEqual(source_row[0], "official-cardlist")
        self.assertEqual(source_row[1], "official-op01-001")
        self.assertEqual(source_row[2], "verified-source-fields")

        dossier = self.engine.fetch_dossier("OP01-001")
        self.assertIsNotNone(dossier)
        self.assertEqual(dossier["verification_state"], "source-backed")
        self.assertEqual(dossier["basic_facts"]["source_id"], "official-cardlist")
        self.assertEqual(dossier["basic_facts"]["card_name"], "Monkey D. Luffy")
        self.assertEqual(dossier["basic_facts"]["effect_text"], "[DON!! x1] This Leader gains +1000 power.")

    def test_community_cardlist_snapshot_only_preserves_provenance(self) -> None:
        """Approved source community-cardlist (snapshot-only) preserves source_id and confidence."""
        from tools.miru_source_registry import build_source_registry, get_source_entry

        registry = build_source_registry()
        self.assertIn("community-cardlist", registry)
        entry = get_source_entry("community-cardlist", registry)
        self.assertTrue(entry.enabled)
        self.assertEqual(entry.trust_tier, 3)
        self.assertFalse(entry.requires_api)

        self.engine.ensure_datastores()
        self.assertTrue(
            self.engine.enqueue_task(
                card_code="OP01-001",
                task_type="verify_official_fields",
                source_id="community-cardlist",
                priority=40,
                task_payload={"snapshot_path": str(self.official_fixture)},
            )
        )

        result = self.engine.process_one()

        self.assertTrue(result["ok"])
        self.assertEqual(result.get("source_id"), "community-cardlist")
        self.assertEqual(result.get("source_reference"), "official-op01-001")

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
        )
        self.assertEqual(snapshot["source_success_count"], 1)
        self.assertEqual(snapshot["last_source_id"], "community-cardlist")

        with closing(sqlite3.connect(self.dossier_db)) as conn:
            source_row = conn.execute(
                """
                SELECT source_id, source_reference, verification_state
                FROM learning_dossier_sources
                WHERE card_code = ?
                """,
                ("OP01-001",),
            ).fetchone()
        self.assertIsNotNone(source_row)
        self.assertEqual(source_row[0], "community-cardlist")
        self.assertEqual(source_row[2], "verified-source-fields")

        dossier = self.engine.fetch_dossier("OP01-001")
        self.assertIsNotNone(dossier)
        self.assertEqual(dossier["basic_facts"]["source_id"], "community-cardlist")
        self.assertGreaterEqual(dossier.get("confidence", 0), 0.0)

    def test_discover_set_cards_queues_verification_for_entire_set(self) -> None:
        result = self.engine.run_once(
            task_type="discover_set_cards",
            source_id="official-cardlist",
            task_payload={"set_code": "OP01", "snapshot_path": str(self.official_fixture)},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["set_code"], "OP01")
        self.assertEqual(result["cards_discovered"], 2)
        self.assertEqual(result["queued_tasks"], 2)

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
        )
        self.assertEqual(snapshot["queue_length"], 2)

    def test_discover_sources_stores_review_candidates(self) -> None:
        result = self.engine.run_once(
            task_type="discover_sources",
            task_payload={
                "urls": [
                    "https://onepiecetopdecks.com/deck-list/op10-meta-report/",
                    {
                        "url": "https://egmanevents.com/one-piece/results/regional-top-cut",
                        "title": "Regional Top Cut",
                    },
                ]
            },
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["discovered_candidates"], 2)
        self.assertEqual(result["new_candidates"], 2)

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
        )
        self.assertEqual(snapshot["discovery_candidate_count"], 2)
        self.assertEqual(snapshot["discovery_pending_review_count"], 2)

        with closing(sqlite3.connect(self.status_db)) as conn:
            rows = conn.execute(
                "SELECT source_kind, review_status FROM discovered_sources ORDER BY id ASC"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][1], "pending_review")
        self.assertEqual(rows[1][1], "pending_review")

    def test_parallel_batch_processes_multiple_validations_and_reports_metrics(self) -> None:
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
        self.assertTrue(
            self.engine.enqueue_task(
                card_code="OP01-060",
                task_type="verify_official_fields",
                source_id="official-cardlist",
                priority=40,
                task_payload={"snapshot_path": str(self.official_fixture)},
            )
        )

        original_fetch = self.engine.fetch_official_source_records

        def slow_fetch(*args, **kwargs):
            time.sleep(1.1)
            return original_fetch(*args, **kwargs)

        with patch.object(self.engine, "fetch_official_source_records", side_effect=slow_fetch):
            started = time.monotonic()
            results = self.engine.process_parallel_batch(limit=2)
            elapsed = time.monotonic() - started

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["ok"] for result in results))

        with tempfile.TemporaryDirectory(prefix="miru-learning-sequential-") as other_root_str:
            other_root = Path(other_root_str)
            other_queue_db = other_root / "miru_learning_queue.db"
            other_status_db = other_root / "miru_learning_log.db"
            other_dossier_db = other_root / "miru_learning_dossiers.db"
            other_catalog_db = other_root / "card_catalog.db"
            other_knowledge_cache = other_root / "miru_ai_onepiece_knowledge.json"
            other_knowledge_cache.write_text(json.dumps(FIXTURE_PAYLOAD, ensure_ascii=False), encoding="utf-8")
            sequential_engine = MiruLearningEngine(
                queue_db_path=other_queue_db,
                status_db_path=other_status_db,
                dossier_db_path=other_dossier_db,
                catalog_db_path=other_catalog_db,
                knowledge_cache_path=other_knowledge_cache,
                sleep_seconds=0.1,
                max_attempts=2,
                seed_batch_size=2,
                max_parallel_validations=1,
            )
            sequential_engine.ensure_datastores()
            self.assertTrue(
                sequential_engine.enqueue_task(
                    card_code="OP01-001",
                    task_type="verify_official_fields",
                    source_id="official-cardlist",
                    priority=40,
                    task_payload={"snapshot_path": str(self.official_fixture)},
                )
            )
            self.assertTrue(
                sequential_engine.enqueue_task(
                    card_code="OP01-060",
                    task_type="verify_official_fields",
                    source_id="official-cardlist",
                    priority=40,
                    task_payload={"snapshot_path": str(self.official_fixture)},
                )
            )
            sequential_fetch = sequential_engine.fetch_official_source_records

            def slow_sequential_fetch(*args, **kwargs):
                time.sleep(1.1)
                return sequential_fetch(*args, **kwargs)

            with patch.object(sequential_engine, "fetch_official_source_records", side_effect=slow_sequential_fetch):
                sequential_started = time.monotonic()
                first = sequential_engine.process_one()
                second = sequential_engine.process_one()
                sequential_elapsed = time.monotonic() - sequential_started

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])

        self.assertLess(elapsed, sequential_elapsed - 0.12)

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
        )
        self.assertEqual(snapshot["processed_count"], 2)
        self.assertEqual(snapshot["validated_card_count"], 2)
        self.assertEqual(snapshot["cards_learned_per_hour"], 2)
        self.assertEqual(snapshot["validation_success_rate"], 100.0)
        self.assertGreater(snapshot["average_validation_seconds"], 0.0)
        self.assertEqual(snapshot["queue_backlog"], 0)

    def test_duplicate_queue_entries_are_blocked_by_task_signature(self) -> None:
        self.engine.ensure_datastores()
        first = self.engine.enqueue_task(
            task_type="discover_set_cards",
            source_id="official-cardlist",
            priority=30,
            task_payload={"set_code": "OP01", "snapshot_path": str(self.official_fixture)},
        )
        duplicate = self.engine.enqueue_task(
            task_type="discover_set_cards",
            source_id="official-cardlist",
            priority=30,
            task_payload={"set_code": "OP01", "snapshot_path": str(self.official_fixture)},
        )

        self.assertTrue(first)
        self.assertFalse(duplicate)


if __name__ == "__main__":
    unittest.main()
