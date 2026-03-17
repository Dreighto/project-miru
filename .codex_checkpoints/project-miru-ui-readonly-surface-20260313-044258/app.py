import os
import copy
import json
import re
import shutil
import sqlite3
import time
import html as html_lib
from flask import Flask, Response, redirect, request, send_from_directory
from urllib.parse import quote

try:
    from PIL import Image, ImageFilter, ImageOps
except Exception:
    Image = None
    ImageFilter = None
    ImageOps = None

PRICES_PATH = "/data/prices.json"
CATALOG_DB_PATH = "/data/card_catalog.db"
CATALOG_DB_CACHE_PATH = "/tmp/project_miru_card_catalog.db"
IMAGES_ROOT = "/images"
THUMB_CACHE_ROOT = "/tmp/project_miru_thumbs"
THUMB_DEFAULT_WIDTH = 420
LIBRARY_PAGE_SIZE = 24
HOMEPAGE_INITIAL_WATCHLIST_COUNT = 8
MAIN_RUNTIME_ROOT = os.getenv("MIRU_MAIN_RUNTIME_ROOT", r"D:\docker\tcg-watcher")
MIRU_RUNTIME_DOSSIER_DB_PATH = os.getenv(
    "MIRU_RUNTIME_DOSSIER_DB_PATH",
    os.path.join(MAIN_RUNTIME_ROOT, "data", "miru_learning_dossiers.db"),
)
MIRU_RUNTIME_IMAGES_ROOT = os.getenv(
    "MIRU_RUNTIME_IMAGES_ROOT",
    os.path.join(MAIN_RUNTIME_ROOT, "data", "miru_images"),
)
MIRU_RUNTIME_DOSSIER_DB_CACHE_PATH = "/tmp/project_miru_runtime_learning_dossiers.db"

CODE_RE = re.compile(r"\b([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\b", re.I)
ANY_CODE_VARIANT_RE = re.compile(r"([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})\(([^)]+)\)", re.I)
SORT_OPTIONS = (
    ("set_card_asc", "Set then card number"),
    ("newest_set", "Newest set first"),
    ("code_asc", "Card code ascending"),
    ("code_desc", "Card code descending"),
    ("name_asc", "Name A-Z"),
)
SET_PREFIX_ORDER = {"OP": 0, "ST": 1, "EB": 2, "PRB": 3, "P": 4}

app = Flask(__name__)
_TTL_CACHE = {}

ALT_MARKERS = (
    "alternate art", "alt art", "alt-art", "alternate-art",
    "parallel", "manga", "special art", "special",
    "pirate foil", "promo foil", "foil",
)

ILLUST_MARKERS = (
    "illustration", "illustration box", "illustrationbox",
    "illustrationboxvol", "illustrationboxvol.",
    "illustration box vol", "illustration box vol.",
)


def path_signature(path_text: str):
    try:
        if not path_text or not os.path.exists(path_text):
            return (False, 0, 0)
        stat = os.stat(path_text)
        return (True, int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        return (False, 0, 0)


def get_ttl_cached_value(key, ttl_seconds, builder, signature=None):
    now = time.time()
    entry = _TTL_CACHE.get(key)
    if entry and entry.get("expires_at", 0) > now and entry.get("signature") == signature:
        return copy.deepcopy(entry["value"])
    value = builder()
    _TTL_CACHE[key] = {
        "expires_at": now + max(float(ttl_seconds), 0.0),
        "signature": signature,
        "value": copy.deepcopy(value),
    }
    return value


def load_prices():
    signature = path_signature(PRICES_PATH)
    return get_ttl_cached_value("prices", 5.0, _load_prices_uncached, signature=signature)


def _load_prices_uncached():
    try:
        with open(PRICES_PATH, "r") as f:
            obj = json.load(f)
            return list(obj.values()) if isinstance(obj, dict) else []
    except Exception:
        return []


def load_prices_map():
    try:
        with open(PRICES_PATH, "r") as f:
            obj = json.load(f)
            return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def invalidate_prices_cache():
    _TTL_CACHE.pop("prices", None)


def save_prices_map(prices_map):
    directory = os.path.dirname(PRICES_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = PRICES_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(prices_map, f, ensure_ascii=False, separators=(",", ": "))
    os.replace(temp_path, PRICES_PATH)
    invalidate_prices_cache()


def parse_price_text(value):
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("$", "").replace(",", "")
    try:
        return round(float(text), 2)
    except Exception:
        return None


def normalize_watch_price_input(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    cleaned = raw.replace("$", "").replace(",", "")
    try:
        amount = round(float(cleaned), 2)
    except Exception:
        return None
    if amount < 0:
        return None
    return amount


def build_watchlist_record_key(code: str, prices_map: dict):
    code_text = str(code or "").strip().upper()
    if code_text:
        for key, item in prices_map.items():
            if str((item or {}).get("code") or "").strip().upper() == code_text:
                return str(key)
    return f"manual:{code_text or int(time.time())}"


def upsert_watchlist_entry(*, code: str, name: str, target_price, current_price=None, market_url: str = ""):
    prices_map = load_prices_map()
    record_key = build_watchlist_record_key(code, prices_map)
    existing = dict(prices_map.get(record_key) or {})
    now_ts = int(time.time())
    updated = {
        "name": str(name or existing.get("name") or code or "").strip(),
        "url": str(market_url or existing.get("url") or "").strip(),
        "product_id": existing.get("product_id") if existing.get("product_id") not in ("", None) else record_key,
        "price": current_price if current_price is not None else existing.get("price"),
        "target": target_price,
        "code": str(code or existing.get("code") or "").strip().upper(),
        "last_checked_ts": now_ts,
    }
    prices_map[record_key] = updated
    save_prices_map(prices_map)
    return updated


def _normalize_catalog_image_src(image_path: str, image_url: str):
    path_text = str(image_path or "").strip()
    if path_text:
        normalized = path_text.replace("\\", "/").lstrip("/")
        if normalized:
            abs_candidate = normalized if os.path.isabs(path_text) else os.path.join(IMAGES_ROOT, normalized)
            if os.path.isfile(abs_candidate):
                return f"/img/{normalized}"
    url_text = str(image_url or "").strip()
    if url_text:
        return url_text
    return None


def open_catalog_db():
    conn = None
    try:
        conn = sqlite3.connect(f"file:{CATALOG_DB_PATH}?mode=ro", uri=True)
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.OperationalError:
        try:
            conn.close()
        except Exception:
            pass
        if not os.path.isfile(CATALOG_DB_PATH):
            raise
        cache_dir = os.path.dirname(CATALOG_DB_CACHE_PATH)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        try:
            needs_copy = (
                not os.path.isfile(CATALOG_DB_CACHE_PATH)
                or os.path.getmtime(CATALOG_DB_CACHE_PATH) < os.path.getmtime(CATALOG_DB_PATH)
                or os.path.getsize(CATALOG_DB_CACHE_PATH) != os.path.getsize(CATALOG_DB_PATH)
            )
        except Exception:
            needs_copy = True
        if needs_copy:
            shutil.copy2(CATALOG_DB_PATH, CATALOG_DB_CACHE_PATH)
        return sqlite3.connect(CATALOG_DB_CACHE_PATH)


def load_catalog_card_index():
    signature = path_signature(CATALOG_DB_PATH)
    return get_ttl_cached_value("catalog_card_index", 30.0, _load_catalog_card_index_uncached, signature=signature)


def _load_catalog_card_index_uncached():
    try:
        conn = open_catalog_db()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    c.canonical_code,
                    c.card_name,
                    c.set_name,
                    c.rarity,
                    c.color,
                    c.card_type,
                    c.cost,
                    c.power,
                    c.counter,
                    c.attribute,
                    c.effect_text,
                    c.trigger_text,
                    (
                        SELECT cv.image_path
                        FROM card_variants cv
                        WHERE cv.card_id = c.id
                          AND trim(coalesce(cv.image_path, '')) != ''
                        ORDER BY cv.is_base DESC, cv.id ASC
                        LIMIT 1
                    ) AS image_path,
                    (
                        SELECT cv.image_url
                        FROM card_variants cv
                        WHERE cv.card_id = c.id
                          AND trim(coalesce(cv.image_url, '')) != ''
                        ORDER BY cv.is_base DESC, cv.id ASC
                        LIMIT 1
                    ) AS image_url
                FROM cards c
                ORDER BY c.canonical_code ASC
                """
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    index = {}
    for row in rows:
        code = str(row["canonical_code"] or "").strip().upper()
        if not code:
            continue
        index[code] = {
            "card_name": clean_display_name(str(row["card_name"] or ""), code),
            "set_name": str(row["set_name"] or "").strip(),
            "rarity": str(row["rarity"] or "").strip(),
            "color": str(row["color"] or "").strip(),
            "card_type": str(row["card_type"] or "").strip(),
            "cost": "" if row["cost"] is None else str(row["cost"]),
            "power": str(row["power"] or "").strip(),
            "counter": str(row["counter"] or "").strip(),
            "attribute": str(row["attribute"] or "").strip(),
            "effect_text": str(row["effect_text"] or "").strip(),
            "trigger_text": str(row["trigger_text"] or "").strip(),
            "catalog_image_src": _normalize_catalog_image_src(
                str(row["image_path"] or "").strip(),
                str(row["image_url"] or "").strip(),
            ),
        }
    return index


def _normalize_rel_image_path(path_text: str) -> str:
    return str(path_text or "").replace("\\", "/").strip().lstrip("/")


def _runtime_image_label(image_entry: dict) -> str:
    if not image_entry:
        return ""
    quality_tier = str(image_entry.get("quality_tier") or "").strip().lower()
    if quality_tier == "official_clean":
        return "Official"
    if quality_tier == "official_sample" or int(image_entry.get("sample_flag") or 0):
        return "Sample"
    if quality_tier == "trusted_scan":
        return "Trusted scan"
    trust_label = str(image_entry.get("source_trust_label") or "").strip()
    return trust_label.title() if trust_label else ""


def load_runtime_best_image_index():
    if not os.path.isfile(MIRU_RUNTIME_DOSSIER_DB_PATH):
        return {}
    if not os.path.isdir(MIRU_RUNTIME_IMAGES_ROOT):
        return {}

    try:
        conn = None
        try:
            conn = sqlite3.connect(f"file:{MIRU_RUNTIME_DOSSIER_DB_PATH}?mode=ro", uri=True)
            conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        except sqlite3.OperationalError:
            try:
                conn.close()
            except Exception:
                pass
            cache_dir = os.path.dirname(MIRU_RUNTIME_DOSSIER_DB_CACHE_PATH)
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
            try:
                needs_copy = (
                    not os.path.isfile(MIRU_RUNTIME_DOSSIER_DB_CACHE_PATH)
                    or os.path.getmtime(MIRU_RUNTIME_DOSSIER_DB_CACHE_PATH) < os.path.getmtime(MIRU_RUNTIME_DOSSIER_DB_PATH)
                    or os.path.getsize(MIRU_RUNTIME_DOSSIER_DB_CACHE_PATH) != os.path.getsize(MIRU_RUNTIME_DOSSIER_DB_PATH)
                )
            except Exception:
                needs_copy = True
            if needs_copy:
                shutil.copy2(MIRU_RUNTIME_DOSSIER_DB_PATH, MIRU_RUNTIME_DOSSIER_DB_CACHE_PATH)
            conn = sqlite3.connect(MIRU_RUNTIME_DOSSIER_DB_CACHE_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT
                    card_code,
                    local_path,
                    source_id,
                    source_trust_label,
                    quality_tier,
                    sample_flag,
                    replacement_eligible,
                    upgrade_status,
                    width,
                    height
                FROM learning_dossier_images
                WHERE is_current_best = 1
                  AND trim(coalesce(local_path, '')) != ''
                ORDER BY card_code ASC, width DESC, height DESC
                """
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}

    index = {}
    for row in rows:
        code = str(row["card_code"] or "").strip().upper()
        rel_path = _normalize_rel_image_path(str(row["local_path"] or ""))
        if not code or not rel_path:
            continue
        abs_path = os.path.join(MIRU_RUNTIME_IMAGES_ROOT, rel_path.replace("/", os.sep))
        if not os.path.isfile(abs_path):
            continue
        entry = {
            "rel_path": rel_path,
            "source_id": str(row["source_id"] or "").strip(),
            "source_trust_label": str(row["source_trust_label"] or "").strip(),
            "quality_tier": str(row["quality_tier"] or "").strip(),
            "sample_flag": int(row["sample_flag"] or 0),
            "replacement_eligible": int(row["replacement_eligible"] or 0),
            "upgrade_status": str(row["upgrade_status"] or "").strip(),
            "width": int(row["width"] or 0),
            "height": int(row["height"] or 0),
        }
        entry["label"] = _runtime_image_label(entry)
        index[code] = entry
    return index


def fmt_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:
        return ""


def wants_alt(name: str) -> bool:
    s = (name or "").lower()
    return any(m in s for m in ALT_MARKERS)


def wants_illustration(name: str) -> bool:
    s = (name or "").lower()
    return any(m in s for m in ILLUST_MARKERS)


def variant_is_altish(v: str) -> bool:
    v = (v or "").strip().lower()
    if not v:
        return False
    if v == "alt":
        return True
    return any(m in v for m in ALT_MARKERS)


def variant_is_illustrationish(v: str) -> bool:
    v = (v or "").strip().lower()
    if not v:
        return False
    return any(m in v for m in ILLUST_MARKERS)


def build_image_index():
    """
    by_base:
      "P-093(ILLUSTRATIONBOXVOL.6)" -> ".../P-093(IllustrationBoxVol.6).png"
      "OP11-067(ALT)" -> ".../OP11-067(Alt).png"
      "OP11-067" -> ".../OP11-067.png"

    by_code:
      "OP11-067" -> {"normal": "...", "alt": "...", "illust": "...", "variants": {...}}
    """
    by_base = {}
    by_code = {}

    if not os.path.isdir(IMAGES_ROOT):
        return {"by_base": by_base, "by_code": by_code}

    for root, _, files in os.walk(IMAGES_ROOT):
        for fn in files:
            low = fn.lower()
            if not low.endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue

            base, _ext = os.path.splitext(fn)
            if not base:
                continue

            abs_path = os.path.join(root, fn)
            rel_path = os.path.relpath(abs_path, IMAGES_ROOT).replace("\\", "/")

            by_base[base.upper()] = rel_path

            m = re.match(r"^([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})(\(([^)]+)\))?$", base, re.I)
            if not m:
                continue

            code = (m.group(1) or "").upper()
            variant = (m.group(3) or "").strip()
            variant_l = variant.lower()

            entry = by_code.setdefault(code, {"variants": {}})

            if variant == "":
                entry["normal"] = rel_path
            else:
                entry["variants"][variant_l] = rel_path
                if variant_is_altish(variant_l):
                    entry["alt"] = rel_path
                if variant_is_illustrationish(variant_l):
                    entry["illust"] = rel_path

    return {"by_base": by_base, "by_code": by_code}


IMAGE_INDEX = build_image_index()
RUNTIME_BEST_IMAGE_INDEX = load_runtime_best_image_index()


def format_compact_count(value):
    try:
        amount = int(value or 0)
    except Exception:
        return "0"
    if abs(amount) >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M"
    if abs(amount) >= 1_000:
        return f"{amount / 1_000:.1f}K"
    return str(amount)


def build_thumbnail_cache_path(filename: str, width: int, namespace: str = "local"):
    normalized = str(filename or "").replace("\\", "/").lstrip("/")
    stem, _ext = os.path.splitext(normalized)
    return os.path.join(THUMB_CACHE_ROOT, namespace, f"w{int(width)}", stem + ".webp")


def ensure_thumbnail_from_root(filename: str, root_dir: str, width: int = THUMB_DEFAULT_WIDTH, namespace: str = "local"):
    normalized = str(filename or "").replace("\\", "/").lstrip("/")
    if not normalized or not Image or not root_dir:
        return ""
    source_path = os.path.join(root_dir, normalized.replace("/", os.sep))
    if not os.path.isfile(source_path):
        return ""

    thumb_width = max(180, min(int(width or THUMB_DEFAULT_WIDTH), 480))
    cache_path = build_thumbnail_cache_path(normalized, thumb_width, namespace=namespace)
    try:
        if os.path.isfile(cache_path) and os.path.getmtime(cache_path) >= os.path.getmtime(source_path):
            return cache_path
    except Exception:
        pass

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    try:
        with Image.open(source_path) as img:
            img = ImageOps.exif_transpose(img) if ImageOps else img
            if "A" in img.getbands():
                background = Image.new("RGB", img.size, (10, 12, 20))
                background.paste(img, mask=img.getchannel("A"))
                img = background
            else:
                img = img.convert("RGB")
            max_height = int(round(thumb_width * 1.42))
            resampling = getattr(Image, "Resampling", Image)
            img.thumbnail((thumb_width, max_height), resampling.LANCZOS)
            if ImageFilter is not None:
                img = img.filter(ImageFilter.UnsharpMask(radius=0.7, percent=120, threshold=2))
            img.save(cache_path, "WEBP", quality=90, method=6)
        return cache_path
    except Exception:
        return ""


def ensure_thumbnail(filename: str, width: int = THUMB_DEFAULT_WIDTH):
    return ensure_thumbnail_from_root(filename, IMAGES_ROOT, width=width, namespace="local")


def apply_cache_headers(response, *, max_age: int, stale_while_revalidate: int = 0):
    response.headers["Cache-Control"] = (
        f"public, max-age={int(max_age)}, stale-while-revalidate={int(max(stale_while_revalidate, 0))}"
    )
    response.headers["Vary"] = "Accept-Encoding"
    return response


def send_cached_directory_file(root_dir: str, filename: str, *, max_age: int, stale_while_revalidate: int = 0):
    response = send_from_directory(root_dir, filename, conditional=True)
    return apply_cache_headers(
        response,
        max_age=max_age,
        stale_while_revalidate=stale_while_revalidate,
    )


@app.get("/img/<path:filename>")
def img(filename):
    return send_cached_directory_file(
        IMAGES_ROOT,
        filename,
        max_age=86400,
        stale_while_revalidate=604800,
    )


@app.get("/thumb/<path:filename>")
def thumb(filename):
    width = max(180, min(int(request.args.get("w", THUMB_DEFAULT_WIDTH) or THUMB_DEFAULT_WIDTH), 480))
    cache_path = ensure_thumbnail(filename, width)
    if cache_path and os.path.isfile(cache_path):
        return send_cached_directory_file(
            os.path.dirname(cache_path),
            os.path.basename(cache_path),
            max_age=86400,
            stale_while_revalidate=604800,
        )
    return send_cached_directory_file(
        IMAGES_ROOT,
        filename,
        max_age=86400,
        stale_while_revalidate=604800,
    )


@app.get("/miru-img/<path:filename>")
def miru_img(filename):
    normalized = _normalize_rel_image_path(filename)
    return send_cached_directory_file(
        MIRU_RUNTIME_IMAGES_ROOT,
        normalized,
        max_age=86400,
        stale_while_revalidate=604800,
    )


@app.get("/miru-thumb/<path:filename>")
def miru_thumb(filename):
    normalized = _normalize_rel_image_path(filename)
    width = max(180, min(int(request.args.get("w", THUMB_DEFAULT_WIDTH) or THUMB_DEFAULT_WIDTH), 480))
    cache_path = ensure_thumbnail_from_root(
        normalized,
        MIRU_RUNTIME_IMAGES_ROOT,
        width=width,
        namespace="miru-runtime",
    )
    if cache_path and os.path.isfile(cache_path):
        return send_cached_directory_file(
            os.path.dirname(cache_path),
            os.path.basename(cache_path),
            max_age=86400,
            stale_while_revalidate=604800,
        )
    return send_cached_directory_file(
        MIRU_RUNTIME_IMAGES_ROOT,
        normalized,
        max_age=86400,
        stale_while_revalidate=604800,
    )


def choose_image_path(name: str, code: str):
    """
    Selection order:

    1) If name contains any CODE(Variant) and exact base exists -> use it
    2) If illustration requested -> prefer illust
    3) If alt requested -> prefer alt
    4) Default normal then alt
    """
    idx_base = IMAGE_INDEX["by_base"]
    idx_code = IMAGE_INDEX["by_code"]

    # 1) Exact CODE(Variant) anywhere in name
    candidates = []
    for m in ANY_CODE_VARIANT_RE.finditer(name or ""):
        c = (m.group(1) or "").upper()
        v = (m.group(2) or "").strip()
        full = f"{c}({v})".upper()
        if full in idx_base:
            candidates.append((c, v, full))

    if candidates:
        if wants_illustration(name):
            for _c, v, full in candidates:
                if variant_is_illustrationish(v):
                    return idx_base[full]
        if wants_alt(name):
            for _c, v, full in candidates:
                if variant_is_altish(v):
                    return idx_base[full]
        return idx_base[candidates[0][2]]

    if not code:
        return None

    entry = idx_code.get(code, {}) or {}
    variants = entry.get("variants", {}) or {}

    if wants_illustration(name):
        if entry.get("illust"):
            return entry["illust"]
        for vname_l, path in variants.items():
            if variant_is_illustrationish(vname_l):
                return path
        return entry.get("normal") or entry.get("alt")

    if wants_alt(name):
        if entry.get("alt"):
            return entry["alt"]
        for vname_l, path in variants.items():
            if variant_is_altish(vname_l):
                return path
        return entry.get("alt") or entry.get("normal")

    return entry.get("normal") or entry.get("alt")


def choose_runtime_best_image(code: str):
    return dict((RUNTIME_BEST_IMAGE_INDEX.get((code or "").strip().upper()) or {}))


def resolve_card_image_sources(name: str, code: str, catalog_entry: dict, width: int = THUMB_DEFAULT_WIDTH):
    runtime_entry = choose_runtime_best_image(code)
    if runtime_entry:
        rel_path = str(runtime_entry.get("rel_path") or "").strip()
        if rel_path:
            return {
                "thumb_src": f"/miru-thumb/{rel_path}?w={int(width)}",
                "detail_src": f"/miru-img/{rel_path}",
                "detail_label": str(runtime_entry.get("label") or "").strip(),
                "source_kind": "miru-runtime-best",
            }

    img_path = choose_image_path(name, code)
    if img_path:
        return {
            "thumb_src": f"/thumb/{img_path}?w={int(width)}",
            "detail_src": f"/img/{img_path}",
            "detail_label": "",
            "source_kind": "local-optcg",
        }

    catalog_src = str(catalog_entry.get("catalog_image_src") or "").strip()
    if catalog_src.startswith("/img/"):
        return {
            "thumb_src": f"/thumb/{catalog_src[5:]}?w={int(width)}",
            "detail_src": catalog_src,
            "detail_label": "",
            "source_kind": "catalog-local",
        }

    return {
        "thumb_src": catalog_src,
        "detail_src": catalog_src,
        "detail_label": "",
        "source_kind": "catalog-remote" if catalog_src else "",
    }


def choose_detail_image_src(name: str, code: str, catalog_entry: dict):
    return str(resolve_card_image_sources(name, code, catalog_entry).get("detail_src") or "")


def choose_thumbnail_src(name: str, code: str, catalog_entry: dict, width: int = THUMB_DEFAULT_WIDTH):
    return str(resolve_card_image_sources(name, code, catalog_entry, width=width).get("thumb_src") or "")


def clean_display_name(name: str, code: str) -> str:
    """
    Removes duplicate leading codes like:
      "P-088 P-088 Trafalgar Law" -> "Trafalgar Law"
      "EB03-062 EB03-062 ..." -> "..."
    """
    s = (name or "").strip()
    if not s:
        return s

    if code:
        re_lead = re.compile(rf"^\s*(?:{re.escape(code)}(?:\([^)]+\))?\s+)+", re.I)
        s2 = re_lead.sub("", s).strip()
        if s2:
            s = s2

    s = re.sub(r"^\s*([A-Z]{1,4}\d{2}-\d{3}|P-\d{3})(?:\([^)]+\))?\s+", "", s, flags=re.I).strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def parse_library_code_parts(code: str):
    value = str(code or "").strip().upper()
    match = re.match(r"^([A-Z]{1,4})(\d{2})-(\d{3})([A-Z]?)$", value)
    if match:
        prefix = match.group(1)
        set_number = int(match.group(2))
        card_number = int(match.group(3))
        suffix = match.group(4) or ""
        return {
            "prefix": prefix,
            "prefix_rank": SET_PREFIX_ORDER.get(prefix, 99),
            "set_number": set_number,
            "card_number": card_number,
            "suffix": suffix,
            "set_code": f"{prefix}{match.group(2)}",
        }
    promo_match = re.match(r"^(P)-(\d{3})([A-Z]?)$", value)
    if promo_match:
        return {
            "prefix": "P",
            "prefix_rank": SET_PREFIX_ORDER.get("P", 99),
            "set_number": 0,
            "card_number": int(promo_match.group(2)),
            "suffix": promo_match.group(3) or "",
            "set_code": "P",
        }
    return {
        "prefix": "",
        "prefix_rank": 999,
        "set_number": 999,
        "card_number": 9999,
        "suffix": "",
        "set_code": "",
    }


def build_library_price_index(items):
    price_index = {}
    for item in items or []:
        name = str(item.get("name") or "").strip()
        code = str(item.get("code") or "").strip().upper()
        if not code:
            match = CODE_RE.search(name)
            code = match.group(1).upper() if match else ""
        if not code:
            continue
        try:
            price_value = float(item.get("price", 0) or 0)
        except Exception:
            continue
        if price_value <= 0:
            continue
        target_value = normalize_watch_price_input(item.get("target"))
        price_index[code] = {
            "price_value": price_value,
            "price_text": f"${price_value:.2f}",
            "target_text": f"${target_value:.2f}" if target_value is not None else "",
            "market_url": str(item.get("url") or "").strip(),
            "watch_name": name,
        }
    return price_index


def normalize_library_query(args):
    filters = {
        "q": str(args.get("q", "") or "").strip(),
        "set": str(args.get("set", "") or "").strip(),
        "color": str(args.get("color", "") or "").strip(),
        "rarity": str(args.get("rarity", "") or "").strip(),
        "card_type": str(args.get("card_type", "") or "").strip(),
        "cost": str(args.get("cost", "") or "").strip(),
        "attribute": str(args.get("attribute", "") or "").strip(),
        "sort": str(args.get("sort", "set_card_asc") or "set_card_asc").strip(),
    }
    if filters["sort"] not in {key for key, _label in SORT_OPTIONS}:
        filters["sort"] = "set_card_asc"
    return filters


def build_library_filter_options(library_cards):
    def unique_values(key):
        seen = []
        values = set()
        for entry in library_cards:
            value = str(entry.get(key) or "").strip()
            if not value or value in values:
                continue
            values.add(value)
            seen.append(value)
        return sorted(seen, key=lambda item: item.lower())

    set_options = []
    seen_sets = set()
    for entry in library_cards:
        set_code = str(entry.get("set_code") or "").strip()
        set_name = str(entry.get("set_name") or "").strip()
        if not set_code and not set_name:
            continue
        key = (set_code, set_name)
        if key in seen_sets:
            continue
        seen_sets.add(key)
        label = f"{set_code} · {set_name}" if set_code and set_name else (set_name or set_code)
        set_options.append({"value": set_code or set_name, "label": label})
    set_options.sort(key=lambda item: item["label"].lower())

    return {
        "set": set_options,
        "color": unique_values("color"),
        "rarity": unique_values("rarity"),
        "card_type": unique_values("card_type"),
        "cost": sorted(unique_values("cost"), key=lambda value: (int(value) if str(value).isdigit() else 999, value)),
        "attribute": unique_values("attribute"),
        "sort": [{"value": key, "label": label} for key, label in SORT_OPTIONS],
    }


def filter_and_sort_library_cards(library_cards, filters):
    query = str(filters.get("q") or "").strip().lower()
    set_filter = str(filters.get("set") or "").strip().lower()
    color_filter = str(filters.get("color") or "").strip().lower()
    rarity_filter = str(filters.get("rarity") or "").strip().lower()
    type_filter = str(filters.get("card_type") or "").strip().lower()
    cost_filter = str(filters.get("cost") or "").strip().lower()
    attribute_filter = str(filters.get("attribute") or "").strip().lower()
    sort_key_name = str(filters.get("sort") or "set_card_asc").strip()

    filtered = []
    for entry in library_cards:
        search_blob = str(entry.get("search_blob") or "").lower()
        if query and query not in search_blob:
            continue
        if set_filter:
            entry_set_values = {
                str(entry.get("set_code") or "").strip().lower(),
                str(entry.get("set_name") or "").strip().lower(),
            }
            if set_filter not in entry_set_values:
                continue
        if color_filter and color_filter != str(entry.get("color") or "").strip().lower():
            continue
        if rarity_filter and rarity_filter != str(entry.get("rarity") or "").strip().lower():
            continue
        if type_filter and type_filter != str(entry.get("card_type") or "").strip().lower():
            continue
        if cost_filter and cost_filter != str(entry.get("cost") or "").strip().lower():
            continue
        if attribute_filter and attribute_filter != str(entry.get("attribute") or "").strip().lower():
            continue
        filtered.append(entry)

    def code_key(entry):
        return (
            int(entry.get("prefix_rank", 999)),
            int(entry.get("set_number", 999)),
            int(entry.get("card_number", 9999)),
            str(entry.get("suffix") or ""),
            str(entry.get("code") or ""),
        )

    if sort_key_name == "code_desc":
        filtered.sort(key=code_key, reverse=True)
    elif sort_key_name == "name_asc":
        filtered.sort(key=lambda entry: ((entry.get("title_name") or "").lower(), code_key(entry)))
    elif sort_key_name == "newest_set":
        filtered.sort(
            key=lambda entry: (
                -int(entry.get("set_number", 999)),
                -int(entry.get("card_number", 9999)),
                int(entry.get("prefix_rank", 999)),
                str(entry.get("code") or ""),
            )
        )
    elif sort_key_name == "code_asc":
        filtered.sort(key=code_key)
    else:
        filtered.sort(
            key=lambda entry: (
                int(entry.get("prefix_rank", 999)),
                int(entry.get("set_number", 999)),
                int(entry.get("card_number", 9999)),
                (entry.get("title_name") or "").lower(),
            )
        )
    return filtered


def calc_deal(price: float, target: float):
    """
    Returns: hit(bool), pct(float), label(str), tier(str)
    pct positive when under target: (target - price)/target * 100
    """
    if target is None or price is None:
        return False, None, "", "watch"

    if target <= 0:
        return False, None, "", "watch"

    diff = target - price
    pct = (diff / target) * 100.0

    if price <= target:
        if pct >= 15:
            tier = "deal3"
        elif pct >= 5:
            tier = "deal2"
        else:
            tier = "deal1"
        return True, pct, f"▼ {pct:.1f}% under target", tier
    else:
        tier = "over"
        return False, pct, f"▲ {abs(pct):.1f}% above target", tier


@app.get("/catalog")
def catalog():
    """Alias: catalog is the library view served at /. Do not replace with .codex_catalog_snapshot.html or old OP Miru Catalog."""
    return redirect("/library", code=302)


@app.post("/api/watchlist/add")
def api_watchlist_add():
    payload = request.get_json(silent=True) or {}
    code = str(payload.get("code") or "").strip().upper()
    if not code:
        return Response(
            json.dumps({"ok": False, "error": "Card code is required."}),
            status=400,
            mimetype="application/json",
        )

    target_price_input = payload.get("target_price")
    target_price = normalize_watch_price_input(target_price_input)
    if target_price_input not in ("", None) and target_price is None:
        return Response(
            json.dumps({"ok": False, "error": "Enter a valid target price."}),
            status=400,
            mimetype="application/json",
        )

    current_price = parse_price_text(payload.get("current_price"))
    market_url = str(payload.get("market_url") or "").strip()
    name = str(payload.get("name") or code).strip()
    entry = upsert_watchlist_entry(
        code=code,
        name=name,
        target_price=target_price,
        current_price=current_price,
        market_url=market_url,
    )
    response_payload = {
        "ok": True,
        "entry": {
            "code": str(entry.get("code") or "").strip(),
            "name": str(entry.get("name") or "").strip(),
            "current_price": f"${float(entry.get('price')):.2f}" if entry.get("price") not in ("", None) and float(entry.get("price")) > 0 else "",
            "target_price": f"${float(entry.get('target', 0) or 0):.2f}" if entry.get("target") not in ("", None) else "",
            "market_url": str(entry.get("url") or "").strip(),
        },
        "message": "Saved to watchlist.",
    }
    return Response(json.dumps(response_payload), mimetype="application/json")


def build_library_card_index(catalog_cards):
    signature = (path_signature(CATALOG_DB_PATH), len(RUNTIME_BEST_IMAGE_INDEX))

    def builder():
        library_cards = []
        for code, catalog_entry in catalog_cards.items():
            title_name = clean_display_name(str(catalog_entry.get("card_name") or ""), code) or code
            set_name = str(catalog_entry.get("set_name") or "").strip()
            code_parts = parse_library_code_parts(code)
            image_sources = resolve_card_image_sources(title_name, code, catalog_entry)
            thumb_src = str(image_sources.get("thumb_src") or "")
            library_cards.append(
                {
                    "code": code,
                    "title_name": title_name or code,
                    "subtitle": set_name,
                    "set_name": set_name,
                    "set_code": code_parts["set_code"],
                    "rarity": str(catalog_entry.get("rarity") or "").strip(),
                    "color": str(catalog_entry.get("color") or "").strip(),
                    "card_type": str(catalog_entry.get("card_type") or "").strip(),
                    "cost": str(catalog_entry.get("cost") or "").strip(),
                    "attribute": str(catalog_entry.get("attribute") or "").strip(),
                    "thumb_src": thumb_src,
                    "has_runtime_thumb": thumb_src.startswith("/miru-thumb/"),
                    "has_local_thumb": thumb_src.startswith("/thumb/"),
                    "detail_src": str(image_sources.get("detail_src") or ""),
                    "detail_label": str(image_sources.get("detail_label") or "").strip(),
                    "runtime_only": False,
                    "prefix_rank": code_parts["prefix_rank"],
                    "set_number": code_parts["set_number"],
                    "card_number": code_parts["card_number"],
                    "suffix": code_parts["suffix"],
                    "search_blob": " ".join(
                        part for part in [
                            code,
                            title_name,
                            set_name,
                            code_parts["set_code"],
                            str(catalog_entry.get("rarity") or "").strip(),
                            str(catalog_entry.get("color") or "").strip(),
                            str(catalog_entry.get("card_type") or "").strip(),
                            str(catalog_entry.get("attribute") or "").strip(),
                        ] if part
                    ),
                }
            )

        for code, runtime_entry in sorted(RUNTIME_BEST_IMAGE_INDEX.items()):
            if code in catalog_cards:
                continue
            code_parts = parse_library_code_parts(code)
            image_sources = resolve_card_image_sources(code, code, {})
            thumb_src = str(image_sources.get("thumb_src") or "")
            detail_label = str(image_sources.get("detail_label") or "").strip()
            library_cards.append(
                {
                    "code": code,
                    "title_name": code,
                    "subtitle": detail_label or "Miru runtime best image",
                    "set_name": "",
                    "set_code": code_parts["set_code"],
                    "rarity": "",
                    "color": "",
                    "card_type": "",
                    "cost": "",
                    "attribute": "",
                    "thumb_src": thumb_src,
                    "has_runtime_thumb": thumb_src.startswith("/miru-thumb/"),
                    "has_local_thumb": thumb_src.startswith("/thumb/"),
                    "detail_src": str(image_sources.get("detail_src") or ""),
                    "detail_label": detail_label,
                    "runtime_only": True,
                    "prefix_rank": code_parts["prefix_rank"],
                    "set_number": code_parts["set_number"],
                    "card_number": code_parts["card_number"],
                    "suffix": code_parts["suffix"],
                    "search_blob": " ".join(part for part in [code, detail_label, code_parts["set_code"]] if part),
                }
            )

        library_cards.sort(
            key=lambda entry: (
                int(entry.get("prefix_rank", 999)),
                int(entry.get("set_number", 999)),
                int(entry.get("card_number", 9999)),
                (entry.get("title_name") or "").lower(),
            )
        )
        return library_cards

    return get_ttl_cached_value("library_card_index", 30.0, builder, signature=signature)


def build_library_detail_payload_attr(entry, catalog_entry):
    watch_data = dict(entry.get("watch_data") or {})
    detail_payload = {
        "code": entry["code"],
        "name": entry["title_name"] or entry["code"],
        "set_name": entry["subtitle"] if not entry.get("runtime_only") else "Miru runtime image",
        "rarity": str(catalog_entry.get("rarity") or "").strip(),
        "color": str(catalog_entry.get("color") or "").strip(),
        "card_type": str(catalog_entry.get("card_type") or "").strip(),
        "cost": str(catalog_entry.get("cost") or "").strip(),
        "power": str(catalog_entry.get("power") or "").strip(),
        "counter": str(catalog_entry.get("counter") or "").strip(),
        "attribute": str(catalog_entry.get("attribute") or "").strip(),
        "effect_text": str(catalog_entry.get("effect_text") or "").strip(),
        "trigger_text": str(catalog_entry.get("trigger_text") or "").strip(),
        "image_thumb_src": str(entry.get("thumb_src") or ""),
        "image_src": str(entry.get("detail_src") or ""),
        "image_source_label": str(entry.get("detail_label") or "").strip(),
        "market_url": str(watch_data.get("market_url") or ""),
        "current_price": str(entry.get("price_text") or ""),
        "target_price": str(watch_data.get("target_text") or ""),
    }
    return html_lib.escape(json.dumps(detail_payload, ensure_ascii=False), quote=True)


def build_library_fragment_html(
    catalog_cards,
    library_cards,
    library_page,
    *,
    base_path="/library",
    browse_mode=False,
    price_index=None,
    query_suffix="",
):
    library_total = len(library_cards)
    library_total_pages = max(1, (library_total + LIBRARY_PAGE_SIZE - 1) // LIBRARY_PAGE_SIZE)
    library_page = min(max(int(library_page or 1), 1), library_total_pages)
    library_start = (library_page - 1) * LIBRARY_PAGE_SIZE
    library_page_items = library_cards[library_start:library_start + LIBRARY_PAGE_SIZE]
    grid_class = "libraryGrid libraryGrid--browse" if browse_mode else "libraryGrid"
    card_class = "libraryCard libraryCard--browse" if browse_mode else "libraryCard"
    thumb_class = "libraryThumb libraryThumb--browse"
    body_class = "libraryCardBody libraryCardBody--browse" if browse_mode else "libraryCardBody"
    title_class = "libraryCardTitle libraryCardTitle--browse" if browse_mode else "libraryCardTitle"
    subtitle_class = "libraryCardSubtitle libraryCardSubtitle--browse" if browse_mode else "libraryCardSubtitle"
    button_class = "libraryOpenBtn libraryOpenBtn--browse" if browse_mode else "libraryOpenBtn"
    button_label = "Open" if browse_mode else "Open card sheet"

    library_html = []
    for entry in library_page_items:
        price_entry = (price_index or {}).get(entry["code"], {})
        entry_with_watch = {**entry, "watch_data": price_entry}
        detail_payload_attr = build_library_detail_payload_attr(entry_with_watch, catalog_cards.get(entry["code"], {}))
        media_html = (
            f'<img class="{thumb_class}" src="{entry["thumb_src"]}" loading="lazy" decoding="async" fetchpriority="low" alt="">'
            if entry["thumb_src"]
            else f'<div class="{thumb_class} libraryThumb--empty">No image</div>'
        )
        rarity_html = (
            f'<span class="libraryRarity">{html_lib.escape(entry["rarity"])}</span>'
            if entry["rarity"]
            else ""
        )
        price_html = (
            f'<div class="libraryPrice">{html_lib.escape(price_entry.get("price_text") or "")}</div>'
            if price_entry.get("price_text")
            else ""
        )
        subtitle_parts = [part for part in [str(entry.get("set_code") or "").strip(), str(entry.get("subtitle") or "").strip()] if part]
        subtitle_html = html_lib.escape(" • ".join(subtitle_parts)) if subtitle_parts else "Miru library image"
        library_html.append(
            f"""
            <article class="{card_class}" data-card="{detail_payload_attr}">
              <button class="libraryThumbButton" type="button" data-card="{detail_payload_attr}">
                {media_html}
              </button>
              <div class="{body_class}">
                <div class="libraryCodeRow">
                  <span class="libraryCode">{entry['code']}</span>
                  {rarity_html}
                </div>
                <h3 class="{title_class}">{html_lib.escape(entry['title_name'])}</h3>
                <div class="{subtitle_class}">{subtitle_html}</div>
                {price_html}
                <button class="{button_class}" type="button" data-card="{detail_payload_attr}">{button_label}</button>
              </div>
            </article>
            """
        )

    prev_page_href = f"{base_path}?library_page={library_page - 1}{query_suffix}" if library_page > 1 else ""
    next_page_href = f"{base_path}?library_page={library_page + 1}{query_suffix}" if library_page < library_total_pages else ""
    return f"""
      <div class="{grid_class}">
        {''.join(library_html) if library_html else '<div class="card">No library cards available yet.</div>'}
      </div>
      <div class="libraryPager">
        <div class="meta">Showing {library_start + 1 if library_total else 0}-{min(library_start + LIBRARY_PAGE_SIZE, library_total)} of {library_total} catalog cards.</div>
        <div class="libraryPagerLinks">
          {f'<a class="libraryPagerLink" href="{prev_page_href}">Previous</a>' if prev_page_href else '<span class="libraryPagerLink isDisabled">Previous</span>'}
          {f'<a class="libraryPagerLink" href="{next_page_href}">Next</a>' if next_page_href else '<span class="libraryPagerLink isDisabled">Next</span>'}
        </div>
      </div>
    """


def build_watchlist_entries(items, catalog_cards):
    enriched = []
    for it in items or []:
        name = (it.get("name", "") or "").strip()
        code = (it.get("code") or "").upper()

        if not code:
            match = CODE_RE.search(name)
            code = match.group(1).upper() if match else ""

        display_name = clean_display_name(name, code)
        catalog_entry = catalog_cards.get(code, {})
        title_name = clean_display_name(str(catalog_entry.get("card_name") or display_name), code)
        subtitle = str(catalog_entry.get("set_name") or "").strip()

        price_f = None
        target_f = None
        price_txt = ""
        target_txt = ""

        try:
            price_f = float(it.get("price", 0))
            if price_f > 0:
                price_txt = f"${price_f:.2f}"
        except Exception:
            pass

        try:
            target_f = float(it.get("target", 0))
            if target_f > 0:
                target_txt = f"${target_f:.2f}"
        except Exception:
            pass

        hit, pct, pct_label, tier = calc_deal(price_f, target_f)

        enriched.append(
            {
                "it": it,
                "code": code,
                "name": name,
                "display_name": display_name,
                "title_name": title_name,
                "subtitle": subtitle,
                "price_txt": price_txt,
                "target_txt": target_txt,
                "hit": hit,
                "pct": pct if pct is not None else -9999.0,
                "pct_label": pct_label,
                "tier": tier,
            }
        )

    def sort_key(entry):
        item = entry["it"]
        last_ts = int(item.get("last_checked_ts", 0) or 0)
        hit_bucket = 0 if entry["hit"] else 1
        deal_rank = -entry["pct"] if entry["hit"] else 0
        recency = -last_ts
        name_key = (entry["display_name"] or "").lower()
        return (hit_bucket, deal_rank, recency, name_key)

    enriched.sort(key=sort_key)
    return enriched


def build_watchlist_cards_html(entries, catalog_cards):
    cards_html = []
    for entry in entries or []:
        item = entry["it"]
        code = entry["code"]
        title_name = entry["title_name"]
        subtitle = entry["subtitle"]
        catalog_entry = catalog_cards.get(code, {})
        image_sources = resolve_card_image_sources(entry["name"], code, catalog_entry)
        detail_payload = {
            "code": code,
            "name": title_name or entry["display_name"] or code,
            "set_name": subtitle,
            "rarity": str(catalog_entry.get("rarity") or "").strip(),
            "color": str(catalog_entry.get("color") or "").strip(),
            "card_type": str(catalog_entry.get("card_type") or "").strip(),
            "cost": str(catalog_entry.get("cost") or "").strip(),
            "power": str(catalog_entry.get("power") or "").strip(),
            "counter": str(catalog_entry.get("counter") or "").strip(),
            "attribute": str(catalog_entry.get("attribute") or "").strip(),
            "effect_text": str(catalog_entry.get("effect_text") or "").strip(),
            "trigger_text": str(catalog_entry.get("trigger_text") or "").strip(),
            "image_thumb_src": str(image_sources.get("thumb_src") or ""),
            "image_src": str(image_sources.get("detail_src") or ""),
            "image_source_label": str(image_sources.get("detail_label") or "").strip(),
            "market_url": str(item.get("url", "") or "").strip(),
            "current_price": entry["price_txt"],
            "target_price": entry["target_txt"],
        }
        detail_payload_attr = html_lib.escape(json.dumps(detail_payload, ensure_ascii=False), quote=True)
        secondary_line_parts = [part for part in [subtitle, code] if part]
        secondary_line = " • ".join(secondary_line_parts)
        rail_detail = entry["pct_label"] or (f'Target {entry["target_txt"]}' if entry["target_txt"] else "Watching")
        thumb_src = str(image_sources.get("thumb_src") or "")
        img_tag = (
            f'<img class="thumb" src="{thumb_src}" loading="lazy" decoding="async" fetchpriority="low" alt="">'
            if thumb_src
            else '<div class="thumb ph">No image</div>'
        )
        card_class = f'card {entry["tier"]}' if entry["tier"] else "card"
        price_class = "val current buy" if entry["hit"] else "val current"
        market_url = str(item.get("url", "") or "").strip()
        buy_action_html = (
            f'<a class="buybtn" href="{market_url}" target="_blank" rel="noopener">TCGplayer</a>'
            if market_url
            else '<span class="buybtn isDisabled">Watch only</span>'
        )
        cards_html.append(
            f"""
            <div class="{card_class}" data-card="{detail_payload_attr}">
              <div class="row" title="Last checked {fmt_time(item.get("last_checked_ts", 0))}">
                <div class="left">
                  {img_tag}
                </div>

                <div class="center">
                  <div class="title">{title_name}</div>
                  <div class="subtitle">{secondary_line}</div>
                </div>

                <div class="rail">
                  <div class="{price_class}">{entry["price_txt"]}</div>
                  <div class="railDetail">{rail_detail}</div>
                </div>
              </div>

              <div class="actions">
                <button class="viewbtn" type="button" data-card="{detail_payload_attr}">View</button>
                {buy_action_html}
              </div>
            </div>
            """
        )
    return "".join(cards_html) if cards_html else '<div class="card">No data yet.</div>'


def warm_start_caches():
    try:
        load_prices()
        catalog_cards = load_catalog_card_index()
        build_library_card_index(catalog_cards)
    except Exception:
        pass


warm_start_caches()


@app.get("/library")
@app.get("/")
def index():
    # Library page: View Card + detail modal (cardDetailModal), titleBlock, full-size image and effect/trigger text.
    # Do NOT restore from .codex_catalog_snapshot.html or the old "OP Miru Catalog" state.
    items = load_prices()
    catalog_cards = load_catalog_card_index()
    is_library_page = request.path.rstrip("/") == "/library"
    try:
        library_page = max(int(request.args.get("library_page", "1") or 1), 1)
    except Exception:
        library_page = 1

    library_cards = build_library_card_index(catalog_cards)
    enriched = build_watchlist_entries(items, catalog_cards)
    initial_watchlist_cards = enriched[:HOMEPAGE_INITIAL_WATCHLIST_COUNT]
    remaining_watchlist_count = max(len(enriched) - len(initial_watchlist_cards), 0)
    cards_html = build_watchlist_cards_html(initial_watchlist_cards, catalog_cards) if not is_library_page else ""

    library_total = len(library_cards)
    library_image_count = sum(1 for entry in library_cards if entry["has_runtime_thumb"] or entry["has_local_thumb"])
    set_total = len(
        {
            (str(entry.get("set_code") or "").strip(), str(entry.get("set_name") or "").strip())
            for entry in library_cards
            if str(entry.get("set_code") or "").strip() or str(entry.get("set_name") or "").strip()
        }
    )

    library_filters = None
    library_fragment_url = ""
    price_index = {}
    query_suffix = ""
    library_filter_options = {}
    if is_library_page:
        price_index = build_library_price_index(items)
        library_filters = normalize_library_query(request.args)
        filtered_library_cards = filter_and_sort_library_cards(library_cards, library_filters)
        library_total = len(filtered_library_cards)
        library_total_pages = max(1, (library_total + LIBRARY_PAGE_SIZE - 1) // LIBRARY_PAGE_SIZE)
        library_page = min(library_page, library_total_pages)
        library_image_count = sum(1 for entry in filtered_library_cards if entry["has_runtime_thumb"] or entry["has_local_thumb"])
        library_filter_options = build_library_filter_options(library_cards)
        query_pairs = []
        for key in ("q", "set", "color", "rarity", "card_type", "cost", "attribute", "sort"):
            value = str(library_filters.get(key) or "").strip()
            if not value or (key == "sort" and value == "set_card_asc"):
                continue
            query_pairs.append((key, value))
        query_string = "&".join(f"{key}={quote(value)}" for key, value in query_pairs)
        query_suffix = f"&{query_string}" if query_string else ""
        library_fragment_url = f"/library-fragment?library_page={library_page}&mode=browse{query_suffix}"

    page_title = "Project Miru Library" if is_library_page else "Project Miru"
    brand_body = (
        "Search the OPTCG catalog by set, color, rarity, and role."
        if is_library_page
        else "Track target prices and crack open card sheets fast."
    )
    hero_eyebrow = "Browse Cards" if is_library_page else "Navigator dashboard"
    brand_hero_class = "brandHero brandHero--library" if is_library_page else "brandHero"
    hero_stats_html = (
        ""
        if is_library_page
        else f"""
          <div class="heroStats" aria-label="Project Miru summary">
            <div class="heroStat">
              <div class="heroStatLabel">Cards Indexed</div>
              <div class="heroStatValue">{format_compact_count(library_total)}</div>
              <div class="heroStatMeta">Local catalog ready for fast browsing.</div>
            </div>
            <div class="heroStat">
              <div class="heroStatLabel">Sets Covered</div>
              <div class="heroStatValue">{format_compact_count(set_total)}</div>
              <div class="heroStatMeta">OPTCG set coverage available in this dashboard.</div>
            </div>
            <div class="heroStat">
              <div class="heroStatLabel">Miru Link</div>
              <div class="heroStatValue">Ready</div>
              <div class="heroStatMeta">Local Miru handoff stays on port 18765.</div>
            </div>
          </div>
        """
    )
    ask_miru_href = (
        "http://127.0.0.1:18765/"
        f"?request_text={quote('Help me navigate the Project Miru watchlist and surface the most actionable cards.')}"
        f"&mode={quote('card lookup')}"
    )
    hero_nav_html = (
        '<a class="heroNavLink" href="/">Home</a>'
        '<a class="heroNavLink heroNavLink--accent" href="/library">Browse Cards</a>'
        '<a class="heroNavLink" href="/library?sort=newest_set">Sets</a>'
        f'<a class="heroNavLink heroNavLink--accentSoft" href="{ask_miru_href}" target="_blank" rel="noopener">Ask Miru</a>'
        if is_library_page
        else '<a class="heroNavLink" href="#watchlist">Watchlist</a>'
             '<a class="heroNavLink heroNavLink--accent" href="/library">Browse Cards</a>'
             '<a class="heroNavLink" href="/library?sort=newest_set">Sets</a>'
             f'<a class="heroNavLink heroNavLink--accentSoft" href="{ask_miru_href}" target="_blank" rel="noopener">Ask Miru</a>'
    )
    hero_utility_html = ""
    homepage_library_entry_html = f"""
        <section class="libraryGateway">
          <div class="libraryGatewayCopy">
            <div class="libraryEyebrow">Library access</div>
            <h2 class="libraryTitle libraryTitle--premium">Browse the dedicated card library</h2>
            <p class="libraryBody">Jump into the full OPTCG browser without crowding the watch rail.</p>
          </div>
          <div class="libraryMetaPills" aria-label="Library preview summary">
            <span class="libraryMetaPill">{format_compact_count(library_total)} catalog cards</span>
            <span class="libraryMetaPill">{format_compact_count(set_total)} sets covered</span>
            <span class="libraryMetaPill">{format_compact_count(library_image_count)} thumbnail-ready cards</span>
          </div>
          <div class="libraryGatewayActions">
            <a class="heroNavLink heroNavLink--accent heroNavLink--large" href="/library">Browse Cards</a>
            <a class="heroNavLink heroNavLink--large" href="/library?library_page=1">Open Library</a>
          </div>
        </section>
    """
    set_options_html = ""
    color_options_html = ""
    rarity_options_html = ""
    type_options_html = ""
    cost_options_html = ""
    attribute_options_html = ""
    sort_options_html = ""
    if is_library_page and library_filters is not None:
        set_options_html = "".join(
            f'<option value="{html_lib.escape(option["value"], quote=True)}"{" selected" if library_filters["set"] == option["value"] else ""}>{html_lib.escape(option["label"])}</option>'
            for option in library_filter_options["set"]
        )
        color_options_html = "".join(
            f'<option value="{html_lib.escape(option, quote=True)}"{" selected" if library_filters["color"] == option else ""}>{html_lib.escape(option)}</option>'
            for option in library_filter_options["color"]
        )
        rarity_options_html = "".join(
            f'<option value="{html_lib.escape(option, quote=True)}"{" selected" if library_filters["rarity"] == option else ""}>{html_lib.escape(option)}</option>'
            for option in library_filter_options["rarity"]
        )
        type_options_html = "".join(
            f'<option value="{html_lib.escape(option, quote=True)}"{" selected" if library_filters["card_type"] == option else ""}>{html_lib.escape(option)}</option>'
            for option in library_filter_options["card_type"]
        )
        cost_options_html = "".join(
            f'<option value="{html_lib.escape(option, quote=True)}"{" selected" if library_filters["cost"] == option else ""}>{html_lib.escape(option)}</option>'
            for option in library_filter_options["cost"]
        )
        attribute_options_html = "".join(
            f'<option value="{html_lib.escape(option, quote=True)}"{" selected" if library_filters["attribute"] == option else ""}>{html_lib.escape(option)}</option>'
            for option in library_filter_options["attribute"]
        )
        sort_options_html = "".join(
            f'<option value="{html_lib.escape(option["value"], quote=True)}"{" selected" if library_filters["sort"] == option["value"] else ""}>{html_lib.escape(option["label"])}</option>'
            for option in library_filter_options["sort"]
        )
    library_page_html = ""
    if is_library_page and library_filters is not None:
        library_page_html = f"""
            <section class="libraryIntro libraryIntro--libraryPage" id="card-library-top">
              <div class="libraryEyebrow">OPTCG browser</div>
              <h2 class="libraryTitle libraryTitle--premium">Browse the full card catalog.</h2>
              <p class="libraryBody">Filter by set, color, rarity, attribute, or role.</p>
            </section>

            <form class="libraryControls" id="libraryControls" action="/library" method="get">
              <div class="librarySearchWrap">
                <label class="libraryControlLabel" for="librarySearchInput">Search</label>
                <input
                  class="librarySearchInput"
                  id="librarySearchInput"
                  type="search"
                  name="q"
                  value="{html_lib.escape(library_filters['q'], quote=True)}"
                  placeholder="Search name, code, set code, or set name"
                  autocomplete="off"
                  inputmode="search"
                >
              </div>
              <div class="libraryFilterRow">
                <label class="libraryControl">
                  <span class="libraryControlLabel">Set</span>
                  <select class="librarySelect" name="set">
                    <option value="">All sets</option>
                    {set_options_html}
                  </select>
                </label>
                <label class="libraryControl">
                  <span class="libraryControlLabel">Color</span>
                  <select class="librarySelect" name="color">
                    <option value="">All colors</option>
                    {color_options_html}
                  </select>
                </label>
                <label class="libraryControl">
                  <span class="libraryControlLabel">Rarity</span>
                  <select class="librarySelect" name="rarity">
                    <option value="">All rarities</option>
                    {rarity_options_html}
                  </select>
                </label>
                <label class="libraryControl">
                  <span class="libraryControlLabel">Type</span>
                  <select class="librarySelect" name="card_type">
                    <option value="">All types</option>
                    {type_options_html}
                  </select>
                </label>
                <label class="libraryControl">
                  <span class="libraryControlLabel">Cost</span>
                  <select class="librarySelect" name="cost">
                    <option value="">Any cost</option>
                    {cost_options_html}
                  </select>
                </label>
                <label class="libraryControl">
                  <span class="libraryControlLabel">Attribute</span>
                  <select class="librarySelect" name="attribute">
                    <option value="">Any attribute</option>
                    {attribute_options_html}
                  </select>
                </label>
                <label class="libraryControl">
                  <span class="libraryControlLabel">Sort</span>
                  <select class="librarySelect" name="sort">
                    {sort_options_html}
                  </select>
                </label>
              </div>
            </form>

            <section class="libraryShell libraryShell--browse" id="card-library" data-library-fragment-url="{library_fragment_url}">
              <div id="libraryDeferredContent" class="libraryDeferredContent">
                <div class="card libraryDeferredState">Loading Project Miru library page...</div>
              </div>
            </section>
        """
    page_content_html = (
        library_page_html
        if is_library_page
        else f"""
        <section class="libraryIntro libraryIntro--watchlist" id="watchlist">
          <div class="libraryEyebrow">Market Rail</div>
          <h2 class="libraryTitle libraryTitle--premium">Tracked cards</h2>
        </section>

        <div class="grid" id="watchlistGrid">
          {cards_html}
        </div>
        {f'<div id="watchlistDeferred" class="libraryDeferredContent" data-watchlist-fragment-url="/watchlist-fragment?offset={len(initial_watchlist_cards)}"><div class="card libraryDeferredState">Loading {remaining_watchlist_count} more tracked cards...</div></div>' if remaining_watchlist_count else ""}

        {homepage_library_entry_html}
        """
    )

    page_html = f"""
    <html>
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{page_title}</title>
      <style>
        :root {{
          --bg: #08060f;
          --stroke: rgba(255,255,255,0.10);
          --muted: rgba(255,255,255,0.55);
          --muted2: rgba(255,255,255,0.35);
          --panel: rgba(10, 14, 22, 0.88);
          --panelStrong: rgba(14, 19, 31, 0.94);
          --brandBg: rgba(255,255,255,0.06);
          --brandStroke: rgba(201, 176, 255, 0.18);
          --brandGlow: rgba(165, 118, 255, 0.26);
          --brandPurple: rgba(184, 160, 255, 0.96);
          --brandGold: rgba(244, 208, 120, 0.96);
          --textSoft: rgba(237,243,255,0.88);

          --blueStroke: rgba(165, 118, 255, 0.34);
          --gText: rgba(200, 255, 225, 0.95);
          --reds: rgba(255, 90, 90, 0.38);

          --g1s: rgba(66, 214, 138, 0.45);
          --g2s: rgba(66, 214, 138, 0.62);
          --g3s: rgba(66, 214, 138, 0.85);
        }}

        body {{
          background: radial-gradient(1200px 800px at 50% -10%, rgba(165,118,255,0.18), transparent 55%),
                      radial-gradient(900px 700px at 100% 0%, rgba(244,208,120,0.08), transparent 48%),
                      radial-gradient(900px 700px at 0% 20%, rgba(114,82,189,0.10), transparent 50%),
                      var(--bg);
          color: white;
          font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
          padding: 14px;
          margin: 0;
          font-size: 13px;
        }}

        .appFrame {{
          width: min(1040px, 100%);
          margin: 0 auto;
        }}

        .brandHero {{
          position: relative;
          overflow: hidden;
          margin-bottom: 20px;
          padding: 36px 20px 22px;
          border-radius: 32px;
          border: 1px solid rgba(196, 173, 255, 0.16);
          background:
            radial-gradient(620px 320px at 50% -8%, rgba(136, 104, 255, 0.26), transparent 60%),
            radial-gradient(360px 240px at 82% 0%, rgba(244,208,120,0.10), transparent 64%),
            linear-gradient(180deg, rgba(17, 14, 30, 0.98), rgba(8, 10, 18, 0.99));
          box-shadow:
            0 24px 58px rgba(0,0,0,0.36),
            0 0 0 1px rgba(255,255,255,0.02) inset;
        }}

        .brandHero::after {{
          content: "";
          position: absolute;
          width: 220px;
          height: 220px;
          top: -92px;
          right: -64px;
          border-radius: 999px;
          background: radial-gradient(circle, rgba(113,194,255,0.12), transparent 72%);
          pointer-events: none;
        }}

        .logoStage {{
          position: absolute;
          left: 50%;
          top: 32px;
          transform: translateX(-50%);
          width: min(196px, 44vw);
          height: min(108px, 24vw);
          border-radius: 999px;
          background: radial-gradient(circle, rgba(166,107,255,0.20) 0%, rgba(244,201,93,0.08) 38%, rgba(166,107,255,0) 74%);
          filter: blur(16px);
          opacity: 0.88;
          pointer-events: none;
        }}

        .brandRow {{
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 18px;
          margin-bottom: 16px;
          text-align: center;
        }}

        .brandHero--library {{
          padding: 20px 18px 14px;
          margin-bottom: 12px;
          border-radius: 24px;
        }}

        .brandMark {{
          position: relative;
          width: clamp(142px, 27vw, 178px);
          height: clamp(142px, 27vw, 178px);
          display: block;
          animation: miruLogoFloat 8s ease-in-out infinite;
          will-change: transform;
        }}

        .brandHero--library .brandMark {{
          width: clamp(98px, 19vw, 122px);
          height: clamp(98px, 19vw, 122px);
        }}

        .brandLogoCompass {{
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          object-fit: contain;
          display: block;
          opacity: 0.97;
          transform: scale(1.01);
          transform-origin: center center;
          filter: drop-shadow(0 10px 20px rgba(0, 0, 0, 0.24));
          animation: miruCompassWheel 18s linear infinite;
          will-change: transform, opacity;
        }}

        .brandLogoFruitWrap {{
          position: absolute;
          width: 56.5%;
          left: 50%;
          top: 50.4%;
          transform: translate3d(-50%, -50%, 0);
          display: block;
          z-index: 2;
          animation: miruFruitPulse 5s ease-in-out infinite;
          will-change: transform, opacity;
        }}

        .brandLogoFruitWrap::after {{
          content: "";
          position: absolute;
          inset: 0;
          background: linear-gradient(
            115deg,
            rgba(255, 255, 255, 0) 28%,
            rgba(255, 236, 188, 0.0) 40%,
            rgba(255, 236, 188, 0.14) 51%,
            rgba(255, 236, 188, 0.0) 62%,
            rgba(255, 255, 255, 0) 74%
          );
          mix-blend-mode: screen;
          opacity: 0.24;
          transform: translateX(-120%);
          animation: miruFruitShimmer 6.4s ease-in-out 2.2s infinite;
          pointer-events: none;
        }}

        .brandLogoFruit {{
          width: 100%;
          height: auto;
          display: block;
          object-fit: contain;
          filter: drop-shadow(0 10px 18px rgba(0,0,0,0.24));
        }}

        .brandCopy {{
          min-width: 0;
          display: grid;
          justify-items: center;
          gap: 8px;
        }}

        .brandEyebrow {{
          color: rgba(244,208,120,0.9);
          font-size: 11px;
          font-weight: 860;
          letter-spacing: 1.7px;
          text-transform: uppercase;
          margin-bottom: 2px;
          text-shadow: 0 0 18px rgba(244,208,120,0.16);
        }}

        .brandTitle {{
          margin: 0;
          font-size: clamp(37px, 7.5vw, 49px);
          line-height: 1;
          font-weight: 940;
          letter-spacing: -1.28px;
          color: rgba(255, 250, 255, 0.98);
          text-shadow: 0 10px 30px rgba(18, 10, 36, 0.28);
        }}

        .brandHero--library .brandTitle {{
          font-size: clamp(28px, 6vw, 34px);
          letter-spacing: -0.9px;
        }}

        .brandBody {{
          margin: 0;
          max-width: 34rem;
          color: rgba(228, 221, 244, 0.84);
          font-size: 14px;
          line-height: 1.52;
        }}

        .brandHero--library .brandBody {{
          max-width: 24rem;
          font-size: 13px;
          line-height: 1.45;
        }}

        .heroUtility {{
          display: flex;
          flex-wrap: wrap;
          justify-content: center;
          gap: 8px;
          margin: 14px auto 0;
          max-width: 820px;
        }}

        .heroUtilityPill {{
          display: inline-flex;
          align-items: center;
          min-height: 30px;
          padding: 0 12px;
          border-radius: 999px;
          border: 1px solid rgba(212, 188, 255, 0.12);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(24, 18, 40, 0.84), rgba(12, 13, 22, 0.88));
          color: rgba(240, 242, 250, 0.9);
          font-size: 11px;
          font-weight: 760;
          letter-spacing: 0.28px;
          box-shadow: 0 10px 20px rgba(0,0,0,0.14);
        }}

        .heroUtilityPill--gold {{
          border-color: rgba(244,208,120,0.24);
          color: rgba(255, 246, 224, 0.98);
          box-shadow: 0 0 0 1px rgba(244,208,120,0.08) inset;
        }}

        .heroStats {{
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 10px;
          margin-top: 16px;
        }}

        .heroStat {{
          min-width: 0;
          padding: 14px 14px 13px;
          border-radius: 20px;
          border: 1px solid rgba(212, 188, 255, 0.09);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(23, 18, 38, 0.9), rgba(11, 12, 20, 0.94));
          box-shadow:
            0 12px 24px rgba(0,0,0,0.16),
            0 1px 0 rgba(255,255,255,0.03) inset;
        }}

        .heroStatLabel {{
          color: rgba(244,208,120,0.82);
          font-size: 10px;
          text-transform: uppercase;
          letter-spacing: 1.05px;
          margin-bottom: 7px;
        }}

        .heroStatValue {{
          font-size: 23px;
          font-weight: 900;
          line-height: 1;
          color: rgba(255, 248, 255, 0.98);
        }}

        .heroStatMeta {{
          margin-top: 6px;
          color: rgba(222, 215, 240, 0.68);
          font-size: 11px;
          line-height: 1.35;
        }}

        .libraryIntro {{
          margin-bottom: 18px;
          padding: 15px 16px;
          border-radius: 20px;
          border: 1px solid rgba(255,255,255,0.06);
          background:
            linear-gradient(180deg, rgba(18, 14, 31, 0.82), rgba(10, 11, 19, 0.9)),
            linear-gradient(135deg, rgba(165,118,255,0.06), rgba(244,208,120,0.03));
          box-shadow: 0 16px 30px rgba(0,0,0,0.18);
        }}

        .libraryIntro--watchlist {{
          margin-bottom: 8px;
          padding: 0 2px;
          border: 0;
          background: transparent;
          box-shadow: none;
        }}

        .libraryIntro--libraryPage {{
          margin-bottom: 10px;
          padding: 4px 2px 0;
          border: 0;
          background: transparent;
          box-shadow: none;
        }}

        .libraryEyebrow {{
          color: rgba(189,170,255,0.84);
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 1.1px;
          text-transform: uppercase;
          margin-bottom: 6px;
        }}

        .libraryTitle {{
          margin: 0;
          font-size: 20px;
          line-height: 1.1;
          letter-spacing: -0.25px;
        }}

        .libraryTitle--premium {{
          color: rgba(255, 250, 255, 0.98);
          text-shadow: 0 1px 0 rgba(255,255,255,0.02);
        }}

        .libraryBody {{
          margin: 6px 0 0;
          color: var(--muted);
          font-size: 12.5px;
          line-height: 1.45;
        }}

        .meta {{
          color: rgba(255,255,255,0.56);
          font-size: 12px;
          margin-top: 8px;
        }}

        .grid {{
          display: grid;
          grid-template-columns: 1fr;
          gap: 14px;
        }}

        .card {{
          background:
            linear-gradient(180deg, rgba(255,255,255,0.042), rgba(255,255,255,0.014)),
            linear-gradient(135deg, rgba(118, 82, 205, 0.08), rgba(244, 208, 120, 0.028) 52%, rgba(8, 9, 16, 0.01)),
            linear-gradient(180deg, rgba(18, 12, 28, 0.96), rgba(10, 10, 18, 0.98));
          border: 1px solid rgba(212, 188, 255, 0.085);
          border-radius: 22px;
          padding: 10px 11px 9px;
          overflow: hidden;
          position: relative;
          box-shadow: 0 14px 26px rgba(0,0,0,0.22);
          min-height: 104px;
          display: flex;
          flex-direction: column;
        }}

        .card[data-card] {{
          cursor: pointer;
          transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
        }}

        .card[data-card]:hover {{
          transform: translateY(-1px);
          border-color: rgba(244,208,120,0.15);
          box-shadow: 0 18px 32px rgba(0,0,0,0.24);
        }}

        .card.deal1 {{
          border-color: var(--g1s);
          box-shadow:
            0 0 0 1px rgba(66,214,138,0.18) inset,
            0 0 14px rgba(66,214,138,0.12),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .card.deal2 {{
          border-color: var(--g2s);
          box-shadow:
            0 0 0 1px rgba(66,214,138,0.24) inset,
            0 0 18px rgba(66,214,138,0.18),
            0 0 40px rgba(66,214,138,0.12),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .card.deal3 {{
          border-color: var(--g3s);
          box-shadow:
            0 0 0 1px rgba(120,255,190,0.22) inset,
            0 0 22px rgba(120,255,190,0.22),
            0 0 55px rgba(120,255,190,0.16),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .card.deal3::before {{
          content: "";
          position: absolute;
          inset: -2px;
          border-radius: 16px;
          background: conic-gradient(from 180deg,
            rgba(120,255,190,0.0),
            rgba(120,255,190,0.25),
            rgba(120,255,190,0.0)
          );
          filter: blur(10px);
          opacity: 0.7;
          pointer-events: none;
        }}

        .card.over {{
          border-color: var(--reds);
          box-shadow:
            0 0 0 1px rgba(255,90,90,0.14) inset,
            0 0 14px rgba(255,90,90,0.10),
            0 14px 30px rgba(0,0,0,0.38);
        }}

        .row {{
          display: grid;
          grid-template-columns: 58px minmax(0, 1fr) minmax(88px, auto);
          gap: 11px;
          align-items: start;
          position: relative;
          z-index: 1;
          min-height: 68px;
        }}

        @media (max-width: 360px) {{
          .row {{ grid-template-columns: 54px minmax(0, 1fr) minmax(82px, auto); gap: 8px; }}
        }}

        .left {{
          display: flex;
          justify-content: center;
          align-items: center;
        }}

        /* IMPORTANT: doubled braces for f-string safety */
        .thumb {{
          width: 58px;
          height: 80px;
          aspect-ratio: 0.72;
          object-fit: cover;
          border-radius: 15px;
          border: 1px solid rgba(255,255,255,0.09);
          background: rgba(0,0,0,0.35);
          box-shadow:
            0 10px 18px rgba(0,0,0,0.24),
            0 0 0 1px rgba(255,255,255,0.05) inset;
          display: block;
          flex: 0 0 auto;
        }}

        .ph {{
          width: 58px;
          height: 80px;
          border-radius: 15px;
          display: flex;
          align-items: center;
          justify-content: center;
          background: rgba(255,255,255,0.06);
          color: var(--muted);
          border: 1px solid rgba(255,255,255,0.10);
          font-size: 10px;
          text-align: center;
        }}

        .center {{
          min-width: 0;
          display: grid;
          gap: 5px;
          align-content: start;
          padding-top: 1px;
        }}

        .rail {{
          min-width: 0;
          display: grid;
          gap: 6px;
          justify-items: end;
          text-align: right;
          align-content: start;
          padding-top: 2px;
        }}

        .railDetail {{
          color: rgba(231, 223, 245, 0.78);
          font-size: 10.5px;
          line-height: 1.25;
          font-weight: 720;
          max-width: 108px;
        }}

        .actions {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 8px;
          margin-top: auto;
          padding-top: 8px;
          border-top: 1px solid rgba(255,255,255,0.06);
          min-width: 0;
        }}

        .title {{
          font-size: 13.5px;
          font-weight: 860;
          line-height: 1.22;
          letter-spacing: -0.01em;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
          word-break: break-word;
        }}

        .subtitle {{
          color: var(--muted);
          font-size: 10.5px;
          line-height: 1.28;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }}

        .val {{
          font-weight: 950;
          font-size: 17px;
          letter-spacing: 0.2px;
          line-height: 1;
          margin: 0;
        }}

        .actions .viewbtn {{
          min-width: 76px;
        }}

        .val.current {{
          color: rgba(255,255,255,0.92);
        }}

        .val.current.buy {{
          color: rgba(220,255,235,0.98);
          text-shadow: 0 0 14px rgba(120,255,190,0.18);
        }}

        .val.target {{
          color: rgba(255,255,255,0.86);
        }}

        .buybtn {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          min-height: 30px;
          width: 100%;
          padding: 0 10px;
          border-radius: 999px;
          background:
            linear-gradient(180deg, rgba(248, 216, 126, 0.14), rgba(248, 216, 126, 0.02)),
            linear-gradient(180deg, rgba(165,118,255,0.42), rgba(89,64,154,0.24));
          border: 1px solid rgba(244,208,120,0.34);
          color: rgba(255,248,236,0.98);
          font-size: 11px;
          font-weight: 860;
          letter-spacing: 0.18px;
          text-decoration: none;
          user-select: none;
          box-shadow:
            0 10px 20px rgba(0,0,0,0.18),
            0 0 0 1px rgba(255,255,255,0.04) inset;
          transition: transform 0.16s ease, filter 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
        }}

        .viewbtn {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          min-height: 30px;
          width: 100%;
          padding: 0 10px;
          border-radius: 999px;
          background:
            linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.016)),
            linear-gradient(180deg, rgba(24, 18, 43, 0.94), rgba(12, 10, 22, 0.94));
          border: 1px solid rgba(212, 188, 255, 0.16);
          color: rgba(250,251,255,0.96);
          font-size: 11px;
          font-weight: 840;
          letter-spacing: 0.18px;
          cursor: pointer;
          box-shadow: 0 8px 18px rgba(0,0,0,0.16);
          transition: transform 0.16s ease, filter 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
        }}

        .viewbtn:hover,
        .buybtn:hover {{
          filter: brightness(1.08);
          transform: translateY(-1px);
          box-shadow: 0 12px 24px rgba(0,0,0,0.22);
        }}

        .buybtn.isDisabled {{
          cursor: default;
          opacity: 0.76;
          border-color: rgba(255,255,255,0.12);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(24, 18, 43, 0.9), rgba(12, 10, 22, 0.94));
        }}

        .buybtn.isDisabled:hover {{
          filter: none;
          transform: none;
          box-shadow:
            0 10px 20px rgba(0,0,0,0.18),
            0 0 0 1px rgba(255,255,255,0.04) inset;
        }}

        .brandHero {{
          padding: 28px 20px 22px;
          box-shadow: 0 26px 58px rgba(0,0,0,0.32);
        }}

        .heroStats {{
          margin-top: 6px;
        }}

        .heroStat {{
          padding: 13px 14px;
          border-radius: 22px;
          border: 1px solid rgba(255,255,255,0.07);
          box-shadow: 0 14px 28px rgba(0,0,0,0.16);
        }}

        .heroNav {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 8px;
          width: min(100%, 860px);
          margin: 18px auto 0;
          padding: 9px;
          border-radius: 24px;
          border: 1px solid rgba(212, 188, 255, 0.1);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.032), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(18, 12, 31, 0.76), rgba(10, 11, 19, 0.62));
          box-shadow: 0 12px 28px rgba(0,0,0,0.18);
          backdrop-filter: blur(12px);
        }}

        .heroNavLink {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 44px;
          padding: 0 14px;
          border-radius: 16px;
          border: 1px solid rgba(212, 188, 255, 0.16);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.036), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(22, 18, 38, 0.94), rgba(12, 11, 20, 0.9));
          color: rgba(246, 248, 255, 0.94);
          font-size: 12px;
          font-weight: 850;
          letter-spacing: 0.45px;
          text-decoration: none;
          box-shadow:
            0 10px 22px rgba(0,0,0,0.18),
            0 1px 0 rgba(255,255,255,0.03) inset;
          transition: transform 0.16s ease, border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease;
        }}

        .heroNavLink--accent {{
          border-color: rgba(244,208,120,0.42);
          background:
            linear-gradient(180deg, rgba(248,216,126,0.16), rgba(248,216,126,0.03)),
            linear-gradient(180deg, rgba(148, 102, 255, 0.46), rgba(82, 55, 151, 0.28));
          color: rgba(255,248,236,0.98);
          box-shadow:
            0 12px 26px rgba(0,0,0,0.18),
            0 0 0 1px rgba(244,208,120,0.08) inset;
        }}

        .heroNavLink--accentSoft {{
          border-color: rgba(190, 165, 255, 0.26);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(64, 40, 118, 0.78), rgba(20, 16, 35, 0.92));
          color: rgba(243, 237, 255, 0.98);
        }}

        .heroNavLink--large {{
          min-height: 44px;
          padding: 0 16px;
        }}

        .heroNavLink:hover,
        .heroNavLink:focus-visible {{
          border-color: rgba(244, 201, 93, 0.34);
          box-shadow: 0 12px 24px rgba(0,0,0,0.22), 0 0 18px rgba(165,118,255,0.12);
          filter: brightness(1.05);
          transform: translateY(-1px);
          outline: none;
        }}

        .libraryIntro {{
          padding: 18px 18px 16px;
          border-radius: 24px;
          border: 1px solid rgba(208, 184, 255, 0.08);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.008)),
            linear-gradient(180deg, rgba(18, 11, 25, 0.82), rgba(10, 8, 15, 0.92));
          box-shadow: 0 18px 34px rgba(0,0,0,0.18);
        }}

        .card {{
          border: 1px solid rgba(255,255,255,0.075);
          border-radius: 26px;
          padding: 17px;
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(18, 11, 25, 0.88), rgba(10, 8, 15, 0.94));
          box-shadow: 0 18px 34px rgba(0,0,0,0.24);
        }}

        .thumb {{
          width: 132px;
          aspect-ratio: 0.72;
          object-fit: contain;
          border-radius: 18px;
          background: linear-gradient(180deg, rgba(13, 16, 28, 0.94), rgba(8, 10, 18, 0.98));
          box-shadow:
            0 12px 22px rgba(0,0,0,0.28),
            0 0 0 1px rgba(255,255,255,0.05) inset;
        }}

        .libraryShell {{
          margin-top: 18px;
        }}

        .libraryShell--browse {{
          margin-top: 14px;
        }}

        .libraryGateway {{
          margin-top: 18px;
          padding: 18px;
          border-radius: 24px;
          border: 1px solid rgba(212, 188, 255, 0.1);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(19, 13, 31, 0.9), rgba(10, 9, 18, 0.95));
          box-shadow: 0 18px 34px rgba(0,0,0,0.22);
        }}

        .libraryGatewayCopy {{
          display: grid;
          gap: 7px;
          margin-bottom: 12px;
        }}

        .libraryGatewayActions {{
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
          margin-top: 14px;
        }}

        .libraryControls {{
          display: grid;
          gap: 10px;
          margin-bottom: 14px;
          padding: 14px;
          border-radius: 22px;
          border: 1px solid rgba(212, 188, 255, 0.10);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(19, 14, 32, 0.94), rgba(10, 10, 18, 0.98));
          box-shadow:
            0 18px 30px rgba(0,0,0,0.22),
            0 1px 0 rgba(255,255,255,0.03) inset;
        }}

        .librarySearchWrap {{
          display: grid;
          gap: 6px;
        }}

        .libraryFilterRow {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 9px;
        }}

        .libraryControl {{
          display: grid;
          gap: 5px;
          padding: 10px;
          border-radius: 16px;
          border: 1px solid rgba(255,255,255,0.05);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.022), rgba(255,255,255,0.008)),
            linear-gradient(180deg, rgba(22, 18, 36, 0.9), rgba(12, 12, 22, 0.94));
        }}

        .libraryControlLabel {{
          color: rgba(244,208,120,0.86);
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 0.55px;
          text-transform: uppercase;
        }}

        .librarySearchInput,
        .librarySelect {{
          width: 100%;
          min-height: 40px;
          padding: 0 12px;
          border-radius: 13px;
          border: 1px solid rgba(212, 188, 255, 0.14);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(23, 19, 40, 0.96), rgba(13, 11, 24, 0.94));
          color: rgba(248,250,255,0.96);
          font-size: 13px;
          box-shadow: 0 8px 18px rgba(0,0,0,0.16);
        }}

        .librarySearchInput::placeholder {{
          color: rgba(222, 215, 240, 0.48);
        }}

        .librarySearchInput:focus,
        .librarySelect:focus {{
          outline: none;
          border-color: rgba(244,208,120,0.28);
          box-shadow:
            0 10px 22px rgba(0,0,0,0.18),
            0 0 0 1px rgba(244,208,120,0.12);
        }}

        .libraryDeferredContent {{
          min-height: 120px;
        }}

        .libraryDeferredState {{
          display: grid;
          place-items: center;
          min-height: 120px;
          color: var(--muted);
          text-align: center;
        }}

        .libraryHeader {{
          display: flex;
          flex-wrap: wrap;
          align-items: flex-end;
          justify-content: space-between;
          gap: 14px;
          margin-bottom: 14px;
          padding: 16px;
          border-radius: 24px;
          border: 1px solid rgba(255,255,255,0.07);
          background:
            linear-gradient(180deg, rgba(18, 14, 31, 0.82), rgba(10, 11, 19, 0.92)),
            linear-gradient(135deg, rgba(165,118,255,0.06), rgba(244,208,120,0.03));
          box-shadow: 0 16px 32px rgba(0,0,0,0.16);
        }}

        .libraryHeaderCopy {{
          min-width: 0;
        }}

        .libraryMetaPills {{
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 8px;
          width: min(100%, 540px);
        }}

        .libraryMetaPill {{
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 50px;
          padding: 9px 10px;
          border-radius: 16px;
          border: 1px solid rgba(212, 188, 255, 0.09);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(22, 17, 37, 0.86), rgba(11, 11, 20, 0.9));
          color: rgba(240, 244, 255, 0.9);
          font-size: 11px;
          font-weight: 760;
          line-height: 1.3;
          text-align: center;
          box-shadow: 0 10px 18px rgba(0,0,0,0.14);
        }}

        .libraryGrid {{
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          gap: 16px;
        }}

        .libraryGrid--browse {{
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }}

        .libraryCard {{
          display: flex;
          flex-direction: column;
          width: 100%;
          max-width: 420px;
          margin: 0 auto;
          border-radius: 24px;
          border: 1px solid rgba(208, 184, 255, 0.08);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.026), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(18, 11, 25, 0.88), rgba(10, 8, 15, 0.96));
          box-shadow: 0 18px 32px rgba(0,0,0,0.22);
          overflow: hidden;
        }}

        .libraryCard--browse {{
          max-width: none;
          border-radius: 16px;
          border-color: rgba(212, 188, 255, 0.1);
          box-shadow: 0 10px 18px rgba(0,0,0,0.16);
          min-height: 214px;
        }}

        .libraryCard[data-card] {{
          cursor: pointer;
        }}

        .libraryThumbButton {{
          padding: 0;
          border: 0;
          background: transparent;
          cursor: pointer;
          text-align: left;
        }}

        .libraryThumb {{
          width: 100%;
          max-width: 420px;
          aspect-ratio: 0.72;
          object-fit: contain;
          display: block;
          margin: 0 auto;
          background: linear-gradient(180deg, rgba(15, 12, 22, 0.98), rgba(8, 9, 16, 0.98));
        }}

        .libraryThumb--browse {{
          max-width: none;
          height: clamp(94px, 21vw, 112px);
          aspect-ratio: auto;
        }}

        .libraryThumb--empty {{
          display: flex;
          align-items: center;
          justify-content: center;
          color: rgba(255,255,255,0.55);
          font-size: 13px;
        }}

        .libraryCardBody {{
          display: grid;
          gap: 8px;
          padding: 16px;
        }}

        .libraryCardBody--browse {{
          gap: 4px;
          padding: 8px 8px 9px;
          flex: 1 1 auto;
        }}

        .libraryCodeRow {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
        }}

        .libraryCode,
        .libraryRarity {{
          font-size: 11px;
          font-weight: 850;
          letter-spacing: 0.55px;
          text-transform: uppercase;
          padding: 5px 9px;
          border-radius: 999px;
          border: 1px solid rgba(255,255,255,0.08);
          background: rgba(255,255,255,0.05);
        }}

        .libraryCardTitle {{
          margin: 0;
          font-size: 16px;
          line-height: 1.2;
        }}

        .libraryCardTitle--browse {{
          font-size: 11.5px;
          line-height: 1.18;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }}

        .libraryCardSubtitle {{
          color: rgba(255,255,255,0.66);
          font-size: 12px;
          line-height: 1.45;
          min-height: 2.8em;
        }}

        .libraryCardSubtitle--browse {{
          font-size: 9.5px;
          min-height: 2.35em;
          line-height: 1.22;
        }}

        .libraryPrice {{
          color: rgba(255, 235, 166, 0.98);
          font-size: 12px;
          font-weight: 860;
          letter-spacing: 0.12px;
        }}

        .libraryOpenBtn {{
          margin-top: 2px;
          min-height: 42px;
          padding: 0 12px;
          border-radius: 14px;
          border: 1px solid rgba(244,208,120,0.26);
          background:
            linear-gradient(180deg, rgba(248,216,126,0.08), rgba(248,216,126,0.015)),
            linear-gradient(180deg, rgba(22, 18, 38, 0.94), rgba(12, 11, 20, 0.92));
          color: rgba(255,250,242,0.98);
          font-weight: 860;
          letter-spacing: 0.22px;
          cursor: pointer;
          box-shadow: 0 10px 20px rgba(0,0,0,0.16);
          transition: transform 0.16s ease, filter 0.16s ease, box-shadow 0.16s ease;
        }}

        .libraryOpenBtn--browse {{
          min-height: 30px;
          padding: 0 9px;
          font-size: 10.5px;
          margin-top: auto;
        }}

        .libraryPager {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 10px;
          margin-top: 14px;
          padding: 14px 16px;
          border-radius: 22px;
          border: 1px solid rgba(255,255,255,0.07);
          background: rgba(255,255,255,0.03);
        }}

        .libraryPagerLinks {{
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }}

        .libraryPagerLink,
        .libraryPagerLink.isDisabled {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 96px;
          min-height: 40px;
          padding: 0 12px;
          border-radius: 999px;
          border: 1px solid rgba(244,208,120,0.24);
          background:
            linear-gradient(180deg, rgba(248,216,126,0.07), rgba(248,216,126,0.014)),
            linear-gradient(180deg, rgba(22, 18, 38, 0.94), rgba(12, 11, 20, 0.92));
          color: rgba(255,248,238,0.96);
          font-size: 12px;
          font-weight: 840;
          text-decoration: none;
          box-shadow: 0 8px 18px rgba(0,0,0,0.16);
        }}

        .libraryPagerLink.isDisabled {{
          opacity: 0.45;
          pointer-events: none;
        }}

        .miruHelper {{
          position: fixed;
          right: 16px;
          bottom: 16px;
          z-index: 1100;
          display: grid;
          justify-items: end;
          gap: 10px;
        }}

        .miruHelperPanel {{
          width: min(280px, calc(100vw - 28px));
          padding: 14px;
          border-radius: 20px;
          border: 1px solid rgba(244,208,120,0.18);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.014)),
            linear-gradient(180deg, rgba(18, 12, 31, 0.96), rgba(10, 10, 20, 0.98));
          box-shadow:
            0 18px 36px rgba(0,0,0,0.34),
            0 0 0 1px rgba(255,255,255,0.03) inset;
          backdrop-filter: blur(14px);
          transform-origin: bottom right;
          transition: opacity 0.18s ease, transform 0.18s ease;
        }}

        .miruHelperPanel[hidden] {{
          display: none;
        }}

        .miruHelperTitle {{
          margin: 0;
          font-size: 15px;
          font-weight: 860;
          color: rgba(255,250,255,0.98);
        }}

        .miruHelperBody {{
          margin: 5px 0 0;
          color: rgba(228, 221, 244, 0.78);
          font-size: 12px;
          line-height: 1.45;
        }}

        .miruHelperContext {{
          margin: 10px 0 0;
          color: rgba(244,208,120,0.88);
          font-size: 11px;
          font-weight: 760;
          line-height: 1.4;
          min-height: 1.4em;
        }}

        .miruHelperActions {{
          display: grid;
          gap: 8px;
          margin-top: 12px;
        }}

        .miruHelperAction {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 38px;
          padding: 0 12px;
          border-radius: 14px;
          border: 1px solid rgba(212, 188, 255, 0.16);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.014)),
            linear-gradient(180deg, rgba(23, 18, 40, 0.96), rgba(12, 10, 21, 0.94));
          color: rgba(250,251,255,0.96);
          text-decoration: none;
          font-size: 12px;
          font-weight: 840;
          letter-spacing: 0.16px;
          box-shadow: 0 10px 20px rgba(0,0,0,0.16);
          transition: transform 0.16s ease, filter 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease;
          cursor: pointer;
        }}

        .miruHelperAction:hover,
        .miruHelperAction:focus-visible {{
          transform: translateY(-1px);
          filter: brightness(1.05);
          box-shadow: 0 12px 24px rgba(0,0,0,0.22);
          outline: none;
        }}

        .miruHelperAction--accent {{
          border-color: rgba(244,208,120,0.28);
          background:
            linear-gradient(180deg, rgba(248,216,126,0.10), rgba(248,216,126,0.02)),
            linear-gradient(180deg, rgba(150, 103, 255, 0.44), rgba(82, 55, 151, 0.28));
          color: rgba(255,248,236,0.98);
        }}

        .miruHelperToggle {{
          position: relative;
          width: 62px;
          height: 62px;
          border-radius: 999px;
          border: 1px solid rgba(244,208,120,0.24);
          background:
            radial-gradient(circle at 30% 30%, rgba(255,255,255,0.18), transparent 48%),
            linear-gradient(180deg, rgba(248,216,126,0.10), rgba(248,216,126,0.01)),
            linear-gradient(180deg, rgba(146, 102, 255, 0.52), rgba(78, 51, 144, 0.34));
          box-shadow:
            0 16px 30px rgba(0,0,0,0.28),
            0 0 0 1px rgba(255,255,255,0.04) inset,
            0 0 26px rgba(165,118,255,0.24);
          display: grid;
          place-items: center;
          cursor: pointer;
          transition: transform 0.16s ease, box-shadow 0.16s ease, filter 0.16s ease;
        }}

        .miruHelperToggle::before {{
          content: "";
          position: absolute;
          inset: -6px;
          border-radius: 999px;
          border: 1px solid rgba(165,118,255,0.18);
          opacity: 0.7;
          pointer-events: none;
        }}

        .miruHelperToggle:hover,
        .miruHelperToggle:focus-visible {{
          transform: translateY(-1px) scale(1.02);
          box-shadow:
            0 20px 34px rgba(0,0,0,0.3),
            0 0 0 1px rgba(255,255,255,0.04) inset,
            0 0 32px rgba(165,118,255,0.3);
          filter: brightness(1.04);
          outline: none;
        }}

        .miruHelperFruit {{
          width: 34px;
          height: 34px;
          object-fit: contain;
          display: block;
          filter: drop-shadow(0 8px 16px rgba(0,0,0,0.24));
        }}

        @keyframes miruLogoFloat {{
          0%, 100% {{ transform: translateY(0); }}
          35% {{ transform: translateY(-5px); }}
          65% {{ transform: translateY(-2px); }}
        }}

        @keyframes miruCompassWheel {{
          0% {{ transform: scale(1.07) rotate(0deg); opacity: 0.92; }}
          50% {{ transform: scale(1.08) rotate(4deg); opacity: 0.95; }}
          100% {{ transform: scale(1.07) rotate(8deg); opacity: 0.92; }}
        }}

        @keyframes miruFruitPulse {{
          0%, 100% {{ transform: translate(-50%, -50%) scale(1); opacity: 1; }}
          45% {{ transform: translate(-50%, -52%) scale(1.03); opacity: 0.98; }}
          72% {{ transform: translate(-50%, -49%) scale(1.01); opacity: 1; }}
        }}

        @keyframes miruFruitShimmer {{
          0%, 62%, 100% {{
            transform: translateX(-120%);
            opacity: 0;
          }}
          12%, 28% {{
            opacity: 0.22;
          }}
          34% {{
            transform: translateX(120%);
            opacity: 0;
          }}
        }}

        @media (min-width: 700px) {{
          .libraryFilterRow {{
            grid-template-columns: repeat(4, minmax(0, 1fr));
          }}

          .libraryGrid {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }}
        }}

        @media (min-width: 1024px) {{
          .libraryFilterRow {{
            grid-template-columns: repeat(7, minmax(0, 1fr));
          }}

          .libraryGrid {{
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(4, minmax(0, 1fr));
          }}
        }}

        @media (min-width: 1360px) {{
          .appFrame {{
            width: min(1360px, 100%);
          }}

          .libraryGrid {{
            grid-template-columns: repeat(4, minmax(0, 1fr));
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(5, minmax(0, 1fr));
          }}
        }}

        @media (max-width: 640px) {{
          .brandHero {{
            padding: 28px 14px 18px;
          }}

          .logoStage {{
            top: 28px;
          }}

          .heroStats {{
            gap: 8px;
          }}

          .heroStat {{
            padding: 12px 12px 11px;
          }}

          .row {{
            grid-template-columns: 52px minmax(0, 1fr) auto;
            gap: 7px;
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}

          .left {{
            justify-content: flex-start;
          }}

          .thumb,
          .ph {{
            width: 52px;
            height: 68px;
            margin: 0;
          }}

          .title {{
            font-size: 12px;
          }}
        }}

        @media (max-width: 480px) {{
          .heroNav {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
            width: 100%;
            padding: 6px;
          }}

          .heroNavLink {{
            min-height: 42px;
          }}

          .heroStats {{
            gap: 7px;
          }}

          .heroStat {{
            padding: 11px 10px 10px;
            border-radius: 18px;
          }}

          .heroStatValue {{
            font-size: 20px;
          }}

          .heroStatMeta {{
            font-size: 10px;
          }}

          .libraryMetaPills {{
            gap: 7px;
          }}

          .libraryMetaPill {{
            min-height: 48px;
            padding: 8px 8px;
            font-size: 10.5px;
          }}

          .libraryGrid {{
            gap: 12px;
          }}

          .card {{
            padding: 8px 8px;
            border-radius: 18px;
            min-height: 106px;
          }}

          .actions {{
            padding-top: 6px;
            gap: 5px;
          }}

          .libraryCardBody {{
            padding: 10px;
          }}

          .libraryCard--browse {{
            min-height: 198px;
          }}

          .libraryPager {{
            align-items: flex-start;
            flex-direction: column;
          }}

          .miruHelper {{
            right: 12px;
            bottom: 12px;
          }}

          .miruHelperToggle {{
            width: 56px;
            height: 56px;
          }}

          .miruHelperFruit {{
            width: 30px;
            height: 30px;
          }}
        }}

        .modalShell {{
          position: fixed;
          inset: 0;
          display: none;
          align-items: center;
          justify-content: center;
          padding: 18px;
          background: rgba(2, 6, 12, 0.82);
          backdrop-filter: blur(10px);
          z-index: 1000;
        }}

        .modalShell.open {{
          display: flex;
        }}

        .modalCard {{
          width: min(760px, 100%);
          max-height: min(90vh, 980px);
          overflow: auto;
          border-radius: 24px;
          border: 1px solid rgba(255,255,255,0.10);
          background:
            radial-gradient(700px 500px at 0% 0%, rgba(165,118,255,0.12), transparent 60%),
            radial-gradient(300px 220px at 100% 0%, rgba(244,208,120,0.08), transparent 58%),
            linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.02));
          box-shadow: 0 30px 64px rgba(0,0,0,0.45);
        }}

        .modalInner {{
          padding: 14px;
        }}

        .modalTop {{
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 10px;
          margin: -14px -14px 14px;
          padding: 13px 14px 11px;
          position: sticky;
          top: 0;
          z-index: 3;
          background:
            linear-gradient(180deg, rgba(17, 15, 29, 0.96), rgba(11, 12, 21, 0.90));
          backdrop-filter: blur(12px);
          border-bottom: 1px solid rgba(255,255,255,0.08);
        }}

        .modalKicker {{
          color: rgba(244,208,120,0.82);
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 1px;
          text-transform: uppercase;
          margin-bottom: 6px;
        }}

        .modalTitle {{
          font-size: 24px;
          line-height: 1.04;
          font-weight: 900;
          margin: 0;
          letter-spacing: -0.02em;
        }}

        .modalSubtitle {{
          margin-top: 6px;
          color: rgba(255,255,255,0.7);
          font-size: 12px;
          line-height: 1.38;
        }}

        .closeBtn {{
          border: 1px solid rgba(255,255,255,0.10);
          background: rgba(255,255,255,0.06);
          color: white;
          border-radius: 14px;
          padding: 10px 14px;
          font-weight: 800;
          cursor: pointer;
          box-shadow: 0 10px 22px rgba(0,0,0,0.22);
        }}

        .detailLayout {{
          display: grid;
          grid-template-columns: minmax(220px, 320px) minmax(0, 1fr);
          gap: 14px;
          align-items: start;
        }}

        .detailMedia {{
          display: flex;
          justify-content: center;
        }}

        .detailMediaStack {{
          width: min(100%, 360px);
          display: grid;
          gap: 6px;
        }}

        .detailImageBadge {{
          display: inline-flex;
          align-items: center;
          justify-self: start;
          min-height: 24px;
          padding: 0 9px;
          border-radius: 999px;
          border: 1px solid rgba(244,208,120,0.16);
          background:
            linear-gradient(180deg, rgba(244,208,120,0.10), rgba(165,118,255,0.04)),
            rgba(255,255,255,0.03);
          color: rgba(249, 223, 151, 0.92);
          font-size: 10px;
          font-weight: 800;
          letter-spacing: 0.35px;
          text-transform: uppercase;
        }}

        .detailImage {{
          width: min(100%, 360px);
          max-height: min(72vh, 540px);
          border-radius: 20px;
          border: 1px solid rgba(255,255,255,0.10);
          background: rgba(0,0,0,0.35);
          box-shadow:
            0 16px 30px rgba(0,0,0,0.4),
            0 0 0 1px rgba(255,255,255,0.05) inset;
          display: block;
          object-fit: contain;
          image-rendering: auto;
        }}

        .detailImagePh {{
          width: min(100%, 360px);
          min-height: 420px;
          border-radius: 16px;
          border: 1px solid rgba(255,255,255,0.10);
          background: rgba(255,255,255,0.04);
          color: var(--muted);
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          padding: 20px;
        }}

        .detailInfo {{
          display: grid;
          gap: 12px;
        }}

        .detailActionHub {{
          border-radius: 16px;
          border: 1px solid rgba(244,208,120,0.14);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(24, 17, 38, 0.88), rgba(12, 11, 20, 0.94));
          padding: 11px;
          box-shadow: 0 12px 24px rgba(0,0,0,0.16);
        }}

        .detailActionHeader {{
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 10px;
          margin-bottom: 10px;
        }}

        .detailActionEyebrow {{
          color: rgba(244,208,120,0.82);
          font-size: 10px;
          font-weight: 820;
          letter-spacing: 0.8px;
          text-transform: uppercase;
        }}

        .detailActionTitle {{
          margin-top: 3px;
          color: rgba(252, 252, 255, 0.98);
          font-size: 16px;
          font-weight: 880;
          line-height: 1.15;
        }}

        .detailPriceChip {{
          min-width: 92px;
          padding: 8px 10px;
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.08);
          background: rgba(255,255,255,0.04);
          text-align: right;
        }}

        .detailPriceChipLabel {{
          color: rgba(255,255,255,0.56);
          font-size: 10px;
          text-transform: uppercase;
          letter-spacing: 0.55px;
        }}

        .detailPriceChipValue {{
          margin-top: 4px;
          color: rgba(255, 239, 182, 0.98);
          font-size: 16px;
          font-weight: 900;
          line-height: 1;
        }}

        .detailActionForm {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 8px;
          align-items: end;
        }}

        .detailTargetField {{
          display: grid;
          gap: 4px;
          min-width: 0;
        }}

        .detailTargetLabel {{
          color: rgba(255,255,255,0.74);
          font-size: 11px;
          font-weight: 760;
        }}

        .detailTargetInput {{
          width: 100%;
          min-height: 38px;
          padding: 0 11px;
          border-radius: 13px;
          border: 1px solid rgba(212, 188, 255, 0.16);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(23, 19, 40, 0.96), rgba(13, 11, 24, 0.94));
          color: rgba(248,250,255,0.96);
          font-size: 13px;
          box-shadow: 0 8px 16px rgba(0,0,0,0.14);
        }}

        .detailTargetInput::placeholder {{
          color: rgba(222, 215, 240, 0.44);
        }}

        .detailWatchBtn {{
          min-height: 38px;
          padding: 0 13px;
          border-radius: 13px;
          border: 1px solid rgba(244,208,120,0.3);
          background:
            linear-gradient(180deg, rgba(248,216,126,0.12), rgba(248,216,126,0.02)),
            linear-gradient(180deg, rgba(82, 58, 148, 0.92), rgba(26, 20, 46, 0.96));
          color: rgba(255,248,236,0.98);
          font-size: 12px;
          font-weight: 860;
          letter-spacing: 0.18px;
          cursor: pointer;
          box-shadow: 0 10px 18px rgba(0,0,0,0.18);
          transition: transform 0.16s ease, filter 0.16s ease, box-shadow 0.16s ease;
        }}

        .detailWatchBtn:hover,
        .detailWatchBtn:focus-visible {{
          filter: brightness(1.06);
          transform: translateY(-1px);
          box-shadow: 0 12px 22px rgba(0,0,0,0.22);
          outline: none;
        }}

        .detailWatchBtn[disabled] {{
          opacity: 0.62;
          cursor: wait;
          transform: none;
          filter: none;
          box-shadow: 0 8px 14px rgba(0,0,0,0.14);
        }}

        .detailWatchFeedback {{
          margin-top: 8px;
          min-height: 1.2em;
          color: rgba(222, 215, 240, 0.74);
          font-size: 11px;
          line-height: 1.35;
        }}

        .detailWatchFeedback[data-tone="success"] {{
          color: rgba(189, 252, 214, 0.96);
        }}

        .detailWatchFeedback[data-tone="error"] {{
          color: rgba(255, 191, 191, 0.96);
        }}

        .detailStats {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 9px;
        }}

        .detailStat,
        .detailTextBlock {{
          background: rgba(255,255,255,0.04);
          border: 1px solid rgba(255,255,255,0.07);
          border-radius: 14px;
          padding: 11px 11px;
        }}

        .detailStatLabel,
        .detailTextLabel {{
          color: rgba(255,255,255,0.56);
          font-size: 10px;
          letter-spacing: 0.6px;
          text-transform: uppercase;
          margin-bottom: 6px;
        }}

        .detailStatValue {{
          font-size: 14px;
          font-weight: 800;
          line-height: 1.3;
          word-break: break-word;
        }}

        .detailTextValue {{
          font-size: 13px;
          line-height: 1.55;
          color: rgba(245,248,255,0.94);
          white-space: pre-wrap;
          word-break: break-word;
        }}

        .detailTextValue.empty {{
          color: rgba(255,255,255,0.5);
        }}

        .modalActions {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 2px;
        }}

        .modalActions .buybtn {{
          display: inline-block;
          min-width: 210px;
        }}

        body.modalOpen {{
          overflow: hidden;
        }}

        @media (max-width: 760px) {{
          .modalShell {{
            padding: 10px;
            align-items: flex-end;
          }}

          .modalCard {{
            max-height: 94vh;
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
          }}

          .detailMediaStack,
          .detailImage,
          .detailImagePh {{
            width: min(100%, 390px);
          }}

          .modalInner {{
            padding: 14px;
          }}

          .modalTop {{
            margin: -14px -14px 16px;
            padding: 14px 14px 12px;
          }}

          .modalTitle {{
            font-size: 22px;
          }}

          .detailLayout {{
            grid-template-columns: 1fr;
          }}

          .detailStats {{
            grid-template-columns: 1fr 1fr;
          }}

          .detailActionForm {{
            grid-template-columns: 1fr;
          }}
        }}

        @media (max-width: 480px) {{
          body {{
            padding: 12px;
          }}

          .brandHero {{
            padding: 24px 14px 16px;
            border-radius: 22px;
          }}

          .brandMark {{
            width: 148px;
            height: 148px;
          }}

          .heroStats {{
            margin-left: 0;
            margin-right: 0;
          }}

          .detailStats {{
            grid-template-columns: 1fr;
          }}

          .detailActionHeader {{
            align-items: stretch;
            flex-direction: column;
          }}

          .detailPriceChip {{
            min-width: 0;
            text-align: left;
          }}

          .modalActions .buybtn {{
            min-width: 100%;
          }}

          .actions {{
            grid-template-columns: 1fr;
          }}

          .prices {{
            grid-template-columns: 1fr;
          }}

          .brandBody {{
            font-size: 14px;
          }}
        }}

        .card.deal3 .buybtn {{
          background: linear-gradient(180deg, rgba(120,255,190,0.20), rgba(66,214,138,0.12));
          border-color: rgba(120,255,190,0.45);
        }}

        .buybtn:active {{
          transform: translateY(1px);
          filter: brightness(1.05);
        }}
      </style>
    </head>
    <body>
      <div class="appFrame">
        <section class="{brand_hero_class}">
          <div class="logoStage" aria-hidden="true"></div>
          <div class="brandRow">
            <div class="brandMark">
              <img class="brandLogoCompass" src="/static/icons/project_miru_compass.png" alt="" aria-hidden="true">
              <span class="brandLogoFruitWrap">
                <img class="brandLogoFruit" src="/static/icons/project_miru_fruit.png" alt="Miru heart logo">
              </span>
            </div>
            <div class="brandCopy">
              <div class="brandEyebrow">{hero_eyebrow}</div>
              <h1 class="brandTitle">Project Miru</h1>
              <p class="brandBody">{brand_body}</p>
            </div>
          </div>
          {hero_utility_html}
          {hero_stats_html}
          <div class="heroNav" aria-label="Project Miru quick navigation">
            {hero_nav_html}
          </div>
        </section>

        {page_content_html}
      </div>

      <div id="cardDetailModal" class="modalShell" aria-hidden="true">
        <div class="modalCard" role="dialog" aria-modal="true" aria-labelledby="cardDetailTitle">
          <div class="modalInner">
            <div class="modalTop">
              <div>
                <div class="modalKicker">Project Miru Card Sheet</div>
                <h3 id="cardDetailTitle" class="modalTitle">Card Details</h3>
                <div id="cardDetailSubtitle" class="modalSubtitle"></div>
              </div>
              <button id="cardDetailClose" class="closeBtn" type="button" aria-label="Close card details">Close</button>
            </div>

            <div class="detailLayout">
              <div id="cardDetailMedia" class="detailMedia"></div>
              <div class="detailInfo">
                <div class="detailActionHub">
                  <div class="detailActionHeader">
                    <div>
                      <div class="detailActionEyebrow">Watchlist</div>
                      <div class="detailActionTitle">Track this card</div>
                    </div>
                    <div class="detailPriceChip">
                      <div class="detailPriceChipLabel">Current price</div>
                      <div id="cardDetailCurrentPrice" class="detailPriceChipValue">—</div>
                    </div>
                  </div>
                  <div class="detailActionForm">
                    <label class="detailTargetField" for="cardDetailTargetPrice">
                      <span class="detailTargetLabel">Target price</span>
                      <input id="cardDetailTargetPrice" class="detailTargetInput" type="text" inputmode="decimal" placeholder="Optional">
                    </label>
                    <button id="cardDetailWatchButton" class="detailWatchBtn" type="button">Add to watchlist</button>
                  </div>
                  <div id="cardDetailWatchFeedback" class="detailWatchFeedback" aria-live="polite"></div>
                </div>
                <div id="cardDetailStats" class="detailStats"></div>
                <div class="detailTextBlock">
                  <div class="detailTextLabel">Effect Text</div>
                  <div id="cardDetailEffect" class="detailTextValue"></div>
                </div>
                <div class="detailTextBlock">
                  <div class="detailTextLabel">Trigger Text</div>
                  <div id="cardDetailTrigger" class="detailTextValue"></div>
                </div>
                <div class="modalActions">
                  <a id="cardDetailMarketLink" class="buybtn" href="#" target="_blank" rel="noopener">Open on TCGplayer</a>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="miruHelper" aria-label="Miru helper">
        <div class="miruHelperPanel" id="miruHelperPanel" hidden>
          <h3 class="miruHelperTitle">Miru helper</h3>
          <p class="miruHelperBody">Quick jump into the local Miru assistant from Project Miru.</p>
          <p class="miruHelperContext" id="miruHelperContext">Open a card to ask with card context.</p>
          <div class="miruHelperActions">
            <a class="miruHelperAction miruHelperAction--accent" id="miruHelperOpenAi" href="http://127.0.0.1:18765/" target="_blank" rel="noopener">Open Miru AI</a>
            <a class="miruHelperAction" id="miruHelperAskCard" href="http://127.0.0.1:18765/" target="_blank" rel="noopener">Ask about this card</a>
            <a class="miruHelperAction" id="miruHelperFacts" href="http://127.0.0.1:18765/" target="_blank" rel="noopener">Check verified facts</a>
          </div>
        </div>
        <button class="miruHelperToggle" id="miruHelperToggle" type="button" aria-expanded="false" aria-controls="miruHelperPanel" aria-label="Open Miru helper">
          <img class="miruHelperFruit" src="/static/icons/project_miru_fruit.png" alt="" aria-hidden="true">
        </button>
      </div>

      <script>
        const modal = document.getElementById("cardDetailModal");
        const closeBtn = document.getElementById("cardDetailClose");
        const titleNode = document.getElementById("cardDetailTitle");
        const subtitleNode = document.getElementById("cardDetailSubtitle");
        const mediaNode = document.getElementById("cardDetailMedia");
        const currentPriceNode = document.getElementById("cardDetailCurrentPrice");
        const targetPriceInput = document.getElementById("cardDetailTargetPrice");
        const watchButton = document.getElementById("cardDetailWatchButton");
        const watchFeedback = document.getElementById("cardDetailWatchFeedback");
        const statsNode = document.getElementById("cardDetailStats");
        const effectNode = document.getElementById("cardDetailEffect");
        const triggerNode = document.getElementById("cardDetailTrigger");
        const marketLink = document.getElementById("cardDetailMarketLink");
        const miruHelperPanel = document.getElementById("miruHelperPanel");
        const miruHelperToggle = document.getElementById("miruHelperToggle");
        const miruHelperContext = document.getElementById("miruHelperContext");
        const miruHelperOpenAi = document.getElementById("miruHelperOpenAi");
        const miruHelperAskCard = document.getElementById("miruHelperAskCard");
        const miruHelperFacts = document.getElementById("miruHelperFacts");
        const libraryControls = document.getElementById("libraryControls");
        const libraryShell = document.getElementById("card-library");
        const libraryDeferredContent = document.getElementById("libraryDeferredContent");
        const watchlistGrid = document.getElementById("watchlistGrid");
        const watchlistDeferred = document.getElementById("watchlistDeferred");
        const libraryPagePath = "/library";
        let lastTrigger = null;
        let currentCardPayload = null;
        let libraryLoaded = false;
        let libraryLoadPromise = null;
        let librarySearchTimer = 0;
        let watchlistLoadPromise = null;

        function buildLibrarySessionKey(fragmentUrl = "") {{
          const target = fragmentUrl
            ? new URL(fragmentUrl, window.location.origin)
            : new URL(window.location.href);
          const pageTarget = new URL(window.location.origin + target.pathname);
          for (const [key, value] of target.searchParams.entries()) {{
            if (key === "mode") {{
              continue;
            }}
            if (key === "library_page" && value === "1") {{
              continue;
            }}
            if (key === "sort" && value === "set_card_asc") {{
              continue;
            }}
            pageTarget.searchParams.set(key, value);
          }}
          return `project-miru:library:${{pageTarget.pathname}}?${{pageTarget.searchParams.toString()}}`;
        }}

        function persistLibraryFragment(fragmentUrl, html) {{
          if (!libraryShell || !window.sessionStorage) {{
            return;
          }}
          try {{
            window.sessionStorage.setItem(
              buildLibrarySessionKey(fragmentUrl),
              JSON.stringify({{ html, savedAt: Date.now() }})
            );
          }} catch (_error) {{
          }}
        }}

        function restoreLibraryFragment() {{
          if (!libraryShell || !libraryDeferredContent || !window.sessionStorage) {{
            return false;
          }}
          try {{
            const raw = window.sessionStorage.getItem(buildLibrarySessionKey());
            if (!raw) {{
              return false;
            }}
            const payload = JSON.parse(raw);
            const html = String(payload && payload.html || "");
            if (!html) {{
              return false;
            }}
            libraryDeferredContent.innerHTML = html;
            libraryLoaded = true;
            return true;
          }} catch (_error) {{
            return false;
          }}
        }}

        function renderDetailText(node, value, emptyText) {{
          const text = (value || "").trim();
          node.textContent = text || emptyText;
          node.classList.toggle("empty", !text);
        }}

        function renderStat(label, value) {{
          const wrapper = document.createElement("div");
          wrapper.className = "detailStat";
          const labelNode = document.createElement("div");
          labelNode.className = "detailStatLabel";
          labelNode.textContent = label;
          const valueNode = document.createElement("div");
          valueNode.className = "detailStatValue";
          valueNode.textContent = (value || "").trim() || "—";
          wrapper.appendChild(labelNode);
          wrapper.appendChild(valueNode);
          return wrapper;
        }}

        function normalizeMoneyInput(value) {{
          const raw = String(value || "").trim();
          if (!raw) {{
            return "";
          }}
          const cleaned = raw.replace(/\\$/g, "").replace(/,/g, "");
          const amount = Number.parseFloat(cleaned);
          if (!Number.isFinite(amount) || amount < 0) {{
            return "";
          }}
          return amount.toFixed(2);
        }}

        function formatMoney(value) {{
          const normalized = normalizeMoneyInput(value);
          return normalized ? `$${{normalized}}` : "";
        }}

        function setWatchFeedback(message, tone = "") {{
          if (!watchFeedback) {{
            return;
          }}
          watchFeedback.textContent = message || "";
          watchFeedback.dataset.tone = tone || "";
        }}

        function syncWatchActionUi(payload = currentCardPayload || {{}}) {{
          if (currentPriceNode) {{
            currentPriceNode.textContent = String(payload.current_price || "").trim() || "—";
          }}
          if (targetPriceInput) {{
            targetPriceInput.value = normalizeMoneyInput(payload.target_price || "");
          }}
          if (watchButton) {{
            watchButton.textContent = String(payload.target_price || "").trim()
              ? "Update watch price"
              : "Add to watchlist";
          }}
          setWatchFeedback("");
        }}

        function updateCardPayloadsForCode(code, updater) {{
          const codeText = String(code || "").trim().toUpperCase();
          if (!codeText) {{
            return;
          }}
          document.querySelectorAll("[data-card]").forEach((node) => {{
            const raw = node instanceof HTMLElement ? node.dataset.card : "";
            if (!raw) {{
              return;
            }}
            try {{
              const payload = JSON.parse(raw);
              if (String(payload.code || "").trim().toUpperCase() !== codeText) {{
                return;
              }}
              updater(payload);
              if (node instanceof HTMLElement) {{
                node.dataset.card = JSON.stringify(payload);
              }}
            }} catch (_error) {{
            }}
          }});
        }}

        function updateVisibleWatchlistRow(code, entry) {{
          const codeText = String(code || "").trim().toUpperCase();
          if (!codeText) {{
            return;
          }}
          document.querySelectorAll(".card[data-card]").forEach((card) => {{
            if (!(card instanceof HTMLElement)) {{
              return;
            }}
            try {{
              const payload = JSON.parse(card.dataset.card || "{{}}");
              if (String(payload.code || "").trim().toUpperCase() !== codeText) {{
                return;
              }}
            }} catch (_error) {{
              return;
            }}
            const railValue = card.querySelector(".val.current, .val.current.buy");
            if (railValue && entry.current_price) {{
              railValue.textContent = entry.current_price;
            }}
            const railDetail = card.querySelector(".railDetail");
            if (railDetail) {{
              railDetail.textContent = entry.target_price ? `Target ${{normalizeMoneyInput(entry.target_price)}}` : "Watching";
            }}
          }});
        }}

        function buildMiruPrompt(kind) {{
          const payload = currentCardPayload || {{}};
          const code = String(payload.code || "").trim();
          const name = String(payload.name || "").trim();
          const label = [code, name].filter(Boolean).join(" ");
          if (kind === "facts") {{
            return label
              ? `Check the verified facts for ${{label}}. Focus on confirmed card details only.`
              : "Check verified facts for this One Piece card and answer only from confirmed data.";
          }}
          return label
            ? `Tell me about ${{label}}. Summarize the verified card details and what matters most.`
            : "Tell me about this One Piece card using verified card details.";
        }}

        function buildMiruHelperHref(kind) {{
          const url = new URL("http://127.0.0.1:18765/", window.location.origin);
          if (kind === "open") {{
            return url.toString();
          }}
          url.searchParams.set("request_text", buildMiruPrompt(kind));
          if (kind === "facts") {{
            url.searchParams.set("mode", "card lookup");
          }}
          return url.toString();
        }}

        function syncMiruHelperLinks() {{
          if (miruHelperOpenAi) {{
            miruHelperOpenAi.href = buildMiruHelperHref("open");
          }}
          if (miruHelperAskCard) {{
            miruHelperAskCard.href = buildMiruHelperHref("ask");
          }}
          if (miruHelperFacts) {{
            miruHelperFacts.href = buildMiruHelperHref("facts");
          }}
          if (miruHelperContext) {{
            const payload = currentCardPayload || {{}};
            const code = String(payload.code || "").trim();
            const name = String(payload.name || "").trim();
            miruHelperContext.textContent = code || name
              ? `Current card: ${{[code, name].filter(Boolean).join(" · ")}}`
              : "Open a card to ask with card context.";
          }}
        }}

        function setMiruHelperOpen(isOpen) {{
          if (!miruHelperPanel || !miruHelperToggle) {{
            return;
          }}
          miruHelperPanel.hidden = !isOpen;
          miruHelperToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
        }}

        function openCardDetail(payload, trigger) {{
          lastTrigger = trigger || null;
          currentCardPayload = payload || null;
          syncMiruHelperLinks();
          syncWatchActionUi(payload || {{}});
          titleNode.textContent = payload.name || payload.code || "Card Details";
          subtitleNode.textContent = [payload.code, payload.set_name].filter(Boolean).join(" • ");
          mediaNode.innerHTML = "";
          const mediaStack = document.createElement("div");
          mediaStack.className = "detailMediaStack";
          const imageSourceLabel = (payload.image_source_label || "").trim();
          if (imageSourceLabel) {{
            const badge = document.createElement("div");
            badge.className = "detailImageBadge";
            badge.textContent = imageSourceLabel;
            mediaStack.appendChild(badge);
          }}
          if (payload.image_src) {{
            const image = document.createElement("img");
            image.className = "detailImage";
            image.decoding = "async";
            image.loading = "eager";
            image.fetchPriority = "high";
            const previewSrc = String(payload.image_thumb_src || "").trim();
            const detailSrc = String(payload.image_src || "").trim();
            image.src = previewSrc || detailSrc;
            image.alt = payload.name ? `${{payload.name}} card image` : "Card image";
            mediaStack.appendChild(image);
            if (previewSrc && detailSrc && previewSrc !== detailSrc) {{
              const fullImage = new Image();
              fullImage.decoding = "async";
              fullImage.loading = "eager";
              fullImage.fetchPriority = "high";
              fullImage.onload = () => {{
                image.src = detailSrc;
              }};
              fullImage.src = detailSrc;
            }}
          }} else {{
            const ph = document.createElement("div");
            ph.className = "detailImagePh";
            ph.textContent = "No card image available yet.";
            mediaStack.appendChild(ph);
          }}
          mediaNode.appendChild(mediaStack);

          statsNode.innerHTML = "";
          [
            ["Card Code", payload.code],
            ["Set Name", payload.set_name],
            ["Rarity", payload.rarity],
            ["Color", payload.color],
            ["Type", payload.card_type],
            ["Cost", payload.cost],
            ["Power", payload.power],
            ["Counter", payload.counter],
            ["Attribute", payload.attribute]
          ].forEach(([label, value]) => statsNode.appendChild(renderStat(label, value)));

          renderDetailText(effectNode, payload.effect_text, "No effect text recorded yet.");
          renderDetailText(triggerNode, payload.trigger_text, "No trigger text recorded.");

          if ((payload.market_url || "").trim()) {{
            marketLink.href = payload.market_url;
            marketLink.style.display = "inline-block";
          }} else {{
            marketLink.removeAttribute("href");
            marketLink.style.display = "none";
          }}

          modal.classList.add("open");
          modal.setAttribute("aria-hidden", "false");
          document.body.classList.add("modalOpen");
          closeBtn.focus();
        }}

        function closeCardDetail() {{
          modal.classList.remove("open");
          modal.setAttribute("aria-hidden", "true");
          document.body.classList.remove("modalOpen");
          if (lastTrigger) {{
            lastTrigger.focus();
          }}
        }}

        async function saveCurrentCardWatch() {{
          if (!currentCardPayload || !watchButton) {{
            return;
          }}
          const payload = currentCardPayload || {{}};
          const hadExistingTarget = Boolean(String(payload.target_price || "").trim());
          const targetPrice = targetPriceInput ? targetPriceInput.value.trim() : "";
          if (targetPrice && !normalizeMoneyInput(targetPrice)) {{
            setWatchFeedback("Enter a valid target price.", "error");
            if (targetPriceInput) {{
              targetPriceInput.focus();
            }}
            return;
          }}
          watchButton.disabled = true;
          setWatchFeedback("Saving watch target...");
          try {{
            const response = await fetch("/api/watchlist/add", {{
              method: "POST",
              headers: {{
                "Content-Type": "application/json",
              }},
              credentials: "same-origin",
              body: JSON.stringify({{
                code: payload.code || "",
                name: payload.name || "",
                current_price: payload.current_price || "",
                target_price: targetPrice,
                market_url: payload.market_url || "",
              }}),
            }});
            const result = await response.json().catch(() => ({{}}));
            if (!response.ok || !result.ok) {{
              throw new Error(String(result.error || "Could not save watchlist entry."));
            }}
            const entry = result.entry || {{}};
            currentCardPayload.current_price = String(entry.current_price || payload.current_price || "").trim();
            currentCardPayload.target_price = String(entry.target_price || "").trim();
            currentCardPayload.market_url = String(entry.market_url || payload.market_url || "").trim();
            syncWatchActionUi(currentCardPayload);
            updateCardPayloadsForCode(currentCardPayload.code, (item) => {{
              item.current_price = currentCardPayload.current_price;
              item.target_price = currentCardPayload.target_price;
              item.market_url = currentCardPayload.market_url;
            }});
            updateVisibleWatchlistRow(currentCardPayload.code, entry);
            const successMessage = currentCardPayload.target_price
              ? (hadExistingTarget ? "Watch price updated." : "Added to watchlist.")
              : "Saved to watchlist.";
            setWatchFeedback(String(result.message || successMessage), "success");
          }} catch (error) {{
            setWatchFeedback(error instanceof Error ? error.message : "Could not save watchlist entry.", "error");
          }} finally {{
            watchButton.disabled = false;
          }}
        }}

        function buildLibraryFragmentUrl(pageOverride) {{
          if (!libraryShell) {{
            return "";
          }}
          const baseUrl = new URL(libraryShell.dataset.libraryFragmentUrl || "", window.location.origin);
          if (libraryControls) {{
            const formData = new FormData(libraryControls);
            for (const [key, value] of formData.entries()) {{
              const normalized = String(value || "").trim();
              if (!normalized || (key === "sort" && normalized === "set_card_asc")) {{
                baseUrl.searchParams.delete(key);
              }} else {{
                baseUrl.searchParams.set(key, normalized);
              }}
            }}
          }}
          if (pageOverride) {{
            baseUrl.searchParams.set("library_page", String(pageOverride));
          }}
          return baseUrl.pathname + baseUrl.search;
        }}

        function syncLibraryHistory(fragmentUrl) {{
          if (!libraryShell || !window.history || typeof window.history.replaceState !== "function") {{
            return;
          }}
          const fragmentTarget = new URL(fragmentUrl, window.location.origin);
          const pageTarget = new URL(window.location.href);
          pageTarget.pathname = window.location.pathname;
          pageTarget.search = "";
          for (const [key, value] of fragmentTarget.searchParams.entries()) {{
            if (key === "mode") {{
              continue;
            }}
            pageTarget.searchParams.set(key, value);
          }}
          if ((pageTarget.searchParams.get("sort") || "") === "set_card_asc") {{
            pageTarget.searchParams.delete("sort");
          }}
          if ((pageTarget.searchParams.get("library_page") || "") === "1") {{
            pageTarget.searchParams.delete("library_page");
          }}
          window.history.replaceState(window.history.state, "", pageTarget.pathname + (pageTarget.search ? pageTarget.search : ""));
        }}

        async function loadLibraryFragment(fragmentUrl = "", {{ force = false }} = {{}}) {{
          if ((!force && (libraryLoaded || libraryLoadPromise)) || !libraryShell || !libraryDeferredContent) {{
            return libraryLoadPromise;
          }}
          const url = fragmentUrl || buildLibraryFragmentUrl();
          if (!url) {{
            return null;
          }}
          libraryLoaded = false;
          libraryLoadPromise = fetch(url, {{
            headers: {{
              "X-Requested-With": "project-miru-fragment",
            }},
            credentials: "same-origin",
          }})
            .then((response) => {{
              if (!response.ok) {{
                throw new Error(`Library fragment failed with ${{response.status}}`);
              }}
              return response.text();
            }})
            .then((html) => {{
              libraryDeferredContent.innerHTML = html;
              libraryLoaded = true;
              syncLibraryHistory(url);
              persistLibraryFragment(url, html);
            }})
            .catch(() => {{
              libraryDeferredContent.innerHTML = '<div class="card libraryDeferredState">Card library is unavailable right now. Reload to try again.</div>';
            }})
            .finally(() => {{
              libraryLoadPromise = null;
            }});
          return libraryLoadPromise;
        }}

        function scheduleLibraryLoad() {{
          if (!libraryShell || !libraryDeferredContent || libraryLoaded) {{
            return;
          }}
          if (window.location.pathname === libraryPagePath && restoreLibraryFragment()) {{
            return;
          }}
          if (libraryControls || window.location.hash === "#card-library") {{
            void loadLibraryFragment();
            return;
          }}
          if ("IntersectionObserver" in window) {{
            const observer = new IntersectionObserver((entries) => {{
              if (!entries.some((entry) => entry.isIntersecting)) {{
                return;
              }}
              observer.disconnect();
              void loadLibraryFragment();
            }}, {{ rootMargin: "480px 0px" }});
            observer.observe(libraryShell);
            return;
          }}
          window.setTimeout(() => {{
            void loadLibraryFragment();
          }}, 180);
        }}

        async function loadWatchlistFragment() {{
          if (!watchlistDeferred || !watchlistGrid || watchlistLoadPromise) {{
            return watchlistLoadPromise;
          }}
          const url = watchlistDeferred.dataset.watchlistFragmentUrl || "";
          if (!url) {{
            watchlistDeferred.remove();
            return null;
          }}
          watchlistLoadPromise = fetch(url, {{
            headers: {{
              "X-Requested-With": "project-miru-fragment",
            }},
            credentials: "same-origin",
          }})
            .then((response) => {{
              if (!response.ok) {{
                throw new Error(`Watchlist fragment failed with ${{response.status}}`);
              }}
              return response.text();
            }})
            .then((html) => {{
              const markup = String(html || "").trim();
              if (markup) {{
                watchlistGrid.insertAdjacentHTML("beforeend", markup);
              }}
              watchlistDeferred.remove();
            }})
            .catch(() => {{
              watchlistDeferred.innerHTML = '<div class="card libraryDeferredState">More tracked cards will load after refresh.</div>';
            }})
            .finally(() => {{
              watchlistLoadPromise = null;
            }});
          return watchlistLoadPromise;
        }}

        function scheduleWatchlistLoad() {{
          if (!watchlistDeferred || !watchlistGrid) {{
            return;
          }}
          const loadLater = () => {{
            void loadWatchlistFragment();
          }};
          if ("requestIdleCallback" in window) {{
            window.requestIdleCallback(loadLater, {{ timeout: 1200 }});
            return;
          }}
          window.setTimeout(loadLater, 140);
        }}

        document.addEventListener("click", (event) => {{
          const pagerLink = event.target instanceof HTMLElement
            ? event.target.closest(".libraryPagerLink[href]")
            : null;
          if (pagerLink && libraryControls && libraryShell) {{
            event.preventDefault();
            void loadLibraryFragment(pagerLink.getAttribute("href") || "", {{ force: true }});
            return;
          }}
          const target = event.target instanceof HTMLElement ? event.target : null;
          if (!target) {{
            return;
          }}
          const outboundLink = target.closest(".buybtn[href], #cardDetailMarketLink[href]");
          if (outboundLink) {{
            return;
          }}
          const openTarget = target.closest(".viewbtn, .libraryThumbButton, .libraryOpenBtn, .card[data-card], .libraryCard[data-card]");
          if (!openTarget) {{
            return;
          }}
          const payload = JSON.parse(openTarget.dataset.card || "{{}}");
          openCardDetail(payload, openTarget);
        }});

        if (libraryControls) {{
          libraryControls.addEventListener("input", (event) => {{
            const target = event.target instanceof HTMLElement ? event.target : null;
            if (!target) {{
              return;
            }}
            if (target.getAttribute("name") !== "q") {{
              return;
            }}
            window.clearTimeout(librarySearchTimer);
            librarySearchTimer = window.setTimeout(() => {{
              void loadLibraryFragment("", {{ force: true }});
            }}, 180);
          }});
          libraryControls.addEventListener("change", () => {{
            void loadLibraryFragment("", {{ force: true }});
          }});
          libraryControls.addEventListener("submit", (event) => {{
            event.preventDefault();
            void loadLibraryFragment("", {{ force: true }});
          }});
        }}

        if (miruHelperToggle) {{
          miruHelperToggle.addEventListener("click", () => {{
            setMiruHelperOpen(Boolean(miruHelperPanel && miruHelperPanel.hidden));
          }});
        }}

        if (watchButton) {{
          watchButton.addEventListener("click", () => {{
            void saveCurrentCardWatch();
          }});
        }}

        if (targetPriceInput) {{
          targetPriceInput.addEventListener("keydown", (event) => {{
            if (event.key !== "Enter") {{
              return;
            }}
            event.preventDefault();
            void saveCurrentCardWatch();
          }});
        }}

        document.addEventListener("click", (event) => {{
          const target = event.target instanceof HTMLElement ? event.target : null;
          if (!target || !miruHelperPanel || !miruHelperToggle) {{
            return;
          }}
          if (miruHelperPanel.contains(target) || miruHelperToggle.contains(target)) {{
            return;
          }}
          setMiruHelperOpen(false);
        }});

        closeBtn.addEventListener("click", closeCardDetail);
        modal.addEventListener("click", (event) => {{
          if (event.target === modal) {{
            closeCardDetail();
          }}
        }});
        document.addEventListener("keydown", (event) => {{
          if (event.key === "Escape" && miruHelperPanel && !miruHelperPanel.hidden) {{
            setMiruHelperOpen(false);
            return;
          }}
          if (event.key === "Escape" && modal.classList.contains("open")) {{
            closeCardDetail();
          }}
        }});
        syncMiruHelperLinks();
        scheduleWatchlistLoad();
        scheduleLibraryLoad();
      </script>
    </body>
    </html>
    """

    return Response(page_html, mimetype="text/html")


@app.get("/library-fragment")
def library_fragment():
    try:
        library_page = max(int(request.args.get("library_page", "1") or 1), 1)
    except Exception:
        library_page = 1
    mode = str(request.args.get("mode", "browse") or "browse").strip().lower()
    browse_mode = mode == "browse"
    base_path = "/library" if browse_mode else "/"
    filters = normalize_library_query(request.args)
    query_pairs = []
    for key in ("q", "set", "color", "rarity", "card_type", "cost", "attribute", "sort"):
        value = str(filters.get(key) or "").strip()
        if not value or (key == "sort" and value == "set_card_asc"):
            continue
        query_pairs.append((key, value))
    query_string = "".join(f"&{key}={quote(value)}" for key, value in query_pairs)
    catalog_cards = load_catalog_card_index()
    library_cards = filter_and_sort_library_cards(build_library_card_index(catalog_cards), filters)
    price_index = build_library_price_index(load_prices())
    for entry in library_cards:
        price_entry = price_index.get(entry["code"], {})
        entry["price_text"] = str(price_entry.get("price_text") or "")
    fragment_html = build_library_fragment_html(
        catalog_cards,
        library_cards,
        library_page,
        base_path=base_path,
        browse_mode=browse_mode,
        price_index=price_index,
        query_suffix=query_string,
    )
    return Response(fragment_html, mimetype="text/html")


@app.get("/watchlist-fragment")
def watchlist_fragment():
    try:
        offset = max(int(request.args.get("offset", HOMEPAGE_INITIAL_WATCHLIST_COUNT) or HOMEPAGE_INITIAL_WATCHLIST_COUNT), 0)
    except Exception:
        offset = HOMEPAGE_INITIAL_WATCHLIST_COUNT
    items = load_prices()
    catalog_cards = load_catalog_card_index()
    enriched = build_watchlist_entries(items, catalog_cards)
    fragment_html = build_watchlist_cards_html(enriched[offset:], catalog_cards) if offset < len(enriched) else ""
    return Response(fragment_html, mimetype="text/html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
