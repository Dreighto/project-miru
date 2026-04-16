"""
Fetch TCGCSV groups + products + prices for One Piece Card Game.

Output: data/tcgcsv/<group_id>/products.json, prices.json, and manifest.json
Resume-safe: skips groups that already have both JSON files.

Note: TCGCSV lists "One Piece Card Game" as categoryId 68. Category 67 is a
different title (see https://tcgcsv.com/tcgplayer/categories). Override with
environment variable TCGCSV_CATEGORY_ID if needed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Worktree root (parent of tools/)
ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "tcgcsv"
BASE = "https://tcgcsv.com"
CATEGORY_ID = int(os.environ.get("TCGCSV_CATEGORY_ID", "68"))
GROUPS_URL = f"{BASE}/tcgplayer/{CATEGORY_ID}/groups"
FETCH_DELAY_SEC = 1.0
USER_AGENT = "tcg-watcher-fetch_tcgcsv_opcg_groups/1.0 (+local)"


def _request_json(url: str, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(
        f"TCGCSV category {CATEGORY_ID} (override with TCGCSV_CATEGORY_ID). "
        f"Groups URL: {GROUPS_URL}"
    )

    try:
        groups_payload = _request_json(GROUPS_URL)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
        print(f"FAILED to load groups: {e}", file=sys.stderr)
        return 1

    if not groups_payload.get("success"):
        print(f"Groups API success=false: {groups_payload.get('errors')}", file=sys.stderr)
        return 1

    results = groups_payload.get("results") or []
    total_api = len(results)
    fetched_ok = 0
    skipped = 0
    failed: list[tuple[int, str, str]] = []

    manifest_rows: list[dict] = []

    for idx, g in enumerate(results):
        gid = int(g.get("groupId") or 0)
        gname = str(g.get("name") or "").strip()
        folder = OUT_DIR / str(gid)
        rel_folder = f"data/tcgcsv/{gid}"
        products_f = folder / "products.json"
        prices_f = folder / "prices.json"

        if products_f.is_file() and prices_f.is_file():
            skipped += 1
            manifest_rows.append(
                {
                    "group_id": gid,
                    "group_name": gname,
                    "folder_path": rel_folder,
                    "fetch_status": "skipped",
                }
            )
            print(f"[skipped] {gid} {gname!r}")
            continue

        network_used = False
        try:
            folder.mkdir(parents=True, exist_ok=True)
            products_url = f"{BASE}/tcgplayer/{CATEGORY_ID}/{gid}/products"
            prices_url = f"{BASE}/tcgplayer/{CATEGORY_ID}/{gid}/prices"

            prod = _request_json(products_url)
            network_used = True
            prices = _request_json(prices_url)
            network_used = True

            _write_json(products_f, prod)
            _write_json(prices_f, prices)

            fetched_ok += 1
            manifest_rows.append(
                {
                    "group_id": gid,
                    "group_name": gname,
                    "folder_path": rel_folder,
                    "fetch_status": "success",
                }
            )
            print(f"[success] {gid} {gname!r}")
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError) as e:
            err = str(e)
            failed.append((gid, gname, err))
            manifest_rows.append(
                {
                    "group_id": gid,
                    "group_name": gname,
                    "folder_path": rel_folder,
                    "fetch_status": "failed",
                    "error": err,
                }
            )
            print(f"[failed] {gid} {gname!r} — {err}", file=sys.stderr)

        if network_used and idx < len(results) - 1:
            time.sleep(FETCH_DELAY_SEC)

    manifest_path = OUT_DIR / "manifest.json"
    _write_json(
        manifest_path,
        {
            "category_id": CATEGORY_ID,
            "groups_url": GROUPS_URL,
            "total_groups_from_api": total_api,
            "fetched_success": fetched_ok,
            "skipped_existing": skipped,
            "failed_count": len(failed),
            "groups": manifest_rows,
        },
    )

    print("--- summary ---")
    print(f"total groups from API: {total_api}")
    print(f"fetched (success): {fetched_ok}")
    print(f"skipped (both files existed): {skipped}")
    print(f"failed: {len(failed)}")
    for gid, gname, err in failed:
        print(f"  failed {gid} {gname!r}: {err}")
    print(f"manifest rows (groups): {len(manifest_rows)}")
    print(f"manifest written: {manifest_path.relative_to(ROOT)}")

    subdirs = sorted(
        p for p in OUT_DIR.iterdir() if p.is_dir() and p.name != "__pycache__"
    )
    print(f"data/tcgcsv subfolders (count={len(subdirs)}): {[p.name for p in subdirs]}")

    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
