"""
Bandai CDN parallel (_pN) and reprint (_rN) fetcher with provenance-based routing.

Writes images under D:\\Miru_Assets, updates card_variants in data/card_catalog.db only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "data" / "card_catalog.db"
ASSETS_ROOT = Path(r"D:\Miru_Assets")
_LOG_OVERRIDE = (os.environ.get("MIRU_PARALLEL_FETCH_LOG") or "").strip()
LOG_PATH = Path(_LOG_OVERRIDE) if _LOG_OVERRIDE else ASSETS_ROOT / "parallel_reprint_fetch_log.txt"
SUMMARY_PATH = ASSETS_ROOT / "parallel_reprint_fetch_summary.txt"
CDN_BASE = "https://en.onepiece-cardgame.com/images/cardlist/card"
LIST_BASE = "https://en.onepiece-cardgame.com/cardlist/"
CDN_DELAY = 2.0
SERIES_DELAY = 3.0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_BYTES = 50 * 1024

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MiruParallelFetcher/1.0; catalog sync)",
    "Accept-Language": "en-US,en;q=0.9",
}

# Series pages with no _p1/_r1 modals per recon — skip HTTP fetch
SKIP_SERIES_IDS = frozenset(
    [str(569000 + i) for i in range(1, 11)] + ["569012", "569014"]
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_prov(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "")
    t = t.replace("\u2019", "'").replace("\u2018", "'").replace("\u2032", "'")
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Literal -> distribution_product_key (exact + common alternates filled at runtime)
PROVENANCE_TO_KEY: dict[str, str] = {}


def _build_provenance_map() -> None:
    global PROVENANCE_TO_KEY
    raw = [
        ("-ROMANCE DAWN- [OP01]", "OP01"),
        ("-PARAMOUNT WAR- [OP02]", "OP02"),
        ("-PILLARS OF STRENGTH- [OP03]", "OP03"),
        ("-KINGDOMS OF INTRIGUE- [OP04]", "OP04"),
        ("-AWAKENING OF THE NEW ERA-[OP05]", "OP05"),
        ("-WINGS OF THE CAPTAIN-[OP06]", "OP06"),
        ("-500 YEARS IN THE FUTURE- [OP-07]", "OP07"),
        ("-TWO LEGENDS- [OP-08]", "OP08"),
        ("-EMPERORS IN THE NEW WORLD- [OP-09]", "OP09"),
        ("-ROYAL BLOOD- [OP-10]", "OP10"),
        ("-A FIST OF DIVINE SPEED- [OP-11]", "OP11"),
        ("-LEGACY OF THE MASTER- [OP-12]", "OP12"),
        ("-CARRYING ON HIS WILL- [OP-13]", "OP13"),
        ("-THE AZURE SEA'S SEVEN- [OP14-EB04]", "OP14"),
        ("-ADVENTURE ON KAMI'S ISLAND- [OP15-EB04]", "OP15"),
        ("-Memorial Collection- [EB-01]", "EB01"),
        ("-Anime 25th Collection- [EB-02]", "EB02"),
        ("-ONE PIECE HEROINES EDITION- [EB-03]", "EB03"),
        ("-ONE PIECE CARD THE BEST- [PRB-01]", "PRB01"),
        ("-ONE PIECE CARD THE BEST vol.2- [PRB-02]", "PRB02"),
        ("-Uta-[ST-11]", "ST11"),
        ("-The Three Brothers-[ST13]", "ST13"),
        ("-Red Edward.Newgate- [ST-15]", "ST15"),
        ("-Green Uta- [ST-16]", "ST16"),
        ("-Blue Donquixote Doflamingo- [ST-17]", "ST17"),
        ("-Purple Monkey.D.Luffy- [ST-18]", "ST18"),
        ("-Black Smoker- [ST-19]", "ST19"),
        ("-Yellow Charlotte Katakuri- [ST-20]", "ST20"),
        ("-GEAR5- [ST-21]", "ST21"),
        ("-Ace & Newgate- [ST-22]", "ST22"),
        ("-RED Shanks- [ST-23]", "ST23"),
        ("-GREEN Jewelry Bonney- [ST-24]", "ST24"),
        ("-BLUE Buggy- [ST-25]", "ST25"),
        ("-PURPLE/BLACK Monkey.D.Luffy- [ST-26]", "ST26"),
        ("-BLACK Marshall.D.Teach- [ST-27]", "ST27"),
        ("-GREEN/YELLOW Yamato- [ST-28]", "ST28"),
        ("-Egghead- [ST-29]", "ST29"),
        ("Premium Card Collection -Best Selection-", "BCV1"),
        ("Premium Card Collection -Best Selection Vol.3-", "BCV3"),
        ("Premium Card Collection -Best Selection Vol.4-", "BCV4"),
        ("Premium Card Collection -Best Selection Vol.5-", "BCV5"),
        ("Premium Card Collection -FILM RED Edition-", "PCCFR"),
        ("Premium Card Collection -Live Action Edition-", "PCCLA1"),
        ("Premium Card Collection -25th Edition-", "PCC25"),
        ("English Version 1st Anniversary Set", "ANN1EN"),
        ("Japanese 1st Anniversary Set", "ANN1JP"),
        ("Japanese 2nd Anniversary Set", "ANN2JP"),
        ("Japanese 3rd ANNIVERSARY SET", "ANN3JP"),
        ("GIFT COLLECTION 2023 [GC-01]", "GC01"),
        ("Seven Warlords of the Sea Binder Set", "SWBS01"),
        ("Tournament Pack Vol.2", "TP02"),
        ("Tournament Pack Vol.3", "TP03"),
        ("Tournament Pack Vol.4", "TP04"),
        ("Tournament Pack Vol.5", "TP05"),
        ("Tournament Pack Vol.6", "TP06"),
        ("Tournament Pack Vol.7", "TP07"),
        ("Tournament Pack 2024 Oct.-Dec.", "TP2024OD"),
        ("Tournament Pack 2025 Vol. 3", "TP2025V3"),
        ("Tournament Pack 2025 Vol. 4", "TP2025V4"),
        ("Tournament Kit 2025 Vol.2", "TK2025V2"),
        ("Included in Event Pack Vol.2", "EP02"),
        ("Event Pack Vol.3", "EP03"),
        ("Included in Online Regional Participation Pack Vol.1", "ORP01"),
        ("Offline Regional Participation Pack 2024 Vol. 1", "ORP2024V1"),
        ("Offline Regional Participation Pack 2024 Vol. 2", "ORP2024V2"),
        ("Offline Regional Participation Pack 2024 Vol. 3", "ORP2024V3"),
        ("Offline Regional Participation Pack 2025 Vol.1", "ORP2025V1"),
        ("Offline Regional Finalist Card Set 2024 Vol. 3", "ORFC2024V3"),
        ("Offline Regional Finalist Card Set 2025 Vol.2", "ORFC2025V2"),
        ("Offline Regional Finalist Card Set 25-26 Season 1", "ORFC2526S1"),
        ("Regional 2024 wave1", "REG2024W1"),
        ("Regional 25-26 Season1", "REG2526S1"),
        ("Regionals Wave 3", "REG2024W3"),
        ("Championship 25-26 Offline Regionals Season 2", "CS2526ORS2"),
        ("CS 25-26 Event Pack", "CS2526EP"),
        ("CS 25-26 Top Player Pack", "CS2526TP1"),
        ("CS 25-26 Top Player Pack 2", "CS2526TP2"),
        ("CS 25-26 Celebration Pack", "CS2526CP"),
        ("BANDAI CARD GAMES Fest 25-26", "BCGF2526"),
        ("ST-11 Uta Deck Battle Participation Pack", "ST11BP"),
        ("ST15-20 Release Event", "ST1520RE"),
        ("OP-11 Release Event", "OP11RE"),
        ("OP-12 Release Event", "OP12RE"),
        ("OP14-EB04 Release Event", "OP14RE"),
        ("OP15-EB04 Release Event", "OP15RE"),
        ("Pre-Release OP03", "PREP03"),
        ("Super Pre-Release", "SPRRE"),
        ("Heroines Battle Winner Pack", "HBWP01"),
        ("Store 2-on-2 Battle", "S2ON2"),
        ("ONE PIECE DAY Dallas -Card Game Celebration-", "OPDALLAS"),
        ("Treasure Cup August - September", "TC2024AS"),
        ("Treasure Cup February 2025", "TC2025FEB"),
    ]
    PROVENANCE_TO_KEY = {}
    for lit, key in raw:
        PROVENANCE_TO_KEY[_norm_prov(lit)] = key
        PROVENANCE_TO_KEY[lit.strip()] = key


_build_provenance_map()

# Recon alternates (spacing / apostrophe)
_EXTRA_ALIASES = [
    ("-500 YEARS IN THE FUTURE- [OP07]", "OP07"),
    ("-THE AZURE SEA'S SEVEN- [OP14-EB04]", "EB04"),
    ("-ADVENTURE ON KAMI'S ISLAND- [OP15-EB04]", "EB04"),
    ("-WINGS OF THE CAPTAIN- [OP06]", "OP06"),
    ("-AWAKENING OF THE NEW ERA- [OP05]", "OP05"),
    ("-The Three Brothers- [ST13]", "ST13"),
]
for a, k in _EXTRA_ALIASES:
    PROVENANCE_TO_KEY.setdefault(_norm_prov(a), k)


def resolve_distribution_key(literal: str) -> str | None:
    if not literal:
        return None
    s = literal.strip()
    if s in PROVENANCE_TO_KEY:
        return PROVENANCE_TO_KEY[s]
    n = _norm_prov(s)
    if n in PROVENANCE_TO_KEY:
        return PROVENANCE_TO_KEY[n]
    return None


def ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(card_variants)").fetchall()}
    alters = []
    if "official_provenance" not in cols:
        alters.append("ALTER TABLE card_variants ADD COLUMN official_provenance TEXT")
    if "distribution_product_key" not in cols:
        alters.append(
            "ALTER TABLE card_variants ADD COLUMN distribution_product_key TEXT"
        )
    if "updated_at" not in cols:
        alters.append("ALTER TABLE card_variants ADD COLUMN updated_at TEXT")
    for sql in alters:
        conn.execute(sql)
    if alters:
        conn.commit()


def extract_card_set_literal(get_info_div) -> str:
    h3 = get_info_div.find("h3")
    if not h3:
        return ""
    lab = h3.get_text(" ", strip=True).lower()
    if "card set" not in lab:
        return ""
    chunks: list[str] = []
    for sib in h3.next_siblings:
        if isinstance(sib, str):
            t = sib.strip()
            if t:
                chunks.append(t)
        elif hasattr(sib, "get_text"):
            t = sib.get_text(" ", strip=True)
            if t:
                chunks.append(t)
    s = " ".join(chunks).strip()
    if not s:
        full = get_info_div.get_text(" ", strip=True)
        s = re.sub(r"^Card Set\(s\)\s*", "", full, flags=re.I).strip()
    return re.sub(r"\s+", " ", s)


def parse_modal_provenance(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    soup = BeautifulSoup(html, "html.parser")
    for el in soup.select("dl.modalCol, div.modalCol"):
        cid = (el.get("id") or "").strip()
        if not cid:
            continue
        for gi in el.select("div.getInfo"):
            prov = extract_card_set_literal(gi)
            if prov:
                out[cid] = prov
                break
    return out


def http_get(url: str, binary: bool = False) -> tuple[int, bytes | str, str]:
    req = Request(url, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read()
            if binary:
                return resp.status, raw, ""
            enc = resp.headers.get_content_charset() or "utf-8"
            return resp.status, raw.decode(enc, errors="replace"), ""
    except HTTPError as e:
        try:
            b = e.read()
            body = b if binary else b.decode("utf-8", errors="replace")
        except Exception:
            body = b"" if binary else ""
        return e.code, body, str(e)
    except (URLError, OSError) as e:
        return -1, b"" if binary else "", str(e)


def fetch_cdn_png(url: str) -> tuple[int, bytes, str]:
    status, body, err = http_get(url, binary=True)
    if status == 429:
        time.sleep(30.0)
        status, body, err = http_get(url, binary=True)
    return status, body if isinstance(body, bytes) else b"", err


def validate_png(data: bytes) -> tuple[bool, str]:
    if len(data) < 8 or data[:8] != PNG_MAGIC:
        return False, "invalid_png"
    if len(data) <= MIN_BYTES:
        return False, "placeholder_detected"
    return True, ""


def load_bandai_series_rows(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT bandai_series_id, product_name FROM bandai_cardlist_scrape ORDER BY bandai_series_id"
    ).fetchall()


def warm_modal_index(
    conn: sqlite3.Connection,
    log_lines: list[str],
) -> dict[str, str]:
    rows = load_bandai_series_rows(conn)
    modal_index: dict[str, str] = {}
    for sid, _name in rows:
        if sid in SKIP_SERIES_IDS:
            continue
        time.sleep(SERIES_DELAY)
        url = f"{LIST_BASE}?series={sid}"
        status, html, err = http_get(url, binary=False)
        if status != 200 or not isinstance(html, str):
            log_lines.append(
                f"[ERROR] series={sid} | series_page | HTTP={status} {err!r}"
            )
            continue
        m = parse_modal_provenance(html)
        for k, v in m.items():
            modal_index[k] = v
        log_lines.append(f"[SERIES] {sid} modals_parsed={len(m)}")
    return modal_index


def get_provenance_for_suffix(
    code: str,
    family: str,
    n: int,
    modal_index: dict[str, str],
) -> str:
    """family 'p' or 'r'."""
    sid = f"{code}_{family}{n}"
    if sid in modal_index:
        return modal_index[sid]
    if n != 1:
        fallback = f"{code}_{family}1"
        return modal_index.get(fallback, "")
    return ""


def existing_variant_asset_on_disk(
    conn: sqlite3.Connection, canonical: str, variant_key: str
) -> bool:
    """Resume: skip CDN when DB already has image_path and file exists under ASSETS_ROOT."""
    r = conn.execute(
        """
        SELECT cv.image_path FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        WHERE c.canonical_code = ? AND cv.variant_key = ?
        LIMIT 1
        """,
        (canonical, variant_key),
    ).fetchone()
    if not r or not r[0]:
        return False
    rel = str(r[0]).strip().replace("\\", "/")
    if not rel:
        return False
    p = ASSETS_ROOT / rel
    try:
        return p.is_file()
    except OSError:
        return False


def variant_row_id(
    conn: sqlite3.Connection, canonical: str, variant_key: str
) -> int | None:
    r = conn.execute(
        """
        SELECT cv.id FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        WHERE c.canonical_code = ? AND cv.variant_key = ?
        LIMIT 1
        """,
        (canonical, variant_key),
    ).fetchone()
    return int(r[0]) if r else None


def distinct_canonical_codes(conn: sqlite3.Connection, limit: int | None) -> list[str]:
    q = """
        SELECT DISTINCT c.canonical_code
        FROM card_variants cv
        JOIN cards c ON c.id = cv.card_id
        ORDER BY c.canonical_code
    """
    rows = conn.execute(q).fetchall()
    codes = [r[0] for r in rows if r[0]]
    if limit is not None:
        codes = codes[:limit]
    return codes


def run_fetch(
    *,
    limit_cards: int | None,
    dry_run: bool,
) -> dict[str, Any]:
    if not DB_PATH.is_file():
        raise SystemExit(f"Missing database: {DB_PATH}")

    ASSETS_ROOT.mkdir(parents=True, exist_ok=True)
    log_lines: list[str] = []
    folders_created: set[str] = set()
    stats = {
        "fetched_p": 0,
        "fetched_r": 0,
        "skipped": 0,
        "resume_skipped": 0,
        "unmapped": 0,
        "errors": 0,
        "db_updates": 0,
    }
    unmapped_strings: set[str] = set()

    conn = sqlite3.connect(DB_PATH, timeout=120.0)
    ensure_columns(conn)

    log_lines.append(f"=== START {_utc_now()} limit={limit_cards} dry_run={dry_run} ===")
    if dry_run:
        modal_index: dict[str, str] = {}
        log_lines.append(
            "[INFO] dry_run: skipping Bandai series-page HTTP (empty modal index)"
        )
    else:
        modal_index = warm_modal_index(conn, log_lines)

    codes = distinct_canonical_codes(conn, limit_cards)
    log_lines.append(f"[INFO] canonical_codes_to_process={len(codes)}")

    def process_family(code: str, family: str) -> None:
        n = 0
        while True:
            n += 1
            suffix = f"_{family}{n}"
            vkey = f"parallel {n}" if family == "p" else f"r{n}"
            if existing_variant_asset_on_disk(conn, code, vkey):
                log_lines.append(
                    f"[SKIP] {code} | {suffix} | resume_existing_asset path_ok variant_key={vkey!r}"
                )
                stats["resume_skipped"] += 1
                continue
            if dry_run:
                log_lines.append(
                    f"[DRY_RUN] {code} | {suffix} | skip_network (would_CDN_fetch) variant_key={vkey!r}"
                )
                break
            url = f"{CDN_BASE}/{code}{suffix}.png"
            time.sleep(CDN_DELAY)
            status, data, err = fetch_cdn_png(url)
            if status == 404:
                break
            if status != 200:
                log_lines.append(
                    f"[ERROR] {code} | {suffix} | HTTP={status} {err!r}"
                )
                stats["errors"] += 1
                break
            ok, reason = validate_png(data)
            if not ok:
                log_lines.append(f"[SKIP] {code} | {suffix} | {reason}")
                stats["skipped"] += 1
                break
            prov = get_provenance_for_suffix(code, family, n, modal_index)
            dkey = resolve_distribution_key(prov)
            if not dkey:
                dkey = "_unclassified"
                if prov:
                    unmapped_strings.add(prov)
                    log_lines.append(
                        f"[UNMAPPED] {code} | {suffix} | {prov!r}"
                    )
                else:
                    log_lines.append(
                        f"[UNMAPPED] {code} | {suffix} | <empty modal Card Set(s)>"
                    )
                stats["unmapped"] += 1
            sub = "parallel" if family == "p" else "reprint"
            rel_path = f"{dkey}/{sub}/{code}{suffix}.png"
            dest = ASSETS_ROOT / dkey / sub / f"{code}{suffix}.png"
            if not dry_run:
                dest.parent.mkdir(parents=True, exist_ok=True)
                folders_created.add(str(dest.parent))
                dest.write_bytes(data)

            if family == "p":
                stats["fetched_p"] += 1
            else:
                stats["fetched_r"] += 1

            log_lines.append(
                f"[FETCHED] {code} | {suffix} | {dkey} | {rel_path}"
            )

            if not dry_run:
                vid = variant_row_id(conn, code, vkey)
                if vid:
                    conn.execute(
                        """
                        UPDATE card_variants SET
                          image_path = ?,
                          official_provenance = ?,
                          distribution_product_key = ?,
                          updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            rel_path.replace("\\", "/"),
                            prov or None,
                            dkey,
                            _utc_now(),
                            vid,
                        ),
                    )
                    stats["db_updates"] += 1
                else:
                    log_lines.append(
                        f"[SKIP] {code} | {suffix} | no_variant_row variant_key={vkey!r}"
                    )
                    stats["skipped"] += 1

            # continue probing next n

    for code in codes:
        try:
            process_family(code, "p")
            process_family(code, "r")
        except Exception as e:
            log_lines.append(f"[ERROR] {code} | loop | {e!r}")
            stats["errors"] += 1
        if not dry_run:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    if not dry_run:
        conn.commit()
    conn.close()

    log_lines.append(f"=== END {_utc_now()} ===")
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    summary = [
        f"parallel_reprint_fetch_summary {_utc_now()}",
        f"limit_cards={limit_cards} dry_run={dry_run}",
        f"fetched_parallel_images={stats['fetched_p']}",
        f"fetched_reprint_images={stats['fetched_r']}",
        f"skipped_events={stats['skipped']}",
        f"resume_skipped={stats['resume_skipped']}",
        f"unmapped_events={stats['unmapped']}",
        f"errors={stats['errors']}",
        f"db_updates={stats['db_updates']}",
        "",
        "unique_unmapped_provenance_literals:",
    ]
    for s in sorted(unmapped_strings):
        summary.append(f"  - {s!r}")
    summary.append("")
    summary.append(f"folders_touched_count={len(folders_created)}")
    for p in sorted(folders_created)[:200]:
        summary.append(f"  {p}")
    if len(folders_created) > 200:
        summary.append("  ... truncated ...")
    SUMMARY_PATH.write_text("\n".join(summary) + "\n", encoding="utf-8")

    return {"stats": stats, "unmapped": sorted(unmapped_strings), "codes": len(codes)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="Max distinct canonical codes")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="No disk writes, no DB commit, no HTTP (series or CDN)",
    )
    args = ap.parse_args()
    out = run_fetch(limit_cards=args.limit, dry_run=args.dry_run)
    print(json.dumps(out["stats"], indent=2))
    print("codes", out["codes"], "unmapped_literals", len(out["unmapped"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
