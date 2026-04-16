"""
Backfill market_products.image_url from data/tcgcsv/*/products.json (imageUrl field).
Writes ONLY market_products.image_url and market_products.updated_at.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TCGCSV = ROOT / "data" / "tcgcsv"
DB_PATH = ROOT / "data" / "card_catalog.db"
MIN_DB_BYTES = 10 * 1024 * 1024


def load_products_payload(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        r = data.get("results")
        if isinstance(r, list):
            return r
    return []


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: {DB_PATH} not found", file=sys.stderr)
        return 1
    sz = DB_PATH.stat().st_size
    if sz < MIN_DB_BYTES:
        print(f"FAILED: {DB_PATH} is {sz} bytes (< 10MB) — aborting", file=sys.stderr)
        return 1

    numeric_dir = re.compile(r"^\d+$")
    group_folders: list[Path] = []
    total_json_records = 0
    id_to_url: dict[str, str] = {}

    for p in sorted(TCGCSV.iterdir()):
        if not p.is_dir() or not numeric_dir.match(p.name):
            continue
        pj = p / "products.json"
        if not pj.is_file():
            continue
        group_folders.append(p)
        items = load_products_payload(pj)
        total_json_records += len(items)
        for rec in items:
            if not isinstance(rec, dict):
                continue
            try:
                pid = int(rec.get("productId"))
            except (TypeError, ValueError):
                continue
            url = rec.get("imageUrl")
            if url is None or str(url).strip() == "":
                continue
            id_to_url[str(pid)] = str(url).strip()

    print(f"Group folders with products.json: {len(group_folders)}")
    print(f"Total product records read from JSON (all groups): {total_json_records}")
    print(f"Unique productId -> imageUrl entries (non-empty imageUrl): {len(id_to_url)}")

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    cur.execute(
        """
        SELECT id, market_product_id FROM market_products
        WHERE image_url IS NULL OR TRIM(image_url) = ''
        """
    )
    null_rows = cur.fetchall()

    updated = 0
    unmatched = 0
    for row_id, mpid in null_rows:
        key = str(mpid).strip()
        if not key:
            unmatched += 1
            continue
        try:
            norm = str(int(key))
        except ValueError:
            norm = key
        url = id_to_url.get(norm) or id_to_url.get(key)
        if not url:
            unmatched += 1
            continue
        cur.execute(
            """
            UPDATE market_products
            SET image_url = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (url, row_id),
        )
        updated += cur.rowcount

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM market_products WHERE image_url IS NOT NULL AND TRIM(image_url) != ''")
    n_has = cur.fetchone()[0]
    cur.execute(
        "SELECT COUNT(*) FROM market_products WHERE image_url IS NULL OR TRIM(image_url) = ''"
    )
    n_null = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM market_products")
    n_total = cur.fetchone()[0]

    print()
    print("Rows updated (was NULL/empty, now set):", updated)
    print("UNMATCHED (NULL in DB, no JSON imageUrl):", unmatched)
    print()
    print("After run:")
    print(f"  image_url NOT NULL: {n_has}")
    print(f"  image_url NULL/empty: {n_null}")
    print(f"  total market_products: {n_total}")
    print()

    cur.execute(
        """
        SELECT id, market_product_id, product_name, image_url
        FROM market_products
        WHERE image_url IS NOT NULL AND TRIM(image_url) != ''
        LIMIT 5
        """
    )
    print("Sample 5 rows with image_url:")
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        print(dict(zip(cols, row)))

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
