"""Local card image analysis for SP / parallel markers (Claude Vision).

Reads images only from the operator image root (default D:/OPTCG_Images).
Does not download from the network. Writes to ``image_variant_analysis`` only
when both marker fields are definite yes/no (never ``unclear``).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tools.miru_action_governance import (
    enqueue_image_variant_sp_review_queue,
    image_variant_sp_queue_item_key,
)
from tools.miru_ai_onepiece import normalize_card_code
from tools.miru_project_sync import (
    DEFAULT_PROJECT_DB_PATH,
    connect_catalog_db,
    ensure_catalog_sync_schema,
)

logger = logging.getLogger("miru.image_variant")

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
VISION_MODEL = "claude-sonnet-4-20250514"
DEFAULT_IMAGE_ROOT = Path("D:/OPTCG_Images")
VERIFIED_MAPPINGS_REL = Path("data/verified_variant_mappings.json")

_PROMPT = """You are reading the ID label area of a One Piece Trading Card Game card.
This image shows the bottom-right corner of the card where the card ID is printed.

Look carefully at the card ID text and answer these two questions:

1. Does the card ID contain the prefix [SP] — for example '[SP] OP03-008' or 'OP03-008 [SP]'?
   Answer: yes / no / unclear

2. Is there a ★ symbol printed directly above or next to the rarity symbol?
   Answer: yes / no / unclear

Respond in this exact JSON format only, no other text:
{
  "sp_marker": "yes" or "no" or "unclear",
  "parallel_marker": "yes" or "no" or "unclear"
}
"""


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_canonical_code(card_code: str) -> str:
    meta = normalize_card_code(card_code or "")
    return str(meta.get("canonical_code") or "").strip().upper()


def image_root() -> Path:
    raw = str(os.environ.get("MIRU_OPTCG_IMAGES_ROOT") or "").strip()
    return Path(raw) if raw else DEFAULT_IMAGE_ROOT


def _path_is_file(p: Path) -> bool:
    try:
        return p.is_file()
    except OSError:
        return False


def _first_existing_file(paths: list[Path]) -> Path | None:
    for p in paths:
        if _path_is_file(p):
            return p
    return None


def _candidate_set_folders(set_prefix: str) -> list[str]:
    sp = str(set_prefix or "").strip().upper()
    if sp.startswith("OP"):
        rest = sp[2:]
        if len(rest) == 2 and rest.startswith("0") and rest[1] in "6789":
            return [sp, f"OP{int(rest)}"]
        if len(rest) == 1 and rest in "6789":
            return [f"OP0{rest}", sp]
    return [sp] if sp else []


def _alts_dir_variants() -> tuple[str, ...]:
    return ("Alts", "ALTS", "ALTs", "Alt", "alt")


def _extensions() -> tuple[str, ...]:
    return ("png", "jpg", "webp")


def _resolve_base_image_path(root: Path, code: str, set_prefix: str) -> Path | None:
    paths: list[Path] = []
    for folder in _candidate_set_folders(set_prefix):
        subdir = root / folder
        for ext in _extensions():
            paths.append(subdir / f"{code}.{ext}")
    return _first_existing_file(paths)


def _resolve_sp_image_path(root: Path, code: str, set_prefix: str) -> Path | None:
    paths: list[Path] = []

    excluded_sp_sources = {"OP05", "OP5"}
    for entry in os.scandir(root):
        if not entry.is_dir():
            continue
        name = str(entry.name or "").strip()
        if not name:
            continue
        up = name.upper()
        if up in ("THUMBS", "AA_S"):
            continue
        if up in excluded_sp_sources:
            continue

        set_dir = Path(entry.path)
        for alts in _alts_dir_variants():
            if not os.path.exists(str(set_dir / alts)):
                continue
            for ext in _extensions():
                paths.append(set_dir / alts / "SP" / f"{code}(SP).{ext}")
                paths.append(set_dir / alts / "SP" / f"{code}(SP1).{ext}")
                paths.append(set_dir / alts / "SP" / f"{code}(SP2).{ext}")
                paths.append(set_dir / alts / "SP" / f"{code}alt(SP).{ext}")
                paths.append(set_dir / alts / "SPs" / f"{code}(SP).{ext}")
                paths.append(set_dir / alts / "SPs" / f"{code}SP.{ext}")

        for ext in _extensions():
            paths.append(set_dir / "SP" / f"{code}SPalt.{ext}")

    thumbs = root / "thumbs"
    paths.append(thumbs / f"{code}(SP).webp")
    paths.append(thumbs / f"{code}SP.webp")
    paths.append(thumbs / f"{code}alt(SP).webp")

    return _first_existing_file(paths)


def _resolve_tr_image_path(root: Path, code: str) -> Path | None:
    paths: list[Path] = []
    colors = ("Black", "Blue", "Green", "Purple", "Red", "Yellow", "Leaders")
    base = root / "AA_s"
    for color in colors:
        color_dir = base / color
        for ext in _extensions():
            paths.append(color_dir / f"{code}(TR).{ext}")
            paths.append(color_dir / f"{code}(TR)_edited.{ext}")
    return _first_existing_file(paths)


def _resolve_alt_image_path(root: Path, code: str, set_prefix: str) -> Path | None:
    paths: list[Path] = []

    parenthetical = ("(alt)", "(Alt)", "(alt1)", "(alt2)")
    bare = ("alt", "Alt", "alt2", "alt3")
    for folder in _candidate_set_folders(set_prefix):
        set_dir = root / folder
        for alts in _alts_dir_variants():
            if not os.path.exists(str(set_dir / alts)):
                continue
            for ext in _extensions():
                for suf in parenthetical:
                    paths.append(set_dir / alts / f"{code}{suf}.{ext}")
                for suf in bare:
                    paths.append(set_dir / alts / f"{code}{suf}.{ext}")
    return _first_existing_file(paths)


def resolve_image_path(
    canonical_code: str, variant_type: str | None = None
) -> Path | None:
    """Resolve a local image path for a canonical code.

    Lookup rules match the operator's on-disk OPTCG image store. This function fail-closes
    (returns None) if no readable file is found.
    """
    code = normalize_canonical_code(canonical_code)
    if not code or "-" not in code:
        return None
    set_prefix, sep, _remainder = code.partition("-")
    if not set_prefix or not sep:
        return None

    root = image_root()
    vt = str(variant_type or "").strip().lower() or None

    if vt == "sp":
        return _resolve_sp_image_path(
            root, code, set_prefix
        ) or _resolve_base_image_path(root, code, set_prefix)
    if vt == "tr":
        return _resolve_tr_image_path(root, code) or _resolve_base_image_path(
            root, code, set_prefix
        )
    if vt == "alt":
        return _resolve_alt_image_path(
            root, code, set_prefix
        ) or _resolve_base_image_path(root, code, set_prefix)
    if vt in (None, "base"):
        return _resolve_base_image_path(root, code, set_prefix)

    return None


def resolve_optcg_image_path(
    canonical_code: str, variant_type: str | None = None
) -> Path | None:
    """Backward-compatible name for :func:`resolve_image_path`."""
    if variant_type is None:
        return None
    return resolve_image_path(canonical_code, variant_type=variant_type)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return None
                return obj if isinstance(obj, dict) else None
    return None


def _tri_state_to_int(word: Any) -> int | None:
    s = str(word or "").strip().lower()
    if s == "yes":
        return 1
    if s == "no":
        return 0
    return None


_VISION_MAX_LONGEST_SIDE = 1200
_VISION_JPEG_QUALITY = 85
# Bottom-right ID label crop on the resized image: 25% width × 15% height.
_ID_CROP_WIDTH_FRAC = 0.50
_ID_CROP_HEIGHT_FRAC = 0.15


def _prepare_vision_jpeg_bytes(image_path: Path) -> tuple[bytes | None, str]:
    """Resize (max longest side 1200px), crop bottom-right ID region, JPEG q=85. In-memory only."""
    try:
        from PIL import Image
    except ImportError:
        logger.error(
            "API_ERROR: Pillow (PIL) is not installed; cannot prepare image for Vision API."
        )
        return None, "pillow_import_error"

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
            if longest > _VISION_MAX_LONGEST_SIDE:
                scale = _VISION_MAX_LONGEST_SIDE / float(longest)
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                img = img.resize((new_w, new_h), resample)

            w, h = img.size
            crop_w = max(1, int(round(w * _ID_CROP_WIDTH_FRAC)))
            crop_h = max(1, int(round(h * _ID_CROP_HEIGHT_FRAC)))
            left = max(0, w - crop_w)
            top = max(0, h - crop_h)
            try:
                img = img.crop((left, top, w, h))
            except Exception as exc:
                logger.error("Vision ID-label crop failed for %s: %s", image_path, exc)
                return None, f"crop_error:{exc}"
            if img.size[0] < 1 or img.size[1] < 1:
                logger.error(
                    "Vision ID-label crop produced empty region for %s", image_path
                )
                return None, "crop_error:empty_region"

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_VISION_JPEG_QUALITY, optimize=True)
            out = buf.getvalue()
    except OSError as exc:
        logger.error("Vision image prepare failed (IO) for %s: %s", image_path, exc)
        return None, f"resize_error:{exc}"
    except Exception as exc:
        logger.error("Vision image prepare failed for %s: %s", image_path, exc)
        return None, f"resize_error:{exc}"

    if not out:
        logger.error("Vision image prepare produced empty bytes for %s", image_path)
        return None, "resize_error:empty_output"
    return out, ""


def call_claude_vision(image_path: Path) -> tuple[dict[str, Any] | None, str]:
    """Returns (full API response dict, raw response body string). On failure, (None, error note)."""
    key = str(os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        logger.error("ANTHROPIC_API_KEY is not set; Vision call skipped.")
        return None, "missing_api_key"

    if not image_path.is_file():
        logger.error("Could not read image %s: not a file", image_path)
        return None, "read_error:not_a_file"

    data_bytes, prep_err = _prepare_vision_jpeg_bytes(image_path)
    if data_bytes is None:
        return None, prep_err

    b64 = base64.standard_b64encode(data_bytes).decode("ascii")
    media_type = "image/jpeg"
    body = {
        "model": VISION_MODEL,
        "max_tokens": 512,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
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
    try:
        with urlopen(req, timeout=120) as resp:
            raw_body = resp.read().decode("utf-8")
    except HTTPError as exc:
        try:
            err_txt = exc.read().decode("utf-8")
        except Exception:
            err_txt = str(exc)
        logger.error("Anthropic HTTP error %s: %s", exc.code, err_txt[:2000])
        return None, err_txt
    except URLError as exc:
        logger.error("Anthropic network error: %s", exc)
        return None, str(exc)

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.error("Anthropic returned non-JSON body.")
        return None, raw_body

    return parsed, raw_body


def _vision_text_block(api_response: dict[str, Any]) -> str:
    parts = api_response.get("content")
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for block in parts:
        if isinstance(block, dict) and block.get("type") == "text":
            chunks.append(str(block.get("text") or ""))
    return "\n".join(chunks).strip()


def load_verified_variant_codes(project_root: Path) -> set[str]:
    path = project_root / VERIFIED_MAPPINGS_REL
    out: set[str] = set()
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    mappings = data.get("mappings")
    if not isinstance(mappings, list):
        return out
    for m in mappings:
        if not isinstance(m, dict):
            continue
        vc = str(m.get("variant_canonical_code") or "").strip().upper()
        if vc:
            out.add(vc)
    return out


def analyze_one_card(
    canonical_code: str,
    *,
    project_db_path: Path | str = DEFAULT_PROJECT_DB_PATH,
    conn: sqlite3.Connection | None = None,
    force: bool = False,
    variant_type: str = "base",
) -> dict[str, Any]:
    """
    Run vision classification for one canonical code.

    ``force`` (operator/testing): remove any existing ``image_variant_analysis`` row and
    matching ``image_variant_sp`` queue row for this code before running; does not relax
    vision fail-closed rules or review semantics.

    Returns a result dict with keys: status, canonical_code, detail, and optional counters.
    status is one of: written, IMAGE_UNAVAILABLE, INCONCLUSIVE, API_ERROR, SKIPPED_VERIFIED, SKIPPED_EXISTS.
    """
    code = normalize_canonical_code(canonical_code)
    project_path = Path(project_db_path)
    ensure_catalog_sync_schema(project_path)

    img_path = resolve_image_path(code, variant_type=variant_type)
    if img_path is None:
        logger.info("IMAGE_UNAVAILABLE canonical_code=%s root=%s", code, image_root())
        return {
            "status": "IMAGE_UNAVAILABLE",
            "canonical_code": code,
            "detail": "no_local_image",
        }
    logger.info("RESOLVED_IMAGE canonical_code=%s path=%s", code, img_path)
    try:
        img_path.read_bytes()
    except OSError as exc:
        logger.info(
            "IMAGE_UNAVAILABLE canonical_code=%s reason=cannot_read path=%s err=%s",
            code,
            img_path,
            exc,
        )
        return {
            "status": "IMAGE_UNAVAILABLE",
            "canonical_code": code,
            "detail": "cannot_read_image_file",
        }

    own_conn = conn is None
    if own_conn:
        conn = connect_catalog_db(project_path)
    assert conn is not None
    try:
        if own_conn:
            conn.execute("BEGIN")
        if force:
            conn.execute(
                "DELETE FROM miru_review_queue WHERE item_key = ?",
                (image_variant_sp_queue_item_key(code),),
            )
            conn.execute(
                "DELETE FROM image_variant_analysis WHERE canonical_code = ?", (code,)
            )
        row = conn.execute(
            "SELECT 1 FROM image_variant_analysis WHERE canonical_code = ? LIMIT 1",
            (code,),
        ).fetchone()
        if row is not None:
            logger.info("SKIPPED_EXISTS canonical_code=%s", code)
            if own_conn:
                conn.rollback()
            return {
                "status": "SKIPPED_EXISTS",
                "canonical_code": code,
                "detail": "already_analyzed",
            }

        api_json, raw_body = call_claude_vision(img_path)
        if api_json is None:
            logger.error("API_ERROR canonical_code=%s detail=%s", code, raw_body[:500])
            if own_conn:
                conn.rollback()
            return {
                "status": "API_ERROR",
                "canonical_code": code,
                "detail": str(raw_body)[:2000],
            }

        assistant_text = _vision_text_block(api_json)
        fields = _extract_json_object(assistant_text)
        if not fields:
            logger.info("INCONCLUSIVE canonical_code=%s reason=bad_json", code)
            if own_conn:
                conn.rollback()
            return {
                "status": "INCONCLUSIVE",
                "canonical_code": code,
                "detail": "unparseable_model_json",
            }

        sp_i = _tri_state_to_int(fields.get("sp_marker"))
        par_i = _tri_state_to_int(fields.get("parallel_marker"))
        if sp_i is None or par_i is None:
            logger.info(
                "INCONCLUSIVE canonical_code=%s sp=%s parallel=%s",
                code,
                fields.get("sp_marker"),
                fields.get("parallel_marker"),
            )
            if own_conn:
                conn.rollback()
            return {
                "status": "INCONCLUSIVE",
                "canonical_code": code,
                "detail": "unclear_marker_field",
            }

        raw_stored = json.dumps(api_json, ensure_ascii=False)
        ts = _utc_ts()
        conn.execute(
            """
            INSERT INTO image_variant_analysis (
                canonical_code,
                image_path,
                sp_marker_detected,
                parallel_marker_detected,
                analysis_confidence,
                raw_vision_response,
                analysis_timestamp,
                review_status,
                operator_decision
            ) VALUES (?, ?, ?, ?, 'high', ?, ?, 'REVIEW_REQUIRED', NULL)
            """,
            (
                code,
                str(img_path.resolve()),
                sp_i,
                par_i,
                raw_stored,
                ts,
            ),
        )
        if sp_i == 1:
            enqueue_image_variant_sp_review_queue(
                conn,
                canonical_code=code,
                summary_text=f"Image analysis reports [SP] on card ID label for {code}.",
                payload={
                    "canonical_code": code,
                    "image_path": str(img_path.resolve()),
                    "sp_marker_detected": sp_i,
                    "parallel_marker_detected": par_i,
                    "analysis_timestamp": ts,
                },
            )
        if own_conn:
            conn.commit()
        logger.info(
            "written canonical_code=%s sp=%s parallel=%s queued_sp_review=%s",
            code,
            sp_i,
            par_i,
            bool(sp_i == 1),
        )
        return {
            "status": "written",
            "canonical_code": code,
            "sp_marker_detected": sp_i,
            "parallel_marker_detected": par_i,
            "flagged_for_review": bool(sp_i == 1),
        }
    except Exception:
        if own_conn:
            conn.rollback()
        raise
    finally:
        if own_conn:
            conn.close()
