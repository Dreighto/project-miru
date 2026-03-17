from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceCardVariant:
    variant_key: str
    variant_family: str = ""
    variant_label: str = ""
    image_identity: str = ""
    official_text: str = ""
    notes: str = ""


@dataclass(frozen=True)
class SourceCardRelationship:
    relationship_type: str
    related_card_code: str = ""
    related_variant_key: str = ""
    related_label: str = ""
    notes: str = ""


@dataclass(frozen=True)
class SourceCardRecord:
    source_key: str
    source_url: str
    source_title: str
    source_card_ref: str
    observed_at: str
    canonical_code: str
    card_name: str = ""
    set_code: str = ""
    set_name: str = ""
    rarity: str = ""
    color: str = ""
    card_type: str = ""
    cost: str = ""
    power: str = ""
    counter: str = ""
    attribute: str = ""
    traits: tuple[str, ...] = ()
    life: str = ""
    block_number: str = ""
    ban_status: str = ""
    restriction_count: str = ""
    effect_text: str = ""
    trigger_text: str = ""
    subtypes: tuple[str, ...] = ()
    availability: str = ""
    status: str = ""
    series_name: str = ""
    product_name: str = ""
    variant_family: str = ""
    official_text: str = ""
    image_identity: str = ""
    variants: tuple[SourceCardVariant, ...] = ()
    relationships: tuple[SourceCardRelationship, ...] = ()
    gameplay_context: dict[str, Any] = field(default_factory=dict)
    market_context: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactSourceCitation:
    source_key: str
    source_url: str
    source_title: str
    trust_tier: int
    trust_label: str
    source_weight: float
    observed_value_text: str
    observed_value_json: str = ""
    citation_text: str = ""
    extraction_method: str = "structured"
    observed_at: str = ""
    is_selected: bool = False
    is_conflicting: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FactSummary:
    field_name: str
    value_text: str = ""
    value_json: str = ""
    value_type: str = "text"
    verification_state: str = "missing"
    confidence_score: float = 0.0
    stable_fact: bool = True
    conflict_count: int = 0
    missing_count: int = 0
    supporting_source_count: int = 0
    last_checked_at: str = ""
    refresh_after_at: str = ""
    summary_json: str = ""
    citations: tuple[FactSourceCitation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["citations"] = [citation.to_dict() for citation in self.citations]
        return payload


@dataclass(frozen=True)
class VariantDossier:
    variant_key: str
    variant_family: str = ""
    variant_label: str = ""
    image_identity: str = ""
    verification_state: str = "missing"
    confidence_score: float = 0.0
    source_summary_json: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RelationshipDossier:
    relationship_type: str
    related_card_code: str = ""
    related_variant_key: str = ""
    related_label: str = ""
    notes: str = ""
    verification_state: str = "missing"
    confidence_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConfidenceSummary:
    overall_state: str
    overall_score: float
    verified_fields: tuple[str, ...] = ()
    likely_fields: tuple[str, ...] = ()
    uncertain_fields: tuple[str, ...] = ()
    conflicting_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CardDossier:
    canonical_code: str
    identity: dict[str, Any]
    official_details: dict[str, Any]
    variants: tuple[VariantDossier, ...]
    set_info: dict[str, Any]
    relationships: tuple[RelationshipDossier, ...]
    gameplay_context: dict[str, Any]
    market_context: dict[str, Any]
    source_ledger: tuple[FactSourceCitation, ...]
    facts: tuple[FactSummary, ...]
    confidence_summary: ConfidenceSummary
    refresh: dict[str, Any]
    future_extensions: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_code": self.canonical_code,
            "identity": self.identity,
            "official_details": self.official_details,
            "variants": [item.to_dict() for item in self.variants],
            "set_info": self.set_info,
            "relationships": [item.to_dict() for item in self.relationships],
            "gameplay_context": self.gameplay_context,
            "market_context": self.market_context,
            "source_ledger": [item.to_dict() for item in self.source_ledger],
            "facts": [item.to_dict() for item in self.facts],
            "confidence_summary": self.confidence_summary.to_dict(),
            "refresh": self.refresh,
            "future_extensions": self.future_extensions,
        }
