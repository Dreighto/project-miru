"""OP01 Pass B: image-asset existence audit. READ-ONLY.

- Stat all 348 card_variants image_path values against the asset roots.
- HEAD-check the 17 _r1/_r2 Bandai CDN URLs.
- Produces JSONL log + summary report. No DB writes, no file downloads.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "card_catalog.db"
REPORT_PATH = ROOT / "data" / "op01_image_audit_report.md"
JSONL_PATH = ROOT / "data" / "op01_image_audit.jsonl"

# Asset roots from canonical .env
OPTCG_ROOT = Path(r"D:\OPTCG_Images")
MIRU_ROOT = Path(r"D:\Miru_Assets")

BANDAI_P = re.compile(r"^OP01-\d{3}(_p\d+)?$")
BANDAI_R = re.compile(r"^OP01-\d{3}_r\d+$")


def candidate_paths(image_path: str) -> list[Path]:
    """Generate candidate disk locations for a DB image_path string."""
    if not image_path:
        return []
    p = Path(image_path)
    # Variants on the path: as-is, with subdirs stripped, alt extensions
    raw_parts = p.parts
    # If path has form OP01/base/foo.png or OP01/parallel/foo_p1.png, try flattening
    # to OP01/foo.png (asset dir is flat per inspection).
    flat = list(raw_parts)
    if len(flat) >= 3 and flat[1] in {"base", "parallel"}:
        flat = [flat[0], *flat[2:]]
    flat_path = Path(*flat) if flat else p
    bases = [OPTCG_ROOT, MIRU_ROOT]
    paths: list[Path] = []
    for b in bases:
        paths.append(b / p)
        paths.append(b / flat_path)
    # Try .jpg / .webp extension swaps too
    for q in list(paths):
        stem = q.with_suffix("")
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            paths.append(stem.with_suffix(ext))
    # Dedupe preserving order
    seen: set[Path] = set()
    out: list[Path] = []
    for q in paths:
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def find_on_disk(image_path: str) -> Path | None:
    for q in candidate_paths(image_path):
        if q.is_file():
            return q
    return None


def head_url(url: str, session: requests.Session) -> tuple[int, int]:
    """HEAD a URL. Returns (status_code, content_length)."""
    try:
        r = session.head(url, allow_redirects=True, timeout=15)
    except Exception:
        return 0, 0
    cl = int(r.headers.get("Content-Length") or 0)
    return r.status_code, cl


def main() -> int:
    if not DB_PATH.exists():
        print(f"FAIL: DB missing at {DB_PATH}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT c.canonical_code, cv.print_id, cv.variant_label,
               cv.image_path, cv.image_url
        FROM card_variants cv JOIN cards c ON cv.card_id=c.id
        WHERE c.canonical_code LIKE 'OP01-%'
        ORDER BY c.canonical_code, cv.print_id
        """
    ).fetchall()
    conn.close()
    rows = [dict(r) for r in rows]
    print(f"loaded {len(rows)} variants for OP01", flush=True)

    JSONL_PATH.unlink(missing_ok=True)

    on_disk = 0
    missing_disk: list[dict] = []
    no_path = 0
    by_category = {"bandai_canonical": [0, 0], "r_variant": [0, 0], "synthetic": [0, 0]}
    # [present, total]

    with JSONL_PATH.open("a", encoding="utf-8") as logf:
        for r in rows:
            ip = r["image_path"] or ""
            pid = r["print_id"]
            if BANDAI_P.match(pid):
                cat = "bandai_canonical"
            elif BANDAI_R.match(pid):
                cat = "r_variant"
            else:
                cat = "synthetic"
            by_category[cat][1] += 1

            if not ip:
                no_path += 1
                logf.write(json.dumps({"pid": pid, "cat": cat, "result": "no_image_path"}) + "\n")
                continue

            found = find_on_disk(ip)
            if found:
                on_disk += 1
                by_category[cat][0] += 1
                logf.write(
                    json.dumps({"pid": pid, "cat": cat, "result": "found", "path": str(found)})
                    + "\n"
                )
            else:
                missing_disk.append({"pid": pid, "cat": cat, "image_path": ip})
                logf.write(
                    json.dumps({"pid": pid, "cat": cat, "result": "missing", "image_path": ip})
                    + "\n"
                )

    print(f"on_disk={on_disk}  missing={len(missing_disk)}  no_image_path={no_path}", flush=True)

    # HEAD-check the 17 _r URLs
    print("HEAD-checking _r URLs...", flush=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "miru-op01-audit/1.0"})
    r_results: list[dict] = []
    r_rows = [r for r in rows if BANDAI_R.match(r["print_id"])]
    for r in r_rows:
        url = r["image_url"]
        if not url:
            r_results.append({"pid": r["print_id"], "url": "", "status": "no_url"})
            continue
        sc, cl = head_url(url, session)
        r_results.append({"pid": r["print_id"], "url": url, "status": sc, "content_length": cl})
        with JSONL_PATH.open("a", encoding="utf-8") as logf:
            logf.write(json.dumps({"head_check": True, **r_results[-1]}) + "\n")
        time.sleep(0.4)

    # Render report
    lines: list[str] = []
    lines.append("# OP01 Pass B — image-asset verification report")
    lines.append("")
    lines.append(
        f"_Generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} — read-only audit_"
    )
    lines.append("")
    lines.append("## Disk existence — `card_variants.image_path` vs filesystem")
    lines.append("")
    lines.append("Asset roots checked: `D:\\OPTCG_Images` (canonical), `D:\\Miru_Assets` (thumbs).")
    lines.append(
        "Path fallback rules applied: flatten `OP01/base/` and `OP01/parallel/` to `OP01/`; try `.png/.jpg/.jpeg/.webp` extensions."
    )
    lines.append("")
    lines.append("| Category | On-disk | Total | Missing |")
    lines.append("| --- | ---: | ---: | ---: |")
    for cat, (pres, tot) in by_category.items():
        lines.append(f"| {cat} | {pres} | {tot} | {tot - pres} |")
    lines.append(f"| **TOTAL** | **{on_disk}** | **{len(rows)}** | **{len(missing_disk)}** |")
    lines.append("")
    if missing_disk:
        lines.append(f"### Missing on disk ({len(missing_disk)} rows)")
        lines.append("")
        lines.append("| print_id | category | image_path |")
        lines.append("| --- | --- | --- |")
        for m in missing_disk[:200]:
            lines.append(f"| {m['pid']} | {m['cat']} | {m['image_path']} |")
        if len(missing_disk) > 200:
            lines.append(f"| ... | ... | ... ({len(missing_disk) - 200} more) |")
        lines.append("")
    lines.append("## Bandai CDN HEAD checks — 17 `_r1`/`_r2` rare-art URLs")
    lines.append("")
    lines.append("| print_id | HTTP status | content-length |")
    lines.append("| --- | ---: | ---: |")
    for r in r_results:
        lines.append(f"| {r['pid']} | {r['status']} | {r.get('content_length', '')} |")
    lines.append("")
    r_ok = sum(1 for r in r_results if r["status"] == 200)
    lines.append(f"**Summary:** {r_ok}/{len(r_results)} `_r` URLs returned HTTP 200.")
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"DONE  on_disk={on_disk}  missing={len(missing_disk)}  r_urls_ok={r_ok}/{len(r_results)}  report={REPORT_PATH}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
