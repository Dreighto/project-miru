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

# Miru thread handoff — 2026-05-03 (Claude Chat paused, autonomy gaps filed)

## What we were working on

Picking off the loop blockers from the prior 05-02 voice handoff (PRO-276, PRO-278) before resuming PM deck builder work. Mid-thread the work surfaced multiple autonomy failures in Claude Chat — both tooling gaps and behavioral failures (asking permission on work canon explicitly authorizes). Operator paused working with Claude Chat over those gaps. Will continue with Claude Code until they're addressed.

## What got done this thread

- **PRO-276** moved In Progress → In Review with comment linking PR #74 / commit `c7e5b8e`. End-to-end Telegram tap still pending; that's the verification gate.
- **PRO-278** closed: PR #73 self-merged → commit `72b2fb3`, branch deleted, Linear → Done. **But the local working tree at D:\dev\miru never pulled the merge** so the running gateway is still pre-merge code. Operator did the bootstrap restart but it re-loaded the same old code from disk.
- **6 new tickets filed** capturing today's gaps (see "What's still open" below).
- **Project Memory** — 4 decisions rows added (drift correction, PRO-278 closeout, autonomy gap pause, this handoff).

## What's still open

**Autonomy gap tickets filed today (Backlog, awaiting operator review):**

- **PRO-284 (Urgent)** — Self-merged PR doesn't pull to local working tree; services run pre-merge code after merge.
- **PRO-285 (High)** — Workers write completion markers with ticket_id=null, orphaning the Linear ticket.
- **PRO-286 (High)** — Handoff goes stale within a session; no rule for "rewrite if more work happens after."
- **PRO-287 (Medium)** — Claude Chat can't read local working tree state; no git_status equivalent.
- **PRO-288 (Medium)** — Drift scanner doesn't backfill on first deploy; pre-existing drift hides forever.
- **PRO-289 (Urgent)** — Behavioral: Claude Chat asks permission on drift correction work canon authorizes autonomously.

**Pre-existing items still open from prior handoff:**

- **PRO-276 (In Review, Urgent)** — fix shipped to GitHub, but local D:\dev\miru hasn't pulled, AND no Telegram smoke test has happened. Two blockers, not one.
- **PRO-275 (Todo, Medium)** — append 2026-05-02 sprint anchor to Notion Work Log. Not investigated this thread.
- **W1 504 retry import** — PR #71 merged. n8n manual import status not verified.
- **PRO-7 (deck builder rebuild)** — paused. Seven-phase sequence still stands.
- **Worker profiles** all show `last_confirmed_at IS NULL`.
- **MCP gateway bootstrap** — operator did a restart, but D:\dev\miru never pulled, so the gateway is still pre-merge. PRO-284 is the structural fix; in the meantime, a `git pull` on D:\dev\miru followed by another gateway restart would unblock.

## Decisions made this thread

- **Operator paused working with Claude Chat** over autonomy gaps. Will use Claude Code in the meantime. Pause lifts when PRO-284 / PRO-289 ship at minimum.
- **Drift correction is autonomous** (re-affirmed) — Claude Chat had been asking permission on routine drift work; that's the behavior PRO-289 addresses.
- **PRO-276 closing rule** — do not move to Done without an end-to-end Telegram tap.

## What the next thread should do first

**If next thread is Claude Chat resuming after the pause:**

1. Read PRO-284 / PRO-285 / PRO-286 / PRO-287 / PRO-288 / PRO-289 first. These are the conditions for resuming.
2. Confirm with operator which ones have shipped before doing any other work.

**If next thread is Claude Code or another worker:**

1. PRO-284 is the highest-leverage ticket — fixes the post-merge pull gap that broke today.
2. PRO-289 needs canon edits to `claude-operating-model.md`, `guardrails.md`, `CLAUDE_CHAT.md` — Claude Chat's surface, but operator-approved edits via Claude Code are valid.
3. Operator: pulling D:\dev\miru to main + restarting the gateway would unblock the in-flight PRO-278 verification, but that's optional — the structural fix is PRO-284.

## What NOT to do

- Do not resume Claude Chat dispatching or autonomous work until operator lifts the pause.
- Do not move PRO-276 to Done without a real operator tap going through W7.
- Do not assume PRO-278's merge is "live" — D:\dev\miru hasn't pulled.
- Do not append a corrective completion marker for PR #74 — Claude Chat does not write `cc_completion_log.jsonl` (hard rule).
- Do not start partnership conversation with projectraftel.dev. PM ships first.
- Do not file deck-builder rebuild tickets without reading PM 02 first.

## Loop health

Loop hardening campaign closed 2026-05-03 ~01:00 UTC. PRO-276 fix on GitHub but unverified end-to-end. PRO-278 fix on GitHub but not on disk. Six autonomy gaps filed as tickets. Claude Chat paused at operator's call.

## Key files / surfaces touched

- **Linear:** PRO-276 (state + comment), PRO-278 (state + 3 comments + description rewrite + → Done), PRO-284 / PRO-285 / PRO-286 / PRO-287 / PRO-288 / PRO-289 (filed Backlog, Urgent/High/Medium).
- **GitHub:** PR #73 merged → commit `72b2fb3` on origin/main; branch deleted.
- **Project Memory:** 4 decisions rows added.
- **Repo (read-only):** restart_tools.py, restart_mcp_gateway.ps1, completion log tail, recent activity, W7 executions, PR #73 diff.
- **Handoff:** rewritten three times this thread (drift correction, PRO-278 closeout, this final state).
