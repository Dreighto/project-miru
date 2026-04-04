"""
Phase B: Fetch images for READY manifest rows (Bandai base, TCGplayer non-base via bridge).

Writes files under D:\\Miru_Assets and inserts into image_assets only.

Optional smoke test: set environment variable PHASE_B_MAX_FETCHES to a positive integer
to cap HTTP GET attempts (remaining work is logged as SKIPPED_FETCH_LIMIT and not run).
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
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"
MANIFEST_PATH = ROOT / "data" / "image_fetch_manifest.json"
FETCH_LOG_PATH = ROOT / "data" / "phase_b_fetch_log.json"
ASSETS_ROOT = Path(r"D:\Miru_Assets")
LEADER_CROPS_PREFIX = "leader_crops"
MIN_DB_BYTES = 10 * 1024 * 1024

BANDAI_PREFIX = "https://en.onepiece-cardgame.com/images/cardlist/card/"

USER_AGENT = "MiruPhaseBFetch/1.0 (+https://github.com/local/tcg-watcher)"
FETCH_SLEEP_SEC = 1.0
RETRY_SLEEP_SEC = 60
MAX_RETRIES_429_503 = 3

_maxfe = os.environ.get("PHASE_B_MAX_FETCHES", "").strip()
MAX_HTTP_FETCHES: int | None = int(_maxfe) if _maxfe.isdigit() else None

PNG_MAGIC_4 = b"\x89PNG"
JPEG_MAGIC_PREFIX = b"\xff\xd8"

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def pragma_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    cur = conn.cursor()
    return [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]


def build_insert_columns(
    available: set[str],
) -> tuple[list[str], list[str]]:
    """Returns (columns_used, columns_skipped_from_wishlist)."""
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
    used = [c for c in wish if c in available]
    skipped = [c for c in wish if c not in available]
    return used, skipped


def validate_png(data: bytes) -> bool:
    if len(data) <= 10 * 1024:
        return False
    return len(data) >= 4 and data[:4] == PNG_MAGIC_4


def validate_market_image(data: bytes) -> bool:
    if len(data) <= 5 * 1024:
        return False
    if data[:2] == JPEG_MAGIC_PREFIX:
        return True
    if len(data) >= 4 and data[:4] == PNG_MAGIC_4:
        return True
    return False


def fetch_bytes(url: str) -> tuple[bytes | None, int | None, str | None]:
    """
    Returns (body, http_status, error_tag).
    error_tag set on failure; body None.
    """
    last_status: int | None = None
    for attempt in range(MAX_RETRIES_429_503 + 1):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                if status != 200:
                    return None, status, "FETCH_FAILED"
                return resp.read(), status, None
        except urllib.error.HTTPError as e:
            last_status = e.code
            if e.code == 404:
                return None, 404, "FETCH_404"
            if e.code in (429, 503) and attempt < MAX_RETRIES_429_503:
                time.sleep(RETRY_SLEEP_SEC)
                continue
            return None, e.code, "FETCH_FAILED"
        except urllib.error.URLError:
            return None, last_status, "FETCH_FAILED"
        except OSError:
            return None, last_status, "FETCH_FAILED"
    return None, last_status, "FETCH_FAILED"


def sleep_between_fetches() -> None:
    time.sleep(FETCH_SLEEP_SEC)


def target_full_path(target_local_path: str) -> Path:
    rel = Path(*target_local_path.replace("\\", "/").split("/"))
    return ASSETS_ROOT / rel


def assert_safe_asset_path(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if parts and parts[0].lower() == LEADER_CROPS_PREFIX:
        return False
    return True


def load_path_owners(cur: sqlite3.Cursor) -> dict[str, int]:
    """local_path -> printing_id, or -1 if multiple distinct printings share path."""
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


def has_asset_row(
    cur: sqlite3.Connection, printing_id: int, local_path: str
) -> bool:
    r = cur.execute(
        "SELECT 1 FROM image_assets WHERE printing_id = ? AND local_path = ? LIMIT 1",
        (printing_id, local_path),
    ).fetchone()
    return r is not None


def bridge_row(
    cur: sqlite3.Cursor, printing_id: int
) -> tuple[dict[str, Any] | None, bool]:
    """
    Returns (first_row_dict_or_none, ambiguous_multiple).
    """
    cur.execute(
        """
        SELECT pmm.id, pmm.market_product_id, pmm.mapping_confidence, pmm.is_preferred
        FROM printing_market_map pmm
        WHERE pmm.printing_id = ?
          AND pmm.mapping_confidence = 'HIGH'
          AND pmm.is_preferred = 1
        ORDER BY pmm.market_product_id ASC
        """,
        (printing_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return None, False
    ambiguous = len(rows) > 1
    cols = [d[0] for d in (cur.description or ())]
    r0 = rows[0]
    return dict(zip(cols, r0, strict=True)), ambiguous


def market_image_url(cur: sqlite3.Cursor, market_product_pk: int) -> str | None:
    r = cur.execute(
        "SELECT image_url FROM market_products WHERE id = ?", (market_product_pk,)
    ).fetchone()
    if not r or r[0] is None:
        return None
    s = str(r[0]).strip()
    return s if s else None


def main() -> int:
    if not DB_PATH.is_file() or DB_PATH.stat().st_size < MIN_DB_BYTES:
        print(f"FAILED: DB missing or < {MIN_DB_BYTES} bytes", file=sys.stderr)
        return 1
    if not MANIFEST_PATH.is_file():
        print(f"FAILED: missing manifest {MANIFEST_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    ia_cols = pragma_columns(conn, "image_assets")
    print("image_assets columns:", ia_cols)
    colset = set(ia_cols)
    insert_cols, insert_skipped_wish = build_insert_columns(colset)

    manifest = load_manifest()
    entries: list[dict[str, Any]] = manifest.get("entries", [])

    ready_by_fam: dict[str, int] = defaultdict(int)
    for e in entries:
        if e.get("fetch_readiness") == "READY":
            fam = e.get("normalized_variant_family") or ""
            ready_by_fam[str(fam)] += 1
    print("READY by normalized_variant_family (pre-flight):")
    for k in sorted(ready_by_fam.keys(), key=lambda x: (-ready_by_fam[x], x)):
        print(f"  {k!r}: {ready_by_fam[k]}")

    pass1 = [
        e
        for e in entries
        if e.get("fetch_readiness") == "READY"
        and (e.get("normalized_variant_family") or "") == "base"
    ]
    print(f"PASS 1 eligible (READY + base): {len(pass1)}")

    pass2_manifest = [
        e
        for e in entries
        if e.get("fetch_readiness") == "READY"
        and (e.get("normalized_variant_family") or "") not in ("base", "unknown")
    ]
    print(f"PASS 2 manifest eligible (READY, not base/unknown): {len(pass2_manifest)}")

    path_owner = load_path_owners(cur)

    fetch_log: list[dict[str, Any]] = []
    outcomes: dict[str, int] = defaultdict(int)
    bridge_ambiguous = 0
    rows_inserted_this_run = 0
    http_fetches_done = 0

    if MAX_HTTP_FETCHES is not None:
        print(
            f"NOTE: PHASE_B_MAX_FETCHES={MAX_HTTP_FETCHES} (smoke test; unset for full run)"
        )

    def log_line(
        pass_name: str,
        e: dict[str, Any] | None,
        *,
        cid: int | None = None,
        canon: str | None = None,
        fam: str | None = None,
        tpath: str | None = None,
        src_url: str | None = None,
        src_label: str | None = None,
        outcome: str = "",
        http_status: int | None = None,
        fsize: int | None = None,
        checksum: str | None = None,
        conflict_other_printing_id: int | None = None,
    ) -> None:
        rec: dict[str, Any] = {
            "pass": pass_name,
            "card_variants_id": cid
            if cid is not None
            else (e.get("card_variants_id") if e else None),
            "canonical_code": canon
            if canon is not None
            else (e.get("canonical_code") if e else None),
            "normalized_variant_family": fam
            if fam is not None
            else (e.get("normalized_variant_family") if e else None),
            "target_local_path": tpath
            if tpath is not None
            else (e.get("target_local_path") if e else None),
            "source_url": src_url,
            "source_label": src_label,
            "outcome": outcome,
            "http_status": http_status,
            "file_size_bytes": fsize,
            "checksum": checksum,
            "attempted_at": iso_now(),
        }
        if conflict_other_printing_id is not None:
            rec["path_conflict_other_printing_id"] = conflict_other_printing_id
        fetch_log.append(rec)
        outcomes[outcome] += 1

    # All non-READY manifest rows
    for e in entries:
        if e.get("fetch_readiness") != "READY":
            log_line("skipped", e, outcome="SKIPPED_NOT_READY")

    def process_fetch(
        pass_name: str,
        e: dict[str, Any],
        *,
        source_url: str,
        source_label: str,
        asset_type: str,
        validate_fn,
        invalid_outcome: str,
    ) -> None:
        nonlocal rows_inserted_this_run, path_owner, http_fetches_done
        cid = int(e["card_variants_id"])
        tpath = e.get("target_local_path") or ""
        canon = e.get("canonical_code") or ""

        if not tpath or not assert_safe_asset_path(tpath):
            log_line(
                pass_name,
                e,
                src_url=source_url,
                src_label=source_label,
                outcome="FETCH_FAILED",
            )
            return

        own = path_owner.get(tpath)
        if own is not None and own != -1 and own != cid:
            log_line(
                pass_name,
                e,
                src_url=source_url,
                src_label=source_label,
                outcome="PATH_OWNERSHIP_CONFLICT",
                conflict_other_printing_id=own,
            )
            return
        if own == -1:
            log_line(
                pass_name,
                e,
                src_url=source_url,
                src_label=source_label,
                outcome="PATH_OWNERSHIP_CONFLICT",
                conflict_other_printing_id=-1,
            )
            return

        dest = target_full_path(tpath)
        dest.parent.mkdir(parents=True, exist_ok=True)

        def insert_asset(chk: str) -> None:
            nonlocal rows_inserted_this_run, path_owner
            colmap: dict[str, Any] = {
                "printing_id": cid,
                "local_path": tpath,
                "checksum": chk,
                "is_primary": 1,
                "asset_type": asset_type,
                "source_label": source_label,
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
            if cur.rowcount > 0:
                rows_inserted_this_run += 1
            path_owner[tpath] = cid

        if dest.is_file():
            data = dest.read_bytes()
            if validate_fn(data):
                if has_asset_row(conn, cid, tpath):
                    log_line(
                        pass_name,
                        e,
                        src_url=source_url,
                        src_label=source_label,
                        outcome="ALREADY_DONE",
                        fsize=len(data),
                        checksum=hashlib.md5(data).hexdigest(),
                    )
                    return
                chk = hashlib.md5(data).hexdigest()
                insert_asset(chk)
                log_line(
                    pass_name,
                    e,
                    src_url=source_url,
                    src_label=source_label,
                    outcome="INSERTED_EXISTING",
                    http_status=None,
                    fsize=len(data),
                    checksum=chk,
                )
                return
            # invalid on disk — refetch
        if MAX_HTTP_FETCHES is not None and http_fetches_done >= MAX_HTTP_FETCHES:
            log_line(
                pass_name,
                e,
                src_url=source_url,
                src_label=source_label,
                outcome="SKIPPED_FETCH_LIMIT",
            )
            return
        sleep_between_fetches()
        http_fetches_done += 1
        body, status, err = fetch_bytes(source_url)
        if body is None:
            oc = "FETCH_404" if err == "FETCH_404" else "FETCH_FAILED"
            log_line(
                pass_name,
                e,
                src_url=source_url,
                src_label=source_label,
                outcome=oc,
                http_status=status,
            )
            return

        if not validate_fn(body):
            log_line(
                pass_name,
                e,
                src_url=source_url,
                src_label=source_label,
                outcome=invalid_outcome,
                http_status=status,
                fsize=len(body),
            )
            return

        chk = hashlib.md5(body).hexdigest()
        fd, tmp_name = tempfile.mkstemp(
            suffix=".bin", dir=str(dest.parent), text=False
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_bytes(body)
            if not validate_fn(tmp_path.read_bytes()):
                tmp_path.unlink(missing_ok=True)
                log_line(
                    pass_name,
                    e,
                    src_url=source_url,
                    src_label=source_label,
                    outcome=invalid_outcome,
                    http_status=status,
                    fsize=len(body),
                )
                return
            if dest.exists():
                dest.unlink()
            tmp_path.rename(dest)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            log_line(
                pass_name,
                e,
                src_url=source_url,
                src_label=source_label,
                outcome="FETCH_FAILED",
                http_status=status,
            )
            return

        insert_asset(chk)
        log_line(
            pass_name,
            e,
            src_url=source_url,
            src_label=source_label,
            outcome="FETCHED_AND_INSERTED",
            http_status=status,
            fsize=len(body),
            checksum=chk,
        )

    # PASS 1 (base only — reprint is never base)
    for e in pass1:
        canon = (e.get("canonical_code") or "").strip()
        url = f"{BANDAI_PREFIX}{canon}.png"
        process_fetch(
            "base",
            e,
            source_url=url,
            source_label="bandai_cdn",
            asset_type="card_scan",
            validate_fn=validate_png,
            invalid_outcome="FETCH_INVALID_PNG",
        )

    # PASS 2 — build work list with bridge + image_url
    pass2_enriched: list[tuple[dict[str, Any], str]] = []
    for e in pass2_manifest:
        fam = e.get("normalized_variant_family") or ""
        if fam == "reprint":
            log_line("market", e, outcome="SKIPPED_REPRINT_EXCLUDED")
            continue
        cid = int(e["card_variants_id"])
        row, amb = bridge_row(cur, cid)
        if amb:
            bridge_ambiguous += 1
        if not row:
            log_line("market", e, outcome="SKIPPED_NO_BRIDGE")
            continue
        mpk = int(row["market_product_id"])
        iu = market_image_url(cur, mpk)
        if not iu:
            log_line("market", e, outcome="SKIPPED_NO_MARKET_IMAGE")
            continue
        pass2_enriched.append((e, iu))

    print(f"PASS 2 enriched eligible (bridge + image_url): {len(pass2_enriched)}")

    for e, iu in pass2_enriched:
        process_fetch(
            "market",
            e,
            source_url=iu,
            source_label="tcgplayer_market_image",
            asset_type="market_reference",
            validate_fn=validate_market_image,
            invalid_outcome="FETCH_INVALID_IMAGE",
        )

    total_ia = cur.execute("SELECT COUNT(*) FROM image_assets").fetchone()[0]

    FETCH_LOG_PATH.write_text(
        json.dumps(fetch_log, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    def pass_outcome_counts(pass_name: str, codes: list[str]) -> dict[str, int]:
        d: dict[str, int] = {}
        for c in codes:
            n = sum(
                1 for x in fetch_log if x["pass"] == pass_name and x["outcome"] == c
            )
            if n:
                d[c] = n
        return d

    p1_codes = [
        "FETCHED_AND_INSERTED",
        "ALREADY_DONE",
        "INSERTED_EXISTING",
        "FETCH_404",
        "FETCH_FAILED",
        "FETCH_INVALID_PNG",
        "PATH_OWNERSHIP_CONFLICT",
        "SKIPPED_FETCH_LIMIT",
    ]
    p2_codes = [
        "FETCHED_AND_INSERTED",
        "ALREADY_DONE",
        "INSERTED_EXISTING",
        "FETCH_404",
        "FETCH_FAILED",
        "FETCH_INVALID_IMAGE",
        "PATH_OWNERSHIP_CONFLICT",
        "SKIPPED_REPRINT_EXCLUDED",
        "SKIPPED_NO_BRIDGE",
        "SKIPPED_NO_MARKET_IMAGE",
        "SKIPPED_FETCH_LIMIT",
    ]

    fetch404_entries = [x for x in fetch_log if x["outcome"] == "FETCH_404"]
    no_bridge_ex = [
        x
        for x in fetch_log
        if x["outcome"] == "SKIPPED_NO_BRIDGE"
    ][:10]
    no_img_ex = [
        x
        for x in fetch_log
        if x["outcome"] == "SKIPPED_NO_MARKET_IMAGE"
    ][:10]
    own_ex = [x for x in fetch_log if x["outcome"] == "PATH_OWNERSHIP_CONFLICT"]

    ok_by_fam: dict[str, int] = defaultdict(int)
    ok_by_src: dict[str, int] = defaultdict(int)
    for x in fetch_log:
        if x["outcome"] == "FETCHED_AND_INSERTED":
            fam = x.get("normalized_variant_family") or ""
            ok_by_fam[fam] += 1
            lbl = x.get("source_label") or ""
            ok_by_src[lbl] += 1

    print("\n=== SUMMARY ===")
    print(f"PASS 1 eligible: {len(pass1)}")
    print("PASS 1 outcomes:", pass_outcome_counts("base", p1_codes))
    print(f"PASS 2 manifest eligible: {len(pass2_manifest)}")
    print(f"PASS 2 enriched eligible: {len(pass2_enriched)}")
    print("PASS 2 fetch outcomes:", pass_outcome_counts("market", p2_codes))
    print(f"SKIPPED_NOT_READY: {outcomes['SKIPPED_NOT_READY']}")
    print(f"SKIPPED_REPRINT_EXCLUDED (total): {outcomes['SKIPPED_REPRINT_EXCLUDED']}")
    print(f"SKIPPED_NO_BRIDGE: {outcomes['SKIPPED_NO_BRIDGE']}")
    print("  first 10 examples (canonical + family):")
    for x in no_bridge_ex:
        print(
            f"    {x.get('canonical_code')!r} | {x.get('normalized_variant_family')!r}"
        )
    print(f"SKIPPED_NO_MARKET_IMAGE: {outcomes['SKIPPED_NO_MARKET_IMAGE']}")
    print("  first 10 examples (canonical + family):")
    if no_img_ex:
        for x in no_img_ex:
            print(
                f"    {x.get('canonical_code')!r} | {x.get('normalized_variant_family')!r}"
            )
    else:
        print("    (none)")
    print(f"PATH_OWNERSHIP_CONFLICT: {outcomes['PATH_OWNERSHIP_CONFLICT']}")
    print("  all examples (path + printing_ids):")
    for x in own_ex:
        print(
            f"    path={x.get('target_local_path')!r} "
            f"this_id={x.get('card_variants_id')} "
            f"other_id={x.get('path_conflict_other_printing_id')}"
        )
    print(f"bridge_ambiguous_queries: {bridge_ambiguous}")
    print(f"rows_inserted_this_run: {rows_inserted_this_run}")
    print(f"total image_assets rows: {total_ia}")
    print("FETCH_404 list (canonical + family):")
    for x in fetch404_entries:
        print(f"  {x.get('canonical_code')} | {x.get('normalized_variant_family')}")
    print("Successful fetches by family:", dict(ok_by_fam))
    print("Successful fetches by source_label:", dict(ok_by_src))
    if insert_skipped_wish:
        print("image_assets columns skipped (wishlist not in schema):", insert_skipped_wish)
    else:
        print("image_assets: all wishlist columns present for insert")
    print(f"fetch_log: {FETCH_LOG_PATH}")

    lim_hit = outcomes.get("SKIPPED_FETCH_LIMIT", 0)
    if lim_hit:
        print(f"SKIPPED_FETCH_LIMIT (PHASE_B_MAX_FETCHES): {lim_hit}")
    status = "CONFIRMED WORKING"
    if MAX_HTTP_FETCHES is not None and lim_hit:
        status = "INCONCLUSIVE"
    print(f"status: {status}")
    print()
    print("=== COMPLETION CONTRACT ===")
    print(f"total_log_entries: {len(fetch_log)}")
    print(f"PASS_1_eligible: {len(pass1)}")
    print(f"PASS_1_outcomes: {pass_outcome_counts('base', p1_codes)}")
    print(f"PASS_2_manifest_eligible: {len(pass2_manifest)}")
    print(f"PASS_2_enriched_eligible: {len(pass2_enriched)}")
    print(f"PASS_2_outcomes: {pass_outcome_counts('market', p2_codes)}")
    print(f"PATH_OWNERSHIP_CONFLICT: {outcomes['PATH_OWNERSHIP_CONFLICT']}")
    print(f"rows_inserted_this_run: {rows_inserted_this_run}")
    print(f"total_image_assets: {total_ia}")
    print(f"fetch_log_path: {FETCH_LOG_PATH}")
    print(f"insert_columns_skipped: {insert_skipped_wish if insert_skipped_wish else []}")
    print(f"status: {status}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
