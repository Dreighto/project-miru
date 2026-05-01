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

# Miru thread handoff — 2026-05-01, late evening (CC session)

## What we were working on

Knowledge architecture pass: building ground-truth reference documents for AI workers so
they have real domain knowledge, not just instructions. Also fixing a sentinel bug and
hardening the Ollama health check to use structured JSON output.

## What got done

- **`miru-context/miru-service-catalog.md`** — Created. Per-service ground truth for all 5
  active services: ports, correct health endpoints, log file paths, normal vs failure log
  patterns, restart mechanisms. Extracted from actual source files.
- **`miru-context/miru-protected-constraints.md`** — Created. Non-negotiable architectural
  constraints for coding workers: reserved ports, append-only files, Telegram webhook ownership,
  DB read-only rule, health endpoint contracts, git hygiene.
- **`tools/sentinel/health_check.py`** — PM health endpoint fixed (`/health` → `/__pm_health`).
  Ollama prompt rebuilt with domain-grounded system prompt, explicit allow/deny lists,
  few-shot boundary-case examples, `format: json` structured output. Now parses
  `should_escalate` boolean from JSON response.
- **PR #64** — Opened (operator-merge, new files). All hooks green. Sentinel confirmed
  `all_clear` with new JSON parsing.
- **PRO-248** — Filed: Codex full code audit + system check before Claude Chat handoff
- **PRO-249** — Filed: n8n route /snooze and /unsnooze Telegram commands (Backlog)

## What's still open

- **PR #64** — Needs operator merge before Codex audit can start
- **PRO-248** (Codex full system audit) — Filed, Backlog. Do NOT start until PR #64 merged
- **PRO-249** (n8n snooze routing) — Filed, Backlog. Operator confirmation needed
- **PRO-244** (Telegram inline action buttons) — Filed, Backlog. Not started
- **PRO-247** (Gemini CLI sentinel fallback) — Filed, Backlog. Not started
- **"Things to work on before Claude Chat"** — Operator mentioned items still pending.
  First action for next thread: ask if PR #64 + Codex audit clears the list, or what remains

## Decisions made

- PM Dashboard health endpoint is `/__pm_health`, not `/health` (SPA catch-all was masking this)
- Ollama prompts need domain grounding + JSON output schema — freeform "start with ALERT:" is too fragile
- `should_escalate: bool` is the right routing signal — not text scanning
- Codex is the right worker for the full system audit (deep static analysis, large codebase)
- Codex audit should run AFTER PR #64 merges so it has the context docs

## What the next thread should do first

1. Confirm PR #64 merged; if yes, dispatch PRO-248 to Codex
2. Ask operator: "Does PRO-248 cover what you meant by things to do before Claude Chat handoff, or are there other items?"
3. Check if docs/dispatch_contract.md and services/dispatch_listener/src/allowlist.js (modified at session start, not part of this work) need attention

## What NOT to do

- Do not start PRO-248, PRO-244, PRO-247, or PRO-249 without operator confirmation
- Do not merge PR #64 as CC — it contains new files (operator-merge column)
- Do not touch config/claude_chat.mcp.json (sensitive local paths, intentionally untracked)

## Loop health

Stall recovery loop healthy. Sentinel running every 20 minutes, all_clear. New JSON output
format confirmed working.

## Key files touched

- `miru-context/miru-service-catalog.md` (new)
- `miru-context/miru-protected-constraints.md` (new)
- `tools/sentinel/health_check.py` (PM endpoint + prompt rebuild)
- PR #64: https://github.com/Dreighto/project-miru/pull/64
