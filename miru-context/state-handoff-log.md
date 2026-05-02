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

# Miru thread handoff — 2026-05-02 (CC session, pre-handoff system testing)

## What we were working on

Canon cleanup + full pre-handoff system test of the autonomous dispatch pipeline. Every
major component was exercised end-to-end before handing off primary orchestration to Claude Chat.

## What got done

- **Canon cleanup** (commit a2e7a58) — Fixed 6 stale claims across 4 files:
  - `worker-roster.md`: auth column, Cursor dispatch pattern, capability profiles
  - `miru-service-catalog.md`: listener receipt label (INCONCLUSIVE, not CONFIRMED_WORKING)
  - `docs/dispatch_contract.md`: auth description (OAuth, no API billing)
  - `CLAUDE_CHAT.md`: Cursor manual relay dispatch pattern added
- **tools/test_dispatch.js** — Node.js HMAC dispatch test tool (commit a2e7a58); fixed bash HMAC failures
- **Worktree cleanliness gate** (commit b7ab5c8) — `tools/check_worktree_clean.py` created;
  CLAUDE.md and dispatch_contract.md updated with ordered FIRST ACTIONS pre-flight block
- **Dispatch system tests completed:**
  - Slot lease + worker spawn: miru-w1 ✓
  - Timeout enforcement: 5s timeout → FAILED + timed_out receipt + DLQ ✓
  - miru-w2 real ticket dispatch: slot assignment correct, no cross-contamination ✓
  - Parallel dispatch: two workers simultaneously, independent receipts ✓
  - n8n W2 routing: executed, scored workers, wrote routing_history, sent triage Telegram ✓
  - Dirty worktree gate: script exits 1 on dirty, exits 0 on clean ✓
- **Bugs filed:**
  - **PRO-258** (High): `routing_history.jsonl` null fields — W2 not writing diagnostic columns
  - **PRO-259** (Normal): Stale "awaiting" entries in `pending_callbacks.jsonl`

## What's still open

- **PRO-258** — W2 routing_history null fields. High priority, should be next dispatch.
- **PRO-259** — Stale pending_callbacks. Normal priority.
- **services/dispatch_listener/src/allowlist.js** — Has unstaged change (M). Likely from
  the earlier testing session. Check if it's intentional before committing.
- **n8n W4 leg** — W2→Telegram triage was verified; auto-dispatch path (W4 direct) not fully
  re-exercised this session. Last confirmed working 2026-04-30. Low risk.
- **Cold handoff test** — Fresh Claude Chat session with no briefing was deferred. Still pending.
- **config/claude_chat.mcp.json** — Intentionally untracked. Do not commit.

## Decisions made

- Cursor CLI is **permanently manual-only** — no W4 wiring ever. All files updated to reflect this.
- `CLAUDE_CODE_OAUTH_TOKEN` is the only auth path for spawned workers — `ANTHROPIC_API_KEY` stripped at spawn time.
- Dirty worktree gate added as step 2 in pre-flight (kill switch → worktree clean → heartbeat).
- Listener receipt `INCONCLUSIVE` for exit 0 is expected — completion log is authoritative, not the receipt.

## What the next thread should do first

1. Dispatch PRO-258 (W2 routing_history null fields) — High priority, claude-code.
2. Check `services/dispatch_listener/src/allowlist.js` diff — `git diff services/dispatch_listener/src/allowlist.js` — confirm if change is intentional or discard.
3. If ready: run the cold handoff test (fresh Claude Chat session, no briefing, read state-handoff-log.md and operate normally).

## What NOT to do

- Do not commit `config/claude_chat.mcp.json` (sensitive local paths, intentionally untracked)
- Do not commit `data/cc_completion_log.jsonl` without running the emit_completion script
- Do not dispatch to Cursor via W4 (permanent manual-only)
- Do not create `data/system_halt` or `data/budget_state.json` unless operator intends to activate those systems

## Loop health

All services healthy. miru-w1 and miru-w2 clean (git status verified). Pre-commit green on all
commits. Listener running on port 19100 ✓.

## Key files touched

- `tools/check_worktree_clean.py` (new)
- `tools/test_dispatch.js` (new)
- `CLAUDE.md` — worktree cleanliness gate section added
- `docs/dispatch_contract.md` — FIRST ACTIONS pre-flight block updated
- `miru-context/worker-roster.md` — auth + Cursor pattern corrected
- `miru-context/miru-service-catalog.md` — receipt label corrected
- `CLAUDE_CHAT.md` — Cursor manual relay pattern added
- `data/cc_completion_log.jsonl` (completion markers appended)
