from __future__ import annotations

import json
import unittest
from pathlib import Path

from shared.intel.card_intel import (
    analyze_card_text,
    build_observed_catalog,
    evaluate_cases,
    load_prices_records,
    summarize_catalog,
)


class MiruCardIntelTests(unittest.TestCase):
    def test_analyze_promo_illustration_card(self) -> None:
        intel = analyze_card_text("P-093 P-093(IllustrationBoxVol.6) Trafalgar Law")
        self.assertEqual(intel.code, "P-093")
        self.assertEqual(intel.set_code, "P")
        self.assertEqual(intel.set_name, "Promotion Cards")
        self.assertEqual(intel.canonical_name, "Trafalgar Law")
        self.assertIn("illustration_box", intel.variants)

    def test_analyze_booster_alt_art_card(self) -> None:
        intel = analyze_card_text(
            "OP11-067 A Fist Of Divine Speed Charlotte Katakuri 067 Alternate Art"
        )
        self.assertEqual(intel.set_code, "OP11")
        self.assertEqual(intel.set_name, "A Fist of Divine Speed")
        self.assertEqual(intel.canonical_name, "Charlotte Katakuri")
        self.assertIn("alternate_art", intel.variants)

    def test_infers_set_type_colors_and_card_type(self) -> None:
        intel = analyze_card_text("ST10-001 Red Purple Trafalgar Law Leader")
        self.assertEqual(intel.set_type, "starter_deck")
        self.assertEqual(intel.colors, ["red", "purple"])
        self.assertEqual(intel.card_types, ["leader"])

    def test_builds_observed_catalog_from_prices(self) -> None:
        records = load_prices_records("data/prices.json")
        catalog = build_observed_catalog(records)
        summary = summarize_catalog(catalog)

        self.assertEqual(summary["cards"], 5)
        self.assertEqual(summary["sets"], 3)
        self.assertIn("alternate_art", summary["variants"])
        self.assertEqual(
            catalog["cards"]["EB03-062"]["canonical_name"],
            "Trafalgar Law",
        )
        self.assertIn(
            "One Piece Heroines Edition",
            catalog["sets"]["EB03"]["aliases"],
        )

    def test_eval_fixture_passes(self) -> None:
        cases = json.loads(
            Path("tests/fixtures/miru_onepiece_eval_cases.json").read_text(encoding="utf-8")
        )
        result = evaluate_cases(cases)
        self.assertEqual(result["summary"]["failed"], 0)


if __name__ == "__main__":
    unittest.main()
