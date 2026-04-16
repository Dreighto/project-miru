"""
Phase B retry: re-fetch TCGplayer market images for FETCH_FAILED market log rows only.

Writes D:\\Miru_Assets + image_assets only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
FETCH_LOG_PATH = ROOT / "data" / "phase_b_fetch_log.json"
ASSETS_ROOT = Path(r"D:\Miru_Assets")
MIN_DB_BYTES = 10 * 1024 * 1024
LEADER_CROPS_PREFIX = "leader_crops"

USER_AGENT = "MiruPhaseBRetry/1.0 (+https://github.com/local/tcg-watcher)"
FETCH_SLEEP_SEC = 1.0
RETRY_SLEEP_SEC = 60
MAX_RETRIES_429_503 = 3

PNG_MAGIC_4 = b"\x89PNG"
JPEG_MAGIC_PREFIX = b"\xff\xd8"


def fetch_bytes(url: str) -> tuple[bytes | None, int | None, str | None]:
    last_status: int | None = None
    for attempt in range(MAX_RETRIES_429_503 + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if status != 200:
                    return None, status, "FETCH_FAILED_AGAIN"
                return resp.read(), status, None
        except urllib.error.HTTPError as e:
            last_status = e.code
            if e.code == 404:
                return None, 404, "FETCH_404"
            if e.code in (429, 503) and attempt < MAX_RETRIES_429_503:
                time.sleep(RETRY_SLEEP_SEC)
                continue
            return None, e.code, "FETCH_FAILED_AGAIN"
        except urllib.error.URLError:
            return None, last_status, "FETCH_FAILED_AGAIN"
        except OSError:
            return None, last_status, "FETCH_FAILED_AGAIN"
    return None, last_status, "FETCH_FAILED_AGAIN"


def validate_market_image(data: bytes) -> bool:
    if len(data) <= 5 * 1024:
        return False
    if data[:2] == JPEG_MAGIC_PREFIX:
        return True
    if len(data) >= 4 and data[:4] == PNG_MAGIC_4:
        return True
    return False


def trim(s: Any) -> str:
    return (str(s) if s is not None else "").strip()


def build_target_local_path(
    npk: str, family: str, canonical: str, nvk: str
) -> str:
    if family == "base":
        return f"{trim(npk)}/base/{trim(canonical)}.png"
    vk = nvk if nvk is not None else ""
    return f"{trim(npk)}/{trim(family)}/{trim(canonical)}{vk}.png"


def assert_safe_rel_path(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    return not (parts and parts[0].lower() == LEADER_CROPS_PREFIX)


def load_path_owners(cur: sqlite3.Cursor) -> dict[str, int]:
    owners: dict[str, int] = {}
    for lp, pid in cur.execute(
        "SELECT local_path, printing_id FROM image_assets"
    ).fetchall():
        if lp in owners:
            if owners[lp] != pid:
                owners[lp] = -1
        else:
            owners[lp] = int(pid)
    return owners


def pragma_insert_cols(conn: sqlite3.Connection) -> list[str]:
    colset = {r[1] for r in conn.execute("PRAGMA table_info(image_assets)").fetchall()}
    wish = [
        "printing_id",
        "local_path",
        "checksum",
        "is_primary",
        "asset_type",
        "source_label",
        "source_url",
        "created_at",
        "updated_at",
    ]
    return [c for c in wish if c in colset]


def insert_asset(
    conn: sqlite3.Connection,
    cur: sqlite3.Cursor,
    insert_cols: list[str],
    *,
    printing_id: int,
    local_path: str,
    checksum: str,
    source_url: str,
) -> int:
    colmap: dict[str, Any] = {
        "printing_id": printing_id,
        "local_path": local_path,
        "checksum": checksum,
        "is_primary": 1,
        "asset_type": "market_reference",
        "source_label": "tcgplayer_market_image",
        "source_url": source_url,
    }
    parts_val: list[str] = []
    use_vals: list[Any] = []
    for c in insert_cols:
        if c in ("created_at", "updated_at"):
            parts_val.append("datetime('now')")
        else:
            parts_val.append("?")
            use_vals.append(colmap[c])
    sql = f"""
        INSERT INTO image_assets ({", ".join(insert_cols)})
        VALUES ({", ".join(parts_val)})
        ON CONFLICT(printing_id, local_path) DO NOTHING
    """
    cur.execute(sql, use_vals)
    conn.commit()
    return cur.rowcount


def main() -> int:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size < MIN_DB_BYTES:
        print(f"FAILED: DB missing or < {MIN_DB_BYTES} bytes", file=sys.stderr)
        return 1
    if not FETCH_LOG_PATH.is_file():
        print("FAILED: missing fetch log", file=sys.stderr)
        return 1

    log = json.loads(FETCH_LOG_PATH.read_text(encoding="utf-8"))
    gate1 = [
        x
        for x in log
        if x.get("outcome") == "FETCH_FAILED" and x.get("pass") == "market"
    ]
    g1_count = len(gate1)
    print(f"Gate 1 FETCH_FAILED market entries: {g1_count}")
    if g1_count == 0 or g1_count > 60:
        print("ABORT: Gate 1 count out of expected range (1..60)", file=sys.stderr)
        return 2

    ids_order = []
    seen: set[int] = set()
    for x in gate1:
        cid = int(x["card_variants_id"])
        if cid not in seen:
            seen.add(cid)
            ids_order.append(cid)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    insert_cols = pragma_insert_cols(conn)

    path_owner = load_path_owners(cur)

    gate2_sql = """
    SELECT cv.id AS cv_id, c.canonical_code, cv.normalized_variant_family,
           cv.normalized_variant_key, cv.normalized_product_key,
           pmm.mapping_confidence, pmm.is_preferred,
           TRIM(COALESCE(mp.image_url, '')) AS image_url, mp.id AS market_product_id
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    JOIN printing_market_map pmm ON pmm.printing_id = cv.id
    JOIN market_products mp ON mp.id = pmm.market_product_id
    WHERE cv.id = ?
      AND pmm.mapping_confidence = 'HIGH'
      AND pmm.is_preferred = 1
      AND mp.image_url IS NOT NULL
      AND TRIM(mp.image_url) != ''
      AND cv.normalized_variant_family NOT IN ('base', 'unknown')
    ORDER BY pmm.market_product_id ASC
    LIMIT 1
    """

    stats = {
        "skipped_disqualified": 0,
        "already_done": 0,
        "fetched_inserted": 0,
        "fetch_404": 0,
        "fetch_failed_again": 0,
        "fetch_invalid_image": 0,
        "path_ownership_conflict": 0,
        "rows_inserted": 0,
    }
    fetch_404_codes: list[str] = []
    failed_again_codes: list[str] = []
    invalid_details: list[str] = []
    ownership_examples: list[str] = []

    confirmed: list[sqlite3.Row] = []
    for cid in ids_order:
        row = cur.execute(gate2_sql, (cid,)).fetchone()
        if not row:
            stats["skipped_disqualified"] += 1
            continue
        ex = cur.execute(
            "SELECT id FROM image_assets WHERE printing_id = ?", (cid,)
        ).fetchone()
        if ex:
            stats["already_done"] += 1
            continue
        confirmed.append(row)

    print(f"Gate 2 confirmed retry candidates: {len(confirmed)}")
    print(
        f"SKIPPED_DISQUALIFIED: {stats['skipped_disqualified']}, "
        f"ALREADY_DONE: {stats['already_done']}"
    )

    for row in confirmed:
        cid = int(row["cv_id"])
        canon = trim(row["canonical_code"])
        fam = trim(row["normalized_variant_family"])
        nvk = row["normalized_variant_key"]
        npk = row["normalized_product_key"]
        image_url = trim(row["image_url"])
        tpath = build_target_local_path(npk, fam, canon, nvk if nvk is not None else "")

        if not assert_safe_rel_path(tpath):
            stats["fetch_invalid_image"] += 1
            invalid_details.append(f"id={cid} unsafe_path={tpath!r}")
            continue

        own = path_owner.get(tpath)
        if own is not None and own != -1 and own != cid:
            stats["path_ownership_conflict"] += 1
            ownership_examples.append(
                f"path={tpath!r} this_printing={cid} other_printing={own}"
            )
            continue
        if own == -1:
            stats["path_ownership_conflict"] += 1
            ownership_examples.append(
                f"path={tpath!r} this_printing={cid} other_printing=AMBIGUOUS_DB"
            )
            continue

        dest = ASSETS_ROOT / Path(*tpath.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)

        time.sleep(FETCH_SLEEP_SEC)
        body, status, err = fetch_bytes(image_url)
        if body is None:
            if err == "FETCH_404":
                stats["fetch_404"] += 1
                fetch_404_codes.append(canon)
            else:
                stats["fetch_failed_again"] += 1
                failed_again_codes.append(canon)
            continue

        if not validate_market_image(body):
            stats["fetch_invalid_image"] += 1
            invalid_details.append(
                f"id={cid} canon={canon} size={len(body)} status={status}"
            )
            continue

        chk = hashlib.md5(body).hexdigest()
        fd, tmp_name = tempfile.mkstemp(
            suffix=".bin", dir=str(dest.parent), text=False
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_bytes(body)
            if not validate_market_image(tmp_path.read_bytes()):
                tmp_path.unlink(missing_ok=True)
                stats["fetch_invalid_image"] += 1
                invalid_details.append(f"id={cid} canon={canon} post_write_invalid")
                continue
            if dest.exists():
                dest.unlink()
            tmp_path.rename(dest)
        except OSError as e:
            tmp_path.unlink(missing_ok=True)
            stats["fetch_failed_again"] += 1
            failed_again_codes.append(f"{canon}({e})")
            continue

        rc = insert_asset(
            conn,
            cur,
            insert_cols,
            printing_id=cid,
            local_path=tpath,
            checksum=chk,
            source_url=image_url,
        )
        if rc > 0:
            stats["rows_inserted"] += 1
            stats["fetched_inserted"] += 1
        path_owner[tpath] = cid

    total_ia = cur.execute("SELECT COUNT(*) FROM image_assets").fetchone()[0]
    conn.close()

    print("\n=== RETRY SUMMARY ===")
    print(f"Gate 1 count: {g1_count}")
    print(f"Gate 2 confirmed (passed gates, not already_done): {len(confirmed)}")
    print(f"SKIPPED_DISQUALIFIED: {stats['skipped_disqualified']}")
    print(f"ALREADY_DONE: {stats['already_done']}")
    print(f"FETCHED_AND_INSERTED: {stats['fetched_inserted']}")
    print(f"FETCH_404: {stats['fetch_404']} {fetch_404_codes}")
    print(f"FETCH_FAILED_AGAIN: {stats['fetch_failed_again']} {failed_again_codes}")
    print(f"FETCH_INVALID_IMAGE: {stats['fetch_invalid_image']}")
    for d in invalid_details[:20]:
        print(f"  invalid: {d}")
    print(f"PATH_OWNERSHIP_CONFLICT: {stats['path_ownership_conflict']}")
    for o in ownership_examples:
        print(f"  {o}")
    print(f"Total image_assets rows: {total_ia}")

    ok = (
        stats["path_ownership_conflict"] == 0
        and stats["fetch_failed_again"] == 0
        and stats["fetch_404"] == 0
        and stats["fetch_invalid_image"] == 0
    )
    status = "CONFIRMED WORKING" if ok else "INCONCLUSIVE"
    print(f"status: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
