"""
Read-only introspection of card_catalog.db: tables, schemas, samples, distincts.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"

FOCUS_TABLES = ("printing_market_map", "market_products", "market_prices", "card_variants")


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: database not found: {DB_PATH}", file=sys.stderr)
        return 1

    st = DB_PATH.stat()
    print("=" * 72)
    print("DATABASE")
    print("=" * 72)
    print(f"Path: {DB_PATH}")
    print(f"Size: {st.st_size:,} bytes ({st.st_size / (1024 * 1024):.2f} MiB)")
    print()

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    # 1) All tables
    cur = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    )
    rows = cur.fetchall()
    names = [r["name"] for r in rows]
    print("=" * 72)
    print(f"ALL TABLES AND VIEWS ({len(names)})")
    print("=" * 72)
    for r in rows:
        print(f"  {r['type']}: {r['name']}")
    print()

    # 3) Schema for focus tables
    print("=" * 72)
    print("SCHEMAS (column name | type | notnull | default | pk)")
    print("=" * 72)
    for t in FOCUS_TABLES:
        if t not in names:
            print(f"\n[{t}] — NOT PRESENT IN DATABASE")
            continue
        print(f"\n--- {t} ---")
        info = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
        for col in info:
            print(
                f"  {col[1]!s:32} | type={col[2]!s:12} | nn={col[3]} | "
                f"default={col[4]!r} | pk={col[5]}"
            )
    print()

    # 4) Row counts
    print("=" * 72)
    print("ROW COUNTS")
    print("=" * 72)
    for t in FOCUS_TABLES:
        if t not in names:
            print(f"  {t}: N/A (missing)")
            continue
        n = conn.execute(f'SELECT COUNT(*) AS c FROM "{t}"').fetchone()[0]
        print(f"  {t}: {n:,}")
    print()

    def table_cols(table: str) -> list[str]:
        return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]

    # 5) printing_market_map
    if "printing_market_map" in names:
        print("=" * 72)
        print("printing_market_map — first 10 rows")
        print("=" * 72)
        cols = table_cols("printing_market_map")
        n = conn.execute('SELECT COUNT(*) FROM "printing_market_map"').fetchone()[0]
        if n == 0:
            print("  (no rows)")
        else:
            sample = conn.execute(
                'SELECT * FROM "printing_market_map" LIMIT 10'
            ).fetchall()
            for i, row in enumerate(sample, 1):
                print(f"  row {i}: {dict(row)}")
        # distinct set-like column
        set_candidates = [
            c
            for c in cols
            if any(
                k in c.lower()
                for k in ("set_code", "setcode", "release_set", "bandai", "op", "prefix")
            )
        ]
        if not set_candidates:
            set_candidates = [c for c in cols if "set" in c.lower()]
        print()
        print("printing_market_map — distinct Bandai-style set codes")
        if set_candidates:
            for c in set_candidates:
                q = f'SELECT COUNT(DISTINCT "{c}") FROM "printing_market_map"'
                try:
                    d = conn.execute(q).fetchone()[0]
                    print(f"  DISTINCT {c}: {d:,}")
                except sqlite3.Error as e:
                    print(f"  DISTINCT {c}: ERROR {e}")
        else:
            print(
                "  (no set_code on this table; join market_products.id = "
                "printing_market_map.market_product_id)"
            )
            try:
                d = conn.execute(
                    """
                    SELECT COUNT(DISTINCT mp.market_set_code)
                    FROM printing_market_map p
                    JOIN market_products mp ON mp.id = p.market_product_id
                    WHERE mp.market_set_code IS NOT NULL AND TRIM(mp.market_set_code) != ''
                    """
                ).fetchone()[0]
                print(f"  DISTINCT market_products.market_set_code (via join): {d:,}")
                codes = conn.execute(
                    """
                    SELECT DISTINCT mp.market_set_code
                    FROM printing_market_map p
                    JOIN market_products mp ON mp.id = p.market_product_id
                    WHERE mp.market_set_code IS NOT NULL AND TRIM(mp.market_set_code) != ''
                    ORDER BY 1
                    """
                ).fetchall()
                print(f"  values: {[r[0] for r in codes]}")
            except sqlite3.Error as e:
                print(f"  join distinct: ERROR {e}")
        print()

    # 6) market_products
    if "market_products" in names:
        print("=" * 72)
        print("market_products — first 5 rows")
        print("=" * 72)
        n = conn.execute('SELECT COUNT(*) FROM "market_products"').fetchone()[0]
        if n == 0:
            print("  (no rows)")
        else:
            for i, row in enumerate(
                conn.execute('SELECT * FROM "market_products" LIMIT 5').fetchall(), 1
            ):
                print(f"  row {i}: {dict(row)}")
        cols = table_cols("market_products")
        gid_cols = [
            c
            for c in cols
            if "group_id" in c.lower() or "tcgplayer_group_id" in c.lower()
        ]
        print()
        print("market_products — distinct values in group_id-like columns")
        if not gid_cols:
            print("  (no column matching *group_id* / tcgplayer_group_id)")
        for c in gid_cols:
            try:
                vals = conn.execute(
                    f'SELECT DISTINCT "{c}" FROM "market_products" '
                    f'WHERE "{c}" IS NOT NULL ORDER BY 1'
                ).fetchall()
                raw = [r[0] for r in vals]
                print(f"  {c}: {len(raw)} distinct values")
                if len(raw) <= 80:
                    print(f"    {raw}")
                else:
                    print(f"    (first 40) {raw[:40]}")
                    print(f"    ... +{len(raw) - 40} more")
            except sqlite3.Error as e:
                print(f"  {c}: ERROR {e}")
        print()

    # 7) card_variants
    if "card_variants" in names:
        print("=" * 72)
        print("card_variants — first 5 rows")
        print("=" * 72)
        for i, row in enumerate(
            conn.execute('SELECT * FROM "card_variants" LIMIT 5').fetchall(), 1
        ):
            print(f"  row {i}: {dict(row)}")
        print()
        print("card_variants — DISTINCT release_set_code")
        codes = conn.execute(
            "SELECT DISTINCT release_set_code FROM card_variants "
            "ORDER BY release_set_code IS NULL, release_set_code"
        ).fetchall()
        clist = [r[0] for r in codes]
        print(f"  count: {len(clist)}")
        print(f"  values: {clist}")
        print()

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
