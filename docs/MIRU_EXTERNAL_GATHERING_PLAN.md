# Miru External Gathering — Safe Path Plan (Worktree-First, Ethics-First)

**Scope:** Investigation/planning only. No implementation of new gathering flows until explicitly requested.

**Repo:** Project Miru worktree @ `C:\Users\andre\.codex\worktrees\0814\tcg-watcher`

---

## 1. Summary

- **Current state:** The worktree learner/sync loop is usable. Intake is **registry-gated**: only registered sources are processed; unregistered sources log `SOURCE_NOT_REGISTERED` and fail; `requires_api` sources log `API_REQUIRED_SOURCE_DETECTED` and fail. Provenance (source_id, source_reference, fetched_at, verification_state) is stored in learning dossiers and flows into project sync and confidence scoring.
- **External today:** “External” intake is limited to **official-cardlist** and **official-card-images** via snapshot JSON (path or URL). There are no generic HTML scrapers; no robots.txt/terms checks; no file-based allowlist; discovery produces **candidates** only (stored in `discovered_sources` with `pending_review`), with **no automatic promotion** to the registry or to gather tasks.
- **Safe path:** To let Miru “gather from allowed sites” safely, the project should: (1) keep approval explicit and manual (discovery → review → optional registry add); (2) add a **worktree allowlist config** so only explicitly approved domains/source_ids can be used for live fetches; (3) enforce **allowed_access** and **request_spacing_seconds** at fetch time; (4) add optional robots/terms checks before first fetch per domain; (5) improve Dev page visibility for discovery and source-block events; (6) keep all writes worktree-only and preserve provenance/confidence; no automatic main-repo promotion.

**Recommended first implementation step:** Add a small **worktree allowlist config** (e.g. `config/miru_approved_sources.json` or worktree-scoped path) and load it in `build_source_registry()` so that approved external sources can be added without code changes, while keeping discovery → approval flow manual and leaving robots/terms and throttling enforcement for the next step.

---

## 2. Current Source-Ingestion Architecture

| Component | Location | Role |
|-----------|----------|------|
| **Source registry** | `tools/miru_source_registry.py` | `MiruSourceEntry` (source_id, trust_tier, enabled, fetch_mode, refresh_policy, rate_limit_hint, backoff_policy, review_state, domain, allowed_access, requires_api, publish_allowed, request_spacing_seconds, default_confidence). `DEFAULT_SOURCE_REGISTRY` is in-code only. `build_source_registry(extra_entries)` returns a copy of the default registry and merges in optional extra entries; **no file or allowlist loading**. |
| **Source adapters** | `tools/miru_source_adapters.py` | `OfficialCardListSourceAdapter`, `OfficialCardImageSourceAdapter`. Both load from payload / snapshot_path / snapshot_url; card list and images expect JSON. `from_url()` uses `urlopen` with timeout; **no request_spacing or throttling** in adapter code. |
| **Discovery** | `tools/miru_source_discovery.py` | `discover_source_candidate(url, title, notes)` → `DiscoveredSourceCandidate` (url, host, source_kind, confidence_score, review_status=`"pending_review"`). `DISCOVERY_RULES` map source kinds (deck_database, card_database, tournament_results, meta_analysis) to host hints (e.g. limitlesstcg, onepiecetopdecks). **Does not add to registry or enqueue gather tasks.** |
| **Learning engine** | `tools/miru_learning_engine.py` | Uses `get_source_entry` / `get_source_entry_or_none` with `self.source_registry`. In `process_claimed_task()`: if source not in registry → log `EVENT_SOURCE_NOT_REGISTERED`, fail task; if `entry.requires_api` → log `EVENT_API_REQUIRED_SOURCE_DETECTED`, fail task. Only then does it run task handlers (bootstrap_dossier, fetch_card_list, fetch_card_image, etc.). Queue table has `source_id`; tasks get source_id from `enqueue_task(..., source_id=...)`. Writes go to worktree DBs (dossier, queue, status, engine_log). |
| **Project sync** | `tools/miru_project_sync.py` | Consumes learning dossiers and source metadata; builds `source_entries` and `winning_source_json`; computes confidence and confidence_reason; writes to project DB (miru_validations, miru_card_insights). Uses `build_source_registry()` and trust_tier for scoring. |
| **Dev / visibility** | `tools/miru_ai_server.py` | Status snapshot includes `discovery_candidate_count`, `discovery_pending_review_count`, `review_queue_count`, `api_permission_events`. Activity feed maps engine_log event_type to human-readable items; **source_not_registered** and **api_required_source_detected** are shown with “Blocked” / “API permission required” titles. |

**Provenance and confidence:** Normalized records carry `source_id`, `source_url`, `source_reference`, `fetched_at`. Learning dossiers store `learning_dossier_sources` (source_id, source_reference, verification_state) and `learning_dossier_images` (source_id, verification_state, etc.). Project sync uses `trust_tier` and source_entries to compute confidence and confidence_reason; no main-repo promotion is automatic.

---

## 3. Sources Already Supported (and Which Are “External”)

- **official-cardlist** — Enabled. Snapshot JSON (path or URL). Trust tier 1. Used for card list ingestion.
- **official-card-images** — Enabled. Snapshot JSON (path or URL). Trust tier 1. Used for image metadata; actual image bytes can be fetched from URLs in the snapshot.
- **reputable-card-db**, **community-market**, **manual-review** — Defined in `DEFAULT_SOURCE_REGISTRY` with `enabled=False` or conservative settings (manual-only, no auto poll). Not used for automated intake today; they are placeholders for future approved community/external sources.

**Partially supported:** Discovery identifies **candidates** (e.g. limitlesstcg, onepiecetopdecks) and stores them in `discovered_sources` with `review_status='pending_review'`. There is **no adapter** for generic “external” HTML or API; no code path yet that turns an approved candidate into a registered source and then enqueues gather tasks for it. So “external” in the sense of “any allowed third-party site” is **not** supported beyond the two official snapshot-based sources.

---

## 4. What Is Missing for Safe Approved-Site Gathering

| Gap | Detail |
|-----|--------|
| **Approval rules** | Only in-code `DEFAULT_SOURCE_REGISTRY` exists. No file-based or worktree allowlist; no way to “approve” a discovered candidate and have it become a registered source without editing Python. |
| **Source allowlist config** | No path (e.g. `config/miru_approved_sources.json`) from which to load extra `MiruSourceEntry` or (domain, source_id) allowlist. `build_source_registry(extra_entries)` accepts in-memory entries only. |
| **Robots / terms** | No checks of robots.txt or terms of use before fetching. Required for ethics and site-policy compliance before enabling live requests to new domains. |
| **Throttling / cadence** | `request_spacing_seconds` and rate_limit_hint exist on `MiruSourceEntry` but are **not applied** in adapters (e.g. no sleep before/after `from_url()`). So throttling is advisory only. |
| **Retry / backoff** | Backoff is described in registry (e.g. “exponential backoff with manual retry”); no automatic retry loop or backoff logic in adapters or learning engine. Failures surface as task fail + engine_log. |
| **allowed_access enforcement** | `allowed_access` (e.g. public_page, permitted_api, manual_only) is stored but **not enforced** in `process_claimed_task` or in adapters. Only `requires_api` is enforced (blocks automated use). |
| **Error visibility** | Engine log and Dev page already surface task_failed, source_not_registered, api_required_source_detected. What’s missing: a dedicated view of **discovered_sources** (list + review_status) and, if desired, a clear “blocked sources” summary. |
| **Dev page reporting** | Snapshot exposes counts (discovery_candidate_count, discovery_pending_review_count, review_queue_count, api_permission_events). No UI yet to list discovered candidates or to “approve” a candidate into the registry (that flow should stay manual; UI can still show the list and link to where to add config). |

---

## 5. Recommended Safe Rollout Path

1. **Enable first (minimal, safe)**  
   - **Worktree allowlist config:** One approved-sources file (e.g. under worktree `config/` or a known data path) that `build_source_registry()` can load and merge into the registry. Only source_ids (and optionally domains) listed there are allowed for gathering.  
   - **Keep manual:** Discovery → review → decision to add to allowlist. No automatic promotion of discovered_sources to registry. No automatic main-repo promotion.  
   - **First “external” use case:** Add one or two explicitly approved community sources (e.g. a single read-only API or a single domain with clear public-page policy) via the allowlist, with `allowed_access=public_page`, `requires_api=False`, and conservative `request_spacing_seconds`.

2. **Keep manual**  
   - Adding/removing sources to the registry (via allowlist or code).  
   - Any source with `allowed_access=manual_only` or `requires_api=True`: only operator-triggered tasks.  
   - Decisions to fetch from a new domain (after robots/terms check).

3. **Throttle**  
   - Apply `request_spacing_seconds` in the code path that performs HTTP requests (e.g. in adapters or a small fetch helper used by them). Prefer per-source_id spacing.  
   - For new external domains, start with a conservative default (e.g. 2–3 seconds) and make it overridable per entry in the registry/allowlist.

4. **Surface for review**  
   - Dev page: list of discovered_sources (url, host, source_kind, review_status, confidence_score) so operators can review and decide whether to add to allowlist.  
   - Continue surfacing source_not_registered and api_required_source_detected in the activity feed.  
   - Optional: simple “blocked source” count or list (tasks failed due to unregistered or API-required source) for quick visibility.

---

## 6. Guardrails / Throttles Needed

- **Registry gate (existing):** Only registered source_ids are processed; unregistered and requires_api are blocked and logged.  
- **Allowlist gate (new):** Only source_ids (and optionally domains) present in the worktree allowlist config may be used for live external fetches (if we split “registered” vs “approved for live fetch” we can do it via a flag or a separate allowlist). For the smallest step, “approved for gathering” = present in registry, where registry can be extended from file.  
- **allowed_access:** Enforce in `process_claimed_task`: e.g. if `allowed_access == ALLOWED_ACCESS_MANUAL_ONLY`, do not run automated tasks for that source (only operator-triggered).  
- **request_spacing_seconds:** Apply before (or after) each HTTP request in the adapter/fetch path for that source_id.  
- **Robots/terms (later):** Before first fetch to a new domain, optionally fetch robots.txt and/or record that terms have been checked; skip or flag if disallowed.  
- **No paywall bypass:** Do not add logic to bypass paywalls or auth; `requires_api` and `allowed_access=permitted_api` / `manual_only` already prevent unauthorized use.  
- **Worktree-only writes:** All learning and sync writes remain in worktree DBs/paths; no automatic promotion to main repo.

---

## 7. Files / Functions Involved

| Area | File(s) | Functions / spots |
|------|---------|-------------------|
| **Registry and allowlist** | `tools/miru_source_registry.py` | `build_source_registry()`, `DEFAULT_SOURCE_REGISTRY`, `MiruSourceEntry` (allowed_access, request_spacing_seconds, domain). **Add:** loading from worktree config path (e.g. `config/miru_approved_sources.json` or env/configurable path) and merging into registry. |
| **Adapters** | `tools/miru_source_adapters.py` | `OfficialCardListSourceAdapter.from_url()`, `OfficialCardImageSourceAdapter.load_payload()` (snapshot_url). **Add (when enforcing throttle):** use `source_entry.request_spacing_seconds` (e.g. sleep before/after request or in a shared fetcher). |
| **Learning engine** | `tools/miru_learning_engine.py` | `process_claimed_task()` (registry + requires_api check), `enqueue_task()`, task handlers (e.g. `handle_fetch_card_list`, `handle_fetch_card_image`). **Add:** optional check for `allowed_access == manual_only` and block automated run; ensure task source_id comes from registry/allowlist only. |
| **Discovery** | `tools/miru_source_discovery.py` | `discover_source_candidate()`, `discover_source_candidates()`. No change required for “safe path”; discovery stays as candidate producer. |
| **Project sync** | `tools/miru_project_sync.py` | `_build_source_entry()`, `_score_source_confidence()`, `_describe_confidence()`. Already use source_id and trust_tier; no change needed for provenance if new sources use same schema. |
| **Dev / status** | `tools/miru_ai_server.py` | Status snapshot (discovery counts, api_permission_events), activity feed (event_type → human-readable). **Add (optional):** endpoint or snapshot field that returns list of `discovered_sources` for review; keep approval action out of scope (manual config edit). |
| **Config location** | New or existing config dir | Recommended: worktree-scoped path, e.g. `config/miru_approved_sources.json` (or `data/miru_approved_sources.json`), with format that can be turned into `MiruSourceEntry` or (source_id, domain) allowlist. |

**Provenance / dossiers / insights:** Existing schema already supports any registered source_id: `learning_dossier_sources`, `learning_dossier_images`, and project sync’s `sources_json` / `winning_source_json` and confidence logic. New external sources should set `source_id`, `source_reference`, `fetched_at`, and appropriate `verification_state`; confidence will follow from `trust_tier` and existing scoring in project sync.

---

## 8. Recommended Implementation Step (Single, Smallest Safe Step)

**Step:** Add **worktree allowlist config** so that approved external sources can be registered without code changes, and keep all gathering rules unchanged otherwise.

- **What to do:**  
  - Define a worktree-scoped config file path (e.g. `config/miru_approved_sources.json`).  
  - Define a minimal format (e.g. list of objects with `source_id`, optional `domain`, optional overrides for `enabled`, `request_spacing_seconds`, `allowed_access`, `trust_tier`, etc.).  
  - In `build_source_registry()`, if the file exists, load it and merge entries into the registry (e.g. create `MiruSourceEntry` from each item, or add to `extra_entries` and merge).  
  - Document that only sources in the default registry or in this file are allowed; discovery still only creates candidates; promotion to “allowed” is by adding to this file (or to code) after human review.

- **What not to do in this step:**  
  - Do not implement generic scrapers or new fetch modes.  
  - Do not add robots.txt or terms checks yet.  
  - Do not change adapter throttling (can be a follow-up).  
  - Do not automate discovery → registry or main-repo promotion.

This gives a clear, ethics-preserving path: “Miru can gather from allowed sites” once an operator has added those sites to the allowlist config and the project has (later) enforced allowed_access, throttling, and optionally robots/terms for those domains.

---

*End of plan. No implementation of new gathering flows has been performed; only the above configuration step is recommended as the first implementation.*
