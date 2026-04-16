import html
import math
import re
from urllib.parse import quote

from db import CATALOG_DB_PATH, connect_catalog
from services.images import _normalize_rel_image_path, choose_thumbnail_src

LIBRARY_PAGE_SIZE = 9999

def clean_display_name(name: str, fallback_code: str = "") -> str:
    text = str(name or "").strip()
    if not text:
        return str(fallback_code or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text

def load_catalog_card_index(require_image_assets: bool = False) -> dict[str, dict]:
    if not CATALOG_DB_PATH.is_file():
        return {}
    try:
        conn = connect_catalog()
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
                -- TEMP OP01 VERIFICATION FILTER — REMOVE AFTER OP01 QA
                AND c.canonical_code LIKE 'OP01-%'
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
                -- TEMP OP01 VERIFICATION FILTER — REMOVE AFTER OP01 QA
                WHERE c.canonical_code LIKE 'OP01-%'
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
