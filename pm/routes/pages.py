from flask import Blueprint, Response, send_from_directory

from services.images import (
    MIRU_ASSETS,
    _legacy_thumbs_to_set_base_png,
    _normalize_rel_image_path,
)

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/static/assets/thumbs/<path:filename>")
def miru_clean_static_thumbs(filename: str):
    normalized = _normalize_rel_image_path(filename)
    if not normalized or any(part == ".." for part in normalized.split("/")):
        return Response("Bad request", status=400)
    return send_from_directory(str(MIRU_ASSETS), normalized)


@pages_bp.get("/img/<path:filename>")
def serve_catalog_variant_image(filename: str):
    normalized = _normalize_rel_image_path(filename)
    if not normalized or any(part == ".." for part in normalized.split("/")):
        return Response("Bad request", status=400)

    legacy = _legacy_thumbs_to_set_base_png(normalized)
    if legacy is not None:
        return send_from_directory(str(legacy.parent), legacy.name)

    # Only serve from D:\Miru_Assets - no F:\OPTCG_Images fallback
    if MIRU_ASSETS.is_dir() and (MIRU_ASSETS / normalized).is_file():
        return send_from_directory(str(MIRU_ASSETS), normalized)
    return Response("Not found", status=404)
