"""Bandai OP01 crawl reader.

Loads `data/bandai_op01_crawl.json` (from PRO-904) and exposes a lookup
by (canonical_code, print_id) that returns the fields Bandai provides.

The crawl stores `print_id` as `"base"` / `"_p1"` / `"_r1"` while the
catalog stores the full printing identifier like `"OP01-001"` /
`"OP01-001_p1"` / `"OP01-001_r1"`. We translate via the crawl row's
`full_id` field which matches the catalog's `print_id` exactly.

PRO-908 PR-B.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BandaiSource:
    """Read-only Bandai crawl lookup. Field map per (canonical_code, full_id)."""

    def __init__(self, crawl_path: Path) -> None:
        self.crawl_path = crawl_path
        self._index: dict[tuple[str, str], dict[str, Any]] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self.crawl_path.exists():
            # Empty index — verifier degrades gracefully (Bandai signal absent).
            self._loaded = True
            return
        payload = json.loads(self.crawl_path.read_text(encoding="utf-8"))
        for printing in payload.get("printings", []):
            canonical_code = printing.get("card_number", "")
            full_id = printing.get("full_id", "")
            if not canonical_code or not full_id:
                continue
            self._index[(canonical_code, full_id)] = {
                # Map Bandai crawl keys to catalog field names.
                "card_name": printing.get("name"),
                "rarity": printing.get("rarity"),
            }
        self._loaded = True

    def lookup(self, canonical_code: str, print_id: str) -> dict[str, Any]:
        """Return Bandai-provided fields for one (canonical_code, print_id).

        Empty dict if not found — verifier treats absent Bandai signal as
        "single-source mode" (catalog-only) for that field.
        """
        self._ensure_loaded()
        return self._index.get((canonical_code, print_id), {})

    def is_available(self) -> bool:
        """True if the crawl file is present and non-empty."""
        self._ensure_loaded()
        return bool(self._index)
