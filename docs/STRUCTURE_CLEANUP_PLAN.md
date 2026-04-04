# Post-Transition Structure Cleanup Plan

**Status:** PLANNING ONLY — no execution
**Date:** 2026-04-03
**Scope:** Architecture blueprint for clean subsystem separation

---

## Environment Law (immutable)

| Port  | System             | Role                        |
|-------|--------------------|-----------------------------|
| 18080 | Project Miru (PM)  | Dashboard UI + card library |
| 18765 | Miru AI / Dev      | AI server + intelligence    |
| 8080  | (reserved)         | Future publish lane         |

---

## 1. Current State Audit

### Top-Level Clutter (items that should not live at root)

| Item | Disposition |
|------|-------------|
| `query_sp.py`, `query_sp_images.py` | One-off query scripts → move to `tools/` |
| `variant_recon.py`, `variant_recon.csv` | One-off recon → move to `tools/` |
| `card_catalog.db`, `card_catalog_pre_source_registry.db` | Stale root copies (canonical is `data/card_catalog.db`) → delete or `.gitignore` |
| `tmp_*` (12 directories) | Completed migration/diag artifacts → archive or delete |
| `miru_fetch_test_*` | Temp test dir → delete |
| `nul` | Windows artifact → delete |
| `load_pub_review_queue.txt`, `pending_approvals_endpoint.txt` | Scratch notes → move to `docs/` or delete |
| `dev_monitor_context_export.txt` | Dev artifact → delete |
| `tmp_dev_status_live.json`, `tmp_unittest_out.txt` | Temp files → delete |
| `dashboard_restart.log` | Stale log → move to `logs/` or delete |
| `Miru_Brain_v19.docx`, `Miru_Brain_v20.docx` | Operator design docs → move to `docs/` |
| `run_miru_dev.ps1`, `run_sync.ps1`, `run_miru_worker_overlap.bat` | Startup scripts → move to `windows/` |
| `Project MIru 18080 UI.code-workspace` | IDE config → stays (root is conventional) |

### Subsystem Ownership Map

**Project Miru (PM) — port 18080:**
```
dashboard/
  app.py                    ← Flask UI server (PM entry point)
  miru_card_intel.py        ← card intel queries
  miru_dossier_queries.py   ← dossier read queries
  miru_intel_adapters.py    ← data adapters
  miru_intel_db.py          ← intel repository class
  miru_intel_models.py      ← data models
  miru_intel_pipeline.py    ← enrichment pipeline
  miru_intel_trust.py       ← trust scoring
  miru_snapshot_refresh.py  ← official snapshot refresh
  static/                   ← PM CSS/JS/icons
  templates/                ← PM HTML templates
  Dockerfile                ← PM container build
  requirements.txt          ← PM Python deps
```

**Miru AI / Dev — port 18765:**
```
tools/
  miru_ai_server.py         ← Flask AI server (Miru AI entry point)
  miru_ai.py                ← core AI logic
  miru_ai_onepiece.py       ← One Piece game AI
  miru_ai_sync_onepiece.py  ← sync logic
  miru_brain.py             ← brain/reasoning engine
  miru_learning_engine.py   ← continuous learner
  miru_env.py               ← shared env config
  miru_pushover.py          ← notifications
  miru_*.py (60+ files)     ← all AI workers, fetchers, ingestion
  static/                   ← Miru AI CSS/JS
  templates/                ← Miru AI HTML templates
  migrations/               ← DB migrations
  scripts/                  ← AI-specific scripts
```

**Shared Infrastructure (used by both):**
```
data/                       ← ALL databases, state files, caches
  card_catalog.db           ← canonical card DB (read: PM, read/write: AI)
  miru_dossiers.db          ← dossiers (read: PM, read/write: AI)
  miru_deck_intel.db        ← deck intel (both)
  miru_learning_*.db        ← learning DBs (AI only)
  miru_official_rules.db    ← rules DB (both)
  miru_source_cache.db      ← source cache (AI only)
  miru_user_decks.db        ← user decks (PM only)
config/                     ← approved sources config
secrets/                    ← service account credentials
.env                        ← environment variables
```

### Critical Cross-Boundary Imports

These `tools/` files import from `dashboard/` — this is the main coupling risk:

| tools/ file | imports from dashboard/ |
|-------------|------------------------|
| `miru_ai_sync_onepiece.py` | `dashboard.miru_card_intel` |
| `miru_insight_cache.py` | `dashboard.miru_intel_models` |
| `miru_intake.py` | `dashboard.miru_intel_db`, `dashboard.miru_snapshot_refresh` |
| `miru_refresh_official_snapshot.py` | `dashboard.miru_intel_db`, `dashboard.miru_snapshot_refresh` |
| `miru_run_sandbox_cycle.py` | `dashboard.miru_intel_db`, `dashboard.miru_snapshot_refresh` |
| `miru_verified_loop.py` | `dashboard.miru_intel_adapters`, `dashboard.miru_intel_db`, `dashboard.miru_intel_pipeline` |

**Direction:** tools → dashboard (AI depends on PM intel layer). No reverse dependency exists.

---

## 2. Proposed Target Structure

```
D:\dev\tcg-watcher-worktree\
│
├── CLAUDE.md
├── .env
├── docker-compose.yml
├── docker-compose.worktree.yml
├── Project MIru 18080 UI.code-workspace
│
├── pm/                          ← PROJECT MIRU (port 18080)
│   ├── server/                  ← current dashboard/
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── static/
│   │   └── templates/
│   └── intel/                   ← extracted shared intel layer
│       ├── __init__.py
│       ├── card_intel.py        ← from miru_card_intel.py
│       ├── dossier_queries.py   ← from miru_dossier_queries.py
│       ├── intel_adapters.py    ← from miru_intel_adapters.py
│       ├── intel_db.py          ← from miru_intel_db.py
│       ├── intel_models.py      ← from miru_intel_models.py
│       ├── intel_pipeline.py    ← from miru_intel_pipeline.py
│       ├── intel_trust.py       ← from miru_intel_trust.py
│       └── snapshot_refresh.py  ← from miru_snapshot_refresh.py
│
├── miru_ai/                     ← MIRU AI / DEV (port 18765)
│   ├── server.py                ← from miru_ai_server.py
│   ├── core/                    ← AI reasoning + brain
│   │   ├── brain.py
│   │   ├── ai.py
│   │   ├── ai_onepiece.py
│   │   └── insight_voice.py
│   ├── workers/                 ← background workers + fetchers
│   │   ├── learning_engine.py
│   │   ├── card_discovery_worker.py
│   │   ├── image_fetcher.py
│   │   ├── tcgcsv_fetcher.py
│   │   ├── verified_loop.py
│   │   └── ...
│   ├── ingestion/               ← data ingestion scripts
│   │   ├── intake.py
│   │   ├── import_card_csv.py
│   │   ├── import_decklist.py
│   │   └── ...
│   ├── governance/              ← safety, ethics, budget
│   │   ├── action_governance.py
│   │   ├── ethics_gates.py
│   │   ├── budget_guardrails.py
│   │   └── preflight_safety.py
│   ├── static/
│   ├── templates/
│   └── migrations/
│
├── shared/                      ← SHARED INFRASTRUCTURE
│   ├── env.py                   ← from miru_env.py
│   ├── pushover.py              ← from miru_pushover.py
│   ├── mongo_client.py          ← from miru_mongo_client.py
│   └── source_registry.py       ← from miru_source_registry.py
│
├── data/                        ← ALL databases + state (unchanged)
├── config/                      ← approved sources (unchanged)
├── secrets/                     ← credentials (unchanged)
├── logs/                        ← all log files
│
├── scripts/                     ← image processing utilities
│   ├── convert_thumbs_to_webp.py
│   ├── crop_leader_art.py
│   └── ...
│
├── windows/                     ← Windows service scripts (unchanged)
├── tests/                       ← all tests (unchanged initially)
├── docs/                        ← documentation (unchanged)
│
├── app/                         ← tcg-watcher container (legacy, unchanged)
└── qc/                          ← quality control (unchanged)
```

---

## 3. Phased Migration Strategy

### Phase 1: Shared Infra Stabilization

**Goal:** Clean the root, establish `shared/` module, remove temp debris.
**Risk:** LOW — no import changes, no runtime changes.

| Step | Action | Risk |
|------|--------|------|
| 1.1 | Delete all `tmp_*` directories (12 dirs) | None — completed migration artifacts |
| 1.2 | Delete `nul`, `miru_fetch_test_*`, `dev_monitor_context_export.txt`, `tmp_dev_status_live.json`, `tmp_unittest_out.txt` | None |
| 1.3 | Move `query_sp.py`, `query_sp_images.py`, `variant_recon.py`, `variant_recon.csv` → `tools/` | None — standalone scripts |
| 1.4 | Move `Miru_Brain_v19.docx`, `Miru_Brain_v20.docx` → `docs/` | None |
| 1.5 | Move `run_miru_dev.ps1`, `run_sync.ps1`, `run_miru_worker_overlap.bat` → `windows/` | Update any NSSM references that point at root |
| 1.6 | Delete or `.gitignore` root `card_catalog.db`, `card_catalog_pre_source_registry.db` | Verify no script references root copy |
| 1.7 | Move `dashboard_restart.log` → `logs/` | None |
| 1.8 | Move `load_pub_review_queue.txt`, `pending_approvals_endpoint.txt` → `docs/` or delete | None |
| 1.9 | Create `shared/__init__.py` as empty placeholder | None — prep for Phase 2 |

**Validation:** All services start on 18080 and 18765. No import errors.

---

### Phase 2: PM Boundary Creation

**Goal:** Create `pm/` directory with clean PM-only ownership.
**Risk:** MEDIUM — docker-compose `build` path changes, template path changes.

| Step | Action | Risk |
|------|--------|------|
| 2.1 | Create `pm/server/` and move `dashboard/*` into it | Docker build context change |
| 2.2 | Extract `pm/intel/` from the `miru_intel_*` / `miru_card_intel` / `miru_snapshot_refresh` modules | Import path changes in both PM and AI |
| 2.3 | Update `dashboard/app.py` internal imports to use `pm.intel.*` | Must update `sys.path` or use relative imports |
| 2.4 | Update `docker-compose*.yml` build path: `./dashboard` → `./pm/server` | Service restart required |
| 2.5 | Update `pm/server/Dockerfile` COPY paths if needed | Build validation required |

**Path-Sensitive Risks:**
- `dashboard/app.py` uses `BASE_DIR = Path(__file__).resolve().parent` — this is relative, safe to move
- `docker-compose.worktree.yml` has `build: ./dashboard` — must update
- `docker-compose.yml` has `build: ./dashboard` — must update
- Template paths use Flask's convention (`templates/`) — safe as long as `templates/` moves with `app.py`

**Validation:** `docker-compose build`, both containers start, PM UI loads on 18080.

---

### Phase 3: Miru AI Boundary Creation

**Goal:** Create `miru_ai/` directory with clean AI-only ownership.
**Risk:** HIGH — 100+ files, many intra-`tools/` imports, `sys.path` manipulation.

| Step | Action | Risk |
|------|--------|------|
| 3.1 | Create `miru_ai/` directory tree (`core/`, `workers/`, `ingestion/`, `governance/`) | None |
| 3.2 | Move `miru_ai_server.py` → `miru_ai/server.py` | NSSM service path, startup scripts |
| 3.3 | Move AI core files (`miru_ai.py`, `miru_brain.py`, etc.) → `miru_ai/core/` | All internal imports must update |
| 3.4 | Move worker files → `miru_ai/workers/` | Internal imports |
| 3.5 | Move ingestion files → `miru_ai/ingestion/` | Internal imports |
| 3.6 | Move governance files → `miru_ai/governance/` | Internal imports |
| 3.7 | Move shared modules (`miru_env.py`, `miru_pushover.py`, etc.) → `shared/` | Both PM and AI import these |
| 3.8 | Update all `from tools.miru_*` imports → `from miru_ai.*` or `from shared.*` | ~100+ import statements |
| 3.9 | Update all `from dashboard.miru_*` cross-imports → `from pm.intel.*` | 6 files with cross-boundary imports |
| 3.10 | Leave non-miru tools (`batch_process_tcgcsv_groups.py`, `process_tcgcsv_group.py`, `recon_*.py`, etc.) in `tools/` as standalone utilities | None |

**Path-Sensitive Risks:**
- `miru_ai_server.py` uses `PROJECT_ROOT = Path(__file__).resolve().parent.parent` — will break if nesting depth changes
- `miru_ai_server.py` references `PROJECT_ROOT / "data" / "*.db"` in ~20 places — must audit all
- `tools/static/` and `tools/templates/` are served by Flask in `miru_ai_server.py` — template_folder and static_folder must update
- `miru_learning_engine.py` is launched as `tools.miru_learning_engine` by subprocess — must update launch command
- Several files use `sys.path.insert(0, str(PROJECT_ROOT))` — must verify after move
- NSSM service for 18765 points to a specific script path — must update

**Validation:** Miru AI starts on 18765. Learner subprocess launches. All AI endpoints respond.

---

### Phase 4: Script/Service Path Updates

**Goal:** Update all external references to new paths.
**Risk:** MEDIUM — affects live service startup.

| Step | Action | Risk |
|------|--------|------|
| 4.1 | Update `windows/start_miru_ai_dev.ps1` paths | Service won't start if wrong |
| 4.2 | Update `windows/start_project_miru_dashboard.ps1` paths | Service won't start if wrong |
| 4.3 | Update `windows/op_miru_runtime.ps1` paths | Service won't start if wrong |
| 4.4 | Update `windows/start_op_miru_worktree.ps1` paths | Service won't start if wrong |
| 4.5 | Update NSSM service definitions if they reference old paths | Must match new layout |
| 4.6 | Update `docker-compose.yml` and `docker-compose.worktree.yml` | Build context paths |
| 4.7 | Update `.code-workspace` file if it has folder references | IDE convenience |
| 4.8 | Verify `data/` path references (all use `PROJECT_ROOT / "data"`) | Should be stable if `PROJECT_ROOT` is correct |
| 4.9 | Verify `secrets/` path references | Same as above |
| 4.10 | Update test imports in `tests/` | Test runner must find new module paths |

**Validation:** Full cold-start test — NSSM services start both ports. All tests pass.

---

### Phase 5: Worker Law Updates

**Goal:** Codify boundaries so future workers cannot accidentally edit the wrong subsystem.
**Risk:** LOW — documentation and linting only.

| Step | Action | Risk |
|------|--------|------|
| 5.1 | Add `pm/CLAUDE.md` — PM worker boundary rules | None |
| 5.2 | Add `miru_ai/CLAUDE.md` — Miru AI worker boundary rules | None |
| 5.3 | Update root `CLAUDE.md` with subsystem map | None |
| 5.4 | Add import boundary linting (optional: `import-linter` or similar) | Optional tooling |
| 5.5 | Update `docs/RUNTIME_AUTHORITY_MATRIX.md` | None |
| 5.6 | Update Notion environment law with new paths | None |

---

## 4. Risk Summary

| Risk | Severity | Mitigation |
|------|----------|------------|
| `PROJECT_ROOT` calculation breaks after moves | HIGH | Audit every `Path(__file__).resolve().parent` chain; add `shared/paths.py` |
| Subprocess launch of learner uses `tools.miru_learning_engine` | HIGH | Update to `miru_ai.workers.learning_engine`; test subprocess spawn |
| Docker build context changes | MEDIUM | Update compose files; rebuild and test |
| NSSM service paths hardcoded | MEDIUM | Update NSSM config after Phase 4 |
| `from dashboard.*` cross-imports in 6 files | MEDIUM | Extract `pm/intel/` first (Phase 2), then update imports (Phase 3) |
| Flask `template_folder` / `static_folder` relative paths | MEDIUM | Both servers use `Path(__file__).parent`-relative — safe if assets move with server |
| `data/` path references (~30 occurrences) | LOW | All use `PROJECT_ROOT / "data"` — stable if `PROJECT_ROOT` is correct |
| Tests break due to import path changes | LOW | Batch-update test imports in Phase 4 |

---

## 5. Files Remaining in `tools/` (non-Miru standalone utilities)

These stay in `tools/` — they are standalone TCGCSV/recon/diagnostic scripts with no Miru prefix:

- `batch_process_tcgcsv_groups.py`
- `process_tcgcsv_group.py`
- `recon_*.py` (3 files)
- `reprocess_op01_mappings.py`
- `print_group_mapping.py`
- `fetch_tcgcsv_opcg_groups.py`
- `rebuild_market_tables.py`
- `delete_p038_p040.py`
- `fix_event_null_cost.py`
- `fix_release_set_names.py`
- `backfill_market_product_image_url.py`
- `move_variant_images.py`
- `verify_base_image_paths.py`
- `phase_a_*.py`, `phase_b_*.py` (image pipeline)
- `diag_*.py` (7 diagnostic scripts)
- `_clean_*.py`, `_test_*.py` (scratch)
- `test_cdn_fetch.py`

---

## 6. data/ Cleanup Candidates

The `data/` directory has significant artifact accumulation (~40 `governed_batch_*` files). Consider:

- Archive `governed_batch_report_*.json` and `governed_batch_review_queue_*.json` to `data/archive/`
- Archive `governed_batch_summary_*.txt` to `data/archive/`
- Move completed fetch/sync logs (`*_v1.txt`, `*_v2.txt`) to `data/archive/`
- Keep active DBs, state files, and current snapshots in place

---

## 7. Execution Prerequisites

Before starting Phase 1:
1. Ensure full git commit of current state
2. Verify NSSM service paths are documented
3. Create rollback checkpoint
4. Confirm operator approval for each phase

Each phase is independently reversible via `git revert`.
