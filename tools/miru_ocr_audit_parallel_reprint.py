"""
OCR audit: compare Vision-read card code vs filename for parallel/reprint/_unclassified PNGs.
Read-only on Miru_Assets except output txt. No DB. Stops at token guard.

Use --continue to skip paths already logged in ocr_audit_parallel_reprint.txt and append.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(r"D:\Miru_Assets")
OUT = ROOT / "ocr_audit_parallel_reprint.txt"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-20250514"
TOKEN_GUARD = 80_000
MAX_EDGE = 512
MAX_BYTES = 5 * 1024 * 1024

HEADERS_BASE = {
    "User-Agent": "Mozilla/5.0 (compatible; MiruOCRAudit/1.0)",
    "content-type": "application/json",
    "anthropic-version": "2023-06-01",
}

PROMPT = """What is the card code printed on this card? Return only the card code (e.g. OP01-001, EB04-003, P-028) and nothing else. If you cannot read a card code, return UNREADABLE."""

CODE_STEM_RE = re.compile(r"^(.+?)(?:_p\d+|_r\d+)$", re.I)


def normalize_code(s: str) -> str:
    return re.sub(r"\s+", "", (s or "").strip()).upper()


def expected_code_from_file(path: Path) -> str:
    stem = path.stem
    m = CODE_STEM_RE.match(stem)
    if m:
        return normalize_code(m.group(1))
    return normalize_code(stem)


def should_audit(path: Path) -> bool:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False
    parts_lower = [p.lower() for p in rel.parts]
    if "base" in parts_lower:
        return False
    if len(rel.parts) >= 1 and rel.parts[0].upper() in ("PRB01", "PRB02"):
        return False
    if path.suffix.lower() != ".png":
        return False
    return any(
        x in ("parallel", "reprint", "_unclassified")
        for x in rel.parts
    )


def iter_audit_pngs() -> list[Path]:
    out: list[Path] = []
    if not ROOT.is_dir():
        return out
    for p in ROOT.rglob("*.png"):
        if p.is_file() and should_audit(p):
            out.append(p)
    return sorted(out, key=lambda x: str(x).lower())


def load_already_logged_paths(audit_path: Path) -> set[str]:
    """Resolve absolute path strings for lines already in the audit file."""
    done: set[str] = set()
    if not audit_path.is_file():
        return done
    try:
        text = audit_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return done
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("==="):
            continue
        if line.startswith("Total images audited:") or line.startswith("MATCH:"):
            continue
        if line.startswith("MISMATCH list:") or line.startswith("UNREADABLE"):
            continue
        if line.startswith("Tokens used") or line.startswith("Status:"):
            continue
        if line.startswith("  "):  # summary sub-bullets
            continue
        if " | " not in line:
            continue
        parts = line.split(" | ", 4)
        if len(parts) < 2:
            continue
        folder, fname = parts[0].strip(), parts[1].strip()
        if not fname.lower().endswith(".png"):
            continue
        try:
            full = (Path(folder) / fname).resolve()
            done.add(str(full))
        except OSError:
            continue
    return done


def count_audit_data_lines(audit_path: Path) -> int:
    """Count result lines (folder | file.png | ... | MATCH/MISMATCH/UNREADABLE)."""
    if not audit_path.is_file():
        return 0
    n = 0
    for line in audit_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("==="):
            continue
        if " | " not in line or ".png" not in line.lower():
            continue
        parts = line.split(" | ")
        if len(parts) < 5:
            continue
        if parts[-1] in ("MATCH", "MISMATCH", "UNREADABLE"):
            n += 1
    return n


def prepare_png_bytes(path: Path) -> bytes | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
            w, h = im.size
            longest = max(w, h)
            if longest > MAX_EDGE:
                scale = MAX_EDGE / float(longest)
                nw = max(1, int(round(w * scale)))
                nh = max(1, int(round(h * scale)))
                try:
                    resample = Image.Resampling.LANCZOS
                except AttributeError:
                    resample = Image.LANCZOS  # type: ignore[attr-defined]
                im = im.resize((nw, nh), resample)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            while len(data) > MAX_BYTES and max(im.size) > 64:
                im = im.resize(
                    (max(1, int(im.size[0] * 0.85)), max(1, int(im.size[1] * 0.85))),
                    resample,
                )
                buf = io.BytesIO()
                im.save(buf, format="PNG", optimize=True)
                data = buf.getvalue()
            return data if data else None
    except OSError:
        return None


def call_vision(png_bytes: bytes, api_key: str) -> tuple[str, int, int]:
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    body = {
        "model": MODEL,
        "max_tokens": 80,
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
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    }
    payload = json.dumps(body).encode("utf-8")
    req = Request(
        ANTHROPIC_URL,
        data=payload,
        method="POST",
        headers={
            **HEADERS_BASE,
            "x-api-key": api_key,
        },
    )
    with urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    usage = parsed.get("usage") or {}
    in_t = int(usage.get("input_tokens") or 0)
    out_t = int(usage.get("output_tokens") or 0)
    text = ""
    for block in parsed.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text += str(block.get("text") or "")
    return text.strip(), in_t, out_t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--continue",
        action="store_true",
        dest="continue_run",
        help="Skip files already listed in audit log; append results",
    )
    args = ap.parse_args()

    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        print("FAILED: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1

    all_paths = iter_audit_pngs()
    logged: set[str] = set()
    prev_total: int | None = None
    if args.continue_run:
        logged = load_already_logged_paths(OUT)
        prev_total = count_audit_data_lines(OUT)

    paths = [
        p
        for p in all_paths
        if str(p.resolve()) not in logged
    ]

    lines: list[str] = []
    tokens_used = 0
    match_c = mismatch_c = unread_c = 0
    mismatches: list[str] = []
    unreadables: list[str] = []
    audited = 0
    status = "COMPLETE"

    for path in paths:
        if tokens_used >= TOKEN_GUARD:
            status = "STOPPED_AT_COST_GUARD"
            break
        rel_folder = str(path.parent)
        fname = path.name
        expected = expected_code_from_file(path)
        png = prepare_png_bytes(path)
        if png is None:
            line = f"{rel_folder} | {fname} | {expected} | PREPARE_FAILED | UNREADABLE"
            lines.append(line)
            unread_c += 1
            unreadables.append(line)
            audited += 1
            continue

        est_tokens = 2000
        if tokens_used + est_tokens > TOKEN_GUARD:
            status = "STOPPED_AT_COST_GUARD"
            break

        try:
            text, in_t, out_t = call_vision(png, api_key)
        except (HTTPError, URLError, OSError, json.JSONDecodeError) as e:
            line = f"{rel_folder} | {fname} | {expected} | API_ERROR:{e!s} | UNREADABLE"
            lines.append(line)
            unread_c += 1
            unreadables.append(line)
            audited += 1
            continue

        tokens_used += in_t + out_t
        vnorm = normalize_code(text.splitlines()[0] if text else "")
        if not vnorm or vnorm == "UNREADABLE" or "UNREADABLE" in text.upper():
            cat = "UNREADABLE"
            unread_c += 1
            unreadables.append(f"{rel_folder} | {fname} | {expected} | {text!r}")
        elif vnorm == expected:
            cat = "MATCH"
            match_c += 1
        else:
            cat = "MISMATCH"
            mismatch_c += 1
            mismatches.append(f"{rel_folder} | {fname} | expected={expected} | got={vnorm}")

        vision_line = text.replace("\n", " ")[:120]
        lines.append(
            f"{rel_folder} | {fname} | {expected} | {vision_line} | {cat}"
        )
        audited += 1

        if tokens_used >= TOKEN_GUARD:
            status = "STOPPED_AT_COST_GUARD"
            break

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = f"\n=== CONTINUATION RUN {ts} ===\n" if args.continue_run else ""
    summary_lines = [
        "",
        f"=== SUMMARY ({'continuation' if args.continue_run else 'initial'}) ===",
        f"Files audited this run: {audited}",
        f"MATCH (this run): {match_c}",
        f"MISMATCH (this run): {mismatch_c}",
        f"UNREADABLE (this run): {unread_c}",
    ]
    if args.continue_run and prev_total is not None:
        summary_lines.append(
            f"Running total images audited (approx): {prev_total + audited}"
        )
    elif not args.continue_run:
        summary_lines.append(f"Total images audited: {audited}")
    if mismatches:
        summary_lines.append("MISMATCH list (this run):")
        for m in mismatches:
            summary_lines.append(f"  {m}")
    else:
        summary_lines.append("MISMATCH list (this run): (none)")
    if unreadables:
        summary_lines.append("UNREADABLE list (this run):")
        for u in unreadables:
            summary_lines.append(f"  {u}")
    else:
        summary_lines.append("UNREADABLE list (this run): (none)")
    summary_lines.extend(
        [
            f"Tokens used (this run, input+output): {tokens_used}",
            f"Status: {status}",
        ]
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    blob = header + "\n".join(lines + summary_lines) + "\n"
    if args.continue_run and OUT.is_file():
        with OUT.open("a", encoding="utf-8") as f:
            f.write(blob)
    else:
        OUT.write_text(blob.lstrip("\n"), encoding="utf-8")

    out_obj = {
        "audited_this_run": audited,
        "match": match_c,
        "mismatch": mismatch_c,
        "unreadable": unread_c,
        "tokens_this_run": tokens_used,
        "status": status,
        "remaining_skipped_as_already_logged": len(logged) if args.continue_run else 0,
        "candidates_this_run": len(paths),
    }
    print(json.dumps(out_obj, indent=2))
    print(f"{'Appended to' if args.continue_run else 'Wrote'} {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
