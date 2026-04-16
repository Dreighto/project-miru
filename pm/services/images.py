import os
from pathlib import Path
from urllib.parse import quote

MIRU_ASSETS = Path(os.getenv("PROJECT_MIRU_CLEAN_THUMB_ROOT", r"D:\\Miru_Assets"))
MIRU_RUNTIME_IMAGES_ROOT = Path(
    os.getenv("MIRU_RUNTIME_IMAGES_ROOT", r"D:\\Miru_Assets")
)

def _normalize_rel_image_path(path_text: str) -> str:
    return str(path_text or "").replace("\\", "/").strip().lstrip("/")


def _candidate_thumb_names(code: str) -> list[str]:
    code_u = str(code or "").strip().upper()
    if not code_u:
        return []
    return [
        f"{code_u}.webp",
        f"{code_u}.png",
        f"{code_u}.jpg",
        f"{code_u}.jpeg",
    ]


def choose_leader_art_src(code: str) -> str:
    """
    Returns the URL for a leader's cropped art image stored at
    D:\\Miru_Assets\\leader_crops\\<CODE>.png (SSD, fast).
    Served via the existing /static/assets/thumbs/ route because
    leader_crops lives inside MIRU_ASSETS.
    Falls back to empty string when the crop file does not exist yet.
    """
    code_u = str(code or "").strip().upper()
    if not code_u:
        return ""
    return f"/static/assets/thumbs/leader_crops/{quote(code_u)}.png"

def choose_thumbnail_src(
    name: str,
    code: str,
    catalog_entry: dict | None = None,
    width: int = 320,
    is_leader: bool = False,
) -> str:
    _ = name
    code_u = str(code or "").strip().upper()
    if not code_u:
        return ""

    set_prefix = code_u.split("-", 1)[0]

    # Check thumbs subfolder first (small WebP for grid/drawer)
    thumb_candidate = MIRU_ASSETS / set_prefix / "base" / "thumbs" / f"{code_u}.webp"
    if thumb_candidate.is_file():
        rel = f"{set_prefix}/base/thumbs/{code_u}.webp"
        return f"/static/assets/thumbs/{quote(rel)}"

    # Fall back to full-size base art
    for ext in (".jpg", ".png", ".webp", ".jpeg"):
        candidate = MIRU_ASSETS / set_prefix / "base" / f"{code_u}{ext}"
        if candidate.is_file():
            rel = f"{set_prefix}/base/{code_u}{ext}"
            return f"/static/assets/thumbs/{quote(rel)}"

    # Fall back to catalog_image_src if present
    entry = catalog_entry or {}
    url = str(entry.get("catalog_image_src") or "").strip()
    if url:
        if (
            url.startswith("http://")
            or url.startswith("https://")
            or url.startswith("/")
        ):
            return url

    return ""

def _legacy_thumbs_to_set_base_png(normalized: str) -> Path | None:
    """
    Map legacy DB paths like thumbs/OP01-001.webp to on-disk base art:
    MIRU_ASSETS/<set_code>/base/<CODE>.png (set_code = segment before first '-' in the card code).
    """
    if not normalized.lower().startswith("thumbs/"):
        return None
    rest = normalized[7:].lstrip("/")
    if not rest:
        return None
    base_name = rest.split("/")[-1]
    stem = Path(base_name).stem
    if not stem or "-" not in stem:
        return None
    set_code = stem.split("-", 1)[0]
    if not set_code:
        return None
    candidate = MIRU_ASSETS / set_code / "base" / f"{stem}.png"
    if candidate.is_file():
        return candidate
    return None
