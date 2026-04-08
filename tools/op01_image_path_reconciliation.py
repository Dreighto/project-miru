"""
OP01 Image Path Reconciliation Diagnostic — READ-ONLY

Inspects the 121 IMAGE_FILE_MISSING rows from the OP01 certification scan
and attempts to locate the real on-disk file via path normalization transforms.

Outputs: data/overlays/op01_image_path_reconciliation.csv
"""

import sqlite3
import csv
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
MIRU_ASSETS = Path(r"D:\Miru_Assets")
CERT_CSV = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_certification_scan.csv")
OUTPUT_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_image_path_reconciliation.csv")

FIELDS = [
    "card_code", "printing_id", "variant_key",
    "current_local_path", "candidate_paths_checked",
    "matched_path", "file_exists", "reconciliation_type",
]


def normalize_candidates(local_path: str) -> list[tuple[str, str]]:
    """
    Given a legacy local_path, return a list of (candidate_path, transform_label) tuples
    representing plausible on-disk locations.
    """
    candidates = []
    p = Path(local_path)
    parts = list(p.parts)
    stem = p.stem
    ext = p.suffix  # e.g. .webp

    # Transform 1: remove 'thumbs/', keep extension
    #   OP01/base/thumbs/OP01-001.webp -> OP01/base/OP01-001.webp
    if "thumbs" in parts:
        no_thumbs = [x for x in parts if x != "thumbs"]
        candidates.append((str(Path(*no_thumbs)), "THUMBS_REMOVED_SAME_EXT"))

    # Transform 2: remove 'thumbs/' + convert .webp -> .png
    #   OP01/base/thumbs/OP01-001.webp -> OP01/base/OP01-001.png
    if "thumbs" in parts and ext.lower() == ".webp":
        no_thumbs = [x for x in parts if x != "thumbs"]
        fixed = Path(*no_thumbs).with_suffix(".png")
        candidates.append((str(fixed), "THUMBS_TO_BASE_FIX"))

    # Transform 3: extension only — .webp -> .png, same folder
    #   OP01/base/thumbs/OP01-001.webp -> OP01/base/thumbs/OP01-001.png
    if ext.lower() == ".webp":
        candidates.append((str(p.with_suffix(".png")), "EXTENSION_FIX"))

    # Transform 4: remove 'thumbs/' + convert .webp -> .jpg
    if "thumbs" in parts and ext.lower() == ".webp":
        no_thumbs = [x for x in parts if x != "thumbs"]
        fixed = Path(*no_thumbs).with_suffix(".jpg")
        candidates.append((str(fixed), "THUMBS_TO_BASE_JPG"))

    # Transform 5: path-only fix (strip leading separators, try as-is relative)
    candidates.append((local_path.lstrip("/"), "PATH_ONLY_FIX"))

    return candidates


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load IMAGE_FILE_MISSING rows from certification CSV
    with open(CERT_CSV, encoding="utf-8") as f:
        cert_rows = [
            r for r in csv.DictReader(f)
            if r["certification_status"] == "IMAGE_FILE_MISSING"
        ]
    print(f"IMAGE_FILE_MISSING rows from certification scan: {len(cert_rows)}")

    # Read actual image_assets.local_path from DB (read-only)
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    asset_map = {}
    for row in con.execute(
        "SELECT printing_id, local_path FROM image_assets"
    ).fetchall():
        asset_map[row["printing_id"]] = row["local_path"]
    con.close()

    counts = {}
    output_rows = []

    for r in cert_rows:
        printing_id = int(r["printing_id"])
        card_code = r["card_code"]
        variant_key = r["variant_key"]
        current_path = asset_map.get(printing_id, r["image_path"] or "")

        candidates = normalize_candidates(current_path)
        checked = []
        matched_path = None
        recon_type = "NO_MATCH_FOUND"

        for cand_path, label in candidates:
            rel = cand_path.lstrip("/")
            full = MIRU_ASSETS / rel
            exists = full.exists()
            checked.append(f"{rel} [{label}] -> {'EXISTS' if exists else 'MISSING'}")
            if exists and matched_path is None:
                matched_path = rel
                recon_type = label

        counts[recon_type] = counts.get(recon_type, 0) + 1
        output_rows.append({
            "card_code": card_code,
            "printing_id": printing_id,
            "variant_key": variant_key,
            "current_local_path": current_path,
            "candidate_paths_checked": " | ".join(checked),
            "matched_path": matched_path or "",
            "file_exists": "YES" if matched_path else "NO",
            "reconciliation_type": recon_type,
        })

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nOP01 Image Path Reconciliation — {len(output_rows)} rows")
    print("-" * 48)
    for rtype, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {rtype:<28} {count}")
    print(f"\nOutput: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
