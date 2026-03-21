# Official legality / banlist ingestion schema

This directory is the **official-rules source path** for Miru regulation intelligence.

## Purpose

- Hold or reference **official Bandai rules / banlist** input when available.
- Snapshot-first: place files here (or reference URLs) that are ingested manually or by an approved pipeline.
- **No fabricated data.** Only ingest from permitted official sources.

## Expected record shape (for ingestion into `miru_card_legality`)

Each record should have:

| Field | Type | Description |
|-------|------|-------------|
| card_code | string | Canonical card code (e.g. OP01-001) |
| format | string | `standard` / `extra` / `legacy` |
| legality_state | string | `legal` / `banned` / `restricted` / `rotated` / `unknown` |
| effective_date | string | Date the state takes effect (YYYY-MM-DD or empty) |
| source_id | string | e.g. `official`, `bandai_rules`, `official_banlist` |
| source_reference | string | URL or doc reference |
| last_checked_at | string | When this was last verified |
| notes | string | Optional reason/summary |

## Workflow

1. Operator or pipeline places an official banlist/legality snapshot here (or configures a permitted source).
2. An ingestion step (to be added or scripted) reads the file and calls `miru_regulation.save_legality_state` for each record.
3. Miru uses `miru_card_legality` for legality-aware targeting and future badges; no claim without an official-source-backed row.

## File naming

- `official_legality.json` – optional: array of records in the shape above.
- Other names as long as the consumer validates and uses `source_id` from the allowed set.
