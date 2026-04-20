# Migration Phase 4 — DRY RUN Preview

**Date:** 2026-04-18
**Host:** ROOM
**Repo:** `D:\dev\miru` (branch `phase3-console-2`, HEAD `a1809526`, tag `migration-phase-2`)
**Status:** **PREVIEW ONLY — no files modified, no directories created, no env vars changed.**
**Authoritative source:** Notion page "🚚 🚚 15 Migration — NAS to ROOM" § "Critical (must fix before any service starts)", items 1–9.

This document is the Phase 4a deliverable Captain asked for: *"Generate the preview of every change you would make across all affected files. Do NOT execute any rewrites yet."*

---

## §0 Scope rules

### §0.1 Authoritative vs. advisory
- **Authoritative (must execute):** Notion Critical items 1–9. These are the minimum required for services to start on ROOM.
- **Advisory (Captain decides before execution):** any additional file matches found by the broader pattern sweep. These don't appear in Notion's Critical list but also contain NAS-era strings. Captain reviews §2 of this doc to include, skip, or defer each.

### §0.2 Pattern families
Four root patterns drive all rewrites:

| Pattern | Replace with | Reason |
|---|---|---|
| `tcg-watcher-worktree` | `miru` | Folder rename (NAS→ROOM) |
| `100.104.150.125` | context-dependent (`127.0.0.1` for dispatcher; `100.81.19.49` for Tailscale-visible hosts) | IP migration |
| `nas.taila28611.ts.net` | `room.taila28611.ts.net` | MagicDNS migration |
| `F:\OPTCG_Images` / `F:/OPTCG_Images` | `D:\OPTCG_Images` / `D:/OPTCG_Images` | Drive letter (F → D) |

Notion confirms **D: stays D:** for the repo (`D:\dev\…`) and for `D:\Miru_Assets`. **No C:→C: or D:→D: drive letter changes needed.** The only real drive-letter change is F:→D: for OPTCG_Images.

### §0.3 Explicit do-not-touch list
These files contain matching patterns but describe *historical state* or are runtime logs. Rewriting them would falsify the audit trail.

- `D:\dev\miru\data\batch_reports\migration_log_2026-04-18.md` — this migration's own log (circular edit risk)
- `D:\dev\miru\data\batch_reports\migration_preview_2026-04-18.md` — this preview itself
- `D:\dev\miru\MIRU_MIGRATION_AUDIT.md` — audit document describing NAS state at time of capture; it's a historical snapshot, leaving it intact preserves the "before" reference
- `D:\dev\miru\archive\**` — archived legacy code (17 files matched), all superseded and not executed by any active service
- `D:\dev\miru\data\overlays\**` — data-pipeline output files (CSVs, SQL dumps, backups), not code
- `D:\dev\miru\data\batch_reports\governed_batch_report_*.{json,txt}` and `governed_autopilot_*` — old runtime batch outputs

---

## §1 Notion Critical Items — exact planned rewrites

### Item 1 — `.env` line 14: `DISPATCHER_BASE_URL`

| | |
|---|---|
| File | `D:\dev\miru\.env` |
| Line | 14 |
| Current | `DISPATCHER_BASE_URL=http://100.104.150.125:19000` |
| Planned | `DISPATCHER_BASE_URL=http://127.0.0.1:19000` |
| Rationale (from Notion) | "localhost is safer — Miru AI calls Dispatcher from same machine anyway" |

No other `.env` lines match any of the four patterns. The credential-bearing lines were neither read nor inspected for pattern matches — this confirmation comes from a targeted grep for the four root patterns only.

**Post-execution follow-up (Phase 4 secondary step, not in preview scope):** the user-scope env var `DISPATCHER_BASE_URL` was loaded in Phase 1 with the NAS value `http://100.104.150.125:19000`. It needs to be re-set to `http://127.0.0.1:19000` via `[Environment]::SetEnvironmentVariable('DISPATCHER_BASE_URL', 'http://127.0.0.1:19000', 'User')` *after* the `.env` rewrite so the two stay in sync.

### Item 2 — `.env` APPEND 3 new lines

Confirmed by grep: none of these variable names currently exist in `D:\dev\miru\.env`. Proposed insertion (exact text, at end of file with preceding newline if missing):

```
PROJECT_MIRU_CLEAN_THUMB_ROOT=D:\Miru_Assets
MIRU_RUNTIME_IMAGES_ROOT=D:\Miru_Assets
MIRU_OPTCG_IMAGES_ROOT=D:\OPTCG_Images
```

Corresponding user-scope env vars should ALSO be set on ROOM via `SetEnvironmentVariable(..., 'User')` so new shells inherit them (same pattern Phase 1 used for the initial 23 vars).

### Item 3 — `config/miru_mcp_policy.json` lines 4 and 7

| Line | Current | Planned |
|---|---|---|
| 4 | `    "canonical_repo": "D:\\dev\\tcg-watcher-worktree",` | `    "canonical_repo": "D:\\dev\\miru",` |
| 7 | `    "canonical_phone_verification_host": "100.104.150.125",` | `    "canonical_phone_verification_host": "100.81.19.49",` |

Note the JSON-escaped backslash: `\\` in source → `\\` in output (still JSON-escaped). Verified by reading first 20 lines of the file.

No other matches in this file.

### Item 4 — `.mcp.json` (10 occurrences of folder name)

Grep confirms exactly 10 matching lines. Breakdown:

| Line | MCP server | Format | Current substring → New substring |
|---|---|---|---|
| 46 | sqlite-ro-snapshot (`--db` arg) | `\\` | `D:\\dev\\tcg-watcher-worktree\\miru-mcp\\sqlite-ro\\card_catalog.snapshot.db` → `D:\\dev\\miru\\miru-mcp\\sqlite-ro\\card_catalog.snapshot.db` |
| 58 | filesystem (Docker bind mount) | `/` | `type=bind,src=D:/dev/tcg-watcher-worktree,dst=/projects/miru` → `type=bind,src=D:/dev/miru,dst=/projects/miru` |
| 71 | playwright `npm_config_cache` | `\\` | `D:\\dev\\tcg-watcher-worktree\\.npm-cache` → `D:\\dev\\miru\\.npm-cache` |
| 83 | git `GIT_BASE_DIR` | `\\` | `D:\\dev\\tcg-watcher-worktree` → `D:\\dev\\miru` |
| 84 | git `npm_config_cache` | `\\` | `D:\\dev\\tcg-watcher-worktree\\.npm-cache` → `D:\\dev\\miru\\.npm-cache` |
| 96 | notion PowerShell `.env` read | `\\` | `$envPath='D:\\dev\\tcg-watcher-worktree\\.env'` → `$envPath='D:\\dev\\miru\\.env'` |
| 109 | youtube PowerShell `.env` read | `\\` | `$envPath='D:\\dev\\tcg-watcher-worktree\\.env'` → `$envPath='D:\\dev\\miru\\.env'` |
| 122 | magic-ui PowerShell `.env` read | `\\` | `$envPath='D:\\dev\\tcg-watcher-worktree\\.env'` → `$envPath='D:\\dev\\miru\\.env'` |
| 125 | magic-ui `npm_config_cache` | `\\` | `D:\\dev\\tcg-watcher-worktree\\.npm-cache` → `D:\\dev\\miru\\.npm-cache` |
| 137 | shadcn `npm_config_cache` | `\\` | `D:\\dev\\tcg-watcher-worktree\\.npm-cache` → `D:\\dev\\miru\\.npm-cache` |

Ratio confirmed: 9 backslash-format + 1 forward-slash format = 10 total. Matches Notion's count exactly.

**Implementation note:** a single two-pass substitution covers all 10:
1. `D:\\dev\\tcg-watcher-worktree` → `D:\\dev\\miru` (literal string, on raw JSON source — matches 9 lines)
2. `D:/dev/tcg-watcher-worktree` → `D:/dev/miru` (literal string, on raw JSON source — matches 1 line)

No other matches in `.mcp.json`. No JSON parse step required (substitutions happen on raw text, preserving formatting).

### Item 5 — `miru_ai/server.py` (F: → D: for OPTCG_Images, 2 lines)

Verified with targeted reads:

| Line | Current | Planned |
|---|---|---|
| 8118 | `OPTCG_IMAGES_ROOT = Path("F:/OPTCG_Images")` | `OPTCG_IMAGES_ROOT = Path("D:/OPTCG_Images")` |
| 9575 | `        return send_from_directory("F:/OPTCG_Images", filename)` | `        return send_from_directory("D:/OPTCG_Images", filename)` |

Grep across `miru_ai/server.py` returned *only* these two `F:/OPTCG_Images` matches — no additional hidden occurrences.

Adjacent lines (179–184, cited by Notion) verified as `Path(r"D:\Miru_Assets")` — **NO CHANGE NEEDED** (D: stays D:). This was confirmed by reading lines 176–187.

### Item 6 — `miru_ai/evidence_collectors.py` line 28 (NO CHANGE)

Read at offset 25 for 8 lines:

```
28: _MIRU_ASSETS_ROOT: Path = Path(r"D:\Miru_Assets")
```

Confirms Notion's note: D: stays D: — **no change required**. Listed here only so the preview is complete on every Notion item.

### Item 7 — `miru_image_variant_classifier.py` (location discrepancy + defense-in-depth)

**Location discrepancy with Notion:** Notion cites this file at `miru_ai/miru_image_variant_classifier.py`, but the file does not exist there. `Glob` confirms the actual location is:

```
D:\dev\miru\tools\miru_image_variant_classifier.py
```

Probably a pre-existing `miru_ai` → `tools` relocation that happened before the audit was written into Notion. Flagging for Captain to sanity-check this isn't a missing-file issue.

Lines at the cited positions in the actual file:

| Line | Current | Role | Planned (defense-in-depth) |
|---|---|---|---|
| 3 | `Reads images only from the operator image root (default F:/OPTCG_Images).` (docstring) | comment | `Reads images only from the operator image root (default D:/OPTCG_Images).` |
| 37 | `DEFAULT_IMAGE_ROOT = Path("F:/OPTCG_Images")` | code default | `DEFAULT_IMAGE_ROOT = Path("D:/OPTCG_Images")` |
| 69 | `    raw = str(os.environ.get("MIRU_OPTCG_IMAGES_ROOT") or "").strip()` | env-var read | **NO CHANGE** (already env-var driven) |

Because Item 2 adds `MIRU_OPTCG_IMAGES_ROOT=D:\OPTCG_Images` to `.env`, line 69's env lookup wins and line 37's default is dead code at runtime. Defense-in-depth change is optional but recommended — if the env var is ever unset, the default still resolves to a valid path. Notion explicitly calls this out as operator choice.

### Item 8 — `.claude/settings.local.json` (MISSING) + `C:\temp\playwright-shots\` (missing)

| Expected target | State on ROOM |
|---|---|
| `D:\dev\miru\.claude\settings.local.json` | **file does not exist** (entire `.claude/` directory absent — gitignored per `.gitignore` line 10, so was not included in `repo_snapshot\`) |
| `C:\temp\playwright-shots\` | **directory does not exist** |

**Recommendation for Phase 4 execution:**
- Do **not** synthesize a `.claude/settings.local.json` from scratch — that file holds Claude Code per-project permission/settings state, and fabricating it without knowing the original NAS content risks hiding or duplicating settings. Captain should decide whether to (a) skip entirely, (b) recover from a backup if one exists elsewhere in `D:\miru-migration\`, or (c) defer until Claude Code auto-generates one from first-use settings.
- **Create** `C:\temp\playwright-shots\` as an empty directory (low-risk, explicit Notion instruction, matches `.gitignore` comment about "must be saved outside the repo").

I searched `D:\miru-migration\` for `settings.local.json` candidates — will surface those if Captain wants before execution (do not recover blindly).

### Item 9 — Folder rename at copy time (ALREADY DONE)

Phase 2 completed this. Source folder `D:\miru-migration\repo_snapshot\tcg-watcher-worktree\` was copied into `D:\dev\miru\` (the rename is baked into the destination path). No Phase 4 action.

---

## §2 Additional findings — outside Notion's Critical 9, Captain decides per-cluster

Broad pattern sweep found **51 files** containing `tcg-watcher-worktree`, **7 files** containing `100.104.150.125`, **6 files** containing `F:\OPTCG_Images` (backslash form), and **8 files** containing `F:/OPTCG_Images` (forward-slash form). After subtracting Notion items (§1) and the do-not-touch list (§0.3), the following files contain NAS-era strings and need a Captain decision.

### §2.1 Worker config markdown at repo root

Five worker-onboarding `.md` files at `D:\dev\miru\`, each with the same `- Canonical repo root: D:\dev\tcg-watcher-worktree` on line 14:

| File | Line | Current |
|---|---|---|
| `CLAUDE.md` | 14 | `- Canonical repo root: D:\dev\tcg-watcher-worktree` |
| `CODEX.md` | 14 | `- Canonical repo root: D:\dev\tcg-watcher-worktree` |
| `COPILOT.md` | 14 | `- Canonical repo root: D:\dev\tcg-watcher-worktree` |
| `CURSOR.md` | 14 | `- Canonical repo root: D:\dev\tcg-watcher-worktree` |
| `GEMINI.md` | 14 | `- Canonical repo root: D:\dev\tcg-watcher-worktree` |

Also `pm\CLAUDE.md` line 17: `- Keep PM data/config access compatible with worktree root D:\dev\tcg-watcher-worktree.`

**Recommendation:** rewrite to `D:\dev\miru`. These are worker-facing docs and stale references will mislead Claude Code / Codex / Cursor in future sessions. **Notion Phase 1's "Always do" list actually requires this**: "Update worker config files to reflect ROOM + new folder name". This was planned for Phase 1 but (per this session's Phase 1 log) only the MCP install + env-var load work got covered — the worker config doc updates appear to have been deferred. Phase 4 is the right place to catch them up.

### §2.2 `AGENT_REPO_LOCK.md`

Lines 4 and 12 both contain `D:\dev\tcg-watcher-worktree` (exact context not sampled, but matches the root-doc pattern). Same recommendation as §2.1.

### §2.3 Dispatcher runtime code and UI

| File | Line | Current | Planned |
|---|---|---|---|
| `dispatcher\task_dispatcher.py` | 1700 | `_REPO_ROOT = r"D:\dev\tcg-watcher-worktree"` | `_REPO_ROOT = r"D:\dev\miru"` |
| `dispatcher\handlers\gemini.py` | 24 | `_REPO_ROOT = r"D:\dev\tcg-watcher-worktree"` | `_REPO_ROOT = r"D:\dev\miru"` |
| `dispatcher\templates\dispatcher.html` | 125 | `<span class="ctx-chip ctx-repo" id="ctx-repo">tcg-watcher-worktree</span>` | `<span class="ctx-chip ctx-repo" id="ctx-repo">miru</span>` |

**Recommendation:** rewrite all three. The dispatcher.html change is cosmetic (UI chip label); the two `.py` changes are runtime and will break features that rely on `_REPO_ROOT` resolving to an existing path.

### §2.4 `windows\*.ps1` startup and MCP-helper scripts

| File | Line(s) | What it does | Action |
|---|---|---|---|
| `windows\start_perplexity_mcp.ps1` | 2 | `$envPath = 'D:\dev\tcg-watcher-worktree\.env'` | rewrite to `D:\dev\miru\.env` |
| `windows\run_miru_asset_job.ps1` | 7, 10, 13 | Three `D:\dev\tcg-watcher-worktree\...` paths (pointer file, tools dir, log path) | rewrite all three |
| `windows\register_restart_tasks.ps1` | 93 | `-Description "... Managed by D:\dev\tcg-watcher-worktree\windows\startup_all.ps1"` | rewrite path |

All three scripts are invoked by scheduled tasks / startup automation — must be corrected before Phase 8 "Firewall + scheduled tasks" phase.

### §2.5 `.gemini\settings.json` (Gemini CLI MCP config — parallels `.mcp.json`)

4 matching lines, all the same pattern as `.mcp.json`:

| Line | Context |
|---|---|
| 26 | Perplexity MCP PowerShell bootstrap — `$envPath='D:\\dev\\tcg-watcher-worktree\\.env'` + `D:\\dev\\tcg-watcher-worktree\\node_modules\\…` |
| 47 | sqlite-ro-snapshot `--db` arg |
| 64 | git `GIT_BASE_DIR` |
| 65 | git `npm_config_cache` |

**Recommendation:** apply the same two-pass substitution as `.mcp.json` (Item 4). This file is parallel MCP config for Gemini CLI. If Gemini is in scope on ROOM, it must be rewritten or Gemini's MCP loading will fail against nonexistent paths.

### §2.6 Living docs under `docs\`

| File | Line(s) | Action |
|---|---|---|
| `docs\STRUCTURE_CLEANUP_PLAN.md` | 112 | rewrite `D:\dev\tcg-watcher-worktree\` → `D:\dev\miru\` |
| `docs\RUNTIME_AUTHORITY_MATRIX.md` | 14, 15, 50, 57 | rewrite all 4 (matrix table + narrative) |

These are living team docs (not historical audits like `MIRU_MIGRATION_AUDIT.md`). Leaving stale paths in them will confuse future readers.

### §2.7 NAS IP (100.104.150.125) in templates and operator docs

Four files reference the NAS Tailscale IP directly in hardcoded URLs:

| File | Line(s) | Current | Planned |
|---|---|---|---|
| `windows\RUNTIME_AUTHORITY.md` | 58, 68 | "Your machine's Tailscale IP (e.g. 100.104.150.125)..." / `http://100.104.150.125:18765/api/health` | `100.81.19.49` |
| `tools\templates\partials\dev_cockpit.html` | 73, 90 | `href="{{ control_deck.get('worktree_site', 'http://100.104.150.125:18080/') }}"` / `'http://100.104.150.125:8080/'` | `100.81.19.49` |
| `tools\templates\miru_ai.html` | 1230, 1353, 1367 | three `href="http://100.104.150.125:<port>..."` buttons | `100.81.19.49` |
| `miru_ai\templates\partials\dev_cockpit.html` | 73, 90 | same as tools/ version | `100.81.19.49` |

**Recommendation:** rewrite all to `100.81.19.49` (ROOM's Tailscale IP). These render in the dev cockpit UI as clickable links — if left at NAS IP, buttons on the ROOM dashboard will link back to the NAS (which is still running but shouldn't be the destination for ROOM's own UI links).

**Concern worth flagging:** these templates hardcode an IP into a fallback default for `control_deck.get('worktree_site', ...)`. The first argument is a dict-lookup key, so in practice a config value usually overrides it. But if config isn't wired, the hardcoded fallback fires. Safer to fix.

### §2.8 `package-lock.json` line 2

```
2:  "name": "tcg-watcher-worktree",
```

This mirrors the package name from `package.json` (and gets regenerated on `npm install`). Let me call this out:
- **Safest:** rewrite both `package.json` and `package-lock.json`, or rewrite only `package.json` and let a Phase-4-post `npm install` regenerate the lockfile.
- **Risk:** if `package.json` already says `"name": "miru"` but the lockfile still says `"tcg-watcher-worktree"`, some tools warn about a name mismatch. Need to check `package.json` — I didn't inspect it (not in any Notion item); Captain can ping me to add that pre-write check.

### §2.9 `pm\routes\pages.py` line 83 — match in a COMMENT, DO NOT REWRITE

```
82:    normalized = _norm_image_path(request.args.get("p", ""))
83:    # Only serve from D:\Miru_Assets - no F:\OPTCG_Images fallback
84:    if MIRU_ASSETS.is_dir() and (MIRU_ASSETS / normalized).is_file():
```

The phrase `F:\OPTCG_Images` appears in a **comment** explaining that this route *deliberately does NOT* fall back to F:/OPTCG_Images. Rewriting it to `D:\OPTCG_Images` would make the comment read "no D:\OPTCG_Images fallback" — which is misleading. **Leave unchanged.**

### §2.10 Historical / archive matches — recommend SKIP

These files contain pattern matches but should remain unchanged:

| Category | File count | Why skip |
|---|---|---|
| `archive\legacy_helpers\tools\*.py` | 17 | Archived scripts. Not executed by any active service. Rewriting falsifies the archive. |
| `archive\op01\overlays\*.csv` | 1 | Data dump from old runs. |
| `archive\docker-compose*.yml` | 2 | Archived Docker compose files. |
| `data\overlays\*.csv`, `*.txt`, `*.sql` | 9 | Data-pipeline output files (cleanup results, summaries). Not code. |
| `data\batch_reports\governed_batch_report_*.*`, `governed_autopilot_*.*` | many | Old runtime batch outputs. |
| `MIRU_MIGRATION_AUDIT.md` | 1 (20+ matches inside) | Historical audit describing NAS-era state. Rewriting destroys the pre-migration reference. |

### §2.11 Directory to create (not a file rewrite)

- `C:\temp\playwright-shots\` — Notion Item 8 explicitly asks for this to be created. Empty directory, no content, no permissions changes. Low risk.

---

## §3 Summary — what Phase 4b (execution) would actually change

### §3.1 Authoritative Notion items (§1)

| Item | Files touched | Line-level changes | Lines added |
|---|---|---|---|
| 1 | `.env` | 1 | 0 |
| 2 | `.env` | 0 | 3 |
| 3 | `config\miru_mcp_policy.json` | 2 | 0 |
| 4 | `.mcp.json` | 10 | 0 |
| 5 | `miru_ai\server.py` | 2 | 0 |
| 6 | — | 0 (verified no-change) | 0 |
| 7 | `tools\miru_image_variant_classifier.py` | 2 (defense-in-depth, Captain-optional) | 0 |
| 8 | (n/a for file) + create `C:\temp\playwright-shots\` | 0 | 0 |
| 9 | — (done in Phase 2) | 0 | 0 |
| **Subtotal** | **5 files** | **17 edits** | **3 additions** |

### §3.2 Additional candidates (§2), if Captain approves

| Cluster | Files | Line-level changes |
|---|---|---|
| §2.1 Worker config md | 6 | 6 |
| §2.2 AGENT_REPO_LOCK.md | 1 | 2 |
| §2.3 Dispatcher code/UI | 3 | 3 |
| §2.4 `windows\*.ps1` | 3 | 5 |
| §2.5 `.gemini\settings.json` | 1 | 4 |
| §2.6 `docs\*.md` | 2 | 5 |
| §2.7 NAS IP in templates + RUNTIME_AUTHORITY.md | 4 | 8 |
| §2.8 `package-lock.json` + `package.json` | 2 | ~2 (after confirming package.json) |
| **Subtotal** | **22 files** | **~35 edits** |

### §3.3 Grand total if Captain approves everything

~27 files, ~52 line-level edits, 3 new lines in `.env`, 1 new directory (`C:\temp\playwright-shots\`).

### §3.4 Services that should come back to life after Phase 4b

Against the Phase 3 end-state projection:

| MCP | Before Phase 4b | After Phase 4b |
|---|---|---|
| `sqlite-ro-snapshot` | ❌ snapshot DB path still says `…\tcg-watcher-worktree\…` | ⚠️ path corrected to `…\miru\…`, but the DB file itself lands in Phase 6. Still ❌ for "connects" until Phase 6, but ✅ for "correctly configured". |
| git MCP | ✅ (worked on the right dir since Phase 2) | ✅ (no change from §1 Item 4 — same target dir, just repo-scope `.mcp.json` catching up to user-scope `.claude.json`) |
| notion / youtube / magic-ui (local) | ✅ (once Claude Code restarts after Phase 3) | ✅ (no regression from path rewrites; PowerShell `.env` path updates) |
| filesystem (Docker) | ❌ (Docker not installed per Phase 1 §3 "MCP dropped") | ❌ (still not installed; path correction is for completeness) |

Services beyond Claude Code MCP (PM, Miru AI, Dispatcher) depend on Phase 5 (assets), Phase 6 (data), Phase 7 (Python pkgs), and Phase 8 (scheduled tasks) to actually run — Phase 4 alone doesn't start them.

---

## §4 Risk notes before Phase 4b

1. **`.mcp.json` two-pass substitution risk.** Pass 1 replaces `D:\\dev\\tcg-watcher-worktree` (9 hits), pass 2 replaces `D:/dev/tcg-watcher-worktree` (1 hit). They don't overlap — safe to apply in either order. But: a naïve one-pass regex replace on `tcg-watcher-worktree` alone would also match the JSON key `"name": "tcg-watcher-worktree"` if that ever appears there (it doesn't today, but is worth a post-execution re-grep to confirm no stray matches).
2. **`package-lock.json` regeneration.** If the Phase 4b plan is to `npm install` anywhere during execution, it will regenerate the lockfile and may churn many lines beyond the name field. Recommend: rewrite `package.json` + `package-lock.json` surgically (just the name field), do NOT run `npm install` during Phase 4b, defer any reinstall to a dedicated phase (Phase 7).
3. **User-scope env vars.** `.env` rewrites on disk are not enough — Phase 1 also loaded vars into the Windows user environment. After Phase 4b's `.env` writes, we need a parallel `[Environment]::SetEnvironmentVariable` pass for any changed/added variable so new shells get the fresh values.
4. **`.claude/settings.local.json` uncertainty.** Notion Item 8 implies this file exists and needs editing, but on ROOM it doesn't exist at all (gitignored, not in snapshot). I recommend Captain explicitly decides — synthesize, skip, or recover from `D:\miru-migration\` — before Phase 4b runs.
5. **Backup before write.** Before Phase 4b executes, a single-shot copy of all files slated for edit → `D:\miru-migration\_backups\phase4_pre_rewrite\` (or similar) preserves a fallback. Same convention Phase 1 used for `.claude.json.pre_phase1_…bak`. Trivial to do, hard to regret.

---

## §5 Questions for Captain before Phase 4b runs

1. **Scope of Phase 4b:** all of §1 + §2, or §1 only? (§2 items are technically required for full correctness but aren't in Notion's Critical list.)
2. **Item 7 (defense-in-depth):** apply the F:→D: rewrite to `tools\miru_image_variant_classifier.py` lines 3 and 37, or leave unchanged and rely on env var at runtime?
3. **Item 8 `.claude\settings.local.json`:** synthesize default, skip entirely, or check `D:\miru-migration\` for a recoverable copy first?
4. **`package.json` + `package-lock.json`:** rewrite name field, or defer to a Phase-7 `npm install` pass?
5. **Pre-execution backup:** do you want me to copy all targeted files into `_backups\phase4_pre_rewrite\` before the first write?

---

## §6 Status

**Phase 4a — DRY RUN: COMPLETE.** Preview document is this file. No files modified on disk beyond creating this preview. Awaiting Captain's go / redlines before Phase 4b.
