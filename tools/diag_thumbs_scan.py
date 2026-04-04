"""Read-only: scan thumbs/ image_path rows against Miru_Assets and OPTCG_Images."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MIRU = Path(r"D:\Miru_Assets")
OPTCG = Path(r"F:\OPTCG_Images")

SQL = """
SELECT id, variant_key, print_id, image_path, release_set_code
FROM card_variants
WHERE image_path LIKE 'thumbs/%'
ORDER BY id
"""


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(SQL).fetchall()
    conn.close()

    cols = ["id", "variant_key", "print_id", "image_path", "release_set_code"]
    records = [dict(zip(cols, r)) for r in rows]

    exists_d = 0
    exists_f = 0
    missing_both = 0

    # variant_key -> [total, d, f, neither]
    by_key: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])

    sample_d: list[dict] = []
    sample_miss: list[dict] = []

    for rec in records:
        ip = str(rec["image_path"] or "").strip().replace("/", "\\")
        path_d = MIRU / ip
        path_f = OPTCG / ip
        d_ok = path_d.is_file()
        f_ok = path_f.is_file()

        vk = str(rec["variant_key"] or "")
        by_key[vk][0] += 1
        if d_ok:
            exists_d += 1
            by_key[vk][1] += 1
            if len(sample_d) < 10:
                sample_d.append({**rec, "resolved_D": str(path_d)})
        if f_ok:
            exists_f += 1
            by_key[vk][2] += 1
        if not d_ok and not f_ok:
            missing_both += 1
            by_key[vk][3] += 1
            if len(sample_miss) < 10:
                sample_miss.append({**rec, "checked_D": str(path_d), "checked_F": str(path_f)})

    n = len(records)
    print("=" * 72)
    print("SUMMARY (thumbs/ rows)")
    print("=" * 72)
    print(f"Total thumbs/ rows in DB:     {n}")
    print(f"Exists at D:\\Miru_Assets:    {exists_d}")
    print(f"Exists at F:\\OPTCG_Images:   {exists_f}")
    print(f"Missing at both locations:   {missing_both}")
    print()
    print("(A row can count in both D and F if present in both.)")
    print()

    print("=" * 72)
    print("BREAKDOWN BY variant_key")
    print("=" * 72)
    print(f"{'variant_key':<20} | {'total':>6} | {'exists_D':>8} | {'exists_F':>8} | {'missing_both':>12}")
    print("-" * 72)
    for vk in sorted(by_key.keys(), key=lambda k: (-by_key[k][0], k)):
        t, d, f, m = by_key[vk]
        print(f"{vk:<20} | {t:6d} | {d:8d} | {f:8d} | {m:12d}")
    print()

    print("=" * 72)
    print("First 10 rows — file EXISTS at D:\\Miru_Assets")
    print("=" * 72)
    if not sample_d:
        print("  (none)")
    else:
        for r in sample_d:
            print(r)
    print()

    print("=" * 72)
    print("First 10 rows — MISSING at both D and F")
    print("=" * 72)
    for r in sample_miss:
        print(r)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
