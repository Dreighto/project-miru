"""
Bandai EN cardlist provenance recon: Card Set(s) strings for _p1 / _r1 modals only.

Read-only DB; HTTP only (no images saved). Writes tools/provenance_recon.txt.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "data" / "card_catalog.db"
OUT_PATH = Path(__file__).resolve().parent / "provenance_recon.txt"
BASE = "https://en.onepiece-cardgame.com/cardlist/"
DELAY = 3.0
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MiruProvenanceRecon/1.0; research)",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_series(series_id: str) -> tuple[int | None, str, str]:
    """Returns (status_code, body_text, error_note). status None on network error."""
    url = f"{BASE}?series={series_id}"
    req = Request(url, headers=HEADERS, method="GET")
    try:
        with urlopen(req, timeout=45) as resp:
            raw = resp.read()
            enc = resp.headers.get_content_charset() or "utf-8"
            text = raw.decode(enc, errors="replace")
            return resp.status, text, ""
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body, str(e)
    except URLError as e:
        return None, "", str(e)
    except OSError as e:
        return None, "", str(e)


def polite_get(series_id: str) -> tuple[int | None, str, str]:
    time.sleep(DELAY)
    code, text, err = fetch_series(series_id)
    if code == 429:
        time.sleep(60.0)
        code, text, err = fetch_series(series_id)
        if code == 429:
            return code, text, "429_after_retry"
    return code, text, err


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
    s = re.sub(r"\s+", " ", s)
    return s


def parse_modals(html: str) -> list[tuple[str, str, str]]:
    """List of (card_id, suffix _p1|_r1, provenance_string)."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, str, str]] = []
    for el in soup.select("dl.modalCol, div.modalCol"):
        cid = (el.get("id") or "").strip()
        if not cid:
            continue
        if cid.endswith("_p1"):
            suf = "_p1"
        elif cid.endswith("_r1"):
            suf = "_r1"
        else:
            continue
        prov = ""
        for gi in el.select("div.getInfo"):
            prov = extract_card_set_literal(gi)
            if prov:
                break
        out.append((cid, suf, prov or ""))
    return out


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FAILED: missing {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT bandai_series_id, product_name FROM bandai_cardlist_scrape ORDER BY bandai_series_id"
    ).fetchall()
    conn.close()

    prov_counter: Counter[str] = Counter()
    example_for: dict[str, str] = {}
    series_stats: list[tuple[str, int, int, int]] = []  # id, p1, r1, empty_prov
    errors: list[str] = []

    total_p1 = 0
    total_r1 = 0

    for r in rows:
        sid = str(r["bandai_series_id"])
        code, html, err = polite_get(sid)
        if code != 200 or not html:
            errors.append(f"series={sid} HTTP={code} err={err!r}")
            series_stats.append((sid, 0, 0, 0))
            continue
        modals = parse_modals(html)
        n_p1 = sum(1 for c, s, _ in modals if s == "_p1")
        n_r1 = sum(1 for c, s, _ in modals if s == "_r1")
        n_empty = sum(1 for c, s, p in modals if not p)
        total_p1 += n_p1
        total_r1 += n_r1
        series_stats.append((sid, n_p1, n_r1, n_empty))

        for cid, suf, prov in modals:
            if not prov:
                continue
            prov_counter[prov] += 1
            if prov not in example_for:
                example_for[prov] = f"{cid} ({suf})"

    lines: list[str] = []
    lines.append("Miru Provenance Recon — Bandai EN cardlist (_p1 / _r1 modals only)")
    lines.append(f"Total unique provenance strings: {len(prov_counter)}")
    lines.append(f"Total series pages checked: {len(rows)}")
    lines.append(f"Total _p1 modals found: {total_p1}")
    lines.append(f"Total _r1 modals found: {total_r1}")
    lines.append("")

    lines.append("UNIQUE PROVENANCE STRINGS FOUND")
    lines.append("================================")
    for s, cnt in sorted(prov_counter.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"[{cnt}] {s}")
    lines.append("")

    lines.append("UNMAPPED EXAMPLES")
    lines.append("=================")
    for s in sorted(prov_counter.keys()):
        lines.append(f"{s!r}")
        lines.append(f"  example: {example_for.get(s, '?')}")
    lines.append("")

    lines.append("SERIES PAGES CHECKED")
    lines.append("====================")
    for sid, n_p1, n_r1, n_empty in series_stats:
        lines.append(
            f"series_id={sid}  _p1_modals={n_p1}  _r1_modals={n_r1}  "
            f"_p1_or_r1_with_empty_CardSet={n_empty}"
        )
    lines.append("")

    lines.append("ERRORS / SKIPPED")
    lines.append("================")
    if not errors:
        lines.append("(none)")
    else:
        for e in errors:
            lines.append(e)

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
