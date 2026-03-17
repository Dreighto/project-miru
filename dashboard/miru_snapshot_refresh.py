from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from dashboard.miru_intel_adapters import OfficialCardListSnapshotAdapter
from dashboard.miru_intel_db import MiruIntelRepository, utc_timestamp
from dashboard.miru_intel_models import CardDossier, FactSummary, SourceCardRecord
from dashboard.miru_intel_pipeline import FACT_FIELD_CONFIG, MiruEnrichmentRunner, canonicalize_card_code
from dashboard.miru_intel_trust import get_source_profile


_OFFICIAL_EXPORT_SOURCE_ALIASES = {
    "source_key": ("source_key", "sourceKey"),
    "source_title": ("source_title", "sourceTitle", "title"),
    "base_url": ("base_url", "baseUrl", "source_url", "sourceUrl"),
    "format": ("format",),
    "snapshot_taken_at": ("snapshot_taken_at", "snapshotTakenAt", "exported_at", "exportedAt", "updated_at", "updatedAt"),
    "source_export_format": ("source_export_format", "sourceExportFormat"),
}

_OFFICIAL_CARD_ALIASES = {
    "official_card_id": ("official_card_id", "officialCardId", "record_id", "recordId"),
    "card_code": ("card_code", "cardCode", "code"),
    "card_name": ("card_name", "cardName", "name"),
    "set_code": ("set_code", "setCode"),
    "set_name": ("set_name", "setName"),
    "rarity": ("rarity",),
    "color": ("color",),
    "card_type": ("card_type", "cardType", "type"),
    "cost": ("cost",),
    "power": ("power",),
    "counter": ("counter",),
    "attribute": ("attribute",),
    "life": ("life",),
    "block_number": ("block_number", "blockNumber", "block_no", "blockNo"),
    "ban_status": ("ban_status", "banStatus", "legality_status", "legalityStatus", "restriction_status", "restrictionStatus"),
    "restriction_count": ("restriction_count", "restrictionCount", "limit", "copies_allowed", "copiesAllowed", "max_copies"),
    "effect_text": ("effect_text", "effectText"),
    "trigger_text": ("trigger_text", "triggerText"),
    "official_text": ("official_text", "officialText", "text"),
    "image_url": ("image_url", "imageUrl", "image_identity", "imageIdentity"),
    "availability": ("availability",),
    "status": ("status",),
    "series_name": ("series_name", "seriesName"),
    "product_name": ("product_name", "productName"),
    "variant_family": ("variant_family", "variantFamily"),
    "last_checked_at": ("last_checked_at", "lastCheckedAt", "updated_at", "updatedAt"),
}


def _first_present(mapping: dict[str, Any], aliases: tuple[str, ...]) -> tuple[Any, bool]:
    for key in aliases:
        if key in mapping:
            return mapping.get(key), True
    return "", False


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_list_value(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = _normalize_text(value)
    if not text:
        return []
    if "/" in text:
        return [part.strip() for part in text.split("/") if part.strip()]
    return [text]


def _normalize_variant_rows(value: Any) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(value, list):
        return [], False
    variants: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        variant_key, has_key = _first_present(item, ("variant_key", "variantKey", "key"))
        variant_family, _ = _first_present(item, ("variant_family", "variantFamily", "family"))
        variant_label, _ = _first_present(item, ("variant_label", "variantLabel", "label"))
        image_value, _ = _first_present(item, ("image_url", "imageUrl", "image_identity", "imageIdentity"))
        official_text, _ = _first_present(item, ("official_text", "officialText", "text"))
        notes, _ = _first_present(item, ("notes",))
        variants.append(
            {
                "variant_key": _normalize_text(variant_key),
                "variant_family": _normalize_text(variant_family),
                "variant_label": _normalize_text(variant_label),
                "image_url": _normalize_text(image_value),
                "official_text": _normalize_text(official_text),
                "notes": _normalize_text(notes),
            }
        )
        if not has_key and variants[-1]["variant_label"]:
            variants[-1]["variant_key"] = variants[-1]["variant_label"].lower().replace(" ", "-")
    return variants, True


def normalize_official_export(payload: dict[str, Any]) -> dict[str, Any]:
    payload = payload or {}
    raw_meta = payload.get("source") or payload.get("export_meta") or {}
    source_profile = get_source_profile(_normalize_text(raw_meta.get("source_key") or raw_meta.get("sourceKey") or "official-cardlist"))
    meta: dict[str, Any] = {
        "source_key": source_profile.source_key,
        "source_title": source_profile.display_name,
        "base_url": source_profile.base_url,
        "format": "official-cardlist-snapshot",
        "snapshot_taken_at": "",
    }
    for field_name, aliases in _OFFICIAL_EXPORT_SOURCE_ALIASES.items():
        value, present = _first_present(raw_meta, aliases)
        if not present:
            continue
        normalized_value = _normalize_text(value)
        if field_name == "format":
            if normalized_value and normalized_value != "official-cardlist-snapshot":
                meta["source_export_format"] = normalized_value
            continue
        if field_name == "source_export_format":
            meta["source_export_format"] = normalized_value
            continue
        meta[field_name] = normalized_value
    if not meta["source_title"]:
        meta["source_title"] = source_profile.display_name
    if not meta["base_url"]:
        meta["base_url"] = source_profile.base_url
    if not meta["format"]:
        meta["format"] = "official-cardlist-snapshot"
    if not meta.get("source_export_format"):
        meta["source_export_format"] = meta["format"]

    raw_cards = payload.get("cards") or payload.get("rows") or payload.get("items") or []
    cards: list[dict[str, Any]] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        card: dict[str, Any] = {}
        present_fields: set[str] = set()
        for field_name, aliases in _OFFICIAL_CARD_ALIASES.items():
            value, present = _first_present(raw_card, aliases)
            if not present:
                continue
            if field_name in {"traits", "subtypes"}:
                continue
            text_value = _normalize_text(value)
            card[field_name] = text_value
            if text_value or field_name in {"trigger_text", "effect_text", "official_text"}:
                present_fields.add(field_name)
                if field_name == "image_url":
                    present_fields.add("image_identity")
        traits_value, traits_present = _first_present(raw_card, ("traits", "trait", "attribute_traits", "attributeTraits"))
        if traits_present:
            card["traits"] = _normalize_list_value(traits_value)
            present_fields.add("traits")
        subtypes_value, subtypes_present = _first_present(raw_card, ("subtypes", "subtype", "types"))
        if subtypes_present:
            card["subtypes"] = _normalize_list_value(subtypes_value)
            present_fields.add("subtypes")
        variants, variants_present = _normalize_variant_rows(raw_card.get("variants"))
        if variants_present:
            card["variants"] = variants
            present_fields.add("variants")
        if card.get("official_text") and "effect_text" not in card:
            card["effect_text"] = card["official_text"]
            present_fields.add("effect_text")
        if card.get("card_code"):
            card["card_code"] = canonicalize_card_code(card["card_code"])
        if card.get("set_code"):
            card["set_code"] = _normalize_text(card["set_code"]).upper()
        if not card.get("card_code"):
            continue
        card["present_fields"] = sorted(present_fields)
        cards.append(card)
    return {"source": meta, "cards": cards}


def normalize_official_export_path(input_path: str | Path, snapshot_output_path: str | Path | None = None) -> dict[str, Any]:
    payload = json.loads(Path(input_path).read_text(encoding="utf-8"))
    snapshot = normalize_official_export(payload)
    if snapshot_output_path:
        output_path = Path(snapshot_output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(snapshot, ensure_ascii=True, indent=2), encoding="utf-8")
    return snapshot


def _fact_lookup(dossier: CardDossier | None) -> dict[str, FactSummary]:
    if not dossier:
        return {}
    return {fact.field_name: fact for fact in dossier.facts}


def _fact_value(fact: FactSummary | None) -> Any:
    if not fact:
        return ""
    if fact.value_type == "json":
        if not fact.value_json:
            return []
        try:
            return json.loads(fact.value_json)
        except Exception:
            return []
    return fact.value_text


def _fact_signature(fact: FactSummary | None) -> tuple[str, str, str]:
    if not fact:
        return ("missing", "text", "")
    value = fact.value_json if fact.value_type == "json" else fact.value_text
    return (fact.verification_state, fact.value_type, value or "")


def _fact_has_value(fact: FactSummary | None) -> bool:
    if not fact:
        return False
    value = _fact_value(fact)
    if fact.verification_state == "conflict":
        return True
    if isinstance(value, list):
        return bool(value)
    return bool(str(value or "").strip())


def _field_change_category(before_fact: FactSummary | None, after_fact: FactSummary | None, *, present_in_refresh: bool) -> str:
    if after_fact and after_fact.verification_state == "conflict":
        return "conflict"
    if not present_in_refresh and _fact_has_value(before_fact) and not _fact_has_value(after_fact):
        return "missing_in_refresh_input"
    if not _fact_has_value(before_fact) and _fact_has_value(after_fact):
        return "added"
    if _fact_signature(before_fact) == _fact_signature(after_fact):
        return "unchanged"
    if not present_in_refresh and not _fact_has_value(before_fact) and not _fact_has_value(after_fact):
        return "unchanged"
    return "updated"


def _overall_refresh_category(counts: Counter[str], *, skipped: bool) -> str:
    if skipped:
        return "skipped"
    for category in ("conflict", "updated", "added", "missing_in_refresh_input"):
        if counts.get(category):
            return category
    if counts.get("unchanged"):
        return "unchanged"
    return "skipped"


def compare_dossier_refresh(
    before: CardDossier | None,
    after: CardDossier | None,
    refresh_card: dict[str, Any] | None,
    *,
    status: str,
) -> dict[str, Any]:
    canonical_code = canonicalize_card_code((refresh_card or {}).get("card_code") or (after.canonical_code if after else before.canonical_code if before else ""))
    if status == "skipped":
        return {
            "canonical_code": canonical_code,
            "overall_category": "skipped",
            "changed_field_count": 0,
            "counts": {"skipped": 1},
            "field_changes": [],
            "before_last_checked_at": before.refresh.get("last_checked_at") if before else "",
            "after_last_checked_at": after.refresh.get("last_checked_at") if after else "",
        }

    present_fields = set((refresh_card or {}).get("present_fields") or [])
    tracked_fields = sorted(FACT_FIELD_CONFIG)
    before_lookup = _fact_lookup(before)
    after_lookup = _fact_lookup(after)
    counts: Counter[str] = Counter()
    field_changes: list[dict[str, Any]] = []
    for field_name in tracked_fields:
        category = _field_change_category(
            before_lookup.get(field_name),
            after_lookup.get(field_name),
            present_in_refresh=field_name in present_fields,
        )
        counts[category] += 1
        if category == "unchanged":
            continue
        field_changes.append(
            {
                "field_name": field_name,
                "category": category,
                "present_in_refresh_input": field_name in present_fields,
                "before_state": before_lookup.get(field_name).verification_state if before_lookup.get(field_name) else "missing",
                "after_state": after_lookup.get(field_name).verification_state if after_lookup.get(field_name) else "missing",
                "before_value": _fact_value(before_lookup.get(field_name)),
                "after_value": _fact_value(after_lookup.get(field_name)),
            }
        )
    overall_category = _overall_refresh_category(counts, skipped=False)
    return {
        "canonical_code": canonical_code,
        "overall_category": overall_category,
        "changed_field_count": len(field_changes),
        "counts": dict(sorted(counts.items())),
        "field_changes": field_changes,
        "before_last_checked_at": before.refresh.get("last_checked_at") if before else "",
        "after_last_checked_at": after.refresh.get("last_checked_at") if after else "",
        "refresh_source_snapshot_at": ((refresh_card or {}).get("last_checked_at") or ""),
    }


def _best_source_key_from_fact(fact: FactSummary) -> str:
    """Return the most-trusted source key from a fact's stored citations."""
    citations = fact.citations or ()
    if not citations:
        return "official-cardlist"
    best = min(
        citations,
        key=lambda c: (c.trust_tier, -c.source_weight),
    )
    return best.source_key or "official-cardlist"


class _StoredDossierFallbackAdapter:
    """
    Preserves existing verified/likely facts for fields that are absent
    from the incoming official-snapshot import.

    During a partial refresh (e.g. a CSV that omits image_url, traits, or
    official_text), the enrichment pipeline has no observations for those
    absent fields and would write verification_state='missing', overwriting
    previously-verified data.  This adapter re-emits the stored verified/
    likely value for every field NOT listed in the card's present_fields,
    so those fields remain stable across partial imports.

    Only instantiated inside refresh_from_snapshot(); never used elsewhere.
    """

    _PRESERVE_STATES: frozenset[str] = frozenset({"verified", "likely"})
    # Text fields on SourceCardRecord that map 1-to-1 to FACT_FIELD_CONFIG keys
    _TEXT_FIELDS: frozenset[str] = frozenset({
        "card_name", "set_code", "set_name", "rarity", "color", "card_type",
        "cost", "power", "counter", "attribute", "life", "block_number",
        "ban_status", "restriction_count",
        "effect_text", "trigger_text", "official_text", "image_identity",
        "availability", "status", "series_name", "product_name", "variant_family",
    })

    def __init__(
        self,
        before_dossiers: dict[str, "CardDossier | None"],
        present_fields_by_code: dict[str, set[str]],
    ) -> None:
        self._dossiers = before_dossiers
        self._present = present_fields_by_code

    def fetch_card_records(self, canonical_code: str) -> list[SourceCardRecord]:
        dossier = self._dossiers.get(canonical_code)
        if not dossier:
            return []
        present = self._present.get(canonical_code, set())
        fact_lookup = {f.field_name: f for f in (dossier.facts or [])}

        # Group fields-to-preserve by source key so we emit one record per source.
        # This keeps trust-tier attribution correct.
        by_source: dict[str, dict[str, Any]] = {}
        for field_name in FACT_FIELD_CONFIG:
            if field_name in present:
                continue  # incoming snapshot owns this field; don't interfere
            fact = fact_lookup.get(field_name)
            if not fact:
                continue
            if fact.verification_state not in self._PRESERVE_STATES:
                continue
            source_key = _best_source_key_from_fact(fact)
            if source_key not in by_source:
                by_source[source_key] = {}
            if fact.value_type == "json":
                try:
                    items = json.loads(fact.value_json) if fact.value_json else []
                except Exception:
                    items = []
                by_source[source_key][field_name] = tuple(str(x) for x in items if x)
            else:
                by_source[source_key][field_name] = fact.value_text or ""

        records: list[SourceCardRecord] = []
        now = utc_timestamp()
        for source_key, fields in by_source.items():
            if not fields:
                continue
            profile = get_source_profile(source_key)
            # Build kwargs with safe defaults; fill only preserved fields
            kwargs: dict[str, Any] = {f: "" for f in self._TEXT_FIELDS}
            kwargs["traits"] = ()
            kwargs["subtypes"] = ()
            for field_name, value in fields.items():
                if field_name in kwargs:
                    kwargs[field_name] = value
            records.append(
                SourceCardRecord(
                    source_key=source_key,
                    source_url=profile.base_url,
                    source_title=profile.display_name,
                    source_card_ref=canonical_code,
                    observed_at=now,
                    canonical_code=canonical_code,
                    extra={"adapter_key": "stored-dossier-fallback"},
                    **kwargs,
                )
            )
        return records


class OfficialSnapshotRefresher:
    def __init__(
        self,
        repository: MiruIntelRepository,
        *,
        extra_adapters: list[Any] | None = None,
    ) -> None:
        self.repository = repository
        self.extra_adapters = list(extra_adapters or [])

    def refresh_from_snapshot(
        self,
        snapshot: dict[str, Any],
        *,
        run_id: str | None = None,
        resume: bool = False,
        notes: str = "",
    ) -> dict[str, Any]:
        normalized_snapshot = normalize_official_export(snapshot)
        cards = normalized_snapshot.get("cards") or []
        canonical_codes = [canonicalize_card_code(card.get("card_code") or "") for card in cards if canonicalize_card_code(card.get("card_code") or "")]
        cards_by_code = {canonicalize_card_code(card.get("card_code") or ""): card for card in cards}
        before = {code: self.repository.build_card_dossier(code) for code in canonical_codes}
        # Build present_fields map so the fallback adapter knows which fields the
        # incoming snapshot explicitly provides (and therefore should NOT preserve).
        present_fields_by_code: dict[str, set[str]] = {
            code: set(card.get("present_fields") or [])
            for code, card in cards_by_code.items()
        }
        # The fallback adapter re-emits stored verified/likely facts for fields
        # absent from the snapshot, preventing partial imports from demoting
        # existing verified data to 'missing'.
        fallback = _StoredDossierFallbackAdapter(before, present_fields_by_code)
        adapters = [OfficialCardListSnapshotAdapter(normalized_snapshot), fallback] + self.extra_adapters
        runner = MiruEnrichmentRunner(self.repository, adapters)
        run_result = runner.run_batch(
            canonical_codes,
            run_id=run_id,
            resume=resume,
            mode="official-snapshot-refresh",
            notes=notes,
        )
        refresh_reports: list[dict[str, Any]] = []
        for item in run_result.get("results") or []:
            code = canonicalize_card_code(item.get("canonical_code") or "")
            after = self.repository.build_card_dossier(code)
            report = compare_dossier_refresh(before.get(code), after, cards_by_code.get(code), status=str(item.get("status") or ""))
            self.repository.record_refresh_report(
                run_id=run_result["run"]["run_id"],
                canonical_code=code,
                source_kind="official-refresh",
                overall_category=report["overall_category"],
                changed_field_count=report["changed_field_count"],
                counts=report.get("counts") or {},
                report=report,
            )
            refresh_reports.append(report)
        return {
            "snapshot": normalized_snapshot,
            "run": run_result.get("run") or {},
            "results": run_result.get("results") or [],
            "refresh_reports": refresh_reports,
        }

    def refresh_from_export_path(
        self,
        input_path: str | Path,
        *,
        snapshot_output_path: str | Path | None = None,
        run_id: str | None = None,
        resume: bool = False,
        notes: str = "",
    ) -> dict[str, Any]:
        snapshot = normalize_official_export_path(input_path, snapshot_output_path=snapshot_output_path)
        return self.refresh_from_snapshot(snapshot, run_id=run_id, resume=resume, notes=notes)
