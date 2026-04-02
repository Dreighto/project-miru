import os
import sqlite3
from collections import defaultdict
from pathlib import Path

asset_base = Path(r"D:\Miru_Assets")

# Scan all non-base folders
variant_folders = ["sp", "tr", "alt_art", "parallel", "_unclassified/parallel", "leader_crops"]
all_files = {}

for vfolder in variant_folders:
    vpath = (
        asset_base / vfolder
        if "/" not in vfolder
        else asset_base / vfolder.split("/")[0] / vfolder.split("/")[1]
    )
    if vpath.exists() and vpath.is_dir():
        for fname in vpath.glob("*.png"):
            all_files[fname.name] = str(vpath.relative_to(asset_base))

print(f"Total non-base image files found: {len(all_files)}")
print()

# Load DB variant data
conn = sqlite3.connect("data/card_catalog.db")
cur = conn.execute(
    """
    SELECT c.canonical_code, cv.variant_key, cv.release_set_name,
           cv.distribution_product_key, cv.image_path
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    WHERE cv.is_base = 0
    ORDER BY c.canonical_code, cv.variant_key
    """
)
db_variants = cur.fetchall()
conn.close()

print(f"Total variant rows in DB: {len(db_variants)}")
print()

# Build maps
db_by_code = defaultdict(list)
for code, vkey, rname, dpk, ipath in db_variants:
    db_by_code[code].append(
        {
            "variant_key": vkey,
            "release_set_name": rname,
            "distribution_product_key": dpk,
            "image_path": ipath,
        }
    )

# Categorize files
matched = {}
unmatched_files = []

for fname, folder in all_files.items():
    # Extract card code from filename
    base = fname.replace(".png", "")
    # Handle _pN, _p, _sp, _tr, _ir, _mr, _gmr suffixes
    code = None
    for sep in ["_p", "_sp", "_tr", "_ir", "_mr", "_gmr"]:
        if sep in base:
            code = base.split(sep)[0]
            break

    if code and code in db_by_code:
        matched[fname] = {
            "code": code,
            "folder": folder,
            "db_variants": db_by_code[code],
        }
    else:
        unmatched_files.append((fname, folder, code))

print(f"Files matched to DB: {len(matched)}")
print(f"Files NOT matched to DB: {len(unmatched_files)}")
print()

if unmatched_files:
    print("=== UNMATCHED FILES ===")
    for fname, folder, code in unmatched_files[:50]:  # First 50
        print(f"  {folder}/{fname} (code: {code})")
    if len(unmatched_files) > 50:
        print(f"  ... and {len(unmatched_files) - 50} more")
    print()

# Analyze matched files — what variant types do they need?
variant_type_needed = defaultdict(list)
null_dpk = defaultdict(int)

for fname, data in matched.items():
    code = data["code"]
    folder = data["folder"]
    variants = data["db_variants"]

    for v in variants:
        vkey = v["variant_key"]
        dpk = v["distribution_product_key"]
        if dpk is None:
            null_dpk[code] += 1

    if any(v["distribution_product_key"] is None for v in variants):
        variant_type_needed[code].append((fname, folder))

print(f"Cards with matched files but NULL distribution_product_key: {len(null_dpk)}")
print("Sample (first 20):")
for code in list(null_dpk.keys())[:20]:
    print(f"  {code}")
print()

# Summary: folder distribution
folder_counts = defaultdict(int)
for folder in all_files.values():
    folder_counts[folder] += 1

print("=== CURRENT FOLDER DISTRIBUTION ===")
for folder in sorted(folder_counts.keys()):
    print(f"  {folder}: {folder_counts[folder]}")
