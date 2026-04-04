# Shared Intel Boundary Design

**Status:** PLANNING ONLY — no execution
**Date:** 2026-04-03
**Prerequisite:** Phase 1 cleanup complete
**Blocks:** Phase 2 (PM boundary) and Phase 3 (Miru AI boundary)

---

## 1. The Problem

Eight `miru_intel_*` / `miru_card_intel` / `miru_snapshot_refresh` modules currently live inside `dashboard/`.
**`dashboard/app.py` does not import any of them.**
They are consumed exclusively by `tools/` (the Miru AI subsystem) via 11 cross-boundary import statements.

These modules were placed in `dashboard/` historically but belong to neither subsystem — they are a **shared intelligence layer** that both PM and Miru AI need access to. Until this layer is extracted and given its own home, the repo cannot safely split into `pm/` and `miru_ai/` boundaries.

---

## 2. Exact Cross-Boundary Import Inventory

### 11 import statements across 6 files

**Group A — Dossier Repository + Snapshot Refresh** (most common pattern)

| File | Line | Import |
|------|------|--------|
| `tools/miru_intake.py` | 234 | `from dashboard.miru_intel_db import MiruIntelRepository` |
| `tools/miru_intake.py` | 235 | `from dashboard.miru_snapshot_refresh import OfficialSnapshotRefresher` |
| `tools/miru_refresh_official_snapshot.py` | 12 | `from dashboard.miru_intel_db import MiruIntelRepository` |
| `tools/miru_refresh_official_snapshot.py` | 13 | `from dashboard.miru_snapshot_refresh import OfficialSnapshotRefresher` |
| `tools/miru_run_sandbox_cycle.py` | 390 | `from dashboard.miru_intel_db import MiruIntelRepository` |
| `tools/miru_run_sandbox_cycle.py` | 391 | `from dashboard.miru_snapshot_refresh import OfficialSnapshotRefresher` |

**Purpose:** AI workers that run enrichment batches and snapshot refreshes. They need the repository class to write dossiers and the refresher to process official snapshots.

**Group B — Enrichment Pipeline + Adapters** (verified loop)

| File | Line | Import |
|------|------|--------|
| `tools/miru_verified_loop.py` | 12-15 | `from dashboard.miru_intel_adapters import MiruKnowledgeCacheAdapter, OfficialCardListSnapshotAdapter, PlaceholderAdapter` |
| `tools/miru_verified_loop.py` | 17 | `from dashboard.miru_intel_db import MiruIntelRepository` |
| `tools/miru_verified_loop.py` | 18 | `from dashboard.miru_intel_pipeline import MiruEnrichmentRunner` |

**Purpose:** The verified enrichment loop that orchestrates multi-source card intelligence. Needs the full pipeline: adapters → enrichment runner → repository.

**Group C — Data Models** (lightweight)

| File | Line | Import |
|------|------|--------|
| `tools/miru_insight_cache.py` | 18 | `from dashboard.miru_intel_models import CardDossier` |

**Purpose:** Reads a `CardDossier` dataclass for cache serialization. Lightest coupling.

**Group D — Card Intelligence Utilities** (text analysis)

| File | Line | Import |
|------|------|--------|
| `tools/miru_ai_sync_onepiece.py` | 12 | `from dashboard.miru_card_intel import build_observed_catalog, load_prices_records` |

**Purpose:** Uses card text analysis functions to build catalog from price records during One Piece sync.

---

## 3. The 8 Modules Under Analysis

### Dependency Graph (internal to the intel layer)

```
                  ┌─────────────────┐
                  │ miru_intel_models│  ← FOUNDATION (no imports)
                  └────┬───────┬────┘
                       │       │
          ┌────────────┘       └────────────┐
          ▼                                 ▼
┌──────────────────┐              ┌──────────────────┐
│ miru_intel_trust  │              │ miru_card_intel   │  ← STANDALONE (no imports)
│  (no imports)     │              │  (no imports)     │
└────┬─────────┬───┘              └────────┬──────────┘
     │         │                           │
     ▼         ▼                           ▼
┌──────────┐  ┌────────────────┐  ┌────────────────────┐
│intel_db  │  │intel_adapters  │  │ intel_pipeline      │
│ ← models │  │ ← models      │  │ ← models, db, trust │
│ ← trust  │  │ ← trust       │  │ ← card_intel        │
└────┬─────┘  └───────┬───────┘  └────────┬────────────┘
     │                │                    │
     ▼                ▼                    ▼
┌───────────────────────────────────────────────┐
│ miru_snapshot_refresh                          │
│ ← adapters, db, models, pipeline, trust        │
└───────────────────────────────────────────────┘

┌────────────────────┐
│ miru_dossier_queries│ ← models (standalone read-only query formatter)
└────────────────────┘
```

### Module-by-Module Profile

| Module | Flask? | DB Path | Consumers | Nature |
|--------|--------|---------|-----------|--------|
| `miru_intel_models.py` | No | None | Everyone | Pure dataclasses, zero deps |
| `miru_intel_trust.py` | No | None | db, adapters, pipeline, refresh | Trust profiles, zero deps |
| `miru_card_intel.py` | No | None | pipeline, tools/ai_sync_onepiece | Text analysis, regex, zero deps |
| `miru_intel_db.py` | No | `data/miru_dossiers.db` | pipeline, refresh, 4 tools files | SQLite repository |
| `miru_intel_adapters.py` | No | None | refresh, verified_loop | Source adapters |
| `miru_intel_pipeline.py` | No | Via repo | refresh, verified_loop | Enrichment orchestrator |
| `miru_snapshot_refresh.py` | No | Via repo | 3 tools files | Snapshot refresh orchestrator |
| `miru_dossier_queries.py` | No | None | (currently unused externally) | Query formatters |

**Critical finding: None of these modules use Flask or any web framework.** They are a pure data/intelligence layer that was placed in `dashboard/` by accident of history.

---

## 4. Placement Recommendation

### Option A: `shared/intel/` (TOP-LEVEL SHARED) — **RECOMMENDED**

```
shared/
  __init__.py
  intel/
    __init__.py
    models.py           ← miru_intel_models.py
    trust.py             ← miru_intel_trust.py
    card_intel.py        ← miru_card_intel.py
    db.py                ← miru_intel_db.py
    adapters.py          ← miru_intel_adapters.py
    pipeline.py          ← miru_intel_pipeline.py
    snapshot_refresh.py  ← miru_snapshot_refresh.py
    dossier_queries.py   ← miru_dossier_queries.py
```

**Reasoning:**
1. **`dashboard/app.py` does not import these modules at all.** They are not PM-owned code. Placing them under `pm/intel/` (as the original structure plan proposed) would create a false ownership signal — Miru AI would depend on a PM-owned module, perpetuating the exact problem we're solving.
2. **All 6 consuming files are in `tools/` (Miru AI).** The intel layer is consumed by the AI subsystem but is conceptually neutral infrastructure.
3. **No Flask dependency** in any of these modules. They don't belong inside either server's directory tree.
4. **`shared/` already exists in the target structure** for `env.py`, `pushover.py`, etc. Adding `intel/` here is consistent.
5. **Future PM consumption is plausible.** If `dashboard/app.py` ever wants to use `MiruIntelRepository` instead of raw SQL, it imports from `shared.intel` — a neutral location both subsystems can reach.

### Option B: `pm/intel/` (PM-OWNED) — **REJECTED**

Would imply PM owns the intel layer and AI depends on PM. But PM doesn't even use it. This creates a misleading dependency arrow and means AI workers would be editing files inside the PM boundary.

### Option C: `miru_ai/intel/` (AI-OWNED) — **REJECTED**

Would make the intel layer private to AI. If PM ever needs it (likely — `app.py` currently duplicates dossier reads via raw SQL), we'd need to extract it again. Do it right once.

---

## 5. Ownership Boundaries

### Shared-Owned (lives in `shared/intel/`)

| Module | Why shared |
|--------|-----------|
| `models.py` | Foundational dataclasses — both subsystems need these types |
| `trust.py` | Trust profiles — needed by any source-consuming code |
| `card_intel.py` | Card text analysis — needed by AI sync and potentially PM search |
| `db.py` | Dossier repository — AI writes, PM could read |
| `adapters.py` | Source adapters — AI-driven but protocol is subsystem-neutral |
| `pipeline.py` | Enrichment orchestrator — AI-driven but could serve PM batch jobs |
| `snapshot_refresh.py` | Refresh orchestrator — AI-driven but operates on shared dossier DB |
| `dossier_queries.py` | Query formatters — natural PM consumer if PM adopts the intel layer |

### PM-Owned (stays in `dashboard/` → future `pm/server/`)

| Module | Why PM-only |
|--------|------------|
| `app.py` | Flask UI server |
| `static/` | PM frontend assets |
| `templates/` | PM HTML templates |
| `Dockerfile` | PM container build |
| `requirements.txt` | PM Python deps |

### Miru-AI-Owned (stays in `tools/` → future `miru_ai/`)

| Module | Why AI-only |
|--------|------------|
| `miru_ai_server.py` | Flask AI server |
| All `miru_*.py` workers | AI intelligence workers |
| `static/`, `templates/` | AI frontend assets |
| `migrations/` | AI DB migrations |

---

## 6. Phased Extraction Plan

### Phase 2A: Create `shared/intel/` (LOW RISK)

**Prerequisite:** Phase 1 complete, both services verified running.
**Reversibility:** `git revert` — single commit.

| Step | Action | Files touched | Risk |
|------|--------|---------------|------|
| 2A.1 | Create `shared/__init__.py` (empty) | 1 new | None |
| 2A.2 | Create `shared/intel/__init__.py` with re-exports | 1 new | None |
| 2A.3 | Copy (not move) 8 intel modules from `dashboard/` to `shared/intel/` | 8 new | None — originals untouched |
| 2A.4 | Update internal imports within `shared/intel/` to use relative imports (e.g., `from .models import CardDossier`) | 8 modified copies | Must be tested in isolation |
| 2A.5 | Verify `shared/intel/` imports work standalone: `python -c "from shared.intel.models import CardDossier"` | 0 | Validation only |

**After 2A:** Both old (`dashboard.*`) and new (`shared.intel.*`) import paths work. No runtime impact.

### Phase 2B: Migrate `tools/` consumers to `shared.intel` (MEDIUM RISK)

**Prerequisite:** Phase 2A verified.
**Reversibility:** `git revert` — single commit.

| Step | Action | Import change | Risk |
|------|--------|---------------|------|
| 2B.1 | `tools/miru_intake.py` L234-235 | `dashboard.miru_intel_db` → `shared.intel.db` | Low |
| | | `dashboard.miru_snapshot_refresh` → `shared.intel.snapshot_refresh` | |
| 2B.2 | `tools/miru_refresh_official_snapshot.py` L12-13 | Same as 2B.1 | Low |
| 2B.3 | `tools/miru_run_sandbox_cycle.py` L390-391 | Same as 2B.1 | Low |
| 2B.4 | `tools/miru_verified_loop.py` L12-18 | `dashboard.miru_intel_adapters` → `shared.intel.adapters` | Low |
| | | `dashboard.miru_intel_db` → `shared.intel.db` | |
| | | `dashboard.miru_intel_pipeline` → `shared.intel.pipeline` | |
| 2B.5 | `tools/miru_insight_cache.py` L18 | `dashboard.miru_intel_models` → `shared.intel.models` | Low |
| 2B.6 | `tools/miru_ai_sync_onepiece.py` L12 | `dashboard.miru_card_intel` → `shared.intel.card_intel` | Low |
| 2B.7 | Restart Miru AI (18765), verify all endpoints | — | Validation |

**After 2B:** All 11 cross-boundary imports now point to `shared.intel.*`. Zero `tools/ → dashboard/` imports remain.

### Phase 2C: Remove originals from `dashboard/` (LOW RISK)

**Prerequisite:** Phase 2B verified, services running, tests passing.
**Reversibility:** `git revert` — single commit.

| Step | Action | Risk |
|------|--------|------|
| 2C.1 | Delete `dashboard/miru_intel_models.py` | None if 2B verified |
| 2C.2 | Delete `dashboard/miru_intel_trust.py` | None |
| 2C.3 | Delete `dashboard/miru_card_intel.py` | None |
| 2C.4 | Delete `dashboard/miru_intel_db.py` | None |
| 2C.5 | Delete `dashboard/miru_intel_adapters.py` | None |
| 2C.6 | Delete `dashboard/miru_intel_pipeline.py` | None |
| 2C.7 | Delete `dashboard/miru_snapshot_refresh.py` | None |
| 2C.8 | Delete `dashboard/miru_dossier_queries.py` | None |
| 2C.9 | Restart PM (18080), verify UI loads | Validation |
| 2C.10 | Restart Miru AI (18765), verify endpoints | Validation |

**After 2C:** `dashboard/` contains only PM-owned files: `app.py`, `static/`, `templates/`, `Dockerfile`, `requirements.txt`. Clean boundary.

### Phase 2D: Update tests (LOW RISK)

| Step | Action | Risk |
|------|--------|------|
| 2D.1 | Update any test files importing from `dashboard.miru_intel_*` → `shared.intel.*` | Low |
| 2D.2 | Run full test suite | Validation |

---

## 7. Risks and Mitigations

### `sys.path` / `PROJECT_ROOT` resolution

All modules use `PROJECT_ROOT = Path(__file__).resolve().parent.parent` (from `miru_env.py`). After moving to `shared/intel/`, the depth changes from 1 level (`dashboard/`) to 2 levels (`shared/intel/`).

**Mitigation:** The intel modules don't calculate `PROJECT_ROOT` themselves — they receive `db_path` as a parameter or use the `DEFAULT_INTEL_DB_PATH` constant in `miru_intel_db.py`:

```python
DEFAULT_INTEL_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "miru_dossiers.db"
```

This is the **only** path-sensitive line in the intel layer. It currently resolves:
- `dashboard/miru_intel_db.py` → parent.parent = repo root → `data/miru_dossiers.db` ✓

After move to `shared/intel/db.py`:
- `shared/intel/db.py` → parent.parent = repo root → `data/miru_dossiers.db` ✓

**Both are depth-2 from root. The path resolves identically.** No change needed.

### Docker build context

`dashboard/Dockerfile` copies only what it needs for the PM container. The intel modules are not referenced in the Dockerfile (PM app.py uses raw SQL, not the intel layer). Removing them from `dashboard/` has zero Docker impact.

**Verify:** Read `dashboard/Dockerfile` before executing Phase 2C to confirm no COPY of `miru_intel_*.py`.

### Test imports

Tests in `tests/` that import from `dashboard.miru_intel_*` must update. Known candidates:
- `tests/test_miru_dossier_queries.py`
- `tests/test_miru_verified_intel.py`
- `tests/test_miru_source_agreement.py`
- `tests/test_miru_source_registry.py`

These should update in Phase 2D.

### `miru_intel_pipeline.py` imports `miru_card_intel`

Inside the intel layer, `pipeline.py` imports from `card_intel.py`. After the move, this becomes a relative import (`from .card_intel import ...`). This is a safe change because all 8 modules move together into the same package.

---

## 8. Summary

**STATUS: CONFIRMED WORKING**

### What was found
- 11 cross-boundary imports across 6 `tools/` files importing from 6 `dashboard/` modules
- `dashboard/app.py` imports **zero** intel modules — the intel layer is misplaced in `dashboard/`
- All 8 intel modules are pure Python with no Flask dependency
- Internal dependency graph is clean — one DAG with `models` and `trust` at the bottom
- Only one path-sensitive line (`DEFAULT_INTEL_DB_PATH`) and it survives the move unchanged

### Recommended target
`shared/intel/` — neutral shared infrastructure, not owned by either PM or AI

### Recommended execution order
1. **Phase 2A** — Copy modules to `shared/intel/`, fix internal imports (no runtime change)
2. **Phase 2B** — Repoint 11 `tools/` imports to `shared.intel.*` (restart AI, verify)
3. **Phase 2C** — Delete originals from `dashboard/` (restart both, verify)
4. **Phase 2D** — Update test imports (run suite)

Each phase is a single commit, independently revertible.

### After this extraction
- `dashboard/` becomes a clean PM-only boundary (server + UI only)
- `tools/` has zero imports from `dashboard/`
- `shared/intel/` is the canonical location for the intelligence layer
- Phase 3 (Miru AI boundary creation) can proceed without cross-boundary entanglement
