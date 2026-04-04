from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import Protocol

from .models import (
    SourceCardRecord,
    SourceCardRelationship,
    SourceCardVariant,
)
from .trust import get_source_profile


def _tupleify_texts(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value or "").strip()
    if not text:
        return ()
    if "/" in text:
        return tuple(part.strip() for part in text.split("/") if part.strip())
    return (text,)


def _normalize_observed_at(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    if "T" not in text:
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class CardSourceAdapter(Protocol):
    adapter_key: str

    def fetch_card_records(self, canonical_code: str) -> list[SourceCardRecord]:
        ...


class StaticJsonAdapter:
    def __init__(self, payload: dict, *, adapter_key: str = "static-json"):
        self.payload = payload or {}
        self.adapter_key = adapter_key

    @classmethod
    def from_path(cls, path: str | Path, *, adapter_key: str = "static-json") -> "StaticJsonAdapter":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload, adapter_key=adapter_key)

    def fetch_card_records(self, canonical_code: str) -> list[SourceCardRecord]:
        cards = (self.payload.get("cards") or {}).get(canonical_code, [])
        return [_record_from_mapping(item, canonical_code) for item in cards if isinstance(item, dict)]


class OfficialCardListSnapshotAdapter:
    def __init__(self, payload: dict, *, adapter_key: str = "official-cardlist-snapshot"):
        self.payload = payload or {}
        self.adapter_key = adapter_key

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        adapter_key: str = "official-cardlist-snapshot",
    ) -> "OfficialCardListSnapshotAdapter":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload, adapter_key=adapter_key)

    def fetch_card_records(self, canonical_code: str) -> list[SourceCardRecord]:
        meta = self.payload.get("source") or {}
        cards = self.payload.get("cards") or []
        results: list[SourceCardRecord] = []
        for item in cards:
            if not isinstance(item, dict):
                continue
            if str(item.get("card_code") or "").strip().upper() != canonical_code:
                continue
            variants = tuple(
                SourceCardVariant(
                    variant_key=str(variant.get("variant_key") or "").strip(),
                    variant_family=str(variant.get("variant_family") or "").strip(),
                    variant_label=str(variant.get("variant_label") or "").strip(),
                    image_identity=str(variant.get("image_url") or variant.get("image_identity") or "").strip(),
                    official_text=str(variant.get("official_text") or "").strip(),
                    notes=str(variant.get("notes") or "").strip(),
                )
                for variant in item.get("variants") or []
                if isinstance(variant, dict)
            )
            source_key = str(meta.get("source_key") or item.get("source_key") or "official-cardlist").strip().lower()
            profile = get_source_profile(source_key)
            source_url = str(item.get("source_url") or meta.get("base_url") or profile.base_url).strip()
            source_card_ref = str(item.get("official_card_id") or item.get("card_code") or canonical_code).strip()
            observed_at = str(
                item.get("last_checked_at")
                or meta.get("snapshot_taken_at")
                or time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
            ).strip()
            results.append(
                SourceCardRecord(
                    source_key=profile.source_key,
                    source_url=source_url,
                    source_title=str(meta.get("source_title") or item.get("source_title") or profile.display_name).strip(),
                    source_card_ref=source_card_ref,
                    observed_at=observed_at,
                    canonical_code=canonical_code,
                    card_name=str(item.get("card_name") or "").strip(),
                    set_code=str(item.get("set_code") or "").strip().upper(),
                    set_name=str(item.get("set_name") or "").strip(),
                    rarity=str(item.get("rarity") or "").strip(),
                    color=str(item.get("color") or "").strip(),
                    card_type=str(item.get("card_type") or "").strip(),
                    cost=str(item.get("cost") or "").strip(),
                    power=str(item.get("power") or "").strip(),
                    counter=str(item.get("counter") or "").strip(),
                    attribute=str(item.get("attribute") or "").strip(),
                    traits=_tupleify_texts(item.get("traits")),
                    life=str(item.get("life") or "").strip(),
                    block_number=str(item.get("block_number") or "").strip(),
                    ban_status=str(item.get("ban_status") or "").strip(),
                    restriction_count=str(item.get("restriction_count") or "").strip(),
                    effect_text=str(item.get("effect_text") or item.get("official_text") or "").strip(),
                    trigger_text=str(item.get("trigger_text") or "").strip(),
                    subtypes=_tupleify_texts(item.get("subtypes") or item.get("subtype")),
                    availability=str(item.get("availability") or "").strip(),
                    status=str(item.get("status") or "").strip(),
                    series_name=str(item.get("series_name") or "").strip(),
                    product_name=str(item.get("product_name") or "").strip(),
                    variant_family=str(item.get("variant_family") or "").strip(),
                    official_text=str(item.get("official_text") or "").strip(),
                    image_identity=str(item.get("image_url") or item.get("image_identity") or "").strip(),
                    variants=variants,
                    relationships=tuple(),
                    gameplay_context={},
                    market_context={},
                    extra={
                        "official_card_id": source_card_ref,
                        "adapter_key": self.adapter_key,
                        "source_format": str(meta.get("format") or "official-cardlist-snapshot"),
                    },
                )
            )
        return results


class MiruKnowledgeCacheAdapter:
    def __init__(self, payload: dict, *, adapter_key: str = "miru-knowledge-cache"):
        self.payload = payload or {}
        self.adapter_key = adapter_key
        self.cards = self.payload.get("cards") or {}
        self.meta = self.payload.get("_meta") or {}
        self.source_priority = tuple(
            str(item).strip().lower()
            for item in self.meta.get("source_priority") or []
            if str(item).strip()
        )

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        adapter_key: str = "miru-knowledge-cache",
    ) -> "MiruKnowledgeCacheAdapter":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload, adapter_key=adapter_key)

    def list_card_codes(self) -> list[str]:
        return sorted(
            str(code).strip().upper()
            for code in self.cards.keys()
            if str(code).strip()
        )

    def fetch_card_records(self, canonical_code: str) -> list[SourceCardRecord]:
        card = self.cards.get(canonical_code) or self.cards.get(str(canonical_code or "").strip().upper())
        if not isinstance(card, dict):
            return []

        field_sources = {
            str(field_name).strip(): str(source_key).strip().lower()
            for field_name, source_key in (card.get("field_sources") or {}).items()
            if str(field_name).strip() and str(source_key).strip()
        }
        source_keys = {
            str(source_key).strip().lower()
            for source_key in card.get("sources") or []
            if str(source_key).strip()
        }
        source_keys.update(field_sources.values())
        for print_info in card.get("prints") or []:
            if not isinstance(print_info, dict):
                continue
            source_key = str(print_info.get("source") or "").strip().lower()
            if source_key:
                source_keys.add(source_key)

        observed_at = _normalize_observed_at(self.meta.get("generated_at"))
        ordered_sources = sorted(source_keys, key=self._source_sort_key)
        records: list[SourceCardRecord] = []
        for source_key in ordered_sources:
            record = self._build_record(
                card=card,
                canonical_code=str(canonical_code or "").strip().upper(),
                source_key=source_key,
                field_sources=field_sources,
                observed_at=observed_at,
            )
            if record is not None:
                records.append(record)
        return records

    def _source_sort_key(self, source_key: str) -> tuple[int, str]:
        if source_key in self.source_priority:
            return (self.source_priority.index(source_key), source_key)
        return (len(self.source_priority), source_key)

    def _build_record(
        self,
        *,
        card: dict,
        canonical_code: str,
        source_key: str,
        field_sources: dict[str, str],
        observed_at: str,
    ) -> SourceCardRecord | None:
        profile = get_source_profile(source_key)
        variants = tuple(self._variants_for_source(card, source_key))

        def sourced_text(field_name: str) -> str:
            if field_sources.get(field_name) != source_key:
                return ""
            return str(card.get(field_name) or "").strip()

        card_name = sourced_text("card_name")
        set_code = sourced_text("set_code")
        set_name = sourced_text("set_name")
        rarity = sourced_text("rarity")
        color = sourced_text("color")
        card_type = sourced_text("card_type")
        cost = sourced_text("cost")
        power = sourced_text("power")
        counter = sourced_text("counter")
        attribute = sourced_text("attribute")
        life = sourced_text("life")
        effect_text = sourced_text("effect_text")
        trigger_text = sourced_text("trigger_text")
        official_text = effect_text
        image_identity = self._image_identity_for_source(card, source_key)

        traits: tuple[str, ...] = ()
        if field_sources.get("traits") == source_key:
            traits = _tupleify_texts(card.get("traits"))

        has_fields = any(
            (
                card_name,
                set_code,
                set_name,
                rarity,
                color,
                card_type,
                cost,
                power,
                counter,
                attribute,
                life,
                effect_text,
                trigger_text,
                official_text,
                image_identity,
            )
        ) or bool(traits)
        if not has_fields and not variants:
            return None

        return SourceCardRecord(
            source_key=profile.source_key,
            source_url=self._source_url(card, source_key) or profile.base_url,
            source_title=profile.display_name,
            source_card_ref=canonical_code,
            observed_at=observed_at,
            canonical_code=canonical_code,
            card_name=card_name,
            set_code=set_code,
            set_name=set_name,
            rarity=rarity,
            color=color,
            card_type=card_type,
            cost=cost,
            power=power,
            counter=counter,
            attribute=attribute,
            traits=traits,
            life=life,
            effect_text=effect_text,
            trigger_text=trigger_text,
            official_text=official_text,
            image_identity=image_identity,
            variants=variants,
            extra={
                "adapter_key": self.adapter_key,
                "cache_generated_at": observed_at,
            },
        )

    def _variants_for_source(self, card: dict, source_key: str) -> list[SourceCardVariant]:
        variants: list[SourceCardVariant] = []
        seen: set[str] = set()
        for print_info in card.get("prints") or []:
            if not isinstance(print_info, dict):
                continue
            if str(print_info.get("source") or "").strip().lower() != source_key:
                continue
            variant_key = str(print_info.get("variant_key") or print_info.get("print_id") or "").strip()
            if not variant_key or variant_key in seen:
                continue
            seen.add(variant_key)
            signals = _tupleify_texts(print_info.get("signals"))
            variant_family = str(print_info.get("variant_family") or "").strip()
            if not variant_family and signals:
                variant_family = signals[0]
            variants.append(
                SourceCardVariant(
                    variant_key=variant_key,
                    variant_family=variant_family or ("base" if variant_key == "base" else ""),
                    variant_label=str(print_info.get("variant_label") or variant_key).strip(),
                    image_identity=str(print_info.get("image_url") or print_info.get("image_path") or "").strip(),
                    notes=str(print_info.get("release_set_name") or "").strip(),
                )
            )
        return variants

    def _image_identity_for_source(self, card: dict, source_key: str) -> str:
        if source_key != "official-cardlist":
            return ""
        for print_info in card.get("prints") or []:
            if not isinstance(print_info, dict):
                continue
            if str(print_info.get("source") or "").strip().lower() != source_key:
                continue
            image_identity = str(print_info.get("image_url") or print_info.get("image_identity") or "").strip()
            if image_identity:
                return image_identity
        return ""

    def _source_url(self, card: dict, source_key: str) -> str:
        for print_info in card.get("prints") or []:
            if not isinstance(print_info, dict):
                continue
            if str(print_info.get("source") or "").strip().lower() != source_key:
                continue
            value = str(print_info.get("image_url") or "").strip()
            if value:
                return value
        return ""


class PlaceholderAdapter:
    def __init__(self, *, adapter_key: str = "placeholder"):
        self.adapter_key = adapter_key

    def fetch_card_records(self, canonical_code: str) -> list[SourceCardRecord]:
        return [
            SourceCardRecord(
                source_key="placeholder",
                source_url="",
                source_title="Placeholder Adapter",
                source_card_ref=canonical_code,
                observed_at=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                canonical_code=canonical_code,
                gameplay_context={"status": "placeholder"},
                market_context={"status": "placeholder"},
                extra={
                    "placeholder_reason": "No real source adapter configured for this card yet.",
                    "adapter_key": self.adapter_key,
                },
            )
        ]


def _record_from_mapping(item: dict, canonical_code: str) -> SourceCardRecord:
    observed_at = str(item.get("observed_at") or time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()))
    variants = tuple(
        SourceCardVariant(
            variant_key=str(variant.get("variant_key") or "").strip(),
            variant_family=str(variant.get("variant_family") or "").strip(),
            variant_label=str(variant.get("variant_label") or "").strip(),
            image_identity=str(variant.get("image_identity") or "").strip(),
            official_text=str(variant.get("official_text") or "").strip(),
            notes=str(variant.get("notes") or "").strip(),
        )
        for variant in item.get("variants") or []
        if isinstance(variant, dict)
    )
    relationships = tuple(
        SourceCardRelationship(
            relationship_type=str(rel.get("relationship_type") or "").strip(),
            related_card_code=str(rel.get("related_card_code") or "").strip().upper(),
            related_variant_key=str(rel.get("related_variant_key") or "").strip(),
            related_label=str(rel.get("related_label") or "").strip(),
            notes=str(rel.get("notes") or "").strip(),
        )
        for rel in item.get("relationships") or []
        if isinstance(rel, dict)
    )
    source_key = str(item.get("source_key") or "sample-fixture").strip().lower()
    source_profile = get_source_profile(source_key)
    return SourceCardRecord(
        source_key=source_profile.source_key,
        source_url=str(item.get("source_url") or "").strip(),
        source_title=str(item.get("source_title") or source_profile.display_name).strip(),
        source_card_ref=str(item.get("source_card_ref") or canonical_code).strip(),
        observed_at=observed_at,
        canonical_code=canonical_code,
        card_name=str(item.get("card_name") or "").strip(),
        set_code=str(item.get("set_code") or "").strip().upper(),
        set_name=str(item.get("set_name") or "").strip(),
        rarity=str(item.get("rarity") or "").strip(),
        color=str(item.get("color") or "").strip(),
        card_type=str(item.get("card_type") or "").strip(),
        cost=str(item.get("cost") or "").strip(),
        power=str(item.get("power") or "").strip(),
        counter=str(item.get("counter") or "").strip(),
        attribute=str(item.get("attribute") or "").strip(),
        traits=_tupleify_texts(item.get("traits")),
        life=str(item.get("life") or "").strip(),
        effect_text=str(item.get("effect_text") or item.get("official_text") or "").strip(),
        trigger_text=str(item.get("trigger_text") or "").strip(),
        subtypes=_tupleify_texts(item.get("subtypes") or item.get("subtype")),
        availability=str(item.get("availability") or "").strip(),
        status=str(item.get("status") or "").strip(),
        series_name=str(item.get("series_name") or "").strip(),
        product_name=str(item.get("product_name") or "").strip(),
        variant_family=str(item.get("variant_family") or "").strip(),
        official_text=str(item.get("official_text") or "").strip(),
        image_identity=str(item.get("image_identity") or "").strip(),
        variants=variants,
        relationships=relationships,
        gameplay_context=item.get("gameplay_context") or {},
        market_context=item.get("market_context") or {},
        extra=item.get("extra") or {},
    )
