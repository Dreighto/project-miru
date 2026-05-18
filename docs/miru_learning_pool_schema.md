# miru_learning_pool.db — shadow-loop learning database

**Created:** 2026-05-17 (PRO-907)
**Location:** `data/miru_learning_pool.db`
**Created by:** `tools/create_miru_learning_pool.py` (idempotent)

## What this DB is

Working memory for the OP01 shadow-evaluation learning loop (PRO-908). The two
shadow-loop models — Qwen 2.5 7B (primary) and Mistral Small 3 7B (validator) —
read from and write to this DB as they learn OP01 cards.

It is **not** canon. `card_catalog.db` is canon. Rows in the learning pool
are experimental until the operator promotes them through the dev-page
review queue (PRO-909). The pool is the sandbox; the catalog is the
shelf.

## Why a separate DB

The shadow-loop models will be wrong, will contradict each other, and will
correct themselves over time. That churn must not muddy `card_catalog.db`,
which stays canonical and operator-verified.

## Tables

### `learned_cards` — 72 columns

A single row represents one model's current best understanding of one
(canonical_code, print_id) pair. Multiple rows can exist for the same pair
when different models disagree — the dev-page review queue (PRO-909) is
where the operator resolves that.

Column breakdown:

| Source              | Columns | Notes                                                         |
| ------------------- | ------: | ------------------------------------------------------------- |
| Own (pool-native)   |       2 | `id` PK + `created_at` timestamp                              |
| Mirrored: `cards`   |      34 | every column from `card_catalog.cards` except its `id`        |
| Mirrored: `card_variants` |   30 | every column from `card_catalog.card_variants` except its `id` |
| Learning metadata   |       6 | the six learning-only fields below                            |

The mirror is built **live** from `card_catalog.db` via `PRAGMA table_info`
at script-run time — it cannot drift from the source schema.

## Mirror mapping

A promoted row should slot into `card_catalog` with no transformation: the
card-level fields go to `cards`, the variant-level fields go to
`card_variants`, the six learning metadata fields are stripped.

### Column-name collision rule

Two columns exist in both `cards` and `card_variants`. To keep both, the
card_variants copy gets the `variant_` prefix:

| Collision        | Pool column name (cards copy) | Pool column name (variants copy) |
| ---------------- | ----------------------------- | -------------------------------- |
| `is_serialized`  | `is_serialized`               | `variant_is_serialized`          |
| `block_icon`     | `block_icon`                  | `variant_block_icon`             |

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

## The six learning-metadata columns

These are pool-only. Stripped on promotion to `card_catalog`.

| Column                | Type | Purpose                                                                                                                                                                                                                          |
| --------------------- | ---- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `confidence_score`    | REAL | How sure the loop is about this row (0.0–1.0). Set by the primary model, may be reweighted by the validator.                                                                                                                     |
| `learned_from`        | TEXT | Which question or correction taught this row. Free-text reference back to the source — a question ID, a review verdict ID, a Bandai URL, etc.                                                                                    |
| `last_verified`       | TEXT | ISO 8601 timestamp of the last successful verification. Drives staleness queries.                                                                                                                                                |
| `promotion_status`    | TEXT | One of `experimental` / `review-ready` / `promoted` / `rejected`. CHECK-constrained. Defaults to `experimental` on insert.                                                                                                       |
| `validator_agreement` | TEXT | Did the primary AI and the shadow validator agree on this row. Free-text so the validator can say _what_ it disagreed about, not just yes/no.                                                                                    |
| `contributing_model`  | TEXT | Which model produced/learned this fact. Load-bearing for the "should we switch a model" decision — drives the per-model performance view in PRO-909.                                                                             |

## Indexes

| Index name                                      | Columns                            | Why                                            |
| ----------------------------------------------- | ---------------------------------- | ---------------------------------------------- |
| `idx_learned_cards_identity`                    | `(canonical_code, print_id)`       | Natural identity lookup                        |
| `idx_learned_cards_promotion_status`            | `promotion_status`                 | Review-queue filtering                         |
| `idx_learned_cards_contributing_model`          | `contributing_model`               | Per-model performance view                     |
| `idx_learned_cards_last_verified`               | `last_verified`                    | Staleness queries                              |

## Promotion semantics (operator gate)

There is no automatic flow from this DB into `card_catalog.db`. The
promotion mechanism itself is out of scope for PRO-907 (likely a later
ticket). The shape of the data here is designed so that promotion is a
mechanical projection: drop the six metadata columns, split the row into
its cards-mirror and card_variants-mirror halves, INSERT/UPDATE
card_catalog.

## Schema-change discipline

The script `tools/create_miru_learning_pool.py` is **idempotent**: re-running
it on an existing pool reports "no-op" if the shape matches, and refuses to
clobber if the shape differs (it tells you to run a migration ticket
instead). If the upstream `card_catalog` schema gains a new column on
`cards` or `card_variants`, the learning pool needs a follow-up migration
ticket — do not let the two schemas drift.

## What this DB is NOT

- Not a write target for the orchestration loop, the kernel, or any other
  system component. The shadow loop owns it. The operator reviews it.
- Not append-only. Rows are mutated as models refine confidence. The
  `data/miru_worker_runs.jsonl` append-only invariant does not apply here.
- Not backed up alongside `card_catalog.db`. Because this is experimental
  data the shadow loop is supposed to be able to reproduce, losing it is
  recoverable. (If that changes, add a backup pass.)
