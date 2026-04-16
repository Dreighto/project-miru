"""
crop_leader_art.py
==================
Reads full-resolution leader card scans from F:\\OPTCG_Images,
crops the character art zone (no border, no text box, no stats),
and saves output PNGs to D:\\Miru_Assets\\leader_crops\\<CODE>.png.

Usage:
    python scripts/crop_leader_art.py            # skip existing
    python scripts/crop_leader_art.py --force    # re-crop all

Crop parameters (percentage-based, resolution-agnostic):
    left:   5%   right:  95%   top:  4%   bottom: 62%
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SOURCE_ROOT = Path(r"F:\OPTCG_Images")
OUTPUT_DIR = Path(r"D:\Miru_Assets\leader_crops")

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

CATALOG_DB = Path(
    os.getenv(
        "PROJECT_MIRU_CATALOG_DB_PATH",
        str(_PROJECT_ROOT / "data" / "card_catalog.db"),
    )
)

# ---------------------------------------------------------------------------
# Crop constants (percentages of image dimensions)
# ---------------------------------------------------------------------------
CROP_LEFT = 0.05
CROP_TOP = 0.04
CROP_RIGHT = 0.95
CROP_BOTTOM = 0.54


def get_leader_codes() -> list[str]:
    """Return all canonical leader codes from the card catalog DB."""
    if not CATALOG_DB.is_file():
        raise FileNotFoundError(f"Catalog DB not found: {CATALOG_DB}")
    con = sqlite3.connect(str(CATALOG_DB))
    try:
        cur = con.execute(
            "SELECT canonical_code FROM cards WHERE LOWER(TRIM(card_type)) = 'leader'"
        )
        rows = cur.fetchall()
    finally:
        con.close()
    return [str(r[0]).strip().upper() for r in rows if r[0]]


def find_source(code: str) -> Path | None:
    """
    Return path to the source image for the given code, or None if not found.
    Checks <SOURCE_ROOT>/<SET>/<CODE>.png then .jpg.
    SET is the prefix before the first '-'.
    """
    set_prefix = code.split("-", 1)[0].strip().upper()
    base = SOURCE_ROOT / set_prefix / code
    for ext in (".png", ".jpg"):
        candidate = base.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return None


def crop_image(src: Path, dst: Path) -> None:
    """Crop src to the character art zone and save as PNG at dst."""
    # Import here so the module loads even if Pillow is unavailable
    # (error will surface at call time, not import time)
    from PIL import Image  # type: ignore

    with Image.open(src) as im:
        w, h = im.size
        box = (
            int(w * CROP_LEFT),
            int(h * CROP_TOP),
            int(w * CROP_RIGHT),
            int(h * CROP_BOTTOM),
        )
        cropped = im.crop(box)
        dst.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(str(dst), format="PNG")


def run(force: bool = False) -> None:
    # Ensure Pillow is importable before doing any work
    try:
        from PIL import Image as _  # noqa: F401
    except ImportError:
        raise SystemExit("ERROR: Pillow is not installed. Run: pip install Pillow")

    print(f"Catalog DB : {CATALOG_DB}")
    print(f"Source root: {SOURCE_ROOT}")
    print(f"Output dir : {OUTPUT_DIR}")
    print()

    try:
        codes = get_leader_codes()
    except FileNotFoundError as exc:
        raise SystemExit(f"ERROR: {exc}")

    if not codes:
        raise SystemExit("ERROR: No leader codes found in catalog DB.")

    print(f"Leaders found: {len(codes)}")
    print()

    n_cropped = 0
    n_skipped = 0
    n_missing = 0
    n_errors = 0

    for code in sorted(codes):
        dst = OUTPUT_DIR / f"{code}.png"

        if dst.is_file() and not force:
            print(f"  SKIP    {code}")
            n_skipped += 1
            continue

        src = find_source(code)
        if src is None:
            print(f"  MISSING {code}")
            n_missing += 1
            continue

        try:
            crop_image(src, dst)
            print(f"  CROP    {code}  →  {dst}")
            n_cropped += 1
        except Exception as exc:
            print(f"  ERROR   {code}: {exc}")
            n_errors += 1

    print()
    print(
        f"Done — {n_cropped} cropped, {n_skipped} skipped, "
        f"{n_missing} missing, {n_errors} errors"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Crop leader card art from OPTCG_Images scans."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-crop even if output already exists.",
    )
    args = parser.parse_args()
    run(force=args.force)
