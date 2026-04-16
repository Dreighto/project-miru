import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
conn = sqlite3.connect(str(ROOT / "data" / "card_catalog.db"))
conn.row_factory = sqlite3.Row

print("=== card_variants for OP01-001 ===")
rows = conn.execute(
    """
    SELECT id, variant_key, print_id, is_base, is_sp, release_set_code,
           distribution_product_key, image_path, variant_label
    FROM card_variants
    WHERE print_id LIKE 'OP01-001%'
    ORDER BY variant_key
"""
).fetchall()
for r in rows:
    print(dict(r))

print()
print("=== printing_market_map for OP01-001 variants ===")
rows = conn.execute(
    """
    SELECT pmm.printing_id, pmm.market_product_id, pmm.mapping_confidence,
           pmm.mapping_method, mp.market_number, mp.market_variant_label,
           mp.market_set_code, mpr.market_price, mpr.subtype_name
    FROM printing_market_map pmm
    JOIN card_variants cv ON cv.id = pmm.printing_id
    JOIN market_products mp ON mp.id = pmm.market_product_id
    LEFT JOIN market_prices mpr ON mpr.market_product_fk = mp.id
    WHERE cv.print_id LIKE 'OP01-001%'
    ORDER BY cv.variant_key
"""
).fetchall()
for r in rows:
    print(dict(r))

conn.close()
