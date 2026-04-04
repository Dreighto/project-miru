from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pm.app as dashboard_app
from tools.miru_project_sync import MiruProjectDbSync
from tools.miru_source_adapters import NormalizedSourceRecord


class DashboardLibraryPageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="dashboard-library-page-")
        root = Path(self.temp_dir.name)
        self.prices_path = root / "prices.json"
        self.catalog_db = root / "card_catalog.db"
        self.images_root = root / "images"
        (self.images_root / "cards").mkdir(parents=True, exist_ok=True)
        (self.images_root / "cards" / "op01-001.png").write_bytes(b"fake-image")
        self.prices_path.write_text(
            json.dumps(
                {
                    "OP01-001": {
                        "name": "OP01-001 Monkey D. Luffy",
                        "code": "OP01-001",
                        "price": 12.5,
                        "target": 15.0,
                        "last_checked_ts": 1710000000,
                        "url": "https://example.invalid/op01-001",
                    }
                }
            ),
            encoding="utf-8",
        )
        sync = MiruProjectDbSync(project_db_path=self.catalog_db)
        sync.queue_validated_record(
            NormalizedSourceRecord(
                card_code="OP01-001",
                card_name="Monkey D. Luffy",
                set_code="OP01",
                set_name="Romance Dawn",
                rarity="L",
                color="Red",
                card_type="Leader",
                cost="5",
                power="5000",
                counter="",
                attribute="Strike",
                traits=["Supernovas", "Straw Hat Crew"],
                life="5",
                effect_text="[DON!! x1] This Leader gains +1000 power.",
                trigger_text="",
                source_id="official-cardlist",
                source_url="https://asia-en.onepiece-cardgame.com/cardlist/",
                source_reference="official-op01-001",
                fetched_at="2026-03-10 12:00:00",
            )
        )
        conn = sqlite3.connect(self.catalog_db)
        try:
            card_id = conn.execute(
                "SELECT id FROM cards WHERE canonical_code = ?",
                ("OP01-001",),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO card_variants (
                    card_id,
                    variant_key,
                    variant_label,
                    print_id,
                    release_set_code,
                    release_set_name,
                    image_path,
                    image_url,
                    source,
                    is_base
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card_id,
                    "",
                    "Base",
                    "OP01-001-base",
                    "OP01",
                    "Romance Dawn",
                    "cards/op01-001.png",
                    "",
                    "official-cardlist",
                    1,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_library_page_uses_card_name_title_and_set_name_subtitle(self) -> None:
        with patch.object(dashboard_app, "PRICES_PATH", str(self.prices_path)), patch.object(
            dashboard_app, "CATALOG_DB_PATH", str(self.catalog_db)
        ), patch.object(
            dashboard_app, "IMAGES_ROOT", str(self.images_root)
        ):
            client = dashboard_app.app.test_client()
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('<div class="title">Monkey D. Luffy</div>', html)
        self.assertIn('<div class="subtitle">Romance Dawn</div>', html)
        self.assertIn('<span class="code">OP01-001</span>', html)
        self.assertIn('class="viewbtn"', html)
        self.assertIn('id="cardDetailModal"', html)
        self.assertIn("/img/cards/op01-001.png", html)
        self.assertIn("Effect Text", html)
        self.assertIn("[DON!! x1] This Leader gains +1000 power.", html)
        self.assertIn("No trigger text recorded.", html)


if __name__ == "__main__":
    unittest.main()
