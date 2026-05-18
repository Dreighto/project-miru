"""Tests for the Bandai crawl reader."""

from __future__ import annotations

import json
from pathlib import Path

from services.shadow_loop.bandai_source import BandaiSource


def _write_crawl(path: Path, printings: list[dict]) -> None:
    payload = {
        "schema_version": 1,
        "ticket": "test",
        "source": "test",
        "queried_at": "2026-05-17T00:00:00Z",
        "card_numbers_queried": [p["card_number"] for p in printings],
        "printings": printings,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_lookup_returns_mapped_fields(tmp_path: Path):
    crawl = tmp_path / "bandai_op01_crawl.json"
    _write_crawl(
        crawl,
        [
            {
                "card_number": "OP01-001",
                "print_id": "base",
                "full_id": "OP01-001",
                "name": "Roronoa Zoro",
                "rarity": "L",
                "card_set": "-ROMANCE DAWN- [OP01]",
                "image_url": "https://example.test/OP01-001.png",
            }
        ],
    )
    source = BandaiSource(crawl)
    fields = source.lookup("OP01-001", "OP01-001")
    assert fields == {"card_name": "Roronoa Zoro", "rarity": "L"}


def test_lookup_returns_empty_for_missing_card(tmp_path: Path):
    crawl = tmp_path / "bandai_op01_crawl.json"
    _write_crawl(crawl, [])
    source = BandaiSource(crawl)
    assert source.lookup("OP01-999", "OP01-999") == {}


def test_lookup_distinguishes_print_ids(tmp_path: Path):
    crawl = tmp_path / "bandai_op01_crawl.json"
    _write_crawl(
        crawl,
        [
            {
                "card_number": "OP01-001",
                "print_id": "base",
                "full_id": "OP01-001",
                "name": "Roronoa Zoro",
                "rarity": "L",
                "card_set": "OP01",
                "image_url": "",
            },
            {
                "card_number": "OP01-001",
                "print_id": "_p1",
                "full_id": "OP01-001_p1",
                "name": "Roronoa Zoro",
                "rarity": "SP",  # different rarity for parallel printing
                "card_set": "OP01",
                "image_url": "",
            },
        ],
    )
    source = BandaiSource(crawl)
    assert source.lookup("OP01-001", "OP01-001")["rarity"] == "L"
    assert source.lookup("OP01-001", "OP01-001_p1")["rarity"] == "SP"


def test_absent_crawl_file_degrades_to_empty(tmp_path: Path):
    """Bandai source should degrade gracefully if the crawl file is missing."""
    source = BandaiSource(tmp_path / "does_not_exist.json")
    assert source.lookup("OP01-001", "OP01-001") == {}
    assert source.is_available() is False
