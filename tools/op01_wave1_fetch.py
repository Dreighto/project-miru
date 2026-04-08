#!/usr/bin/env python3
"""
op01_wave1_fetch.py -- Controlled fetch pass for PRB01-WAVE1

Downloads images from official OPTCG API URLs and writes them to
D:\\Miru_Assets\\PRB01\\parallel\\. Writes a results CSV.

NO DB WRITES.  NO REGISTRATION.  NO PM CHANGES.  NO WAVE 2.  NO SKIP ROWS.
"""
from __future__ import annotations

import csv
import io
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
INPUT_CSV = ROOT / "data" / "overlays" / "op01_fetch_mirror_planning.csv"
OUTPUT_CSV = ROOT / "data" / "overlays" / "op01_wave1_fetch_results.csv"

USER_AGENT = "ProjectMiru/1.0 (OP01-wave1-fetch; controlled pass; 1s pacing)"
HTTP_TIMEOUT = 30
PACING_SEC = 1.0

OUTPUT_COLUMNS = [
    "card_code",
    "provenance_folder",
    "derived_full_local_path",
    "official_source_url",
    "fetch_status",
    "bytes_written",
    "failure_reason",
]


def fetch_binary(url: str) -> tuple[bytes | None, str]:
    """Download binary content. Returns (data, error_reason)."""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = resp.read()
            return data, ""
    except HTTPError as e:
        return None, f"HTTP {e.code}"
    except (URLError, TimeoutError, OSError) as e:
        return None, str(e)


def main() -> int:
    print("=" * 70)
    print("OP01 WAVE 1 CONTROLLED FETCH PASS — PRB01-WAVE1")
    print("=" * 70)
    print(f"  Input CSV : {INPUT_CSV}")
    print(f"  Output CSV: {OUTPUT_CSV}")
    print()

    # ── Load and filter ──────────────────────────────────────────────────
    with open(INPUT_CSV, "r", encoding="utf-8") as f:
        all_rows = list(csv.DictReader(f))

    wave1 = [
        r for r in all_rows
        if r["planning_classification"] == "FETCH_OR_MIRROR_NEEDED"
        and r["batch_group"] == "PRB01-WAVE1"
    ]

    print(f"  Selected PRB01-WAVE1 rows: {len(wave1)}")
    if len(wave1) != 21:
        print(f"  STOP: Expected 21, got {len(wave1)}")
        print(f"  STATUS: INCONCLUSIVE")
        return 1

    # ── Execute fetches ──────────────────────────────────────────────────
    results: list[dict[str, str]] = []
    folders_created: set[str] = set()
    total_bytes = 0
    fetched_count = 0
    failed_count = 0
    skipped_count = 0

    for idx, r in enumerate(wave1, 1):
        card_code = r["card_code"]
        provenance = r["provenance_folder"]
        local_path = Path(r["derived_full_local_path"])
        url = r["official_source_url"]

        if idx > 1:
            time.sleep(PACING_SEC)

        # Check existing file
        if local_path.is_file() and local_path.stat().st_size > 0:
            size = local_path.stat().st_size
            print(f"  [{idx:>2}/21] SKIPPED_EXISTING  {local_path.name}  ({size} bytes)")
            results.append({
                "card_code": card_code,
                "provenance_folder": provenance,
                "derived_full_local_path": str(local_path),
                "official_source_url": url,
                "fetch_status": "SKIPPED_EXISTING",
                "bytes_written": "0",
                "failure_reason": "",
            })
            skipped_count += 1
            continue

        # Create parent folder if needed
        parent = local_path.parent
        if not parent.is_dir():
            parent.mkdir(parents=True, exist_ok=True)
            folders_created.add(str(parent))
            print(f"  Created folder: {parent}")

        # Fetch
        data, error = fetch_binary(url)

        if data is None or len(data) == 0:
            reason = error if error else "Empty response (0 bytes)"
            print(f"  [{idx:>2}/21] FAILED            {local_path.name}  reason={reason}")
            results.append({
                "card_code": card_code,
                "provenance_folder": provenance,
                "derived_full_local_path": str(local_path),
                "official_source_url": url,
                "fetch_status": "FAILED",
                "bytes_written": "0",
                "failure_reason": reason,
            })
            failed_count += 1
            continue

        # Write to disk
        local_path.write_bytes(data)
        written = len(data)
        total_bytes += written

        # Verify
        if local_path.is_file() and local_path.stat().st_size > 0:
            print(f"  [{idx:>2}/21] FETCHED           {local_path.name}  ({written} bytes)")
            results.append({
                "card_code": card_code,
                "provenance_folder": provenance,
                "derived_full_local_path": str(local_path),
                "official_source_url": url,
                "fetch_status": "FETCHED",
                "bytes_written": str(written),
                "failure_reason": "",
            })
            fetched_count += 1
        else:
            reason = "Post-write verification failed: file missing or 0 bytes"
            print(f"  [{idx:>2}/21] FAILED            {local_path.name}  reason={reason}")
            results.append({
                "card_code": card_code,
                "provenance_folder": provenance,
                "derived_full_local_path": str(local_path),
                "official_source_url": url,
                "fetch_status": "FAILED",
                "bytes_written": "0",
                "failure_reason": reason,
            })
            failed_count += 1

    # ── Write results CSV ────────────────────────────────────────────────
    print()
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        w.writerows(results)

    # ── Verification ─────────────────────────────────────────────────────
    print("=" * 70)
    print("VERIFICATION")
    print("=" * 70)
    print(f"  Selected row count:    {len(wave1)}")
    print(f"  Fetched success count: {fetched_count}")
    print(f"  Failed count:          {failed_count}")
    print(f"  Skipped existing:      {skipped_count}")
    print(f"  Total bytes written:   {total_bytes}")
    print(f"  Folders created:       {sorted(folders_created) if folders_created else '(none)'}")
    print(f"  Output CSV:            {OUTPUT_CSV}")
    print(f"  DB writes:             no")
    print(f"  Wave 2 rows touched:   no")
    print()

    if failed_count == 0:
        print("STATUS: CONFIRMED WORKING")
    elif fetched_count > 0:
        print("STATUS: CONFIRMED WORKING (with partial failures)")
    else:
        print("STATUS: FAILED")

    return 0 if fetched_count > 0 or skipped_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
