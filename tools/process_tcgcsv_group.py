"""
Import one TCGCSV group (products + prices) into card_catalog.db.
INSERT only; skip existing market_products and printing_market_map pairs.

Usage:
  python tools/process_tcgcsv_group.py 17698
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MAPPING_PATH = ROOT / "data" / "tcgcsv" / "group_set_mapping.json"
TCGCSV_DIR = ROOT / "data" / "tcgcsv"
SOURCE = "tcgcsv"
CATEGORY_ID = "68"


def utc_now_sql() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_mapping(group_id: int) -> tuple[str, str]:
    data = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit("group_set_mapping.json must be a JSON array")
    for row in data:
        if int(row.get("group_id") or 0) == group_id:
            conf = str(row.get("confidence") or "").lower()
            if conf in ("unknown",):
                raise SystemExit(
                    f"group_id {group_id}: confidence is '{conf}' — aborting (unknown not allowed)"
                )
            if conf not in ("high", "low"):
                raise SystemExit(
                    f"group_id {group_id}: unexpected confidence '{conf}' — aborting"
                )
            code = str(row.get("proposed_set_code") or "").strip()
            if not code:
                raise SystemExit(f"group_id {group_id}: empty proposed_set_code")
            return code, conf
    raise SystemExit(f"group_id {group_id}: not found in group_set_mapping.json")


def ext_data_map(ext: list | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in ext or []:
        if not isinstance(item, dict):
            continue
        k = str(item.get("name") or "").strip()
        v = item.get("value")
        if k:
            out[k] = "" if v is None else str(v)
    return out


def ext_number(ed: dict[str, str]) -> str | None:
    v = ed.get("Number") or ed.get("Card Number")
    if v is not None and str(v).strip():
        return str(v).strip()
    return None


def ext_rarity(ed: dict[str, str]) -> str | None:
    v = ed.get("Rarity")
    if v is not None and str(v).strip():
        return str(v).strip()
    return None


def ext_color(ed: dict[str, str]) -> str | None:
    for key, val in ed.items():
        kl = key.lower()
        if kl == "color" or kl.endswith(" color") or kl == "card color":
            if str(val).strip():
                return str(val).strip()
    return None


def variant_label_from_name(name: str) -> str | None:
    n = name or ""
    if "(Parallel)" in n:
        return "Parallel"
    if "(Foil)" in n:
        return "Foil"
    if "(Box Topper)" in n:
        return "Box Topper"
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python tools/process_tcgcsv_group.py <group_id>", file=sys.stderr)
        return 2

    try:
        group_id = int(sys.argv[1])
    except ValueError:
        print("group_id must be an integer", file=sys.stderr)
        return 2

    proposed_set_code, _map_conf = load_mapping(group_id)

    products_path = TCGCSV_DIR / str(group_id) / "products.json"
    prices_path = TCGCSV_DIR / str(group_id) / "prices.json"
    if not products_path.is_file():
        raise SystemExit(f"missing {products_path}")
    if not prices_path.is_file():
        raise SystemExit(f"missing {prices_path}")

    products_payload = json.loads(products_path.read_text(encoding="utf-8"))
    prices_payload = json.loads(prices_path.read_text(encoding="utf-8"))
    product_results = products_payload.get("results") or []
    price_results = prices_payload.get("results") or []

    prices_by_pid: dict[int, list[dict]] = defaultdict(list)
    for pr in price_results:
        if not isinstance(pr, dict):
            continue
        try:
            pid = int(pr["productId"])
        except (KeyError, TypeError, ValueError):
            continue
        prices_by_pid[pid].append(pr)

    stats = {
        "json_products": len(product_results),
        "inserted_products": 0,
        "skipped_existing_products": 0,
        "inserted_prices": 0,
        "map_high": 0,
        "map_medium": 0,
        "skipped_map_dup": 0,
    }
    unmatched: list[tuple[str, str | None]] = []  # (market_product_id, market_number or None)

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    try:
        cur.execute("BEGIN IMMEDIATE")

        for p in product_results:
            if not isinstance(p, dict):
                continue
            try:
                tcg_pid = int(p.get("productId"))
            except (TypeError, ValueError):
                continue
            ext = p.get("extendedData")
            ed = ext_data_map(ext if isinstance(ext, list) else None)
            market_number = ext_number(ed)
            rarity_market = ext_rarity(ed)
            color_val = ext_color(ed)

            raw_extra = None
            if color_val:
                raw_extra = json.dumps({"color": color_val}, ensure_ascii=False)

            name = str(p.get("name") or "")
            clean_name = str(p.get("cleanName") or "") or None
            gid = p.get("groupId")
            market_group_id = str(int(gid)) if gid is not None else str(group_id)
            url = str(p.get("url") or "").strip() or None
            image_url = str(p.get("imageUrl") or "").strip() or None
            variant_label = variant_label_from_name(name)

            cur.execute(
                """
                SELECT id FROM market_products
                WHERE market_product_id = ? AND source_name = ?
                """,
                (str(tcg_pid), SOURCE),
            )
            existing = cur.fetchone()
            if existing:
                stats["skipped_existing_products"] += 1
                continue

            now = utc_now_sql()
            cur.execute(
                """
                INSERT INTO market_products (
                    source_name, market_product_id, market_group_id, market_category_id,
                    product_name, clean_product_name, market_variant_label, market_set_name,
                    market_set_code, market_number, rarity_market, subtype_support, active,
                    url, image_url, raw_payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?)
                """,
                (
                    SOURCE,
                    str(tcg_pid),
                    market_group_id,
                    CATEGORY_ID,
                    name,
                    clean_name,
                    variant_label,
                    None,
                    proposed_set_code,
                    market_number,
                    rarity_market,
                    url,
                    image_url,
                    raw_extra,
                    now,
                    now,
                ),
            )
            new_mp_id = cur.lastrowid
            stats["inserted_products"] += 1

            for pr in prices_by_pid.get(tcg_pid, []):
                st = str(pr.get("subTypeName") or "") or "Normal"
                cur.execute(
                    """
                    INSERT INTO market_prices (
                        market_product_fk, source_name, captured_at, currency, subtype_name,
                        low_price, mid_price, high_price, market_price, direct_low_price
                    ) VALUES (?, ?, ?, 'USD', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_mp_id,
                        SOURCE,
                        now,
                        st,
                        pr.get("lowPrice"),
                        pr.get("midPrice"),
                        pr.get("highPrice"),
                        pr.get("marketPrice"),
                        pr.get("directLowPrice"),
                    ),
                )
                stats["inserted_prices"] += 1

            printing_id = None
            map_confidence = None
            map_method = None
            if market_number:
                cur.execute(
                    """
                    SELECT id FROM card_variants
                    WHERE print_id = ? AND is_base = 1
                    LIMIT 1
                    """,
                    (market_number,),
                )
                row = cur.fetchone()
                if row:
                    printing_id = row[0]
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
                        (market_number,),
                    )
                    row = cur.fetchone()
                    if row:
                        printing_id = row[0]
                        map_confidence = "MEDIUM"
                        map_method = "exact_code_set_fallback"

            if printing_id is None:
                unmatched.append((str(tcg_pid), market_number))
            else:
                cur.execute(
                    """
                    SELECT 1 FROM printing_market_map
                    WHERE printing_id = ? AND market_product_id = ?
                    """,
                    (printing_id, new_mp_id),
                )
                if cur.fetchone():
                    stats["skipped_map_dup"] += 1
                else:
                    cur.execute(
                        """
                        INSERT INTO printing_market_map (
                            printing_id, market_product_id, mapping_confidence,
                            mapping_method, mapping_notes, is_preferred, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, NULL, 1, ?, NULL)
                        """,
                        (
                            printing_id,
                            new_mp_id,
                            map_confidence,
                            map_method,
                            now,
                        ),
                    )
                    if map_confidence == "HIGH":
                        stats["map_high"] += 1
                    else:
                        stats["map_medium"] += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"group_id:              {group_id}")
    print(f"proposed_set_code:     {proposed_set_code}")
    print(f"Products in JSON:      {stats['json_products']}")
    print(f"Inserted market_products: {stats['inserted_products']}")
    print(f"Skipped (already in DB):  {stats['skipped_existing_products']}")
    print(f"Inserted market_prices:   {stats['inserted_prices']}")
    print(f"printing_market_map HIGH: {stats['map_high']}")
    print(f"printing_market_map MED:  {stats['map_medium']}")
    print(f"Skipped map duplicate:    {stats['skipped_map_dup']}")
    print(f"UNMATCHED (no variant):   {len(unmatched)}")
    for mpid, mn in unmatched[:50]:
        print(f"  UNMATCHED market_product_id={mpid} market_number={mn!r}")
    if len(unmatched) > 50:
        print(f"  ... +{len(unmatched) - 50} more")

    # Live verification
    print()
    print("=" * 72)
    print("POST-RUN VERIFICATION (DB)")
    print("=" * 72)
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    gid_s = str(group_id)
    cur.execute(
        "SELECT COUNT(*) FROM market_products WHERE market_group_id = ? AND source_name = ?",
        (gid_s, SOURCE),
    )
    n_mp = cur.fetchone()[0]
    print(f"market_products with market_group_id={gid_s!r}: {n_mp}")

    cur.execute(
        """
        SELECT COUNT(*) FROM market_prices pr
        JOIN market_products mp ON mp.id = pr.market_product_fk
        WHERE mp.market_group_id = ? AND mp.source_name = ?
        """,
        (gid_s, SOURCE),
    )
    n_pr = cur.fetchone()[0]
    print(f"market_prices joined to those products: {n_pr}")

    cur.execute(
        """
        SELECT COUNT(*) FROM printing_market_map pm
        JOIN market_products mp ON mp.id = pm.market_product_id
        WHERE mp.market_group_id = ? AND mp.source_name = ?
        """,
        (gid_s, SOURCE),
    )
    n_map = cur.fetchone()[0]
    print(f"printing_market_map joined to those products: {n_map}")

    print()
    print("UNMATCHED product codes (from this run log above):")
    codes = sorted({mn for _, mn in unmatched if mn})
    if not codes:
        print("  (none with non-null market_number; see full UNMATCHED list for sealed/no-number)")
    else:
        for c in codes:
            print(f"  {c}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
