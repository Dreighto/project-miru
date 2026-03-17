# Miru Source-Agreement and Cross-Verification Design (Planning Only)

**Scope:** Design only. No implementation in this document.

**Goal:** Allow Miru to compare multiple approved sources for the same card and determine agreement level and confidence, so multiple snapshot-based sources (e.g. official-cardlist + community-cardlist) can be cross-verified.

---

## 1. Where source records are stored

| Location | Role |
|----------|------|
| **learning_dossier_sources** (worktree dossier DB) | One row per `(card_code, source_id, source_reference)`. Columns: `card_code`, `source_id`, `source_reference`, **field_payload_json** (full normalized record: card_name, set_code, effect_text, etc., plus source_id, source_url, fetched_at), `verification_state`, `fetched_at`, `updated_at`. So a card can have **multiple rows** (one per source). |
| **learning_dossiers** (same DB) | One row per card. **basic_facts_json** = merged view (last merge overwrites); **source_summary** = single string e.g. `"source_id: source_reference"`; **confidence**, **verification_state**. The dossier does **not** currently list all contributing sources; it reflects the result of the latest merge. |
| **miru_validations** (project DB) | After sync: **sources_json** (list of source entries with trust_tier, etc.), **winning_source_json**, **rejected_sources_json**, **conflict_summary_json** (rule, rejected_fields, accepted_conflict_fields, field_decisions). This is the **result** of comparing “existing” vs “incoming” at sync time, not a precomputed cross-check of all learning sources. |

So: **multi-source evidence** is already stored per card in **learning_dossier_sources** (multiple rows with different source_id). The dossier row itself and project sync today do not aggregate “all sources for this card” into an explicit agreement metric; sync merges incrementally (existing validation + one incoming payload).

---

## 2. How dossiers store source-backed fields

- **store_source_record** writes one row to `learning_dossier_sources` with `field_payload_json = record.to_dict()` (the full NormalizedSourceRecord).
- **merge_source_record_into_dossier** updates the **single** dossier row: it overwrites `basic_facts` with the incoming record’s fields plus existing basics, sets `source_summary` to that one record’s `source_id: source_reference`, and sets `confidence` to at least 0.9 for that merge. So the dossier does **not** retain a history of which source contributed which field; it only stores the latest merged snapshot and a single source_summary string.
- **field_payload_json** in `learning_dossier_sources` is the only place that keeps **per-source** field values. Comparing those rows for a given `card_code` gives you what each source says for that card.

---

## 3. How trust_tier is used in confidence scoring

- **Project sync** (`miru_project_sync.py`):
  - **\_score_source_confidence(source_entries):** Uses **best_tier** = min(trust_tier) and **distinct_sources** = count of unique source_ids. Base confidence: tier 1 → 0.95, tier 2 → 0.78, tier 3 → 0.58, tier 4 → 0.35. **Bonus:** if best_tier == 2 and distinct_sources >= 2, add 0.07 (cap 0.85). **Penalty:** if best_tier >= 3 and distinct_sources == 1, subtract 0.05.
  - **\_describe_confidence(..., conflict_count):** Builds human reason; e.g. “Multiple high-confidence community sources agree” when best_tier == 2 and distinct_sources >= 2; appends conflict_count if any.
- **\_select_value** (per-field merge): If existing == incoming → **reason "agreement"**, no conflict. Otherwise chooses by trust_tier and confidence (higher tier or same tier + higher confidence wins); conflict = True when values differ and one is rejected.
- So **agreement** is already implicit when existing == incoming; **multi-source bonus** is already applied when there are 2+ distinct sources at tier 2. What’s missing is an **explicit, comparable-field-level** agreement metric across **all** sources in `learning_dossier_sources` for a card, independent of the order of syncs.

---

## 4. How multiple sources could be compared

- **Today:** Comparison happens at **sync time**: one “incoming” payload (one or more source entries) is merged with “existing” validation; field_decisions and conflict_summary record agreement/conflict per field. There is **no** step that loads all `learning_dossier_sources` for a card and runs a single “agreement over all sources” pass.
- **To compare multiple sources explicitly:**
  1. **Read:** For a given `card_code`, `SELECT * FROM learning_dossier_sources WHERE card_code = ?` and parse `field_payload_json` for each row.
  2. **Normalize:** Use the same normalization as project sync (e.g. `clean_display_text` for text, coerce int for cost) so comparison is consistent.
  3. **Compare:** For each comparable field (card_name, set_code, set_name, rarity, color, card_type, cost, power, counter, attribute, traits, life, effect_text, trigger_text), compute whether values are equal (after normalization), or one is blank, or they conflict (differing non-blank).
  4. **Aggregate:** Derive an **agreement level** (see below) and optionally store or return it.

This can be done **on read** (no schema change) or **on write** (when a new source is stored, recompute agreement for that card and persist).

---

## 5. Minimal agreement model

- **Scope:** Per card, over all rows in `learning_dossier_sources` for that card.
- **Comparable fields:** The same fields used in project sync merge: card_name, set_code, set_name, rarity, color, card_type, cost, power, counter, attribute, traits, life, effect_text, trigger_text. (Traits: normalize to a canonical form, e.g. sorted list or “ / ”-joined, then compare.)
- **Per-field outcome:** For each field, across all source payloads:
  - **agree:** All non-blank values (after normalization) are equal.
  - **conflict:** At least two non-blank values differ.
  - **missing:** All values blank, or only one source has a value (no comparison).
- **Per-card agreement level (minimal):**
  - **single_source:** Only one source row for this card → no cross-verification.
  - **full:** Two or more sources, and for every comparable field with at least two non-blank values, all agree.
  - **partial:** Two or more sources; at least one field has agreement (multiple non-blank and equal), and no field has conflict.
  - **conflict:** At least one field has conflicting non-blank values across sources.
- **Optional numeric:** e.g. `agree_count`, `conflict_count`, `missing_count` over comparable fields; or a ratio `agree / (agree + conflict)` for fields that had at least two non-blank values.

---

## 6. How to compute agreement level

- **Input:** List of source rows for one card (from `learning_dossier_sources`), each with parsed `field_payload_json`.
- **Algorithm:**
  1. If len(sources) < 2 → return `agreement_level = "single_source"`, optional counts = 0.
  2. Define comparable fields (list above); normalize each source’s value per field (text: clean_display_text; int: coerce; traits: normalize to comparable form).
  3. For each field: collect distinct non-blank normalized values. If len(distinct) == 0 → missing. If len(distinct) == 1 → agree. If len(distinct) > 1 → conflict.
  4. Aggregate: if any field is conflict → level = "conflict". Else if any field is agree → level = "partial" (or "full" if all non-missing fields are agree). Else → level = "partial" or "single_source" (only missing).
  5. Optionally attach: list of agreeing fields, list of conflicting fields, source_count, and (if desired) trust_tier of each source for display.
- **Where to run:** As a pure function in the learning engine or project sync module, taking a list of `{source_id, source_reference, field_payload_json}` (or NormalizedSourceRecord-like dicts). Can be called from:
  - A new “agreement check” path that reads `learning_dossier_sources` for a card and returns agreement + details.
  - A post-store hook after `store_source_record` (recompute agreement for that card and optionally persist).
  - A batch job that periodically recomputes agreement for cards with 2+ sources.

---

## 7. How to surface agreement on Dev page or dossiers

- **Dossiers (worktree):**
  - **Option A (on read):** When serving a dossier (e.g. `fetch_dossier` or an API that returns dossier + sources), optionally compute agreement from `learning_dossier_sources` for that card and add keys e.g. `agreement_level`, `agreement_sources_count`, `agreeing_fields`, `conflicting_fields`. No schema change; computed when requested.
  - **Option B (stored):** Add an optional **agreement_summary_json** (or similar) to `learning_dossiers` or a small side table keyed by card_code, updated when sources change, so reads are cheap.
- **Dev page:**
  - **Source-agreement panel:** New section or tab that lists cards with **2+ sources** and their agreement level (full / partial / conflict), with expandable per-field detail (which sources agree vs conflict). Data from worktree dossier DB (query `learning_dossier_sources` grouped by card_code, then compute agreement).
  - **Validation audit (existing):** Already shows conflict_summary, winning_source, rejected_sources per validation. Could add a badge or line for “cross-source agreement: full/partial/conflict” when the card has multiple sources in `learning_dossier_sources`, using the same agreement function.
- **API:** e.g. `GET /api/dev/dossier-agreement?card_code=OP01-001` or `GET /api/dev/source-agreement?limit=50` returning list of cards with multiple sources and their agreement level + details.

---

## 8. How to store agreement results without breaking existing structures

- **Preferred (minimal):** **Do not add required columns.** Either:
  - **Compute on read only:** No new tables or columns; agreement is computed when needed (Dev panel, dossier API, or internal use). No migration; existing code paths unchanged.
  - **Optional column:** Add **agreement_summary_json** to **learning_dossiers** (nullable/default '{}'). Schema stays backward compatible; existing rows keep null/empty. Content e.g. `{"agreement_level": "full", "source_count": 2, "agreeing_fields": ["card_name", "set_code", ...], "conflicting_fields": [], "updated_at": "..."}`. Updated when we recompute (e.g. after storing a new source for that card, or in a batch).
- **Alternative:** New table **learning_dossier_agreement** with `card_code` (PK), `agreement_level`, `source_count`, `agreeing_fields_json`, `conflicting_fields_json`, `updated_at`. Keeps dossiers table unchanged; agreement is a separate view. Queries join or read on demand.
- **Avoid:** Changing `learning_dossier_sources` schema or the semantics of `basic_facts` / `source_summary`; those should remain as today so existing merge and sync logic is unchanged.
- **Project DB:** No need to store agreement in `miru_validations` for now; agreement is a worktree-side concept over learning_dossier_sources. If later we want “validations that had full agreement” in the project DB, we could add an optional column or derive from existing conflict_summary + sources_json.

---

## 9. Summary

- **Stored today:** Multiple sources per card live in **learning_dossier_sources** (one row per source, with full field_payload_json). Dossier row and project sync do not persist an explicit “agreement over all sources” metric.
- **Trust and confidence:** Project sync already uses trust_tier and distinct_sources for confidence and describes “multiple sources agree” when tier 2 and 2+ sources; per-field “agreement” exists when existing == incoming in _select_value.
- **Minimal agreement model:** Per-card, over all rows in learning_dossier_sources: classify each comparable field as agree / conflict / missing; then aggregate to **single_source** | **full** | **partial** | **conflict**.
- **Compute:** Pure function over list of source payloads; run on read and/or after store; optionally persist in **learning_dossiers.agreement_summary_json** or a small **learning_dossier_agreement** table.
- **Surface:** Dev “source agreement” panel or validation-audit extension; optional dossier API fields; no change to existing dossier merge or sync semantics.

This design keeps existing structures and flows intact and adds agreement as an optional, additive feature.
