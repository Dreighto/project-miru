# Skill: card-corpus-training-prep

## When this skill applies

Any work preparing card data for AI / Hermes / knowledge-layer training. Triggers: "train Hermes on this", "is this row training-ready", "prep the corpus", "what should we feed the AI", "knowledge layer", "OP01 as a clean corpus", "AI training on cards", "fact extraction".

This skill governs the contract between the card data layer and the AI training input. A bad corpus bakes its defects into the knowledge layer.

## The core rule

**A clean corpus is the prerequisite for training. Training on dirty data poisons the model.**

The 113 duplicate `::`-style rows in OP01 are the exact reason this skill exists. Train on them and the model learns OP01-016 has 11 printings when it actually has 7. Train on a `card_relationships` table that's 99.9% noise from one batch and the model learns false relationships at scale. Train on `miru_card_insights` (templating bug, OP01-002 row reads "Roronoa Zoro is a flex piece") and the model learns wrong card names against wrong card IDs.

Before any corpus rerun: clean first.

## What counts as a clean corpus

Each row that enters training must satisfy ALL of these:

1. **No duplicates.** No two rows describing the same underlying entity. For OP01: no `::`-style row alongside its `_pN` twin. For other sets: same rule, applied to the format-of-record (Bandai-style `print_id`).
2. **Full provenance.** `official_provenance` non-null. The row must point at a verifiable source (Bandai cardlist URL, manga page, official ruling document).
3. **Verified evidence source.** The `evidence_source` field is operator-approved or sourced from a high-weight provenance. NOT `text_analysis_2026_03_24` (that's the 99.9% noise batch).
4. **Confidence score is real, not default.** `card_intelligence.confidence_score = 0.93` on 88% of rows is the default; treat it as "no confidence captured." Trainable confidence comes from explicit verification.
5. **No sentinel dates.** No `2026-12-31` or other obviously-bogus dates. Real dates only.
6. **Verification state is `verified` or `corroborated`.** Not `inferred`, not `pending`, not `disputed`.

If a row fails ANY of these, it does not enter the corpus for that training run.

## What to do with rows that fail

Three buckets:

- **Fixable now** (e.g., row needs provenance backfill from the crawl JSON) → fix it, then include.
- **Fixable later** (e.g., row needs operator verification of an inferred fact) → flag in `data/corpus_flags/<set>_<date>.jsonl`, exclude from this training run, file a Linear ticket for the fix.
- **Permanently exclude** (e.g., the row describes a duplicate already covered) → mark with `corpus_exclude_reason` and never include. The 113 `::`-style OP01 duplicates fall here AFTER Pass C dedup merges their content into the canonical `_pN` rows.

## Provenance + evidence weight discipline

Per the cleanest piece of the system (`miru_dev_training_reviews.db.evidence_source_weights`), evidence has a tier:

- **Tier 1 — Bandai official** (cardlist, rulebook, errata): full weight.
- **Tier 2 — Operator knowledge** (e.g., the 60 verified Rosinante relationships): full weight, operator-attested.
- **Tier 3 — Manga/anime canon** (One Piece source material): high weight for flavor/identity, weaker weight for game mechanics.
- **Tier 4 — Tournament results / community data**: medium weight, depends on data quality.
- **Tier 5 — Inferred (text analysis, pattern matching)**: low weight, usually excluded from training unless corroborated by a higher tier.

The `card_relationships` 51,390 low-confidence rows from `text_analysis_2026_03_24` are Tier 5 inferred — they don't enter training without corroboration.

## Dedup discipline (the OP01 Pass C pattern)

When duplicates exist (multiple rows describing the same underlying printing/fact):

1. **Identify the canonical row.** The Bandai-format `print_id` (`OP01-NNN_pN`) is canonical; legacy `::`-style is duplicate.
2. **Migrate any unique content from the duplicate to the canonical.** Don't lose data by silently dropping.
3. **Mark the duplicate as superseded.** Don't hard-delete from production DB; add a `superseded_by` pointer. (Decision: confirm with operator on first dedup batch whether soft-delete or hard-delete is the policy.)
4. **Log the dedup.** Per `db-schema-discipline`, every write gets a log entry. Dedup is a high-signal write — log thoroughly.
5. **Re-verify the canonical row** is still complete and correct after migration.

## Corpus rerun discipline

When prepping a training run:

1. **Snapshot the corpus.** `SELECT ... INTO temp table` or export to a frozen JSONL file at `data/training_corpus/<set>_<date>.jsonl`.
2. **Document the exclusions.** A `data/training_corpus/<set>_<date>_exclusions.md` listing what was filtered out and why.
3. **Hash the corpus.** `sha256sum` the JSONL. Record the hash in `data/training_corpus/<set>_<date>.meta.json`.
4. **The hash is the contract.** When the training run produces output, it can be tied to this exact corpus snapshot. If we rerun and the hash differs, the corpus changed; the training comparison isn't apples-to-apples.

## Hermes / next-phase AI training context

Per memory `project_hermes_stage1`: Hermes Stage 1 is the spawn-time predictor (routes + outcomes). Stage 2+ takes routing authority once track record validated. The corpus prep work here is foundational for any KNOWLEDGE-layer Hermes work — different from the routing predictor.

The exact training pipeline for the card knowledge layer is not yet documented (CH brief acknowledged this gap). Treat "knowledge-layer training" as a stated goal whose mechanics still need a design pass — the corpus prep discipline above applies regardless of the training framework chosen.

## How CC uses this skill

Implementation side:

- When prepping a corpus snapshot for any training run, apply the 6-criterion clean check.
- When dedupping (Pass C work and beyond), apply the dedup discipline.
- When tasked with "feed this to the AI" — STOP, verify clean-corpus criteria, surface gaps before feeding.
- Pair with `db-schema-discipline` for any write work in the corpus prep flow.

## How CH uses this skill

Brainstorm/architect side:

- When discussing the AI training pipeline, use the clean-corpus criteria as the design baseline.
- When the operator asks "are we ready to train on OP01?" — check the corpus state ([[project-op01-corpus-state]]) against the 6 criteria; the gap analysis is the answer.
- When designing new fact sources or evidence pipelines, design FOR the corpus criteria from the start (don't ship a fact source that produces Tier 5 inferred output without explicit operator opt-in).
- For brainstorm output about training design, write the synthesis to Notion per the `design-session-output` skill.

## Reference

- OP01 state: `project_op01_corpus_state` memory
- Data layer audit: `D:\dev\miru\docs\audits\data-layer-audit.md`
- Hermes Stage 1: `project_hermes_stage1` memory
- Evidence source weights (cleanest subsystem): `data/miru_dev_training_reviews.db.evidence_source_weights`
- DB write discipline: `db-schema-discipline` skill

## What this skill is NOT

- Not a model-architecture choice. The criteria here are corpus-quality criteria — they apply regardless of which model gets trained.
- Not a substitute for verifying live corpus state. Always pull current DB state before declaring a corpus training-ready.
- Not authority to start a training run. Operator approves training runs explicitly.
