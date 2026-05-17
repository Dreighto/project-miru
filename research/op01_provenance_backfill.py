"""OP01 Pass A: backfill `official_provenance` from Bandai crawl JSON.

Uses crawl's `card_set` as the authoritative source. Performs UPDATE only
for the 218 (canonical_code, print_id) pairs that the crawl confirms.

Dry-run by default. Pass --apply to write.

Backup the DB before --apply (cp data/card_catalog.db .bak.YYYYMMDD).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "card_catalog.db"
CRAWL_PATH = ROOT / "data" / "bandai_op01_crawl.json"
LOG_PATH = ROOT / "data" / "op01_provenance_backfill.log"


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Actually write to the DB")
    args = parser.parse_args()

    LOG_PATH.unlink(missing_ok=True)
    log(f"mode={'APPLY' if args.apply else 'DRY-RUN'}")

    crawl = json.loads(CRAWL_PATH.read_text(encoding="utf-8"))["printings"]
    # Build (card_number, full_id) -> card_set lookup
    crawl_index: dict[tuple[str, str], str] = {}
    for p in crawl:
        if p.get("card_set"):
            crawl_index[(p["card_number"], p["full_id"])] = p["card_set"]
    log(f"crawl provides authority for {len(crawl_index)} (card_number, print_id) pairs")

    mode = "" if args.apply else "?mode=ro"
    conn = sqlite3.connect(f"file:{DB_PATH}{mode}", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT cv.id AS variant_id, c.canonical_code, cv.print_id, cv.official_provenance
        FROM card_variants cv JOIN cards c ON cv.card_id=c.id
        WHERE c.canonical_code LIKE 'OP01-%'
        """
    ).fetchall()

    set_null_to_value = 0
    correct_existing = 0
    already_correct = 0
    no_authority = 0
    updates: list[tuple[str, int]] = []  # (new_value, variant_id)

    for r in rows:
        key = (r["canonical_code"], r["print_id"])
        authority = crawl_index.get(key)
        if not authority:
            no_authority += 1
            continue
        existing = r["official_provenance"]
        if existing == authority:
            already_correct += 1
            continue
        updates.append((authority, r["variant_id"]))
        if existing is None or existing == "":
            set_null_to_value += 1
            log(f"  SET {key[0]} {key[1]}: NULL -> {authority!r}")
        else:
            correct_existing += 1
            log(f"  FIX {key[0]} {key[1]}: {existing!r} -> {authority!r}")

    log("")
    log(f"summary: total_op01_variants={len(rows)}")
    log(f"  pairs_with_crawl_authority={len(crawl_index)}")
    log(f"  already_correct={already_correct}")
    log(f"  set_null_to_value={set_null_to_value}")
    log(f"  correct_existing_wrong={correct_existing}")
    log(f"  no_crawl_authority_left_as_is={no_authority}")
    log(f"  total_updates_pending={len(updates)}")

    if args.apply and updates:
        cur = conn.cursor()
        cur.executemany(
            "UPDATE card_variants SET official_provenance = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = ?",
            updates,
        )
        conn.commit()
        log(f"APPLIED {cur.rowcount} updates")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
