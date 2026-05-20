# miru_learning_pool.db — shadow-loop learning database

**Created:** 2026-05-17 (PRO-907)
**Location:** `data/miru_learning_pool.db`
**Created by:** `tools/create_miru_learning_pool.py` (idempotent)

## What this DB is

Working memory for the OP01 shadow-evaluation learning loop (PRO-908). The two
shadow-loop models — Qwen 2.5 7B (primary) and Qwen 2.5 14B (validator) — read
from and write to this DB as they learn OP01 cards.

It is **not** canon. `card_catalog.db` is canon. Rows in the learning pool are
unreviewed until the operator approves them through the dev-page review queue
(PRO-909). The pool is the sandbox; the catalog is the shelf.

## Why a separate DB

The shadow-loop models will be wrong, will contradict each other, and will
correct themselves over time. That churn must not muddy `card_catalog.db`,
which stays canonical and operator-verified.

## Tables

### `learned_cards` — 76 columns

A single row represents one model's current best understanding of one
(canonical_code, print_id) pair. Multiple rows can exist for the same pair
when different models disagree — the dev-page review queue (PRO-909) is
where the operator resolves that.

Column breakdown:

| Source                    | Columns | Notes                                                          |
| ------------------------- | ------: | -------------------------------------------------------------- |
| Own (pool-native)         |       2 | `id` PK + `created_at` timestamp                               |
| Mirrored: `cards`         |      34 | every column from `card_catalog.cards` except its `id`         |
| Mirrored: `card_variants` |      30 | every column from `card_catalog.card_variants` except its `id` |
| Learning metadata         |      10 | the ten learning-only fields below                             |

The mirror is built **live** from `card_catalog.db` via `PRAGMA table_info`
at script-run time — it cannot drift from the source schema.

## Mirror mapping

A promoted row should slot into `card_catalog` with no transformation: the
card-level fields go to `cards`, the variant-level fields go to
`card_variants`, the ten learning-metadata fields are stripped.

### Column-name collision rule

Two columns exist in both `cards` and `card_variants`. To keep both, the
card*variants copy gets the `variant*` prefix:

| Collision       | Pool column name (cards copy) | Pool column name (variants copy) |
| --------------- | ----------------------------- | -------------------------------- |
| `is_serialized` | `is_serialized`               | `variant_is_serialized`          |
| `block_icon`    | `block_icon`                  | `variant_block_icon`             |

### NOT NULL relaxation

`card_catalog.db` declares many columns NOT NULL with empty-string defaults
(e.g. `card_name TEXT NOT NULL DEFAULT ''`). The learning pool relaxes
those to nullable. A learner can know a card's `card_name` but not yet its
`power`; forcing NOT NULL would mean inserting `''` placeholders that look
like real values to a downstream reader. Better to let unknown fields be
NULL and let `confidence_score` + `last_verified` carry the certainty
signal.

(Source-side defaults are preserved so an explicit empty-string insert
still works for callers that want the catalog-shaped behaviour.)

## The ten learning-metadata columns

These are pool-only. Stripped on promotion to `card_catalog`.

| Column                | Type | Purpose                                                                                                                |
| --------------------- | ---- | ---------------------------------------------------------------------------------------------------------------------- |
| `confidence_score`    | REAL | How sure the loop is about this row (0.0–1.0). Set by the primary model, may be reweighted by the validator.           |
| `learned_from`        | TEXT | Which question or correction taught this row. Free-text reference — a question ID, a review verdict ID, a Bandai URL.  |
| `last_verified`       | TEXT | ISO 8601 timestamp of the last successful verification. Drives staleness queries.                                      |
| `validator_agreement` | TEXT | Per-field outcome JSON: did the primary AI and the shadow validator agree, and on what.                                |
| `contributing_model`  | TEXT | Which model produced/learned this fact. Drives the per-model performance view in PRO-909.                              |
| `source_trace_json`   | TEXT | Per-field Bandai-source pointers, written when the Stage 3 auto-clear gate passes (PRO-927). NULL when the gate fails. |
| `derived_from_json`   | TEXT | Parent `print_id` list for derived-card attenuation (PRO-926). Defaults to `'[]'`.                                     |
| `readiness_state`     | TEXT | Verification-pipeline position. CHECK-constrained. See the three-axis state model below.                               |
| `approval_state`      | TEXT | Operator-review verdict axis. CHECK-constrained. See below.                                                            |
| `promotion_state`     | TEXT | Promotion-to-catalog axis. CHECK-constrained. `''` is the real pre-promotion state, not absence. See below.            |

## The three-axis state model (PRO-928)

PRO-928 replaced the single `promotion_status` enum with three independent
CHECK-constrained columns, adopting the proven shape of `card_catalog.db`'s
dormant publication pipeline (`miru_review_queue`) — the "BORROW" architecture
decision (operator + CH, 2026-05-20). One axis tracks how far the row has come,
one tracks the operator's verdict, one tracks promotion.

### `readiness_state` — pipeline position

Written by the shadow loop. Default on insert: `ready_for_review`.

| Value                         | Meaning                                                      |
| ----------------------------- | ------------------------------------------------------------ |
| `not_ready`                   | Not yet verified enough to review.                           |
| `ready_for_review`            | Stage 3 passed; awaiting operator review.                    |
| `blocked_by_guardrail`        | A guardrail (e.g. the Stage 3 Bandai-trace gate) blocked it. |
| `ready_for_publish_candidate` | Cleared review; eligible to become a publish candidate.      |

The shadow loop writes `blocked_by_guardrail` whenever the Stage 3 gate fails
or is absent (fail-closed).

### `approval_state` — operator verdict

Default on insert: `pending_review`. The review queue is exactly the set of
`pending_review` rows.

| Value                    | Meaning                                                                     |
| ------------------------ | --------------------------------------------------------------------------- |
| `pending_review`         | In the review queue, awaiting an operator call.                             |
| `approved_for_candidate` | Operator approved the row.                                                  |
| `rejected`               | Operator rejected the row.                                                  |
| `deferred`               | Reserved. The current review flow leaves deferred rows at `pending_review`. |

### `promotion_state` — promotion to catalog

Default on insert: `''`.

| Value                       | Meaning                                    |
| --------------------------- | ------------------------------------------ |
| `''` (empty string)         | Pre-promotion. A real state, not absence.  |
| `review_approved_candidate` | Approved by review; a promotion candidate. |
| `blocked_from_promotion`    | Blocked from promotion.                    |
| `deferred`                  | Promotion deferred.                        |

### Verdict mapping in the current review flow

The full three-door QA flow is debrief Tickets 3b/3c. PRO-928 keeps the
existing `correct` / `wrong` / `defer` review flow working with a minimal
subset of the vocabularies:

| Operator verdict | `approval_state`             | `promotion_state`           |
| ---------------- | ---------------------------- | --------------------------- |
| `correct`        | `approved_for_candidate`     | `review_approved_candidate` |
| `wrong`          | `rejected`                   | `blocked_from_promotion`    |
| `defer`          | unchanged (`pending_review`) | unchanged (`''`)            |

## Indexes

| Index name                             | Columns                      | Why                        |
| -------------------------------------- | ---------------------------- | -------------------------- |
| `idx_learned_cards_identity`           | `(canonical_code, print_id)` | Natural identity lookup    |
| `idx_learned_cards_readiness_state`    | `readiness_state`            | Review-queue filtering     |
| `idx_learned_cards_contributing_model` | `contributing_model`         | Per-model performance view |
| `idx_learned_cards_last_verified`      | `last_verified`              | Staleness queries          |

## Promotion semantics (operator gate)

There is no automatic flow from this DB into `card_catalog.db`. The
promotion mechanism itself is out of scope for PRO-907 (debrief Tickets
3b/3c). The shape of the data here is designed so that promotion is a
mechanical projection: drop the ten metadata columns, split the row into
its cards-mirror and card_variants-mirror halves, INSERT/UPDATE
card_catalog.

## Schema-change discipline

`tools/create_miru_learning_pool.py` builds the **current** schema directly,
so a freshly-created pool already lands at the latest 76-column shape. The
script is **idempotent**: re-running it on an existing pool reports "no-op"
if the shape matches, and refuses to clobber if the shape differs.

Existing pool DBs are upgraded by dated migration scripts in `tools/`:

| Migration                                              | Ticket  | Change                                                       |
| ------------------------------------------------------ | ------- | ------------------------------------------------------------ |
| `migrate_miru_learning_pool_2026-05-19_qa-flow.py`     | PRO-926 | +`source_trace_json`, +`derived_from_json` (72 → 74 columns) |
| `migrate_miru_learning_pool_2026-05-20_state-model.py` | PRO-928 | three-axis state model replaces `promotion_status` (74 → 76) |

Each migration is idempotent and refuses to run against a drifted schema. If
the upstream `card_catalog` schema gains a new column on `cards` or
`card_variants`, the learning pool needs a follow-up migration ticket — do
not let the two schemas drift.

## What this DB is NOT

- Not a write target for the orchestration loop, the kernel, or any other
  system component. The shadow loop owns it. The operator reviews it.
- Not append-only. Rows are mutated as models refine confidence. The
  `data/miru_worker_runs.jsonl` append-only invariant does not apply here.
- Not backed up alongside `card_catalog.db`. Because this is experimental
  data the shadow loop is supposed to be able to reproduce, losing it is
  recoverable. (Migration scripts still take a one-off backup before they
  touch the file.)
