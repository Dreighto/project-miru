import sqlite3
import csv
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
MIRU_ASSETS = Path(r"D:\Miru_Assets")
OUTPUT_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_certification_scan.csv")

CERTIFICATION_SQL = """
WITH op01_variants AS (
    SELECT
        cv.id                           AS printing_id,
        cv.card_id,
        c.canonical_code                AS card_code,
        cv.variant_key,
        cv.is_base,
        cv.is_sp,
        cv.is_tr,
        cv.is_alt,
        cv.is_illustration_rare,
        cv.is_manga_rare,
        cv.is_golden_manga_rare,
        cv.is_promo,
        cv.image_path                   AS legacy_image_path,
        cv.release_set_code
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    WHERE c.canonical_code LIKE 'OP01-%'
),
image_layer AS (
    SELECT
        ia.printing_id,
        ia.local_path                   AS asset_image_path,
        ia.source_url,
        ia.is_primary
    FROM image_assets ia
),
price_bridge AS (
    SELECT
        pmm.printing_id,
        pmm.market_product_id,
        pmm.mapping_confidence,
        pmm.is_preferred
    FROM printing_market_map pmm
    WHERE pmm.is_preferred = 1
),
price_layer AS (
    SELECT
        mp.market_product_fk,
        mp.market_price,
        mp.mid_price,
        mp.low_price,
        mp.high_price,
        mp.source_name
    FROM market_prices mp
),
assembled AS (
    SELECT
        v.card_code,
        v.printing_id,
        v.variant_key,
        v.is_base,
        v.is_sp,
        v.is_tr,
        v.is_alt,
        v.is_illustration_rare,
        v.is_manga_rare,
        v.is_golden_manga_rare,
        v.is_promo,
        v.release_set_code,
        v.legacy_image_path,
        il.asset_image_path,
        pb.market_product_id            AS mapped_product_id,
        pb.mapping_confidence,
        pr.market_price,
        pr.mid_price,
        pr.low_price,
        CASE WHEN il.printing_id IS NOT NULL THEN 1 ELSE 0 END AS has_image_asset,
        CASE WHEN pb.printing_id IS NOT NULL THEN 1 ELSE 0 END AS has_price_bridge,
        CASE WHEN pr.market_price IS NOT NULL THEN 1 ELSE 0 END AS has_price,
        CASE
            WHEN v.is_sp = 1                THEN '_sp'
            WHEN v.is_tr = 1                THEN '_tr'
            WHEN v.is_manga_rare = 1        THEN '_mr'
            WHEN v.is_golden_manga_rare = 1 THEN '_gmr'
            WHEN v.is_illustration_rare = 1 THEN '_ir'
            WHEN v.is_alt = 1               THEN '_alt'
            ELSE NULL
        END AS expected_suffix,
        COALESCE(il.asset_image_path, v.legacy_image_path) AS effective_image_path,
        CASE
            WHEN v.variant_key = 'base'  AND v.is_base = 1 THEN 1
            WHEN v.variant_key = 'sp'    AND v.is_sp = 1   THEN 1
            WHEN v.variant_key = 'tr'    AND v.is_tr = 1   THEN 1
            WHEN v.variant_key = 'alt'   AND v.is_alt = 1  THEN 1
            WHEN v.variant_key = 'mr'    AND v.is_manga_rare = 1 THEN 1
            WHEN v.variant_key = 'promo' AND v.is_promo = 1 THEN 1
            WHEN v.variant_key LIKE 'r%' AND v.is_base = 1 THEN 1
            WHEN v.variant_key LIKE 'p%' THEN 1
            ELSE 0
        END AS label_coherent
    FROM op01_variants v
    LEFT JOIN image_layer il  ON il.printing_id = v.printing_id
    LEFT JOIN price_bridge pb ON pb.printing_id = v.printing_id
    LEFT JOIN price_layer pr  ON pr.market_product_fk = pb.market_product_id
)
SELECT
    card_code,
    printing_id,
    variant_key,
    mapped_product_id,
    market_price,
    mid_price,
    low_price,
    effective_image_path    AS image_path,
    mapping_confidence,
    release_set_code,
    has_image_asset,
    has_price_bridge,
    has_price,
    expected_suffix,
    label_coherent,
    CASE
        WHEN has_image_asset = 0  THEN 'MISSING_IMAGE_ASSET'
        WHEN has_price_bridge = 0 THEN 'MISSING_PRICE_BRIDGE'
        WHEN has_price = 0        THEN 'MISSING_PRICE'
        WHEN label_coherent = 0   THEN 'LABEL_VARIANT_MISMATCH'
        WHEN expected_suffix IS NOT NULL
             AND effective_image_path IS NOT NULL
             AND effective_image_path NOT LIKE ('%' || expected_suffix || '%')
                                  THEN 'VARIANT_SUFFIX_MISMATCH'
        WHEN has_image_asset = 1
             AND has_price_bridge = 1
             AND has_price = 1
             AND label_coherent = 1
                                  THEN 'CERTIFIED'
        ELSE 'UNRESOLVED'
    END AS certification_status,
    CASE
        WHEN has_image_asset = 0  THEN 'No image_assets row for printing_id'
        WHEN has_price_bridge = 0 THEN 'No printing_market_map row (is_preferred=1)'
        WHEN has_price = 0        THEN 'Bridge exists but market_price is null'
        WHEN label_coherent = 0   THEN 'variant_key does not match identity flags'
        WHEN expected_suffix IS NOT NULL
             AND effective_image_path IS NOT NULL
             AND effective_image_path NOT LIKE ('%' || expected_suffix || '%')
                                  THEN 'Image path missing expected suffix: ' || expected_suffix
        ELSE NULL
    END AS failure_reason
FROM assembled
ORDER BY card_code, variant_key;
"""

FIELDS = [
    "card_code", "printing_id", "variant_key", "mapped_product_id",
    "market_price", "mid_price", "low_price", "image_path",
    "mapping_confidence", "release_set_code", "certification_status",
    "failure_reason"
]

def check_disk(image_path):
    if not image_path:
        return False
    rel = image_path.lstrip("/")
    if rel.startswith("img/"):
        rel = rel[4:]
    return (MIRU_ASSETS / rel).exists()

def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(CERTIFICATION_SQL).fetchall()
    con.close()

    counts = {
        "CERTIFIED": 0, "MISSING_IMAGE_ASSET": 0, "IMAGE_FILE_MISSING": 0,
        "MISSING_PRICE_BRIDGE": 0, "MISSING_PRICE": 0,
        "VARIANT_SUFFIX_MISMATCH": 0, "LABEL_VARIANT_MISMATCH": 0,
        "UNRESOLVED": 0
    }

    output_rows = []
    for r in rows:
        status = r["certification_status"]
        reason = r["failure_reason"]
        # Upgrade CERTIFIED to IMAGE_FILE_MISSING if file absent on disk
        if status == "CERTIFIED":
            if not check_disk(r["image_path"]):
                status = "IMAGE_FILE_MISSING"
                reason = "image_assets row exists but file not found under D:\\Miru_Assets"
        counts[status] = counts.get(status, 0) + 1
        output_rows.append({
            "card_code":           r["card_code"],
            "printing_id":         r["printing_id"],
            "variant_key":         r["variant_key"],
            "mapped_product_id":   r["mapped_product_id"],
            "market_price":        r["market_price"],
            "mid_price":           r["mid_price"],
            "low_price":           r["low_price"],
            "image_path":          r["image_path"],
            "mapping_confidence":  r["mapping_confidence"],
            "release_set_code":    r["release_set_code"],
            "certification_status": status,
            "failure_reason":      reason,
        })

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nOP01 Certification Scan — {len(output_rows)} printings")
    print("-" * 42)
    for status, count in counts.items():
        print(f"  {status:<28} {count}")
    print(f"\nOutput: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()
