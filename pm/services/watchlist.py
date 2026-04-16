import json
import os
import time
from pathlib import Path

from db import PROJECT_ROOT
from services.pricing import load_card_prices

PRICES_PATH = Path(
    os.getenv("PROJECT_MIRU_PRICES_PATH", str(PROJECT_ROOT / "data" / "prices.json"))
)

def load_prices() -> list[dict]:
    if not PRICES_PATH.is_file():
        return []
    try:
        with PRICES_PATH.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            return [dict(v or {}) for v in payload.values()]
        if isinstance(payload, list):
            return [dict(v or {}) for v in payload if isinstance(v, dict)]
    except Exception:
        return []
    return []


def add_watchlist_item(body: dict) -> tuple[dict, int]:
    code = str(body.get("code") or "").strip().upper()
    if not code:
        return {"ok": False, "reason": "missing_code"}, 400
    target_raw = body.get("target")
    try:
        target = float(target_raw) if target_raw is not None else None
    except (TypeError, ValueError):
        target = None

    price_entry = load_card_prices().get(code) or {}
    name = str(price_entry.get("name") or code).strip()
    market = price_entry.get("market") or 0
    product_id_raw = str(price_entry.get("product_id") or "").strip()
    tcgplayer_url = str(price_entry.get("tcgplayer_url") or "").strip()

    try:
        watchlist: dict = {}
        if PRICES_PATH.is_file():
            with PRICES_PATH.open("r", encoding="utf-8") as fh:
                watchlist = json.load(fh)

        for entry in watchlist.values():
            if isinstance(entry, dict) and str(entry.get("code") or "").upper() == code:
                return {"ok": False, "reason": "already_in_watchlist"}, 200

        key = product_id_raw if product_id_raw else code
        try:
            product_id_stored = (
                int(product_id_raw) if product_id_raw.isdigit() else product_id_raw
            )
        except Exception:
            product_id_stored = product_id_raw

        watchlist[key] = {
            "code": code,
            "name": name,
            "price": market,
            "target": target,
            "product_id": product_id_stored,
            "url": tcgplayer_url,
            "last_checked_ts": int(time.time()),
        }

        with PRICES_PATH.open("w", encoding="utf-8") as fh:
            json.dump(watchlist, fh, indent=2)

        return {"ok": True, "code": code}, 200
    except Exception:
        return {"ok": False, "reason": "write_error"}, 200

def remove_watchlist_item(body: dict) -> tuple[dict, int]:
    code = str(body.get("code") or "").strip().upper()
    if not code:
        return {"ok": False, "reason": "missing_code"}, 400

    try:
        if not PRICES_PATH.is_file():
            return {"ok": False, "reason": "not_found"}, 200

        with PRICES_PATH.open("r", encoding="utf-8") as fh:
            watchlist = json.load(fh)

        key_to_remove = None
        for k, entry in watchlist.items():
            if isinstance(entry, dict) and str(entry.get("code") or "").upper() == code:
                key_to_remove = k
                break

        if key_to_remove is None:
            return {"ok": False, "reason": "not_found"}, 200

        del watchlist[key_to_remove]

        with PRICES_PATH.open("w", encoding="utf-8") as fh:
            json.dump(watchlist, fh, indent=2)

        return {"ok": True}, 200
    except Exception:
        return {"ok": False, "reason": "write_error"}, 200
