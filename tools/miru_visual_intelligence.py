from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from tools.miru_ai_onepiece import clean_display_text, normalize_card_code


CARD_CODE_RE = re.compile(r"\b(?:OP\d{2}-\d{3}|EB\d{2}-\d{3}|P-\d{3}|PRB-\d{2})\b", re.IGNORECASE)
SET_CODE_RE = re.compile(r"\b(?:OP\d{2}|EB\d{2}|PRB-\d{2}|P-\d{3})\b", re.IGNORECASE)
COST_RE = re.compile(r"(?:cost|コスト)\s*[:\-]?\s*(\d{1,2})", re.IGNORECASE)
POWER_RE = re.compile(r"(?:power|パワー)\s*[:\-]?\s*(\d{3,5})", re.IGNORECASE)
COLOR_RE = re.compile(r"\b(red|blue|green|purple|black|yellow)\b", re.IGNORECASE)
TYPE_RE = re.compile(r"\b(leader|character|event|stage|don!!)\b", re.IGNORECASE)
RARITY_RE = re.compile(r"\b(?:SEC|SP|L|SR|R|UC|C|TR)\b", re.IGNORECASE)


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
) -> VisualAnalysisResult:
    expected = dict(expected_facts or {})
    rollup = dict(source_rollup or {})
    path = Path(image_path)
    notes: list[str] = []
    conflicts: list[str] = []

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

    extracted["image_hash"] = image_hash

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
    )
