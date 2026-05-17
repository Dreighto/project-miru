"""PRO-904 Phase 2: Diff bandai_op01_crawl.json vs DB card_variants. READ ONLY."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CRAWL_PATH = ROOT / "data" / "bandai_op01_crawl.json"
DB_PATH = ROOT / "data" / "card_catalog.db"
REPORT_PATH = ROOT / "data" / "bandai_op01_diff_report.md"

BANDAI_PRINT_RE = re.compile(r"^OP01-\d{3}(_[a-z]\d+)?$")


def load_crawl() -> list[dict[str, Any]]:
    data = json.loads(CRAWL_PATH.read_text(encoding="utf-8"))
    return data["printings"]


def load_db_variants() -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT c.canonical_code AS card_number,
               cv.print_id,
               cv.variant_label,
               cv.release_set_code,
               cv.release_set_name,
               cv.image_url,
               cv.official_provenance
        FROM card_variants cv
        JOIN cards c ON cv.card_id = c.id
        WHERE c.canonical_code LIKE 'OP01-%'
        ORDER BY c.canonical_code, cv.print_id
        """
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def diff() -> dict[str, Any]:
    crawl = load_crawl()
    db = load_db_variants()

    crawl_keys: dict[tuple[str, str], dict[str, Any]] = {}
    for row in crawl:
        key = (row["card_number"], row["full_id"])
        crawl_keys[key] = row

    db_bandai_format: dict[tuple[str, str], dict[str, Any]] = {}
    db_synthetic: list[dict[str, Any]] = []
    for row in db:
        pid = row["print_id"]
        if BANDAI_PRINT_RE.match(pid):
            db_bandai_format[(row["card_number"], pid)] = row
        else:
            db_synthetic.append(row)

    matched_keys = sorted(set(crawl_keys) & set(db_bandai_format))
    candidate_missing = sorted(set(crawl_keys) - set(db_bandai_format))
    candidate_phantom = sorted(set(db_bandai_format) - set(crawl_keys))

    return {
        "crawl_total": len(crawl),
        "db_total": len(db),
        "db_bandai_format": len(db_bandai_format),
        "db_synthetic": len(db_synthetic),
        "matched_count": len(matched_keys),
        "candidate_missing_count": len(candidate_missing),
        "candidate_phantom_count": len(candidate_phantom),
        "matched": matched_keys,
        "candidate_missing": [
            {"card_number": k[0], "print_id": k[1], **crawl_keys[k]} for k in candidate_missing
        ],
        "candidate_phantom": [
            {"card_number": k[0], "print_id": k[1], **db_bandai_format[k]}
            for k in candidate_phantom
        ],
        "synthetic_legacy_rows": db_synthetic,
    }


def render_report(d: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# PRO-904 — OP01 Bandai crawl vs DB diff")
    lines.append("")
    lines.append("## Counts")
    lines.append("")
    lines.append(f"- Crawl printings total: **{d['crawl_total']}**")
    lines.append(f"- DB variants total (OP01-%): **{d['db_total']}**")
    lines.append(
        f"  - Bandai-format print_id (`OP01-NNN` / `OP01-NNN_pN`): **{d['db_bandai_format']}**"
    )
    lines.append(f"  - Synthetic `::`-style print_id (legacy): **{d['db_synthetic']}**")
    lines.append("")
    lines.append("## Diff (on Bandai-format keys)")
    lines.append("")
    lines.append(f"- **Matched** (in both crawl and DB): **{d['matched_count']}**")
    lines.append(
        f"- **Candidate missing** (in crawl, not in DB): **{d['candidate_missing_count']}**"
    )
    lines.append(
        f"- **Candidate phantom** (in DB Bandai-format, not in crawl): **{d['candidate_phantom_count']}**"
    )
    lines.append(
        f"- **Synthetic legacy** (DB non-Bandai-format, not part of diff key): **{d['db_synthetic']}**"
    )
    lines.append("")
    if d["candidate_missing"]:
        lines.append("## Candidate missing (Bandai has, DB doesn't)")
        lines.append("")
        lines.append("| card_number | print_id | rarity | card_set | image_url |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in d["candidate_missing"]:
            lines.append(
                f"| {r['card_number']} | {r['print_id']} | {r.get('rarity') or ''} | "
                f"{r.get('card_set') or ''} | {r.get('image_url') or ''} |"
            )
        lines.append("")
    if d["candidate_phantom"]:
        lines.append("## Candidate phantom (DB Bandai-format has, Bandai doesn't)")
        lines.append("")
        lines.append("| card_number | print_id | variant_label | release_set | image_url |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in d["candidate_phantom"]:
            lines.append(
                f"| {r['card_number']} | {r['print_id']} | {r.get('variant_label') or ''} | "
                f"{r.get('release_set_code') or ''} | {r.get('image_url') or ''} |"
            )
        lines.append("")
    if d["synthetic_legacy_rows"]:
        lines.append("## Synthetic legacy rows (DB non-Bandai-format print_id)")
        lines.append("")
        lines.append(
            f"_{d['db_synthetic']} rows. Not part of the (card_number, print_id) diff key —"
        )
        lines.append("these are legacy `::`-style entries that the three OP01 remediation passes")
        lines.append("will reconcile separately. Listed here for visibility._")
        lines.append("")
        lines.append("| card_number | print_id | variant_label | release_set_name |")
        lines.append("| --- | --- | --- | --- |")
        for r in d["synthetic_legacy_rows"]:
            lines.append(
                f"| {r['card_number']} | {r['print_id']} | {r.get('variant_label') or ''} | "
                f"{r.get('release_set_name') or ''} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not CRAWL_PATH.exists():
        print(f"FAIL: crawl file missing at {CRAWL_PATH}", file=sys.stderr)
        return 2
    if not DB_PATH.exists():
        print(f"FAIL: DB missing at {DB_PATH}", file=sys.stderr)
        return 2

    d = diff()
    REPORT_PATH.write_text(render_report(d), encoding="utf-8")
    summary = {
        "crawl_total": d["crawl_total"],
        "db_total": d["db_total"],
        "db_bandai_format": d["db_bandai_format"],
        "db_synthetic": d["db_synthetic"],
        "matched": d["matched_count"],
        "candidate_missing": d["candidate_missing_count"],
        "candidate_phantom": d["candidate_phantom_count"],
        "report_path": str(REPORT_PATH),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
