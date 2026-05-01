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

_No handoff written yet. Claude Chat will write here at the close of the first thread that uses
this protocol._
