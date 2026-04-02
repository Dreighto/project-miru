from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tools.miru_ai_onepiece import clean_display_text, normalize_card_code


logger = logging.getLogger(__name__)

CARD_CODE_RE = re.compile(r"\b(?:OP\d{2}-\d{3}|EB\d{2}-\d{3}|P-\d{3}|PRB-\d{2})\b", re.IGNORECASE)
SET_CODE_RE = re.compile(r"\b(?:OP\d{2}|EB\d{2}|PRB-\d{2}|P-\d{3})\b", re.IGNORECASE)
COST_RE = re.compile(r"(?:cost|コスト)\s*[:\-]?\s*(\d{1,2})", re.IGNORECASE)
POWER_RE = re.compile(r"(?:power|パワー)\s*[:\-]?\s*(\d{3,5})", re.IGNORECASE)
COLOR_RE = re.compile(r"\b(red|blue|green|purple|black|yellow)\b", re.IGNORECASE)
TYPE_RE = re.compile(r"\b(leader|character|event|stage|don!!)\b", re.IGNORECASE)
RARITY_RE = re.compile(r"\b(?:SEC|SP|L|SR|R|UC|C|TR)\b", re.IGNORECASE)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
VISION_MODEL = "claude-sonnet-4-20250514"
VISION_SYSTEM_PROMPT = """You are a card image analyst for the One Piece Trading Card Game.
Analyze the card image provided and extract the following fields if
visible. Return ONLY a JSON object with these keys:
card_code, card_name, color, card_type, rarity, cost, power,
sp_marker_detected, parallel_marker_detected, tr_marker_detected,
confidence, notes.

For marker fields return true/false.
For confidence return a float 0.0-1.0 representing your certainty
across all extracted fields.
For notes return a list of strings describing anything ambiguous.
Return null for any field you cannot determine with reasonable certainty.
Do not guess. Return null rather than speculate."""

SP_BRACKET_RE = re.compile(r"\[SP\]", re.IGNORECASE)
STAR_CHARS = frozenset("★☆✩✪✫✬✭✮✯⭐🌟")
TR_TOKEN_RE = re.compile(r"\bTR\b", re.IGNORECASE)


@dataclass(frozen=True)
class VisualAnalysisResult:
    extraction_method: str
    extracted_fields: dict[str, Any]
    confidence: float
    verification_status: str
    source_rollup: dict[str, Any]
    conflict_flags: list[str]
    analysis_notes: list[str]
    analyzed_at: str
    cache_hit: bool = False
    ocr_text_excerpt: str = ""
    token_spend: int = 0
    tier_used: int = 1
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_card_match(value: str) -> str:
    normalized = normalize_card_code(value)
    return normalized["canonical_code"] or clean_display_text(str(value or "")).upper()


def _extract_codes_from_text(text: str) -> tuple[str, str]:
    card_match = CARD_CODE_RE.search(text or "")
    set_match = SET_CODE_RE.search(text or "")
    card_code = _normalize_card_match(card_match.group(0)) if card_match else ""
    set_code = clean_display_text(set_match.group(0)).upper() if set_match else ""
    if not set_code and card_code:
        set_code = card_code.split("-", 1)[0]
    return card_code, set_code


def _extract_text_fields(text: str) -> dict[str, Any]:
    card_code, set_code = _extract_codes_from_text(text)
    payload: dict[str, Any] = {}
    if card_code:
        payload["card_code"] = card_code
    if set_code:
        payload["set_code"] = set_code
    if match := COST_RE.search(text or ""):
        payload["cost"] = match.group(1)
    if match := POWER_RE.search(text or ""):
        payload["power"] = match.group(1)
    if match := COLOR_RE.search(text or ""):
        payload["color"] = match.group(1).title()
    if match := TYPE_RE.search(text or ""):
        payload["card_type"] = match.group(1).title()
    if match := RARITY_RE.search(text or ""):
        payload["rarity"] = match.group(0).upper()
    lines = [clean_display_text(line) for line in (text or "").splitlines() if clean_display_text(line)]
    if lines:
        candidate = lines[0]
        compact = candidate.replace(" ", "")
        has_many_digits = sum(char.isdigit() for char in compact) >= 4
        looks_like_path = any(marker in candidate.lower() for marker in ("http://", "https://", ".png", ".jpg", ".jpeg", "/"))
        if (
            candidate
            and not CARD_CODE_RE.fullmatch(candidate)
            and len(candidate) <= 80
            and not has_many_digits
            and not looks_like_path
        ):
            payload["card_name"] = candidate
    return payload


def _extract_from_metadata(*, image_path: Path, source_reference: str, source_url: str, variant_key: str) -> dict[str, Any]:
    text = " ".join(
        [
            image_path.name,
            image_path.stem,
            str(source_reference or ""),
            str(source_url or ""),
            str(variant_key or ""),
        ]
    )
    return _extract_text_fields(text)


def _run_local_tesseract(image_path: Path) -> tuple[str, str]:
    tesseract_cmd = shutil.which("tesseract")
    if not tesseract_cmd:
        return "", "local-parser"
    try:
        completed = subprocess.run(
            [tesseract_cmd, str(image_path), "stdout", "--psm", "6"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return "", "local-parser"
    if completed.returncode != 0:
        return "", "local-parser"
    return clean_display_text(completed.stdout), "local-ocr"


def _resolve_lookup_card_code(
    *,
    image_path: Path,
    expected: dict[str, Any],
    variant_key: str,
    source_reference: str,
    source_url: str,
) -> str:
    cc = clean_display_text(str(expected.get("card_code") or "")).strip()
    if cc:
        return _normalize_card_match(cc)
    for fragment in (variant_key, source_reference, source_url, image_path.name, image_path.stem):
        c2, _ = _extract_codes_from_text(str(fragment or ""))
        if c2:
            return c2
    return ""


def check_variant_index(
    *,
    card_code: str,
    db_path: Path,
) -> dict[str, Any] | None:
    """READ-ONLY lookup in miru_learning_dossiers.db; returns full row dict or None."""
    code = clean_display_text(str(card_code or "")).strip().upper()
    if not code:
        return None
    try:
        p = Path(db_path)
        if not p.is_file():
            return None
        uri_path = p.resolve().as_posix()
        uri = f"file:{uri_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                """
                SELECT * FROM miru_variant_index
                WHERE upper(trim(variant_card_code)) = ?
                   OR upper(trim(base_card_code)) = ?
                LIMIT 1
                """,
                (code, code),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}
    except Exception as exc:
        logger.info("Variant index lookup skipped or failed (non-fatal): %s", exc)
        return None


def detect_structural_markers(ocr_text: str) -> dict[str, Any]:
    """Detect SP / parallel / TR structural signals in OCR text."""
    t = ocr_text or ""
    raw_markers_found: list[str] = []
    sp_marker_detected = bool(SP_BRACKET_RE.search(t))
    if sp_marker_detected:
        raw_markers_found.append("[SP]")
    parallel_marker_detected = any(ch in t for ch in STAR_CHARS)
    if parallel_marker_detected:
        raw_markers_found.append("★")
    tr_marker_detected = False
    for m in CARD_CODE_RE.finditer(t):
        start = max(0, m.start() - 30)
        end = min(len(t), m.end() + 30)
        chunk = t[start:end]
        if TR_TOKEN_RE.search(chunk):
            tr_marker_detected = True
            raw_markers_found.append("TR")
            break
    if sp_marker_detected:
        marker_confidence = 0.9
    elif tr_marker_detected:
        marker_confidence = 0.85
    elif parallel_marker_detected:
        marker_confidence = 0.7
    else:
        marker_confidence = 0.0
    return {
        "sp_marker_detected": sp_marker_detected,
        "parallel_marker_detected": parallel_marker_detected,
        "tr_marker_detected": tr_marker_detected,
        "marker_confidence": float(marker_confidence),
        "raw_markers_found": raw_markers_found,
    }


def _prepare_vision_jpeg_max512(image_path: Path) -> tuple[bytes | None, str]:
    try:
        from PIL import Image
    except ImportError:
        return None, "pillow_not_installed"
    try:
        with Image.open(image_path) as img:
            img.load()
            if img.mode in ("RGBA", "P", "LA"):
                background = Image.new("RGB", img.size, (255, 255, 255))
                if img.mode == "P":
                    img = img.convert("RGBA")
                background.paste(
                    img,
                    mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None,
                )
                img = background
            else:
                img = img.convert("RGB")
            w, h = img.size
            longest = max(w, h)
            if longest > 512:
                scale = 512.0 / float(longest)
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                img = img.resize((new_w, new_h), resample)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            out = buf.getvalue()
    except OSError as exc:
        return None, f"io_error:{exc}"
    except Exception as exc:
        return None, f"prepare_error:{exc}"
    if not out:
        return None, "empty_jpeg"
    return out, ""


def _vision_json_from_text(text: str) -> dict[str, Any] | None:
    s = (text or "").strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            return None
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(s[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def analyze_with_vision_api(
    *,
    image_path: Path,
    expected_facts: dict[str, Any],
    analyzed_at: str,
) -> dict[str, Any]:
    """Call Anthropic Vision; returns extracted_fields, token_spend, latency_ms, notes, excerpt."""
    _ = analyzed_at  # reserved for future correlation
    _ = expected_facts  # prompt could incorporate hints later; keep signature stable
    key = str(os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return {
            "extracted_fields": {},
            "token_spend": 0,
            "latency_ms": 0.0,
            "vision_notes": ["Vision API call failed: ANTHROPIC_API_KEY not set"],
            "raw_response_excerpt": "",
        }
    if not image_path.is_file():
        return {
            "extracted_fields": {},
            "token_spend": 0,
            "latency_ms": 0.0,
            "vision_notes": ["Vision API call failed: image path is not a file"],
            "raw_response_excerpt": "",
        }
    jpeg_bytes, err = _prepare_vision_jpeg_max512(image_path)
    if jpeg_bytes is None:
        return {
            "extracted_fields": {},
            "token_spend": 0,
            "latency_ms": 0.0,
            "vision_notes": [f"Vision API call failed: {err}"],
            "raw_response_excerpt": "",
        }
    b64 = base64.standard_b64encode(jpeg_bytes).decode("ascii")
    body = {
        "model": VISION_MODEL,
        "max_tokens": 1024,
        "system": VISION_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Analyze this One Piece TCG card image and return the JSON object as specified.",
                    },
                ],
            }
        ],
    }
    payload = json.dumps(body).encode("utf-8")
    req = Request(
        ANTHROPIC_MESSAGES_URL,
        data=payload,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=120) as resp:
            raw_body = resp.read().decode("utf-8")
    except HTTPError as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        try:
            err_txt = exc.read().decode("utf-8")
        except Exception:
            err_txt = str(exc)
        return {
            "extracted_fields": {},
            "token_spend": 0,
            "latency_ms": round(latency_ms, 2),
            "vision_notes": [f"Vision API call failed: HTTP {exc.code} {err_txt[:500]}"],
            "raw_response_excerpt": "",
        }
    except URLError as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "extracted_fields": {},
            "token_spend": 0,
            "latency_ms": round(latency_ms, 2),
            "vision_notes": [f"Vision API call failed: {exc}"],
            "raw_response_excerpt": "",
        }
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return {
            "extracted_fields": {},
            "token_spend": 0,
            "latency_ms": round(latency_ms, 2),
            "vision_notes": [f"Vision API call failed: {exc}"],
            "raw_response_excerpt": "",
        }
    latency_ms = (time.perf_counter() - t0) * 1000.0
    excerpt = raw_body[:300]
    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        return {
            "extracted_fields": {},
            "token_spend": 0,
            "latency_ms": round(latency_ms, 2),
            "vision_notes": ["Vision API call failed: non-JSON response body"],
            "raw_response_excerpt": excerpt,
        }
    usage = parsed.get("usage") if isinstance(parsed, dict) else None
    in_tok = int((usage or {}).get("input_tokens") or 0) if isinstance(usage, dict) else 0
    out_tok = int((usage or {}).get("output_tokens") or 0) if isinstance(usage, dict) else 0
    token_spend = in_tok + out_tok
    parts = parsed.get("content") if isinstance(parsed, dict) else None
    text_chunks: list[str] = []
    if isinstance(parts, list):
        for block in parts:
            if isinstance(block, dict) and block.get("type") == "text":
                text_chunks.append(str(block.get("text") or ""))
    assistant_text = "\n".join(text_chunks).strip()
    data = _vision_json_from_text(assistant_text)
    if not data:
        return {
            "extracted_fields": {},
            "token_spend": token_spend,
            "latency_ms": round(latency_ms, 2),
            "vision_notes": ["Vision API call failed: could not parse model JSON"],
            "raw_response_excerpt": excerpt,
        }
    out_fields: dict[str, Any] = {}
    for k in (
        "card_code",
        "card_name",
        "color",
        "card_type",
        "rarity",
        "cost",
        "power",
        "sp_marker_detected",
        "parallel_marker_detected",
        "tr_marker_detected",
        "confidence",
        "notes",
    ):
        if k not in data:
            continue
        v = data[k]
        if v is None:
            continue
        if k == "notes":
            if isinstance(v, list):
                out_fields["vision_model_notes"] = [str(x) for x in v]
            else:
                out_fields["vision_model_notes"] = [str(v)]
            continue
        if k in ("sp_marker_detected", "parallel_marker_detected", "tr_marker_detected"):
            out_fields[k] = bool(v)
        elif k == "confidence":
            try:
                out_fields["vision_field_confidence"] = float(v)
            except (TypeError, ValueError):
                pass
        else:
            out_fields[k] = clean_display_text(str(v)).strip() if isinstance(v, str) else v
    vision_notes_list: list[str] = []
    if isinstance(data.get("notes"), list):
        vision_notes_list.extend(str(x) for x in data["notes"])
    elif data.get("notes") is not None:
        vision_notes_list.append(str(data["notes"]))
    return {
        "extracted_fields": out_fields,
        "token_spend": token_spend,
        "latency_ms": round(latency_ms, 2),
        "vision_notes": vision_notes_list,
        "raw_response_excerpt": excerpt,
    }


def _merge_vision_identity(extracted: dict[str, Any], vision_fields: dict[str, Any]) -> None:
    for key in ("card_code", "card_name", "rarity"):
        v = vision_fields.get(key)
        if v is None or v == "":
            continue
        extracted[key] = v


def _merge_vision_structural_markers(sm: dict[str, Any], vision_fields: dict[str, Any]) -> None:
    raw = list(sm.get("raw_markers_found") or [])
    if bool(vision_fields.get("sp_marker_detected")):
        sm["sp_marker_detected"] = True
        if "vision:sp_marker" not in raw:
            raw.append("vision:sp_marker")
    if bool(vision_fields.get("parallel_marker_detected")):
        sm["parallel_marker_detected"] = True
        if "vision:parallel_marker" not in raw:
            raw.append("vision:parallel_marker")
    if bool(vision_fields.get("tr_marker_detected")):
        sm["tr_marker_detected"] = True
        if "vision:tr_marker" not in raw:
            raw.append("vision:tr_marker")
    sm["raw_markers_found"] = raw
    if sm.get("sp_marker_detected"):
        sm["marker_confidence"] = 0.9
    elif sm.get("tr_marker_detected"):
        sm["marker_confidence"] = max(float(sm.get("marker_confidence") or 0.0), 0.85)
    elif sm.get("parallel_marker_detected"):
        sm["marker_confidence"] = max(float(sm.get("marker_confidence") or 0.0), 0.7)
    else:
        sm["marker_confidence"] = float(sm.get("marker_confidence") or 0.0)


def _apply_governance_flags(
    *,
    extracted: dict[str, Any],
    notes: list[str],
    tier_used: int,
    structural_markers: dict[str, Any],
) -> None:
    if tier_used >= 2:
        extracted["governance_flag"] = "REVIEW_REQUIRED"
        notes.append(
            "Vision API was used — result requires operator approval before any write to verified records.",
        )
    if (
        structural_markers.get("sp_marker_detected")
        or structural_markers.get("tr_marker_detected")
        or structural_markers.get("parallel_marker_detected")
    ):
        extracted["governance_flag"] = "REVIEW_REQUIRED"
        notes.append("Structural variant marker detected — operator classification required.")


def build_perception_log_entry(result: VisualAnalysisResult, card_code: str) -> dict[str, Any]:
    sm = (result.extracted_fields or {}).get("structural_markers")
    if not isinstance(sm, dict):
        sm = {}
    gf = (result.extracted_fields or {}).get("governance_flag")
    if gf is not None:
        gf_val: str | None = str(gf)
    else:
        gf_val = None
    return {
        "card_code": str(card_code or ""),
        "tier_used": result.tier_used,
        "token_spend": result.token_spend,
        "latency_ms": result.latency_ms,
        "cache_hit": result.cache_hit,
        "verification_status": result.verification_status,
        "confidence": result.confidence,
        "governance_flag": gf_val,
        "sp_marker_detected": sm.get("sp_marker_detected"),
        "parallel_marker_detected": sm.get("parallel_marker_detected"),
        "tr_marker_detected": sm.get("tr_marker_detected"),
        "conflict_flags": json.dumps(result.conflict_flags),
        "analysis_notes": json.dumps(result.analysis_notes),
        "analyzed_at": result.analyzed_at,
    }


def analyze_card_image(
    *,
    image_path: str | Path,
    analyzed_at: str,
    image_hash: str,
    source_reference: str = "",
    source_url: str = "",
    variant_key: str = "",
    expected_facts: dict[str, Any] | None = None,
    source_rollup: dict[str, Any] | None = None,
    learning_dossiers_db_path: Path | None = None,
    image_source_context: str | None = None,
) -> VisualAnalysisResult:
    expected = dict(expected_facts or {})
    rollup = dict(source_rollup or {})
    path = Path(image_path)
    notes: list[str] = []
    conflicts: list[str] = []
    token_spend = 0
    tier_used = 1
    latency_ms_total = 0.0

    lookup_code = _resolve_lookup_card_code(
        image_path=path,
        expected=expected,
        variant_key=variant_key,
        source_reference=source_reference,
        source_url=source_url,
    )
    if learning_dossiers_db_path is not None and lookup_code:
        row = check_variant_index(card_code=lookup_code, db_path=learning_dossiers_db_path)
        if row:
            ef = dict(row)
            ef["image_hash"] = image_hash
            nlist = [
                "Variant index hit — classification loaded from operator-verified record. Zero tokens spent.",
            ]
            return VisualAnalysisResult(
                extraction_method="variant_index_cache",
                extracted_fields=ef,
                confidence=0.99,
                verification_status="verified_operator_classification",
                source_rollup={
                    "source_count": int(rollup.get("source_count") or 0),
                    "source_names": list(rollup.get("display_names") or []),
                    "confidence_level": str(rollup.get("confidence_level") or ""),
                },
                conflict_flags=[],
                analysis_notes=nlist,
                analyzed_at=analyzed_at,
                cache_hit=True,
                ocr_text_excerpt="",
                token_spend=0,
                tier_used=0,
                latency_ms=0.0,
            )

    extracted = _extract_from_metadata(
        image_path=path,
        source_reference=source_reference,
        source_url=source_url,
        variant_key=variant_key,
    )
    method = "local-parser"
    confidence = 0.3 if extracted else 0.15
    notes.append("Used local filename and source-reference parsing before any heavier analysis.")

    ocr_text, ocr_method = _run_local_tesseract(path)
    if ocr_text:
        ocr_fields = _extract_text_fields(ocr_text)
        for key, value in ocr_fields.items():
            extracted.setdefault(key, value)
        method = ocr_method
        confidence = max(confidence, 0.45 if len(ocr_fields) >= 2 else 0.35)
        notes.append("Local OCR contributed additional image text candidates.")
    elif ocr_method == "local-parser":
        notes.append("No local OCR dependency was available, so Miru stayed on deterministic parsing only.")

    structural_markers = detect_structural_markers(ocr_text)
    extracted["structural_markers"] = structural_markers

    # TODO: Tier 3 pipeline (e.g. secondary model or cross-check) not built yet.
    needs_vision = (confidence < 0.6) or (
        float(structural_markers.get("marker_confidence") or 0.0) < 0.7 and confidence < 0.75
    )
    vision_raw_excerpt = ""
    if needs_vision:
        vout = analyze_with_vision_api(
            image_path=path,
            expected_facts=expected,
            analyzed_at=analyzed_at,
        )
        token_spend += int(vout.get("token_spend") or 0)
        latency_ms_total += float(vout.get("latency_ms") or 0.0)
        tier_used = 2
        vf = dict(vout.get("extracted_fields") or {})
        _merge_vision_identity(extracted, vf)
        for k, v in vf.items():
            if k in ("card_code", "card_name", "rarity"):
                continue
            if v is None or v == "":
                continue
            extracted.setdefault(k, v)
        vn = vout.get("vision_notes")
        if isinstance(vn, list) and vn:
            notes.extend(str(x) for x in vn if x)
        vision_raw_excerpt = str(vout.get("raw_response_excerpt") or "")
        if vf:
            notes.append("Claude Vision tier merged identity fields where local confidence was insufficient.")
        combined_text = "\n".join(
            x
            for x in (
                ocr_text,
                json.dumps({k: vf.get(k) for k in ("card_code", "card_name", "rarity", "color") if vf.get(k)}),
            )
            if x
        )
        structural_markers = detect_structural_markers(combined_text)
        _merge_vision_structural_markers(structural_markers, vf)
        extracted["structural_markers"] = structural_markers
        vfc = vf.get("vision_field_confidence")
        if isinstance(vfc, (int, float)) and vfc > 0:
            confidence = max(confidence, min(0.95, float(vfc)))

    extracted["image_hash"] = image_hash
    if vision_raw_excerpt:
        extracted["vision_raw_response_excerpt"] = vision_raw_excerpt

    for field_name in ("card_code", "set_code", "card_name", "cost", "power", "color", "card_type", "rarity"):
        expected_value = clean_display_text(str(expected.get(field_name) or ""))
        extracted_value = clean_display_text(str(extracted.get(field_name) or ""))
        if not expected_value or not extracted_value:
            continue
        if field_name in {"card_code", "set_code", "rarity"}:
            matches = expected_value.upper() == extracted_value.upper()
        else:
            matches = expected_value.casefold() == extracted_value.casefold()
        if not matches:
            if image_source_context == "miru_image_training" and field_name == "card_code":
                continue
            conflicts.append(field_name)

    distinct_sources = int(rollup.get("source_count") or 0)
    if conflicts:
        verification_status = "conflict"
        confidence = min(confidence, 0.4)
        notes.append("Image-derived candidates conflict with current trusted facts, so confidence remains conservative.")
    elif extracted:
        if distinct_sources >= 2:
            verification_status = "verified_with_image_confirmation"
            confidence = max(confidence, 0.78 if distinct_sources == 2 else 0.9)
        elif distinct_sources >= 1:
            verification_status = "source_backed_image_confirmation"
            confidence = max(confidence, 0.62)
        else:
            verification_status = "image_signal_only"
            confidence = min(confidence, 0.45)
        notes.append("Image analysis was used as supporting evidence only, not as a standalone source of truth.")
    else:
        verification_status = "no_visual_signal"
        notes.append("No reliable card facts could be extracted from the current image analysis path.")

    excerpt = clean_display_text(ocr_text)[:240] if ocr_text else ""

    _apply_governance_flags(
        extracted=extracted,
        notes=notes,
        tier_used=tier_used,
        structural_markers=structural_markers,
    )

    return VisualAnalysisResult(
        extraction_method=method,
        extracted_fields=extracted,
        confidence=round(confidence, 2),
        verification_status=verification_status,
        source_rollup={
            "source_count": distinct_sources,
            "source_names": list(rollup.get("display_names") or []),
            "confidence_level": str(rollup.get("confidence_level") or ""),
        },
        conflict_flags=sorted(set(conflicts)),
        analysis_notes=notes,
        analyzed_at=analyzed_at,
        cache_hit=False,
        ocr_text_excerpt=excerpt,
        token_spend=token_spend,
        tier_used=tier_used,
        latency_ms=round(latency_ms_total, 2),
    )
