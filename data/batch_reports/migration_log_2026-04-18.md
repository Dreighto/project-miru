# Migration Phase 1 — Install Log

**Date:** 2026-04-18
**Host:** ROOM (Tailscale `100.81.19.49`, MagicDNS `room.taila28611.ts.net`)
**User:** Dreighto (non-admin session, user-scope installs only)
**Source of truth:** https://www.notion.so/346c5d34014181ac8388c02f74f79fcd

Phase 1 absorbed all of Phase 7's bootstrap (Option C approved by Captain).
This log will be moved to `D:\dev\miru\data\batch_reports\migration_log_2026-04-18.md` in Phase 2.

---

## 1. Runtimes, CLIs, tools

| Tool | NAS | Installed on ROOM | Install path | Delta |
|---|---|---|---|---|
| Git | 2.53.0.windows.2 | 2.53.0.windows.3 (pre-existing) | `C:\Program Files\Git\cmd\git.exe` | patch bump, no action needed |
| Node.js | 22.22.0 | **22.22.2** (winget `OpenJS.NodeJS.22`) | `C:\Users\Dreighto\AppData\Local\Microsoft\WinGet\Packages\OpenJS.NodeJS.22_…` | patch bump, Captain-approved |
| npm (bundled) | 11.12.1 | **10.9.7** (whatever shipped with Node 22.22.2) | same as Node | self-upgrade failed; Captain-approved skip |
| npm global prefix | `…\Roaming\npm` | **reset to `C:\Users\Dreighto\AppData\Roaming\npm`** (default was fragile WinGet-package path) | — | hardening fix |
| Python | 3.14.3 | **3.14.3** (winget `Python.Python.3.14`) | `C:\Users\Dreighto\AppData\Local\Programs\Python\Python314\python.exe` | exact match |
| pip/setuptools/wheel | bundled | upgraded to latest after Python install, then used for -r requirements | — | housekeeping |
| uv / uvx | not in NAS snapshot | **0.11.7** (winget `astral-sh.uv`) | `…\WinGet\Packages\astral-sh.uv_…` | first install |
| NSSM | 2.24 (custom build) | **2.24-101-g897c7ad** (winget `NSSM.NSSM`) | `…\WinGet\Packages\NSSM.NSSM_…` | same 2.24 line |
| SQLite3 CLI | 3.46.1 | **3.53.0** (winget `SQLite.SQLite`) | `…\WinGet\Packages\SQLite.SQLite_…` | minor bump, Captain-approved, backward-compatible |
| Claude Code CLI | 2.1.76 | **2.1.76** (`npm i -g @anthropic-ai/claude-code@2.1.76`) | `C:\Users\Dreighto\AppData\Roaming\npm\claude.cmd` | exact match |
| Codex CLI | 0.120.0 | **0.120.0** (`npm i -g @openai/codex@0.120.0`) | `C:\Users\Dreighto\AppData\Roaming\npm\codex.cmd` | exact match |
| Gemini CLI | 0.38.0 | **0.38.0** (`npm i -g @google/gemini-cli@0.38.0`) | `C:\Users\Dreighto\AppData\Roaming\npm\gemini.cmd` | exact match |

### Python packages
99 packages pinned from `D:\miru-migration\tools_info\python_pip_list.txt`. Installed with `--prefer-binary --no-warn-script-location`. `pip freeze` on ROOM now matches the NAS list at the recorded versions. No build failures.

## 2. Environment variables

Loaded **23 user-scope env vars** from `D:\miru-migration\secrets\.env` via `[Environment]::SetEnvironmentVariable(name, value, 'User')`. **Values were never echoed, logged, or written to any file.** Names only:

- ANTHROPIC_API_KEY, ASSEMBLY_AI_API_KEY, CURSOR_API_KEY, DEBUG_IMAGES, DISPATCHER_BASE_URL, FIRECRAWL_API_KEY, JUSTTCG_API_KEY, MAGIC_UI_API_KEY, MIRU_HELPER_BASE_URL, MIRU_HELPER_ENABLED, MIRU_HELPER_MODEL, NOTION_TOKEN, OLLAMA_BASE_URL, OPENAI_API_KEY, PERPLEXITY_API_KEY, PUSHOVER_API_TOKEN, PUSHOVER_DEFAULT_PRIORITY, PUSHOVER_ENABLED, PUSHOVER_USER_KEY, SLACK_APP_TOKEN, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID, YOUTUBE_API_KEY

**Note (flagged for Phase 4 follow-up):** `DISPATCHER_BASE_URL` is currently loaded as `http://100.104.150.125:19000` (the NAS value from the source .env). Phase 4 rewrites `.env` to `http://127.0.0.1:19000`; the user env var will need to be refreshed at that time (single `SetEnvironmentVariable` call).

## 3. MCP config

Source: `D:\miru-migration\secrets\project_mcp.json` (.mcp.json is gitignored, so it was not in repo_snapshot — the canonical copy lives in secrets).

Target: `C:\Users\Dreighto\.claude.json` (user scope, top-level `mcpServers`).

Backup of previous .claude.json: `D:\miru-migration\_backups\claude.json.pre_phase1_20260418_120047.bak`

### Inline text substitutions applied before JSON parsing:

| Old | New | Hits replaced |
|---|---|---|
| `D:\\dev\\tcg-watcher-worktree` | `D:\\dev\\miru` | 9 |
| `D:/dev/tcg-watcher-worktree`   | `D:/dev/miru`   | 1 |
| `nas.taila28611.ts.net`         | `room.taila28611.ts.net` | 0 (none in MCP config) |
| `100.104.150.125`               | `100.81.19.49`  | 0 (none in MCP config) |

### MCPs registered (11 of 12 original):

| MCP | Type | Depends on | Expected state after Claude Code restart |
|---|---|---|---|
| `fetch` | uvx | uvx installed | ✅ should respond |
| `justtcg` | HTTP | `${JUSTTCG_API_KEY}` env var | ✅ should respond (env var set) |
| `perplexity` | npx.cmd | Node + PERPLEXITY_API_KEY env var | ✅ should respond |
| `sequential-thinking` | npx.cmd | Node | ✅ should respond |
| `playwright` | npx.cmd | Node | ✅ should respond |
| `shadcn` | npx.cmd | Node | ✅ should respond |
| `git` | npx.cmd + GIT_BASE_DIR | repo at `D:\dev\miru` | ⚠️ partial until Phase 2 (repo copy); dir exists but no git repo yet |
| `sqlite-ro-snapshot` | npx.cmd + DB file | `D:\dev\miru\miru-mcp\sqlite-ro\card_catalog.snapshot.db` | ❌ will fail until Phase 6 (data copy) |
| `notion` | PowerShell reads `.env` | `D:\dev\miru\.env` | ❌ will fail until Phase 3 (secrets placed) |
| `youtube` | PowerShell reads `.env` | `D:\dev\miru\.env` | ❌ will fail until Phase 3 |
| `magic-ui` | PowerShell reads `.env` | `D:\dev\miru\.env` | ❌ will fail until Phase 3 |

### MCP dropped:

- `filesystem` — Docker-based. Skipped per Captain's instruction (built-in file tools cover this, Docker not installed on ROOM).

## 4. Helper scripts created

- `D:\miru-migration\_phase1_verify.ps1` — tool verification with PATH refresh
- `D:\miru-migration\_phase1_npm_install.ps1` — npm CLI installs at pinned versions, stable prefix
- `D:\miru-migration\_phase1_pip_install.ps1` — pip -r install
- `D:\miru-migration\_phase1_env_load.ps1` — loads .env into user env vars (values not logged)
- `D:\miru-migration\_phase1_mcp_prepare.py` — substitutions + .claude.json merge with backup

## 5. Next steps

1. **Captain:** restart Claude Code (close this session, re-launch) so the 11 registered MCPs initialize.
2. **Captain:** verify via `/mcp` (Claude Code slash command) — expect 6 ✅, 4 ❌ pending later phases, 1 ⚠️ partial. Any unexpected errors → report back before Phase 2.
3. On confirmation, Phase 2 begins: repo copy from `D:\miru-migration\repo_snapshot\tcg-watcher-worktree\` → `D:\dev\miru\` with folder rename.

---

# Migration Phase 2 — Repo Copy Log

**Date:** 2026-04-18 (started 12:14:14, finished 12:14:17 local on ROOM)
**Host:** ROOM (Tailscale `100.81.19.49`, MagicDNS `room.taila28611.ts.net`)
**User:** Dreighto
**Source of truth:** same Notion doc as Phase 1

Phase 2 scope (as directed by Captain):
1. Copy `D:\miru-migration\repo_snapshot\tcg-watcher-worktree\*` → `D:\dev\miru\` (folder rename baked in).
2. Verify `.git` copied intact, HEAD points at `phase3-console-2`, working tree clean.
3. Tag the migration checkpoint as `migration-phase-2`.
4. Move this log to its new permanent home under the repo.

---

## 1. Pre-copy state of `D:\dev\miru\`

Only artifact present: `D:\dev\miru\.npm-cache` (created during Phase 1 MCP startup — the `git` / `playwright` / `magic-ui` / `shadcn` MCP entries set `npm_config_cache=D:\dev\miru\.npm-cache`). Preserved across the copy — see §3 "Extras" below.

## 2. Copy command

```
MSYS_NO_PATHCONV=1 robocopy \
  "D:\miru-migration\repo_snapshot\tcg-watcher-worktree" \
  "D:\dev\miru" \
  /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NP
```

- `/E` — copy all subdirs including empty ones
- `/COPY:DAT` / `/DCOPY:DAT` — data + attributes + timestamps (no ACLs; not needed on this box)
- `/R:1 /W:1` — retry once with 1-second wait on transient error
- **No `/MIR`** — intentional, so the pre-existing `.npm-cache` at destination is preserved
- `MSYS_NO_PATHCONV=1` — prevents Git Bash from rewriting `/E` into an E:\ path (first attempt without this env var failed with "Invalid Parameter #3: E:/", retried successfully)

## 3. Robocopy result

| Metric | Total | Copied | Skipped | FAILED | Extras |
|---|---|---|---|---|---|
| Dirs | 201 | 200 | 1 | 0 | 1 |
| Files | 1327 | 1327 | 0 | 0 | 0 |
| Bytes | 425.21 MB | 425.21 MB | 0 | 0 | 0 |

Elapsed: 3 seconds. Throughput: ~155 MB/s (local SSD → local SSD, D:).

- 1 skipped dir = the source root (already existed at target as `D:\dev\miru`).
- 1 extra dir = `D:\dev\miru\.npm-cache` (preserved from Phase 1, correctly not touched).
- Robocopy exit code **3** = 1 (files/dirs copied) + 2 (extras in dest). < 8 = success per robocopy convention.

## 4. Git verification

Run from `D:\dev\miru`:

| Check | Command | Result | Expected | Match |
|---|---|---|---|---|
| HEAD ref | `cat .git/HEAD` | `ref: refs/heads/phase3-console-2` | `refs/heads/phase3-console-2` | ✅ |
| Symbolic HEAD | `git symbolic-ref HEAD` | `refs/heads/phase3-console-2` | `refs/heads/phase3-console-2` | ✅ |
| Current branch | `git branch --show-current` | `phase3-console-2` | `phase3-console-2` | ✅ |
| Commit SHA | `git rev-parse HEAD` | `a1809526983cd292df05b3b7a3c2ec93c574fd87` | `a1809526983cd292df05b3b7a3c2ec93c574fd87` (per README_FIRST.md) | ✅ |
| Commit subject | `git log -1 --oneline` | `a180952 docs: craft guides for PM + UI/UX; migration audit` | — | — |
| Working tree | `git status --porcelain` | (empty; 0 lines) | clean | ✅ |
| Upstream | `git status` | "Your branch is up to date with 'origin/phase3-console-2'." | tracking intact | ✅ |

`.git/` object store, `packed-refs`, `logs/`, `index`, and `config` all copied successfully. `remote "origin"` still points at `https://github.com/Dreighto/project-miru.git` (no path rewrite needed for remotes).

## 5. Tag

Created: **`migration-phase-2`** → `a1809526983cd292df05b3b7a3c2ec93c574fd87`

```
$ git show-ref --tags migration-phase-2
a1809526983cd292df05b3b7a3c2ec93c574fd87 refs/tags/migration-phase-2
```

**Note on tag type:** This is a **lightweight** tag, not annotated. Reason: no `user.name` / `user.email` is configured in either global or local git config on ROOM, and annotated tags require a committer identity. I did NOT set git identity autonomously (no config change without Captain's OK). Lightweight tag still accomplishes the checkpoint — `git rev-parse migration-phase-2` works, `git checkout migration-phase-2` works, `git log migration-phase-2..HEAD` works. If the Captain wants an annotated tag with message/tagger, let me know a `user.name` + `user.email` to use and I'll add one (scoped to this repo, not global).

## 6. Log migration

Phase 1 log file:
- **Was:** `D:\miru-migration\phase1_install_log_2026-04-18.md`
- **Now:** `D:\dev\miru\data\batch_reports\migration_log_2026-04-18.md` (this file)

The rename (`phase1_install_log` → `migration_log`) matches the Phase 1 log's own stated plan (see top of §5 in the Phase 1 section above). Phase 2 content appended below the original Phase 1 content; nothing deleted or edited from Phase 1.

## 7. MCP status impact (informational)

Re-verifying the 11 local MCP entries against the post-Phase-2 state:

| MCP | Phase 1 state | Now (post Phase 2) | Next unblocker |
|---|---|---|---|
| fetch | ✅ | ✅ | — |
| justtcg | ✅ | ✅ | — |
| perplexity | ✅ | ✅ | — |
| sequential-thinking | ✅ | ✅ | — |
| playwright | ✅ | ✅ | — |
| shadcn | ✅ | ✅ | — |
| git | ⚠️ partial (dir existed, no repo) | ✅ repo now present at `D:\dev\miru` (will upgrade from partial to full on next Claude Code restart) | — |
| notion | ❌ no `.env` | ❌ still no `.env` | Phase 3 (secrets) |
| magic-ui | ❌ no `.env` | ❌ still no `.env` | Phase 3 (secrets) |
| youtube | ❌ no `.env` | ❌ still no `.env` | Phase 3 (secrets) |
| sqlite-ro-snapshot | ❌ no DB | ❌ still no DB (snapshot comes from `data/mcp/card_catalog.snapshot.db` → Phase 6 data copy) | Phase 6 (data) |

No MCP state **regressed**. The `git` MCP will go from ⚠️ → ✅ at next Claude Code restart.

## 8. Non-actions (deliberately skipped)

To respect the "standard pause at end before Phase 3":
- Did **NOT** create `.env` at `D:\dev\miru\.env` (Phase 3 task).
- Did **NOT** create `.mcp.json` at `D:\dev\miru\.mcp.json` (Phase 3 task).
- Did **NOT** run Step 6 "Path remapping" from `README_FIRST.md` (Phase 4 task — rewrites paths inside `.env`, `.mcp.json`, `config\*.json`, `windows\*.ps1`).
- Did **NOT** copy `data\` payload from `D:\miru-migration\data\` (Phase 6 task — card_catalog.db, runtime JSON state, snapshots, dispatcher jobs.db, MCP snapshot DB).
- Did **NOT** set git `user.name` / `user.email` (would be needed for annotated tags or future commits — flagged for Captain).
- Did **NOT** push the `migration-phase-2` tag to `origin` (local-only checkpoint).
- Did **NOT** touch the local `notion` MCP entry in `.claude.json` (Captain: "leave for now, trim in future pass").
- Did **NOT** disable the 4 expected-failure MCP entries (Captain: "don't disable or patch anything").

## 9. Open items to surface at Phase 3 kickoff

1. **Git identity on ROOM.** First time git needs a committer (any future annotated tag or commit) will prompt again. Captain should decide: which name/email to configure, and scope (local to `D:\dev\miru` vs user-global).
2. **Lightweight tag vs annotated.** `migration-phase-2` is currently lightweight. Happy to upgrade to annotated once identity is settled — no objections from git on replacing the lightweight with an annotated of the same name (`git tag -f -a migration-phase-2 -m "..."`).
3. **Phase 3 input needed:** `D:\miru-migration\secrets\.env` and `D:\miru-migration\secrets\project_mcp.json` are the sources for Phase 3 (per `README_FIRST.md` Step 4). I already verified those exist in the Phase 1 run; no new work needed to locate them.

## 10. Next steps

1. **Pause here.** Awaiting Captain's OK before Phase 3 (secrets placement).
2. **Phase 3 (when Captain greenlights):** copy `secrets\.env` → `D:\dev\miru\.env`, copy `secrets\project_mcp.json` → `D:\dev\miru\.mcp.json`. Per Project Miru secrets-handling rules, values will not be echoed or logged — only names and file paths.
3. **Post-Phase-3 MCP restart:** Captain restarts Claude Code; expect `notion`, `magic-ui`, `youtube` to flip from ❌ → ✅. `sqlite-ro-snapshot` stays ❌ until Phase 6.

---

**Phase 2 status: COMPLETE — awaiting Captain review before Phase 3.**

---

# Migration Phase 3 — Secrets Placement Log

**Date:** 2026-04-18 (~12:16–12:18 local on ROOM)
**Host:** ROOM
**User:** Dreighto

Phase 3 scope (as directed by Captain):
1. Pre-Phase-3 fixup A: set repo-scoped git identity.
2. Pre-Phase-3 fixup B: upgrade `migration-phase-2` tag from lightweight to annotated.
3. Confirm `.env` is listed in `.gitignore` **before** placing the file.
4. Place `secrets\.env` → `D:\dev\miru\.env`.
5. Place `secrets\project_mcp.json` → `D:\dev\miru\.mcp.json` (only if different from repo version; skip if identical).
6. Verify both files land and remain gitignored.
7. Log with **names and paths only** — no secret values ever recorded.
8. Pause before Phase 4.

---

## 1. Git identity fixup (repo-scoped)

Applied in `D:\dev\miru\.git\config` only. **NOT** set at global scope.

| Key | Value |
|---|---|
| `user.name` | `Dreighto` |
| `user.email` | `dreighto@users.noreply.github.com` |

Verification:
- `git config --local --get user.name` → `Dreighto` ✓
- `git config --local --get user.email` → `dreighto@users.noreply.github.com` ✓
- `git config --global --get user.name` → unset (confirmed clean) ✓
- `git config --global --get user.email` → unset (confirmed clean) ✓

No other repos on ROOM are affected.

## 2. Tag upgrade — `migration-phase-2` (lightweight → annotated)

| Step | Command | Result |
|---|---|---|
| Confirm old type | `git cat-file -t migration-phase-2` | `commit` (= lightweight) |
| Delete | `git tag -d migration-phase-2` | `Deleted tag 'migration-phase-2' (was a180952)` |
| Recreate | `git tag -a migration-phase-2 a1809526983cd292df05b3b7a3c2ec93c574fd87 -m "<msg>"` | OK |
| Confirm new type | `git cat-file -t migration-phase-2` | `tag` (= annotated) |
| Confirm target | `git rev-parse migration-phase-2^{commit}` | `a1809526983cd292df05b3b7a3c2ec93c574fd87` (same commit) |
| Tagger | — | `Dreighto <dreighto@users.noreply.github.com>` |

Annotated message body: _"Migration Phase 2 complete — repo snapshot copied from NAS (D:\miru-migration\repo_snapshot\tcg-watcher-worktree) to ROOM at D:\dev\miru. Branch phase3-console-2. Working tree clean. See data/batch_reports/migration_log_2026-04-18.md for full log."_

**Policy going forward:** every future phase tag is annotated (Captain's directive).

## 3. .gitignore pre-flight check — PASSED ✅

Read `D:\dev\miru\.gitignore` before placing any secrets file. Relevant entries:

| Line | Rule | Covers |
|---|---|---|
| 1 | `.env` | the file being placed in this phase |
| 2 | `.env.*` | `.env.dev`, `.env.local`, etc. |
| 3 | `!.env.example` | allow `.env.example` to be tracked (not a risk) |
| 9 | `secrets/` | if anyone ever drops a `secrets/` dir *inside* the repo, it's ignored |
| 11 | `.mcp.json` | the other file being placed in this phase |
| 12 | `mcp.json` | case-insensitive fallback |
| 13 | `.cursor/mcp.json` | Cursor user MCP (out of Phase 3 scope, but noted) |

`git check-ignore -v .env .mcp.json` after placement confirmed each path matches its rule — no ambiguity, no override.

## 4. Source inventory — `D:\miru-migration\secrets\`

| File | Size | Purpose | This phase? |
|---|---|---|---|
| `.env` | 1402 bytes | 23 env-var assignments for Project Miru | **yes → `D:\dev\miru\.env`** |
| `project_mcp.json` | 4399 bytes | Project-scope MCP server config | **yes → `D:\dev\miru\.mcp.json`** |
| `cursor_mcp_user.json` | 2072 bytes | Cursor user-scope MCP config | no (destined for `C:\Users\Dreighto\.cursor\mcp.json`, out of Phase 3 scope) |

**Values from `.env` were never read, echoed, parsed, or logged during this phase.** Only the file size and SHA-256 digest were computed for verification. Env-var names were already recorded in the Phase 1 section of this log (§2, 23 names) — no re-listing needed.

## 5. Copy operations

```
cp -p "D:\miru-migration\secrets\.env"             "D:\dev\miru\.env"
cp -p "D:\miru-migration\secrets\project_mcp.json" "D:\dev\miru\.mcp.json"
```

`-p` preserves timestamps and mode. No content printed to stdout at any point.

### 5a. `.env` — verification by size + hash

| Check | Source | Destination | Match |
|---|---|---|---|
| Size | 1402 bytes | 1402 bytes | ✓ |
| SHA-256 | `897d503cca5cea62dce6c33acb7e2405798cb9d05e136e92898698e7f513b6f0` | `897d503cca5cea62dce6c33acb7e2405798cb9d05e136e92898698e7f513b6f0` | ✓ |

### 5b. `.mcp.json` — pre-copy "repo version" check

Per Captain's instruction ("only if different from repo version — otherwise note and skip"):
- Checked: `D:\dev\miru\.mcp.json` — **did not exist** prior to Phase 3 (gitignored per `.gitignore:11`, and excluded from `repo_snapshot/` as noted in Phase 1 §3).
- Therefore "different from repo version" is trivially true (source vs. non-existent). Proceeded with copy.

| Check | Source | Destination | Match |
|---|---|---|---|
| Size | 4399 bytes | 4399 bytes | ✓ |
| SHA-256 | `ed064920372436c08033d61955242c9e379449b8108043650605e51301946efc` | `ed064920372436c08033d61955242c9e379449b8108043650605e51301946efc` | ✓ |

**Caveat for Phase 4:** this `.mcp.json` is the *pre-substitution* version from `secrets\`. It still contains NAS-era paths (`D:\dev\tcg-watcher-worktree\...`) that will break the `git` MCP and the `sqlite-ro-snapshot` MCP until Phase 4 "Path remapping" rewrites them to `D:\dev\miru\...`. This matches the README_FIRST.md Step 6 plan. I did NOT pre-emptively rewrite paths — that's Phase 4's explicit job, and keeping it separated preserves the per-phase audit trail.

> The Claude-Code-scope MCP config (`C:\Users\Dreighto\.claude.json`) already has the substituted paths — that was done in Phase 1. The `.mcp.json` at the repo root is a **separate** file used by other tooling that reads project-scope MCP config (e.g., `cursor` project settings). It needs its own remap in Phase 4.

## 6. Post-placement git state

```
$ git check-ignore -v .env .mcp.json
.gitignore:1:.env	.env
.gitignore:11:.mcp.json	.mcp.json
```

Both files match a `.gitignore` rule exactly. No risk of accidental commit.

`git status --ignored --short` fragment:
```
!! .env
!! .mcp.json
```

`git status` (full) shows:
- Branch: `phase3-console-2`, up to date with `origin/phase3-console-2`
- **One untracked file:** `data/batch_reports/migration_log_2026-04-18.md` — **this log itself**, created during Phase 2 in a tracked folder. It is deliberately untracked (I did not run `git add` on it) because commits haven't been authorized by Captain. Not a Phase 3 regression.
- No other untracked files. No modified files. No deleted files.

**Net change from Phase 3 to the git-visible working tree: zero** (both placed files are ignored).

## 7. MCP status impact (projected, pending next Claude Code restart)

Phase 3 unblocks the three `.env`-dependent servers. It does **not** unblock `sqlite-ro-snapshot` (Phase 6).

| MCP | Before Phase 3 | After Phase 3 + Claude Code restart | Notes |
|---|---|---|---|
| notion (local) | ❌ no `.env` | ✅ should connect (NOTION_TOKEN readable) | Remains redundant vs. Anthropic-hosted Notion connector — future cleanup per Captain |
| magic-ui | ❌ no `.env` | ✅ should connect (MAGIC_UI_API_KEY readable) | — |
| youtube | ❌ no `.env` | ✅ should connect (YOUTUBE_API_KEY readable) | — |
| sqlite-ro-snapshot | ❌ no DB | ❌ still no DB | Snapshot DB comes from Phase 6 data copy |
| git | ✅ (post Phase 2) | ✅ | `GIT_BASE_DIR` in `.claude.json` already points at `D:\dev\miru` |
| fetch, justtcg, perplexity, sequential-thinking, playwright, shadcn | ✅ | ✅ | Unchanged |

**Captain-facing expectation at next restart:** 10 of 11 local MCPs green, `sqlite-ro-snapshot` still red (expected, waiting on Phase 6).

## 8. Non-actions (deliberately skipped)

- Did **NOT** copy `secrets\cursor_mcp_user.json` to `C:\Users\Dreighto\.cursor\mcp.json` (not in Captain's Phase 3 scope — separate user-scope artifact).
- Did **NOT** run Step 6 "Path remapping" inside `.env` or `.mcp.json` (Phase 4).
- Did **NOT** execute any commands that would read, parse, grep, or echo the contents of `.env`.
- Did **NOT** run `git add .env` / `git add .mcp.json` — both are correctly excluded by `.gitignore`, and manually `-f` forcing them would defeat the whole point.
- Did **NOT** push or tag-push to `origin`. The annotated `migration-phase-2` tag is still local only.
- Did **NOT** commit the `migration_log_2026-04-18.md` file itself — kept as untracked until Captain says otherwise.

## 9. Open items to surface at Phase 4 kickoff

1. **Path remap targets.** README_FIRST.md Step 6 lists the files:
   - `D:\dev\miru\.env` — check `DISPATCHER_BASE_URL`, `MIRU_HELPER_BASE_URL`, `OLLAMA_BASE_URL`, any absolute paths.
   - `D:\dev\miru\.mcp.json` — check `sqlite-ro-snapshot --db` argument + filesystem mount path.
   - `D:\dev\miru\config\*.json` — absolute paths.
   - `D:\dev\miru\windows\*.ps1` — restart scripts with absolute paths.
2. **Env-var refresh.** Phase 1 set `DISPATCHER_BASE_URL` as a user-scope env var to the NAS value `http://100.104.150.125:19000`. Phase 4 needs to also re-set the user env var to the ROOM value (likely `http://127.0.0.1:19000`) via `[Environment]::SetEnvironmentVariable`.
3. **Commit of migration log.** Whether `data/batch_reports/migration_log_2026-04-18.md` should be committed to git (now that identity is set) — Captain's call.
4. **`cursor_mcp_user.json` placement.** Separate follow-up; not blocking Phase 4 but needed before Cursor is useful on ROOM.

## 10. Next steps

1. **Pause here.** Awaiting Captain's OK before Phase 4 (path remapping).
2. **Phase 4 (when greenlit):** read `.env` / `.mcp.json` / `config/*.json` / `windows/*.ps1`, apply the three path substitutions from README_FIRST.md Step 6:
   - `D:\dev\tcg-watcher-worktree` → `D:\dev\miru`
   - `D:\Miru_Assets` → `C:\Miru_Assets` *(or whatever the ROOM layout decides — README_FIRST.md assumed C:\ but ROOM may use D:\ since this is a D-drive-based install)*
   - `F:\OPTCG_Images` → `C:\OPTCG_Images` *(same caveat — ROOM drive layout needs Captain's decision)*
3. **At Phase 4 start, Captain should confirm:** final drive letters for `Miru_Assets` and `OPTCG_Images` on ROOM (the SSD README assumed `C:\`, but this install has been using `D:\` so far).

---

**Phase 3 status: COMPLETE — awaiting Captain review before Phase 4.**

---

# Migration Phase 4 — Path Rewrites

**Date:** 2026-04-18 (~13:13–13:18 local on ROOM)
**Host:** ROOM
**User:** Dreighto
**Authoritative plan:** Notion "🚚 🚚 15 Migration — NAS to ROOM" § "Critical" items 1–9
**Preview doc:** [data/batch_reports/migration_preview_2026-04-18.md](migration_preview_2026-04-18.md) (Captain-approved 2026-04-18)

Phase 4a (dry run) was approved with full §1 + §2 scope, two documented skips (§1 Item 6 no-op, §1 Item 9 already done), plus the do-not-touch list from §0.3 of the preview. Phase 4b (this section) executed that plan.

---

## 1. Pre-execution pre-flight

| Check | Result | Action |
|---|---|---|
| Recoverable `.claude/settings.local.json` anywhere in `D:\miru-migration\` | ❌ not found (globbed `**/settings.local.json` and `**/.claude/**` — zero hits) | Per Captain's directive, §1 Item 8 **file** rewrite SKIPPED. Directory creation (`C:\temp\playwright-shots\`) proceeds as planned. |
| `package.json` has `"name": "tcg-watcher-worktree"` | ❌ no `"name"` field exists at all (only `devDependencies`) | §2.8 SKIPPED. Phase 7's `npm install` will regenerate/refresh `package-lock.json` cleanly — no pre-rewrite needed. |
| Backup directory creation | ✅ `D:\miru-migration\_backups\phase4_pre_rewrite_2026-04-18\` | proceed |

## 2. Backup (25 files, pre-rewrite snapshot)

One-shot backup before any byte of any target was altered. Implementation: `shutil.copy2` from each target into `D:\miru-migration\_backups\phase4_pre_rewrite_2026-04-18\<same-relative-path>`, preserving timestamps and mode.

All 25 files backed up successfully. Total backup size ~850 KB (dominated by `miru_ai/server.py` at 495 KB and `tools/templates/miru_ai.html` at 88 KB). Exact list reproduced from the Python script's stdout — same 25 files as the rewrite phase below.

Rollback procedure (if ever needed): `cp -pr "D:\miru-migration\_backups\phase4_pre_rewrite_2026-04-18\*" "D:\dev\miru\"` (via PowerShell `Copy-Item -Recurse -Force`).

## 3. Rewrites executed (25 files, 52 in-place replacements + 3 new .env lines)

Implementation: `D:\miru-migration\_phase4_rewrite.py` — binary-mode read/write, per-file substitution rules with expected-minimum-hit floors for sanity checking. Summary (from script stdout):

### §1 Notion authoritative items

| File | Rule(s) | Hits |
|---|---|---|
| `.env` | `DISPATCHER_BASE_URL=http://100.104.150.125:19000` → `…=http://127.0.0.1:19000` | 1 |
| `config/miru_mcp_policy.json` | `canonical_repo` NAS→miru; `canonical_phone_verification_host` 100.104.150.125→100.81.19.49 | 1+1 |
| `.mcp.json` | `D:\\dev\\tcg-watcher-worktree` → `D:\\dev\\miru` (9 hits); `D:/dev/tcg-watcher-worktree` → `D:/dev/miru` (1 hit) | **10** ✓ matches Notion's expected count |
| `miru_ai/server.py` | `F:/OPTCG_Images` → `D:/OPTCG_Images` | 2 (lines 8118 + 9575) |
| `tools/miru_image_variant_classifier.py` (defense-in-depth) | `F:/OPTCG_Images` → `D:/OPTCG_Images` | 2 (lines 3 + 37; line 69 unchanged — env-var driven) |

### §2 Additional candidates

| File | Rule | Hits |
|---|---|---|
| `CLAUDE.md`, `CODEX.md`, `COPILOT.md`, `CURSOR.md`, `GEMINI.md`, `pm/CLAUDE.md` | `D:\dev\tcg-watcher-worktree` → `D:\dev\miru` | 1 each = **6** |
| `AGENT_REPO_LOCK.md` | same | 2 |
| `dispatcher/task_dispatcher.py`, `dispatcher/handlers/gemini.py` | same | 1 each = 2 |
| `dispatcher/templates/dispatcher.html` | bare `>tcg-watcher-worktree<` → `>miru<` (UI chip label, line 125) | 1 |
| `windows/start_perplexity_mcp.ps1` | `D:\dev\tcg-watcher-worktree` → `D:\dev\miru` | 1 |
| `windows/run_miru_asset_job.ps1` | same | 3 |
| `windows/register_restart_tasks.ps1` | same | 1 |
| `.gemini/settings.json` | `D:\\dev\\tcg-watcher-worktree` → `D:\\dev\\miru` (backslash form only) | **5** (the backslash pattern hit 5 times, not 4 as the preview estimated — line 26 contains two occurrences in one long PowerShell command string. Forward-slash pass hit 0, as expected.) |
| `docs/STRUCTURE_CLEANUP_PLAN.md` | `D:\dev\tcg-watcher-worktree` → `D:\dev\miru` | 1 |
| `docs/RUNTIME_AUTHORITY_MATRIX.md` | same | 4 |
| `windows/RUNTIME_AUTHORITY.md` | `100.104.150.125` → `100.81.19.49` | 2 |
| `tools/templates/partials/dev_cockpit.html` | same | 2 |
| `tools/templates/miru_ai.html` | same | 3 |
| `miru_ai/templates/partials/dev_cockpit.html` | same | 2 |

### .env append (§1 Item 2)

Appended exactly 3 new lines to `D:\dev\miru\.env`, preserving the file's existing line-ending style (CRLF vs LF was auto-detected from existing content):

```
PROJECT_MIRU_CLEAN_THUMB_ROOT=D:\Miru_Assets
MIRU_RUNTIME_IMAGES_ROOT=D:\Miru_Assets
MIRU_OPTCG_IMAGES_ROOT=D:\OPTCG_Images
```

Implementation never printed `.env` contents; only confirmed "3 new lines appended".

### Totals

- **25 files touched** (as previewed)
- **52 in-place byte replacements** (matches the preview's §3.3 estimate of ~52 exactly)
- **3 new lines** appended to `.env`
- **Zero warnings** from the rewrite script (every rule hit its expected minimum)

## 4. Windows user-scope environment variables

Four variables set via `[Environment]::SetEnvironmentVariable(name, value, 'User')` in a single PowerShell invocation, then read back from the User hive for verification:

| Variable | New value (readback) |
|---|---|
| `DISPATCHER_BASE_URL` | `http://127.0.0.1:19000` |
| `PROJECT_MIRU_CLEAN_THUMB_ROOT` | `D:\Miru_Assets` |
| `MIRU_RUNTIME_IMAGES_ROOT` | `D:\Miru_Assets` |
| `MIRU_OPTCG_IMAGES_ROOT` | `D:\OPTCG_Images` |

`DISPATCHER_BASE_URL` previously loaded as the NAS IP (Phase 1 §2 flagged this) — now resynchronized with the Phase 4 `.env` rewrite. The three new vars from §1 Item 2 are now live at User scope and will be inherited by every new shell / service.

## 5. Directory created

`C:\temp\playwright-shots\` — empty, no content, no ACL changes. Matches `.gitignore` note "Playwright / debug screenshots (must be saved outside the repo…)".

## 6. Post-execution verification (all five patterns)

Ran `Grep` for each pattern across the repo. Expected: zero matches in the approved scope; only matches permitted are in the preview's §0.3 do-not-touch list.

| Pattern | Total files with remaining matches | Where they live | Verdict |
|---|---|---|---|
| `tcg-watcher-worktree` | 35 | `migration_preview`, `migration_log`, `package-lock.json` (deferred to Phase 7), `data/overlays/**` (11), `archive/**` (20), `MIRU_MIGRATION_AUDIT.md` | ✅ all allowed |
| `100.104.150.125` | 3 | `migration_preview`, `migration_log`, `MIRU_MIGRATION_AUDIT.md` | ✅ all allowed |
| `nas.taila28611.ts.net` | 2 | `migration_preview`, `migration_log` | ✅ all allowed |
| `F:\OPTCG_Images` (backslash) | 7 | `migration_preview`, `migration_log`, `pm/routes/pages.py` (intentional comment), `archive/**` (3), `MIRU_MIGRATION_AUDIT.md` | ✅ all allowed |
| `F:/OPTCG_Images` (forward slash) | 7 | `migration_preview`, `data/overlays/repo_cleanup_inventory.csv`, `archive/**` (5) | ✅ all allowed |

**Zero matches in any approved-scope (§1 or §2) file.** Clean sweep.

## 7. Skipped items — recap

- **§1 Item 6** — `miru_ai/evidence_collectors.py:28` — verified as `Path(r"D:\Miru_Assets")`, NO CHANGE required (D: stays D:).
- **§1 Item 8 (file portion)** — `.claude\settings.local.json` — no recoverable copy anywhere in `D:\miru-migration\`; not synthesized per Captain's directive.
- **§1 Item 9** — folder rename completed in Phase 2.
- **§2.8** — `package.json` has no `"name"` field to rewrite; `package-lock.json` deferred to Phase 7 per Captain.
- **§2.9** — `pm/routes/pages.py:83` — comment "no F:\OPTCG_Images fallback" intentionally preserved.
- **§2.10** — archive/, data/overlays/, MIRU_MIGRATION_AUDIT.md, old `governed_*` batch reports — historical, not rewritten.

## 8. Git commit + tag

_Filled in after the commit lands — see §9._

## 9. Next steps

1. **Pause here.** Awaiting Captain's OK before Phase 5 (assets copy).
2. **Phase 5 (when greenlit):**
   - `D:\miru-migration\assets\Miru_Assets\*` → `D:\Miru_Assets\`
   - `D:\miru-migration\assets\OPTCG_Images\*` → `D:\OPTCG_Images\`
   - Spot-check against `D:\miru-migration\manifest\sha256.txt` for file-count + random-hash integrity.
3. **MCP restart expectation after Phase 4:** the path corrections in `.mcp.json` mean the next Claude Code restart should see `git` MCP fully healthy (already was), `sqlite-ro-snapshot` MCP's `--db` arg now points at the correct post-Phase-6 path (still ❌ until Phase 6 places the DB), and the 3 PowerShell-wrapper MCPs (notion / youtube / magic-ui) continue to resolve `.env` at the correct new location.

---

**Phase 4 status: COMPLETE — awaiting Captain review before Phase 5.**
