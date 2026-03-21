from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlparse


DISCOVERY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "rules_legality",
        ("rules", "faq", "errata", "restriction", "banned", "limited"),
        ("onepiece-cardgame",),
    ),
    (
        "structured_api",
        ("api", "json", "endpoint", "documentation", "swagger"),
        ("optcgapi",),
    ),
    (
        "deck_database",
        ("deck", "decks", "decklist", "decklists", "archetype", "archetypes"),
        ("limitlesstcg", "onepiecetopdecks", "egmanevents", "nakamadecks", "optcg.gg"),
    ),
    (
        "card_database",
        ("card", "cards", "cardlist", "catalog", "database"),
        ("onepiece-cardgame", "limitlesstcg", "tcgplayer", "collectr", "onepiece-cardgame.dev"),
    ),
    (
        "tournament_results",
        ("tournament", "results", "placements", "top cut", "regional", "championship"),
        ("limitlesstcg", "egmanevents", "topdeck", "melee"),
    ),
    (
        "meta_analysis",
        ("meta", "analysis", "tier list", "guide", "matchup", "strategy"),
        ("onepiecetopdecks", "pokemoncard", "youtube", "substack", "medium"),
    ),
    (
        "market_listing",
        ("price", "listing", "listings", "auction", "sale", "seller"),
        ("ebay", "cardmarket", "tcgplayer"),
    ),
    (
        "community_chat",
        ("discord", "reddit", "forum", "community", "chat"),
        ("discord", "reddit", "facebook", "patreon"),
    ),
)

OFFICIAL_HOST_HINTS = ("onepiece-cardgame.com",)
PERMITTED_API_HOST_HINTS = ("optcgapi.com",)
PREAPPROVED_REFERENCE_HOST_HINTS = ("onepiece-cardgame.dev",)
PREAPPROVED_HYBRID_HOST_HINTS = ("limitlesstcg.com", "optcg.gg")
COMMUNITY_HOST_HINTS = (
    "onepiecetopdecks.com",
    "nakamadecks.com",
    "egmanevents.com",
    "limitlesstcg.com",
    "topdeck.gg",
    "optcg.gg",
    "onepiece-cardgame.dev",
)
MARKET_HOST_HINTS = ("ebay.com", "cardmarket.com", "tcgplayer.com")
MANUAL_ONLY_HOST_HINTS = (
    "discord.com",
    "facebook.com",
    "instagram.com",
    "patreon.com",
)
RESTRICTED_ACCESS_HINTS = (
    "login",
    "signin",
    "account",
    "member",
    "members",
    "subscribe",
    "premium",
    "patreon",
    "discord",
)
RULES_SIGNAL_HINTS = ("rules", "faq", "errata", "restriction", "banned", "limited")
SOURCE_KIND_GAP_SUPPORT: dict[str, tuple[str, ...]] = {
    "rules_legality": ("legality_recheck", "source_depth_fill"),
    "structured_api": ("source_depth_fill", "stale_refresh"),
    "deck_database": ("usage_meta_fill", "leader_profile_expand", "source_depth_fill"),
    "card_database": ("source_depth_fill", "stale_refresh"),
    "tournament_results": ("usage_meta_fill", "leader_profile_expand"),
    "meta_analysis": ("usage_meta_fill", "leader_profile_expand", "stale_refresh"),
    "market_listing": (),
    "community_chat": (),
}


@dataclass(frozen=True)
class SourceDiscoveryProfile:
    source_type: str
    source_family: str
    source_classification: str
    permission_posture: str
    likely_allowed_access: str
    trust_tier: int
    trust_label: str
    evidence_role: str
    manual_approval_required: bool
    expected_gap_support: tuple[str, ...]
    usefulness_score: float
    risk_flags: tuple[str, ...]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiscoveredSourceCandidate:
    url: str
    host: str
    source_kind: str
    confidence_score: float
    review_status: str
    detected_at: str
    title: str = ""
    notes: str = ""
    signals: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata or {})
        return payload


def _normalized_tokens(*values: str) -> list[str]:
    tokens: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text:
            continue
        normalized = (
            text.replace("-", " ")
            .replace("_", " ")
            .replace(".", " ")
            .replace("/", " ")
        )
        tokens.extend(part for part in normalized.split() if part)
    return tokens


def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
    normalized_host = str(host or "").strip().lower()
    if not normalized_host:
        return False
    for pattern in patterns:
        normalized_pattern = str(pattern or "").strip().lower()
        if not normalized_pattern:
            continue
        if normalized_host == normalized_pattern or normalized_host.endswith(f".{normalized_pattern}"):
            return True
    return False


def discover_source_candidate(
    *,
    url: str,
    title: str = "",
    notes: str = "",
    detected_at: str = "",
) -> DiscoveredSourceCandidate | None:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    tokens = _normalized_tokens(host, path, title, notes)
    token_set = set(tokens)

    best_kind = ""
    best_score = 0.0
    best_signals: list[str] = []
    for source_kind, keywords, host_hints in DISCOVERY_RULES:
        score = 0.0
        signals: list[str] = []
        for keyword in keywords:
            keyword_parts = tuple(keyword.split())
            if len(keyword_parts) == 1:
                if keyword_parts[0] in token_set:
                    score += 0.2
                    signals.append(keyword)
            elif all(part in token_set for part in keyword_parts):
                score += 0.25
                signals.append(keyword)
        for host_hint in host_hints:
            if host_hint in host:
                score += 0.35
                signals.append(host_hint)
        if score > best_score:
            best_kind = source_kind
            best_score = score
            best_signals = signals

    if best_score <= 0.0:
        return None

    confidence_score = min(round(0.35 + best_score, 2), 0.95)
    return DiscoveredSourceCandidate(
        url=parsed.geturl(),
        host=host,
        source_kind=best_kind or "unknown_candidate",
        confidence_score=confidence_score,
        review_status="pending_review",
        detected_at=str(detected_at or ""),
        title=str(title or "").strip(),
        notes=str(notes or "").strip(),
        signals=tuple(sorted(set(best_signals))),
        metadata={"path": path},
    )


def discover_source_candidates(
    rows: list[dict[str, Any]],
    *,
    detected_at: str = "",
) -> list[DiscoveredSourceCandidate]:
    candidates: list[DiscoveredSourceCandidate] = []
    seen: set[str] = set()
    for row in rows:
        candidate = discover_source_candidate(
            url=str(row.get("url") or ""),
            title=str(row.get("title") or ""),
            notes=str(row.get("notes") or ""),
            detected_at=detected_at,
        )
        if candidate is None:
            continue
        if candidate.url in seen:
            continue
        seen.add(candidate.url)
        candidates.append(candidate)
    return candidates


def infer_source_discovery_profile(candidate: DiscoveredSourceCandidate) -> SourceDiscoveryProfile:
    host = str(candidate.host or "").strip().lower()
    path = str((candidate.metadata or {}).get("path") or "").strip().lower()
    tokens = set(_normalized_tokens(host, path, candidate.title, candidate.notes, candidate.source_kind))
    source_kind = str(candidate.source_kind or "").strip().lower() or "unknown_candidate"
    expected_gap_support = SOURCE_KIND_GAP_SUPPORT.get(source_kind, ("source_depth_fill",))
    risk_flags: list[str] = []

    source_type = source_kind
    source_family = "unclassified"
    source_classification = "unknown"
    permission_posture = "unclear_permissions"
    likely_allowed_access = "manual_only"
    trust_tier = 4
    trust_label = "manual review only"
    evidence_role = "blocked"
    manual_approval_required = True
    rationale = "Candidate source remains ungoverned until Miru can confirm permission posture and source role."

    if _host_matches(host, OFFICIAL_HOST_HINTS):
        source_family = "official_bandai"
        source_classification = "official"
        permission_posture = "public_official"
        likely_allowed_access = "public_page"
        trust_tier = 1
        trust_label = "official"
        evidence_role = "verified-facts"
        manual_approval_required = False
        if source_kind == "rules_legality" or any(token in tokens for token in RULES_SIGNAL_HINTS):
            source_type = "official-rules-page"
            rationale = "Official Bandai rules or legality surface appears publicly reachable and suited to verified fact corroboration."
            expected_gap_support = ("legality_recheck", "source_depth_fill")
        elif source_kind == "deck_database":
            source_type = "official-deck-feature"
            rationale = "Official Bandai deck or feature page appears publicly reachable and suited to verified usage/profile corroboration."
            expected_gap_support = ("usage_meta_fill", "leader_profile_expand", "source_depth_fill")
        else:
            source_type = "official-card-catalog"
            rationale = "Official Bandai catalog-style page appears publicly reachable and suited to verified fact corroboration."
    elif _host_matches(host, PERMITTED_API_HOST_HINTS) or source_kind == "structured_api":
        source_family = "structured_reference_api"
        source_classification = "reference"
        permission_posture = "permitted_api_public"
        likely_allowed_access = "permitted_api"
        trust_tier = 3
        trust_label = "secondary/reference"
        evidence_role = "reference-facts"
        manual_approval_required = False
        source_type = "structured-reference-api"
        rationale = "Structured public API candidate looks suitable for reference-safe corroboration, but not for outranking official truth."
    elif _host_matches(host, MARKET_HOST_HINTS) or source_kind == "market_listing":
        source_family = "market_signal"
        source_classification = "market"
        permission_posture = "market_signal_only"
        likely_allowed_access = "public_page"
        trust_tier = 4
        trust_label = "market signal only"
        evidence_role = "market-hint-only"
        manual_approval_required = True
        source_type = "market-listing"
        expected_gap_support = ()
        risk_flags.append("non_authoritative_market_signal")
        rationale = "Marketplace coverage may surface lead hints, but it is not safe for truth-critical learning or automatic intake."
    elif _host_matches(host, PREAPPROVED_REFERENCE_HOST_HINTS):
        source_family = "governed_external_reference"
        source_classification = "reference"
        permission_posture = "public_governed"
        likely_allowed_access = "public_page"
        trust_tier = 2
        trust_label = "secondary/reference"
        evidence_role = "reference-facts"
        manual_approval_required = False
        source_type = "governed-card-reference"
        expected_gap_support = ("source_depth_fill", "leader_profile_expand", "stale_refresh")
        rationale = "Pre-approved public card-reference source is suitable for bounded secondary corroboration and discovery queueing."
    elif _host_matches(host, PREAPPROVED_HYBRID_HOST_HINTS):
        source_family = "governed_external_hybrid"
        source_classification = "reference"
        permission_posture = "public_governed"
        likely_allowed_access = "public_page"
        trust_tier = 2
        trust_label = "secondary/reference"
        evidence_role = "reference-facts"
        manual_approval_required = False
        source_type = "governed-hybrid-deck-meta"
        expected_gap_support = ("usage_meta_fill", "leader_profile_expand", "source_depth_fill", "stale_refresh")
        rationale = "Pre-approved public deck/meta source is suitable for bounded reference-safe usage and archetype corroboration."
    elif _host_matches(host, MANUAL_ONLY_HOST_HINTS) or any(token in tokens for token in RESTRICTED_ACCESS_HINTS):
        source_family = "restricted_or_manual"
        source_classification = "restricted"
        permission_posture = "manual_only_or_login"
        likely_allowed_access = "manual_only"
        trust_tier = 4
        trust_label = "manual review only"
        evidence_role = "blocked"
        manual_approval_required = True
        source_type = "restricted-community-surface"
        expected_gap_support = ()
        risk_flags.append("restricted_access")
        rationale = "Candidate appears to require login, membership, or manual handling, so Miru must fail closed."
    elif _host_matches(host, COMMUNITY_HOST_HINTS) or source_kind in {
        "deck_database",
        "tournament_results",
        "meta_analysis",
        "community_chat",
    }:
        source_family = "community_reference"
        source_classification = "community"
        permission_posture = "public_unapproved"
        likely_allowed_access = "public_page"
        trust_tier = 3
        trust_label = "community lead only"
        evidence_role = "lead-signal-only"
        manual_approval_required = True
        source_type = f"community-{source_kind}"
        risk_flags.append("needs_registry_review")
        rationale = "Community coverage may help Miru discover leads or corroborate usage patterns, but it needs governed review before intake."
    else:
        risk_flags.append("permission_uncertain")

    usefulness_score = round(
        min(
            1.0,
            max(
                0.1,
                float(candidate.confidence_score or 0.0) * 0.6
                + len(expected_gap_support) * 0.12
                + (0.18 if source_classification == "official" else 0.1 if source_classification == "reference" else 0.0),
            ),
        ),
        2,
    )

    return SourceDiscoveryProfile(
        source_type=source_type,
        source_family=source_family,
        source_classification=source_classification,
        permission_posture=permission_posture,
        likely_allowed_access=likely_allowed_access,
        trust_tier=trust_tier,
        trust_label=trust_label,
        evidence_role=evidence_role,
        manual_approval_required=manual_approval_required,
        expected_gap_support=tuple(expected_gap_support),
        usefulness_score=usefulness_score,
        risk_flags=tuple(sorted(set(flag for flag in risk_flags if flag))),
        rationale=rationale,
    )
