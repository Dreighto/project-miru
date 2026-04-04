from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

CARD_CODE_RE = re.compile(r"\b([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\b", re.I)
CARD_CODE_WITH_VARIANT_RE = re.compile(
    r"\b([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\(([^)]+)\)\b",
    re.I,
)
COLOR_RE = re.compile(r"\b(red|green|blue|purple|black|yellow)\b", re.I)
CARD_TYPE_RE = re.compile(r"\b(leader|character|event|stage|don!!)\b", re.I)
RARITY_RE = re.compile(
    r"\b(common|uncommon|rare|super rare|secret rare|special|sp|sec|sr|r|uc|c)\b",
    re.I,
)

SET_TYPE_BY_PREFIX = {
    "OP": "booster",
    "ST": "starter_deck",
    "EB": "extra_booster",
    "PRB": "premium_booster",
    "P": "promotion",
}

KNOWN_SET_PROFILES = {
    "P": {
        "name": "Promotion Cards",
        "type": "promotion",
        "aliases": ("promo", "promotion", "promotion cards"),
    },
    "PRB01": {
        "name": "Premium Booster - The Best",
        "type": "premium_booster",
        "aliases": ("the best", "premium booster the best", "premium booster - the best"),
    },
    "EB01": {
        "name": "Extra Booster - Memorial Collection",
        "type": "extra_booster",
        "aliases": ("memorial collection",),
    },
    "EB02": {
        "name": "Extra Booster - Anime 25th Collection",
        "type": "extra_booster",
        "aliases": ("anime 25th collection",),
    },
    "EB03": {
        "name": "Extra Booster - One Piece Heroines",
        "type": "extra_booster",
        "aliases": ("one piece heroines", "one piece heroines edition", "heroines edition"),
    },
    "OP01": {"name": "Romance Dawn", "type": "booster", "aliases": ("romance dawn",)},
    "OP02": {"name": "Paramount War", "type": "booster", "aliases": ("paramount war",)},
    "OP03": {
        "name": "Pillars of Strength",
        "type": "booster",
        "aliases": ("pillars of strength",),
    },
    "OP04": {
        "name": "Kingdoms of Intrigue",
        "type": "booster",
        "aliases": ("kingdoms of intrigue",),
    },
    "OP05": {
        "name": "Awakening of the New Era",
        "type": "booster",
        "aliases": ("awakening of the new era",),
    },
    "OP06": {
        "name": "Wings of the Captain",
        "type": "booster",
        "aliases": ("wings of the captain",),
    },
    "OP07": {
        "name": "500 Years in the Future",
        "type": "booster",
        "aliases": ("500 years in the future",),
    },
    "OP08": {"name": "Two Legends", "type": "booster", "aliases": ("two legends",)},
    "OP09": {
        "name": "Emperors in the New World",
        "type": "booster",
        "aliases": ("emperors in the new world",),
    },
    "OP10": {"name": "Royal Blood", "type": "booster", "aliases": ("royal blood",)},
    "OP11": {
        "name": "A Fist of Divine Speed",
        "type": "booster",
        "aliases": ("a fist of divine speed",),
    },
}

VARIANT_RULES = (
    ("illustration_box", re.compile(r"\billustration\s*box(?:\s*vol\.?\s*\d+)?\b", re.I)),
    ("alternate_art", re.compile(r"\b(alternate art|alt art|alt-art|parallel)\b", re.I)),
    ("manga", re.compile(r"\bmanga\b", re.I)),
    ("foil", re.compile(r"\b(foil|pirate foil|promo foil)\b", re.I)),
    ("serialized", re.compile(r"\bserial(?:ized)?\b", re.I)),
    ("judge", re.compile(r"\bjudge\b", re.I)),
    ("winner", re.compile(r"\bwinner\b", re.I)),
)

SET_NAME_STRIP_TERMS = tuple(
    sorted(
        {
            candidate
            for profile in KNOWN_SET_PROFILES.values()
            for candidate in (profile["name"], *profile.get("aliases", ()))
        }
        | {"extra booster", "starter deck", "premium booster", "promotion cards"},
        key=len,
        reverse=True,
    )
)
SET_NAME_STRIP_PATTERNS = tuple(re.compile(rf"\b{re.escape(term)}\b", re.I) for term in SET_NAME_STRIP_TERMS)


@dataclass(frozen=True)
class SetIntel:
    code: str
    name: str
    type: str
    aliases: tuple[str, ...] = ()
    confidence: str = "known"


@dataclass
class CardIntel:
    source_text: str
    normalized_text: str
    code: str | None
    set_code: str | None
    set_name: str | None
    set_type: str | None
    card_number: str | None
    canonical_name: str
    variants: list[str] = field(default_factory=list)
    variant_details: list[str] = field(default_factory=list)
    colors: list[str] = field(default_factory=list)
    card_types: list[str] = field(default_factory=list)
    rarity: str | None = None
    confidence: str = "low"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def normalize_variant_label(value: str) -> str:
    value = normalize_whitespace(value).lower()
    return value.replace(".", "")


def extract_card_code(text: str) -> str | None:
    match = CARD_CODE_RE.search(text or "")
    return match.group(1).upper() if match else None


def infer_set_code(card_code: str | None) -> str | None:
    if not card_code:
        return None
    code = card_code.upper()
    if code.startswith("P-"):
        return "P"
    if "-" not in code:
        return None
    return code.split("-", 1)[0]


def infer_set_type(set_code: str | None) -> str | None:
    if not set_code:
        return None
    profile = KNOWN_SET_PROFILES.get(set_code)
    if profile:
        return profile["type"]
    prefix = "P" if set_code == "P" else re.sub(r"\d+$", "", set_code)
    return SET_TYPE_BY_PREFIX.get(prefix)


def lookup_set(set_code: str | None) -> SetIntel | None:
    if not set_code:
        return None
    profile = KNOWN_SET_PROFILES.get(set_code)
    if profile:
        return SetIntel(
            code=set_code,
            name=profile["name"],
            type=profile["type"],
            aliases=tuple(profile.get("aliases", ())),
            confidence="known",
        )
    inferred_type = infer_set_type(set_code)
    if not inferred_type:
        return None
    return SetIntel(
        code=set_code,
        name=f"{set_code} ({inferred_type.replace('_', ' ')})",
        type=inferred_type,
        confidence="inferred",
    )


def _dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(value)
    return output


def detect_variants(text: str) -> tuple[list[str], list[str]]:
    labels: list[str] = []
    details: list[str] = []
    for match in CARD_CODE_WITH_VARIANT_RE.finditer(text or ""):
        detail = normalize_whitespace(match.group(2))
        if detail:
            details.append(detail)

    detail_text = " ".join(details)
    for label, pattern in VARIANT_RULES:
        if pattern.search(text or "") or pattern.search(detail_text):
            labels.append(label)

    for detail in details:
        detail_label = normalize_variant_label(detail)
        if "alt" in detail_label and "alternate_art" not in labels:
            labels.append("alternate_art")
        if "illustration" in detail_label and "illustration_box" not in labels:
            labels.append("illustration_box")
        if "foil" in detail_label and "foil" not in labels:
            labels.append("foil")
    return _dedupe_preserve_order(labels), _dedupe_preserve_order(details)


def clean_card_name(text: str, card_code: str | None = None, set_name: str | None = None) -> str:
    value = normalize_whitespace(text)
    if not value:
        return ""

    if card_code:
        value = re.sub(
            rf"^\s*(?:{re.escape(card_code)}(?:\([^)]+\))?\s+)+",
            "",
            value,
            flags=re.I,
        ).strip()

    value = re.sub(
        r"^\s*([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})(?:\([^)]+\))?\s+",
        "",
        value,
        flags=re.I,
    ).strip()

    if set_name:
        value = re.sub(rf"\b{re.escape(set_name)}\b", "", value, flags=re.I).strip()

    for pattern in SET_NAME_STRIP_PATTERNS:
        value = pattern.sub("", value).strip()

    value = re.sub(
        r"\s*\((alternate art|alt art|pirate foil|promo foil|parallel|illustration[^)]*)\)\s*",
        " ",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b\d{3}\s+alternate art\b", "", value, flags=re.I)
    return normalize_whitespace(value)


def detect_colors(text: str) -> list[str]:
    return _dedupe_preserve_order(match.group(1).lower() for match in COLOR_RE.finditer(text or ""))


def detect_card_types(text: str) -> list[str]:
    return _dedupe_preserve_order(match.group(1).lower() for match in CARD_TYPE_RE.finditer(text or ""))


def detect_rarity(text: str) -> str | None:
    match = RARITY_RE.search(text or "")
    if not match:
        return None
    return normalize_whitespace(match.group(1)).lower()


def analyze_card_text(text: str) -> CardIntel:
    normalized = normalize_whitespace(text)
    code = extract_card_code(normalized)
    set_code = infer_set_code(code)
    set_intel = lookup_set(set_code)
    card_number = code.split("-", 1)[1] if code and "-" in code else None
    variants, variant_details = detect_variants(normalized)
    colors = detect_colors(normalized)
    card_types = detect_card_types(normalized)
    rarity = detect_rarity(normalized)
    canonical_name = clean_card_name(normalized, code, set_intel.name if set_intel else None)

    confidence = "low"
    if code and set_intel and canonical_name:
        confidence = "high"
    elif code:
        confidence = "medium"

    return CardIntel(
        source_text=text or "",
        normalized_text=normalized,
        code=code,
        set_code=set_code,
        set_name=set_intel.name if set_intel else None,
        set_type=set_intel.type if set_intel else infer_set_type(set_code),
        card_number=card_number,
        canonical_name=canonical_name,
        variants=variants,
        variant_details=variant_details,
        colors=colors,
        card_types=card_types,
        rarity=rarity,
        confidence=confidence,
    )


def load_prices_records(prices_path: str | Path) -> list[dict[str, Any]]:
    path = Path(prices_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [value for value in data.values() if isinstance(value, dict)]
    if isinstance(data, list):
        return [value for value in data if isinstance(value, dict)]
    return []


def _detect_set_aliases(product_name: str) -> list[str]:
    normalized = normalize_whitespace(product_name).lower()
    aliases: list[str] = []
    for set_code, profile in KNOWN_SET_PROFILES.items():
        candidates = (profile["name"], *profile.get("aliases", ()))
        for candidate in candidates:
            if candidate.lower() in normalized:
                aliases.append(candidate)
                if set_code == "EB03" and "edition" in normalized:
                    aliases.append("One Piece Heroines Edition")
    return _dedupe_preserve_order(aliases)


def build_observed_catalog(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    cards: dict[str, dict[str, Any]] = {}
    sets: dict[str, dict[str, Any]] = {}

    for record in records:
        intel = analyze_card_text(str(record.get("name", "")))
        code = record.get("code") or intel.code
        if not code:
            continue

        card = cards.setdefault(
            code,
            {
                "code": code,
                "set_code": intel.set_code,
                "set_name": intel.set_name,
                "canonical_name": intel.canonical_name,
                "aliases": [],
                "variant_labels": [],
                "variant_details": [],
                "product_ids": [],
                "urls": [],
            },
        )

        alias = normalize_whitespace(str(record.get("name", "")))
        if alias:
            card["aliases"].append(alias)
        if intel.canonical_name:
            card["canonical_name"] = intel.canonical_name
        card["variant_labels"].extend(intel.variants)
        card["variant_details"].extend(intel.variant_details)

        product_id = record.get("product_id")
        if product_id is not None:
            card["product_ids"].append(product_id)

        url = record.get("url")
        if url:
            card["urls"].append(url)

        if intel.set_code:
            set_bucket = sets.setdefault(
                intel.set_code,
                {
                    "set_code": intel.set_code,
                    "set_name": intel.set_name,
                    "set_type": intel.set_type,
                    "card_codes": [],
                    "aliases": [],
                },
            )
            if intel.set_name:
                set_bucket["set_name"] = intel.set_name
            if intel.set_type:
                set_bucket["set_type"] = intel.set_type
            set_bucket["card_codes"].append(code)
            set_bucket["aliases"].extend(_detect_set_aliases(alias))

    for card in cards.values():
        card["aliases"] = _dedupe_preserve_order(card["aliases"])
        card["variant_labels"] = _dedupe_preserve_order(card["variant_labels"])
        card["variant_details"] = _dedupe_preserve_order(card["variant_details"])
        card["product_ids"] = sorted(set(card["product_ids"]))
        card["urls"] = _dedupe_preserve_order(card["urls"])

    for set_bucket in sets.values():
        set_bucket["card_codes"] = sorted(set(set_bucket["card_codes"]))
        set_bucket["aliases"] = _dedupe_preserve_order(
            alias for alias in set_bucket["aliases"] if alias
        )

    return {
        "summary": {
            "cards": len(cards),
            "sets": len(sets),
            "variant_labels": sorted(
                {label for card in cards.values() for label in card["variant_labels"]}
            ),
        },
        "cards": dict(sorted(cards.items())),
        "sets": dict(sorted(sets.items())),
    }


def summarize_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    cards = catalog.get("cards", {})
    variant_counter: dict[str, int] = defaultdict(int)
    for card in cards.values():
        for label in card.get("variant_labels", []):
            variant_counter[label] += 1
    return {
        "cards": len(cards),
        "sets": len(catalog.get("sets", {})),
        "variants": dict(sorted(variant_counter.items())),
    }


def evaluate_cases(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    passed = 0
    total = 0
    for case in cases:
        total += 1
        intel = analyze_card_text(str(case.get("input", ""))).to_dict()
        expectations = case.get("expect", {})
        failures: list[str] = []
        for field, expected in expectations.items():
            actual = intel.get(field)
            if isinstance(expected, list):
                missing = [item for item in expected if item not in (actual or [])]
                if missing:
                    failures.append(f"{field}: missing {missing}, actual={actual}")
            elif actual != expected:
                failures.append(f"{field}: expected {expected!r}, actual={actual!r}")

        if not failures:
            passed += 1
        results.append(
            {
                "name": case.get("name", case.get("input", "")),
                "passed": not failures,
                "failures": failures,
                "intel": intel,
            }
        )

    return {
        "summary": {
            "passed": passed,
            "failed": total - passed,
            "total": total,
        },
        "results": results,
    }
