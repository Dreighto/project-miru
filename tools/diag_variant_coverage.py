"""Read-only coverage report for card_variants in card_catalog.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"

Q1 = """
SELECT variant_key, COUNT(*) as cnt
FROM card_variants
GROUP BY variant_key
ORDER BY cnt DESC
"""

Q2 = """
SELECT
  CASE
    WHEN image_path LIKE 'thumbs/%' THEN 'thumbs/ (legacy)'
    WHEN image_path LIKE '%/base/%' THEN 'base'
    WHEN image_path LIKE '%/parallel/%' THEN 'parallel'
    WHEN image_path LIKE '%/sp/%' THEN 'sp'
    WHEN image_path LIKE '%/tr/%' THEN 'tr'
    WHEN image_path LIKE '%/alt_art/%' THEN 'alt_art'
    WHEN image_path = '' OR image_path IS NULL THEN 'missing'
    ELSE 'other'
  END as folder_type,
  COUNT(*) as cnt
FROM card_variants
GROUP BY folder_type
ORDER BY cnt DESC
"""

Q3 = """
SELECT
  CASE WHEN tcgplayer_product_id IS NULL THEN 'no_tcgplayer_id'
       ELSE 'has_tcgplayer_id' END as status,
  COUNT(*) as cnt
FROM card_variants
GROUP BY status
"""

Q4 = """
SELECT
  CASE WHEN pmm.printing_id IS NULL THEN 'no_price_mapping'
       ELSE 'has_price_mapping' END as status,
  COUNT(*) as cnt
FROM card_variants cv
LEFT JOIN printing_market_map pmm ON pmm.printing_id = cv.id
GROUP BY status
"""

Q5 = """
SELECT variant_key, COUNT(*) as cnt
FROM card_variants
WHERE image_path LIKE 'thumbs/%'
GROUP BY variant_key
ORDER BY cnt DESC
"""


def run(cur: sqlite3.Cursor, title: str, sql: str) -> None:
    print("=" * 72)
    print(title)
    print("=" * 72)
    cur.execute(sql)
    cols = [c[0] for c in (cur.description or ())]
    for row in cur.fetchall():
        print(dict(zip(cols, row)))
    print()


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    cur = conn.cursor()

    run(cur, "1. Count by variant_key", Q1)
    run(cur, "2. Count by image_path folder_type", Q2)
    run(cur, "3. tcgplayer_product_id populated vs null", Q3)
    run(cur, "4. printing_market_map coverage", Q4)
    run(cur, "5. Legacy thumbs/ — variant_key counts", Q5)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
