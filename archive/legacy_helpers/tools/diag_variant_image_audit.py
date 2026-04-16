import os
import re
import sqlite3
from pathlib import Path

ASSETS_ROOT = r"D:\Miru_Assets"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"

# Folders to skip — base card folders only
SKIP_SUBFOLDERS = {"base"}

# Collect all non-base PNG files across all set folders
variant_files = []
for set_folder in os.listdir(ASSETS_ROOT):
    set_path = os.path.join(ASSETS_ROOT, set_folder)
    if not os.path.isdir(set_path):
        continue
    for subfolder in os.listdir(set_path):
        if subfolder in SKIP_SUBFOLDERS:
            continue
        sub_path = os.path.join(set_path, subfolder)
        if not os.path.isdir(sub_path):
            continue
        for fname in os.listdir(sub_path):
            if fname.endswith(".png"):
                variant_files.append(
                    {
                        "filename": fname,
                        "current_folder": set_folder,
                        "current_subfolder": subfolder,
                        "current_path": os.path.join(sub_path, fname),
                    }
                )

print(f"Total non-base variant files found: {len(variant_files)}")

# Load card_variants joined to cards for canonical_code lookup
conn = sqlite3.connect(DB_PATH.as_uri() + "?mode=ro", uri=True)
cur = conn.execute(
    """
    SELECT
        c.canonical_code,
        cv.variant_key,
        cv.is_sp,
        cv.is_tr,
        cv.is_alt,
        cv.is_manga_rare,
        cv.is_golden_manga_rare,
        cv.is_illustration_rare,
        cv.is_promo,
        cv.distribution_product_key,
        cv.release_set_name
    FROM card_variants cv
    JOIN cards c ON cv.card_id = c.id
    """
)
variant_rows = {}
for row in cur.fetchall():
    code = row[0]
    if code not in variant_rows:
        variant_rows[code] = []
    variant_rows[code].append(row)
conn.close()

print(f"Card variant DB rows loaded: {len(variant_rows)} unique codes")

# Determine correct destination for each file


def get_target_subfolder(filename):
    name = filename.replace(".png", "")
    if "_sp" in name:
        return "sp"
    if "_tr" in name:
        return "tr"
    if "_gmr" in name:
        return "alt_art"
    if "_mr" in name:
        return "alt_art"
    if "_ir" in name:
        return "alt_art"
    if "_alt" in name:
        return "alt_art"
    if re.search(r"_r\d+$", name):
        return "reprints"
    if re.search(r"_p\d+$", name):
        return "reprints"
    return "unknown"


def get_base_set(filename):
    # Extract set prefix from card code in filename
    m = re.match(r"^([A-Z0-9]+-\d+)", filename)
    if m:
        code = m.group(1)
        parts = code.split("-")
        return parts[0]
    return None


results = {
    "already_correct": [],
    "needs_move": [],
    "no_db_match": [],
    "unknown_type": [],
}

for f in variant_files:
    fname = f["filename"]
    base_set = get_base_set(fname)
    target_sub = get_target_subfolder(fname)

    if target_sub == "unknown":
        results["unknown_type"].append(f)
        continue

    if base_set is None:
        results["no_db_match"].append(f)
        continue

    correct_folder = base_set
    correct_subfolder = target_sub

    if (
        f["current_folder"] == correct_folder
        and f["current_subfolder"] == correct_subfolder
    ):
        results["already_correct"].append(f)
    else:
        results["needs_move"].append(
            {
                **f,
                "target_folder": correct_folder,
                "target_subfolder": correct_subfolder,
            }
        )

print()
print(f"Already in correct location: {len(results['already_correct'])}")
print(f"Needs to move: {len(results['needs_move'])}")
print(f"No DB match / unknown code: {len(results['no_db_match'])}")
print(f"Unknown variant type: {len(results['unknown_type'])}")
print()

print("--- NEEDS MOVE (first 30) ---")
for r in results["needs_move"][:30]:
    print(
        f"  {r['filename']}: {r['current_folder']}\\{r['current_subfolder']} → "
        f"{r['target_folder']}\\{r['target_subfolder']}"
    )

print()
print("--- UNKNOWN TYPE (all) ---")
for r in results["unknown_type"]:
    print(f"  {r['filename']} in {r['current_folder']}\\{r['current_subfolder']}")
