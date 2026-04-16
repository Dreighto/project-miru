"""Read-only: analyze base rows with sp/ image_path vs expected base files on disk."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MIRU = Path(r"D:\Miru_Assets")


def canon_print_id(print_id: str) -> str:
    return (print_id or "").split("::", 1)[0].strip()


def norm_sp_condition() -> str:
    return """is_base = 1 AND (
      image_path LIKE '%/sp/%' OR image_path LIKE '%\\sp\\%'
      OR image_path LIKE '%/sp\\%' OR image_path LIKE '%\\sp/%'
    )"""


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    q1 = f"""
    SELECT id, card_id, print_id, release_set_code, image_path, is_base, is_sp
    FROM card_variants
    WHERE {norm_sp_condition()}
    ORDER BY release_set_code, print_id
    """
    wrong = cur.execute(q1).fetchall()

    print("=" * 72)
    print(f"STEP 1 — WRONG_FORMAT base rows (count={len(wrong)})")
    print("=" * 72)
    for r in wrong:
        print(dict(r))

    cids = sorted({int(r["card_id"]) for r in wrong})
    placeholders = ",".join("?" * len(cids))
    q2 = f"""
    SELECT id, card_id, print_id, release_set_code, image_path, is_base, is_sp
    FROM card_variants
    WHERE card_id IN ({placeholders})
    ORDER BY card_id, is_base DESC, id
    """
    siblings = cur.execute(q2, cids).fetchall()

    print()
    print("=" * 72)
    print(f"STEP 2 — All variants for affected card_ids (rows={len(siblings)})")
    print("=" * 72)
    for r in siblings:
        print(dict(r))

    base_exists = 0
    base_missing = 0
    missing_examples: list[dict] = []

    print()
    print("=" * 72)
    print("STEP 3 — Expected base file on disk")
    print("=" * 72)
    for r in wrong:
        rsc = str(r["release_set_code"] or "").strip()
        pid = canon_print_id(str(r["print_id"] or ""))
        rel = f"{rsc}/base/{pid}.png".replace("\\", "/")
        full = MIRU / rsc / "base" / f"{pid}.png"
        exists = full.is_file()
        sz = full.stat().st_size if exists else None
        status = "BASE_EXISTS" if exists else "BASE_MISSING"
        if exists:
            base_exists += 1
        else:
            base_missing += 1
            missing_examples.append(
                {
                    "id": r["id"],
                    "card_id": r["card_id"],
                    "print_id": r["print_id"],
                    "release_set_code": rsc,
                    "expected_path": str(full),
                    "current_image_path": r["image_path"],
                }
            )
        print(
            {
                "variant_id": r["id"],
                "card_id": r["card_id"],
                "print_id": r["print_id"],
                "release_set_code": rsc,
                "expected_disk": str(full),
                "status": status,
                "size_bytes": sz,
            }
        )

    print()
    print("=" * 72)
    print("STEP 4 — SUMMARY")
    print("=" * 72)
    print(f"Total WRONG_FORMAT base rows: {len(wrong)}")
    print(f"BASE_EXISTS on disk: {base_exists}")
    print(f"BASE_MISSING on disk: {base_missing}")
    print()
    print("ALL BASE_MISSING examples:")
    for m in missing_examples:
        print(m)

    # Step 5: base row shares card_id with is_sp=1 row that has valid sp path
    sp_rows = [
        dict(r)
        for r in siblings
        if int(r["is_sp"] or 0) == 1
        and str(r["image_path"] or "").strip()
        and ("/sp/" in str(r["image_path"]).replace("\\", "/") or "\\sp\\" in str(r["image_path"]))
    ]
    wrong_ids = {int(r["id"]) for r in wrong}
    wrong_cids = {int(r["card_id"]) for r in wrong}
    share_count = 0
    share_examples: list[dict] = []

    for r in wrong:
        cid = int(r["card_id"])
        for s in siblings:
            if int(s["card_id"]) != cid:
                continue
            if int(s["id"]) == int(r["id"]):
                continue
            if int(s["is_sp"] or 0) != 1:
                continue
            ip = str(s["image_path"] or "").strip()
            if not ip:
                continue
            n = ip.replace("\\", "/")
            if "/sp/" not in n:
                continue
            rel = n
            fpath = MIRU / rel.replace("/", "\\")
            valid = fpath.is_file()
            if valid:
                share_count += 1
                if len(share_examples) < 5:
                    share_examples.append(
                        {
                            "base_variant_id": r["id"],
                            "base_print_id": r["print_id"],
                            "base_image_path": r["image_path"],
                            "sp_variant_id": s["id"],
                            "sp_print_id": s["print_id"],
                            "sp_image_path": s["image_path"],
                            "sp_file_exists": True,
                            "sp_size": fpath.stat().st_size,
                        }
                    )
                break

    print()
    print("=" * 72)
    print("STEP 5 — Base row + sibling is_sp=1 with sp/ path and file on disk")
    print("=" * 72)
    print(
        "Count of WRONG_FORMAT base rows that have a sibling is_sp=1 row "
        "with non-empty sp/ image_path AND file exists under D:\\Miru_Assets:"
    )
    print(share_count)
    print("Examples (up to 5):")
    for e in share_examples:
        print(e)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
