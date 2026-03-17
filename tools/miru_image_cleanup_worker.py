"""
Phase 21 – Library Image Cleanup, Image Promotion, and Best-Master Refresh.

Worker-only. No request-time promotion or remote fetching.
Improves library images by upgrading when a better compliant image is available.
Uses Phase 19 quality model: clean > clear_sample > acceptable.
Preserves aspect ratio 63/88; only permitted sources may be promoted.
"""
from __future__ import annotations

from typing import Any, Callable

from tools.miru_dossier_store import (
    IMAGE_QUALITY_ACCEPTABLE,
    IMAGE_QUALITY_CLEAN,
    IMAGE_QUALITY_CLEAR_SAMPLE,
    MiruDossierStore,
    source_policy_status,
)


def _derive_thumbnail_path(master_path: str, master_url: str) -> str:
    """Derive thumbnail path from master path or URL. Preserves extension pattern."""
    base = (master_path or master_url or "").strip()
    if not base:
        return ""
    if base.endswith(".png"):
        return base[:-4] + "_thumb.png"
    if base.endswith(".jpg") or base.endswith(".jpeg"):
        return base.rsplit(".", 1)[0] + "_thumb.jpg"
    return base.rstrip("/") + "_thumb"


def _derive_full_size_path(master_path: str, master_url: str) -> str:
    """Full-size modal image: prefer master path, else master URL (same asset as master)."""
    return (master_path or master_url or "").strip()


def find_cards_needing_image_upgrade(
    store: MiruDossierStore,
    *,
    limit: int = 500,
) -> list[str]:
    """
    Return card_codes that have a master image record but are low-quality,
    due for upgrade (replacement_priority=high), or missing derivatives.
    Worker-only; uses store.list_card_codes_needing_image_upgrade.
    """
    return store.list_card_codes_needing_image_upgrade(limit=limit)


def promote_better_card_images(
    store: MiruDossierStore,
    card_code: str,
    candidate: dict[str, Any],
    source_id: str,
    *,
    require_aspect_ratio: bool = True,
) -> dict[str, Any]:
    """
    Promote a candidate image to master if it is from a permitted source,
    aspect ratio is preserved (if require_aspect_ratio), and it is strictly better.
    Phase 19 rules: clean > clear_sample > acceptable; no downgrade.
    Returns store.upsert_card_master_image result with optional key 'promoted': True/False.
    """
    resolved = str(card_code or "").strip().upper()
    if not resolved:
        return {"stored": False, "reason": "missing_card_code", "promoted": False}
    if source_policy_status(source_id) != "permitted":
        return {"stored": False, "reason": "source_not_permitted", "promoted": False}
    if require_aspect_ratio and not candidate.get("aspect_ratio_preserved", True):
        return {"stored": False, "reason": "aspect_ratio_invalid", "promoted": False}
    quality = str(candidate.get("image_quality") or IMAGE_QUALITY_ACCEPTABLE).strip().lower()
    if quality not in (IMAGE_QUALITY_CLEAN, IMAGE_QUALITY_CLEAR_SAMPLE, IMAGE_QUALITY_ACCEPTABLE):
        quality = IMAGE_QUALITY_ACCEPTABLE
    watermark = str(candidate.get("watermark_status") or "none").strip().lower()
    if watermark not in ("none", "sample"):
        watermark = "none"
    thumb = str(candidate.get("thumbnail_path") or "").strip()
    full = str(candidate.get("full_size_modal_path") or "").strip()
    master_path = str(candidate.get("master_image_path") or "").strip()
    master_url = str(candidate.get("master_image_url") or "").strip()
    if not thumb and (master_path or master_url):
        thumb = _derive_thumbnail_path(master_path, master_url)
    if not full and (master_path or master_url):
        full = _derive_full_size_path(master_path, master_url)
    out = store.upsert_card_master_image(
        resolved,
        master_image_path=master_path,
        master_image_url=master_url,
        thumbnail_path=thumb,
        full_size_modal_path=full,
        image_quality=quality,
        watermark_status=watermark,
        replacement_priority=str(candidate.get("replacement_priority") or "medium").strip(),
        image_source_url=str(candidate.get("image_source_url") or "").strip(),
        image_verified=bool(candidate.get("image_verified")),
        aspect_ratio_preserved=bool(candidate.get("aspect_ratio_preserved", True)),
    )
    out["promoted"] = out.get("stored", False)
    return out


def rebuild_card_image_derivatives(
    store: MiruDossierStore,
    card_code: str,
    *,
    thumbnail_path: str | None = None,
    full_size_modal_path: str | None = None,
) -> dict[str, Any]:
    """
    Ensure thumbnail and full-size modal paths are aligned to the current best master.
    If thumbnail_path/full_size_modal_path are provided, use them; otherwise derive from master.
    Returns { updated: bool, thumbnail_path, full_size_modal_path }.
    """
    resolved = str(card_code or "").strip().upper()
    if not resolved:
        return {"updated": False, "thumbnail_path": "", "full_size_modal_path": ""}
    row = store.get_card_master_image(resolved)
    if not row:
        return {"updated": False, "thumbnail_path": "", "full_size_modal_path": ""}
    master_path = str(row.get("master_image_path") or "").strip()
    master_url = str(row.get("master_image_url") or "").strip()
    if thumbnail_path is not None:
        thumb = str(thumbnail_path).strip()
    else:
        thumb = str(row.get("thumbnail_path") or "").strip()
        if not thumb and (master_path or master_url):
            thumb = _derive_thumbnail_path(master_path, master_url)
    if full_size_modal_path is not None:
        full = str(full_size_modal_path).strip()
    else:
        full = str(row.get("full_size_modal_path") or "").strip()
        if not full and (master_path or master_url):
            full = _derive_full_size_path(master_path, master_url)
    updated = store.update_master_image_derivatives(
        resolved,
        thumbnail_path=thumb,
        full_size_modal_path=full,
    )
    return {"updated": updated, "thumbnail_path": thumb, "full_size_modal_path": full}


def run_image_cleanup_cycle(
    store: MiruDossierStore,
    *,
    limit: int = 200,
    upgrade_candidate_provider: Callable[[str], dict[str, Any] | None] | None = None,
    rebuild_derivatives_after_promote: bool = True,
) -> dict[str, Any]:
    """
    Run one image cleanup cycle: find cards needing upgrade, optionally promote better images
    from provider (permitted sources only), then rebuild derivatives so thumb/full align to best master.
    Worker-only. Returns { promoted, rebuilt, skipped_no_candidate, skipped_not_better, skipped_blocked }.
    """
    result: dict[str, Any] = {
        "promoted": 0,
        "rebuilt": 0,
        "skipped_no_candidate": 0,
        "skipped_not_better": 0,
        "skipped_blocked": 0,
    }
    codes = find_cards_needing_image_upgrade(store, limit=limit)
    for card_code in codes:
        if upgrade_candidate_provider:
            candidate = upgrade_candidate_provider(card_code)
            if candidate and isinstance(candidate, dict):
                source_id = str(candidate.get("source_id") or "").strip()
                if source_policy_status(source_id) != "permitted":
                    result["skipped_blocked"] += 1
                else:
                    out = promote_better_card_images(store, card_code, candidate, source_id)
                    if out.get("promoted"):
                        result["promoted"] += 1
                    elif out.get("reason") == "not_better":
                        result["skipped_not_better"] += 1
                    elif out.get("reason") == "source_not_permitted":
                        result["skipped_blocked"] += 1
            else:
                result["skipped_no_candidate"] += 1
        if rebuild_derivatives_after_promote:
            rebuild = rebuild_card_image_derivatives(store, card_code)
            if rebuild.get("updated"):
                result["rebuilt"] += 1
    return result


def is_aspect_ratio_preserved(width: float | None, height: float | None, *, tolerance: float = 0.05) -> bool:
    """
    Return True if width/height ratio is within tolerance of official 63/88.
    Use when validating candidate images before promotion.
    """
    if width is None or height is None or height <= 0:
        return False
    from tools.miru_dossier_store import ASPECT_RATIO_OPTCG
    actual = width / height
    return abs(actual - ASPECT_RATIO_OPTCG) <= tolerance
