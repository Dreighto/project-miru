# Reconciliation Notes (2026-03-17)

Branch: reconcile/miru-main-worktree
Scope: preservation-first additive reconciliation in WORKTREE only.

## Phase B (MAIN authoritative imports)
Imported from MAIN into WORKTREE:
- tools/miru_dossier_store.py
- tools/miru_maintenance.py
- tools/miru_insight_cache.py
- tools/miru_brain.py
- tools/miru_visual_intelligence.py
- tools/miru_image_cleanup_worker.py
- tools/miru_card_discovery_worker.py
- tools/miru_insight_voice.py
- tools/miru_budget_guardrails.py
- tools/miru_preflight_safety.py
- tools/miru_contextual_insight.py
- tools/miru_source_adapters.py

Learning engine strategy:
- tools/miru_learning_engine.py switched to MAIN base.
- Worktree pre-reconcile engine preserved at tools/miru_learning_engine_worktree_overlay.py.
- Compatibility hooks kept in main-base engine:
  - append_review_item
  - maybe_send_learning_notification

## Phase C (WORKTREE authoritative retained)
Left as WORKTREE authority (not replaced in this pass):
- tools/miru_ai_server.py
- tools/miru_project_sync.py
- tools/miru_regulation.py
- tools/miru_ethics_gates.py
- tools/miru_worktree_overlap.py
- tools/run_worktree_worker.py
- tools/run_governed_batch_test.py
- tools/run_governed_autopilot.py
- tools/miru_print_variant.py
- tools/miru_fetch_banlist.py
- tools/miru_learner_config.py
- tools/miru_learner_loop.py (if present in this tree)
- tools/templates/miru_ai.html
- tools/static/miru_ai.js
- tools/static/miru_ai.css
- tools/static/miru_voyage/
- dashboard/miru_dossier_queries.py
- dashboard/miru_snapshot_refresh.py
- dashboard/templates/leader.html
- dashboard/templates/leaders_index.html

## Phase D (shared-file reconciliation posture)
Manual reconciliation started with evidence preservation, not blind overwrite:
- tools/miru_learning_engine.py: main base + worktree compatibility hook methods.
- tools/miru_project_sync.py: kept WORKTREE as active authority.
- tools/miru_ai_server.py: kept WORKTREE as active authority.
- dashboard/app.py: kept WORKTREE as active authority.

MAIN baselines stored for explicit side-by-side reconciliation:
- reconcile/main_baseline/tools/miru_project_sync.py
- reconcile/main_baseline/tools/miru_ai_server.py
- reconcile/main_baseline/dashboard/app.py

## Known unresolved risk items
- dashboard/app.py has many MAIN-only helpers/routes not yet merged into active WORKTREE app.py.
- tools/miru_ai_server.py has many MAIN-only dossier context helpers not yet merged.
- tools/miru_project_sync.py appears to be WORKTREE-superset by function definitions, but behavior-level parity still needs tests.
- tools/miru_learning_engine.py hook compatibility is preserved by additive bridge methods; full behavior-level parity with prior WORKTREE review/autopilot flow still requires targeted integration testing.

## Safety constraints honored in this pass
- No runtime port changes.
- No active authority switch away from WORKTREE.
- No destructive deletion of data/artifacts.
- Additive preservation of both sides where uncertain.
