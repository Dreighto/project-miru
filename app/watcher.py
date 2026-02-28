import os
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import csv
import io
import time
import json
import random
import requests
from dotenv import load_dotenv

load_dotenv()

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
WATCHLIST_CSV_URL = os.getenv("WATCHLIST_CSV_URL")

CHECK_MINUTES = int(os.getenv("CHECK_MINUTES", "20"))
JITTER_MINUTES = int(os.getenv("JITTER_MINUTES", "5"))

STATE_PATH = "/data/state.json"
PRICES_PATH = "/data/prices.json"
PRODUCT_CACHE_PATH = "/data/product_cache.json"

PRICING_URL = "https://mpapi.tcgplayer.com/v2/product/{product_id}/pricepoints"
PRODUCT_PAGE_URL = "https://www.tcgplayer.com/product/{product_id}"

# Safety guard: avoid wiping state if the sheet fetch glitches
EMPTY_WATCHLIST_ABORT = True
EMPTY_WATCHLIST_MAX_CONSECUTIVE = 3  # require 3 consecutive empty reads before treating as real empty
EMPTY_WATCHLIST_STATE = "/data/empty_watchlist_count.txt"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def send_discord(message: str):
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"content": message}, timeout=15)
    except Exception as e:
        print("Discord error:", e)


def extract_product_id(url: str):
    if not url:
        return None

    try:
        path = urlparse(url).path
        m = re.search(r"/product/(\d+)", path)
        return int(m.group(1)) if m else None
    except Exception as e:
        print(f"[extract_product_id] error for url='{url}': {e}")
        return None

CARD_CODE_RE = re.compile(r"([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})", re.I)

def extract_card_code(name: str):
    if not name:
        return None
    m = CARD_CODE_RE.search(name)
    return m.group(1).upper() if m else None

def should_use_variant_v2(name: str) -> bool:
    n = (name or "").lower()
    return ("foil" in n) or ("pirate" in n)

def fetch_limitless_image(card_code: str, use_v2: bool = False) -> str | None:
    """
    Prefer the direct Limitless CDN image if possible.
    This avoids scraping and is super reliable for set codes like EB03-062.
    """
    code = (card_code or "").upper()

    # Folder is the set prefix (e.g., EB03 from EB03-062)
    folder = code.split("-")[0] if "-" in code else None

    # Try EN webp on CDN for set codes (EB03-062, OP01-001, etc.)
    if folder and folder != "P":
        cdn = f"https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/{folder}/{code}_EN.webp"
        try:
            h = requests.head(cdn, headers=HEADERS, timeout=15, allow_redirects=True)
            if h.status_code == 200:
                return cdn
        except:
            pass

    # Promo codes (P-xxx) or if CDN check fails: use the Limitless card page "Image" link
    url = f"https://onepiece.limitlesstcg.com/cards/{code}"
    if use_v2:
        url += "?v=2"

    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return None

    html = r.text or ""
    m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>\s*Image\s*</a>', html, re.I)
    if not m:
        return None

    href = m.group(1).strip()
    if href.startswith("//"):
        href = "https:" + href
    return href if href.startswith("http") else None

def fetch_official_bandai_image(card_code: str) -> str | None:
    url = f"https://asia-en.onepiece-cardgame.com/cardlist/?freewords={card_code}&series=556901"
    r = requests.get(url, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        return None

    html = r.text or ""

    # Grab all potential image sources (data-src first, then src)
    candidates = []
    for pat in [
        r'<img[^>]+data-src="([^"]+)"',
        r'<img[^>]+src="([^"]+)"',
    ]:
        candidates.extend([m.group(1).strip() for m in re.finditer(pat, html, re.I)])

    def normalize(src: str) -> str:
        if src.startswith("//"):
            return "https:" + src
        if src.startswith("../"):
            # ../images/... -> https://asia-en.onepiece-cardgame.com/images/...
            return "https://asia-en.onepiece-cardgame.com/" + src[3:]
        if src.startswith("/"):
            return "https://asia-en.onepiece-cardgame.com" + src
        return src

    # Prefer actual card image paths (these are what you want)
    # Example: /images/cardlist/card/P-093.png?...  or /images/cardlist/card/P-088.png?...
    for src in candidates:
        if not src:
            continue
        low = src.lower()
        if "thumbnail_official-shop" in low:
            continue
        if f"/images/cardlist/card/{card_code.lower()}" in low:
            return normalize(src)

    # Fallback: return any image under /images/cardlist/card/
    for src in candidates:
        if not src:
            continue
        low = src.lower()
        if "thumbnail_official-shop" in low:
            continue
        if "/images/cardlist/card/" in low:
            return normalize(src)

    return None

    return None

def get_card_image_url(name: str, cache: dict) -> str | None:
    code = extract_card_code(name)
    if not code:
        return None

    cached = cache.get(code)
    if isinstance(cached, str) and cached:
        return cached
    if cached == "":
        return None

    use_v2 = should_use_variant_v2(name)

    img = fetch_limitless_image(code, use_v2=use_v2)
    if not img:
        img = fetch_official_bandai_image(code)

    cache[code] = img or ""
    return img

def fetch_watchlist_rows():
    if not WATCHLIST_CSV_URL:
        raise RuntimeError("WATCHLIST_CSV_URL is not set in .env")

    r = requests.get(WATCHLIST_CSV_URL, headers=HEADERS, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Watchlist CSV fetch failed: HTTP {r.status_code}")

    # More robust CSV parsing (handles quoted commas/newlines better)
    text = r.text.lstrip("\ufeff")  # strip UTF-8 BOM if present
    reader = csv.DictReader(io.StringIO(text), skipinitialspace=True)

    # Validate headers so we don't treat HTML/login pages as CSV
    required = {"enabled", "name", "url", "target_price", "cooldown_minutes"}
    fieldnames = {(f or "").strip().lower() for f in (reader.fieldnames or [])}

    if not required.issubset(fieldnames):
        preview = (text[:300] or "").replace("\n", "\\n")
        raise RuntimeError(
            f"Watchlist CSV did not contain expected headers. "
            f"Got headers={sorted(fieldnames)} Preview={preview}"
        )

    def parse_bool(val: str, default=True) -> bool:
        if val is None:
            return default
        s = str(val).strip().lower()
        if s == "":
            return default
        return s in ("true", "t", "yes", "y", "1", "on")

    rows = []

    for idx, row in enumerate(reader, start=2):  # start=2 assumes header is row 1
        # Normalize keys/values
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}

        enabled = parse_bool(row.get("enabled", "true"), default=True)
        if not enabled:
            continue

        name = row.get("name", "").strip()
        url = row.get("url", "").strip()
        if not name or not url:
            continue

        product_id = extract_product_id(url)
        if not product_id:
            print(f"[watchlist row {idx}] {name}: Could not extract product_id from url: {url}")
            continue

        # Parse target price
        target_raw = row.get("target_price", "0").strip()
        try:
            target = float(target_raw) if target_raw else 0.0
        except ValueError:
            print(f"[watchlist row {idx}] {name} ({product_id}): Invalid target_price='{target_raw}', skipping.")
            continue

        # Parse cooldown minutes
        cooldown_raw = row.get("cooldown_minutes", "240").strip()
        try:
            cooldown = int(float(cooldown_raw)) if cooldown_raw else 240
        except ValueError:
            cooldown = 240

        if cooldown < 0:
            cooldown = 0

        rows.append(
            {
                "name": name,
                "url": url,
                "product_id": product_id,
                "target_price": target,
                "cooldown_minutes": cooldown,
            }
        )

    return rows

def get_lowest_price(product_id: int):
    try:
        r = requests.get(PRICING_URL.format(product_id=product_id), headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return None

        data = r.json()
        entries = data.get("results", []) if isinstance(data, dict) else data

        prices = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("lowestPrice", "lowPrice", "marketPrice"):
                val = entry.get(key)
                if isinstance(val, (int, float)) and val > 0:
                    prices.append(float(val))

        return min(prices) if prices else None

    except Exception as e:
        print("Pricing fetch error:", e)
        return None


def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return default


def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print("JSON save error:", e)


def _normalize_url(u: str):
    if not u:
        return None
    u = u.strip()
    if u.startswith("//"):
        return "https:" + u
    return u


def _extract_best_image_url(html: str, product_id: int):
    """
    Extract a likely product image URL from TCGplayer HTML when meta tags are missing.

    Strategy:
    1) Collect ALL URLs in the HTML (including protocol-relative //...)
    2) Prefer ones that:
       - include the product_id
       - look like image/CDN endpoints (contain 'image', 'cdn', 'cloudfront', 'tcgplayer')
    3) Return best match.
    """
    if not html:
        return None

    pid = str(product_id)

    # Grab URLs inside quotes OR url(...) OR bare protocol-relative
    # This is intentionally broad.
    url_candidates = set()

    # Quoted URLs: "https://..." or '//...'
    for m in re.finditer(r'["\']((?:https?:)?//[^"\']+)["\']', html, re.IGNORECASE):
        url_candidates.add(_normalize_url(m.group(1)))

    # CSS url(...)
    for m in re.finditer(r'url\(([^)]+)\)', html, re.IGNORECASE):
        raw = m.group(1).strip().strip('"').strip("'")
        if raw.startswith("http") or raw.startswith("//"):
            url_candidates.add(_normalize_url(raw))

    # Filter out obvious non-useful URLs
    cleaned = []
    for u in url_candidates:
        if not u:
            continue
        lu = u.lower()

        # skip scripts, css, fonts
        if any(lu.endswith(x) for x in (".js", ".css", ".woff", ".woff2", ".ttf", ".ico")):
            continue
        if "googletagmanager" in lu or "doubleclick" in lu:
            continue

        cleaned.append(u)

    # Scoring: higher = more likely to be the product image
    def score(u: str):
        lu = u.lower()
        s = 0
        if pid in u:
            s += 50
        if "image" in lu or "img" in lu:
            s += 15
        if "cdn" in lu or "cloudfront" in lu:
            s += 15
        if "tcgplayer" in lu:
            s += 10
        if "product" in lu:
            s += 5
        # small penalty for png because on this page it might be icons
        if ".png" in lu:
            s -= 3
        return s

    cleaned.sort(key=score, reverse=True)

    # Return first decently-scored URL
    for u in cleaned:
        if score(u) >= 20:
            return u

    return None


def get_product_image_url(product_id: int, cache: dict):
    pid = str(product_id)

    cached = cache.get(pid)
    if isinstance(cached, str) and cached:
        return cached
    if cached == "":
        return None

    try:
        url = PRODUCT_PAGE_URL.format(product_id=product_id)
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        if r.status_code != 200:
            cache[pid] = ""
            save_json(PRODUCT_CACHE_PATH, cache)
            return None

        img = _extract_best_image_url(r.text, product_id)

        if img:
            cache[pid] = img
        else:
            cache[pid] = ""

        save_json(PRODUCT_CACHE_PATH, cache)
        return img

    except Exception as e:
        print("Image fetch error:", e)
        return None

def _read_empty_count() -> int:
    try:
        with open(EMPTY_WATCHLIST_STATE, "r", encoding="utf-8") as f:
            return int((f.read() or "0").strip())
    except Exception:
        return 0

def _write_empty_count(n: int) -> None:
    try:
        with open(EMPTY_WATCHLIST_STATE, "w", encoding="utf-8") as f:
            f.write(str(int(n)))
    except Exception:
        pass


def main():
    print("🟢 TCG Watcher Started (Google Sheet mode)")

    state = load_json(STATE_PATH, {"last_alert_ts": {}, "last_alert_price": {}})
    prices_data = load_json(PRICES_PATH, {})
    product_cache = load_json(PRODUCT_CACHE_PATH, {})

    while True:
        try:
            watchlist = fetch_watchlist_rows()
            print(f"Loaded {len(watchlist)} active items from sheet.")

            # SAFETY GUARD: if sheet returns 0 rows, do NOT prune/wipe
            if EMPTY_WATCHLIST_ABORT and len(watchlist) == 0:
                cnt = _read_empty_count() + 1
                _write_empty_count(cnt)

                print(
                    f"⚠️ Watchlist returned 0 rows ({cnt}/{EMPTY_WATCHLIST_MAX_CONSECUTIVE}). "
                    "Skipping prune/update to avoid wiping prices.json due to a transient sheet issue."
                )

                if cnt < EMPTY_WATCHLIST_MAX_CONSECUTIVE:
                    # IMPORTANT: use continue if inside while True loop, otherwise return
                    continue
            else:
                _write_empty_count(0)

            # Only prune AFTER the guard passes
            active_ids = {str(item["product_id"]) for item in watchlist}
            prices_data = {k: v for k, v in prices_data.items() if str(k) in active_ids}

            for item in watchlist:
                name = item["name"]
                product_id = item["product_id"]
                target = item["target_price"]
                url = item["url"]
                cooldown = item["cooldown_minutes"]

                price = get_lowest_price(product_id)
                if price is None:
                    print(f"{name}: Could not detect price.")
                    continue

                # 👇 image pulled from Limitless / fallback
                image_url = get_card_image_url(name, product_cache)

                print(f"{name}: Lowest ${price:.2f} | Target ${target:.2f}")

                code = extract_card_code(name)
                
                key = str(product_id)
                prices_data[key] = {
                    "name": name,
                    "url": url,
                    "product_id": product_id,
                    "price": price,
                    "target": target,
                    "code": code,
                    "last_checked_ts": int(time.time()),
                }

                now = int(time.time())
                last_ts = int(state["last_alert_ts"].get(name, 0) or 0)

                if price <= target and (now - last_ts) > (cooldown * 60):
                    send_discord(f"💸 {name} hit ${price:.2f}\n{url}")
                    state["last_alert_ts"][name] = now
                    state["last_alert_price"][name] = price
                    save_json(STATE_PATH, state)

            save_json(PRICES_PATH, prices_data)
            save_json(PRODUCT_CACHE_PATH, product_cache)

            base_sleep = CHECK_MINUTES * 60
            jitter = random.randint(-JITTER_MINUTES * 60, JITTER_MINUTES * 60)
            sleep_time = max(60, base_sleep + jitter)
            print(f"Sleeping for {sleep_time // 60} minutes...")
            time.sleep(sleep_time)

        except Exception as e:
            print("Main loop error:", e)
            time.sleep(120)


if __name__ == "__main__":
    main()