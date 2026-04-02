"""
Fix generic release_set_name values by scraping Card Set(s) from Bandai EN cardlist modals.

For each distribution_product_key, loads the correct *series* page (?series=<id>), finds the
card modal by id, and reads only the div.getInfo block whose h3 is Card Set(s).

Default: DRY RUN. Use --commit to UPDATE card_variants.release_set_name only.

HTTP: one GET per key (?series=...). 2 second delay between requests.
"""

from __future__ import annotations

import argparse
import html as html_lib
import re
import sqlite3
import sys
import time
from html import unescape
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "card_catalog.db"
LIST_BASE = "https://en.onepiece-cardgame.com/cardlist/"
REQUEST_DELAY_SEC = 2.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MiruFixReleaseSetNames/1.1)",
    "Accept-Language": "en-US,en;q=0.9",
}

GENERIC_LITERALS = frozenset(
    {
        "Promotion Card",
        "Other Product Card",
        "Promotion card",
        "One Piece Promotion Cards",
    }
)


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def plain_product_name(raw_html: str) -> str:
    return re.sub(r"\s+", " ", unescape(strip_tags(raw_html))).strip()


def extract_card_set_from_getinfo(get_info_div: Any) -> str | None:
    """Only the Card Set(s) block: h3 mentions Card Set(s), value from following siblings."""
    h3 = get_info_div.find("h3")
    if not h3:
        return None
    lab = h3.get_text(" ", strip=True)
    if "card set" not in lab.lower():
        return None
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
    s = re.sub(r"\s+", " ", s)
    return html_lib.unescape(s).strip() if s else None


def card_set_from_modal_element(modal_el: Any) -> str | None:
    """Inside dl.modalCol / div.modalCol: only div.getInfo that is the Card Set(s) row."""
    if not modal_el:
        return None
    for gi in modal_el.select("div.getInfo"):
        h3 = gi.find("h3")
        if not h3:
            continue
        if "card set" not in h3.get_text(" ", strip=True).lower():
            continue
        got = extract_card_set_from_getinfo(gi)
        if got:
            return got
    return None


def discover_modal_ids_for_code(soup: BeautifulSoup, canonical_code: str) -> list[str]:
    """Ids present in DOM: code_pN, code_rN, code."""
    code = re.escape(canonical_code.strip())
    pat = re.compile(rf"^{code}_(p|r)(\d+)$")
    found: list[tuple[int, str, int]] = []
    for el in soup.select("[id]"):
        i = el.get("id") or ""
        m = pat.match(i)
        if m:
            kind = 0 if m.group(1) == "p" else 1
            found.append((kind, i, int(m.group(2))))
    found.sort(key=lambda x: (x[0], x[2]))
    ordered = [x[1] for x in found]
    bare = canonical_code.strip()
    if soup.find(id=bare):
        ordered.append(bare)
    return ordered


def card_set_for_modal_id(soup: BeautifulSoup, modal_id: str) -> str | None:
    el = soup.find(id=modal_id)
    if not el:
        return None
    return card_set_from_modal_element(el)


def refine_orfc_champion_modal(
    soup: BeautifulSoup,
    distribution_key: str,
    canonical_code: str,
    scraped: str | None,
    used_modal: str | None,
) -> tuple[str | None, str | None]:
    """
    ORFC* distribution keys correspond to Online Regional Champion / Finalist lines on the
    Promotion Card series page (569901). The catalog MIN(print_id) may point at _p2 (Finalist)
    while the product key targets the Champion parallel — prefer the modal whose Card Set
    contains 'Online Regional Champion' when present.
    """
    if not scraped or not re.match(r"^ORFC\d+S\d+$", distribution_key.strip().upper()):
        return scraped, used_modal
    code = canonical_code.strip()
    if "online regional champion" in scraped.lower():
        return scraped, used_modal
    candidates: list[str] = []
    for el in soup.select("[id]"):
        i = (el.get("id") or "").strip()
        if i.startswith(f"{code}_") or i == code:
            candidates.append(i)
    candidates.sort()
    for mid in candidates:
        cs = card_set_for_modal_id(soup, mid)
        if cs and "online regional champion" in cs.lower():
            return cs, mid
    return scraped, used_modal


def try_modal_ids_in_order(
    soup: BeautifulSoup, canonical_code: str, preferred_modal_id: str | None
) -> tuple[str | None, str | None]:
    """
    Try preferred id first (DB print_id), then _p1, _r1, bare, then remaining pN/rN numerically.
    Returns (card_set_value, modal_id_used).
    """
    code = canonical_code.strip()
    tried: list[str] = []
    if preferred_modal_id and preferred_modal_id.strip():
        pid = preferred_modal_id.strip()
        if pid.startswith(code):
            tried.append(pid)
    order = [f"{code}_p1", f"{code}_r1", code]
    for x in order:
        if x not in tried:
            tried.append(x)
    for mid in discover_modal_ids_for_code(soup, code):
        if mid not in tried:
            tried.append(mid)

    seen: set[str] = set()
    for mid in tried:
        if mid in seen:
            continue
        seen.add(mid)
        cs = card_set_for_modal_id(soup, mid)
        if cs:
            return cs, mid
    return None, None


def is_generic_release_name(name: str) -> bool:
    s = (name or "").strip()
    if not s:
        return True
    if s in GENERIC_LITERALS:
        return True
    low = s.lower()
    if "other product" in low:
        return True
    if "promotion" in low and "one piece promotion cards" in low:
        return True
    if low in ("promotion card", "promotion"):
        return True
    return False


def is_series_name_only_placeholder(name: str) -> bool:
    """
    Reject values that are only a main-series booster title like '-ROMANCE DAWN- [OP01]'
    without being tied to a promo / anniversary / event product line.
    We allow long descriptive names and any string containing 'Anniversary', 'Regional',
    'Champion', 'Finalist', 'Participation', 'Event', 'Tournament', etc.
    """
    s = (name or "").strip()
    if not s:
        return True
    low = s.lower()
    if any(
        k in low
        for k in (
            "anniversary",
            "regional",
            "champion",
            "finalist",
            "participation",
            "tournament",
            "event pack",
            "celebration",
            "premium",
            "collection",
            "starter deck",
            "extra booster",
        )
    ):
        return False
    # Typical booster pack line: -TITLE- [OPxx] with short title
    if re.match(r"^-.+-\s*\[(?:OP|EB|ST|PRB)[-]?\w+\]$", s.strip()):
        return True
    return False


def canonical_prefix(canonical_code: str) -> str:
    c = canonical_code.strip().upper()
    if not c:
        return ""
    return c.split("-", 1)[0]


def bracket_search_needles(prefix: str) -> list[str]:
    p = prefix.upper()
    needles: list[str] = []
    m = re.match(r"^(OP)(\d+)$", p)
    if m:
        n = m.group(2)
        needles.extend((f"[OP-{n.zfill(2)}]", f"[OP-{n}]", f"[OP{n.zfill(2)}]"))
    m = re.match(r"^(EB)(\d+)$", p)
    if m:
        n = m.group(2)
        needles.extend((f"[EB-{n.zfill(2)}]", f"[EB-{n}]", f"[EB{n.zfill(2)}]"))
    m = re.match(r"^(ST)(\d+)$", p)
    if m:
        n = m.group(2)
        needles.extend((f"[ST-{n}]", f"[ST{n}]", f"[ST-{n.zfill(2)}]"))
    m = re.match(r"^(PRB)(\d+)$", p)
    if m:
        n = m.group(2)
        needles.extend((f"[PRB-{n}]", f"[PRB-{n.zfill(2)}]"))
    if not needles and p:
        needles.append(f"[{p}]")
    return needles


def resolve_series_id_from_scrape_names(
    old_names: str, scrape_rows: list[tuple[str, str]]
) -> str | None:
    """
    Map generic GROUP_CONCAT(old release_set_name) to the Bandai bucket series used for
    'Other Product Card' / 'Promotion card' single-row listings in bandai_cardlist_scrape.
    """
    o = (old_names or "").lower()
    if "other product" in o:
        for sid, raw in scrape_rows:
            if plain_product_name(raw).lower() == "other product card":
                return sid
    if "promotion" in o and "other product" not in o:
        for sid, raw in scrape_rows:
            pl = plain_product_name(raw).lower()
            if pl == "promotion card" or pl == "promotion":
                return sid
    return None


def resolve_series_id_bracket_fallback(
    prefix: str, scrape_rows: list[tuple[str, str]]
) -> str | None:
    needles = bracket_search_needles(prefix)
    if not needles:
        return None
    for sid, raw_name in scrape_rows:
        plain = plain_product_name(raw_name)
        plain_compact = re.sub(r"\s+", "", plain)
        for nd in needles:
            if nd.replace(" ", "") in plain_compact:
                return sid
    return None


def resolve_series_id(
    old_names: str, prefix: str, scrape_rows: list[tuple[str, str]]
) -> str | None:
    sid = resolve_series_id_from_scrape_names(old_names, scrape_rows)
    if sid:
        return sid
    return resolve_series_id_bracket_fallback(prefix, scrape_rows)


def http_get(url: str) -> tuple[int, str, str]:
    req = Request(url, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=60) as resp:
            raw = resp.read()
            enc = resp.headers.get_content_charset() or "utf-8"
            return resp.status, raw.decode(enc, errors="replace"), ""
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body, str(e)
    except (URLError, OSError) as e:
        return -1, "", str(e)


_first_http = True


def polite_get(url: str) -> tuple[int, str, str]:
    global _first_http
    if not _first_http:
        time.sleep(REQUEST_DELAY_SEC)
    _first_http = False
    return http_get(url)


def load_targets(conn: sqlite3.Connection) -> list[tuple[str, str, str, str]]:
    """distribution_product_key, representative canonical_code, print_id, old_names."""
    rows = conn.execute(
        """
        WITH mc AS (
          SELECT cv.distribution_product_key AS dk,
                 MIN(c.canonical_code) AS min_code
          FROM card_variants cv
          JOIN cards c ON c.id = cv.card_id
          WHERE cv.distribution_product_key IS NOT NULL
            AND cv.distribution_product_key NOT LIKE '!_unclassified%' ESCAPE '!'
            AND (
              cv.release_set_name IN ('Promotion Card', 'Other Product Card', 'Promotion card',
                                      'One Piece Promotion Cards')
              OR cv.release_set_name LIKE '%Promotion%'
              OR cv.release_set_name LIKE '%Other Product%'
            )
          GROUP BY cv.distribution_product_key
        )
        SELECT mc.dk,
               c.canonical_code,
               MIN(cv.print_id) AS print_id,
               GROUP_CONCAT(DISTINCT cv.release_set_name) AS old_names
        FROM mc
        JOIN card_variants cv ON cv.distribution_product_key = mc.dk
        JOIN cards c ON c.id = cv.card_id AND c.canonical_code = mc.min_code
        GROUP BY mc.dk, c.canonical_code
        ORDER BY mc.dk
        """
    ).fetchall()
    out: list[tuple[str, str, str, str]] = []
    for r in rows:
        out.append((str(r[0]), str(r[1] or ""), str(r[2] or ""), str(r[3] or "")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix generic release_set_name from Bandai EN cardlist modals.")
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Apply UPDATEs to card_variants.release_set_name (default: dry run).",
    )
    ap.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to card_catalog.db (default: {DEFAULT_DB})",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process at most N keys (0 = all).",
    )
    args = ap.parse_args()
    db_path: Path = args.db
    if not db_path.is_file():
        print(f"FAILED: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    targets = load_targets(conn)
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]
    scrape_rows = conn.execute(
        "SELECT bandai_series_id, product_name FROM bandai_cardlist_scrape ORDER BY bandai_series_id"
    ).fetchall()

    log_lines: list[str] = []
    updated = 0
    skipped = 0

    for dkey, canonical_code, print_id, old_names in targets:
        prefix = canonical_prefix(canonical_code)
        sid = resolve_series_id(old_names, prefix, scrape_rows)
        if not sid:
            skipped += 1
            log_lines.append(
                f"SKIP | {dkey} | {old_names} | {canonical_code} | "
                f"reason=no_bandai_series_id_for_prefix={prefix!r}_and_scrape_buckets"
            )
            continue

        series_url = f"{LIST_BASE}?series={sid}"
        st, body, err = polite_get(series_url)
        if st != 200 or not body:
            skipped += 1
            log_lines.append(
                f"SKIP | {dkey} | {old_names} | {canonical_code} | "
                f"reason=series_page_HTTP_{st}_err={err!r}"
            )
            continue

        soup = BeautifulSoup(body, "html.parser")
        preferred = print_id if print_id and print_id.strip() else None
        scraped, used_modal = try_modal_ids_in_order(soup, canonical_code, preferred)
        scraped, used_modal = refine_orfc_champion_modal(
            soup, dkey, canonical_code, scraped, used_modal
        )

        if not scraped:
            skipped += 1
            log_lines.append(
                f"SKIP | {dkey} | {old_names} | {canonical_code} | "
                f"reason=no_modal_or_empty_Card_Set_modal={used_modal!r}_series={sid}"
            )
            continue

        if is_generic_release_name(scraped):
            skipped += 1
            log_lines.append(
                f"SKIP | {dkey} | {old_names} | {canonical_code} | "
                f"reason=generic_placeholder_after_parse_modal={used_modal!r}"
            )
            continue

        if is_series_name_only_placeholder(scraped):
            skipped += 1
            log_lines.append(
                f"SKIP | {dkey} | {old_names} | {canonical_code} | "
                f"reason=series_booster_line_only_not_product_line_value={scraped!r}"
            )
            continue

        log_lines.append(
            f"OK | {dkey} | {old_names} | {scraped} | {canonical_code} | source={sid}"
        )

        if args.commit:
            conn.execute(
                """
                UPDATE card_variants
                SET release_set_name = ?
                WHERE distribution_product_key = ?
                """,
                (scraped, dkey),
            )
        updated += 1

    if args.commit:
        conn.commit()
    conn.close()

    print("", flush=True)
    print("=== Miru fix_release_set_names ===", flush=True)
    print(f"Mode: {'COMMIT' if args.commit else 'DRY RUN (no DB writes)'}", flush=True)
    print(f"Database: {db_path}", flush=True)
    print(f"Total keys processed: {len(targets)}", flush=True)
    print(f"Updated count: {updated}", flush=True)
    print(f"Skipped count: {skipped}", flush=True)
    print("", flush=True)
    print("=== Full log ===", flush=True)
    for line in log_lines:
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
