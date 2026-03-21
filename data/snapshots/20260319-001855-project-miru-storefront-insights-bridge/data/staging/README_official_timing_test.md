# Staging fixtures for official rules / timing tests

**Worktree-local test data only. Not live production data.**

## Official timing test (future-dated legality)

Used to validate that future-dated official legality changes are stored as **upcoming**, not applied to current catalog and not surfaced as a false "current conflict" review item.

### Fixtures

- **op_official_timing_test_seed_current.csv** — One row: `OP01-001`, `legal`, format `standard`, effective `2020-01-01`. Establishes current catalog state so a later future-dated row can be tested as "would conflict if applied now."
- **op_official_timing_test_future.csv** — One row: `OP01-001`, `banned`, format `standard`, effective `2026-12-31`. Represents a future-dated official ban. Should be routed to `UPCOMING_STORED`, written to `data/miru_official_rules.db` with `is_upcoming=1`, and **not** overwrite catalog or create a review conflict.

### How to run the validation

From repo root:

```bash
python -m tools.validate_official_timing
```

Expected: "Validation PASSED" and confirmation that the future-dated item was stored as upcoming, catalog remained unchanged, and no misleading review item was created.

### Source ID

Both CSVs are intended to be run with `--source-id official` (or equivalent); the governed path only accepts official source IDs for legality writes.
