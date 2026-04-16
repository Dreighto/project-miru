"""OP01 Lane 2 asset readiness diagnostic.

Read-only disk + DB check for the 15 NOT_READY_FOR_REVIEW alt/sp/tr/mr
printing rows. Classifies each as REGISTERABLE_NOW / SOURCE_NEEDED /
AMBIGUOUS_SOURCE based on whether treatment-variant images already exist
on disk under D:\\Miru_Assets but are simply unregistered.

Usage:
    python -m tools.op01_lane2_asset_readiness

Output:
    data/overlays/op01_lane2_asset_readiness.csv

Boundary: read-only DB, disk existence checks only, no writes, no network.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "card_catalog.db"
CSV_INPUT = PROJECT_ROOT / "data" / "overlays" / "op01_lane2_candidate_check.csv"
CSV_OUTPUT = PROJECT_ROOT / "data" / "overlays" / "op01_lane2_asset_readiness.csv"
ASSET_ROOT = Path(r"D:\Miru_Assets")


def main() -> None:
    # --Step 1: Load target rows ------------------------------------------
    print("=" * 64)
    print("STEP 1 --Load NOT_READY target rows from candidate CSV")
    print("=" * 64)

    with open(CSV_INPUT, encoding="utf-8") as fh:
        all_csv = list(csv.DictReader(fh))

    # Filter to CLEAR / MULTI_MATCH rows (the ones with candidates)
    candidate_rows = [
        r for r in all_csv
        if r.get("sub_classification") in ("CLEAR", "MULTI_MATCH")
    ]

    # Deduplicate by printing_id (multiple candidates per pid possible)
    pid_map: dict[int, dict] = {}
    for r in candidate_rows:
        pid = int(r["printing_id"])
        if pid not in pid_map:
            pid_map[pid] = {
                "printing_id": pid,
                "card_code": r["card_code"],
                "variant_key": r["variant_key"],
            }

    # Check which pids already have image_assets --those are NOT not-ready
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row

    pids_with_assets: set[int] = set()
    if pid_map:
        placeholders = ",".join("?" * len(pid_map))
        rows = con.execute(
            f"SELECT DISTINCT printing_id FROM image_assets "
            f"WHERE printing_id IN ({placeholders}) AND is_primary = 1",
            list(pid_map.keys()),
        ).fetchall()
        pids_with_assets = {r["printing_id"] for r in rows}

    # NOT_READY = has candidates but no image_assets entry
    not_ready_pids = {pid for pid in pid_map if pid not in pids_with_assets}
    targets = [pid_map[pid] for pid in sorted(not_ready_pids)]

    print(f"  Candidate CSV rows (CLEAR/MULTI_MATCH): {len(candidate_rows)}")
    print(f"  Unique printing_ids with candidates:    {len(pid_map)}")
    print(f"  Already have image_assets:              {len(pids_with_assets)}")
    print(f"  NOT_READY (no image_assets):            {len(targets)}")
    print()

    if not targets:
        print("No NOT_READY rows found. Nothing to check.")
        con.close()
        return

    # --Step 2: Derive expected disk paths from DB flags ------------------
    print("=" * 64)
    print("STEP 2 --Derive expected disk paths from identity flags")
    print("=" * 64)

    target_pids = [t["printing_id"] for t in targets]
    placeholders = ",".join("?" * len(target_pids))
    flag_rows = con.execute(
        f"""SELECT cv.id AS printing_id, cv.variant_key,
                   cv.is_alt, cv.is_sp, cv.is_tr,
                   cv.is_manga_rare, cv.is_golden_manga_rare,
                   cv.is_illustration_rare,
                   c.canonical_code
            FROM card_variants cv
            JOIN cards c ON c.id = cv.card_id
            WHERE cv.id IN ({placeholders})""",
        target_pids,
    ).fetchall()
    con.close()

    flag_map: dict[int, dict] = {}
    for r in flag_rows:
        flag_map[int(r["printing_id"])] = dict(r)

    # Build candidate paths per printing_id
    enriched: list[dict] = []
    for t in targets:
        pid = t["printing_id"]
        code = t["card_code"]
        vkey = t["variant_key"]
        flags = flag_map.get(pid, {})

        is_alt = flags.get("is_alt", 0)
        is_sp = flags.get("is_sp", 0)
        is_tr = flags.get("is_tr", 0)
        is_mr = flags.get("is_manga_rare", 0)
        is_gmr = flags.get("is_golden_manga_rare", 0)
        is_ir = flags.get("is_illustration_rare", 0)

        candidate_paths: list[str] = []
        notes_parts: list[str] = []

        # Primary path derivation from flags (first match)
        if is_alt:
            candidate_paths.append(f"OP01/alt/{code}_alt.png")
        if is_sp:
            candidate_paths.append(f"OP01/sp/{code}_sp.png")
            # sp might also live under alt/ in some collections
            candidate_paths.append(f"OP01/alt/{code}_sp.png")
        if is_tr:
            candidate_paths.append(f"OP01/tr/{code}_tr.png")
            # tr might also live under alt/ in some collections
            candidate_paths.append(f"OP01/alt/{code}_tr.png")
        if is_mr:
            candidate_paths.append(f"OP01/manga/{code}_mr.png")
        if is_gmr:
            candidate_paths.append(f"OP01/manga/{code}_gmr.png")
        if is_ir:
            candidate_paths.append(f"OP01/alt/{code}_ir.png")

        # Secondary: variant_key-based alternatives
        if vkey and vkey not in ("alt", "sp", "tr", "mr", "base"):
            # e.g. parallel_1 -> OP01/parallel/{code}_p1.png
            if vkey.startswith("parallel_"):
                p_num = vkey.replace("parallel_", "")
                candidate_paths.append(f"OP01/parallel/{code}_p{p_num}.png")

        # Fallback if no flags produced any paths
        if not candidate_paths:
            # Best-guess from variant_key
            if vkey == "alt":
                candidate_paths.append(f"OP01/alt/{code}_alt.png")
            elif vkey == "sp":
                candidate_paths.append(f"OP01/sp/{code}_sp.png")
                candidate_paths.append(f"OP01/alt/{code}_sp.png")
            elif vkey == "tr":
                candidate_paths.append(f"OP01/tr/{code}_tr.png")
                candidate_paths.append(f"OP01/alt/{code}_tr.png")
            elif vkey == "mr":
                candidate_paths.append(f"OP01/manga/{code}_mr.png")
            else:
                candidate_paths.append(f"OP01/alt/{code}_{vkey}.png")
            notes_parts.append("FLAG_UNSET_FALLBACK_FROM_VARIANT_KEY")

        # Deduplicate while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for p in candidate_paths:
            if p not in seen:
                seen.add(p)
                deduped.append(p)

        enriched.append({
            "card_code": code,
            "printing_id": pid,
            "variant_key": vkey,
            "is_alt": is_alt,
            "is_sp": is_sp,
            "is_tr": is_tr,
            "is_manga_rare": is_mr,
            "candidate_paths": deduped,
            "notes_parts": notes_parts,
        })

    for e in enriched:
        print(f"  {e['card_code']} pid={e['printing_id']} vkey={e['variant_key']} "
              f"-> {len(e['candidate_paths'])} candidate path(s)")
    print()

    # --Step 3: Disk existence check --------------------------------------
    print("=" * 64)
    print("STEP 3 --Disk existence check")
    print("=" * 64)

    results: list[dict] = []
    for e in enriched:
        matched: list[tuple[str, int]] = []  # (path, size_bytes)
        all_checked: list[str] = []

        for cp in e["candidate_paths"]:
            full = ASSET_ROOT / cp
            all_checked.append(cp)
            if full.is_file():
                size = full.stat().st_size
                matched.append((cp, size))

        notes = "; ".join(e["notes_parts"]) if e["notes_parts"] else ""

        if len(matched) == 0:
            status = "SOURCE_NEEDED"
            matched_path = ""
            matched_size = ""
        elif len(matched) == 1:
            status = "REGISTERABLE_NOW"
            matched_path = matched[0][0]
            matched_size = str(matched[0][1])
        else:
            status = "AMBIGUOUS_SOURCE"
            matched_path = "|".join(m[0] for m in matched)
            matched_size = "|".join(str(m[1]) for m in matched)
            notes = (notes + "; " if notes else "") + f"{len(matched)} files matched"

        row = {
            "card_code": e["card_code"],
            "printing_id": e["printing_id"],
            "variant_key": e["variant_key"],
            "is_alt": e["is_alt"],
            "is_sp": e["is_sp"],
            "is_tr": e["is_tr"],
            "is_manga_rare": e["is_manga_rare"],
            "candidate_paths_checked": "|".join(all_checked),
            "matched_path": matched_path or "",
            "matched_file_size_bytes": matched_size or "",
            "asset_status": status,
            "notes": notes,
        }
        results.append(row)
        symbol = {"REGISTERABLE_NOW": "+", "AMBIGUOUS_SOURCE": "?", "SOURCE_NEEDED": "-"}[status]
        print(f"  [{symbol}] {e['card_code']} pid={e['printing_id']} {e['variant_key']} -> {status}"
              + (f"  ({matched_path})" if matched_path else ""))

    print()

    # --Step 4: Write output CSV ------------------------------------------
    print("=" * 64)
    print("STEP 4 --Write output CSV")
    print("=" * 64)

    CSV_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "card_code", "printing_id", "variant_key", "is_alt", "is_sp", "is_tr",
        "is_manga_rare", "candidate_paths_checked", "matched_path",
        "matched_file_size_bytes", "asset_status", "notes",
    ]
    with open(CSV_OUTPUT, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    reg_count = sum(1 for r in results if r["asset_status"] == "REGISTERABLE_NOW")
    amb_count = sum(1 for r in results if r["asset_status"] == "AMBIGUOUS_SOURCE")
    src_count = sum(1 for r in results if r["asset_status"] == "SOURCE_NEEDED")

    print(f"  Written to: {CSV_OUTPUT}")
    print()
    print("  +---------------------------------------+")
    print(f"  | Total NOT_READY rows checked: {len(results):>4}  |")
    print(f"  | REGISTERABLE_NOW:             {reg_count:>4}  |")
    print(f"  | AMBIGUOUS_SOURCE:             {amb_count:>4}  |")
    print(f"  | SOURCE_NEEDED:                {src_count:>4}  |")
    print("  +---------------------------------------+")
    print()

    # --Step 5: Sample report --------------------------------------------─
    print("=" * 64)
    print("STEP 5 --Detailed report")
    print("=" * 64)

    reg_rows = [r for r in results if r["asset_status"] == "REGISTERABLE_NOW"]
    if reg_rows:
        print(f"\n  REGISTERABLE_NOW ({len(reg_rows)} rows --full list, actionable):")
        print(f"  {'card_code':<12} {'pid':>6} {'variant':<8} {'matched_path':<45} {'size':>10}")
        print(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*45} {'-'*10}")
        for r in reg_rows:
            print(f"  {r['card_code']:<12} {r['printing_id']:>6} {r['variant_key']:<8} "
                  f"{r['matched_path']:<45} {r['matched_file_size_bytes']:>10}")
    else:
        print("\n  REGISTERABLE_NOW: (none)")

    amb_rows = [r for r in results if r["asset_status"] == "AMBIGUOUS_SOURCE"]
    if amb_rows:
        print(f"\n  AMBIGUOUS_SOURCE ({len(amb_rows)} rows --operator must confirm):")
        for r in amb_rows:
            print(f"    {r['card_code']} pid={r['printing_id']} {r['variant_key']}")
            for p in r["matched_path"].split("|"):
                print(f"      ->{p}")
    else:
        print("\n  AMBIGUOUS_SOURCE: (none)")

    src_rows = [r for r in results if r["asset_status"] == "SOURCE_NEEDED"]
    if src_rows:
        show = src_rows[:5]
        print(f"\n  SOURCE_NEEDED ({len(src_rows)} rows --showing up to 5):")
        for r in show:
            paths = r["candidate_paths_checked"].split("|")
            print(f"    {r['card_code']} pid={r['printing_id']} {r['variant_key']}")
            for p in paths:
                print(f"      checked: {p}")
        if len(src_rows) > 5:
            print(f"    ... and {len(src_rows) - 5} more")
    else:
        print("\n  SOURCE_NEEDED: (none)")

    print()
    print("Boundary confirmation: DB read-only, no writes, no network requests.")


if __name__ == "__main__":
    main()
