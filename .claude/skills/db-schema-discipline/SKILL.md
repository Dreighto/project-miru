---
name: db-schema-discipline
description: Required discipline for any UPDATE/INSERT/DELETE on Project Miru SQLite databases. Backup-before-write, log-the-change, surface diff in commits, verify after. Hard rule on schema-change escalation (ALTER/CREATE/DROP TABLE need operator approval). Use when working on card_catalog.db, miru_dossiers.db, miru_learning_dossiers.db, or any DB writes. Triggers include update the catalog, backfill provenance, fix the variant rows, dedup, schema change, DB migration, add a column, sqlite, UPDATE, INSERT, DELETE, set population, OP01 Pass, card_catalog, miru_dossiers.
---

## When this skill applies

Any work touching the Project Miru SQLite databases:

- `data/card_catalog.db` — the catalog backbone (cards, variants, prices, images, intelligence) — **CC has write authority as of 2026-05-17**
- `data/miru_dossiers.db` — fact graph (card_facts, fact_sources, confidence_records)
- `data/miru_learning_dossiers.db` — verification process log (accepted-fact history, corroboration)
- `data/miru_dev_training_reviews.db` — dev-training review subsystem (the cleanest piece of the system)
- `data/logueos_memory.db` — tactical memory (kernel-side; read via miru_memory MCP tool)

Triggers: "update the catalog", "backfill provenance", "fix the variant rows", "dedup", "schema change", "DB migration", "add a column", any UPDATE/INSERT/DELETE on the catalog.

## The architecture

Three intelligence layers form a 3-stage verification funnel (NOT competing systems):

1. **Layer C — `miru_learning_dossiers.db`** — verification process log. Where facts get corroborated before acceptance.
2. **Layer B — `miru_dossiers.db`** — fact graph with provenance. Accepted facts land here with sources + confidence.
3. **Layer A — `card_catalog.db`** — final flat state. What the storefront and AI read. Backbone.

Flow: C → B → A. The C→B paths exist; B→A flatten (via `tools/miru_project_sync.py`) was stalled since 2026-03-25 (last broad write to `card_intelligence`).

When working on catalog content: you're writing to Layer A (flat state). When working on fact provenance / sources: you're writing to Layer B. Don't confuse the two.

## Required discipline before ANY UPDATE/INSERT/DELETE

These are non-negotiable for CC writes to `card_catalog.db` (per memory `project_cc_card_catalog_write_authority`):

1. **Backup first. Always.**

   ```bash
   cp data/card_catalog.db data/card_catalog.db.bak.$(date +%Y%m%d_%H%M%S)
   ```

   No exceptions, even for one-row updates. The backup is your rollback.

2. **Log the change.**

   Append to a `data/*.log` file (or a per-ticket log like `data/PRO-XXX_writes.log`) with: the predicate, expected row count, actual row count, source-of-truth used. Format suggestion:

   ```
   2026-05-17T18:34:21Z | PRO-904 | UPDATE card_variants SET official_provenance=? WHERE print_id IN (...) | expected=218 | actual=218 | source=data/bandai_op01_crawl.json
   ```

3. **Surface the diff in the commit message.**

   Don't hide DB changes in "misc" commits. The commit body should name the table, the row count, and the predicate.

4. **Verify after.**

   Re-query the affected rows. Confirm the change landed as expected. If row count differs from expected, ROLLBACK from backup and ESCALATE.

## Out-of-scope writes (require operator approval)

CC does NOT have standing authority for these:

- `ALTER TABLE` — schema changes (new columns, type changes, constraint changes).
- `CREATE TABLE` — new tables.
- `DROP TABLE` or any data-destroying operation beyond row-level cleanup.
- Index changes.
- Writing to `card_catalog.db` from outside `project-miru` worktrees.
- Anything touching `miru_dossiers.db` or `miru_learning_dossiers.db` schema.

When a task requires any of these: STOP and ask the operator. Schema changes get a Linear ticket of their own.

## Append-only file rules

The orchestration append-only chains live in the kernel (`D:\dev\LogueOS-Orchestrator\data\`) — never write to those from miru worktrees except via the `tools/emit_*.py` helpers, which auto-resolve `LOGUEOS_DATA_DIR`.

The only miru-side append-only file: `data/miru_worker_runs.jsonl`. Same rules: never edit, truncate, sort, dedupe, or read-modify-write. Workers append via the helper, not by hand.

## Known data-quality landmines (don't make them worse)

Per the data-layer audit (`docs/audits/data-layer-audit.md`):

- **`card_relationships`** — 61,679 rows, 99.9% noise from a single 2026-03-24 text-analysis batch. Only 60 are operator-verified (Rosinante synergy set). If working on relationships, scope to the verified 60 unless explicitly dedupping the noise.
- **`miru_card_insights`** — templating bug, 242 rows never approved (OP01-002 row reads "Roronoa Zoro is a flex piece"). Don't promote any insight without operator review.
- **`card_intelligence.confidence_score`** — 0.93 on 88% of rows is almost certainly a default, not a metric. Don't treat it as authoritative confidence.
- **OP01-001 row in `card_intelligence`** has `2026-12-31` sentinel dates. Don't propagate.
- **`miru_perception_ledger` family** — 4 tables, 45-column schema, 0 rows, zero writers. Schema-only. Decide drop-vs-build with the operator before adding writers.

## How CC uses this skill

When doing DB write work:

1. Read this skill at the start of the task (don't try to remember the rules).
2. Apply the 4-step discipline (backup, log, commit, verify) for every batch.
3. Before any schema-touching work, STOP and ask.
4. After the work lands, surface the diff to the operator clearly.

## How CC uses this skill in architect/brainstorm mode

When discussing DB design, schema evolution, or data integrity:

- Use the precise terminology (3-stage funnel, Layer A/B/C, append-only).
- Recognize when a design decision requires a schema change (= operator approval needed = ticket scope).
- Distinguish between content writes (CC authority) and schema writes (operator authority).
- Write the synthesis of any brainstorm output about DB design to the repo (Notion is retired and is no longer a target for design synthesis).

## Reference

- Data-layer audit: `D:\dev\miru\docs\audits\data-layer-audit.md` (1,222 lines, authored 2026-05-16)
- Write authority memory: `project_cc_card_catalog_write_authority`
- OP01 corpus state: `project_op01_corpus_state`
- Database rules (kernel canon): `D:\dev\LogueOS-Orchestrator\.logueos\reference\database-rules.md`

## What this skill is NOT

- Not a SQL tutorial.
- Not a substitute for reading the audit when working on the affected tables.
- Not authority to bypass the schema-change rule. That rule is hard.
