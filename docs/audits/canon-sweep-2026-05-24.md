# Canon Sweep — 2026-05-24

Triggered by operator: "CH reports all canon is stale. CH should boot with full
context. its memory DB should be something it should read from and constantly
read and write to when booting and during active work to not operate on stale
data on next thread. it suggested the kernel was slowly rotting only after
reading the latest linear tickets did it say the data was stale and not
current."

Last full sweep: 2026-05-17 (`canon-sweep-2026-05-17.md`). Cadence rule is
72h or after every major ship — overdue by ~4 days. Today's PR #259 ship
(shadow-loop validator-answer recording) is the prompting "major ship".

## Sources checked

- **Runtime** — `Get-ScheduledTask` (15 LogueOS-\* tasks all Ready, 1
  MiruFlaskHeartbeat), `Get-NetTCPConnection` (listening ports), worktree
  inventory (`D:\dev\worktrees\<repo>\w<N>` layout intact).
- **Linear** — issues across PRO/LOS/NAS in Triage/Todo/Backlog/In Progress/In
  Review states. PRO has 9 Backlog tickets surveyed; LOS has 1 (LOS-110);
  NAS dormant per cycle.
- **File canon** — kernel (`LogueOS-Orchestrator/CLAUDE.md`, `AGENTS.md`,
  `.logueos/overlays/`, `.logueos/reference/`, `.logueos/context/`),
  project-miru (`CLAUDE.md`, `AGENTS.md`, `CLAUDE_CHAT.md`,
  `miru-context/*`), LogueOS-Console (no kernel canon files present —
  inherits from Orchestrator).
- **DB memory** — `LogueOS-Orchestrator/data/logueos_memory.db` tables:
  `lessons` (3 rows), `observations` (76 rows), `provisional_lessons` (45),
  `synthesis_consumed` (67), `usage_events` (140), `watchdog_state` (9).
- **Personal memory** — `~/.claude/projects/D--dev/memory/MEMORY.md` plus
  body files (113 entries total).

## Critical drift (4 findings — proposed, do not auto-fix)

### 1. `.miru/` path references in skills + CI workflow are dead

The kernel extraction (2026-05-11, per memory `project_logueos.md`) moved
canon from `D:\dev\miru\.miru\overlays\` and `\.miru\reference\` to
`D:\dev\LogueOS-Orchestrator\.logueos\overlays\` and
`\.logueos\reference\`. The miru-side `.miru/` directory was retired. But
not every consumer was updated:

| File                                                                                                                                                 | Issue                                                                                                                                                                                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `D:\dev\miru\.claude\skills\dgas-implementation\SKILL.md`                                                                                            | 9 refs to `.miru/overlays/workflow-git.md`, `.../workflow-completion.md`, `.../adopted-lessons.md`. The skill will fail to find canon when invoked.                                                         |
| `D:\dev\miru\.claude\skills\locked-design-ticket\SKILL.md`                                                                                           | 7 refs to `.miru/overlays/*`. Same failure mode.                                                                                                                                                            |
| `D:\dev\miru\.github\workflows\canon-freshness.yml`                                                                                                  | Lines 14–15 trigger on path changes under `.miru/overlays/**` and `.miru/reference/**`. Those paths don't exist, so the workflow **never fires** — canon drift goes undetected by CI. This is the meta-rot. |
| `D:\dev\miru\.github\workflows\governance-check.yml`                                                                                                 | Line 4 comment lists `.miru/overlays` as a gated path (just a comment, but misleading).                                                                                                                     |
| `D:\dev\miru\tools\check_canon_freshness.py`                                                                                                         | Lines 39, 64–65 — comments document the rename but inspect the code path to confirm it's not still scanning the dead dir.                                                                                   |
| `D:\dev\miru\tools\git-hooks\pre-push`, `tools\git-hooks\README.md`, `data\templates\cr-fix-worker-prompt.md`, `data\templates\multi-repo\README.md` | Lesser refs in tooling/templates.                                                                                                                                                                           |

**Proposed fix:** one PR titled `chore(canon): retire .miru/ path refs after kernel extraction`. Search-replace `.miru/overlays/` → `D:\dev\LogueOS-Orchestrator\.logueos\overlays\` and `.miru/reference/` → `D:\dev\LogueOS-Orchestrator\.logueos\reference\` across the 9 files. Critically, update `canon-freshness.yml` paths so CI actually triggers on canon edits going forward. **This is the single most important finding** because it's the reason future drift will go undetected.

### 2. Port 18765 (Flask backend) — runtime contradicts canon

`D:\dev\LogueOS-Orchestrator\.logueos\reference\ports-and-services.md:16` says
18765 is **ACTIVE** ("Miru AI — Flask backend API"). Runtime check via
`Get-NetTCPConnection -State Listen` shows 18765 is **NOT listening**.
`MiruFlaskHeartbeat` scheduled task exists but is silently failing or paused.

Downstream consequence: the SvelteKit Hub UI on 18768 calls 18765 via BFF
(`src/lib/server/flask.ts`) — Glance loader hits 18765 and gets timeouts
(matches PRO-939 "6.3s response blocks the dev page" — and worse, a hard
failure when Flask is fully down).

**Proposed fix:**

- (a) If Flask is supposed to be up: restart via `windows\restart_miru_ai.ps1` and investigate why the heartbeat task isn't keeping it alive.
- (b) If Flask is intentionally paused: update `ports-and-services.md:16` to **PAUSED** (mirroring the 18080 row) and disable `MiruFlaskHeartbeat` so it stops generating false-active signal.

Operator decision needed.

### 3. `MiruRestartMiruAI` task in canon, not in live task list

`D:\dev\LogueOS-Orchestrator\.logueos\reference\restart-procedures.md:22` and
line 164 reference scheduled task `MiruRestartMiruAI`. Live
`Get-ScheduledTask` shows no such task. The de-Miru rename moved everything
to `LogueOS-*` but this entry wasn't renamed. The 2026-05-17 sweep already
flagged this as `MiruRestartDispatchListener`/`MiruRestartMCPGateway`/etc.
in the miru service catalog — and that catalog got updated. But the
**kernel reference** (which is canon for restart procedures) still has the
stale name on line 22 + 164.

**Proposed fix:** update `restart-procedures.md:22` and line 164 — either rename to `LogueOS-RestartMiruAI` if the task should exist, or delete the row if the service is being retired (interacts with finding #2).

### 4. CH boot doc + live memory DB pipeline is stalled — root of CH staleness

`D:\dev\LogueOS-Orchestrator\.logueos\context\ch-tool-operations.md` already
defines canon correctly:

> "Canon definition: 'Canon' means Notion pages and memory DB — the persistent
> truth surfaces. Repo files are repo files, not canon. When verifying or
> updating canon, that means Notion and the `logueos_memory` DB."

And tells CH "Read this on every session start." So the **boot protocol is
in place** — but the underlying data pipeline that's supposed to keep the
memory DB live has stalled:

| Table                 | Count | Latest                    | Health               |
| --------------------- | ----- | ------------------------- | -------------------- |
| `observations`        | 76    | 2026-05-24T15:29Z (today) | ✅ Live              |
| `usage_events`        | 140   | 2026-05-24T16:38Z (today) | ✅ Live              |
| `watchdog_state`      | 9     | 2026-05-24T17:41Z (today) | ✅ Live              |
| `provisional_lessons` | 45    | 2026-05-22T04:54Z         | ⚠ Two days stale     |
| **`lessons`**         | **3** | **2026-05-14T18:18Z**     | **❌ 10 days stale** |
| `synthesis_consumed`  | 67    | (no ts col)               | unknown              |

The synthesizer pipeline (`synthesized_by='synthesizer-v2'`) is generating
provisional_lessons but **none are getting promoted to `lessons`** since
2026-05-14. CH reads the `lessons` table on boot to anchor its world model —
but that table is frozen 10 days behind reality. The 45 provisional_lessons
include observations about today's session work that CH would otherwise
benefit from. Two watchdogs are also showing yellow (`W2
Pending-Approval Watchdog`: silent, `W1 Error Handler`: unstable, 3 failures
in 24h) — could be related to the synthesizer pipeline.

**Proposed fix:** see the "CH memory architecture" section below — it's the
operator-requested deep-dive.

## Stale references (6 findings)

### 5. PRO-957 superseded by today's PR #259 + PRO-961

PRO-957 ("Wire multi-verifier evidence into shadow_review before retraining",
Backlog, High priority, 2026-05-23) describes the same architectural gap that
PR #259 (validator_answer recording, merged 17:13 UTC today) just closed,
with the remaining Bandai-expansion work captured in PRO-961.

**Proposed fix:** close PRO-957 with a Linear comment linking PR #259 and PRO-961.

### 6. LogueOS-Console has no project-level canon — depends on Orchestrator implicitly

`D:\dev\LogueOS-Console\` has no `CLAUDE.md`, `AGENTS.md`, or `.logueos\`
directory at root. Workers dispatched into Console will only see the kernel
canon from Orchestrator (if the kernel boot path works — see PRO-903) or
nothing. This is silent canon — no failure today because no Console-specific
overlay exists, but workers can't tell if that's "no rules" or "rules not
loaded."

**Proposed fix:** Either add a tiny `CLAUDE.md` at Console root saying "this
repo has no project-specific overlay; kernel canon from
`D:\dev\LogueOS-Orchestrator\` applies" — OR add to MEMORY.md so workers know
the absence is intentional. Low priority.

### 7. PRO-924 / Flask cleanup ticket aging — depends on finding #2

PRO-924 ("Drop legacy HTML dev-page routes from server.py", Backlog,
2026-05-19) is downstream of the Flask 18765 active/paused decision. If
Flask is being retired, this ticket changes shape (delete more, not less).

**Proposed fix:** revisit after finding #2 is resolved.

### 8. PRO-903 — kernel boot-path fix still blocked

Long-standing tech debt: dispatched workers in worktrees don't pull kernel
`CLAUDE.md` + `AGENTS.md` because `dispatch_listener/src/index.js` doesn't
prepend kernel canon to the dispatch prompt. As a result `D:\dev\miru\CLAUDE.md`
duplicates kernel sections "as a load-bearing safety net" (explicit note in
that file).

**Proposed fix:** elevate priority. Until this lands, EVERY canon update has
to be made in two places (kernel + project overlay duplicate) or workers run
on stale rules. **This is the second-most-important finding after #1.**

### 9. Personal memory entry fixed during sweep

`feedback_dispatch_prompt_required_clauses.md` body referenced
`.miru/overlays/adopted-lessons.md`. The path was retired in Phase 5 of the
kernel extraction. **Auto-fixed during this sweep** to
`D:\dev\LogueOS-Orchestrator\.logueos\overlays\adopted-lessons.md` per the
canon-sweep skill's personal-memory hygiene rule.

### 10. canon-sweep skill itself has a stale `.miru/` reference

The skill (`C:\Users\Dreighto\.claude\skills\canon-sweep\SKILL.md`) Pass 3
instructions say "read `.miru/overlays/*.md` and `.miru/reference/*.md`" —
but those paths were retired. The skill works because it falls back to
miru-context, but the documented expectation is wrong.

**Proposed fix:** update SKILL.md Pass 3 to drop the `.miru/` line and add
"miru-context/\*.md".

## Promotion candidates (2)

### 11. Tier-0 observation worth emitting: today's shadow-loop sweep

Two operator-validated lessons surfaced in this session that aren't yet in
the memory DB:

1. **Verifier transparency rule** — "When recording verifier outcomes per
   field, all participating-source values must be recorded for ALL fields
   regardless of which fields have an authoritative second source. A
   Bandai-only gating decision left validator answers dark on 10 of 12
   fields, looking like a verifier outage." (Origin: PR #259.)
2. **Orphan-process recipe for shadow_loop restart** — "`Get-CimInstance` with
   a CommandLine filter misses python.exe processes whose CommandLine is
   blank in WMI (orphaned by parent shell death). Always also enumerate
   empty-CommandLine python.exe and inspect parent process." (Origin:
   today's restart, captured into `reference_shadow_loop_restart.md`.)

**Proposed action:** emit both as observations via `emit_observation.py` so
the synthesizer-v2 picks them up. (Once #4 / synthesizer pipeline is
unblocked.)

### 12. Today's shadow_loop fix Linear ticket lineage

PRO-961 was filed today with the full Bandai-expansion proposal. After it
lands, the related canon updates (BANDAI_FIELDS reference, stage3_autoclear
behavior under wider coverage) should land as one consolidated kernel
reference doc — a candidate for `learning-layer-brief.md` extension.

## Demotion / archive candidates (1)

### 13. `project_brainstorm_backlog.md` — historical snapshot

MEMORY.md index marks this as "(HISTORICAL)" with 2026-05-09 snapshot. 15+
days old, kept "for provenance." It's already labeled — but the operator
should decide if it should be archived to `_archive/` and dropped from the
index to reduce memory load.

**Proposed action:** archive after operator review.

## CH memory architecture — operator's deep-dive ask

Captain's framing:

> "CH's memory DB should be something it should read from and constantly
> read and write to when booting and during active work to not operate on
> stale data on next thread."

### Current state — infrastructure is mostly in place

CH (Claude Chat on claude.ai) already has:

1. **Boot doc** — `D:\dev\LogueOS-Orchestrator\.logueos\context\ch-tool-operations.md`
   explicitly says "Boot context for Claude Chat. Read this on every session
   start." CH's project memory in Anthropic's project should be set to load
   this on every conversation. (Operator to confirm.)
2. **Canon definition** — that doc tells CH "Canon means Notion + memory DB,
   not repo files." That framing is correct.
3. **MCP tool access** — CH has the `claude.ai LogueOS Gateway` MCP connector,
   which exposes:
   - `fs_read_text_file` / `fs_read_multiple_files` — live repo reads
   - `read_query` / `list_tables` / `describe_table` — live memory DB reads
   - `write_query` — live memory DB writes (this is the key one for active-work writes)
   - `linear_get_issue` / `linear_list_labels` — live Linear reads
   - `linear_create_issue` / `linear_add_comment` / `linear_update_issue_state` — live Linear writes
4. **Notion MCP** — `claude.ai Notion` connector for live read/write of Notion canon.

So **the hardware exists**. The reason CH felt stale is not infrastructure —
it's discipline + a stalled synthesizer pipeline.

### Why CH still felt stale

Three things, in priority order:

1. **`lessons` table is frozen 10 days behind** (finding #4). CH boots, looks
   at the `lessons` table for tactical guidance, and sees 2026-05-14 data.
   The 45 provisional_lessons from the last 10 days never got promoted, so
   from CH's view, nothing has happened. THIS is the root cause of "kernel
   slowly rotting."
2. **CH does not actively poll Linear / current_lane.md at session start.**
   The boot doc lists tools but doesn't say "before forming an opinion on
   anything, fetch the latest from Linear + current_lane + recent lessons."
   CH operates on the Anthropic-side project memory unless prompted.
3. **CH writes are end-of-session, not in-session.** When CH adopts a new
   convention or makes a decision mid-session, that decision lives only in
   the chat transcript — not in the memory DB. Next session, CH has to be
   re-told.

### Proposed fix — three layers

**Layer A (data — high priority):** unblock the synthesizer pipeline so
provisional_lessons → lessons promotion resumes. The synthesizer is named
`synthesizer-v2` per the rows; find its scheduled task / cron / trigger and
verify it's running. (Per the watchdog table, the `W1 Error Handler` is
"unstable" with 3 failures in 24h — may be related.)

**Layer B (boot protocol — medium):** update
`.logueos/context/ch-tool-operations.md` to add a "Session-start protocol"
section at the top:

```
1. fs_read_text_file('D:/dev/LogueOS-Orchestrator/.logueos/context/current_lane.md')
2. read_query("SELECT title, advice, plain_english_summary FROM lessons
   WHERE last_referenced_at IS NULL OR
         last_referenced_at < datetime('now','-7 days')
   ORDER BY confidence_score DESC LIMIT 10")
3. linear_list_issues(team='PRO', state='In Progress', limit=10)
4. linear_list_issues(team='LOS', state='In Progress', limit=10)
5. fs_read_text_file(most_recent canon-sweep-*.md in docs/audits/)
```

That's ~5 tool calls before any operator-facing output. Cost: a few seconds

- a few cents per session, in exchange for never starting on stale context
  again.

**Layer C (in-session writes — medium):** add an "Active-work write rule"
to the boot doc — when CH adopts a convention, files a lesson, or makes an
architectural decision, write to the memory DB immediately via
`write_query` (preferably to `observations` if it's a new finding, or
`provisional_lessons` if synthesized). Don't wait for end-of-session.

### Layer D — Captain's specific phrasing

> "its memory DB should be something it should read from and constantly read
> and write to when booting and during active work"

Layer A + B + C above accomplish that exactly. The DB already exists, the
write tool exists, the read tool exists — the missing piece is the
**protocol**, and the synthesizer pipeline stall is the dominant reason the
DB looks stale.

## Action checklist

Ordered by operator effort vs payoff:

- [ ] **(highest payoff)** Restart / debug the synthesizer-v2 pipeline so
      `provisional_lessons` → `lessons` promotion resumes. Investigate
      `W1 Error Handler` watchdog instability while there.
- [ ] **PR — retire `.miru/` path refs across 9 files** (finding #1). Most
      important because `canon-freshness.yml` CI is currently no-op'd, which
      will let future drift go silent.
- [ ] Update `ch-tool-operations.md` with the Session-start protocol +
      Active-work write rule (Layer B + C above).
- [ ] Decide on Flask 18765 — restart or mark paused (finding #2). Update
      canon either way.
- [ ] Update `restart-procedures.md:22,164` — rename or remove
      `MiruRestartMiruAI` (finding #3).
- [ ] Close PRO-957 as superseded by PR #259 + PRO-961 (finding #5).
- [ ] Update canon-sweep skill SKILL.md to drop `.miru/` references
      (finding #10).
- [ ] After PRO-961 lands: revisit `learning-layer-brief.md` to fold in the
      consolidated multi-source-verification doctrine (finding #12).
- [ ] Operator-decision: archive `project_brainstorm_backlog.md`?
      (finding #13)
- [ ] **Long-running:** PRO-903 / kernel boot-path fix. Until that lands,
      every canon update has to be made twice.

## Next sweep due

**2026-05-27** (+3 days) — or earlier if any major ship lands.
