"""
OP01 Lane 2 Candidate Check -- READ-ONLY

Checks whether market_products candidate rows exist for the 90 alt/sp/tr/mr
FETCH_NEEDED printing rows that currently have NO_BRIDGE status.

No DB writes. No network requests.
"""

import sqlite3
import csv
from collections import Counter, defaultdict
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
URL_DIAG_CSV = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_lane2_url_diagnostic.csv")
OUTPUT_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_lane2_candidate_check.csv")

OUTPUT_FIELDS = [
    "card_code", "printing_id", "variant_key", "market_product_id",
    "product_name", "image_url", "existing_preferred_pid",
    "candidate_status", "sub_classification",
]


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ── Step 1 ─────────────────────────────────────────────────
    with open(URL_DIAG_CSV, encoding="utf-8") as f:
        targets = [
            r for r in csv.DictReader(f)
            if r["url_status"] == "NO_BRIDGE"
        ]

    vk_counts = Counter(r["variant_key"] for r in targets)
    pids = [int(r["printing_id"]) for r in targets]

    print(f"Step 1: NO_BRIDGE rows from Lane 2 diagnostic: {len(targets)}")
    for vk in ("alt", "sp", "tr", "mr"):
        print(f"  {vk}: {vk_counts.get(vk, 0)}")

    # Build a lookup: pid -> (card_code, variant_key)
    pid_meta = {int(r["printing_id"]): (r["card_code"], r["variant_key"]) for r in targets}

    # ── Step 2 ─────────────────────────────────────────────────
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    placeholders = ",".join("?" * len(pids))
    sql = f"""
        SELECT
            c.canonical_code            AS card_code,
            cv.id                       AS printing_id,
            cv.variant_key,
            cv.is_alt,
            cv.is_sp,
            cv.is_tr,
            cv.is_manga_rare,
            mp.id                       AS market_product_id,
            mp.product_name,
            mp.image_url,
            (SELECT pmm2.printing_id
             FROM printing_market_map pmm2
             WHERE pmm2.market_product_id = mp.id
               AND pmm2.is_preferred = 1
               AND pmm2.printing_id != cv.id
             LIMIT 1)                   AS existing_preferred_pid
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        JOIN market_products mp ON (
            mp.product_name LIKE '%' || c.canonical_code || '%'
            AND (
                mp.product_name LIKE '%Alternate Art%'
                OR mp.product_name LIKE '%Alt Art%'
                OR mp.product_name LIKE '%Special%'
                OR mp.product_name LIKE '%Treasure Rare%'
                OR mp.product_name LIKE '%Manga%'
                OR mp.product_name LIKE '%SP%'
                OR mp.product_name LIKE '%TR%'
                OR mp.product_name LIKE '%Super Rare%'
                OR mp.product_name LIKE '%Secret%'
                OR mp.product_name LIKE '%Parallel%'
                OR mp.product_name LIKE '%Rainbow%'
                OR mp.product_name LIKE '%Anniversary%'
            )
        )
        WHERE cv.id IN ({placeholders})
        ORDER BY c.canonical_code, cv.variant_key, mp.product_name
    """
    db_rows = con.execute(sql, pids).fetchall()
    con.close()

    print(f"\nStep 2: DB candidate rows returned: {len(db_rows)}")

    # ── Step 3 ─────────────────────────────────────────────────
    # Group candidates by printing_id
    cands_by_pid: dict[int, list] = defaultdict(list)
    for r in db_rows:
        cands_by_pid[r["printing_id"]].append(r)

    output_rows = []
    clear_rows = []
    owned_rows = []
    no_cand_rows = []

    # Per-pid classification
    pid_status: dict[int, str] = {}  # pid -> top-level status
    pid_sub: dict[int, str] = {}     # pid -> sub_classification

    for pid in pids:
        cc, vk = pid_meta[pid]
        cands = cands_by_pid.get(pid, [])

        if not cands:
            pid_status[pid] = "NO_CANDIDATE"
            pid_sub[pid] = "NO_CANDIDATE"
            no_cand_rows.append({"card_code": cc, "printing_id": pid, "variant_key": vk})
            output_rows.append({
                "card_code": cc,
                "printing_id": pid,
                "variant_key": vk,
                "market_product_id": "",
                "product_name": "",
                "image_url": "",
                "existing_preferred_pid": "",
                "candidate_status": "NO_CANDIDATE",
                "sub_classification": "NO_CANDIDATE",
            })
            continue

        pid_status[pid] = "CANDIDATE_EXISTS"

        has_clear = False
        all_owned = True
        is_multi = len(cands) > 1

        for r in cands:
            ep = r["existing_preferred_pid"]
            if ep is None:
                has_clear = True
                all_owned = False
                row_sub = "MULTI_MATCH" if is_multi else "CLEAR"
                clear_rows.append({
                    "card_code": cc, "printing_id": pid, "variant_key": vk,
                    "market_product_id": r["market_product_id"],
                    "product_name": r["product_name"],
                    "image_url": r["image_url"] or "",
                    "existing_preferred_pid": "",
                })
            else:
                row_sub = "ALREADY_OWNED"
                owned_rows.append({
                    "card_code": cc, "printing_id": pid, "variant_key": vk,
                    "market_product_id": r["market_product_id"],
                    "product_name": r["product_name"],
                    "image_url": r["image_url"] or "",
                    "existing_preferred_pid": ep,
                })

            output_rows.append({
                "card_code": cc,
                "printing_id": pid,
                "variant_key": vk,
                "market_product_id": r["market_product_id"],
                "product_name": r["product_name"],
                "image_url": r["image_url"] or "",
                "existing_preferred_pid": ep if ep is not None else "",
                "candidate_status": "CANDIDATE_EXISTS",
                "sub_classification": row_sub,
            })

        # Determine pid-level sub
        if is_multi:
            pid_sub[pid] = "MULTI_MATCH"
        elif all_owned:
            pid_sub[pid] = "ALREADY_OWNED"
        elif has_clear:
            pid_sub[pid] = "CLEAR"
        else:
            pid_sub[pid] = "ALREADY_OWNED"

    # Counts
    ce_pids = [p for p in pids if pid_status[p] == "CANDIDATE_EXISTS"]
    nc_pids = [p for p in pids if pid_status[p] == "NO_CANDIDATE"]
    clear_pids = [p for p in ce_pids if pid_sub[p] == "CLEAR"]
    owned_only_pids = [p for p in ce_pids if pid_sub[p] == "ALREADY_OWNED"]
    multi_pids = [p for p in ce_pids if pid_sub[p] == "MULTI_MATCH"]

    print(f"\nStep 3: Classification summary")
    print("-" * 60)
    print(f"  Total rows checked: {len(pids)}")
    print(f"  CANDIDATE_EXISTS (unique pids): {len(ce_pids)}")
    print(f"    — CLEAR (at least one clear candidate): {len(clear_pids)}")
    print(f"    — ALREADY_OWNED only (all candidates owned): {len(owned_only_pids)}")
    print(f"    — MULTI_MATCH (multiple candidates): {len(multi_pids)}")
    print(f"  NO_CANDIDATE: {len(nc_pids)}")

    # Breakdown by variant_key
    print(f"\n  Breakdown by variant_key:")
    for vk in ("alt", "sp", "tr", "mr"):
        vk_pids = [p for p in pids if pid_meta[p][1] == vk]
        vk_ce = sum(1 for p in vk_pids if pid_status[p] == "CANDIDATE_EXISTS")
        vk_nc = sum(1 for p in vk_pids if pid_status[p] == "NO_CANDIDATE")
        print(f"    {vk}: CANDIDATE_EXISTS {vk_ce} / NO_CANDIDATE {vk_nc}")

    # ── Step 4 ─────────────────────────────────────────────────
    print(f"\nStep 4: Sample CLEAR candidate rows (up to 10):")
    for r in clear_rows[:10]:
        url = (r["image_url"] or "")[:60]
        print(
            f"  {r['card_code']:<12}  pid={r['printing_id']:<5}  vk={r['variant_key']:<5}  "
            f"mpid={r['market_product_id']:<5}  name={r['product_name']}"
        )
        if url:
            print(f"    url={url}")

    print(f"\n  Sample ALREADY_OWNED rows (up to 5):")
    for r in owned_rows[:5]:
        print(
            f"  {r['card_code']:<12}  pid={r['printing_id']:<5}  vk={r['variant_key']:<5}  "
            f"mpid={r['market_product_id']:<5}  owner_pid={r['existing_preferred_pid']:<5}  "
            f"name={r['product_name']}"
        )

    print(f"\n  Sample NO_CANDIDATE rows (up to 5):")
    for r in no_cand_rows[:5]:
        print(f"  {r['card_code']:<12}  pid={r['printing_id']:<5}  vk={r['variant_key']}")

    # ── Step 5 ─────────────────────────────────────────────────
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nOutput: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
