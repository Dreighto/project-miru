"""Evidence collectors and reconciliation engine.

Wires BANDAI_CDN_CHECK, INTERNAL_ASSET_CHECK, PM_PARITY_CHECK, and
JUSTTCG_CONSTRAINED into the post-review evidence pipeline.  Called after
a dev_training_reviews row is stored.  All writes stay inside
miru_dev_training_reviews.db.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

_PROJECT_ROOT: Path | None = None
_DB_PATH: Path | None = None
_MIRU_ASSETS_ROOT: Path = Path(r"D:\Miru_Assets")

# Watchdog deadline for new reconciliation rows
_WATCHDOG_MINUTES = 15

# Confidence model
_OPERATOR_BASE_APPROVED = 0.60
_OPERATOR_BASE_REJECTED = -0.60

# PM parity target (GET-only, localhost)
_PM_IMG_BASE = "http://127.0.0.1:18080/img/"

# Bandai CDN HEAD target
_BANDAI_CDN_BASE = "https://en.onepiece-cardgame.com/images/cardlist/card/"

# PNG magic bytes
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"

# JustTCG constrained lookup
_JUSTTCG_API_BASE = "https://api.justtcg.com/v1"
_JUSTTCG_API_KEY: str | None = None


def _load_justtcg_api_key() -> str | None:
    """Load JUSTTCG_API_KEY from .env (lazy, cached)."""
    global _JUSTTCG_API_KEY
    if _JUSTTCG_API_KEY is not None:
        return _JUSTTCG_API_KEY
    env_path = (_PROJECT_ROOT or Path(__file__).resolve().parent.parent) / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "JUSTTCG_API_KEY":
            _JUSTTCG_API_KEY = v.strip()
            return _JUSTTCG_API_KEY
    return None


def configure(project_root: Path, miru_assets_root: Path | None = None) -> None:
    global _PROJECT_ROOT, _DB_PATH, _MIRU_ASSETS_ROOT
    _PROJECT_ROOT = Path(project_root)
    _DB_PATH = _PROJECT_ROOT / "data" / "miru_dev_training_reviews.db"
    if miru_assets_root is not None:
        _MIRU_ASSETS_ROOT = Path(miru_assets_root)


def _reviews_db_path() -> Path:
    if _DB_PATH is not None:
        return _DB_PATH
    root = _PROJECT_ROOT or Path(__file__).resolve().parent.parent
    return root / "data" / "miru_dev_training_reviews.db"


# ── Individual collectors ────────────────────────────────────────────────────

def _collect_bandai_cdn(card_code: str) -> dict[str, Any]:
    """HEAD check against official Bandai EN CDN for card image existence."""
    url = f"{_BANDAI_CDN_BASE}{card_code}.png"
    result: dict[str, Any] = {
        "source": "BANDAI_CDN_CHECK",
        "evidence_type": "CARD_EXISTENCE",
        "raw_query": f"HEAD {url}",
        "source_url": url,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }
    try:
        req = Request(url, method="HEAD")
        req.add_header("User-Agent", "MiruEvidenceCollector/1.0")
        with urlopen(req, timeout=10) as resp:
            status = resp.status
            content_length = resp.headers.get("Content-Length", "")
            result["raw_result_summary"] = f"HTTP {status}, Content-Length: {content_length}"
            result["raw_result_json"] = json.dumps({
                "status": status,
                "content_length": content_length,
            })
            if 200 <= status < 300:
                result["alignment"] = "SUPPORTS_OPERATOR"
            else:
                result["alignment"] = "INCONCLUSIVE"
    except HTTPError as exc:
        status = exc.code
        result["raw_result_summary"] = f"HTTP {status}"
        result["raw_result_json"] = json.dumps({"status": status})
        if status == 404:
            # Bandai CDN can contradict identity (can_contradict_identity=1)
            result["alignment"] = "CONTRADICTS_OPERATOR"
        else:
            result["alignment"] = "INCONCLUSIVE"
            result["error_detail"] = f"HTTP {status}"
    except (URLError, OSError) as exc:
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = str(exc)
        result["raw_result_summary"] = f"Network error: {exc}"
    return result


def _collect_internal_asset(card_code: str, variant_key: str,
                            miru_image_relpath: str) -> dict[str, Any]:
    """Check local Miru asset existence and basic integrity."""
    result: dict[str, Any] = {
        "source": "INTERNAL_ASSET_CHECK",
        "evidence_type": "IMAGE_REFERENCE",
        "raw_query": f"local:{miru_image_relpath or '(none)'}",
        "source_url": None,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }

    rel = (miru_image_relpath or "").strip().replace("\\", "/")
    if not rel:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = "No miru_image_relpath available"
        return result

    root = _MIRU_ASSETS_ROOT.resolve()
    candidate = (root / rel).resolve()

    # Path-traversal guard
    try:
        candidate.relative_to(root)
    except ValueError:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = "Path escapes asset root"
        result["error_detail"] = "path_traversal_blocked"
        return result

    if not candidate.is_file():
        result["alignment"] = "CONTRADICTS_OPERATOR"
        result["raw_result_summary"] = f"File not found: {rel}"
        result["raw_result_json"] = json.dumps({"exists": False, "path": rel})
        return result

    # File exists — check size and header
    file_size = candidate.stat().st_size
    png_ok = False
    try:
        with candidate.open("rb") as f:
            header = f.read(8)
            png_ok = header == _PNG_HEADER
    except OSError:
        pass

    checks: dict[str, Any] = {
        "exists": True,
        "path": rel,
        "size_bytes": file_size,
        "size_ok": file_size >= 102400,  # 100KB
        "png_header_valid": png_ok,
    }
    result["raw_result_json"] = json.dumps(checks)

    if file_size >= 102400 and png_ok:
        result["alignment"] = "SUPPORTS_OPERATOR"
        result["raw_result_summary"] = (
            f"File exists, {file_size} bytes, valid PNG header"
        )
    elif file_size >= 102400:
        # Large enough but not PNG — might be WebP or JPEG, still supportive
        result["alignment"] = "SUPPORTS_OPERATOR"
        result["raw_result_summary"] = (
            f"File exists, {file_size} bytes, non-PNG header"
        )
    elif file_size > 0:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = (
            f"File exists but small ({file_size} bytes)"
        )
    else:
        result["alignment"] = "CONTRADICTS_OPERATOR"
        result["raw_result_summary"] = "File exists but empty (0 bytes)"

    return result


def _collect_pm_parity(card_code: str, variant_key: str,
                       miru_image_relpath: str) -> dict[str, Any]:
    """GET-only parity check against PM (18080) /img/ endpoint."""
    result: dict[str, Any] = {
        "source": "PM_PARITY_CHECK",
        "evidence_type": "INTERNAL_CONSISTENCY",
        "raw_query": "",
        "source_url": None,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }

    rel = (miru_image_relpath or "").strip().replace("\\", "/")
    if not rel:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = "No miru_image_relpath to check parity for"
        return result

    url = f"{_PM_IMG_BASE}{rel}"
    result["raw_query"] = f"GET {url}"
    result["source_url"] = url

    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "MiruEvidenceCollector/1.0")
        with urlopen(req, timeout=10) as resp:
            status = resp.status
            content_length = resp.headers.get("Content-Length", "")
            content_type = resp.headers.get("Content-Type", "")
            # Read first 8 bytes only — enough for PNG header check, minimal load
            head_bytes = resp.read(8)
            pm_png = head_bytes == _PNG_HEADER

        checks = {
            "status": status,
            "content_length": content_length,
            "content_type": content_type,
            "pm_serves_file": True,
            "pm_png_header": pm_png,
        }
        result["raw_result_json"] = json.dumps(checks)
        result["raw_result_summary"] = (
            f"PM HTTP {status}, Content-Type: {content_type}, "
            f"Content-Length: {content_length}"
        )

        if 200 <= status < 300:
            result["alignment"] = "SUPPORTS_OPERATOR"
        else:
            result["alignment"] = "INCONCLUSIVE"

    except HTTPError as exc:
        status = exc.code
        result["raw_result_json"] = json.dumps({
            "status": status,
            "pm_serves_file": False,
        })
        if status == 404:
            result["alignment"] = "INCONCLUSIVE"
            result["raw_result_summary"] = "PM returned 404 — image not served"
        else:
            result["alignment"] = "INCONCLUSIVE"
            result["raw_result_summary"] = f"PM returned HTTP {status}"
            result["error_detail"] = f"HTTP {status}"

    except (URLError, OSError) as exc:
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = str(exc)
        result["raw_result_summary"] = f"PM unreachable: {exc}"
        result["raw_result_json"] = json.dumps({
            "pm_serves_file": False,
            "error": str(exc),
        })

    return result


def _resolve_tcgplayer_id(card_code: str, variant_key: str,
                          printing_id: int | None) -> int | None:
    """Resolve TCGPlayer product ID from card_catalog.db (read-only).

    Tries three paths in order:
      1. card_variants.tcgplayer_product_id (direct column)
      2. printing_market_map → market_products.market_product_id
      3. tcgplayer_products by card_code + parsed_variant_key
    """
    catalog = (_PROJECT_ROOT or Path(__file__).resolve().parent.parent) / "data" / "card_catalog.db"
    if not catalog.is_file():
        return None
    try:
        uri = f"file:{catalog}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row

            # Path 1: direct tcgplayer_product_id on card_variants
            if printing_id is not None:
                row = conn.execute(
                    "SELECT tcgplayer_product_id FROM card_variants WHERE id = ?",
                    (printing_id,),
                ).fetchone()
                if row and row["tcgplayer_product_id"]:
                    return int(row["tcgplayer_product_id"])

            # Path 2: printing_market_map → market_products
            if printing_id is not None:
                row = conn.execute(
                    """
                    SELECT mp.market_product_id
                    FROM printing_market_map pmm
                    JOIN market_products mp ON mp.id = pmm.market_product_id
                    WHERE pmm.printing_id = ? AND pmm.is_preferred = 1
                    LIMIT 1
                    """,
                    (printing_id,),
                ).fetchone()
                if row and row["market_product_id"]:
                    try:
                        return int(row["market_product_id"])
                    except (ValueError, TypeError):
                        pass

            # Path 3: tcgplayer_products by card_code + variant_key
            vk = variant_key or "base"
            row = conn.execute(
                """
                SELECT product_id FROM tcgplayer_products
                WHERE card_code = ? AND parsed_variant_key = ?
                LIMIT 1
                """,
                (card_code, vk),
            ).fetchone()
            if row and row["product_id"]:
                return int(row["product_id"])

    except sqlite3.Error:
        pass
    return None


def _collect_justtcg_constrained(card_code: str, variant_key: str,
                                 printing_id: int | None) -> dict[str, Any]:
    """Constrained JustTCG lookup by tcgplayerId for market-identity evidence."""
    result: dict[str, Any] = {
        "source": "JUSTTCG_CONSTRAINED",
        "evidence_type": "MARKET_IDENTITY",
        "raw_query": "",
        "source_url": None,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }

    api_key = _load_justtcg_api_key()
    if not api_key:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = "JUSTTCG_API_KEY not configured"
        result["error_detail"] = "no_api_key"
        return result

    tcg_id = _resolve_tcgplayer_id(card_code, variant_key, printing_id)
    if tcg_id is None:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = (
            f"No tcgplayerId found for {card_code}/{variant_key} "
            f"(printing_id={printing_id})"
        )
        return result

    url = f"{_JUSTTCG_API_BASE}/cards?tcgplayerId={tcg_id}&limit=5&include_price_history=false"
    result["raw_query"] = f"GET {url} (tcgplayerId={tcg_id})"
    result["source_url"] = url

    # Fetch with narrow retry on 429
    body: str | None = None
    status: int = 0
    for attempt in range(3):
        try:
            req = Request(url)
            req.add_header("User-Agent", "MiruEvidenceCollector/1.0")
            req.add_header("x-api-key", api_key)
            with urlopen(req, timeout=15) as resp:
                status = resp.status
                body = resp.read().decode("utf-8")
            break
        except HTTPError as exc:
            status = exc.code
            if status == 429 and attempt < 2:
                delay = 2 ** (attempt + 1)  # 2s, 4s
                log.info("JustTCG 429 for tcgplayerId=%s, backing off %ds", tcg_id, delay)
                time.sleep(delay)
                continue
            try:
                body = exc.read().decode("utf-8")
            except Exception:
                body = ""
            result["error_detail"] = f"HTTP {status}"
            break
        except (URLError, OSError) as exc:
            result["alignment"] = "INCONCLUSIVE"
            result["error_detail"] = str(exc)
            result["raw_result_summary"] = f"Network error: {exc}"
            return result

    if status != 200 or body is None:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = f"JustTCG HTTP {status}"
        result["raw_result_json"] = json.dumps({
            "tcgplayer_id": tcg_id, "status": status,
        })
        return result

    # Parse response
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = "JustTCG response not valid JSON"
        result["error_detail"] = "json_parse_error"
        return result

    data = payload.get("data", []) if isinstance(payload, dict) else []
    if not data:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = f"JustTCG returned empty data for tcgplayerId={tcg_id}"
        result["raw_result_json"] = json.dumps({
            "tcgplayer_id": tcg_id, "data_count": 0,
        })
        return result

    # Use the first (should be only) match for constrained ID lookup
    card = data[0] if isinstance(data, list) else data
    jtcg_name = str(card.get("name", "")).strip()
    jtcg_number = str(card.get("number", "")).strip().upper()
    jtcg_set = str(card.get("set_name") or card.get("set", "")).strip()
    jtcg_tcg_id = card.get("tcgplayerId")

    # Store concise replayable detail (omit price history / variant arrays)
    stored_detail = {
        "tcgplayer_id_queried": tcg_id,
        "data_count": len(data) if isinstance(data, list) else 1,
        "matched_name": jtcg_name,
        "matched_number": jtcg_number,
        "matched_set": jtcg_set,
        "matched_tcgplayerId": jtcg_tcg_id,
        "variant_count": len(card.get("variants", [])),
    }
    result["raw_result_json"] = json.dumps(stored_detail)

    # Market identity alignment: compare card number
    code_upper = card_code.upper()
    number_match = jtcg_number == code_upper

    if number_match:
        result["alignment"] = "SUPPORTS_OPERATOR"
        result["raw_result_summary"] = (
            f"JustTCG confirms {jtcg_number} \"{jtcg_name}\" "
            f"(tcgplayerId={jtcg_tcg_id}, set={jtcg_set})"
        )
    else:
        # Card number mismatch = market identity contradiction
        result["alignment"] = "CONTRADICTS_OPERATOR"
        result["raw_result_summary"] = (
            f"JustTCG returned number={jtcg_number} for tcgplayerId={tcg_id}, "
            f"expected {code_upper}. Name=\"{jtcg_name}\", set={jtcg_set}"
        )

    return result


# OPTCG API base
_OPTCG_API_BASE = "https://optcgapi.com/api"


def _collect_optcgapi_cross_check(card_code: str) -> dict[str, Any]:
    """Community OPTCG API cross-check.  Corroboration only — never CONTRADICTS."""
    url = f"{_OPTCG_API_BASE}/sets/card/{card_code}/"
    result: dict[str, Any] = {
        "source": "OPTCGAPI_CROSS_CHECK",
        "evidence_type": "CARD_EXISTENCE",
        "raw_query": f"GET {url}",
        "source_url": url,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }

    try:
        req = Request(url)
        req.add_header("User-Agent", "MiruEvidenceCollector/1.0")
        with urlopen(req, timeout=15) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        status = exc.code
        if status == 404:
            result["alignment"] = "NOT_APPLICABLE"
            result["raw_result_summary"] = f"OPTCG API 404 for {card_code}"
            result["raw_result_json"] = json.dumps({
                "card_code": card_code, "status": 404,
            })
            return result
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = f"HTTP {status}"
        result["raw_result_summary"] = f"OPTCG API HTTP {status}"
        result["raw_result_json"] = json.dumps({
            "card_code": card_code, "status": status,
        })
        return result
    except (URLError, OSError) as exc:
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = str(exc)
        result["raw_result_summary"] = f"OPTCG API unreachable: {exc}"
        return result

    if status != 200:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = f"OPTCG API HTTP {status}"
        result["raw_result_json"] = json.dumps({
            "card_code": card_code, "status": status,
        })
        return result

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = "OPTCG API response not valid JSON"
        result["error_detail"] = "json_parse_error"
        return result

    # API returns a list of variant objects for the card
    items = payload if isinstance(payload, list) else []
    if not items:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = f"OPTCG API returned empty for {card_code}"
        result["raw_result_json"] = json.dumps({
            "card_code": card_code, "data_count": 0,
        })
        return result

    # Use first item for identity corroboration
    first = items[0] if isinstance(items[0], dict) else {}
    api_code = str(first.get("card_set_id", "")).strip().upper()
    api_name = str(first.get("card_name", "")).strip()
    api_set = str(first.get("set_name", "")).strip()

    stored_detail = {
        "card_code": card_code,
        "data_count": len(items),
        "api_card_set_id": api_code,
        "api_card_name": api_name,
        "api_set_name": api_set,
    }
    result["raw_result_json"] = json.dumps(stored_detail)

    code_upper = card_code.upper()
    if api_code == code_upper:
        result["alignment"] = "SUPPORTS_OPERATOR"
        result["raw_result_summary"] = (
            f"OPTCG API confirms {api_code} \"{api_name}\" "
            f"(set={api_set}, variants={len(items)})"
        )
    else:
        # Mismatch — but per authority rules, never CONTRADICTS
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = (
            f"OPTCG API returned card_set_id={api_code} for query {code_upper}, "
            f"name=\"{api_name}\""
        )

    return result


_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _extract_operator_url(source_note: str, missing_image_source_url: str) -> str | None:
    """Return the first valid operator-supplied URL, or None."""
    # Explicit URL field takes priority
    url = (missing_image_source_url or "").strip()
    if url and _URL_RE.match(url):
        return url
    # Fall back to source_note if it contains a URL
    m = _URL_RE.search(source_note or "")
    if m:
        return m.group(0)
    return None


def _html_to_visible_text(raw_html: str) -> str:
    """Minimal tag-stripping for visible-text extraction. No heavy parser."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html,
                  flags=re.IGNORECASE | re.DOTALL)
    # Strip remaining tags
    text = _TAG_RE.sub(" ", text)
    # Decode entities
    text = html.unescape(text)
    # Collapse whitespace
    return re.sub(r"\s+", " ", text).strip()


def _collect_operator_url(card_code: str, source_note: str,
                          missing_image_source_url: str) -> dict[str, Any]:
    """Operator-supplied URL corroboration.  Never CONTRADICTS."""
    result: dict[str, Any] = {
        "source": "OPERATOR_URL",
        "evidence_type": "CARD_EXISTENCE",
        "raw_query": "",
        "source_url": None,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }

    url = _extract_operator_url(source_note, missing_image_source_url)
    if not url:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = "No operator URL present in review"
        return result

    result["raw_query"] = f"GET {url}"
    result["source_url"] = url

    try:
        req = Request(url)
        req.add_header("User-Agent", "MiruEvidenceCollector/1.0")
        with urlopen(req, timeout=10) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read(512_000).decode("utf-8", errors="replace")
    except HTTPError as exc:
        status = exc.code
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = f"HTTP {status}"
        result["raw_result_summary"] = f"Operator URL returned HTTP {status}"
        result["raw_result_json"] = json.dumps({"url": url, "status": status})
        return result
    except (URLError, OSError) as exc:
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = str(exc)
        result["raw_result_summary"] = f"Operator URL unreachable: {exc}"
        result["raw_result_json"] = json.dumps({"url": url, "error": str(exc)})
        return result

    if status != 200:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = f"Operator URL HTTP {status}"
        result["raw_result_json"] = json.dumps({"url": url, "status": status})
        return result

    # Extract visible text
    if "html" in content_type.lower():
        visible = _html_to_visible_text(body)
    else:
        visible = body

    code_upper = card_code.upper()
    # Case-insensitive search in visible text
    visible_upper = visible.upper()
    code_found = code_upper in visible_upper

    stored_detail: dict[str, Any] = {
        "url": url,
        "status": status,
        "content_type": content_type,
        "visible_text_length": len(visible),
        "card_code_found": code_found,
    }

    if not code_found:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = (
            f"Operator URL fetched OK but card code {code_upper} not found in visible text"
        )
        result["raw_result_json"] = json.dumps(stored_detail)
        return result

    # Card code found — SUPPORTS
    result["alignment"] = "SUPPORTS_OPERATOR"
    result["raw_result_summary"] = (
        f"Operator URL contains card code {code_upper} in visible text "
        f"(page {len(visible)} chars)"
    )
    result["raw_result_json"] = json.dumps(stored_detail)
    return result


# Perplexity API
_PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
_PERPLEXITY_API_KEY: str | None = None


def _load_perplexity_api_key() -> str | None:
    """Load PERPLEXITY_API_KEY from .env (lazy, cached)."""
    global _PERPLEXITY_API_KEY
    if _PERPLEXITY_API_KEY is not None:
        return _PERPLEXITY_API_KEY
    env_path = (_PROJECT_ROOT or Path(__file__).resolve().parent.parent) / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "PERPLEXITY_API_KEY":
            _PERPLEXITY_API_KEY = v.strip()
            return _PERPLEXITY_API_KEY
    return None


def _resolve_card_name(card_code: str) -> str | None:
    """Look up card name from card_catalog.db (read-only)."""
    catalog = (_PROJECT_ROOT or Path(__file__).resolve().parent.parent) / "data" / "card_catalog.db"
    if not catalog.is_file():
        return None
    try:
        uri = f"file:{catalog}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT card_name, set_code FROM cards WHERE canonical_code = ? LIMIT 1",
                (card_code,),
            ).fetchone()
            if row and row["card_name"]:
                return str(row["card_name"]).strip()
    except sqlite3.Error:
        pass
    return None


def _collect_perplexity(card_code: str, variant_key: str) -> dict[str, Any]:
    """Perplexity web-search corroboration.  Never CONTRADICTS."""
    result: dict[str, Any] = {
        "source": "PERPLEXITY",
        "evidence_type": "CARD_EXISTENCE",
        "raw_query": "",
        "source_url": None,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }

    api_key = _load_perplexity_api_key()
    if not api_key:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = "PERPLEXITY_API_KEY not configured"
        result["error_detail"] = "no_api_key"
        return result

    # Build narrow query from review context
    card_name = _resolve_card_name(card_code)
    query_parts = ["One Piece Card Game", card_code]
    if card_name:
        query_parts.append(card_name)
    if variant_key and variant_key != "base":
        query_parts.append(variant_key)
    query = " ".join(query_parts)
    result["raw_query"] = query

    request_body = json.dumps({
        "model": "sonar",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a concise card-game fact checker. "
                    "Answer with only verifiable facts about the card. "
                    "Include the card code, name, set, and type if known."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"What is known about the One Piece Card Game card {card_code}"
                    + (f" ({card_name})" if card_name else "")
                    + "? Include the card code, name, set name, and card type."
                ),
            },
        ],
        "max_tokens": 300,
        "temperature": 0.0,
    }).encode("utf-8")

    try:
        req = Request(_PERPLEXITY_API_URL, data=request_body, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "MiruEvidenceCollector/1.0")
        with urlopen(req, timeout=20) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        status = exc.code
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = f"HTTP {status}"
        result["raw_result_summary"] = f"Perplexity API HTTP {status}"
        result["raw_result_json"] = json.dumps({"query": query, "status": status})
        return result
    except (URLError, OSError) as exc:
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = str(exc)
        result["raw_result_summary"] = f"Perplexity API unreachable: {exc}"
        result["raw_result_json"] = json.dumps({"query": query, "error": str(exc)})
        return result

    if status != 200:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = f"Perplexity API HTTP {status}"
        result["raw_result_json"] = json.dumps({"query": query, "status": status})
        return result

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = "Perplexity response not valid JSON"
        result["error_detail"] = "json_parse_error"
        return result

    # Extract answer text from chat completion response
    choices = payload.get("choices", [])
    if not choices:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = "Perplexity returned no choices"
        result["raw_result_json"] = json.dumps({"query": query, "choices": 0})
        return result

    message = choices[0].get("message", {})
    answer = str(message.get("content", "")).strip()
    citations = payload.get("citations", [])

    # Truncate answer for storage (keep replayable but bounded)
    stored_answer = answer[:1000]
    stored_citations = citations[:5] if isinstance(citations, list) else []

    stored_detail: dict[str, Any] = {
        "query": query,
        "answer_length": len(answer),
        "answer_snippet": stored_answer,
        "citations": stored_citations,
        "card_code_in_answer": False,
        "card_name_in_answer": False,
    }

    if not answer:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = "Perplexity returned empty answer"
        result["raw_result_json"] = json.dumps(stored_detail)
        return result

    # Check if card code appears in answer
    answer_upper = answer.upper()
    code_upper = card_code.upper()
    code_found = code_upper in answer_upper
    stored_detail["card_code_in_answer"] = code_found

    # Check if card name appears in answer
    name_found = False
    if card_name:
        name_found = card_name.lower() in answer.lower()
        stored_detail["card_name_in_answer"] = name_found

    result["raw_result_json"] = json.dumps(stored_detail)

    if code_found:
        result["alignment"] = "SUPPORTS_OPERATOR"
        result["raw_result_summary"] = (
            f"Perplexity confirms {code_upper}"
            + (f" \"{card_name}\"" if name_found else "")
            + f" ({len(answer)} chars, {len(stored_citations)} citations)"
        )
    elif name_found:
        # Card name found but not code — weak support still sufficient
        result["alignment"] = "SUPPORTS_OPERATOR"
        result["raw_result_summary"] = (
            f"Perplexity mentions \"{card_name}\" (card code not verbatim)"
            + f" ({len(answer)} chars, {len(stored_citations)} citations)"
        )
    else:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = (
            f"Perplexity response does not clearly reference {code_upper}"
            + (f" or \"{card_name}\"" if card_name else "")
            + f" ({len(answer)} chars)"
        )

    return result


# YouTube Data API v3
_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_YOUTUBE_API_KEY: str | None = None

# Promo / alt-art / showcase keywords for relevance filtering
_YOUTUBE_PROMO_KEYWORDS = frozenset({
    "promo", "alt art", "alt-art", "alternate art", "showcase",
    "reveal", "preview", "unbox", "opening", "one piece card",
    "optcg", "one piece tcg",
})


def _load_youtube_api_key() -> str | None:
    """Load YOUTUBE_API_KEY from .env (lazy, cached)."""
    global _YOUTUBE_API_KEY
    if _YOUTUBE_API_KEY is not None:
        return _YOUTUBE_API_KEY
    env_path = (_PROJECT_ROOT or Path(__file__).resolve().parent.parent) / ".env"
    if not env_path.is_file():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "YOUTUBE_API_KEY":
            _YOUTUBE_API_KEY = v.strip()
            return _YOUTUBE_API_KEY
    return None


def _collect_youtube(card_code: str, variant_key: str) -> dict[str, Any]:
    """YouTube promo/alt-art corroboration.  Never CONTRADICTS."""
    result: dict[str, Any] = {
        "source": "YOUTUBE",
        "evidence_type": "PROMO_REVEAL",
        "raw_query": "",
        "source_url": None,
        "alignment": "INCONCLUSIVE",
        "raw_result_summary": "",
        "raw_result_json": "{}",
        "error_detail": None,
    }

    api_key = _load_youtube_api_key()
    if not api_key:
        result["alignment"] = "NOT_APPLICABLE"
        result["raw_result_summary"] = "YOUTUBE_API_KEY not configured"
        result["error_detail"] = "no_api_key"
        return result

    # Build narrow query
    card_name = _resolve_card_name(card_code)
    query_parts = ["One Piece Card Game", card_code]
    if card_name:
        query_parts.append(card_name)
    if variant_key and variant_key != "base":
        query_parts.append(variant_key.replace("_", " "))
    query = " ".join(query_parts)
    result["raw_query"] = query

    from urllib.parse import quote as url_quote
    params = (
        f"part=snippet&q={url_quote(query)}&type=video"
        f"&maxResults=3&key={api_key}"
    )
    url = f"{_YOUTUBE_SEARCH_URL}?{params}"

    try:
        req = Request(url)
        req.add_header("User-Agent", "MiruEvidenceCollector/1.0")
        with urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8")
    except HTTPError as exc:
        status = exc.code
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = f"HTTP {status}"
        result["raw_result_summary"] = f"YouTube API HTTP {status}"
        result["raw_result_json"] = json.dumps({"query": query, "status": status})
        return result
    except (URLError, OSError) as exc:
        result["alignment"] = "INCONCLUSIVE"
        result["error_detail"] = str(exc)
        result["raw_result_summary"] = f"YouTube API unreachable: {exc}"
        result["raw_result_json"] = json.dumps({"query": query, "error": str(exc)})
        return result

    if status != 200:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = f"YouTube API HTTP {status}"
        result["raw_result_json"] = json.dumps({"query": query, "status": status})
        return result

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = "YouTube API response not valid JSON"
        result["error_detail"] = "json_parse_error"
        return result

    items = payload.get("items", [])
    if not items:
        result["alignment"] = "INCONCLUSIVE"
        result["raw_result_summary"] = f"YouTube returned no results for \"{query}\""
        result["raw_result_json"] = json.dumps({
            "query": query, "result_count": 0,
        })
        return result

    # Evaluate top results for relevance
    code_upper = card_code.upper()
    card_name_lower = (card_name or "").lower()
    best_match: dict[str, Any] | None = None

    stored_items: list[dict[str, Any]] = []
    for item in items[:3]:
        snippet = item.get("snippet", {})
        title = str(snippet.get("title", ""))
        description = str(snippet.get("description", ""))
        channel = str(snippet.get("channelTitle", ""))
        video_id = item.get("id", {}).get("videoId", "")
        video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""

        combined = f"{title} {description}".upper()
        combined_lower = combined.lower()

        code_found = code_upper in combined
        name_found = bool(card_name_lower and card_name_lower in combined_lower)
        promo_relevant = any(kw in combined_lower for kw in _YOUTUBE_PROMO_KEYWORDS)

        entry = {
            "video_id": video_id,
            "title": title[:200],
            "channel": channel[:100],
            "url": video_url,
            "code_found": code_found,
            "name_found": name_found,
            "promo_relevant": promo_relevant,
        }
        stored_items.append(entry)

        if best_match is None and (code_found or name_found) and promo_relevant:
            best_match = entry

    stored_detail: dict[str, Any] = {
        "query": query,
        "result_count": len(items),
        "evaluated": stored_items,
        "best_match": best_match,
    }
    result["raw_result_json"] = json.dumps(stored_detail)

    if best_match:
        result["alignment"] = "SUPPORTS_OPERATOR"
        result["source_url"] = best_match["url"]
        result["raw_result_summary"] = (
            f"YouTube: \"{best_match['title']}\" by {best_match['channel']}"
            f" (code={'yes' if best_match['code_found'] else 'no'},"
            f" name={'yes' if best_match['name_found'] else 'no'},"
            f" promo_relevant=yes)"
        )
    else:
        result["alignment"] = "INCONCLUSIVE"
        titles = ", ".join(f"\"{e['title'][:60]}\"" for e in stored_items[:2])
        result["raw_result_summary"] = (
            f"YouTube returned {len(items)} result(s) but none clearly "
            f"promo/alt-art relevant for {code_upper}. Top: {titles}"
        )

    return result


# ── Confidence calculation ───────────────────────────────────────────────────

def _compute_confidence(operator_action: str,
                        evidence_rows: list[dict[str, Any]],
                        weights: dict[str, float]) -> float:
    """Locked first-pass model: operator base + weighted evidence sum, clamped."""
    if operator_action == "approve":
        base = _OPERATOR_BASE_APPROVED
    else:
        base = _OPERATOR_BASE_REJECTED

    total = base
    for ev in evidence_rows:
        src = ev["evidence_source"]
        w = weights.get(src, 0.0)
        alignment = ev["alignment"]
        if alignment == "SUPPORTS_OPERATOR":
            total += w
        elif alignment == "CONTRADICTS_OPERATOR":
            total -= w
        # INCONCLUSIVE / NOT_APPLICABLE contribute 0
    return max(-1.0, min(1.0, total))


# ── Main orchestration ───────────────────────────────────────────────────────

def collect_evidence_for_review(review_id: int) -> dict[str, Any]:
    """Run Phase B evidence collection for a single review row.

    Idempotent: supersedes prior ACTIVE evidence for the same review+source
    before inserting new rows.  Creates reconciliation row if absent.

    Returns a summary dict for logging.
    """
    db = _reviews_db_path()
    if not db.is_file():
        return {"error": "DB not found"}

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    deadline = (now + timedelta(minutes=_WATCHDOG_MINUTES)).isoformat()

    with closing(sqlite3.connect(str(db))) as conn:
        conn.row_factory = sqlite3.Row

        # Load review row
        review = conn.execute(
            "SELECT * FROM dev_training_reviews WHERE id = ?", (review_id,)
        ).fetchone()
        if review is None:
            return {"error": f"review_id {review_id} not found"}

        card_code = review["card_code"]
        variant_key = review["variant_key"] or ""
        relpath = review["miru_image_relpath"] or ""
        printing_id = review["printing_id"]
        source_note = review["source_note"] or ""
        missing_image_source_url = review["missing_image_source_url"] or ""
        action = review["action"]
        verdict = review["verdict"]

        # Load source weights
        weight_rows = conn.execute(
            "SELECT source, weight FROM evidence_source_weights WHERE active = 1"
        ).fetchall()
        weights = {r["source"]: r["weight"] for r in weight_rows}

        # Ensure reconciliation row exists (idempotent)
        conn.execute(
            """
            INSERT OR IGNORE INTO evidence_reconciliation
                (review_id, operator_verdict, reconciliation_status, watchdog_deadline)
            VALUES (?, ?, 'PENDING', ?)
            """,
            (review_id, verdict, deadline),
        )

        # Collect evidence from all 8 sources (Phase B–G)
        raw_evidence = [
            _collect_bandai_cdn(card_code),
            _collect_internal_asset(card_code, variant_key, relpath),
            _collect_pm_parity(card_code, variant_key, relpath),
            _collect_justtcg_constrained(card_code, variant_key, printing_id),
            _collect_optcgapi_cross_check(card_code),
            _collect_operator_url(card_code, source_note, missing_image_source_url),
            _collect_perplexity(card_code, variant_key),
            _collect_youtube(card_code, variant_key),
        ]

        # Supersede any prior ACTIVE evidence for same review+source, then insert
        for ev in raw_evidence:
            conn.execute(
                """
                UPDATE post_review_evidence
                   SET evidence_status = 'SUPERSEDED'
                 WHERE review_id = ?
                   AND evidence_source = ?
                   AND evidence_status = 'ACTIVE'
                """,
                (review_id, ev["source"]),
            )

            # Compute per-row confidence contribution
            w = weights.get(ev["source"], 0.0)
            if ev["alignment"] == "SUPPORTS_OPERATOR":
                contrib = w
            elif ev["alignment"] == "CONTRADICTS_OPERATOR":
                contrib = -w
            else:
                contrib = 0.0

            conn.execute(
                """
                INSERT INTO post_review_evidence (
                    review_id, card_code, variant_key, evidence_source,
                    evidence_type, raw_query, raw_result_summary,
                    raw_result_json, alignment, confidence_contribution,
                    source_url, fetched_at, evidence_status, error_detail
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    review_id, card_code, variant_key, ev["source"],
                    ev["evidence_type"], ev["raw_query"],
                    ev["raw_result_summary"], ev["raw_result_json"],
                    ev["alignment"], contrib, ev["source_url"],
                    now_iso, ev.get("error_detail"),
                ),
            )

        # Reconcile: count ACTIVE evidence for this review
        counts = conn.execute(
            """
            SELECT
                COUNT(*)                                           AS total,
                SUM(CASE WHEN alignment = 'SUPPORTS_OPERATOR'    THEN 1 ELSE 0 END) AS supporting,
                SUM(CASE WHEN alignment = 'CONTRADICTS_OPERATOR' THEN 1 ELSE 0 END) AS contradicting,
                SUM(CASE WHEN alignment = 'INCONCLUSIVE'         THEN 1 ELSE 0 END) AS inconclusive
            FROM post_review_evidence
            WHERE review_id = ? AND evidence_status = 'ACTIVE'
            """,
            (review_id,),
        ).fetchone()

        evidence_count = counts["total"]
        supporting = counts["supporting"]
        contradicting = counts["contradicting"]
        inconclusive = counts["inconclusive"]

        # Reload active evidence rows for confidence calc
        active_rows = conn.execute(
            """
            SELECT evidence_source, alignment
            FROM post_review_evidence
            WHERE review_id = ? AND evidence_status = 'ACTIVE'
            """,
            (review_id,),
        ).fetchall()
        active_dicts = [dict(r) for r in active_rows]
        composite = _compute_confidence(action, active_dicts, weights)

        # Determine reconciliation status
        if contradicting > 0:
            recon_status = "CONTRADICTED"
        elif supporting > 0 and inconclusive == 0:
            recon_status = "SUPPORTED"
        elif supporting > 0:
            recon_status = "SUPPORTED"
        elif inconclusive > 0:
            recon_status = "INCONCLUSIVE"
        else:
            recon_status = "INCONCLUSIVE"

        # Collect contradiction sources
        contra_sources = None
        if contradicting > 0:
            contra_rows = conn.execute(
                """
                SELECT DISTINCT evidence_source
                FROM post_review_evidence
                WHERE review_id = ? AND evidence_status = 'ACTIVE'
                  AND alignment = 'CONTRADICTS_OPERATOR'
                """,
                (review_id,),
            ).fetchall()
            contra_sources = ",".join(r["evidence_source"] for r in contra_rows)

        # Update reconciliation
        conn.execute(
            """
            UPDATE evidence_reconciliation
               SET evidence_count        = ?,
                   supporting_count      = ?,
                   contradicting_count   = ?,
                   inconclusive_count    = ?,
                   composite_confidence  = ?,
                   reconciliation_status = ?,
                   contradiction_sources = ?,
                   reconciled_at         = ?,
                   requires_elevated_review = CASE WHEN ? > 0 THEN 1 ELSE 0 END
             WHERE review_id = ?
            """,
            (
                evidence_count, supporting, contradicting, inconclusive,
                composite, recon_status, contra_sources, now_iso,
                contradicting, review_id,
            ),
        )

        conn.commit()

    # Refresh recurrence aggregates now that reconciliation is final.
    # Runs outside the evidence DB connection so it gets its own transaction.
    try:
        from miru_ai.recurrence import refresh_recurrence_for_review
        recurrence_result = refresh_recurrence_for_review(review_id)
        log.info("Recurrence refresh for review %d: %s", review_id, recurrence_result)
    except Exception:
        log.exception("Recurrence refresh failed for review %d (non-fatal)", review_id)

    summary = {
        "review_id": review_id,
        "card_code": card_code,
        "evidence_count": evidence_count,
        "supporting": supporting,
        "contradicting": contradicting,
        "inconclusive": inconclusive,
        "composite_confidence": round(composite, 4),
        "reconciliation_status": recon_status,
        "contradiction_sources": contra_sources,
    }
    log.info("Evidence collected for review %d: %s", review_id, summary)
    return summary
