from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.miru_source_adapters import (
    OfficialCardImageSourceAdapter,
    OfficialCardListSourceAdapter,
)
from tools.miru_source_registry import (
    build_source_registry,
    build_unknown_source_entry,
    get_source_entry,
    load_approved_sources_from_config,
)


class MiruSourceRegistryTests(unittest.TestCase):
    def test_registry_exposes_enabled_official_source(self) -> None:
        registry = build_source_registry()
        official = get_source_entry("official-cardlist", registry)

        self.assertTrue(official.enabled)
        self.assertEqual(official.trust_tier, 1)
        self.assertEqual(official.trust_label, "official")
        self.assertEqual(official.fetch_mode, "snapshot-json")
        self.assertEqual(official.source_type, "official-cardlist-snapshot")
        self.assertEqual(official.review_state, "active")
        self.assertGreater(official.default_confidence, 0.9)
        self.assertIn("card_code", official.supported_fields)
        self.assertIn("fetched_at", official.supported_fields)

    def test_official_adapter_normalizes_fixture_snapshot(self) -> None:
        fixture_path = Path("tests/fixtures/miru_official_cardlist_sample.json")
        registry = build_source_registry()
        official = get_source_entry("official-cardlist", registry)
        adapter = OfficialCardListSourceAdapter()

        records = adapter.fetch_records(
            source_entry=official,
            card_code="OP01-001",
            snapshot_path=fixture_path,
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.card_code, "OP01-001")
        self.assertEqual(record.card_name, "Monkey D. Luffy")
        self.assertEqual(record.set_code, "OP01")
        self.assertEqual(record.set_name, "Romance Dawn")
        self.assertEqual(record.rarity, "L")
        self.assertEqual(record.color, "Red")
        self.assertEqual(record.card_type, "Leader")
        self.assertEqual(record.power, "5000")
        self.assertEqual(record.life, "5")
        self.assertEqual(record.attribute, "Strike")
        self.assertEqual(record.traits, ["Supernovas", "Straw Hat Crew"])
        self.assertEqual(record.effect_text, "[DON!! x1] This Leader gains +1000 power.")
        self.assertEqual(record.source_id, "official-cardlist")
        self.assertEqual(record.source_reference, "official-op01-001")
        self.assertIn("asia-en.onepiece-cardgame.com", record.source_url)

    def test_registry_exposes_enabled_official_image_source(self) -> None:
        registry = build_source_registry()
        official = get_source_entry("official-card-images", registry)

        self.assertTrue(official.enabled)
        self.assertEqual(official.trust_tier, 1)
        self.assertEqual(official.trust_label, "official")
        self.assertEqual(official.fetch_mode, "snapshot-json")
        self.assertEqual(official.source_type, "official-card-image-snapshot")
        self.assertIn("card_code", official.supported_fields)
        self.assertIn("filename", official.supported_fields)

    def test_unknown_source_defaults_to_manual_review_profile(self) -> None:
        entry = build_unknown_source_entry("mystery-source")

        self.assertFalse(entry.enabled)
        self.assertEqual(entry.trust_tier, 4)
        self.assertEqual(entry.trust_label, "experimental/manual review only")
        self.assertEqual(entry.review_state, "manual-review-only")
        self.assertLess(entry.default_confidence, 0.5)

    def test_image_adapter_normalizes_payload(self) -> None:
        registry = build_source_registry()
        official = get_source_entry("official-card-images", registry)
        adapter = OfficialCardImageSourceAdapter()

        payload = {
            "source": {"base_url": "https://example.invalid/images/"},
            "images": [
                {
                    "card_code": "OP01-001",
                    "set_code": "OP01",
                    "variant_key": "alt",
                    "image_url": "https://example.invalid/images/OP01-001_alt.png",
                    "source_reference": "img-op01-001-alt",
                    "width": 200,
                    "height": 280,
                }
            ],
        }
        records = adapter.fetch_records(
            source_entry=official,
            card_code="OP01-001",
            payload=payload,
        )

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.card_code, "OP01-001")
        self.assertEqual(record.variant_key, "alt")
        self.assertEqual(record.source_id, "official-card-images")
        self.assertEqual(record.source_reference, "img-op01-001-alt")
        self.assertEqual(record.width, 200)
        self.assertEqual(record.height, 280)

    def test_approved_sources_missing_config_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonexistent.json"
            entries, errors = load_approved_sources_from_config(path)
        self.assertEqual(entries, [])
        self.assertEqual(errors, [])

    def test_approved_sources_valid_entry_merged_into_registry(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(
                {
                    "approved_sources": [
                        {
                            "source_id": "test-approved",
                            "source_name": "Test Approved",
                            "trust_tier": 4,
                            "enabled": True,
                        }
                    ]
                },
                f,
            )
            path = Path(f.name)
        try:
            registry = build_source_registry(approved_sources_path=path)
            self.assertIn("test-approved", registry)
            entry = get_source_entry("test-approved", registry)
            self.assertEqual(entry.source_name, "Test Approved")
            self.assertEqual(entry.trust_tier, 4)
            self.assertTrue(entry.enabled)
            self.assertIn("official-cardlist", registry)
        finally:
            path.unlink(missing_ok=True)

    def test_approved_sources_malformed_json_fails_safely(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json ")
            path = Path(f.name)
        try:
            entries, errors = load_approved_sources_from_config(path)
            self.assertEqual(entries, [])
            self.assertEqual(len(errors), 1)
            self.assertIn("Invalid JSON", errors[0])
        finally:
            path.unlink(missing_ok=True)

    def test_approved_sources_invalid_entry_skipped_with_error(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"approved_sources": [{"source_id": ""}, {"source_id": "valid-one"}]}, f)
            path = Path(f.name)
        try:
            entries, errors = load_approved_sources_from_config(path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].source_id, "valid-one")
            self.assertEqual(len(errors), 1)
            self.assertIn("missing or empty 'source_id'", errors[0])
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
