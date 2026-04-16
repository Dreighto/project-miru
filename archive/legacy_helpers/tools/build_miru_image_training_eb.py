"""Build EB01/EB02 base card images into miru_image_training (512px longest edge, PNG)."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

SOURCE_EB01 = Path(r"F:\OPTCG_Images\EB01")
SOURCE_EB02 = Path(r"F:\OPTCG_Images\EB02")
OUT_ROOT = Path(r"F:\OPTCG_Images\miru_image_training")

MAX_EDGE = 512
EXT_IN = {".jpg", ".jpeg", ".png"}
SKIP_NAMES = {"thumbs.db"}


def resize_longest_edge(img: Image.Image, max_edge: int = MAX_EDGE) -> Image.Image:
    w, h = img.size
    m = max(w, h)
    if m == 0 or m <= max_edge:
        return img
    scale = max_edge / m
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    try:
        resample = Image.Resampling.LANCZOS
    except AttributeError:
        resample = Image.LANCZOS  # type: ignore[attr-defined]
    return img.resize((nw, nh), resample)


def to_rgb_png_mode(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "RGBA":
            bg.paste(img, mask=img.split()[-1])
        else:
            bg.paste(img)
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def process_set(set_code: str, source: Path, out_dir: Path) -> tuple[int, int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    processed = skipped_exist = errors = 0
    if not source.is_dir():
        print(f"[{set_code}] SOURCE MISSING: {source}", file=sys.stderr)
        return 0, 0, 1

    files = sorted(
        p
        for p in source.iterdir()
        if p.is_file() and p.suffix.lower() in EXT_IN and p.name.lower() not in SKIP_NAMES
    )
    for src in files:
        stem = src.stem
        dest = out_dir / f"{stem}.png"
        if dest.is_file():
            print(f"[{set_code}] {src.name} -> {dest} (SKIPPED)")
            skipped_exist += 1
            continue
        try:
            with Image.open(src) as im:
                im = im.copy()
                im = to_rgb_png_mode(im)
                im = resize_longest_edge(im, MAX_EDGE)
                im.save(dest, format="PNG", optimize=True)
        except Exception as exc:
            print(f"[{set_code}] {src.name} -> ERROR: {exc}", file=sys.stderr)
            errors += 1
            continue
        print(f"[{set_code}] {src.name} -> {dest} (OK)")
        processed += 1
    return processed, skipped_exist, errors


def main() -> int:
    for set_code, source in (("EB01", SOURCE_EB01), ("EB02", SOURCE_EB02)):
        out_dir = OUT_ROOT / set_code
        print(f"=== {set_code} === source={source} out={out_dir}")
        p, s, e = process_set(set_code, source, out_dir)
        print(
            f"=== {set_code} SUMMARY: processed={p} skipped={s} errors={e} ===\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
