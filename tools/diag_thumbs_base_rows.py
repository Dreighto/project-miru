"""Read-only: base rows with thumbs/ webp paths — disk + optional CDN mirror paths."""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MIRU = Path(r"D:\Miru_Assets")

# URL https://en.onepiece-cardgame.com/images/cardlist/card/{code}.png → possible local mirror
CDN_REL = Path("images") / "cardlist" / "card"


def canon_print_id(print_id: str) -> str:
    return (print_id or "").split("::", 1)[0].strip()


def disk_under_miru(rel: str) -> Path:
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p]
    return MIRU.joinpath(*parts) if parts else MIRU


def main() -> int:
    if not DB_PATH.is_file():
        print("FAILED: db missing", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    sql = """
    SELECT id, card_id, print_id, release_set_code, image_path, is_base, is_sp, is_alt
    FROM card_variants
    WHERE is_base = 1
    AND (image_path LIKE '%thumbs/%' OR image_path LIKE '%thumbs\\%')
    ORDER BY release_set_code, print_id
    """
    rows = cur.execute(sql).fetchall()
    conn.close()

    print("=" * 72)
    print(f"STEP 1 — thumbs/ base rows (count={len(rows)})")
    print("=" * 72)
    for r in rows:
        print(dict(r))

    per_row: list[dict] = []
    for r in rows:
        ip = str(r["image_path"] or "")
        rsc = str(r["release_set_code"] or "").strip()
        pid = canon_print_id(str(r["print_id"] or ""))
        thumbs_full = disk_under_miru(ip)
        base_full = MIRU / rsc / "base" / f"{pid}.png"
        t_ok = thumbs_full.is_file()
        b_ok = base_full.is_file()
        if t_ok and b_ok:
            cat = "BOTH_EXIST"
        elif b_ok:
            cat = "BASE_ONLY"
        elif t_ok:
            cat = "THUMBS_ONLY"
        else:
            cat = "NEITHER"
        per_row.append(
            {
                "id": r["id"],
                "card_id": r["card_id"],
                "print_id": r["print_id"],
                "release_set_code": rsc,
                "image_path": ip,
                "category": cat,
                "thumbs_disk": str(thumbs_full),
                "thumbs_exists": t_ok,
                "base_disk": str(base_full),
                "base_exists": b_ok,
            }
        )

    print()
    print("=" * 72)
    print("STEP 2 — per-row disk classification")
    print("=" * 72)
    for p in per_row:
        print(
            f"{p['category']:12} id={p['id']} print_id={p['print_id']!r} "
            f"rsc={p['release_set_code']!r}"
        )
        print(f"             thumbs: {p['thumbs_disk']} exists={p['thumbs_exists']}")
        print(f"             base:   {p['base_disk']} exists={p['base_exists']}")

    ctr = Counter(p["category"] for p in per_row)
    print()
    print("=" * 72)
    print("STEP 3 — summary counts")
    print("=" * 72)
    for k in ("BOTH_EXIST", "BASE_ONLY", "THUMBS_ONLY", "NEITHER"):
        print(f"  {k}: {ctr.get(k, 0)}")
    print(f"  TOTAL: {len(per_row)}")

    print()
    print("STEP 3b — THUMBS_ONLY and NEITHER (full paths)")
    for p in per_row:
        if p["category"] in ("THUMBS_ONLY", "NEITHER"):
            print(p)

    rsc_ctr = Counter(str(r["release_set_code"] or "").strip() for r in rows)
    print()
    print("=" * 72)
    print("STEP 4 — distinct release_set_code (count per set)")
    print("=" * 72)
    for code, n in sorted(rsc_ctr.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {code}: {n}")

    print()
    print("=" * 72)
    print(
        "STEP 5 — Bandai CDN-style on-disk checks (no network) for THUMBS_ONLY + NEITHER"
    )
    print(
        "  A) Standard ingest path (same as base check): "
        "<SET>/base/<canonical>.png under Miru_Assets"
    )
    print(
        f"  B) URL-path mirror if present: {MIRU / CDN_REL}/<canonical>.png"
    )
    problem = [p for p in per_row if p["category"] in ("THUMBS_ONLY", "NEITHER")]
    for p in problem:
        code = canon_print_id(p["print_id"]).upper()
        rsc = p["release_set_code"]
        ingest = MIRU / rsc / "base" / f"{code}.png"
        cdn_mirror = MIRU / CDN_REL / f"{code}.png"
        print(
            f"  id={p['id']} {code} [{p['category']}] "
            f"ingest_exists={ingest.is_file()} ingest={ingest} | "
            f"cdn_mirror_exists={cdn_mirror.is_file()} cdn_mirror={cdn_mirror}"
        )

    print()
    print("Status: CONFIRMED (read-only diagnostic complete)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
