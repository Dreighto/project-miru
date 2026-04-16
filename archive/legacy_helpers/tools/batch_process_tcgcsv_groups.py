"""
Batch process high-confidence TCGCSV groups into data/card_catalog.db.

Rules:
- confidence == "high" only
- skip groups already present in market_products (idempotent)
- skip group_id 3188 (OP01) and 17698 (OP02)
- process in ascending group_id order
- use existing tools/process_tcgcsv_group.py logic per group
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MAPPING_PATH = ROOT / "data" / "tcgcsv" / "group_set_mapping.json"
PROCESSOR = ROOT / "tools" / "process_tcgcsv_group.py"
SOURCE = "tcgcsv"
FORCED_SKIP = {3188, 17698}


def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(str(DB_PATH))


def table_counts(conn: sqlite3.Connection) -> Tuple[int, int, int]:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM market_products")
    mp = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM market_prices")
    mpr = int(cur.fetchone()[0])
    cur.execute("SELECT COUNT(*) FROM printing_market_map")
    pmm = int(cur.fetchone()[0])
    return mp, mpr, pmm


def group_has_products(conn: sqlite3.Connection, group_id: int) -> bool:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT 1
        FROM market_products
        WHERE source_name = ?
          AND market_group_id = ?
        LIMIT 1
        """,
        (SOURCE, str(group_id)),
    )
    return cur.fetchone() is not None


def group_metrics(conn: sqlite3.Connection, group_id: int) -> Tuple[int, int, int, int]:
    cur = conn.cursor()
    gid = str(group_id)

    cur.execute(
        """
        SELECT COUNT(*)
        FROM market_products
        WHERE source_name = ?
          AND market_group_id = ?
        """,
        (SOURCE, gid),
    )
    products = int(cur.fetchone()[0])

    cur.execute(
        """
        SELECT COUNT(*)
        FROM market_prices mpr
        JOIN market_products mp ON mp.id = mpr.market_product_fk
        WHERE mp.source_name = ?
          AND mp.market_group_id = ?
        """,
        (SOURCE, gid),
    )
    prices = int(cur.fetchone()[0])

    cur.execute(
        """
        SELECT COUNT(*)
        FROM printing_market_map pmm
        JOIN market_products mp ON mp.id = pmm.market_product_id
        WHERE mp.source_name = ?
          AND mp.market_group_id = ?
        """,
        (SOURCE, gid),
    )
    mapped = int(cur.fetchone()[0])

    cur.execute(
        """
        SELECT COUNT(*)
        FROM market_products mp
        WHERE mp.source_name = ?
          AND mp.market_group_id = ?
          AND NOT EXISTS (
              SELECT 1
              FROM printing_market_map pmm
              WHERE pmm.market_product_id = mp.id
          )
        """,
        (SOURCE, gid),
    )
    unmatched = int(cur.fetchone()[0])

    return products, prices, mapped, unmatched


def group_unmatched_rows(conn: sqlite3.Connection, group_id: int) -> List[Tuple[str, str | None, str]]:
    cur = conn.cursor()
    gid = str(group_id)
    cur.execute(
        """
        SELECT
            mp.market_group_id,
            mp.market_number,
            mp.product_name
        FROM market_products mp
        WHERE mp.source_name = ?
          AND mp.market_group_id = ?
          AND NOT EXISTS (
              SELECT 1
              FROM printing_market_map pmm
              WHERE pmm.market_product_id = mp.id
          )
        ORDER BY
            CASE WHEN mp.market_number IS NULL OR mp.market_number = '' THEN 1 ELSE 0 END,
            mp.market_number,
            mp.product_name
        """,
        (SOURCE, gid),
    )
    out: List[Tuple[str, str | None, str]] = []
    for row in cur.fetchall():
        out.append((str(row[0]), row[1], str(row[2])))
    return out


def load_high_confidence_groups() -> List[Tuple[int, str]]:
    payload = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("group_set_mapping.json must be an array")

    out: List[Tuple[int, str]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        conf = str(row.get("confidence") or "").strip().lower()
        if conf != "high":
            continue
        gid = int(row.get("group_id") or 0)
        set_code = str(row.get("proposed_set_code") or "").strip()
        if gid <= 0:
            continue
        out.append((gid, set_code))
    out.sort(key=lambda x: x[0])
    return out


def run_group(group_id: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROCESSOR), str(group_id)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: missing DB at {DB_PATH}")
        return 1
    if not PROCESSOR.is_file():
        print(f"FAILED: missing processor at {PROCESSOR}")
        return 1

    with get_conn() as conn:
        pre_mp, pre_mpr, pre_pmm = table_counts(conn)

    print("=" * 72)
    print("BATCH START")
    print("=" * 72)
    print(f"DB_PATH: {DB_PATH}")
    print(f"Pre-run counts -> market_products={pre_mp}, market_prices={pre_mpr}, printing_market_map={pre_pmm}")

    all_high = load_high_confidence_groups()
    skipped_fixed: List[Tuple[int, str]] = []
    skipped_existing: List[Tuple[int, str]] = []
    to_process: List[Tuple[int, str]] = []

    with get_conn() as conn:
        for group_id, set_code in all_high:
            if group_id in FORCED_SKIP:
                skipped_fixed.append((group_id, set_code))
                continue
            if group_has_products(conn, group_id):
                skipped_existing.append((group_id, set_code))
                continue
            to_process.append((group_id, set_code))

    print(f"High-confidence groups found: {len(all_high)}")
    print(f"Skipped fixed groups: {len(skipped_fixed)}")
    print(f"Skipped already-processed groups: {len(skipped_existing)}")
    print(f"Groups to process: {len(to_process)}")
    print()

    totals = {
        "products": 0,
        "prices": 0,
        "mapped": 0,
    }
    attempted = 0
    completed = 0
    failed: List[Tuple[int, str, str]] = []
    unmatched_all: List[Tuple[str, str | None, str]] = []
    group_rollup: Dict[int, Tuple[int, int, int, int, str]] = {}

    for group_id, set_code in to_process:
        attempted += 1
        proc = run_group(group_id)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            failed.append((group_id, set_code, err))
            print(f"[{group_id}] {set_code} — FAILED")
            continue

        completed += 1
        with get_conn() as conn:
            products, prices, mapped, unmatched = group_metrics(conn, group_id)
            rows = group_unmatched_rows(conn, group_id)

        totals["products"] += products
        totals["prices"] += prices
        totals["mapped"] += mapped
        unmatched_all.extend(rows)
        group_rollup[group_id] = (products, prices, mapped, unmatched, set_code)

        print(f"[{group_id}] {set_code} — products: {products}, prices: {prices}, mapped: {mapped}, unmatched: {unmatched}")

    with get_conn() as conn:
        post_mp, post_mpr, post_pmm = table_counts(conn)

    print()
    print("=" * 72)
    print("BATCH SUMMARY")
    print("=" * 72)
    print(f"Total groups attempted: {attempted}")
    print(f"Total groups completed successfully: {completed}")
    print(f"Total products inserted across all groups: {post_mp - pre_mp}")
    print(f"Total prices inserted across all groups: {post_mpr - pre_mpr}")
    print(f"Total printing_market_map rows inserted across all groups: {post_pmm - pre_pmm}")
    print(f"Total unmatched products across all groups: {len(unmatched_all)}")
    if unmatched_all:
        for grp, market_number, product_name in unmatched_all:
            print(
                f"  unmatched group={grp} market_number={market_number!r} product_name={product_name}"
            )
    else:
        print("  (none)")

    if failed:
        print("Any groups that failed:")
        for gid, set_code, err in failed:
            print(f"  group_id={gid} set_code={set_code} error={err}")
    else:
        print("Any groups that failed: (none)")

    print()
    print("=" * 72)
    print("LIVE VERIFICATION")
    print("=" * 72)
    print(f"Post-run counts -> market_products={post_mp}, market_prices={post_mpr}, printing_market_map={post_pmm}")
    print(f"Counts increased from baseline -> market_products={post_mp > pre_mp}, market_prices={post_mpr > pre_mpr}, printing_market_map={post_pmm > pre_pmm}")

    if failed:
        print("FINAL STATUS: INCONCLUSIVE")
        return 2
    print("FINAL STATUS: CONFIRMED WORKING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
