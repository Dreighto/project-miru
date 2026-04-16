#!/usr/bin/env python3
"""Chapter 19.11C — DOM-driven official Bandai cardlist image resolution + surgical fetch.

Reads only CLEAR_TO_INSTALL rows from chapter19_11_install_plan.csv. Resolves the exact
official ``<img src>`` for each row from the live EN cardlist HTML (no URL suffix guessing).

For OP15 ``variant_family=alt`` rows, the official frontend uses modal id ``{CODE}_p1`` with
CDN filename ``{CODE}_p1.png`` (verified from en.onepiece-cardgame.com/cardlist/ DOM).

Writes PNG + same-stem WebP under D:\\Miru_Assets, emits manifest for the 18765 Dev panel,
and a CSV report. Does not touch card_catalog.db or any other database.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from miru_ai.workers.image_fetcher import _fetch_one_image  # noqa: E402

INPUT_CSV = ROOT / "data" / "overlays" / "chapter19_11_install_plan.csv"
REPORT_CSV = ROOT / "data" / "overlays" / "chapter19_11c_image_resolution_and_fetch_report.csv"
SUMMARY_TXT = ROOT / "data" / "overlays" / "chapter19_11c_image_resolution_and_fetch_summary.txt"
MANIFEST_JSON = ROOT / "data" / "overlays" / "chapter19_11c_install_panel_image_manifest.json"
ASSETS_ROOT = Path(r"D:\Miru_Assets")

CARDLIST_PAGE_URL = "https://en.onepiece-cardgame.com/cardlist/"
CARDLIST_ORIGIN = "https://en.onepiece-cardgame.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_cardlist_html() -> str:
    import urllib.request

    req = urllib.request.Request(CARDLIST_PAGE_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", "replace")


def _find_modal_block(html: str, modal_id: str) -> str | None:
    m = re.search(
        rf'<dl class="modalCol" id="{re.escape(modal_id)}"[^>]*>(.*?)</dl>\s*',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    return m.group(1) if m else None


def _first_cardlist_card_png_src(block: str) -> str | None:
    for m in re.finditer(r'<img[^>]+src="([^"]+)"', block):
        src = m.group(1).strip()
        if "images/cardlist/card/" in src and ".png" in src.lower():
            return src
    return None


def _absolute_official_img_url(relative_src: str) -> str:
    return urljoin(CARDLIST_PAGE_URL, relative_src).split("?")[0]


def _stem_and_suffix_from_modal(canonical_code: str, modal_id: str, filename: str) -> tuple[str, str]:
    """Return (stem, resolved_suffix_type e.g. _p1 / base)."""
    stem = Path(filename).stem
    code = canonical_code.strip().upper()
    if modal_id.upper().startswith(code) and len(modal_id) > len(code):
        suffix = modal_id[len(code) :]
        return stem, suffix
    return stem, "base"


def _miru_assets_relpath_for_op15_alt(canonical_code: str, filename: str) -> str:
    """Local layout: OP15/alt_art/<official_filename>."""
    return f"OP15/alt_art/{filename}"


def resolve_official_image_from_dom(
    html: str,
    canonical_code: str,
    variant_family: str,
) -> dict[str, Any]:
    """Return resolution fields or error."""
    code = str(canonical_code or "").strip().upper()
    vf = str(variant_family or "").strip().lower().replace("-", "_")

    if not code.startswith("OP15-"):
        return {"ok": False, "error": "unsupported_set_for_11c_dom_resolver"}

    if vf in ("alt", "alt_art", "alternate_art"):
        modal_id = f"{code}_p1"
        block = _find_modal_block(html, modal_id)
        if not block:
            return {"ok": False, "error": f"modal_not_found:{modal_id}"}
        rel_src = _first_cardlist_card_png_src(block)
        if not rel_src:
            return {"ok": False, "error": f"no_card_png_in_modal:{modal_id}"}
        abs_url = _absolute_official_img_url(rel_src)
        fname = Path(urlparse(abs_url).path).name
        stem, suffix_type = _stem_and_suffix_from_modal(code, modal_id, fname)
        return {
            "ok": True,
            "modal_id": modal_id,
            "official_img_src": rel_src,
            "official_image_absolute_url": abs_url,
            "resolved_official_stem": stem,
            "resolved_suffix_type": suffix_type,
            "cdn_filename": fname,
        }

    return {"ok": False, "error": f"unsupported_variant_family:{vf}"}


def _png_to_webp(png_path: Path, webp_path: Path) -> tuple[bool, str]:
    try:
        webp_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(png_path) as im:
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.save(webp_path, format="WEBP", quality=90, method=6)
        return True, "ok"
    except Exception as exc:  # pragma: no cover
        return False, str(exc)


def _load_clear_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        out: list[dict[str, str]] = []
        for raw in reader:
            if str(raw.get("pre_install_status") or "").strip() != "CLEAR_TO_INSTALL":
                continue
            out.append(
                {
                    "printing_id": str(raw.get("printing_id") or "").strip(),
                    "canonical_code": str(raw.get("canonical_code") or "").strip().upper(),
                    "variant_family": str(raw.get("variant_family") or "").strip(),
                    "candidate_mp_name": str(raw.get("candidate_mp_name") or "").strip(),
                }
            )
        return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Chapter 19.11C DOM-driven OP15 alt image fetch.")
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch and overwrite PNG/WebP even if PNG already exists.",
    )
    args = ap.parse_args()

    rows = _load_clear_rows(INPUT_CSV)
    if len(rows) != 16:
        print(f"WARNING: expected 16 CLEAR_TO_INSTALL rows, got {len(rows)}", file=sys.stderr)

    print("Fetching official cardlist HTML...")
    html = _fetch_cardlist_html()

    report_rows: list[dict[str, str]] = []
    manifest: dict[str, str] = {}
    png_fetched = 0
    webp_generated = 0
    failures = 0

    for row in rows:
        code = row["canonical_code"]
        res = resolve_official_image_from_dom(html, code, row["variant_family"])
        base_report: dict[str, str] = {
            "printing_id": row["printing_id"],
            "canonical_code": code,
            "variant_family": row["variant_family"],
            "official_cardlist_page_used": CARDLIST_PAGE_URL,
            "official_img_src": "",
            "resolved_official_stem": "",
            "resolved_suffix_type": "",
            "png_saved_path": "",
            "webp_saved_path": "",
            "png_exists_after": "0",
            "webp_exists_after": "0",
            "fetch_status": "",
            "dev_thumb_visible": "pending_operator",
            "full_png_modal_verified": "pending_operator",
            "notes": "",
        }

        if not res.get("ok"):
            failures += 1
            base_report["fetch_status"] = "RESOLUTION_FAILED"
            base_report["notes"] = str(res.get("error") or "unknown")
            report_rows.append(base_report)
            continue

        base_report["official_img_src"] = str(res.get("official_img_src") or "")
        base_report["resolved_official_stem"] = str(res.get("resolved_official_stem") or "")
        base_report["resolved_suffix_type"] = str(res.get("resolved_suffix_type") or "")

        fname = str(res.get("cdn_filename") or "")
        rel_png = _miru_assets_relpath_for_op15_alt(code, fname)
        png_path = ASSETS_ROOT / rel_png.replace("/", "\\")
        webp_path = png_path.with_suffix(".webp")
        official_url = str(res.get("official_image_absolute_url") or "")

        notes: list[str] = []

        if png_path.is_file() and not args.force:
            notes.append("PNG_ALREADY_PRESENT_SKIP")
        else:
            try:
                ok, reason, _sz = _fetch_one_image(official_url, png_path, None)
            except Exception as exc:  # pragma: no cover
                ok, reason, _sz = False, f"exception:{exc}", 0
            if not ok:
                failures += 1
                base_report["png_saved_path"] = str(png_path)
                base_report["webp_saved_path"] = str(webp_path)
                base_report["fetch_status"] = "FETCH_FAILED"
                base_report["notes"] = reason
                report_rows.append(base_report)
                continue
            png_fetched += 1
            notes.append("PNG_FETCHED")

        if webp_path.is_file() and not args.force:
            notes.append("WEBP_ALREADY_PRESENT")
        else:
            okw, reason = _png_to_webp(png_path, webp_path)
            if okw:
                webp_generated += 1
                notes.append("WEBP_CREATED")
            else:
                failures += 1
                notes.append(f"WEBP_FAILED:{reason}")

        base_report["png_saved_path"] = str(png_path)
        base_report["webp_saved_path"] = str(webp_path)
        base_report["png_exists_after"] = "1" if png_path.is_file() else "0"
        base_report["webp_exists_after"] = "1" if webp_path.is_file() else "0"
        base_report["fetch_status"] = "OK" if png_path.is_file() and webp_path.is_file() else "PARTIAL"
        base_report["notes"] = "; ".join(notes)

        if png_path.is_file():
            manifest[code] = Path(rel_png).as_posix()
        report_rows.append(base_report)

    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "printing_id",
        "canonical_code",
        "variant_family",
        "official_cardlist_page_used",
        "official_img_src",
        "resolved_official_stem",
        "resolved_suffix_type",
        "png_saved_path",
        "webp_saved_path",
        "png_exists_after",
        "webp_exists_after",
        "fetch_status",
        "dev_thumb_visible",
        "full_png_modal_verified",
        "notes",
    ]
    with REPORT_CSV.open("w", encoding="utf-8", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(report_rows)

    meta = {
        "chapter": "19.11C",
        "miru_assets_root": str(ASSETS_ROOT),
        "cardlist_page": CARDLIST_PAGE_URL,
        "resolution": "official_cardlist_modal_img_src",
    }
    payload = {"meta": meta, "png_relpath_by_canonical_code": manifest}
    MANIFEST_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    png_after = sum(1 for r in report_rows if r.get("png_exists_after") == "1")
    webp_after = sum(1 for r in report_rows if r.get("webp_exists_after") == "1")

    lines = [
        "Chapter 19.11C — DOM-driven official image resolution + surgical fetch",
        f"Input: {INPUT_CSV}",
        f"Official cardlist: {CARDLIST_PAGE_URL}",
        f"Rows processed: {len(rows)}",
        f"PNG files present after (report): {png_after}",
        f"WebP files present after (report): {webp_after}",
        f"PNG newly fetched (this run): {png_fetched}",
        f"WebP newly generated (this run): {webp_generated}",
        f"Failures (resolution or fetch): {failures}",
        f"Manifest: {MANIFEST_JSON}",
        f"Report: {REPORT_CSV}",
        "Database writes: none",
    ]
    SUMMARY_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
