"""
Miru Vision audit: variant subfolder classification under D:\\Miru_Assets.

Read-only on images; writes report JSON + summary only (no card_catalog.db).
Uses Anthropic Vision (claude-sonnet-4-20250514), one image per request.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Run as script from repo: ensure `tools` package imports resolve
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.miru_ai_onepiece import normalize_card_code  # noqa: E402

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
VISION_MODEL = "claude-sonnet-4-20250514"
DEFAULT_ASSETS_ROOT = Path(r"D:\Miru_Assets")
REPORT_JSON = Path(r"D:\Miru_Assets\vision_audit_report.json")
SUMMARY_TXT = Path(r"D:\Miru_Assets\vision_audit_summary.txt")

TOKEN_GUARD = 40_000
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
MAX_BYTES = 5 * 1024 * 1024
MAX_EDGE = 512

# Approx Sonnet 4 tier (USD per million tokens) — label "approximate" in summary
USD_PER_M_INPUT = 3.0
USD_PER_M_OUTPUT = 15.0

_AUDIT_PROMPT = """You are auditing One Piece TCG card scan files for print treatment vs on-disk folder.

The file path context (folder treatment) will be given as text after this image.
Study the full card frame: rarity line, star above rarity, alt-art style, SP-style frame,
parallel sparkle treatment, Treasure Rare (TR) styling, DON alt-art, or standard base print.

Return ONLY a single JSON object (no markdown, no preamble) with exactly these keys:
{
  "file_path": "<copy the exact full path string provided below>",
  "canonical_code": "<card id from filename if visible on card or infer from filename stem, else empty string>",
  "current_folder_treatment": "<one of: sp|tr|parallel|alt_art|base — use the value given below>",
  "vision_detected_treatment": "<one of: base_print|parallel|sp|alt_art|treasure_rare|don_alt_art|unknown>",
  "star_marker_detected": <true|false>,
  "star_marker_ambiguous": <true|false>,
  "treatment_matches_folder": <true|false>,
  "flag_for_review": <true|false>,
  "flag_reason": "<short string or null>",
  "confidence": <number 0.0-1.0>
}

Rules for vision_detected_treatment:
- base_print: standard English print, no parallel/SP/TR/DON-alt visual treatment.
- parallel: parallel / textured / alternate illustration variant consistent with parallel printing.
- sp: SP-style treatment (often [SP] or special SP presentation).
- alt_art: alternate art that is not clearly parallel sparkle and not DON.
- treasure_rare: TR / treasure rare presentation.
- don_alt_art: DON!! card alternate-art style.
- unknown: cannot decide.

Set treatment_matches_folder true only if the image treatment clearly matches the folder role:
- Folder sp -> expect sp
- Folder tr -> expect treasure_rare
- Folder alt_art -> expect alt_art OR don_alt_art
- Folder parallel -> expect parallel
- Folder base -> expect base_print

Set your initial flag_for_review using the same rules you will apply; we may reconcile in tooling.

After the image, you will receive:
FILE_PATH: ...
CURRENT_FOLDER_TREATMENT: ...
FILENAME_STEM: ...
"""


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    # strip accidental fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


def _folder_treatment_from_path(rel_parts: tuple[str, ...]) -> str | None:
    """Second path segment under set folder defines treatment folder."""
    if len(rel_parts) < 2:
        return None
    key = rel_parts[1].lower().replace("\\", "/")
    mapping = {
        "sp": "sp",
        "tr": "tr",
        "alt_art": "alt_art",
        "parallel": "parallel",
        "base": "base",
    }
    return mapping.get(key)


def _iter_audit_files(root: Path) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    try:
        set_dirs = [p for p in root.iterdir() if p.is_dir()]
    except OSError:
        return out
    treat_names = ("sp", "tr", "alt_art", "parallel", "base")
    for sdir in sorted(set_dirs, key=lambda p: p.name.upper()):
        for tname in treat_names:
            tpath = sdir / tname
            if not tpath.is_dir():
                continue
            try:
                for f in tpath.rglob("*"):
                    if not f.is_file():
                        continue
                    if f.suffix.lower() not in IMAGE_EXTS:
                        continue
                    out.append(f.resolve())
            except OSError:
                continue
    # de-dupe, stable order
    seen: set[str] = set()
    unique: list[Path] = []
    for p in sorted(out, key=lambda x: str(x).lower()):
        k = str(p)
        if k not in seen:
            seen.add(k)
            unique.append(p)
    return unique


def _canonical_from_filename(path: Path) -> str:
    stem = path.stem
    meta = normalize_card_code(stem.replace("_", "-"))
    code = str(meta.get("canonical_code") or "").strip().upper()
    if code:
        return code
    # fallback: OP01-001 style
    m = re.match(
        r"^((?:OP|EB|ST|PRB)\d{1,2}|P)-(\d{3}[A-Z]?)", stem.upper().replace("_", "-")
    )
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return ""


def _prepare_png_bytes(path: Path) -> tuple[bytes | None, str]:
    try:
        from PIL import Image
    except ImportError:
        return None, "missing_pillow"

    try:
        with Image.open(path) as im:
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            w, h = im.size
            longest = max(w, h)
            if longest > MAX_EDGE:
                scale = MAX_EDGE / float(longest)
                new_w = max(1, int(round(w * scale)))
                new_h = max(1, int(round(h * scale)))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                im = im.resize((new_w, new_h), resample)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            if len(data) > MAX_BYTES:
                # further downscale
                factor = 0.85
                while len(data) > MAX_BYTES and max(im.size) > 64:
                    nw = max(1, int(im.size[0] * factor))
                    nh = max(1, int(im.size[1] * factor))
                    im = im.resize((nw, nh), resample)
                    buf = io.BytesIO()
                    im.save(buf, format="PNG", optimize=True)
                    data = buf.getvalue()
            if not data:
                return None, "empty_png"
            return data, ""
    except OSError as exc:
        return None, f"io_error:{exc}"
    except Exception as exc:
        return None, f"prepare_error:{exc}"


def _expected_vision_values(folder: str) -> frozenset[str]:
    if folder == "sp":
        return frozenset({"sp"})
    if folder == "tr":
        return frozenset({"treasure_rare"})
    if folder == "alt_art":
        return frozenset({"alt_art", "don_alt_art"})
    if folder == "parallel":
        return frozenset({"parallel"})
    if folder == "base":
        return frozenset({"base_print"})
    return frozenset()


def _apply_flag_rules(
    folder: str,
    vision: str,
    treatment_matches: bool,
    star_detected: bool,
    star_ambiguous: bool,
    confidence: float,
) -> tuple[bool, str | None]:
    reasons: list[str] = []
    if vision == "unknown":
        reasons.append("vision_unknown")
    if confidence < 0.75:
        reasons.append("low_confidence")
    if not treatment_matches:
        reasons.append("treatment_mismatch")
    if star_detected and star_ambiguous:
        reasons.append("ambiguous_star_marker")
    if folder == "base" and vision != "base_print":
        reasons.append("base_folder_non_base_print")
    if reasons:
        return True, "; ".join(reasons)
    return False, None


def _call_vision(
    png_bytes: bytes,
    file_path: str,
    folder_treatment: str,
    filename_stem: str,
) -> tuple[dict[str, Any] | None, str, int, int]:
    key = str(os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return None, "missing_api_key", 0, 0

    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    tail = (
        f"\nFILE_PATH: {file_path}\n"
        f"CURRENT_FOLDER_TREATMENT: {folder_treatment}\n"
        f"FILENAME_STEM: {filename_stem}\n"
    )
    body = {
        "model": VISION_MODEL,
        "max_tokens": 700,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": _AUDIT_PROMPT + tail},
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
        return None, err_txt, 0, 0
    except URLError as exc:
        return None, str(exc), 0, 0

    try:
        parsed = json.loads(raw_body)
    except json.JSONDecodeError:
        return None, raw_body[:2000], 0, 0

    usage = parsed.get("usage") or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    parts = parsed.get("content")
    text = ""
    if isinstance(parts, list):
        for block in parts:
            if isinstance(block, dict) and block.get("type") == "text":
                text += str(block.get("text") or "")
    text = text.strip()
    obj = _parse_json_object(text)
    return obj, text, in_tok, out_tok


def run_audit(
    assets_root: Path | None = None,
    token_guard: int | None = None,
) -> int:
    guard = int(
        token_guard
        if token_guard is not None
        else (os.environ.get("MIRU_VISION_AUDIT_TOKEN_GUARD") or TOKEN_GUARD)
    )
    root = Path(assets_root or os.environ.get("MIRU_ASSETS_ROOT") or DEFAULT_ASSETS_ROOT)
    files = _iter_audit_files(root)
    audit_date = _utc_iso()

    flagged: list[dict[str, Any]] = []
    clean: list[dict[str, str]] = []
    token_spend = 0
    cumulative_input_tokens = 0
    cumulative_output_tokens = 0
    cost_guard_triggered = False
    processed = 0

    for path in files:
        if token_spend >= guard:
            cost_guard_triggered = True
            break

        rel = path.relative_to(root)
        folder_tr = _folder_treatment_from_path(tuple(rel.parts))
        if not folder_tr:
            continue

        png_bytes, prep_err = _prepare_png_bytes(path)
        if png_bytes is None:
            flagged.append(
                {
                    "file_path": str(path),
                    "canonical_code": _canonical_from_filename(path),
                    "current_folder_treatment": folder_tr,
                    "vision_detected_treatment": "unknown",
                    "star_marker_detected": False,
                    "star_marker_ambiguous": False,
                    "treatment_matches_folder": False,
                    "flag_for_review": True,
                    "flag_reason": f"image_prepare_failed:{prep_err}",
                    "confidence": 0.0,
                }
            )
            processed += 1
            continue

        fp = str(path)
        stem = path.stem
        parsed, raw_text, in_tok, out_tok = _call_vision(
            png_bytes, fp, folder_tr, stem
        )
        cumulative_input_tokens += in_tok
        cumulative_output_tokens += out_tok
        token_spend += in_tok + out_tok
        processed += 1

        if parsed is None:
            flagged.append(
                {
                    "file_path": fp,
                    "canonical_code": _canonical_from_filename(path),
                    "current_folder_treatment": folder_tr,
                    "vision_detected_treatment": "unknown",
                    "star_marker_detected": False,
                    "star_marker_ambiguous": True,
                    "treatment_matches_folder": False,
                    "flag_for_review": True,
                    "flag_reason": f"api_or_parse_failure:{raw_text[:500]}",
                    "confidence": 0.0,
                }
            )
        else:
            vision = str(parsed.get("vision_detected_treatment") or "unknown").strip()
            conf = float(parsed.get("confidence") or 0.0)
            star_d = bool(parsed.get("star_marker_detected"))
            star_a = bool(parsed.get("star_marker_ambiguous"))
            expected = _expected_vision_values(folder_tr)
            treatment_matches = vision in expected if expected else False

            flag, reason = _apply_flag_rules(
                folder_tr, vision, treatment_matches, star_d, star_a, conf
            )
            row = {
                "file_path": fp,
                "canonical_code": str(parsed.get("canonical_code") or "")
                or _canonical_from_filename(path),
                "current_folder_treatment": folder_tr,
                "vision_detected_treatment": vision,
                "star_marker_detected": star_d,
                "star_marker_ambiguous": star_a,
                "treatment_matches_folder": treatment_matches,
                "flag_for_review": flag,
                "flag_reason": reason,
                "confidence": conf,
            }
            # reconcile flag with model hint
            if parsed.get("flag_for_review") is True and not flag:
                row["flag_for_review"] = True
                row["flag_reason"] = (
                    (row["flag_reason"] or "")
                    + "; model_suggested_review"
                ).strip("; ")

            if row["flag_for_review"]:
                flagged.append(row)
            else:
                clean.append(
                    {
                        "canonical_code": row["canonical_code"],
                        "file_path": row["file_path"],
                    }
                )

        if token_spend >= guard:
            cost_guard_triggered = True
            break

    total_flagged = len(flagged)
    total_clean = len(clean)
    report = {
        "audit_date": audit_date,
        "total_files_scanned": processed,
        "total_flagged": total_flagged,
        "total_clean": total_clean,
        "token_spend": token_spend,
        "cost_guard_triggered": cost_guard_triggered,
        "flagged": flagged,
        "clean": clean,
    }

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    est_usd = (
        cumulative_input_tokens * USD_PER_M_INPUT
        + cumulative_output_tokens * USD_PER_M_OUTPUT
    ) / 1_000_000.0

    from collections import Counter

    reason_ctr: Counter[str] = Counter()
    for item in flagged:
        r = item.get("flag_reason") or "unspecified"
        for part in str(r).split(";"):
            part = part.strip()
            if part:
                reason_ctr[part.split(":")[0]] += 1

    lines = [
        f"Miru Vision Variant Folder Audit",
        f"audit_date={audit_date}",
        f"assets_root={root}",
        f"total_files_discovered_in_scope={len(files)}",
        f"total_files_scanned={processed}",
        f"token_guard_limit={guard}",
        f"input_tokens={cumulative_input_tokens} output_tokens={cumulative_output_tokens}",
        f"total_flagged={total_flagged}",
        f"total_clean={total_clean}",
        f"token_spend={token_spend}",
        f"cost_guard_triggered={cost_guard_triggered}",
        f"estimated_usd_approx={est_usd:.4f} (using ${USD_PER_M_INPUT}/M input, ${USD_PER_M_OUTPUT}/M output — verify current Anthropic pricing)",
    ]
    if cost_guard_triggered:
        lines.append("COST_GUARD_TRIGGERED — no further API calls; partial audit only.")
        lines.append(
            "Re-run with a higher MIRU_VISION_AUDIT_TOKEN_GUARD to continue (or shard by set folder)."
        )
    lines.extend(
        [
            "",
            "Flag reason breakdown (rough key prefix counts):",
        ]
    )
    for k, v in reason_ctr.most_common():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Flagged files:")
    for item in flagged:
        lines.append(
            f"  {item.get('canonical_code','')} | folder={item.get('current_folder_treatment')} "
            f"| vision={item.get('vision_detected_treatment')} | conf={item.get('confidence')} "
            f"| {item.get('flag_reason')} | {item.get('file_path')}"
        )

    SUMMARY_TXT.write_text("\n".join(lines), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(run_audit())
