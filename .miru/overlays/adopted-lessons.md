# Overlay — adopted-lessons

```text
Overlay: adopted-lessons
Architecture: MIRU-INSTRUCTIONS-v2
Load when: doing a non-trivial code change (more than typo or lint).
Last reviewed: 2026-05-11
```

Lessons promoted from Provisional to Adopted via the Lesson Promotion
Discipline (Notion canon, 2026-04-28). These are battle-tested patterns that
prevent specific failure modes we've already hit.

---

## Test the JS as it lives in the workflow JSON (PRO-189 retro, adopted 2026-04-28)

When testing JavaScript embedded in workflow JSON files (e.g. `docker/n8n/workflows/*.json`), the test MUST:

1. Load the JSON file from disk via `fs.readFileSync` and `JSON.parse`.
2. Extract the `jsCode` string from the relevant node.
3. Eval it as JS via `new Function(jsCode)` or `vm.Script(jsCode)` to confirm it parses without `SyntaxError`.
4. Exercise the algorithm against that loaded code path — NOT a clean extracted copy of the algorithm.

**Why this is a hard rule:** PRO-160 shipped with two latent bugs (SyntaxError from a literal newline inside a string literal, and a missing `$getWorkflowStaticData('global')` call). PRO-160's tests passed because they imported a clean copy of the diff function and exercised it directly. The deploy-time mangling and the embedded-newline bug both happened at the boundary between "JS source in the JSON file" and "JS that n8n actually runs," and the tests were structurally unable to see across that boundary. The watcher crashed on every poll for 12 minutes in production before being deactivated.

PRO-189 added the boundary-crossing test, which catches both bug classes and any future deploy-pipeline mangling.

**Applies to:** any change to a workflow JSON file under `docker/n8n/workflows/` that touches a `jsCode` field.

---

## Lock design in the Linear ticket description, not in the prompt wrapper (PRO-180 retro, adopted 2026-04-28)

When dispatching a non-trivial worker task, the design specification belongs in the Linear ticket description. The prompt wrapper handles execution mechanics (model, reasoning level, pre-flight, completion contract) and points back at the ticket for the design.

**What goes in the Linear ticket:**

- Schema, rules, scope.
- Don't-touch list.
- Done-when criteria.
- Provisional flag and promotion criteria if applicable.
- Investigation steps if the bug isn't fully understood yet.

**What stays in the prompt wrapper:**

- Worker selection (model, reasoning level).
- Pre-flight checks (branch hygiene, working tree state).
- Completion contract format.
- Escalation rules.
- Post-merge cleanup steps.

**Why this is a hard rule:** the design survives if the worker session restarts mid-task or if anyone else picks up the ticket later. The prompt wrapper does not — it's ephemeral. Putting the design in the ticket also makes ticket-only dispatch viable (operator taps Telegram dispatch button without Claude Chat drafting an elaborated prompt first), which is critical for autonomy.

PRO-180 shipped cleanly via ticket-only dispatch in 3 minutes. The Linear ticket description carried the full design; CC executed three coordinated edits across three files without needing my prompt wrapper.

**Applies to:** any worker dispatch that's more than a one-line change. Trivial fixes (typos, lint) don't need a locked design.

---

## Required clauses in every dispatch_worker prompt (set 2026-05-08)

Every prompt CC writes for a `dispatch_worker` MCP call MUST include both clauses below. They are workarounds for two recurring loop bugs (empty-INCONCLUSIVE bounce + runaway-fix loop). PRO-335 (status pattern + ESCALATE diagnostic capture) shipped 2026-05-09 and closes the empty-summary side of the gap; the prompt-side guards stay until the orchestrator-side enforcement is validated in production.

1. **Max 3 review-fix rounds.** "After at most 3 rounds of CodeRabbit/Bugbot fixes, declare a terminal state. If actionable findings still exist after round 3, emit `STATUS: ESCALATE: REPEATED_FAILURE` with a non-empty summary listing what's still outstanding."
2. **Non-empty summary on INCONCLUSIVE.** "If you cannot complete the work, emit `STATUS: INCONCLUSIVE` with a non-empty summary that names what was tried, why each attempt failed, and one specific question that would unblock you. An empty INCONCLUSIVE summary will be treated as a worker failure and bounced."
3. **Operator-facing summary format on every output that reaches the operator.** "Begin every operator-facing output (chat reports, completion summaries, escalations) with a one- or two-sentence plain-English summary that states the terminal STATUS and the key outcome. Follow with a single line containing only `---` and then all technical detail (file paths, commit SHAs, test output, JSON). The plain-English summary must be non-empty. This mirrors the Operator Communication Standard in AGENTS.md — codified here so dispatch prompts enforce it explicitly rather than relying on AGENTS.md being loaded mid-task."

**Applies to:** every `dispatch_worker` MCP invocation. Operator-relayed manual prompts (Cursor, Codex) follow the same convention.

---

## Test live claude.ai access before merging gateway changes (set 2026-05-08)

Any change to MCP gateway entry middleware (`tools/miru_mcp_gateway/server.py` `_is_local_origin`, `_ProfileExtractor`, related auth/header handling) MUST be smoke-tested against the live claude.ai connector before merge — not just unit tests.

**Why:** claude.ai arrives via Tailscale Funnel as a public IP carrying a `Tailscale-Funnel-Request` header, **NOT** as a CGNAT IP. Unit tests that mock `_is_local_origin` with a CGNAT address will pass while the real connector breaks with 403. We hit this twice in a row (PR #136 → hotfix #138 → hotfix #139) before committing to the live-test rule.

**Smoke test procedure:**

1. Merge the change locally / push the PR branch.
2. Restart the gateway: `Start-ScheduledTask -TaskName MiruRestartMcpGateway`.
3. From the operator's claude.ai web session, attempt to invoke any tool through the connector (e.g., `linear_get_issue`).
4. Confirm the call succeeds (not 403). Check `logs/mcp_gateway_reads.jsonl` for the request — must show `result: "ok"` and the Tailscale-Funnel-Request header in the trace.
5. Only then mark the PR ready for merge.

**Applies to:** any PR touching `tools/miru_mcp_gateway/server.py` middleware, profile extraction, origin trust logic, or header-based auth.

---

## Worktree contamination prevention (PRO-334, adopted 2026-05-09)

Workers are dispatched into git worktrees (`miru-w1` through `miru-w6`). The `dispatch_listener` pre-spawn cleanup MUST verify the target worktree is parked on its `_parking_<name>` branch and clean before spawning. If a previous worker timed out or crashed and left uncommitted edits or a non-parking branch checkout, **refuse to spawn** and emit a `pre_spawn_dirty_refusal` log line.

**Why:** PRO-330 left dirty state in `miru-w1` after timing out. PRO-332 dispatched into the same worktree minutes later, inherited PRO-330's `dispatcher/` and `services/` edits, and silently merged them into the new ticket's branch. Diagnosing took longer than re-doing both tickets clean. The pre-spawn refusal makes contamination visible and self-clearing instead of silently corrupting downstream work.

**Companion:** post-worker cleanup parks the worktree to `_parking_<name>` and stashes any uncommitted edits. Stash failures abort cleanup (no silent carry-over). Merged-PR detection filters by branch + `mergedAt` + `headRepositoryOwner.login` matching the local origin (forks can't trigger a delete).

**Applies to:** any change to `services/dispatch_listener/src/spawn.js` worktree management or to the worktree registration flow.

---

## Status-pattern recognition + diagnostic capture (PRO-335, adopted 2026-05-09)

`scanStdoutForStatus()` MUST recognize all four canonical terminal states (`CONFIRMED_WORKING`, `INCONCLUSIVE`, `FAILED`, `ESCALATE: <category>`) and capture the diagnostic block following the marker into the result.json `summary` field, with `escalation_category` populated when the marker is `ESCALATE: <category>`. The summary cap is **exactly 4096 chars total including the truncation marker** (not 4096 + marker length).

**Why:** before PRO-335, the scanner only matched `CONFIRMED_WORKING`. Workers correctly emitted `STATUS: ESCALATE: HUMAN-REQUIRED` with a full diagnostic block in stdout, but the orchestrator saw `INCONCLUSIVE` with an empty summary and bounced them. The bounce burned worker tokens and operator attention without any signal about what actually went wrong. The new pattern table + diagnostic capture closes that loop.

**Applies to:** any change to `services/dispatch_listener/src/spawn.js` status detection, or to the `result.json` schema in `services/dispatch_listener/src/receipt.js`.

---

## Dispatch_listener must boot into operator's interactive session (lesson + fix shipped PRO-336 PR #154/#155 on 2026-05-09)

When registering a Windows scheduled task or service that produces a long-running child process the operator's worker shell needs to manage, the spawned process MUST land in the operator's interactive Windows session — not Session 0.

**Why:** `dispatch_listener` (Node, port 19100) was spawned by `MiruDispatchListener` (S4U logon, "At system startup" trigger). At boot, before login, the spawn lands in Session 0. A non-elevated worker shell in the operator's interactive session **cannot** kill a Session 0 process owned by the same user — Windows requires `SeDebugPrivilege` (admin) for cross-session termination. Result: the supposedly-non-elevated `MiruRestartDispatcher` task fails with `Access is denied`, and CC has to ping the operator to recover instead of restarting autonomously.

**The Session 0 trap also applies to:** any future service that needs to be killed/restarted by a non-elevated worker shell.

**Decision rule when registering a task:**

1. Will a non-elevated worker need to kill or restart this process? → **Must run in operator's interactive session.** Use a `shell:startup` shortcut (fires at logon, in Session 1+), or an AtLogOn-triggered task with `LogonType=Interactive` AND a verification step. Verify post-launch that `SessionId != 0`.
2. Service/daemon-like, never killed by workers? → SYSTEM (Session 0) is fine, per the existing `domain-ops.md` rule.

The existing `domain-ops.md` "Scheduled Tasks — Hard Rule" optimizes for "no focus stealing" via SYSTEM logon. That rule is still correct for non-interactive services. This lesson **adds a second axis**: if a worker needs to restart it, Session 1+ is mandatory regardless of focus considerations (the wrapper-based VBS approach prevents focus stealing in Session 1+ too).

**Applies to:** any new Windows scheduled task or service registration where a worker may need to restart the process.

---

## Ticket handoff = ticket only, no chat-wrapper context (set 2026-05-08)

When handing a ticket to a sub-worker (operator-relayed or auto-dispatched), the wrapper message MUST contain only:

- The file path **or** a fenced code block (triple backticks) containing the paste-ready payload. Any paste-ready content MUST be wrapped in a fenced code block — never inline prose, never an indented snippet. This is the same hard rule as "Copy-paste content for manual routing" in `AGENTS.md`.
- One short line of context (e.g., "Loop ticket — PRO-336, in Miru Orchestration / Autonomy.").

All design context, priority, ordering hints, and "I'd suggest" notes belong **inside the ticket description**, not in the wrapper. Wrapper context is ephemeral and lost on session boundaries; ticket context survives.

**Why:** when CC ships a wrapper that says "here's PRO-X and PRO-Y; I'd suggest dispatching X first because of Z" the next session or sub-worker either re-reads the wrapper (slow) or misses it entirely (silent loss). The locked-design-in-Linear rule already says this for design — this lesson generalizes it to **all** dispatch context.

**Applies to:** every ticket handoff CC produces, whether for operator paste, auto-dispatch, or manual relay.

---

## Gemini-cli requires .gemini/settings.json + console-allocated parent (set 2026-05-10)

Two distinct gemini-cli requirements that **both** must be satisfied for `dispatch_worker(worker="gemini", ...)` to actually function in a target repo:

**1. Workspace-tier `.gemini/settings.json` per repo.** Gemini-cli has **no `--mcp-config` CLI flag** (verified gemini-cli issue [#4674](https://github.com/google-gemini/gemini-cli/issues/4674) closed as duplicate of #3470). MCP servers are discovered via 4-tier `settings.json` files only: `SystemDefaults > User (~/.gemini/settings.json) > Workspace (<cwd>/.gemini/settings.json) > SystemOverrides`. The blocks merge by server-name across tiers. Without a workspace-tier file in a target repo, gemini lands with whatever the operator-level User config has — which is empty on this machine, so dispatched gemini hangs trying to use shell to read Linear tickets. Use `httpUrl` (not `url` — that's SSE) for the gateway. `${env:VAR}` substitution syntax is supported but Gemini silently leaves the literal string if the env var is missing (different from claude-code's fail-closed behavior — verify spawn.js exports `MIRU_TOOL_PROFILE` + `MIRU_TRACE_ID` before spawning).

**2. Console-allocated parent process.** gemini-cli's `@lydell/node-pty/conpty_console_list_agent.js` calls Windows `AttachConsole()` at startup. If the parent process has no console allocated, AttachConsole fails and gemini-cli exits 1 immediately (in the child stderr: `Error: AttachConsole failed`). `dispatch_listener` launched via Task Scheduler with `windowsHide: true` does NOT have a console; the PRO-336 `shell:startup` shortcut path gives the wrapper an interactive Session 1+ context with a console allocated. `spawn.js` line ~689 sets `windowsHide: false` for gemini specifically so gemini inherits the wrapper's console. If you ever see "AttachConsole failed" in a gemini stderr trace: the listener was started without a console (e.g. via `Start-ScheduledTask MiruRestartDispatcher` instead of the shell:startup shortcut). Recovery: kill the listener and relaunch via `windows\start_dispatch_listener.ps1` from an interactive PowerShell.

**Why both:** missing either one yields a silent hang (gemini alive, 0 file activity, ~0% CPU). Took ~30 minutes to diagnose during LOS-2 dispatch. Documented now so the next person doesn't re-tread it.

**Applies to:** every new repo added to multi-repo dispatch; every change to listener launch path.

---

## Worktree contamination prevention for non-miru repos (set 2026-05-10)

Every target repo (anything with a worktree pool in `WORKTREE_POOLS`) MUST gitignore `.mcp.json` AND `mcp.json` on its `main` branch. The dispatch_listener writes per-spawn config to `.mcp.json` in the worker's CWD; if the file is tracked, the worktree shows as "modified" on the next dispatch and `verifyWorktreeParked` refuses with `dirty_worktree: ?? .mcp.json`.

**Bit on LOS-1 bootstrap:** PR #1's `git add .` committed the listener's `.mcp.json` before the new `.gitignore` rule landed (because SvelteKit's `npx sv create` overwrote my pre-bootstrap `.gitignore`). LOS-2 dispatch then failed with `pre_spawn_dirty_refusal: dirty_worktree`. Fix sequence: `git rm --cached .mcp.json` on `main`, sync to parking branch, retry dispatch. Both files in the patch: commit `e2543bf` (untrack) + `76a0764` (re-add `.gitignore` entry).

**The single source for these gitignore lines:** `data/templates/multi-repo/dot-gitignore` (PRO-340, pending). Every new target repo should `cp` from this template.

**Applies to:** every new target repo + any bootstrap that uses a generator (`npx sv create`, `create-react-app`, etc.) that may overwrite `.gitignore`.

---

## Completion-marker appends require their own one-line PR (set 2026-05-10)

Branch protection on `main` blocks direct pushes. Worker `tools/emit_completion.py` calls write the marker to `data/cc_completion_log.jsonl` in the worker's worktree, but the orchestrator can't `git push` it directly to `main` afterwards. Pattern established by PR #158 (LOS-1 marker) + PR #161 (LOS-2 marker):

1. After worker terminates, on `main` branch (clean), check out `chore/<ticket>-completion-marker`
2. The marker line is already in `data/cc_completion_log.jsonl` (worker's emit landed there)
3. `git add data/cc_completion_log.jsonl && git commit && git push`
4. `gh pr create` with title `chore(log): append <ticket> completion marker (...)`. Body documents the worker, trace_id, merge_commit_sha of the corresponding feature PR.
5. CI runs the append-only pre-commit hook (`tests/test_jsonl_append_only_invariant.py` validates pure-append diff). Squash merge.
6. Sync local main, return to clean state.

**CodeRabbit assertive will sometimes flag the new line** with "consider editing the row to backfill `merge_commit_sha`" — this is a textbook violation of the append-only invariant and MUST be dismissed with the canonical message. PRO-339 (PR #159) tightened `.coderabbit.yaml` `path_filters` + `path_instructions` to suppress these false positives going forward, but operator-side awareness still helps.

**Why each marker gets its own PR (and not bundled):** the marker is the canonical proof-of-completion event in the DGAS audit chain; bundling it with feature work would conflate "feature shipped" with "feature shipping recorded." Keeping the chore commits one-per-marker also makes `git log -- data/cc_completion_log.jsonl` legible.

**Applies to:** every dispatched ticket completion that emits a marker.

---

## Frontend dispatches MUST verify with Playwright MCP at iPhone viewport BEFORE declaring CONFIRMED_WORKING (set 2026-05-10)

The "verified locally with curl" pattern is a lie for any UI change. `curl http://127.0.0.1:18767/` will return 200 + valid HTML even when the actual user-agent (operator's iPhone) loads a blank page. Frontend tickets MUST run a Playwright MCP screenshot at iPhone-shape viewport against the **same URL the operator will use** before emitting CONFIRMED_WORKING.

Concrete recipe (built into every frontend ticket's done-when checklist from now on):

```text
mcp__playwright__browser_resize { width: 440, height: 956 }   # iPhone 16 Pro Max (CSS pixels / "points")
mcp__playwright__browser_navigate { url: <production/tailnet URL, NOT localhost> }
mcp__playwright__browser_take_screenshot { filename: "los-N-iphone-verified.png" }
mcp__playwright__browser_console_messages { level: "error" }
```

The `filename:` parameter is mandatory — leaving it default produces `page-{timestamp}.png` artifacts that are hard to correlate with a specific dispatch. Use the same filename token (`los-N-iphone-verified.png`, where N is the ticket number) in both this recipe AND in the terminal-state output so the audit trail stays consistent.

**Viewport size matters and the easy-to-confuse numbers are a real trap.** iPhone 16 Pro Max is 440×956 CSS pixels (6.9" display). Do NOT use 430×932 — that's the iPhone 16 Plus / 15 Pro Max / 15 Plus (6.7" display). Ask-the-cause: this canon was first written with 430×932 because the author conflated the two; CodeRabbit caught it on PR #172. If the operator changes phones, update this canon AND every dispatched prompt that copies the recipe.

If the snapshot is blank, broken, or the console has errors → status is INCONCLUSIVE, not CONFIRMED. Investigate and fix.

**Why:** today (2026-05-10) the operator wasted 30+ minutes trying to load the LogueOS Console on iPhone. CC repeatedly claimed the dashboard was "verified live" via local curl probes. The actual failure was a cascade: Tailscale serve subpath strip + SvelteKit base path mismatch + bare `/api/runs` fetch hitting wrong host + Tailwind 4 missing `--color-foreground` token. Each layer LOOKED fine to curl. Playwright at iPhone viewport caught it in 30 seconds. The skill exists; we have no excuse not to use it.

**Applies to:** every PR touching `src/routes/`, `src/lib/components/`, `src/app.html`, `vite.config.*`, `svelte.config.*`, or any file under `LogueOS-Console/`.

**Worker-tool-availability — UPDATED 2026-05-10 (RESOLVED).** The earlier version of this clause said "Playwright MCP tools live in CC's profile, NOT gemini's" and required gemini frontend dispatches to defer the gate to a claude-code follow-up. That gap is now CLOSED: `services/dispatch_listener/src/mcp_config.js` (the source of truth for every dispatched worker's `.mcp.json`) now writes `playwright` as a stdio MCP server alongside `miru-gateway` for ALL workers (claude-code AND gemini). Both lanes can run the iPhone gate inline. The check is conditional on `@playwright/mcp` being installed in the operator's global npm dir — if missing, the listener emits a `console.warn` (visible in `dispatch_listener_stdout.log`) and still spawns the worker, but the iPhone gate will fail. Historical incident: LOS-7 cc-LOS-7-12cad92b-3a38104d gemini variant thrashed for 22 min trying to npm-install Playwright before being killed; that failure mode was the prompt for this fix.

**Maintenance procedure for MCP tools** (set 2026-05-10): If you discover ANOTHER MCP tool the workers need, add it to `mcp_config.js` and restart the listener. DO NOT hand-edit individual worktree `.mcp.json` files (the listener regenerates them on every spawn via `worktree_auto_clean`). Restart command depends on which workers will pick up the change next:

- **claude-code-only lanes**: `Start-ScheduledTask -TaskName MiruRestartDispatcher` is sufficient. The scheduled task lands the listener in the operator's interactive Session 1+ via the PRO-336 shell:startup shortcut, which is a fully attached console — claude-code spawn works.
- **gemini-involved lanes**: gemini's CLI does an `AttachConsole` call on spawn that can fail if the listener was launched via the legacy S4U scheduled task (Session 0). Verify via `(Get-Process -Id $listenerPid).SessionId` — must be ≥1. If it's 0, do an interactive relaunch instead: `Stop-ScheduledTask -TaskName MiruRestartDispatcher; powershell -ExecutionPolicy Bypass -File windows\start_dispatch_listener.ps1` from your interactive shell.

**Mandatory clause for every frontend `dispatch_worker` prompt** (added 2026-05-10 after overnight LOS-4 + LOS-5 shipped broken because the gate wasn't enforced):

```text
Before declaring CONFIRMED_WORKING, you MUST verify the change end-to-end via Playwright MCP at iPhone 16 Pro Max viewport (440x956 CSS pixels — NOT 430x932, that is the 16 Plus) hitting the operator-facing URL (NOT localhost). Concrete recipe:

  mcp__playwright__browser_resize { width: 440, height: 956 }
  mcp__playwright__browser_navigate { url: "https://room.taila28611.ts.net/console/<your-route>" }
  mcp__playwright__browser_take_screenshot { filename: "los-N-iphone-verified.png" }
  mcp__playwright__browser_console_messages { level: "error" }

If the screenshot shows a 500/blank/error page, OR if console_messages returns ANY errors, the status is INCONCLUSIVE not CONFIRMED. Iterate until both pass. Include the screenshot filename in your terminal-state output as proof.
```

---

## Multi-file dispatch audit + squash-merge verification (set 2026-05-10)

**Applies to:** every `dispatch_worker` prompt that touches multiple files — frontend OR backend, no exception. The Playwright iPhone gate above is frontend-specific; this section is the parallel rule for catching files that silently disappear between branch and main during a squash merge. The two gates compose: frontend multi-file PRs run BOTH, backend multi-file PRs run this one alone (Playwright is meaningless without a UI surface).

**Two checkpoints, NOT one:**

**Pre-push (on the feature branch, before opening PR):** `git fetch origin && git diff origin/main..HEAD --stat` shows every file your branch will introduce. Compare against the file list you intended to ship; fail-fast if anything's missing. Do this BEFORE pushing — once pushed, anything dropped will surface as a phantom file in the PR's diff view but is hard to spot in a long file list.

**Post-merge (after PR is squashed to main):** the `origin/main..HEAD` diff is empty (same SHA) — that comparison is useless after merge. Instead capture the merge commit SHA and inspect IT: `git fetch origin && git show --stat <merge-sha>` (or `gh pr view <N> --json mergeCommit --jq .mergeCommit.oid` then `git show --stat`). PR #5's branch had 6 files in `git diff origin/main..HEAD --stat`, but the operator's squash-merge produced a merge commit that contained only 2 of those files — `git show --stat <merge-sha>` revealed the drop. **File existence in `git show --stat` does NOT prove the change reached the running app** — it only proves the file is present in the merge commit. For frontend PRs, you MUST run the Playwright iPhone gate (see section above) against the operator-facing production URL after squash, regardless of what the diff shows. For backend PRs, exercise the affected endpoint/script against the live service. Treat the diff as a necessary-but-insufficient signal; treat the running app as ground truth.

**Mandatory clause for every multi-file `dispatch_worker` prompt** (frontend or backend):

```text
Before declaring CONFIRMED_WORKING:
1. On feature branch (pre-push): `git fetch origin && git diff origin/main..HEAD --stat`. Confirm every file you intended to ship is listed.
2. After squash merge to main (post-merge): capture the merge commit SHA via
   `gh pr view <N> --json mergeCommit --jq .mergeCommit.oid`
   then run
   `git fetch origin && git show --stat <merge-sha>`
   to confirm every file from step 1 is also in the squash diff. (squash merges can silently drop files between PR open and main; this catches the drop)
3. After step 2 passes: ALWAYS exercise the running app against the change.
   - Frontend: run the Playwright iPhone gate (440x956) against the operator-facing URL.
   - Backend: hit the affected endpoint/script against the live service and confirm behavior.
   `git show --stat <merge-sha>` is necessary but NOT sufficient — file existence in the squash diff does not prove the change reached production. Mark CONFIRMED_WORKING only after steps 1, 2, AND 3 all pass; the running app is the ground truth.

REQUIRED in your CONFIRMED_WORKING terminal-state output (audit evidence — not optional):
- The merge commit SHA from `gh pr view ... --jq .mergeCommit.oid` (step 2).
- The full `git show --stat <merge-sha>` output (step 2) — file list, not summary.
- The running-app verification result from step 3:
  - Frontend: the screenshot filename emitted by `mcp__playwright__browser_take_screenshot { filename: "los-N-iphone-verified.png" }` AND the count of `mcp__playwright__browser_console_messages { level: "error" }` results (must be 0).
  - Backend: the endpoint/script name + a 1-2 line excerpt of the response/output that confirms the change took effect.
A CONFIRMED_WORKING block missing any of these three evidence artifacts will be treated as INCONCLUSIVE and bounced.
```

LOS-5 (PR #5) is the canonical motivating example — gemini emitted CONFIRMED_WORKING based on git status in its branch, but the squash on the operator's side merged only 2 of 6 files (`+page.server.ts` + `+layout.svelte`); the new component, types, API endpoint, and replaced `+page.svelte` all silently disappeared. The dashboard rendered the original placeholder for 7 hours before Playwright iPhone verification caught it.

---

## Tailwind 4 utility classes are generated from `@theme` ONLY, not `:root` (set 2026-05-10)

Tailwind 4 + shadcn migration trap. shadcn's legacy convention puts design tokens in `:root { --foreground: ...; --card: ...; }`. Tailwind 3 generated utility classes (`text-foreground`, `bg-card`) from those. **Tailwind 4 does NOT.** It generates utility classes only from tokens declared in the `@theme` block with the `--color-` prefix.

Symptom: `Cannot apply unknown utility class 'text-foreground'` 500 errors on every page request. The class is undefined because `--color-foreground` doesn't exist in `@theme`.

```css
/* WRONG (legacy Tailwind 3 / shadcn) — does not generate text-foreground utility */
@layer base {
  :root {
    --foreground: #ffffff;
  }
}

/* RIGHT (Tailwind 4) — generates text-foreground utility */
@theme {
  --color-foreground: #ffffff;
}
```

Both can coexist if components also read raw CSS vars. The point is: `@theme` is mandatory for utility class generation in Tailwind 4.

**Applies to:** every Tailwind-styled SvelteKit/React project that's on Tailwind 4 (>=4.0.0).

---

## Tailscale serve subpath BEHAVIOR depends on target URL trailing path (set 2026-05-10)

`tailscale serve --bg --set-path=/console http://localhost:18767` — Tailscale **strips** `/console` from incoming requests before forwarding to localhost. So `/console/api/runs` arrives at SvelteKit as `/api/runs`.

`tailscale serve --bg --set-path=/console http://localhost:18767/console` — Tailscale **preserves** the path because the target URL has matching prefix. SvelteKit receives `/console/api/runs` as-is.

Pick one based on what the downstream app expects:

- **App at root (no base path):** use the strip variant (`http://localhost:18767`). SvelteKit `kit.paths.base = ''`.
- **App with base path:** use the preserve variant (`http://localhost:18767/console`). SvelteKit `kit.paths.base = '/console'`. This is required when the app needs to generate URL-prefixed asset paths (Vite's `/@fs/...`, SvelteKit's `resolve('/foo')` → `/console/foo`).

For LogueOS Console specifically: app at `/console` because n8n owns the tailnet root. `kit.paths.base = '/console'` + Tailscale preserve-path is the working combination.

**Applies to:** any service exposed via `tailscale serve --set-path` where the served app generates internal URLs.

---

## Client-side `fetch('/api/foo')` is NOT base-path aware in SvelteKit; use `resolve()` (set 2026-05-10)

SvelteKit's `kit.paths.base` is honored by `<a href>` anchors, server-side `fetch` (in load functions), and `resolve()` from `$app/paths`. It is **NOT** honored by client-side `fetch()` in `+page.svelte` — `fetch('/api/runs')` resolves relative to the **origin**, not the base path.

When served behind a reverse proxy at a subpath (e.g. Tailscale serve at `/console`), `fetch('/api/runs')` from the browser hits `https://<host>/api/runs` instead of `https://<host>/console/api/runs`. The request bypasses the SvelteKit app entirely and may hit a different service at root.

```typescript
// WRONG when app is served at /console behind a reverse proxy
const resp = await fetch('/api/runs');

// RIGHT — base-path aware
import { resolve } from '$app/paths';
const resp = await fetch(resolve('/api/runs'));
```

**Applies to:** every client-side `fetch()` call in `+page.svelte`, `+layout.svelte`, or any Svelte component running in the browser.

---

## iOS PWA viewport-fit=cover is REQUIRED for safe-area insets to be non-zero (set 2026-05-10)

`env(safe-area-inset-bottom)` and the other safe-area-inset variables resolve to `0px` on iPhone unless the viewport meta tag includes `viewport-fit=cover`. Without it, the bottom nav gets covered by the iPhone home indicator (the 34pt translucent bar at the bottom of the screen on iPhone 16 Pro Max in portrait), and `padding-bottom: env(safe-area-inset-bottom)` does nothing.

The full minimum-viable iOS PWA meta tag set:

```html
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="<App Name>" />
<meta name="theme-color" content="#0d1117" />
<link rel="apple-touch-icon" href="/apple-touch-icon.png" />
<link rel="manifest" href="/manifest.webmanifest" />
```

iOS 16+ also requires `apple-touch-icon` as a separate `<link>` even when the manifest specifies icons (Safari ignores manifest icons; uses apple-touch-icon for the home-screen icon).

For bottom navs:

```css
.bottom-nav {
  padding-bottom: calc(<design-padding> + env(safe-area-inset-bottom, 0px));
}
```

Always supply the `, 0px` fallback so older browsers without `env()` support don't drop the whole declaration.

For container heights, use `100dvh` (dynamic viewport height) not `100vh` — `dvh` accounts for the iOS address bar contracting/expanding during scroll.

**Applies to:** every SvelteKit/React app intended to be installed as an iOS home-screen PWA.

---

## Kill all background Monitor loops before a terminal state (2026-05-11 popup retro, adopted 2026-05-11)

Every `Bash --run_in_background` task and every `Monitor` task you arm is a real bash.exe process on the host. They survive compaction. The task IDs you have for them (`blecb01pq`, `bvale5wja`, etc.) DO NOT survive compaction — but the bash processes do, with Claude Code's main PID as parent.

**The trap:** an `until <poll>; do sleep 30; done` Monitor loop keeps running after compaction. Each `sleep 30` spawns a fresh `sleep.exe`. On Windows 11 24H2 (Terminal default), each `sleep.exe` allocates a brief console window unless `DelegationTerminal` registry routes are set. The operator sees popups every 30 s with no obvious source.

**Today's specific incident (2026-05-11):** 6 Monitor loops armed earlier in the session (PR-poll watchers + a TEST OK callback watcher) survived a compaction. The session was diagnosing the `MiruRestartLogueOSConsole` task as the popup source. A `DelegationTerminal`/`DelegationConsole` registry fix was applied — correct as defense-in-depth — but popups continued. Root cause discovered only after a `wmic process` snapshot showed 5 `sleep.exe` invocations in 8 s with different parent PIDs.

**Discipline:**

1. **Before declaring any terminal state (CONFIRMED_WORKING / INCONCLUSIVE / FAILED),** run `tasklist | grep -iE "^(bash|sleep)"` and verify only your active diagnostic shells remain. Zero `sleep.exe` is the canonical clean state when no Monitor loops are running.
2. **Before compaction or session handoff,** TaskStop every Monitor / background Bash task you armed. Even if you think the loop's `until` condition will fire soon, kill it explicitly.
3. **Recovery procedure when popups surface and Monitor loops are suspected:**
   ```bash
   # Identify rogue bashes
   powershell -Command "Get-CimInstance Win32_Process -Filter \"Name='bash.exe'\" | Select-Object ProcessId, ParentProcessId, CommandLine | Format-Table"
   # Kill top-level loops (children die with parent)
   taskkill //F //T //PID <pid>
   # Verify zero respawn over one full sleep cycle (35 s for sleep 30)
   sleep 35 && tasklist | grep -i sleep
   ```

**Why "before terminal state" not "at session end":** compaction can happen mid-task. Task IDs don't survive compaction. If you wait until session-end to clean up, the next compaction may strand the loops with no way to find them except by process inspection.

**Applies to:** every CC session that arms background Bash tasks or Monitor loops, especially long polling loops watching PR state, CI checks, or callback files.
