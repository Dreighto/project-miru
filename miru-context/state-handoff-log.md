# State Handoff Log

Claude Chat writes the latest handoff directly to this file at every thread close. The next
thread reads it at startup to restore context without asking the operator to recap.

**How it works:**

- At thread close: Claude Chat runs thread-close hygiene (memory sync, Notion drift check, Linear
  cleanup), then overwrites the "Latest Handoff" section below with the current handoff.
- At thread start: read this file. If a handoff exists, start from it. No copy-paste needed.
- Only the latest handoff is kept here. History goes in Project Memory (agenda table) if needed.

This file is not the place to solve autonomy or continuity — that's handled by the operating
model and Project Memory. This is just the bridge that tells the next thread where to start.

---

## Template

```markdown
# Miru thread handoff — [DATE + TIME CONTEXT]

## What we were working on

[1-2 sentences. Plain English. What was the main focus?]

## What got done

[Bullet list. Each item = one ticket or decision that shipped/closed. Include PRO-### numbers.]

## What's still open

[Bullet list. Each item = one ticket or task in progress or blocked. Include Linear state and why it's stuck.]

## Decisions made

[Bullet list. Key decisions from this thread. Already logged in Project Memory — this is just a quick reference.]

## What the next thread should do first

[1-3 concrete actions. Specific, not vague. "Promote PRO-191 to Todo" not "continue the smoke test work."]

## What NOT to do

[Tickets that shouldn't be promoted, conversations that are paused, known traps.]

## Loop health (if relevant)

[Quick status. Only include if the loop was exercised or changed this thread.]

## Key files touched

[Repo files, Notion pages, or Linear tickets created or significantly changed.]
```

---

## Rules

- **Keep it short.** The whole handoff should fit on one phone screen, maybe two.
- **Use plain English.** The operator reads these too.
- **No redundancy with Project Memory.** Reference decision IDs, don't repeat full rationales.
- **Don't pack everything.** The next thread has tools — it can look things up. The handoff just says where to start.
- **Write at thread close.** Canon hygiene checks happen first, then the handoff is drafted.

---

## Where It Lives

The latest handoff is written directly to the "Latest Handoff" section below by Claude Chat
at thread close. The next thread reads this file at startup — no copy-paste required.

If the operator wants a handoff archived, Claude logs a compact version to Project Memory's
agenda table with the handoff content in a notes-style field.

---

## Latest Handoff

# Miru thread handoff — 2026-05-04 (CC closeout after Phase 2 Judgment Trail ship)

## What we were working on

One CC session: shipped Phase 2 Judgment Trail of the Autonomy Overhaul. New tool `tools/emit_decision.py` + `data/agent_decisions.jsonl` (8th protected append-only file) + 51 unit tests. Standalone foundation, NOT wired into runtime. PR #86 merged on main.

## What got done

- **Phase 2 Judgment Trail shipped.** PR #86 merged on main as squash-merge `b9a92fb`. `tools/emit_decision.py` validator + appender, `data/agent_decisions.jsonl` as 8th protected append-only file (gitignored), 45 unit tests in 9 logical groups + 3 invariant tests = 51/51 green. Hand-rolled two-pass validator with closed enums on `trigger` / `proposed_tag` / `authority_mode` / `confidence` / `grading_state`. **Authority discipline:** `tool_profile` (free string) and `authority_mode` (enum) are SEPARATE gates; `authority_mode` deliberately excludes `full_operator` to prevent tool-access vs canon-authority conflation. **Anti-gaming:** 20-char floor on narrative fields + blacklist on filler alternatives. Standalone foundation: NOT wired into runtime.
- **Peer review via Gemini 3.1 operator-relay.** Codex direct-dispatch (via `dispatch_worker` MCP) hit an MCP transport stall mid-review (rmcp transport fatal at startup: `Unexpected content type: missing-content-type; body: ""` when sending initialized notification). Codex announced its plan and read 4 canon files, then went silent for ~17 min. Listener killed it at the 1200s timeout. Operator chose Gemini operator-relay per `CLAUDE_CHAT.md` Step 2.5 fallback. CC built a self-contained paste-ready bundle (prompt + full diff = 62KB) at `/tmp/pr86_gemini_review_bundle.txt`; operator uploaded the .txt to gemini.google.com (now on **Gemini 3.1**, updated from 2.5 Pro). Gemini returned `OVERALL: READY FOR OPERATOR MERGE` with 2 Medium + 2 Low findings. 3 of 5 fixed in commit `31b505e` (Findings 5 + 7 + the alternatives anti-gaming reinforcement that came along); 1 Medium (Finding 8 — marker placement) addressed by splitting marker direct-to-main as commit `197c698`; 1 Low (Finding 4 — file locking project-wide) deferred per operator agreement.
- **Marker placement reaffirmed as direct-to-main.** Gemini Finding 8 flagged that bundling the Phase 2 completion marker into the feature branch (commit `0b3904d`) drifted from canon (CLAUDE.md PR Merge Policy lists `cc_completion_log.jsonl` appends under direct-to-main; Phase 1 / PRO-290 followed this with `0942ec9`). CC reset the feature branch (`git reset --hard HEAD~1`), force-pushed `--force-with-lease` (mine-alone branch), and re-emitted the captured marker line direct on main as commit `197c698`. Original timestamp preserved. 3-way merge simulation (`git merge-tree`) verified the marker survives operator merge.
- **Notion canon synced.** Autonomy Overhaul canonical page (356c5d34...) updated for Phase 2 SHIPPED + Phase 3 NEXT CANDIDATE + Phase 4 LOCKED, including new "Phase 2 — Shipped State" section parallel to Phase 1's. 01 Now updated. Work Log update timed out twice (PRO-275 pattern) — deferred for operator manual update or future retry.
- **Project Memory synced.** `stack_state.loop_health` refreshed; 3 new `decisions` rows (Phase 2 ship summary, Codex/Gemini relay precedent, marker direct-to-main discipline); 1 new `agenda` row (Phase 3 planning, priority 1, active).
- **Post-merge cleanup.** Local `main` pulled, merge SHA verified, feature branch deleted with `git branch -d` (safe-delete after squash-merge — local matched its remote-tracking ref). Working tree clean of tracked changes.

## What's still open

**Phase 3 Subagent Isolation — operator-approved as next candidate phase, planning/evaluation only:**

- Scope per Gemini blueprint and Notion canon page (356c5d34...): `X-Miru-Tool-Profile` header on dispatch + `MIRU_TOOL_PROFILE` env injection + gateway-level profile enforcement + restricted profiles (e.g. `drift_executor` with `linear_*`, `git_*`, `fs_*` but NO `telegram_*`) + Phase 3 Denial Test gate before W2 can auto-assign `drift_executor` automatically. Operator approved 2026-05-04 to proceed in a new session. **Planning/evaluation only — DO NOT START IMPLEMENTATION** without a separate explicit approval.
- Project Memory `agenda` row: priority 1, status `active`, title "Phase 3 Subagent Isolation — planning/evaluation pass (operator-approved 2026-05-04, new session)".

**Phase 4 Ingress Classifier remains LOCKED** until Phase 3 is complete and approved.

**Other open items (non-blocking):**

- **Notion Work Log update for 2026-05-04** — timed out twice. PRO-275 pattern. Operator can add the anchor manually or CH/CC can retry in a future session. Other Notion canon (Autonomy Overhaul page, 01 Now) updated successfully.
- **File-locking on append-only writers** — Gemini Finding 4 (Low). Project-wide concern across all 8 writers (`emit_completion`, `emit_heartbeat`, `agent_bus`, `emit_decision`, etc.). Out of Phase 2 scope. Open as a future ticket if operator wants it.
- **Pre-existing untracked file at repo root** — `C\357\200\272UsersDreighto.claudeprojectsD--dev-miruanalysis2.txt`. Predates this session entirely. Operator deferred cleanup decision.
- **Codex MCP transport issue** (rmcp fatal at startup, "Unexpected content type: missing-content-type; body: ''") — separate dispatch-environment problem. Out of Phase 2 scope but worth investigating later. Symptom: Codex spawns, reads a few files, then hangs indefinitely. Workaround: use Gemini operator-relay.
- **Remote feature branch `origin/dreighto/phase2-judgment-trail`** still exists on GitHub (squash-merge didn't auto-delete). Local branch deleted. Operator can run `git push origin --delete` if they want it cleaned up.

## Decisions made

(All logged to Project Memory `decisions` table — recent rows cover this session.)

- **Phase 2 shipped with authority/profile separation enforced via enum exclusion** — `AUTHORITY_MODES` deliberately excludes `full_operator` so the validator catches workers conflating tool access with canon-write authority. The grant context lives outside the JSON; the validator does not cross-check `tool_profile` vs `authority_mode`.
- **Gemini 3.1 operator-relay is a working fallback** when Codex direct-dispatch fails at MCP transport. Documented as a process precedent. Future workers stalling at the same MCP transport class can fall back to operator-relay rather than re-dispatching the same broken transport.
- **Completion marker placement is direct-to-main only**, never bundled into feature branches even for operator-merge PRs. Reaffirmed after the Gemini Finding 8 retro. Going forward, workers emit the marker via `tools/emit_completion.py` AFTER the operator merges, on `main`.
- **Length floor extension over brief-verbatim** — operator chose consistency: `_MIN_MEANINGFUL_LENGTH` (20 chars) now applies to `decision` and `confidence_reason` in addition to `would_change_mind_if` and alternatives narrative fields. Closes the 1-char filler gaming vector.

## What the next thread should do first

1. Read this handoff. Read `CLAUDE.md` (if CC) or `CLAUDE_CHAT.md` (if CH).
2. Query Project Memory: `stack_state.loop_health` (just refreshed), recent `decisions` (top 5 cover this session), active `agenda` items where priority ≤ 2 (the new Phase 3 planning row will be there).
3. Read the Notion canon page "Project Miru Autonomy Overhaul — 4-Phase Implementation Plan" (356c5d34-0141-811e-8de5-d9e9be4175e5) — Phase 3 section now marked NEXT CANDIDATE PHASE; design rules and Denial Test requirement are in the preserved Gemini blueprint.
4. **Wait for explicit operator direction on Phase 3 planning kickoff.** Operator approved planning/evaluation only — implementation requires a separate approval.

## What NOT to do

- Do NOT begin Phase 3 implementation without explicit operator approval.
- Do NOT begin Phase 4 — still LOCKED.
- Do NOT modify `tools/emit_decision.py`, `tests/test_emit_decision.py`, or `data/agent_decisions.jsonl` unless fixing a Phase 2 bug.
- Do NOT wire Phase 2 into W2, gateway, VP Ops, or dispatch_listener — that's a future explicitly approved grading/wiring pass.
- Do NOT modify W2 routine-drift behavior, tool profiles, or gateway profile enforcement outside an approved Phase 3 implementation pass.
- Do NOT change canon authority rules.
- Do NOT bundle completion markers into feature branches — append direct-to-main only.
- Do NOT generate paste-ready content for the operator without wrapping in a code block (existing hard rule, set 2026-05-03).

## Loop health

- All 5 services healthy (mcp_gateway 18766, pm 18080, miru_ai 18765, dispatch_listener 19100, n8n 15678).
- Local `main` at `b9a92fb` (squash-merge of PR #86); `197c698` (Phase 2 marker direct-to-main) is just before. Working tree clean of tracked changes.
- 8 protected append-only data files (just added `data/agent_decisions.jsonl`).
- 88 markers in `cc_completion_log.jsonl`.
- `agent_messages` table (Phase 1) live but still no active producers/consumers.
- VP Ops verification + Gemini operator-relay both confirmed working in this session.

## Key files / surfaces touched

- **GitHub:** PR #86 merged. Local main `12c7daf` → `197c698` (marker) → `b9a92fb` (squash-merge).
- **Notion:** Autonomy Overhaul canonical page (356c5d34-0141-811e-8de5-d9e9be4175e5) updated; 01 Now (09bd7fc1...) updated. Work Log (0bdebb75...) timed out — deferred.
- **Project Memory:** `stack_state.loop_health` UPDATEd; 3 new `decisions` rows; 1 new active priority-1 `agenda` row (Phase 3 planning).
- **Repo new files:** `tools/emit_decision.py`, `tests/test_emit_decision.py`.
- **Repo edits:** `CLAUDE.md`, `.gitignore`, `tests/test_jsonl_append_only_invariant.py`, `data/cc_completion_log.jsonl` (direct-to-main marker append on main), `miru-context/state-handoff-log.md` (this file).
- **CC auto-memory:** new entry `reference_gemini_version.md` (Gemini 3.1 noted).

---

# 2026-05-05 — CH boot redesign + dispatch failure investigation + canon sweep + PRO-298 ship (Claude Code direct)

## Summary

Long session. CH was offline so CC ran direct. Six discrete pieces shipped:

1. **CH boot redesign — THE_ONE_PIECE.md.** Six-step scavenger-hunt boot replaced with two project-knowledge files at `D:\CLAUDE_DF\` (THE_ONE_PIECE.md + ch-tool-operations.md). Tested across Sonnet 4.6, Opus 4.6, Opus 4.7 with three increasingly stringent prompts. Result: Opus 4.7 + identity framing finally held; Sonnet 4.6 hesitates. Locked Opus 4.7 as CH default, Opus 4.6 as comparison baseline.

2. **CH self-serve loophole — six-patch escalation.** Tested with build-request prompt ("bruh i keep having to open that jsonl file..."). Patches: code-boundary rule, mockup carve-out, build-request intent table row, dispatch-tree reorder, Preferences carve-out, generic project-folder reference. Each patch closed one rationalization path; CH found another. Final fix: identity rewrite ("Miru Dispatch Coordinator, not Lead Architect") + stated rationale ("audit trail violation") + named anti-patterns. Sourced from Perplexity research synthesized via Gemini. Worked on Opus 4.7. **The deeper issue is structural — CH should not have `dispatch_worker` at all.**

3. **Autonomy Overhaul Phases 3 + 4 canon sync.** Phases 3 (PR #88+#89, PRO-291, gateway profile enforcement live) + 4 (PR #90, W2 ingress classifier active) shipped 2026-05-05 but Notion + memory DB still showed them as not-shipped. Updated by CC under standing post-ticket canon authority. See "Canon updates" section below for full list.

4. **Dispatcher resurrection planning.** New Notion page filed at `https://www.notion.so/358c5d340141817c8ddae2f91a50a9c5` ("Dispatcher (Resurrected) — Local Router Architecture"). Old Phase 3/4 dispatcher pages marked HISTORICAL. Salvage report shows ~70% of needed infrastructure already built in `dispatcher/` directory (Ollama handler is production-grade, Flask shell + SQLite history reusable, prompt-injection prefilter from PRO-201 reusable). Worker roster slim-down: Cursor + Codex removed from dispatch (Cursor stays as IDE only, $60/mo cancellation in flight; Codex benched per 2026-05-04 connectivity gate). Gemini CLI promoted to primary frontend dispatch worker.

5. **PRO-298 — Recent-completions viewer (PowerShell surface).** Shipped via PR #92, squash-merged as `4bbb314`. Notable: original dispatch (cc-PRO-298-fa1c63d2-7ad26644) had actually written the script before the 600s timeout killed it — empty stdout was a Windows pipe-buffer flush issue on `taskkill`, not no-prompt-received. The retry's pre-flight found the uncommitted work; CC took it to completion directly rather than going through CH→dispatch chain again. Telegram surface deferred to PRO-299.

6. **CANARY-003 stall watcher loop closed.** Diagnostic canary I dispatched during dispatch failure investigation never wrote a completion marker (canaries just print + exit). Stall watcher fired Telegram alerts every 3 min trying to recover with worker="canary" (allowlist_reject). Wrote retroactive marker to break the loop.

## Canon updates (Notion + Memory DB + Repo)

**Notion (8 changes, 1 new page):**

- `356c5d34-0141-811e-8de5-d9e9be4175e5` Autonomy Overhaul Plan — Phase 3+4 marked SHIPPED with full per-phase shipped state, links section updated with dispatcher resurrection link
- `09bd7fc1-b3c4-43dc-a745-cbf109606ffa` 01 Now — top-of-page rewrite (hybrid pivot summary), worker roster 2026-05-05 update, decommission entry annotated
- `348c5d34-0141-813e-b730-d1412d7153f3` Worker Operating Baseline — new 2026-05-05 worker status section, Linear labels note about active dispatch roster
- `0fa04edc-caeb-416f-b51e-5854879dc932` Old Dispatcher Phase 3 page — HISTORICAL banner + link to new
- `346c5d34-0141-812c-8269-fe6791fe5cdf` Old Dispatcher Phase 4 page — title prefixed HISTORICAL/SUPERSEDED
- NEW: `358c5d34-0141-817c-8dda-e2f91a50a9c5` Dispatcher (Resurrected) — Local Router Architecture

**Memory DB (4 writes):**

- INSERT `decisions`: Phase 3 Subagent Isolation shipped (full PR/commit/test refs)
- INSERT `decisions`: Phase 4 Ingress Classifier shipped (full PR/commit/test refs)
- UPDATE `agenda` id `da44f4bc...`: Phase 3 planning task → status=done
- UPDATE `stack_state[phase]`: all 4 phases shipped, hybrid pivot evaluating
- (this session) INSERT `agenda`: Hybrid orchestration pivot Phase 1 (resurrect dispatcher in shadow mode), priority=1

**Repo:**

- `miru-context/THE_ONE_PIECE.md` — Crew table updated (Cursor/Codex removed from dispatch, Gemini promoted to primary frontend)
- `D:\CLAUDE_DF\THE_ONE_PIECE.md` — synced for CH project knowledge
- `tools/scripts/recent.ps1` + `tests/test_recent_ps1.py` — shipped via PR #92

## Linear

- PRO-298 — Done. PR #92 merged.
- PRO-299 — Backlog. Telegram /recent surface follow-up.

## Open / what the next session should do

**Hybrid orchestration pivot — Phase 1 (next sprint candidate):**

- Resurrect `task_dispatcher.py` on port 19000 (already built, just stopped per PRO-234)
- Strip `dispatcher/handlers/cursor.py` and `dispatcher/handlers/codex.py` (out of roster)
- Strip Slack-bolt code from `task_dispatcher.py` (no longer used)
- Bench Qwen 2.5 14B and Gemma 2 9B for routing decisions (ROOM has headroom, ~9GB Q4)
- Add classifier prompt + JSON schema for: build / query / brainstorm / triage
- Run in **shadow mode** — don't touch CH's tool profile yet
- Plan only. Implementation requires fresh approval gate.

**Phase 2 (subsequent):** add `cc_handoff` MCP tool that calls router instead of listener directly. Additive on CH (dispatch_worker still present). Test on real dispatches.

**Phase 3 (cutover):** remove `dispatch_worker` from CH's tool profile. CH only has `cc_handoff`. Self-serve loophole closes structurally.

## What NOT to do

- Do NOT start dispatcher resurrection implementation without explicit operator approval — planning only until then
- Do NOT re-introduce the old Workstreams A-E (file browser, runtime control, repo browser) when resurrecting — new role is dispatch-only
- Do NOT modify CH's tool profile (remove dispatch_worker) until the router is verified working in shadow mode
- Do NOT delete `dispatcher/handlers/cursor.py` or `codex.py` outside the dispatcher resurrection ticket — that's where the cleanup belongs
- Do NOT bundle the THE_ONE_PIECE.md repo edit into another commit unrelated to canon — it's a worker-rule file, eligible for direct-to-main if you want to land it solo
- Do NOT generate paste-ready content for the operator without wrapping in a code block (existing hard rule)

## Outstanding bug worth flagging

**Windows pipe-buffer flush on dispatch timeout kill.** When the dispatch listener kills a worker via timeout (PRO-298 trace cc-PRO-298-fa1c63d2-7ad26644 hit this at 21:47-21:57 UTC), the worker's stdout buffer doesn't flush before the process is terminated. Result: empty `.stdout.log` even though the worker did real work and wrote files. Listener should `SIGTERM` first to allow flush, then `SIGKILL` after a grace period. Not yet ticketed. Belongs in dispatcher reliability cleanup or as its own infrastructure ticket.

## Session ended on

- main repo `D:\dev\miru` — on `main` at `4bbb314` (PR #92 squash-merge), clean tracked working tree. Untracked files predate this session.
- worktree `D:\dev\miru-w1` — detached HEAD at `4bbb314`, clean. Ready for next dispatch's worktree-cleanliness gate.
- Linear PRO-298 → Done; PRO-299 → Backlog.
- All five services healthy (port 18766, 18080, 18765, 19100, 15678).
