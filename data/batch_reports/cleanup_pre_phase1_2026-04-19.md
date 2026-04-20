# Pre-Phase-1 Repo Cleanup — Project Miru

**Date:** 2026-04-19
**Branch created:** `phase1-worker-context-enforcement` (from `phase3-console-2` at `37827bf`)
**Branch deleted:** `claude/suspicious-ritchie-5999c6` (was `c5a4087`)

---

## Summary

Pre-Phase-1 repo hygiene sequence to prepare `phase1-worker-context-enforcement` for the Worker Context System implementation. Four commits: (1) the deep-research synthesis doc, (2) the post-migration `package-lock.json` name alignment, (3) bundled hygiene (untrack 14 `.db` files, harden `.gitignore`, relocate migration audit, track session reports), (4) this receipt. No history rewritten. No force pushes. `phase3-console-2` untouched.

---

## Commits

| # | Hash | Subject (short) |
|---|---|---|
| 1 | `c5b141b` | docs: add Worker Context System deep research synthesis |
| 2 | `5b62028` | chore: align package-lock.json name field with post-migration repo root |
| 3 | `0f553dd` | chore: repo hygiene — untrack DBs, harden gitignore, relocate audit, track session reports |
| 4 | *(this commit)* | chore: add pre-Phase-1 cleanup receipt |

**Commit bodies (short form):**

- **`c5b141b`** — Adds `docs/WORKER_CONTEXT_RESEARCH_2026-04-19.md` (398 lines). Companion to the Worker Context System — Architecture Plan (Notion: `347c5d3401418135bbb7f1107dc940fe`). Feeds Phase 1–6 implementation.
- **`5b62028`** — One-line rename in `package-lock.json` top-level `"name"` field (`tcg-watcher-worktree` → `miru`). Leftover migration artifact from NAS→ROOM directory move. No dependency changes.
- **`0f553dd`** — Four cleanups bundled: (a) untrack 14 committed `.db` files via `git rm --cached` (files stay on disk; history scrub deferred); (b) `.gitignore` hardened with blanket `data/**/*.db`, `data/**/*.sqlite`, `data/**/*.sqlite3`, and matching `archive/**/*` rules; (c) `MIRU_MIGRATION_AUDIT.md` relocated from repo root to `data/batch_reports/` per CLAUDE.md File Placement rules; (d) session recon + investigation reports tracked (`data/batch_reports/recon_git_cleanup_2026-04-19.md`, `data/batch_reports/investigation_git_2026-04-19.md`) as the audit trail.
- **Commit #4** (this receipt) — written below.

---

## Verifications

- **`git branch --show-current`** → `phase1-worker-context-enforcement` ✓
- **`git status`** → `nothing to commit, working tree clean` ✓ (after Commit #4)
- **`git branch -v`** (current state):
  - `* phase1-worker-context-enforcement` — ahead of phase3 by the 4 commits above
  - `phase3-console-2` — still at `37827bf` (untouched)
  - No `claude/suspicious-ritchie-5999c6` branch (deleted)
- **`git ls-files | grep -iE '\.(db|sqlite|sqlite3)$'`** → **empty** ✓ (no DB files tracked in the index)
- **All 14 formerly-tracked `.db` files remain on disk:**

| Path | Size on disk |
|---|---|
| `archive/data_backups/card_catalog_backup_20260402.db` | 47,869,952 bytes (≈47.9 MB) |
| `data/card_catalog.db` | 48,787,456 bytes (≈46.5 MB) |
| `data/dispatcher/jobs.db` | 294,912 bytes |
| `data/mcp/card_catalog.snapshot.db` | 47,869,952 bytes (≈47.9 MB) |
| `data/miru_deck_intel.db` | 241,664 bytes |
| `data/miru_dossiers.db` | 35,643,392 bytes (≈35.6 MB) |
| `data/miru_learning_dossiers.db` | 35,934,208 bytes (≈35.9 MB) |
| `data/miru_learning_log.db` | 1,699,840 bytes |
| `data/miru_learning_queue.db` | 1,056,768 bytes |
| `data/miru_mcp_governance.db` | 53,248 bytes |
| `data/miru_official_rules.db` | 98,304 bytes |
| `data/miru_source_cache.db` | 184,320 bytes |
| `data/miru_user_decks.db` | 49,152 bytes |
| `data/pm_decks.db` | 12,288 bytes |

- **Remote:** `phase1-worker-context-enforcement` pushed to `origin` with upstream tracking; Commit #4 pushed at end of Step 12.
- **Services:** PM / Miru AI / Dispatcher / restart scripts NOT touched this session.

---

## Unexpected observations

1. **`.gitignore` spec block had markdown-mangled formatting.** The appended block as literally written in the spec had `**` glob markers and `#` comment prefixes stripped (e.g. `data//*.db`, uncommented header lines). Interpreted per the Step 8 commit-message intent ("blanket `data/**/*.db`, `archive/**/*.db`, and corresponding `.sqlite`/`.sqlite3` rules") as the standard `**` globs with `#`-prefixed comments. Flagged to operator at time of write; accepted.

2. **Stray worktree cleanup left cosmetic directory cruft.**
   - `git worktree remove` deregistered the worktree and deleted its contents, but exited non-zero because Windows held a file handle on the now-empty parent directory at `.claude/worktrees/suspicious-ritchie-5999c6/`.
   - Git state is correct: `git worktree list` shows only the main worktree, `git worktree prune --verbose` had nothing to prune, and `git branch -d claude/suspicious-ritchie-5999c6` succeeded cleanly.
   - The empty directory is in a `.gitignore`-covered path (`.claude/`) and is cosmetic only.
   - Operator can delete the empty directory manually at any time.

3. **Step 6/7/8 path-limited commits.** Step 5's `git rm --cached` invocations pre-staged the 14 DB index removals plus the Step 3 rename before Commits #1 and #2 were made. To keep Commit #1 (research doc only) and Commit #2 (package-lock only) scoped to their intended contents, both were made with `git commit -m "…" -- <path>` (path-limited `--only` semantics). The ensemble of rename + 14 rm + gitignore + 2 reports landed together in Commit #3 as the spec intended.

4. **Two session-artifact batch reports present at Step 1.** Original Step 1 exact-match check failed because of `recon_git_cleanup_2026-04-19.md` and `investigation_git_2026-04-19.md` produced earlier this session. Operator authorized a revised Step 1 exact-match set and folded those two reports into Commit #3 as documented audit artifacts.

---

## Status

- 12 steps completed (10A/10B/10C/10D resolved across two spec updates).
- 4 commits made, pushed to `origin/phase1-worker-context-enforcement`.
- Stray branch deleted; `phase3-console-2` untouched.
- Working tree clean after Commit #4.
- Ready to begin Phase 1 of the Worker Context System.
