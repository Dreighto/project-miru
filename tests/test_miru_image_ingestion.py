from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.miru_learning_engine import (
    MiruLearningEngine,
    build_image_filename,
    load_learning_engine_status,
)


ONE_PX_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+vrLsAAAAASUVORK5CYII="
)


def build_minimal_knowledge_payload() -> dict[str, object]:
    return {
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
                "prints": [],
                "field_sources": {},
                "discrepancies": [],
                "sources": ["fixture"],
            }
        },
    }


class MiruImageIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="miru-image-ingest-")
        root = Path(self.temp_dir.name)
        self.queue_db = root / "miru_learning_queue.db"
        self.status_db = root / "miru_learning_log.db"
        self.dossier_db = root / "miru_learning_dossiers.db"
        self.catalog_db = root / "card_catalog.db"
        self.knowledge_cache = root / "miru_ai_onepiece_knowledge.json"
        self.image_root = root / "images"
        self.knowledge_cache.write_text(
            json.dumps(build_minimal_knowledge_payload(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.engine = MiruLearningEngine(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
            catalog_db_path=self.catalog_db,
            knowledge_cache_path=self.knowledge_cache,
            image_dest_root=self.image_root,
            sleep_seconds=0.1,
            max_attempts=1,
            seed_batch_size=1,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_image_filename_preserves_naming_rules(self) -> None:
        self.assertEqual(build_image_filename("OP09-001", ""), "OP09-001.png")
        self.assertEqual(build_image_filename("OP09-001", "alt"), "OP09-001(alt).png")
        self.assertEqual(build_image_filename("p-088", ""), "P-088.png")

    def test_fetch_card_image_creates_registry_entry(self) -> None:
        self.engine.ensure_datastores()
        source_image = Path(self.temp_dir.name) / "source.png"
        source_image.write_bytes(ONE_PX_PNG)
        snapshot_path = Path(self.temp_dir.name) / "image_snapshot.json"
        snapshot_payload = {
            "source": {"base_url": "https://example.invalid/images/"},
            "images": [
                {
                    "card_code": "OP01-001",
                    "variant_key": "alt",
                    "image_path": str(source_image),
                    "source_reference": "img-op01-001-alt",
                    "width": 1,
                    "height": 1,
                }
            ],
        }
        snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")

        self.assertTrue(
            self.engine.enqueue_task(
                card_code="OP01-001",
                variant_key="alt",
                task_type="fetch_card_image",
                source_id="official-card-images",
                priority=50,
                task_payload={"snapshot_path": str(snapshot_path)},
            )
        )
        result = self.engine.process_one()
        self.assertTrue(result["ok"])
        expected_path = self.image_root / "OP01-001(alt).png"
        self.assertTrue(expected_path.is_file())

        with closing(sqlite3.connect(self.dossier_db)) as conn:
            row = conn.execute(
                """
                SELECT filename, local_path, source_id, verification_state, width, height
                FROM learning_dossier_images
                WHERE card_code = ? AND variant_key = ? AND source_id = ?
                """,
                ("OP01-001", "alt", "official-card-images"),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "OP01-001(alt).png")
        self.assertEqual(row[2], "official-card-images")
        self.assertEqual(row[3], "provisional")
        self.assertEqual(row[4], 1)
        self.assertEqual(row[5], 1)

        snapshot = load_learning_engine_status(
            queue_db_path=self.queue_db,
            status_db_path=self.status_db,
            dossier_db_path=self.dossier_db,
            total_cards=1,
        )
        self.assertEqual(snapshot["image_success_count"], 1)
        self.assertEqual(snapshot["images_tracked"], 1)
        self.assertEqual(snapshot["images_missing"], 0)


if __name__ == "__main__":
    unittest.main()
