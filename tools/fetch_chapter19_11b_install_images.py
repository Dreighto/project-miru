#!/usr/bin/env python3
"""Surgical official image fetch for Chapter 19.11 CLEAR_TO_INSTALL rows.

Reads only the chapter19_11_install_plan.csv artifact, fetches only those official
Bandai images, writes PNG masters plus same-stem WebP siblings under D:\\Miru_Assets,
and emits a read-only fetch report. This script does not touch the database.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from miru_ai.workers.image_fetcher import _build_base_relative_path, _fetch_one_image  # noqa: E402
from miru_ai.workers.image_fetcher import _build_variant_relative_path  # noqa: E402

INPUT_CSV = ROOT / "data" / "overlays" / "chapter19_11_install_plan.csv"
REPORT_CSV = ROOT / "data" / "overlays" / "chapter19_11b_image_fetch_report.csv"
SUMMARY_TXT = ROOT / "data" / "overlays" / "chapter19_11b_image_fetch_summary.txt"
ASSETS_ROOT = Path(r"D:\Miru_Assets")
BANDAI_IMAGE_PREFIX = "https://en.onepiece-cardgame.com/images/cardlist/card/"

def _normalize_variant_family(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if value in {"alt", "alt_art", "alternate art", "alternate_art"}:
        return "alt"
    if value in {"parallel", "p1", "p2", "p3"}:
        return "parallel"
    if value in {"sp", "special"}:
        return "sp"
    if value in {"tr", "treasure rare", "treasure_rare"}:
        return "tr"
    if value in {"mr", "manga", "manga_rare"}:
        return "mr"
    if value in {"gmr", "golden_manga_rare"}:
        return "gmr"
    if value in {"ir", "illustration_rare"}:
        return "ir"
    if value in {"promo", "p"}:
        return "promo"
    return "base"


def _build_relative_png_path(canonical_code: str, variant_family: str) -> str:
    family = _normalize_variant_family(variant_family)
    code = str(canonical_code or "").strip().upper()
    if family == "base":
        return _build_base_relative_path(code)

    row: dict[str, Any] = {
        "canonical_code": code,
        "print_id": code,
        "variant_key": family,
        "variant_label": family,
        "is_base": 0,
        "is_alt": 1 if family == "alt" else 0,
        "is_sp": 1 if family == "sp" else 0,
        "is_tr": 1 if family == "tr" else 0,
        "is_manga_rare": 1 if family == "mr" else 0,
        "is_golden_manga_rare": 1 if family == "gmr" else 0,
        "is_promo": 1 if family == "promo" else 0,
        "is_illustration_rare": 1 if family == "ir" else 0,
    }
    if family == "parallel":
        row["variant_key"] = "parallel"
        row["variant_label"] = "parallel"
        row["print_id"] = f"{code}::parallel"

    return _build_variant_relative_path(row)  # type: ignore[arg-type]


def _load_rows(path: Path) -> list[dict[str, str]]:
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
                    "pre_install_status": str(raw.get("pre_install_status") or "").strip(),
                    "candidate_mp_id": str(raw.get("candidate_mp_id") or "").strip(),
                    "candidate_mp_name": str(raw.get("candidate_mp_name") or "").strip(),
                }
            )
    return out


def _png_to_webp(png_path: Path, webp_path: Path) -> tuple[bool, str]:
    try:
        webp_path.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(png_path) as im:
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.save(webp_path, format="WEBP", quality=90, method=6)
        return True, "ok"
    except Exception as exc:  # pragma: no cover - surfaced in report
        return False, str(exc)


def main() -> int:
    rows = _load_rows(INPUT_CSV)
    report_rows: list[dict[str, str]] = []
    png_fetched = 0
    webp_generated = 0
    failures = 0

    for row in rows:
        rel_png = _build_relative_png_path(row["canonical_code"], row["variant_family"])
        png_path = ASSETS_ROOT / Path(rel_png)
        webp_path = png_path.with_suffix(".webp")
        official_url = f"{BANDAI_IMAGE_PREFIX}{png_path.name}"
        notes: list[str] = []
        png_downloaded = False
        webp_created = False

        if png_path.is_file():
            notes.append("PNG_ALREADY_PRESENT")
        else:
            try:
                ok, reason, _size = _fetch_one_image(official_url, png_path, None)
            except Exception as exc:  # pragma: no cover - surfaced in report
                ok, reason, _size = False, f"exception:{exc}", 0
            if ok:
                png_downloaded = True
                png_fetched += 1
                notes.append("PNG_FETCHED")
            else:
                failures += 1
                report_rows.append(
                    {
                        "printing_id": row["printing_id"],
                        "canonical_code": row["canonical_code"],
                        "variant_family": row["variant_family"],
                        "official_image_url": official_url,
                        "official_image_stem": png_path.stem,
                        "png_saved_path": str(png_path),
                        "webp_saved_path": str(webp_path),
                        "png_exists_after": "1" if png_path.is_file() else "0",
                        "webp_exists_after": "1" if webp_path.is_file() else "0",
                        "fetch_status": "FETCH_FAILED",
                        "notes": reason,
                    }
                )
                continue

        if webp_path.is_file():
            notes.append("WEBP_ALREADY_PRESENT")
        else:
            ok, reason = _png_to_webp(png_path, webp_path)
            if ok:
                webp_created = True
                webp_generated += 1
                notes.append("WEBP_CREATED")
            else:
                failures += 1
                notes.append(f"WEBP_FAILED:{reason}")

        if png_downloaded and webp_created:
            status = "FETCHED_PNG_AND_CREATED_WEBP"
        elif png_downloaded:
            status = "FETCHED_PNG"
        elif webp_created:
            status = "PNG_PRESENT_CREATED_WEBP"
        else:
            status = "ALREADY_PRESENT"

        report_rows.append(
            {
                "printing_id": row["printing_id"],
                "canonical_code": row["canonical_code"],
                "variant_family": row["variant_family"],
                "official_image_url": official_url,
                "official_image_stem": png_path.stem,
                "png_saved_path": str(png_path),
                "webp_saved_path": str(webp_path),
                "png_exists_after": "1" if png_path.is_file() else "0",
                "webp_exists_after": "1" if webp_path.is_file() else "0",
                "fetch_status": status,
                "notes": "; ".join(notes),
            }
        )

    REPORT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "printing_id",
                "canonical_code",
                "variant_family",
                "official_image_url",
                "official_image_stem",
                "png_saved_path",
                "webp_saved_path",
                "png_exists_after",
                "webp_exists_after",
                "fetch_status",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerows(report_rows)

    lines = [
        "Chapter 19.11B surgical official image fetch summary",
        f"Input artifact: {INPUT_CSV}",
        f"Rows processed: {len(rows)}",
        f"PNG fetched: {png_fetched}",
        f"WebP generated: {webp_generated}",
        f"Failures observed: {failures}",
        f"Assets root: {ASSETS_ROOT}",
        "DB touched: no",
    ]
    SUMMARY_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
