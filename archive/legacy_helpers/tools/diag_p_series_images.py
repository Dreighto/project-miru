import os
import sqlite3

conn = sqlite3.connect("data/card_catalog.db")
cur = conn.execute(
    """
    SELECT canonical_code, card_name
    FROM cards
    WHERE canonical_code LIKE 'P-%'
    ORDER BY canonical_code
    """
)
rows = cur.fetchall()
conn.close()

asset_base = r"D:\Miru_Assets\P\base"
has_image = []
no_image = []

for code, name in rows:
    fpath = os.path.join(asset_base, code + ".png")
    if os.path.isfile(fpath):
        has_image.append((code, name))
    else:
        no_image.append((code, name))

print("Total P-series cards in DB:", len(rows))
print("Has image:", len(has_image))
print("No image:", len(no_image))
print()
print("--- HAS IMAGE ---")
for r in has_image:
    print(r)
print()
print("--- NO IMAGE ---")
for r in no_image:
    print(r)
