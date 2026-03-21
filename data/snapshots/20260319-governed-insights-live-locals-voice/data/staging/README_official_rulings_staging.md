# Official rulings / Q&A staging (JSON intake)

**Worktree-only. No scraping or live fetch.** Use this path to add real Bandai/OPTCG official rulings and Q&A so Miru can answer rules questions with source-backed references.

## Schema (staged rulings JSON)

Payload is either:
- **Single ruling:** one object with the fields below.
- **Multiple rulings:** `{ "rulings": [ {...}, ... ] }` with each element having the fields below.

### Required per ruling

| Field         | Type   | Description |
|---------------|--------|-------------|
| `ruling_id`   | string | Unique identifier (e.g. `optcg-faq-001`) |
| `ruling_text` or `answer_text` | string | Official answer / ruling text |
| `source_id`   | string | Source identifier (e.g. `official`) |

### Optional per ruling

| Field                | Type   | Description |
|----------------------|--------|-------------|
| `card_code`          | string | e.g. `OP01-001`; omit or null for general rulings |
| `topic_key`          | string | Topic (e.g. `card_effect_timing`, `counter_timing`) |
| `question_text`      | string | Question as published |
| `normalized_summary` | string | Short summary for display/search |
| `source_type`        | string | `faq` / `ruling` / `errata` / `rules_update` / `other_official` |
| `source_title`       | string | Title of the source (e.g. "OPTCG Official FAQ") |
| `source_url`         | string | URL of the source |
| `source_reference`   | string | Question number / page / section label |
| `source_anchor`      | string | Deep-link anchor if available |
| `published_at`       | string | Publication date (ISO or YYYY-MM-DD) |
| `effective_at`       | string | When the ruling is effective |
| `status`             | string | `current` / `historical` / `superseded` (default `current`) |
| `tags`               | string | Comma- or space-separated labels for search |

## How to prepare a staged official rulings file

1. Copy `official_rulings_example_template.json` or use the structure above.
2. Give each ruling a unique `ruling_id` (e.g. `optcg-faq-2024-001`).
3. Set `ruling_text` (or `answer_text`) and `source_id`.
4. For **card-specific** rulings set `card_code`; for **general** rulings leave `card_code` null or omit it.
5. Set `source_title`, `source_url`, and `source_reference` so Miru can show citations (e.g. "OPTCG Official FAQ", "Q&A No. 001").
6. Use `status: current` for active rulings and `historical` or `superseded` when a ruling has been replaced.

## How to ingest

From the repo root:

```bash
python -m tools.miru_official_rulings_ingest data/staging/your_rulings.json
```

Optional:

```bash
python -m tools.miru_official_rulings_ingest data/staging/your_rulings.json --rules-db data/miru_official_rules.db
```

Malformed JSON or missing required fields will print errors and exit with code 1.

## How source references are stored

Each ruling is stored in `official_card_rulings` with:

- **source_title** — Display title (e.g. "OPTCG Official FAQ").
- **source_type** — `faq`, `ruling`, `errata`, `rules_update`, or `other_official`.
- **source_reference** — Question number, section, or page (e.g. "Q&A No. 001", "Section 8.2").
- **source_url** — Full URL to the source.
- **source_anchor** — Optional deep-link fragment (e.g. `#q001`).

Future UI/insight surfaces can call `format_source_citation(ruling_row)` to get a dict with these fields for clean citation display.

## Current vs historical / superseded

- **status** is stored as given, or derived from `effective_at` (future date → treated as not yet current).
- Retrieval helpers (`get_current_rulings_for_card`, `get_rulings_for_topic`, `search_official_rulings`) accept a `status` parameter; default is `current` so only active rulings are returned unless you ask for `historical` or `superseded`.
- Miru can distinguish: **card-specific** (card_code set) vs **general** (card_code null), **errata-driven** (source_type errata), and **current vs historical** via the status field.

## Retrieval (for insights / Q&A)

- `get_current_rulings_for_card(rules_db_path, card_code)` — current rulings for a card.
- `get_rulings_for_topic(rules_db_path, topic_key, card_code=None)` — by topic, optionally for a card.
- `search_official_rulings(rules_db_path, card_code=None, query=None, tags=None, topic_key=None, status='current')` — flexible search.
- `get_best_official_ruling_match(rules_db_path, card_code=None, topic_key=None, query=None)` — single best match (card-specific preferred when requested).
- `format_source_citation(ruling_row)` — returns source_title, source_type, source_reference, source_url, source_anchor for display.

All live in `tools.miru_official_rules`.
