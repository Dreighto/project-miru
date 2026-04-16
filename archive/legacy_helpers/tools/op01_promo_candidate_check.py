"""
OP01 Promo Candidate Check -- READ-ONLY

Checks whether market_products candidate rows exist for the 20 promo
printing_ids currently sitting in MISSING_PRICE_BRIDGE.

No DB writes. No network requests.
"""

import sqlite3
import csv
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
FETCH_LOG_CSV = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_promo_fetch_log.csv")
OUTPUT_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_promo_candidate_check.csv")

PROMO_SIGNAL_KEYWORDS = [
    "promo", "pre-release", "tournament", "league",
    "event", "championship", "winner", "stamped",
]

OUTPUT_FIELDS = [
    "card_code", "printing_id", "market_product_id", "product_name",
    "image_url", "candidate_status", "sub_classification",
]


def sub_classify(product_name: str) -> str:
    """Classify a single candidate row by promo signal keywords."""
    lower = (product_name or "").lower()
    for kw in PROMO_SIGNAL_KEYWORDS:
        if kw in lower:
            return "PROMO_SIGNAL"
    return "PLAIN_MATCH"


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Step 1 -- Load target rows from fetch log
    with open(FETCH_LOG_CSV, encoding="utf-8") as f:
        log_rows = [
            r for r in csv.DictReader(f)
            if r.get("fetch_status") == "FETCH_SUCCESS"
        ]

    targets = [(r["card_code"], int(r["printing_id"])) for r in log_rows]
    print(f"Step 1: Target rows from fetch log: {len(targets)}")
    for cc, pid in targets:
        print(f"  {cc}  pid={pid}")

    pids = [t[1] for t in targets]

    # Step 2 -- Query DB read-only
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    placeholders = ",".join("?" * len(pids))
    sql = f"""
        SELECT
            c.canonical_code            AS card_code,
            cv.id                       AS printing_id,
            cv.variant_key,
            mp.id                       AS market_product_id,
            mp.product_name,
            mp.image_url
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        JOIN market_products mp ON (
            mp.product_name LIKE '%' || c.canonical_code || '%'
        )
        WHERE cv.id IN ({placeholders})
        ORDER BY c.canonical_code, mp.product_name
    """
    db_rows = con.execute(sql, pids).fetchall()
    con.close()

    print(f"\nStep 2: DB candidate rows returned: {len(db_rows)}")

    # Step 3 -- Classify
    # Group candidates by printing_id
    candidates_by_pid: dict[int, list] = {}
    for r in db_rows:
        pid = r["printing_id"]
        candidates_by_pid.setdefault(pid, []).append(r)

    output_rows = []
    candidate_exists_pids = set()
    no_candidate_pids = []

    sub_counts = {"PROMO_SIGNAL": 0, "PLAIN_MATCH": 0, "AMBIGUOUS": 0}

    for cc, pid in targets:
        cands = candidates_by_pid.get(pid, [])
        if not cands:
            no_candidate_pids.append((cc, pid))
            output_rows.append({
                "card_code": cc,
                "printing_id": pid,
                "market_product_id": "",
                "product_name": "",
                "image_url": "",
                "candidate_status": "NO_CANDIDATE",
                "sub_classification": "",
            })
            continue

        candidate_exists_pids.add(pid)

        # Sub-classify each candidate individually
        subs = [sub_classify(r["product_name"]) for r in cands]
        unique_subs = set(subs)

        # If mixed signals across candidates for this pid, mark all as AMBIGUOUS
        if len(cands) > 1 and len(unique_subs) > 1:
            pid_sub = "AMBIGUOUS"
        elif len(cands) > 1 and unique_subs == {"PLAIN_MATCH"}:
            pid_sub = "AMBIGUOUS"  # multiple plain matches, ambiguous which is the promo
        else:
            pid_sub = subs[0]

        sub_counts[pid_sub] = sub_counts.get(pid_sub, 0) + 1

        for r in cands:
            row_sub = pid_sub if len(cands) > 1 else sub_classify(r["product_name"])
            output_rows.append({
                "card_code": cc,
                "printing_id": pid,
                "market_product_id": r["market_product_id"],
                "product_name": r["product_name"],
                "image_url": r["image_url"] or "",
                "candidate_status": "CANDIDATE_EXISTS",
                "sub_classification": row_sub,
            })

    print(f"\nStep 3: Classification summary")
    print("-" * 52)
    print(f"  CANDIDATE_EXISTS (unique printing_ids): {len(candidate_exists_pids)}")
    print(f"    PROMO_SIGNAL: {sub_counts['PROMO_SIGNAL']}")
    print(f"    PLAIN_MATCH:  {sub_counts['PLAIN_MATCH']}")
    print(f"    AMBIGUOUS:    {sub_counts['AMBIGUOUS']}")
    print(f"  NO_CANDIDATE: {len(no_candidate_pids)}")

    # Step 4 -- Report
    print(f"\nStep 4: ALL CANDIDATE_EXISTS rows:")
    for r in output_rows:
        if r["candidate_status"] == "CANDIDATE_EXISTS":
            url = (r["image_url"] or "")[:60]
            print(
                f"  {r['card_code']:<12}  pid={r['printing_id']:<5}  "
                f"mpid={r['market_product_id']:<6}  sub={r['sub_classification']:<14}  "
                f"name={r['product_name']}"
            )
            if url:
                print(f"    url={url}")

    print(f"\n  NO_CANDIDATE rows (up to 5):")
    for cc, pid in no_candidate_pids[:5]:
        print(f"  {cc:<12}  pid={pid}")
    if len(no_candidate_pids) > 5:
        print(f"  ... and {len(no_candidate_pids) - 5} more")

    # Step 5 -- Write CSV
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nOutput: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
