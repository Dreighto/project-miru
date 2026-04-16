"""
fetch_prices.py
===============
Fetch current market prices for all One Piece Card Game singles from TCGCSV
(https://tcgcsv.com), which mirrors TCGplayer pricing daily.

Writes output to data/card_prices.json, keyed by Bandai card code
(e.g. OP01-001).

Usage:
    python scripts/fetch_prices.py

Dependencies: requests (third-party), csv / json / time / pathlib (stdlib)
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    sys.exit(
        "ERROR: 'requests' is not installed.\n" "Install it with:  pip install requests"
    )

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
DATA_DIR = _PROJECT_ROOT / "data"
OUTPUT_PATH = DATA_DIR / "card_prices.json"

# ---------------------------------------------------------------------------
# TCGCSV constants
# ---------------------------------------------------------------------------
CATEGORY_ID = 68  # One Piece Card Game
GROUPS_URL = f"https://tcgcsv.com/tcgplayer/{CATEGORY_ID}/groups"
CSV_URL_TEMPLATE = "https://tcgcsv.com/tcgplayer/{cat}/{group}/ProductsAndPrices.csv"
REQUEST_DELAY_S = 0.5  # polite inter-request delay


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: str) -> float | None:
    """Return float from string, or None if blank / non-numeric."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        f = float(v)
        return f if f > 0 else None
    except ValueError:
        return None


def _fetch_groups(session: requests.Session) -> list[dict]:
    """Fetch the list of set groups from TCGCSV.  Exits on failure."""
    print("Fetching groups...")
    try:
        resp = session.get(GROUPS_URL, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        sys.exit(f"ERROR: Could not fetch groups from {GROUPS_URL}\n{exc}")

    try:
        data = resp.json()
    except ValueError:
        sys.exit(f"ERROR: Groups endpoint returned non-JSON response.")

    if not isinstance(data, dict):
        sys.exit("ERROR: Groups response was not an object.")

    if not data.get("success") or not data.get("results"):
        sys.exit("ERROR: Groups response indicates failure or has no results.")

    results = data["results"]
    if not isinstance(results, list):
        sys.exit("ERROR: Groups response 'results' is not a list.")

    return results


def _fetch_group_csv(
    session: requests.Session,
    group_id: int | str,
    group_name: str,
) -> list[dict] | None:
    """
    Fetch and parse the ProductsAndPrices CSV for one group.
    Returns a list of raw row dicts, or None on network error.
    Empty responses (future sets) return an empty list.
    """
    url = CSV_URL_TEMPLATE.format(cat=CATEGORY_ID, group=group_id)
    try:
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"  WARNING: Could not fetch group {group_id} ({group_name}): {exc}")
        return None

    content = resp.text.strip()
    if not content:
        return []

    try:
        reader = csv.DictReader(io.StringIO(content))
        return list(reader)
    except Exception:
        return []


def _process_rows(rows: list[dict]) -> dict[str, dict]:
    """
    Process CSV rows for one group.

    Returns a dict keyed by extNumber with:
      market, low, name, rarity, alt_art_market

    Rules:
    - Skip rows where extNumber is blank (sealed products)
    - Skip rows where marketPrice is blank or 0
    - subTypeName == "Normal"  → primary entry
    - subTypeName != "Normal" (Foil / Alt Art) → alt_art_market field
    - Duplicate extNumber within a group: prefer Normal; store alt in alt_art_market
    """
    result: dict[str, dict] = {}

    for row in rows:
        try:
            code = (row.get("extNumber") or "").strip()
            if not code:
                continue  # sealed product

            market = _safe_float(row.get("marketPrice") or "")
            if market is None:
                continue  # no usable price

            low = _safe_float(row.get("lowPrice") or "")
            name = (row.get("name") or "").strip()
            rarity = (row.get("extRarity") or "").strip()
            sub_type = (row.get("subTypeName") or "").strip()

            is_normal = sub_type.lower() == "normal"

            if code not in result:
                # First time we see this code — store regardless of subtype
                result[code] = {
                    "market": market,
                    "low": low,
                    "name": name,
                    "rarity": rarity,
                    "product_id": (row.get("productId") or "").strip(),
                    "alt_art_market": None,
                    "_is_normal": is_normal,
                }
            else:
                existing = result[code]
                if is_normal and not existing["_is_normal"]:
                    # Upgrade: replace alt-art entry with Normal as primary,
                    # demote previous entry to alt_art_market
                    alt_prev = existing["market"]
                    result[code] = {
                        "market": market,
                        "low": low,
                        "name": name,
                        "rarity": rarity,
                        "product_id": (row.get("productId") or "").strip(),
                        "alt_art_market": alt_prev,
                        "_is_normal": True,
                    }
                elif not is_normal and existing["_is_normal"]:
                    # Current row is alt art; existing is Normal — just update alt price
                    existing["alt_art_market"] = market
                else:
                    # Same type twice — keep whichever has higher market price
                    if market > existing["market"]:
                        existing["market"] = market
                        existing["low"] = low
                        existing["name"] = name
                        existing["rarity"] = rarity
        except Exception:
            # Malformed row — skip silently
            continue

    for code, entry in result.items():
        pid = entry.get("product_id") or ""
        entry["tcgplayer_url"] = (
            f"https://www.tcgplayer.com/product/{pid}" if pid else ""
        )

    return result


def _merge_into(
    master: dict[str, dict],
    incoming: dict[str, dict],
) -> None:
    """
    Merge an incoming group result into the master prices dict.

    For duplicate card codes (reprints), keep the entry with the
    higher marketPrice.
    """
    for code, entry in incoming.items():
        if code not in master:
            master[code] = entry
        else:
            existing = master[code]
            incoming_normal = entry.get("_is_normal", False)
            existing_normal = existing.get("_is_normal", False)
            if incoming_normal and not existing_normal:
                # Incoming is Normal, existing is alt — prefer Normal
                master[code] = entry
            elif incoming_normal == existing_normal:
                # Same type — keep higher market price
                if entry["market"] > existing["market"]:
                    master[code] = entry
            # else: existing is Normal, incoming is alt — keep existing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def fetch_prices() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "ProjectMiru-PriceFetcher/1.0"})

    groups = _fetch_groups(session)
    print(f"Found {len(groups)} groups")

    master_prices: dict[str, dict] = {}

    for group in groups:
        group_id = group.get("groupId") or group.get("id") or group.get("group_id")
        group_name = (
            group.get("name") or group.get("groupName") or str(group_id)
        ).strip()

        if not group_id:
            print("  WARNING: Skipping group with no ID")
            continue

        time.sleep(REQUEST_DELAY_S)

        rows = _fetch_group_csv(session, group_id, group_name)
        if rows is None:
            # Network error — already printed warning
            continue

        group_prices = _process_rows(rows)
        card_count = len(group_prices)

        print(f"  [{group_name}] {card_count} cards")
        _merge_into(master_prices, group_prices)

    for entry in master_prices.values():
        entry.pop("_is_normal", None)

    total = len(master_prices)

    output = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "tcgcsv.com / TCGplayer",
        "category_id": CATEGORY_ID,
        "total_cards": total,
        "prices": master_prices,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")

    print(f"Done — {total} total cards priced, written to {OUTPUT_PATH}")


if __name__ == "__main__":
    fetch_prices()
