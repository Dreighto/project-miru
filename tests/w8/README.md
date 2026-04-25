# W8 — Callbacks GC tests

Manual test recipe for `docker/n8n/workflows/w8_callbacks_gc.json` (PRO-77).

## What W8 does

Daily at 04:00 (cron `0 4 * * *`), the workflow:

1. Reads `/miru-data/pending_callbacks.jsonl`.
2. Groups rows by `token`. For each group, computes the most recent timestamp
   (`intent_written_at` from intent rows, `decided_at` from decided rows).
3. Drops both rows for any token where the newest timestamp is older than 48h.
   Pair-aware so a `decided` row never gets orphaned from its `intent` (which
   would degrade `w7006-lookup-pending`'s reverse scan in W7).
4. Atomic rewrite via `<file>.tmp` + `fs.renameSync`.
5. Logs a summary row to `/miru-data/routing_history.jsonl` with
   `outcome: "gc-callbacks"`.

Rows that can't be parsed or have no `token`/timestamp are kept verbatim
(fail-safe — never silently dropped).

## Manual smoke test

> Operator runs this. Don't run on prod data without snapshotting first.

```bash
# 1. Snapshot
cp data/pending_callbacks.jsonl data/pending_callbacks.jsonl.preGC

# 2. Seed (3 aged pairs at -72h, 2 fresh pairs at -1h)
node tests/w8/seed_aged_callbacks.js seed data/pending_callbacks.jsonl

# 3. Trigger W8 manually in the n8n UI
#    (Workflows -> "W8 - Callbacks GC" -> Execute Workflow)

# 4. Verify
node tests/w8/seed_aged_callbacks.js verify data/pending_callbacks.jsonl

# 5. Confirm GC log row was written
grep '"outcome":"gc-callbacks"' data/routing_history.jsonl | tail -1

# 6. Sanity: tap an inline button on a fresh Telegram dispatch.
#    W7 should resolve the callback normally (file rewrite preserved live rows).

# 7. Restore if anything looks off
mv data/pending_callbacks.jsonl.preGC data/pending_callbacks.jsonl
```

## Expected verify output (success)

```
[verify] file: data/pending_callbacks.jsonl
[verify] aged tokens dropped: 3/3
[verify] fresh tokens kept:   2/2
[verify] pre-seed tokens preserved: N/N
[verify] PASS
```

The verify script removes its sidecar (`*.w8seed.json`) on success.

## Notes

- The HMAC replay window in `w7004-hmac-validate` is ~10 minutes. The 48h GC
  threshold is conservative — kept generous for audit/observability margin.
  Revisit after the W2 design review research pass.
- Failure handling: any throw before `renameSync` leaves the original file
  intact. Errors bubble to n8n's error trigger -> W1.
