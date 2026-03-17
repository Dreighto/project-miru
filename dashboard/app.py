import os
import copy
import json
import re
import shutil
import sqlite3
import time
import html as html_lib
from pathlib import Path
from flask import Flask, Response, redirect, render_template, request, send_from_directory
from urllib.parse import quote

try:
    from PIL import Image, ImageFilter, ImageOps
except Exception:
    Image = None
    ImageFilter = None
    ImageOps = None

# Data paths: env overrides, then project-relative data/ (worktree-local), then Docker-style fallback
_DASHBOARD_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DASHBOARD_DIR.parent
_DEFAULT_PRICES_PATH = _PROJECT_ROOT / "data" / "prices.json"
_DEFAULT_CATALOG_DB_PATH = _PROJECT_ROOT / "data" / "card_catalog.db"

PRICES_PATH = (
    os.getenv("PROJECT_MIRU_PRICES_PATH")
    or os.getenv("PRICES_PATH")
    or str(_DEFAULT_PRICES_PATH)
    or "/data/prices.json"
)
CATALOG_DB_PATH = (
    os.getenv("PROJECT_MIRU_CATALOG_DB_PATH")
    or os.getenv("CATALOG_DB_PATH")
    or str(_DEFAULT_CATALOG_DB_PATH)
    or "/data/card_catalog.db"
)
CATALOG_DB_CACHE_PATH = "/tmp/project_miru_card_catalog.db"
IMAGES_ROOT = "/images"
THUMB_CACHE_ROOT = "/tmp/project_miru_thumbs"
THUMB_DEFAULT_WIDTH = 420
LIBRARY_PAGE_SIZE = 24
HOMEPAGE_INITIAL_WATCHLIST_COUNT = 8
# Primary insight type order: meta > price > synergy > lore (best first).
MIRU_PRIMARY_INSIGHT_TYPE_ORDER = ("meta", "price", "strength", "synergy", "lore")
MIRU_INSIGHT_ADDITIONAL_MAX = 3
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
MIRU_INTEL_DB_PATH = os.getenv("PROJECT_MIRU_INTEL_DB_PATH", "/data/miru_dossiers.db")
MIRU_INTEL_DB_CACHE_PATH = "/tmp/project_miru_intel_preview.db"
# Worktree deck intelligence (leader_card_signals, decklists)
_DEFAULT_DECK_INTEL_DB_PATH = _PROJECT_ROOT / "data" / "miru_deck_intel.db"
DECK_INTEL_DB_PATH = os.getenv("PROJECT_MIRU_DECK_INTEL_DB_PATH", str(_DEFAULT_DECK_INTEL_DB_PATH))
MIRU_AI_PORT = int(os.getenv("MIRU_AI_PORT", "8765"))
MIRU_PREVIEW_TONES = (
    {"value": "friendly", "label": "Friendly"},
    {"value": "neutral", "label": "Neutral"},
    {"value": "concise", "label": "Concise"},
)
MIRU_STORY_MODES = (
    {"value": "off", "label": "Off"},
    {"value": "light", "label": "Light"},
    {"value": "full", "label": "Full"},
)

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


def load_miru_insight_status():
    signature = path_signature(CATALOG_DB_PATH)

    def builder():
        try:
            conn = open_catalog_db()
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS insight_count,
                        MAX(updated_at) AS last_sync_time
                    FROM miru_card_insights
                    """
                ).fetchone()
            finally:
                conn.close()
        except Exception:
            return {
                "connected": False,
                "sync_running": False,
                "last_sync_time": "",
                "insight_count": 0,
                "db_health": {
                    "project_catalog_readable": os.path.isfile(CATALOG_DB_PATH),
                    "runtime_dossier_readable": os.path.isfile(MIRU_RUNTIME_DOSSIER_DB_PATH),
                },
            }
        return {
            "connected": bool(row and int(row["insight_count"] or 0) > 0),
            "sync_running": False,
            "last_sync_time": str((row["last_sync_time"] if row else "") or ""),
            "insight_count": int((row["insight_count"] if row else 0) or 0),
            "db_health": {
                "project_catalog_readable": os.path.isfile(CATALOG_DB_PATH),
                "runtime_dossier_readable": os.path.isfile(MIRU_RUNTIME_DOSSIER_DB_PATH),
            },
        }

    return get_ttl_cached_value("miru_insight_status", 5.0, builder, signature=signature)


def load_miru_card_insight(card_id: str):
    """Load prioritized primary + additional insights (best type first, no rotation)."""
    raw_card_id = str(card_id or "").strip().upper()
    code_match = CODE_RE.search(raw_card_id)
    canonical = (code_match.group(1).upper() if code_match else raw_card_id).strip().upper()
    if not canonical:
        return None
    signature = path_signature(CATALOG_DB_PATH)
    order = MIRU_PRIMARY_INSIGHT_TYPE_ORDER
    max_add = MIRU_INSIGHT_ADDITIONAL_MAX

    def builder():
        try:
            conn = open_catalog_db()
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT card_id, insight_type, insight_text, confidence, updated_at
                    FROM miru_card_insights
                    WHERE card_id = ?
                    """,
                    (canonical,),
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return None
        if not rows:
            return None
        items = [
            {
                "insight_text": str(r.get("insight_text") or "").strip(),
                "insight_type": str(r.get("insight_type") or "").strip(),
                "confidence": round(float(r.get("confidence") or 0.0), 2),
                "updated_at": str(r.get("updated_at") or ""),
            }
            for r in rows
            if str(r.get("insight_text") or "").strip()
        ]
        if not items:
            return None
        # Sort by type priority then confidence (best first).
        def key(i):
            t = i.get("insight_type") or ""
            idx = order.index(t) if t in order else len(order)
            return (idx, -i.get("confidence", 0))
        items.sort(key=key)
        primary = items[0]
        # Additional: different types only, limit count.
        seen_types = {primary.get("insight_type")}
        additional = []
        for i in items[1:]:
            if len(additional) >= max_add:
                break
            t = i.get("insight_type")
            if t and t not in seen_types:
                seen_types.add(t)
                additional.append(
                    {
                        "insight": i.get("insight_text", ""),
                        "type": t,
                        "confidence": i.get("confidence", 0),
                    }
                )
        return {
            "card_id": canonical,
            "insight": primary.get("insight_text", ""),
            "type": primary.get("insight_type", ""),
            "confidence": primary.get("confidence", 0),
            "updated_at": primary.get("updated_at", ""),
            "primary": {
                "insight": primary.get("insight_text", ""),
                "type": primary.get("insight_type", ""),
                "confidence": primary.get("confidence", 0),
                "updated_at": primary.get("updated_at", ""),
            },
            "additional": additional,
        }

    return get_ttl_cached_value(f"miru_insight:{canonical}", 5.0, builder, signature=signature)


def open_miru_intel_db():
    conn = None
    try:
        conn = sqlite3.connect(f"file:{MIRU_INTEL_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()
        return conn
    except sqlite3.OperationalError:
        try:
            conn.close()
        except Exception:
            pass
        if not os.path.isfile(MIRU_INTEL_DB_PATH):
            raise
        cache_dir = os.path.dirname(MIRU_INTEL_DB_CACHE_PATH)
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
        try:
            needs_copy = (
                not os.path.isfile(MIRU_INTEL_DB_CACHE_PATH)
                or os.path.getmtime(MIRU_INTEL_DB_CACHE_PATH) < os.path.getmtime(MIRU_INTEL_DB_PATH)
                or os.path.getsize(MIRU_INTEL_DB_CACHE_PATH) != os.path.getsize(MIRU_INTEL_DB_PATH)
            )
        except Exception:
            needs_copy = True
        if needs_copy:
            shutil.copy2(MIRU_INTEL_DB_PATH, MIRU_INTEL_DB_CACHE_PATH)
        conn = sqlite3.connect(MIRU_INTEL_DB_CACHE_PATH)
        conn.row_factory = sqlite3.Row
        return conn


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


def _variant_classification_alt_illust(code: str, variant: str, label_or_filename: str):
    """Use stored/inferred variant type for alt/illust slots so library retrieval is consistent."""
    try:
        import sys
        root = _PROJECT_ROOT
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tools.miru_print_variant import (
            get_classification_or_infer,
            set_variant_classification,
            PRINT_VARIANT_ALT_ART,
            PRINT_VARIANT_SPECIAL_ART,
            PRINT_VARIANT_ILLUSTRATOR_ART,
            PRINT_VARIANT_PARALLEL,
            PRINT_VARIANT_PROMO_VARIANT,
            PRINT_VARIANT_SP,
            PRINT_VARIANT_ANNIVERSARY,
            PRINT_VARIANT_SERIALIZED,
        )
        vtype = get_classification_or_infer(code, variant, label_or_filename)
        set_variant_classification(code, variant, vtype)
        is_illust = vtype == PRINT_VARIANT_ILLUSTRATOR_ART
        is_alt = vtype in (
            PRINT_VARIANT_ALT_ART,
            PRINT_VARIANT_SPECIAL_ART,
            PRINT_VARIANT_PARALLEL,
            PRINT_VARIANT_PROMO_VARIANT,
            PRINT_VARIANT_SP,
            PRINT_VARIANT_ANNIVERSARY,
            PRINT_VARIANT_SERIALIZED,
        )
        return is_alt, is_illust
    except Exception:
        return None, None


def build_image_index():
    """
    by_base:
      "P-093(ILLUSTRATIONBOXVOL.6)" -> ".../P-093(IllustrationBoxVol.6).png"
      "OP11-067(ALT)" -> ".../OP11-067(Alt).png"
      "OP11-067" -> ".../OP11-067.png"

    by_code:
      "OP11-067" -> {"normal": "...", "alt": "...", "illust": "...", "variants": {...}}
    Variant alt/illust slots use miru_print_variant classification when available.
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
                is_alt_cls, is_illust_cls = _variant_classification_alt_illust(code, variant, base)
                if is_alt_cls is not None and is_illust_cls is not None:
                    if is_illust_cls:
                        entry["illust"] = rel_path
                    if is_alt_cls:
                        entry["alt"] = rel_path
                else:
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


def get_all_card_image_sources(name: str, code: str, catalog_entry: dict, width: int = THUMB_DEFAULT_WIDTH):
    """
    Return a list of {"thumb_src", "detail_src"} for all available arts (normal + variants).
    Used for library alt-art switching; single element when no alternates.
    """
    idx_code = IMAGE_INDEX["by_code"]
    entry = idx_code.get((code or "").strip().upper()) if code else {}
    variants = (entry or {}).get("variants") or {}
    normal_path = (entry or {}).get("normal")
    alt_path = (entry or {}).get("alt")
    illust_path = (entry or {}).get("illust")

    # Runtime/miru best image: single source only (no alternates from local index).
    runtime_entry = choose_runtime_best_image(code)
    if runtime_entry:
        rel_path = str(runtime_entry.get("rel_path") or "").strip()
        if rel_path:
            return [
                {
                    "thumb_src": f"/miru-thumb/{rel_path}?w={int(width)}",
                    "detail_src": f"/miru-img/{rel_path}",
                }
            ]

    # Local by_code: build ordered list (normal, alt, illust, then rest of variants).
    if entry:
        seen = set()
        ordered_paths = []
        for path in (normal_path, alt_path, illust_path):
            if path and path not in seen:
                seen.add(path)
                ordered_paths.append(path)
        for vname in sorted(variants.keys()):
            path = variants[vname]
            if path and path not in seen:
                seen.add(path)
                ordered_paths.append(path)
        if ordered_paths:
            return [
                {
                    "thumb_src": f"/thumb/{p}?w={int(width)}",
                    "detail_src": f"/img/{p}",
                }
                for p in ordered_paths
            ]

    # Fallback: single source from resolve_card_image_sources.
    single = resolve_card_image_sources(name, code, catalog_entry, width=width)
    return [
        {
            "thumb_src": str(single.get("thumb_src") or ""),
            "detail_src": str(single.get("detail_src") or ""),
        }
    ]


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


LIBRARY_DEFAULT_SORT = "newest_set"


def normalize_library_query(args):
    filters = {
        "q": str(args.get("q", "") or "").strip(),
        "set": str(args.get("set", "") or "").strip(),
        "color": str(args.get("color", "") or "").strip(),
        "rarity": str(args.get("rarity", "") or "").strip(),
        "card_type": str(args.get("card_type", "") or "").strip(),
        "cost": str(args.get("cost", "") or "").strip(),
        "attribute": str(args.get("attribute", "") or "").strip(),
        "ban_status": str(args.get("ban_status", "") or "").strip().lower(),
        "block_number": str(args.get("block_number", "") or "").strip(),
        "sort": str(args.get("sort", LIBRARY_DEFAULT_SORT) or LIBRARY_DEFAULT_SORT).strip(),
    }
    if filters["sort"] not in {key for key, _label in SORT_OPTIONS}:
        filters["sort"] = LIBRARY_DEFAULT_SORT
    return filters


def build_library_filter_options(library_cards, legality_index=None):
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

    block_number_options = []
    if legality_index:
        seen_blocks = set()
        for entry in library_cards:
            code = str(entry.get("code") or "").strip().upper()
            block = str((legality_index.get(code) or {}).get("block_number") or "").strip()
            if block and block not in seen_blocks:
                seen_blocks.add(block)
                block_number_options.append(block)
        block_number_options.sort(key=lambda v: (int(v) if v.isdigit() else 999, v))

    return {
        "set": set_options,
        "color": unique_values("color"),
        "rarity": unique_values("rarity"),
        "card_type": unique_values("card_type"),
        "cost": sorted(unique_values("cost"), key=lambda value: (int(value) if str(value).isdigit() else 999, value)),
        "attribute": unique_values("attribute"),
        "block_number": block_number_options,
        "sort": [{"value": key, "label": label} for key, label in SORT_OPTIONS],
    }


def get_library_browse_context(library_filters, library_filter_options):
    """Return (browse_context_label, active_filter_count) for the library page."""
    f = library_filters or {}
    opts = library_filter_options or {}
    active = 0
    for key in ("set", "color", "rarity", "card_type", "cost", "attribute", "ban_status", "block_number"):
        if str(f.get(key) or "").strip():
            active += 1
    if str(f.get("sort") or "").strip() != LIBRARY_DEFAULT_SORT:
        active += 1

    if str(f.get("card_type") or "").strip().lower() == "leader":
        return "Leaders", active
    set_val = str(f.get("set") or "").strip()
    if set_val:
        set_label = set_val
        for o in opts.get("set") or []:
            if str(o.get("value") or "").strip() == set_val:
                set_label = str(o.get("label") or set_val).strip() or set_val
                break
        return f"Set: {set_label}", active
    if str(f.get("q") or "").strip():
        return "Search results", active
    sort_val = str(f.get("sort") or LIBRARY_DEFAULT_SORT).strip()
    for o in opts.get("sort") or []:
        if str(o.get("value") or "").strip() == sort_val:
            return str(o.get("label") or "All cards"), active
    return "Newest set first" if sort_val == LIBRARY_DEFAULT_SORT else "All cards", active


def filter_and_sort_library_cards(library_cards, filters, legality_index=None):
    query = str(filters.get("q") or "").strip().lower()
    set_filter = str(filters.get("set") or "").strip().lower()
    color_filter = str(filters.get("color") or "").strip().lower()
    rarity_filter = str(filters.get("rarity") or "").strip().lower()
    type_filter = str(filters.get("card_type") or "").strip().lower()
    cost_filter = str(filters.get("cost") or "").strip().lower()
    attribute_filter = str(filters.get("attribute") or "").strip().lower()
    ban_status_filter = str(filters.get("ban_status") or "").strip().lower()
    block_number_filter = str(filters.get("block_number") or "").strip()
    sort_key_name = str(filters.get("sort") or LIBRARY_DEFAULT_SORT).strip()

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
        if ban_status_filter or block_number_filter:
            code = str(entry.get("code") or "").strip().upper()
            card_legality = (legality_index or {}).get(code, {})
            if ban_status_filter:
                card_ban = str(card_legality.get("ban_status") or "").strip().lower()
                if ban_status_filter == "normal":
                    if card_ban in ("banned", "restricted"):
                        continue
                elif card_ban != ban_status_filter:
                    continue
            if block_number_filter:
                card_block = str(card_legality.get("block_number") or "").strip()
                if card_block != block_number_filter:
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


def build_legality_index():
    """Load all stored legality facts from the dossier DB, keyed by canonical card code.

    Returns {code: {ban_status, restriction_count, block_number}} for cards with any
    stored legality fact. Returns an empty dict if the dossier DB is unavailable.
    Result is TTL-cached for 30 s, consistent with the catalog index.
    """
    signature = path_signature(MIRU_INTEL_DB_PATH)

    def _build():
        if not os.path.isfile(MIRU_INTEL_DB_PATH):
            return {}
        try:
            conn = sqlite3.connect(f"file:{MIRU_INTEL_DB_PATH}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT c.canonical_code, cf.field_name, cf.value_text
                    FROM card_facts cf
                    JOIN cards c ON c.id = cf.card_id
                    WHERE cf.field_name IN ('block_number', 'ban_status', 'restriction_count')
                      AND cf.verification_state != 'missing'
                      AND cf.value_text IS NOT NULL
                      AND cf.value_text != ''
                    """
                ).fetchall()
            finally:
                conn.close()
            index = {}
            for row in rows:
                code = str(row["canonical_code"] or "").strip().upper()
                if not code:
                    continue
                if code not in index:
                    index[code] = {}
                index[code][str(row["field_name"])] = str(row["value_text"] or "").strip()
            return index
        except Exception:
            return {}

    return get_ttl_cached_value("legality_index", 30.0, _build, signature=signature)


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
        return True, pct, f"▼ {pct:.0f}% under target", tier
    else:
        tier = "over"
        return False, pct, f"▲ {abs(pct):.0f}% above target", tier


@app.get("/catalog")
def catalog():
    """Alias: catalog is the library view served at /. Do not replace with .codex_catalog_snapshot.html or old OP Miru Catalog."""
    return redirect("/library", code=302)


@app.post("/api/miru-preview")
def api_miru_preview():
    payload = request.get_json(silent=True) or request.form or {}
    query = str(payload.get("query") or "").strip()
    tone = str(payload.get("tone") or "neutral").strip().lower()
    story_mode = str(payload.get("story_mode") or "off").strip().lower()
    response_payload = build_miru_preview_payload(query, tone=tone, story_mode=story_mode)
    return Response(json.dumps(response_payload), mimetype="application/json")


@app.get("/api/miru/status")
def api_miru_status():
    return Response(json.dumps(load_miru_insight_status()), mimetype="application/json")


@app.get("/api/miru/insight/<card_id>")
def api_miru_insight(card_id: str):
    payload = load_miru_card_insight(card_id)
    if payload is None:
        return Response(
            json.dumps(
                {
                    "card_id": str(card_id or "").strip().upper(),
                    "insight": "",
                    "type": "",
                    "confidence": 0.0,
                    "error": "No Miru insight is stored for this card yet.",
                }
            ),
            status=404,
            mimetype="application/json",
        )
    return Response(json.dumps(payload), mimetype="application/json")


@app.get("/api/miru/insights/<card_id>")
def api_miru_insights(card_id: str):
    """Return all stored Miru insights for a card, ordered by confidence."""
    raw_card_id = str(card_id or "").strip().upper()
    code_match = CODE_RE.search(raw_card_id)
    canonical = (code_match.group(1).upper() if code_match else raw_card_id).strip().upper()
    if not canonical:
        return Response(
            json.dumps({"card_id": "", "insights": [], "error": "Card code required."}),
            status=400,
            mimetype="application/json",
        )
    signature = path_signature(CATALOG_DB_PATH)

    def builder():
        try:
            conn = open_catalog_db()
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT card_id, insight_type, insight_text, confidence, updated_at
                    FROM miru_card_insights
                    WHERE card_id = ?
                    ORDER BY confidence DESC, updated_at DESC, insight_type ASC
                    """,
                    (canonical,),
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return None
        if not rows:
            return None
        return {
            "card_id": canonical,
            "insights": [
                {
                    "insight": str(row["insight_text"] or ""),
                    "type": str(row["insight_type"] or ""),
                    "confidence": round(float(row["confidence"] or 0.0), 2),
                    "updated_at": str(row["updated_at"] or ""),
                }
                for row in rows
                if str(row["insight_text"] or "").strip()
            ],
        }

    result = get_ttl_cached_value(f"miru_insights_all:{canonical}", 5.0, builder, signature=signature)
    if result is None or not (result.get("insights") or []):
        return Response(
            json.dumps({
                "card_id": canonical,
                "insights": [],
                "error": "No Miru insight is stored for this card yet.",
            }),
            status=404,
            mimetype="application/json",
        )
    return Response(json.dumps(result), mimetype="application/json")


@app.get("/api/miru/legality/<card_id>")
def api_miru_legality(card_id: str):
    """Return stored legality facts (block_number, ban_status, restriction_count) for a card."""
    raw_code = str(card_id or "").strip().upper()
    code_match = CODE_RE.search(raw_code)
    canonical = (code_match.group(1).upper() if code_match else raw_code).strip().upper()
    if not canonical:
        return Response(
            json.dumps({"card_id": "", "error": "Card code required."}),
            status=400,
            mimetype="application/json",
        )
    result = {"card_id": canonical, "block_number": "", "ban_status": "", "restriction_count": ""}
    try:
        if os.path.isfile(MIRU_INTEL_DB_PATH):
            conn = sqlite3.connect(f"file:{MIRU_INTEL_DB_PATH}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT cf.field_name, cf.value_text
                    FROM card_facts cf
                    JOIN cards c ON c.id = cf.card_id
                    WHERE c.canonical_code = ?
                      AND cf.field_name IN ('block_number', 'ban_status', 'restriction_count')
                      AND cf.verification_state != 'missing'
                      AND cf.value_text IS NOT NULL
                      AND cf.value_text != ''
                    """,
                    (canonical,),
                ).fetchall()
            finally:
                conn.close()
            for row in rows:
                fname = str(row["field_name"] or "")
                if fname in result:
                    result[fname] = str(row["value_text"] or "").strip()
    except Exception:
        pass
    return Response(json.dumps(result), mimetype="application/json")


def _leader_confidence_label(decks_sampled: int) -> str:
    """Derive confidence from deck sample size (match tools/miru_ai_server thresholds)."""
    if decks_sampled < 5:
        return "low"
    if decks_sampled <= 14:
        return "medium"
    return "strong"


def load_leader_deck_intel(leader_code):
    """
    Read-only load of core/flex cards, decks_sampled, and summary fields for a leader from
    worktree data/miru_deck_intel.db (leader_card_signals, decklists, archetype_profiles).
    Returns dict with core_cards, flex_cards, decks_sampled, confidence_label,
    archetype_count, dominant_archetype_id. Returns empty result if DB missing or no data.
    """
    code = str(leader_code or "").strip().upper()
    if not code:
        return None
    empty = {
        "core_cards": [],
        "flex_cards": [],
        "decks_sampled": 0,
        "confidence_label": "",
        "archetype_count": 0,
        "dominant_archetype_id": "",
        "variants": [],
    }
    if not os.path.isfile(DECK_INTEL_DB_PATH):
        return empty
    try:
        conn = sqlite3.connect(f"file:{DECK_INTEL_DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('leader_card_signals','decklists','archetype_profiles','archetype_profile_cards')"
            ).fetchall()}
            if "leader_card_signals" not in tables:
                return empty
            format_code = ""
            rows = conn.execute(
                """
                SELECT card_code, usage_percent, avg_copies, deck_count, role_label
                FROM leader_card_signals
                WHERE leader_code = ? AND COALESCE(NULLIF(TRIM(format_code), ''), '') = ?
                ORDER BY role_label, usage_percent DESC, card_code
                """,
                (code, format_code),
            ).fetchall()
            core_cards = []
            flex_cards = []
            for r in rows:
                role = (r["role_label"] or "").strip().lower()
                entry = {
                    "card_code": str(r["card_code"] or "").strip(),
                    "usage_percent": round(float(r["usage_percent"] or 0), 4),
                    "avg_copies": round(float(r["avg_copies"] or 0), 2),
                    "deck_count": int(r["deck_count"] or 0),
                }
                if role == "core":
                    core_cards.append(entry)
                elif role == "flex":
                    flex_cards.append(entry)
            decks_sampled = 0
            if "decklists" in tables:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT deck_uid) AS n FROM decklists WHERE leader_code = ?",
                    (code,),
                ).fetchone()
                if row and row["n"] is not None:
                    decks_sampled = int(row["n"])
            confidence_label = _leader_confidence_label(decks_sampled) if decks_sampled else ""
            archetype_count = 0
            dominant_archetype_id = ""
            variants = []
            if "archetype_profiles" in tables:
                arch_rows = conn.execute(
                    """
                    SELECT archetype_id, deck_count
                    FROM archetype_profiles
                    WHERE leader_code = ? AND COALESCE(NULLIF(TRIM(format_code), ''), '') = ?
                    ORDER BY deck_count DESC, archetype_id ASC
                    """,
                    (code, format_code),
                ).fetchall()
                archetype_count = len(arch_rows)
                if arch_rows:
                    dominant_archetype_id = str(arch_rows[0]["archetype_id"] or "").strip()
                if "archetype_profile_cards" in tables and arch_rows:
                    max_cards_per_variant = 8
                    for arch in arch_rows:
                        aid = str(arch["archetype_id"] or "").strip()
                        deck_count = int(arch["deck_count"] or 0)
                        card_rows = conn.execute(
                            """
                            SELECT card_code, role_label
                            FROM archetype_profile_cards
                            WHERE leader_code = ? AND COALESCE(NULLIF(TRIM(format_code), ''), '') = ?
                              AND archetype_id = ?
                            ORDER BY deck_count DESC, total_copies DESC, card_code ASC
                            LIMIT ?
                            """,
                            (code, format_code, aid, max_cards_per_variant),
                        ).fetchall()
                        cards = [
                            {"card_code": str(r["card_code"] or "").strip(), "role_label": str(r["role_label"] or "").strip()}
                            for r in card_rows
                            if (r["card_code"] or "").strip()
                        ]
                        variants.append({"archetype_id": aid, "deck_count": deck_count, "cards": cards})
            return {
                "core_cards": core_cards,
                "flex_cards": flex_cards,
                "decks_sampled": decks_sampled,
                "confidence_label": confidence_label,
                "archetype_count": archetype_count,
                "dominant_archetype_id": dominant_archetype_id,
                "variants": variants,
            }
        finally:
            conn.close()
    except Exception:
        return empty


@app.get("/api/leader-deck-intel/<leader_code>")
def api_leader_deck_intel(leader_code: str):
    """Return core/flex cards and decks_sampled for a leader from miru_deck_intel.db (read-only)."""
    code = str(leader_code or "").strip().upper()
    if not code:
        return Response(
            json.dumps({"error": "Leader code required."}),
            status=400,
            mimetype="application/json",
        )
    result = load_leader_deck_intel(code)
    if result is None:
        return Response(
            json.dumps({"error": "Leader code required."}),
            status=400,
            mimetype="application/json",
        )
    return Response(json.dumps(result), mimetype="application/json")


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
            all_sources = get_all_card_image_sources(title_name, code, catalog_entry)
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
                    "image_sources": all_sources,
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
            all_sources = get_all_card_image_sources(code, code, {})
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
                    "image_sources": all_sources,
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
        "image_sources": entry.get("image_sources") or [],
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
        # Data kept for search/filter/modal/Miru; only display is simplified (no pills, no set on tile)
        is_leader = str(entry.get("card_type") or "").strip().lower() == "leader"
        tile_class = f"{card_class} libraryCard--leader" if is_leader else card_class
        alt_sources = entry.get("image_sources") or []
        has_alt = len(alt_sources) > 1
        alt_sources_attr = html_lib.escape(json.dumps(alt_sources), quote=True) if has_alt else ""
        alt_control_html = ""
        if has_alt:
            n = len(alt_sources)
            alt_control_html = f'<button type="button" class="libraryAltControl" aria-label="Switch art (1 of {n})" data-alt-index="0">{html_lib.escape(str(1))}/{n}</button>'
        article_attrs = f'data-card="{detail_payload_attr}"'
        if has_alt:
            article_attrs += f' data-alt-sources="{alt_sources_attr}" data-alt-index="0"'
        # Flip wrapper for variant animation (double-tap to flip when multiple arts)
        thumb_inner = f'<div class="libraryThumbFlip" role="presentation">{media_html}</div>' if entry["thumb_src"] else media_html
        # Grid tile: image only (no pill overlays); below: card name + code only
        library_html.append(
            f"""
            <article class="{tile_class}" {article_attrs} tabindex="0" role="button">
              <div class="libraryThumbWrap">
                {thumb_inner}
                {alt_control_html}
              </div>
              <div class="{body_class}">
                <h3 class="{title_class}">{html_lib.escape(entry['title_name'])}</h3>
                <div class="libraryCardCodeLine">{html_lib.escape(entry['code'])}</div>
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
        progress_pct = 0
        progress_label = "Watching"
        if price_f and target_f and price_f > 0 and target_f > 0:
            if price_f <= target_f:
                progress_pct = 100
                progress_label = "Target hit"
            else:
                gap_ratio = min(abs(price_f - target_f) / max(price_f, target_f), 1.0)
                progress_pct = int(round((1.0 - gap_ratio) * 100))
                progress_label = f"{progress_pct}% to target"

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
                "progress_pct": progress_pct,
                "progress_label": progress_label,
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
        secondary_line = subtitle or "Watchlist"
        rail_detail = entry["pct_label"] or (f'Target {entry["target_txt"]}' if entry["target_txt"] else "Watching")
        thumb_src = str(image_sources.get("thumb_src") or "")
        img_tag = (
            f'<img class="watchTileImage" src="{thumb_src}" loading="lazy" decoding="async" fetchpriority="low" alt="">'
            if thumb_src
            else '<div class="watchTileImage watchTileImage--empty">No image</div>'
        )
        card_class = f'watchTile {entry["tier"]}' if entry["tier"] else "watchTile"
        current_price = entry["price_txt"] or "—"
        target_price = entry["target_txt"] or "—"
        progress_width = max(0, min(int(entry.get("progress_pct") or 0), 100))
        market_url = str(item.get("url", "") or "").strip()
        buy_link_html = (
            f'<a class="watchTileBuyLink" href="{html_lib.escape(market_url)}"'
            f' target="_blank" rel="noopener noreferrer"'
            f' onclick="event.stopPropagation()" tabindex="-1"'
            f' aria-label="Open on TCGplayer">TCGplayer ↗</a>'
            if market_url
            else '<span class="watchTileBuyLink watchTileBuyLink--none">TCGplayer</span>'
        )
        cards_html.append(
            f"""
            <article class="{card_class}" data-card="{detail_payload_attr}" tabindex="0" role="button" title="Last checked {fmt_time(item.get("last_checked_ts", 0))}">
              <div class="watchTileMedia">
                {img_tag}
              </div>
              <div class="watchTileBody">
                <div class="watchTileMain">
                  <div class="watchTileTopRow">
                    <span class="watchTileCode">{code or "WATCH"}</span>
                    <span class="watchTileSignal">{html_lib.escape(rail_detail)}</span>
                  </div>
                  <div class="watchTileTitle">{html_lib.escape(title_name)}</div>
                  <div class="watchTileMeter" aria-label="{html_lib.escape(entry['progress_label'])}">
                    <div class="watchTileMeterTrack">
                      <div class="watchTileMeterFill" style="width:{progress_width}%"></div>
                    </div>
                  </div>
                </div>
                <div class="watchTileSide">
                  <div class="watchTilePriceBlock">
                    <span class="watchTilePriceLabel">Now</span>
                    <strong class="watchTilePriceValue">{current_price}</strong>
                  </div>
                  <div class="watchTilePriceBlock">
                    <span class="watchTilePriceLabel">Target</span>
                    <strong class="watchTilePriceValue">{target_price}</strong>
                  </div>
                  {buy_link_html}
                </div>
              </div>
            </article>
            """
        )
    return "".join(cards_html) if cards_html else '<div class="watchEmpty">Your Watchlist – track cards you want to buy and get alerts when they reach your target price.</div>'


def normalize_lookup_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def build_catalog_name_lookup(catalog_cards):
    signature = path_signature(CATALOG_DB_PATH)

    def builder():
        lookup = {}
        for code, entry in (catalog_cards or {}).items():
            names = {
                clean_display_name(str(entry.get("card_name") or ""), code),
                str(entry.get("card_name") or "").strip(),
            }
            for name in names:
                normalized = normalize_lookup_text(name)
                if normalized and normalized not in lookup:
                    lookup[normalized] = str(code or "").strip().upper()
        return lookup

    return get_ttl_cached_value("catalog_name_lookup", 30.0, builder, signature=signature)


def find_catalog_match(query: str, catalog_cards):
    query_text = str(query or "").strip()
    if not query_text:
        return None
    code_match = CODE_RE.search(query_text)
    if code_match:
        code = code_match.group(1).upper()
        entry = dict(catalog_cards.get(code) or {})
        return {
            "code": code,
            "entry": entry,
            "match_type": "Exact card code",
        } if entry else None

    normalized_query = normalize_lookup_text(query_text)
    if not normalized_query:
        return None
    name_lookup = build_catalog_name_lookup(catalog_cards)
    exact_code = str(name_lookup.get(normalized_query) or "").strip().upper()
    if exact_code:
        return {
            "code": exact_code,
            "entry": dict(catalog_cards.get(exact_code) or {}),
            "match_type": "Exact card name",
        }

    best_code = ""
    best_entry = {}
    for code, entry in (catalog_cards or {}).items():
        title_name = clean_display_name(str(entry.get("card_name") or ""), code)
        haystack = normalize_lookup_text(title_name)
        if not haystack:
            continue
        if haystack.startswith(normalized_query) or normalized_query in haystack:
            best_code = str(code or "").strip().upper()
            best_entry = dict(entry or {})
            break
    if best_code:
        return {
            "code": best_code,
            "entry": best_entry,
            "match_type": "Catalog name match",
        }
    return None


def load_miru_dossier_summary(canonical_code: str):
    code = str(canonical_code or "").strip().upper()
    if not code:
        return None
    if not os.path.isfile(MIRU_INTEL_DB_PATH):
        return None
    try:
        conn = open_miru_intel_db()
    except sqlite3.Error:
        return None
    try:
        card = conn.execute(
            """
            SELECT canonical_code, set_code, set_name, card_name, rarity, color, card_type,
                   official_text, image_identity, overall_state, overall_score, last_checked_at
            FROM cards
            WHERE canonical_code = ?
            """,
            (code,),
        ).fetchone()
        if not card:
            return None
        facts = []
        for row in conn.execute(
            """
            SELECT field_name, value_text, value_json, value_type, verification_state,
                   confidence_score, supporting_source_count
            FROM card_facts
            WHERE card_id = (SELECT id FROM cards WHERE canonical_code = ?)
            ORDER BY field_name
            """,
            (code,),
        ).fetchall():
            facts.append(
                {
                    "field_name": row["field_name"],
                    "value_text": row["value_text"] or "",
                    "value_json": row["value_json"] or "",
                    "value_type": row["value_type"] or "text",
                    "verification_state": row["verification_state"] or "missing",
                    "confidence_score": float(row["confidence_score"] or 0.0),
                    "supporting_source_count": int(row["supporting_source_count"] or 0),
                }
            )
        selected_sources = conn.execute(
            """
            SELECT fs.source_key, fs.source_title, fs.trust_label
            FROM fact_sources fs
            JOIN card_facts cf ON cf.id = fs.fact_id
            WHERE cf.card_id = (SELECT id FROM cards WHERE canonical_code = ?)
              AND fs.is_selected = 1
            ORDER BY fs.trust_tier ASC, fs.source_weight DESC, fs.id ASC
            LIMIT 4
            """,
            (code,),
        ).fetchall()
    finally:
        conn.close()

    fact_lookup = {item["field_name"]: item for item in facts}

    def fact_text(field_name: str) -> str:
        item = fact_lookup.get(field_name) or {}
        if item.get("value_type") == "json":
            raw = str(item.get("value_json") or "").strip()
            if not raw:
                return ""
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return ", ".join(str(part).strip() for part in parsed if str(part).strip())
                if isinstance(parsed, dict):
                    return ", ".join(f"{key}: {value}" for key, value in parsed.items())
                return str(parsed)
            except Exception:
                return raw
        return str(item.get("value_text") or "").strip()

    verified_fields = [item["field_name"] for item in facts if item["verification_state"] == "verified"]
    likely_fields = [item["field_name"] for item in facts if item["verification_state"] == "likely"]
    return {
        "code": card["canonical_code"],
        "name": card["card_name"] or "",
        "set_code": card["set_code"] or "",
        "set_name": card["set_name"] or "",
        "rarity": card["rarity"] or "",
        "color": card["color"] or "",
        "card_type": card["card_type"] or "",
        "official_text": card["official_text"] or "",
        "image_identity": card["image_identity"] or "",
        "overall_state": card["overall_state"] or "missing",
        "overall_score": float(card["overall_score"] or 0.0),
        "last_checked_at": card["last_checked_at"] or "",
        "facts": facts,
        "verified_fields": verified_fields,
        "likely_fields": likely_fields,
        "effect_text": fact_text("effect_text"),
        "trigger_text": fact_text("trigger_text"),
        "cost": fact_text("cost"),
        "power": fact_text("power"),
        "counter": fact_text("counter"),
        "attribute": fact_text("attribute"),
        "traits": fact_text("traits"),
        "sources": [
            {
                "source_key": row["source_key"] or "",
                "source_title": row["source_title"] or "",
                "trust_label": row["trust_label"] or "",
            }
            for row in selected_sources
        ],
    }


def classify_miru_preview_intent(query: str, *, card_type: str = ""):
    normalized = str(query or "").strip().lower()
    normalized_card_type = str(card_type or "").strip().lower()
    if "leader" in normalized or normalized_card_type == "leader":
        return {"key": "leader_hub", "label": "Leader hub", "detail": "Leader-focused planning surface with hub scaffolding."}
    if any(token in normalized for token in ("variant", "print", "parallel", "manga", "alt art", "alternate art", "foil")):
        return {"key": "print_check", "label": "Print and variant", "detail": "Compare versions, variants, and print notes."}
    if any(token in normalized for token in ("effect", "trigger", "counter", "power", "cost", "ability", "does")):
        return {"key": "card_effects", "label": "Card effects", "detail": "Read effect text and grounded gameplay-facing facts."}
    if re.search(r"\b(?:op|eb|st|prb)\d{2}\b", normalized):
        return {"key": "set_context", "label": "Set context", "detail": "Use set-level context when the query is broader than one card."}
    return {"key": "card_lookup", "label": "Card lookup", "detail": "Resolve the cleanest verified card facts first."}


def build_story_note(*, story_mode: str, query: str, card: dict | None = None, set_name: str = "") -> str:
    mode = str(story_mode or "off").strip().lower()
    if mode == "off":
        return ""
    card = card or {}
    card_name = str(card.get("name") or "").strip()
    card_type = str(card.get("card_type") or "").strip().lower()
    set_label = str(set_name or card.get("set_name") or "").strip()
    if mode == "light":
        if card_type == "leader":
            return "Leader notes stay spoiler-light here and focus on identity, not plot."
        if set_label:
            return f"This card sits in {set_label}. Story notes stay secondary and spoiler-light in this preview."
        return "Story notes are available, but this preview is keeping them spoiler-light."
    if card_type == "leader":
        return f"{card_name or 'This leader'} reads best as a deck anchor first; any story note stays clearly secondary to verified card facts."
    if set_label:
        return f"{card_name or 'This card'} ties back to {set_label}. Full story notes can add atmosphere, but this preview still keeps verified facts first."
    return "Full story mode is on, but this preview only adds light atmosphere when a clear card or set anchor is present."


def tone_lead_text(tone: str) -> str:
    normalized = str(tone or "neutral").strip().lower()
    if normalized == "friendly":
        return "Here is the clean read."
    if normalized == "concise":
        return "Quick read."
    return "Read-only preview."


def build_miru_preview_payload(query: str, *, tone: str = "neutral", story_mode: str = "off"):
    query_text = str(query or "").strip()
    safe_tone = str(tone or "neutral").strip().lower()
    if safe_tone not in {item["value"] for item in MIRU_PREVIEW_TONES}:
        safe_tone = "neutral"
    safe_story_mode = str(story_mode or "off").strip().lower()
    if safe_story_mode not in {item["value"] for item in MIRU_STORY_MODES}:
        safe_story_mode = "off"

    catalog_cards = load_catalog_card_index()
    card_match = find_catalog_match(query_text, catalog_cards)
    matched_entry = dict((card_match or {}).get("entry") or {})
    matched_code = str((card_match or {}).get("code") or "").strip().upper()
    dossier = load_miru_dossier_summary(matched_code) if matched_code else None
    set_code_match = re.search(r"\b((?:OP|EB|ST|PRB)\d{2})\b", query_text, re.I)
    set_code = set_code_match.group(1).upper() if set_code_match else ""
    intent = classify_miru_preview_intent(query_text, card_type=str((dossier or matched_entry).get("card_type") or ""))

    if dossier:
        source_label = "Verified dossier cache" if dossier["overall_state"] == "verified" else "Dossier reference layer"
        resolution_label = f"{card_match.get('match_type', 'Card match')} via dossier"
        truth_label = "Verified" if dossier["overall_state"] == "verified" else "Reference only"
        truth_tone = "verified" if dossier["overall_state"] == "verified" else "reference"
        bundle_label = "Verified card dossier" if intent["key"] != "leader_hub" else "Leader hub scaffold"
        card_name = dossier["name"] or matched_entry.get("card_name") or matched_code
        set_name = dossier["set_name"] or matched_entry.get("set_name") or ""
        primary_bits = [bit for bit in [dossier.get("color"), dossier.get("card_type")] if bit]
        stat_bits = [bit for bit in [dossier.get("cost"), dossier.get("power"), dossier.get("counter"), dossier.get("attribute")] if bit]
        response_lines = [f"{tone_lead_text(safe_tone)} {card_name} ({matched_code}) is tracked in Miru's {truth_label.lower()} card layer."]
        if primary_bits:
            response_lines.append(f"Known profile: {' '.join(primary_bits)}.")
        if stat_bits:
            response_lines.append(f"Key facts: {', '.join(stat_bits)}.")
        effect_text = str(dossier.get("effect_text") or "").strip()
        trigger_text = str(dossier.get("trigger_text") or "").strip()
        if intent["key"] in {"card_effects", "leader_hub"} and effect_text:
            response_lines.append(f"Effect: {effect_text}")
        elif effect_text and safe_tone != "concise":
            response_lines.append(f"Verified effect text is available for this card.")
        if trigger_text and intent["key"] == "card_effects":
            response_lines.append(f"Trigger: {trigger_text}")
        if intent["key"] == "leader_hub":
            response_lines.append("Leader hub note: deck, flex, and meta rows stay read-only until linked deck intel is attached.")
        status_detail = (
            f"{len(dossier.get('verified_fields') or [])} verified field(s)"
            if truth_label == "Verified"
            else f"{len(dossier.get('likely_fields') or [])} likely field(s) and verified fields are still limited"
        )
    elif matched_entry:
        source_label = "Catalog card sheet"
        resolution_label = card_match.get("match_type", "Catalog card match")
        truth_label = "Reference only"
        truth_tone = "reference"
        bundle_label = "Catalog reference sheet" if intent["key"] != "leader_hub" else "Leader hub scaffold"
        card_name = clean_display_name(str(matched_entry.get("card_name") or matched_code), matched_code) or matched_code
        set_name = str(matched_entry.get("set_name") or "").strip()
        effect_text = str(matched_entry.get("effect_text") or "").strip()
        trigger_text = str(matched_entry.get("trigger_text") or "").strip()
        response_lines = [f"{tone_lead_text(safe_tone)} {card_name} ({matched_code}) matched the local catalog, but this preview does not have a verified dossier for it yet."]
        summary_bits = [bit for bit in [matched_entry.get("color"), matched_entry.get("card_type"), matched_entry.get("rarity")] if str(bit or "").strip()]
        if summary_bits:
            response_lines.append(f"Reference profile: {', '.join(str(bit).strip() for bit in summary_bits)}.")
        if effect_text and intent["key"] in {"card_effects", "leader_hub"}:
            response_lines.append(f"Catalog effect text: {effect_text}")
        elif effect_text:
            response_lines.append("Catalog effect text is available, but it is being treated as reference-only in this preview.")
        if trigger_text and intent["key"] == "card_effects":
            response_lines.append(f"Catalog trigger: {trigger_text}")
        if intent["key"] == "leader_hub":
            response_lines.append("Leader hub note: this page is ready for playstyle, core cards, flex slots, and meta notes once linked deck data is available.")
        status_detail = "This match is coming from the local catalog layer only."
    elif set_code:
        set_cards = [
            entry for code, entry in catalog_cards.items()
            if str(code or "").strip().upper().startswith(f"{set_code}-")
        ]
        set_name = str(set_cards[0].get("set_name") or "").strip() if set_cards else ""
        source_label = "Catalog set summary"
        resolution_label = "Set-code fast path"
        truth_label = "Reference only"
        truth_tone = "reference"
        bundle_label = "Set context"
        response_lines = [
            f"{tone_lead_text(safe_tone)} {set_code} matched the catalog set layer.",
            f"Project Miru currently sees {len(set_cards)} catalog card(s) in this set.",
            "Use an exact card code if you want a tighter verified card preview.",
        ]
        status_detail = "Set summaries stay reference-only until they resolve to a verified card dossier."
    else:
        set_name = ""
        source_label = "No fast path"
        resolution_label = "No exact card or set match"
        truth_label = "Awaiting match"
        truth_tone = "empty"
        bundle_label = "Guided card lookup"
        response_lines = [
            f"{tone_lead_text(safe_tone)} I could not match a verified card or exact set from that query yet.",
            "Try an exact card code like OP01-001, a leader name, or a variant prompt like 'OP01-001 alt art'.",
        ]
        status_detail = "Miru keeps this conservative and does not invent a card match."

    story_note = build_story_note(
        story_mode=safe_story_mode,
        query=query_text,
        card={
            "name": (dossier or {}).get("name") or clean_display_name(str((matched_entry or {}).get("card_name") or ""), matched_code),
            "card_type": (dossier or {}).get("card_type") or (matched_entry or {}).get("card_type") or "",
            "set_name": set_name,
        },
        set_name=set_name,
    )
    if story_note:
        response_lines.append(f"Story note: {story_note}")

    return {
        "ok": True,
        "query": query_text,
        "intent": intent,
        "fact_bundle": {
            "label": bundle_label,
            "detail": "Read-only bundle selection for UI testing. Verified and reference layers stay distinct.",
        },
        "fast_path": {
            "label": source_label,
            "detail": resolution_label,
        },
        "truth_state": {
            "label": truth_label,
            "tone": truth_tone,
            "detail": status_detail,
        },
        "voice_mode": {
            "label": next((item["label"] for item in MIRU_PREVIEW_TONES if item["value"] == safe_tone), "Neutral"),
            "detail": "Voice affects phrasing only. It does not change verified truth.",
        },
        "story_mode": {
            "label": next((item["label"] for item in MIRU_STORY_MODES if item["value"] == safe_story_mode), "Off"),
            "detail": "Story notes stay optional, spoiler-aware, and visually secondary.",
            "note": story_note,
        },
        "card": {
            "code": matched_code,
            "name": (dossier or {}).get("name") or clean_display_name(str((matched_entry or {}).get("card_name") or ""), matched_code),
            "set_name": set_name,
            "card_type": (dossier or {}).get("card_type") or str((matched_entry or {}).get("card_type") or "").strip(),
        },
        "response_preview": "\n\n".join(line for line in response_lines if line),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    }


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
        legality_index = build_legality_index()
        filtered_library_cards = filter_and_sort_library_cards(library_cards, library_filters, legality_index=legality_index)
        library_total = len(filtered_library_cards)
        library_total_pages = max(1, (library_total + LIBRARY_PAGE_SIZE - 1) // LIBRARY_PAGE_SIZE)
        library_page = min(library_page, library_total_pages)
        library_image_count = sum(1 for entry in filtered_library_cards if entry["has_runtime_thumb"] or entry["has_local_thumb"])
        library_filter_options = build_library_filter_options(library_cards, legality_index=legality_index)
        query_pairs = []
        for key in ("q", "set", "color", "rarity", "card_type", "cost", "attribute", "ban_status", "block_number", "sort"):
            value = str(library_filters.get(key) or "").strip()
            if not value or (key == "sort" and value == LIBRARY_DEFAULT_SORT):
                continue
            query_pairs.append((key, value))
        query_string = "&".join(f"{key}={quote(value)}" for key, value in query_pairs)
        query_suffix = f"&{query_string}" if query_string else ""
        library_fragment_url = f"/library-fragment?library_page={library_page}&mode=browse{query_suffix}"

    is_leader_library_view = bool(is_library_page and library_filters and str(library_filters.get("card_type") or "").strip().lower() == "leader")
    page_title = "Project Miru Library" if is_library_page else "Project Miru"
    brand_body = (
        ("Browse leader cards as hubs, then tap into the sheet when needed."
         if is_leader_library_view
         else "Search the One Piece TCG catalog by set, color, rarity, and role.")
        if is_library_page
        else "Card intelligence for the One Piece TCG."
    )
    hero_eyebrow = "Leader hub" if is_leader_library_view else ("Browse Cards" if is_library_page else "Home")
    brand_hero_class = "brandHero brandHero--slim" if is_library_page else "brandHero"
    hero_stats_html = ""
    hero_nav_html = (
        '<a class="heroNavLink heroNavLink--current" href="/" aria-current="page">Home</a>'
        '<a class="heroNavLink" href="/library">Library</a>'
        '<a class="heroNavLink" href="/leaders">Leaders</a>'
        '<a class="heroNavLink" href="/library?sort=newest_set">Sets</a>'
        if is_library_page
        else '<a class="heroNavLink heroNavLink--current" href="#watchlist" aria-current="page">Watchlist</a>'
             '<a class="heroNavLink" href="/library">Library</a>'
             '<a class="heroNavLink" href="/leaders">Leaders</a>'
             '<a class="heroNavLink" href="/library?sort=newest_set">Sets</a>'
    )
    hero_utility_html = ""
    miru_preview_html = ""
    homepage_library_entry_html = """
        <div class="homeProductRail">
          <div class="homeProductCard">
            <div class="homeProductCardEyebrow">Preview</div>
            <div class="homeProductCardTitle">Meta Watch</div>
            <div class="homeProductCardBody">Format trends, top archetypes, and price movement across the competitive scene.</div>
          </div>
          <div class="homeProductCard">
            <div class="homeProductCardEyebrow">Preview</div>
            <div class="homeProductCardTitle">Deck Builder</div>
            <div class="homeProductCardBody">Build and price complete decks using your watchlist and catalog data.</div>
          </div>
        </div>
    """
    set_options_html = ""
    color_options_html = ""
    rarity_options_html = ""
    type_options_html = ""
    cost_options_html = ""
    attribute_options_html = ""
    ban_status_options_html = ""
    block_number_options_html = ""
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
        _ban_val = str(library_filters.get("ban_status") or "").strip().lower()
        ban_status_options_html = "".join([
            f'<option value="banned"{"  selected" if _ban_val == "banned" else ""}>Banned</option>',
            f'<option value="restricted"{"  selected" if _ban_val == "restricted" else ""}>Restricted</option>',
            f'<option value="normal"{"  selected" if _ban_val == "normal" else ""}>Normal</option>',
        ])
        block_number_options_html = "".join(
            f'<option value="{html_lib.escape(opt, quote=True)}"{" selected" if library_filters["block_number"] == opt else ""}>Block {html_lib.escape(opt)}</option>'
            for opt in library_filter_options.get("block_number") or []
        )
        sort_options_html = "".join(
            f'<option value="{html_lib.escape(option["value"], quote=True)}"{" selected" if library_filters["sort"] == option["value"] else ""}>{html_lib.escape(option["label"])}</option>'
            for option in library_filter_options["sort"]
        )
    library_page_html = ""
    if is_library_page and library_filters is not None:
        is_sets_library_view = bool(
            not is_leader_library_view
            and str(library_filters.get("sort") or "").strip() == "newest_set"
        )
        library_intro_title = "Leaders" if is_leader_library_view else ("Sets" if is_sets_library_view else "Library")
        library_intro_body = (
            "Open a leader hub."
            if is_leader_library_view
            else ("Browse by set." if is_sets_library_view else "Search, filter, and tap a card.")
        )
        browse_context_label, active_filter_count = get_library_browse_context(library_filters, library_filter_options)
        browse_context_escaped = html_lib.escape(browse_context_label)
        filter_toggle_label = f"Filters ({active_filter_count})" if active_filter_count else "Filters"
        library_page_html = f"""
            <form class="libraryControls libraryControlBand" id="libraryControls" action="/library" method="get">
              <div class="libraryControlsRow">
                <div class="librarySearchWrap">
                  <input
                    class="librarySearchInput"
                    id="librarySearchInput"
                    type="search"
                    name="q"
                    value="{html_lib.escape(library_filters['q'], quote=True)}"
                    placeholder="Search by name, code, or set"
                    autocomplete="off"
                    inputmode="search"
                    aria-label="Search cards"
                  >
                </div>
                <button type="button" class="libraryFiltersToggle" id="libraryFiltersToggle" aria-expanded="false" aria-controls="libraryFilterPanel">
                  {html_lib.escape(filter_toggle_label)}
                </button>
              </div>
              <div class="libraryFilterPanel" id="libraryFilterPanel" role="region" aria-label="Filter options">
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
                      <option value="">Any color</option>
                      {color_options_html}
                    </select>
                  </label>
                  <label class="libraryControl">
                    <span class="libraryControlLabel">Rarity</span>
                    <select class="librarySelect" name="rarity">
                      <option value="">Any rarity</option>
                      {rarity_options_html}
                    </select>
                  </label>
                  <label class="libraryControl">
                    <span class="libraryControlLabel">Type</span>
                    <select class="librarySelect" name="card_type">
                      <option value="">Any type</option>
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
                      <option value="">Any attr.</option>
                      {attribute_options_html}
                    </select>
                  </label>
                  <label class="libraryControl">
                    <span class="libraryControlLabel">Status</span>
                    <select class="librarySelect" name="ban_status">
                      <option value="">Any status</option>
                      {ban_status_options_html}
                    </select>
                  </label>
                  <label class="libraryControl">
                    <span class="libraryControlLabel">Block</span>
                    <select class="librarySelect" name="block_number">
                      <option value="">Any block</option>
                      {block_number_options_html}
                    </select>
                  </label>
                  <label class="libraryControl">
                    <span class="libraryControlLabel">Sort</span>
                    <select class="librarySelect" name="sort">
                      {sort_options_html}
                    </select>
                  </label>
                </div>
              </div>
            </form>

            <div class="libraryBrowseContextWrap">
              <p class="libraryBrowseContext" id="libraryBrowseContext">
                <span class="libraryBrowseContextLabel">Now browsing:</span> <span id="libraryBrowseContextValue">{browse_context_escaped}</span>
              </p>
              <button type="button" class="libraryBrowseInfo" id="libraryBrowseInfo" aria-label="About this view" title="Project Miru stages and verifies card data. This view shows catalog cards you can browse; leaders and sets have dedicated flows.">
                <span aria-hidden="true">&#9432;</span>
              </button>
            </div>
            <p class="libraryVariantHint" id="libraryVariantHint">Tip: Double-tap a card to flip between art variants.</p>

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
        <nav class="homeQuickNav" aria-label="Project Miru quick navigation">
          <a class="homeQuickTile" href="/library">
            <span class="homeQuickLabel">Library</span>
          </a>
          <a class="homeQuickTile" href="/library?sort=newest_set">
            <span class="homeQuickLabel">Sets</span>
          </a>
          <a class="homeQuickTile" href="/leaders">
            <span class="homeQuickLabel">Leaders</span>
          </a>
          <a class="homeQuickTile" href="#watchlist">
            <span class="homeQuickLabel">Watchlist</span>
          </a>
        </nav>

        <section class="libraryIntro libraryIntro--watchlist" id="watchlist">
          <div class="libraryEyebrow">Price Watch</div>
          <h2 class="libraryTitle libraryTitle--premium">Buy Radar</h2>
          <p class="libraryBody">Sorted by closeness to target — best buys first.</p>
        </section>

        <div class="grid watchGrid" id="watchlistGrid">
          {cards_html}
        </div>
        {f'<div id="watchlistDeferred" class="libraryDeferredContent" data-watchlist-fragment-url="/watchlist-fragment?offset={len(initial_watchlist_cards)}"><div class="card libraryDeferredState">Loading {remaining_watchlist_count} more tracked cards...</div></div>' if remaining_watchlist_count else ""}
        {homepage_library_entry_html}
        """
    )

    body_class = "pageBody pageBody--library" if is_library_page else "pageBody pageBody--dashboard"
    page_html = f"""
    <html data-ui-version="worktree-home-v2">
    <head>
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{page_title}</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Geist:wght@600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
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

          --font-display: 'Geist', system-ui, sans-serif;
          --font-ui: 'Inter', system-ui, -apple-system, sans-serif;
        }}

        html {{
          overflow-x: hidden;
          box-sizing: border-box;
        }}

        *, *::before, *::after {{
          box-sizing: inherit;
        }}

        body {{
          background: radial-gradient(1200px 800px at 50% -10%, rgba(165,118,255,0.18), transparent 55%),
                      radial-gradient(900px 700px at 100% 0%, rgba(244,208,120,0.08), transparent 48%),
                      radial-gradient(900px 700px at 0% 20%, rgba(114,82,189,0.10), transparent 50%),
                      var(--bg);
          color: white;
          font-family: var(--font-ui);
          padding: 14px;
          margin: 0;
          font-size: 13px;
          overflow-x: clip;
          max-width: 100vw;
        }}

        .appFrame {{
          width: min(1040px, 100%);
          max-width: 100%;
          margin: 0 auto;
          overflow-x: hidden;
        }}

        .brandHero {{
          position: relative;
          overflow: hidden;
          margin-bottom: 14px;
          padding: 22px 20px 12px;
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
          top: 26px;
          transform: translateX(-50%);
          width: min(150px, 34vw);
          height: min(86px, 20vw);
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
          gap: 12px;
          margin-bottom: 10px;
          text-align: center;
        }}

        .pageBody--library .brandHero--library {{
          padding: 20px 18px 14px;
          margin-bottom: 12px;
          border-radius: 24px;
        }}

        .pageBody--library .brandHero--slim {{
          padding: 6px 10px;
          margin-bottom: 10px;
          border-radius: 14px;
          background: rgba(12, 10, 20, 0.84);
          border: 1px solid rgba(255,255,255,0.07);
          box-shadow: none;
        }}
        .pageBody--library .brandHero--slim::after,
        .pageBody--library .brandHero--slim .logoStage,
        .pageBody--library .brandHero--slim .brandRow,
        .pageBody--library .brandHero--slim .heroStats {{
          display: none;
        }}
        .pageBody--library .brandHero--slim .heroNav {{
          margin: 0;
          padding: 4px 2px;
        }}

        .brandMark {{
          position: relative;
          width: clamp(96px, 18vw, 132px);
          height: clamp(96px, 18vw, 132px);
          display: block;
          animation: miruLogoFloat 8s ease-in-out infinite;
          will-change: transform;
        }}

        .pageBody--library .brandHero--library .brandMark {{
          width: clamp(80px, 14vw, 100px);
          height: clamp(80px, 14vw, 100px);
        }}

        .brandLogoCompass {{
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          object-fit: contain;
          display: block;
          opacity: 0.94;
          transform: scale(1.08);
          transform-origin: center center;
          filter: drop-shadow(0 10px 20px rgba(0, 0, 0, 0.24));
          animation: miruCompassWheel 32s linear infinite;
          will-change: transform, opacity;
        }}

        .brandLogoFruitWrap {{
          position: absolute;
          width: 45.5%;
          left: 50%;
          top: 50.1%;
          transform: translate3d(-50%, -50%, 0);
          display: block;
          z-index: 2;
          animation: miruFruitPulse 8s ease-in-out infinite;
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
          filter: drop-shadow(0 8px 14px rgba(0,0,0,0.2));
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
          font-weight: 700;
          letter-spacing: -0.8px;
          font-family: var(--font-display);
          color: rgba(255, 250, 255, 0.98);
          background: linear-gradient(140deg,
            rgba(255,252,255,1) 0%,
            rgba(244,208,120,0.82) 48%,
            rgba(255,252,255,0.97) 100%);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          background-clip: text;
          filter:
            drop-shadow(0 2px 12px rgba(165,118,255,0.22))
            drop-shadow(0 1px 2px rgba(0,0,0,0.44));
        }}

        .pageBody--library .brandHero--library .brandTitle {{
          font-size: clamp(28px, 6vw, 34px);
          letter-spacing: -0.9px;
        }}

        .brandBody {{
          margin: 0;
          max-width: 34rem;
          color: rgba(228, 221, 244, 0.84);
          font-size: 13px;
          line-height: 1.52;
        }}

        .pageBody--library .brandHero--library .brandBody {{
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

        .homeAskShell {{
          margin: 18px auto 14px;
          padding: 14px 14px 12px;
          border-radius: 20px;
          border: 1px solid rgba(224, 207, 255, 0.18);
          background:
            radial-gradient(520px 260px at 0% 0%, rgba(139, 92, 246, 0.18), transparent 60%),
            linear-gradient(180deg, rgba(15, 14, 30, 0.96), rgba(10, 12, 26, 0.98));
          box-shadow: 0 18px 40px rgba(5, 4, 12, 0.32);
          display: grid;
          gap: 10px;
        }}

        .homeAskCopy {{
          display: grid;
          gap: 4px;
        }}

        .homeAskEyebrow {{
          text-transform: uppercase;
          letter-spacing: 0.16em;
          font-size: 10px;
          font-weight: 750;
          color: rgba(244, 208, 120, 0.9);
        }}

        .homeAskTitle {{
          margin: 0;
          font-size: 17px;
          letter-spacing: -0.03em;
        }}

        .homeAskBody {{
          margin: 0;
          color: rgba(228, 221, 244, 0.86);
          font-size: 12px;
        }}

        .homeAskForm {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 8px;
          align-items: center;
        }}

        .homeAskInput {{
          width: 100%;
          min-width: 0;
          padding: 9px 11px;
          border-radius: 999px;
          border: 1px solid rgba(224, 207, 255, 0.26);
          background: rgba(7, 9, 20, 0.96);
          color: white;
        }}

        .homeAskInput::placeholder {{
          color: rgba(201, 190, 229, 0.72);
        }}

        .homeAskButton {{
          border-radius: 999px;
          border: 1px solid rgba(244, 208, 120, 0.42);
          padding: 0 14px;
          min-height: 34px;
          background:
            radial-gradient(circle at 0% 0%, rgba(244, 208, 120, 0.24), transparent 58%),
            linear-gradient(180deg, rgba(31, 22, 48, 0.98), rgba(18, 14, 34, 0.99));
          color: white;
          font-weight: 700;
          font-size: 12px;
          cursor: pointer;
        }}

        .pageBody--dashboard .homeQuickNav {{
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 8px;
          margin: 0 auto 16px;
          max-width: 100%;
        }}

        .pageBody--dashboard .homeQuickTile {{
          display: flex;
          align-items: center;
          justify-content: center;
          min-height: 36px;
          border-radius: 10px;
          border: 1px solid rgba(224, 207, 255, 0.18);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.09) 0%, rgba(255,255,255,0) 55%),
            linear-gradient(180deg, rgba(24, 18, 40, 0.96), rgba(10, 10, 22, 0.99));
          color: rgba(238, 232, 255, 0.96);
          font-size: 12px;
          font-weight: 700;
          letter-spacing: 0.03em;
          text-decoration: none;
          box-shadow:
            0 2px 6px rgba(0,0,0,0.28),
            inset 0 1px 0 rgba(255,255,255,0.10),
            inset 0 -1px 0 rgba(0,0,0,0.18);
          -webkit-tap-highlight-color: transparent;
          transition: border-color 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease, transform 0.10s ease;
        }}

        .pageBody--dashboard .homeQuickTile:hover,
        .pageBody--dashboard .homeQuickTile:focus-visible {{
          border-color: rgba(191, 169, 255, 0.30);
          box-shadow:
            0 4px 12px rgba(20, 10, 40, 0.28),
            inset 0 1px 0 rgba(255,255,255,0.12),
            inset 0 -1px 0 rgba(0,0,0,0.20);
          filter: brightness(1.05);
          outline: none;
        }}

        .pageBody--dashboard .homeQuickTile:active {{
          transform: translateY(1px);
          box-shadow:
            0 1px 3px rgba(0,0,0,0.20),
            inset 0 2px 5px rgba(0,0,0,0.28),
            inset 0 -1px 0 rgba(255,255,255,0.04);
          filter: brightness(0.93);
        }}

        .pageBody--dashboard .homeQuickLabel {{
          text-align: center;
        }}

        .heroUtilityPill {{
          display: inline-flex;
          align-items: center;
          min-height: 30px;
          padding: 0 12px;
          border-radius: 8px;
          border: 1px solid rgba(212, 188, 255, 0.14);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0) 60%),
            linear-gradient(180deg, rgba(24, 18, 40, 0.88), rgba(12, 13, 22, 0.94));
          color: rgba(240, 242, 250, 0.9);
          font-size: 11px;
          font-weight: 760;
          letter-spacing: 0.28px;
          box-shadow:
            0 2px 6px rgba(0,0,0,0.22),
            inset 0 1px 0 rgba(255,255,255,0.10),
            inset 0 -1px 0 rgba(0,0,0,0.18);
        }}

        .heroUtilityPill--gold {{
          border-color: rgba(244,208,120,0.26);
          color: rgba(255, 246, 224, 0.98);
          box-shadow:
            0 2px 6px rgba(0,0,0,0.22),
            inset 0 1px 0 rgba(255,238,170,0.14),
            inset 0 -1px 0 rgba(0,0,0,0.18);
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

        .pageBody--dashboard .libraryIntro--watchlist {{
          margin-top: 10px;
          margin-bottom: 4px;
          padding: 0;
          border: 0;
          background: transparent;
          box-shadow: none;
        }}

        .pageBody--dashboard .libraryIntro--watchlist .libraryTitle {{
          font-size: 15px;
        }}

        .pageBody--dashboard .libraryIntro--watchlist .libraryBody {{
          font-size: 12px;
          margin-top: 2px;
        }}

        .pageBody--library .libraryIntro--libraryPage {{
          margin-bottom: 12px;
          padding: 4px 2px 0;
          border: 0;
          background: transparent;
          box-shadow: none;
        }}

        .pageBody--library .libraryPageHead {{
          margin-bottom: 20px;
          padding: 0 2px 0;
          border: 0;
          background: transparent;
          box-shadow: none;
        }}

        .pageBody--library .libraryPageHeadEyebrow {{
          color: rgba(189,170,255,0.72);
          font-size: 10px;
          font-weight: 700;
          letter-spacing: 1.2px;
          text-transform: uppercase;
          margin: 0 0 6px;
        }}

        .pageBody--library .libraryPageHeadTitle {{
          margin: 0;
          font-size: 22px;
          font-weight: 700;
          line-height: 1.12;
          letter-spacing: -0.2px;
          font-family: var(--font-display);
          color: rgba(255, 250, 255, 0.98);
          text-shadow: 0 1px 0 rgba(255,255,255,0.06), 0 2px 12px rgba(165,118,255,0.16);
        }}

        .pageBody--library .libraryPageHeadBody {{
          margin: 8px 0 0;
          color: rgba(255,255,255,0.58);
          font-size: 13px;
          line-height: 1.4;
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
          letter-spacing: -0.2px;
          font-family: var(--font-display);
          font-weight: 700;
          text-shadow: 0 1px 0 rgba(255,255,255,0.06), 0 2px 12px rgba(165,118,255,0.16);
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
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
          gap: 14px;
        }}

        .pageBody--dashboard .watchGrid {{
          gap: 6px;
          grid-auto-rows: max-content;
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
          gap: 4px;
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

        .railActions {{
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          align-items: center;
          gap: 6px;
          margin-top: 4px;
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
          min-height: 32px;
          width: 100%;
          padding: 0 12px;
          border-radius: 10px;
          background:
            linear-gradient(180deg, rgba(255,236,160,0.16) 0%, rgba(248,216,126,0) 55%),
            linear-gradient(180deg, rgba(148,100,255,0.52), rgba(80,56,140,0.38));
          border: 1px solid rgba(244,208,120,0.38);
          color: rgba(255,248,236,0.98);
          font-size: 11px;
          font-weight: 860;
          letter-spacing: 0.18px;
          text-decoration: none;
          user-select: none;
          box-shadow:
            0 3px 8px rgba(0,0,0,0.28),
            inset 0 1px 0 rgba(255,238,170,0.18),
            inset 0 -1px 0 rgba(0,0,0,0.22);
          transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease, filter 0.12s ease;
        }}

        .viewbtn {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          min-height: 32px;
          width: 100%;
          padding: 0 12px;
          border-radius: 10px;
          background:
            linear-gradient(180deg, rgba(255,255,255,0.08) 0%, rgba(255,255,255,0) 55%),
            linear-gradient(180deg, rgba(28, 20, 50, 0.96), rgba(14, 12, 26, 0.98));
          border: 1px solid rgba(212, 188, 255, 0.18);
          color: rgba(250,251,255,0.96);
          font-size: 11px;
          font-weight: 840;
          letter-spacing: 0.18px;
          cursor: pointer;
          box-shadow:
            0 3px 8px rgba(0,0,0,0.24),
            inset 0 1px 0 rgba(255,255,255,0.10),
            inset 0 -1px 0 rgba(0,0,0,0.18);
          transition: transform 0.12s ease, box-shadow 0.12s ease, border-color 0.12s ease, filter 0.12s ease;
        }}

        .viewbtn:hover,
        .buybtn:hover {{
          border-color: rgba(212,188,255,0.28);
          box-shadow:
            0 5px 14px rgba(0,0,0,0.32),
            inset 0 1px 0 rgba(255,255,255,0.13),
            inset 0 -1px 0 rgba(0,0,0,0.20);
          filter: brightness(1.06);
        }}

        .buybtn:hover {{
          border-color: rgba(244,208,120,0.52);
          box-shadow:
            0 5px 14px rgba(0,0,0,0.32),
            inset 0 1px 0 rgba(255,238,170,0.22),
            inset 0 -1px 0 rgba(0,0,0,0.22);
        }}

        .viewbtn:active,
        .buybtn:active {{
          transform: translateY(1px);
          box-shadow:
            0 1px 3px rgba(0,0,0,0.22),
            inset 0 1px 4px rgba(0,0,0,0.30),
            inset 0 -1px 0 rgba(255,255,255,0.04);
          filter: brightness(0.92);
        }}

        .buybtn.isDisabled {{
          cursor: default;
          opacity: 0.72;
          border-color: rgba(255,255,255,0.12);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0) 55%),
            linear-gradient(180deg, rgba(24, 18, 43, 0.9), rgba(12, 10, 22, 0.94));
        }}

        .buybtn.isDisabled:hover {{
          filter: none;
          transform: none;
          box-shadow:
            0 3px 8px rgba(0,0,0,0.24),
            inset 0 1px 0 rgba(255,255,255,0.08),
            inset 0 -1px 0 rgba(0,0,0,0.18);
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
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          justify-content: center;
          width: 100%;
          max-width: min(860px, 100%);
          margin: 12px auto 0;
          padding: 7px;
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
          flex: 1 1 100px;
          min-width: 0;
          min-height: 30px;
          padding: 0 10px;
          border-radius: 10px;
          border: 1px solid rgba(212, 188, 255, 0.20);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.09) 0%, rgba(255,255,255,0) 55%),
            linear-gradient(180deg, rgba(24, 20, 42, 0.96), rgba(12, 11, 22, 0.99));
          color: rgba(246, 248, 255, 0.96);
          font-size: 11px;
          font-weight: 850;
          letter-spacing: 0.45px;
          text-decoration: none;
          box-shadow:
            0 2px 6px rgba(0,0,0,0.32),
            inset 0 1px 0 rgba(255,255,255,0.12),
            inset 0 -1px 0 rgba(0,0,0,0.22);
          transition: border-color 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease, transform 0.10s ease;
        }}

        .heroNavLink--accent {{
          border-color: rgba(244,208,120,0.44);
          background:
            linear-gradient(180deg, rgba(255,232,148,0.18) 0%, rgba(248,216,126,0.04) 55%),
            linear-gradient(180deg, rgba(148, 102, 255, 0.52), rgba(76, 50, 140, 0.36));
          color: rgba(255,248,236,0.98);
          box-shadow:
            0 2px 5px rgba(0,0,0,0.30),
            inset 0 1px 0 rgba(255,238,170,0.20),
            inset 0 -1px 0 rgba(0,0,0,0.20);
        }}

        .heroNavLink--current {{
          border-color: rgba(165,118,255,0.44);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.01) 0%, rgba(255,255,255,0) 100%),
            linear-gradient(180deg, rgba(14, 11, 26, 0.99), rgba(8, 6, 17, 1.0));
          color: rgba(210, 196, 255, 0.96);
          box-shadow:
            0 1px 1px rgba(0,0,0,0.24),
            inset 0 2px 8px rgba(0,0,0,0.48),
            inset 0 1px 3px rgba(0,0,0,0.36),
            inset 0 -1px 0 rgba(255,255,255,0.05),
            inset 0 0 0 1px rgba(148,100,255,0.10);
          transform: translateY(1px);
        }}

        .heroNavLink--accentSoft {{
          border-color: rgba(190, 165, 255, 0.26);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0) 55%),
            linear-gradient(180deg, rgba(64, 40, 118, 0.78), rgba(20, 16, 35, 0.92));
          color: rgba(243, 237, 255, 0.98);
        }}

        .heroNavLink--large {{
          min-height: 36px;
          padding: 0 14px;
        }}

        .heroNavLink:hover,
        .heroNavLink:focus-visible {{
          border-color: rgba(190,165,255,0.34);
          box-shadow:
            0 4px 10px rgba(0,0,0,0.28),
            inset 0 1px 0 rgba(255,255,255,0.13),
            inset 0 -1px 0 rgba(0,0,0,0.20);
          filter: brightness(1.05);
          outline: none;
        }}

        .heroNavLink--accent:hover,
        .heroNavLink--accent:focus-visible {{
          border-color: rgba(244,208,120,0.60);
          box-shadow:
            0 4px 10px rgba(0,0,0,0.28),
            inset 0 1px 0 rgba(255,238,170,0.26),
            inset 0 -1px 0 rgba(0,0,0,0.22);
          filter: brightness(1.06);
        }}

        .heroNavLink--current:hover,
        .heroNavLink--current:focus-visible {{
          filter: brightness(1.04);
          border-color: rgba(165,118,255,0.52);
          transform: translateY(1px);
        }}

        .heroNavLink:active {{
          transform: translateY(1px);
          box-shadow:
            0 1px 2px rgba(0,0,0,0.18),
            inset 0 2px 5px rgba(0,0,0,0.30),
            inset 0 -1px 0 rgba(255,255,255,0.05);
          filter: brightness(0.92);
        }}

        .libraryIntro {{
          padding: 18px 18px 16px;
          border-radius: 24px;
          border: 1px solid rgba(208, 184, 255, 0.08);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.008)),
            linear-gradient(180deg, rgba(18, 11, 25, 0.82), rgba(10, 8, 15, 0.92));
          box-shadow: 0 18px 34px rgba(0,0,0,0.18);
          max-width: 100%;
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

        .pageBody--library .libraryShell {{
          margin-top: 10px;
        }}

        .pageBody--library .libraryShell--browse {{
          margin-top: 6px;
        }}

        .pageBody--library .libraryGateway {{
          margin-top: 14px;
          padding: 14px;
          border-radius: 18px;
          border: 1px solid rgba(212, 188, 255, 0.1);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(19, 13, 31, 0.9), rgba(10, 9, 18, 0.95));
          box-shadow: 0 12px 26px rgba(0,0,0,0.18);
        }}

        .pageBody--library .libraryGatewayCopy {{
          display: grid;
          gap: 5px;
          margin-bottom: 10px;
        }}

        .pageBody--library .libraryGatewayActions {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 10px;
        }}

        .pageBody--dashboard .homeProductRail {{
          display: grid;
          grid-template-columns: repeat(2, 1fr);
          gap: 8px;
          margin-top: 14px;
          opacity: 0.72;
        }}

        .pageBody--dashboard .homeProductCard {{
          padding: 14px 16px;
          border-radius: 16px;
          border: 1px solid rgba(255,255,255,0.07);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.008));
        }}

        .pageBody--dashboard .homeProductCardEyebrow {{
          font-size: 9px;
          font-weight: 600;
          letter-spacing: 0.08em;
          text-transform: uppercase;
          color: rgba(189,170,255,0.56);
          margin-bottom: 5px;
        }}

        .pageBody--dashboard .homeProductCardTitle {{
          font-size: 14px;
          font-weight: 700;
          font-family: var(--font-display);
          color: rgba(255,250,255,0.70);
          margin-bottom: 5px;
          letter-spacing: -0.1px;
        }}

        .pageBody--dashboard .homeProductCardBody {{
          font-size: 11px;
          line-height: 1.45;
          color: rgba(255,255,255,0.34);
        }}

        .pageBody--library .libraryControls {{
          display: grid;
          gap: 0;
          margin-bottom: 6px;
          max-width: 100%;
          min-width: 0;
        }}

        .pageBody--library .libraryControlBand {{
          display: grid;
          gap: 0;
          margin-bottom: 6px;
          padding: 6px 8px;
          border-radius: 10px;
          border: 1px solid rgba(255,255,255,0.05);
          background: rgba(14, 12, 22, 0.6);
          max-width: 100%;
          min-width: 0;
        }}

        .pageBody--library .libraryControlsRow {{
          display: flex;
          align-items: center;
          gap: 6px;
          min-width: 0;
        }}

        .pageBody--library .librarySearchWrap {{
          flex: 1 1 auto;
          min-width: 0;
        }}

        .pageBody--library .libraryFiltersToggle {{
          flex: 0 0 auto;
          min-height: 30px;
          padding: 0 10px;
          border-radius: 8px;
          border: 1px solid rgba(255,255,255,0.1);
          background: rgba(255,255,255,0.06);
          color: rgba(248,250,255,0.9);
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
          transition: background 0.15s ease, border-color 0.15s ease;
        }}
        .pageBody--library .libraryFiltersToggle:hover {{
          background: rgba(255,255,255,0.09);
          border-color: rgba(255,255,255,0.14);
        }}

        .pageBody--library .libraryFilterPanel {{
          max-height: 0;
          overflow: hidden;
          transition: max-height 0.2s ease;
        }}
        .pageBody--library .libraryFilterPanel.is-open {{
          max-height: 280px;
        }}

        .pageBody--library .libraryFilterRow {{
          display: flex;
          flex-wrap: wrap;
          gap: 5px;
          align-items: center;
          padding-top: 6px;
          margin-top: 4px;
          border-top: 1px solid rgba(255,255,255,0.06);
        }}

        .pageBody--library .libraryBrowseContextWrap {{
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 6px;
          min-width: 0;
        }}

        .pageBody--library .libraryBrowseContext {{
          margin: 0;
          font-size: 11px;
          color: rgba(255,255,255,0.5);
          line-height: 1.3;
        }}

        .pageBody--library .libraryBrowseContextLabel {{
          color: rgba(255,255,255,0.4);
          margin-right: 4px;
        }}

        .pageBody--library .libraryBrowseInfo {{
          flex: 0 0 auto;
          width: 18px;
          height: 18px;
          padding: 0;
          border: none;
          border-radius: 50%;
          background: transparent;
          color: rgba(255,255,255,0.35);
          font-size: 11px;
          cursor: help;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          transition: color 0.15s ease;
        }}
        .pageBody--library .libraryBrowseInfo:hover {{
          color: rgba(255,255,255,0.55);
        }}

        .pageBody--library .libraryVariantHint {{
          margin: 0 0 10px 0;
          font-size: 10px;
          color: rgba(255,255,255,0.38);
          line-height: 1.35;
        }}

        .pageBody--library .libraryControl {{
          display: block;
          padding: 0;
          border: 0;
          background: transparent;
          flex: 0 1 auto;
        }}

        .pageBody--library .libraryControlLabel {{
          display: none;
        }}

        .pageBody--library .librarySearchInput {{
          width: 100%;
          min-height: 32px;
          height: 32px;
          padding: 0 12px;
          border-radius: 10px;
          border: 1px solid rgba(212, 188, 255, 0.12);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.008)),
            rgba(11, 12, 21, 0.96);
          color: rgba(248,250,255,0.98);
          font-size: 13px;
          min-width: 0;
          box-shadow:
            0 1px 3px rgba(0,0,0,0.2),
            inset 0 1px 0 rgba(255,255,255,0.05),
            inset 0 -1px 0 rgba(0,0,0,0.12);
        }}

        .pageBody--library .librarySelect {{
          min-height: 28px;
          height: 28px;
          padding: 0 22px 0 9px;
          border-radius: 8px;
          border: 1px solid rgba(212, 188, 255, 0.12);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0) 55%),
            rgba(11, 12, 21, 0.96);
          color: rgba(248,250,255,0.98);
          font-size: 11.5px;
          min-width: 0;
          max-width: 140px;
          cursor: pointer;
          box-shadow:
            0 1px 3px rgba(0,0,0,0.2),
            inset 0 1px 0 rgba(255,255,255,0.06),
            inset 0 -1px 0 rgba(0,0,0,0.14);
        }}

        .pageBody--library .librarySearchInput::placeholder {{
          color: rgba(222, 215, 240, 0.45);
        }}

        .pageBody--library .librarySearchInput:focus,
        .pageBody--library .librarySelect:focus {{
          outline: none;
          border-color: rgba(165,118,255,0.36);
          box-shadow:
            inset 0 1px 3px rgba(0,0,0,0.24),
            0 0 0 3px rgba(148,100,255,0.10);
        }}

        .pageBody--library .libraryDeferredContent {{
          min-height: 120px;
        }}

        .pageBody--library .libraryDeferredState {{
          display: grid;
          place-items: center;
          min-height: 120px;
          color: var(--muted);
          text-align: center;
        }}

        .pageBody--library .libraryHeader {{
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

        .pageBody--library .libraryHeaderCopy {{
          min-width: 0;
        }}

        .pageBody--library .libraryMetaPills {{
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 8px;
          width: min(100%, 540px);
        }}

        .pageBody--library .libraryMetaPill {{
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

        .pageBody--library .libraryGrid {{
          display: grid;
          grid-template-columns: minmax(0, 1fr);
          gap: 16px;
          min-width: 0;
          max-width: 100%;
        }}

        .pageBody--library .libraryGrid--browse {{
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 9px;
        }}

        .pageBody--library .libraryCard {{
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

        .pageBody--library .libraryCard--browse {{
          max-width: none;
          border-radius: 8px;
          border: 1px solid rgba(255,255,255,0.04);
          box-shadow: 0 1px 4px rgba(0,0,0,0.06);
          min-height: 0;
        }}

        .pageBody--library .libraryCard--leader {{
          border-color: rgba(244,208,120,0.08);
          box-shadow: 0 1px 4px rgba(0,0,0,0.06);
        }}

        .pageBody--library .libraryCard[data-card] {{
          cursor: pointer;
          transition: transform 0.18s ease, box-shadow 0.18s ease;
        }}

        .pageBody--library .libraryCard[data-card]:hover,
        .pageBody--library .libraryCard[data-card]:focus {{
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(0,0,0,0.12);
          outline: none;
        }}

        .pageBody--library .libraryThumbWrap {{
          display: block;
          width: 100%;
          min-width: 0;
          overflow: hidden;
          border-radius: 8px 8px 0 0;
        }}

        .pageBody--library .libraryCard--browse .libraryThumbWrap {{
          border-radius: 8px 8px 0 0;
        }}

        .pageBody--library .libraryThumbFlip {{
          perspective: 520px;
          display: block;
          width: 100%;
          min-width: 0;
        }}
        .pageBody--library .libraryThumbFlip .libraryThumb {{
          transform-style: preserve-3d;
          backface-visibility: hidden;
          transition: transform 0.14s ease-out;
        }}
        .pageBody--library .libraryThumbFlip--phase1 .libraryThumb {{
          transform: rotateY(90deg);
        }}
        .pageBody--library .libraryThumbFlip--phase2 .libraryThumb {{
          transform: rotateY(-90deg);
        }}

        .previewShell {{
          display: grid;
          gap: 14px;
          margin-bottom: 18px;
          padding: 16px;
          border-radius: 26px;
          border: 1px solid rgba(212, 188, 255, 0.1);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.028), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(20, 14, 31, 0.9), rgba(11, 10, 18, 0.96));
          box-shadow: 0 18px 32px rgba(0,0,0,0.18);
        }}

        .miruPreviewHeader {{
          display: grid;
          gap: 10px;
        }}

        .miruPreviewCopy {{
          display: grid;
          gap: 8px;
        }}

        .miruPreviewMetaPills {{
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
        }}

        .miruPreviewForm {{
          display: grid;
          gap: 10px;
        }}

        .miruPreviewInputWrap {{
          display: grid;
          gap: 5px;
        }}

        .miruPreviewControls {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
          gap: 10px;
          align-items: end;
        }}

        .miruPreviewRunButton {{
          min-height: 44px;
          white-space: nowrap;
        }}

        .miruPreviewGrid {{
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 10px;
        }}

        .miruPreviewCard,
        .miruPreviewResponseCard {{
          border-radius: 18px;
          border: 1px solid rgba(255,255,255,0.07);
          background: rgba(255,255,255,0.04);
          padding: 12px;
        }}

        .miruPreviewCard strong {{
          display: block;
          font-size: 15px;
          line-height: 1.25;
          margin-bottom: 5px;
        }}

        .miruPreviewCard p,
        .miruPreviewResponse {{
          margin: 0;
          color: rgba(236, 240, 250, 0.82);
          line-height: 1.55;
        }}

        #miruPreviewTruth[data-tone="verified"] {{
          color: rgba(193, 255, 214, 0.98);
        }}

        #miruPreviewTruth[data-tone="reference"] {{
          color: rgba(244, 220, 166, 0.98);
        }}

        #miruPreviewTruth[data-tone="empty"] {{
          color: rgba(222, 215, 240, 0.72);
        }}

        .miruPreviewResponseCard {{
          padding: 14px;
        }}

        .miruPreviewResponse {{
          font-size: 14px;
          white-space: pre-wrap;
          word-break: break-word;
        }}

        .pageBody--library .libraryThumb {{
          width: 100%;
          max-width: 420px;
          aspect-ratio: 0.72;
          object-fit: contain;
          display: block;
          margin: 0 auto;
          background: linear-gradient(180deg, rgba(15, 12, 22, 0.98), rgba(8, 9, 16, 0.98));
        }}

        .pageBody--library .libraryThumb--browse {{
          max-width: none;
          width: 100%;
          aspect-ratio: 63/88;
          object-fit: contain;
          height: auto;
          box-shadow: 0 2px 6px rgba(0,0,0,0.25), 0 1px 2px rgba(0,0,0,0.18);
        }}
        .pageBody--library .libraryCard[data-card]:hover .libraryThumb--browse {{
          transform: translateY(-1px);
        }}

        .pageBody--library .libraryThumb--empty {{
          display: flex;
          align-items: center;
          justify-content: center;
          color: rgba(255,255,255,0.55);
          font-size: 13px;
        }}

        .pageBody--library .libraryCardBody {{
          display: grid;
          gap: 8px;
          padding: 16px;
        }}

        .pageBody--library .libraryCardBody--browse {{
          gap: 2px;
          padding: 6px 8px 8px;
          flex: 1 1 auto;
        }}

        .pageBody--library .libraryCardCodeLine {{
          margin: 0;
          font-size: 10px;
          font-weight: 600;
          letter-spacing: 0.04em;
          color: rgba(255,255,255,0.55);
          line-height: 1.25;
        }}

        .pageBody--library .libraryCodeRow {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
        }}

        .pageBody--library .libraryCode,
        .pageBody--library .libraryRarity {{
          font-size: 10px;
          font-weight: 700;
          letter-spacing: 0.4px;
          text-transform: uppercase;
          padding: 4px 8px;
          border-radius: 6px;
          border: 1px solid rgba(255,255,255,0.06);
          background: rgba(255,255,255,0.04);
          color: rgba(255,255,255,0.78);
        }}

        .pageBody--library .libraryCardTitle {{
          margin: 0;
          font-size: 16px;
          line-height: 1.2;
        }}

        .pageBody--library .libraryCardTitle--browse {{
          font-size: 12px;
          font-weight: 700;
          line-height: 1.2;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
          color: rgba(255,255,255,0.95);
        }}

        .pageBody--library .libraryCardSubtitle {{
          color: rgba(255,255,255,0.52);
          font-size: 11px;
          line-height: 1.35;
          min-height: 0;
        }}

        .pageBody--library .libraryCardSubtitle--browse {{
          font-size: 10px;
          min-height: 0;
          line-height: 1.25;
          display: -webkit-box;
          -webkit-line-clamp: 1;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }}

        .pageBody--library .libraryPrice {{
          color: rgba(255, 235, 166, 0.9);
          font-size: 11px;
          font-weight: 800;
          letter-spacing: 0.08px;
        }}

        .pageBody--library .libraryOpenBtn {{
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

        .pageBody--library .libraryOpenBtn--browse {{
          min-height: 30px;
          padding: 0 9px;
          font-size: 10.5px;
          margin-top: auto;
        }}

        .pageBody--library .libraryPager {{
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

        .pageBody--library .libraryShell[data-loading] .libraryPager {{
          position: relative;
        }}
        .pageBody--library .libraryShell[data-loading] .libraryPager::after {{
          content: "Loading…";
          display: block;
          margin-top: 6px;
          text-align: center;
          font-size: 11px;
          color: rgba(255,255,255,0.45);
        }}

        .pageBody--library .libraryPagerLinks {{
          display: flex;
          flex-wrap: wrap;
          gap: 10px;
        }}

        .pageBody--library .libraryPagerLink,
        .pageBody--library .libraryPagerLink.isDisabled {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 96px;
          min-height: 38px;
          padding: 0 14px;
          border-radius: 10px;
          border: 1px solid rgba(244,208,120,0.28);
          background:
            linear-gradient(180deg, rgba(255,238,170,0.10) 0%, rgba(248,216,126,0) 55%),
            linear-gradient(180deg, rgba(22, 18, 38, 0.96), rgba(12, 11, 20, 0.98));
          color: rgba(255,248,238,0.96);
          font-size: 12px;
          font-weight: 840;
          text-decoration: none;
          box-shadow:
            0 3px 8px rgba(0,0,0,0.26),
            inset 0 1px 0 rgba(255,238,160,0.14),
            inset 0 -1px 0 rgba(0,0,0,0.20);
          transition: box-shadow 0.12s ease, border-color 0.12s ease, filter 0.12s ease;
        }}

        .pageBody--library .libraryPagerLink:hover {{
          border-color: rgba(244,208,120,0.44);
          box-shadow:
            0 5px 14px rgba(0,0,0,0.30),
            inset 0 1px 0 rgba(255,238,160,0.18),
            inset 0 -1px 0 rgba(0,0,0,0.22);
          filter: brightness(1.06);
        }}

        .pageBody--library .libraryPagerLink:active {{
          transform: translateY(1px);
          box-shadow:
            0 1px 3px rgba(0,0,0,0.20),
            inset 0 1px 4px rgba(0,0,0,0.28),
            inset 0 -1px 0 rgba(255,255,255,0.04);
          filter: brightness(0.92);
        }}

        .pageBody--library .libraryPagerLink.isDisabled {{
          opacity: 0.40;
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
          0% {{ transform: scale(1.08) rotate(0deg); opacity: 0.92; }}
          50% {{ transform: scale(1.09) rotate(180deg); opacity: 0.95; }}
          100% {{ transform: scale(1.08) rotate(360deg); opacity: 0.92; }}
        }}

        @keyframes miruFruitPulse {{
          0%, 100% {{ transform: translate(-50%, -50%) scale(1); opacity: 1; }}
          45% {{ transform: translate(-50%, -51.2%) scale(1.015); opacity: 0.985; }}
          72% {{ transform: translate(-50%, -49.6%) scale(1.006); opacity: 1; }}
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
          .miruPreviewHeader {{
            grid-template-columns: minmax(0, 1fr) auto;
            align-items: start;
          }}

          .libraryGrid {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }}
        }}

        @media (min-width: 1024px) {{
          .libraryGrid {{
            grid-template-columns: repeat(3, minmax(0, 1fr));
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(3, minmax(0, 1fr));
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
            grid-template-columns: repeat(4, minmax(0, 1fr));
          }}
        }}

        @media (max-width: 640px) {{
          .brandHero {{
            padding: 12px 12px 8px;
            border-radius: 20px;
            margin-bottom: 10px;
          }}

          .logoStage {{
            top: 12px;
          }}

          .brandRow {{
            gap: 8px;
            margin-bottom: 6px;
          }}

          .brandMark {{
            width: 52px;
            height: 52px;
          }}

          .brandTitle {{
            font-size: clamp(22px, 5.5vw, 26px);
            letter-spacing: -0.4px;
          }}

          .brandBody {{
            font-size: 12px;
            line-height: 1.4;
          }}

          .heroNav {{
            margin-top: 6px;
            padding: 4px;
            gap: 4px;
          }}

          .heroNavLink {{
            min-height: 28px;
            font-size: 11px;
            padding: 0 9px;
          }}

          .pageBody--dashboard .homeQuickNav {{
            display: none;
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

          .miruPreviewControls,
          .miruPreviewGrid,
          .detailLeaderGrid {{
            grid-template-columns: 1fr;
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
          .pageBody--dashboard .homeProductRail {{
            grid-template-columns: 1fr;
          }}

          .heroNav {{
            padding: 3px;
          }}

          .heroNavLink {{
            min-height: 26px;
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

          .pageBody--library .libraryCardBody {{
            padding: 10px;
          }}

          .pageBody--library .libraryCard--browse {{
            min-height: 0;
          }}

          .pageBody--library .libraryPager {{
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
          overflow: hidden;
          display: flex;
          flex-direction: column;
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
          flex: 1;
          min-height: 0;
          display: flex;
          flex-direction: column;
          overflow: hidden;
        }}

        .modalTop {{
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 10px;
          flex-shrink: 0;
          margin: -14px -14px 12px;
          padding: 13px 14px 11px;
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
          flex-shrink: 0;
          min-height: 40px;
          min-width: 44px;
          border: 1px solid rgba(255,255,255,0.18);
          background: rgba(255,255,255,0.10);
          color: rgba(255,252,255,0.98);
          border-radius: 12px;
          padding: 10px 16px;
          font-size: 13px;
          font-weight: 800;
          cursor: pointer;
          box-shadow: 0 4px 12px rgba(0,0,0,0.24), inset 0 1px 0 rgba(255,255,255,0.08);
          transition: background 0.12s ease, border-color 0.12s ease;
        }}

        .closeBtn:hover {{
          background: rgba(255,255,255,0.16);
          border-color: rgba(255,255,255,0.26);
        }}

        .closeBtn:active {{
          background: rgba(255,255,255,0.08);
        }}

        .detailLayout {{
          display: flex;
          flex-direction: column;
          gap: 0;
          flex: 1 1 auto;
          min-height: 0;
          overflow-y: auto;
          -webkit-overflow-scrolling: touch;
        }}

        .detailIdentity {{
          display: flex;
          flex-direction: row;
          align-items: flex-start;
          gap: 12px;
          padding: 10px 12px;
          margin-bottom: 10px;
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.08);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(22, 18, 36, 0.92), rgba(12, 11, 20, 0.96));
          box-shadow: 0 4px 12px rgba(0,0,0,0.10);
        }}

        .detailMedia {{
          flex-shrink: 0;
          display: flex;
          justify-content: center;
        }}

        .detailMediaStack {{
          width: min(100%, 160px);
          display: grid;
          gap: 6px;
        }}

        .detailImageFlip {{
          perspective: 520px;
          display: block;
          width: 100%;
        }}
        .detailImageFlip .detailImage {{
          transform-style: preserve-3d;
          backface-visibility: hidden;
          transition: transform 0.14s ease-out;
        }}
        .detailImageFlip--phase1 .detailImage {{
          transform: rotateY(90deg);
        }}
        .detailImageFlip--phase2 .detailImage {{
          transform: rotateY(-90deg);
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
          width: min(100%, 176px);
          max-height: min(44vh, 260px);
          aspect-ratio: 63/88;
          object-fit: contain;
          border-radius: 20px;
          border: 1px solid rgba(255,255,255,0.10);
          background: rgba(0,0,0,0.35);
          box-shadow:
            0 16px 30px rgba(0,0,0,0.4),
            0 0 0 1px rgba(255,255,255,0.05) inset;
          display: block;
          image-rendering: auto;
          cursor: pointer;
        }}

        .detailAltControlWrap {{
          display: flex;
          justify-content: center;
          margin-top: 4px;
        }}

        .detailAltControl {{
          min-height: 26px;
          padding: 2px 8px;
          font-size: 11px;
          font-weight: 600;
          color: rgba(255,255,255,0.88);
          background: rgba(0,0,0,0.45);
          border: 1px solid rgba(255,255,255,0.12);
          border-radius: 8px;
          cursor: pointer;
          transition: background 0.15s ease, border-color 0.15s ease;
        }}
        .detailAltControl:hover {{
          background: rgba(0,0,0,0.6);
          border-color: rgba(255,255,255,0.18);
        }}

        .cardImageFullscreen {{
          display: none;
          position: fixed;
          inset: 0;
          z-index: 1100;
          align-items: center;
          justify-content: center;
          padding: 12px;
          background: rgba(2, 6, 12, 0.92);
          backdrop-filter: blur(8px);
        }}

        .cardImageFullscreen.open {{
          display: flex;
        }}

        .cardImageFullscreenBackdrop {{
          position: absolute;
          inset: 0;
          cursor: pointer;
          z-index: 1;
        }}

        .cardImageFullscreenInner {{
          position: relative;
          z-index: 2;
          max-width: 100%;
          max-height: 100%;
          display: flex;
          align-items: center;
          justify-content: center;
        }}

        .cardImageFullscreenInner img {{
          pointer-events: auto;
          max-width: 100%;
          max-height: 100%;
          width: auto;
          height: auto;
          aspect-ratio: 63/88;
          object-fit: contain;
          border-radius: 12px;
          box-shadow: 0 24px 48px rgba(0,0,0,0.5);
        }}

        .cardImageFullscreenClose {{
          position: absolute;
          top: -8px;
          right: -8px;
          width: 40px;
          height: 40px;
          border-radius: 50%;
          border: 1px solid rgba(255,255,255,0.2);
          background: rgba(0,0,0,0.7);
          color: white;
          font-size: 24px;
          line-height: 1;
          cursor: pointer;
          pointer-events: auto;
          display: flex;
          align-items: center;
          justify-content: center;
          padding: 0;
          -webkit-tap-highlight-color: transparent;
        }}

        .cardImageFullscreenClose:hover {{
          background: rgba(255,255,255,0.15);
        }}

        .cardImageFullscreenClose:active {{
          background: rgba(255,255,255,0.25);
        }}

        .detailImagePh {{
          width: min(100%, 176px);
          min-height: 220px;
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
          gap: 6px;
        }}

        .detailActionHub {{
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.08);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.025), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(22, 18, 36, 0.92), rgba(12, 11, 20, 0.96));
          padding: 10px 12px;
          box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        }}

        .detailActionHeader {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          margin-bottom: 8px;
        }}

        .detailActionEyebrow {{
          display: none;
        }}

        .detailActionTitle {{
          margin: 0;
          color: rgba(252, 252, 255, 0.94);
          font-size: 12px;
          font-weight: 760;
          line-height: 1.2;
          color: rgba(255,255,255,0.7);
        }}

        .detailPriceChip {{
          min-width: 72px;
          padding: 5px 8px;
          border-radius: 10px;
          border: 1px solid rgba(255,255,255,0.08);
          background: rgba(255,255,255,0.04);
          text-align: right;
        }}

        .detailPriceChipLabel {{
          color: rgba(255,255,255,0.5);
          font-size: 9px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }}

        .detailPriceChipValue {{
          margin-top: 2px;
          color: rgba(255, 239, 182, 0.98);
          font-size: 13px;
          font-weight: 880;
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
          min-height: 32px;
          padding: 0 10px;
          border-radius: 10px;
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
          min-height: 32px;
          padding: 0 12px;
          border-radius: 10px;
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
          grid-template-columns: auto 1fr;
          gap: 4px 10px;
          align-content: start;
          min-width: 0;
        }}

        .detailStat {{
          display: contents;
        }}

        .detailStatLabel {{
          color: rgba(255,255,255,0.48);
          font-size: 9px;
          letter-spacing: 0.05em;
          text-transform: uppercase;
          white-space: nowrap;
        }}

        .detailStat .detailStatLabel {{
          grid-column: 1;
        }}

        .detailStatValue {{
          font-size: 11px;
          font-weight: 700;
          line-height: 1.3;
          color: rgba(248,250,255,0.94);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }}

        .detailStat .detailStatValue {{
          grid-column: 2;
        }}

        .detailStats--legality:empty {{
          display: none;
        }}

        .detailStats--legality {{
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px solid rgba(255,255,255,0.07);
        }}

        .detailStatValue--banned {{
          color: rgba(248,100,80,0.92);
        }}

        .detailStatValue--restricted {{
          color: rgba(242,168,50,0.92);
        }}

        .detailTextBlock {{
          background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 14px;
          padding: 10px 12px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}

        .detailTextLabel {{
          color: rgba(255,255,255,0.56);
          font-size: 10px;
          letter-spacing: 0.6px;
          text-transform: uppercase;
          margin-bottom: 6px;
        }}

        .detailTextValue {{
          font-size: 12.5px;
          line-height: 1.48;
          color: rgba(245,248,255,0.94);
          white-space: pre-wrap;
          word-break: break-word;
        }}

        .detailTextValue.empty {{
          color: rgba(255,255,255,0.5);
        }}

        .detailHint {{
          margin-top: 8px;
          color: rgba(222, 215, 240, 0.68);
          font-size: 11px;
          line-height: 1.45;
          min-height: 1.4em;
        }}

        .detailLeaderHub {{
          display: grid;
          gap: 10px;
          padding-top: 2px;
        }}

        .modalCard.is-leader .detailLeaderHub {{
          margin-top: 2px;
          padding: 10px 0 0;
          border-top: 1px solid rgba(244,208,120,0.12);
        }}

        .modalCard.is-leader .detailLeaderHub .detailTextLabel {{
          color: rgba(244,208,120,0.78);
          font-size: 10px;
        }}

        .detailLeaderGrid {{
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 9px;
        }}

        .detailLeaderCard {{
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.07);
          background: rgba(255,255,255,0.04);
          padding: 11px;
        }}

        .modalCard.is-leader .detailLeaderCard {{
          border-color: rgba(212, 188, 255, 0.1);
          background: rgba(255,255,255,0.05);
        }}

        .detailLeaderCard .detailTextValue {{
          margin: 0;
          color: rgba(236, 240, 250, 0.82);
          line-height: 1.55;
        }}

        .leaderPageLinkWrap {{
          margin-top: 12px;
          padding-top: 12px;
          border-top: 1px solid rgba(255, 255, 255, 0.07);
          display: flex;
          justify-content: flex-end;
        }}

        .leaderPageBtn {{
          font-size: 11px;
          padding: 6px 14px;
          border-radius: 8px;
          background: rgba(184, 160, 255, 0.09);
          border: 1px solid rgba(184, 160, 255, 0.26);
          color: rgba(184, 160, 255, 0.88);
          text-decoration: none;
          font-weight: 500;
          transition: background 0.14s;
        }}

        .leaderPageBtn:hover {{
          background: rgba(184, 160, 255, 0.17);
        }}

        .detailPreviewBtn {{
          cursor: pointer;
          border-color: rgba(212, 188, 255, 0.2);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(23, 18, 38, 0.94), rgba(12, 11, 20, 0.92));
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
          touch-action: none;
          overscroll-behavior: none;
          position: fixed;
          left: 0;
          right: 0;
          width: 100%;
          top: var(--modal-scroll-top, 0);
        }}

        @media (max-width: 760px) {{
          .pageBody--dashboard .homeQuickNav {{
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }}

          .modalShell {{
            padding: 10px;
            align-items: flex-end;
          }}

          .modalCard {{
            max-height: 94vh;
            border-bottom-left-radius: 0;
            border-bottom-right-radius: 0;
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

          .detailIdentity {{
            padding: 8px 10px;
            gap: 10px;
            margin-bottom: 8px;
          }}

          .detailMediaStack,
          .detailImage,
          .detailImagePh {{
            width: min(100%, 112px);
          }}

          .detailImage {{
            max-height: 176px;
          }}

          .detailImagePh {{
            min-height: 156px;
          }}

          .detailActionForm {{
            grid-template-columns: minmax(0, 1fr);
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
            width: 118px;
            height: 118px;
          }}

          .heroStats {{
            margin-left: 0;
            margin-right: 0;
          }}

          .pageBody--library .librarySearchInput,
          .pageBody--library .librarySelect {{
            font-size: 12px;
          }}

          .pageBody--dashboard .watchTile {{
            grid-template-columns: 46px minmax(0, 1fr);
          }}

          .pageBody--dashboard .watchTileBody {{
            grid-template-columns: minmax(0, 1fr) 56px;
            gap: 5px;
            padding: 5px 6px;
          }}

          .pageBody--dashboard .watchTileTitle {{
            font-size: 10.5px;
          }}

          .pageBody--dashboard .watchTileSubtitle {{
            font-size: 8.5px;
          }}

          .pageBody--dashboard .watchTileSignal {{
            max-width: 96px;
          }}

          .pageBody--dashboard .watchTilePriceValue {{
            font-size: 10px;
          }}

          .pageBody--library .libraryGrid--browse {{
            gap: 7px;
          }}

          .pageBody--library .libraryThumbWrap {{
            aspect-ratio: 0.61;
          }}

          .pageBody--library .libraryCardBody--browse {{
            padding: 5px 6px 6px;
          }}

          .pageBody--library .libraryCardTitle--browse {{
            font-size: 10px;
          }}

          .pageBody--library .libraryCardCodeLine {{
            font-size: 8.5px;
          }}

          .detailIdentity {{
            padding: 8px 10px;
            gap: 8px;
            margin-bottom: 8px;
          }}

          .detailMediaStack,
          .detailImage,
          .detailImagePh {{
            width: min(100%, 104px);
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

        body,
        .appFrame,
        .brandHero,
        .pageBody--library .libraryShell,
        .pageBody--library .libraryControlBand,
        .pageBody--library .libraryPager,
        .modalCard {{
          min-width: 0;
          max-width: 100%;
        }}

        .brandMark {{
          width: clamp(86px, 14vw, 108px);
          height: clamp(86px, 14vw, 108px);
        }}

        .brandLogoCompass {{
          transform: scale(1.02);
          opacity: 0.88;
          animation-duration: 42s;
        }}

        .brandLogoFruitWrap {{
          width: 39%;
          top: 50%;
          left: 50%;
        }}

        .brandTitle {{
          color: rgba(248, 221, 148, 0.98);
          text-shadow: 0 10px 26px rgba(18, 10, 36, 0.34), 0 0 18px rgba(244, 208, 120, 0.14);
        }}

        .brandBody {{
          max-width: 28rem;
          color: rgba(232, 225, 244, 0.74);
        }}

        .uiPassMarker {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 24px;
          padding: 0 10px;
          border-radius: 999px;
          border: 1px solid rgba(244,208,120,0.26);
          background: rgba(244,208,120,0.08);
          color: rgba(255,244,214,0.92);
          font-size: 10px;
          font-weight: 800;
          letter-spacing: 0.08em;
        }}

        .heroNav {{
          gap: 6px;
          padding: 7px;
          border-radius: 16px;
        }}

        .heroNavLink {{
          flex: 1 1 110px;
          min-height: 34px;
          border-radius: 10px;
          border-color: rgba(212, 188, 255, 0.20);
          box-shadow:
            0 1px 4px rgba(0,0,0,0.26),
            0 1px 0 rgba(255,255,255,0.09) inset,
            0 -1px 0 rgba(0,0,0,0.16) inset;
        }}

        .pageBody--dashboard .homeQuickNav {{
          grid-template-columns: repeat(auto-fit, minmax(110px, 1fr));
          gap: 8px;
          margin-bottom: 16px;
        }}

        .pageBody--dashboard .homeQuickTile {{
          min-height: 38px;
          border-radius: 10px;
          border-color: rgba(244, 208, 120, 0.13);
          box-shadow:
            0 1px 4px rgba(0, 0, 0, 0.28),
            0 1px 0 rgba(255,255,255,0.08) inset,
            0 -1px 0 rgba(0,0,0,0.18) inset;
        }}

        .pageBody--dashboard .libraryIntro--watchlist,
        .pageBody--library .libraryPageHead {{
          margin-bottom: 12px;
          padding-left: 2px;
          padding-right: 2px;
        }}

        .pageBody--library .libraryPageHeadTitle,
        .libraryTitle {{
          letter-spacing: -0.04em;
        }}

        .pageBody--library .libraryControlBand {{
          gap: 10px;
          padding: 8px 10px;
          border-radius: 12px;
          background: rgba(14, 12, 22, 0.65);
          box-shadow: 0 4px 12px rgba(0,0,0,0.12);
        }}

        .pageBody--library .librarySearchInput {{
          min-height: 34px;
          height: 34px;
          font-size: 13px;
        }}
        .pageBody--library .librarySelect {{
          min-height: 30px;
          height: 30px;
          font-size: 12px;
        }}

        .pageBody--dashboard .watchGrid {{
          grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
          gap: 6px;
          align-items: start;
          align-content: start;
          grid-auto-rows: max-content;
        }}

        .pageBody--dashboard .watchTile {{
          position: relative;
          display: grid;
          grid-template-columns: 50px minmax(0, 1fr);
          align-items: stretch;
          gap: 0;
          padding: 0;
          min-height: 76px;
          border-radius: 14px;
          overflow: hidden;
          cursor: pointer;
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.008)),
            linear-gradient(180deg, rgba(16, 11, 24, 0.96), rgba(9, 8, 15, 0.98));
          border: 1px solid rgba(255,255,255,0.07);
          box-shadow: 0 12px 26px rgba(0,0,0,0.22);
          transition: transform 0.16s ease, border-color 0.16s ease, box-shadow 0.16s ease;
        }}

        .pageBody--dashboard .watchTile:hover,
        .pageBody--dashboard .watchTile:focus-visible {{
          border-color: rgba(244,208,120,0.2);
          box-shadow: 0 6px 16px rgba(0,0,0,0.24);
          filter: brightness(1.04);
          outline: none;
        }}

        .pageBody--dashboard .watchTile:active {{
          filter: brightness(0.94);
        }}

        .pageBody--dashboard .watchTileMedia {{
          position: relative;
          overflow: hidden;
          background: linear-gradient(180deg, rgba(10, 10, 18, 0.96), rgba(5, 7, 14, 1));
        }}

        .pageBody--dashboard .watchTileImage {{
          position: absolute;
          inset: 0;
          width: 100%;
          height: 100%;
          display: block;
          object-fit: contain;
          padding: 3px;
          box-sizing: border-box;
        }}

        .pageBody--dashboard .watchTileImage--empty {{
          display: grid;
          place-items: center;
          color: rgba(255,255,255,0.5);
          font-size: 10px;
          text-align: center;
          padding: 8px;
        }}

        .pageBody--dashboard .watchTileTopRow {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 6px;
        }}

        .pageBody--dashboard .watchTileCode,
        .pageBody--dashboard .watchTileSignal,
        .pageBody--library .libraryThumbLabel {{
          display: inline-flex;
          align-items: center;
          min-height: 20px;
          padding: 0 7px;
          border-radius: 999px;
          background: rgba(7, 9, 20, 0.78);
          border: 1px solid rgba(255,255,255,0.09);
          color: rgba(248, 247, 255, 0.94);
          font-size: 9px;
          font-weight: 760;
          letter-spacing: 0.04em;
          backdrop-filter: blur(10px);
        }}

        .pageBody--dashboard .watchTileSignal {{
          max-width: 120px;
          text-align: right;
          justify-content: center;
          color: rgba(244, 208, 120, 0.94);
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }}

        .pageBody--dashboard .watchTileBody {{
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 6px;
          padding: 5px 7px;
          align-items: center;
        }}

        .pageBody--dashboard .watchTileMain {{
          display: grid;
          gap: 2px;
          min-width: 0;
        }}

        .pageBody--dashboard .watchTileSide {{
          display: grid;
          gap: 2px;
          align-content: center;
          justify-items: end;
          min-width: 62px;
        }}

        .pageBody--dashboard .watchTileTitle {{
          font-size: 11px;
          font-weight: 860;
          line-height: 1.2;
          letter-spacing: -0.02em;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }}

        .pageBody--dashboard .watchTileSubtitle {{
          color: rgba(255,255,255,0.56);
          font-size: 9px;
          line-height: 1.25;
          display: -webkit-box;
          -webkit-line-clamp: 1;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }}

        .pageBody--dashboard .watchTilePriceBlock {{
          display: grid;
          gap: 2px;
          min-width: 0;
          justify-items: end;
        }}

        .pageBody--dashboard .watchTilePriceLabel {{
          color: rgba(255,255,255,0.44);
          font-size: 8px;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }}

        .pageBody--dashboard .watchTilePriceValue {{
          color: rgba(250, 247, 255, 0.98);
          font-size: 10.5px;
          font-weight: 860;
          line-height: 1;
          white-space: nowrap;
        }}

        .pageBody--dashboard .watchTileBottomRow {{
          display: none;
        }}

        .pageBody--dashboard .watchTileProgressLabel {{
          display: none;
        }}

        .pageBody--dashboard .watchTileProgress {{
          display: none;
        }}

        .pageBody--dashboard .watchTileMeter {{
          padding-top: 3px;
        }}

        @keyframes meterGoldShimmer {{
          0%   {{ background-position: 200% center; }}
          100% {{ background-position: -200% center; }}
        }}

        .pageBody--dashboard .watchTileMeterTrack {{
          height: 4px;
          border-radius: 2px;
          background: rgba(255,255,255,0.10);
          overflow: hidden;
        }}

        .pageBody--dashboard .watchTileMeterFill {{
          height: 100%;
          border-radius: 2px;
          background: linear-gradient(90deg, rgba(100,200,160,0.55), rgba(66,214,138,0.80));
          transition: width 0.4s ease;
        }}

        .pageBody--dashboard .watchTile.deal3 .watchTileMeterFill {{
          background: linear-gradient(90deg,
            rgba(66,214,138,0.55) 0%,
            rgba(244,208,120,0.90) 40%,
            rgba(255,236,160,0.96) 55%,
            rgba(244,208,120,0.90) 70%,
            rgba(66,214,138,0.55) 100%);
          background-size: 300% 100%;
          animation: meterGoldShimmer 3.2s linear infinite;
          box-shadow: 0 0 6px rgba(244,208,120,0.36), 0 0 2px rgba(66,214,138,0.22);
        }}

        .pageBody--dashboard .watchTile.deal3 {{
          border-color: rgba(244,208,120,0.22);
          box-shadow:
            0 12px 26px rgba(0,0,0,0.22),
            0 0 12px rgba(244,208,120,0.08) inset;
        }}

        .pageBody--dashboard .watchTile.deal2 .watchTileMeterFill {{
          background: linear-gradient(90deg, rgba(66,214,138,0.72), rgba(86,225,148,0.94));
        }}

        .pageBody--dashboard .watchTile.deal1 .watchTileMeterFill {{
          background: linear-gradient(90deg, rgba(100,200,155,0.58), rgba(100,200,155,0.82));
        }}

        .pageBody--dashboard .watchTile.over .watchTileMeterFill {{
          background: linear-gradient(90deg, rgba(255,100,70,0.32), rgba(255,100,70,0.52));
        }}

        .pageBody--dashboard .watchTile.watch .watchTileMeterFill {{
          background: rgba(255,255,255,0.12);
        }}

        .pageBody--dashboard .watchTileBuyLink {{
          display: inline-flex;
          align-items: center;
          justify-content: flex-end;
          font-size: 8px;
          font-weight: 840;
          letter-spacing: 0.03em;
          color: rgba(244,208,120,0.64);
          text-decoration: none;
          white-space: nowrap;
          padding-top: 1px;
          -webkit-tap-highlight-color: transparent;
          transition: color 0.12s ease;
        }}

        .pageBody--dashboard .watchTileBuyLink:hover {{
          color: rgba(244,208,120,0.96);
        }}

        .pageBody--dashboard .watchTileBuyLink--none {{
          color: rgba(255,255,255,0.20);
          cursor: default;
          pointer-events: none;
        }}

        .watchEmpty {{
          display: grid;
          place-items: center;
          min-height: 180px;
          padding: 18px;
          border-radius: 22px;
          border: 1px dashed rgba(244,208,120,0.18);
          background: rgba(255,255,255,0.02);
          color: rgba(232, 225, 244, 0.74);
          text-align: center;
          line-height: 1.5;
        }}

        .pageBody--library .libraryGrid--browse {{
          grid-template-columns: repeat(auto-fill, minmax(110px, 1fr));
          gap: 9px;
        }}

        .pageBody--library .libraryCard--browse {{
          border-radius: 8px;
          min-height: 0;
          overflow: hidden;
        }}

        .pageBody--library .libraryThumbWrap {{
          position: relative;
          aspect-ratio: 0.64;
          background: linear-gradient(180deg, rgba(10, 10, 18, 0.96), rgba(6, 7, 12, 1));
        }}

        .pageBody--library .libraryThumb--browse {{
          width: 100%;
          height: 100%;
          padding: 4px;
          object-fit: contain;
          box-shadow: 0 2px 6px rgba(0,0,0,0.25), 0 1px 2px rgba(0,0,0,0.18);
        }}

        .pageBody--library .libraryThumbOverlay {{
          position: absolute;
          inset: 5px 5px auto 5px;
          display: flex;
          justify-content: space-between;
          gap: 4px;
          pointer-events: none;
        }}

        .pageBody--library .libraryThumbMeta {{
          position: absolute;
          left: 5px;
          right: 5px;
          bottom: 5px;
          display: flex;
          justify-content: flex-start;
          pointer-events: none;
        }}

        .pageBody--library .libraryAltControl {{
          position: absolute;
          right: 6px;
          bottom: 6px;
          pointer-events: auto;
          z-index: 2;
          min-width: 0;
          padding: 2px 6px;
          font-size: 10px;
          line-height: 1.2;
          font-weight: 600;
          color: rgba(255,255,255,0.88);
          background: rgba(0,0,0,0.5);
          border: 1px solid rgba(255,255,255,0.12);
          border-radius: 8px;
          cursor: pointer;
          transition: background 0.15s ease, border-color 0.15s ease;
        }}
        .pageBody--library .libraryAltControl:hover {{
          background: rgba(0,0,0,0.65);
          border-color: rgba(255,255,255,0.2);
        }}

        .pageBody--library .libraryThumb--browse {{
          transition: opacity 0.18s ease, transform 0.18s ease;
        }}
        .pageBody--library .libraryThumb--browse.libraryThumb--switching {{
          opacity: 0.6;
        }}

        .pageBody--library .libraryCode--overlay,
        .pageBody--library .libraryRarity {{
          background: rgba(7, 9, 20, 0.78);
          border-color: rgba(255,255,255,0.08);
          color: rgba(246, 248, 255, 0.9);
        }}

        .pageBody--library .libraryCard--browse .libraryCode--overlay,
        .pageBody--library .libraryCard--browse .libraryRarity {{
          font-size: 8.5px;
          padding: 2px 5px;
          border-radius: 4px;
          background: rgba(7, 9, 20, 0.58);
          border-color: rgba(255,255,255,0.06);
          font-weight: 680;
        }}

        .pageBody--library .libraryCard--browse .libraryThumbLabel {{
          min-height: 16px;
          padding: 0 5px;
          font-size: 8px;
          border-radius: 6px;
          background: rgba(7, 9, 20, 0.62);
          border-color: rgba(255,255,255,0.07);
        }}

        .pageBody--library .libraryCardBody--browse {{
          gap: 2px;
          padding: 5px 6px 6px;
        }}

        .pageBody--library .libraryCardTitle--browse {{
          font-size: 10.75px;
          font-weight: 820;
        }}

        .pageBody--library .libraryCardCodeLine {{
          font-size: 9px;
          color: rgba(255,255,255,0.55);
        }}

        .pageBody--library .libraryCardMetaRow {{
          display: flex;
          align-items: center;
          justify-content: flex-start;
          gap: 4px;
          margin-top: auto;
        }}

        .pageBody--library .libraryPrice {{
          color: rgba(255, 235, 166, 0.92);
          font-size: 9px;
          font-weight: 700;
        }}

        .pageBody--library .libraryPrice--muted {{
          color: rgba(255,255,255,0.48);
        }}

        .detailIdentity {{
          padding: 12px 14px;
        }}

        .detailMediaStack {{
          width: min(100%, 188px);
        }}

        .detailImage {{
          max-height: min(46vh, 280px);
          border-radius: 16px;
        }}

        .detailImagePh {{
          min-height: 220px;
        }}

        .detailInfo {{
          gap: 10px;
        }}

        .detailTextBlock {{
          border-radius: 12px;
          padding: 9px 10px;
        }}

        .detailStatValue {{
          font-size: 11.5px;
        }}

        .detailTextValue {{
          font-size: 12px;
          line-height: 1.42;
        }}

        .detailHint {{
          margin-top: 6px;
          font-size: 10px;
        }}

        @keyframes detailInsightFadeIn {{
          from {{ opacity: 0; }}
          to {{ opacity: 1; }}
        }}

        @keyframes detailInsightReadyShine {{
          0% {{
            box-shadow:
              0 2px 6px rgba(0,0,0,0.26),
              inset 0 1px 0 rgba(255,238,170,0.18),
              inset 0 -1px 0 rgba(0,0,0,0.20),
              0 0 12px rgba(244,208,120,0.18),
              0 0 6px rgba(165,118,255,0.14);
          }}
          50% {{
            box-shadow:
              0 2px 6px rgba(0,0,0,0.26),
              inset 0 1px 0 rgba(255,238,170,0.24),
              inset 0 -1px 0 rgba(0,0,0,0.20),
              0 0 20px rgba(244,208,120,0.32),
              0 0 10px rgba(165,118,255,0.20);
          }}
          100% {{
            box-shadow:
              0 2px 6px rgba(0,0,0,0.26),
              inset 0 1px 0 rgba(255,238,170,0.20),
              inset 0 -1px 0 rgba(0,0,0,0.20),
              0 0 16px rgba(244,208,120,0.24),
              0 0 8px rgba(165,118,255,0.18);
          }}
        }}

        .detailTextBlock--effect {{
          margin-top: 0;
        }}

        .detailInsightStrip {{
          position: relative;
          overflow: hidden;
          border: 1px solid rgba(255,255,255,0.08);
          border-radius: 12px;
          background:
            linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01)),
            linear-gradient(180deg, rgba(22, 18, 36, 0.92), rgba(12, 11, 20, 0.96));
          box-shadow: 0 2px 8px rgba(0,0,0,0.08);
        }}

        .detailInsightStrip[data-state="idle"],
        .detailInsightStrip[data-state="empty"] {{
          display: none;
        }}

        .detailInsightStrip[data-state="ready"],
        .detailInsightStrip[data-state="loading"] {{
          animation: detailInsightFadeIn 0.35s ease;
        }}

        .detailInsightStrip[data-category="meta"] {{
          border-left: 3px solid rgba(100, 160, 255, 0.45);
          box-shadow: 0 2px 8px rgba(0,0,0,0.08), -2px 0 12px rgba(100, 160, 255, 0.08);
        }}

        .detailInsightStrip[data-category="lore"] {{
          border-left: 3px solid rgba(244, 208, 120, 0.45);
          box-shadow: 0 2px 8px rgba(0,0,0,0.08), -2px 0 12px rgba(244, 208, 120, 0.08);
        }}

        .detailInsightStrip[data-category="market"],
        .detailInsightStrip[data-category="price"] {{
          border-left: 3px solid rgba(140, 220, 160, 0.45);
          box-shadow: 0 2px 8px rgba(0,0,0,0.08), -2px 0 12px rgba(140, 220, 160, 0.08);
        }}

        .detailInsightStrip[data-category="strategy"],
        .detailInsightStrip[data-category="synergy"] {{
          border-left: 3px solid rgba(180, 140, 255, 0.45);
          box-shadow: 0 2px 8px rgba(0,0,0,0.08), -2px 0 12px rgba(180, 140, 255, 0.08);
        }}

        .detailInsightStrip[data-category="usage"] {{
          border-left: 3px solid rgba(120, 200, 240, 0.45);
          box-shadow: 0 2px 8px rgba(0,0,0,0.08), -2px 0 12px rgba(120, 200, 240, 0.08);
        }}

        .miruInsightStripToggle {{
          display: flex;
          align-items: center;
          gap: 8px;
          width: 100%;
          min-height: 40px;
          padding: 8px 12px;
          border: none;
          border-radius: 12px;
          background: transparent;
          color: rgba(248,250,255,0.9);
          font-size: 12px;
          line-height: 1.35;
          text-align: left;
          cursor: pointer;
          -webkit-tap-highlight-color: transparent;
        }}

        .miruInsightStripToggle:hover {{
          background: rgba(255,255,255,0.04);
        }}

        .miruInsightStripLabel {{
          flex-shrink: 0;
          font-weight: 700;
          font-size: 10px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
          color: rgba(244,208,120,0.88);
        }}

        .miruInsightStripTeaser {{
          flex: 1 1 auto;
          min-width: 0;
          overflow: hidden;
          display: -webkit-box;
          -webkit-box-orient: vertical;
          -webkit-line-clamp: 2;
          line-height: 1.35;
          white-space: normal;
          word-break: break-word;
          color: rgba(232,228,248,0.78);
        }}

        .miruInsightStripChevron {{
          flex-shrink: 0;
          width: 0;
          height: 0;
          border-left: 5px solid transparent;
          border-right: 5px solid transparent;
          border-top: 6px solid rgba(255,255,255,0.5);
          transition: transform 0.2s ease;
        }}

        .miruInsightStripToggle[aria-expanded="true"] .miruInsightStripChevron {{
          transform: rotate(180deg);
        }}

        .miruInsightStripContent {{
          padding: 0 12px 10px 12px;
          border-top: 1px solid rgba(255,255,255,0.06);
        }}

        .miruInsightStripContent[hidden] {{
          display: none;
        }}

        .miruInsightStripContent .miruInsightSections {{
          padding-top: 8px;
          display: grid;
          gap: 8px;
        }}

        .miruInsightSection {{
          padding-top: 6px;
          border-top: 1px solid rgba(255,255,255,0.06);
        }}

        .miruInsightSection:first-child {{
          padding-top: 0;
          border-top: none;
        }}

        .miruInsightSectionLabel {{
          font-size: 9px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: rgba(244,208,120,0.55);
          margin-bottom: 4px;
        }}

        .miruInsightSectionText {{
          font-size: 12px;
          line-height: 1.45;
          color: rgba(232,228,248,0.86);
        }}

        .detailInsightMeta {{
          margin-top: 6px;
          font-size: 10px;
          color: rgba(255,255,255,0.4);
        }}

        .detailInsightStrip[data-state="loading"] .miruInsightStripTeaser {{
          min-height: 14px;
          color: rgba(255,255,255,0.4);
        }}

        .detailWatchlistSection {{
          border-radius: 12px;
          border: 1px solid rgba(255,255,255,0.08);
          background: rgba(255,255,255,0.02);
          overflow: hidden;
        }}

        .detailWatchlistToggle {{
          display: flex;
          align-items: center;
          justify-content: space-between;
          width: 100%;
          min-height: 40px;
          padding: 8px 12px;
          border: none;
          border-radius: 0;
          background: transparent;
          color: rgba(255,255,255,0.78);
          font-size: 12px;
          font-weight: 600;
          text-align: left;
          cursor: pointer;
          -webkit-tap-highlight-color: transparent;
        }}

        .detailWatchlistToggle:hover {{
          background: rgba(255,255,255,0.04);
        }}

        .detailWatchlistToggleLabel {{
          flex: 1;
        }}

        .detailWatchlistToggleChevron {{
          flex-shrink: 0;
          width: 0;
          height: 0;
          border-left: 5px solid transparent;
          border-right: 5px solid transparent;
          border-top: 6px solid rgba(255,255,255,0.45);
          transition: transform 0.2s ease;
        }}

        .detailWatchlistSection:not(.detailWatchlistSection--collapsed) .detailWatchlistToggleChevron {{
          transform: rotate(180deg);
        }}

        .detailWatchlistContent {{
          overflow: hidden;
          transition: max-height 0.25s ease;
        }}

        .detailWatchlistSection--collapsed .detailWatchlistContent {{
          max-height: 0;
          padding-top: 0;
          padding-bottom: 0;
          margin: 0;
          border: none;
          opacity: 0;
          pointer-events: none;
        }}

        .detailWatchlistSection:not(.detailWatchlistSection--collapsed) .detailWatchlistContent {{
          max-height: 360px;
          overflow: auto;
        }}

        .detailWatchlistContent .detailActionHub {{
          border-radius: 0;
          border: none;
          border-top: 1px solid rgba(255,255,255,0.06);
          box-shadow: none;
          padding: 10px 12px;
        }}

        @media (prefers-reduced-motion: reduce) {{
          .detailInsightStrip[data-state="ready"],
          .detailInsightStrip[data-state="loading"] {{
            animation: none;
          }}
          .detailWatchlistContent {{
            transition: none;
          }}
          .pageBody--library .libraryThumbFlip .libraryThumb {{
            transition-duration: 0s;
          }}
          .detailImageFlip .detailImage {{
            transition-duration: 0s;
          }}
        }}

        .detailLeaderGrid {{
          grid-template-columns: repeat(3, minmax(0, 1fr));
        }}

        .microAlertStack {{
          position: fixed;
          top: 14px;
          right: 14px;
          z-index: 1200;
          display: grid;
          gap: 8px;
          pointer-events: none;
        }}

        .microAlert {{
          min-width: min(280px, calc(100vw - 28px));
          max-width: min(320px, calc(100vw - 28px));
          padding: 10px 12px;
          border-radius: 14px;
          border: 1px solid rgba(255,255,255,0.08);
          background:
            linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.012)),
            linear-gradient(180deg, rgba(18, 12, 31, 0.96), rgba(10, 10, 20, 0.98));
          color: rgba(248,250,255,0.96);
          font-size: 12px;
          line-height: 1.4;
          box-shadow: 0 18px 36px rgba(0,0,0,0.28);
          opacity: 0;
          transform: translate3d(24px, 0, 0);
          transition: opacity 0.22s ease, transform 0.22s ease;
        }}

        .microAlert[data-state="live"] {{
          opacity: 1;
          transform: translate3d(0, 0, 0);
        }}

        .microAlert[data-state="leaving"] {{
          opacity: 0;
          transform: translate3d(18px, 0, 0);
        }}

        .microAlert[data-tone="success"] {{
          border-color: rgba(120,255,190,0.28);
        }}

        .microAlert[data-tone="accent"] {{
          border-color: rgba(244,208,120,0.28);
        }}

        .microAlert[data-tone="error"] {{
          border-color: rgba(255, 120, 120, 0.28);
        }}

        .microAlert[data-tone="muted"] {{
          color: rgba(232, 225, 244, 0.76);
        }}

        .miruHelper {{
          display: none !important;
        }}

        @media (min-width: 1200px) {{
          .pageBody--dashboard .watchGrid {{
            grid-template-columns: repeat(auto-fill, minmax(178px, 1fr));
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
          }}
        }}

        @media (max-width: 640px) {{
          .pageBody--dashboard .watchGrid {{
            grid-template-columns: 1fr;
            gap: 6px;
            align-items: start;
            align-content: start;
            grid-auto-rows: max-content;
          }}

          .libraryGrid--browse {{
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
          }}

          .detailLeaderGrid {{
            grid-template-columns: 1fr;
          }}

          .detailIdentity {{
            padding: 8px 10px;
            gap: 10px;
            margin-bottom: 8px;
          }}

          .detailMediaStack,
          .detailImage,
          .detailImagePh {{
            width: 112px;
          }}

          .detailImage {{
            max-height: 164px;
          }}

          .detailImagePh {{
            min-height: 164px;
          }}

          .microAlertStack {{
            left: 12px;
            right: 12px;
            top: auto;
            bottom: 12px;
          }}

          .microAlert {{
            min-width: 0;
            max-width: none;
          }}
        }}

        @media (max-width: 640px) {{
          body {{
            padding: 10px;
          }}

          .brandHero {{
            padding: 16px 12px 10px;
            border-radius: 20px;
            margin-bottom: 12px;
          }}

          .brandMark {{
            width: 60px;
            height: 60px;
          }}

          .brandTitle {{
            font-size: clamp(22px, 5.5vw, 26px);
          }}

          .heroNav {{
            margin-top: 10px;
            padding: 5px;
            gap: 5px;
          }}

          .heroNavLink {{
            min-height: 28px;
            font-size: 11px;
            padding: 0 9px;
            border-radius: 10px;
          }}

          .pageBody--dashboard .homeQuickNav {{
            display: none;
          }}

          .pageBody--dashboard .libraryIntro--watchlist {{
            margin-top: 2px;
            margin-bottom: 6px;
          }}
        }}

      </style>
    </head>
    <body class="{body_class}">
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
        <div id="cardDetailDialog" class="modalCard" role="dialog" aria-modal="true" aria-labelledby="cardDetailTitle">
          <div class="modalInner">
            <div class="modalTop">
              <div>
                <div id="cardDetailKicker" class="modalKicker">Card details</div>
                <h3 id="cardDetailTitle" class="modalTitle">Card Details</h3>
                <div id="cardDetailSubtitle" class="modalSubtitle"></div>
              </div>
              <button id="cardDetailClose" class="closeBtn" type="button" aria-label="Close card details">Close</button>
            </div>

            <div class="detailLayout">
              <div class="detailIdentity">
                <div id="cardDetailMedia" class="detailMedia"></div>
                <div id="cardDetailStats" class="detailStats"></div>
                <div id="cardDetailLegalityStats" class="detailStats detailStats--legality"></div>
              </div>
              <div class="detailInfo">
                <div class="detailTextBlock detailTextBlock--effect">
                  <div class="detailTextLabel">Effect</div>
                  <div id="cardDetailEffect" class="detailTextValue"></div>
                </div>
                <div id="cardDetailInsightBlock" class="detailInsightStrip miruInsightPanel" data-state="idle" data-category="" aria-label="Miru Insight">
                  <button type="button" class="miruInsightStripToggle" id="cardDetailInsightToggle" aria-expanded="false" aria-controls="cardDetailInsightContent">
                    <span class="miruInsightStripLabel">Miru</span>
                    <span id="cardDetailInsightTeaser" class="miruInsightStripTeaser"></span>
                    <span class="miruInsightStripChevron" aria-hidden="true"></span>
                  </button>
                  <div id="cardDetailInsightContent" class="miruInsightStripContent" hidden>
                    <div id="cardDetailInsightSections" class="miruInsightSections"></div>
                    <div id="cardDetailInsightMeta" class="detailHint detailInsightMeta" aria-live="polite"></div>
                  </div>
                </div>
                <section id="cardDetailWatchlistSection" class="detailWatchlistSection detailWatchlistSection--collapsed" aria-label="Watchlist">
                  <button type="button" class="detailWatchlistToggle" id="cardDetailWatchlistToggle" aria-expanded="false" aria-controls="cardDetailWatchlistContent">
                    <span class="detailWatchlistToggleLabel">Track this card</span>
                    <span class="detailWatchlistToggleChevron" aria-hidden="true"></span>
                  </button>
                  <div id="cardDetailWatchlistContent" class="detailWatchlistContent">
                    <div class="detailActionHub">
                      <div class="detailActionHeader">
                        <div class="detailActionTitle">Price watch</div>
                        <div class="detailPriceChip">
                          <div class="detailPriceChipLabel">Current</div>
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
                  </div>
                </section>
                <div class="detailTextBlock">
                  <div class="detailTextLabel">Trigger</div>
                  <div id="cardDetailTrigger" class="detailTextValue"></div>
                </div>
                <section id="cardDetailLeaderHub" class="detailLeaderHub" hidden>
                  <div class="detailTextLabel">Leader guide</div>
                  <div class="detailLeaderGrid">
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Summary</div>
                      <div id="cardDetailLeaderOverview" class="detailTextValue"></div>
                    </article>
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Playstyle</div>
                      <div id="cardDetailLeaderPlaystyle" class="detailTextValue"></div>
                    </article>
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Core cards</div>
                      <div id="cardDetailLeaderStrengths" class="detailTextValue"></div>
                    </article>
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Budget path</div>
                      <div id="cardDetailLeaderWeaknesses" class="detailTextValue"></div>
                    </article>
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Variant lanes</div>
                      <div id="cardDetailLeaderCore" class="detailTextValue"></div>
                    </article>
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Engine</div>
                      <div id="cardDetailLeaderFlex" class="detailTextValue"></div>
                    </article>
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Flex slots</div>
                      <div id="cardDetailLeaderVariants" class="detailTextValue"></div>
                    </article>
                    <article class="detailLeaderCard">
                      <div class="detailTextLabel">Current status</div>
                      <div id="cardDetailLeaderMeta" class="detailTextValue"></div>
                    </article>
                  </div>
                  <div id="leaderPageLinkWrap" class="leaderPageLinkWrap" hidden>
                    <a id="leaderPageLink" class="leaderPageBtn" href="#">Open Leader Page</a>
                  </div>
                </section>
                <div class="modalActions">
                  <button id="cardDetailPreviewButton" class="buybtn detailPreviewBtn" type="button">Preview in Miru</button>
                  <a id="cardDetailMarketLink" class="buybtn" href="#" target="_blank" rel="noopener">Open on TCGplayer</a>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div id="cardImageFullscreen" class="cardImageFullscreen" aria-hidden="true" role="dialog" aria-label="Card image full size">
        <div class="cardImageFullscreenBackdrop"></div>
        <div class="cardImageFullscreenInner">
          <img id="cardImageFullscreenImg" src="" alt="" />
          <button type="button" class="cardImageFullscreenClose" aria-label="Close full-size image">×</button>
        </div>
      </div>

      <div id="microAlertStack" class="microAlertStack" aria-live="polite" aria-atomic="true"></div>

      <script>
        /* PERFORMANCE: Library fragment and page load.
         * - Fragment response: server sends Cache-Control (private, max-age=20) so repeat URLs are fast.
         * - After receiving fragment HTML: only innerHTML + libraryLoaded + browse context run immediately;
         *   syncLibraryHistory and persistLibraryFragment are deferred (requestIdleCallback/setTimeout(0))
         *   so the browser can paint new content sooner.
         * - Keep fragment payload minimal; avoid adding heavy work to the fragment .then() path.
         * - data-loading on #card-library shows a minimal "Loading…" without clearing the grid.
         */
        const modal = document.getElementById("cardDetailModal");
        const modalDialog = document.getElementById("cardDetailDialog");
        const modalKicker = document.getElementById("cardDetailKicker");
        const closeBtn = document.getElementById("cardDetailClose");
        const titleNode = document.getElementById("cardDetailTitle");
        const subtitleNode = document.getElementById("cardDetailSubtitle");
        const mediaNode = document.getElementById("cardDetailMedia");
        let modalScrollRestore = 0;
        const currentPriceNode = document.getElementById("cardDetailCurrentPrice");
        const targetPriceInput = document.getElementById("cardDetailTargetPrice");
        const watchButton = document.getElementById("cardDetailWatchButton");
        const watchFeedback = document.getElementById("cardDetailWatchFeedback");
        const statsNode = document.getElementById("cardDetailStats");
        const effectNode = document.getElementById("cardDetailEffect");
        const triggerNode = document.getElementById("cardDetailTrigger");
        const insightBlockNode = document.getElementById("cardDetailInsightBlock");
        const insightSectionsNode = document.getElementById("cardDetailInsightSections");
        const insightMetaNode = document.getElementById("cardDetailInsightMeta");
        const insightTeaserNode = document.getElementById("cardDetailInsightTeaser");
        const insightToggleBtn = document.getElementById("cardDetailInsightToggle");
        const insightContentNode = document.getElementById("cardDetailInsightContent");
        const watchlistSectionNode = document.getElementById("cardDetailWatchlistSection");
        const watchlistToggleBtn = document.getElementById("cardDetailWatchlistToggle");
        const watchlistContentNode = document.getElementById("cardDetailWatchlistContent");
        const leaderHubNode = document.getElementById("cardDetailLeaderHub");
        const leaderOverviewNode = document.getElementById("cardDetailLeaderOverview");
        const leaderPlaystyleNode = document.getElementById("cardDetailLeaderPlaystyle");
        const leaderStrengthsNode = document.getElementById("cardDetailLeaderStrengths");
        const leaderWeaknessesNode = document.getElementById("cardDetailLeaderWeaknesses");
        const leaderCoreNode = document.getElementById("cardDetailLeaderCore");
        const leaderFlexNode = document.getElementById("cardDetailLeaderFlex");
        const leaderVariantsNode = document.getElementById("cardDetailLeaderVariants");
        const leaderMetaNode = document.getElementById("cardDetailLeaderMeta");
        const leaderPageLinkWrap = document.getElementById("leaderPageLinkWrap");
        const leaderPageLink = document.getElementById("leaderPageLink");
        const marketLink = document.getElementById("cardDetailMarketLink");
        const previewButton = document.getElementById("cardDetailPreviewButton");
        const cardImageFullscreen = document.getElementById("cardImageFullscreen");
        const cardImageFullscreenImg = document.getElementById("cardImageFullscreenImg");
        const cardImageFullscreenClose = document.querySelector(".cardImageFullscreenClose");
        const miruHelperPanel = document.getElementById("miruHelperPanel");
        const miruHelperToggle = document.getElementById("miruHelperToggle");
        const miruHelperContext = document.getElementById("miruHelperContext");
        const miruHelperOpenAi = document.getElementById("miruHelperOpenAi");
        const miruHelperAskCard = document.getElementById("miruHelperAskCard");
        const miruHelperFacts = document.getElementById("miruHelperFacts");
        const microAlertStack = document.getElementById("microAlertStack");
        const previewShell = document.getElementById("previewShell");
        const miruPreviewForm = document.getElementById("miruPreviewForm");
        const miruPreviewQuery = document.getElementById("miruPreviewQuery");
        const miruPreviewTone = document.getElementById("miruPreviewTone");
        const miruPreviewStoryMode = document.getElementById("miruPreviewStoryMode");
        const miruPreviewRunButton = document.getElementById("miruPreviewRunButton");
        const miruPreviewIntent = document.getElementById("miruPreviewIntent");
        const miruPreviewIntentDetail = document.getElementById("miruPreviewIntentDetail");
        const miruPreviewBundle = document.getElementById("miruPreviewBundle");
        const miruPreviewBundleDetail = document.getElementById("miruPreviewBundleDetail");
        const miruPreviewFastPath = document.getElementById("miruPreviewFastPath");
        const miruPreviewFastPathDetail = document.getElementById("miruPreviewFastPathDetail");
        const miruPreviewTruth = document.getElementById("miruPreviewTruth");
        const miruPreviewTruthDetail = document.getElementById("miruPreviewTruthDetail");
        const miruPreviewVoice = document.getElementById("miruPreviewVoice");
        const miruPreviewVoiceDetail = document.getElementById("miruPreviewVoiceDetail");
        const miruPreviewStory = document.getElementById("miruPreviewStory");
        const miruPreviewStoryDetail = document.getElementById("miruPreviewStoryDetail");
        const miruPreviewResponse = document.getElementById("miruPreviewResponse");
        const libraryControls = document.getElementById("libraryControls");
        const libraryFiltersToggle = document.getElementById("libraryFiltersToggle");
        const libraryFilterPanel = document.getElementById("libraryFilterPanel");
        const libraryBrowseContextValue = document.getElementById("libraryBrowseContextValue");
        const libraryShell = document.getElementById("card-library");
        const libraryDeferredContent = document.getElementById("libraryDeferredContent");
        const watchlistGrid = document.getElementById("watchlistGrid");
        const watchlistDeferred = document.getElementById("watchlistDeferred");
        const libraryPagePath = "/library";
        const miruPreviewToneKey = "project-miru:preview-tone";
        const miruPreviewStoryKey = "project-miru:preview-story";
        let lastTrigger = null;
        let currentCardPayload = null;
        let modalAltSources = [];
        let modalAltIndex = 0;
        let libraryLoaded = false;
        let libraryLoadPromise = null;
        let librarySearchTimer = 0;
        const LIBRARY_TAP_DELAY_MS = 380;
        let libraryTapTimer = null;
        let libraryTapCard = null;
        let libraryTapTime = 0;
        let modalTapTimer = null;
        let modalTapTime = 0;
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

        function setCardInsightState(hasContent, meta = "") {{
          if (insightBlockNode) {{
            insightBlockNode.dataset.state = hasContent ? "ready" : "empty";
          }}
          if (insightMetaNode) {{
            insightMetaNode.textContent = String(meta || "").trim();
          }}
        }}

        function pushMicroAlert(message, tone = "info") {{
          if (!microAlertStack || !message) {{
            return;
          }}
          const alert = document.createElement("div");
          alert.className = "microAlert";
          alert.dataset.tone = tone;
          alert.textContent = message;
          microAlertStack.appendChild(alert);
          window.setTimeout(() => {{
            alert.dataset.state = "live";
          }}, 16);
          window.setTimeout(() => {{
            alert.dataset.state = "leaving";
            window.setTimeout(() => {{
              alert.remove();
            }}, 260);
          }}, 2400);
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
          document.querySelectorAll(".watchTile[data-card]").forEach((card) => {{
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
            const priceValues = card.querySelectorAll(".watchTilePriceValue");
            if (priceValues[0] && entry.current_price) {{
              priceValues[0].textContent = entry.current_price;
            }}
            if (priceValues[1]) {{
              priceValues[1].textContent = entry.target_price ? `$${{normalizeMoneyInput(entry.target_price)}}` : "—";
            }}
            const progressLabel = card.querySelector(".watchTileProgressLabel");
            if (progressLabel) {{
              progressLabel.textContent = entry.target_price ? "Updated target" : "Watching";
            }}
            const signal = card.querySelector(".watchTileSignal");
            if (signal) {{
              signal.textContent = entry.target_price ? `Target $${{normalizeMoneyInput(entry.target_price)}}` : "Watching";
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
          const url = new URL("http://127.0.0.1:{MIRU_AI_PORT}/", window.location.origin);
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

        function truncatePreviewText(value, maxLength = 180) {{
          const text = String(value || "").replace(/\\s+/g, " ").trim();
          if (!text) {{
            return "";
          }}
          if (text.length <= maxLength) {{
            return text;
          }}
          return `${{text.slice(0, Math.max(0, maxLength - 1)).trimEnd()}}…`;
        }}

        function setLeaderHubVisible(isVisible) {{
          if (!leaderHubNode) {{
            return;
          }}
          leaderHubNode.hidden = !isVisible;
        }}

        function renderLeaderHub(payload = {{}}) {{
          if (!leaderHubNode) {{
            return;
          }}
          const isLeader = String(payload.card_type || "").trim().toLowerCase() === "leader";
          setLeaderHubVisible(isLeader);
          if (!isLeader) {{
            return;
          }}
          const cardLabel = [payload.code, payload.name].filter(Boolean).join(" ");
          const profileBits = [payload.color, payload.attribute, payload.power].filter((value) => String(value || "").trim());
          const effectText = truncatePreviewText(payload.effect_text || "", 220);
          const triggerText = truncatePreviewText(payload.trigger_text || "", 120);
          renderDetailText(
            leaderOverviewNode,
            `${{cardLabel || "This leader"}} is ready for a focused leader sheet. Current card signals: ${{profileBits.length ? profileBits.join(" • ") : "waiting on a fuller verified profile"}}.`,
            "Leader overview will appear here."
          );
          renderDetailText(
            leaderPlaystyleNode,
            effectText
              ? `Current card text points toward ${{effectText}}`
              : "A fuller playstyle read will land once linked deck and event intel is attached.",
            "Playstyle read will appear here."
          );
          renderDetailText(
            leaderStrengthsNode,
            "Core card groups will show up here once verified deck shells are linked.",
            "Core group will appear here."
          );
          renderDetailText(
            leaderWeaknessesNode,
            "Budget-friendly routes will appear here when linked list data is available.",
            "Budget routes will appear here."
          );
          renderDetailText(
            leaderCoreNode,
            "Variant branches will appear here once verified list clusters are attached.",
            "Variant group will appear here."
          );
          renderDetailText(
            leaderFlexNode,
            "Engine notes stay conservative until supporting cards are linked from real deck samples.",
            "Engine notes will appear here."
          );
          renderDetailText(
            leaderVariantsNode,
            "Flex slots stay open until matchup or event data is linked to this leader.",
            "Flex notes will appear here."
          );
          renderDetailText(
            leaderMetaNode,
            triggerText
              ? `Current note: ${{triggerText}}`
              : "Current status will stay honest here until verified event results and matchup tracking are attached.",
            "Leader notes will appear here."
          );
          if (leaderPageLinkWrap && leaderPageLink && payload.code) {{
            leaderPageLink.href = "/leader/" + encodeURIComponent(String(payload.code).trim().toUpperCase());
            leaderPageLinkWrap.hidden = false;
          }} else if (leaderPageLinkWrap) {{
            leaderPageLinkWrap.hidden = true;
          }}
        }}

        const MIRU_INSIGHT_SECTION_MAP = {{
          "meta": "Meta Role",
          "meta_role": "Meta Role",
          "usage": "Usage Insight",
          "gameplay": "Usage Insight",
          "deck": "Usage Insight",
          "strategy": "Strategy Insight",
          "standout": "Strategy Insight",
          "relevance": "Strategy Insight",
          "market": "Market Insight",
          "price": "Market Insight",
          "lore": "Optional Lore",
          "trivia": "Optional Lore",
        }};
        const MIRU_INSIGHT_SECTION_ORDER = ["Meta Role", "Usage Insight", "Strategy Insight", "Market Insight", "Optional Lore"];

        async function loadCardInsight(cardCode, {{ announce = false }} = {{}}) {{
          const normalizedCode = String(cardCode || "").trim().toUpperCase();
          if (!normalizedCode) {{
            if (insightBlockNode) insightBlockNode.dataset.state = "empty";
            return null;
          }}
          if (insightBlockNode) insightBlockNode.dataset.state = "loading";
          if (insightTeaserNode) insightTeaserNode.textContent = "Loading…";
          try {{
            const response = await fetch(`/api/miru/insights/${{encodeURIComponent(normalizedCode)}}`, {{
              credentials: "same-origin",
            }});
            const payload = await response.json().catch(() => ({{}}));
            const raw = Array.isArray(payload.insights)
              ? payload.insights.filter(ins => String(ins.insight || "").trim())
              : [];
            if (!response.ok || !raw.length) {{
              throw new Error(String(payload.error || "No Miru insight is stored for this card yet."));
            }}
            const MIN_LENGTH = 24;
            const MIN_CONFIDENCE = 0.15;
            function isRelevant(ins) {{
              const text = String(ins.insight || "").trim();
              const conf = Number(ins.confidence);
              if (text.length < MIN_LENGTH) return false;
              if (Number.isFinite(conf) && conf < MIN_CONFIDENCE) return false;
              return true;
            }}
            const relevant = raw.filter(isRelevant);
            const bySection = Object.create(null);
            relevant.forEach(ins => {{
              const type = String(ins.type || "").trim().toLowerCase().replace(/[^a-z0-9_]/g, "_");
              const key = type ? (MIRU_INSIGHT_SECTION_MAP[type] || MIRU_INSIGHT_SECTION_MAP[type.replace(/_/g, "")] || null) : null;
              const label = key || "Insight";
              if (!bySection[label]) bySection[label] = [];
              bySection[label].push(ins);
            }});
            const orderedSections = MIRU_INSIGHT_SECTION_ORDER.filter(lbl => (bySection[lbl] || []).length > 0);
            const extraLabels = Object.keys(bySection).filter(lbl => !MIRU_INSIGHT_SECTION_ORDER.includes(lbl));
            const sectionLabels = orderedSections.length ? orderedSections.concat(extraLabels) : [];
            if (!sectionLabels.length) {{
              throw new Error("No sufficiently relevant Miru insight for this card.");
            }}
            if (insightSectionsNode) {{
              insightSectionsNode.innerHTML = "";
              sectionLabels.forEach(label => {{
                const items = bySection[label] || [];
                const texts = items
                  .sort((a, b) => (Number(b.confidence) || 0) - (Number(a.confidence) || 0))
                  .map(ins => String(ins.insight || "").trim())
                  .filter(Boolean);
                if (!texts.length) return;
                const section = document.createElement("div");
                section.className = "miruInsightSection";
                section.setAttribute("data-section", label);
                const header = document.createElement("div");
                header.className = "miruInsightSectionLabel";
                header.textContent = label;
                const body = document.createElement("div");
                body.className = "miruInsightSectionText";
                body.textContent = texts.join(" ");
                section.appendChild(header);
                section.appendChild(body);
                insightSectionsNode.appendChild(section);
              }});
            }}
            const latest = relevant[0];
            const updatedAt = String(latest.updated_at || "").trim();
            const updatedLabel = updatedAt ? (() => {{
              try {{
                const d = new Date(updatedAt);
                if (!Number.isNaN(d.getTime())) {{
                  const now = new Date();
                  const diff = (now - d) / 86400000;
                  if (diff < 1) return "Updated today";
                  if (diff < 7) return "Updated this week";
                  return "Updated " + d.toLocaleDateString(undefined, {{ month: "short", year: "numeric" }});
                }}
              }} catch (_) {{}}
              return "";
            }})() : "";
            if (insightMetaNode) {{
              insightMetaNode.textContent = updatedLabel;
            }}
            const firstLabel = sectionLabels[0] || "";
            const categoryMap = {{ "Meta Role": "meta", "Usage Insight": "usage", "Strategy Insight": "strategy", "Market Insight": "market", "Optional Lore": "lore" }};
            const category = categoryMap[firstLabel] || (firstLabel ? firstLabel.toLowerCase().replace(/\\s+/g, "_") : "");
            if (insightBlockNode) {{
              insightBlockNode.dataset.state = "ready";
              insightBlockNode.dataset.category = category;
            }}
            const firstText = (bySection[firstLabel] || [])[0];
            const teaserText = firstText ? String(firstText.insight || "").trim().slice(0, 78) : "";
            if (insightTeaserNode) insightTeaserNode.textContent = teaserText + (teaserText.length >= 78 ? "…" : "");
            if (insightContentNode) insightContentNode.hidden = true;
            if (insightToggleBtn) insightToggleBtn.setAttribute("aria-expanded", "false");
            if (announce) pushMicroAlert("Miru insight ready.", "accent");
            return payload;
          }} catch (_error) {{
            if (insightBlockNode) {{ insightBlockNode.dataset.state = "empty"; insightBlockNode.dataset.category = ""; }}
            if (insightSectionsNode) insightSectionsNode.innerHTML = "";
            if (insightMetaNode) insightMetaNode.textContent = "";
            if (insightTeaserNode) insightTeaserNode.textContent = "";
            if (insightContentNode) insightContentNode.hidden = true;
            if (insightToggleBtn) insightToggleBtn.setAttribute("aria-expanded", "false");
            return null;
          }}
        }}

        async function loadCardLegality(cardCode) {{
          const normalizedCode = String(cardCode || "").trim().toUpperCase();
          const legalityNode = document.getElementById("cardDetailLegalityStats");
          if (legalityNode) legalityNode.innerHTML = "";
          if (!normalizedCode || !legalityNode) return;
          try {{
            const response = await fetch(`/api/miru/legality/${{encodeURIComponent(normalizedCode)}}`, {{
              credentials: "same-origin",
            }});
            if (!response.ok) return;
            const data = await response.json().catch(() => ({{}}));
            const blockNum = String(data.block_number || "").trim();
            const banStatus = String(data.ban_status || "").trim();
            const restrictCount = String(data.restriction_count || "").trim();
            if (!blockNum && !banStatus && !restrictCount) return;
            if (blockNum) legalityNode.appendChild(renderStat("Block", blockNum));
            if (banStatus) {{
              const stat = renderStat("Ban Status", banStatus);
              const valNode = stat.querySelector(".detailStatValue");
              if (valNode) {{
                const lower = banStatus.toLowerCase();
                if (lower === "banned") valNode.classList.add("detailStatValue--banned");
                else if (lower === "restricted") valNode.classList.add("detailStatValue--restricted");
              }}
              legalityNode.appendChild(stat);
            }}
            if (restrictCount) legalityNode.appendChild(renderStat("Copies", restrictCount));
          }} catch (_) {{}}
        }}

        function loadMiruPreviewPreferences() {{
          if (!window.localStorage) {{
            return;
          }}
          try {{
            const savedTone = String(window.localStorage.getItem(miruPreviewToneKey) || "").trim();
            const savedStory = String(window.localStorage.getItem(miruPreviewStoryKey) || "").trim();
            if (miruPreviewTone && savedTone) {{
              miruPreviewTone.value = savedTone;
            }}
            if (miruPreviewStoryMode && savedStory) {{
              miruPreviewStoryMode.value = savedStory;
            }}
          }} catch (_error) {{
          }}
        }}

        function persistMiruPreviewPreferences() {{
          if (!window.localStorage) {{
            return;
          }}
          try {{
            if (miruPreviewTone) {{
              window.localStorage.setItem(miruPreviewToneKey, miruPreviewTone.value);
            }}
            if (miruPreviewStoryMode) {{
              window.localStorage.setItem(miruPreviewStoryKey, miruPreviewStoryMode.value);
            }}
          }} catch (_error) {{
          }}
        }}

        function renderMiruPreview(payload = {{}}) {{
          if (!miruPreviewResponse) {{
            return;
          }}
          const intent = payload.intent || {{}};
          const factBundle = payload.fact_bundle || {{}};
          const fastPath = payload.fast_path || {{}};
          const truthState = payload.truth_state || {{}};
          const voiceMode = payload.voice_mode || {{}};
          const storyMode = payload.story_mode || {{}};
          miruPreviewIntent.textContent = String(intent.label || "Awaiting query");
          miruPreviewIntentDetail.textContent = String(intent.detail || "Miru will keep intent routing compact and readable.");
          miruPreviewBundle.textContent = String(factBundle.label || "Read-only planning");
          miruPreviewBundleDetail.textContent = String(factBundle.detail || "Verified and reference layers stay separate.");
          miruPreviewFastPath.textContent = String(fastPath.label || "No fast path");
          miruPreviewFastPathDetail.textContent = String(fastPath.detail || "Miru will report the cleanest source it used.");
          miruPreviewTruth.textContent = String(truthState.label || "Waiting");
          miruPreviewTruthDetail.textContent = String(truthState.detail || "Truth state will appear here.");
          miruPreviewTruth.dataset.tone = String(truthState.tone || "");
          miruPreviewVoice.textContent = String(voiceMode.label || "Neutral");
          miruPreviewVoiceDetail.textContent = String(voiceMode.detail || "Tone only changes phrasing.");
          miruPreviewStory.textContent = String(storyMode.label || "Off");
          miruPreviewStoryDetail.textContent = String(storyMode.note || storyMode.detail || "Optional, spoiler-aware, and secondary.");
          miruPreviewResponse.textContent = String(payload.response_preview || "Ask about a card code, leader, print, or effect to preview Miru's answer framing.");
        }}

        async function requestMiruPreview(queryOverride = "") {{
          if (!miruPreviewForm || !miruPreviewRunButton || !miruPreviewQuery) {{
            return null;
          }}
          const query = String(queryOverride || miruPreviewQuery.value || "").trim();
          persistMiruPreviewPreferences();
          miruPreviewRunButton.disabled = true;
          miruPreviewRunButton.textContent = "Previewing…";
          try {{
            const response = await fetch("/api/miru-preview", {{
              method: "POST",
              headers: {{
                "Content-Type": "application/json",
              }},
              credentials: "same-origin",
              body: JSON.stringify({{
                query,
                tone: miruPreviewTone ? miruPreviewTone.value : "neutral",
                story_mode: miruPreviewStoryMode ? miruPreviewStoryMode.value : "off",
              }}),
            }});
            const payload = await response.json().catch(() => ({{ ok: false }}));
            if (!response.ok || payload.ok === false) {{
              throw new Error(String(payload.error || "Miru preview is unavailable right now."));
            }}
            renderMiruPreview(payload);
            return payload;
          }} catch (error) {{
            renderMiruPreview({{
              intent: {{ label: "Preview unavailable", detail: "The read-only preview endpoint did not answer cleanly." }},
              fact_bundle: {{ label: "Read-only planning", detail: "Miru preview did not complete this request." }},
              fast_path: {{ label: "Unavailable", detail: "Try an exact card code like OP01-001." }},
              truth_state: {{ label: "Unavailable", tone: "empty", detail: error instanceof Error ? error.message : "Preview request failed." }},
              voice_mode: {{ label: miruPreviewTone ? miruPreviewTone.options[miruPreviewTone.selectedIndex].text : "Neutral", detail: "Tone setting was preserved." }},
              story_mode: {{ label: miruPreviewStoryMode ? miruPreviewStoryMode.options[miruPreviewStoryMode.selectedIndex].text : "Off", detail: "Story-note setting was preserved." }},
              response_preview: error instanceof Error ? error.message : "Miru preview request failed.",
            }});
            return null;
          }} finally {{
            miruPreviewRunButton.disabled = false;
            miruPreviewRunButton.textContent = "Preview answer";
          }}
        }}

        function focusMiruPreview(query = "", runPreview = false) {{
          if (!previewShell || !miruPreviewQuery) {{
            return;
          }}
          if (query) {{
            miruPreviewQuery.value = query;
          }}
          previewShell.scrollIntoView({{ behavior: "smooth", block: "start" }});
          miruPreviewQuery.focus({{ preventScroll: true }});
          if (runPreview) {{
            void requestMiruPreview(query);
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
          syncWatchActionUi(payload || {{}});
          const isLeader = String(payload.card_type || "").trim().toLowerCase() === "leader";
          if (modalKicker) {{
            modalKicker.textContent = isLeader ? "Leader hub" : "Card details";
          }}
          if (modalDialog) {{
            modalDialog.classList.toggle("is-leader", isLeader);
          }}
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
            const flipWrap = document.createElement("div");
            flipWrap.className = "detailImageFlip";
            flipWrap.appendChild(image);
            mediaStack.appendChild(flipWrap);
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
            const sources = payload.image_sources;
            if (Array.isArray(sources) && sources.length > 1) {{
              let idx = 0;
              for (let i = 0; i < sources.length; i++) {{
                if (String(sources[i].detail_src || "").trim() === detailSrc) {{ idx = i; break; }}
              }}
              modalAltSources = sources;
              modalAltIndex = idx;
              const wrap = document.createElement("div");
              wrap.className = "detailAltControlWrap";
              const btn = document.createElement("button");
              btn.type = "button";
              btn.className = "detailAltControl";
              btn.setAttribute("aria-label", "Switch art (" + (idx + 1) + " of " + sources.length + ")");
              btn.textContent = (idx + 1) + " / " + sources.length;
              wrap.appendChild(btn);
              mediaStack.appendChild(wrap);
            }} else {{
              modalAltSources = [];
              modalAltIndex = 0;
            }}
          }} else {{
            modalAltSources = [];
            modalAltIndex = 0;
            const ph = document.createElement("div");
            ph.className = "detailImagePh";
            ph.textContent = "No card image available yet.";
            mediaStack.appendChild(ph);
          }}
          mediaNode.appendChild(mediaStack);

          statsNode.innerHTML = "";
          [
            ["Code", payload.code],
            ["Set", payload.set_name],
            ["Type", payload.card_type],
            ["Color", payload.color],
            ["Rarity", payload.rarity],
            ["Cost", payload.cost],
            ["Power", payload.power],
            ["Counter", payload.counter]
          ].forEach(([label, value]) => statsNode.appendChild(renderStat(label, value)));

          renderDetailText(effectNode, payload.effect_text, "No effect text recorded yet.");
          renderDetailText(triggerNode, payload.trigger_text, "No trigger text recorded.");
          if (insightBlockNode) {{
            insightBlockNode.dataset.state = "idle";
            insightBlockNode.dataset.category = "";
          }}
          if (insightTeaserNode) insightTeaserNode.textContent = "";
          if (insightContentNode) insightContentNode.hidden = true;
          if (insightToggleBtn) insightToggleBtn.setAttribute("aria-expanded", "false");
          if (watchlistSectionNode) {{
            watchlistSectionNode.classList.add("detailWatchlistSection--collapsed");
            if (watchlistToggleBtn) watchlistToggleBtn.setAttribute("aria-expanded", "false");
          }}
          void loadCardInsight(payload.code || "");
          void loadCardLegality(payload.code || "");
          renderLeaderHub(payload || {{}});

          if ((payload.market_url || "").trim()) {{
            marketLink.href = payload.market_url;
            marketLink.style.display = "inline-block";
          }} else {{
            marketLink.removeAttribute("href");
            marketLink.style.display = "none";
          }}
          if (previewButton) {{
            previewButton.hidden = !Boolean((payload.code || payload.name || "").trim());
          }}

          modal.classList.add("open");
          modal.setAttribute("aria-hidden", "false");
          modalScrollRestore = window.scrollY || window.pageYOffset || 0;
          document.body.style.setProperty("--modal-scroll-top", `-${{modalScrollRestore}}px`);
          document.body.classList.add("modalOpen");
          closeBtn.focus();
        }}

        function closeCardDetail() {{
          const restore = modalScrollRestore;
          modal.classList.remove("open");
          modal.setAttribute("aria-hidden", "true");
          document.body.classList.remove("modalOpen");
          document.body.style.removeProperty("--modal-scroll-top");
          if (restore) {{
            window.scrollTo(0, restore);
          }}
          modalScrollRestore = 0;
          if (lastTrigger) {{
            lastTrigger.focus();
          }}
        }}

        function reapplyModalScrollLock() {{
          if (!document.body.classList.contains("modalOpen")) {{ return; }}
          const scrollY = window.scrollY || window.pageYOffset || 0;
          modalScrollRestore = scrollY;
          document.body.style.setProperty("--modal-scroll-top", `-${{scrollY}}px`);
        }}

        document.addEventListener("visibilitychange", () => {{
          if (document.visibilityState === "visible") {{
            reapplyModalScrollLock();
          }}
        }});

        function openCardImageFullscreen(src, alt) {{
          if (!cardImageFullscreen || !cardImageFullscreenImg) {{ return; }}
          cardImageFullscreenImg.src = src || "";
          cardImageFullscreenImg.alt = alt || "Card image";
          cardImageFullscreen.setAttribute("aria-hidden", "false");
          cardImageFullscreen.classList.add("open");
        }}

        function closeCardImageFullscreen() {{
          if (!cardImageFullscreen) {{ return; }}
          cardImageFullscreen.classList.remove("open");
          cardImageFullscreen.setAttribute("aria-hidden", "true");
          if (cardImageFullscreenImg) {{ cardImageFullscreenImg.src = ""; }}
        }}

        if (mediaNode) {{
          mediaNode.addEventListener("click", (event) => {{
            const altBtn = event.target.closest(".detailAltControl");
            if (altBtn && modalAltSources.length > 1) {{
              event.preventDefault();
              event.stopPropagation();
              modalAltIndex = (modalAltIndex + 1) % modalAltSources.length;
              const nextSrc = modalAltSources[modalAltIndex];
              const thumbSrc = String(nextSrc.thumb_src || "").trim();
              const detailSrc = String(nextSrc.detail_src || "").trim();
              if (currentCardPayload) {{
                currentCardPayload.image_src = detailSrc;
                currentCardPayload.image_thumb_src = thumbSrc;
              }}
              const stack = altBtn.closest(".detailMediaStack");
              const img = stack && stack.querySelector(".detailImage");
              if (img && thumbSrc) {{
                img.src = thumbSrc;
                if (detailSrc && detailSrc !== thumbSrc) {{
                  const fullImage = new Image();
                  fullImage.onload = () => {{ img.src = detailSrc; }};
                  fullImage.src = detailSrc;
                }}
              }}
              altBtn.textContent = (modalAltIndex + 1) + " / " + modalAltSources.length;
              altBtn.setAttribute("aria-label", "Switch art (" + (modalAltIndex + 1) + " of " + modalAltSources.length + ")");
              return;
            }}
            const img = event.target;
            if (!img || img.tagName !== "IMG" || !img.classList.contains("detailImage")) {{ return; }}
            const payload = currentCardPayload || {{}};
            const detailSrc = String(payload.image_src || "").trim();
            const thumbSrc = String(payload.image_thumb_src || "").trim();
            const src = detailSrc || thumbSrc;
            if (!src) {{ return; }}
            event.preventDefault();
            event.stopPropagation();
            const isDoubleTap = (Date.now() - modalTapTime) < LIBRARY_TAP_DELAY_MS;
            if (modalAltSources.length > 1 && isDoubleTap) {{
              if (modalTapTimer) {{ clearTimeout(modalTapTimer); modalTapTimer = null; }}
              modalTapTime = 0;
              modalAltIndex = (modalAltIndex + 1) % modalAltSources.length;
              const nextSrc = modalAltSources[modalAltIndex];
              const nextThumb = String(nextSrc.thumb_src || "").trim();
              const nextDetail = String(nextSrc.detail_src || "").trim();
              if (currentCardPayload) {{
                currentCardPayload.image_src = nextDetail;
                currentCardPayload.image_thumb_src = nextThumb;
              }}
              const flipWrap = img.closest(".detailImageFlip");
              if (flipWrap) {{
                flipWrap.classList.add("detailImageFlip--phase1");
                let phase1Done = false;
                const onPhase1End = () => {{
                  if (phase1Done) return;
                  phase1Done = true;
                  img.removeEventListener("transitionend", onPhase1End);
                  flipWrap.classList.remove("detailImageFlip--phase1");
                  img.src = nextThumb || nextDetail;
                  if (nextDetail && nextDetail !== nextThumb) {{
                    const fullImage = new Image();
                    fullImage.onload = () => {{ img.src = nextDetail; }};
                    fullImage.src = nextDetail;
                  }}
                  const altBtn = flipWrap.closest(".detailMediaStack") && flipWrap.closest(".detailMediaStack").querySelector(".detailAltControl");
                  if (altBtn) {{
                    altBtn.textContent = (modalAltIndex + 1) + " / " + modalAltSources.length;
                    altBtn.setAttribute("aria-label", "Switch art (" + (modalAltIndex + 1) + " of " + modalAltSources.length + ")");
                  }}
                  flipWrap.classList.add("detailImageFlip--phase2");
                  requestAnimationFrame(() => {{
                    requestAnimationFrame(() => {{
                      flipWrap.classList.remove("detailImageFlip--phase2");
                    }});
                  }});
                }};
                img.addEventListener("transitionend", onPhase1End, {{ once: true }});
                setTimeout(onPhase1End, 180);
              }}
              return;
            }}
            if (modalTapTimer) {{ clearTimeout(modalTapTimer); }}
            modalTapTime = Date.now();
            modalTapTimer = setTimeout(() => {{
              modalTapTimer = null;
              openCardImageFullscreen(
                String((currentCardPayload || {{}}).image_src || (currentCardPayload || {{}}).image_thumb_src || "").trim(),
                (currentCardPayload && currentCardPayload.name) ? `${{currentCardPayload.name}} card` : "Card image"
              );
            }}, LIBRARY_TAP_DELAY_MS);
          }});
        }}

        if (cardImageFullscreen) {{
          cardImageFullscreen.addEventListener("click", (event) => {{
            if (event.target === cardImageFullscreen || (event.target && event.target.classList.contains("cardImageFullscreenBackdrop"))) {{
              closeCardImageFullscreen();
            }}
          }});
        }}

        if (cardImageFullscreenClose) {{
          cardImageFullscreenClose.addEventListener("click", (event) => {{
            event.preventDefault();
            event.stopPropagation();
            closeCardImageFullscreen();
          }});
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
            pushMicroAlert(String(result.message || successMessage), "success");
          }} catch (error) {{
            setWatchFeedback(error instanceof Error ? error.message : "Could not save watchlist entry.", "error");
            pushMicroAlert(error instanceof Error ? error.message : "Could not save watchlist entry.", "error");
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
              if (!normalized || (key === "sort" && normalized === "newest_set")) {{
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
          if ((pageTarget.searchParams.get("sort") || "") === "newest_set") {{
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
          if (libraryShell) libraryShell.setAttribute("data-loading", "true");
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
              if (libraryBrowseContextValue) {{
                const p = new URL(url, window.location.origin).searchParams;
                const setVal = (p.get("set") || "").trim();
                const qVal = (p.get("q") || "").trim();
                const sortVal = (p.get("sort") || "newest_set").trim();
                const cardType = (p.get("card_type") || "").trim().toLowerCase();
                let label = "All cards";
                if (cardType === "leader") label = "Leaders";
                else if (setVal) label = "Set: " + setVal;
                else if (qVal) label = "Search results";
                else if (sortVal === "newest_set") label = "Newest set first";
                else if (sortVal === "set_card_asc") label = "Set then card number";
                else if (sortVal === "code_asc") label = "Card code ascending";
                libraryBrowseContextValue.textContent = label;
              }}
              const defer = (fn) => {{
                if (typeof requestIdleCallback !== "undefined") {{
                  requestIdleCallback(fn, {{ timeout: 100 }});
                }} else {{
                  setTimeout(fn, 0);
                }};
              }};
              defer(() => {{
                syncLibraryHistory(url);
                persistLibraryFragment(url, html);
              }});
            }})
            .catch(() => {{
              libraryDeferredContent.innerHTML = '<div class="card libraryDeferredState">Card library is unavailable right now. Reload to try again.</div>';
            }})
            .finally(() => {{
              libraryLoadPromise = null;
              if (libraryShell) libraryShell.removeAttribute("data-loading");
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
            const href = pagerLink.getAttribute("href") || "";
            const targetPage = href ? (function() {{
              try {{
                const u = new URL(href, window.location.origin);
                return u.searchParams.get("library_page") || "";
              }} catch (e) {{ return ""; }}
            }})() : "";
            const fragmentUrl = (href.startsWith("/library-fragment") ? href : buildLibraryFragmentUrl(targetPage)) || href;
            void loadLibraryFragment(fragmentUrl, {{ force: true }});
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
          const altBtn = target.closest(".libraryAltControl");
          if (altBtn) {{
            event.preventDefault();
            event.stopPropagation();
            const card = altBtn.closest(".libraryCard[data-card]");
            if (!card) {{ return; }}
            let sources = [];
            try {{
              sources = JSON.parse(card.dataset.altSources || "[]");
            }} catch (e) {{ return; }}
            if (sources.length < 2) {{ return; }}
            const currentIndex = parseInt(altBtn.dataset.altIndex || "0", 10);
            const nextIndex = (currentIndex + 1) % sources.length;
            const nextSrc = sources[nextIndex];
            const payload = JSON.parse(card.dataset.card || "{{}}");
            payload.image_src = nextSrc.detail_src || "";
            payload.image_thumb_src = nextSrc.thumb_src || "";
            card.dataset.card = JSON.stringify(payload);
            altBtn.dataset.altIndex = String(nextIndex);
            altBtn.textContent = (nextIndex + 1) + "/" + sources.length;
            altBtn.setAttribute("aria-label", "Switch art (" + (nextIndex + 1) + " of " + sources.length + ")");
            const img = card.querySelector(".libraryThumb");
            if (img && nextSrc.thumb_src) {{
              img.classList.add("libraryThumb--switching");
              img.src = nextSrc.thumb_src;
              const onDone = () => {{
                img.classList.remove("libraryThumb--switching");
                img.removeEventListener("load", onDone);
              }};
              img.addEventListener("load", onDone);
              setTimeout(onDone, 220);
            }}
            return;
          }}
          const card = target.closest(".libraryCard[data-card]");
          const onImageArea = card && target.closest(".libraryThumbWrap");
          if (onImageArea) {{
            let sources = [];
            try {{
              sources = JSON.parse(card.dataset.altSources || "[]");
            }} catch (e) {{ sources = []; }}
            const isDoubleTap = card === libraryTapCard && (Date.now() - libraryTapTime) < LIBRARY_TAP_DELAY_MS;
            if (sources.length >= 2 && isDoubleTap) {{
              if (libraryTapTimer) {{ clearTimeout(libraryTapTimer); libraryTapTimer = null; }}
              libraryTapCard = null;
              event.preventDefault();
              event.stopPropagation();
              const flipWrap = card.querySelector(".libraryThumbFlip");
              const img = card.querySelector(".libraryThumb");
              if (flipWrap && img) {{
                const currentIndex = parseInt(card.dataset.altIndex || "0", 10);
                const nextIndex = (currentIndex + 1) % sources.length;
                const nextSrc = sources[nextIndex];
                flipWrap.classList.add("libraryThumbFlip--phase1");
                let phase1Done = false;
                const onPhase1End = () => {{
                  if (phase1Done) return;
                  phase1Done = true;
                  img.removeEventListener("transitionend", onPhase1End);
                  flipWrap.classList.remove("libraryThumbFlip--phase1");
                  img.src = nextSrc.thumb_src || "";
                  const payload = JSON.parse(card.dataset.card || "{{}}");
                  payload.image_src = nextSrc.detail_src || "";
                  payload.image_thumb_src = nextSrc.thumb_src || "";
                  card.dataset.card = JSON.stringify(payload);
                  card.dataset.altIndex = String(nextIndex);
                  const btn = card.querySelector(".libraryAltControl");
                  if (btn) {{
                    btn.dataset.altIndex = String(nextIndex);
                    btn.textContent = (nextIndex + 1) + "/" + sources.length;
                    btn.setAttribute("aria-label", "Switch art (" + (nextIndex + 1) + " of " + sources.length + ")");
                  }}
                  flipWrap.classList.add("libraryThumbFlip--phase2");
                  requestAnimationFrame(() => {{
                    requestAnimationFrame(() => {{
                      flipWrap.classList.remove("libraryThumbFlip--phase2");
                    }});
                  }});
                }};
                img.addEventListener("transitionend", onPhase1End, {{ once: true }});
                setTimeout(onPhase1End, 180);
              }}
              return;
            }}
            if (libraryTapTimer) {{ clearTimeout(libraryTapTimer); }}
            libraryTapCard = card;
            libraryTapTime = Date.now();
            libraryTapTimer = setTimeout(() => {{
              libraryTapTimer = null;
              libraryTapCard = null;
              const payload = JSON.parse(card.dataset.card || "{{}}");
              openCardDetail(payload, card);
            }}, LIBRARY_TAP_DELAY_MS);
            event.preventDefault();
            event.stopPropagation();
            return;
          }}
          const openTarget = target.closest(".viewbtn, .libraryThumbButton, .libraryOpenBtn, .watchTile[data-card], .libraryCard[data-card]");
          if (!openTarget) {{
            return;
          }}
          const payload = JSON.parse(openTarget.dataset.card || "{{}}");
          openCardDetail(payload, openTarget);
        }});

        document.addEventListener("keydown", (event) => {{
          const cardEl = event.target instanceof HTMLElement ? event.target.closest(".libraryCard[data-card], .watchTile[data-card]") : null;
          if (!cardEl || (event.key !== "Enter" && event.key !== " ")) {{
            return;
          }}
          event.preventDefault();
          const payload = JSON.parse(cardEl.dataset.card || "{{}}");
          openCardDetail(payload, cardEl);
        }});

        if (libraryFiltersToggle && libraryFilterPanel) {{
          libraryFiltersToggle.addEventListener("click", () => {{
            const isOpen = libraryFilterPanel.classList.toggle("is-open");
            libraryFiltersToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
          }});
        }}
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

        if (watchButton) {{
          watchButton.addEventListener("click", () => {{
            void saveCurrentCardWatch();
          }});
        }}

        if (insightToggleBtn && insightContentNode) {{
          insightToggleBtn.addEventListener("click", () => {{
            const expanded = insightToggleBtn.getAttribute("aria-expanded") === "true";
            insightContentNode.hidden = expanded;
            insightToggleBtn.setAttribute("aria-expanded", expanded ? "false" : "true");
          }});
        }}

        if (watchlistToggleBtn && watchlistSectionNode && watchlistContentNode) {{
          watchlistToggleBtn.addEventListener("click", () => {{
            const collapsed = watchlistSectionNode.classList.contains("detailWatchlistSection--collapsed");
            watchlistSectionNode.classList.toggle("detailWatchlistSection--collapsed", !collapsed);
            watchlistToggleBtn.setAttribute("aria-expanded", collapsed ? "true" : "false");
          }});
        }}

        if (previewButton) {{
          previewButton.addEventListener("click", () => {{
            const payload = currentCardPayload || {{}};
            const query = [payload.code, payload.name].filter(Boolean).join(" ").trim();
            focusMiruPreview(query || String(payload.code || payload.name || "").trim(), true);
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

        if (miruPreviewForm) {{
          loadMiruPreviewPreferences();
          miruPreviewForm.addEventListener("submit", (event) => {{
            event.preventDefault();
            void requestMiruPreview();
          }});
          if (miruPreviewTone) {{
            miruPreviewTone.addEventListener("change", persistMiruPreviewPreferences);
          }}
          if (miruPreviewStoryMode) {{
            miruPreviewStoryMode.addEventListener("change", persistMiruPreviewPreferences);
          }}
        }}

        closeBtn.addEventListener("click", closeCardDetail);
        modal.addEventListener("click", (event) => {{
          if (event.target === modal) {{
            closeCardDetail();
          }}
        }});
        document.addEventListener("keydown", (event) => {{
          if (event.key !== "Escape") {{ return; }}
          if (cardImageFullscreen && cardImageFullscreen.classList.contains("open")) {{
            closeCardImageFullscreen();
            return;
          }}
          if (modal.classList.contains("open")) {{
            closeCardDetail();
          }}
        }});
        setLeaderHubVisible(false);
        scheduleWatchlistLoad();
        scheduleLibraryLoad();
      </script>
    </body>
    </html>
    """

    return Response(page_html, mimetype="text/html")


@app.get("/sets")
def sets():
    return redirect("/library?sort=newest_set", code=302)


@app.get("/leaders")
def leaders():
    """Leader index: list of leaders linking to /leader/<code>. Uses catalog only."""
    catalog = load_catalog_card_index()
    leader_list = []
    for code, card in (catalog or {}).items():
        if str(card.get("card_type") or "").strip().lower() != "leader":
            continue
        name = (card.get("card_name") or code).strip()
        set_name = (card.get("set_name") or "").strip()
        thumb_src = choose_thumbnail_src(name, code, card, width=200)
        leader_list.append({
            "code": code,
            "name": name,
            "set_name": set_name,
            "color": (card.get("color") or "").strip(),
            "thumb_src": thumb_src,
            "sort_reason": f"From {set_name}" if set_name else "In catalog",
        })
    leader_list.sort(key=lambda x: (x["set_name"], x["code"]))
    return render_template("leaders_index.html", leader_list=leader_list)


@app.get("/leader/<leader_code>")
def leader_page(leader_code: str):
    """Dedicated leader page — card stats, Miru insight, and placeholder intelligence sections."""
    code = str(leader_code or "").strip().upper()
    if not code:
        return redirect("/leaders", code=302)
    catalog = load_catalog_card_index()  # TTL-cached; no new DB overhead
    card = catalog.get(code)
    return render_template(
        "leader.html",
        leader_code=code,
        name=(card.get("card_name") or code) if card else code,
        set_name=(card.get("set_name") or "") if card else "",
        color=(card.get("color") or "") if card else "",
        power=(card.get("power") or "") if card else "",
        attribute=(card.get("attribute") or "") if card else "",
        effect_text=(card.get("effect_text") or "") if card else "",
        trigger_text=(card.get("trigger_text") or "") if card else "",
        image_src=(card.get("catalog_image_src") or "") if card else "",
        not_found=card is None,
    )


@app.get("/library-fragment")
def library_fragment():
    # PERFORMANCE: Keep this path light. catalog/library index and load_prices are TTL-cached.
    # Response has Cache-Control for short-term browser cache. Avoid new uncached work here.
    try:
        library_page = max(int(request.args.get("library_page", "1") or 1), 1)
    except Exception:
        library_page = 1
    mode = str(request.args.get("mode", "browse") or "browse").strip().lower()
    browse_mode = mode == "browse"
    # Use fragment path for pager links so the client fetches fragment HTML only (avoids full-page inject and duplicate shell).
    base_path = "/library-fragment" if browse_mode else "/"
    filters = normalize_library_query(request.args)
    query_pairs = []
    if browse_mode:
        query_pairs.append(("mode", "browse"))
    for key in ("q", "set", "color", "rarity", "card_type", "cost", "attribute", "ban_status", "block_number", "sort"):
        value = str(filters.get(key) or "").strip()
        if not value or (key == "sort" and value == LIBRARY_DEFAULT_SORT):
            continue
        query_pairs.append((key, value))
    query_string = "".join(f"&{key}={quote(value)}" for key, value in query_pairs)
    catalog_cards = load_catalog_card_index()
    legality_index = build_legality_index()
    library_cards = filter_and_sort_library_cards(build_library_card_index(catalog_cards), filters, legality_index=legality_index)
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
    response = Response(fragment_html, mimetype="text/html")
    response.headers["Cache-Control"] = "private, max-age=20, stale-while-revalidate=10"
    return response


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
    port = int(os.environ.get("PORT", os.environ.get("PROJECT_MIRU_DASHBOARD_PORT", "8080")))
    app.run(host="0.0.0.0", port=port)
