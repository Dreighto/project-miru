#!/usr/bin/env python
"""Structured print-variant / alt-art classification for Project Miru.

Minimal foundation: classify variant_key or label/filename into stable types,
store per (card_code, variant_key) in worktree-local JSON. Same card_code = same
canonical card identity; variant_key = distinct print/art variant.

Use: tools.miru_print_variant.classify_print_variant(), get/set_variant_classification().

Wired: (1) Learning engine store_image_record() classifies and stores on ingest.
(2) Dashboard build_image_index() uses get_classification_or_infer() for alt/illust
slots so library double-tap and retrieval use the same classification.

Investigation note (alt-art/double-tap): The library's alt-art switching uses
IMAGE_INDEX built from filenames and variant_is_altish()/variant_is_illustrationish()
with hardcoded substring checks. There was no stored variant_type or canonical
(card_code, variant_key) -> classification. Missing variant association/classification
is plausibly connected to alt-art or double-tap issues: a single source of truth
for variant type (this module + storage) lets index build and API align.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VARIANTS_PATH = PROJECT_ROOT / "data" / "miru_print_variants.json"

# Stable classification categories (canonical names for downstream use)
PRINT_VARIANT_BASE = "base"
PRINT_VARIANT_ALT_ART = "alt_art"
PRINT_VARIANT_SPECIAL_ART = "special_art"
PRINT_VARIANT_ILLUSTRATOR_ART = "illustrator_art"
PRINT_VARIANT_SP = "sp"
PRINT_VARIANT_ANNIVERSARY = "anniversary"
PRINT_VARIANT_SERIALIZED = "serialized"
PRINT_VARIANT_PROMO_VARIANT = "promo_variant"
PRINT_VARIANT_PARALLEL = "parallel"
PRINT_VARIANT_OTHER_VARIANT = "other_variant"
PRINT_VARIANT_UNKNOWN = "unknown"

PRINT_VARIANT_TYPES = (
    PRINT_VARIANT_BASE,
    PRINT_VARIANT_ALT_ART,
    PRINT_VARIANT_SPECIAL_ART,
    PRINT_VARIANT_ILLUSTRATOR_ART,
    PRINT_VARIANT_SP,
    PRINT_VARIANT_ANNIVERSARY,
    PRINT_VARIANT_SERIALIZED,
    PRINT_VARIANT_PROMO_VARIANT,
    PRINT_VARIANT_PARALLEL,
    PRINT_VARIANT_OTHER_VARIANT,
    PRINT_VARIANT_UNKNOWN,
)

# Signals from existing code (dashboard ALT_MARKERS / ILLUST_MARKERS style)
_ALT_MARKERS = (
    "alternate art", "alt art", "alt-art", "alternate-art",
    "parallel", "manga", "special art", "special",
    "pirate foil", "promo foil", "foil",
)
_ILLUST_MARKERS = (
    "illustration", "illustration box", "illustrationbox",
    "illustrationboxvol", "illustrationboxvol.",
    "illustration box vol", "illustration box vol.",
)
_SP_ANNIVERSARY_SERIALIZED = ("sp", "anniversary", "serialized", "serialised", "promo", "promo_variant")


def _normalize_for_match(text: str) -> str:
    return (text or "").strip().lower().replace("-", " ").replace("_", " ")


def classify_print_variant(variant_key: str, label_or_filename: str = "") -> str:
    """Classify a print/art variant from variant_key and optional label or filename.

    Uses existing naming/marker conventions. Returns one of PRINT_VARIANT_*.
    When uncertain, returns alt_art/other_variant when justified, else unknown.
    """
    key = _normalize_for_match(variant_key)
    label = _normalize_for_match(label_or_filename)
    combined = f"{key} {label}"

    if not key or key in ("base", "default", "standard", "normal"):
        return PRINT_VARIANT_BASE

    if key == "alt" or combined.startswith("alt ") or " alt " in combined or combined.endswith(" alt"):
        return PRINT_VARIANT_ALT_ART
    if any(m in combined for m in _ILLUST_MARKERS):
        return PRINT_VARIANT_ILLUSTRATOR_ART
    if any(m in combined for m in _ALT_MARKERS):
        if "parallel" in combined or "manga" in combined:
            return PRINT_VARIANT_PARALLEL
        if "special" in combined:
            return PRINT_VARIANT_SPECIAL_ART
        if "promo" in combined or "foil" in combined:
            return PRINT_VARIANT_PROMO_VARIANT
        return PRINT_VARIANT_ALT_ART
    if any(m in combined for m in _SP_ANNIVERSARY_SERIALIZED):
        if "serialized" in combined or "serialised" in combined:
            return PRINT_VARIANT_SERIALIZED
        if "anniversary" in combined:
            return PRINT_VARIANT_ANNIVERSARY
        if key == "sp" or " sp " in combined or combined.startswith("sp "):
            return PRINT_VARIANT_SP
        if "promo" in combined:
            return PRINT_VARIANT_PROMO_VARIANT
        return PRINT_VARIANT_OTHER_VARIANT

    # variant_key present but no known signal -> other_variant or unknown
    if key:
        return PRINT_VARIANT_OTHER_VARIANT
    return PRINT_VARIANT_UNKNOWN


def _storage_key(card_code: str, variant_key: str) -> str:
    c = (card_code or "").strip().upper()
    v = (variant_key or "").strip().lower() or ""
    return f"{c}|{v}"


def _load_variants(path: Path | None = None) -> dict[str, str]:
    p = path or DEFAULT_VARIANTS_PATH
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return dict(data) if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_variants(data: dict[str, str], path: Path | None = None) -> None:
    p = path or DEFAULT_VARIANTS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_variant_classification(
    card_code: str,
    variant_key: str,
    path: Path | None = None,
) -> str | None:
    """Return stored variant_type for (card_code, variant_key), or None if not stored."""
    store = _load_variants(path)
    return store.get(_storage_key(card_code, variant_key))


def set_variant_classification(
    card_code: str,
    variant_key: str,
    variant_type: str,
    path: Path | None = None,
) -> None:
    """Store variant_type for (card_code, variant_key). variant_type must be in PRINT_VARIANT_TYPES."""
    if variant_type not in PRINT_VARIANT_TYPES:
        variant_type = PRINT_VARIANT_OTHER_VARIANT
    store = _load_variants(path)
    store[_storage_key(card_code, variant_key)] = variant_type
    _save_variants(store, path)


def get_classification_or_infer(
    card_code: str,
    variant_key: str,
    label_or_filename: str = "",
    path: Path | None = None,
) -> str:
    """Return stored classification if present, else infer via classify_print_variant."""
    stored = get_variant_classification(card_code, variant_key, path)
    if stored is not None:
        return stored
    return classify_print_variant(variant_key, label_or_filename)


def list_variants_for_card(card_code: str, path: Path | None = None) -> list[dict[str, Any]]:
    """Return list of {variant_key, variant_type} for the given card_code from storage."""
    store = _load_variants(path)
    prefix = (card_code or "").strip().upper() + "|"
    out = []
    for k, v in store.items():
        if k.startswith(prefix):
            vkey = k[len(prefix):]
            out.append({"variant_key": vkey, "variant_type": v})
    return sorted(out, key=lambda x: (x["variant_key"], x["variant_type"]))
