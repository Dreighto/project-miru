from pathlib import Path
from PIL import Image
import sys

ASSETS_ROOT = Path(r"D:\Miru_Assets")
WEBP_QUALITY = 82
SKIP_IF_EXISTS = True

def convert():
    converted = 0
    skipped = 0
    failed = 0
    total_saved_bytes = 0

    # Walk all <SET>/base/ folders
    for png_path in sorted(ASSETS_ROOT.rglob("base/*.png")):
        webp_path = png_path.with_suffix(".webp")

        if SKIP_IF_EXISTS and webp_path.exists():
            skipped += 1
            continue

        try:
            with Image.open(png_path) as img:
                # Preserve transparency if present
                if img.mode in ("RGBA", "LA"):
                    img.save(webp_path, "WEBP", quality=WEBP_QUALITY, lossless=False)
                else:
                    img = img.convert("RGB")
                    img.save(webp_path, "WEBP", quality=WEBP_QUALITY)

            orig_size = png_path.stat().st_size
            webp_size = webp_path.stat().st_size
            saved = orig_size - webp_size
            total_saved_bytes += saved
            converted += 1

            if converted % 100 == 0:
                print(f"  {converted} converted... ({total_saved_bytes / 1_048_576:.1f} MB saved so far)")

        except Exception as e:
            print(f"  FAILED: {png_path.name} — {e}")
            failed += 1

    print(f"\nDone.")
    print(f"  Converted : {converted}")
    print(f"  Skipped   : {skipped} (already had .webp)")
    print(f"  Failed    : {failed}")
    print(f"  Space saved: {total_saved_bytes / 1_048_576:.1f} MB")

if __name__ == "__main__":
    print(f"Converting PNGs to WebP in {ASSETS_ROOT}")
    print(f"Quality: {WEBP_QUALITY}, Skip existing: {SKIP_IF_EXISTS}\n")
    convert()
