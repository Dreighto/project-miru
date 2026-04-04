from __future__ import annotations

import re
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_VALID_IMAGE_BYTES = 50 * 1024
DEFAULT_REQUEST_DELAY_SECONDS = 1.0
PLACEHOLDER_URL_PATTERNS = (
    re.compile(r"now[-_\s]?design", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
)


def _utc_timestamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _emit(
    line: str,
    fetch_log_path: Path,
    log_callback: Callable[[str], None] | None,
) -> None:
    stamped = f"[{_utc_timestamp()}] {line}"
    try:
        fetch_log_path.parent.mkdir(parents=True, exist_ok=True)
        with fetch_log_path.open("a", encoding="utf-8") as handle:
            handle.write(stamped + "\n")
    except Exception:
        pass
    if log_callback is not None:
        try:
            log_callback(stamped)
        except Exception:
            pass


def _extract_set_code(canonical_code: str) -> str:
    code = str(canonical_code or "").strip().upper()
    if not code:
        return "UNKNOWN"
    if code.startswith("P-"):
        return "P"
    match = re.match(r"^([A-Z]+\d{2})-", code)
    if match:
        return match.group(1)
    return "UNKNOWN"


def _parallel_index(*values: str) -> int:
    for value in values:
        text = str(value or "")
        match = re.search(r"parallel\D*(\d+)", text, flags=re.IGNORECASE)
        if match:
            try:
                idx = int(match.group(1))
                if idx > 0:
                    return idx
            except ValueError:
                continue
        match = re.search(r"\bp\D*(\d+)\b", text, flags=re.IGNORECASE)
        if match:
            try:
                idx = int(match.group(1))
                if idx > 0:
                    return idx
            except ValueError:
                continue
    return 1


def _is_parallel_variant(variant_key: str, variant_label: str, print_id: str) -> bool:
    blob = " ".join((variant_key or "", variant_label or "", print_id or "")).lower()
    return "parallel" in blob


def _build_variant_relative_path(row: sqlite3.Row) -> str:
    canonical_code = str(row["canonical_code"] or "").strip().upper()
    set_code = _extract_set_code(canonical_code)
    code = canonical_code or str(row["print_id"] or "").strip().upper()

    is_promo = int(row["is_promo"] or 0) == 1
    is_gmr = int(row["is_golden_manga_rare"] or 0) == 1
    is_mr = int(row["is_manga_rare"] or 0) == 1
    is_ir = int(row["is_illustration_rare"] or 0) == 1
    is_sp = int(row["is_sp"] or 0) == 1
    is_tr = int(row["is_tr"] or 0) == 1
    is_alt = int(row["is_alt"] or 0) == 1
    is_base = int(row["is_base"] or 0) == 1

    variant_key = str(row["variant_key"] or "")
    variant_label = str(row["variant_label"] or "")
    print_id = str(row["print_id"] or "")

    if is_promo:
        return f"P/base/{code}.png"
    if _is_parallel_variant(variant_key, variant_label, print_id):
        index = _parallel_index(variant_key, variant_label, print_id)
        return f"{set_code}/parallel/{code}_p{index}.png"
    if is_gmr:
        return f"{set_code}/alt_art/{code}_gmr.png"
    if is_mr:
        return f"{set_code}/alt_art/{code}_mr.png"
    if is_ir:
        return f"{set_code}/alt_art/{code}_ir.png"
    if is_sp:
        return f"{set_code}/sp/{code}_sp.png"
    if is_tr:
        return f"{set_code}/tr/{code}_tr.png"
    if is_alt:
        return f"{set_code}/alt_art/{code}_alt.png"
    if is_base:
        return f"{set_code}/base/{code}.png"
    return f"{set_code}/base/{code}.png"


def _build_base_relative_path(canonical_code: str) -> str:
    code = str(canonical_code or "").strip().upper()
    set_code = _extract_set_code(code)
    if code.startswith("P-"):
        return f"P/base/{code}.png"
    return f"{set_code}/base/{code}.png"


def _url_looks_like_placeholder(image_url: str) -> bool:
    value = str(image_url or "")
    return any(pattern.search(value) for pattern in PLACEHOLDER_URL_PATTERNS)


def _validate_png(path: Path) -> tuple[bool, str, int]:
    size_bytes = path.stat().st_size if path.exists() else 0
    if size_bytes <= 0:
        return False, "empty_file", size_bytes
    with path.open("rb") as handle:
        header = handle.read(8)
    if header != PNG_MAGIC:
        return False, "invalid_png_header", size_bytes
    if size_bytes <= MIN_VALID_IMAGE_BYTES:
        return False, "placeholder_detected", size_bytes
    return True, "ok", size_bytes


def _curl_download(url: str, output_path: Path) -> tuple[int | None, str]:
    command = [
        "curl.exe",
        "-L",
        "--silent",
        "--show-error",
        "--output",
        str(output_path),
        "--write-out",
        "%{http_code}",
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    raw_code = str(result.stdout or "").strip()[-3:]
    status_code: int | None = None
    if raw_code.isdigit():
        status_code = int(raw_code)
    error = str(result.stderr or "").strip()
    if result.returncode != 0 and not error:
        error = f"curl_exit_{result.returncode}"
    return status_code, error


def _fetch_one_image(
    image_url: str,
    destination: Path,
    log_callback: Callable[[str], None] | None,
) -> tuple[bool, str, int]:
    if _url_looks_like_placeholder(image_url):
        return False, "placeholder_detected", 0

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="miru_fetch_", suffix=".png", delete=False,
        dir=destination.parent
    ) as tmp:
        tmp_path = Path(tmp.name)

    status_code: int | None = None
    try:
        status_code, curl_error = _curl_download(image_url, tmp_path)
        if status_code != 200:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            reason = f"http_{status_code}" if status_code is not None else "http_unknown"
            if curl_error:
                reason = f"{reason}:{curl_error}"
            return False, reason, 0

        valid, reason, size_bytes = _validate_png(tmp_path)
        if not valid:
            tmp_path.unlink(missing_ok=True)
            return False, reason, size_bytes

        tmp_path.replace(destination)
        return True, "ok", size_bytes
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        time.sleep(DEFAULT_REQUEST_DELAY_SECONDS)


def fetch_missing_variant_images(
    db_path: str | Path,
    assets_dir: str | Path,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    db_file = Path(db_path)
    assets_root = Path(assets_dir)
    fetch_log_path = assets_root / "fetch_log.txt"

    fetched = 0
    skipped = 0
    failed: list[str] = []

    query = """
        SELECT
            cv.id,
            cv.card_id,
            cv.variant_key,
            cv.variant_label,
            cv.print_id,
            cv.image_url,
            cv.image_path,
            cv.source,
            cv.is_base,
            cv.is_alt,
            cv.is_sp,
            cv.is_tr,
            cv.is_manga_rare,
            cv.is_golden_manga_rare,
            cv.is_promo,
            cv.is_illustration_rare,
            c.canonical_code
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        WHERE TRIM(COALESCE(cv.image_url, '')) != ''
          AND TRIM(COALESCE(cv.image_path, '')) = ''
          AND cv.source = 'official-cardlist'
        ORDER BY c.canonical_code ASC, cv.id ASC
    """

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()

        for row in rows:
            variant_id = int(row["id"])
            canonical_code = str(row["canonical_code"] or "").strip().upper()
            image_url = str(row["image_url"] or "").strip()
            relative_path = _build_variant_relative_path(row)
            destination = assets_root / Path(relative_path)

            if destination.exists():
                skipped += 1
                _emit(
                    f"FETCH_SKIP type=variant code={canonical_code} reason=already_exists path={relative_path}",
                    fetch_log_path,
                    log_callback,
                )
                continue

            ok, reason, size_bytes = _fetch_one_image(
                image_url=image_url,
                destination=destination,
                log_callback=log_callback,
            )
            if ok:
                conn.execute(
                    """
                    UPDATE card_variants
                    SET image_path = ?
                    WHERE id = ?
                    """,
                    (relative_path.replace("\\", "/"), variant_id),
                )
                conn.commit()
                fetched += 1
                _emit(
                    f"FETCH_OK type=variant code={canonical_code} path={relative_path} size={size_bytes}",
                    fetch_log_path,
                    log_callback,
                )
                continue

            if reason.startswith("http_"):
                failed.append(f"{canonical_code}: {reason}")
                _emit(
                    f"FETCH_FAIL type=variant code={canonical_code} status={reason} url={image_url}",
                    fetch_log_path,
                    log_callback,
                )
            elif reason == "placeholder_detected":
                skipped += 1
                _emit(
                    f"FETCH_SKIP type=variant code={canonical_code} reason=placeholder_detected url={image_url}",
                    fetch_log_path,
                    log_callback,
                )
            else:
                failed.append(f"{canonical_code}: {reason}")
                _emit(
                    f"FETCH_FAIL type=variant code={canonical_code} reason={reason} url={image_url}",
                    fetch_log_path,
                    log_callback,
                )

    return {"fetched": fetched, "skipped": skipped, "failed": failed}


def fetch_missing_base_images(
    db_path: str | Path,
    assets_dir: str | Path,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    db_file = Path(db_path)
    assets_root = Path(assets_dir)
    fetch_log_path = assets_root / "fetch_log.txt"

    fetched = 0
    skipped = 0
    failed: list[str] = []

    query = """
        SELECT canonical_code
        FROM cards
        WHERE COALESCE(is_variant, 0) = 0
          AND TRIM(COALESCE(canonical_code, '')) != ''
        ORDER BY canonical_code ASC
    """

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchall()

    for row in rows:
        canonical_code = str(row["canonical_code"] or "").strip().upper()
        relative_path = _build_base_relative_path(canonical_code)
        destination = assets_root / Path(relative_path)
        if destination.exists():
            skipped += 1
            continue

        image_url = (
            f"https://en.onepiece-cardgame.com/images/cardlist/card/{canonical_code}.png"
        )
        ok, reason, size_bytes = _fetch_one_image(
            image_url=image_url,
            destination=destination,
            log_callback=log_callback,
        )
        if ok:
            fetched += 1
            _emit(
                f"FETCH_OK type=base code={canonical_code} path={relative_path} size={size_bytes}",
                fetch_log_path,
                log_callback,
            )
            continue

        if reason.startswith("http_"):
            failed.append(f"{canonical_code}: {reason}")
            _emit(
                f"FETCH_FAIL type=base code={canonical_code} status={reason} url={image_url}",
                fetch_log_path,
                log_callback,
            )
        elif reason == "placeholder_detected":
            skipped += 1
            _emit(
                f"FETCH_SKIP type=base code={canonical_code} reason=placeholder_detected url={image_url}",
                fetch_log_path,
                log_callback,
            )
        else:
            failed.append(f"{canonical_code}: {reason}")
            _emit(
                f"FETCH_FAIL type=base code={canonical_code} reason={reason} url={image_url}",
                fetch_log_path,
                log_callback,
            )

    return {"fetched": fetched, "skipped": skipped, "failed": failed}


def fetch_all_missing(
    db_path: str | Path,
    assets_dir: str | Path,
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    assets_root = Path(assets_dir)
    fetch_log_path = assets_root / "fetch_log.txt"
    _emit("FETCH_RUN_START scope=all_missing", fetch_log_path, log_callback)

    variant_summary = fetch_missing_variant_images(
        db_path=db_path,
        assets_dir=assets_dir,
        log_callback=log_callback,
    )
    base_summary = fetch_missing_base_images(
        db_path=db_path,
        assets_dir=assets_dir,
        log_callback=log_callback,
    )

    summary = {
        "fetched": int(variant_summary["fetched"]) + int(base_summary["fetched"]),
        "skipped": int(variant_summary["skipped"]) + int(base_summary["skipped"]),
        "failed": list(variant_summary["failed"]) + list(base_summary["failed"]),
    }
    _emit(
        "FETCH_RUN_DONE "
        f"fetched={summary['fetched']} skipped={summary['skipped']} failed={len(summary['failed'])}",
        fetch_log_path,
        log_callback,
    )
    return summary


__all__ = [
    "fetch_missing_variant_images",
    "fetch_missing_base_images",
    "fetch_all_missing",
]
