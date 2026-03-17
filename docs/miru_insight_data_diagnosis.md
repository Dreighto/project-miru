# Miru Insight Data Diagnosis

## Phase 1 — Current limitation

### Why only "synergy" insights are generated

Insight type | Data required | Current source | Status
-------------|---------------|-----------------|--------
**Meta** | `role_summary`, `deck_usage_summary` | `card_intelligence` in catalog (LEFT JOIN) | **Blocked**: `card_intelligence` table is not created or populated in the worktree; sync gets no rows.
**Price** | Current price per card | `prices.json` (watch data) + optional `card_intelligence.price_value` | **Partial**: Works when `prices.json` contains the card; no trend (prev price not stored).
**Usage / trend** | Deck frequency, core/flex/tech role | Same as meta (deck intel) | **Blocked**: Same as meta.
**Strength / gameplay** | Role tags (aggressive/control/support, tempo/utility) | Not wired | **Blocked**: No structured strength field; effect text exists in `basic_facts_json` but no keyword-derived tags.
**Lore / trivia** | Short spoiler-free trivia | `dossier.trivia` or `basic_facts.trivia` | **Blocked**: `learning_dossiers` has no `trivia` column; `basic_facts_json` is not populated with trivia.

**Synergy** is the only type that has enough data today: `basic_facts_json` contains `traits` and `effect_text` from verified dossiers, so trait-based and effect-based synergy lines are produced.

### What data exists

| Location | Fields | Used by insight sync |
|----------|--------|----------------------|
| **learning_dossiers** | card_code, card_name, set_code, rarity, basic_facts_json, source_summary, confidence, verification_state, updated_at | Yes (dossier + basic_facts for synergy, lore, meta fallback) |
| **basic_facts_json** | color, card_type, traits, effect_text, set_name, etc. | Yes (synergy, lore trivia when present) |
| **card_catalog.db** | cards, miru_card_insights, miru_validations; **card_intelligence** (expected but not created/populated) | catalog: yes; card_intelligence: empty |
| **prices.json** | By product_id; each item: code, price, target, last_checked_ts | Yes (price insight when code matches) |
| **miru_deck_intel.db** | leader_card_signals (leader_code, card_code, role_label, usage_percent, deck_count, …) | **No** — not read by sync |
| **learning_dossier_deck_usage** / **learning_card_usage** / **learning_dossier_market_signals** | Schema exists in learning engine | **No** — never populated; sync does not read them |

### What is missing

- **Meta/usage**: No `deck_usage_summary` or `role_summary` for cards. `leader_card_signals` in deck intel has this per (leader, card) but is not aggregated or fed into the sync.
- **Price trend**: Only current price; no previous price or change signal.
- **Strength**: No derived tags from effect text; no `strength_profile` or strategy_notes in the pipeline.
- **Trivia**: No `trivia` column on dossiers; no permitted source filling it yet.

### Summary

- **Meta / usage**: Blocked by missing ingestion from `miru_deck_intel.db` (leader_card_signals) into the insight pipeline.
- **Price**: Works for level when in prices.json; trend blocked by no history.
- **Strength**: Blocked by no derivation from effect text or strategy notes.
- **Lore**: Blocked by no trivia field and no source.

Implementing: (1) load deck intel usage and merge into “intelligence” for meta/usage; (2) derive strength from effect text keywords; (3) add optional trivia column and wire through; (4) price trend only when data exists later.

---

## Enrichment results (after implementation)

- **Data fields added or improved**
  - **Meta/usage**: Sync now loads leader_card_signals from miru_deck_intel.db, aggregates by card_code into deck_usage_summary / role_summary, and merges with catalog card_intelligence (catalog wins when present).
  - **Strength**: New insight type strength derived from effect text keywords (aggressive / control / support / tempo / utility); no fabrication.
  - **Trivia**: Optional column trivia on learning_dossiers (learning engine + sync ensure); lore insight uses it when present.
  - **Price**: Unchanged; trend not added (no prev price in current pipeline).

- **Sources used**
  - Existing pipeline: miru_deck_intel.db (leader_card_signals), prices.json, learning_dossiers + basic_facts_json, catalog card_intelligence when present.
  - No new scraping or external APIs.

- **Insight distribution after run (sample rebuild, limit 30)**
  - strength: 14, synergy: 14. Meta/price/lore: 0 in this sample (no deck intel rows for these cards; prices.json has other codes; no trivia populated yet).

---

## Remaining gaps

- **Meta/usage**: Depends on miru_deck_intel.db existing and leader_card_signals populated (e.g. by miru_compute_deck_signals after deck imports).
- **Price trend**: Not implemented; would require storing or receiving previous price.
- **Trivia**: Column and wiring in place; no ingestion from a permitted source yet.
- **Strength**: Keyword-based only; optional future: populate learning_dossier_strategy_notes and consume in sync.
