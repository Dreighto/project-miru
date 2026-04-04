#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

from miru_tcgcsv_fetcher import (
    extract_extended_data_value,
    extract_market_variant_label,
    insert_market_price_if_present,
    insert_printing_market_map,
    match_card_variant,
    parse_internal_variant_family,
    upsert_market_product,
)


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
TCGCSV_ROOT = ROOT / "data" / "tcgcsv"
MANIFEST_PATH = TCGCSV_ROOT / "manifest.json"
MAPPING_PATH = TCGCSV_ROOT / "group_set_mapping.json"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Expected object JSON root at {path}")
    return data


def get_count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
    return int(row["c"])


def build_qualifying_groups() -> List[Dict[str, Any]]:
    manifest = load_json(MANIFEST_PATH)
    manifest_groups = manifest.get("groups", [])
    if not isinstance(manifest_groups, list):
        raise ValueError("manifest.json: groups must be a list")
    manifest_lookup: Dict[int, str] = {}
    for row in manifest_groups:
        if not isinstance(row, dict):
            continue
        try:
            gid = int(row.get("group_id"))
        except (TypeError, ValueError):
            continue
        gname = str(row.get("group_name") or "").strip()
        manifest_lookup[gid] = gname

    mapping_raw = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    if not isinstance(mapping_raw, list):
        raise ValueError("group_set_mapping.json must be a list")

    qualifying: List[Dict[str, Any]] = []
    for row in mapping_raw:
        if not isinstance(row, dict):
            continue
        conf = str(row.get("confidence") or "").strip().lower()
        if conf != "high":
            continue

        try:
            group_id = int(row.get("group_id"))
        except (TypeError, ValueError):
            continue

        set_code = str(row.get("proposed_set_code") or "").strip()
        if not set_code:
            continue

        group_dir = TCGCSV_ROOT / str(group_id)
        products_path = group_dir / "products.json"
        prices_path = group_dir / "prices.json"
        if not (products_path.is_file() and prices_path.is_file()):
            continue

        group_name = manifest_lookup.get(group_id) or str(row.get("group_name") or "").strip()
        qualifying.append(
            {
                "group_id": group_id,
                "group_name": group_name,
                "set_code": set_code,
                "products_path": products_path,
                "prices_path": prices_path,
            }
        )

    qualifying.sort(key=lambda g: g["group_id"])
    return qualifying


def process_group(conn: sqlite3.Connection, group: Dict[str, Any]) -> Dict[str, int]:
    group_id = int(group["group_id"])
    group_name = str(group["group_name"])
    set_code = str(group["set_code"])

    products_payload = load_json(Path(group["products_path"]))
    prices_payload = load_json(Path(group["prices_path"]))
    products = products_payload.get("results", [])
    prices = prices_payload.get("results", [])
    if not isinstance(products, list):
        raise ValueError(f"group {group_id}: products results must be a list")
    if not isinstance(prices, list):
        raise ValueError(f"group {group_id}: prices results must be a list")

    prices_by_id: Dict[int, Dict[str, Any]] = {}
    for pr in prices:
        if not isinstance(pr, dict):
            continue
        try:
            pid = int(pr.get("productId"))
        except (TypeError, ValueError):
            continue
        prices_by_id[pid] = pr

    counters = {
        "products": 0,
        "matched": 0,
        "unmatched": 0,
        "skipped": 0,
        "inserted_products": 0,
        "inserted_prices": 0,
        "inserted_maps": 0,
    }

    for product in products:
        if not isinstance(product, dict):
            counters["skipped"] += 1
            continue

        try:
            product_id = int(product.get("productId"))
            product_group_id = int(product.get("groupId"))
        except (TypeError, ValueError):
            counters["skipped"] += 1
            continue

        product_name = str(product.get("name") or "")
        clean_name = str(product.get("cleanName") or product_name)
        product_url = (
            product.get("url")
            if isinstance(product.get("url"), str) and str(product.get("url")).strip()
            else None
        )
        card_code = extract_extended_data_value(product, "Number")
        if not card_code:
            counters["skipped"] += 1
            continue

        counters["products"] += 1
        internal_variant_family = parse_internal_variant_family(product_name)
        market_variant_label = extract_market_variant_label(product_name)
        rarity_market = extract_extended_data_value(product, "Rarity")

        before = conn.total_changes
        market_product_fk = upsert_market_product(
            conn,
            product_id=product_id,
            group_id=product_group_id,
            product_name=product_name,
            clean_name=clean_name,
            market_variant_label=market_variant_label,
            market_set_name=group_name,
            market_set_code=set_code,
            market_number=card_code,
            rarity_market=rarity_market,
            product_url=product_url,
        )
        if conn.total_changes > before:
            counters["inserted_products"] += 1

        before = conn.total_changes
        insert_market_price_if_present(
            conn,
            market_product_fk=market_product_fk,
            price_row=prices_by_id.get(product_id),
        )
        if conn.total_changes > before:
            counters["inserted_prices"] += 1

        match = match_card_variant(
            conn,
            card_code=card_code,
            set_folder=set_code,
            internal_variant_family=internal_variant_family,
        )

        if match.get("status") == "MATCHED":
            before = conn.total_changes
            insert_printing_market_map(
                conn,
                printing_id=int(match["printing_id"]),
                market_product_id=market_product_fk,
                mapping_confidence=str(match["confidence"]),
                mapping_method=str(match["method"]),
                mapping_notes=match.get("notes"),
            )
            if conn.total_changes > before:
                counters["inserted_maps"] += 1
            counters["matched"] += 1
        else:
            counters["unmatched"] += 1

    conn.commit()
    print(
        f"[{group_id}] [{set_code}] [{group_name}] — "
        f"products: {counters['products']}, matched: {counters['matched']}, "
        f"unmatched: {counters['unmatched']}, skipped: {counters['skipped']}"
    )
    return counters


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: database not found at {DB_PATH}")
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    failed_groups: List[Tuple[int, str]] = []

    try:
        # Step 1: wipe tables in single transaction
        print("STEP 1 — Wipe tables")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM printing_market_map")
        conn.execute("DELETE FROM market_prices")
        conn.execute("DELETE FROM market_products")
        conn.commit()

        mp_zero = get_count(conn, "market_products")
        mpr_zero = get_count(conn, "market_prices")
        pmm_zero = get_count(conn, "printing_market_map")
        print(
            f"Post-wipe counts: market_products={mp_zero}, "
            f"market_prices={mpr_zero}, printing_market_map={pmm_zero}"
        )

        # Step 2: qualifying groups
        print("\nSTEP 2 — Build qualifying group list")
        qualifying = build_qualifying_groups()
        print(f"Qualifying groups (confidence=high with files present): {len(qualifying)}")

        # Step 4: process each group
        print("\nSTEP 4 — Process groups")
        totals = {
            "groups_processed": 0,
            "products_inserted": 0,
            "prices_inserted": 0,
            "maps_inserted": 0,
            "unmatched": 0,
        }

        for group in qualifying:
            gid = int(group["group_id"])
            try:
                counters = process_group(conn, group)
                totals["groups_processed"] += 1
                totals["products_inserted"] += counters["inserted_products"]
                totals["prices_inserted"] += counters["inserted_prices"]
                totals["maps_inserted"] += counters["inserted_maps"]
                totals["unmatched"] += counters["unmatched"]
            except Exception as exc:
                conn.rollback()
                failed_groups.append((gid, f"{type(exc).__name__}: {exc}"))
                print(f"[{gid}] ERROR: {type(exc).__name__}: {exc}")

        # Step 5: final summary
        print("\nSTEP 5 — Final batch summary")
        print(f"Total groups processed: {totals['groups_processed']}")
        print(f"Total products inserted: {totals['products_inserted']}")
        print(f"Total prices inserted: {totals['prices_inserted']}")
        print(f"Total printing_market_map rows inserted: {totals['maps_inserted']}")
        print(f"Total unmatched: {totals['unmatched']}")
        if failed_groups:
            print("Groups that errored:")
            for gid, err in failed_groups:
                print(f"  group_id={gid} error={err}")
        else:
            print("Groups that errored: none")

        # Step 6: live verification
        print("\nSTEP 6 — Live verification")
        print(f"SELECT COUNT(*) FROM market_products -> {get_count(conn, 'market_products')}")
        print(f"SELECT COUNT(*) FROM market_prices -> {get_count(conn, 'market_prices')}")
        print(f"SELECT COUNT(*) FROM printing_market_map -> {get_count(conn, 'printing_market_map')}")

        print("\nOP01-001 check:")
        rows = conn.execute(
            """
            SELECT cv.print_id, mp.market_number, mp.market_set_code,
                   mp.market_variant_label, mpr.market_price, mpr.subtype_name
            FROM printing_market_map pmm
            JOIN card_variants cv ON cv.id = pmm.printing_id
            JOIN market_products mp ON mp.id = pmm.market_product_id
            JOIN market_prices mpr ON mpr.market_product_fk = mp.id
            WHERE cv.print_id LIKE 'OP01-001%'
            ORDER BY cv.print_id, mp.market_set_code
            """
        ).fetchall()
        if not rows:
            print("  (no rows)")
        else:
            for row in rows:
                print(
                    f"  print_id={row['print_id']}, market_number={row['market_number']}, "
                    f"market_set_code={row['market_set_code']}, market_variant_label={row['market_variant_label']}, "
                    f"market_price={row['market_price']}, subtype_name={row['subtype_name']}"
                )

        return 0 if not failed_groups else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
