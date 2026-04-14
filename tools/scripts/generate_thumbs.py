from pathlib import Path
from PIL import Image

ASSETS_ROOT = Path(r"D:\Miru_Assets")
THUMB_WIDTH = 180
WEBP_QUALITY = 82
SKIP_IF_EXISTS = True

def generate():
    converted = 0
    skipped = 0
    failed = 0

    for webp_path in sorted(ASSETS_ROOT.rglob("base/*.webp")):
        # Skip files already in a thumbs subfolder
        if webp_path.parent.name == "thumbs":
            continue

        thumb_dir = webp_path.parent / "thumbs"
        thumb_dir.mkdir(exist_ok=True)
        thumb_path = thumb_dir / webp_path.name

        if SKIP_IF_EXISTS and thumb_path.exists():
            skipped += 1
            continue

        try:
            with Image.open(webp_path) as img:
                w, h = img.size
                ratio = THUMB_WIDTH / w
                new_h = int(h * ratio)
                img = img.resize((THUMB_WIDTH, new_h), Image.LANCZOS)
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGB")
                img.save(thumb_path, "WEBP", quality=WEBP_QUALITY)
            converted += 1
            if converted % 100 == 0:
                print(f"  {converted} thumbnails generated...")
        except Exception as e:
            print(f"  FAILED: {webp_path.name} — {e}")
            failed += 1

    print(f"\nDone.")
    print(f"  Generated : {converted}")
    print(f"  Skipped   : {skipped} (already existed)")
    print(f"  Failed    : {failed}")

if __name__ == "__main__":
    print(f"Generating {THUMB_WIDTH}px thumbnails in {ASSETS_ROOT}")
    print(f"Output: <SET>/base/thumbs/<CODE>.webp\n")
    generate()
