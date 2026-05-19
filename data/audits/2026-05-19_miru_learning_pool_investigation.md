# miru_learning_pool.db Investigation — PRO-925

**Date:** 2026-05-19
**Investigator:** CC (claude-code, project-miru-w1)
**Ticket:** PRO-925

## Summary

No blockers found — write path confirmed, row health healthy, script idempotency confirmed. **Ticket 2 (schema migration) is unblocked.**

One structural note: the live pool lives in the main checkout (`D:\dev\miru\data\miru_learning_pool.db`), not in any worktree `data/` directory. Worktrees have a 0-byte placeholder file at the same relative path (gitignored DBs are not shared between worktrees). Deliverables 2 and 3 were run against the main checkout DB.

---

## Deliverable 1 — Shadow-Loop Write Path

### Writers

**Primary writer:** `services/shadow_loop/db_writer.py:66`
`sqlite3.connect(pool_db)` — invoked by `launch.py:113-121` as the `writer` closure passed to `loop_runner.run_forever()`.

- Path sourced from: `services/shadow_loop/config.py:50-52`
- Resolution: `REPO_ROOT / "data" / "miru_learning_pool.db"`
  where `REPO_ROOT = Path(__file__).resolve().parent.parent.parent` (three levels up from `services/shadow_loop/`) = repo root
- Env-var override: `SHADOW_LOOP_POOL_DB` — not currently set
- Resolved path (main checkout): `D:\dev\miru\data\miru_learning_pool.db` ✓

**Secondary writer (promotion updates only):** `miru_ai/shadow_review.py:275`
`conn_rw = sqlite3.connect(pool_db)` — UPDATE to `promotion_status` field only, triggered when operator commits a verdict. Not an INSERT path.

- Path sourced from: `shadow_review.py:31` — `DEFAULT_POOL_DB = REPO_ROOT / "data" / "miru_learning_pool.db"`
  where `REPO_ROOT = Path(__file__).resolve().parent.parent` (two levels up from `miru_ai/`) = repo root
- Used as default when `pool_db` arg is `None`
- Also opens read-only at `shadow_review.py:110,165,238` for queue/item/verdict reads

### Non-writers

- `miru_ai/workers/learning_engine.py` — no references to `miru_learning_pool` at all. Not a write path.
- `services/shadow_loop/loop_runner.py` — reads only from `card_catalog.db` (for queue seeding and card lookup); writes to the pool via the injected `writer` closure (which resolves to `db_writer.upsert_learned_card`)
- `services/shadow_loop/launch.py:86-90` — checks `cfg.learning_pool_db.exists()` before loop start; does not write

### Path agreement

All writers target `data/miru_learning_pool.db` relative to repo root. The two independent `REPO_ROOT` computations (3-level vs 2-level parent walk) both resolve to the same repo root given the repo structure. No env-var redirects active. **All writers agree.**

### Worktree discrepancy (notable)

Worktrees do not share gitignored files from the main checkout. The worktree at `D:\dev\worktrees\project-miru\w1\data\miru_learning_pool.db` is a 0-byte file (created/touched 2026-05-19T15:53:32, no schema, no rows). The shadow loop service runs from the main checkout — all actual writes land in `D:\dev\miru\data\miru_learning_pool.db`. Any investigation or tooling run from a worktree that uses the default DB path will silently hit the wrong (empty) file.

---

## Deliverable 2 — Row Health

All queries run read-only (`file:...?mode=ro` URI) against `D:\dev\miru\data\miru_learning_pool.db`.
**Total rows:** 348

### Signal 1 — Promotion-status distribution

| promotion_status | count |
| ---------------- | ----- |
| experimental     | 348   |

**Assessment: HEALTHY.** All rows `experimental`. PRO-914 (auto-promotion gate) is a backlog item — no rows should have graduated past `experimental` yet.

### Signal 2 — Contributing-model distribution

| contributing_model | count |
| ------------------ | ----- |
| qwen2.5:7b         | 348   |

**Assessment: EXPECTED BY DESIGN.** Only the primary model (`qwen2.5:7b`) appears. The validator (`qwen2.5:14b`, per PRO-917) contributes via the `validator_agreement` JSON field — it does not write its own `contributing_model` rows. The db*writer comment confirms: *"contributing*model set to the primary model's identifier."* The ticket's expected value ("both shadow models writing") overstates what PR-A does; Ticket 2 should not assume a second model will appear in this column.

### Signal 3 — Time distribution

```
MIN(created_at):   2026-05-19T16:58:25Z
MAX(created_at):   2026-05-19T22:56:12Z
MIN(last_verified): 2026-05-19T16:58:25Z
MAX(last_verified): 2026-05-19T22:56:12Z
```

**Assessment: HEALTHY.** All activity today (2026-05-19). Timestamps span ~6 hours, confirming the loop has been running continuously — not stalled since day 1.

### Signal 4 — Set-prefix distribution

| prefix | count |
| ------ | ----- |
| OP01   | 348   |

**Assessment: HEALTHY.** All rows are OP01-scoped. No scope drift to other sets.

### Signal 5 — Null rate on learning metadata

| column              | NULL count | total |
| ------------------- | ---------- | ----- |
| confidence_score    | 0          | 348   |
| validator_agreement | 0          | 348   |
| last_verified       | 0          | 348   |

**Assessment: HEALTHY.** All learning metadata columns fully populated. Writers are correctly setting all three fields on every row.

### Signal 6 — Mutation indicator (created_at vs updated_at)

```
Total rows:                     348
sqlite_sequence.seq:           2421
Delta (upsert cycles implied): 2073
Rows where created_at != updated_at: 0
```

**Assessment: HEALTHY, with explanation.** The `db_writer` uses a DELETE + INSERT pattern (not UPDATE), so each upsert of an existing card deletes the old row and inserts a fresh one. `created_at == updated_at` for all rows because `updated_at` is set at INSERT time. The sequence counter (2421 vs 348 rows) confirms the loop has re-visited cards 2073 times — evidence of active multi-pass processing.

**Bonus finding:** `updated_at` is present in the `learned_cards` schema despite not appearing in `tools/create_miru_learning_pool.py`'s `METADATA_COLUMNS` list. It is mirrored from the `card_variants` table via the introspect-and-mirror step (confirmed: `card_variants` has `updated_at`; `cards` does not). This is expected behavior per the create script design.

**Full schema:** 72 columns (2 own + 33 from cards + 31 from card_variants + 6 learning metadata).

---

## Deliverable 3 — Script Idempotency

### Pre-run state

```
Path:      D:\dev\miru\data\miru_learning_pool.db
Size:      1,507,328 bytes
mtime:     2026-05-19 15:58:23 UTC
Row count: 348
```

### Backup

Backed up live DB to worktree: `data/miru_learning_pool.db.bak.2026-05-19T22-59-05Z` (1,507,328 bytes).
`.gitignore` updated to cover `data/miru_learning_pool.db.bak.*`.

_Note: A 0-byte placeholder in the worktree was also backed up earlier to `data/miru_learning_pool.db.bak.2026-05-19T22-55-39Z` (0 bytes) — that was the worktree's empty file, not the live pool._

### Script run

Command: `python D:\dev\miru\tools\create_miru_learning_pool.py`

```
stdout: learned_cards already exists with matching shape (72 columns). No-op.
exit code: 0
```

### Post-run state

```
Size:      1,507,328 bytes  (unchanged)
mtime:     2026-05-19 15:58:23 UTC  (unchanged — script wrote nothing)
Row count: 348  (unchanged)
```

### Idempotency verdict

**CONFIRMED.** Script detected the existing 72-column schema, matched it against the schema it would build from the current `card_catalog.db`, and exited 0 with no-op output. Schema has not drifted since the pool was created.

### Edge case noted (worktree context)

Running `python tools/create_miru_learning_pool.py` from a worktree (without `--db`) fails with exit code 2 (`FAIL: card_catalog.db missing`) because worktrees do not have `card_catalog.db` in their `data/` directory. This is not a bug in the script — it is a worktree environment limitation. Any automated CI that runs this script must point at the main checkout's `card_catalog.db` or run from the main checkout directory.

---

## Concerns and Flags

| #   | Severity | Finding                                                                                                   | Action                                                 |
| --- | -------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| 1   | LOW      | Worktree `data/miru_learning_pool.db` is a 0-byte placeholder; shadow loop always writes to main checkout | Document in service-catalog; no code change needed     |
| 2   | INFO     | Only `qwen2.5:7b` appears in `contributing_model`; ticket expected both models                            | By design (PR-A); Ticket 2 should not assume a 14b row |
| 3   | INFO     | `updated_at` column in schema sourced from `card_variants` mirror, not explicit in METADATA_COLUMNS       | Expected — no schema drift                             |
| 4   | INFO     | 2073 upsert cycles confirm active loop but all rows same-day; no multi-day history yet                    | Normal for a recently-started loop                     |

---

## Conclusion

**Write path:** Confirmed — all writers target `data/miru_learning_pool.db` relative to repo root; no env-var redirects; main checkout is the live target.

**Row health:** Healthy — 348 rows, all OP01, all `experimental`, all learning metadata populated, loop actively processing (2073 upsert cycles, 6-hour activity window today).

**Script idempotency:** Confirmed — exit 0, no-op output, row count and mtime unchanged, schema match verified against current card_catalog.

**Recommendation: Proceed to Ticket 2 (schema migration).**
