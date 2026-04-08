import sqlite3, csv

db = sqlite3.connect("data/card_catalog.db")
db.row_factory = sqlite3.Row

rows = db.execute(
    """
    SELECT c.canonical_code, cv.variant_key, cv.variant_label, cv.print_id,
           cv.release_set_name, cv.image_path, cv.is_base, cv.is_sp, cv.is_tr,
           cv.is_alt, cv.is_manga_rare, cv.is_golden_manga_rare, cv.is_illustration_rare
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    WHERE cv.source = 'official-cardlist'
      AND c.id IN (
          SELECT card_id FROM card_variants
          WHERE source = 'official-cardlist'
          GROUP BY card_id HAVING COUNT(*) > 1
      )
    ORDER BY c.canonical_code, cv.id
"""
).fetchall()

with open("variant_recon.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows([dict(r) for r in rows])

print(f"Done - {len(rows)} rows written to variant_recon.csv")
