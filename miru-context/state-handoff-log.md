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

# Miru thread handoff — 2026-05-02 (CC session, ops tooling + watchdog sprint)

## What we were working on

Operator-facing ops tooling sprint: redesigned the weekly Telegram digest to plain English,
built the n8n loop watchdog, fixed budget display, cleaned up Linear, and updated all MD files.
Session ended with operator requesting iPhone SSH access to run Claude CLI remotely.

## What got done

- **PRO-258 (merged, #67)** — W2 routing_history null fields fixed; file-extension scoring
  added so `.py` → claude-code, `.js/.html/.css` → cursor.
- **PRO-251 ops digest redesign (merged, #68)** — `tools/ops_digest.ps1` and W7 jsCode
  completely rewritten: emoji sections, plain-English step names, relative timestamps,
  TEST-\* filtering, 15-min active worker window.
- **Budget display fix (merged, #69)** — `Format-Budget` (ops_digest.ps1) and `budgetText`
  (W7 jsCode) both now handle the legacy array format from `miru_limits_status.json`.
  Stale budget timestamps refreshed from 2025 → 2026-05-02.
- **PRO-230 n8n loop watchdog (merged, #70)** — `tools/n8n_loop_watchdog.py` +
  `windows/register_watchdog_task.ps1`; standalone stdlib-only Python, 4-pass detection
  (failing/unstable/silence/recurring), state-change-only Telegram alerts with 60-min cooldown.
- **Linear cleanup** — PRO-78 and PRO-248 marked Done; PRO-230 closed via PR merge.
- **PRO-261 filed** — stall detector fires on Done tickets with stale heartbeats; Normal priority, Backlog.
- **DLQ analysed** — 108 entries, 65 from April 26 initial-setup noise (one-time). Not a crisis.
  Root cause of recent entries: stall detector / PRO-261 (see above).

## What's still open

- **PRO-261** — stall detector: skip Done tickets before queuing DLQ. Normal priority, Backlog.
- **PRO-259** — pending_callbacks stale awaiting entries (58 of 61 expired on Telegram side).
  Low risk. Backlog. W2 watchdog should write expiry entries for >48h awaiting callbacks.
- **PRO-154** — W1 504 retry-with-backoff. Still Todo, valid, no blocker.
- **MiruN8nWatchdog task** — watchdog is merged but task registration (`register_watchdog_task.ps1`)
  needs running once from an **elevated** PowerShell shell on the ROOM node.
- **MiruOpsDigest task** — `windows/register_ops_digest_task.ps1` needs running from elevated shell
  (weekly Friday 9am digest). Script already exists.
- **`miru_limits_status.json` usage values** — timestamps refreshed but actual remaining_percent
  values are approximate. Operator should verify at cursor.com/settings (Cursor Pro) and
  platform.openai.com/usage (Codex/OpenAI).
- **`services/dispatch_listener/src/allowlist.js`** — has an unstaged tracked change (M in git status).
  Verify with `git diff services/dispatch_listener/src/allowlist.js` before next commit.
- **`config/claude_chat.mcp.json`** — intentionally untracked. Do not commit.

## Decisions made

- Ops digest and W7 both show budget in "Safe — cursor 85% left · codex 60% left" format
  (computed from array; `{state}` format supported for future migration).
- n8n_loop_watchdog is stdlib-only Python (no pip deps) so it runs in any environment.
- DLQ entries from April 26 are historical noise; not worth clearing. The real fix is PRO-261.
- PRO-259 pending callbacks: expired entries are harmless (Telegram rejects them); structural
  fix deferred to a future worker dispatch.

## What the next thread should do first

1. Confirm MiruN8nWatchdog task is registered — run `Get-ScheduledTask -TaskName MiruN8nWatchdog`
   in PowerShell; if missing, run `register_watchdog_task.ps1` from elevated shell.
2. Check `services/dispatch_listener/src/allowlist.js` diff — confirm change is intentional or discard.
3. Dispatch PRO-154 (W1 504 retry-with-backoff) — next real code ticket in the queue.

## What NOT to do

- Do not commit `config/claude_chat.mcp.json` (sensitive local paths, intentionally untracked)
- Do not commit `data/cc_completion_log.jsonl` directly — use `tools/emit_completion.py`
- Do not dispatch to Cursor via W4 (permanent manual-only)
- Do not create `data/system_halt` unless operator intends to halt autonomous work
- Do not clear `data/dispatch_dlq.jsonl` — it is append-only; historical entries are expected

## Loop health

All PRs merged as of 2026-05-02. Local main pulled and clean (untracked files only).
Listener on port 19100 ✓. Budget: Cursor ~85% left, Codex ~60% left (unverified).

## Key files touched

- `tools/ops_digest.ps1` — full rewrite (operator-friendly format)
- `docker/n8n/workflows/w7-telegram-callback-handler.json` — jsCode rewritten (operator-friendly)
- `data/miru_limits_status.json` — timestamps refreshed
- `tools/n8n_loop_watchdog.py` (new)
- `windows/register_watchdog_task.ps1` (new)
- `miru-context/miru-service-catalog.md` — n8n loop watchdog section added
- `data/cc_completion_log.jsonl` (completion markers appended)
