"""
OP01 Lane 2 URL Diagnostic -- READ-ONLY

Checks market_products.image_url population for alt/sp/tr/mr FETCH_NEEDED rows.
Determines which printings have a price bridge AND a non-null image_url,
making them candidates for automated TCGPlayer CDN image fetch.

No DB writes. No network requests.
"""

import sqlite3
import csv
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
DISCOVERY_CSV = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_missing_asset_discovery.csv")
OUTPUT_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_lane2_url_diagnostic.csv")

TARGET_VKS = {"alt", "sp", "tr", "mr"}

OUTPUT_FIELDS = [
    "card_code", "printing_id", "variant_key",
    "market_product_id", "tcg_image_url", "url_status",
]


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Step 1 — Extract target printing_ids from discovery CSV
    with open(DISCOVERY_CSV, encoding="utf-8") as f:
        disc_rows = [
            r for r in csv.DictReader(f)
            if r["discovery_status"] == "FETCH_NEEDED" and r["variant_key"] in TARGET_VKS
        ]

    from collections import Counter
    vk_counts = Counter(r["variant_key"] for r in disc_rows)
    pids = [int(r["printing_id"]) for r in disc_rows]

    print(f"Step 1: FETCH_NEEDED alt/sp/tr/mr rows from discovery CSV: {len(pids)}")
    for vk in ("alt", "sp", "tr", "mr"):
        print(f"  {vk}: {vk_counts.get(vk, 0)}")

    # Step 2 — Query DB read-only
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    placeholders = ",".join("?" * len(pids))
    sql = f"""
        SELECT
            c.canonical_code            AS card_code,
            cv.id                       AS printing_id,
            cv.variant_key,
            pmm.market_product_id,
            mp.image_url                AS tcg_image_url,
            CASE
                WHEN pmm.printing_id IS NOT NULL
                 AND mp.image_url IS NOT NULL
                 AND mp.image_url != ''
                    THEN 'TCG_URL_AVAILABLE'
                WHEN pmm.printing_id IS NOT NULL
                 AND (mp.image_url IS NULL OR mp.image_url = '')
                    THEN 'BRIDGE_EXISTS_NO_URL'
                WHEN pmm.printing_id IS NULL
                    THEN 'NO_BRIDGE'
                ELSE 'UNKNOWN'
            END AS url_status
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        LEFT JOIN printing_market_map pmm
            ON pmm.printing_id = cv.id AND pmm.is_preferred = 1
        LEFT JOIN market_products mp
            ON mp.id = pmm.market_product_id
        WHERE cv.id IN ({placeholders})
        ORDER BY cv.variant_key, c.canonical_code
    """
    db_rows = con.execute(sql, pids).fetchall()
    con.close()

    print(f"Step 2: DB rows returned: {len(db_rows)}")

    # Step 3 — Classify and report
    status_counts = {"TCG_URL_AVAILABLE": 0, "BRIDGE_EXISTS_NO_URL": 0, "NO_BRIDGE": 0, "UNKNOWN": 0}
    by_status: dict[str, list] = {"TCG_URL_AVAILABLE": [], "BRIDGE_EXISTS_NO_URL": [], "NO_BRIDGE": [], "UNKNOWN": []}

    output_rows = []
    for r in db_rows:
        s = r["url_status"]
        status_counts[s] = status_counts.get(s, 0) + 1
        by_status.setdefault(s, []).append(r)
        output_rows.append({
            "card_code": r["card_code"],
            "printing_id": r["printing_id"],
            "variant_key": r["variant_key"],
            "market_product_id": r["market_product_id"] or "",
            "tcg_image_url": r["tcg_image_url"] or "",
            "url_status": s,
        })

    print(f"\nStep 3: URL status summary")
    print("-" * 52)
    print(f"  Total alt/sp/tr/mr FETCH_NEEDED rows: {len(db_rows)}")
    print(f"  TCG_URL_AVAILABLE:    {status_counts['TCG_URL_AVAILABLE']:<4} <- automated fetch viable")
    print(f"  BRIDGE_EXISTS_NO_URL: {status_counts['BRIDGE_EXISTS_NO_URL']:<4} <- bridge exists but no URL")
    print(f"  NO_BRIDGE:            {status_counts['NO_BRIDGE']:<4} <- no bridge, needs more work")
    if status_counts["UNKNOWN"]:
        print(f"  UNKNOWN:              {status_counts['UNKNOWN']}")

    # Step 4 — Sample rows
    print(f"\nStep 4: Sample TCG_URL_AVAILABLE rows (up to 5):")
    for r in by_status["TCG_URL_AVAILABLE"][:5]:
        url = (r["tcg_image_url"] or "")[:80]
        print(f"  {r['card_code']:<12}  pid={r['printing_id']:<5}  vk={r['variant_key']:<6}  mpid={r['market_product_id']:<6}  url={url}")

    print(f"\nSample NO_BRIDGE rows (up to 5):")
    for r in by_status["NO_BRIDGE"][:5]:
        print(f"  {r['card_code']:<12}  pid={r['printing_id']:<5}  vk={r['variant_key']}")

    # Step 5 — Write CSV
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nOutput: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
