from pathlib import Path
from PIL import Image

ASSETS_ROOT = Path(r"D:\Miru_Assets")

def diagnose():
    sizes = []
    for img_path in sorted(ASSETS_ROOT.rglob("base/*.webp"))[:50]:
        try:
            with Image.open(img_path) as img:
                w, h = img.size
                file_kb = img_path.stat().st_size / 1024
                sizes.append((w, h, file_kb, img_path.name))
                print(f"{img_path.name}: {w}x{h}px, {file_kb:.1f}KB")
        except Exception as e:
            print(f"FAILED: {img_path.name} - {e}")

    if sizes:
        avg_w = sum(s[0] for s in sizes) / len(sizes)
        avg_h = sum(s[1] for s in sizes) / len(sizes)
        avg_kb = sum(s[2] for s in sizes) / len(sizes)
        print(f"\nAverage: {avg_w:.0f}x{avg_h:.0f}px, {avg_kb:.1f}KB")

if __name__ == "__main__":
    diagnose()
