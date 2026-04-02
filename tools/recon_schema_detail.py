"""Read-only: detailed schema + samples for key market tables."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"

TABLES = (
    "printing_market_map",
    "market_products",
    "market_prices",
    "card_variants",
)


def row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: {DB_PATH} not found", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    for t in TABLES:
        print("=" * 72)
        print(f"TABLE: {t}")
        print("=" * 72)

        info = conn.execute(f'PRAGMA table_info("{t}")').fetchall()
        if not info:
            print(f"  (missing or no columns)\n")
            continue

        print(f"{'#':>3}  {'column':<32}  {'type':<14}  NOT NULL  default")
        print("-" * 72)
        for col in info:
            cid, name, ctype, notnull, default, pk = col
            nn = "YES" if notnull else "no"
            d = repr(default) if default is not None else "—"
            pk_mark = f" [PK]" if pk else ""
            print(f"{cid:3}  {name:<32}  {ctype or '':<14}  {nn:8}  {d}{pk_mark}")
        print()

        rows = conn.execute(f'SELECT * FROM "{t}" LIMIT 3').fetchall()
        print(f"First 3 rows ({len(rows)} shown):")
        for i, row in enumerate(rows, 1):
            print(f"  --- row {i} ---")
            print(f"  {row_to_dict(row)}")
        print()

    # 4) market_products — one full row pretty-printed
    print("=" * 72)
    print("market_products — one full row (JSON pretty-print)")
    print("=" * 72)
    r = conn.execute('SELECT * FROM "market_products" LIMIT 1').fetchone()
    if r:
        print(json.dumps(row_to_dict(r), indent=2, ensure_ascii=False, default=str))
    else:
        print("(no rows)")
    print()

    # 5) printing_market_map — one full row pretty-printed
    print("=" * 72)
    print("printing_market_map — one full row (JSON pretty-print)")
    print("=" * 72)
    r = conn.execute('SELECT * FROM "printing_market_map" LIMIT 1').fetchone()
    if r:
        print(json.dumps(row_to_dict(r), indent=2, ensure_ascii=False, default=str))
    else:
        print("(no rows)")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
