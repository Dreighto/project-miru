# OP01 readiness report — three verification passes complete

**Date:** 2026-05-17
**Author:** CC (in-session)
**Source ticket:** PRO-904 (Done). Pass A/B/C executed inline after operator authorization.

## TL;DR

OP01 is **ready for any text/effect/rule-reasoning training corpus right now.** Image-classification training requires fetching 53 missing image files from Bandai (out of 218 canonical-format printings). No DB destructive operations were performed; the 113 legacy `::`-style rows are cleanly filterable rather than deleted.

## Final OP01 state

| Layer                                                  | Count | Notes                                                                                                                                  |
| ------------------------------------------------------ | ----: | -------------------------------------------------------------------------------------------------------------------------------------- |
| Base cards (`cards` table)                             |   121 | Full card-side data (name, effect, rarity, color, type, traits, cost, power) — 100% populated.                                         |
| Canonical-format variants (`OP01-NNN` / `_pN` / `_rN`) |   235 | Each has `image_url`, each has `official_provenance` (post Pass A).                                                                    |
| Legacy synthetic variants (`OP01-NNN::*`)              |   113 | No `image_url`, no `official_provenance`. Thumbs exist on disk (`D:\Miru_Assets`). Excluded from training-grade view by either filter. |
| Total `card_variants` rows                             |   348 |                                                                                                                                        |

## Pass A — provenance backfill from Bandai crawl (write)

Source-of-truth: `data/bandai_op01_crawl.json` (PRO-904 deliverable). Used the crawl's `card_set` value as authoritative `official_provenance`.

| Bucket                          |   Count |
| ------------------------------- | ------: |
| Already correct                 |      61 |
| NULL → set to crawl value       |     155 |
| Corrected existing wrong value  |       2 |
| Left as-is (no crawl authority) |     130 |
| **Updates applied**             | **157** |

Before Pass A: 80 of 348 had provenance. After: 235 of 348 (every Bandai-format row).

The 2 corrections caught existing-but-wrong values where the DB had `-ROMANCE DAWN- [OP01]` but Bandai's actual `card_set` was a cross-set product. Log at `data/op01_provenance_backfill.log`.

DB backed up to `data/card_catalog.db.bak.20260517_112635` before write.

## Pass B — image-asset verification (read-only)

| Category                           | On disk |   Total |                                                        Missing |
| ---------------------------------- | ------: | ------: | -------------------------------------------------------------: |
| Bandai canonical (`OP01-NNN[_pN]`) |     182 |     218 | **36** (cross-set dirs PCC25, GC01, ANN1EN, PCCFR not present) |
| `_r1`/`_r2` rare-art               |       0 |      17 |                              **17** (downloadable — see below) |
| Legacy synthetic                   |     113 |     113 |                                         0 (thumbs all present) |
| **TOTAL**                          | **295** | **348** |                                                         **53** |

**All 17 `_r1`/`_r2` Bandai CDN URLs return HTTP 200**, confirming these are real Bandai assets simply not indexed by the freewords-by-number search. Image fetcher can download them on demand.

Asset roots: `D:\OPTCG_Images` (canonical), `D:\Miru_Assets` (thumbs). Per-row results in `data/op01_image_audit.jsonl`. Full report at `data/op01_image_audit_report.md`.

## Pass C — legacy-row training filter validation (read-only)

Goal: confirm a soft filter cleanly separates training-grade rows from legacy noise, without hard-deleting.

| Filter                                                                | Canonical kept | Synthetic kept |
| --------------------------------------------------------------------- | -------------: | -------------: |
| `WHERE image_url IS NOT NULL AND image_url != ''`                     |            235 |          **0** |
| `WHERE official_provenance IS NOT NULL AND official_provenance != ''` |            235 |          **0** |

Both filters are 100% clean — every canonical row passes, every legacy row is excluded. **No DB delete required.** The 113 legacy `::`-style rows are preserved (audit trail) but ignorable by any corpus builder that uses either filter.

## Training-readiness by corpus shape

| Shape                                           | Ready today? | If not, what's blocking                                                                             |
| ----------------------------------------------- | :----------: | --------------------------------------------------------------------------------------------------- |
| Card-text / effect-resolution / rules reasoning |      ✅      | —                                                                                                   |
| Structured-rules tuples (JSON / JSONL)          |      ✅      | —                                                                                                   |
| Image classification (full image set)           |      ❌      | Fetch 53 missing images from Bandai CDN (17 `_r` + 36 cross-set). All URLs known and HTTP-verified. |
| Image classification (canonical printings only) |      ⚠️      | 36 cross-set printings still missing on disk; same fetch task.                                      |

## Recommended next step

Single ticket if image-classification training is in scope:

> **PRO-XXX — OP01 image fetcher: download 53 missing assets from Bandai CDN**
> Inputs: `data/op01_image_audit.jsonl` (filter `result=missing`), `data/bandai_op01_crawl.json` (image_url field).
> Output: files placed under `D:\OPTCG_Images\OP01\` (flat) or per-set subdir, then re-run Pass B.

If text-only training proceeds first, OP01 is ready to be consumed today.

## What was NOT done

- No hard-delete of any `card_variants` row. Soft-filter approach preserves audit trail.
- No image downloads — Pass B is read-only verification per scope.
- No DB write outside Pass A's 157 `UPDATE` statements on `official_provenance`/`updated_at`.
- No card-side (`cards` table) writes — card-text data was already complete.
