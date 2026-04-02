#!/usr/bin/env python3
"""
Populate P-series promo rows in card_catalog.db from the official EN cardlist HTML.

Promo modals live on the Bandai promo series page (569901). Only a fixed target list
of canonical codes is ingested (cards that were missing card_type in the catalog).

Usage (from repo root):
  python tools/miru_ingest_promo_official_cardlist.py [--dry-run] [--commit]

Default: parse only (no DB writes). Pass --commit to apply updates. --dry-run forces no writes
even if --commit is passed.
"""

from __future__ import annotations

import argparse
import html as html_module
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "card_catalog.db"

PROMO_CARDLIST_URL = "https://en.onepiece-cardgame.com/cardlist/?series=569901"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 30 codes: nine explicit promos plus P-108…P-134 range with gaps matching prior catalog gaps
TARGET_CODES: frozenset[str] = frozenset(
    {
        "P-000",
        "P-064",
        "P-066",
        "P-067",
        "P-080",
        "P-086",
        "P-087",
        "P-094",
        "P-095",
        "P-108",
        "P-109",
        "P-114",
        "P-115",
        "P-116",
        "P-118",
        "P-119",
        "P-120",
        "P-122",
        "P-123",
        "P-124",
        "P-125",
        "P-126",
        "P-127",
        "P-128",
        "P-129",
        "P-130",
        "P-131",
        "P-132",
        "P-133",
        "P-134",
    }
)

# Zero-padded P-NNN only (e.g. P-064), not parallel _pN variants
BASE_ID_RE = re.compile(r"^P-\d{3}$")


def _fetch_promo_html() -> str:
    req = urllib.request.Request(PROMO_CARDLIST_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read().decode("utf-8", "replace")


def _text_after_h3(block: str, heading: str) -> str:
    """Extract inner text after <h3>heading</h3> until next tag close."""
    m = re.search(
        rf"<h3>{re.escape(heading)}</h3>\s*(.*?)(?:</div>|</dd>)",
        block,
        flags=re.I | re.DOTALL,
    )
    if not m:
        return ""
    raw = m.group(1)
    raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    raw = re.sub(r"<[^>]+>", "", raw)
    return html_module.unescape(raw).replace("\xa0", " ").strip()


def _parse_modal_block(block: str) -> dict[str, str]:
    """Parse one <dl class=\"modalCol\" id=\"P-NNN\">...</dl> inner HTML."""
    name_m = re.search(r'<div class="cardName">\s*([^<]+?)\s*</div>', block)
    card_name = html_module.unescape(name_m.group(1).strip()) if name_m else ""

    info_m = re.search(
        r'<div class="infoCol">\s*(.*?)\s*</div>',
        block,
        re.DOTALL,
    )
    rarity = ""
    card_type_raw = ""
    if info_m:
        spans = re.findall(r"<span>([^<]*)</span>", info_m.group(1))
        if len(spans) >= 3:
            rarity = spans[1].strip()
            card_type_raw = spans[2].strip()

    life = _text_after_h3(block, "Life")
    cost = _text_after_h3(block, "Cost")
    power = _text_after_h3(block, "Power")
    counter = _text_after_h3(block, "Counter")
    color = _text_after_h3(block, "Color")
    attr_i = re.search(r'<div class="attribute">.*?<i>([^<]*)</i>', block, re.DOTALL)
    attribute = html_module.unescape(attr_i.group(1).strip()) if attr_i else ""

    feat_m = re.search(
        r'<div class="feature">\s*<h3>Type</h3>\s*(.*?)\s*</div>',
        block,
        re.DOTALL,
    )
    traits = ""
    if feat_m:
        traits = re.sub(r"<[^>]+>", "", feat_m.group(1))
        traits = html_module.unescape(traits).strip()

    effect = ""
    trigger = ""
    eff_m = re.search(
        r'<div class="text">\s*<h3>Effect</h3>\s*(.*?)\s*</div>',
        block,
        re.DOTALL,
    )
    if eff_m:
        body = eff_m.group(1)
        raw = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
        raw = re.sub(r"<[^>]+>", "", raw)
        effect = html_module.unescape(raw).strip()

    trig_m = re.search(
        r'<div class="trigger">\s*<h3>Trigger</h3>\s*(.*?)\s*</div>',
        block,
        re.DOTALL,
    )
    if trig_m:
        t_raw = re.sub(r"<br\s*/?>", "\n", trig_m.group(1), flags=re.I)
        t_raw = re.sub(r"<[^>]+>", "", t_raw)
        trigger = html_module.unescape(t_raw).strip()

    if power == "-":
        power = ""
    if counter == "-":
        counter = ""

    return {
        "card_name": card_name,
        "rarity": rarity,
        "card_type_raw": card_type_raw,
        "cost": cost if cost else "",
        "life": life if life else "",
        "power": power,
        "counter": counter,
        "color": color,
        "attribute": attribute,
        "traits": traits,
        "effect_text": effect,
        "trigger_text": trigger,
    }


def _title_case_card_type(raw: str) -> str:
    r = (raw or "").strip().upper()
    mapping = {
        "LEADER": "Leader",
        "CHARACTER": "Character",
        "EVENT": "Event",
        "STAGE": "Stage",
        "DON!!": "DON!!",
    }
    return mapping.get(r, raw.strip().title() if raw else "")


def parse_promo_modals(html: str) -> dict[str, dict[str, str]]:
    """Return canonical_code -> field dict for target P-NNN modals present in HTML."""
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(
        r'<dl class="modalCol" id="(P-\d{3})">(.*?)</dl>\s*',
        html,
        re.DOTALL,
    ):
        cid = m.group(1)
        if cid not in TARGET_CODES:
            continue
        if not BASE_ID_RE.match(cid):
            continue
        inner = m.group(2)
        parsed = _parse_modal_block(inner)
        card_type = _title_case_card_type(parsed.pop("card_type_raw", ""))
        parsed["card_type"] = card_type
        if card_type == "Leader":
            parsed["cost"] = ""
        else:
            parsed["life"] = ""
        if cid not in out:
            out[cid] = parsed
    return out


def _coerce_cost_int(cost_str: str, card_type: str) -> int | None:
    if card_type == "Leader":
        return None
    s = (cost_str or "").strip()
    if not s or s == "-":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _snippet_around_first_dl(html: str, before: int = 120, after: int = 500) -> str:
    lower = html.lower()
    idx = lower.find("<dl")
    if idx == -1:
        return "(no <dl substring found in fetched HTML)"
    start = max(0, idx - before)
    end = min(len(html), idx + after)
    return html[start:end]


def run(*, dry_run: bool) -> tuple[dict[str, int | str], str, list[str]]:
    html = _fetch_promo_html()
    modals = parse_promo_modals(html)
    not_found = sorted(TARGET_CODES - set(modals.keys()))
    stats: dict[str, int | str] = {
        "modals_parsed": len(modals),
        "rows_updated": 0,
        "rows_missing_catalog": 0,
    }
    if dry_run:
        return stats, html, not_found

    set_name = "Promotion Card"
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        for code, fields in sorted(modals.items()):
            row = conn.execute(
                "SELECT id FROM cards WHERE canonical_code = ?",
                (code,),
            ).fetchone()
            if not row:
                stats["rows_missing_catalog"] = int(stats["rows_missing_catalog"]) + 1
                continue
            num = code.split("-", 1)[1]
            cost_val = _coerce_cost_int(fields.get("cost") or "", fields["card_type"])
            conn.execute(
                """
                UPDATE cards SET
                    set_code = ?,
                    card_number = ?,
                    set_name = ?,
                    card_name = ?,
                    rarity = ?,
                    color = ?,
                    card_type = ?,
                    cost = ?,
                    power = ?,
                    counter = ?,
                    attribute = ?,
                    traits = ?,
                    life = ?,
                    effect_text = ?,
                    trigger_text = ?
                WHERE canonical_code = ?
                """,
                (
                    "P",
                    num,
                    set_name,
                    fields["card_name"],
                    fields["rarity"],
                    fields["color"],
                    fields["card_type"],
                    cost_val,
                    fields["power"],
                    fields["counter"],
                    fields["attribute"],
                    fields["traits"],
                    fields["life"],
                    fields["effect_text"],
                    fields["trigger_text"],
                    code,
                ),
            )
            stats["rows_updated"] = int(stats["rows_updated"]) + 1
        conn.commit()
    finally:
        conn.close()
    return stats, html, not_found


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Ingest target P-series promo cards from official EN cardlist HTML (series 569901)."
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse only; do not write DB (same as default; also cancels writes if combined with --commit).",
    )
    ap.add_argument(
        "--commit",
        action="store_true",
        help="Write updates to card_catalog.db (default without this flag: no writes).",
    )
    args = ap.parse_args()

    if not DB_PATH.is_file():
        print("ERROR: card_catalog.db not found:", DB_PATH, file=sys.stderr)
        return 2

    will_write = args.commit and not args.dry_run
    if not args.commit:
        print(
            "WARNING: No --commit: database will not be modified. "
            "Pass --commit to apply UPDATEs.",
            file=sys.stderr,
        )
    if args.dry_run and args.commit:
        print(
            "NOTE: --dry-run is set; database writes are disabled despite --commit.",
            file=sys.stderr,
        )

    dry_run = not will_write
    stats, html, not_found = run(dry_run=dry_run)

    print("modals_parsed:", stats["modals_parsed"])
    print("not_found:", not_found)
    if stats["modals_parsed"] == 0:
        print("--- snippet around first <dl> in fetched HTML ---")
        print(_snippet_around_first_dl(html))

    if dry_run:
        print("(dry-run: no database writes)")
    else:
        print("rows_updated:", stats["rows_updated"])
        print("rows_missing_catalog:", stats["rows_missing_catalog"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
