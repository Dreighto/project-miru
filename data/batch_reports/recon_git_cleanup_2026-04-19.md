# Git Recon — Project Miru (Read-Only)

**Date:** 2026-04-19
**Branch:** phase3-console-2
**Mode:** Read-only reconnaissance. No writes, no commits, no stage, no push, no merge.

---

## SUMMARY

- **Branch:** phase3-console-2
- **Commits unpushed:** 2 (vs origin/phase3-console-2)
- **Total lines in diff (tracked changes):** 2 (1 insertion + 1 deletion, unstaged only)
- **Untracked new content:** 1 file, 398 lines (docs research doc)
- **Files by bucket:**
  - SOURCE: 0
  - BUILD_ARTIFACT: 0
  - LOG_OR_SCRATCH: 0
  - CONFIG_INFRA: 1 (package-lock.json)
  - DATA: 0
  - DOCS_MD: 1 (docs/WORKER_CONTEXT_RESEARCH_2026-04-19.md — untracked)
  - UNCERTAIN: 0
- **HIGH PRIORITY FLAGS:** **14 `.db` files are currently tracked in git, including `data/card_catalog.db` (the live database per CLAUDE.md).** See Step 7.
- **UNCERTAIN files needing operator review:** 0

---

## Step 1 — Environment

- **Current branch:** `phase3-console-2` (matches expected)
- **Local branches:**
  - `* phase3-console-2  37827bf [ahead 2] chore: worker context packet cleanup`
  - `+ claude/suspicious-ritchie-5999c6  c5a4087 docs: add load-on-demand craft guide triggers to worker rule files` (non-current; likely from a prior Claude IDE session — worth the operator's attention)
- **Remote:**
  - `origin  https://github.com/Dreighto/project-miru.git  (fetch)`
  - `origin  https://github.com/Dreighto/project-miru.git  (push)`
- **Upstream tracking:** `origin/phase3-console-2`
- **Ahead / behind:** ahead 2, behind 0

---

## Step 2 — Status Snapshot

```
On branch phase3-console-2
Your branch is ahead of 'origin/phase3-console-2' by 2 commits.

Changes not staged for commit:
	modified:   package-lock.json

Untracked files:
	docs/WORKER_CONTEXT_RESEARCH_2026-04-19.md

no changes added to commit
```

**Short form:**
```
 M package-lock.json
?? docs/WORKER_CONTEXT_RESEARCH_2026-04-19.md
```

**Counts:**
- Staged: 0
- Unstaged (modified): 1 (`package-lock.json`)
- Untracked: 1 (`docs/WORKER_CONTEXT_RESEARCH_2026-04-19.md`)

---

## Step 3 — Unpushed Commits

Branch `phase3-console-2` exists on origin at `0066a6b` (phase 11 migration commit).

**Unpushed on current branch (2):**
```
37827bf chore: worker context packet cleanup
8245e79 post-migration: Perplexity MCP reconnect on ROOM
```

**vs. origin/main (11 commits ahead; larger divergence from main):**
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

---

## Step 4 — Diff Summary

**Unstaged (`git diff --stat`):**
```
 package-lock.json | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

**Staged (`git diff --stat --cached`):** empty — nothing staged.

**Total tracked-change lines:** 2 (1 + 1).

**Untracked (not in diff totals):** `docs/WORKER_CONTEXT_RESEARCH_2026-04-19.md` — 398 lines (new file, added this session).

---

## Step 5 — Bucket Classification

Only two changed files exist in the working tree. Classification:

### 1. SOURCE — real work in pm/, miru_ai/, dispatcher/, windows/, tools/, shared/
- **Count:** 0
- **Lines changed:** 0
- **Recommendation:** n/a — no source code changes.

### 2. BUILD_ARTIFACT — __pycache__/, *.pyc, node_modules/, .venv/, dist/, build/, compiled assets
- **Count:** 0
- **Lines changed:** 0
- **Recommendation:** n/a — none in working tree changes.

### 3. LOG_OR_SCRATCH — logs/, data/batch_reports/, .claude/worktrees/, *.tmp, *.bak
- **Count:** 0
- **Lines changed:** 0
- **Recommendation:** n/a.

### 4. CONFIG_INFRA — .mcp.json, package.json, package-lock.json, .gitignore, .cursor/, XML scheduled task exports, PowerShell scripts
- **Count:** 1
- **Lines changed:** 2 (1 insertion + 1 deletion)
- **Representative paths:**
  - `package-lock.json` (modified, unstaged)
- **Recommendation:** **ASK_OPERATOR** — a 2-line change to a lockfile is usually either a dependency version bump or an incidental artifact; the nature isn't clear without viewing the diff.

### 5. DATA — anything inside data/ (flag .db/.sqlite/.sqlite3 as HIGH PRIORITY FLAG)
- **Count:** 0 in working-tree changes
- **Lines changed:** 0
- **Recommendation:** n/a for working-tree changes. **BUT** see Step 7 — 14 DB files are ALREADY TRACKED in git and should be reviewed separately.

### 6. DOCS_MD — *.md files anywhere
- **Count:** 1
- **Lines changed:** 398 (new file, untracked)
- **Representative paths:**
  - `docs/WORKER_CONTEXT_RESEARCH_2026-04-19.md` (new, untracked — authored this session as deep research synthesis)
- **Recommendation:** **COMMIT** — intentional research deliverable placed per file-placement rules (`docs/`).

### 7. UNCERTAIN — anything that doesn't cleanly fit
- **Count:** 0
- **Recommendation:** n/a.

---

## Step 6 — `.gitignore` Audit

**Current contents (verbatim):**

```gitignore
.env
.env.*
!.env.example

# Local secrets, editor config, and virtualenvs
.venv/
venv/
env/
secrets/
.claude/
.mcp.json
mcp.json
.cursor/mcp.json
.cursor/
.vscode/

miru_ai/dev_review_hub_ui/node_modules/

# Claude in Chrome / CDP temp files
.edge-cdp-temp/

# Local caches, temp build outputs, and runtime logs
.npm-cache/
.tmp-ui-build/
.tmp-pip-cache/
.playwright-browsers/
logs/
.cdp-edge-log.txt
data/startup-logs/
windows/dispatcher_startup.log

# Python temp and bytecode artifacts
.pip-tmp/
.tmp-pip/
.tmp-pip-temp/
miru_ai/dev_review_hub_ui/.tmp-build-test/
__pycache__/
*.pyc

# Local runtime databases and SQLite sidecars
data/miru_dev_training_reviews.db
*.db-shm
*.db-wal

# Dispatcher runtime databases (job history — regenerated at startup)
dispatcher/data/jobs.db
windows/dispatcher_jobs.db

# Playwright / debug screenshots (must be saved outside the repo, e.g. C:\temp\playwright-shots\)
/*.png
voice-*.png
/windows/restart_dispatcher_tmp.ps1

# Root-level node_modules (package.json lives at root for MCP server)
node_modules/

# Gemini CLI session data (settings.json is tracked; tmp/ is not)
.gemini/tmp/

# Generated MCP snapshot DB (runtime file, not source)
miru-mcp/sqlite-ro/*.db

# Backup and dev databases (large binaries, regenerable)
data/card_catalog_backup_*.db
data/card_data.db

# Temp test artifacts
tests/_tmp/

# Ad-hoc output/report files in tools/ (date-stamped CSVs, txt output dumps)
tools/*.csv
tools/*.txt
tools/diagnostics/
archive/diagnostics/

# Junk / temp artifacts
.playwright-mcp/
.playwright-browsers/
.codex_playwright_tmp/
.codex_checkpoints/
.tmp-*/
.pip-*/
.npm-*/
.wheelhouse/
.docker-config/
debug.log
*.log
__pycache__/
*.pyc
*.pyo
.venv/
.pip-cache/
.npm-tmp/
```

**Gaps observed (items in buckets 2/3/5 NOT fully covered):**

- **Bucket 5 (DATA) — significant gap.** `.gitignore` whitelists *specific* db files and patterns but there is no blanket `data/*.db` or `data/**/*.db` rule. Uncovered `.db` files already tracked in git (see Step 7):
  - `data/card_catalog.db` — the live database (CLAUDE.md says never write to it; it should not be in git at all)
  - `data/mcp/card_catalog.snapshot.db`
  - `data/miru_deck_intel.db`
  - `data/miru_dossiers.db`
  - `data/miru_learning_dossiers.db`
  - `data/miru_learning_log.db`
  - `data/miru_learning_queue.db`
  - `data/miru_mcp_governance.db`
  - `data/miru_official_rules.db`
  - `data/miru_source_cache.db`
  - `data/miru_user_decks.db`
  - `data/pm_decks.db`
  - `data/dispatcher/jobs.db` (the ignore rule is `dispatcher/data/jobs.db` — different path; this alternate path is not covered)
  - `archive/data_backups/card_catalog_backup_20260402.db` (the ignore rule `data/card_catalog_backup_*.db` doesn't match `archive/data_backups/...`)

- **Bucket 2 (BUILD_ARTIFACT):** covered adequately — `__pycache__/`, `*.pyc`, `*.pyo`, `node_modules/`, `.venv/`, various tmp paths.
- **Bucket 3 (LOG_OR_SCRATCH):** covered — `logs/`, `*.log`, `tests/_tmp/`, `.tmp-*/`, etc. Note: `data/batch_reports/` itself is NOT gitignored; some `.json`/`.txt`/`.md` reports from that directory are tracked in git (visible in earlier commit history). Whether this is intentional is an operator decision.

---

## Step 7 — Tracked Files That Look Suspicious

Command run (bash-equivalent of requested `findstr`):
```
git -C "D:\dev\miru" ls-files | grep -iE '\.(db|sqlite|sqlite3|log|pyc)$'
```

**Matches (14, all `.db`):**

```
archive/data_backups/card_catalog_backup_20260402.db
data/card_catalog.db
data/dispatcher/jobs.db
data/mcp/card_catalog.snapshot.db
data/miru_deck_intel.db
data/miru_dossiers.db
data/miru_learning_dossiers.db
data/miru_learning_log.db
data/miru_learning_queue.db
data/miru_mcp_governance.db
data/miru_official_rules.db
data/miru_source_cache.db
data/miru_user_decks.db
data/pm_decks.db
```

**HIGH PRIORITY FLAG (14 files):** These are SQLite databases committed to git. Per CLAUDE.md: `card_catalog.db is the live database — never write to it directly from a worker session`. The live DB being in git is structurally inconsistent with that governance rule. The operator should review whether:

1. These files should be removed from git history (separate, destructive cleanup — operator-only decision).
2. The `.gitignore` should be extended to prevent future re-adds (also operator decision; per recon contract, no changes made here).
3. The `data/dispatcher/jobs.db` file duplicates intent with `dispatcher/data/jobs.db` (which IS ignored) — two different paths, only one covered.

No `.log` or `.pyc` files were found tracked. Those exclusions appear to be working.

---

## Step 8 — Report Location

This report: `data/batch_reports/recon_git_cleanup_2026-04-19.md`.

SUMMARY echoed to stdout at end of recon run.

---

## Recon Contract

- No files modified.
- No git state changed (no add, commit, push, merge, reset, checkout, stash).
- No `.gitignore` edits.
- No proposals for cleanup — operator reviews this report and decides next steps.
