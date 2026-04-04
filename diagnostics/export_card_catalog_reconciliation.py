#!/usr/bin/env python
"""
Read-only reconciliation export for data/card_catalog.db.

Opens SQLite via Python's ``sqlite3`` module with URI ``mode=ro`` (same engine as the
``sqlite3`` CLI). No writes, no migrations.
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "card_catalog.db"
DIAG = Path(__file__).resolve().parent


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _pick(cols: list[str], *candidates: str) -> str | None:
    low = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def _qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _coalesce_price_expr(alias: str = "mpr") -> str:
    parts = [
        f'{alias}.market_price',
        f'{alias}.mid_price',
        f'{alias}.low_price',
        f'{alias}.high_price',
        f'{alias}.direct_low_price',
        f'{alias}.listed_median_price',
    ]
    return "COALESCE(" + ", ".join(parts) + ")"


def main() -> int:
    if not DB_PATH.is_file():
        print(f"FATAL: DB not found: {DB_PATH}", file=sys.stderr)
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir = DIAG / f"card_catalog_reconciliation_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    stat_before = DB_PATH.stat()
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row

    required_tables = ("cards", "card_variants", "printing_market_map", "market_products", "market_prices")
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    missing = [t for t in required_tables if t not in tables]
    if missing:
        print(f"FATAL: missing tables: {missing}", file=sys.stderr)
        conn.close()
        return 1

    cc = _cols(conn, "cards")
    cv = _cols(conn, "card_variants")
    pmm_c = _cols(conn, "printing_market_map")
    mp_c = _cols(conn, "market_products")
    mpr_c = _cols(conn, "market_prices")

    price_cv = "COALESCE(cv.tcgplayer_market_price, cv.tcgplayer_mid_price, cv.tcgplayer_low_price)"

    def row_dict(r: sqlite3.Row) -> dict:
        return {k: r[k] for k in r.keys()}

    # --- Dynamic card / variant / market column export ---
    card_sel: list[str] = []
    if "id" in cc:
        # cards.id — distinct from card_variants.card_id (FK) exported separately
        card_sel.append(f'c.{_qident("id")} AS {_qident("card_id")}')
    if canon := _pick(cc, "canonical_code"):
        card_sel.append(f'c.{_qident(canon)} AS card_canonical_code')
    if code := _pick(cc, "card_code"):
        card_sel.append(f'c.{_qident(code)} AS card_card_code')
    if nm := _pick(cc, "card_name", "name"):
        card_sel.append(f'c.{_qident(nm)} AS card_name')
    if ra := _pick(cc, "rarity"):
        card_sel.append(f'c.{_qident(ra)} AS card_rarity')
    if src := _pick(cc, "source", "distribution_source"):
        card_sel.append(f'c.{_qident(src)} AS card_source')

    cv_sel: list[str] = []
    for logical, cands in [
        ("card_variant_id", ["id"]),
        ("card_variants_card_id", ["card_id"]),
        ("print_id", ["print_id"]),
        ("tcgplayer_product_id", ["tcgplayer_product_id"]),
        ("variant", ["variant", "variant_key"]),
        ("variant_label", ["variant_label"]),
        ("variant_family", ["variant_family"]),
        ("normalized_variant_family", ["normalized_variant_family"]),
        ("variant_source", ["source"]),
        ("image_path", ["image_path"]),
        ("image_url", ["image_url"]),
        ("asset_local_path", ["local_path"]),
    ]:
        col = _pick(cv, *cands)
        if col:
            cv_sel.append(f'cv.{_qident(col)} AS {_qident("cv_" + logical)}')

    pmm_sel: list[str] = []
    for logical, cands in [
        ("printing_market_map_id", ["id"]),
        ("printing_id", ["printing_id"]),
        ("printing_market_map_market_product_internal_fk", ["market_product_id"]),
        ("mapping_confidence", ["mapping_confidence"]),
        ("mapping_method", ["mapping_method"]),
    ]:
        col = _pick(pmm_c, *cands)
        if col:
            alias = "pmm_" + logical if not logical.startswith("printing") else logical
            pmm_sel.append(f'pmm.{_qident(col)} AS {_qident(alias)}')

    mp_sel: list[str] = []
    for logical, cands in [
        ("market_products_internal_id", ["id"]),
        ("market_product_id", ["market_product_id"]),
        ("product_name", ["product_name"]),
        ("clean_name", ["clean_product_name", "clean_name"]),
        ("ext_number", ["ext_number", "market_number"]),
        ("rarity_market", ["rarity_market", "rarity"]),
        ("subtype", ["subtype", "market_variant_label"]),
        ("market_product_source", ["source_name", "source"]),
    ]:
        col = _pick(mp_c, *cands)
        if col:
            alias = "mp_" + logical
            mp_sel.append(f'mp.{_qident(col)} AS {_qident(alias)}')

    mpr_sel: list[str] = []
    for logical, cands in [
        ("market_prices_id", ["id"]),
        ("market_product_fk", ["market_product_fk"]),
        ("low_price", ["low_price"]),
        ("mid_price", ["mid_price"]),
        ("high_price", ["high_price"]),
        ("market_price", ["market_price"]),
        ("direct_low_price", ["direct_low_price"]),
        ("listed_median_price", ["listed_median_price"]),
        ("captured_at", ["captured_at"]),
        ("mpr_source_name", ["source_name"]),
    ]:
        col = _pick(mpr_c, *cands)
        if col:
            alias = "mpr_" + logical if logical not in ("market_product_fk",) else logical
            mpr_sel.append(f'mpr.{_qident(col)} AS {_qident(alias)}')

    pmm_join = (
        "LEFT JOIN (SELECT * FROM (SELECT pmm.*, ROW_NUMBER() OVER "
        "(PARTITION BY printing_id ORDER BY is_preferred DESC, id) AS _rn "
        "FROM printing_market_map pmm) z WHERE _rn = 1) pmm ON pmm.printing_id = cv.id"
    )
    mpr_join = (
        "LEFT JOIN (SELECT * FROM (SELECT mpr.*, ROW_NUMBER() OVER "
        "(PARTITION BY market_product_fk ORDER BY captured_at DESC, id DESC) AS _rn "
        "FROM market_prices mpr) z WHERE _rn = 1) mpr ON mpr.market_product_fk = mp.id"
    )

    wide_sql = f"""
    SELECT
      {", ".join(card_sel) if card_sel else "NULL AS _no_cards"},
      {", ".join(cv_sel) if cv_sel else "NULL AS _no_cv"},
      {", ".join(pmm_sel) if pmm_sel else "NULL AS _no_pmm"},
      {", ".join(mp_sel) if mp_sel else "NULL AS _no_mp"},
      {", ".join(mpr_sel) if mpr_sel else "NULL AS _no_mpr"},
      TRIM(COALESCE(cv.print_id, '')) AS print_id_trim,
      CASE WHEN EXISTS (SELECT 1 FROM printing_market_map p WHERE p.printing_id = cv.id) THEN 1 ELSE 0 END AS has_printing_market_map,
      CASE WHEN EXISTS (
        SELECT 1 FROM printing_market_map p
        JOIN market_products mp2 ON mp2.id = p.market_product_id
        WHERE p.printing_id = cv.id) THEN 1 ELSE 0 END AS has_market_product_via_map,
      CASE WHEN EXISTS (
        SELECT 1 FROM printing_market_map p
        JOIN market_products mp2 ON mp2.id = p.market_product_id
        JOIN market_prices mpr2 ON mpr2.market_product_fk = mp2.id
        WHERE p.printing_id = cv.id AND ({_coalesce_price_expr('mpr2')}) IS NOT NULL
      ) THEN 1 ELSE 0 END AS has_market_price_via_chain,
      CASE WHEN ({price_cv}) IS NOT NULL THEN 1 ELSE 0 END AS has_tcgplayer_column_price,
      CASE WHEN (
        TRIM(COALESCE(cv.image_path,'')) != '' OR TRIM(COALESCE(cv.image_url,'')) != ''
      ) THEN 1 ELSE 0 END AS has_variant_image_fields,
      CASE WHEN EXISTS (SELECT 1 FROM image_assets ia WHERE ia.printing_id = cv.id)
        THEN 1 ELSE 0 END AS has_image_assets_row,
      CASE
        WHEN (cv.tcgplayer_product_id IS NULL OR cv.tcgplayer_product_id = 0)
             AND (
               EXISTS (
                 SELECT 1 FROM printing_market_map p
                 JOIN market_products mp2 ON mp2.id = p.market_product_id
                 JOIN market_prices mpr2 ON mpr2.market_product_fk = mp2.id
                 WHERE p.printing_id = cv.id AND ({_coalesce_price_expr('mpr2')}) IS NOT NULL
               ) OR ({price_cv}) IS NOT NULL
             )
          THEN 'missing_tcgplayer_product_id_only'
        WHEN TRIM(COALESCE(cv.print_id, '')) = '' THEN 'missing_print_id'
        WHEN NOT EXISTS (SELECT 1 FROM printing_market_map p WHERE p.printing_id = cv.id)
          THEN 'missing_printing_market_map'
        WHEN NOT EXISTS (
          SELECT 1 FROM printing_market_map p
          JOIN market_products mp2 ON mp2.id = p.market_product_id
          WHERE p.printing_id = cv.id)
          THEN 'missing_market_product'
        WHEN NOT EXISTS (
          SELECT 1 FROM printing_market_map p
          JOIN market_products mp2 ON mp2.id = p.market_product_id
          JOIN market_prices mpr2 ON mpr2.market_product_fk = mp2.id
          WHERE p.printing_id = cv.id AND ({_coalesce_price_expr('mpr2')}) IS NOT NULL
        )
          THEN 'missing_market_price'
        ELSE 'other'
      END AS failure_stage
    FROM card_variants cv
    JOIN cards c ON c.id = cv.card_id
    {pmm_join}
    LEFT JOIN market_products mp ON mp.id = pmm.market_product_id
    {mpr_join}
    """

    wide_rows = conn.execute(wide_sql).fetchall()
    wide_keys = list(wide_rows[0].keys()) if wide_rows else []
    wide_keys = [k for k in wide_keys if k != "_rn"]

    def _tcg_pid(d: dict) -> object:
        return d.get("cv_tcgplayer_product_id", d.get("tcgplayer_product_id"))

    def write_csv(path: Path, dict_rows: list[dict], fieldnames: list[str]) -> None:
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for d in dict_rows:
                w.writerow({k: d.get(k, "") for k in fieldnames})

    all_dicts = [row_dict(r) for r in wide_rows]
    for d in all_dicts:
        if "_rn" in d:
            del d["_rn"]

    if not all_dicts:
        print("FATAL: no card_variants rows (or query failed)", file=sys.stderr)
        conn.close()
        return 1

    unresolved = [d for d in all_dicts if not d.get("has_market_price_via_chain") and not d.get("has_tcgplayer_column_price")]
    unresolved_limited = unresolved[:2000]

    has_image = lambda d: d.get("has_variant_image_fields") or d.get("has_image_assets_row")
    image_no_price = [d for d in all_dicts if has_image(d) and not d.get("has_market_price_via_chain") and not d.get("has_tcgplayer_column_price")]

    tcg_missing = [d for d in all_dicts if _tcg_pid(d) in (None, "", 0)]

    # failure_stage counts for unresolved only
    st_counts = Counter(d["failure_stage"] for d in unresolved)
    recoverable: list[dict] = []
    for d in all_dicts:
        hp = d.get("has_printing_market_map")
        hm = d.get("has_market_product_via_map")
        hch = d.get("has_market_price_via_chain")
        htc = d.get("has_tcgplayer_column_price")
        tid = _tcg_pid(d)
        tcg_empty = tid is None or tid == "" or tid == 0
        if hp and hm and not hch and not htc:
            recoverable.append({**d, "recoverable_reason": "map_and_product_ok_price_row_missing"})
        elif hp and hm and hch and tcg_empty:
            recoverable.append({**d, "recoverable_reason": "price_via_chain_tcgplayer_id_missing"})

    recoverable.sort(
        key=lambda x: (0 if x.get("recoverable_reason") == "map_and_product_ok_price_row_missing" else 1,)
    )

    # Bucket counts (explicit)
    n_variants = conn.execute("SELECT COUNT(*) FROM card_variants").fetchone()[0]
    n_unresolved = len(unresolved)
    n_missing_print_id = sum(1 for d in unresolved if (d.get("print_id_trim") in ("", None)))
    n_missing_pmm = sum(1 for d in unresolved if not d.get("has_printing_market_map"))
    n_missing_mp = sum(
        1 for d in unresolved if d.get("has_printing_market_map") and not d.get("has_market_product_via_map")
    )
    n_missing_mpr = sum(
        1
        for d in unresolved
        if d.get("has_market_product_via_map") and not d.get("has_market_price_via_chain")
    )
    n_tcg_only_resolved = sum(
        1
        for d in all_dicts
        if d.get("has_tcgplayer_column_price") and not d.get("has_market_price_via_chain")
    )

    # Top failure patterns (boolean tuple)
    patterns: Counter[str] = Counter()
    for d in unresolved:
        pat = "|".join(
            [
                f"img={1 if has_image(d) else 0}",
                f"pmm={d.get('has_printing_market_map')}",
                f"mp={d.get('has_market_product_via_map')}",
                f"mpr={d.get('has_market_price_via_chain')}",
                f"tcgpid={0 if _tcg_pid(d) in (None, '', 0) else 1}",
                f"stage={d.get('failure_stage')}",
            ]
        )
        patterns[pat] += 1
    top10 = patterns.most_common(10)

    dominant_stage = st_counts.most_common(1)[0][0] if st_counts else "n/a"

    # failure_stage_counts.csv
    with (out_dir / "failure_stage_counts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["failure_stage", "row_count"])
        for stage, cnt in sorted(st_counts.items(), key=lambda x: (-x[1], x[0])):
            w.writerow([stage, cnt])

    fk = [k for k in wide_keys if k not in ("failure_stage", "_rn")]
    if "failure_stage" in wide_keys:
        export_fields = fk + ["failure_stage"]
    else:
        export_fields = fk

    write_csv(out_dir / "unresolved_variants_full.csv", unresolved_limited, export_fields)
    write_csv(out_dir / "image_without_price_detailed.csv", image_no_price[:2000], export_fields)
    write_csv(
        out_dir / "missing_tcgplayer_product_id_detailed.csv",
        tcg_missing[:2000],
        export_fields,
    )
    rec_fields = export_fields + ["recoverable_reason"]
    write_csv(out_dir / "likely_recoverable_rows.csv", recoverable[:2000], rec_fields)

    summary_md = f"""# Card catalog reconciliation summary

**Generated (UTC):** {stamp}
**Database:** `{DB_PATH}`
**Read-only URI:** `file:...?mode=ro`

## Interpretation note (linkage)

In this worktree schema, `printing_market_map.printing_id` joins to **`card_variants.id`**
(internal variant / printing row), not to the human-readable `card_variants.print_id` string.
Tools such as `tools/rebuild_market_tables.py` use `JOIN card_variants cv ON cv.id = pmm.printing_id`.

The `print_id` column is still exported for operator diagnostics; empty `print_id` is flagged as
`missing_print_id` even when a `printing_market_map` row exists keyed by `card_variants.id`.

## Row counts (variant-level)

| Bucket | Count |
|--------|------:|
| Total `card_variants` | {n_variants} |
| Unresolved (no chain price row with non-null coalesced price **and** no tcgplayer column price) | {n_unresolved} |
| Unresolved with empty `print_id` | {n_missing_print_id} |
| Unresolved with no `printing_market_map` for `cv.id` | {n_missing_pmm} |
| Unresolved with map but no resolvable `market_products` row | {n_missing_mp} |
| Unresolved with product but no `market_prices` row with any price column set | {n_missing_mpr} |
| Resolved **only** via tcgplayer columns (no chain price) | {n_tcg_only_resolved} |
| Image present (`image_path`/`image_url` or `image_assets` by `printing_id=cv.id`) but no price | {len(image_no_price)} |
| `tcgplayer_product_id` null/0 (all variants) | {len(tcg_missing)} |

## Dominant `failure_stage` (unresolved subset)

**{dominant_stage}** — {st_counts.get(dominant_stage, 0)} rows.

Stage definitions:

- `missing_print_id` — `print_id` column empty (after trim).
- `missing_printing_market_map` — no `printing_market_map` row with `printing_id = card_variants.id`.
- `missing_market_product` — map exists but internal FK does not resolve to a `market_products` row.
- `missing_market_price` — product exists (via map) but no `market_prices` row with a non-null coalesced price.
- `missing_tcgplayer_product_id_only` — used when **price exists** (chain or column) but tcgplayer id empty; these are **not** counted as price-unresolved.
- `other` — price-unresolved but none of the above (investigate manually).

Price usability: chain side uses non-null `COALESCE(market_price, mid_price, low_price, high_price, direct_low_price, listed_median_price)` on **any** joined price row for the mapped product; variant side uses non-null `COALESCE(tcgplayer_market_price, tcgplayer_mid_price, tcgplayer_low_price)`.

## Top 10 failure patterns (unresolved)

Patterns encode: image flag, has map, has product, has chain price, tcgplayer id present, failure_stage.

| Pattern | Count |
|---------|------:|
{chr(10).join(f"| `{p}` | {c} |" for p, c in top10)}

## Which linkage step fails most often (unresolved)

Among unresolved rows, the staged classifier above attributes the first failing step.
The highest-frequency `failure_stage` is listed above; raw counts are in `failure_stage_counts.csv`.

## Missing price attribution (summary)

See bucket table: missing prices are primarily explained by **`failure_stage`** distribution
(`failure_stage_counts.csv`). Empty `print_id` is common metadata debt even when internal
`printing_id` mapping uses `card_variants.id`.

## Exports

| File | Description |
|------|-------------|
| `unresolved_variants_full.csv` | Up to 2000 price-unresolved variants |
| `image_without_price_detailed.csv` | Variants with image signals but no price |
| `missing_tcgplayer_product_id_detailed.csv` | `tcgplayer_product_id` null/0 (up to 2000) |
| `failure_stage_counts.csv` | Aggregated stages for unresolved |
| `likely_recoverable_rows.csv` | Map+product OK but price missing, or chain price OK but tcg id missing |
| `candidate_join_keys.md` | Join key notes |
| `reconciliation_summary.md` | This file |

## Verification

- Script: `diagnostics/export_card_catalog_reconciliation.py`
- DB opened read-only; no migrations or updates performed.
- `failure_stage_counts.csv` counts **price-unresolved** variants only (excludes rows that already have chain or tcgplayer column prices).
"""

    (out_dir / "reconciliation_summary.md").write_text(summary_md, encoding="utf-8")

    keys_md = f"""# Candidate join keys (card_catalog.db)

Observed in this schema (see `PRAGMA table_info` / prior probe exports).

## Strong / safe

- **`card_variants.id` → `printing_market_map.printing_id`** — This is the active bridge used in
  repo tooling (`JOIN card_variants cv ON cv.id = pmm.printing_id`). Treat as **canonical** for automation.
- **`printing_market_map.market_product_id` → `market_products.id`** — INTEGER internal PK on
  `market_products`; **not** the TEXT `market_products.market_product_id` (external TCG id).
- **`market_prices.market_product_fk` → `market_products.id`** — Standard price attachment.

## Moderately safe (with validation)

- **`cards.id` → `card_variants.card_id`** — Core catalog structure.
- **`tcgplayer_product_id` on `card_variants`** — Shortcut to TCGPlayer product id when populated;
  compare against `market_products.market_product_id` (TEXT) only after normalizing types.

## Risky / ambiguous

- **`card_variants.print_id`** — Human-readable code; **not** the same as `printing_market_map.printing_id`
  in this database (type and semantics differ). Do not assume `print_id` joins to `printing_market_map`
  without a documented mapping rule.
- **`market_products.market_product_id` (TEXT)** — External id; join via `market_products.id` from the map.
- **`canonical_code` / set+number** — Good for human reconciliation; risk of collisions or format drift across sources.
- **`market_number` / “ext number”** — Useful for matching within a set; weak alone across sets.
- **Variant family fields** — Not present on `card_variants` in the probed schema; `variant_key` /
  `variant_label` are the local discriminantors.
- **Image paths (`image_path`, `image_url`, `image_assets.local_path`)** — Great for diagnostics;
  filenames alone are risky as unique keys (duplicates, moves).

## `image_assets`

`image_assets.printing_id` aligns with **`card_variants.id`** in the same way as `printing_market_map.printing_id`.
"""

    (out_dir / "candidate_join_keys.md").write_text(keys_md, encoding="utf-8")

    conn.close()

    stat_after = DB_PATH.stat()
    verify_ok = stat_before.st_size == stat_after.st_size and stat_before.st_mtime_ns == stat_after.st_mtime_ns

    (out_dir / "verification.txt").write_text(
        f"db_path={DB_PATH}\n"
        f"size_before={stat_before.st_size}\n"
        f"size_after={stat_after.st_size}\n"
        f"mtime_ns_before={stat_before.st_mtime_ns}\n"
        f"mtime_ns_after={stat_after.st_mtime_ns}\n"
        f"unchanged={verify_ok}\n",
        encoding="utf-8",
    )

    print(f"Output: {out_dir}")
    print(f"DB unchanged: {verify_ok}")
    return 0 if verify_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
