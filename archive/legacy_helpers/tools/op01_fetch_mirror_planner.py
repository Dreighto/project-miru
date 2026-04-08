#!/usr/bin/env python3
"""
op01_fetch_mirror_planner.py -- Read-only OP01 fetch/mirror planning pass

Reads the completed OPTCG OP01 normalizer CSV, builds a 37-row active set
(LOCAL_HAS_NO_IMAGE_ASSET) plus 2 held SKIP rows, derives local paths from
OPTCG source truth using the locked suffix taxonomy, checks disk presence
(informational only), and writes a 39-row planning CSV.

NO DB WRITES.  NO IMAGE FETCHES.  NO PM CHANGES.  NO SERVICE RESTARTS.
NO MODIFICATIONS TO D:\\Miru_Assets.
"""
from __future__ import annotations

import csv
import io
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
INPUT_CSV = ROOT / "data" / "overlays" / "optcg_api_op01_normalized.csv"
OUTPUT_CSV = ROOT / "data" / "overlays" / "op01_fetch_mirror_planning.csv"
MIRU_ASSETS = Path("D:/Miru_Assets")

# ── Locked suffix taxonomy ──────────────────────────────────────────────
SUFFIX_TO_SUBFOLDER = {
    "base":  "base",
    "_p1":   "parallel",
    "_p2":   "parallel",
    "_p3":   "parallel",
    "_p4":   "parallel",
    "_p5":   "parallel",
    "_p7":   "parallel",
    "_p8":   "parallel",
    "_r1":   "parallel",
    "_r2":   "parallel",
    "_r3":   "parallel",
}

# ── Locked provenance folder mapping ────────────────────────────────────
SET_NAME_TO_FOLDER = {
    "Romance Dawn":                         "OP01",
    "Premium Booster -The Best-":           "PRB01",
    "Premium Booster -The Best- Vol. 2":    "PRB02",
    "Pillars of Strength":                  "OP03",
    "Kingdoms of Intrigue":                 "OP04",
    "Awakening of the New Era":             "OP05",
    "500 Years in the Future":              "OP07",
}

# ── Output columns (exact order) ────────────────────────────────────────
OUTPUT_COLUMNS = [
    "planning_classification",
    "provenance_folder",
    "card_code",
    "card_image_id",
    "treatment_suffix",
    "derived_local_filename",
    "derived_local_subfolder",
    "derived_full_local_path",
    "official_source_url",
    "mirror_fallback_source",
    "mirror_fallback_url",
    "mirror_fallback_limitation",
    "acquisition_wave",
    "batch_group",
    "held_skip_reason",
    "fail_closed_note",
    "local_file_present",
]

MIRROR_FALLBACK_LIMITATION = (
    "Base-code only; not suffix-aware; may not represent "
    "distributed print provenance or parallel treatments"
)


def extract_suffix(card_id: str, card_image_id: str) -> str:
    """Return the raw suffix (e.g. '_p1') or 'base' if no suffix."""
    if card_image_id and card_image_id.startswith(card_id):
        sfx = card_image_id[len(card_id):]
        return sfx if sfx else "base"
    return card_image_id or ""


def derive_filename(card_id: str, suffix: str) -> str:
    """Derive the local filename from card_id and suffix."""
    if suffix == "base":
        return f"{card_id}.png"
    return f"{card_id}{suffix}.png"


def build_active_row(r: dict[str, str]) -> dict[str, str]:
    """Build a planning row for an active FETCH_OR_MIRROR_NEEDED entry."""
    card_id = r["card_id"]
    card_image_id = r["card_image_id"]
    set_name = r["set_name"]
    official_url = (r.get("card_image_url") or "").strip()

    suffix = extract_suffix(card_id, card_image_id)
    fail_notes: list[str] = []
    held_reason = ""

    # Provenance folder
    provenance_folder = SET_NAME_TO_FOLDER.get(set_name, "")
    if not provenance_folder:
        held_reason = "PROVENANCE_UNKNOWN"
        fail_notes.append(
            "set_name not in approved provenance folder mapping — "
            "requires operator review."
        )

    # Subfolder from suffix
    subfolder = SUFFIX_TO_SUBFOLDER.get(suffix, "")
    if not subfolder:
        held_reason = "SUFFIX_UNKNOWN"
        fail_notes.append(
            "Suffix not in locked taxonomy — do not auto-classify. "
            "Requires operator review before any action."
        )

    # Filename
    filename = derive_filename(card_id, suffix) if subfolder else ""

    # Full local path
    if provenance_folder and subfolder and filename:
        full_path = str(MIRU_ASSETS / provenance_folder / subfolder / filename)
    else:
        full_path = ""

    # Classification
    classification = "FETCH_OR_MIRROR_NEEDED"
    if held_reason:
        classification = "SKIP"

    # Missing official URL check (fail-closed)
    if not official_url and not held_reason:
        fail_notes.append(
            "Official OPTCG API source URL missing in normalized CSV — "
            "future execution pass must not auto-fetch without operator review."
        )

    # Local file presence check (informational only)
    local_present = ""
    if full_path:
        local_present = "True" if Path(full_path).is_file() else "False"

    # Wave/batch
    if held_reason:
        wave = ""
        batch = ""
    elif provenance_folder == "PRB01":
        wave = "1"
        batch = "PRB01-WAVE1"
    else:
        wave = "2"
        batch = f"{provenance_folder}-WAVE2"

    # Mirror fallback
    mirror_url = (
        f"https://en.onepiece-cardgame.com/images/cardlist/card/{card_id}.png"
    )

    return {
        "planning_classification": classification,
        "provenance_folder": provenance_folder,
        "card_code": card_id,
        "card_image_id": card_image_id,
        "treatment_suffix": suffix,
        "derived_local_filename": filename,
        "derived_local_subfolder": subfolder,
        "derived_full_local_path": full_path,
        "official_source_url": official_url,
        "mirror_fallback_source": "BANDAI_BASE_CDN_HINT",
        "mirror_fallback_url": mirror_url,
        "mirror_fallback_limitation": MIRROR_FALLBACK_LIMITATION,
        "acquisition_wave": wave,
        "batch_group": batch,
        "held_skip_reason": held_reason,
        "fail_closed_note": "; ".join(fail_notes) if fail_notes else "",
        "local_file_present": local_present,
    }


def build_skip_row(
    r: dict[str, str],
    held_reason: str,
    fail_note: str,
) -> dict[str, str]:
    """Build a SKIP row for a held entry."""
    card_id = r["card_id"]
    return {
        "planning_classification": "SKIP",
        "provenance_folder": SET_NAME_TO_FOLDER.get(r["set_name"], ""),
        "card_code": card_id,
        "card_image_id": r["card_image_id"],
        "treatment_suffix": extract_suffix(card_id, r["card_image_id"]),
        "derived_local_filename": "",
        "derived_local_subfolder": "",
        "derived_full_local_path": "",
        "official_source_url": (r.get("card_image_url") or "").strip(),
        "mirror_fallback_source": "",
        "mirror_fallback_url": "",
        "mirror_fallback_limitation": "",
        "acquisition_wave": "",
        "batch_group": "",
        "held_skip_reason": held_reason,
        "fail_closed_note": fail_note,
        "local_file_present": "",
    }


def main() -> int:
    print("=" * 70)
    print("OP01 FETCH/MIRROR PLANNING PASS — Read-Only")
    print("=" * 70)
    print(f"  Input CSV : {INPUT_CSV}")
    print(f"  Output CSV: {OUTPUT_CSV}")
    print(f"  Assets    : {MIRU_ASSETS}")
    print()

    # ── STEP 1: Load and validate active set ─────────────────────────────
    print("STEP 1 — BUILD ACTIVE SET")
    print("-" * 50)

    if not INPUT_CSV.is_file():
        print(f"  ERROR: Input CSV not found: {INPUT_CSV}")
        return 1

    all_rows: list[dict[str, str]] = []
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            all_rows.append(row)

    active_rows = [r for r in all_rows if r["gap_status"] == "LOCAL_HAS_NO_IMAGE_ASSET"]
    print(f"  Total normalized CSV rows: {len(all_rows)}")
    print(f"  LOCAL_HAS_NO_IMAGE_ASSET:  {len(active_rows)}")

    if len(active_rows) != 37:
        print(f"\n  STOP: Active row count mismatch — expected 37, got {len(active_rows)}")
        print(f"\n  STATUS: INCONCLUSIVE")
        return 1

    print(f"  Count == 37: PASS — proceeding to Step 2")
    print()

    # ── STEP 2: Build held set ───────────────────────────────────────────
    print("STEP 2 — BUILD HELD SET")
    print("-" * 50)

    # Find OP01-001 base row (any gap_status)
    op01_001_rows = [r for r in all_rows if r["card_id"] == "OP01-001"]
    # Find OP01-004 base row (any gap_status)
    op01_004_rows = [r for r in all_rows if r["card_id"] == "OP01-004"]

    if not op01_001_rows:
        print("  ERROR: OP01-001 not found in normalized CSV")
        return 1
    if not op01_004_rows:
        print("  ERROR: OP01-004 not found in normalized CSV")
        return 1

    # Take the first (base) row for each
    op01_001 = op01_001_rows[0]
    op01_004 = op01_004_rows[0]

    print(f"  OP01-001: card_image_id={op01_001['card_image_id']}, "
          f"gap_status={op01_001['gap_status']}")
    print(f"  OP01-004: card_image_id={op01_004['card_image_id']}, "
          f"gap_status={op01_004['gap_status']}")
    print()

    # ── Process active rows ──────────────────────────────────────────────
    print("STEPS 3-5 — DERIVE PATHS, CHECK DISK, CLASSIFY")
    print("-" * 50)

    output_rows: list[dict[str, str]] = []
    suffix_unknown_rows: list[dict[str, str]] = []
    provenance_unknown_rows: list[dict[str, str]] = []
    missing_url_rows: list[dict[str, str]] = []

    for r in active_rows:
        row = build_active_row(r)
        output_rows.append(row)

        if row["held_skip_reason"] == "SUFFIX_UNKNOWN":
            suffix_unknown_rows.append(row)
        if row["held_skip_reason"] == "PROVENANCE_UNKNOWN":
            provenance_unknown_rows.append(row)
        if (
            row["planning_classification"] == "FETCH_OR_MIRROR_NEEDED"
            and not row["official_source_url"]
        ):
            missing_url_rows.append(row)

    # Append the two SKIP rows
    skip_001 = build_skip_row(
        op01_001,
        "HARD_IDENTITY_BREAK",
        "API card_name != local DB name — hard identity break under "
        "investigation. No writes or fetches until resolved.",
    )
    skip_004 = build_skip_row(
        op01_004,
        "HELD_OPERATOR_DECISION",
        "Held by operator decision. No action until operator explicitly "
        "releases this row.",
    )
    output_rows.append(skip_001)
    output_rows.append(skip_004)

    active_count = sum(
        1 for r in output_rows
        if r["planning_classification"] == "FETCH_OR_MIRROR_NEEDED"
    )
    skip_count = sum(
        1 for r in output_rows
        if r["planning_classification"] == "SKIP"
    )

    print(f"  Active FETCH_OR_MIRROR_NEEDED: {active_count}")
    print(f"  SKIP rows:                     {skip_count}")
    print(f"  Total output rows:             {len(output_rows)}")
    print()

    # ── Row count contract ───────────────────────────────────────────────
    if len(output_rows) != 39:
        print(f"  STOP: Final row count mismatch — expected 39, got {len(output_rows)}")
        print(f"\n  STATUS: INCONCLUSIVE")
        return 1

    # ── STEP 6: Write output CSV ─────────────────────────────────────────
    print("STEP 6 — WRITE PLANNING CSV")
    print("-" * 50)

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        w.writerows(output_rows)

    print(f"  Path: {OUTPUT_CSV.relative_to(ROOT)}")
    print(f"  Rows: {len(output_rows)}")
    print()

    # ── VERIFICATION BLOCK ───────────────────────────────────────────────
    local_true = sum(1 for r in output_rows if r["local_file_present"] == "True")
    local_false = sum(1 for r in output_rows if r["local_file_present"] == "False")

    prb01_wave1 = sum(1 for r in output_rows if r["batch_group"] == "PRB01-WAVE1")

    # Wave 2 by provenance folder
    wave2_folders: dict[str, int] = {}
    for r in output_rows:
        if r["acquisition_wave"] == "2":
            pf = r["provenance_folder"]
            wave2_folders[pf] = wave2_folders.get(pf, 0) + 1

    print("=" * 70)
    print("VERIFICATION BLOCK")
    print("=" * 70)
    print(f"  Total rows written: {len(output_rows)}")
    print(f"  Active FETCH_OR_MIRROR_NEEDED rows: {active_count}")
    print(f"  SKIP rows: {skip_count}")
    print(f"  Rows missing official_source_url: {len(missing_url_rows)}")
    print(f"  Rows with local_file_present=True: {local_true}   [informational]")
    print(f"  Rows with local_file_present=False: {local_false}  [informational]")
    print(f"  PRB01-WAVE1 rows: {prb01_wave1}")

    wave2_parts = ", ".join(
        f"{k}={v}" for k, v in sorted(wave2_folders.items())
    )
    print(f"  Other Wave 2 rows by provenance_folder: {wave2_parts}")
    print(f"  SUFFIX_UNKNOWN rows: {len(suffix_unknown_rows)}")
    print(f"  PROVENANCE_UNKNOWN rows: {len(provenance_unknown_rows)}")
    print(f"  Output file: {OUTPUT_CSV.relative_to(ROOT)} — WRITTEN")
    print()

    # ── COMPLETION CONTRACT ──────────────────────────────────────────────
    if len(output_rows) == 39 and active_count == 37 and skip_count == 2:
        print("=" * 70)
        print("STATUS: CONFIRMED WORKING")
        print("=" * 70)
        print()
        print("  Scope boundary honored:")
        print("    - No DB writes performed")
        print("    - No image fetches performed")
        print("    - No D:\\Miru_Assets writes or modifications")
        print("    - No PM (port 18080) touch")
        print("    - No scope past OP01")
        print()
        print("  Authority sources used:")
        print("    - OPTCG API normalized CSV as input")
        print("    - Source Authority Registry rules applied (locked suffix taxonomy)")
        print("    - Bandai CDN listed as HINT only (not executable)")
        print()
        print("  Remaining uncertainty:")
        if suffix_unknown_rows:
            for r in suffix_unknown_rows:
                print(f"    - SUFFIX_UNKNOWN: {r['card_code']} ({r['card_image_id']})")
        if provenance_unknown_rows:
            for r in provenance_unknown_rows:
                print(f"    - PROVENANCE_UNKNOWN: {r['card_code']} ({r['card_image_id']})")
        if missing_url_rows:
            for r in missing_url_rows:
                print(f"    - MISSING_URL: {r['card_code']} ({r['card_image_id']})")
        if not (suffix_unknown_rows or provenance_unknown_rows or missing_url_rows):
            print("    - None — all rows clean")
        print()
        print("  Execution-ready next prompt:")
        print(f"    Operator must review {OUTPUT_CSV.relative_to(ROOT)}")
        print("    before any fetch pass proceeds.")
        return 0
    else:
        print("=" * 70)
        print("STATUS: INCONCLUSIVE")
        print("=" * 70)
        print(f"  Row count: {len(output_rows)} (expected 39)")
        print(f"  Active: {active_count} (expected 37)")
        print(f"  Skip: {skip_count} (expected 2)")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
