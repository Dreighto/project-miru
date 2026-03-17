from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


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

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


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


DEFAULT_SOURCE_REGISTRY: dict[str, MiruSourceEntry] = {
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
    ),
}


def build_source_registry(
    extra_entries: Iterable[MiruSourceEntry] | None = None,
    *,
    approved_sources_path: Path | None = None,
) -> dict[str, MiruSourceEntry]:
    """Build registry from built-in sources, then worktree approved_sources config, then extra_entries.
    Invalid config entries are skipped (see get_approved_sources_config_status() for errors).
    """
    registry = dict(DEFAULT_SOURCE_REGISTRY)
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
