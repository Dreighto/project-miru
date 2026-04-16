import json
import sqlite3
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from flask import Blueprint, current_app, jsonify, request

from db import CATALOG_DB_PATH, connect_catalog
from services.catalog import build_library_card_index, load_catalog_card_index
from services.images import MIRU_ASSETS, _normalize_rel_image_path, choose_thumbnail_src
from services.pricing import get_card_price_chain, load_card_prices, _load_mismatch_review_queue
from services.watchlist import add_watchlist_item, remove_watchlist_item

api_bp = Blueprint("api", __name__)

@api_bp.get("/api/miru/insight/<card_id>")
def api_miru_insight(card_id: str):
    _ = card_id
    return jsonify({"primary": None, "additional": []})


@api_bp.get("/api/leader-deck-intel/<leader_code>")
def api_leader_deck_intel(leader_code: str):
    _ = leader_code
    return jsonify({})


@api_bp.get("/api/card-price/<card_code>")
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


@api_bp.get("/api/card-prices")
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

@api_bp.post("/api/watchlist/add")
def api_watchlist_add():
    payload, status = add_watchlist_item(request.get_json(silent=True) or {})
    return jsonify(payload), status

@api_bp.post("/api/watchlist/remove")
def api_watchlist_remove():
    payload, status = remove_watchlist_item(request.get_json(silent=True) or {})
    return jsonify(payload), status

@api_bp.get("/api/card-variants/<card_code>")
def api_card_variants(card_code: str):
    """
    Get variant images for a card, ordered by priority.
    Used by mobile double-tap variant switcher.
    """
    try:
        conn = connect_catalog()
        cursor = conn.cursor()

        query = """
        SELECT cv.id AS printing_id, cv.variant_key, cv.is_base, cv.is_sp, cv.is_tr, cv.is_alt, cv.is_manga_rare,
               cv.is_golden_manga_rare, cv.is_illustration_rare, cv.release_set_name,
               cv.image_path, cv.image_url, cv.source,
               ia.local_path, ia.source_label,
               EXISTS(SELECT 1 FROM printing_market_map pmm WHERE pmm.printing_id = cv.id) AS has_pmm_map
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
              OR EXISTS (SELECT 1 FROM printing_market_map pmm WHERE pmm.printing_id = cv.id)
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
                rel_ia = _normalize_rel_image_path(ia_local_path)
                if rel_ia:
                    image_path = f"/img/{quote(rel_ia)}"
                    image_source = str(row["source_label"] or "").strip() or "image_assets"

            # Priority 2: cv.image_path non-null and non-empty
            if not image_path:
                cv_image_path = str(row["image_path"] or "").strip()
                if cv_image_path:
                    rel_cv = _normalize_rel_image_path(cv_image_path)
                    if rel_cv:
                        full_filesystem_path = MIRU_ASSETS / rel_cv
                        if full_filesystem_path.is_file():
                            image_path = f"/img/{quote(rel_cv)}"
                            image_source = "legacy_fallback"
                        current_app.logger.warning(
                            f"image_assets fallback: card={card_code} printing_id={row['printing_id']}"
                        )

            # Priority 3: cv.image_url non-null and non-empty
            if not image_path:
                cv_image_url = str(row["image_url"] or "").strip()
                if cv_image_url:
                    image_path = cv_image_url
                    image_source = "legacy_url"
                    current_app.logger.warning(
                        f"image_url fallback: card={card_code} printing_id={row['printing_id']}"
                    )

            # Skip if no image found AND no confirmed PMM mapping (avoids surfacing broken-path unmapped variants)
            if not image_path and not row["has_pmm_map"]:
                continue

            # -- Mismatch review queue: fail closed for flagged variants --
            review_entry = _load_mismatch_review_queue().get(int(row["printing_id"]))
            review_status = None
            v_market_price = price_chain.get("market_price")
            v_mid_price = price_chain.get("mid_price")
            v_mapping_confidence = price_chain.get("mapping_confidence")
            if review_entry:
                review_status = review_entry["review_status"]
                # Fail closed: suppress untrusted price for non-resolved rows
                v_market_price = None
                v_mid_price = None
                v_mapping_confidence = "REVIEW_REQUIRED"

            variants.append(
                {
                    "printing_id": int(row["printing_id"]),
                    "image_path": image_path,
                    "display_label": display_label,
                    "variant_key": variant_key,
                    "is_base": is_base_flag,
                    "image_source": image_source,
                    "source": str(row["source"] or "").strip(),
                    "market_price": v_market_price,
                    "mid_price": v_mid_price,
                    "market_variant_label": price_chain.get("market_variant_label"),
                    "mapping_confidence": v_mapping_confidence,
                    "captured_at": price_chain.get("captured_at"),
                    "review_status": review_status,
                    "has_market_map": bool(row["has_pmm_map"]),
                }
            )

        # --- DEDUP PASS: collapse visually-redundant variant rows ---
        # TEMP OP01 VERIFICATION FILTER — REMOVE AFTER OP01 QA

        # 1. Find the base row's image_path + price for reprint comparison
        base_image = None
        base_price = None
        for v in variants:
            if v.get("variant_key") == "base" and v.get("is_base"):
                base_image = v.get("image_path")
                base_price = v.get("market_price")
                break

        # 2. Drop reprint rows (r1, r2, …) that duplicate base image AND price
        if base_image is not None:
            variants = [
                v for v in variants
                if not (
                    v.get("variant_key", "").startswith("r")
                    and v.get("variant_key") != "base"
                    and v.get("image_path") == base_image
                    and v.get("market_price") == base_price
                )
            ]

        # 3. Dedup rows sharing the same image_path — keep the best one
        #    Priority: has price > no price, then non-reprint > reprint, then lowest printing_id
        seen_images: dict[str, int] = {}
        deduped: list[dict] = []
        for v in variants:
            img = v.get("image_path") or ""
            if img and img in seen_images:
                # Compare with the already-kept row
                kept_idx = seen_images[img]
                kept = deduped[kept_idx]
                # Score: has_price=2, is_not_reprint=1
                def _score(row):
                    s = 0
                    if row.get("market_price") is not None:
                        s += 2
                    if not (row.get("variant_key", "").startswith("r") and row.get("variant_key") != "base"):
                        s += 1
                    return s
                if _score(v) > _score(kept):
                    deduped[kept_idx] = v  # replace with better row
                # else: discard the duplicate
            else:
                if img:
                    seen_images[img] = len(deduped)
                deduped.append(v)
        variants = deduped

        # 4. Suppress legacy_url variants when a local-image variant already
        #    exists for the same variant_type (e.g., "Parallel")
        local_variant_types: set[str] = set()
        for v in variants:
            if v.get("image_source") not in ("legacy_url",):
                local_variant_types.add(v.get("display_label", ""))
        variants = [
            v for v in variants
            if v.get("image_source") != "legacy_url"
            or v.get("display_label", "") not in local_variant_types
        ]

        # 5. Suppress placeholder promo rows: variant_key='promo' with
        #    generic "Variant" label and no price, when other non-base variants exist
        has_non_base_variant = any(
            v for v in variants
            if v.get("variant_key") != "base"
            and v.get("variant_key") != "promo"
            and not v.get("is_base")
        )
        if has_non_base_variant:
            variants = [
                v for v in variants
                if not (
                    v.get("variant_key") == "promo"
                    and v.get("market_price") is None
                    and "Variant" in v.get("display_label", "")
                    and not v.get("has_market_map")
                )
            ]

        # 6. Differentiate duplicate display_labels: when multiple variants
        #    share the exact same label, append the parallel number suffix
        label_counts: dict[str, int] = {}
        for v in variants:
            lbl = v.get("display_label", "")
            label_counts[lbl] = label_counts.get(lbl, 0) + 1
        for v in variants:
            lbl = v.get("display_label", "")
            if label_counts.get(lbl, 0) > 1:
                vk = v.get("variant_key", "")
                # Extract parallel number from variant_key like "parallel_2"
                if vk.startswith("parallel_"):
                    suffix = vk.replace("parallel_", "#")
                    v["display_label"] = f"{lbl} ({suffix})"

        # --- END DEDUP PASS ---

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

@api_bp.get("/api/cards-json")
def api_cards_json():
    # Do not hard-gate the catalog on image_assets rows; cards can render via runtime thumbnail fallback.
    # Grid-only JSON: omit drawer blobs (effect/trigger); use GET /api/card-detail/<code> when needed.
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
                "thumb_src": entry.get("thumb_src", ""),
            }
        )
    response = jsonify({"ok": True, "cards": cards})
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@api_bp.get("/api/card-detail/<canonical_code>")
def api_card_detail(canonical_code: str):
    """Read-only drawer detail from cards row. No prices or images."""
    code = str(canonical_code or "").strip().upper()
    if not code:
        return jsonify({"error": "not found"}), 404
    if not CATALOG_DB_PATH.is_file():
        return jsonify({"error": "not found"}), 404
    try:
        conn = connect_catalog()
        row = conn.execute(
            """
            SELECT effect_text, trigger_text, traits
            FROM cards
            WHERE canonical_code = ?
            LIMIT 1
            """,
            (code,),
        ).fetchone()
        conn.close()
    except Exception:
        return jsonify({"error": "not found"}), 404
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(
        {
            "ok": True,
            "canonical_code": code,
            "effect_text": str(row["effect_text"] or "").strip(),
            "trigger_text": str(row["trigger_text"] or "").strip(),
            "traits": str(row["traits"] or "").strip(),
        }
    )


@api_bp.get("/api/card-lookup")
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


# ── Phase 3: Storefront API — sets, cards, card detail ─────────────
# These endpoints feed the SvelteKit storefront mounted at /storefront/.
# All reads are against card_catalog.db via the read-only pool.

def _img_url(local_path: str | None) -> str | None:
    """Convert an image_assets.local_path into a /img/<rel> URL, or None."""
    if not local_path:
        return None
    rel = _normalize_rel_image_path(str(local_path))
    if not rel:
        return None
    return f"/img/{quote(rel)}"


@api_bp.get("/api/sets")
def api_sets():
    """All sets with card counts, newest set_code first."""
    conn = connect_catalog()
    try:
        rows = conn.execute(
            """
            SELECT s.set_code, s.set_name,
                   (SELECT COUNT(*) FROM cards c WHERE c.set_code = s.set_code) AS card_count
            FROM sets s
            ORDER BY s.set_code DESC
            """
        ).fetchall()
    finally:
        conn.close()

    sets = [
        {
            "set_id": r["set_code"],
            "set_name": r["set_name"],
            "card_count": int(r["card_count"] or 0),
        }
        for r in rows
    ]
    return jsonify({"sets": sets})


@api_bp.get("/api/cards")
def api_cards_list():
    """
    Paginated, filterable cards list.

    Query params:
      set   (required): filter by cards.set_code
      page  (optional, default 1)
      limit (optional, default 40, max 100)
      color (optional): exact match on cards.color
      type  (optional): exact match on cards.card_type
    """
    set_id = str(request.args.get("set") or "").strip()
    if not set_id:
        return jsonify({"error": "set parameter required"}), 400

    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = int(request.args.get("limit") or 40)
    except (TypeError, ValueError):
        limit = 40
    limit = max(1, min(100, limit))
    offset = (page - 1) * limit

    color = str(request.args.get("color") or "").strip()
    ctype = str(request.args.get("type") or "").strip()

    filters = ["c.set_code = ?"]
    params: list = [set_id]
    if color:
        filters.append("c.color = ?")
        params.append(color)
    if ctype:
        filters.append("c.card_type = ?")
        params.append(ctype)
    where = " AND ".join(filters)

    conn = connect_catalog()
    try:
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM cards c WHERE {where}", params
            ).fetchone()[0]
        )
        rows = conn.execute(
            f"""
            SELECT c.canonical_code, c.card_name, c.card_type, c.color,
                   c.cost, c.power, c.counter, c.rarity, c.set_code,
                   (
                     SELECT ia.local_path
                     FROM card_variants cv
                     JOIN image_assets ia ON ia.printing_id = cv.id
                     WHERE cv.card_id = c.id AND cv.is_base = 1
                     ORDER BY ia.is_primary DESC, ia.id ASC
                     LIMIT 1
                   ) AS base_image_path
            FROM cards c
            WHERE {where}
            ORDER BY c.canonical_code ASC
            LIMIT ? OFFSET ?
            """,
            params + [limit, offset],
        ).fetchall()
    finally:
        conn.close()

    cards = [
        {
            "code": r["canonical_code"],
            "name": r["card_name"],
            "type": r["card_type"],
            "color": r["color"],
            "cost": r["cost"],
            "power": r["power"],
            "counter": r["counter"],
            "rarity": r["rarity"],
            "set_id": r["set_code"],
            "image_path": _img_url(r["base_image_path"]),
        }
        for r in rows
    ]
    pages = (total + limit - 1) // limit if total > 0 else 0
    return jsonify(
        {
            "cards": cards,
            "total": total,
            "page": page,
            "limit": limit,
            "pages": pages,
        }
    )


@api_bp.get("/api/cards/<code>")
def api_card_single(code: str):
    """Full detail for a single card by canonical code, with variants."""
    code_norm = str(code or "").strip().upper()
    if not code_norm:
        return jsonify({"error": "Card not found"}), 404

    conn = connect_catalog()
    try:
        row = conn.execute(
            """
            SELECT id, canonical_code, card_name, card_type, color, cost,
                   power, counter, rarity, set_code, effect_text, attribute, traits
            FROM cards
            WHERE canonical_code = ?
            LIMIT 1
            """,
            (code_norm,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Card not found"}), 404

        base_img_row = conn.execute(
            """
            SELECT ia.local_path
            FROM card_variants cv
            JOIN image_assets ia ON ia.printing_id = cv.id
            WHERE cv.card_id = ? AND cv.is_base = 1
            ORDER BY ia.is_primary DESC, ia.id ASC
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()

        vrows = conn.execute(
            """
            SELECT cv.id AS printing_id,
                   cv.variant_key,
                   cv.variant_label,
                   cv.is_base,
                   ia.local_path,
                   (
                     SELECT mp.market_price
                     FROM printing_market_map pmm
                     JOIN market_prices mp ON mp.market_product_fk = pmm.market_product_id
                     WHERE pmm.printing_id = cv.id
                     ORDER BY mp.captured_at DESC
                     LIMIT 1
                   ) AS market_price
            FROM card_variants cv
            JOIN image_assets ia
              ON ia.printing_id = cv.id AND ia.is_primary = 1
            WHERE cv.card_id = ?
            ORDER BY cv.is_base DESC, cv.id ASC
            """,
            (row["id"],),
        ).fetchall()
    finally:
        conn.close()

    # Archetype: traits column is a " / " separated list
    traits_raw = str(row["traits"] or "").strip()
    archetype = (
        [t.strip() for t in traits_raw.split("/") if t.strip()]
        if traits_raw
        else []
    )

    variants = [
        {
            "variant_key": v["variant_key"],
            "label": v["variant_label"] or ("Base" if v["is_base"] else ""),
            "image_path": _img_url(v["local_path"]),
            "market_price": v["market_price"],
        }
        for v in vrows
    ]

    base_img = _img_url(base_img_row["local_path"]) if base_img_row else None

    return jsonify(
        {
            "code": row["canonical_code"],
            "name": row["card_name"],
            "type": row["card_type"],
            "color": row["color"],
            "cost": row["cost"],
            "power": row["power"],
            "counter": row["counter"],
            "rarity": row["rarity"],
            "set_id": row["set_code"],
            "effect_text": row["effect_text"],
            "attribute": row["attribute"],
            "archetype": archetype,
            "image_path": base_img,
            "variants": variants,
        }
    )


# ── Phase 4: Deck save/load/validate ───────────────────────────────
# Decks live in a separate sqlite file to keep PM deck data isolated
# from card_catalog.db (which is treated as read-only for card data).

DECKS_DB_PATH = CATALOG_DB_PATH.parent / "pm_decks.db"


def _connect_decks():
    conn = sqlite3.connect(str(DECKS_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_decks_schema():
    conn = _connect_decks()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                leader_code TEXT NOT NULL,
                cards TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


# Initialize on module import so the file exists before first request.
_ensure_decks_schema()


def _parse_card_colors(color: str | None) -> list[str]:
    """Split a catalog color string like 'Red/Blue' into ['Red', 'Blue']."""
    if not color:
        return []
    return [c.strip() for c in str(color).split("/") if c.strip()]


def _fetch_card_info(codes: list[str]) -> dict[str, sqlite3.Row]:
    """Batch-load card metadata for the given canonical codes."""
    out: dict[str, sqlite3.Row] = {}
    codes = [c for c in codes if c]
    if not codes:
        return out
    unique = list({c.upper() for c in codes})
    placeholders = ",".join(["?"] * len(unique))
    conn = connect_catalog()
    try:
        rows = conn.execute(
            f"""
            SELECT canonical_code, card_name, card_type, color, cost, power
            FROM cards
            WHERE canonical_code IN ({placeholders})
            """,
            unique,
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        out[r["canonical_code"]] = r
    return out


@api_bp.get("/api/decks")
def api_decks_list():
    conn = _connect_decks()
    try:
        rows = conn.execute(
            """
            SELECT id, name, leader_code, cards, created_at, updated_at
            FROM decks
            ORDER BY updated_at DESC
            """
        ).fetchall()
    finally:
        conn.close()

    leader_codes = list({r["leader_code"] for r in rows})
    leader_info = _fetch_card_info(leader_codes)

    decks = []
    for r in rows:
        try:
            card_list = json.loads(r["cards"] or "[]")
        except (ValueError, TypeError):
            card_list = []
        total = sum(int(c.get("count") or 0) for c in card_list if isinstance(c, dict))
        leader_row = leader_info.get(r["leader_code"])
        decks.append(
            {
                "id": r["id"],
                "name": r["name"],
                "leader_code": r["leader_code"],
                "leader_name": leader_row["card_name"] if leader_row else "",
                "card_count": total,
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return jsonify({"decks": decks})


@api_bp.post("/api/decks")
def api_decks_create():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name") or "").strip()
    leader_code = str(payload.get("leader_code") or "").strip().upper()
    raw_cards = payload.get("cards")
    deck_id_in = str(payload.get("id") or "").strip()

    errors: list[str] = []
    if not name:
        errors.append("name is required")
    if not leader_code:
        errors.append("leader_code is required")

    normalized: list[dict] = []
    if raw_cards is None:
        raw_cards = []
    if not isinstance(raw_cards, list):
        errors.append("cards must be an array")
        raw_cards = []

    for c in raw_cards:
        if not isinstance(c, dict):
            errors.append(f"invalid card entry: {c!r}")
            continue
        code = str(c.get("code") or "").strip().upper()
        try:
            count = int(c.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if not code:
            errors.append("card entry missing code")
            continue
        if count < 1 or count > 4:
            errors.append(f"{code}: count {count} must be between 1 and 4")
        normalized.append({"code": code, "count": count})

    # Validate against catalog
    codes_to_check = [c["code"] for c in normalized]
    if leader_code:
        codes_to_check.append(leader_code)
    info = _fetch_card_info(codes_to_check)

    if leader_code:
        lrow = info.get(leader_code)
        if not lrow:
            errors.append(f"leader_code {leader_code} not found in catalog")
        elif (lrow["card_type"] or "").strip().lower() != "leader":
            errors.append(
                f"leader_code {leader_code} is not a Leader card "
                f"(found card_type={lrow['card_type']!r})"
            )

    for c in normalized:
        if c["code"] not in info:
            errors.append(f"card code {c['code']} not found in catalog")

    total_non_leader = sum(c["count"] for c in normalized)
    if total_non_leader > 50:
        errors.append(f"total card count {total_non_leader} exceeds 50")

    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    deck_id = deck_id_in or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    conn = _connect_decks()
    try:
        existing = conn.execute(
            "SELECT created_at FROM decks WHERE id = ?", (deck_id,)
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        conn.execute(
            """
            INSERT OR REPLACE INTO decks
                (id, name, leader_code, cards, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                deck_id,
                name,
                leader_code,
                json.dumps(normalized),
                created_at,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    leader_row = info.get(leader_code)
    return (
        jsonify(
            {
                "id": deck_id,
                "name": name,
                "leader_code": leader_code,
                "leader_name": leader_row["card_name"] if leader_row else "",
                "cards": normalized,
                "created_at": created_at,
                "updated_at": now,
            }
        ),
        201,
    )


@api_bp.get("/api/decks/<deck_id>")
def api_decks_get(deck_id: str):
    conn = _connect_decks()
    try:
        row = conn.execute(
            """
            SELECT id, name, leader_code, cards, created_at, updated_at
            FROM decks
            WHERE id = ?
            """,
            (deck_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Deck not found"}), 404

    try:
        card_list = json.loads(row["cards"] or "[]")
    except (ValueError, TypeError):
        card_list = []

    codes = [c["code"] for c in card_list if isinstance(c, dict) and c.get("code")]
    all_codes = codes + [row["leader_code"]] if row["leader_code"] else list(codes)
    info = _fetch_card_info(all_codes)
    leader_row = info.get(row["leader_code"])

    cards_out = []
    for c in card_list:
        if not isinstance(c, dict):
            continue
        code = str(c.get("code") or "").strip().upper()
        cinfo = info.get(code)
        cards_out.append(
            {
                "code": code,
                "count": int(c.get("count") or 0),
                "name": cinfo["card_name"] if cinfo else "",
                "type": cinfo["card_type"] if cinfo else "",
                "color": cinfo["color"] if cinfo else "",
                "cost": cinfo["cost"] if cinfo else None,
                "power": cinfo["power"] if cinfo else "",
            }
        )

    return jsonify(
        {
            "id": row["id"],
            "name": row["name"],
            "leader_code": row["leader_code"],
            "leader_name": leader_row["card_name"] if leader_row else "",
            "cards": cards_out,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


@api_bp.post("/api/decks/<deck_id>/validate")
def api_decks_validate(deck_id: str):
    conn = _connect_decks()
    try:
        row = conn.execute(
            "SELECT id, name, leader_code, cards FROM decks WHERE id = ?",
            (deck_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Deck not found"}), 404

    try:
        card_list = json.loads(row["cards"] or "[]")
    except (ValueError, TypeError):
        card_list = []

    codes = [c["code"] for c in card_list if isinstance(c, dict) and c.get("code")]
    all_codes = list(codes)
    if row["leader_code"]:
        all_codes.append(row["leader_code"])
    info = _fetch_card_info(all_codes)

    errors: list[str] = []
    warnings: list[str] = []

    leader_row = info.get(row["leader_code"])
    leader_name = ""
    leader_colors: list[str] = []
    if not row["leader_code"]:
        errors.append("Deck has no leader")
    elif not leader_row:
        errors.append(f"leader_code {row['leader_code']} not found in catalog")
    else:
        leader_name = leader_row["card_name"]
        if (leader_row["card_type"] or "").strip().lower() != "leader":
            errors.append(f"{row['leader_code']} is not a Leader card")
        leader_colors = _parse_card_colors(leader_row["color"])

    total_cards = 0
    colors_seen: set[str] = set()
    cost_curve: dict[str, int] = {}

    for c in card_list:
        if not isinstance(c, dict):
            continue
        code = str(c.get("code") or "").strip().upper()
        try:
            count = int(c.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        total_cards += max(0, count)

        if count > 4:
            errors.append(f"{code}: {count} copies exceeds max of 4")

        cinfo = info.get(code)
        if not cinfo:
            errors.append(f"card code {code} not found in catalog")
            continue

        card_colors = _parse_card_colors(cinfo["color"])
        for cc in card_colors:
            colors_seen.add(cc)
        if leader_colors and card_colors:
            if not any(cc in leader_colors for cc in card_colors):
                errors.append(
                    f"{code} color {cinfo['color']} does not match leader colors "
                    f"{leader_row['color'] if leader_row else ''}"
                )

        if cinfo["cost"] is not None:
            key = str(cinfo["cost"])
            cost_curve[key] = cost_curve.get(key, 0) + count

    # Card count: <50 is a warning (legal but still building), >50 is an error
    if total_cards > 50:
        errors.append(f"Deck has {total_cards} cards — exceeds 50")
    elif total_cards < 50:
        warnings.append(f"Deck has {total_cards} cards — {50 - total_cards} short of 50")

    valid = not errors and total_cards == 50

    return jsonify(
        {
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
            "summary": {
                "total_cards": total_cards,
                "leader": leader_name,
                "colors": sorted(colors_seen),
                "cost_curve": cost_curve,
            },
        }
    )
