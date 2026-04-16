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
