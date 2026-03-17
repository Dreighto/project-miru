from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.miru_ai_server import build_image_coverage_by_set


class MiruImageCoverageReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="miru-image-coverage-")
        root = Path(self.temp_dir.name)
        self.catalog_db = root / "card_catalog.db"
        self.dossier_db = root / "miru_learning_dossiers.db"

        with closing(sqlite3.connect(self.catalog_db)) as conn:
            conn.executescript(
                """
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_code TEXT NOT NULL,
                    set_code TEXT NOT NULL
                );
                INSERT INTO cards (canonical_code, set_code) VALUES
                    ('OP01-001', 'OP01'),
                    ('OP01-002', 'OP01'),
                    ('OP02-001', 'OP02');
                """
            )

        with closing(sqlite3.connect(self.dossier_db)) as conn:
            conn.executescript(
                """
                CREATE TABLE learning_dossier_images (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_code TEXT NOT NULL,
                    verification_state TEXT NOT NULL DEFAULT 'provisional'
                );
                INSERT INTO learning_dossier_images (card_code, verification_state) VALUES
                    ('OP01-001', 'verified');
                """
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_build_image_coverage_by_set(self) -> None:
        coverage = build_image_coverage_by_set(
            catalog_db_path=self.catalog_db,
            dossier_db_path=self.dossier_db,
        )

        by_set = {item["set_code"]: item for item in coverage}
        self.assertEqual(by_set["OP01"]["total_cards"], 2)
        self.assertEqual(by_set["OP01"]["images_tracked"], 1)
        self.assertEqual(by_set["OP01"]["images_verified"], 1)
        self.assertEqual(by_set["OP01"]["images_missing"], 1)
        self.assertEqual(by_set["OP01"]["coverage_percent"], 50.0)
        self.assertEqual(by_set["OP01"]["milestone_stage"], 2)
        self.assertEqual(by_set["OP01"]["milestone_label"], "tracked")

        self.assertEqual(by_set["OP02"]["total_cards"], 1)
        self.assertEqual(by_set["OP02"]["images_tracked"], 0)
        self.assertEqual(by_set["OP02"]["images_verified"], 0)
        self.assertEqual(by_set["OP02"]["images_missing"], 1)
        self.assertEqual(by_set["OP02"]["coverage_percent"], 0.0)
        self.assertEqual(by_set["OP02"]["milestone_stage"], 0)
        self.assertEqual(by_set["OP02"]["milestone_label"], "not_started")


if __name__ == "__main__":
    unittest.main()
