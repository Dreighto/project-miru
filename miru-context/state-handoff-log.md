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

# Miru thread handoff — 2026-05-01 (CC session, documentation sprint)

## What we were working on

Full autonomous team operating model documentation sprint. 10 new miru-context docs +
3 existing file updates committed direct to main. This completes the knowledge architecture
foundation before Claude Chat takes over.

## What got done

- **10 new miru-context docs** — canon-contract, coordination-contract, job-stewardship,
  operating-model, operator-translation, source-of-truth, budget-governance, retry-backoff,
  kill-switch, performance-scorecard. All committed to main (commit 932dbaf).
- **worker-roster.md** — capability profiles added for claude-code, codex, gemini, cursor.
- **CLAUDE_CHAT.md** — 4 new priority session-start reads added (operating-model, canon-contract,
  job-stewardship, source-of-truth).
- **Cross-references** — canon-and-drift.md, claude-operating-model.md, concurrency-policy.md,
  CLAUDE.md all updated with pointers to new docs.
- **docs/dispatch_contract.md + services/dispatch_listener/src/allowlist.js** — These were
  modified at session start (pre-existing, not part of this sprint). Still uncommitted/unstaged.
  Next thread should check if they belong to a ticket and handle accordingly.

## What's still open

- **PRO-248** (Codex full system audit) — Backlog. Now has all context docs to work from.
  Awaiting operator confirmation to dispatch.
- **PRO-249** (n8n /snooze /unsnooze routing) — Backlog. Operator confirmation needed.
- **PRO-250** (Notion + Linear canon cleanup) — Backlog. After all docs exist (done now).
- **PRO-244** (Telegram inline action buttons) — Backlog. Not started.
- **PRO-247** (Gemini CLI sentinel fallback) — Backlog. Not started.
- **config/claude_chat.mcp.json** — Intentionally untracked. Do not commit.

## Decisions made

- All 10 docs committed direct to main (docs-only sprint, no service files touched)
- kill-switch.md defines the contract only — data/system_halt file NOT created yet
- budget-governance.md defines the contract only — data/budget_state.json NOT created yet
- Worker capability profiles added to worker-roster.md (not a separate file — routing and capability live together)
- CLAUDE_CHAT.md session-start list gets 4 new entries, not all 10 new docs

## What the next thread should do first

1. Ask operator: "Are you ready for me to dispatch PRO-248 (Codex audit) now that the context docs are complete?"
2. Check docs/dispatch_contract.md and services/dispatch_listener/src/allowlist.js — these pre-existing modifications need a decision (which ticket they belong to, or whether to discard).
3. Confirm git status is clean on main before any new work.

## What NOT to do

- Do not create data/system_halt — that file only exists when operator intends to halt the system
- Do not create data/budget_state.json — contract defined, implementation is a separate task
- Do not commit config/claude_chat.mcp.json (sensitive local paths, intentionally untracked)
- Do not start PRO-249 or PRO-250 without operator confirmation

## Loop health

Sentinel healthy. All_clear. No stalls. Pre-commit all green on this commit.

## Key files touched

- 10 new files in `miru-context/` (see commit 932dbaf)
- `miru-context/worker-roster.md`, `CLAUDE_CHAT.md`, `CLAUDE.md`, and 3 other cross-ref updates
- `data/cc_completion_log.jsonl` (completion marker appended)
