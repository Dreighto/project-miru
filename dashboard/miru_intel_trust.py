from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


@dataclass(frozen=True)
class SourceTrustProfile:
    source_key: str
    display_name: str
    trust_tier: int
    trust_label: str
    default_weight: float
    source_kind: str
    base_url: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_SOURCE_TRUST: dict[str, SourceTrustProfile] = {
    "official-cardlist": SourceTrustProfile(
        source_key="official-cardlist",
        display_name="Official One Piece Card List",
        trust_tier=1,
        trust_label="official",
        default_weight=1.0,
        source_kind="official",
        base_url="https://asia-en.onepiece-cardgame.com/cardlist/",
        notes="Highest trust for direct card identity, printed text, and official set labeling.",
    ),
    "reputable-card-db": SourceTrustProfile(
        source_key="reputable-card-db",
        display_name="Reputable Structured Card Database",
        trust_tier=2,
        trust_label="structured",
        default_weight=0.75,
        source_kind="structured-db",
        notes="Useful secondary source, but not higher trust than official source data.",
    ),
    "local-catalog": SourceTrustProfile(
        source_key="local-catalog",
        display_name="Miru Local Catalog",
        trust_tier=2,
        trust_label="structured",
        default_weight=0.65,
        source_kind="local-structured",
        notes="Helpful local structured context, but not automatically authoritative.",
    ),
    "community-market": SourceTrustProfile(
        source_key="community-market",
        display_name="Community or Market Source",
        trust_tier=3,
        trust_label="interpretive",
        default_weight=0.4,
        source_kind="community-market",
        notes="Useful for hints or disputes, but not enough to verify identity facts by itself.",
    ),
    "sample-fixture": SourceTrustProfile(
        source_key="sample-fixture",
        display_name="Sample Fixture Source",
        trust_tier=3,
        trust_label="fixture",
        default_weight=0.35,
        source_kind="fixture",
        notes="Local fixture-only source for architecture and test coverage.",
    ),
    "placeholder": SourceTrustProfile(
        source_key="placeholder",
        display_name="Placeholder Adapter",
        trust_tier=3,
        trust_label="placeholder",
        default_weight=0.2,
        source_kind="placeholder",
        notes="Explicitly incomplete placeholder source. Never treat as verified truth.",
    ),
}


def build_source_registry(
    extra_profiles: Iterable[SourceTrustProfile] | None = None,
) -> dict[str, SourceTrustProfile]:
    registry = dict(DEFAULT_SOURCE_TRUST)
    for profile in extra_profiles or ():
        registry[profile.source_key] = profile
    return registry


def get_source_profile(
    source_key: str,
    registry: dict[str, SourceTrustProfile] | None = None,
) -> SourceTrustProfile:
    registry = registry or DEFAULT_SOURCE_TRUST
    key = (source_key or "").strip().lower()
    if key in registry:
        return registry[key]
    return SourceTrustProfile(
        source_key=key or "unknown-source",
        display_name=(key or "Unknown Source").replace("-", " ").title() or "Unknown Source",
        trust_tier=3,
        trust_label="unknown",
        default_weight=0.25,
        source_kind="unknown",
        notes="Unknown source key encountered during enrichment.",
    )
