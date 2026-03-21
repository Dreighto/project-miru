from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


# Allowed access and API-permission: for compliance, sources that need API/auth must not be used automatically.
ALLOWED_ACCESS_PUBLIC_PAGE = "public_page"
ALLOWED_ACCESS_PERMITTED_API = "permitted_api"
ALLOWED_ACCESS_MANUAL_ONLY = "manual_only"


@dataclass(frozen=True)
class MiruSourceEntry:
    source_id: str
    source_name: str
    source_type: str
    trust_tier: int
    trust_label: str
    enabled: bool
    fetch_mode: str
    supported_fields: tuple[str, ...]
    refresh_policy: str
    rate_limit_hint: str
    backoff_policy: str
    review_state: str
    notes: str = ""
    base_url: str = ""
    snapshot_url: str = ""
    request_spacing_seconds: float = 0.0
    default_confidence: float = 0.0
    # Allowed-source registry fields (learner readiness)
    domain: str = ""
    allowed_access: str = ALLOWED_ACCESS_PUBLIC_PAGE
    language: str = "EN"
    data_types: tuple[str, ...] = ()
    publish_allowed: bool = True
    requires_api: bool = False
    source_category: str = ""
    capability_tags: tuple[str, ...] = ()
    gap_support: tuple[str, ...] = ()
    snapshot_candidates: tuple[str, ...] = ()
    execution_adapter: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def public_data_only(self) -> bool:
        return self.allowed_access in {
            ALLOWED_ACCESS_PUBLIC_PAGE,
            ALLOWED_ACCESS_PERMITTED_API,
        } and not bool(self.requires_api)

    @property
    def requires_login(self) -> bool:
        return bool(self.requires_api) or self.allowed_access == ALLOWED_ACCESS_MANUAL_ONLY

    @property
    def respect_site_policies(self) -> bool:
        return True

    @property
    def allow_aggressive_crawling(self) -> bool:
        return False

    @property
    def data_categories(self) -> tuple[str, ...]:
        if self.data_types:
            return tuple(str(item).strip() for item in self.data_types if str(item).strip())
        return ()

    @property
    def anti_crawl_policy(self) -> str:
        if self.allowed_access == ALLOWED_ACCESS_MANUAL_ONLY:
            return "manual review only"
        if self.allowed_access == ALLOWED_ACCESS_PERMITTED_API:
            return "permitted API or explicit operator-controlled access only"
        return "public snapshot access only; respect request spacing and fail closed on policy uncertainty"


def is_source_registered(
    source_id: str,
    registry: dict[str, MiruSourceEntry] | None = None,
) -> bool:
    """True if source_id is in the registry (and thus allowed for discovery)."""
    reg = registry or DEFAULT_SOURCE_REGISTRY
    return (source_id or "").strip().lower() in reg


def get_source_entry_or_none(
    source_id: str,
    registry: dict[str, MiruSourceEntry] | None = None,
) -> MiruSourceEntry | None:
    """Return the source entry if registered, else None. Use for safe checks before operating."""
    reg = registry or DEFAULT_SOURCE_REGISTRY
    key = (source_id or "").strip().lower()
    return reg.get(key)


def _normalized_registry_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
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
        for part in normalized.split():
            if part:
                tokens.add(part)
    return tokens


def _entry_host_hints(entry: MiruSourceEntry) -> set[str]:
    hints: set[str] = set()
    for value in (entry.domain, entry.base_url, entry.snapshot_url):
        text = str(value or "").strip().lower()
        if not text:
            continue
        parsed = urlparse(text if "://" in text else f"https://{text}")
        host = str(parsed.netloc or parsed.path or "").strip().lower().removeprefix("www.")
        if host:
            hints.add(host)
    return hints


def _core_host(host: str) -> str:
    normalized = str(host or "").strip().lower().removeprefix("www.")
    parts = [part for part in normalized.split(".") if part]
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return normalized


def find_source_registry_matches(
    *,
    url: str = "",
    host: str = "",
    hint_text: Iterable[str] = (),
    registry: dict[str, MiruSourceEntry] | None = None,
) -> list[dict[str, Any]]:
    reg = registry or DEFAULT_SOURCE_REGISTRY
    parsed = urlparse(str(url or "").strip())
    candidate_host = str(host or parsed.netloc or "").strip().lower().removeprefix("www.")
    candidate_path = str(parsed.path or "").strip().lower()
    candidate_tokens = _normalized_registry_tokens(candidate_host, candidate_path, url, *tuple(hint_text))
    if not candidate_host:
        return []

    matches: list[dict[str, Any]] = []
    for entry in reg.values():
        entry_hosts = _entry_host_hints(entry)
        entry_tokens = _normalized_registry_tokens(
            entry.source_id,
            entry.source_name,
            entry.source_type,
            entry.source_category,
            entry.base_url,
            entry.snapshot_url,
            entry.notes,
            " ".join(entry.capability_tags),
            " ".join(entry.gap_support),
        )
        overlap = sorted(candidate_tokens & entry_tokens)
        matched_host = next(
            (
                hint
                for hint in entry_hosts
                if candidate_host == hint
                or candidate_host.endswith(f".{hint}")
                or _core_host(candidate_host) == _core_host(hint)
            ),
            "",
        )
        candidate_core = _core_host(candidate_host)
        sid = str(entry.source_id or "").strip().lower()
        fallback_family_match = (
            (candidate_core == "onepiece-cardgame.com" and sid.startswith("official-"))
            or (candidate_core == "optcgapi.com" and sid == "optcg-api")
            or (candidate_core == "limitlesstcg.com" and sid == "limitless")
            or (candidate_core == "optcg.gg" and sid == "optcg-gg")
            or (candidate_core == "onepiece-cardgame.dev" and sid == "onepiece-cardgame-dev")
        )
        if not matched_host and (not fallback_family_match or len(overlap) < 3):
            continue

        score = 1.0 if matched_host and candidate_host == matched_host else 0.75 if matched_host else 0.55
        reasons = [f"host:{matched_host}"] if matched_host else ["token_overlap_only"]
        if overlap:
            score += min(1.25, 0.2 * len(overlap))
            reasons.append(f"token_overlap:{','.join(overlap[:6])}")
        if str(entry.source_id or "").strip().lower() in str(url or "").strip().lower():
            score += 0.4
            reasons.append("source_id_hint")

        matches.append(
            {
                "source_id": str(entry.source_id or "").strip().lower(),
                "match_score": round(score, 2),
                "match_reasons": tuple(reasons),
                "entry": entry,
            }
        )

    matches.sort(
        key=lambda item: (
            -float(item.get("match_score") or 0.0),
            int(getattr(item.get("entry"), "trust_tier", 4) or 4),
            str(item.get("source_id") or ""),
        )
    )
    return matches


SUPPORTED_CARD_FIELDS = (
    "card_code",
    "card_name",
    "set_code",
    "set_name",
    "rarity",
    "color",
    "card_type",
    "cost",
    "power",
    "counter",
    "attribute",
    "traits",
    "life",
    "effect_text",
    "trigger_text",
    "source_id",
    "source_url",
    "source_reference",
    "fetched_at",
)

SUPPORTED_IMAGE_FIELDS = (
    "card_code",
    "variant_key",
    "filename",
    "local_path",
    "source_id",
    "source_url",
    "source_reference",
    "verification_state",
    "image_hash",
    "width",
    "height",
    "downloaded_at",
    "last_verified_at",
    "last_error",
    "fetched_at",
)


TRUST_TIER_LABELS = {
    1: "official",
    2: "high-confidence community",
    3: "secondary/reference",
    4: "experimental/manual review only",
}

# Worktree-local approved-sources allowlist (ethics-first; discovery stays manual).
_REGISTRY_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _REGISTRY_DIR.parent  # tools/ -> repo root
DEFAULT_APPROVED_SOURCES_CONFIG_PATH = _PROJECT_ROOT / "config" / "miru_approved_sources.json"


def load_approved_sources_from_config(
    path: Path | None = None,
) -> tuple[list[MiruSourceEntry], list[str]]:
    """Load approved source entries from worktree config. Missing file or invalid entries fail safely.
    Returns (list of valid MiruSourceEntry, list of error messages for invalid/missing data).
    """
    config_path = Path(path) if path is not None else DEFAULT_APPROVED_SOURCES_CONFIG_PATH
    if not config_path.is_file():
        return [], []

    raw: str
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as e:
        return [], [f"Could not read config: {e}"]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [], [f"Invalid JSON in approved-sources config: {e}"]

    if not isinstance(data, dict):
        return [], ["Approved-sources config must be a JSON object with an 'approved_sources' array."]

    sources = data.get("approved_sources")
    if sources is None:
        return [], []
    if not isinstance(sources, list):
        return [], ["'approved_sources' must be a JSON array."]

    entries: list[MiruSourceEntry] = []
    errors: list[str] = []

    for i, item in enumerate(sources):
        if not isinstance(item, dict):
            errors.append(f"approved_sources[{i}]: entry must be an object; skipped.")
            continue
        source_id = item.get("source_id")
        if not source_id or not str(source_id).strip():
            errors.append(f"approved_sources[{i}]: missing or empty 'source_id'; skipped.")
            continue
        source_id = str(source_id).strip().lower().replace(" ", "-")
        source_name = str(item.get("source_name") or source_id).strip() or source_id.replace("-", " ").title()
        source_type = str(item.get("source_type") or "community-approved").strip()
        trust_tier = int(item.get("trust_tier", 4))
        if trust_tier not in TRUST_TIER_LABELS:
            trust_tier = 4
        enabled = bool(item.get("enabled", True))
        allowed_access = str(item.get("allowed_access") or ALLOWED_ACCESS_PUBLIC_PAGE).strip()
        if allowed_access not in (ALLOWED_ACCESS_PUBLIC_PAGE, ALLOWED_ACCESS_PERMITTED_API, ALLOWED_ACCESS_MANUAL_ONLY):
            allowed_access = ALLOWED_ACCESS_PUBLIC_PAGE
        request_spacing_seconds = float(item.get("request_spacing_seconds", 2.0))
        if request_spacing_seconds < 0:
            request_spacing_seconds = 2.0
        requires_api = bool(item.get("requires_api", False))
        snapshot_url = str(item.get("snapshot_url") or "").strip()
        base_url = str(item.get("base_url") or "").strip()
        domain = str(item.get("domain") or "").strip()
        notes = str(item.get("notes") or "").strip()
        fetch_mode = str(item.get("fetch_mode") or "snapshot-json").strip()
        if not fetch_mode:
            fetch_mode = "snapshot-json"
        publish_allowed = bool(item.get("publish_allowed", True))
        source_category = str(item.get("source_category") or "").strip()
        capability_tags = tuple(
            str(tag).strip()
            for tag in (item.get("capability_tags") or [])
            if str(tag).strip()
        ) if isinstance(item.get("capability_tags"), list) else ()
        gap_support = tuple(
            str(tag).strip()
            for tag in (item.get("gap_support") or [])
            if str(tag).strip()
        ) if isinstance(item.get("gap_support"), list) else ()
        snapshot_candidates = tuple(
            str(tag).strip()
            for tag in (item.get("snapshot_candidates") or [])
            if str(tag).strip()
        ) if isinstance(item.get("snapshot_candidates"), list) else ()
        execution_adapter = str(item.get("execution_adapter") or "").strip()

        try:
            entry = MiruSourceEntry(
                source_id=source_id,
                source_name=source_name,
                source_type=source_type,
                trust_tier=trust_tier,
                trust_label=TRUST_TIER_LABELS.get(trust_tier, TRUST_TIER_LABELS[4]),
                enabled=enabled,
                fetch_mode=fetch_mode,
                supported_fields=SUPPORTED_CARD_FIELDS,
                refresh_policy="manual review before use",
                rate_limit_hint="Use approved-sources config; respect request_spacing_seconds.",
                backoff_policy="manual retry after failure",
                review_state="active",
                notes=notes or f"Approved via worktree config: {config_path.name}",
                base_url=base_url,
                snapshot_url=snapshot_url,
                request_spacing_seconds=request_spacing_seconds,
                default_confidence=0.35 if trust_tier >= 3 else 0.58,
                domain=domain,
                allowed_access=allowed_access,
                publish_allowed=publish_allowed,
                requires_api=requires_api,
                source_category=source_category,
                capability_tags=capability_tags,
                gap_support=gap_support,
                snapshot_candidates=snapshot_candidates,
                execution_adapter=execution_adapter,
            )
            entries.append(entry)
        except (TypeError, ValueError) as e:
            errors.append(f"approved_sources[{i}] ({source_id}): {e}; skipped.")

    return entries, errors


def get_approved_sources_config_status(path: Path | None = None) -> dict[str, Any]:
    """Return status of the approved-sources config load (for Dev page / visibility).
    Does not modify registry. Keys: config_path, found, loaded_count, errors.
    """
    config_path = Path(path) if path is not None else DEFAULT_APPROVED_SOURCES_CONFIG_PATH
    found = config_path.is_file()
    entries, errors = load_approved_sources_from_config(config_path)
    return {
        "config_path": str(config_path),
        "found": found,
        "loaded_count": len(entries),
        "errors": errors,
    }


def load_approved_source_ids_from_config(
    path: Path | None = None,
) -> set[str]:
    entries, _ = load_approved_sources_from_config(path)
    return {
        str(entry.source_id or "").strip().lower()
        for entry in entries
        if str(entry.source_id or "").strip()
    }


def is_source_approved_in_config(
    source_id: str,
    path: Path | None = None,
) -> bool:
    key = str(source_id or "").strip().lower()
    if not key:
        return False
    return key in load_approved_source_ids_from_config(path)


CORE_SOURCE_REGISTRY: dict[str, MiruSourceEntry] = {
    "official-cardlist": MiruSourceEntry(
        source_id="official-cardlist",
        source_name="Official One Piece Card List",
        source_type="official-cardlist-snapshot",
        trust_tier=1,
        trust_label=TRUST_TIER_LABELS[1],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="manual-or-queued refresh only",
        rate_limit_hint="Keep live fetches explicit and slow; roughly one request per second.",
        backoff_policy="exponential backoff with manual retry for repeated failures",
        review_state="active",
        notes="Primary trusted source for direct card identity and printed official fields.",
        base_url="https://asia-en.onepiece-cardgame.com/cardlist/",
        snapshot_url="",
        request_spacing_seconds=1.0,
        default_confidence=0.95,
        source_category="card_identity",
        capability_tags=("card_identity", "print_confirmation", "card_fields"),
        gap_support=("source_depth_fill", "leader_profile_expand"),
        snapshot_candidates=("data/official_cardlist_snapshot.json", "data/snapshots/official_cardlist.json"),
        execution_adapter="official-cardlist",
    ),
    "official-card-images": MiruSourceEntry(
        source_id="official-card-images",
        source_name="Official One Piece Card Images",
        source_type="official-card-image-snapshot",
        trust_tier=1,
        trust_label=TRUST_TIER_LABELS[1],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_IMAGE_FIELDS,
        refresh_policy="manual-or-queued refresh only",
        rate_limit_hint="Keep image fetches explicit and rate-limited; roughly one request per second.",
        backoff_policy="exponential backoff with manual retry for repeated failures",
        review_state="active",
        notes="Trusted image source for official card art with controlled ingestion.",
        base_url="https://en.onepiece-cardgame.com/images/cardlist/card/",
        snapshot_url="",
        request_spacing_seconds=1.0,
        default_confidence=0.95,
        source_category="print_confirmation",
        capability_tags=("card_images", "print_confirmation"),
        gap_support=("source_depth_fill",),
        snapshot_candidates=("data/official_card_images_snapshot.json",),
        execution_adapter="official-card-images",
    ),
    "reputable-card-db": MiruSourceEntry(
        source_id="reputable-card-db",
        source_name="Reputable Structured Card Database",
        source_type="community-card-database",
        trust_tier=2,
        trust_label=TRUST_TIER_LABELS[2],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="manual review before broader use",
        rate_limit_hint="Use conservative spacing and cache snapshots locally.",
        backoff_policy="slow retry with operator review after repeated failures",
        review_state="active",
        notes="Useful structured secondary source, but never preferred over official printed data.",
        request_spacing_seconds=2.0,
        default_confidence=0.78,
        source_category="card_identity",
        capability_tags=("card_identity", "card_fields", "secondary_corroboration"),
        gap_support=("source_depth_fill", "leader_profile_expand"),
        execution_adapter="official-cardlist",
    ),
    "community-market": MiruSourceEntry(
        source_id="community-market",
        source_name="Community or Market Source",
        source_type="community-market-reference",
        trust_tier=3,
        trust_label=TRUST_TIER_LABELS[3],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="manual review before validation use",
        rate_limit_hint="Treat as advisory only and prefer cached snapshots.",
        backoff_policy="manual retry only after review",
        review_state="active",
        notes="Useful for hints, usage signals, or discrepancies, but not strong enough to override trusted verified values by itself.",
        request_spacing_seconds=3.0,
        default_confidence=0.58,
        source_category="market_signal",
        capability_tags=("market_signal",),
        gap_support=(),
    ),
    "manual-review": MiruSourceEntry(
        source_id="manual-review",
        source_name="Manual Review Candidate",
        source_type="experimental-manual",
        trust_tier=4,
        trust_label=TRUST_TIER_LABELS[4],
        enabled=False,
        fetch_mode="manual",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="never auto-activate",
        rate_limit_hint="Operator-triggered only.",
        backoff_policy="manual review only",
        review_state="manual-review-only",
        notes="Experimental or manually gathered source data. Never auto-promote into verified truth.",
        request_spacing_seconds=0.0,
        default_confidence=0.35,
        source_category="manual_only",
        capability_tags=("manual_only",),
        gap_support=(),
    ),
}


EXPANDED_SOURCE_REGISTRY: dict[str, MiruSourceEntry] = {
    "optcg-api": MiruSourceEntry(
        source_id="optcg-api",
        source_name="OPTCG API",
        source_type="reference-structured-api",
        trust_tier=2,
        trust_label=TRUST_TIER_LABELS[2],
        enabled=True,
        fetch_mode="live",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="bounded per-card corroboration only",
        rate_limit_hint="Use only bounded, cache-first per-card calls; docs request callers avoid heavy daily traffic.",
        backoff_policy="stop on ambiguity or repeated failures and fall back to cached records only",
        review_state="active",
        notes=(
            "Public English-language structured reference API with explicit docs that it is open for anyone to use "
            "and does not require authentication. Use only GET requests, prefer narrow lookups before broader pulls, "
            "and keep request volume bounded out of courtesy to the maintainer's VPS. Treat strictly as secondary "
            "corroboration and never as canonical truth over Bandai-published sources."
        ),
        base_url="https://optcgapi.com/",
        snapshot_url="https://optcgapi.com/api/sets/card/{card_code}/",
        request_spacing_seconds=4.0,
        default_confidence=0.72,
        domain="optcgapi.com",
        allowed_access=ALLOWED_ACCESS_PERMITTED_API,
        requires_api=False,
        data_types=("sets", "set_cards", "starter_decks", "starter_deck_cards", "promo_cards", "don_cards"),
        source_category="card_identity",
        capability_tags=(
            "card_identity",
            "structured_corroboration",
            "coverage_expansion",
            "public_api",
            "get_only",
            "no_auth",
            "courtesy_limited",
        ),
        gap_support=("source_depth_fill", "leader_profile_expand", "stale_refresh"),
        execution_adapter="optcg-api",
    ),
    "official-deck-features": MiruSourceEntry(
        source_id="official-deck-features",
        source_name="Official Recommended Deck Features",
        source_type="official-deck-feature-snapshot",
        trust_tier=1,
        trust_label=TRUST_TIER_LABELS[1],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="snapshot-backed usage/meta refresh only",
        rate_limit_hint="Use public official feature pages through explicit snapshots only.",
        backoff_policy="manual retry after snapshot refresh",
        review_state="active",
        notes="Public official recommended deck pages can support leader-profile, staple-role, and usage/meta corroboration when normalized into snapshots.",
        base_url="https://en.onepiece-cardgame.com/feature/deck/",
        snapshot_url="",
        request_spacing_seconds=2.0,
        default_confidence=0.88,
        source_category="leader_meta",
        capability_tags=("leader_usage", "meta_support", "staple_corroboration"),
        gap_support=("usage_meta_fill", "leader_profile_expand", "source_depth_fill"),
        snapshot_candidates=("data/snapshots/official_deck_features.json",),
        execution_adapter="official-deck-features",
    ),
    "official-rules-faq": MiruSourceEntry(
        source_id="official-rules-faq",
        source_name="Official Rules and FAQ",
        source_type="official-rules-faq-snapshot",
        trust_tier=1,
        trust_label=TRUST_TIER_LABELS[1],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="snapshot-backed rules corroboration only",
        rate_limit_hint="Use public official rules/FAQ pages or PDFs via explicit snapshots only.",
        backoff_policy="manual retry after snapshot refresh",
        review_state="active",
        notes="Public official rules and FAQ material can support legality-sensitive and rules-sensitive dossier corroboration when normalized into snapshots.",
        base_url="https://en.onepiece-cardgame.com/rules/",
        snapshot_url="",
        request_spacing_seconds=2.0,
        default_confidence=0.92,
        source_category="rules_legality",
        capability_tags=("rules", "faq", "legality_corroboration"),
        gap_support=("legality_recheck", "source_depth_fill"),
        snapshot_candidates=("data/snapshots/official_rules_faq.json",),
        execution_adapter="official-rules-faq",
    ),
    "official-restriction-notices": MiruSourceEntry(
        source_id="official-restriction-notices",
        source_name="Official Restrictions and Notices",
        source_type="official-restriction-snapshot",
        trust_tier=1,
        trust_label=TRUST_TIER_LABELS[1],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="snapshot-backed legality refresh only",
        rate_limit_hint="Use public official restriction or announcement pages through explicit snapshots only.",
        backoff_policy="manual retry after snapshot refresh",
        review_state="active",
        notes="Public official restriction, limitation, and announcement pages can corroborate legality-sensitive cards and review-near staples when normalized into snapshots.",
        base_url="https://en.onepiece-cardgame.com/rules/",
        snapshot_url="",
        request_spacing_seconds=2.0,
        default_confidence=0.93,
        source_category="rules_legality",
        capability_tags=("legality_corroboration", "restriction_notice"),
        gap_support=("legality_recheck",),
        snapshot_candidates=("data/snapshots/official_restriction_notices.json",),
        execution_adapter="official-restriction-notices",
    ),
    "official-errata-cards": MiruSourceEntry(
        source_id="official-errata-cards",
        source_name="Official Errata and Card Updates",
        source_type="official-errata-snapshot",
        trust_tier=1,
        trust_label=TRUST_TIER_LABELS[1],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="snapshot-backed card update refresh only",
        rate_limit_hint="Use public official errata/update pages through explicit snapshots only.",
        backoff_policy="manual retry after snapshot refresh",
        review_state="active",
        notes="Public official errata and card update pages can support print confirmation, text reconciliation, and legality-sensitive rechecks when normalized into snapshots.",
        base_url="https://en.onepiece-cardgame.com/rules/",
        snapshot_url="",
        request_spacing_seconds=2.0,
        default_confidence=0.93,
        source_category="card_identity",
        capability_tags=("errata", "card_text_confirmation", "print_confirmation"),
        gap_support=("source_depth_fill", "legality_recheck", "stale_refresh"),
        snapshot_candidates=("data/snapshots/official_errata_cards.json",),
        execution_adapter="official-errata-cards",
    ),
    # Pre-approved governed community lanes (secondary corroboration only; never over official truth).
    "limitless": MiruSourceEntry(
        source_id="limitless",
        source_name="Limitless TCG",
        source_type="community-deck-meta",
        trust_tier=2,
        trust_label=TRUST_TIER_LABELS[2],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="snapshot-backed deck and meta corroboration only",
        rate_limit_hint="Use local normalized snapshots or operator-provided JSON; respect site policies.",
        backoff_policy="fail closed on access or robots uncertainty",
        review_state="active",
        notes=(
            "Governed lane for deck lists, archetypes, and usage signals from Limitless public surfaces. "
            "Reference-safe / corroboration only — never authoritative vs Bandai official data."
        ),
        base_url="https://limitlesstcg.com/",
        snapshot_url="",
        request_spacing_seconds=3.0,
        default_confidence=0.68,
        domain="limitlesstcg.com",
        allowed_access=ALLOWED_ACCESS_PUBLIC_PAGE,
        source_category="deck_meta",
        data_types=("decklist", "usage", "archetype"),
        capability_tags=("usage_meta_fill", "leader_profile_expand", "decklist_composition", "archetype_signal"),
        gap_support=("usage_meta_fill", "leader_profile_expand", "source_depth_fill", "stale_refresh"),
        snapshot_candidates=("data/snapshots/limitless_tcg.json",),
        execution_adapter="community-structured",
    ),
    "optcg-gg": MiruSourceEntry(
        source_id="optcg-gg",
        source_name="optcg.gg",
        source_type="community-deck-meta",
        trust_tier=2,
        trust_label=TRUST_TIER_LABELS[2],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="snapshot-backed deck and meta corroboration only",
        rate_limit_hint="Use local normalized snapshots; bounded explicit access only.",
        backoff_policy="fail closed on policy uncertainty",
        review_state="active",
        notes=(
            "Governed lane for optcg.gg deck and meta signals. Secondary corroboration only; "
            "does not outrank official or structured API reference rows."
        ),
        base_url="https://optcg.gg/",
        snapshot_url="",
        request_spacing_seconds=3.0,
        default_confidence=0.66,
        domain="optcg.gg",
        allowed_access=ALLOWED_ACCESS_PUBLIC_PAGE,
        source_category="deck_meta",
        data_types=("decklist", "usage", "archetype"),
        capability_tags=("usage_meta_fill", "decklist_composition", "archetype_signal", "leader_profile_expand"),
        gap_support=("usage_meta_fill", "leader_profile_expand", "source_depth_fill", "stale_refresh"),
        snapshot_candidates=("data/snapshots/optcg_gg.json",),
        execution_adapter="community-structured",
    ),
    "onepiece-cardgame-dev": MiruSourceEntry(
        source_id="onepiece-cardgame-dev",
        source_name="onepiece-cardgame.dev",
        source_type="community-card-reference",
        trust_tier=3,
        trust_label=TRUST_TIER_LABELS[3],
        enabled=True,
        fetch_mode="snapshot-json",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="snapshot-backed reference corroboration only",
        rate_limit_hint="Use local snapshots; treat as advisory vs official card text.",
        backoff_policy="fail closed on ambiguity",
        review_state="active",
        notes=(
            "Reference-style community card database lane. Use for coverage hints and weak corroboration; "
            "never preferred over official-cardlist or optcg-api structured rows."
        ),
        base_url="https://onepiece-cardgame.dev/",
        snapshot_url="",
        request_spacing_seconds=3.0,
        default_confidence=0.55,
        domain="onepiece-cardgame.dev",
        allowed_access=ALLOWED_ACCESS_PUBLIC_PAGE,
        source_category="card_reference",
        data_types=("card", "reference"),
        capability_tags=("card_identity", "secondary_corroboration", "coverage_expansion"),
        gap_support=("source_depth_fill", "stale_refresh"),
        snapshot_candidates=("data/snapshots/onepiece_cardgame_dev.json",),
        execution_adapter="community-structured",
    ),
}


def build_builtin_source_registry(*, include_expanded_builtin: bool = True) -> dict[str, MiruSourceEntry]:
    registry = dict(CORE_SOURCE_REGISTRY)
    if include_expanded_builtin:
        registry.update(EXPANDED_SOURCE_REGISTRY)
    return registry


DEFAULT_SOURCE_REGISTRY: dict[str, MiruSourceEntry] = build_builtin_source_registry(include_expanded_builtin=True)


def build_source_registry(
    extra_entries: Iterable[MiruSourceEntry] | None = None,
    *,
    approved_sources_path: Path | None = None,
    include_expanded_builtin: bool = True,
) -> dict[str, MiruSourceEntry]:
    """Build registry from built-in sources, then worktree approved_sources config, then extra_entries.
    Invalid config entries are skipped (see get_approved_sources_config_status() for errors).
    """
    registry = build_builtin_source_registry(include_expanded_builtin=include_expanded_builtin)
    approved, _ = load_approved_sources_from_config(approved_sources_path)
    for entry in approved:
        key = (entry.source_id or "").strip().lower()
        if key:
            registry[key] = entry
    for entry in extra_entries or ():
        key = (entry.source_id or "").strip().lower()
        if key:
            registry[key] = entry
    return registry


def get_source_entry(
    source_id: str,
    registry: dict[str, MiruSourceEntry] | None = None,
) -> MiruSourceEntry:
    registry = registry or DEFAULT_SOURCE_REGISTRY
    key = (source_id or "").strip().lower()
    if key in registry:
        return registry[key]
    raise KeyError(f"Unknown Miru source_id: {source_id}")


def build_unknown_source_entry(source_id: str) -> MiruSourceEntry:
    key = (source_id or "").strip().lower() or "unknown-source"
    return MiruSourceEntry(
        source_id=key,
        source_name=key.replace("-", " ").title(),
        source_type="unknown",
        trust_tier=4,
        trust_label=TRUST_TIER_LABELS[4],
        enabled=False,
        fetch_mode="manual",
        supported_fields=SUPPORTED_CARD_FIELDS,
        refresh_policy="manual review required",
        rate_limit_hint="Do not poll automatically.",
        backoff_policy="manual review only",
        review_state="manual-review-only",
        notes="Unknown source encountered during Miru ingestion.",
        request_spacing_seconds=0.0,
        default_confidence=0.25,
    )


def list_enabled_sources(
    registry: dict[str, MiruSourceEntry] | None = None,
) -> list[MiruSourceEntry]:
    registry = registry or DEFAULT_SOURCE_REGISTRY
    return [entry for entry in registry.values() if entry.enabled]
