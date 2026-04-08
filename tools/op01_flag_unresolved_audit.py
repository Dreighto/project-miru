"""
OP01 FLAG_UNRESOLVED Audit -- READ-ONLY

Classifies the 49 FLAG_UNRESOLVED rows from the missing asset discovery pass
as SCAN_RULE_GAP, FLAG_MISSING, or AMBIGUOUS.

No DB writes. No network requests.
"""

import sqlite3
import csv
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
DISCOVERY_CSV = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_missing_asset_discovery.csv")
OUTPUT_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_flag_unresolved_audit.csv")

FIELDS = [
    "card_code", "printing_id", "variant_key",
    "is_base", "is_sp", "is_tr", "is_alt",
    "is_manga_rare", "is_golden_manga_rare", "is_illustration_rare", "is_promo",
    "source", "audit_classification", "expected_flag", "action_needed",
]

# Scan coherence rules that require a specific flag:
#   variant_key exact match -> required flag column
FLAG_RULES = {
    "base": "is_base",
    "sp":   "is_sp",
    "tr":   "is_tr",
    "alt":  "is_alt",
}

# variant_key patterns handled by the scan without a flag requirement:
#   LIKE 'p%' -> coherent unconditionally (parallels)
#   LIKE 'r%' -> requires is_base = 1 (reprints)


def classify(variant_key: str, flags: dict) -> tuple[str, str, str]:
    """Return (audit_classification, expected_flag, action_needed)."""
    vk = (variant_key or "").strip().lower()

    # 1. Exact-match rules that need a flag
    if vk in FLAG_RULES:
        required = FLAG_RULES[vk]
        if flags.get(required):
            # Flag is set -- should already be coherent; shouldn't be here
            return "AMBIGUOUS", required, "MANUAL_REVIEW"
        return "FLAG_MISSING", required, "PATCH_FLAG"

    # 2. Promo: clear treatment name, has a dedicated flag
    if vk == "promo":
        if flags.get("is_promo"):
            return "AMBIGUOUS", "is_promo", "MANUAL_REVIEW"
        return "FLAG_MISSING", "is_promo", "PATCH_FLAG"

    # 3. Reprint pattern: r1, r2, ... -> requires is_base
    if vk.startswith("r") and len(vk) >= 2 and vk[1:].isdigit():
        if flags.get("is_base"):
            return "AMBIGUOUS", "is_base", "MANUAL_REVIEW"
        return "FLAG_MISSING", "is_base", "PATCH_FLAG"

    # 4. Parallel pattern: p1, p2, parallel_1, ... -> no flag needed, always coherent
    #    These shouldn't land here, but guard anyway.
    if vk.startswith("p"):
        return "AMBIGUOUS", "", "MANUAL_REVIEW"

    # 5. Known non-canonical variant_keys with no scan branch
    if vk in ("operator_review", "mr", "gmr", "ir"):
        return "SCAN_RULE_GAP", "", "PATCH_SCAN"

    # 6. Empty / unknown
    if not vk:
        return "AMBIGUOUS", "", "MANUAL_REVIEW"

    # 7. Anything else we don't recognise
    return "SCAN_RULE_GAP", "", "PATCH_SCAN"


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Step 1 -- extract FLAG_UNRESOLVED printing_ids from discovery CSV
    with open(DISCOVERY_CSV, encoding="utf-8") as f:
        disc_rows = [r for r in csv.DictReader(f) if r["discovery_status"] == "FLAG_UNRESOLVED"]
    pids = [int(r["printing_id"]) for r in disc_rows]
    print(f"Step 1: FLAG_UNRESOLVED rows from discovery CSV: {len(pids)}")

    # Step 2 -- query card_variants (read-only)
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(pids))
    sql = f"""
        SELECT
            c.canonical_code,
            cv.id              AS printing_id,
            cv.variant_key,
            cv.is_base,
            cv.is_sp,
            cv.is_tr,
            cv.is_alt,
            cv.is_manga_rare,
            cv.is_golden_manga_rare,
            cv.is_illustration_rare,
            cv.is_promo,
            cv.source
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        WHERE cv.id IN ({placeholders})
        ORDER BY c.canonical_code, cv.variant_key
    """
    db_rows = con.execute(sql, pids).fetchall()
    con.close()
    print(f"Step 2: card_variants rows returned: {len(db_rows)}")

    # Step 3 -- classify each row
    counts: dict[str, int] = {"SCAN_RULE_GAP": 0, "FLAG_MISSING": 0, "AMBIGUOUS": 0}
    vk_by_class: dict[str, dict[str, int]] = {
        "SCAN_RULE_GAP": {},
        "FLAG_MISSING": {},
        "AMBIGUOUS": {},
    }

    output_rows = []
    for r in db_rows:
        flags = {
            "is_base": bool(r["is_base"]),
            "is_sp": bool(r["is_sp"]),
            "is_tr": bool(r["is_tr"]),
            "is_alt": bool(r["is_alt"]),
            "is_manga_rare": bool(r["is_manga_rare"]),
            "is_golden_manga_rare": bool(r["is_golden_manga_rare"]),
            "is_illustration_rare": bool(r["is_illustration_rare"]),
            "is_promo": bool(r["is_promo"]),
        }

        classification, expected_flag, action = classify(r["variant_key"], flags)
        counts[classification] += 1

        vk = r["variant_key"] or "(empty)"
        label = f"{vk} -> {expected_flag}" if expected_flag else vk
        vk_by_class[classification][label] = vk_by_class[classification].get(label, 0) + 1

        output_rows.append({
            "card_code": r["canonical_code"],
            "printing_id": r["printing_id"],
            "variant_key": r["variant_key"],
            "is_base": int(flags["is_base"]),
            "is_sp": int(flags["is_sp"]),
            "is_tr": int(flags["is_tr"]),
            "is_alt": int(flags["is_alt"]),
            "is_manga_rare": int(flags["is_manga_rare"]),
            "is_golden_manga_rare": int(flags["is_golden_manga_rare"]),
            "is_illustration_rare": int(flags["is_illustration_rare"]),
            "is_promo": int(flags["is_promo"]),
            "source": r["source"] or "",
            "audit_classification": classification,
            "expected_flag": expected_flag,
            "action_needed": action,
        })

    # Step 4 -- write CSV
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nOP01 FLAG_UNRESOLVED Audit -- {len(output_rows)} rows")
    print("-" * 52)
    for cls in ("SCAN_RULE_GAP", "FLAG_MISSING", "AMBIGUOUS"):
        action = {"SCAN_RULE_GAP": "PATCH_SCAN", "FLAG_MISSING": "PATCH_FLAG", "AMBIGUOUS": "MANUAL_REVIEW"}[cls]
        print(f"  {cls} ({action}): {counts[cls]}")
        for label, cnt in sorted(vk_by_class[cls].items(), key=lambda x: -x[1]):
            print(f"    {label}: {cnt}")

    print(f"\nOutput: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
