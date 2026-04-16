#!/usr/bin/env python3
"""
op01_register_image_assets.py -- Controlled image_assets registration for OP01

Reads the registration readiness audit, resolves cv_ids, inserts rows into
image_assets for all READY rows. Handles duplicate-target groups by
registering the shared path once and marking twin rows as covered.

DB: card_catalog.db -- WRITES to image_assets table ONLY.
NO image fetches. NO Miru_Assets modifications. NO PM touch.
"""
from __future__ import annotations

import csv
import hashlib
import io
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT       = Path(__file__).resolve().parents[1]
ASSETS_ROOT = Path("D:/Miru_Assets")
DB_PATH    = ROOT / "data" / "card_catalog.db"
OUT_PATH   = ROOT / "data" / "overlays" / "op01_registration_results.csv"

ISO_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

OUTPUT_COLUMNS = [
    "card_code", "card_image_id", "derived_full_local_path",
    "registration_status", "duplicate_target_group_id",
    "db_table_touched", "failure_reason",
]


def abs_to_rel(abs_path: str) -> str:
    p = Path(abs_path)
    rel = p.relative_to(ASSETS_ROOT)
    return str(rel).replace("\\", "/")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    print("=" * 60)
    print("OP01 CONTROLLED IMAGE REGISTRATION PASS")
    print("=" * 60)

    # ── Load inputs ──────────────────────────────────────────────────────
    audit = list(csv.DictReader(open(
        ROOT / "data/overlays/op01_registration_readiness_audit.csv", encoding="utf-8"
    )))
    plan_csv = list(csv.DictReader(open(
        ROOT / "data/overlays/op01_fetch_mirror_planning.csv", encoding="utf-8"
    )))
    reg_plan = list(csv.DictReader(open(
        ROOT / "data/overlays/optcg_api_op01_image_asset_registration_plan.csv", encoding="utf-8"
    )))

    # cv_id lookup: card_image_id -> int(cv_id)
    cv_id_map: dict[str, int] = {}
    for r in reg_plan:
        if r["local_variant_match_id"]:
            cv_id_map[r["card_image_id"]] = int(r["local_variant_match_id"])

    # source_url lookup: prefer shorter (canonical) URL per card_image_id
    src_url_map: dict[str, str] = {}
    for r in plan_csv:
        if r["planning_classification"] == "FETCH_OR_MIRROR_NEEDED":
            cid = r["card_image_id"]
            url = r["official_source_url"]
            existing = src_url_map.get(cid, "")
            if not existing or len(url) < len(existing):
                src_url_map[cid] = url

    print(f"  Audit rows loaded:     {len(audit)}")
    print(f"  cv_id entries:         {len(cv_id_map)}")
    print()

    # ── De-duplicate by cv_id (handle groups where 2 rows share one cv_id) ──
    seen_cv_ids: set[int] = set()
    work_items = []
    for r in audit:
        cv_id = cv_id_map.get(r["card_image_id"])
        dedup_skip = (cv_id is not None) and (cv_id in seen_cv_ids)
        if cv_id is not None:
            seen_cv_ids.add(cv_id)
        work_items.append({"audit": r, "cv_id": cv_id, "dedup_skip": dedup_skip})

    print(f"  Unique cv_ids:         {len(seen_cv_ids)}")
    print(f"  Dedup-skip rows:       {sum(1 for w in work_items if w['dedup_skip'])}")
    print()

    # ── Open DB for writing ──────────────────────────────────────────────
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    results = []
    registered_count = 0
    shared_dup_count = 0
    skipped_count    = 0
    failed_count     = 0

    for w in work_items:
        r           = w["audit"]
        cv_id       = w["cv_id"]
        card_code   = r["card_code"]
        card_img_id = r["card_image_id"]
        abs_path    = r["derived_full_local_path"]
        readiness   = r["registration_readiness"]
        dup_group   = r["duplicate_target_group_id"]
        dedup_skip  = w["dedup_skip"]

        def make_fail(reason: str) -> dict:
            return {
                "card_code": card_code,
                "card_image_id": card_img_id,
                "derived_full_local_path": abs_path,
                "registration_status": "FAILED",
                "duplicate_target_group_id": dup_group,
                "db_table_touched": "",
                "failure_reason": reason,
            }

        # Missing cv_id
        if cv_id is None:
            results.append(make_fail("No cv_id resolved"))
            failed_count += 1
            continue

        # Dedup-skip: the cv_id was registered by the first (leader) row of this group
        if dedup_skip:
            results.append({
                "card_code": card_code,
                "card_image_id": card_img_id,
                "derived_full_local_path": abs_path,
                "registration_status": "REGISTERED_SHARED_DUPLICATE_GROUP",
                "duplicate_target_group_id": dup_group,
                "db_table_touched": "image_assets (covered by group leader row)",
                "failure_reason": "",
            })
            shared_dup_count += 1
            continue

        # Derive relative path
        try:
            rel_path = abs_to_rel(abs_path)
        except Exception as exc:
            results.append(make_fail(f"Cannot derive relative path: {exc}"))
            failed_count += 1
            continue

        # Check existing image_assets for this printing_id
        cur.execute(
            "SELECT id, local_path FROM image_assets WHERE printing_id = ?", (cv_id,)
        )
        existing_rows = cur.fetchall()
        if existing_rows:
            existing_paths = [e["local_path"] for e in existing_rows]
            if rel_path in existing_paths:
                results.append({
                    "card_code": card_code,
                    "card_image_id": card_img_id,
                    "derived_full_local_path": abs_path,
                    "registration_status": "SKIPPED_ALREADY_REGISTERED",
                    "duplicate_target_group_id": dup_group,
                    "db_table_touched": "image_assets (no change)",
                    "failure_reason": (
                        f"printing_id={cv_id} already registered to local_path={rel_path}"
                    ),
                })
                skipped_count += 1
                continue
            else:
                results.append(make_fail(
                    f"printing_id={cv_id} already has image_assets row(s) with "
                    f"different path(s): {existing_paths}. Fail-closed."
                ))
                failed_count += 1
                continue

        # Compute checksum
        try:
            checksum = sha256_file(Path(abs_path))
        except Exception as exc:
            results.append(make_fail(f"Checksum error: {exc}"))
            failed_count += 1
            continue

        source_url = src_url_map.get(card_img_id, "")

        # INSERT
        try:
            cur.execute(
                """
                INSERT INTO image_assets
                    (printing_id, asset_type, local_path, source_label, source_url,
                     checksum, has_sample_watermark, image_confidence, is_primary,
                     created_at, updated_at)
                VALUES
                    (?, 'card_scan', ?, 'optcg_api_official', ?,
                     ?, 0, 'UNVERIFIED', 1,
                     ?, ?)
                """,
                (cv_id, rel_path, source_url, checksum, ISO_NOW, ISO_NOW),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            results.append(make_fail(f"INSERT failed: {exc}"))
            failed_count += 1
            continue

        if readiness == "READY_FOR_REGISTRATION_DUPLICATE_TARGET_GROUP":
            reg_status = "REGISTERED_SHARED_DUPLICATE_GROUP"
            shared_dup_count += 1
        else:
            reg_status = "REGISTERED"
            registered_count += 1

        results.append({
            "card_code": card_code,
            "card_image_id": card_img_id,
            "derived_full_local_path": abs_path,
            "registration_status": reg_status,
            "duplicate_target_group_id": dup_group,
            "db_table_touched": "image_assets",
            "failure_reason": "",
        })

        print(f"  [{reg_status:42s}] {card_img_id}  printing_id={cv_id}  {rel_path}")

    conn.close()

    # ── Write results CSV ────────────────────────────────────────────────
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        w.writerows(results)

    # ── Summary ──────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("REGISTRATION SUMMARY")
    print("=" * 60)
    print(f"  Total audit rows processed:        {len(audit)}")
    print(f"  REGISTERED:                        {registered_count}")
    print(f"  REGISTERED_SHARED_DUPLICATE_GROUP: {shared_dup_count}")
    print(f"  SKIPPED_ALREADY_REGISTERED:        {skipped_count}")
    print(f"  FAILED:                            {failed_count}")
    print(f"  DB table touched:                  image_assets")
    print(f"  Output CSV:                        {OUT_PATH}")
    print(f"  Output rows:                       {len(results)}")

    if failed_count == 0:
        print("\n  VERDICT: CONFIRMED WORKING")
    else:
        print(f"\n  VERDICT: PARTIAL -- {failed_count} failures")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
