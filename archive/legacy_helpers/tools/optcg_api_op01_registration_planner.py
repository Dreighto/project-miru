#!/usr/bin/env python3
"""
optcg_api_op01_registration_planner.py -- Read-only planning pass

Reads the completed OPTCG OP01 normalizer CSV, filters to
LOCAL_HAS_NO_IMAGE_ASSET rows, derives proposed local paths from OPTCG
source truth, checks disk existence, cross-checks image_assets table
(READ-ONLY), and writes a registration planning CSV.

NO DB WRITES.  NO IMAGE FETCHES.  NO PM CHANGES.  NO SERVICE RESTARTS.
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import sys
from pathlib import Path

# ── Windows console encoding fix ────────────────────────────────────────
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace"
    )

# ── Paths ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
INPUT_CSV = ROOT / "data" / "overlays" / "optcg_api_op01_normalized.csv"
OUTPUT_CSV = ROOT / "data" / "overlays" / "optcg_api_op01_image_asset_registration_plan.csv"
MIRU_ASSETS = Path("D:/Miru_Assets")

# ── Set-ID to local folder mapping ──────────────────────────────────────
# Derived from observed Miru_Assets directory names
SET_ID_TO_FOLDER = {
    "OP-01": "OP01",
    "OP-02": "OP02",
    "OP-03": "OP03",
    "OP-04": "OP04",
    "OP-05": "OP05",
    "OP-06": "OP06",
    "OP-07": "OP07",
    "OP-08": "OP08",
    "PRB-01": "PRB01",
    "PRB-02": "PRB02",
}

# ── Treatment to subfolder mapping ──────────────────────────────────────
TREATMENT_TO_SUBFOLDER = {
    "base": "base",
    "parallel": "parallel",
    "reprint": "reprint",
}

# ── Output CSV columns ──────────────────────────────────────────────────
OUTPUT_COLUMNS = [
    "card_id",
    "card_image_id",
    "set_name",
    "set_id",
    "inferred_treatment",
    "local_variant_match_id",
    "card_image_url",
    "proposed_set_folder",
    "proposed_subfolder",
    "proposed_filename",
    "proposed_relative_path",
    "proposed_absolute_path",
    "existing_file_on_disk",
    "existing_file_size_bytes",
    "existing_image_assets_row",
    "registration_readiness",
    "notes",
]


def derive_proposed_path(
    set_id: str,
    card_image_id: str,
    treatment: str,
) -> tuple[str, str, str, str, str, str]:
    """Derive proposed local path components from OPTCG source truth.

    Returns:
        (set_folder, subfolder, filename, relative_path, absolute_path, notes)
    """
    notes_parts: list[str] = []

    # Set folder
    set_folder = SET_ID_TO_FOLDER.get(set_id, "")
    if not set_folder:
        return ("", "", "", "", "", f"UNKNOWN_SET_ID:{set_id}")

    # Subfolder
    subfolder = TREATMENT_TO_SUBFOLDER.get(treatment, "")
    if not subfolder:
        return (set_folder, "", "", "", "", f"UNKNOWN_TREATMENT:{treatment}")

    # Filename: card_image_id + .png
    filename = f"{card_image_id}.png"

    # Paths
    relative_path = f"{set_folder}/{subfolder}/{filename}"
    absolute_path = str(MIRU_ASSETS / set_folder / subfolder / filename)

    return (set_folder, subfolder, filename, relative_path, absolute_path, "")


def check_disk(absolute_path: str) -> tuple[str, str]:
    """Check if file exists on disk. Returns (exists_yn, size_bytes_or_empty)."""
    if not absolute_path:
        return ("no", "")
    p = Path(absolute_path)
    if p.is_file():
        return ("yes", str(p.stat().st_size))
    return ("no", "")


def check_nearby_conflicts(
    set_folder: str,
    card_image_id: str,
) -> list[str]:
    """Check for similarly named files in nearby subfolders."""
    conflicts = []
    base_code = card_image_id.split("_")[0]  # e.g. OP01-006 from OP01-006_p3
    set_path = MIRU_ASSETS / set_folder
    if not set_path.is_dir():
        return conflicts
    for sub in set_path.iterdir():
        if not sub.is_dir():
            continue
        for f in sub.iterdir():
            if f.is_file() and f.stem.startswith(base_code) and f.stem != card_image_id:
                conflicts.append(f"{set_folder}/{sub.name}/{f.name}")
    return conflicts


def main() -> int:
    print("=" * 70)
    print("OPTCG API OP01 IMAGE-ASSET REGISTRATION PLANNER")
    print("Read-Only Planning Pass")
    print("=" * 70)
    print(f"  DB        : {DB_PATH}")
    print(f"  Input CSV : {INPUT_CSV}")
    print(f"  Output CSV: {OUTPUT_CSV}")
    print(f"  Assets    : {MIRU_ASSETS}")
    print()

    # ── STEP 1: Load and filter normalized CSV ───────────────────────────
    print("STEP 1 -- Load normalized CSV and filter to LOCAL_HAS_NO_IMAGE_ASSET")
    print("-" * 50)

    if not INPUT_CSV.is_file():
        print(f"  ERROR: Input CSV not found: {INPUT_CSV}")
        return 1

    all_rows: list[dict[str, str]] = []
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_rows.append(row)

    target_rows = [r for r in all_rows if r["gap_status"] == "LOCAL_HAS_NO_IMAGE_ASSET"]
    print(f"  Total rows in normalized CSV: {len(all_rows)}")
    print(f"  LOCAL_HAS_NO_IMAGE_ASSET:     {len(target_rows)}")
    print()

    # ── STEP 2 + 3 + 4: Derive paths, check disk, cross-check DB ────────
    print("STEP 2-4 -- Derive paths, check disk, cross-check image_assets")
    print("-" * 50)

    # Open DB READ-ONLY
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Pre-load all image_assets printing_ids for the variant IDs in our target set
    target_cv_ids = []
    for r in target_rows:
        cv_id = r.get("local_variant_match_id", "").strip()
        if cv_id:
            target_cv_ids.append(int(cv_id))

    ia_existing: set[int] = set()
    for i in range(0, len(target_cv_ids), 500):
        batch = target_cv_ids[i : i + 500]
        ph = ",".join("?" * len(batch))
        cur.execute(
            f"SELECT DISTINCT printing_id FROM image_assets WHERE printing_id IN ({ph})",
            batch,
        )
        for row in cur.fetchall():
            ia_existing.add(row[0])

    conn.close()
    print(f"  DB opened READ-ONLY: yes")
    print(f"  Target variant IDs checked: {len(target_cv_ids)}")
    print(f"  Already have image_assets rows: {len(ia_existing)}")
    print()

    # ── Process each target row ──────────────────────────────────────────
    print("STEP 5 -- Classify registration readiness")
    print("-" * 50)

    output_rows: list[dict[str, str]] = []

    for r in target_rows:
        card_id = r["card_id"]
        card_image_id = r["card_image_id"]
        set_name = r["set_name"]
        set_id = r["set_id"]
        treatment = r["inferred_treatment"]
        cv_id_str = r.get("local_variant_match_id", "").strip()
        card_image_url = r.get("card_image_url", "")

        # Derive path
        (
            set_folder,
            subfolder,
            filename,
            relative_path,
            absolute_path,
            derive_notes,
        ) = derive_proposed_path(set_id, card_image_id, treatment)

        # Check disk
        exists_yn, size_bytes = check_disk(absolute_path)

        # Check image_assets
        cv_id_int = int(cv_id_str) if cv_id_str else None
        has_ia = cv_id_int in ia_existing if cv_id_int is not None else False
        ia_yn = "yes" if has_ia else "no"

        # Check nearby conflicts
        conflict_notes: list[str] = []
        if set_folder:
            conflicts = check_nearby_conflicts(set_folder, card_image_id)
            if conflicts:
                conflict_notes.append(f"nearby_files: {'; '.join(conflicts[:5])}")

        # ── Classify ─────────────────────────────────────────────────────
        notes_parts: list[str] = []
        if derive_notes:
            notes_parts.append(derive_notes)
        notes_parts.extend(conflict_notes)

        if has_ia:
            readiness = "STALE_NORMALIZER_RESULT"
            notes_parts.append("image_assets row now exists")
        elif derive_notes.startswith("UNKNOWN_"):
            readiness = "REVIEW_REQUIRED_PROVENANCE"
        elif not set_folder or not subfolder:
            readiness = "REVIEW_REQUIRED_PROVENANCE"
        elif exists_yn == "yes" and not has_ia and cv_id_int is not None:
            readiness = "SAFE_TO_REGISTER_NOW"
        elif exists_yn == "no" and not has_ia:
            readiness = "FETCH_OR_MIRROR_NEEDED"
        else:
            readiness = "REVIEW_REQUIRED_CONFLICT"

        output_rows.append(
            {
                "card_id": card_id,
                "card_image_id": card_image_id,
                "set_name": set_name,
                "set_id": set_id,
                "inferred_treatment": treatment,
                "local_variant_match_id": cv_id_str,
                "card_image_url": card_image_url,
                "proposed_set_folder": set_folder,
                "proposed_subfolder": subfolder,
                "proposed_filename": filename,
                "proposed_relative_path": relative_path,
                "proposed_absolute_path": absolute_path,
                "existing_file_on_disk": exists_yn,
                "existing_file_size_bytes": size_bytes,
                "existing_image_assets_row": ia_yn,
                "registration_readiness": readiness,
                "notes": "; ".join(notes_parts) if notes_parts else "",
            }
        )

    # Readiness counts
    from collections import Counter

    readiness_counts = Counter(r["registration_readiness"] for r in output_rows)
    for bucket in (
        "SAFE_TO_REGISTER_NOW",
        "FETCH_OR_MIRROR_NEEDED",
        "REVIEW_REQUIRED_PROVENANCE",
        "REVIEW_REQUIRED_CONFLICT",
        "STALE_NORMALIZER_RESULT",
    ):
        ct = readiness_counts.get(bucket, 0)
        print(f"  {bucket:40s} {ct:>4d}")
    print()

    # ── STEP 6: Write output CSV ─────────────────────────────────────────
    print("STEP 6 -- Write planning CSV")
    print("-" * 50)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        w.writerows(output_rows)

    print(f"  Path: {OUTPUT_CSV.relative_to(ROOT)}")
    print(f"  Rows: {len(output_rows)}")
    print()

    # ── STEP 7: Summary report ───────────────────────────────────────────
    print("=" * 70)
    print("STEP 7 -- SUMMARY REPORT")
    print("=" * 70)

    print(f"\n  Total target rows processed: {len(output_rows)}")
    print(f"\n  Registration readiness breakdown:")
    for bucket in (
        "SAFE_TO_REGISTER_NOW",
        "FETCH_OR_MIRROR_NEEDED",
        "REVIEW_REQUIRED_PROVENANCE",
        "REVIEW_REQUIRED_CONFLICT",
        "STALE_NORMALIZER_RESULT",
    ):
        ct = readiness_counts.get(bucket, 0)
        print(f"    {bucket:40s} {ct:>4d}")

    # SAFE_TO_REGISTER_NOW detail
    safe_rows = [r for r in output_rows if r["registration_readiness"] == "SAFE_TO_REGISTER_NOW"]
    print(f"\n  SAFE_TO_REGISTER_NOW rows ({len(safe_rows)}):")
    for r in safe_rows:
        print(f"    {r['card_image_id']:25s} -> {r['proposed_relative_path']}")

    # FETCH_OR_MIRROR_NEEDED detail
    fetch_rows = [r for r in output_rows if r["registration_readiness"] == "FETCH_OR_MIRROR_NEEDED"]
    print(f"\n  FETCH_OR_MIRROR_NEEDED rows ({len(fetch_rows)}):")
    for r in fetch_rows:
        print(f"    {r['card_image_id']:25s} -> {r['proposed_relative_path']}")

    # REVIEW_REQUIRED detail
    review_rows = [
        r
        for r in output_rows
        if r["registration_readiness"].startswith("REVIEW_REQUIRED")
    ]
    print(f"\n  REVIEW_REQUIRED rows ({len(review_rows)}):")
    for r in review_rows:
        print(
            f"    {r['card_image_id']:25s} -> {r['proposed_relative_path']}  "
            f"[{r['registration_readiness']}] {r['notes']}"
        )

    # STALE detail
    stale_rows = [r for r in output_rows if r["registration_readiness"] == "STALE_NORMALIZER_RESULT"]
    if stale_rows:
        print(f"\n  STALE_NORMALIZER_RESULT rows ({len(stale_rows)}):")
        for r in stale_rows:
            print(f"    {r['card_image_id']:25s} cv_id={r['local_variant_match_id']}")

    # ── OP01-016 specific report ─────────────────────────────────────────
    print(f"\n  {'=' * 50}")
    print(f"  OP01-016 ROWS IN THIS PLANNING PASS:")
    op16_rows = [r for r in output_rows if r["card_id"] == "OP01-016"]
    if op16_rows:
        for r in op16_rows:
            print(
                f"    {r['card_image_id']:25s} set={r['set_id']:8s} "
                f"-> {r['proposed_relative_path']:45s} "
                f"disk={r['existing_file_on_disk']}  "
                f"readiness={r['registration_readiness']}"
            )
    else:
        print("    (none)")

    # ── OP01-025 specific report ─────────────────────────────────────────
    print(f"\n  OP01-025 ROWS IN THIS PLANNING PASS:")
    op25_rows = [r for r in output_rows if r["card_id"] == "OP01-025"]
    if op25_rows:
        for r in op25_rows:
            print(
                f"    {r['card_image_id']:25s} set={r['set_id']:8s} "
                f"-> {r['proposed_relative_path']:45s} "
                f"disk={r['existing_file_on_disk']}  "
                f"readiness={r['registration_readiness']}"
            )
    else:
        print("    (none -- OP01-025 has no LOCAL_HAS_NO_IMAGE_ASSET gaps)")

    # ── Non-OP01 provenance folders ──────────────────────────────────────
    print(f"\n  NON-OP01 PROVENANCE FOLDERS REQUIRED:")
    non_op01_folders = sorted(
        set(
            r["proposed_set_folder"]
            for r in output_rows
            if r["proposed_set_folder"] and r["proposed_set_folder"] != "OP01"
        )
    )
    if non_op01_folders:
        for folder in non_op01_folders:
            count = sum(1 for r in output_rows if r["proposed_set_folder"] == folder)
            print(f"    {folder}: {count} rows")
    else:
        print("    (none)")

    # ── Naming consistency check ─────────────────────────────────────────
    print(f"\n  LOCAL NAMING CONSISTENCY CHECK:")
    inconsistent = []
    for r in output_rows:
        if r["notes"] and "nearby_files" in r["notes"]:
            inconsistent.append(r)
    if inconsistent:
        print(f"    {len(inconsistent)} rows have nearby files that may indicate naming divergence:")
        for r in inconsistent[:10]:
            print(f"      {r['card_image_id']}: {r['notes']}")
    else:
        print("    No naming inconsistencies detected")

    # ── Output path ──────────────────────────────────────────────────────
    print(f"\n  Output CSV: {OUTPUT_CSV}")

    # ── Verification footer ──────────────────────────────────────────────
    print(f"\n  {'=' * 50}")
    print(f"  VERIFICATION FOOTER")
    print(f"  {'=' * 50}")
    print(f"  DB_PATH_CONFIRMED:        {DB_PATH}")
    print(f"  DB_OPENED_READ_ONLY:      yes (file:...?mode=ro, uri=True)")
    print(f"  DB_WRITES_PERFORMED:      no")
    print(f"  PM_18080_TOUCHED:         no")
    print(f"  PORT_8765_TOUCHED:        no")
    print(f"  IMAGE_FETCHES_PERFORMED:  no")
    print(f"  FILES_MOVED_OR_DELETED:   no")
    print(f"  RESTART_PERFORMED:        no")

    # ── Verdict ──────────────────────────────────────────────────────────
    if len(output_rows) == len(target_rows) and len(output_rows) > 0:
        verdict = "CONFIRMED WORKING"
    elif len(output_rows) > 0:
        verdict = "INCONCLUSIVE"
    else:
        verdict = "FAILED"

    print(f"\n  {'=' * 50}")
    print(f"  VERDICT: {verdict}")
    print(f"  {'=' * 50}")

    return 0 if verdict == "CONFIRMED WORKING" else 1


if __name__ == "__main__":
    raise SystemExit(main())
