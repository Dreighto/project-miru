import html
from urllib.parse import quote

from flask import Blueprint, Response, redirect, render_template, request, send_from_directory, url_for

from services.catalog import (
    _filter_library_cards,
    _normalize_library_filters,
    build_library_card_index,
    build_library_fragment_html,
    build_watchlist_entries,
    clean_display_name,
    load_catalog_card_index,
)
from services.images import (
    MIRU_ASSETS,
    _legacy_thumbs_to_set_base_png,
    _normalize_rel_image_path,
    choose_leader_art_src,
    choose_thumbnail_src,
)
from services.watchlist import load_prices

HOMEPAGE_INITIAL_WATCHLIST_COUNT = 8

pages_bp = Blueprint("pages", __name__)

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

@pages_bp.get("/static/assets/thumbs/<path:filename>")
def miru_clean_static_thumbs(filename: str):
    normalized = _normalize_rel_image_path(filename)
    if not normalized or any(part == ".." for part in normalized.split("/")):
        return Response("Bad request", status=400)
    return send_from_directory(str(MIRU_ASSETS), normalized)

@pages_bp.get("/img/<path:filename>")
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

# PRO-6 Stage 1: route served by SvelteKit catch-all in app.py; function kept for Stage 2 deletion.
# @pages_bp.get("/")
def home():
    items = load_prices()
    catalog_cards = load_catalog_card_index()
    entries = build_watchlist_entries(items, catalog_cards)
    # TEMP OP01 VERIFICATION FILTER — REMOVE AFTER OP01 QA
    entries = [e for e in entries if e.get("code", "").startswith("OP01-")]

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


# PRO-6 Stage 1: route served by SvelteKit catch-all in app.py; function kept for Stage 2 deletion.
# @pages_bp.get("/cards")
# @pages_bp.get("/library")
def index():
    # Shell only: client loads /api/cards-json; fragment URL mirrors query args for /library-fragment.
    try:
        library_page = max(int(request.args.get("library_page", "1") or 1), 1)
    except Exception:
        library_page = 1

    query_parts = []
    for key in ("q", "set", "color", "rarity", "card_type"):
        value = str(request.args.get(key) or "").strip()
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


# PRO-6 Stage 1: route served by SvelteKit catch-all in app.py; function kept for Stage 2 deletion.
# @pages_bp.get("/leaders")
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


# PRO-6 Stage 1: route served by SvelteKit catch-all in app.py; function kept for Stage 2 deletion.
# @pages_bp.get("/leader/<leader_code>")
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


# PRO-6 Stage 1: route served by SvelteKit catch-all in app.py; function kept for Stage 2 deletion.
# @pages_bp.get("/deck-builder")
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


# PRO-6 Stage 1: route served by SvelteKit catch-all in app.py; function kept for Stage 2 deletion.
# @pages_bp.get("/profile")
def profile_page():
    return render_template(
        "profile.html",
        app_bar_page_label="Profile",
        bottom_nav_html=build_primary_bottom_nav_html(active="profile"),
    )


@pages_bp.get("/sets")
def sets():
    return redirect("/library", code=302)

@pages_bp.get("/library-fragment")
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
