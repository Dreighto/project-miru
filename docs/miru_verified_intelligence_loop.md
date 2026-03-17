# Miru Verified Intelligence Loop

This document describes Miru's additive SQLite-first verified-intelligence sidecar. It does not replace the current dashboard runtime. It provides a controlled, auditable path for storing source-backed card facts, citations, conflicts, and refresh metadata card-by-card.

## Official Source Path In This Pass

This pass implements a Tier 1 `OfficialCardListSnapshotAdapter` that reads a local official-cardlist-style snapshot file. The snapshot format is intentionally close to a structured export of official card-list data, and the adapter preserves the official record id, source URL, and snapshot timestamp for downstream fact citations.

Current implemented path:
- local official-style snapshot fixture
- local official export refresh input normalized into the snapshot shape
- refresh-aware sidecar runner that updates dossiers and records per-card change reports
- adapter abstraction ready for future live official fetch mode
- no broad runtime integration
- no unsafe web-scale scraping

## What Fields Are Supported Now

The official adapter can map these fields into the verified loop today when present in the snapshot:
- card code
- card name
- set code
- set name
- rarity
- color
- card type
- cost, power, counter, and life when present
- attribute and traits when present
- effect text and trigger text when present
- subtype/subtypes when present
- series/product metadata, availability, and status when present
- image URL or image identity
- official record id / source card ref
- variant clues when the snapshot includes explicit variant entries
- last checked / snapshot time

## What Remains Ambiguous Or Deferred

Still deferred or intentionally unknown unless a source provides them:
- gameplay interpretation
- market interpretation
- relationships that are not explicit in the source
- variant families that are not explicit in the source data
- live official fetching beyond local export-to-snapshot mode
- long-term historical fact versioning beyond refresh reports and current citations

## Confidence Behavior

When only one Tier 1 official observation exists for a supported field, the current trust model stores that field as `verified`.
If a supported field is absent from the official snapshot, it stays `missing`.
If a lower-tier source disagrees with the Tier 1 source in the same enrichment run, Miru records a `conflict` instead of silently overriding or collapsing the values.

## Official Refresh Workflow

Miru now supports a tightly scoped refresh path for trusted official data:
- read a local official export or refresh input file
- normalize field aliases into the existing `OfficialCardListSnapshotAdapter` shape
- run a refresh-aware enrichment pass against the stored dossiers
- compare before/after dossier facts deterministically
- record per-card refresh outcomes such as `unchanged`, `added`, `updated`, `missing_in_refresh_input`, `conflict`, or `skipped`

The refresh path stays sidecar-only. It updates the verified SQLite store, keeps citations attached to refreshed facts, and writes auditable refresh reports without changing dashboard runtime behavior.


## Question-Style Validation

Miru now includes a small dossier query helper that answers practical card questions from stored dossier facts rather than unsourced generation. Current helper coverage includes:
- identity summaries
- fact answers for set, color, type, stats, effect text, and trigger text
- variant summaries
- source summaries explaining why a fact is marked verified
- conflict summaries when lower-tier sources disagree

## Schema Overview

The sidecar SQLite schema stores:
- `source_registry`
- `enrichment_runs`
- `enrichment_run_cards`
- `cards`
- `card_variants`
- `card_relationships`
- `card_facts`
- `fact_sources`
- `confidence_records`
- `refresh_reports`

## Dossier Shape

Each dossier currently contains:
- identity
- set info
- variants
- relationships
- fact summaries with citations
- source ledger
- confidence summary
- refresh metadata
- future extension hooks for semantic/vector memory later

## Local Validation

```powershell
python -m py_compile dashboard\miru_intel_models.py dashboard\miru_intel_trust.py dashboard\miru_intel_adapters.py dashboard\miru_intel_db.py dashboard\miru_intel_pipeline.py dashboard\miru_dossier_queries.py dashboard\miru_snapshot_refresh.py tools\miru_verified_loop.py tools\miru_refresh_official_snapshot.py tests\test_miru_verified_intel.py tests\test_miru_dossier_queries.py tests\test_miru_snapshot_refresh.py
python -m unittest tests.test_miru_verified_intel tests.test_miru_dossier_queries tests.test_miru_snapshot_refresh
python tools\miru_verified_loop.py OP01-001 OP01-060 --db-path data\miru_intel_test.db --official-snapshot tests\fixtures\miru_official_cardlist_sample.json
python tools\miru_refresh_official_snapshot.py tests\fixtures\miru_official_export_refresh_input.json --db-path data\miru_intel_test.db --snapshot-output data\miru_official_refresh_snapshot.json
```
