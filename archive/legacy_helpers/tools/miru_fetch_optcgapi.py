#!/usr/bin/env python3
"""
miru_fetch_optcgapi.py

Fetch One Piece card rows from optcgapi.com (tier-2 public JSON API, no auth) and write
``data/snapshots/optcgapi.json``.

Reconnaissance (2026-03-22, GET https://optcgapi.com + /documentation):
  - Site root describes OP-01 through OP-14 English data + starter decks; live ``/api/allSets/``
    currently lists main boosters OP-01 … OP-13 (no ``OP-14`` set slug — ``/api/sets/OP-14/`` 404).
  - Documentation: https://optcgapi.com/documentation
  - Relevant endpoints:
      GET https://optcgapi.com/api/allSets/  -> list[{set_name, set_id}]  (set_id like ``OP-01``)
      GET https://optcgapi.com/api/sets/{set_id}/  -> list of card objects (includes parallel prints)
      GET https://optcgapi.com/api/sets/card/{card_id}/  -> list of variants for one code
  - Sample card object keys: inventory_price, market_price, card_name, set_name, card_text,
    set_id, rarity, card_set_id, card_color, card_type, life, card_cost, card_power,
    sub_types, counter_amount, attribute, date_scraped, card_image_id, card_image

This fetcher keeps only fields aligned with existing snapshot/catalog card payloads (same
general shape as ``data/snapshots/community_cardlist.json``): card_code, card_name,
set_code, set_name, color, card_type, cost, power, counter, life, rarity, attribute,
traits, effect_text, trigger_text. Parallel / alt-art rows share ``card_code`` but differ
in ``card_name``; all rows are retained.

Ethics: sequential requests with a fixed delay; identifiable User-Agent; no parallelism.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "snapshots" / "optcgapi.json"
DEFAULT_VALIDATION = ROOT / "data" / "optcgapi_fetch_validation_v1.txt"
DEFAULT_CATALOG = ROOT / "data" / "card_catalog.db"
API_BASE = "https://optcgapi.com/api"
USER_AGENT = "ProjectMiru/1.0 (optcgapi snapshot fetcher; conservative pacing; no API key)"

REQUEST_INTERVAL_SEC = 0.85
HTTP_TIMEOUT_SEC = 120

SET_ID_RE = re.compile(r"^OP-(\d{2})$")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("miru_fetch_optcgapi")


def _http_get_json(url: str) -> Any:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _sleep_pace() -> None:
    time.sleep(REQUEST_INTERVAL_SEC)


def _normalize_set_code(set_id: str) -> str:
    """API ``OP-01`` -> catalog-style ``OP01``."""
    s = str(set_id or "").strip().upper()
    if "-" in s:
        parts = s.split("-", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[0]}{parts[1]}"
    return s.replace("-", "")


def _normalize_card_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Map API row to community_cardlist-style keys only."""
    sub = raw.get("sub_types")
    if isinstance(sub, str):
        traits = sub.strip()
    elif sub is None:
        traits = ""
    else:
        traits = str(sub).strip()

    def t(key: str) -> str:
        v = raw.get(key)
        if v is None:
            return ""
        return str(v).strip()

    card_code = t("card_set_id").upper()
    set_id = t("set_id")
    return {
        "card_code": card_code,
        "card_name": t("card_name"),
        "set_code": _normalize_set_code(set_id),
        "set_name": t("set_name"),
        "color": t("card_color"),
        "card_type": t("card_type"),
        "cost": t("card_cost") if raw.get("card_cost") is not None else "",
        "power": t("card_power") if raw.get("card_power") is not None else "",
        "counter": t("counter_amount") if raw.get("counter_amount") is not None else "",
        "life": t("life") if raw.get("life") is not None else "",
        "rarity": t("rarity"),
        "attribute": t("attribute"),
        "traits": traits,
        "effect_text": t("card_text"),
        "trigger_text": "",
    }


def _select_op_sets(all_sets: list[dict[str, Any]], max_num: int = 14) -> list[str]:
    """Return API set_id values OP-01 .. OP-14 when present in allSets."""
    selected: list[str] = []
    for row in all_sets:
        sid = str(row.get("set_id") or "").strip()
        m = SET_ID_RE.match(sid)
        if not m:
            continue
        n = int(m.group(1), 10)
        if 1 <= n <= max_num:
            selected.append(sid)
    # Stable OP-01 .. OP-14 order
    def sort_key(s: str) -> int:
        m2 = SET_ID_RE.match(s)
        return int(m2.group(1), 10) if m2 else 999

    selected.sort(key=sort_key)
    return selected


def fetch_snapshot(*, output_path: Path) -> dict[str, Any]:
    _sleep_pace()
    all_sets = _http_get_json(f"{API_BASE}/allSets/")
    if not isinstance(all_sets, list):
        raise RuntimeError("allSets response is not a list")

    set_ids = _select_op_sets(all_sets, max_num=14)
    cards_out: list[dict[str, Any]] = []
    sets_fetched: list[str] = []
    sets_failed: list[dict[str, str]] = []

    for sid in set_ids:
        url = f"{API_BASE}/sets/{sid}/"
        try:
            _sleep_pace()
            block = _http_get_json(url)
        except HTTPError as e:
            sets_failed.append({"set_id": sid, "error": f"HTTP {e.code}", "url": url})
            log.warning("Failed set %s: HTTP %s", sid, e.code)
            continue
        except (URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            sets_failed.append({"set_id": sid, "error": str(e), "url": url})
            log.warning("Failed set %s: %s", sid, e)
            continue

        if not isinstance(block, list):
            sets_failed.append({"set_id": sid, "error": "response not a list", "url": url})
            continue

        sets_fetched.append(sid)
        for raw in block:
            if isinstance(raw, dict):
                cards_out.append(_normalize_card_row(raw))

    payload: dict[str, Any] = {
        "schema_version": 1,
        "source_id": "optcgapi",
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api_base": API_BASE,
        "documentation_url": "https://optcgapi.com/documentation",
        "recon_note": (
            "Main boosters are requested as /api/sets/{set_id}/ for each OP-NN from /api/allSets/ "
            "with 1<=N<=14. As of recon, API lists OP-01..OP-13 only; OP-14 returns 404 and is omitted."
        ),
        "sets_requested": list(set_ids),
        "sets_fetched": sets_fetched,
        "sets_failed": sets_failed,
        "summary": {
            "set_count_fetched": len(sets_fetched),
            "total_card_rows": len(cards_out),
        },
        "cards": cards_out,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info(
        "Wrote %s (%s sets, %s card rows)",
        output_path,
        len(sets_fetched),
        len(cards_out),
    )
    return payload


def _catalog_card_name(catalog_db: Path, canonical_code: str) -> str | None:
    if not catalog_db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{catalog_db.as_posix()}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT card_name FROM cards WHERE canonical_code = ? LIMIT 1",
            (canonical_code.upper().strip(),),
        ).fetchone()
        conn.close()
        return str(row[0]).strip() if row and row[0] else None
    except sqlite3.Error:
        return None


def write_validation(
    *,
    snapshot_path: Path,
    validation_path: Path,
    catalog_db: Path,
    cross_codes: tuple[str, ...] = ("OP01-001", "OP07-097", "OP13-002"),
) -> dict[str, Any]:
    snap = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cards = snap.get("cards") or []
    by_code: dict[str, list[dict[str, Any]]] = {}
    for c in cards:
        if not isinstance(c, dict):
            continue
        code = str(c.get("card_code") or "").strip().upper()
        if not code:
            continue
        by_code.setdefault(code, []).append(c)

    set_codes = sorted({str(c.get("set_code") or "").strip().upper() for c in cards if isinstance(c, dict)})

    xref: dict[str, Any] = {}
    for code in cross_codes:
        rows = by_code.get(code.upper(), [])
        cat_name = _catalog_card_name(catalog_db, code)
        snap_names = [str(r.get("card_name") or "").strip() for r in rows]
        xref[code] = {
            "in_snapshot": bool(rows),
            "snapshot_row_count": len(rows),
            "snapshot_card_names": snap_names[:6],
            "catalog_card_name": cat_name,
            "name_match_any_row": any(cat_name and sn and cat_name.lower() == sn.lower() for sn in snap_names),
        }

    report = {
        "validation_version": 1,
        "snapshot_path": str(snapshot_path.relative_to(ROOT))
        if ROOT in snapshot_path.parents or snapshot_path == ROOT
        else str(snapshot_path),
        "valid_json": True,
        "total_cards": len(cards),
        "sets_present_set_codes": set_codes,
        "sets_fetched_api_ids": snap.get("sets_fetched") or [],
        "cross_reference": xref,
        "catalog_db_readonly": str(catalog_db),
    }
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    validation_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info("Wrote validation %s", validation_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch optcgapi.com OP booster cards into data/snapshots/optcgapi.json")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output JSON path (default: data/snapshots/optcgapi.json)",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Skip fetch; only read snapshot and write validation JSON",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=DEFAULT_CATALOG,
        help="Read-only card_catalog.db for cross-reference",
    )
    args = parser.parse_args()

    try:
        if not args.validate_only:
            fetch_snapshot(output_path=args.output)
        write_validation(
            snapshot_path=args.output,
            validation_path=DEFAULT_VALIDATION,
            catalog_db=args.catalog,
        )
    except Exception as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
