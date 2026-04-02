"""
Re-build printing_market_map for TCGCSV group 3188 (OP01 / Romance Dawn) only.

Deletes existing maps for all market_products in that group, then re-inserts using
the same matching rules as tools/process_tcgcsv_group.py.

Usage:
  python tools/reprocess_op01_mappings.py
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MARKET_GROUP_ID = "3188"


def utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: missing database {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    print("=" * 72)
    print("STEP 1 — market_products for group", MARKET_GROUP_ID)
    print("=" * 72)
    cur.execute(
        """
        SELECT id, market_product_id, market_number, market_variant_label
        FROM market_products
        WHERE market_group_id = ?
        ORDER BY id
        """,
        (MARKET_GROUP_ID,),
    )
    mp_rows = cur.fetchall()
    for r in mp_rows:
        print(
            f"  id={r['id']}  market_product_id={r['market_product_id']!r}  "
            f"market_number={r['market_number']!r}  market_variant_label={r['market_variant_label']!r}"
        )
    print(f"  (total {len(mp_rows)} rows)")

    print()
    print("=" * 72)
    print("STEP 2 — printing_market_map rows linked to those products")
    print("=" * 72)
    cur.execute(
        """
        SELECT pmm.id, pmm.printing_id, pmm.market_product_id, mp.market_number
        FROM printing_market_map pmm
        JOIN market_products mp ON mp.id = pmm.market_product_id
        WHERE mp.market_group_id = ?
        ORDER BY pmm.id
        """,
        (MARKET_GROUP_ID,),
    )
    pmm_rows = cur.fetchall()
    for r in pmm_rows:
        print(
            f"  pmm.id={r['id']}  printing_id={r['printing_id']}  "
            f"market_product_id={r['market_product_id']}  mp.market_number={r['market_number']!r}"
        )
    print(f"  (total {len(pmm_rows)} rows)")

    stats_deleted = 0
    stats_high = 0
    stats_medium = 0
    stats_skipped_dup = 0
    unmatched: list[tuple[int, str | None, str | None]] = []  # mp.id, market_number, market_product_id

    now = utc_now_sql()

    try:
        cur.execute("BEGIN IMMEDIATE")

        cur.execute(
            """
            DELETE FROM printing_market_map
            WHERE market_product_id IN (
                SELECT id FROM market_products WHERE market_group_id = ?
            )
            """,
            (MARKET_GROUP_ID,),
        )
        stats_deleted = cur.rowcount

        print()
        print("=" * 72)
        print("STEP 3 — DELETE complete")
        print("=" * 72)
        print(f"  Rows deleted: {stats_deleted}")

        print()
        print("=" * 72)
        print("STEP 4 — Re-insert printing_market_map")
        print("=" * 72)

        for r in mp_rows:
            mp_id = int(r["id"])
            market_number = r["market_number"]
            mn = (str(market_number).strip() if market_number is not None else "") or None

            printing_id = None
            map_confidence = None
            map_method = None

            if mn:
                cur.execute(
                    """
                    SELECT id FROM card_variants
                    WHERE print_id = ? AND is_base = 1
                    LIMIT 1
                    """,
                    (mn,),
                )
                row = cur.fetchone()
                if row:
                    printing_id = int(row[0])
                    map_confidence = "HIGH"
                    map_method = "exact_code_plus_base_flag"
                else:
                    cur.execute(
                        """
                        SELECT id FROM card_variants
                        WHERE print_id = ?
                        ORDER BY id
                        LIMIT 1
                        """,
                        (mn,),
                    )
                    row = cur.fetchone()
                    if row:
                        printing_id = int(row[0])
                        map_confidence = "MEDIUM"
                        map_method = "exact_code_set_fallback"

            if printing_id is None:
                unmatched.append((mp_id, mn, str(r["market_product_id"])))
                print(
                    f"  UNMATCHED  mp.id={mp_id}  market_product_id={r['market_product_id']!r}  "
                    f"market_number={mn!r}"
                )
                continue

            cur.execute(
                """
                SELECT 1 FROM printing_market_map
                WHERE printing_id = ? AND market_product_id = ?
                """,
                (printing_id, mp_id),
            )
            if cur.fetchone():
                stats_skipped_dup += 1
                print(
                    f"  SKIP DUP  printing_id={printing_id}  market_product_id={mp_id}  "
                    f"({map_confidence})"
                )
                continue

            cur.execute(
                """
                INSERT INTO printing_market_map (
                    printing_id, market_product_id, mapping_confidence,
                    mapping_method, mapping_notes, is_preferred, created_at, updated_at
                ) VALUES (?, ?, ?, ?, NULL, 1, ?, NULL)
                """,
                (printing_id, mp_id, map_confidence, map_method, now),
            )
            if map_confidence == "HIGH":
                stats_high += 1
            else:
                stats_medium += 1
            print(
                f"  INSERT  printing_id={printing_id}  mp.id={mp_id}  "
                f"market_number={mn!r}  {map_confidence}  {map_method}"
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    print()
    print("=" * 72)
    print("STEP 5 — SUMMARY")
    print("=" * 72)
    print(f"  Rows deleted:     {stats_deleted}")
    print(f"  Rows inserted:    {stats_high + stats_medium}  (HIGH={stats_high}, MEDIUM={stats_medium})")
    print(f"  Skipped (dup):    {stats_skipped_dup}")
    print(f"  UNMATCHED:        {len(unmatched)}")

    print()
    print("=" * 72)
    print("LIVE VERIFICATION — OP01-001% variants × group 3188 × market_prices")
    print("=" * 72)
    cur.execute(
        """
        SELECT cv.print_id, mp.market_number, mp.market_group_id,
               mp.market_variant_label, mpr.market_price
        FROM printing_market_map pmm
        JOIN card_variants cv ON cv.id = pmm.printing_id
        JOIN market_products mp ON mp.id = pmm.market_product_id
        JOIN market_prices mpr ON mpr.market_product_fk = mp.id
        WHERE mp.market_group_id = ?
        AND cv.print_id LIKE 'OP01-001%'
        ORDER BY cv.print_id, mpr.market_price
        """,
        (MARKET_GROUP_ID,),
    )
    ver = cur.fetchall()
    for row in ver:
        print(
            f"  print_id={row['print_id']!r}  market_number={row['market_number']!r}  "
            f"group={row['market_group_id']!r}  variant={row['market_variant_label']!r}  "
            f"market_price={row['market_price']}"
        )
    if not ver:
        print("  (no rows)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
