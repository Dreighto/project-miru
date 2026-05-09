# Overlay — adopted-lessons

```text
Overlay: adopted-lessons
Architecture: MIRU-INSTRUCTIONS-v2
Load when: doing a non-trivial code change (more than typo or lint).
Last reviewed: 2026-05-09
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

## Dispatch_listener must boot into operator's interactive session (lesson, fix tracked PRO-336, adopted 2026-05-09)

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

- The file path or copy-paste block for the ticket.
- One short line of context (e.g., "Loop ticket — PRO-336, in Miru Orchestration / Autonomy.").

All design context, priority, ordering hints, and "I'd suggest" notes belong **inside the ticket description**, not in the wrapper. Wrapper context is ephemeral and lost on session boundaries; ticket context survives.

**Why:** when CC ships a wrapper that says "here's PRO-X and PRO-Y; I'd suggest dispatching X first because of Z" the next session or sub-worker either re-reads the wrapper (slow) or misses it entirely (silent loss). The locked-design-in-Linear rule already says this for design — this lesson generalizes it to **all** dispatch context.

**Applies to:** every ticket handoff CC produces, whether for operator paste, auto-dispatch, or manual relay.
