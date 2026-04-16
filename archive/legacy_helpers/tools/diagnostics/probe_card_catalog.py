#!/usr/bin/env python
"""
Read-only diagnostic probe of card_catalog.db.
Generates a self-contained export bundle for external analysis.
"""
from __future__ import annotations

import csv
import io
import os
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "card_catalog.db"

RELEVANCE_TERMS = {
    "card", "catalog", "variant", "image", "asset", "price", "product",
    "tcgplayer", "tcgcsv", "mapping", "rarity", "source", "provenance",
    "subtype", "print", "market", "group", "leader", "set", "deck",
}

def is_relevant(name: str, columns: list[str]) -> bool:
    low = name.lower()
    all_cols = " ".join(c.lower() for c in columns)
    combined = low + " " + all_cols
    return any(t in combined for t in RELEVANCE_TERMS)


def main() -> None:
    if not DB_PATH.exists():
        print(f"FATAL: DB not found at {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(__file__).resolve().parent / f"card_catalog_probe_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    samples_dir = out_dir / "samples"
    samples_dir.mkdir(exist_ok=True)

    # Record DB size and mtime before
    db_stat_before = DB_PATH.stat()

    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    # ── 1. schema.sql ──────────────────────────────────────────────
    rows = conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL ORDER BY type, name"
    ).fetchall()
    schema_lines: list[str] = []
    for r in rows:
        schema_lines.append(f"-- {r['type']}: {r['name']}")
        schema_lines.append(r["sql"].rstrip(";") + ";\n")
    (out_dir / "schema.sql").write_text("\n".join(schema_lines), encoding="utf-8")
    print(f"  schema.sql  ({len(rows)} objects)")

    # ── 2. table list + columns ────────────────────────────────────
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]

    all_columns: dict[str, list[dict]] = {}
    for tbl in tables:
        cols = conn.execute(f"PRAGMA table_info([{tbl}])").fetchall()
        all_columns[tbl] = [dict(c) for c in cols]

    # table_columns.csv
    with open(out_dir / "table_columns.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table_name", "cid", "column_name", "type", "notnull", "default_value", "pk"])
        for tbl in tables:
            for c in all_columns[tbl]:
                w.writerow([tbl, c["cid"], c["name"], c["type"], c["notnull"], c["dflt_value"], c["pk"]])
    print(f"  table_columns.csv  ({sum(len(v) for v in all_columns.values())} columns)")

    # table_counts.csv
    counts: dict[str, int] = {}
    with open(out_dir / "table_counts.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table_name", "row_count"])
        for tbl in tables:
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM [{tbl}]").fetchone()[0]
            except Exception:
                n = -1
            counts[tbl] = n
            w.writerow([tbl, n])
    print(f"  table_counts.csv  ({len(tables)} tables)")

    # ── 3. relevant_tables.txt ─────────────────────────────────────
    relevant: list[str] = []
    for tbl in tables:
        col_names = [c["name"] for c in all_columns[tbl]]
        if is_relevant(tbl, col_names):
            relevant.append(tbl)
    (out_dir / "relevant_tables.txt").write_text("\n".join(relevant) + "\n", encoding="utf-8")
    print(f"  relevant_tables.txt  ({len(relevant)} relevant)")

    # ── 4. sample CSVs for relevant tables ─────────────────────────
    for tbl in relevant:
        try:
            sample_rows = conn.execute(f"SELECT * FROM [{tbl}] LIMIT 200").fetchall()
            if not sample_rows:
                (samples_dir / f"{tbl}_sample.csv").write_text("(empty table)\n", encoding="utf-8")
                continue
            col_names = sample_rows[0].keys()
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(col_names)
            for r in sample_rows:
                w.writerow([r[c] for c in col_names])
            (samples_dir / f"{tbl}_sample.csv").write_text(buf.getvalue(), encoding="utf-8")
        except Exception as exc:
            (samples_dir / f"{tbl}_sample_error.txt").write_text(str(exc), encoding="utf-8")
    print(f"  samples/  ({len(relevant)} files)")

    # ── 5. best-effort optional diagnostics ────────────────────────
    optional_queries: dict[str, str] = {}

    # Detect columns
    def tbl_has(tbl: str, col: str) -> bool:
        return any(c["name"].lower() == col.lower() for c in all_columns.get(tbl, []))

    # The schema links cards → card_variants (card_id FK) and
    # card_variants has tcgplayer_product_id which maps to
    # market_products.market_product_id. printing_market_map is
    # also a bridge table.

    # missing_price_candidates: variants with no tcgplayer price linkage
    if tbl_has("card_variants", "tcgplayer_product_id") and tbl_has("card_variants", "card_id"):
        optional_queries["missing_price_candidates"] = textwrap.dedent("""\
            SELECT c.canonical_code, c.card_name, c.rarity,
                   cv.variant_key, cv.variant_label, cv.is_base,
                   cv.tcgplayer_product_id, cv.tcgplayer_market_price
            FROM cards c
            JOIN card_variants cv ON cv.card_id = c.id
            WHERE cv.tcgplayer_product_id IS NULL
               OR cv.tcgplayer_product_id = ''
               OR cv.tcgplayer_product_id = 0
            ORDER BY c.canonical_code, cv.variant_key
            LIMIT 500
        """)

    # image_without_price_candidates: have image but no tcgplayer price
    if tbl_has("card_variants", "image_path") and tbl_has("card_variants", "tcgplayer_market_price"):
        optional_queries["image_without_price_candidates"] = textwrap.dedent("""\
            SELECT c.canonical_code, c.card_name,
                   cv.variant_key, cv.variant_label, cv.image_path,
                   cv.tcgplayer_product_id, cv.tcgplayer_market_price
            FROM cards c
            JOIN card_variants cv ON cv.card_id = c.id
            WHERE cv.image_path IS NOT NULL AND cv.image_path != ''
              AND (cv.tcgplayer_market_price IS NULL OR cv.tcgplayer_market_price = 0)
            ORDER BY c.canonical_code, cv.variant_key
            LIMIT 500
        """)

    # duplicate_mapping_candidates: market_products with same market_product_id
    if tbl_has("market_products", "market_product_id"):
        optional_queries["duplicate_mapping_candidates"] = textwrap.dedent("""\
            SELECT market_product_id, source_name, COUNT(*) AS cnt
            FROM market_products
            WHERE market_product_id IS NOT NULL
            GROUP BY market_product_id, source_name
            HAVING cnt > 1
            ORDER BY cnt DESC
            LIMIT 200
        """)

    # printing_market_map coverage: unmapped printings
    if "printing_market_map" in all_columns and tbl_has("printing_market_map", "card_id"):
        optional_queries["unmapped_printing_market"] = textwrap.dedent("""\
            SELECT c.canonical_code, c.card_name, cv.variant_key, cv.variant_label
            FROM cards c
            JOIN card_variants cv ON cv.card_id = c.id
            LEFT JOIN printing_market_map pmm ON pmm.card_id = c.id AND pmm.variant_key = cv.variant_key
            WHERE pmm.id IS NULL
            ORDER BY c.canonical_code, cv.variant_key
            LIMIT 500
        """)

    optional_notes: list[str] = []
    for label, sql in optional_queries.items():
        try:
            rows = conn.execute(sql).fetchall()
            if rows:
                col_names = rows[0].keys()
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(col_names)
                for r in rows:
                    w.writerow([r[c] for c in col_names])
                (out_dir / f"{label}.csv").write_text(buf.getvalue(), encoding="utf-8")
                optional_notes.append(f"- `{label}.csv`: {len(rows)} rows")
            else:
                optional_notes.append(f"- `{label}.csv`: 0 rows (query ran clean, no results)")
        except Exception as exc:
            optional_notes.append(f"- `{label}`: SKIPPED ({exc})")
    if not optional_queries:
        optional_notes.append("- No optional queries could be safely constructed from detected schema.")

    print(f"  optional diagnostics: {len(optional_queries)} attempted")

    # ── 6. analysis_summary.md ─────────────────────────────────────
    # Build quick analysis
    card_tables = [t for t in relevant if "card" in t.lower() and "variant" not in t.lower() and "market" not in t.lower()]
    variant_tables = [t for t in relevant if "variant" in t.lower()]
    price_tables = [t for t in relevant if any(k in t.lower() for k in ("price", "market", "product"))]
    image_tables = [t for t in relevant if any(k in t.lower() for k in ("image", "asset"))]
    source_tables = [t for t in relevant if any(k in t.lower() for k in ("source", "provenance"))]
    mapping_tables = [t for t in relevant if any(k in t.lower() for k in ("mapping", "tcg", "group"))]

    # Detect join keys
    join_keys: set[str] = set()
    key_terms = {"canonical_code", "card_code", "card_id", "product_id", "ext_product_id",
                 "ext_number", "image_path", "image_url", "source", "source_id",
                 "variant_key", "print_id", "set_code", "group_id", "tcgcsv_group_id"}
    for tbl in relevant:
        for c in all_columns[tbl]:
            if c["name"].lower() in key_terms:
                join_keys.add(c["name"])

    def fmt_table_list(tlist: list[str]) -> str:
        if not tlist:
            return "_none detected_"
        return ", ".join(f"`{t}` ({counts.get(t, '?')} rows)" for t in tlist)

    summary = f"""# Card Catalog Diagnostic Summary

**Generated:** {stamp}
**DB:** `{DB_PATH}`
**DB size:** {db_stat_before.st_size:,} bytes
**Tables:** {len(tables)} total, {len(relevant)} relevant

## Source-of-Truth Candidates

| Category | Tables |
|----------|--------|
| Card identity | {fmt_table_list(card_tables)} |
| Variants / prints | {fmt_table_list(variant_tables)} |
| Prices / market | {fmt_table_list(price_tables)} |
| Images / assets | {fmt_table_list(image_tables)} |
| Sources / provenance | {fmt_table_list(source_tables)} |
| Mappings / TCG IDs | {fmt_table_list(mapping_tables)} |

## Observed Join Keys

{', '.join(f'`{k}`' for k in sorted(join_keys)) or '_none detected_'}

## Key Linkage Chain

```
cards.id
  -> card_variants.card_id  (1:N, variant_key differentiates)
     -> card_variants.print_id
        -> printing_market_map.printing_id  (bridge to market)
           -> printing_market_map.market_product_id
              -> market_products.market_product_id
                 -> market_prices.market_product_fk = market_products.id
     -> card_variants.tcgplayer_product_id  (direct shortcut to TCGplayer)
```

## Optional Diagnostics

{chr(10).join(optional_notes)}

## Risk Areas

_Review the sample CSVs and optional diagnostic outputs for:_
- Duplicate `ext_product_id` or `canonical_code` values across tables
- Multiple tables serving similar mapping roles (e.g. both `market_products` and a separate price table)
- Variants with images but no market linkage
- Cards present in `cards` but absent from price/market tables
- `printing_market_map` uses `printing_id` (= `card_variants.print_id`) NOT `card_id`
- `market_products` has NO direct FK to `cards` — linkage is only through variant print_id or tcgplayer_product_id
"""
    (out_dir / "analysis_summary.md").write_text(summary, encoding="utf-8")
    print(f"  analysis_summary.md")

    conn.close()

    # ── 7. Verify DB was not modified ──────────────────────────────
    db_stat_after = DB_PATH.stat()
    modified = db_stat_after.st_mtime != db_stat_before.st_mtime
    size_changed = db_stat_after.st_size != db_stat_before.st_size

    print()
    if modified or size_changed:
        print(f"WARNING: DB may have been modified (mtime changed: {modified}, size changed: {size_changed})")
    else:
        print("DB integrity: mtime and size unchanged (read-only confirmed)")

    print(f"\nOutput: {out_dir}")
    return


if __name__ == "__main__":
    print(f"Probing {DB_PATH} ...\n")
    main()
