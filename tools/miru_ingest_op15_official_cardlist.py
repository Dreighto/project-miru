#!/usr/bin/env python3
"""
Populate OP15 rows in card_catalog.db from the official EN cardlist HTML.

onepiece-cardgame.dev cards.json currently ends at OP08 (no OP15 rows). The official
site embeds full card text in /cardlist/ HTML modals — this script parses those modals
for ids matching OP15-NNN (base prints only, not parallel _pN variants).

Usage (from repo root):
  python tools/miru_ingest_op15_official_cardlist.py [--dry-run]
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
CARDLIST_URL = "https://en.onepiece-cardgame.com/cardlist/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Base print only: OP15-001, not OP15-001_p1
BASE_ID_RE = re.compile(r'^OP15-\d{3}$')


def _fetch_cardlist_html() -> str:
    req = urllib.request.Request(CARDLIST_URL, headers={"User-Agent": USER_AGENT})
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
    """Parse one <dl class=\"modalCol\" id=\"OP15-NNN\">...</dl> inner HTML."""
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


def parse_op15_modals(html: str) -> dict[str, dict[str, str]]:
    """Return canonical_code -> field dict for base OP15-NNN modals."""
    out: dict[str, dict[str, str]] = {}
    for m in re.finditer(
        r'<dl class="modalCol" id="(OP15-\d{3})">(.*?)</dl>\s*',
        html,
        re.DOTALL,
    ):
        cid = m.group(1)
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


def run(*, dry_run: bool) -> dict[str, int | str]:
    html = _fetch_cardlist_html()
    modals = parse_op15_modals(html)
    stats: dict[str, int | str] = {
        "modals_parsed": len(modals),
        "rows_updated": 0,
        "rows_missing_catalog": 0,
    }
    if dry_run:
        return stats

    set_name = "Adventure on Kamis Island"
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
                    "OP15",
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
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest OP15 cards from official EN cardlist HTML.")
    ap.add_argument("--dry-run", action="store_true", help="Parse only; do not write DB.")
    args = ap.parse_args()
    if not DB_PATH.is_file():
        print("ERROR: card_catalog.db not found:", DB_PATH, file=sys.stderr)
        return 2
    stats = run(dry_run=args.dry_run)
    print("modals_parsed:", stats["modals_parsed"])
    if args.dry_run:
        print("(dry-run: no database writes)")
        return 0
    print("rows_updated:", stats["rows_updated"])
    print("rows_missing_catalog:", stats["rows_missing_catalog"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
