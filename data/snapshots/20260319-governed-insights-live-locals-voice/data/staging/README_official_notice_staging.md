# Official notice staging (JSON intake)

**Worktree-only. No scraping or live fetch.** Use this path to add real Bandai rules/banlist/block-rotation notices in a structured way.

## Schema (staged official notice JSON)

### Required

| Field        | Type   | Description |
|-------------|--------|-------------|
| `notice_id` | string | Unique identifier for the notice (e.g. `bandai-op-standard-2026-06`) |
| `title`     | string | Human-readable title |
| `source_id` | string | Source identifier; use `official` for Bandai official (must be in Miru’s official allowlist) |

### Optional (top-level)

| Field              | Type   | Description |
|--------------------|--------|-------------|
| `source_url`       | string | URL of the official notice |
| `source_reference` | string | Short reference (e.g. "Bandai OP TCG official site") |
| `region`           | string | Region code if applicable |
| `format_name`      | string | Format (default `standard`) |
| `notice_type`      | string | One of: `rules_update`, `banlist`, `block_update`, `ruling`, `errata`, `tournament_rule`, `other` |
| `published_at`     | string | Publication date (ISO date or YYYY-MM-DD) |
| `effective_at`     | string | When the notice takes effect (ISO or YYYY-MM-DD). **Future dates → stored as upcoming; current catalog not overwritten.** |
| `status`          | string | `current`, `upcoming`, `historical`, `superseded`. If empty, derived from `effective_at` (future → `upcoming`). |
| `summary`         | string | Short summary text |
| `affected_cards`  | array  | Card-level legality changes (see below) |
| `format_context`   | object | Format/block-rotation context (see below) |

### `affected_cards[]` (optional)

Each element:

| Field             | Type   | Required | Description |
|-------------------|--------|----------|-------------|
| `card_code`       | string | yes      | e.g. `OP01-001` |
| `legality_state`  | string | yes      | `legal`, `banned`, `restricted`, `rotated`, `unknown` (aliases like `ban` → `banned` accepted) |
| `effective_at`    | string | no       | Override notice-level effective date for this card |
| `notes`           | string | no       | Optional note |

### `format_context` (optional)

| Field                   | Type    | Description |
|-------------------------|---------|-------------|
| `block_rotation_active` | boolean | Whether block rotation is active for this format |
| `effective_at`         | string  | When this context applies |
| `notes`                | string  | Optional note |

## How to prepare a real official Bandai notice file

1. Copy `official_notice_example_template.json` (or use the structure above).
2. Set `notice_id` to a unique, stable id (e.g. date + format: `bandai-op-standard-2026-06`).
3. Fill `title`, `source_id` (`official`), and at least one of `source_url` or `source_reference`.
4. Set `effective_at` to the date the notice takes effect. Use a **future** date if the change is not yet in force — Miru will store it as **upcoming** and will **not** overwrite current catalog legality.
5. If the notice includes card-level changes, add `affected_cards` with `card_code` and `legality_state` (and optional `effective_at` per card).
6. If the notice describes format/block rotation, add `format_context`.
7. Save as JSON (e.g. `data/staging/bandai-op-standard-2026-06.json`).

## How to ingest

From the repo root:

```bash
python -m tools.miru_official_notice_ingest data/staging/your_notice.json
```

Optional:

```bash
python -m tools.miru_official_notice_ingest data/staging/your_notice.json --rules-db data/miru_official_rules.db
```

- **Validation:** Malformed JSON or missing/invalid required fields will print errors and exit with code 1.
- **Success:** Prints how many rows were written to notices, legality history, and format context.

## How Miru distinguishes current vs upcoming

- **effective_at** in the past or empty (or today) → notice/card row is treated as **current** (`is_current=1`, `is_upcoming=0` in the rules DB). The staged intake does **not** write to the card catalog; it only populates `data/miru_official_rules.db`. Applying current official state to the catalog is done by the governed batch (e.g. CSV path) or a separate process.
- **effective_at** in the future → notice status is set to **upcoming**; card rows in `official_legality_history` get `is_upcoming=1`, `is_current=0`. Current catalog legality is **not** overwritten. No false “current conflict” review item is created.

## Where data is written

| Content           | Table                      | DB file                |
|-------------------|----------------------------|------------------------|
| Notice metadata   | `official_rule_notices`     | `data/miru_official_rules.db` |
| Card legality     | `official_legality_history`  | same                   |
| Format/rotation   | `official_format_context`  | same                   |

The card catalog (`data/card_catalog.db`, `miru_card_legality`) is **not** updated by this intake. Use the governed batch (CSV) or another path to apply current official legality to the catalog when desired.

## Validation (after ingest)

- **Malformed input:** Run the tool on invalid JSON or on a file missing required fields; it exits with code 1 and prints a clear error (e.g. `Missing or empty required field: notice_id`).
- **DB checks:** After ingesting the example template, you can confirm rows in `data/miru_official_rules.db`:
  - `official_rule_notices`: one row with your `notice_id`, `status` = `upcoming` when `effective_at` is in the future.
  - `official_legality_history`: one row per entry in `affected_cards` with `is_upcoming=1`, `is_current=0` when the effective date is in the future.
  - `official_format_context`: one row when `format_context` is present.
- **Catalog:** This path does not write to the catalog, so current catalog legality is never overwritten by notice ingest, including for future-dated notices.
