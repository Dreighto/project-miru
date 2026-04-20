# Migration Complete — NAS → ROOM

**Migration ID:** 2026-04-18
**Authoritative plan:** Notion — [🚚 15 Migration — NAS to ROOM](https://www.notion.so/346c5d34014181ac8388c02f74f79fcd)
**Full phase-by-phase record:** [migration_log_2026-04-18.md](migration_log_2026-04-18.md)

## Status

**COMPLETE.** All 11 phases executed. All phone-side verifications passed on first try. Zero migration regressions. Services live on ROOM and reachable from Captain's phone via Tailscale. NAS remains operational as the rollback safety net until Captain decommissions it in a separate, explicit task.

## Hosts

| Role | Host | Tailscale MagicDNS | Drive layout |
|---|---|---|---|
| Source (old, still live) | `NAS` | `nas.taila28611.ts.net` | `D:\dev\tcg-watcher-worktree`, `F:\OPTCG_Images` |
| Destination (new, now live) | `ROOM` | `room.taila28611.ts.net` | `D:\dev\miru`, `D:\Miru_Assets`, `D:\OPTCG_Images` |

## Service endpoints (post-migration, reachable via Tailscale)

- **PM Dashboard** — `http://room.taila28611.ts.net:18080/`
- **Miru AI** — `http://room.taila28611.ts.net:18765/`
- **Task Dispatcher** — `http://room.taila28611.ts.net:19000/`

All three respond 200 OK on localhost AND on Tailscale. All three verified rendering correctly on Captain's phone.

## Phase-by-phase checkpoint record

| Phase | Scope | Tag | Status |
|---|---|---|---|
| 1 | Claude Code MCP install + worker config file rewrites on ROOM | — (no tag; merged into Phase 2 commit) | ✓ |
| 2 | Repo copy + rename (`tcg-watcher-worktree` → `miru`) at `D:\dev\miru` | `migration-phase-2` | ✓ |
| 3 | Secrets (`.env`, Cursor/project MCP configs) in place | — (no tag) | ✓ |
| 4 | Path / hostname / IP rewrites (dry run preview → execute) | `migration-phase-4` | ✓ |
| 5 | Assets copied: `D:\Miru_Assets` (3,389 files, 1.27 GB) + `D:\OPTCG_Images` (14,022 files, 22.5 GB) | `migration-phase-5` | ✓ |
| 6 | Data copied (DBs, snapshots, tcgcsv) — 180 files, 181 MB, canonical DB hashes verified | `migration-phase-6` | ✓ |
| 7 | Python 3.14 + CLIs + pip parity + 26 env vars — tooling sanity sweep | `migration-phase-7` | ✓ |
| 8 | 3 firewall rules + 4 scheduled tasks registered (OP Miru Startup, Miru Nightly Backup [disabled], Miru Worker [disabled], MiruTaskDispatcher) | `migration-phase-8` | ✓ |
| 9 | Services started; Tailscale verified from ROOM and from Captain's phone; UTF-8 BOM fix applied to 15 `.ps1` files (PS 5.1 CP1252 parse blocker); `MiruRestart{PM,MiruAI,Dispatcher}` tasks registered; `data/overlays/asset_job_pointer.txt` late Phase-4 touch-up | `migration-phase-9` | ✓ |
| 10 | MCP verification: Claude Code live-verified 4 previously-red MCPs (sqlite-ro-snapshot, notion, youtube, magic-ui); Cursor / Codex / Gemini / Copilot / Windsurf config-audited; Dispatcher Files tab root confirmed at `D:\dev\miru` | `migration-phase-10` | ✓ |
| 11 | Full-stack integration test via phone; Dispatcher → Claude → Slack full-loop live test; `MIGRATION_COMPLETE.md` written | `migration-phase-11` | ✓ |

## What the migration actually delivered

- Working repo at `D:\dev\miru` on branch `phase3-console-2` (same branch NAS was on). HEAD = Phase 11 commit.
- All three services running and auto-starting on boot via registered Windows scheduled tasks (ROOM\Dreighto SID; S4U for startup, Interactive/Limited for on-demand restarts — no UAC prompts).
- Firewall rules scoped to Private profile only on TCP 18080 / 18765 / 19000.
- All paths in code, config, `.env`, `.mcp.json`, and `.gemini/settings.json` rewritten from `D:\dev\tcg-watcher-worktree` → `D:\dev\miru`, and from `F:\OPTCG_Images` → `D:\OPTCG_Images`.
- DB (`card_catalog.db`) reachable by Miru AI with 2,497 cards / 51 sets / 5,413 variants indexed.
- Leader crops served correctly from `D:\Miru_Assets\leader_crops\` via PM's `/static/assets/thumbs/leader_crops/...` route.
- Dispatcher Files tab rooted at `D:\dev\miru\` with zero `tcg-watcher-worktree` references anywhere.
- Slack wiring live: Dispatcher → Claude → Slack notification channel `C0ASSN9JULW` tested end-to-end with a real job.
- Claude Code MCPs: previously-red `sqlite-ro-snapshot`, `notion`, `youtube`, `magic-ui` all green on ROOM.
- 26 / 26 required env vars set at user scope (secrets SET, non-secrets echo clean).

## Follow-up items (non-blocking, deferred to post-migration threads)

Sorted by who picks them up, not by severity. Every one of these is tracked with enough breadcrumbs in [migration_log_2026-04-18.md](migration_log_2026-04-18.md) to resume without this session's context.

### Captain's manual worker eyeball-verification (post-migration, at leisure)

- Launch Cursor in `D:\dev\miru`, confirm project-level MCPs load.
- Run `codex` in a shell, confirm clean launch (no user MCPs by design).
- Run `gemini` in `D:\dev\miru`, confirm the 8-MCP intentional subset loads.
- Launch VS Code, confirm Copilot is signed in (no MCP expected).
- Launch Windsurf once to create its config dirs, then decide whether to deploy `D:\Drop-In\windsurf\` backup (with any needed NAS→ROOM rewrites).

### Cleanup items (not migration failures — just residue)

- **`tools\miru_backup.ps1`** is missing from the repo. `Miru Nightly Backup` scheduled task is registered with the correct future path but stays `Disabled`. Restore or recreate the script, then enable the task.
- **`RunMiruAssetJob`** scheduled task not recreated — no installer exists, and no XML was in either backup source. Captain to decide: author a new installer, accept manual runs, or recover XML from NAS.
- **Cursor user-level MCP** not deployed. Backup at `D:\Drop-In\cursor\dot_cursor\mcp.json` has old paths + inlined API keys — do not deploy verbatim. Captain's call: scrub-and-deploy or skip entirely (project-level `.mcp.json` already covers miru-repo work).
- **VS Code user `settings.json`** is empty on ROOM; backup exists in `D:\Drop-In\vscode\User\settings.json`. Outside MCP scope, but available when Captain wants it.
- **`.claude/settings.local.json`** not present; `.claude/` is gitignored; Claude Code regenerates the file on first local setting change. No rewrite needed.

### Pre-existing polish (NOT migration-related — present on NAS too)

Parked until a dedicated UI pass. Captain observed during Phase 11 smoke testing:

- PM mobile: card-grid image loading performance could be better.
- PM mobile: minor scroll behavior glitch (repro detail TBD).
- Dispatcher Files tab (iOS): bottom nav occasionally appears mid-list during scroll — likely `position:fixed` + `transform:translateZ(0)` interaction.
- Dispatcher Files tab: batch download bar (`#fb-batch-bar`) does not slide up when files are multi-selected via checkbox.

### Phase-4-era rewrite residue

- None currently outstanding. The known miss (`data/overlays/asset_job_pointer.txt`) was fixed inside the Phase 9 commit.
- If any runtime code later trips on a stale `tcg-watcher-worktree` path, it's fair game for a targeted one-line fix — the Phase 4 rewrites covered audit items 1–8 and the `.mcp.json` sweep.

## Rollback posture

- Every phase is an annotated tag on `phase3-console-2`. Roll back by checking out the previous tag — e.g., `git checkout migration-phase-10` to rewind past Phase 11.
- NAS is untouched throughout. Services on NAS are still running. Captain's original phone-access URLs pointing at NAS remain live. Only after Captain explicitly decommissions NAS (separate, explicit task) does the rollback net go away.

## Next-thread queue (per Notion migration page §Anchor for post-migration thread)

1. OP01 verification audit (on ROOM).
2. Notion path reference update across the MIRU hub.
3. NAS decommission (runs on NAS, preserves Plex / HTPC stack).
4. Refactor + `.gitignore` audit + clean push — only **after** NAS decommission.

## Credits

- **Captain (Dreighto):** migration planning, phase approvals, phone-side smoke testing.
- **Claude Code (this session, on ROOM):** execution, logging, commits, phase tagging, final push.
- **NAS:** not touched. Thanks for being the safety net.
