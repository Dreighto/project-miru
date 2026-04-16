"""Read-only: verify base card_variants.image_path vs D:\\Miru_Assets on disk."""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MIRU = Path(r"D:\Miru_Assets")
MIN_OK_BYTES = 10 * 1024

# Normalized: SET/base/CODE.png
PATH_RE = re.compile(r"^([^/]+)/base/([^/]+\.png)$", re.I)


def canonical_print_id(print_id: str) -> str:
    return (print_id or "").split("::", 1)[0].strip().upper()


def norm_path(p: str) -> str:
    return (p or "").strip().replace("\\", "/")


def main() -> int:
    if not DB_PATH.is_file():
        print("FAILED: db missing", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    print("=" * 72)
    print("1. PRAGMA table_info(card_variants) — ALL columns")
    print("=" * 72)
    info = cur.execute("PRAGMA table_info(card_variants)").fetchall()
    col_names = [row[1] for row in info]
    for row in info:
        print(dict(zip(["cid", "name", "type", "notnull", "dflt_value", "pk"], row)))
    print()

    # Use confirmed names
    cur.execute(
        """
        SELECT id, print_id, release_set_code, image_path, is_base
        FROM card_variants
        WHERE is_base = 1
        ORDER BY id
        """
    )
    rows = cur.fetchall()
    conn.close()

    total = len(rows)
    ok_n = miss_path = wrong_fmt = file_miss = suspect = 0
    ex_wrong: list[dict] = []
    ex_miss: list[dict] = []
    ex_suspect: list[dict] = []
    set_codes_from_paths: set[str] = set()

    for r in rows:
        rid = r["id"]
        print_id = str(r["print_id"] or "")
        rsc = str(r["release_set_code"] or "").strip()
        ip_raw = r["image_path"]
        ip = ip_raw if isinstance(ip_raw, str) else (str(ip_raw) if ip_raw is not None else "")

        if ip is None or str(ip).strip() == "":
            miss_path += 1
            continue

        n = norm_path(ip)
        parts = n.split("/")
        if len(parts) >= 3 and parts[1].lower() == "base":
            set_codes_from_paths.add(parts[0])

        m = PATH_RE.match(n)
        if not m:
            wrong_fmt += 1
            if len(ex_wrong) < 10:
                ex_wrong.append(
                    {
                        "id": rid,
                        "print_id": print_id,
                        "release_set_code": rsc,
                        "image_path": ip,
                    }
                )
            continue

        set_from_path = m.group(1)
        file_name = m.group(2)
        stem = Path(file_name).stem.upper()
        canon = canonical_print_id(print_id)

        set_codes_from_paths.add(set_from_path)

        # Expected: set matches DB release_set_code when present; stem matches canonical code
        fmt_bad = False
        if rsc and set_from_path.upper() != rsc.upper():
            fmt_bad = True
        if stem != canon:
            fmt_bad = True

        if fmt_bad:
            wrong_fmt += 1
            if len(ex_wrong) < 10:
                ex_wrong.append(
                    {
                        "id": rid,
                        "print_id": print_id,
                        "release_set_code": rsc,
                        "image_path": ip,
                        "parsed_set": set_from_path,
                        "parsed_stem": stem,
                        "expected_stem": canon,
                    }
                )
            continue

        full = MIRU / n.replace("/", "\\")
        if not full.is_file():
            file_miss += 1
            if len(ex_miss) < 10:
                ex_miss.append(
                    {
                        "id": rid,
                        "print_id": print_id,
                        "release_set_code": rsc,
                        "image_path": ip,
                        "expected_disk": str(full),
                    }
                )
            continue

        sz = full.stat().st_size
        if sz <= MIN_OK_BYTES:
            suspect += 1
            if len(ex_suspect) < 10:
                ex_suspect.append(
                    {
                        "id": rid,
                        "print_id": print_id,
                        "image_path": ip,
                        "disk": str(full),
                        "size_bytes": sz,
                    }
                )
            continue

        ok_n += 1

    # SET_CODEs in paths with empty base folder
    base_gaps: list[str] = []
    for sc in sorted(set_codes_from_paths, key=str.upper):
        bdir = MIRU / sc / "base"
        if not bdir.is_dir():
            base_gaps.append(f"{sc} (no base dir)")
            continue
        pngs = list(bdir.glob("*.png"))
        if len(pngs) == 0:
            base_gaps.append(f"{sc} (base dir exists, 0 png files)")

    print("=" * 72)
    print("4. SUMMARY (base rows only, is_base=1)")
    print("=" * 72)
    print(f"Total base card_variants rows:     {total}")
    print(f"OK:                                {ok_n}")
    print(f"MISSING_PATH:                      {miss_path}")
    print(f"WRONG_FORMAT:                      {wrong_fmt}")
    print(f"FILE_MISSING:                      {file_miss}")
    print(f"SUSPECT_SIZE (<= {MIN_OK_BYTES} bytes): {suspect}")
    print()

    def show(title: str, examples: list):
        if not examples:
            return
        print(f"--- Examples: {title} (up to {len(examples)}) ---")
        for e in examples:
            print(e)
        print()

    show("WRONG_FORMAT", ex_wrong)
    show("FILE_MISSING", ex_miss)
    show("SUSPECT_SIZE", ex_suspect)

    print("=" * 72)
    print("5. SET_CODE coverage")
    print("=" * 72)
    print(f"Distinct SET_CODE in base image_path (parsed): {len(set_codes_from_paths)}")
    print(f"SET_CODEs with no PNGs under D:\\Miru_Assets\\<SET>\\base\\: {len(base_gaps)}")
    for g in base_gaps:
        print(f"  - {g}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
