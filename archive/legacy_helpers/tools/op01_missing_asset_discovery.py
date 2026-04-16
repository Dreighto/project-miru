"""
OP01 Missing Image Asset Discovery -- READ-ONLY

Reads the 227 MISSING_IMAGE_ASSET rows from the certification scan CSV,
pulls identity flags from card_variants via read-only DB access,
derives expected on-disk paths, and checks whether the file already
exists under D:\\Miru_Assets.

No DB writes. No network requests. No fetches.
"""

import sqlite3
import csv
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
CERT_CSV = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_certification_scan.csv")
MIRU_ASSETS = Path(r"D:\Miru_Assets")
OUTPUT_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_missing_asset_discovery.csv")

FIELDS = [
    "card_code", "printing_id", "variant_key",
    "is_base", "is_sp", "is_tr", "is_alt",
    "is_manga_rare", "is_golden_manga_rare", "is_illustration_rare", "is_promo",
    "expected_path", "discovery_status",
]


def derive_expected_path(code, vk, flags):
    """Apply PATH DERIVATION RULES in priority order. Returns (path, flag_status)."""
    if flags["is_base"]:
        return f"OP01/base/{code}.png", None
    if flags["is_sp"]:
        return f"OP01/sp/{code}_sp.png", None
    if flags["is_tr"]:
        return f"OP01/tr/{code}_tr.png", None
    if flags["is_alt"]:
        return f"OP01/alt/{code}_alt.png", None
    if flags["is_manga_rare"]:
        return f"OP01/manga/{code}_mr.png", None
    if flags["is_golden_manga_rare"]:
        return f"OP01/manga/{code}_gmr.png", None
    if flags["is_illustration_rare"]:
        return f"OP01/alt/{code}_ir.png", None
    if flags["is_promo"]:
        return f"P/base/{code}.png", None
    # parallel_N → _pN  (e.g. parallel_1 → _p1)
    if vk.startswith("parallel_"):
        suffix = vk.replace("parallel_", "p")
        return f"OP01/parallel/{code}_{suffix}.png", None
    if vk.startswith("p") and vk not in ("promo",):
        return f"OP01/parallel/{code}_{vk}.png", None

    # Fallback — no flag matched and no variant_key rule applies
    return f"OP01/base/{code}.png", "FLAG_UNRESOLVED"


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Step 1 -- load MISSING_IMAGE_ASSET rows from certification CSV
    with open(CERT_CSV, encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))
    missing = [r for r in all_rows if r["certification_status"] == "MISSING_IMAGE_ASSET"]
    print(f"MISSING_IMAGE_ASSET rows loaded: {len(missing)}")

    # Pull identity flags from card_variants (read-only)
    printing_ids = [int(r["printing_id"]) for r in missing]
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    placeholders = ",".join("?" * len(printing_ids))
    flag_rows = con.execute(
        f"SELECT id, is_base, is_sp, is_tr, is_alt, is_manga_rare, "
        f"is_golden_manga_rare, is_illustration_rare, is_promo "
        f"FROM card_variants WHERE id IN ({placeholders})",
        printing_ids,
    ).fetchall()
    con.close()

    flag_map = {}
    for fr in flag_rows:
        flag_map[fr["id"]] = {
            "is_base": bool(fr["is_base"]),
            "is_sp": bool(fr["is_sp"]),
            "is_tr": bool(fr["is_tr"]),
            "is_alt": bool(fr["is_alt"]),
            "is_manga_rare": bool(fr["is_manga_rare"]),
            "is_golden_manga_rare": bool(fr["is_golden_manga_rare"]),
            "is_illustration_rare": bool(fr["is_illustration_rare"]),
            "is_promo": bool(fr["is_promo"]),
        }
    print(f"Identity flags loaded for {len(flag_map)} printing_ids")

    counts = {"DISK_MATCH_FOUND": 0, "FETCH_NEEDED": 0, "FLAG_UNRESOLVED": 0}
    output_rows = []

    # Step 2 -- disk existence check
    for r in missing:
        pid = int(r["printing_id"])
        code = r["card_code"]
        vk = r["variant_key"]
        flags = flag_map.get(pid, {
            "is_base": False, "is_sp": False, "is_tr": False, "is_alt": False,
            "is_manga_rare": False, "is_golden_manga_rare": False,
            "is_illustration_rare": False, "is_promo": False,
        })

        expected, flag_status = derive_expected_path(code, vk, flags)
        full = MIRU_ASSETS / expected

        if flag_status == "FLAG_UNRESOLVED":
            # Derived path is a guess — do not claim a match against an
            # unrelated base image; always report as unresolved.
            status = "FLAG_UNRESOLVED"
        elif full.exists():
            status = "DISK_MATCH_FOUND"
        else:
            status = "FETCH_NEEDED"

        counts[status] += 1
        output_rows.append({
            "card_code": code,
            "printing_id": pid,
            "variant_key": vk,
            "is_base": int(flags["is_base"]),
            "is_sp": int(flags["is_sp"]),
            "is_tr": int(flags["is_tr"]),
            "is_alt": int(flags["is_alt"]),
            "is_manga_rare": int(flags["is_manga_rare"]),
            "is_golden_manga_rare": int(flags["is_golden_manga_rare"]),
            "is_illustration_rare": int(flags["is_illustration_rare"]),
            "is_promo": int(flags["is_promo"]),
            "expected_path": expected,
            "discovery_status": status,
        })

    # Step 3 -- write CSV
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"\nOP01 Missing Asset Discovery — {len(output_rows)} rows")
    print("-" * 48)
    for status in ("DISK_MATCH_FOUND", "FETCH_NEEDED", "FLAG_UNRESOLVED"):
        print(f"  {status:<28} {counts[status]}")
    print(f"\nOutput: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
