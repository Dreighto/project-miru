import csv
import html
import json
import math
import os
import re
import sqlite3
import time
import requests
from pathlib import Path
from urllib.parse import quote

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from waitress import serve

try:
    from flask_compress import Compress
except ImportError:
    Compress = None


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent

CATALOG_DB_PATH = Path(
    os.getenv(
        "PROJECT_MIRU_CATALOG_DB_PATH", str(PROJECT_ROOT / "data" / "card_catalog.db")
    )
)
PRICES_PATH = Path(
    os.getenv("PROJECT_MIRU_PRICES_PATH", str(PROJECT_ROOT / "data" / "prices.json"))
)
MIRU_ASSETS = Path(os.getenv("PROJECT_MIRU_CLEAN_THUMB_ROOT", r"D:\Miru_Assets"))
MIRU_RUNTIME_IMAGES_ROOT = Path(
    os.getenv("MIRU_RUNTIME_IMAGES_ROOT", r"D:\Miru_Assets")
)
CARD_PRICES_PATH = Path(
    os.getenv(
        "PROJECT_MIRU_PRICES_DB_PATH", str(PROJECT_ROOT / "data" / "card_prices.json")
    )
)
# High-confidence miss-only CSV overlay; absent file => identical to pre-overlay behavior.
PRINTING_MARKET_MAP_OVERLAY_CSV = Path(
    os.getenv(
        "PROJECT_MIRU_PRINTING_MAP_OVERLAY_CSV",
        str(PROJECT_ROOT / "data" / "overlays" / "printing_market_map_overlay_v1.csv"),
    )
)
_printing_overlay_map_cache: dict[int, int] | None = None
_card_prices_cache: dict | None = None


def load_card_prices() -> dict:
    global _card_prices_cache
    if _card_prices_cache is not None:
        return _card_prices_cache
    if not CARD_PRICES_PATH.is_file():
        _card_prices_cache = {}
        return _card_prices_cache
    try:
        with CARD_PRICES_PATH.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            _card_prices_cache = data.get("prices", {})
    except Exception:
        _card_prices_cache = {}
    return _card_prices_cache


LIBRARY_PAGE_SIZE = 9999
HOMEPAGE_INITIAL_WATCHLIST_COUNT = 8


app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
    static_url_path="/static",
)
if Compress:
    Compress(app)
app.config["TEMPLATES_AUTO_RELOAD"] = True


_NAV_ICON_FILES = {
    "home": "home.svg",
    "cards": "cards.svg",
    "leaders": "leaders.svg",
    "deck": "deck.svg",
    "profile": "room.svg",
}


def nav_icon_url(key: str) -> str:
    icon = _NAV_ICON_FILES.get(str(key or "").strip(), "home.svg")
    return url_for("static", filename=f"icons/{icon}")


def build_primary_bottom_nav_html(*, active: str) -> str:
    tabs = [
        ("Home", "/", "home"),
        ("Cards", "/cards", "cards"),
        ("Leaders", "/leaders", "leaders"),
        ("Deck Builder", "/deck-builder", "deck"),
        ("Profile", "/profile", "profile"),
    ]
    parts = []
    for label, href, key in tabs:
        current = key == active
        aria = ' aria-current="page"' if current else ""
        cur_cls = " bottomNavItem--current active" if current else ""
        src = nav_icon_url(key)
        parts.append(
            f'<a class="bottomNavItem navButton{cur_cls}" href="{html.escape(href)}"{aria}>'
            f'<span class="bottomNavIcon navIconWrap" aria-hidden="true">'
            f'<img class="navIcon navIcon--{html.escape(key)}" src="{html.escape(src)}" width="22" height="22" alt="" decoding="async">'
            f"</span>"
            f'<span class="bottomNavLabel">{html.escape(label)}</span>'
            f"</a>"
        )
    return "".join(parts)


def clean_display_name(name: str, fallback_code: str = "") -> str:
    text = str(name or "").strip()
    if not text:
        return str(fallback_code or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_rel_image_path(path_text: str) -> str:
    return str(path_text or "").replace("\\", "/").strip().lstrip("/")


def _candidate_thumb_names(code: str) -> list[str]:
    code_u = str(code or "").strip().upper()
    if not code_u:
        return []
    return [
        f"{code_u}.webp",
        f"{code_u}.png",
        f"{code_u}.jpg",
        f"{code_u}.jpeg",
    ]


def choose_leader_art_src(code: str) -> str:
    """
    Returns the URL for a leader's cropped art image stored at
    D:\\Miru_Assets\\leader_crops\\<CODE>.png (SSD, fast).
    Served via the existing /static/assets/thumbs/ route because
    leader_crops lives inside MIRU_ASSETS.
    Falls back to empty string when the crop file does not exist yet.
    """
    code_u = str(code or "").strip().upper()
    if not code_u:
        return ""
    return f"/static/assets/thumbs/leader_crops/{quote(code_u)}.png"


def choose_thumbnail_src(
    name: str,
    code: str,
    catalog_entry: dict | None = None,
    width: int = 320,
    is_leader: bool = False,
) -> str:
    _ = name
    code_u = str(code or "").strip().upper()
    if not code_u:
        return ""

    set_prefix = code_u.split("-", 1)[0]

    # Check thumbs subfolder first (small WebP for grid/drawer)
    thumb_candidate = MIRU_ASSETS / set_prefix / "base" / "thumbs" / f"{code_u}.webp"
    if thumb_candidate.is_file():
        rel = f"{set_prefix}/base/thumbs/{code_u}.webp"
        return f"/static/assets/thumbs/{quote(rel)}"

    # Fall back to full-size base art
    for ext in (".jpg", ".png", ".webp", ".jpeg"):
        candidate = MIRU_ASSETS / set_prefix / "base" / f"{code_u}{ext}"
        if candidate.is_file():
            rel = f"{set_prefix}/base/{code_u}{ext}"
            return f"/static/assets/thumbs/{quote(rel)}"

    # Fall back to catalog_image_src if present
    entry = catalog_entry or {}
    url = str(entry.get("catalog_image_src") or "").strip()
    if url:
        if (
            url.startswith("http://")
            or url.startswith("https://")
            or url.startswith("/")
        ):
            return url

    return ""


@app.get("/static/assets/thumbs/<path:filename>")
def miru_clean_static_thumbs(filename: str):
    normalized = _normalize_rel_image_path(filename)
    if not normalized or any(part == ".." for part in normalized.split("/")):
        return Response("Bad request", status=400)
    return send_from_directory(str(MIRU_ASSETS), normalized)


def _legacy_thumbs_to_set_base_png(normalized: str) -> Path | None:
    """
    Map legacy DB paths like thumbs/OP01-001.webp to on-disk base art:
    MIRU_ASSETS/<set_code>/base/<CODE>.png (set_code = segment before first '-' in the card code).
    """
    if not normalized.lower().startswith("thumbs/"):
        return None
    rest = normalized[7:].lstrip("/")
    if not rest:
        return None
    base_name = rest.split("/")[-1]
    stem = Path(base_name).stem
    if not stem or "-" not in stem:
        return None
    set_code = stem.split("-", 1)[0]
    if not set_code:
        return None
    candidate = MIRU_ASSETS / set_code / "base" / f"{stem}.png"
    if candidate.is_file():
        return candidate
    return None


@app.get("/img/<path:filename>")
def serve_catalog_variant_image(filename: str):
    normalized = _normalize_rel_image_path(filename)
    if not normalized or any(part == ".." for part in normalized.split("/")):
        return Response("Bad request", status=400)

    legacy = _legacy_thumbs_to_set_base_png(normalized)
    if legacy is not None:
        return send_from_directory(str(legacy.parent), legacy.name)

    # Only serve from D:\Miru_Assets - no F:\OPTCG_Images fallback
    if MIRU_ASSETS.is_dir() and (MIRU_ASSETS / normalized).is_file():
        return send_from_directory(str(MIRU_ASSETS), normalized)
    return Response("Not found", status=404)


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


def load_catalog_card_index(require_image_assets: bool = False) -> dict[str, dict]:
    if not CATALOG_DB_PATH.is_file():
        return {}
    try:
        conn = sqlite3.connect(str(CATALOG_DB_PATH))
        conn.row_factory = sqlite3.Row
        if require_image_assets:
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
                    c.traits,
                    c.effect_text,
                    c.trigger_text,
                    c.set_code,
                    asset_pick.asset_thumb_local_path,
                    asset_pick.asset_thumb_source_label
                FROM cards c
                LEFT JOIN (
                    SELECT
                        ranked.card_id,
                        ranked.asset_thumb_local_path,
                        ranked.asset_thumb_source_label
                    FROM (
                        SELECT
                            cv_base.card_id,
                            ia_thumb.local_path AS asset_thumb_local_path,
                            ia_thumb.source_label AS asset_thumb_source_label,
                            ROW_NUMBER() OVER (
                                PARTITION BY cv_base.card_id
                                ORDER BY
                                    cv_base.is_base DESC,
                                    CASE
                                        WHEN ia_thumb.source_label = 'bandai_cdn' THEN 0
                                        ELSE 1
                                    END ASC,
                                    ia_thumb.is_primary DESC,
                                    ia_thumb.id ASC
                            ) AS rn
                        FROM card_variants cv_base
                        JOIN image_assets ia_thumb
                            ON ia_thumb.printing_id = cv_base.id
                    ) ranked
                    WHERE ranked.rn = 1
                ) asset_pick ON asset_pick.card_id = c.id
                WHERE EXISTS (
                    SELECT 1
                    FROM card_variants cv2
                    JOIN image_assets ia2 ON ia2.printing_id = cv2.id
                    WHERE cv2.card_id = c.id
                )
                ORDER BY c.canonical_code ASC
                """
            ).fetchall()
        else:
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
                    c.traits,
                    c.effect_text,
                    c.trigger_text,
                    c.set_code
                FROM cards c
                ORDER BY c.canonical_code ASC
                """
            ).fetchall()
        conn.close()
    except Exception:
        return {}

    out: dict[str, dict] = {}
    for row in rows:
        code = str(row["canonical_code"] or "").strip().upper()
        if not code:
            continue
        catalog_image_src = ""
        if require_image_assets:
            apath = str(row["asset_thumb_local_path"] or "").strip()
            if apath:
                rel = _normalize_rel_image_path(apath)
                if rel:
                    catalog_image_src = f"/img/{quote(rel)}"
        out[code] = {
            "card_name": clean_display_name(str(row["card_name"] or ""), code),
            "set_name": str(row["set_name"] or "").strip(),
            "rarity": str(row["rarity"] or "").strip(),
            "color": str(row["color"] or "").strip(),
            "card_type": str(row["card_type"] or "").strip(),
            "cost": "" if row["cost"] is None else str(row["cost"]),
            "power": str(row["power"] or "").strip(),
            "counter": str(row["counter"] or "").strip(),
            "attribute": str(row["attribute"] or "").strip(),
            "traits": str(row["traits"] or "").strip(),
            "effect_text": str(row["effect_text"] or "").strip(),
            "trigger_text": str(row["trigger_text"] or "").strip(),
            "set_code": str(row["set_code"] or "").strip(),
            "catalog_image_src": catalog_image_src,
        }
    return out


def _load_printing_market_overlay_map() -> dict[int, int]:
    """
    Load approved high-confidence rows only. Malformed lines skipped.
    First valid row wins for a given card_variant_id (printing_id).
    """
    global _printing_overlay_map_cache
    if _printing_overlay_map_cache is not None:
        return _printing_overlay_map_cache
    out: dict[int, int] = {}
    path = PRINTING_MARKET_MAP_OVERLAY_CSV
    if not path.is_file():
        _printing_overlay_map_cache = out
        return out
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                _printing_overlay_map_cache = out
                return out
            for raw in reader:
                if not raw:
                    continue
                try:
                    vid = int(str(raw.get("card_variant_id") or "").strip())
                    mpk = int(str(raw.get("candidate_market_product_pk") or "").strip())
                except ValueError:
                    continue
                conf = (raw.get("confidence") or "").strip().upper()
                if conf != "HIGH":
                    continue
                if (raw.get("needs_manual_review") or "").strip() == "1":
                    continue
                if vid in out:
                    continue
                out[vid] = mpk
    except Exception:
        out = {}
    _printing_overlay_map_cache = out
    return out


def _price_chain_row_usable(row: sqlite3.Row | None) -> bool:
    if row is None:
        return False
    return row["market_price"] is not None or row["mid_price"] is not None


def _primary_price_sql() -> str:
    return """
            SELECT
                mp.market_variant_label,
                pmm.mapping_confidence,
                mpr.market_price,
                mpr.mid_price,
                mpr.captured_at
            FROM printing_market_map pmm
            JOIN market_products mp ON mp.id = pmm.market_product_id
            JOIN market_prices mpr ON mpr.market_product_fk = mp.id
            WHERE pmm.printing_id = ?
            ORDER BY
                CASE mpr.subtype_name
                    WHEN 'Normal' THEN 0
                    WHEN 'Foil' THEN 1
                    ELSE 2
                END ASC,
                mpr.captured_at DESC
            LIMIT 1
            """


def _overlay_price_sql() -> str:
    """Same subtype / captured_at ordering as primary path, without printing_market_map."""
    return """
            SELECT
                mp.market_variant_label,
                mpr.market_price,
                mpr.mid_price,
                mpr.captured_at
            FROM market_products mp
            JOIN market_prices mpr ON mpr.market_product_fk = mp.id
            WHERE mp.id = ?
            ORDER BY
                CASE mpr.subtype_name
                    WHEN 'Normal' THEN 0
                    WHEN 'Foil' THEN 1
                    ELSE 2
                END ASC,
                mpr.captured_at DESC
            LIMIT 1
            """


def _mapped_product_count(conn: sqlite3.Connection, printing_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT market_product_id) AS mapped_count
        FROM printing_market_map
        WHERE printing_id = ?
        """,
        (printing_id,),
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["mapped_count"] or 0)
    except Exception:
        return 0


def get_card_price_chain(db_path, printing_id):
    """
    Walk the full price chain for a single printing_id.
    Returns dict with keys: market_price, mid_price, market_variant_label,
    mapping_confidence, captured_at — all None if any link is missing.
    Never borrows from sibling printings.

    Resolution order (miss-only overlay):
      1. Real printing_market_map + market_prices (unchanged).
      2. If that yields no usable price, optional CSV overlay maps printing_id -> market_products.id.
      3. Otherwise fail closed (empty price fields).

    mapping_confidence == \"OVERLAY_V1_HIGH\" indicates the overlay path produced the row.
    """
    empty = {
        "market_price": None,
        "mid_price": None,
        "market_variant_label": None,
        "mapping_confidence": None,
        "captured_at": None,
    }
    try:
        pid = int(printing_id)
    except (TypeError, ValueError):
        return dict(empty)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            mapped_count = _mapped_product_count(conn, pid)
            if mapped_count > 1:
                # Multiple market products mapped to one printing_id is ambiguous.
                # Use explicit overlay disambiguation if available; otherwise fail closed.
                mpk = _load_printing_market_overlay_map().get(pid)
                if mpk is not None:
                    orow = conn.execute(_overlay_price_sql(), (mpk,)).fetchone()
                    if _price_chain_row_usable(orow):
                        return {
                            "market_price": orow["market_price"],
                            "mid_price": orow["mid_price"],
                            "market_variant_label": orow["market_variant_label"],
                            "mapping_confidence": "OVERLAY_V1_HIGH",
                            "captured_at": orow["captured_at"],
                        }
                out = dict(empty)
                out["mapping_confidence"] = "AMBIGUOUS_PMM_MULTI"
                return out

            row = conn.execute(_primary_price_sql(), (pid,)).fetchone()

            if _price_chain_row_usable(row):
                return {
                    "market_price": row["market_price"],
                    "mid_price": row["mid_price"],
                    "market_variant_label": row["market_variant_label"],
                    "mapping_confidence": row["mapping_confidence"],
                    "captured_at": row["captured_at"],
                }

            mpk = _load_printing_market_overlay_map().get(pid)
            if mpk is not None:
                orow = conn.execute(_overlay_price_sql(), (mpk,)).fetchone()
                if _price_chain_row_usable(orow):
                    return {
                        "market_price": orow["market_price"],
                        "mid_price": orow["mid_price"],
                        "market_variant_label": orow["market_variant_label"],
                        "mapping_confidence": "OVERLAY_V1_HIGH",
                        "captured_at": orow["captured_at"],
                    }

            if row is None:
                return dict(empty)
            return {
                "market_price": row["market_price"],
                "mid_price": row["mid_price"],
                "market_variant_label": row["market_variant_label"],
                "mapping_confidence": row["mapping_confidence"],
                "captured_at": row["captured_at"],
            }
        finally:
            conn.close()
    except Exception:
        return dict(empty)


def build_watchlist_entries(
    items: list[dict], catalog_cards: dict[str, dict]
) -> list[dict]:
    entries = []
    for it in items or []:
        code = str(it.get("code") or "").strip().upper()
        name = str(it.get("name") or "").strip()
        if not code:
            m = re.search(r"([A-Z]{1,4}\d{2}-\d{3})", name or "", re.I)
            code = m.group(1).upper() if m else ""
        catalog_entry = dict(catalog_cards.get(code) or {})
        title = clean_display_name(str(catalog_entry.get("card_name") or name), code)
        entries.append(
            {
                "it": dict(it),
                "code": code,
                "name": name,
                "title_name": title,
                "subtitle": str(catalog_entry.get("set_name") or "").strip(),
                "price_txt": str(it.get("price") or ""),
                "target_txt": str(it.get("target") or ""),
            }
        )
    return entries


def _library_code_sort_key(code: str) -> tuple:
    text = str(code or "").strip().upper()
    m = re.match(r"^([A-Z]+)(\d+)-(\d+)(.*)$", text)
    if not m:
        return (9999, text, 9999, 9999, "")
    prefix, set_num, card_num, suffix = m.groups()
    # Canonical Bandai release order: ST -> OP -> EB -> PRB -> P-series -> everything else
    prefix_order = {
        "ST": 0,
        "OP": 1,
        "EB": 2,
        "PRB": 3,
        "P": 4,
    }
    order = prefix_order.get(prefix, 5)
    return (order, prefix, int(set_num), int(card_num), suffix or "")


def build_library_card_index(catalog_cards: dict[str, dict]) -> list[dict]:
    library_cards = []
    for code, catalog_entry in (catalog_cards or {}).items():
        title = clean_display_name(str(catalog_entry.get("card_name") or ""), code)
        set_name = str(catalog_entry.get("set_name") or "").strip()
        thumb_src = choose_thumbnail_src(title, code, catalog_entry, width=240)
        search_blob = " ".join(
            [
                code,
                title,
                set_name,
                str(catalog_entry.get("color") or "").strip(),
                str(catalog_entry.get("rarity") or "").strip(),
                str(catalog_entry.get("card_type") or "").strip(),
                str(catalog_entry.get("attribute") or "").strip(),
            ]
        ).lower()
        library_cards.append(
            {
                "code": code,
                "title_name": title or code,
                "subtitle": set_name,
                "set_name": set_name,
                "set_code": str(code).split("-", 1)[0] if "-" in str(code) else "",
                "rarity": str(catalog_entry.get("rarity") or "").strip(),
                "color": str(catalog_entry.get("color") or "").strip(),
                "card_type": str(catalog_entry.get("card_type") or "").strip(),
                "cost": str(catalog_entry.get("cost") or "").strip(),
                "power": str(catalog_entry.get("power") or "").strip(),
                "counter": str(catalog_entry.get("counter") or "").strip(),
                "attribute": str(catalog_entry.get("attribute") or "").strip(),
                "traits": str(catalog_entry.get("traits") or "").strip(),
                "effect_text": str(catalog_entry.get("effect_text") or "").strip(),
                "trigger_text": str(catalog_entry.get("trigger_text") or "").strip(),
                "thumb_src": thumb_src,
                "detail_src": thumb_src,
                "search_blob": search_blob,
                "has_runtime_thumb": bool(thumb_src),
                "has_local_thumb": bool(thumb_src),
            }
        )
    library_cards.sort(key=lambda e: _library_code_sort_key(str(e.get("code") or "")))
    return library_cards


def _normalize_library_filters(args) -> dict:
    return {
        "q": str(args.get("q") or "").strip(),
        "set": str(args.get("set") or "").strip(),
        "color": str(args.get("color") or "").strip(),
        "rarity": str(args.get("rarity") or "").strip(),
        "card_type": str(args.get("card_type") or "").strip(),
    }


def _filter_library_cards(cards: list[dict], filters: dict) -> list[dict]:
    q = str(filters.get("q") or "").strip().lower()
    set_filter = str(filters.get("set") or "").strip().lower()
    color_filter = str(filters.get("color") or "").strip().lower()
    rarity_filter = str(filters.get("rarity") or "").strip().lower()
    type_filter = str(filters.get("card_type") or "").strip().lower()
    out = []
    for entry in cards:
        if q and q not in str(entry.get("search_blob") or "").lower():
            continue
        if set_filter:
            set_values = {
                str(entry.get("set_name") or "").lower(),
                str(entry.get("set_code") or "").lower(),
            }
            if set_filter not in set_values:
                continue
        if color_filter and color_filter != str(entry.get("color") or "").lower():
            continue
        if rarity_filter and rarity_filter != str(entry.get("rarity") or "").lower():
            continue
        if type_filter and type_filter != str(entry.get("card_type") or "").lower():
            continue
        out.append(entry)
    return out


def build_library_fragment_html(
    catalog_cards: dict[str, dict],
    library_cards: list[dict],
    library_page: int,
    *,
    base_path: str = "/library-fragment",
    browse_mode: bool = True,
    query_suffix: str = "",
) -> str:
    _ = catalog_cards
    _ = browse_mode
    total = len(library_cards)
    total_pages = max(1, math.ceil(total / LIBRARY_PAGE_SIZE))
    page = min(max(int(library_page or 1), 1), total_pages)
    start = (page - 1) * LIBRARY_PAGE_SIZE
    items = library_cards[start : start + LIBRARY_PAGE_SIZE]

    card_html = []
    for entry in items:
        thumb = str(entry.get("thumb_src") or "").strip()
        entry_code = str(entry.get("code") or "").strip()
        media = (
            f'<img class="libraryThumb" data-card-code="{html.escape(entry_code)}" src="{html.escape(thumb)}" alt="" width="63" height="88" loading="lazy" decoding="async">'
            if thumb
            else '<div class="libraryThumb libraryThumb--empty">No image</div>'
        )
        card_html.append(
            f"""
            <article class="libraryCard"
              data-card-code="{html.escape(entry_code)}"
              data-set-code="{html.escape(str(entry.get('set_code') or ''))}"
              data-set-name="{html.escape(str(entry.get('set_name') or ''))}"
              data-rarity="{html.escape(str(entry.get('rarity') or ''))}"
              data-color="{html.escape(str(entry.get('color') or ''))}"
              data-card-type="{html.escape(str(entry.get('card_type') or ''))}"
              data-cost="{html.escape(str(entry.get('cost') or ''))}"
              data-power="{html.escape(str(entry.get('power') or ''))}"
              data-counter="{html.escape(str(entry.get('counter') or ''))}"
              data-effect="{html.escape(str(entry.get('effect_text') or ''))}"
              data-trigger="{html.escape(str(entry.get('trigger_text') or ''))}"
            >
              <div class="libraryCardMedia">{media}</div>
              <div class="libraryCardBody">
                <div class="libraryCardCode">{html.escape(str(entry.get("code") or ""))}</div>
                <h3 class="libraryCardTitle">{html.escape(str(entry.get("title_name") or ""))}</h3>
                <p class="libraryCardSubtitle">{html.escape(str(entry.get("subtitle") or ""))}</p>
              </div>
            </article>
            """
        )

    prev_link = ""
    next_link = ""
    if page > 1:
        prev_link = f'<a class="libraryPagerLink" href="{html.escape(base_path)}?library_page={page - 1}{query_suffix}">Prev</a>'
    if page < total_pages:
        next_link = f'<a class="libraryPagerLink" href="{html.escape(base_path)}?library_page={page + 1}{query_suffix}">Next</a>'

    return f"""
    <div class="libraryGrid">
      {''.join(card_html) if card_html else '<div class="card">No cards found.</div>'}
    </div>
    <div class="libraryPager">
      <span class="libraryPagerStat">Page {page} / {total_pages} · {total} cards</span>
      <div class="libraryPagerLinks">{prev_link}{next_link}</div>
    </div>
    """


@app.get("/")
def home():
    items = load_prices()
    catalog_cards = load_catalog_card_index()
    entries = build_watchlist_entries(items, catalog_cards)

    watchlist_rows = []
    for entry in entries:
        item = entry["it"]
        code = entry["code"]
        catalog_entry = catalog_cards.get(code, {})
        thumb_src = choose_thumbnail_src(entry["name"], code, catalog_entry, width=180)
        market_url = str(item.get("url", "") or "").strip()

        price_f = None
        target_f = None
        try:
            price_f = float(item.get("price", 0))
        except Exception:
            pass
        try:
            target_f = float(item.get("target", 0))
        except Exception:
            pass

        price_txt = f"${price_f:.2f}" if price_f and price_f > 0 else ""
        target_txt = f"${target_f:.2f}" if target_f and target_f > 0 else ""

        # Delta: positive = under target (good), negative = over target
        delta_pct = None
        delta_direction = ""
        hit = False
        tier = "watch"
        progress_width = 0

        if price_f and target_f and price_f > 0 and target_f > 0:
            delta_pct = ((target_f - price_f) / target_f) * 100.0
            if price_f <= target_f:
                hit = True
                delta_direction = "under"
                progress_width = 100
                if delta_pct >= 15:
                    tier = "deal3"
                elif delta_pct >= 5:
                    tier = "deal2"
                else:
                    tier = "deal1"
            else:
                delta_direction = "over"
                tier = "over"
                gap_ratio = min(abs(price_f - target_f) / max(price_f, target_f), 1.0)
                progress_width = int(round((1.0 - gap_ratio) * 100))

        # Sort key: hits first (sorted by deal %), then non-hits by closeness
        sort_key = (0 if hit else 1, -(delta_pct or -9999))

        watchlist_rows.append(
            {
                "code": code,
                "title_name": entry["title_name"],
                "thumb_src": thumb_src,
                "current_price": price_txt or "\u2014",
                "target_price": target_txt or "\u2014",
                "delta_pct": delta_pct,
                "delta_direction": delta_direction,
                "progress_width": progress_width,
                "hit": hit,
                "tier": tier,
                "market_url": market_url,
                "_sort_key": sort_key,
            }
        )

    watchlist_rows.sort(key=lambda r: r["_sort_key"])

    return render_template(
        "home.html",
        app_bar_page_label="HOME",
        bottom_nav_html=build_primary_bottom_nav_html(active="home"),
        watchlist_rows=watchlist_rows,
    )


@app.get("/cards")
@app.get("/library")
def index():
    items = load_prices()
    catalog_cards = load_catalog_card_index()
    library_cards = build_library_card_index(catalog_cards)
    _watch_entries = build_watchlist_entries(items, catalog_cards)

    filters = _normalize_library_filters(request.args)
    filtered_cards = _filter_library_cards(library_cards, filters)
    try:
        library_page = max(int(request.args.get("library_page", "1") or 1), 1)
    except Exception:
        library_page = 1

    query_parts = []
    for key in ("q", "set", "color", "rarity", "card_type"):
        value = str(filters.get(key) or "").strip()
        if value:
            query_parts.append((key, value))
    query_suffix = "".join(f"&{k}={quote(v)}" for k, v in query_parts)
    fragment_url = f"/library-fragment?library_page={library_page}{query_suffix}"

    page_title = "Project Miru Library"
    bottom_nav_html = build_primary_bottom_nav_html(active="cards")

    return render_template(
        "cards_library.html",
        page_title=page_title,
        app_bar_page_label="Cards",
        bottom_nav_html=bottom_nav_html,
        fragment_url=fragment_url,
    )


@app.get("/leaders")
def leaders():
    catalog = load_catalog_card_index()
    leader_list = []
    for code, card in (catalog or {}).items():
        if str(card.get("card_type") or "").strip().lower() != "leader":
            continue
        name = clean_display_name(str(card.get("card_name") or ""), code)
        set_name = str(card.get("set_name") or "").strip()
        color_raw = str(card.get("color") or "").strip()
        traits_raw = str(card.get("traits") or "").strip()
        trait_pills = (
            [t.strip() for t in traits_raw.replace("|", "/").split("/") if t.strip()]
            if traits_raw
            else []
        )
        color_tags = (
            [t.strip() for t in color_raw.replace("|", "/").split("/") if t.strip()]
            if color_raw
            else []
        )
        leader_list.append(
            {
                "code": code,
                "name": name,
                "set_name": set_name,
                "color": color_raw,
                "color_tags": color_tags,
                "power": str(card.get("power") or "").strip(),
                "attribute": str(card.get("attribute") or "").strip(),
                "trait_pills": trait_pills,
                "effect_text": str(card.get("effect_text") or "").strip(),
                "trigger_text": str(card.get("trigger_text") or "").strip(),
                "thumb_src": choose_leader_art_src(code)
                or choose_thumbnail_src(name, code, card, width=320, is_leader=True),
                "sort_reason": f"From {set_name}" if set_name else "In catalog",
            }
        )
    leader_list.sort(key=lambda x: (x.get("set_name") or "", x.get("code") or ""))
    return render_template(
        "leaders_index.html",
        leader_list=leader_list,
        bottom_nav_html=build_primary_bottom_nav_html(active="leaders"),
    )


@app.get("/leader/<leader_code>")
def leader_page(leader_code: str):
    code = str(leader_code or "").strip().upper()
    catalog = load_catalog_card_index()
    card = dict(catalog.get(code) or {})
    found = bool(card)
    traits_raw = str(card.get("traits") or "").strip()
    trait_pills = (
        [t.strip() for t in traits_raw.replace("|", "/").split("/") if t.strip()]
        if traits_raw
        else []
    )
    color_raw = str(card.get("color") or "").strip()
    color_tags = (
        [t.strip() for t in color_raw.replace("|", "/").split("/") if t.strip()]
        if color_raw
        else []
    )

    return render_template(
        "leader.html",
        leader_code=code,
        name=(
            clean_display_name(str(card.get("card_name") or ""), code) if found else ""
        ),
        set_name=str(card.get("set_name") or "").strip() if found else "",
        color_tags=color_tags if found else [],
        power=str(card.get("power") or "").strip() if found else "",
        attribute=str(card.get("attribute") or "").strip() if found else "",
        trait_pills=trait_pills if found else [],
        effect_text=str(card.get("effect_text") or "").strip() if found else "",
        trigger_text=str(card.get("trigger_text") or "").strip() if found else "",
        not_found=not found,
        bottom_nav_html=build_primary_bottom_nav_html(active="leaders"),
    )


def miru_flip_back_urls() -> dict:
    return {"leader_red": "", "character_blue": ""}


@app.get("/deck-builder")
def deck_builder():
    _selected_leader = str(request.args.get("leader") or "").strip().upper()
    catalog = load_catalog_card_index()

    deck_leader_pool = []
    deck_card_pool = []

    for code, card in (catalog or {}).items():
        card_type = str(card.get("card_type") or "").strip().lower()
        name = clean_display_name(str(card.get("card_name") or ""), code)
        color_raw = str(card.get("color") or "").strip()
        if card_type == "leader":
            deck_leader_pool.append(
                {
                    "code": code,
                    "name": name,
                    "set_name": str(card.get("set_name") or "").strip(),
                    "color": color_raw,
                    "thumb_src": choose_thumbnail_src(
                        name, code, card, width=320, is_leader=True
                    ),
                    "colors": [
                        t.strip()
                        for t in str(card.get("color") or "")
                        .replace("|", "/")
                        .split("/")
                        if t.strip()
                    ],
                    "power": str(card.get("power") or "").strip(),
                    "attribute": str(card.get("attribute") or "").strip(),
                    "effect_text": str(card.get("effect_text") or "").strip(),
                    "trigger_text": str(card.get("trigger_text") or "").strip(),
                    "trait_pills": [
                        t.strip()
                        for t in str(card.get("traits") or "")
                        .replace("|", "/")
                        .split("/")
                        if t.strip()
                    ],
                    "color_tags": [
                        t.strip()
                        for t in str(card.get("color") or "")
                        .replace("|", "/")
                        .split("/")
                        if t.strip()
                    ],
                }
            )
        else:
            deck_card_pool.append(
                {
                    "id": code,
                    "name": name,
                    "type": str(card.get("card_type") or "").strip(),
                    "color": color_raw,
                    "cost": str(card.get("cost") or "").strip(),
                    "rarity": str(card.get("rarity") or "").strip(),
                    "set_code": str(card.get("set_code") or "").strip(),
                    "image_url": choose_thumbnail_src(name, code, card, width=480),
                    "power": str(card.get("power") or "").strip(),
                    "counter": str(card.get("counter") or "").strip(),
                    "attribute": str(card.get("attribute") or "").strip(),
                    "traits": str(card.get("traits") or "").strip(),
                    "effect_text": str(card.get("effect_text") or "").strip(),
                    "trigger_text": str(card.get("trigger_text") or "").strip(),
                }
            )

    deck_leader_pool.sort(key=lambda x: (x.get("name") or "", x.get("code") or ""))
    deck_card_pool.sort(key=lambda x: (x.get("type") or "", x.get("id") or ""))

    return render_template(
        "deck_builder.html",
        app_bar_page_label="Deck Builder",
        bottom_nav_html=build_primary_bottom_nav_html(active="deck"),
        deck_leader_pool=deck_leader_pool,
        deck_card_pool=deck_card_pool,
        miru_flip_back_urls=miru_flip_back_urls(),
    )


@app.get("/profile")
def profile_page():
    return render_template(
        "profile.html",
        app_bar_page_label="Profile",
        bottom_nav_html=build_primary_bottom_nav_html(active="profile"),
    )


@app.get("/sets")
def sets():
    return redirect("/library", code=302)


@app.get("/api/miru/insight/<card_id>")
def api_miru_insight(card_id: str):
    _ = card_id
    return jsonify({"primary": None, "additional": []})


@app.get("/api/leader-deck-intel/<leader_code>")
def api_leader_deck_intel(leader_code: str):
    _ = leader_code
    return jsonify({})


@app.get("/api/card-price/<card_code>")
def api_card_price(card_code: str):
    code = str(card_code or "").strip().upper()
    if not code:
        return jsonify({"found": False}), 400
    prices = load_card_prices()
    entry = prices.get(code)
    if not entry:
        return jsonify({"found": False, "code": code})
    return jsonify(
        {
            "found": True,
            "code": code,
            "market": entry.get("market"),
            "low": entry.get("low"),
            "name": entry.get("name"),
            "rarity": entry.get("rarity"),
            "alt_art_market": entry.get("alt_art_market"),
        }
    )


@app.get("/api/card-prices")
def api_card_prices_bulk():
    """
    Query params: codes = comma-separated card codes (max 60)
    Returns market price for each code found.
    """
    raw = str(request.args.get("codes") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "codes parameter required"}), 400
    requested = [c.strip().upper() for c in raw.split(",") if c.strip()]
    if not requested:
        return jsonify({"ok": False, "error": "no valid codes"}), 400
    if len(requested) > 60:
        return jsonify({"ok": False, "error": "max 60 codes per request"}), 400
    prices = load_card_prices()
    results = {}
    for code in requested:
        entry = prices.get(code)
        if entry:
            results[code] = {
                "market": entry.get("market"),
                "low": entry.get("low"),
                "rarity": entry.get("rarity"),
            }
        else:
            results[code] = None
    return jsonify({"ok": True, "results": results})


@app.post("/api/watchlist/add")
def api_watchlist_add():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip().upper()
    if not code:
        return jsonify({"ok": False, "reason": "missing_code"}), 400
    target_raw = body.get("target")
    try:
        target = float(target_raw) if target_raw is not None else None
    except (TypeError, ValueError):
        target = None

    # Look up market price / metadata from card_prices.json
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

        # Duplicate check by code
        for entry in watchlist.values():
            if isinstance(entry, dict) and str(entry.get("code") or "").upper() == code:
                return jsonify({"ok": False, "reason": "already_in_watchlist"})

        # Use product_id as key when available, else code
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

        return jsonify({"ok": True, "code": code})
    except Exception:
        return jsonify({"ok": False, "reason": "write_error"})


@app.post("/api/watchlist/remove")
def api_watchlist_remove():
    body = request.get_json(silent=True) or {}
    code = str(body.get("code") or "").strip().upper()
    if not code:
        return jsonify({"ok": False, "reason": "missing_code"}), 400

    try:
        if not PRICES_PATH.is_file():
            return jsonify({"ok": False, "reason": "not_found"})

        with PRICES_PATH.open("r", encoding="utf-8") as fh:
            watchlist = json.load(fh)

        key_to_remove = None
        for k, entry in watchlist.items():
            if isinstance(entry, dict) and str(entry.get("code") or "").upper() == code:
                key_to_remove = k
                break

        if key_to_remove is None:
            return jsonify({"ok": False, "reason": "not_found"})

        del watchlist[key_to_remove]

        with PRICES_PATH.open("w", encoding="utf-8") as fh:
            json.dump(watchlist, fh, indent=2)

        return jsonify({"ok": True})
    except Exception:
        return jsonify({"ok": False, "reason": "write_error"})


@app.get("/api/card-variants/<card_code>")
def api_card_variants(card_code: str):
    """
    Get variant images for a card, ordered by priority.
    Used by mobile double-tap variant switcher.
    """
    try:
        conn = sqlite3.connect(str(CATALOG_DB_PATH))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
        SELECT cv.id AS printing_id, cv.variant_key, cv.is_base, cv.is_sp, cv.is_tr, cv.is_alt, cv.is_manga_rare,
               cv.is_golden_manga_rare, cv.is_illustration_rare, cv.release_set_name,
               cv.image_path, cv.image_url, cv.source,
               ia.local_path, ia.source_label
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        LEFT JOIN (
          SELECT printing_id, local_path, source_label,
                 ROW_NUMBER() OVER (
                   PARTITION BY printing_id
                   ORDER BY is_primary DESC, id ASC
                 ) as rn
          FROM image_assets
        ) ia ON ia.printing_id = cv.id AND ia.rn = 1
        WHERE c.canonical_code = ?
          AND (
              EXISTS (SELECT 1 FROM image_assets ia WHERE ia.printing_id = cv.id)
              OR (cv.image_path IS NOT NULL AND cv.image_path != '')
              OR (cv.image_url IS NOT NULL AND cv.image_url != '')
          )
        ORDER BY
          CASE WHEN cv.variant_key = 'base' AND cv.is_base = 1 THEN 0 ELSE 1 END,
          CASE cv.variant_key
            WHEN 'base' THEN 1
            ELSE 2
          END,
          CASE
            WHEN cv.is_sp = 1 AND cv.variant_key != 'base' THEN 2
            WHEN cv.variant_key LIKE 'parallel%' THEN 3
            WHEN cv.is_tr = 1 THEN 4
            WHEN cv.is_alt = 1 THEN 5
            WHEN cv.variant_key LIKE 'r%' THEN 6
            ELSE 7
          END,
          cv.id ASC
        """

        cursor.execute(query, (card_code,))
        rows = cursor.fetchall()
        conn.close()

        variants = []
        for row in rows:
            price_chain = get_card_price_chain(CATALOG_DB_PATH, row["printing_id"])

            # Determine variant type
            variant_key = str(row["variant_key"] or "").strip()
            is_base_flag = bool(row["is_base"])
            is_sp = bool(row["is_sp"])
            is_tr = bool(row["is_tr"])
            is_alt = bool(row["is_alt"])
            is_manga_rare = bool(row["is_manga_rare"])
            is_golden_manga_rare = bool(row["is_golden_manga_rare"])
            is_illustration_rare = bool(row["is_illustration_rare"])

            if variant_key == "base" and not is_sp:
                variant_type = ""
            elif variant_key == "base" and is_sp:
                variant_type = "Special Rare"
            elif variant_key == "sp":
                variant_type = "Special Rare"
            elif variant_key.startswith("parallel"):
                variant_type = "Parallel"
            elif variant_key.startswith("r") and variant_key != "base":
                variant_type = "Reprint"
            elif variant_key == "tr":
                variant_type = "Treasure Rare"
            elif variant_key == "alt":
                variant_type = "Alt Art"
            elif is_manga_rare:
                variant_type = "Manga Rare"
            elif is_golden_manga_rare:
                variant_type = "Golden Manga Rare"
            elif is_illustration_rare:
                variant_type = "Illustration Rare"
            else:
                variant_type = "Variant"

            # Determine set label
            release_set_name = str(row["release_set_name"] or "").strip()
            if release_set_name in ("Other Product Card", "Promotion Card", "", None):
                set_label = ""
            else:
                set_label = release_set_name

            # Build display label
            if variant_type and set_label:
                display_label = f"{variant_type} · {set_label}"
            elif variant_type:
                display_label = variant_type
            elif set_label:
                display_label = set_label
            else:
                display_label = "Base"

            # Image path resolution with three-tier precedence
            image_path = None
            image_source = None

            # Priority 1: ia.local_path non-null
            ia_local_path = str(row["local_path"] or "").strip()
            if ia_local_path:
                image_path = "/img/" + ia_local_path.replace("\\", "/")
                image_source = str(row["source_label"] or "").strip() or "image_assets"

            # Priority 2: cv.image_path non-null and non-empty
            if not image_path:
                cv_image_path = str(row["image_path"] or "").strip()
                if cv_image_path:
                    full_filesystem_path = MIRU_ASSETS / cv_image_path
                    if full_filesystem_path.is_file():
                        image_path = "/img/" + cv_image_path.replace("\\", "/")
                        image_source = "legacy_fallback"
                        app.logger.warning(
                            f"image_assets fallback: card={card_code} printing_id={row['printing_id']}"
                        )

            # Priority 3: cv.image_url non-null and non-empty
            if not image_path:
                cv_image_url = str(row["image_url"] or "").strip()
                if cv_image_url:
                    image_path = cv_image_url
                    image_source = "legacy_url"
                    app.logger.warning(
                        f"image_url fallback: card={card_code} printing_id={row['printing_id']}"
                    )

            # Skip if no image found
            if not image_path:
                continue

            variants.append(
                {
                    "printing_id": int(row["printing_id"]),
                    "image_path": image_path,
                    "display_label": display_label,
                    "variant_key": variant_key,
                    "is_base": is_base_flag,
                    "image_source": image_source,
                    "source": str(row["source"] or "").strip(),
                    "market_price": price_chain.get("market_price"),
                    "mid_price": price_chain.get("mid_price"),
                    "market_variant_label": price_chain.get("market_variant_label"),
                    "mapping_confidence": price_chain.get("mapping_confidence"),
                    "captured_at": price_chain.get("captured_at"),
                }
            )

        # Base printing first, then all others in existing order (stable).
        base_first = [
            v for v in variants if v.get("variant_key") == "base" and v.get("is_base")
        ]
        rest = [
            v
            for v in variants
            if not (v.get("variant_key") == "base" and v.get("is_base"))
        ]
        variants = base_first + rest

        return jsonify({"card_code": card_code, "variants": variants})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.get("/api/cards-json")
def api_cards_json():
    # Do not hard-gate the catalog on image_assets rows; cards can render via runtime thumbnail fallback.
    catalog_cards = load_catalog_card_index(require_image_assets=False)
    library_cards = build_library_card_index(catalog_cards)
    cards = []
    for entry in library_cards:
        cards.append(
            {
                "code": entry.get("code", ""),
                "name": entry.get("title_name", ""),
                "set_code": entry.get("set_code", ""),
                "set_name": entry.get("set_name", ""),
                "rarity": entry.get("rarity", ""),
                "color": entry.get("color", ""),
                "card_type": entry.get("card_type", ""),
                "cost": entry.get("cost", ""),
                "power": entry.get("power", ""),
                "counter": entry.get("counter", ""),
                "attribute": entry.get("attribute", ""),
                "effect_text": entry.get("effect_text", ""),
                "trigger_text": entry.get("trigger_text", ""),
                "thumb_src": entry.get("thumb_src", ""),
            }
        )
    response = jsonify({"ok": True, "cards": cards})
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@app.get("/api/card-lookup")
def api_card_lookup():
    """
    Resolve one or more card codes against the local card catalog.
    Used by the deck builder mass entry / TCGPlayer import feature.

    Query params:
      codes = comma-separated list of canonical card codes
              e.g. ?codes=OP01-001,OP01-002,EB04-003

    Returns JSON:
    {
      "ok": true,
      "results": {
        "OP01-001": {
          "found": true,
          "card_name": "Roronoa Zoro",
          "set_code": "OP01",
          "set_name": "Romance Dawn",
          "card_type": "Leader",
          "color": "Green",
          "cost": "0",
          "rarity": "L",
          "image_src": "/img/thumbs/OP01-001.webp"
        },
        "OP01-999": {
          "found": false
        }
      }
    }
    """
    raw = str(request.args.get("codes") or "").strip()
    if not raw:
        return jsonify({"ok": False, "error": "codes parameter required"}), 400

    requested = [c.strip().upper() for c in raw.split(",") if c.strip()]
    if not requested:
        return jsonify({"ok": False, "error": "no valid codes provided"}), 400

    if len(requested) > 60:
        return jsonify({"ok": False, "error": "max 60 codes per request"}), 400

    catalog = load_catalog_card_index()
    results = {}

    for code in requested:
        entry = catalog.get(code)
        if not entry:
            results[code] = {"found": False}
            continue

        image_src = str(entry.get("catalog_image_src") or "").strip()
        if not image_src:
            image_src = choose_thumbnail_src(
                str(entry.get("card_name") or ""), code, entry
            )

        results[code] = {
            "found": True,
            "card_name": str(entry.get("card_name") or code).strip(),
            "set_code": str(entry.get("set_code") or "").strip(),
            "set_name": str(entry.get("set_name") or "").strip(),
            "card_type": str(entry.get("card_type") or "").strip(),
            "color": str(entry.get("color") or "").strip(),
            "cost": str(entry.get("cost") or "").strip(),
            "rarity": str(entry.get("rarity") or "").strip(),
            "image_src": image_src,
        }

    return jsonify({"ok": True, "results": results})


@app.get("/library-fragment")
def library_fragment():
    try:
        page = max(int(request.args.get("library_page", "1") or 1), 1)
    except Exception:
        page = 1

    filters = _normalize_library_filters(request.args)
    query_pairs = []
    for key in ("q", "set", "color", "rarity", "card_type"):
        value = str(filters.get(key) or "").strip()
        if value:
            query_pairs.append((key, value))
    query_suffix = "".join(f"&{k}={quote(v)}" for k, v in query_pairs)

    catalog_cards = load_catalog_card_index()
    library_cards = build_library_card_index(catalog_cards)
    filtered_cards = _filter_library_cards(library_cards, filters)
    fragment_html = build_library_fragment_html(
        catalog_cards,
        filtered_cards,
        page,
        base_path="/library-fragment",
        browse_mode=True,
        query_suffix=query_suffix,
    )
    response = Response(fragment_html, mimetype="text/html")
    response.headers["Cache-Control"] = "private, max-age=20, stale-while-revalidate=10"
    return response


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "18080"))
    serve(app, host="0.0.0.0", port=port, threads=8)
