import csv
import json
import os
import sqlite3
from pathlib import Path

from db import PROJECT_ROOT, connect_sqlite

CARD_PRICES_PATH = Path(
    os.getenv(
        "PROJECT_MIRU_PRICES_DB_PATH", str(PROJECT_ROOT / "data" / "card_prices.json")
    )
)
PRINTING_MARKET_MAP_OVERLAY_CSV = Path(
    os.getenv(
        "PROJECT_MIRU_PRINTING_MAP_OVERLAY_CSV",
        str(PROJECT_ROOT / "data" / "overlays" / "printing_market_map_overlay_v1.csv"),
    )
)
_printing_overlay_map_cache: dict[int, int] | None = None
_card_prices_cache: dict | None = None

MISMATCH_REVIEW_QUEUE_CSV = Path(
    os.getenv(
        "PROJECT_MIRU_REVIEW_QUEUE_CSV",
        str(PROJECT_ROOT / "data" / "overlays" / "op01_mismatch_review_queue.csv"),
    )
)
_review_queue_cache: dict[int, dict] | None = None

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


def _load_mismatch_review_queue() -> dict[int, dict]:
    """
    Load the mismatch review queue CSV.
    Returns {printing_id: {"review_status": str, "issue_type": str}}
    for all non-RESOLVED rows.  Resolved rows no longer suppress prices.
    """
    global _review_queue_cache
    if _review_queue_cache is not None:
        return _review_queue_cache
    out: dict[int, dict] = {}
    path = MISMATCH_REVIEW_QUEUE_CSV
    if not path.is_file():
        _review_queue_cache = out
        return out
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                if not raw:
                    continue
                try:
                    pid = int(str(raw.get("printing_id") or "").strip())
                except ValueError:
                    continue
                status = (raw.get("review_status") or "").strip().upper()
                if status == "RESOLVED":
                    continue
                out[pid] = {
                    "review_status": status or "OPEN",
                    "issue_type": (raw.get("issue_type") or "").strip(),
                }
    except Exception:
        out = {}
    _review_queue_cache = out
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


# Variant indicators in market product names that signal non-base printings.
_VARIANT_NAME_INDICATORS: tuple[str, ...] = (
    "(Alternate Art)", "(Alt Art)", "(SP)", "(Reprint)", "(Manga)",
    "(Pirate Foil)", "(Treasure)", "Treasure Cup", "Winner Pack",
    "Tournament Pack", "Participation Pack", "Champion Card Set",
    "Finalist Card Set", "Judge Pack", "Binder Set", "Celebration Pack",
    "Premium Card", "Beginners Deck Party", "Chase Promo",
    "Regional", "Textured", "Full Art", "Jolly Roger", "Box Topper",
    "Event Pack", "Promotion Pack",
)


def _disambiguate_multi_mapped(conn: sqlite3.Connection, printing_id: int) -> int | None:
    """
    For a printing_id mapped to multiple market_products, attempt to pick
    the correct one using name/set heuristics. Returns market_product.id
    or None if disambiguation fails.
    """
    vi = conn.execute(
        "SELECT cv.variant_key, c.set_code "
        "FROM card_variants cv JOIN cards c ON c.id = cv.card_id "
        "WHERE cv.id = ?",
        (printing_id,),
    ).fetchone()
    if vi is None:
        return None

    variant_key = (vi["variant_key"] or "").strip()
    set_code = (vi["set_code"] or "").strip().upper()

    products = conn.execute(
        "SELECT mp.id, mp.product_name, mp.market_set_code "
        "FROM printing_market_map pmm "
        "JOIN market_products mp ON mp.id = pmm.market_product_id "
        "WHERE pmm.printing_id = ?",
        (printing_id,),
    ).fetchall()
    if not products:
        return None

    # SP variant: match "(SP)" in name directly.
    if variant_key == "sp":
        sp_match = [p for p in products if "(SP)" in (p["product_name"] or "")]
        if len(sp_match) == 1:
            return sp_match[0]["id"]

    # Base / general path: filter out variant-indicator products.
    plain: list[sqlite3.Row] = []
    for p in products:
        name = (p["product_name"] or "").lower()
        if not any(ind.lower() in name for ind in _VARIANT_NAME_INDICATORS):
            plain.append(p)

    if not plain:
        # All products have variant indicators (common for promo cards).
        # Fallback: shortest name, lowest id among all products.
        fallback = sorted(products, key=lambda p: (len(p["product_name"] or ""), p["id"]))
        return fallback[0]["id"]
    if len(plain) == 1:
        return plain[0]["id"]

    # Multiple plain candidates: prefer matching set_code.
    if set_code:
        set_match = [p for p in plain if (p["market_set_code"] or "").upper() == set_code]
        if len(set_match) == 1:
            return set_match[0]["id"]
        if set_match:
            plain = set_match

    # Tiebreak: shortest name, then lowest market_product id.
    plain_sorted = sorted(plain, key=lambda p: (len(p["product_name"] or ""), p["id"]))
    return plain_sorted[0]["id"]


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
        conn = connect_sqlite(db_path)
        try:
            mapped_count = _mapped_product_count(conn, pid)
            if mapped_count > 1:
                # Multiple market products mapped to one printing_id is ambiguous.
                # Resolution order: overlay CSV → name/set disambiguation → fail closed.
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
                # Attempt name/set heuristic disambiguation.
                dis_mpk = _disambiguate_multi_mapped(conn, pid)
                if dis_mpk is not None:
                    drow = conn.execute(_overlay_price_sql(), (dis_mpk,)).fetchone()
                    if _price_chain_row_usable(drow):
                        return {
                            "market_price": drow["market_price"],
                            "mid_price": drow["mid_price"],
                            "market_variant_label": drow["market_variant_label"],
                            "mapping_confidence": "AUTO_DISAMBIG",
                            "captured_at": drow["captured_at"],
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
