# Git Investigation — Project Miru (Read-Only)

**Date:** 2026-04-19
**Branch:** phase3-console-2
**Mode:** Read-only investigation. No writes, no commits, no stage, no push, no merge, no branch create/delete.

---

## SUMMARY

- **`package-lock.json` change:** one-line rename of the `"name"` field from `"tcg-watcher-worktree"` to `"miru"` — a migration-rename artifact, not a dependency update and not noise.
- **Stray branch `claude/suspicious-ritchie-5999c6`:** **0 commits ahead / 11 commits behind** `phase3-console-2`; it is a strict ancestor — all its content is already folded into `phase3-console-2`. No work to bring in; the branch is stale.
- **`phase3-console-2` vs `origin/main`:** **65 files changed, 9,619 insertions, 64 deletions**, across these top-level groups:
  - **Root-level docs (7 files, ~1,001 lines)** — worker rule files (CLAUDE/GEMINI/CODEX/COPILOT/CURSOR/AGENT_REPO_LOCK) path-rewritten + new MIRU_MIGRATION_AUDIT.md (965 lines)
  - **`.gemini/` (1 file, 8 lines)** — settings path rewrite
  - **`config/` (1 file, 4 lines)** — MCP policy path rewrite
  - **`data/` (7 files; 1,996 text lines + 47.9MB of binary DB)** — three migration reports (log, preview, complete) + overlay pointer + **3 new binary `.db` files committed during migration**
  - **`dispatcher/` (3 files, 6 lines)** — small path rewrites
  - **`docs/` (23 files, 6,602 lines)** — 2 small doc edits + 10-file PM craft guide + 11-file UI/UX craft guide
  - **`miru_ai/` (2 files, 8 lines)** — path rewrites in `server.py` and dev cockpit partial
  - **`pm/` (1 file, 2 lines)** — `pm/CLAUDE.md` path rewrite
  - **`tools/` (3 files, 14 lines)** — path rewrites
  - **`windows/` (17 files, 40 lines)** — ps1 scripts + tasks path rewrites
- **Flags for operator attention:**
  1. **3 binary `.db` files added to git during migration** — `data/dispatcher/jobs.db` (294 KB), `data/mcp/card_catalog.snapshot.db` (47.9 MB), `data/pm_decks.db` (12 KB). These reinforce the HIGH PRIORITY FLAG from the 2026-04-19 recon (14 `.db` files tracked total).
  2. **`MIRU_MIGRATION_AUDIT.md` (965 lines) sits at repo root.** CLAUDE.md's file-placement rule puts documentation under `docs/`. Operator decides whether this is an intentional root placement or should be moved.
  3. **Three large migration reports in `data/batch_reports/`** (total 1,994 lines). Intentional migration artifacts; flagging only so operator can decide whether to keep, archive, or prune.
  4. **Stray branch `claude/suspicious-ritchie-5999c6` has no unique commits** — safe to leave alone or delete at operator discretion. Nothing to cherry-pick.

---

## Step 1 — `package-lock.json` diff

**Command:** `git diff package-lock.json`

**Verbatim output:**

```diff
diff --git a/package-lock.json b/package-lock.json
index 5cb64fb..fa46956 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,5 +1,5 @@
 {
-  "name": "tcg-watcher-worktree",
+  "name": "miru",
   "lockfileVersion": 3,
   "requires": true,
   "packages": {
```

**Classification:** **Migration artifact.** The only change is the top-level `"name"` field, renamed from `"tcg-watcher-worktree"` (a pre-migration worktree-oriented name) to `"miru"` (matches the post-migration repo root `D:\dev\miru`). `lockfileVersion`, `requires`, and the package tree are untouched — not a dependency bump, not noise, just a leftover rename from the directory move.

---

## Step 2 — Stray branch `claude/suspicious-ritchie-5999c6`

### 2A. Commits on stray branch not on `phase3-console-2`

**Command:** `git log phase3-console-2..claude/suspicious-ritchie-5999c6 --oneline`

**Output:** *(empty — no commits)*

### 2B. Commits on `phase3-console-2` not on stray branch

**Command:** `git log claude/suspicious-ritchie-5999c6..phase3-console-2 --oneline`

**Output:**

```
37827bf chore: worker context packet cleanup
8245e79 post-migration: Perplexity MCP reconnect on ROOM
0066a6b migration: phase 11 — full-stack verified, migration complete
352b224 migration: phase 10 — MCP + Dispatcher Files tab verification
3d4f43d migration: phase 9 — services running, Tailscale verified
1caeca6 migration: phase 8 — firewall + scheduled tasks
9f54b95 migration: phase 7 — tooling verification sweep
53856a8 migration: phase 6 — data placed
77ed7ba migration: phase 5 — assets placed
8d17769 migration: phase 4 — path rewrites applied
a180952 docs: craft guides for PM + UI/UX; migration audit
```

### 2C. File-level diff stat

**Command:** `git diff phase3-console-2..claude/suspicious-ritchie-5999c6 --stat`

**Output:** 65 files, 64 insertions, 9,619 deletions (totals **9,683 lines** — over the 30-line threshold, so the full diff is **not** included per spec).

The diff represents the inverse of the 11 missing commits: going from `phase3-console-2` back to the stray branch deletes the PM + UI/UX craft guide set, the three migration reports, `MIRU_MIGRATION_AUDIT.md`, the three new `.db` binaries, and reverts path-rewrite edits across worker rule files, `windows/` scripts, and service templates.

### 2D. Summary

- **Ahead (stray vs phase3):** 0 commits
- **Behind (stray vs phase3):** 11 commits
- **Files that differ:** 65 (all are on `phase3-console-2` but missing or older on stray)
- **One-sentence read:** The stray branch is a strict ancestor of `phase3-console-2` — it has no unique work; every file difference represents migration + craft-guide commits already on `phase3-console-2`. It is stale, not a source of pending changes.

---

## Step 3 — `phase3-console-2` vs `origin/main`

**Command:** `git diff origin/main..phase3-console-2 --stat`
(Note: `main` is not a local branch; used the tracking remote `origin/main` which local `origin/HEAD` points to.)

**Full output:**

```
 .gemini/settings.json                              |    8 +-
 AGENT_REPO_LOCK.md                                 |    4 +-
 CLAUDE.md                                          |    2 +-
 CODEX.md                                           |    7 +-
 COPILOT.md                                         |    7 +-
 CURSOR.md                                          |    7 +-
 GEMINI.md                                          |    9 +-
 MIRU_MIGRATION_AUDIT.md                            |  965 ++++++++++++
 config/miru_mcp_policy.json                        |    4 +-
 data/batch_reports/MIGRATION_COMPLETE.md           |  105 ++
 data/batch_reports/migration_log_2026-04-18.md     | 1528 ++++++++++++++++++++
 data/batch_reports/migration_preview_2026-04-18.md |  361 +++++
 data/dispatcher/jobs.db                            |  Bin 0 -> 294912 bytes
 data/mcp/card_catalog.snapshot.db                  |  Bin 0 -> 47869952 bytes
 data/overlays/asset_job_pointer.txt                |    2 +-
 data/pm_decks.db                                   |  Bin 0 -> 12288 bytes
 dispatcher/handlers/gemini.py                      |    2 +-
 dispatcher/task_dispatcher.py                      |    2 +-
 dispatcher/templates/dispatcher.html               |    2 +-
 docs/RUNTIME_AUTHORITY_MATRIX.md                   |    8 +-
 docs/STRUCTURE_CLEANUP_PLAN.md                     |    2 +-
 docs/pm/00_PRINCIPLES.md                           |  214 +++
 docs/pm/01_TAB_LANDINGS.md                         |  308 ++++
 docs/pm/02_PM_PRIMITIVES.md                        |  511 +++++++
 docs/pm/03_MIRU_LAYER.md                           |  345 +++++
 docs/pm/04_WATCHLIST_AND_METER.md                  |  369 +++++
 docs/pm/05_GESTURES_PM.md                          |  282 ++++
 docs/pm/06_DESIGN_LANGUAGE.md                      |  392 +++++
 docs/pm/07_OPTCG_STUDY.md                          |  246 ++++
 docs/pm/08_PM_ANTI_PATTERNS.md                     |  304 ++++
 docs/pm/README.md                                  |   74 +
 docs/ui_ux/00_PRINCIPLES.md                        |  128 ++
 docs/ui_ux/01_MOBILE_PWA.md                        |  431 ++++++
 docs/ui_ux/02_GESTURES.md                          |  308 ++++
 docs/ui_ux/03_SUB_PAGE_ARCHITECTURE.md             |  344 +++++
 docs/ui_ux/04_PRIMITIVES.md                        |  464 ++++++
 docs/ui_ux/05_ACCESSIBILITY.md                     |  365 +++++
 docs/ui_ux/06_PERFORMANCE.md                       |  364 +++++
 docs/ui_ux/07_COMPETITIVE_STUDY.md                 |  278 ++++
 docs/ui_ux/08_ANTI_PATTERNS.md                     |  498 +++++++
 docs/ui_ux/09_TOOLING.md                           |  312 ++++
 docs/ui_ux/README.md                               |   55 +
 miru_ai/server.py                                  |    4 +-
 miru_ai/templates/partials/dev_cockpit.html        |    4 +-
 pm/CLAUDE.md                                       |    2 +-
 tools/miru_image_variant_classifier.py             |    4 +-
 tools/templates/miru_ai.html                       |    6 +-
 tools/templates/partials/dev_cockpit.html          |    4 +-
 windows/RUNTIME_AUTHORITY.md                       |    4 +-
 windows/op_miru_common.ps1                         |    2 +-
 windows/op_miru_runtime.ps1                        |    2 +-
 windows/register_restart_tasks.ps1                 |    2 +-
 windows/restart_dispatcher.ps1                     |    2 +-
 windows/restart_miru_ai.ps1                        |    2 +-
 windows/restart_pm.ps1                             |    2 +-
 windows/run_miru_asset_job.ps1                     |    8 +-
 windows/start_all_services.ps1                     |    2 +-
 windows/start_dispatcher.ps1                       |    2 +-
 windows/start_miru_ai_dev.ps1                      |    2 +-
 windows/start_perplexity_mcp.ps1                   |    2 +-
 windows/start_project_miru_dashboard.ps1           |    2 +-
 windows/startup_all.ps1                            |    2 +-
 windows/tasks/restart_dispatcher_task.ps1          |    2 +-
 windows/tasks/restart_miru_ai_task.ps1             |    2 +-
 windows/tasks/restart_pm_task.ps1                  |    2 +-
 65 files changed, 9619 insertions(+), 64 deletions(-)
```

### Grouped by top-level directory

| Group | Files | Lines (ins+del) | Summary |
|---|---|---|---|
| **Root-level files** | 7 | 1,001 | Worker rule files (CLAUDE.md, GEMINI.md, CODEX.md, COPILOT.md, CURSOR.md, AGENT_REPO_LOCK.md) — small path-rewrite edits, plus new `MIRU_MIGRATION_AUDIT.md` (965 lines) |
| **`.gemini/`** | 1 | 8 | `settings.json` path rewrite |
| **`config/`** | 1 | 4 | `miru_mcp_policy.json` path rewrite |
| **`data/`** | 7 | 1,996 text + 48.2 MB binary | 3 migration reports (MIGRATION_COMPLETE, migration_log, migration_preview) + overlay pointer + **3 new binary `.db` files** (`dispatcher/jobs.db`, `mcp/card_catalog.snapshot.db` [47.9 MB], `pm_decks.db`) |
| **`dispatcher/`** | 3 | 6 | Path-rewrite edits in `handlers/gemini.py`, `task_dispatcher.py`, `templates/dispatcher.html` |
| **`docs/`** | 23 | 6,602 | 2 small edits (RUNTIME_AUTHORITY_MATRIX, STRUCTURE_CLEANUP_PLAN) + the new PM craft-guide library (10 files, 3,045 lines) + the new UI/UX craft-guide library (11 files, 3,547 lines) |
| **`miru_ai/`** | 2 | 8 | Path rewrites in `server.py` and `templates/partials/dev_cockpit.html` |
| **`pm/`** | 1 | 2 | `pm/CLAUDE.md` path rewrite |
| **`tools/`** | 3 | 14 | Path rewrites in `miru_image_variant_classifier.py` and two template files |
| **`windows/`** | 17 | 40 | PowerShell restart/start/startup scripts + scheduled-task scripts — all small path rewrites; plus `RUNTIME_AUTHORITY.md` |
| **TOTAL** | **65** | **9,683** (incl. binary) | |

### Flags — unexpected or worth operator attention

1. **Three binary `.db` files added to the history on `phase3-console-2`.** `data/dispatcher/jobs.db` (294 KB), `data/mcp/card_catalog.snapshot.db` (47.9 MB), `data/pm_decks.db` (12 KB). These should be expected from neither "migration + craft guides + MCP fix + worker context cleanup" nor `.gitignore` (which whitelists specific db patterns but has no blanket `data/**/*.db` rule). Consistent with the 14-tracked-.db HIGH PRIORITY FLAG from the 2026-04-19 recon.
2. **`MIRU_MIGRATION_AUDIT.md` (965 lines) lives at repo root**, whereas CLAUDE.md's File Placement rules say "Documentation → `docs/`" and "Never create temp, scratch, or debug files at repo root." Operator to decide whether this is intentional root placement for a one-time migration artifact.
3. **Three migration-report docs totalling 1,994 text lines** (`MIGRATION_COMPLETE.md` 105, `migration_log_2026-04-18.md` 1,528, `migration_preview_2026-04-18.md` 361). They are legitimate migration artifacts per placement rules (`data/batch_reports/`), flagged only because they are large and one-time.
4. **Everything else matches the expected scope** — worker rule files, PowerShell scripts, service templates and config all show 2–9 line changes consistent with a repo-wide path rewrite, and the two craft-guide directories match "craft guides for PM + UI/UX" from commit `a180952`.

---

## Step 4 — Report Location

This report: `data/batch_reports/investigation_git_2026-04-19.md`.

SUMMARY echoed to stdout at end of investigation.

---

## Investigation Contract

- No files modified.
- No git state changed (no add, commit, push, merge, reset, checkout, stash, branch create/delete).
- No `.gitignore` edits.
- No fix proposals — operator reviews this report and decides next steps.
