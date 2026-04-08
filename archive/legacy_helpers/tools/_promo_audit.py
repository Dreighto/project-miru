"""Audit promo image_assets rows and P/base directory for fix planning."""
import sqlite3
import os
from pathlib import Path

DB_PATH = Path("data/card_catalog.db")
MIRU_ASSETS = Path(r"D:\Miru_Assets")
P_BASE = MIRU_ASSETS / "P" / "base"

IDS = [706,720,727,728,758,759,770,771,779,780,798,801,802,815,816,823,836,859,950,1164]

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
cur = conn.cursor()

placeholders = ",".join(str(i) for i in IDS)
cur.execute(f"""
SELECT ia.id as ia_id, ia.printing_id, ia.local_path, ia.source_url,
       ia.image_confidence, ia.checksum,
       cv.print_id, cv.variant_key, cv.variant_label
FROM image_assets ia
JOIN card_variants cv ON cv.id = ia.printing_id
WHERE ia.printing_id IN ({placeholders})
  AND ia.is_primary = 1
ORDER BY ia.printing_id
""")
rows = [dict(r) for r in cur.fetchall()]
conn.close()

print(f"Found {len(rows)} image_assets rows")
print()

# List what's in D:\Miru_Assets\P\base\
if P_BASE.is_dir():
    p_files = sorted(os.listdir(str(P_BASE)))
    print(f"D:\\Miru_Assets\\P\\base\\ contains {len(p_files)} files:")
    for f in p_files[:40]:
        sz = os.path.getsize(str(P_BASE / f))
        print(f"  {f}  ({sz} bytes)")
    if len(p_files) > 40:
        print(f"  ... and {len(p_files)-40} more")
else:
    print(f"P\\base dir does not exist: {P_BASE}")

print()
print("=== IMAGE_ASSETS ROWS ===")
for r in rows:
    # Derive what the canonical path would be if one exists
    base_num = r["print_id"].split("::")[0]  # e.g. OP01-001
    num_part = base_num.replace("OP01-", "")  # e.g. 001
    candidate_name = f"P-{num_part}.png"
    candidate_path = P_BASE / candidate_name
    candidate_exists = candidate_path.is_file()
    candidate_size = os.path.getsize(str(candidate_path)) if candidate_exists else 0

    print(f"pid={r['printing_id']} ia_id={r['ia_id']} print_id={r['print_id']} variant_label={r['variant_label']}")
    print(f"  current local_path: {r['local_path']}")
    print(f"  candidate canonical: P/base/{candidate_name}  exists={candidate_exists}  size={candidate_size}")
    print(f"  source_url: {r['source_url']}")
    print()
