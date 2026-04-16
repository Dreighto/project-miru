"""
OP01 Promo Image Fetch — Bandai CDN

Fetches promo card images from the Bandai CDN, validates each file,
writes to disk under D:\Miru_Assets\P\base\, and registers successful
fetches in image_assets.

Rate limited: 1.5s between requests.
"""

import sqlite3
import csv
import time
import urllib.request
import urllib.error
from pathlib import Path

DB_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\card_catalog.db")
DISCOVERY_CSV = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_missing_asset_discovery.csv")
FETCH_LOG_PATH = Path(r"D:\dev\tcg-watcher-worktree\data\overlays\op01_promo_fetch_log.csv")
ASSET_ROOT = Path(r"D:\Miru_Assets")
TARGET_DIR = ASSET_ROOT / "P" / "base"

CDN_TEMPLATE = "https://en.onepiece-cardgame.com/images/cardlist/card/{card_code}.png"
PNG_MAGIC = b"\x89PNG"
MIN_FILE_SIZE = 50_000  # 50 KB
REQUEST_DELAY = 1.5  # seconds between requests

FETCH_LOG_FIELDS = [
    "card_code", "printing_id", "fetch_url", "fetch_status",
    "file_size_bytes", "disk_path", "image_assets_inserted",
]


def fetch_one(card_code: str) -> tuple[str, bytes | None, str]:
    """Fetch a single card image. Returns (status, data, reason)."""
    url = CDN_TEMPLATE.format(card_code=card_code)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; MiruBot/1.0)",
        "Accept": "image/png,image/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status_code = resp.getcode()
            if status_code != 200:
                return "FETCH_SKIP", None, f"HTTP {status_code}"

            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                return "FETCH_SKIP", None, f"Content-Type: {content_type}"

            data = resp.read()

            if not data[:4] == PNG_MAGIC:
                return "FETCH_SKIP", None, f"Not PNG (magic: {data[:4]!r})"

            if len(data) < MIN_FILE_SIZE:
                return "FETCH_SKIP", None, f"Too small: {len(data)} bytes < {MIN_FILE_SIZE}"

            return "FETCH_SUCCESS", data, ""

    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "FETCH_404", None, "HTTP 404"
        return "FETCH_SKIP", None, f"HTTP {e.code}"
    except Exception as e:
        return "FETCH_ERROR", None, str(e)


def main():
    # Step 1 — Load target rows
    with open(DISCOVERY_CSV, encoding="utf-8") as f:
        disc_rows = [
            r for r in csv.DictReader(f)
            if r["discovery_status"] == "FETCH_NEEDED" and r["variant_key"] == "promo"
        ]

    targets = [(r["card_code"], int(r["printing_id"])) for r in disc_rows]
    print(f"Step 1: Promo FETCH_NEEDED rows: {len(targets)}")
    for card_code, pid in targets:
        print(f"  {card_code}  pid={pid}")

    # Step 2 — Pre-flight
    pids = [t[1] for t in targets]
    uri = f"file:{DB_PATH}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    ph = ",".join("?" * len(pids))
    (existing_count,) = con.execute(
        f"SELECT COUNT(*) FROM image_assets WHERE printing_id IN ({ph})", pids
    ).fetchone()
    con.close()

    print(f"\nStep 2: Pre-flight — existing image_assets rows: {existing_count}")
    if existing_count > 0:
        # Exclude already-registered pids
        con = sqlite3.connect(uri, uri=True)
        existing_pids = {
            r[0] for r in con.execute(
                f"SELECT printing_id FROM image_assets WHERE printing_id IN ({ph})", pids
            ).fetchall()
        }
        con.close()
        targets = [(cc, pid) for cc, pid in targets if pid not in existing_pids]
        print(f"  Excluded {existing_count} already-registered. Remaining: {len(targets)}")
    else:
        print("  Pre-flight PASS: 0 existing rows.")

    # Step 3 — Fetch, validate, write to disk
    TARGET_DIR.mkdir(parents=True, exist_ok=True)

    results = []  # (card_code, pid, url, status, file_size, disk_path)
    counts = {"FETCH_SUCCESS": 0, "FETCH_404": 0, "FETCH_SKIP": 0, "FETCH_ERROR": 0}

    print(f"\nStep 3: Fetching {len(targets)} images (1.5s rate limit)...")
    for i, (card_code, pid) in enumerate(targets):
        if i > 0:
            time.sleep(REQUEST_DELAY)

        url = CDN_TEMPLATE.format(card_code=card_code)
        status, data, reason = fetch_one(card_code)
        counts[status] += 1

        file_size = 0
        disk_path = ""

        if status == "FETCH_SUCCESS" and data:
            dest = TARGET_DIR / f"{card_code}.png"
            dest.write_bytes(data)
            file_size = len(data)
            disk_path = str(dest)
            print(f"  [{i+1:>2}/{len(targets)}] {card_code}  FETCH_SUCCESS  {file_size:,} bytes")
        else:
            reason_short = reason[:60] if reason else ""
            print(f"  [{i+1:>2}/{len(targets)}] {card_code}  {status}  {reason_short}")

        results.append((card_code, pid, url, status, file_size, disk_path))

    print(f"\nStep 3 summary:")
    for s in ("FETCH_SUCCESS", "FETCH_404", "FETCH_SKIP", "FETCH_ERROR"):
        print(f"  {s}: {counts[s]}")

    # Step 4 — Register image_assets
    success_rows = [(cc, pid, url) for cc, pid, url, st, _, _ in results if st == "FETCH_SUCCESS"]
    print(f"\nStep 4: Registering {len(success_rows)} image_assets rows...")

    if success_rows:
        con = sqlite3.connect(str(DB_PATH))
        cur = con.cursor()
        inserted = 0
        for card_code, pid, source_url in success_rows:
            local_path = f"P/base/{card_code}.png"
            try:
                cur.execute(
                    "INSERT INTO image_assets (printing_id, local_path, asset_type, source_url, is_primary) "
                    "VALUES (?, ?, 'card_image', ?, 1)",
                    (pid, local_path, source_url),
                )
                inserted += 1
                print(f"  INSERT pid={pid}  path={local_path}")
            except sqlite3.IntegrityError as e:
                print(f"  SKIP pid={pid}  UNIQUE violation: {e}")
        con.commit()
        con.close()
        print(f"  Committed {inserted} rows.")
    else:
        inserted = 0
        print("  No FETCH_SUCCESS rows to register.")

    # Build inserted set for log
    inserted_pids = {pid for _, pid, _ in success_rows}

    # Step 5 — Write fetch log
    FETCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FETCH_LOG_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FETCH_LOG_FIELDS)
        writer.writeheader()
        for card_code, pid, url, status, file_size, disk_path in results:
            writer.writerow({
                "card_code": card_code,
                "printing_id": pid,
                "fetch_url": url,
                "fetch_status": status,
                "file_size_bytes": file_size,
                "disk_path": disk_path,
                "image_assets_inserted": 1 if pid in inserted_pids else 0,
            })

    print(f"\nStep 5: Fetch log written to {FETCH_LOG_PATH}")
    print(f"\nDone. Run 'python tools/op01_certification_scan.py' for Step 6.")


if __name__ == "__main__":
    main()
