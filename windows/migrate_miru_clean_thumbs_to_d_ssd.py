"""
One-off / repeatable: move Miru card images from D:\\docker\\tcg-watcher\\data\\miru_images
into D:\\Miru_Assets (SSD). Excludes startup-logs and non-data paths.

Run: python windows/migrate_miru_clean_thumbs_to_d_ssd.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

SOURCE_DIRS = [
    Path(r"D:\docker\tcg-watcher\data\miru_images"),
]
DEST = Path(r"D:\Miru_Assets")
EXT_OK = {".webp", ".png", ".jpg", ".jpeg"}


def move_one(src: Path, dest_dir: Path) -> Path:
    dest = dest_dir / src.name
    if dest.exists():
        stem, suf = dest.stem, dest.suffix
        n = 2
        while True:
            cand = dest_dir / f"{stem}__dup{n}{suf}"
            if not cand.exists():
                dest = cand
                break
            n += 1
    shutil.move(str(src), str(dest))
    return dest


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "backs").mkdir(exist_ok=True)
    moved: list[tuple[str, str]] = []
    for root in SOURCE_DIRS:
        if not root.is_dir():
            print("skip missing", root)
            continue
        for f in sorted(root.iterdir()):
            if not f.is_file() or f.suffix.lower() not in EXT_OK:
                continue
            dest = move_one(f, DEST)
            moved.append((str(f), str(dest)))
            print("moved", f, "->", dest)
    print("total", len(moved))


if __name__ == "__main__":
    main()
