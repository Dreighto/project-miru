# Canon Sweep — 2026-05-17

Scoped sweep run after tonight's project-miru pool migration (Phases A–D
plus cosmetic). Audited the surfaces NOT touched by the migration: project-miru
side canon, tactical memory DB, state-handoff-log, kernel AGENTS.md, and a
spot-check of older personal memory entries.

## Sources checked

- **Runtime:** scheduled-task inventory (live `Get-ScheduledTask` output, all
  `LogueOS-*` naming).
- **Linear:** not pulled (not in scoped surface; deferred to next full sweep).
- **File canon:** 6 files read in full — `D:\dev\miru\CLAUDE.md`,
  `D:\dev\miru\AGENTS.md`, `D:\dev\miru\miru-context\miru-service-catalog.md`,
  `D:\dev\miru\miru-context\miru-protected-constraints.md`,
  `D:\dev\miru\miru-context\miru-vocab.md`,
  `D:\dev\LogueOS-Orchestrator\.logueos\context\state-handoff-log.md`,
  `D:\dev\LogueOS-Orchestrator\AGENTS.md`.
- **DB memory:** `data\logueos_memory.db` tables `observations` (48 rows),
  `lessons` (3 rows), `provisional_lessons`, `usage_events`, `watchdog_state`
  scanned for stale path / retired-worker references.
- **Personal memory:** 4 older entries spot-checked.

## Critical drift (4 findings)

### 1. miru-protected-constraints.md Section 4 contradicts live policy

`D:\dev\miru\miru-context\miru-protected-constraints.md:78-95` (last updated
2026-05-01) says `card_catalog.db` is "Read-Only for Workers" with direct
writes "prohibited." This DIRECTLY CONTRADICTS the live policy in project-miru
`CLAUDE.md:138`, which states (effective 2026-05-17, operator-set):

> `card_catalog.db` writes are in scope when work requires them — set
> population (OP01–OP15), provenance backfills, meta-relevancy / insight
> columns, image-asset linkage. The earlier never-touch rule was situational
> to the schema-setup-and-initial-population phase and is no longer in force.

This is a "non-negotiable architectural constraints" file — a worker reading
it before touching the DB will refuse work that the operator has explicitly
authorized. Per source-of-truth hierarchy (Runtime > Audit > Linear > Repo),
the live CLAUDE.md wins; protected-constraints.md must update.

**Proposed fix:** rewrite Section 4 to mirror the CLAUDE.md language (DB
writes in scope for set population / provenance / insights, with the backup +
log + diff-in-commit discipline). Refresh "Last updated" to 2026-05-17.

### 2. miru-service-catalog.md references task names that no longer exist

`D:\dev\miru\miru-context\miru-service-catalog.md` (last updated 2026-05-02)
documents restart procedures using `Miru*` scheduled-task names. Live task
list (just verified via `Get-ScheduledTask`) shows the de-Miru rename is
already done — current names are `LogueOS-*`:

| Stale ref in catalog (line)             | Live task name                         | Status         |
| --------------------------------------- | -------------------------------------- | -------------- |
| `MiruRestartDispatchListener` (line 89) | (none — listener launched via Startup) | broken         |
| `MiruRestartMCPGateway` (line 148)      | `LogueOS-RestartMcpGateway`            | renamed        |
| `MiruRestartMiruAI` (line 210)          | (not in live list)                     | broken/removed |
| `MiruRestartPM` (line 273)              | (not in live list)                     | broken/removed |
| `MiruN8nWatchdog` (line 363)            | `LogueOS-ServiceWatchdog`?             | likely renamed |

Workers following these restart instructions will invoke task names that
don't exist. A worker who needs to restart Miru AI will hit a dead end.

**Proposed fix:** update all task names in the service catalog to match the
live `LogueOS-*` names. Verify which services actually still have dedicated
restart tasks vs which were consolidated into `LogueOS-Startup` /
`LogueOS-ServiceWatchdog`. Refresh "Last updated."

### 3. miru-service-catalog.md lists Codex as an active worker

Same file, line 35 ("Spawns worker processes (claude-code, codex, gemini)")
and line 154 ("The primary Python service workers (Claude Code, Codex) write
backend code here") — Codex was retired from the loop on 2026-05-12 per
memory `feedback_loop_workers.md`. Workers reading this catalog still think
Codex is an option.

**Proposed fix:** remove Codex mentions; replace with `claude-code` +
`gemini` (current loop allowlist).

### 4. state-handoff-log.md latest entry says LOS-74 is still pending

`D:\dev\LogueOS-Orchestrator\.logueos\context\state-handoff-log.md:335` (the
latest active handoff section) lists LOS-74 worker-slot rename as still open.
That work shipped today (2026-05-17) — see commits `55bdd13c`, `bb92e588`,
`0705b873`, `55b267b2` on Orchestrator main. The next worker reading this
handoff will start with a false belief that LOS-74 is queued.

(The five earlier mentions on lines 255, 267, 291, 335, 346 are historical
handoff snapshots — fine to leave as period-correct records. Only the latest
section needs the update.)

**Proposed fix:** append a new handoff entry at the bottom dated 2026-05-17
documenting the project-miru pool migration as shipped + what's truly open
now (Console P5 Settings tab, Hermes Phase 2, NASDOOM minor fixes, etc.).

## Stale references (2 findings)

### 5. miru-protected-constraints.md Section 2 lists wrong append-only files

`miru-protected-constraints.md:38-58` lists 5 append-only files as living in
miru `data/`. Per kernel CLAUDE.md ("Append-Only Data Files") there are 12
files, and per project-miru CLAUDE.md:91-95 these now LIVE IN THE ORCHESTRATOR
at `D:\dev\LogueOS-Orchestrator\data\` (since LOS-55 Migration Phase 3,
2026-05-14). The only file that stays miru-side per current CLAUDE.md is
`data/miru_worker_runs.jsonl`, which isn't listed at all.

**Proposed fix:** rewrite Section 2 to point at the kernel list and explicitly
call out `miru_worker_runs.jsonl` as the only miru-side append-only file.

### 6. miru-vocab.md references retired DB name

`D:\dev\miru\miru-context\miru-vocab.md:42` — "Log this" action maps to
"Write to `miru_memory.db`". The DB was renamed to `logueos_memory.db` (the
de-Miru kernel rename). Cosmetic but mis-teaches workers.

**Proposed fix:** s/miru_memory.db/logueos_memory.db/.

## Promotion candidates (0 findings)

Nothing in this sweep's scope met promotion bar.

## Demotion candidates (0 findings)

Nothing in this sweep's scope met demotion bar. (state-handoff-log historical
entries were considered but they're a journal — superseding via newer entries
is the right pattern, not demotion.)

## What's actually clean

For the record — surfaces audited that came back drift-free:

- **Kernel `AGENTS.md`** — fully read; references CC + CH workers correctly,
  no stale path references, no retired tooling.
- **project-miru `CLAUDE.md`** — uses new `D:\dev\worktrees\project-miru\w{N}`
  paths throughout. Updated as part of v3 architecture stamp (2026-05-13).
- **project-miru `AGENTS.md`** — clean; defers to kernel AGENTS.md correctly.
- **Tactical memory DB** (`logueos_memory.db`):
  - `observations` table — 0 stale path references; 1 incidental Codex mention.
  - `lessons` table — 0 stale references.
  - `watchdog_state` — live, currently tracking 5 workflows incl. one
    `failing` (W2 Worker Selection Router) and one `silent` (W2 Pending-Approval
    Watchdog). Note: those are NOT canon-sweep findings — they're live
    service health signals worth a separate look.
- **Personal memory** older entries spot-checked — no broad drift detected.

## Watchdog signals worth a separate look

(Out of canon-sweep scope but surfaced during DB inspection.)

- `W2 Worker Selection Router` — status `failing` since 2026-05-16 18:57 UTC.
- `W2 Pending-Approval Watchdog` — status `silent` since 2026-05-16 15:00 UTC.

If those are real failures and not just stale `watchdog_state` rows, the n8n
W2 lane has been broken for ~36 hours. Flag for the operator.

## Next sweep due

2026-05-20 (3-day cadence) OR after the next major ship — whichever comes first.

Personal memory entries already auto-updated tonight as part of migration work:
`project_miru_pool_los14_migration.md` (new), `project_multi_agent_intent.md`
(refreshed). No additional auto-fixes applied during this sweep — all canon
findings above need operator approval before edit.
