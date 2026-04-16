"""
Phase 20 – Card Discovery Worker.

Worker-side only. Do not run during page load, card lookup, or modal open.
Designed to run on a scheduled basis.

- discover_new_cards: create card_identity for new cards from permitted sources.
- verify_discovered_cards: move discovered → verified when conditions met.
- ingest_images_for_discovered_cards: ingest master images for discovered cards via provider.

Compliance: only permitted sources (source_policy_status). Phase 15 audit remains the gate before publication.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from tools.miru_dossier_store import (
    RELEASE_STATUS_PRERELEASE,
    RELEASE_STATUS_RELEASED,
    VERIFICATION_STAGE_DISCOVERED,
    VERIFICATION_STAGE_VERIFIED,
    MiruDossierStore,
    source_policy_status,
)

# One Piece TCG card code: OP + 2 digits + hyphen + 3 digits (e.g. OP01-001, OP12-050)
CARD_CODE_PATTERN = re.compile(r"^OP\d{2}-\d{3}$", re.IGNORECASE)


def is_card_code_valid(card_code: str) -> bool:
    """Return True if card_code has valid structure (e.g. OP01-001)."""
    return bool(CARD_CODE_PATTERN.match(str(card_code or "").strip().upper()))


def discover_new_cards(
    store: MiruDossierStore,
    candidates: list[dict[str, Any]],
    *,
    default_release_status: str = RELEASE_STATUS_RELEASED,
) -> dict[str, Any]:
    """
    Process candidate cards from permitted sources only.
    Creates card_identity for cards that do not yet exist (verification_stage=discovered).

    Each candidate must have: card_code, source_id.
    Optional: source_provenance (dict), card_name_jp, card_name_en, effect_text_jp, effect_text_en,
    trigger_text, color, card_type, cost, power, counter, life, rarity, set_code, set_name,
    block_icon, release_status, translated_text_en, translation_confidence, image_source.

    Returns: { created, skipped_duplicate, skipped_blocked, skipped_invalid, errors }.
    """
    result: dict[str, Any] = {
        "created": 0,
        "skipped_duplicate": 0,
        "skipped_blocked": 0,
        "skipped_invalid": 0,
        "errors": [],
    }
    for c in candidates:
        card_code = str(c.get("card_code") or "").strip().upper()
        if not card_code:
            result["errors"].append("missing_card_code")
            continue
        if not is_card_code_valid(card_code):
            result["skipped_invalid"] += 1
            continue
        source_id = str(c.get("source_id") or "").strip()
        if source_policy_status(source_id) != "permitted":
            result["skipped_blocked"] += 1
            continue
        if store.get_card_identity(card_code) is not None:
            result["skipped_duplicate"] += 1
            continue
        release_status = str(c.get("release_status") or default_release_status).strip()
        if release_status not in (RELEASE_STATUS_PRERELEASE, RELEASE_STATUS_RELEASED):
            release_status = default_release_status
        prov = c.get("source_provenance")
        if not isinstance(prov, dict):
            prov = {}
        out = store.upsert_card_identity(
            card_code,
            card_name_jp=str(c.get("card_name_jp") or "").strip(),
            card_name_en=str(c.get("card_name_en") or "").strip(),
            effect_text_jp=str(c.get("effect_text_jp") or "").strip(),
            effect_text_en=str(c.get("effect_text_en") or "").strip(),
            trigger_text=str(c.get("trigger_text") or "").strip(),
            color=str(c.get("color") or "").strip(),
            card_type=str(c.get("card_type") or "").strip(),
            cost=str(c.get("cost") or "").strip(),
            power=str(c.get("power") or "").strip(),
            counter=str(c.get("counter") or "").strip(),
            life=str(c.get("life") or "").strip(),
            rarity=str(c.get("rarity") or "").strip(),
            set_code=str(c.get("set_code") or "").strip(),
            set_name=str(c.get("set_name") or "").strip(),
            block_icon=str(c.get("block_icon") or "").strip(),
            release_status=release_status,
            translated_text_en=str(c.get("translated_text_en") or "").strip(),
            translation_confidence=float(c.get("translation_confidence") or 0),
            source_id=source_id,
            source_provenance=prov,
            image_source=str(c.get("image_source") or "").strip(),
        )
        if out.get("stored"):
            result["created"] += 1
        else:
            result["errors"].append(out.get("reason", "unknown"))
    return result


def verify_discovered_cards(
    store: MiruDossierStore,
    *,
    limit: int = 200,
    require_image: bool = False,
) -> dict[str, Any]:
    """
    Move cards from discovered → verified when:
    - card_code structure is valid
    - required fields populated (card_name_jp or card_name_en, set_code)
    - image: verified or acceptable placeholder (has master image with quality clean/clear_sample/acceptable).
      If require_image=True, at least one of these must be present; otherwise we allow no image.

    Does not run publication audit or publish; that remains a separate step.
    Returns: { verified, skipped_no_name, skipped_no_set, skipped_no_image, skipped_invalid }.
    """
    result: dict[str, Any] = {
        "verified": 0,
        "skipped_no_name": 0,
        "skipped_no_set": 0,
        "skipped_no_image": 0,
        "skipped_invalid": 0,
    }
    codes = store.list_cards_by_verification_stage(VERIFICATION_STAGE_DISCOVERED, limit=limit)
    for card_code in codes:
        if not is_card_code_valid(card_code):
            result["skipped_invalid"] += 1
            continue
        row = store.get_card_identity(card_code)
        if not row:
            continue
        name_jp = str(row.get("card_name_jp") or "").strip()
        name_en = str(row.get("card_name_en") or "").strip()
        if not name_jp and not name_en:
            result["skipped_no_name"] += 1
            continue
        set_code = str(row.get("set_code") or "").strip()
        if not set_code:
            result["skipped_no_set"] += 1
            continue
        if require_image:
            img = store.get_card_master_image(card_code)
            if not img:
                result["skipped_no_image"] += 1
                continue
            q = str(img.get("image_quality") or "").strip().lower()
            if q not in ("clean", "clear_sample", "acceptable"):
                result["skipped_no_image"] += 1
                continue
        store.set_verification_stage(card_code, VERIFICATION_STAGE_VERIFIED)
        result["verified"] += 1
    return result


def ingest_images_for_discovered_cards(
    store: MiruDossierStore,
    *,
    limit: int = 200,
    image_provider: Callable[[str], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """
    For cards in verification_stage=discovered (and optionally verified without image),
    attempt to ingest a master image via image_provider(card_code).

    image_provider(card_code) should return a dict with:
    - master_image_path, thumbnail_path, full_size_modal_path (optional),
    - image_quality (clean | clear_sample | acceptable),
    - watermark_status (none | sample),
    - image_source_url (optional), image_verified (bool, optional).

    If only a Sample watermark image is available, use image_quality=clear_sample, watermark_status=sample.
    Returns: { ingested, skipped_no_provider, skipped_not_better, skipped_no_data }.
    """
    result: dict[str, Any] = {
        "ingested": 0,
        "skipped_no_provider": 0,
        "skipped_not_better": 0,
        "skipped_no_data": 0,
    }
    if not image_provider:
        result["skipped_no_provider"] = limit
        return result
    codes = store.list_cards_by_verification_stage(VERIFICATION_STAGE_DISCOVERED, limit=limit)
    for card_code in codes:
        data = image_provider(card_code)
        if not data or not isinstance(data, dict):
            result["skipped_no_data"] += 1
            continue
        path = str(data.get("master_image_path") or "").strip()
        url = str(data.get("master_image_url") or "").strip()
        if not path and not url:
            result["skipped_no_data"] += 1
            continue
        quality = str(data.get("image_quality") or "acceptable").strip().lower()
        if quality not in ("clean", "clear_sample", "acceptable"):
            quality = "acceptable"
        watermark = str(data.get("watermark_status") or "none").strip().lower()
        if watermark not in ("none", "sample"):
            watermark = "none"
        out = store.upsert_card_master_image(
            card_code,
            master_image_path=path,
            master_image_url=url,
            thumbnail_path=str(data.get("thumbnail_path") or "").strip(),
            full_size_modal_path=str(data.get("full_size_modal_path") or "").strip(),
            image_quality=quality,
            watermark_status=watermark,
            image_source_url=str(data.get("image_source_url") or "").strip(),
            image_verified=bool(data.get("image_verified")),
            aspect_ratio_preserved=True,
        )
        if out.get("stored"):
            result["ingested"] += 1
        elif out.get("reason") == "not_better":
            result["skipped_not_better"] += 1
        else:
            result["skipped_no_data"] += 1
    return result


def run_discovery_cycle(
    store: MiruDossierStore,
    candidates: list[dict[str, Any]],
    *,
    image_provider: Callable[[str], dict[str, Any] | None] | None = None,
    verify_limit: int = 200,
    ingest_limit: int = 200,
    default_release_status: str = RELEASE_STATUS_RELEASED,
) -> dict[str, Any]:
    """
    Run one discovery cycle: discover_new_cards → ingest_images_for_discovered_cards → verify_discovered_cards.
    Does not run publication audit or publish_card_intelligence; call those separately after audit.
    """
    discover = discover_new_cards(store, candidates, default_release_status=default_release_status)
    ingest = ingest_images_for_discovered_cards(store, limit=ingest_limit, image_provider=image_provider)
    verify = verify_discovered_cards(store, limit=verify_limit, require_image=False)
    return {
        "discover": discover,
        "ingest": ingest,
        "verify": verify,
    }
