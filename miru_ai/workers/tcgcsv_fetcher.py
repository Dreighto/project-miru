#!/usr/bin/env python3
import argparse
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
TCGCSV_DATA_ROOT = PROJECT_ROOT / "data" / "tcgcsv"
LOG_PATH = PROJECT_ROOT / "logs" / "tcgcsv_fetcher.log"

PAREN_RE = re.compile(r"\(([^)]+)\)")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Logger:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, line: str) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(f"{line}\n")

    def start_run(self) -> None:
        self.write(f"=== TCGCSV LOCAL FILE RUN {utc_now_iso()} ===")

    def log_product(
        self,
        status: str,
        card_code: str,
        parsed_variant_key: str,
        product_id: Any,
        product_name: str,
        reason: Optional[str] = None,
        details: Optional[str] = None,
    ) -> None:
        suffix_parts: List[str] = []
        if reason:
            suffix_parts.append(f"reason={reason}")
        if details:
            suffix_parts.append(details)
        suffix = f" [{' ; '.join(suffix_parts)}]" if suffix_parts else ""
        self.write(
            f"{status} | {card_code} | {parsed_variant_key} | {product_id} | {product_name}{suffix}"
        )

    def set_summary(self, set_folder: str, counters: Dict[str, int]) -> None:
        self.write(f"=== SET {set_folder} COMPLETE ===")
        self.write(f"Total products: {counters['total']}")
        self.write(f"Cards found (had Number field): {counters['cards_found']}")
        self.write(f"Matched: {counters['matched']}")
        self.write(f"Unmatched: {counters['unmatched']}")
        self.write(f"Ambiguous: {counters['ambiguous']}")
        self.write(f"Skipped: {counters['skipped']}")
        self.write(f"Errors: {counters['errors']}")


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return data


def list_set_folders(root: Path) -> List[str]:
    return sorted([p.name for p in root.iterdir() if p.is_dir()])


def parse_internal_variant_family(product_name: str) -> str:
    upper = product_name.upper()
    if "(SP)" in upper:
        return "sp"
    if "(TREASURE RARE)" in upper or "(TR)" in upper:
        return "tr"
    if "(ALTERNATE ART)" in upper and "(MANGA)" in upper:
        return "manga"
    if "(MANGA)" in upper:
        return "manga"
    if "(ALTERNATE ART)" in upper:
        return "alternate_art"
    if "(PARALLEL)" in upper:
        return "parallel"
    if "(REPRINT)" in upper:
        return "reprint_variant"
    if "(BOX TOPPER)" in upper:
        return "promo"
    if "(GOLD)" in upper:
        return "promo"
    return "base"


def extract_market_variant_label(product_name: str) -> Optional[str]:
    parts = [p.strip() for p in PAREN_RE.findall(product_name)]
    # Keep only parenthetical parts with alphabetic content to avoid card-index tags like (002).
    parts = [p for p in parts if re.search(r"[A-Za-z]", p)]
    if not parts:
        return None
    return " ".join(parts)


def extract_extended_data_value(product: Dict[str, Any], field_name: str) -> Optional[str]:
    ext = product.get("extendedData")
    if not isinstance(ext, list):
        return None
    for item in ext:
        if not isinstance(item, dict):
            continue
        if item.get("name") == field_name:
            value = item.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def build_groups_lookup(root: Path) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    groups_file = root / "groups.json"
    if not groups_file.exists():
        return lookup

    payload = load_json(groups_file)
    rows = payload.get("results", [])
    if not isinstance(rows, list):
        return lookup

    for row in rows:
        if not isinstance(row, dict):
            continue
        gid = row.get("groupId")
        if gid is None:
            continue
        lookup[str(gid)] = row
    return lookup


def group_meta_from_lookup(
    groups_lookup: Dict[str, Dict[str, Any]], group_id: int, set_folder: str
) -> Tuple[Optional[str], str]:
    row = groups_lookup.get(str(group_id), {})
    set_name = row.get("name") if isinstance(row.get("name"), str) else None

    set_code: Optional[str] = None
    for key in ("abbreviation", "abbr", "groupAbbreviation", "groupCode", "code"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            set_code = value.strip()
            break

    if not set_code:
        set_code = set_folder

    return set_name, set_code


def upsert_market_product(
    conn: sqlite3.Connection,
    *,
    product_id: int,
    group_id: int,
    product_name: str,
    clean_name: str,
    market_variant_label: Optional[str],
    market_set_name: Optional[str],
    market_set_code: Optional[str],
    market_number: str,
    rarity_market: Optional[str],
    product_url: Optional[str],
) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO market_products (
            source_name,
            market_product_id,
            market_group_id,
            market_category_id,
            product_name,
            clean_product_name,
            market_variant_label,
            market_set_name,
            market_set_code,
            market_number,
            rarity_market,
            url,
            updated_at
        ) VALUES (
            'tcgcsv',
            ?,
            ?,
            '68',
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            datetime('now')
        )
        """,
        (
            str(product_id),
            str(group_id),
            product_name,
            clean_name,
            market_variant_label,
            market_set_name,
            market_set_code,
            market_number,
            rarity_market,
            product_url,
        ),
    )

    row = conn.execute(
        """
        SELECT id
        FROM market_products
        WHERE source_name = 'tcgcsv'
          AND market_product_id = ?
        """,
        (str(product_id),),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Failed to resolve market_products.id for product_id={product_id}")
    return int(row["id"])


def insert_market_price_if_present(
    conn: sqlite3.Connection,
    *,
    market_product_fk: int,
    price_row: Optional[Dict[str, Any]],
) -> None:
    if not isinstance(price_row, dict):
        return

    low_price = price_row.get("lowPrice")
    mid_price = price_row.get("midPrice")
    high_price = price_row.get("highPrice")
    market_price = price_row.get("marketPrice")
    direct_low_price = price_row.get("directLowPrice")
    subtype_name = price_row.get("subTypeName")

    if (
        low_price is None
        and mid_price is None
        and high_price is None
        and market_price is None
        and direct_low_price is None
    ):
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO market_prices (
            market_product_fk,
            source_name,
            captured_at,
            currency,
            subtype_name,
            low_price,
            mid_price,
            high_price,
            market_price,
            direct_low_price
        ) VALUES (
            ?,
            'tcgcsv',
            datetime('now'),
            'USD',
            ?,
            ?,
            ?,
            ?,
            ?,
            ?
        )
        """,
        (
            market_product_fk,
            subtype_name,
            low_price,
            mid_price,
            high_price,
            market_price,
            direct_low_price,
        ),
    )


def match_card_variant(
    conn: sqlite3.Connection,
    *,
    card_code: str,
    set_folder: str,
    internal_variant_family: str,
) -> Dict[str, Any]:
    def rows_to_ids(rows: List[sqlite3.Row]) -> List[int]:
        return [int(r["id"]) for r in rows]

    if internal_variant_family == "base":
        rows = conn.execute(
            """
            SELECT id
            FROM card_variants
            WHERE print_id LIKE ?
              AND is_base = 1
            """,
            (f"{card_code}%",),
        ).fetchall()
        ids = rows_to_ids(rows)
        if len(ids) == 0:
            return {"status": "UNMATCHED"}
        if len(ids) == 1:
            return {
                "status": "MATCHED",
                "printing_id": ids[0],
                "confidence": "HIGH",
                "method": "exact_code_plus_base_flag",
                "notes": None,
            }
        return {"status": "AMBIGUOUS", "ids": ids}

    if internal_variant_family == "parallel":
        rows_primary = conn.execute(
            """
            SELECT id
            FROM card_variants
            WHERE print_id LIKE ?
              AND variant_key LIKE 'parallel_%'
              AND distribution_product_key IS NOT NULL
              AND distribution_product_key != '_unclassified'
              AND release_set_code = ?
            ORDER BY variant_key ASC
            """,
            (f"{card_code}%", set_folder),
        ).fetchall()
        if rows_primary:
            ids = rows_to_ids(rows_primary)
            return {
                "status": "MATCHED",
                "printing_id": ids[0],
                "confidence": "HIGH",
                "method": "exact_code_plus_set_match",
                "notes": f"primary_rows={','.join(str(i) for i in ids)}",
            }

        rows_fallback = conn.execute(
            """
            SELECT id
            FROM card_variants
            WHERE print_id LIKE ?
              AND variant_key LIKE 'parallel_%'
              AND release_set_code = ?
              AND is_base = 0
            ORDER BY variant_key ASC
            """,
            (f"{card_code}%", set_folder),
        ).fetchall()
        if rows_fallback:
            ids = rows_to_ids(rows_fallback)
            return {
                "status": "MATCHED",
                "printing_id": ids[0],
                "confidence": "MEDIUM",
                "method": "exact_code_set_fallback",
                "notes": f"fallback_rows={','.join(str(i) for i in ids)}",
            }

        return {"status": "UNMATCHED", "reason": "no_parallel_found"}

    if internal_variant_family in {"sp", "tr", "manga", "alternate_art", "promo"}:
        variant_lookup = {
            "sp": "sp",
            "tr": "tr",
            "manga": "mr",
            "alternate_art": "alt",
            "promo": "promo",
        }
        variant_key = variant_lookup[internal_variant_family]
        rows = conn.execute(
            """
            SELECT id
            FROM card_variants
            WHERE print_id LIKE ?
              AND variant_key = ?
            """,
            (f"{card_code}%", variant_key),
        ).fetchall()
        ids = rows_to_ids(rows)
        if len(ids) == 0:
            return {"status": "UNMATCHED"}
        if len(ids) == 1:
            if internal_variant_family == "promo":
                confidence = "MEDIUM"
                method = "exact_code_promo_match"
            else:
                confidence = "HIGH"
                method = "exact_code_plus_variant_flag"
            return {
                "status": "MATCHED",
                "printing_id": ids[0],
                "confidence": confidence,
                "method": method,
                "notes": None,
            }
        return {"status": "AMBIGUOUS", "ids": ids}

    if internal_variant_family == "reprint_variant":
        rows = conn.execute(
            """
            SELECT id
            FROM card_variants
            WHERE print_id LIKE ?
              AND variant_key IN ('r1','r2')
              AND tcgplayer_product_id IS NULL
            ORDER BY variant_key ASC
            """,
            (f"{card_code}%",),
        ).fetchall()
        ids = rows_to_ids(rows)
        if len(ids) == 0:
            return {"status": "UNMATCHED"}
        return {
            "status": "MATCHED",
            "printing_id": ids[0],
            "confidence": "MEDIUM",
            "method": "exact_code_reprint_sequence",
            "notes": f"reprint_rows={','.join(str(i) for i in ids)}",
        }

    return {"status": "UNMATCHED"}


def insert_printing_market_map(
    conn: sqlite3.Connection,
    *,
    printing_id: int,
    market_product_id: int,
    mapping_confidence: str,
    mapping_method: str,
    mapping_notes: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO printing_market_map (
            printing_id,
            market_product_id,
            mapping_confidence,
            mapping_method,
            mapping_notes,
            is_preferred,
            created_at
        ) VALUES (?, ?, ?, ?, ?, 1, datetime('now'))
        """,
        (printing_id, market_product_id, mapping_confidence, mapping_method, mapping_notes),
    )


def process_set(
    conn: sqlite3.Connection,
    logger: Logger,
    *,
    set_folder: str,
    groups_lookup: Dict[str, Dict[str, Any]],
) -> Dict[str, int]:
    set_path = Path(TCGCSV_DATA_ROOT) / set_folder
    products_path = set_path / "products.json"
    prices_path = set_path / "prices.json"

    products_data = load_json(products_path)
    prices_data = load_json(prices_path)

    products = products_data.get("results", [])
    prices = prices_data.get("results", [])
    if not isinstance(products, list):
        raise ValueError(f"products.json results is not a list for {set_folder}")
    if not isinstance(prices, list):
        raise ValueError(f"prices.json results is not a list for {set_folder}")

    prices_by_id = {
        p["productId"]: p
        for p in prices
        if isinstance(p, dict) and "productId" in p
    }

    counters = {
        "total": 0,
        "cards_found": 0,
        "matched": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "skipped": 0,
        "errors": 0,
    }

    for product in products:
        counters["total"] += 1

        if not isinstance(product, dict):
            counters["errors"] += 1
            logger.log_product("ERROR", "", "", "", "invalid_product_row")
            continue

        product_name = str(product.get("name") or "")
        clean_name = str(product.get("cleanName") or product_name)
        product_url = product.get("url") if isinstance(product.get("url"), str) else None

        try:
            product_id = int(product.get("productId"))
            group_id = int(product.get("groupId"))
        except (TypeError, ValueError):
            counters["errors"] += 1
            logger.log_product(
                "ERROR",
                "",
                "",
                product.get("productId"),
                product_name,
                reason="invalid_ids",
            )
            continue

        card_code = extract_extended_data_value(product, "Number")
        if not card_code:
            counters["skipped"] += 1
            logger.log_product("SKIP", "", "", product_id, product_name, reason="no_card_code")
            continue

        counters["cards_found"] += 1

        internal_variant_family = parse_internal_variant_family(product_name)
        market_variant_label = extract_market_variant_label(product_name)
        rarity_market = extract_extended_data_value(product, "Rarity")

        try:
            market_set_name, market_set_code = group_meta_from_lookup(
                groups_lookup, group_id, set_folder
            )
            market_product_fk = upsert_market_product(
                conn,
                product_id=product_id,
                group_id=group_id,
                product_name=product_name,
                clean_name=clean_name,
                market_variant_label=market_variant_label,
                market_set_name=market_set_name,
                market_set_code=market_set_code,
                market_number=card_code,
                rarity_market=rarity_market,
                product_url=product_url,
            )

            insert_market_price_if_present(
                conn,
                market_product_fk=market_product_fk,
                price_row=prices_by_id.get(product_id),
            )

            match = match_card_variant(
                conn,
                card_code=card_code,
                set_folder=set_folder,
                internal_variant_family=internal_variant_family,
            )

            if match["status"] == "MATCHED":
                insert_printing_market_map(
                    conn,
                    printing_id=int(match["printing_id"]),
                    market_product_id=market_product_fk,
                    mapping_confidence=str(match["confidence"]),
                    mapping_method=str(match["method"]),
                    mapping_notes=match.get("notes"),
                )
                counters["matched"] += 1
                logger.log_product(
                    "MATCHED",
                    card_code,
                    internal_variant_family,
                    product_id,
                    product_name,
                    details=f"printing_id={match['printing_id']}",
                )
            elif match["status"] == "UNMATCHED":
                counters["unmatched"] += 1
                logger.log_product(
                    "UNMATCHED",
                    card_code,
                    internal_variant_family,
                    product_id,
                    product_name,
                    reason=match.get("reason"),
                )
            else:
                counters["ambiguous"] += 1
                ids = match.get("ids", [])
                logger.log_product(
                    "AMBIGUOUS",
                    card_code,
                    internal_variant_family,
                    product_id,
                    product_name,
                    details=f"variant_ids={','.join(str(i) for i in ids)}",
                )

        except Exception as exc:
            counters["errors"] += 1
            logger.log_product(
                "ERROR",
                card_code,
                internal_variant_family,
                product_id,
                product_name,
                details=f"error={type(exc).__name__}: {exc}",
            )

    conn.commit()
    logger.set_summary(set_folder, counters)
    return counters


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Load TCGCSV local JSON into market_products/market_prices/printing_market_map."
    )
    parser.add_argument("--set", dest="set_folder", help="Process one set folder, e.g. OP01")
    args = parser.parse_args()

    logger = Logger(LOG_PATH)
    logger.start_run()

    root = Path(TCGCSV_DATA_ROOT)
    if not root.exists():
        raise FileNotFoundError(f"TCGCSV data root does not exist: {root}")
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(f"Card catalog DB does not exist: {DB_PATH}")
    set_folders = [args.set_folder] if args.set_folder else list_set_folders(root)
    groups_lookup = build_groups_lookup(root)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    overall = {
        "sets": [],
        "total": 0,
        "cards_found": 0,
        "matched": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "skipped": 0,
        "errors": 0,
    }

    try:
        for set_folder in set_folders:
            counters = process_set(conn, logger, set_folder=set_folder, groups_lookup=groups_lookup)
            overall["sets"].append(set_folder)
            for key in ("total", "cards_found", "matched", "unmatched", "ambiguous", "skipped", "errors"):
                overall[key] += counters[key]
    finally:
        conn.close()

    print(json.dumps(overall))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
