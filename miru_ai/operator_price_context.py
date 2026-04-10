"""Operator review price context — read-only snapshot + narrow TCGCSV refresh (Miru 18765).

Uses ``card_catalog`` tables: ``card_variants``, ``printing_market_map``, ``market_products``,
``market_prices``. Does not trust ambiguous mappings; weak confidence is surfaced, not upgraded.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CATALOG_DB = _REPO_ROOT / "data" / "card_catalog.db"
_TCGCSV_ROOT = _REPO_ROOT / "data" / "tcgcsv"

# Freshness thresholds (wall-clock age of ``captured_at``).
_FRESH = timedelta(hours=24)
_AGING = timedelta(days=7)

_STRONG = frozenset({"HIGH", "MEDIUM"})


def _parse_captured_at(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip().replace(" ", "T", 1)
    try:
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _freshness_label(captured_at: str | None) -> str:
    dt = _parse_captured_at(captured_at)
    if dt is None:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age = now - dt
    if age < _FRESH:
        return "fresh"
    if age < _AGING:
        return "aging"
    return "stale"


def _normalize_code(s: str) -> str:
    return str(s or "").strip().upper()


def _normalize_vk(s: str) -> str:
    return str(s or "").strip().lower()


def _resolve_tcgcsv_folder(set_code: str) -> Path | None:
    code = str(set_code or "").strip()
    if not code:
        return None
    for name in (code.upper(), code.lower()):
        p = _TCGCSV_ROOT / name
        if p.is_dir() and (p / "prices.json").is_file():
            return p
    return None


def _verify_printing_identity(
    conn: sqlite3.Connection,
    printing_id: int,
    expected_card: str,
    expected_vk: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT cv.id, cv.variant_key, cv.variant_label, c.canonical_code
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        WHERE cv.id = ?
        """,
        (printing_id,),
    ).fetchone()
    if row is None:
        return None
    if _normalize_code(row["canonical_code"]) != _normalize_code(expected_card):
        return None
    if _normalize_vk(row["variant_key"] or "") != _normalize_vk(expected_vk):
        return None
    return {
        "printing_id": int(row["id"]),
        "variant_key": str(row["variant_key"] or ""),
        "variant_label": str(row["variant_label"] or ""),
        "canonical_code": str(row["canonical_code"] or ""),
    }


def _resolve_mapping(
    conn: sqlite3.Connection, printing_id: int
) -> dict[str, Any]:
    """Single market product for this printing, or unresolved/ambiguous."""
    pref = conn.execute(
        """
        SELECT market_product_id, mapping_confidence, mapping_method, is_preferred
        FROM printing_market_map
        WHERE printing_id = ? AND is_preferred = 1
        """,
        (printing_id,),
    ).fetchall()
    distinct_pref = {int(r["market_product_id"]) for r in pref}
    if len(distinct_pref) > 1:
        return {"truth": "unresolved", "reason": "multiple_preferred_maps"}
    if len(pref) == 1:
        r = pref[0]
        conf = str(r["mapping_confidence"] or "").strip().upper()
        truth = "confirmed" if conf in _STRONG else "weak"
        return {
            "truth": truth,
            "market_product_pk": int(r["market_product_id"]),
            "confidence": conf,
            "method": str(r["mapping_method"] or ""),
        }

    all_maps = conn.execute(
        """
        SELECT market_product_id, mapping_confidence, mapping_method
        FROM printing_market_map
        WHERE printing_id = ?
        """,
        (printing_id,),
    ).fetchall()
    distinct_all = {int(r["market_product_id"]) for r in all_maps}
    if len(all_maps) == 0:
        return {"truth": "unmapped", "reason": "no_printing_market_map"}
    if len(distinct_all) > 1:
        return {"truth": "unresolved", "reason": "multiple_market_products"}
    r = all_maps[0]
    conf = str(r["mapping_confidence"] or "").strip().upper()
    truth = "confirmed" if conf in _STRONG else "weak"
    return {
        "truth": truth,
        "market_product_pk": int(r["market_product_id"]),
        "confidence": conf,
        "method": str(r["mapping_method"] or ""),
    }


def _latest_price_row(
    conn: sqlite3.Connection, market_product_pk: int
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT market_price, mid_price, low_price, subtype_name, captured_at, source_name
        FROM market_prices
        WHERE market_product_fk = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (market_product_pk,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def build_operator_price_snapshot(
    *,
    printing_id: int,
    card_code: str,
    variant_key: str,
) -> dict[str, Any]:
    """Read-only price context for the reviewed printing + variant (fail-closed)."""
    out: dict[str, Any] = {
        "ok": False,
        "error": "",
        "printingId": printing_id,
        "cardCode": _normalize_code(card_code),
        "variantKey": str(variant_key or "").strip(),
    }
    if not _CATALOG_DB.is_file():
        out["error"] = "card_catalog.db not found."
        return out
    try:
        uri = f"file:{_CATALOG_DB}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            ident = _verify_printing_identity(conn, printing_id, card_code, variant_key)
            if ident is None:
                out["ok"] = False
                out["error"] = "Printing does not match card code / variant (fail-closed)."
                return out
            mapping = _resolve_mapping(conn, printing_id)
            out["mapping"] = mapping
            out["variantLabel"] = ident["variant_label"]

            if mapping["truth"] in ("unmapped", "unresolved"):
                out["ok"] = True
                out["price"] = None
                out["freshness"] = "unknown"
                return out

            mpk = mapping["market_product_pk"]
            mp = conn.execute(
                """
                SELECT id, market_product_id, product_name, market_variant_label, source_name
                FROM market_products
                WHERE id = ?
                """,
                (mpk,),
            ).fetchone()
            if mp is None:
                out["error"] = "market_products row missing."
                return out

            pr = _latest_price_row(conn, mpk)
            out["ok"] = True
            out["marketProduct"] = {
                "id": str(mp["market_product_id"] or ""),
                "productName": str(mp["product_name"] or ""),
                "marketVariantLabel": str(mp["market_variant_label"] or ""),
                "source": str(mp["source_name"] or "tcgcsv"),
            }
            if pr is None:
                out["price"] = None
                out["freshness"] = "unknown"
                return out

            cap = pr.get("captured_at")
            out["price"] = {
                "market": pr["market_price"],
                "mid": pr["mid_price"],
                "low": pr["low_price"],
                "subtypeName": pr["subtype_name"],
                "sourceName": str(pr["source_name"] or "tcgcsv"),
                "capturedAtIso": str(cap) if cap else "",
            }
            out["freshness"] = _freshness_label(str(cap) if cap else None)
            return out
    except sqlite3.Error as exc:
        out["error"] = f"Database read failed: {exc}"
        return out


def refresh_operator_price_from_tcgcsv(
    *,
    printing_id: int,
    card_code: str,
    variant_key: str,
) -> dict[str, Any]:
    """Insert a new ``market_prices`` row from local ``tcgcsv/.../prices.json`` only.

    Refuses unresolved/ambiguous mappings (same rules as display). Localhost-only at HTTP layer.
    """
    snap = build_operator_price_snapshot(
        printing_id=printing_id, card_code=card_code, variant_key=variant_key
    )
    if not snap.get("ok"):
        return snap
    mapping = snap.get("mapping") or {}
    if mapping.get("truth") in ("unmapped", "unresolved"):
        return {
            "ok": False,
            "error": "Cannot refresh: marketplace mapping is ambiguous or missing.",
            "previous": snap,
        }

    mp = snap.get("marketProduct") or {}
    ext_id = str(mp.get("id") or "").strip()
    if not ext_id:
        return {"ok": False, "error": "Missing market product id.", "previous": snap}

    set_code = ""
    try:
        uri = f"file:{_CATALOG_DB}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=15)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT c.set_code
                FROM card_variants cv
                JOIN cards c ON c.id = cv.card_id
                WHERE cv.id = ?
                """,
                (printing_id,),
            ).fetchone()
            if row:
                set_code = str(row["set_code"] or "")
    except sqlite3.Error:
        pass

    folder = _resolve_tcgcsv_folder(set_code)
    if folder is None:
        return {
            "ok": False,
            "error": f"No local TCGCSV folder for set {set_code!r} (data/tcgcsv).",
            "previous": snap,
        }

    prices_path = folder / "prices.json"
    try:
        with prices_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"Could not read prices.json: {exc}",
            "previous": snap,
        }

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return {"ok": False, "error": "prices.json has no results list.", "previous": snap}

    try:
        want = int(ext_id)
    except ValueError:
        return {"ok": False, "error": "Invalid market product id.", "previous": snap}

    price_row = None
    for p in results:
        if not isinstance(p, dict):
            continue
        try:
            if int(p.get("productId")) == want:
                price_row = p
                break
        except (TypeError, ValueError):
            continue

    if price_row is None:
        return {
            "ok": False,
            "error": "Product not found in local TCGCSV prices snapshot.",
            "previous": snap,
        }

    from miru_ai.workers.tcgcsv_fetcher import insert_market_price_if_present

    mpk = mapping["market_product_pk"]
    try:
        with closing(sqlite3.connect(str(_CATALOG_DB), timeout=20)) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            insert_market_price_if_present(conn, market_product_fk=mpk, price_row=price_row)
            conn.commit()
    except sqlite3.Error as exc:
        return {
            "ok": False,
            "error": f"Failed to store price row: {exc}",
            "previous": snap,
        }

    after = build_operator_price_snapshot(
        printing_id=printing_id, card_code=card_code, variant_key=variant_key
    )
    after["refreshed"] = True
    return after
