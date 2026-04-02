import sqlite3
import os

db_path = os.path.join("data", "card_catalog.db")
conn = sqlite3.connect(db_path)
rows = conn.execute(
    'SELECT canonical_code, card_name FROM cards WHERE variant_subtype = "sp" LIMIT 20'
).fetchall()

image_root = "F:/OPTCG_Images"

for code, name in rows:
    # Try a few common filename patterns
    found = False
    for ext in ["jpg", "png", "webp"]:
        path = os.path.join(image_root, f"{code}.{ext}")
        if os.path.exists(path):
            print(f"FOUND: {code} — {name} → {path}")
            found = True
            break
    if not found:
        print(f"MISSING: {code} — {name}")
