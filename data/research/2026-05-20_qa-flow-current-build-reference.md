# QA Verification Flow — Current Build Reference

> **Date:** 2026-05-20
> **Author:** CC (research pass requested by operator before building Stage 5 / promotion)
> **Purpose:** Document how card verification + promotion is actually built in
> `project-miru` TODAY, so the dev-page debrief's 5-stage QA flow gets built
> consistent with (or deliberately replacing) what already exists — not bolted
> on blind.

---

## TL;DR — the one thing that matters

**There are TWO verification/promotion systems in this repo, built a generation
apart, and the dev-page debrief designed the new QA flow without referencing the
old one.**

- **System A — the publication pipeline.** Built Feb–April 2026, lives in
  `card_catalog.db`. **Dormant** — last write 2026-03-29 (review queue) /
  2026-04-25 (validations). Already has a full readiness → approval → promotion
  state model, a review queue, a publication staging area, multi-source
  validation records, and a perception ledger.
- **System B — the shadow loop.** Built from 2026-05-17, lives in
  `miru_learning_pool.db`. **Live and running** (348 rows, growing). Two local
  models learn OP01 cards; Stage 3 (Bandai-trace gate) is wired; Stage 4/5 and
  any promotion mechanism are absent.

The debrief's QA flow (Stages 1–5, three doors, five-rung score, Door B override
markers) is the design for *finishing System B*. It re-invents concepts System A
already has. Before building Stage 5 / Door B / promotion, someone has to decide:
does System B **replace** System A, **feed into** it, or **adopt its proven
state model**? That is an operator + CH design call. This document gives them
the facts to make it.

---

## System A — the publication pipeline (dormant)

Lives entirely in `card_catalog.db`. Built before the LogueOS extraction, went
quiet when the project pivoted to orchestration work in May. Tables:

### `miru_review_queue` — 172 rows (155 resolved / 16 pending / 1 deferred)

The operator review queue. 24 columns. Notable:

- `readiness_state`, `approval_state`, `promotion_state` — a **three-axis state
  model**. This is the same shape the debrief's "five-rung score" + `promotion_status`
  split is reaching for.
- `guardrail_label`, `confidence_score`, `risk_level`, `recommended_next_step`
- `decision_source` — who/what made the call (operator vs machine)
- `status` (pending / resolved / deferred), `resolution_note`, `resolved_at`
- `payload_json`, `supporting_sections_json` — the evidence the operator sees

Latest `updated_at`: **2026-03-29**.

### `miru_publication_stage` — 3 rows

A staging area between "reviewed" and "published to catalog". 26 columns:

- `stage_state`, `approval_state`, `promotion_state`, `readiness_state`
- `candidate_score`, `candidate_score_band`, `candidate_profile`,
  `candidate_score_reasons_json`, `candidate_risk_factors_json` — a
  **candidate-scoring model** already exists here.
- `batch_id` — links to `miru_publication_batches`

Latest `updated_at`: **2026-03-19**.

### `miru_publication_batches` (2 rows) / `miru_publication_batch_items` (4 rows)

Batch publication — group reviewed items, publish together.

### `miru_validations` — 1336 rows

Per-card multi-source adjudication records. Columns: `card_code`, `confidence`,
`task_type`, `verified_at`, `winning_source_json`, `rejected_sources_json`,
`validated_fields_json`, `conflict_summary_json`, `confidence_reason`,
`payload_json`. **This is the closest existing analogue to the debrief's
"Verifier output" — multi-source, with a winning source and rejected sources
recorded.** Latest `updated_at`: **2026-04-25**.

### `miru_perception_ledger` (+ `_fields`, `_recurrence`, `_summary`) — 0 rows, schema built

A 52-column image/OCR discrepancy ledger. Fully schema'd, never populated.
Columns cover OCR runs, discrepancy categories, severity, `review_status`,
`final_disposition`, `recurrence_count`, `suppression_active`,
`patch_candidate_state`, variant-risk scoring. Built for an image-verification
flow that never went live.

### Who writes System A

`miru_ai/server.py`, `tools/miru_self_report.py`, `tools/miru_project_sync.py`,
`miru_ai/governance/action_governance.py`, `tools/miru_image_variant_classifier.py`.

---

## System B — the shadow loop (live)

Lives in `miru_learning_pool.db`. Started 2026-05-17 (PRO-907 schema, PRO-908
loop). Actively running.

### The pipeline (verified against code 2026-05-20)

`services/shadow_loop/launch.py` → config load → build Ollama client + verifier
→ `loop_runner.run_forever()`. Per tick:

1. Pop a card from `priority_queue.py`.
2. Fetch the card's catalog values.
3. Invoke **primary model** (`qwen2.5:7b`) and **validator model**
   (`qwen2.5:14b`) — both via `ollama_client.OllamaClient.ask_json()`.
4. `real_verifier.score()` — per-field outcomes; agreement decided per field
   (`field_outcomes[f]["agree"]`). Field tiers (`field_tiers.py`): HARD
   (deterministic catalog compare), SOFT (validator LLM semantic match),
   INFERRED (operator-only, can't auto-promote).
5. **Stage 3** — `stage3_autoclear.stage3_autoclear()` called at
   `loop_runner.py:171`. Requires Bandai-trace agreement (PRO-927). Wired + live.
6. `db_writer.upsert_learned_card()` — writes the row to `learned_cards`.

### What `db_writer` writes

`learned_cards` row. **`promotion_status` is ALWAYS `'experimental'`**
(`db_writer.py:131`) — it never writes any other value. `source_trace_json` is
written when Stage 3 advances (PRO-927). The six learning-metadata columns
(`confidence_score`, `validator_agreement`, `last_verified`, etc.) are populated.

### The Sentinel (verifier-of-verifier)

`sentinel.py` — a self-consistency check on 2 hardcoded leader cards every 50
ticks. The hook (`should_check_sentinel()`) exists but the **check invocation is
not wired into `loop_runner`**. Structurally present, functionally dormant.

### What does NOT exist in System B

- **Stage 4** (operator review) — absent.
- **Stage 5** (final gate before catalog write) — absent.
- **Any promotion mechanism** — absent. `miru_learning_pool_schema.md` says so
  outright: *"There is no automatic flow from this DB into card_catalog.db. The
  promotion mechanism itself is out of scope... likely a later ticket."*
- **`door_b_overrides` / `score_transitions` tables** — created by PRO-926's
  migration, but **no code reads or writes them.** Empty, schema-only.

### The current operator-review surface

`miru_ai/shadow_review.py` — `submit_verdict()` accepts `correct / wrong /
defer` (the PRO-909 three-button model). It appends to
`data/shadow_loop_verifier_overrides.jsonl` and UPDATEs `promotion_status` in
the pool. It does NOT know the three-door model, Door B, or
`door_b_overrides`. The SvelteKit Review surface (`miru_ai/hub_ui/`) is the
PRO-922 read-only scaffold with inert buttons.

---

## The overlap — debrief QA flow vs. what exists

| Debrief concept | Already exists in System A? | Status in System B |
| --- | --- | --- |
| 5-stage flow | Partial (review→stage→publish→batch) | Stages 1–3 only |
| Operator review queue | **Yes** — `miru_review_queue` (24-col, live state model) | `shadow_review.py` JSONL, no queue table |
| readiness / approval / promotion states | **Yes** — three columns on `miru_review_queue` | one `promotion_status` enum, always `experimental` |
| Five-rung confidence score | Partial — `confidence_score` + `candidate_score_band` | `confidence_score` REAL, no rung tiers |
| Multi-source verifier output | **Yes** — `miru_validations` (winning/rejected sources) | `validator_agreement` JSON per row |
| Publication staging before catalog | **Yes** — `miru_publication_stage` + batches | absent |
| Three doors (A fix / B approve / C fault) | No (old queue is resolve/defer) | `correct/wrong/defer` in code |
| Door B override marker | No | `door_b_overrides` table empty/unused |
| Score-transition log | Partial — `miru_action_history` (70 rows) | `score_transitions` table empty/unused |

**The debrief re-invented at least four things System A already built:** the
review queue, the readiness/approval/promotion state model, candidate scoring,
and multi-source validation records. PRO-926's new tables (`door_b_overrides`,
`score_transitions`) and the five-rung score are parallel inventions.

---

## The open design question (for operator + CH)

Before Stage 5 / Door B / promotion can be built *properly*, one decision:

**Does System B (shadow loop) replace, feed, or borrow from System A?**

1. **Replace.** System A is dead; System B is the future; build the debrief's
   QA flow fresh; the `miru_*` publication tables in `card_catalog.db` become
   legacy to drop. Debrief is right as written. Cost: re-building proven
   machinery (review queue, state model, staging) from scratch.

2. **Feed.** The shadow loop produces verified cards; they flow INTO System A's
   `miru_review_queue` → `miru_publication_stage` → catalog. Stage 4/5 = the
   existing queue + staging tables. Door B = an `approval_state` value on
   `miru_review_queue`, not a new table. Cost: bridging two DBs; reviving a
   dormant pipeline.

3. **Borrow.** System A stays dormant, but System B adopts its proven
   state-model shape (readiness/approval/promotion three-axis, candidate
   scoring) instead of the debrief's parallel design. Cost: revising the
   debrief + PRO-926's schema choices.

This is not a CC call. It needs operator + CH judgment — it changes what Stage
5, Door B, the five-rung score, and the `door_b_overrides`/`score_transitions`
tables should even be.

---

## Recommendation

**Pause the Stage 5 / Door B build (debrief Ticket 3b) until the question above
is settled.** Building 3b now means picking option 1 (replace) by default,
silently — which may be the right call, but it should be a *decision*, not an
accident of nobody having looked at `card_catalog.db`'s existing tables.

The fastest path: a short operator + CH design session that looks at this
document, decides replace / feed / borrow, and records it as a `decisions` row.
Then Ticket 3b (and 3c, and the debrief's whole QA-flow section) gets re-scoped
to match. Tickets 1–3a already shipped and are not affected — they're shadow-loop
internals that hold regardless of the decision.

Backend tickets that CAN proceed regardless: none remaining in the 3-series
until this is settled. The UI tickets (4–6) were already gated on typography
(now locked) but also depend on which review-surface design wins here.
