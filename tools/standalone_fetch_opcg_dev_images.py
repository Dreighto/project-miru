#!/usr/bin/env python3
"""
One-time card image fetcher: onepiece-cardgame.dev -> D:\\Miru_Assets\\

- Reads the catalog from data/snapshots/community_cardlist.json (card list only).
- Pulls image URLs from the site's cards.json (field `iu`), matching `cid` to `card_code`.
- Standalone: does not import Miru pipeline modules or touch databases.

Requires: pip install curl_cffi  (Cloudflare-friendly HTTP; stdlib urllib alone gets 403.)

Usage:
  python tools/standalone_fetch_opcg_dev_images.py
  python tools/standalone_fetch_opcg_dev_images.py --site-json D:\\cache\\cards.json
  python tools/standalone_fetch_opcg_dev_images.py --limit 50
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SITE_CARDS_URL = "https://onepiece-cardgame.dev/cards.json"
SITE_ORIGIN = "https://onepiece-cardgame.dev"
DEFAULT_CATALOG = (
    Path(__file__).resolve().parents[1] / "data" / "snapshots" / "community_cardlist.json"
)
DEFAULT_OUT_ROOT = Path(r"D:\Miru_Assets")
DEFAULT_LOG = Path(r"D:\Miru_Assets\fetch_log.txt")
REQUEST_DELAY_SEC = 0.3
DOWNLOAD_TIMEOUT_SEC = 60
IMPERSONATE = "chrome120"

IMAGE_EXT_RE = re.compile(r"\.(webp|jpg|jpeg|png)(\?|$)", re.I)


def _need_curl_cffi():
    try:
        from curl_cffi import requests as cf_requests  # noqa: F401

        return True
    except ImportError:
        return False


def load_catalog_codes(catalog_path: Path) -> set[str]:
    data = json.loads(catalog_path.read_text(encoding="utf-8"))
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        raise ValueError("catalog JSON must contain a 'cards' array")
    codes: set[str] = set()
    for c in cards:
        if not isinstance(c, dict):
            continue
        code = str(c.get("card_code") or "").strip().upper()
        if code:
            codes.add(code)
    return codes


def normalize_set_folder(card_code: str) -> str:
    """OP14-020 -> OP14, P-002 -> P."""
    part = card_code.split("-", 1)[0].strip().upper()
    return part or "MISC"


def ext_from_url(url: str, content_type: str | None) -> str:
    path = urlparse(url).path
    m = IMAGE_EXT_RE.search(path)
    if m:
        return "." + m.group(1).lower()
    ct = (content_type or "").split(";")[0].strip().lower()
    if "webp" in ct:
        return ".webp"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    return ".bin"


def classify_variant(
    row: dict[str, Any],
    *,
    primary_normal_key: tuple[str, str] | None,
    row_key: tuple[str, str],
) -> str:
    """Return subfolder name: base, sp, tr, alt_art."""
    iu = (row.get("iu") or "").lower()
    name = (row.get("n") or "").lower()
    r = str(row.get("r") or "")

    if "treasure" in iu or "_tr." in iu or "treasure rare" in name:
        return "tr"
    if r in ("8", "9"):
        return "sp"
    if r == "7":
        return "alt_art"
    if primary_normal_key and row_key == primary_normal_key:
        return "base"
    return "alt_art"


def dest_path_for_row(
    card_code: str,
    variant: str,
    url: str,
) -> Path:
    """Build destination path under set folder."""
    set_folder = normalize_set_folder(card_code)
    parsed = urlparse(url)
    remote_base = Path(parsed.path).name.split("?")[0]
    ext = ext_from_url(url, None)
    if variant == "base":
        fname = f"{card_code}{ext}"
    else:
        if remote_base and IMAGE_EXT_RE.search(remote_base):
            fname = remote_base
        else:
            fname = f"{card_code}{ext}"
    return Path(set_folder) / variant / fname


def load_site_rows(site_json_path: Path | None) -> list[dict[str, Any]]:
    if site_json_path is not None:
        raw = site_json_path.read_bytes()
    else:
        from curl_cffi import requests

        time.sleep(REQUEST_DELAY_SEC)
        r = requests.get(
            SITE_CARDS_URL,
            impersonate=IMPERSONATE,
            timeout=120,
            headers={"Referer": f"{SITE_ORIGIN}/"},
        )
        if r.status_code != 200:
            raise RuntimeError(f"cards.json HTTP {r.status_code}")
        raw = r.content
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError("site cards.json must be a JSON array")
    return [x for x in data if isinstance(x, dict)]


def build_download_plan(
    catalog_codes: set[str],
    site_rows: list[dict[str, Any]],
) -> list[tuple[str, Path, str]]:
    """
    List of (card_code, relative_dest_path_under_out_root, url).
    """
    by_cid: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in site_rows:
        cid = str(row.get("cid") or "").strip().upper()
        if not cid or cid not in catalog_codes:
            continue
        iu = str(row.get("iu") or "").strip()
        if not iu or not iu.startswith("http"):
            continue
        if "onepiece-cardgame.dev" not in iu:
            continue
        by_cid[cid].append(row)

    plan: list[tuple[str, Path, str]] = []

    for cid, rows in by_cid.items():
        def sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
            iu = str(row.get("iu") or "")
            en = 0 if "_en." in iu.lower() else 1
            try:
                gid = int(str(row.get("gid") or "0"))
            except ValueError:
                gid = 0
            return (en, gid, iu)

        rows_sorted = sorted(rows, key=sort_key)
        # AA (7) and Special (8/9) go to alt_art / sp; everything else can claim "base" for first print.
        normal_rows = [r for r in rows_sorted if str(r.get("r") or "") not in ("7", "8", "9")]

        def row_key(row: dict[str, Any]) -> tuple[str, str]:
            return (str(row.get("gid") or ""), str(row.get("iu") or ""))

        primary_normal_key: tuple[str, str] | None = (
            row_key(normal_rows[0]) if normal_rows else None
        )

        seen_urls: set[str] = set()
        for row in rows_sorted:
            url = str(row.get("iu") or "").strip()
            if url in seen_urls:
                continue
            seen_urls.add(url)

            rk = row_key(row)
            variant = classify_variant(row, primary_normal_key=primary_normal_key, row_key=rk)
            rel = dest_path_for_row(cid, variant, url)
            plan.append((cid, rel, url))

    plan.sort(key=lambda x: (str(x[1]), x[2]))
    return plan


def download_one(url: str, dest: Path, session) -> tuple[bool, str]:
    from curl_cffi import requests

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        time.sleep(REQUEST_DELAY_SEC)
        r = session.get(
            url,
            impersonate=IMPERSONATE,
            timeout=DOWNLOAD_TIMEOUT_SEC,
            headers={"Referer": f"{SITE_ORIGIN}/"},
        )
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        ext = ext_from_url(url, r.headers.get("Content-Type"))
        if dest.suffix.lower() == ".bin" and ext != ".bin":
            dest = dest.with_suffix(ext)
        dest.write_bytes(r.content)
        return True, ""
    except Exception as e:
        return False, str(e)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch OP TCG card images to D:\\Miru_Assets")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Path to community_cardlist.json",
    )
    parser.add_argument(
        "--site-json",
        type=Path,
        default=None,
        help="Local copy of onepiece-cardgame.dev/cards.json (skip network if set)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT_ROOT,
        help="Output root (default D:\\Miru_Assets)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help="Log file path",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max downloads to attempt (0 = no limit)")
    args = parser.parse_args()

    if not _need_curl_cffi():
        print("ERROR: Install curl_cffi:  pip install curl_cffi", file=sys.stderr)
        return 2

    if not args.catalog.is_file():
        print(f"ERROR: catalog not found: {args.catalog}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    args.log.parent.mkdir(parents=True, exist_ok=True)

    catalog_codes = load_catalog_codes(args.catalog)
    site_rows = load_site_rows(args.site_json)
    plan = build_download_plan(catalog_codes, site_rows)

    stats = {
        "attempted": 0,
        "downloaded": 0,
        "skipped_exists": 0,
        "errors": 0,
    }
    errors_sample: list[str] = []

    from curl_cffi import requests

    session = requests.Session()

    def log_line(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}\n"
        with args.log.open("a", encoding="utf-8") as fp:
            fp.write(line)

    log_line("RUN_START catalog=%s site_json=%s limit=%s" % (args.catalog, args.site_json, args.limit))

    for i, (code, rel, url) in enumerate(plan):
        if args.limit and stats["attempted"] >= args.limit:
            break
        stats["attempted"] += 1
        dest = args.out / rel
        if dest.is_file() and dest.stat().st_size > 0:
            stats["skipped_exists"] += 1
            log_line(f"SKIP_EXISTS {code} -> {dest}")
            continue
        ok, err = download_one(url, dest, session)
        if ok:
            stats["downloaded"] += 1
            log_line(f"OK {code} {url} -> {dest}")
        else:
            stats["errors"] += 1
            if len(errors_sample) < 20:
                errors_sample.append(f"{code} {err} url={url[:120]}")
            log_line(f"ERROR {code} {err} url={url}")

    log_line(
        "RUN_END attempted=%s downloaded=%s skipped=%s errors=%s"
        % (stats["attempted"], stats["downloaded"], stats["skipped_exists"], stats["errors"])
    )

    print(json.dumps({"stats": stats, "errors_sample": errors_sample, "log": str(args.log)}, indent=2))
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
