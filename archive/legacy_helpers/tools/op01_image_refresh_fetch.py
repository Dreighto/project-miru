"""Fetch OP01 non-base images from OPTCG API source_urls.

Respects source registry rate limit (4s spacing).
Saves to canonical paths under D:/Miru_Assets (canonical card image root).
"""
import hashlib
import os
import sqlite3
import time
import json
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

IMAGE_ROOT = Path(r"D:\Miru_Assets")
DB_PATH = Path("data/card_catalog.db")
RATE_LIMIT_SECONDS = 4.0
USER_AGENT = "MiruImageRefresh/1.0 (bounded operator fetch)"


def fetch_image(url, dest_path):
    """Download image from URL to dest_path. Returns (success, bytes, error)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with urlopen(req, timeout=30) as resp:
            data = resp.read()
            if len(data) < 1000:
                return False, 0, "response_too_small"
            dest_path.write_bytes(data)
            return True, len(data), None
    except HTTPError as e:
        return False, 0, f"HTTP_{e.code}"
    except URLError as e:
        return False, 0, f"URL_ERROR: {e.reason}"
    except Exception as e:
        return False, 0, str(e)


def compute_checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def get_dims(path):
    try:
        from PIL import Image
        with Image.open(str(path)) as img:
            return img.width, img.height
    except Exception:
        return None, None


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all fetchable OP01 non-base image_assets
    cur.execute("""
    SELECT ia.id as ia_id, ia.printing_id, ia.local_path, ia.source_url, ia.checksum,
           cv.print_id, cv.variant_key, cv.variant_label
    FROM image_assets ia
    JOIN card_variants cv ON cv.id = ia.printing_id
    WHERE cv.print_id LIKE 'OP01-%'
      AND cv.variant_key != 'base'
      AND ia.source_url IS NOT NULL
      AND ia.is_primary = 1
    ORDER BY ia.printing_id
    """)
    rows = [dict(r) for r in cur.fetchall()]

    # Filter to only those whose file doesn't exist on disk
    fetchable = []
    for r in rows:
        dest = IMAGE_ROOT / r["local_path"]
        if not dest.is_file():
            fetchable.append(r)

    print(f"Total image_assets with source_url: {len(rows)}")
    print(f"Already on disk (skipping): {len(rows) - len(fetchable)}")
    print(f"To fetch: {len(fetchable)}")
    print()

    results = []
    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, r in enumerate(fetchable):
        pid = r["printing_id"]
        url = r["source_url"]
        local_path = r["local_path"]
        dest = IMAGE_ROOT / local_path

        print(f"[{i+1}/{len(fetchable)}] pid={pid} {r['print_id']} -> {local_path}")

        # Rate limit
        if i > 0:
            time.sleep(RATE_LIMIT_SECONDS)

        ok, nbytes, err = fetch_image(url, dest)

        if ok:
            checksum = compute_checksum(dest)
            w, h = get_dims(dest)
            dims = f"{w}x{h}" if w else ""
            print(f"  OK: {nbytes} bytes, {dims}, sha256={checksum[:16]}...")
            success_count += 1

            # Update image_assets checksum in DB
            cur.execute(
                "UPDATE image_assets SET checksum = ?, image_confidence = 'OPTCG_API_FETCHED', updated_at = datetime('now') WHERE id = ?",
                (checksum, r["ia_id"]),
            )

            results.append({
                "printing_id": pid,
                "card_code": r["print_id"],
                "variant_key": r["variant_key"],
                "current_path": "",
                "refreshed_path": local_path,
                "old_byte_size": 0,
                "new_byte_size": nbytes,
                "old_dimensions": "",
                "new_dimensions": dims,
                "refresh_status": "FETCHED",
                "source_used": "optcg-api",
                "notes": f"sha256={checksum[:32]}",
            })
        else:
            print(f"  FAIL: {err}")
            fail_count += 1
            results.append({
                "printing_id": pid,
                "card_code": r["print_id"],
                "variant_key": r["variant_key"],
                "current_path": "",
                "refreshed_path": local_path,
                "old_byte_size": 0,
                "new_byte_size": 0,
                "old_dimensions": "",
                "new_dimensions": "",
                "refresh_status": "FAILED",
                "source_used": "optcg-api",
                "notes": err,
            })

    conn.commit()
    conn.close()

    print(f"\n=== FETCH SUMMARY ===")
    print(f"Success: {success_count}")
    print(f"Failed: {fail_count}")
    print(f"Skipped (already on disk): {len(rows) - len(fetchable)}")

    # Save results
    return results


if __name__ == "__main__":
    results = main()
    # Save as JSON for the final artifact builder
    with open("data/overlays/_op01_fetch_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nFetch results saved to data/overlays/_op01_fetch_results.json")
