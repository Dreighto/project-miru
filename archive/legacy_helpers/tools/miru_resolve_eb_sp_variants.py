#!/usr/bin/env python3
"""
Resolve EB-set SP cards to OP/ST base prints using the official Bandai EN card list
Card Set(s) field plus name matching in card_catalog.db.

Append-only updates to data/verified_variant_mappings.json; run log JSON to
data/eb_sp_resolver_run_v1.txt. Optional DB updates for high-confidence rows only.
"""

from __future__ import annotations

import argparse
import html as html_module
import json
import re
import sqlite3
import ssl
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CATALOG_DB = DATA / "card_catalog.db"
VERIFIED_JSON = DATA / "verified_variant_mappings.json"
RUN_LOG = DATA / "eb_sp_resolver_run_v1.txt"

DEFAULT_EB_SP_CODES = [
    "EB01-003",
    "EB01-023",
    "EB01-057",
    "EB02-028",
    "EB03-003",
    "EB03-018",
    "EB03-024",
    "EB03-026",
    "EB03-031",
    "EB03-042",
    "EB03-045",
    "EB03-053",
    "EB03-055",
    "EB04-003",
    "EB04-039",
]

USER_AGENT = "MiruEBSPResolver/1.0 (+https://local; tcg-watcher catalog sync)"
MIN_REQUEST_INTERVAL_S = 1.0

_last_request_ts = 0.0


def _throttled_get(url: str, timeout: float = 60.0) -> tuple[int, str]:
    global _last_request_ts
    now = time.monotonic()
    wait = MIN_REQUEST_INTERVAL_S - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            _last_request_ts = time.monotonic()
            return resp.getcode(), body
    except HTTPError as e:
        _last_request_ts = time.monotonic()
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body
    except URLError:
        _last_request_ts = time.monotonic()
        raise


def fetch_cardlist_html(code: str) -> tuple[int, str, str]:
    """Returns (status, html, url_used)."""
    primary = (
        f"https://en.onepiece-cardgame.com/cardlist/?search=true&card_code={quote(code)}"
    )
    status, html = _throttled_get(primary)
    if _modal_present(html, code):
        return status, html, primary
    fallback = f"https://en.onepiece-cardgame.com/cardlist/?freewords={quote(code)}"
    status2, html2 = _throttled_get(fallback)
    return status2, html2, fallback


def _modal_present(html: str, code: str) -> bool:
    return bool(
        re.search(
            rf'<dl\s+class="modalCol"\s+id="{re.escape(code)}"\s*>',
            html,
            flags=re.IGNORECASE,
        )
    )


def extract_modal_html(html: str, code: str) -> str | None:
    m = re.search(
        rf'(<dl\s+class="modalCol"\s+id="{re.escape(code)}"\s*>.*?</dl>)',
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return m.group(1) if m else None


def extract_card_name(modal_html: str) -> str | None:
    m = re.search(
        r'<div\s+class="cardName"\s*>([^<]+)</div>',
        modal_html,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    return html_module.unescape(m.group(1).strip())


def extract_card_set_raw(modal_html: str) -> str | None:
    m = re.search(
        r'<div\s+class="getInfo"\s*>\s*<h3>\s*Card Set\(s\)\s*</h3>\s*(.*?)\s*</div>',
        modal_html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return None
    inner = m.group(1)
    inner = re.sub(r"<[^>]+>", " ", inner)
    return html_module.unescape(re.sub(r"\s+", " ", inner).strip())


def bracket_codes(text: str) -> list[str]:
    return re.findall(r"\[([^\]]+)\]", text)


def expand_bandai_codes_to_catalog_set_codes(raw: str) -> list[str]:
    """Map bracket innards like OP-13, EB-02, OP15-EB04 to catalog set_code tokens."""
    s = raw.strip().upper()
    seen: list[str] = []
    m_compound = re.fullmatch(r"OP(\d+)-EB(\d+)", s)
    if m_compound:
        seen.extend([f"OP{m_compound.group(1)}", f"EB{m_compound.group(2)}"])
        return seen
    m_ld = re.fullmatch(r"([A-Z]+)-(\d+)", s)
    if m_ld:
        letters, digits = m_ld.group(1), m_ld.group(2)
        if letters in ("OP", "ST", "EB"):
            seen.append(f"{letters}{digits}")
            return seen
    m_plain = re.fullmatch(r"(OP|ST|EB)(\d+)", s)
    if m_plain:
        seen.append(s)
        return seen
    compact = s.replace("-", "")
    if re.fullmatch(r"(OP|ST|EB)\d+", compact):
        seen.append(compact)
    else:
        seen.append(compact)
    return seen


def normalize_card_name(name: str) -> str:
    n = html_module.unescape(name).lower()
    n = n.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", n)


@dataclass
class ResolveRow:
    variant_canonical_code: str
    status: str
    http_status: int | None
    url_used: str | None
    card_name_bandai: str | None
    card_set_field_raw: str | None
    expanded_set_codes: list[str]
    candidate_base_codes: list[str]
    base_canonical_code: str | None
    reason: str | None


def catalog_candidates(
    conn: sqlite3.Connection,
    set_codes: list[str],
    name_norm: str,
    variant_code: str,
) -> list[tuple[str, str]]:
    """Return (canonical_code, set_code) for OP/ST rows matching name; exclude variant row."""
    out: list[tuple[str, str]] = []
    for sc in set_codes:
        rows = conn.execute(
            "SELECT canonical_code, card_name, set_code FROM cards WHERE set_code = ?",
            (sc,),
        ).fetchall()
        for cc, cn, scc in rows:
            if cc == variant_code:
                continue
            if normalize_card_name(cn) != name_norm:
                continue
            if not str(scc).startswith(("OP", "ST")):
                continue
            out.append((cc, scc))
    dedup: dict[str, tuple[str, str]] = {}
    for cc, scc in out:
        dedup[cc] = (cc, scc)
    return list(dedup.values())


def row_exists_op_st(conn: sqlite3.Connection, canonical: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM cards WHERE canonical_code = ? AND (set_code LIKE 'OP%' OR set_code LIKE 'ST%')",
        (canonical,),
    ).fetchone()
    return r is not None


def resolve_one(
    conn: sqlite3.Connection, code: str, abort_http: list[int] | None = None
) -> ResolveRow:
    abort_http = abort_http or [403, 429]
    try:
        status, html, url_used = fetch_cardlist_html(code)
    except (URLError, OSError) as e:
        return ResolveRow(
            variant_canonical_code=code,
            status="error",
            http_status=None,
            url_used=None,
            card_name_bandai=None,
            card_set_field_raw=None,
            expanded_set_codes=[],
            candidate_base_codes=[],
            base_canonical_code=None,
            reason=f"fetch_failed:{e!r}",
        )

    if status in abort_http:
        return ResolveRow(
            variant_canonical_code=code,
            status="aborted",
            http_status=status,
            url_used=url_used,
            card_name_bandai=None,
            card_set_field_raw=None,
            expanded_set_codes=[],
            candidate_base_codes=[],
            base_canonical_code=None,
            reason="fetch_failed",
        )

    if status >= 400:
        return ResolveRow(
            variant_canonical_code=code,
            status="error",
            http_status=status,
            url_used=url_used,
            card_name_bandai=None,
            card_set_field_raw=None,
            expanded_set_codes=[],
            candidate_base_codes=[],
            base_canonical_code=None,
            reason="fetch_failed",
        )

    modal = extract_modal_html(html, code)
    if not modal:
        return ResolveRow(
            variant_canonical_code=code,
            status="unresolved",
            http_status=status,
            url_used=url_used,
            card_name_bandai=None,
            card_set_field_raw=None,
            expanded_set_codes=[],
            candidate_base_codes=[],
            base_canonical_code=None,
            reason="fetch_failed",
        )

    cname = extract_card_name(modal)
    cset_raw = extract_card_set_raw(modal)
    brackets = bracket_codes(cset_raw or "")
    expanded: list[str] = []
    for b in brackets:
        for x in expand_bandai_codes_to_catalog_set_codes(b):
            if x not in expanded:
                expanded.append(x)

    if not cname:
        return ResolveRow(
            variant_canonical_code=code,
            status="unresolved",
            http_status=status,
            url_used=url_used,
            card_name_bandai=None,
            card_set_field_raw=cset_raw,
            expanded_set_codes=expanded,
            candidate_base_codes=[],
            base_canonical_code=None,
            reason="fetch_failed",
        )

    nn = normalize_card_name(cname)
    cands = catalog_candidates(conn, expanded, nn, code)
    codes = [c[0] for c in cands]

    if len(cands) == 1:
        base_cc = cands[0][0]
        if not row_exists_op_st(conn, base_cc):
            return ResolveRow(
                variant_canonical_code=code,
                status="unresolved",
                http_status=status,
                url_used=url_used,
                card_name_bandai=cname,
                card_set_field_raw=cset_raw,
                expanded_set_codes=expanded,
                candidate_base_codes=codes,
                base_canonical_code=None,
                reason="no_base_found",
            )
        return ResolveRow(
            variant_canonical_code=code,
            status="resolved",
            http_status=status,
            url_used=url_used,
            card_name_bandai=cname,
            card_set_field_raw=cset_raw,
            expanded_set_codes=expanded,
            candidate_base_codes=codes,
            base_canonical_code=base_cc,
            reason=None,
        )

    if len(cands) > 1:
        return ResolveRow(
            variant_canonical_code=code,
            status="unresolved",
            http_status=status,
            url_used=url_used,
            card_name_bandai=cname,
            card_set_field_raw=cset_raw,
            expanded_set_codes=expanded,
            candidate_base_codes=codes,
            base_canonical_code=None,
            reason="ambiguous_name_match",
        )

    return ResolveRow(
        variant_canonical_code=code,
        status="unresolved",
        http_status=status,
        url_used=url_used,
        card_name_bandai=cname,
        card_set_field_raw=cset_raw,
        expanded_set_codes=expanded,
        candidate_base_codes=[],
        base_canonical_code=None,
        reason="no_base_found",
    )


def load_verified() -> dict[str, Any]:
    with open(VERIFIED_JSON, encoding="utf-8") as f:
        return json.load(f)


def save_verified(data: dict[str, Any]) -> None:
    with open(VERIFIED_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def append_results(rows: list[ResolveRow], today: str) -> None:
    data = load_verified()
    mappings: list[dict[str, Any]] = data.setdefault("mappings", [])
    unresolved: list[dict[str, Any]] = data.setdefault("unresolved", [])
    mapped_codes = {m["variant_canonical_code"] for m in mappings}

    for row in rows:
        if row.status == "resolved" and row.base_canonical_code:
            unresolved = [
                u for u in unresolved if u["variant_canonical_code"] != row.variant_canonical_code
            ]
            if row.variant_canonical_code in mapped_codes:
                continue
            mappings.append(
                {
                    "variant_canonical_code": row.variant_canonical_code,
                    "base_canonical_code": row.base_canonical_code,
                    "variant_category": "premium_rarity",
                    "variant_subtype": "sp",
                    "mapping_type": "cross_set",
                    "mapping_confidence": "high",
                    "source": "en.onepiece-cardgame.com automated fetch",
                    "verified_date": today,
                    "notes": (
                        f"Card Set(s) field: {row.card_set_field_raw or ''}. "
                        f"Name match: {row.card_name_bandai or ''}."
                    ),
                }
            )
            mapped_codes.add(row.variant_canonical_code)
        elif row.status in ("unresolved", "error") or row.status == "aborted":
            reason = row.reason or "fetch_failed"
            if row.status == "aborted":
                reason = "fetch_failed"
            u_entry = {
                "variant_canonical_code": row.variant_canonical_code,
                "reason": reason
                if reason
                in ("ambiguous_name_match", "no_base_found", "fetch_failed")
                else "fetch_failed",
                "attempted_home_set": ",".join(row.expanded_set_codes)
                if row.expanded_set_codes
                else "",
                "candidates": row.candidate_base_codes,
                "notes": row.card_set_field_raw or "",
            }
            unresolved = [u for u in unresolved if u["variant_canonical_code"] != row.variant_canonical_code]
            unresolved.append(u_entry)

    data["unresolved"] = unresolved
    save_verified(data)


def apply_db_updates(resolved: list[ResolveRow], conn: sqlite3.Connection) -> list[str]:
    """UPDATE cards for high-confidence resolutions. Returns list of updated codes."""
    updated: list[str] = []
    for row in resolved:
        if row.status != "resolved" or not row.base_canonical_code:
            continue
        cur = conn.execute(
            "SELECT canonical_code FROM cards WHERE canonical_code = ?",
            (row.base_canonical_code,),
        ).fetchone()
        if not cur:
            continue
        conn.execute(
            """
            UPDATE cards SET
              base_card_id = ?,
              is_variant = 1,
              variant_category = 'premium_rarity',
              variant_subtype = 'sp',
              is_premium_variant = 1
            WHERE canonical_code = ?
            """,
            (row.base_canonical_code, row.variant_canonical_code),
        )
        updated.append(row.variant_canonical_code)
    conn.commit()
    return updated


def orphan_variant_count(conn: sqlite3.Connection) -> int:
    r = conn.execute(
        """
        SELECT COUNT(*) FROM card_variants cv
        LEFT JOIN cards c ON cv.card_id = c.id
        WHERE c.id IS NULL
        """
    ).fetchone()
    return int(r[0]) if r else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "codes",
        nargs="*",
        help="EB SP canonical codes (default: built-in list of 15)",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Do not write card_catalog.db updates",
    )
    args = parser.parse_args()
    codes = args.codes if args.codes else DEFAULT_EB_SP_CODES

    today = date.today().isoformat()
    conn = sqlite3.connect(str(CATALOG_DB))
    conn.row_factory = sqlite3.Row

    rows: list[ResolveRow] = []
    aborted = False
    for code in codes:
        row = resolve_one(conn, code)
        rows.append(row)
        if row.status == "aborted":
            aborted = True
            break

    payload = {
        "run_date": today,
        "aborted_early": aborted,
        "rows": [asdict(r) for r in rows],
    }
    RUN_LOG.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if aborted:
        conn.close()
        return 2

    resolved = [r for r in rows if r.status == "resolved" and r.base_canonical_code]
    append_results(rows, today)

    if not args.no_db and resolved:
        apply_db_updates(resolved, conn)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
