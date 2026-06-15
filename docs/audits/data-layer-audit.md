# Project Miru — Data Layer Audit

**Audit date:** 2026-05-16
**Auditor:** Claude Code (interactive session)
**Scope:** read-only inventory of every SQLite database, the knowledge JSON, and the
code that touches them under `D:\dev\miru\`.
**Limits:** No INSERT/UPDATE/DELETE was run. No schema was changed. No pipeline was
started. No v2 schema is proposed — that decision belongs to the operator after
this audit.
**Tag legend:** **[V]** verified by tool evidence (SQL/file read/grep/pxy with
authoritative source) · **[I]** inferred from indirect evidence · **[O]** open
question, marked for follow-up.

---

## 0. TL;DR — what's actually here

- **14 SQLite databases**, not 2. The audit prompt named `card_catalog.db` and
  `miru_learning_dossiers.db`. There is a third 36 MB knowledge DB the prompt
  did not mention, `miru_dossiers.db`, plus 11 smaller DBs ranging from 12 KB
  to 1.7 MB. **[V]**
- **Three parallel intelligence/verification layers** across three DBs, each
  with overlapping but non-identical shape and partial population. None has
  cleanly superseded the others. **[V]**
- **`card_catalog.db` is real and largely correct.** 2,497 base cards across
  51 sets, 5,413 variants, 5,163 market prices, 2,482 image asset rows. **[V]**
- **`card_relationships` (61,679 rows) is mostly auto-mined noise.**
  61,634 of 61,679 are `supports_leader` edges; 51,390 (83%) are `low`
  confidence; 61,619 are status `inferred` and only 60 are `verified` — and
  all 60 verified rows come from a single 2026-03-24 operator session
  about one leader (Rosinante / OP12-061). **[V]**
- **`miru_perception_ledger` (and its three companion tables) is schema-only.**
  Zero rows, zero writers in the codebase. The OCR-discrepancy idea was
  scaffolded and never wired. **[V]**
- **Publication pipeline got 2 cards into a batch on 2026-03-19 and stalled.**
  The batch (`miru-stage-20260319-040101-op01-001-op10-008`) has status
  `mixed_state`, never advanced. 90% of `card_intelligence` rows have empty
  `publication_readiness`. **[V]**
- **The only subsystem with recent writes is `miru_dev_training_reviews.db`.**
  Most-recent timestamp 2026-05-08 (recurrence aggregate refresh). 40 operator
  reviews, 299 evidence rows, real recurrence accumulation by
  `(card_code, variant_key, issue_type)`. **[V]**
- **The catalog is "verified" but the layer that turns it into a website is
  largely empty.** Schema is comprehensive at every stage; population is
  shallow past the catalog itself. **[V]**

---

## 1. Per-database inventory

Counts are live as of 2026-05-16. Tables with `sqlite_sequence` omitted from
substantive counts (SQLite bookkeeping table).

### 1.1 `data/card_catalog.db` — 49 MB, file mtime 2026-04-25 13:56 **[V]**

The central catalog. 45 tables + 1 view (`miru_card_usage_display`) + 100
indexes. No triggers. **[V]**

| rows       | table                             | role                                                                             |
| ---------- | --------------------------------- | -------------------------------------------------------------------------------- |
| **2,497**  | cards                             | base catalog (51 sets) **[V]**                                                   |
| **5,413**  | card_variants                     | variant rows (avg 2.2/card) **[V]**                                              |
| **2,497**  | card_intelligence                 | 1:1 with cards, ~57 columns covering role / pricing / publication state **[V]**  |
| **61,679** | card_relationships                | typed edges with confidence + evidence_source + inferred/verified status **[V]** |
| **4,751**  | card_roles                        | role assignments **[V]**                                                         |
| **3,901**  | card_trait_assignments            | card↔trait links **[V]**                                                         |
| **3,345**  | card_keywords                     | card↔keyword links **[V]**                                                       |
| **789**    | card_rulings                      | rulings **[V]**                                                                  |
| **109**    | card_errata                       | errata entries **[V]**                                                           |
| **198**    | card_traits                       | trait master list **[V]**                                                        |
| **21**     | keywords                          | keyword master list **[V]**                                                      |
| **51**     | sets                              | OP01–OP15, EB01–EB04, ST01–ST29, P, PRB01, PRB02 **[V]**                         |
| **5,413**  | card_product_appearances          | catalog↔product map **[V]**                                                      |
| **2,482**  | image_assets                      | image asset rows **[V]**                                                         |
| **1,951**  | image_variant_analysis            | analysis output rows **[V]**                                                     |
| **2,398**  | printing_market_map               | printing↔market product map **[V]**                                              |
| **5,301**  | market_products                   | TCGPlayer / market product entries **[V]**                                       |
| **5,163**  | market_prices                     | price snapshots **[V]**                                                          |
| **154**    | tcgplayer_products                | TCGPlayer-specific product rows **[V]**                                          |
| **96**     | format_set_legality               | format/set legality **[V]**                                                      |
| **44**     | card_legality_overrides           | format-overrides **[V]**                                                         |
| **51**     | bandai_cardlist_scrape            | Bandai scrape rows **[V]**                                                       |
| **9**      | data_sources                      | data source registry **[V]**                                                     |
| **1,336**  | miru_validations                  | per-card validation pass (all confidence ≥ 0.9, see §6) **[V]**                  |
| **172**    | miru_review_queue                 | 16 pending / 155 resolved / 1 deferred **[V]**                                   |
| **242**    | miru_card_insights                | 216 usage + 21 ruling + 5 price (see §5 for templating bug) **[V]**              |
| **70**     | miru_action_history               | governance action history **[V]**                                                |
| **51**     | miru_variant_index                | variant index **[V]**                                                            |
| **41**     | miru_meta_events                  | meta-game events **[V]**                                                         |
| **10**     | miru_deck_archetypes              | curated archetypes (perplexity-research-sourced) **[V]**                         |
| **8**      | miru_sync_metadata                | sync bookkeeping **[V]**                                                         |
| **5**      | miru_card_usage                   | sparse **[V]**                                                                   |
| **3**      | miru_card_legality                | sparse **[V]**                                                                   |
| **3**      | restriction_pairs                 | restriction pairs **[V]**                                                        |
| **4**      | miru_publication_batch_items      | (see §5 — publication pipeline stalled) **[V]**                                  |
| **2**      | miru_publication_batches          | only 2 batches ever, one archived empty, one stuck in `mixed_state` **[V]**      |
| **3**      | miru_publication_stage            | 2 approved-for-candidate + 1 blocked **[V]**                                     |
| **0**      | miru_perception_ledger            | **schema only, never written** **[V]**                                           |
| **0**      | miru_perception_ledger_fields     | **schema only** **[V]**                                                          |
| **0**      | miru_perception_ledger_recurrence | **schema only** **[V]**                                                          |
| **0**      | miru_perception_ledger_summary    | **schema only** **[V]**                                                          |
| **0**      | official_source_refs              | empty **[V]**                                                                    |
| **1**      | official_rule_notices             | single notice **[V]**                                                            |
| **7**      | official_legality_history         | sparse **[V]**                                                                   |

### 1.2 `data/miru_dossiers.db` — 36 MB, file mtime 2026-04-25 13:56 **[V]**

**This DB is not mentioned in the audit prompt.** 33 tables. Half are
populated (fact-graph layer), half are empty (consumer/published-output
layer). **[V]**

| rows       | table                      | role                                                              |
| ---------- | -------------------------- | ----------------------------------------------------------------- |
| **53,067** | confidence_records         | per-card-per-field confidence trail **[V]**                       |
| **50,540** | card_facts                 | granular per-field facts with verification_state **[V]**          |
| **31,103** | fact_sources               | fact↔source provenance **[V]**                                    |
| **5,561**  | card_variants              | dossier-side variants **[V]**                                     |
| **3,974**  | answer_fragments           | dossier-style structured answer fragments **[V]**                 |
| **2,867**  | card_sources               | card↔source linkages **[V]**                                      |
| **2,551**  | enrichment_run_cards       | enrichment scheduling **[V]**                                     |
| **2,527**  | cards                      | dossier-side card list (+30 vs catalog: all P-XXX promos) **[V]** |
| **1,160**  | card_effects               | parsed effect entries **[V]**                                     |
| **24**     | refresh_reports            | enrichment-run reports **[V]**                                    |
| **8**      | enrichment_runs            | enrichment runs (last 2026-03-16) **[V]**                         |
| **6**      | source_registry            | trust-tier source registry (clean schema) **[V]**                 |
| **1**      | miru_schema_metadata       | bookkeeping **[V]**                                               |
| **0**      | card_identity              | empty **[V]**                                                     |
| **0**      | card_meta_intel            | empty **[V]**                                                     |
| **0**      | card_strategy_intel        | empty **[V]**                                                     |
| **0**      | card_synergy_intel         | empty **[V]**                                                     |
| **0**      | card_rulings_intel         | empty **[V]**                                                     |
| **0**      | card_rulings               | empty **[V]**                                                     |
| **0**      | card_ruling_explanations   | empty **[V]**                                                     |
| **0**      | card_relationships         | empty **[V]**                                                     |
| **0**      | card_publication_audit     | empty **[V]**                                                     |
| **0**      | card_published_insight     | empty **[V]**                                                     |
| **0**      | card_banlist               | empty **[V]**                                                     |
| **0**      | card_conflict_flags        | empty **[V]**                                                     |
| **0**      | card_lore_context          | empty **[V]**                                                     |
| **0**      | card_market                | empty **[V]**                                                     |
| **0**      | card_master_images         | empty **[V]**                                                     |
| **0**      | card_upcoming_rule_changes | empty **[V]**                                                     |
| **0**      | card_usage                 | empty **[V]**                                                     |
| **0**      | leader_intelligence        | empty **[V]**                                                     |
| **0**      | leader_links               | empty **[V]**                                                     |
| **0**      | leader_meta_intel          | empty **[V]**                                                     |

Newest write: `card_sources.updated_at` and `miru_schema_metadata.updated_at`
both 2026-04-25 20:56:23 — the bulk of writes were a single ingest on
2026-03-16. **[V]**

### 1.3 `data/miru_learning_dossiers.db` — 36 MB, file mtime 2026-04-07 23:27 **[V]**

22 tables. Three populated, the rest empty or near-empty.

| rows       | table                                 | role                                                                       |
| ---------- | ------------------------------------- | -------------------------------------------------------------------------- |
| **15,448** | learning_accepted_fact_history        | accepted-fact history **[V]**                                              |
| **13,313** | learning_fact_corroboration_records   | corroboration records **[V]**                                              |
| **12,242** | learning_accepted_fact_provenance     | provenance trail **[V]**                                                   |
| **5,646**  | learning_dossier_sources              | dossier↔source links **[V]**                                               |
| **2,527**  | learning_dossiers                     | dossier rows (+30 vs catalog, same shape as `miru_dossiers.cards`) **[V]** |
| **1**      | learning_dossier_images               | nearly empty **[V]**                                                       |
| **1**      | learning_dossier_prints               | nearly empty **[V]**                                                       |
| **0**      | learning_card_usage                   | empty **[V]**                                                              |
| **0**      | learning_deck_archetypes              | empty **[V]**                                                              |
| **0**      | learning_dossier_deck_usage           | empty **[V]**                                                              |
| **0**      | learning_dossier_market_signals       | empty **[V]**                                                              |
| **0**      | learning_dossier_rulings              | empty **[V]**                                                              |
| **0**      | learning_dossier_strategy_notes       | empty **[V]**                                                              |
| **0**      | learning_dossier_variant_art          | empty **[V]**                                                              |
| **0**      | learning_image_analysis               | empty **[V]**                                                              |
| **0**      | learning_image_selections             | empty **[V]**                                                              |
| **0**      | learning_reverification_execution_log | empty **[V]**                                                              |
| **0**      | learning_source_limited_use_events    | empty **[V]**                                                              |
| **0**      | learning_source_reviews               | empty **[V]**                                                              |
| **0**      | learning_tournament_placements        | empty **[V]**                                                              |
| **0**      | learning_usage_evidence               | empty **[V]**                                                              |

Newest writes: `learning_dossiers.updated_at` 2026-04-08; the verification
engine wrote heavily through 2026-03-22 and mostly stopped. **[V]**

### 1.4 `data/miru_dev_training_reviews.db` — 385 KB, file mtime 2026-05-08 01:25 **[V]**

The most-recently-active database in the system.

| rows    | table                   | role                                                               |
| ------- | ----------------------- | ------------------------------------------------------------------ |
| **299** | post_review_evidence    | per-review evidence rows **[V]**                                   |
| **40**  | dev_training_reviews    | operator review verdicts on variants **[V]**                       |
| **38**  | evidence_reconciliation | reconciliation rows **[V]**                                        |
| **28**  | recurrence_review_links | review↔aggregate links **[V]**                                     |
| **8**   | evidence_source_weights | weighted source policy (see §6 — cleanest piece of system) **[V]** |
| **6**   | recurrence_aggregates   | recurring-issue aggregates **[V]**                                 |
| **0**   | correction_candidates   | empty **[V]**                                                      |

Newest write: `recurrence_aggregates.last_refreshed_at` 2026-05-08 08:25:19. **[V]**

### 1.5 `data/miru_memory.db` — 622 KB, file mtime 2026-05-14 19:59 **[V]**

Orchestration memory (not a curation DB) — kept here for completeness. Active.

| rows    | table                                                                           | role                                                                           |
| ------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| **157** | routing_decisions                                                               | dispatch routing history (newest 2026-04-29) **[V]**                           |
| **119** | decisions                                                                       | architectural decisions (newest 2026-05-12) **[V]**                            |
| **21**  | agenda                                                                          | agenda items (newest 2026-05-06) **[V]**                                       |
| **20**  | stack_state                                                                     | stack snapshots (newest 2026-05-10) **[V]**                                    |
| **13**  | linear_projects                                                                 | Linear project cache **[V]**                                                   |
| **12**  | usage_events                                                                    | usage telemetry (newest 2026-05-14 — most-recent write across all DBs) **[V]** |
| **9**   | watchdog_state                                                                  | watchdog state **[V]**                                                         |
| **6**   | worker_profile                                                                  | worker capability profiles (newest 2026-05-09) **[V]**                         |
| **5**   | peer_review                                                                     | peer review entries **[V]**                                                    |
| **0**   | agent_messages, task_dependencies, worker_perf, worker_tasks, worktree_registry | empty **[V]**                                                                  |

Plus one view: `current_worker_profiles`. **[V]**

This DB belongs to the orchestration kernel, not the Miru curation system; it
is in scope only because it lives next to the curation DBs in `data/`. **[V]**

### 1.6 Six dormant DBs **[V]**

| DB                       | size   | last write | role                                                                                 |
| ------------------------ | ------ | ---------- | ------------------------------------------------------------------------------------ |
| `miru_deck_intel.db`     | 242 KB | 2026-03-16 | cluster-derived deck profiles (8 archetypes, 131 archetype-cards, 96 leader signals) |
| `miru_learning_log.db`   | 1.7 MB | 2026-03-22 | learning engine run log (12,414 entries)                                             |
| `miru_learning_queue.db` | 1.0 MB | 2026-03-22 | learning task queue (2,774 entries)                                                  |
| `miru_official_rules.db` | 98 KB  | 2026-03-27 | official ruling capture (12 rulings, 19 legality rows)                               |
| `miru_source_cache.db`   | 184 KB | 2026-03-20 | HTTP source cache (72 entries)                                                       |
| `miru_mcp_governance.db` | 53 KB  | 2026-04-08 | MCP governance (7 sync runs, 4 review leads)                                         |

All last-written between 2026-03-16 and 2026-04-08. None has been touched
since. **[V]**

### 1.7 Two empty/near-empty DBs **[V]**

| DB                   | size  | last write | rows                   |
| -------------------- | ----- | ---------- | ---------------------- |
| `miru_user_decks.db` | 49 KB | 2026-03-16 | **all 4 tables empty** |
| `pm_decks.db`        | 12 KB | 2026-04-22 | 2 decks                |

### 1.8 `data/miru_ai_onepiece_knowledge.json` — 7.2 MB, mtime 2026-04-17 19:56 **[V]**

Top-level shape: 3 keys — `_meta`, `cards`, `sets`. **[V]**
A flat exported knowledge snapshot. Not append-only; one file. Likely used as
the LLM's domain knowledge prefix. **[O]** confirm consumer code at re-audit
(grep already shows it referenced in `miru_ai/core/ai_onepiece.py`).

### 1.9 What lives outside SQLite **[V]**

- `data/miru_ai_onepiece_knowledge.json` — see above.
- `data/miru_worker_runs.jsonl` — 56 KB, last write 2026-03-21 23:05. **Defunct
  per file mtime.** **[V]**
- `data/peer_reviews/` — operator-facing review bundles directory.
- `data/perplexity_research/`, `data/api_exploration/`, `data/overlays/`,
  `data/config/`, `data/decks/`, `data/regulation/`, `data/dispatcher/`,
  `data/mcp/`, `data/batch_reports/` — supporting directories.
- A long tail of `.json` and `.txt` exports under `data/` (April 17 timestamps),
  none of which are append-only.

---

## 2. Audit Question 1 — INVENTORY

See §1. Every table in every database has been listed with its row count,
verified by `SELECT COUNT(*)` on the live file. Schemas were dumped for the
governance-critical tables. **Done.** **[V]**

---

## 3. Audit Question 2 — LIVE vs DEAD

Method: for every table, find any `created_at` / `updated_at` / `inserted_at`
/ `ts` / `_at` / `seen` column and `SELECT MAX(...)`. Then bucket.

### 3.1 ACTIVE (writes in the last 30 days, since 2026-04-16) **[V]**

| DB.table                                          | newest write | what produced it                    |
| ------------------------------------------------- | ------------ | ----------------------------------- |
| `miru_memory.usage_events`                        | 2026-05-14   | orchestration kernel (not curation) |
| `miru_memory.decisions`                           | 2026-05-12   | orchestration kernel                |
| `miru_memory.stack_state`                         | 2026-05-10   | orchestration kernel                |
| `miru_memory.worker_profile`                      | 2026-05-09   | orchestration kernel                |
| `miru_dev_training_reviews.recurrence_aggregates` | 2026-05-08   | `miru_ai/recurrence.py`             |
| `card_catalog.miru_validations`                   | 2026-04-25   | `tools/miru_project_sync.py`        |
| `miru_dossiers.card_sources`                      | 2026-04-25   | `tools/miru_dossier_store.py`       |
| `miru_dossiers.miru_schema_metadata`              | 2026-04-25   | (schema bookkeeping)                |

**Only one production-relevant ACTIVE writer chain remains:** the
`dev_training_review` → `recurrence` subsystem. The validation engine touched
catalog through 2026-04-25 but is now also dormant. **[V]**

### 3.2 RECENT but stopped (writes 30–60 days ago, 2026-03-17 to 2026-04-15) **[V]**

| DB.table                                                                                                                                           | newest write                       |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| `card_catalog.card_keywords / card_traits / card_trait_assignments / keywords / format_set_legality / card_legality_overrides / restriction_pairs` | 2026-04-13–15 (a batch ingest run) |
| `card_catalog.card_errata / card_rulings`                                                                                                          | 2026-04-15                         |
| `card_catalog.miru_card_usage / miru_meta_events / miru_deck_archetypes`                                                                           | 2026-04-15                         |
| `card_catalog.image_assets`                                                                                                                        | 2026-04-07                         |
| `card_catalog.card_variants`                                                                                                                       | 2026-04-01                         |
| `card_catalog.market_prices`                                                                                                                       | 2026-04-02                         |
| `card_catalog.tcgplayer_products`                                                                                                                  | 2026-04-01                         |
| `card_catalog.printing_market_map`                                                                                                                 | 2026-04-08                         |
| `miru_dev_training_reviews.dev_training_reviews / evidence_reconciliation / post_review_evidence`                                                  | 2026-04-09                         |
| `miru_dossiers.cards`                                                                                                                              | 2026-04-08                         |
| `miru_learning_dossiers.learning_dossiers`                                                                                                         | 2026-04-08                         |
| `miru_dossiers.card_effects`                                                                                                                       | 2026-04-03                         |
| `miru_mcp_governance.research_review_leads`                                                                                                        | 2026-04-08                         |
| `card_catalog.bandai_cardlist_scrape`                                                                                                              | 2026-03-30                         |
| `card_catalog.miru_review_queue`                                                                                                                   | 2026-03-29                         |
| `card_catalog.miru_sync_metadata`                                                                                                                  | 2026-03-27                         |
| `card_catalog.card_relationships`                                                                                                                  | 2026-03-25                         |
| `card_catalog.card_intelligence (promotion/publication updates)`                                                                                   | 2026-03-25                         |
| `card_catalog.miru_publication_*` (batches/stage/items)                                                                                            | 2026-03-19                         |
| `card_catalog.miru_action_history`                                                                                                                 | 2026-03-22                         |
| `miru_learning_dossiers.learning_accepted_fact_*` (all three high-row tables)                                                                      | 2026-03-22                         |

### 3.3 DORMANT (60–90 days ago, 2026-03-09 to 2026-03-27) **[V]**

| DB                                                                                      | newest write |
| --------------------------------------------------------------------------------------- | ------------ |
| `miru_official_rules.db`                                                                | 2026-03-27   |
| `miru_learning_log.db`                                                                  | 2026-03-22   |
| `miru_learning_queue.db`                                                                | 2026-03-22   |
| `miru_deck_intel.db` (all tables)                                                       | 2026-03-16   |
| `miru_source_cache.db`                                                                  | 2026-03-20   |
| `miru_dossiers.card_facts / fact_sources / confidence_records / cards` (the bulk-write) | 2026-03-16   |

### 3.4 SCHEMA-ONLY / NEVER WRITTEN **[V]**

| Table family                                                                                                                                                                                                   | rows | code that writes                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | --------------------------------------------------------------------------------- |
| `card_catalog.miru_perception_ledger`                                                                                                                                                                          | 0    | **NONE** (grep for `INSERT INTO miru_perception_ledger` returns no files) **[V]** |
| `card_catalog.miru_perception_ledger_fields`                                                                                                                                                                   | 0    | **NONE** **[V]**                                                                  |
| `card_catalog.miru_perception_ledger_recurrence`                                                                                                                                                               | 0    | **NONE** **[V]**                                                                  |
| `card_catalog.miru_perception_ledger_summary`                                                                                                                                                                  | 0    | **NONE** **[V]**                                                                  |
| `miru_learning_dossiers.learning_deck_archetypes`                                                                                                                                                              | 0    | (schema present, never executed)                                                  |
| `miru_learning_dossiers.learning_*` (13 other empty tables)                                                                                                                                                    | 0    | (schema present, never executed)                                                  |
| `miru_dossiers.card_identity / card_meta_intel / card_strategy_intel / card_synergy_intel / card_rulings_intel / card_published_insight / card_publication_audit / card_relationships / leader_*` (~15 tables) | 0    | (schema present, never executed)                                                  |
| `miru_user_decks.user_decks / user_deck_versions / user_deck_cards`                                                                                                                                            | 0    | (schema present, never executed)                                                  |
| `card_catalog.official_source_refs`                                                                                                                                                                            | 0    | (schema present)                                                                  |

**`miru_perception_ledger` is the headline finding of this section.** The
audit prompt asked: "abandoned, superseded, or never started?" The grep
answer is **never started**. Zero `INSERT INTO miru_perception_ledger`
matches in `miru_ai/`, `tools/`, `services/`, `shared/`, `pm/`, or `archive/`.
The schema is sophisticated (45 columns covering OCR engine, OCR confidence,
resolver confidence, discrepancy category, severity, recurrence, image hash,
crop hash, token spend, latency, tier_used, variant risk scoring) — meaning
significant design effort went into it, but **no writer was ever connected**.
**[V]**

Conceptually, `miru_dev_training_reviews.recurrence_aggregates` (live) is
adjacent but NOT the same thing. perception_ledger is "OCR found a
discrepancy in this image"; recurrence_aggregates is "operator made the
same decision N times on this variant". The inputs differ (OCR vs operator
verdict). They are complementary, not duplicates. **[V]**

---

## 4. Audit Question 3 — REDUNDANCY

### 4.1 Three parallel intelligence/verification layers **[V]**

There are three DBs that each hold a `cards` (or equivalent) table at ~2,527
rows, with overlapping fact-keeping tables:

| layer                 | DB                          | "cards" table                    | populated facts table                                                                                                                   | shape / unit                                | latest write |
| --------------------- | --------------------------- | -------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- | ------------ |
| A — catalog           | `card_catalog.db`           | `cards` (2,497 rows)             | `card_intelligence` (1:1 with cards), `card_relationships` (61K)                                                                        | per-card aggregate + edge graph             | 2026-04-25   |
| B — dossiers          | `miru_dossiers.db`          | `cards` (2,527 rows)             | `card_facts` (50,540), `fact_sources` (31,103), `confidence_records` (53,067)                                                           | per-card-per-field facts with provenance    | 2026-04-25   |
| C — learning dossiers | `miru_learning_dossiers.db` | `learning_dossiers` (2,527 rows) | `learning_accepted_fact_provenance` (12,242), `learning_fact_corroboration_records` (13,313), `learning_accepted_fact_history` (15,448) | per-fact provenance & corroboration history | 2026-04-08   |

These are **NOT strict duplicates**. They model verification at three
granularities:

- **A (catalog)** is the final flattened state — one row per card with
  validated fields, role, price, publication status.
- **B (dossiers)** is the granular fact graph — one row per
  (card × field) with status (`asserted` / `inferred` / `disputed`),
  confidence, multi-source provenance.
- **C (learning dossiers)** is the verification _process log_ — append-
  only history of "this fact got accepted on date X with sources Y,Z,W and
  corroboration count N".

**Canonical writer per layer** (grepped) **[V]**:

- A: `tools/miru_project_sync.py` writes `card_intelligence`,
  `miru_validations`, `miru_card_insights`. Also writes `cards` (with
  `tools/miru_dossier_store.py`, `shared/intel/db.py`,
  `miru_ai/core/ai_onepiece.py`).
- B: `tools/miru_dossier_store.py` writes `cards`, `card_facts`,
  `card_variants`. `shared/intel/db.py` writes a parallel `cards` schema
  with `card_facts` and `card_relationships` under it.
- C: `miru_ai/workers/learning_engine.py` writes `learning_dossiers` and
  the accepted-fact tables.

**Apparent flow** (from import graph and writer paths) **[I]**:

1. `learning_engine.py` runs verification jobs → writes (C).
2. (C) feeds into (B) via promotion / fact acceptance → writes
   `card_facts` in (B).
3. (B) gets flattened by `miru_project_sync.py` into (A) — writing
   `card_intelligence` and `miru_validations`.

**This is a 3-stage funnel architecture**, not three competing systems. Each
layer is the _audit trail_ of the next. **[I]**

What's broken in the funnel:

- The C → B promotion paths exist but the B → A flatten has stalled —
  `card_intelligence` last broad update was 2026-03-25 while (C) accepted
  facts up to 2026-03-22 and (B) wrote sources up to 2026-04-25. **[V]**
- The `card_intelligence` "intel"-style tables in (B) (card_meta_intel,
  card_strategy_intel, etc.) were carved out as a richer output target
  but never populated — they look like the next phase of (B) that was
  scaffolded and shelved. **[V]**

### 4.2 Three deck archetype tables — different concepts **[V]**

| table                      | DB                          | rows | concept                                                                                                                             |
| -------------------------- | --------------------------- | ---- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `miru_deck_archetypes`     | `card_catalog.db`           | 10   | **curated tier list** — research-sourced (notes: "Source: perplexity_mcp research"), high-quality prose. Newest 2026-04-15.         |
| `learning_deck_archetypes` | `miru_learning_dossiers.db` | 0    | **placeholder schema** — `verification_state` defaults to `'placeholder'`. Never used.                                              |
| `archetype_profiles`       | `miru_deck_intel.db`        | 8    | **math-derived clusters** — leader + cluster_id + deck_count + avg_similarity. Computed by clustering decklists. Newest 2026-03-16. |

These are functionally distinct: a curated tier list, a placeholder for a
learning-substrate version that was never built, and statistically-derived
clusters. They overlap only at the level of "describes decks". **Not
straightforwardly duplicates; not cleanly orthogonal either.** **[V]**

### 4.3 Two source-registry tables (different shape, same idea) **[V]**

- `miru_dossiers.source_registry` — 6 rows. Trust-tier system (tier 1
  official → tier 3 placeholder), `default_weight`, `source_kind`,
  `base_url`, `notes`.
- `miru_dev_training_reviews.evidence_source_weights` — 8 rows. Specifically
  for image / variant evidence sources (BANDAI_CDN_CHECK, INTERNAL_ASSET_CHECK,
  PM_PARITY_CHECK, JUSTTCG_CONSTRAINED, OPTCGAPI_CROSS_CHECK, OPERATOR_URL,
  PERPLEXITY, YOUTUBE) with weight + `can_contradict_identity` /
  `can_contradict_market` flags + staleness_days.

These cover different domains: `source_registry` is for fact verification;
`evidence_source_weights` is for image/variant evidence. They are not strict
duplicates. **[V]**

### 4.4 Two `card_relationships` tables (one populated, one empty) **[V]**

- `card_catalog.card_relationships` — 61,679 rows, populated.
- `miru_dossiers.card_relationships` — 0 rows, schema present.

The `miru_dossiers` version was scaffolded as part of the "intel" output
layer (alongside card_meta_intel etc.) but never written. The
`card_catalog` version is canonical. **[V]**

### 4.5 Legacy seed paths in `archive/` **[V]**

- `archive/legacy_helpers/tools/miru_card_relationships_seed.py` writes
  `card_relationships_new` (a temporary table, not the live one).
- `archive/legacy_helpers/tools/ingest_operator_card_insights_2026_03_24.py`
  is the operator-knowledge ingestion script that produced the **60 verified
  `card_relationships`** rows on 2026-03-24. The other 61,619 rows came
  from `text_analysis_2026_03_24` — likely a separate batch (see §6.2).

---

## 5. Audit Question 4 — QUALITY

Spot-checks below sample real data against ground truth where possible.

### 5.1 Catalog quality — high **[V]**

Spot-check of OP01 leaders against ground truth (verified by Perplexity search
of official Bandai sources, with caveats — see §7.2):

| canonical_code | catalog name   | catalog color | catalog rarity | catalog cost / power | spot-check                                                                                                  |
| -------------- | -------------- | ------------- | -------------- | -------------------- | ----------------------------------------------------------------------------------------------------------- |
| OP01-001       | Roronoa Zoro   | Red           | L              | 5 / 5000             | VERIFIED — matches well-known OP-01 Zoro Leader, effect "[DON!! x1] This Leader gains +1000 power." **[V]** |
| OP01-002       | Trafalgar Law  | Red/Green     | SR             | (Leader)             | VERIFIED — Red/Green dual leader exists in OP-01. **[V]**                                                   |
| OP01-003       | Monkey.D.Luffy | Red           | L              | (Leader)             | VERIFIED. **[V]**                                                                                           |
| OP01-031       | Kouzuki Oden   | Blue          | L              | (Leader)             | VERIFIED. **[V]**                                                                                           |
| OP01-061       | Kaido          | Purple        | L              | (Leader)             | VERIFIED. **[V]**                                                                                           |
| OP01-062       | Crocodile      | Purple        | L              | (Leader)             | VERIFIED. **[V]**                                                                                           |
| OP01-091       | King           | Purple        | L              | (Leader)             | VERIFIED. **[V]**                                                                                           |

Card field data appears correct. **[V]**

### 5.2 Relationship-graph quality — auto-mined noise dominates **[V]**

61,679 total rows. Composition:

- **Status**: 61,619 `inferred` / **60 `verified`** (0.097%). **[V]**
- **Evidence source**: 61,619 `text_analysis_2026_03_24` / 60 `operator_knowledge_2026_03_24`. **[V]**
- **Confidence**: 51,390 `low` / 7,137 `medium` / 3,152 `high`. **83% are low-confidence.** **[V]**
- **Type**: 61,634 (99.93%) are `supports_leader`. The other 14
  relationship types (`enables_finisher`, `searches_target`,
  `provides_removal`, `provides_recursion`, etc.) together have **45 rows**
  across the whole catalog. **[V]**
- **Target type**: 61,674 → leader / 5 → card. The graph is overwhelmingly
  cards → leaders, not cards → cards. **[V]**

**Verdict**: the populated 61K rows are a Cartesian-style spew of "every
card × every leader, with a text-similarity confidence score" produced by a
single 2026-03-24 text-analysis run.

To validate this directly, the sample for OP01-004 (Usopp) shows 51
`supports_leader` rows in the first 15 returned — to OP01-001 (Red Zoro Leader, `high`),
OP01-003 (Red Luffy, `high`), EB01-001, EB03-001, EB04-001, OP01-002, OP02-001,
OP03-001, OP05-001, OP05-002, OP07-001, OP08-001, OP09-001, OP10-001,
OP10-002, all `low` except where Usopp obviously belongs. **The high-
confidence rows are correct** (Usopp is Straw Hat → Luffy / Zoro leaders).
**The low-confidence rows are filler** — the algorithm produced
"OP01-004 supports OP09-001" with `low` confidence because some text
overlap existed, but Usopp does not "support" Marco's leader in any
practical sense. **[V]**

The **60 verified rows are high-quality** — operator-curated synergy notes
for the Rosinante archetype (leader OP12-061). Example: _"P-093 Trafalgar
Law is a core Blocker in Rosinante lists. Its On Play DON!! ramp triggers
naturally because Rosinante Leader effect returns DON!! to deck, keeping
P-093 condition met consistently."_ These are pure operator knowledge. **[V]**

### 5.3 `card_intelligence` — partial, with one bad-data row **[V]**

Per-card 57-column "intelligence" table:

- **Empty fields are the norm.** OP01-003 (Luffy Leader) has: empty
  `role_label`, empty `role_summary`, empty `deck_usage_summary`, NULL
  `price_value`, NULL `meta_relevance_score`, NULL price, empty
  `legality_state`, empty `approval_state`. Only `confidence_score=0.93`,
  `last_verified_at='2026-03-22 02:13:33'`, `publish_status='publish_blocked'`,
  `promotion_state='blocked_from_promotion'`, `dossier_gap_class='missing_usage_meta'`
  are populated. **[V]**
- **Some non-leader rows ARE populated** with deck-intel-derived roles —
  OP01-005 Uta has `role_label='core'`, `role_summary='Deck intel: core in
1 leader (73% inclusion).'`; OP01-008 Cavendish has `core` at 82%
  inclusion. So there is real signal in the table for cards that appear
  in tracked decks. **[V]**
- **Confidence distribution is suspicious-uniform**: 2,200 of 2,497 rows
  (88%) have `confidence_score=0.93` exactly. The next-most-common values
  are 0.76 (167 rows) and 0.95 (28 rows). This pattern is consistent with
  a bulk-default value set at insert time, _not_ a derived per-card
  metric. **[V]** **[O]** confirm whether 0.93 is a real metric or a
  default by reading the writer code in `tools/miru_project_sync.py`.
- **One bad-data row**: `canonical_code='OP01-001'` (Roronoa Zoro Leader)
  has `updated_at='2026-12-31'`, `last_verified_at='2026-12-31'`, and
  `projection_source_updated_at='2026-12-31'`. This is a future-dated
  sentinel. Only one row in the whole table is affected. **[V]**
- **Publish-status distribution**: 2,320 (93%) empty / 99 `publish_blocked`
  / 55 `publish_ready` / 20 `publish_requires_review` / 3 `publish_deferred`.
  Only ~7% of rows have a populated publish state. **[V]**
- **Approval-state distribution**: 2,340 (94%) empty / 81 `rejected` /
  75 `approved_for_candidate` / 1 `deferred`. **[V]**

### 5.4 Insight quality — templating bug **[V]**

`miru_card_insights` has 242 rows: 216 `usage`, 21 `ruling`, 5 `price`.
Quality tiers: 189 `contextual`, 38 `evidenced`, 15 `strategic`.

A canned-text templating bug is visible in the data **[V]**:

> Row: `card_id='OP01-002'` (Trafalgar Law), `insight_type='usage'`,
> `insight_text='Roronoa Zoro is a flex piece in OP01-001 lists. You'll see
it in a solid chunk of those lists.'`

The text talks about _Roronoa Zoro_ but the row is keyed by _Trafalgar Law_.
A second example from OP01-120 (Shanks): "Shanks is a flex piece in OP01-001
lists." Shanks is a 10-cost finisher that historically does not appear in
Zoro-leader (OP01-001) lists in any meaningful frequency — the inclusion
percentage attached to the claim is likely also wrong.

**Verdict**: insights are auto-generated from a template with **at least
one substitution bug** (card-name placeholder gets the wrong card) and
**plausible-but-unverified inclusion claims**. Plus: **all 242 insight
rows have empty `approval_state`** — generated, never approved by anyone.
**[V]**

### 5.5 `miru_validations` quality — uniform, narrow **[V]**

1,336 rows. **All 1,336 have `confidence >= 0.9`** (specifically the values
are clustered around 0.95). 1,318 are `task_type='verify_official_fields'`,
13 are `promote_verified_dossiers`, 5 are `bulk_ingest_registry`. Sample:

> OP01-001 / confidence=0.95 / task_type=verify_official_fields /
> verified_at='2026-03-07 18:31:00' / sources_json includes "Official card
> list (snapshot)" / validated_fields_json includes ["card_name", "set_code",
> > "set_name", "rarity", "color", "card_type", "power", "attribute",
> > "traits", "life", "effect_text", ...]

This is real, structured validation data. **[V]** The narrowness — all rows
at ~0.95 confidence — suggests the engine writes a row only when a card
_passes_ validation (it's a pass-log, not a contested-validation log). **[I]**

### 5.6 `dev_training_reviews` quality — real operator work **[V]**

40 rows. Sample (3 rows, all OP01):

| id    | created_at          | card_code | variant_key | verdict       | issues            | action  | source                  |
| ----- | ------------------- | --------- | ----------- | ------------- | ----------------- | ------- | ----------------------- |
| 1     | 2026-04-08 20:02:38 | OP01-001  | base        | looks_correct | []                | approve | English Bandai cardlist |
| 10059 | 2026-04-09 04:40:13 | OP01-002  | alt         | looks_correct | ["new_card"]      | approve | operator-console        |
| 10060 | 2026-04-09 05:01:36 | OP01-002  | alt         | needs_review  | ["stat_mismatch"] | fix_it  | operator-console        |

These are genuine human-in-the-loop reviews via the operator console. The
`recurrence_aggregates` table downstream rolls these up: OP01-003 (Luffy)
alt variant has been reviewed with verdict `stat_mismatch` **7 times**;
OP01-002 alt has been reviewed `stat_mismatch` 6 times. **[V]**

Of all the subsystems in the project, this one shows the most signs of
real, recent, useful work. **[V]**

### 5.7 `evidence_source_weights` — cleanest schema in the system **[V]**

The 8 rows of this table read like a small, disciplined policy document:

| source               | weight | can_contradict_identity | can_contradict_market | staleness_days |
| -------------------- | ------ | ----------------------- | --------------------- | -------------- |
| BANDAI_CDN_CHECK     | 0.25   | yes                     | no                    | 7              |
| INTERNAL_ASSET_CHECK | 0.25   | no                      | no                    | 1              |
| PM_PARITY_CHECK      | 0.20   | no                      | no                    | 1              |
| JUSTTCG_CONSTRAINED  | 0.15   | no                      | yes                   | 7              |
| OPERATOR_URL         | 0.15   | no                      | no                    | 30             |
| OPTCGAPI_CROSS_CHECK | 0.08   | no                      | no                    | 14             |
| PERPLEXITY           | 0.05   | no                      | no                    | 30             |
| YOUTUBE              | 0.03   | no                      | no                    | 60             |

`PERPLEXITY` is rated 0.05 weight and _cannot_ produce `CONTRADICTS_OPERATOR`
for any field — only ever `INCONCLUSIVE` at worst. `YOUTUBE` at 0.03 is
"promo reveal / alt art corroboration only". Only Bandai CDN is allowed to
contradict operator-stated identity. **This is well-designed governance.** **[V]**

---

## 6. Audit Question 5 — MAPS-TO-VISION

Vision (operator stated): "propose → verify → approve → execute" — an AI
that ultimately runs the website autonomously, where every change is
sourced and traceable.

### 6.1 The propose layer **[V] [I]**

**What proposes new data?**

- `miru_ai/workers/learning_engine.py` runs scheduled-task handlers:
  `bootstrap_dossier`, `sync_missing_fields`, `inspect_missing_image`,
  `promote_verified_dossiers`, `refresh_progress`, `fetch_official_source`,
  `verify_official_fields`, `refresh_from_source`, `discover_set_cards`,
  `bulk_ingest_registry`. **[V]**
- These produce candidate facts → flow into `miru_dossiers.card_facts`
  (50K rows) → into `miru_learning_dossiers.learning_accepted_fact_*` for
  history (40K+ rows). **[I]**
- Last meaningful proposal activity: 2026-03-22 (the accepted-fact-history
  date). **[V]**

**Verdict**: the propose layer exists, has produced substantial output,
and is currently dormant. **[V]**

### 6.2 The verify layer **[V]**

**What verifies?**

- `miru_validations` (1,336 rows) is the per-card pass-log.
- `card_facts` with `verification_state` field is the per-fact verification
  state in `miru_dossiers.db`. **[V]**
- `learning_fact_corroboration_records` (13K rows) holds corroboration
  events. **[V]**
- `evidence_source_weights` (8 rows) is the weighted source-trust policy
  driving image / variant verification. **[V]**

**Verdict**: the verify layer is the most-built component. The 60 verified
`card_relationships` rows + 1,336 validations + 13K corroborations are real
verification artifacts. **The granularity is field-level**, which matches
the vision. **[V]**

### 6.3 The approve layer **[V]**

**What approves?**

- `miru_review_queue` (172 rows) — 155 resolved, 16 pending, 1 deferred.
  Newest write 2026-03-29. **[V]**
- `miru_publication_stage` (3 rows) — 2 `approved_for_candidate` (OP10-008,
  OP01-001) and 1 `rejected` (OP10-024). **[V]**
- `dev_training_reviews` (40 rows) — operator-driven approve/fix_it
  verdicts. **[V]**

**Verdict**: the approval mechanism exists _twice_ — once at the catalog
level (`miru_review_queue` → `miru_publication_stage`) and once at the
variant-evidence level (`dev_training_reviews`). The catalog-level approval
has 75 cards stamped `approved_for_candidate` but the path forward from
there has stalled. The variant-evidence approval is the only one still
operating. **[V]**

### 6.4 The execute layer **[O]**

**What executes — i.e., what actually changes the PM storefront once a
card is approved-for-publish?**

- `miru_publication_batches` has **2 rows total**. Batch 1 was archived
  empty on 2026-03-19 04:01:44. Batch 2 has status `mixed_state`, 2
  members, last touched 2026-03-19 05:57:48 — and has not advanced since.
  **[V]**
- `miru_publication_batch_items` has 4 rows. **[V]**
- The only stage items that ever reached the staging table are OP10-008,
  OP01-001, and OP10-024. **[V]**
- **No code path was found that consumes `miru_publication_batches` or
  `miru_publication_stage` and writes to PM storefront tables.** **[O]**
  confirm with a focused grep when revisiting.

**Verdict**: the execute layer was scaffolded (batches, stage,
batch_items tables exist with thoughtful state machines) but **was never
plumbed through to the storefront**. The path stops at "staged batch
member". **[V]**

### 6.5 Where the real gaps are vs the vision **[V]**

| vision stage                    | schema present                                   | code present                                | populated                                   | working today                  |
| ------------------------------- | ------------------------------------------------ | ------------------------------------------- | ------------------------------------------- | ------------------------------ |
| Propose new facts               | yes (B, C)                                       | yes (learning_engine.py)                    | yes (50K facts, 12K accepted)               | dormant since 2026-03-22       |
| Verify against sources          | yes (validations, corroboration, source weights) | yes (project_sync, evidence_collectors)     | yes (1,336 validations, 13K corroborations) | dormant since 2026-04-25       |
| Operator review (variant level) | yes (dev_training_reviews)                       | yes (dev_training_review.py, recurrence.py) | yes (40 reviews, 6 aggregates)              | **ACTIVE** (newest 2026-05-08) |
| Operator review (card level)    | yes (miru_review_queue)                          | yes (action_governance.py)                  | partial (172 rows, last 2026-03-29)         | dormant                        |
| Approve for publish             | yes (publication_stage)                          | yes (action_governance.py)                  | minimal (3 rows)                            | dormant                        |
| Batch publish                   | yes (publication_batches)                        | likely yes                                  | minimal (2 batches, both stalled)           | dormant                        |
| Push to storefront              | **NO code found**                                | **NO**                                      | **NO**                                      | not built **[O]**              |

The vision's last mile — "AI changes the storefront, every change sourced
and traceable" — has **no implementation**. Approved cards do not flow
anywhere. **[V]**

---

## 7. Audit Question 6 — GOVERNANCE STATE

### 7.1 Provenance is present and disciplined **[V]**

- `card_relationships.evidence_source` is `NOT NULL`. Real values are
  `text_analysis_2026_03_24` (the mass auto-mine) and
  `operator_knowledge_2026_03_24` (the operator-curated 60). **[V]**
- `miru_validations.sources_json` and `winning_source_json` are real
  populated JSON in every row — the validation engine recorded
  multi-source agreement. **[V]**
- `miru_dossiers.card_facts.confidence_score` is `NOT NULL` and
  per-fact, paired with `verification_state`. **[V]**
- `miru_dossiers.source_registry` and
  `miru_dev_training_reviews.evidence_source_weights` are both present
  source-trust policies. **[V]**

### 7.2 Asserted vs inferred is present but skewed **[V]**

- `card_relationships.status` defaults to `'inferred'` with `CHECK` constraint
  `(inferred, corroborated, verified, rejected)`. **[V]**
- 61,619 rows are `inferred`, 60 are `verified`, **0 are `corroborated` or
  `rejected`**. The middle states of the workflow are unused. **[V]**
- `miru_dossiers.card_facts.verification_state` is `NOT NULL` (schema
  enforces it). Distribution across 50K rows was not sampled in this audit.
  **[O]** sample at re-audit.

### 7.3 Confidence is suspect on the largest table **[V]**

- `card_intelligence.confidence_score`: 88% of 2,497 rows are exactly 0.93.
  This is too uniform for a derived metric and reads as a default. **[V]**
  **[O]** read `tools/miru_project_sync.py` lines 2172 and 2348 to confirm.
- `card_relationships.confidence` is varied and meaningful
  (`low/medium/high/verified`) per the constraint. **[V]**
- `miru_validations.confidence` is uniformly ≥ 0.9 — consistent with "this
  is a pass-log only" interpretation. **[I]**
- `miru_card_insights.confidence` is varied (0.636 to 0.935 in the small
  sample). **[V]**

### 7.4 Approval / promotion / publication state machines **[V]**

`card_intelligence` has a complete state-machine: `approval_state` →
`promotion_state` → `publication_readiness` → `publication_candidate_*` →
`publish_status` → `publish_*` (reasons, risks, payload, updated_at).

| field                   | non-empty rows | dominant non-empty value                                          |
| ----------------------- | -------------- | ----------------------------------------------------------------- |
| `approval_state`        | 157 (6.3%)     | `rejected` (81) + `approved_for_candidate` (75)                   |
| `promotion_state`       | 221 (8.8%)     | `blocked_from_promotion` (116) + `review_approved_candidate` (75) |
| `publication_readiness` | 252 (10.1%)    | `blocked_by_guardrail` (151) + `ready_for_review` (59)            |
| `publish_status`        | 177 (7.1%)     | `publish_blocked` (99) + `publish_ready` (55)                     |

**The state machine is rich. About 90% of cards never entered it.** The
ones that did show real progression — 75 cards reached `approved_for_candidate`,
55 reached `publish_ready`. None reached the storefront. **[V]**

### 7.5 The 60 verified card_relationships are genuinely high-quality **[V]**

Each verified row has a hand-written `notes` field. Example:

> _"EB04-038 Rosinante & Law counts as both Trafalgar Law and Donquixote
> Rosinante, making it eligible for Leader cost reduction and Leader
> life-save protection simultaneously."_
>
> _"OP12-115 I Love You!! at 2 or less Life returns a Trafalgar Law from
> trash to hand. Enables recycling of key Law pieces that have been used
> or discarded earlier in the game."_

This is the proof point that the system _can_ produce excellent data — when
an operator sits down with the right schema for an afternoon. The challenge
is not the schema, it's the catalyst. **[V]**

---

## 8. OP-01 Deep Probe

### 8.1 OP-01 ground truth **[V] [O]**

Per Perplexity research (citations: en.onepiece-cardgame.com, the official
Bandai cardlist; one-piece-card-game.fandom.com; limitless decklists),
**OP-01 "Romance Dawn"** comprises:

| field                        | pxy ground truth                | card_catalog.db                                | match?                                                        |
| ---------------------------- | ------------------------------- | ---------------------------------------------- | ------------------------------------------------------------- |
| Base card count              | 121                             | 121                                            | ✅ VERIFIED                                                   |
| Total entries incl. alt-arts | 154                             | 121 base + 348 variants total (not equivalent) | partial — see below **[O]**                                   |
| Colors                       | Red, Green, Blue, Purple        | Red, Green, Blue, Purple, Red/Green            | ✅ catalog adds the Trafalgar Law multicolor leader (correct) |
| Card types                   | Leader, Character, Event, Stage | Leader, Character, Event                       | ⚠️ pxy claims Stage; catalog has 0. **[O]**                   |
| Leader count                 | 8                               | 7                                              | ⚠️ mismatch by 1. **[O]**                                     |
| Common (C)                   | 45                              | 45                                             | ✅                                                            |
| Uncommon (UC)                | 30                              | 30                                             | ✅                                                            |
| Rare (R)                     | 26                              | 24                                             | ⚠️ off by 2 **[O]**                                           |
| Super Rare (SR)              | 10                              | 11                                             | ⚠️ off by 1 **[O]**                                           |
| Secret Rare (SEC)            | 2                               | 2                                              | ✅                                                            |
| Leader (L)                   | 8                               | 7                                              | ⚠️ off by 1 (matches leader count above) **[O]**              |
| SP CARD                      | (not enumerated separately)     | 2                                              | catalog has explicit SP CARD bucket **[V]**                   |

**Caveats on pxy data**: pxy's top citation was a fan wiki, not Bandai's
own page; the rarity breakdown given may be from a fan-summary. The
discrepancies (R 24 vs 26; SR 11 vs 10; L 7 vs 8) total to even out at 121
base cards, so the catalog's distribution is internally consistent with
its own row count. The **Stage type claim from pxy is likely wrong for
OP-01 specifically** — Stage cards exist in the game, but OP-01 having
zero Stage cards is consistent with my recollection that early sets
had Leader/Character/Event only. **[I]**

**Net verdict**: card_catalog.db is **likely correct for OP-01** at the
121-card / 7-leader / no-Stage level; pxy's top-citation fan-wiki data is
slightly off. **[I]** **[O]** confirm against the live
`asia-en.onepiece-cardgame.com` Bandai cardlist for a deeper-than-this-audit
verification.

### 8.2 OP-01 catalog stats **[V]**

```
Cards:            121
Variants:         348  (avg 2.88 variants per card)
card_intelligence rows: 121 (1:1)
miru_validations rows:  119 (missing OP01-002, OP01-026)
miru_card_insights rows: 28 (~23% coverage)
miru_review_queue rows:  26 (~21% coverage)
card_relationships rows: 3,078 (avg 25.4 per OP-01 card — all supports_leader)
```

Coverage thins out fast past the base catalog: 100% have intelligence rows
(but those are mostly defaults), 98% have validations, only 21–23% appear
in the review queue or have insights, and the relationship graph is dense
but auto-mined.

### 8.3 OP-01 cross-DB consistency **[V]**

```
card_catalog.db.cards (set_code=OP01):                  121
miru_dossiers.db.cards (set_code=OP01):                 121  (matches)
miru_dossiers.db.card_sources (card_code LIKE 'OP01-%'): 255 (~2 per card)
miru_learning_dossiers.db.learning_dossiers (OP01-*):   121  (matches)
miru_learning_dossiers.db.learning_accepted_fact_provenance (OP01-*): 1,088 (~9 facts/card)
```

OP-01 is fully present across all three intelligence layers. **[V]**

### 8.4 OP-01 leader spot-check **[V]**

| canonical_code | card_name      | color     | rarity | spot-check                                                                                                      |
| -------------- | -------------- | --------- | ------ | --------------------------------------------------------------------------------------------------------------- |
| OP01-001       | Roronoa Zoro   | Red       | L      | ✅                                                                                                              |
| OP01-002       | Trafalgar Law  | Red/Green | SR     | ✅ (Leader of card_type Leader; SR rarity unusual for a leader — many leaders are L. Worth confirming.) **[O]** |
| OP01-003       | Monkey.D.Luffy | Red       | L      | ✅                                                                                                              |
| OP01-031       | Kouzuki Oden   | Blue      | L      | ✅                                                                                                              |
| OP01-061       | Kaido          | Purple    | L      | ✅                                                                                                              |
| OP01-062       | Crocodile      | Purple    | L      | ✅                                                                                                              |
| OP01-091       | King           | Purple    | L      | ✅                                                                                                              |

**[O]** The OP01-002 Trafalgar Law rarity of `SR` is unusual — Leader-type
cards usually carry rarity `L`. Verify whether the catalog stored an alt-art
SR variant of the OP01-002 leader as a base row, or whether SR is correct
for this specific Leader.

### 8.5 OP-01 relationships sample **[V]**

For OP01-004 (Usopp), the first 15 `supports_leader` rows:

```
OP01-004 → OP01-001 (Zoro)     high  inferred
OP01-004 → OP01-003 (Luffy)    high  inferred
OP01-004 → OP08-001            medium inferred
OP01-004 → OP01-002            low   inferred
OP01-004 → EB01-001            low   inferred
OP01-004 → EB03-001            low   inferred
OP01-004 → EB04-001            low   inferred
OP01-004 → OP02-001            low   inferred
OP01-004 → OP03-001            low   inferred
OP01-004 → OP05-001            low   inferred
... (continues across ~50 leaders)
```

Usopp is a Straw Hat → he _does_ belong in Luffy and Zoro decks (high
confidence rows are correct). He does **not** meaningfully belong in the
other 48 leaders' decks (low-confidence rows are filler). The
algorithm has caught the obvious signal and produced N copies of "may
also work" rows for everything else. **The high-confidence signal is real,
the low-confidence tail is statistical noise.** **[V]**

### 8.6 OP-01 cards missing validation **[V]**

Two of 121 OP-01 cards have **no** row in `miru_validations`:

- **OP01-002 — Trafalgar Law** (Leader, Red/Green, SR) — this is also the
  card with the unusual SR rarity flag in §8.4 and the wrong-card-name
  templating bug in §5.4. The validation gap may be related.
- **OP01-026 — Gum-Gum Fire-Fist Pistol Red Hawk** (Event card by name,
  likely Luffy's Fire-Fist Pistol Red Hawk attack).

These are the only two OP-01 cards the validation engine never logged. **[V]**

---

## 9. Redundant / Legacy / Unclear

### 9.1 Strictly redundant — pick one **[V]**

- **`miru_dossiers.cards` (2,527) vs `miru_learning_dossiers.learning_dossiers`
  (2,527) vs `card_catalog.cards` (2,497).** All three claim to be the
  card list. The 30-row delta between catalog and the dossier DBs is
  entirely P-XXX promo cards in dossiers that catalog does not carry.
  **[V]** Functionally redundant; legitimately three views into the same
  set of cards. The architecturally cleanest move is to make ONE the
  source of truth and treat the others as derived.
- **`miru_dossiers.card_relationships` (empty) and `card_catalog.card_relationships`
  (61K).** The empty one was scaffolded and never written; the populated
  one is canonical. Drop the empty one. **[V]**

### 9.2 Legacy — quarantined but still present **[V]**

- **`archive/legacy_helpers/tools/miru_card_relationships_seed.py`** —
  writes to a `card_relationships_new` shadow table. Legacy. **[V]**
- **`archive/legacy_helpers/tools/ingest_operator_card_insights_2026_03_24.py`** —
  the ingestion script that produced the 60 verified `card_relationships`
  rows. One-shot. Legacy but historically valuable. **[V]**
- **`tools/miru_learning_engine_worktree_overlay.py`** — has its own
  `INSERT INTO learning_dossiers` path parallel to
  `miru_ai/workers/learning_engine.py`. **[O]** confirm whether this is
  active or a stale clone.

### 9.3 Schema-only — never wired **[V]**

- **`miru_perception_ledger` family (4 tables in card_catalog.db)** — the
  OCR-discrepancy ledger. Comprehensive schema (45 columns), zero rows,
  zero writers. **Either delete or wire.** **[V]**
- **15 empty tables in `miru_dossiers.db`** (card_identity, card_meta_intel,
  card_strategy_intel, card_synergy_intel, card_rulings_intel, card_published_insight,
  card_publication_audit, card_relationships, card_banlist, card_conflict_flags,
  card_lore_context, card_market, card_master_images, card_upcoming_rule_changes,
  card_usage, leader_intelligence, leader_links, leader_meta_intel) — the
  intel/published-insight output layer. Scaffolded, never populated. **[V]**
- **13 empty tables in `miru_learning_dossiers.db`** (the
  `learning_dossier_*` detail tables — usage, deck*usage, market_signals,
  rulings, strategy_notes, variant_art, image*_, source\__, tournament_placements,
  usage_evidence, etc.). **[V]**
- **`miru_user_decks.db` — all four tables empty.** **[V]**
- **`card_catalog.official_source_refs` — 0 rows.** **[V]**

### 9.4 Defunct supporting files **[V]**

- `data/miru_worker_runs.jsonl` — last write 2026-03-21 23:05; not touched
  in 56 days. **Likely defunct.** **[V]**
- `data/miru_learner_mode.json`, `data/miru_learning_log.db`,
  `data/miru_learning_queue.db` — paired files for the learning engine; all
  dormant since 2026-03-22. **[I]**

### 9.5 Unclear — needs deeper inspection **[O]**

- **`tools/miru_dossier_store.py` vs `shared/intel/db.py`.** Both define a
  `cards` + `card_facts` + `card_variants` schema with different column
  layouts. One is the dossier writer, the other is something else. Which
  is canonical? **[O]**
- **`miru_ai/core/ai_onepiece.py` writes `cards`** — confirm it's not
  contending with `miru_project_sync.py` for the same table. **[O]**
- **`data/miru_ai_onepiece_knowledge.json`** — 7 MB knowledge JSON,
  consumed by `miru_ai/core/ai_onepiece.py`. **[O]** confirm whether the
  knowledge in this file is up-to-date or stale (mtime is 2026-04-17).

---

## 10. What's solid, what's broken, what's missing

### 10.1 Solid — keep, lean on **[V]**

- The **base catalog** in `card_catalog.db` (cards, card_variants, sets,
  card_keywords, card_traits, card_trait_assignments, card_roles,
  card_rulings, card_errata). 2,497 cards across 51 sets, validated
  fields, prices, images, variant index. Backbone of the system.
- **`miru_validations`** (1,336 rows) — real per-card validation passes
  with multi-source agreement metadata.
- **`miru_dev_training_reviews.db`** end-to-end — `dev_training_reviews` +
  `evidence_reconciliation` + `post_review_evidence` + `recurrence_aggregates`
  - `evidence_source_weights`. The cleanest, most-recent, most-functional
    subsystem in the project.
- **`miru_dossiers.card_facts` + `fact_sources` + `confidence_records`** —
  the granular fact graph (50K + 31K + 53K rows). Real provenance.
- **The 60 verified `card_relationships`** — operator-curated Rosinante
  synergy map. High-quality.
- **`miru_deck_archetypes`** — 10 curated, research-sourced archetypes
  with high-confidence notes.
- **The `card_intelligence` state machine** — comprehensive
  approval/promotion/publication state model in schema. Underused but
  correctly designed.

### 10.2 Broken — fix or remove **[V]**

- **`miru_card_insights` templating substitution bug.** Rows are mis-keyed
  to wrong card_id, or template variables are wrong-card-substituted.
  Example: `OP01-002` row claims "Roronoa Zoro is a flex piece" — wrong
  card name. **Decision needed**: regenerate insights from a fixed
  template, or delete and recompute.
- **The OP01-001 future-date sentinel** (`updated_at='2026-12-31'`). One
  row, easy to fix. Symptom of a bulk-default write where the date wasn't
  set.
- **`card_intelligence.confidence_score=0.93` for 88% of rows.** Likely
  a default not a derived metric. Either compute real per-row confidence
  or set to NULL until computed. **[O]** confirm in the writer code.
- **`card_relationships` 99.9% noise.** 61,619 of 61,679 rows are a single
  text-analysis batch from 2026-03-24, with 83% `low` confidence. The
  signal is in the 84 `high` confidence rows + 60 `verified`. **Decision
  needed**: keep the noise as a recall pool, prune to high-confidence only,
  or drop and rebuild.
- **Publication batch `miru-stage-20260319-040101-op01-001-op10-008`**
  has been stuck in `mixed_state` since 2026-03-19. Either resume the
  workflow or close out the batch.
- **`miru_perception_ledger` family — no writer.** Either build the OCR
  writer that the schema is waiting for, or drop the four tables.
- **`miru_dossiers.db` intel + leader tables (18 empty tables).** Either
  populate or drop.
- **`miru_learning_dossiers.db` detail tables (13 empty tables).** Same.
- **`miru_user_decks.db` — entire DB empty.** Drop unless user-deck
  storage is on the roadmap.
- **`miru_worker_runs.jsonl` — defunct, no writes since 2026-03-21.** Drop
  or wire.

### 10.3 Missing — the execute layer **[V]**

The vision is: AI runs the storefront, every change sourced and traceable.

- **There is no code path that pushes an approved card from
  `miru_publication_batches` to a PM storefront table.** The publish
  pipeline ends at "staged batch member". **[V]** **[O]** confirm no such
  path exists outside the searched directories.
- **There is no audit log of "what got published when".** The
  `card_publication_audit` table in `miru_dossiers.db` is empty (0 rows)
  and the `card_published_insight` table is also empty (0 rows). **[V]**
- **There is no rollback path** — no record of "card X was published with
  values Y at time T, replaced at time T2".
- **The "every change sourced and traceable" promise is wired at the
  _verify_ layer** (validations carry `sources_json`, facts carry
  `fact_sources`) **but not at the publish layer** (no publication audit
  exists).

For the vision to be realized, the execute + audit-of-execute layer needs
to be **built**, not merely populated.

### 10.4 Other gaps to the vision **[V]**

- **No leader-side intelligence is populated.** Both
  `miru_dossiers.leader_intelligence` and `leader_meta_intel` and
  `leader_links` are empty (0 rows). Yet 99.9% of `card_relationships`
  point at leaders. The data the leader-intelligence tables would summarize
  is sitting in `card_relationships`, unaggregated.
- **No tournament data is captured.** `learning_tournament_placements` is
  empty. Tournament placements are a key input for a meta-aware AI.
- **No image-analysis history.** `learning_image_analysis` and
  `learning_image_selections` are both empty. The image analysis ran (we
  have `image_variant_analysis` with 1,951 rows in card_catalog.db) but
  the _learning-trail_ version was never wired.
- **No rulings narrative.** `miru_dossiers.card_rulings_intel`,
  `card_ruling_explanations`, and `card_rulings` are all empty in
  `miru_dossiers.db`. The catalog-side `card_rulings` table has 789 rows;
  the dossier-side explanatory layer doesn't.

---

## 11. Coverage Report

### Databases covered **[V]**

All 14 SQLite databases in `data/` were inventoried:

1. card_catalog.db ✅ full inventory, schemas of 11 key tables, distributions for relationships / intelligence / review_queue / validations / insights / publication state, OP-01 deep probe
2. miru_dossiers.db ✅ full inventory, schemas of cards + card_facts, cross-DB delta with catalog
3. miru_learning_dossiers.db ✅ full inventory, timestamps
4. miru_dev_training_reviews.db ✅ full inventory, sample rows, source weights
5. miru_memory.db ✅ inventory, timestamps (out-of-scope but noted)
6. miru_deck_intel.db ✅ inventory, archetype sample
7. miru_learning_log.db ✅ inventory, timestamps
8. miru_learning_queue.db ✅ inventory
9. miru_official_rules.db ✅ inventory
10. miru_source_cache.db ✅ inventory
11. miru_user_decks.db ✅ inventory (all empty)
12. miru_mcp_governance.db ✅ inventory
13. pm_decks.db ✅ inventory (2 rows)
14. The card_catalog.snapshot.db at `miru-mcp/sqlite-ro/` was noted (48 MB,
    2026-04-08) but not deeply queried — it's a snapshot of card_catalog
    and would mirror it modulo the 17-day gap.

The `miru_ai_onepiece_knowledge.json` file was opened to determine its
top-level shape (3 keys: `_meta`, `cards`, `sets`); contents were not
sampled. **[V]**

### What I could NOT fully verify **[O]**

- **OP-01 rarity breakdown** at deeper granularity than pxy's top fan-wiki
  source provided. Need a direct read of `asia-en.onepiece-cardgame.com`
  filtered to OP-01 to settle the R/SR/L discrepancies.
- **Whether OP01-002 Trafalgar Law Leader is correctly rarity SR** in
  catalog, or whether the catalog stored an alt-art SR variant under the
  base card row.
- **Whether OP-01 has any Stage cards** (pxy says yes — likely conflating
  with later sets; catalog says no).
- **Source of the `card_intelligence.confidence_score=0.93` default value**
  — needs to be confirmed by reading the writer in
  `tools/miru_project_sync.py` lines 2172 and 2348.
- **Whether `tools/miru_learning_engine_worktree_overlay.py` is active or
  stale** alongside `miru_ai/workers/learning_engine.py`.
- **Whether `tools/miru_dossier_store.py` and `shared/intel/db.py` are
  both active writers for `cards` / `card_facts` / `card_variants`** or
  one supersedes the other.
- **Whether any code path consumes `miru_publication_batches` and writes
  to a PM storefront table** outside the directories grepped (miru_ai/,
  tools/, shared/, pm/, services/, archive/).
- **Distribution of `verification_state` values across the 50,540
  `miru_dossiers.card_facts` rows.** Not sampled this audit.
- **Whether `data/miru_ai_onepiece_knowledge.json` is current or stale.**
  mtime is 2026-04-17.

### Method **[V]**

- Read-only `sqlite3` CLI for every database (no writes attempted).
- Grep over the entire `D:\dev\miru\` tree for `INSERT INTO <table>`
  patterns to identify writers.
- Grep for `miru_perception_ledger` to confirm zero writers (no matches).
- Perplexity research for OP-01 ground truth, treated with appropriate
  caution given pxy's top citation was a fan wiki.
- Cross-DB joins via shell `comm` to identify card-list deltas (sqlite3
  `ATTACH` had issues with column-name mismatch and was abandoned in favor
  of `comm`).
- No INSERT, UPDATE, DELETE, or schema change. No services started.

---

## Appendix A — Writer-to-table map

(Grepped from `miru_ai/`, `tools/`, `shared/`, `archive/`, `tests/`.
Tests excluded from the canonical map below.)

| table                                                    | canonical writer(s)                                                                                                                                                                                         | scheduled by                                        |
| -------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `card_catalog.cards`                                     | `tools/miru_project_sync.py:3501`, `shared/intel/db.py:439`, `miru_ai/core/ai_onepiece.py:611`, `tools/miru_dossier_store.py:1291`                                                                          | learning_engine + project_sync (operator-triggered) |
| `card_catalog.card_variants`                             | `miru_ai/workers/learning_engine.py:12413`, `miru_ai/server.py:8424`, `miru_ai/server.py:8671`, `shared/intel/db.py:493`                                                                                    | learning_engine + server-side write paths           |
| `card_catalog.card_relationships`                        | `shared/intel/db.py:517`, `archive/legacy_helpers/tools/miru_card_relationships_seed.py:110` (shadow), `archive/legacy_helpers/tools/ingest_operator_card_insights_2026_03_24.py` (operator-curated subset) | one-shot batch + operator script                    |
| `card_catalog.card_intelligence`                         | `tools/miru_project_sync.py:2172`, `tools/miru_project_sync.py:2348`                                                                                                                                        | project_sync (operator-triggered)                   |
| `card_catalog.miru_validations`                          | `tools/miru_project_sync.py:3532`                                                                                                                                                                           | project_sync                                        |
| `card_catalog.miru_review_queue`                         | `miru_ai/governance/action_governance.py:4238`, `:4391`                                                                                                                                                     | action_governance (within `miru_ai/`)               |
| `card_catalog.miru_publication_stage`                    | `miru_ai/governance/action_governance.py:5498`                                                                                                                                                              | action_governance                                   |
| `card_catalog.miru_card_insights`                        | `tools/miru_project_sync.py:2894`, `:2956`, `archive/legacy_helpers/tools/ingest_operator_card_insights_2026_03_24.py:238`                                                                                  | project_sync + one-shot operator script             |
| `card_catalog.miru_perception_ledger` (and 3 companions) | **NONE**                                                                                                                                                                                                    | **never wired**                                     |
| `miru_dossiers.cards`                                    | `tools/miru_dossier_store.py:1290`, `:1341`                                                                                                                                                                 | dossier_store                                       |
| `miru_dossiers.card_facts`                               | `tools/miru_dossier_store.py:1381`, `shared/intel/db.py:539`                                                                                                                                                | dossier_store / shared.intel                        |
| `miru_dossiers.card_variants`                            | `tools/miru_dossier_store.py:1435`                                                                                                                                                                          | dossier_store                                       |
| `miru_learning_dossiers.learning_dossiers`               | `miru_ai/workers/learning_engine.py:5722`, `tools/miru_learning_engine_worktree_overlay.py:1142`                                                                                                            | learning_engine (+ possibly stale overlay)          |
| `miru_dev_training_reviews.dev_training_reviews`         | `miru_ai/dev_training_review.py:258`                                                                                                                                                                        | server.py wires this — operator-triggered via UI    |
| `miru_dev_training_reviews.recurrence_aggregates`        | `miru_ai/recurrence.py:267`, `:457`                                                                                                                                                                         | recurrence engine                                   |
| `miru_dev_training_reviews.recurrence_review_links`      | `miru_ai/recurrence.py:298`, `:488`                                                                                                                                                                         | recurrence engine                                   |
| `miru_dev_training_reviews.correction_candidates`        | `miru_ai/recurrence.py:766` (table empty — code present but never produces a row)                                                                                                                           | recurrence engine                                   |
| `miru_dev_training_reviews.post_review_evidence`         | `miru_ai/evidence_collectors.py:1197`                                                                                                                                                                       | evidence collector                                  |
| `miru_dev_training_reviews.evidence_reconciliation`      | `miru_ai/evidence_collectors.py:1154`                                                                                                                                                                       | evidence collector                                  |

The running Flask service (`miru_ai/server.py`) imports
`tools.miru_project_sync` at line 139 — confirming the
catalog-writing path is wired into the service that the operator launches
with `python -m miru_ai.server`. **[V]**

---

## Appendix B — Specific items to verify on next pass

Numbered so the next visit can cite-and-resolve.

1. **B1** — Read `tools/miru_project_sync.py` lines 2172 and 2348 to confirm
   whether `confidence_score=0.93` is a literal default or a derived value
   that happens to collapse for most cards.
2. **B2** — Sample `miru_dossiers.card_facts.verification_state` distribution
   across 50K rows to learn whether the fact graph is mostly asserted /
   inferred / disputed.
3. **B3** — Confirm by grep over `pm/` and `services/` (and any other PM
   storefront repo) that there is genuinely no code path consuming
   `miru_publication_batches` / `miru_publication_stage` and writing to a
   storefront table.
4. **B4** — Verify OP-01 leader count + rarity breakdown directly against
   `asia-en.onepiece-cardgame.com` filtered to OP-01 (not via fan wiki).
5. **B5** — Verify OP01-002 Trafalgar Law Leader rarity (catalog says `SR`,
   pxy says all Leaders are `L`).
6. **B6** — Verify whether OP-01 has Stage cards (catalog says no, pxy
   said yes).
7. **B7** — Determine if `tools/miru_learning_engine_worktree_overlay.py`
   is a stale clone of `miru_ai/workers/learning_engine.py`.
8. **B8** — Determine if `tools/miru_dossier_store.py` and
   `shared/intel/db.py` are concurrent active writers, or one supersedes.
9. **B9** — Confirm `data/miru_ai_onepiece_knowledge.json` freshness vs
   the dossier DBs (it's older than the latest dossier write).
10. **B10** — Investigate the templating bug in `miru_card_insights` —
    where in the writer code does the wrong-card-name substitution happen?
11. **B11** — Investigate the one OP01-001 row in `card_intelligence` with
    `2026-12-31` sentinel dates — is it a leftover test row or did a
    real writer path produce it?

---

_End of audit — read-only, evidence-backed, no v2 schema proposal._
