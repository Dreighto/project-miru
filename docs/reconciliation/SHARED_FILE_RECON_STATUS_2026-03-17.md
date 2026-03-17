# Shared File Reconciliation Status (2026-03-17)

## tools/miru_learning_engine.py
Status: Partially reconciled in this pass.
- Active file switched to MAIN base for intelligence/image pipeline depth.
- Worktree prior version preserved at tools/miru_learning_engine_worktree_overlay.py.
- Added compatibility hooks to keep worktree operator/governance integration points callable:
  - append_review_item
  - maybe_send_learning_notification

## tools/miru_project_sync.py
Status: Kept WORKTREE as active authority.
- Function-name comparison showed no MAIN-only defs missing in WORKTREE.
- WORKTREE-specific insight builders and force-rebuild/by-type behavior retained.
- MAIN baseline preserved at reconcile/main_baseline/tools/miru_project_sync.py for behavior-level review.

## tools/miru_ai_server.py
Status: Kept WORKTREE as active authority.
- WORKTREE operator/dev surface retained as requested.
- MAIN baseline preserved at reconcile/main_baseline/tools/miru_ai_server.py for selective import of dossier helpers.

## dashboard/app.py
Status: Kept WORKTREE as active authority; manual merge deferred due complexity.
- WORKTREE app remains active starting base.
- MAIN baseline preserved at reconcile/main_baseline/dashboard/app.py.
- Route/helper-level integration requires targeted route-by-route test plan before merging.

## Reconciliation principle used
- Preservation-first additive: no blind overwrite of WORKTREE-authoritative governance/operator surfaces.
- MAIN intelligence depth imported where audit marked MAIN authoritative.
- Shared files documented with explicit baselines for safe next-step integration.
