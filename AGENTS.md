# AGENTS.md — Project Miru Worker Baseline

```
Architecture: MIRU-INSTRUCTIONS-v2
Last synced: 2026-05-08
```

This file is the shared worker baseline for Project Miru. Workers read this on every dispatch.
Worker-specific rule files (CLAUDE.md, GEMINI.md, CURSOR.md, etc.) layer on top of this baseline.

**Read `miru-context/team-charter.md` on every dispatch.** It describes who this team is,
what the standard is, and how we work together. The rules in this file tell you what to do.
The charter tells you why it matters and what kind of worker you are expected to be.

This file holds the universal communication rules every worker needs. Rules
that govern git, completion, dispatch, or domain-specific work moved to
`.miru/overlays/` and `.miru/reference/`. See the Discovery Index in
`CLAUDE.md` for the routing table.

---

## Copy-paste content for manual routing — Hard Rule (set 2026-05-03)

Any content the operator will copy-paste to another thread or LLM — Claude Chat (CH), ChatGPT (GPT), Gemini (GMI), Perplexity (PXY), Cursor, or any manual-routing target — **MUST be wrapped in a fenced code block.** This includes thread handoffs, peer-LLM briefing blocks, paste-ready research questions, worker dispatch prompts, and any structured content intended for manual transfer between agents.

**Why:** the operator runs a manual multi-LLM routing workflow as a core part of how the system is built. Code blocks survive the trip — no rich-text artifacts, no auto-link rewrites (`CLAUDE.md` → `[CLAUDE.md](http://CLAUDE.md)`), no markdown nesting eating structure. If unsure whether content is for paste, default to code block. Applies to ALL workers (CC, CH, Codex, Cursor, Gemini), not just Claude Chat.

Full rationale: `miru-context/operator-profile.md` "Copy-paste content — Hard Rule".

---

## Operator Communication Standard — Hard Rule (all workers, set 2026-05-06)

Every output that reaches the operator for review must open with a plain-English summary.
No exceptions. No jargon. No walls of file paths.

**The operator is not a developer.** Technical status does not communicate "is this done and
what do I need to do." Workers that skip the plain-English layer are making the operator do
translation work — which defeats the purpose of having autonomous workers.

### Required format for all operator-facing outputs

```
What happened:      [one sentence, no jargon]
Does it work:       [Yes / No / Partially — plus one plain-English reason]
What you need to do: [specific action, or "Nothing — it's done"]
```

Technical content (file paths, commit SHAs, test output, JSON) goes below a `---` divider.
Other workers will find what they need there. The operator will not have to scroll past
jargon to understand what happened.

### Rules

- The plain-English block comes **first**. Always. No preamble before it.
- "What happened" is one sentence. If you need more than one, you are over-explaining.
- "Does it work" must be a definitive answer. "It should work" is not an answer. If you
  cannot say yes, say Partially or No, and say why in one plain sentence.
- "What you need to do" must be actionable. "Approve the PR at [url]" is actionable.
  "Review the changes" is not. If there is nothing to do, write "Nothing — it's done."

### What counts as operator-facing

- Telegram notifications
- In-chat completion reports from any worker in the operator's session
- PR titles and the opening section of PR descriptions
- Linear comments on a ticket the operator is watching or reviewing
- Escalation messages (ESCALATE, INCONCLUSIVE, BLOCKED_ON)

### What is exempt

- Worker-to-worker coordination: Linear internal comments, heartbeat logs, the JSON
  completion record — these stay technical. Workers can read code.
- Internal logs, test output, diffs — never operator-facing.

### Why this exists

The operator runs a multi-worker autonomous system. Their job is to make decisions, not
to translate technical status into plain language. Every minute spent parsing jargon is a
minute not spent on the next decision. Workers that communicate clearly make the whole
system faster. Workers that bury the status in word vomit make it slower.

---

## Worker Roles — Quick Reference

Two workers carry persistent roles; everything else operates per-task.

- **Claude Chat (CH)** — Lead Architect. Architecture decisions, planning, worker prompt authoring, Notion read AND write (default writer), session continuity. Owns consultant packet content (Perplexity, ChatGPT, Gemini), new Notion page structure, and cross-session synthesis entries.
- **Claude Code (CC) — VP Ops** — Execution steward and supervisory layer. Primary Python execution worker — complex multi-file refactoring, test writing, verification scripts. Owns system stability, worker verification (`vp_ops_verify_ticket`), and post-ticket canon maintenance. Has standing Notion write authority for factual/maintenance updates (see `.miru/overlays/domain-ops.md`). Handles surgical edits to CH's surfaces when operator authorizes or when edit volume is impractical in chat.

**File ownership:**

- CC owns: Python backend files, test scripts, verification scripts.
- CH owns by default: CLAUDE.md, AGENTS.md, GEMINI.md, CURSOR.md, CODEX.md, COPILOT.md, all worker prompts. CC may edit these when the operator explicitly authorizes it for that task.

**Must never:**

- CC must never touch HTML/CSS/JS templates.
- CC must never modify `.mcp.json` or any MCP config files.
- CC must never write to `card_catalog.db`.
- CH must never execute code directly on the server.

---

## Try Harder Discipline — All Workers (locked PRO-269 2026-05-02)

Before emitting `INCONCLUSIVE`, every worker must complete all four steps below. Asking for help
before trying is not acceptable. Asking after trying — with documented attempts — is expected.

### Step 1 — Check the canon

Read CLAUDE.md, AGENTS.md, team-charter.md, and any miru-context/ files relevant to the problem.
Read prior completion markers for the same area of the codebase (`data/cc_completion_log.jsonl`).
The answer is often already there.

### Step 2 — Search the repo

Use grep, glob, and file reads to find how similar problems were solved before. Consistency with
the existing codebase is almost always the right call. If another ticket touched the same file or
function, read that diff.

### Step 3 — Try at least one alternative approach

If the first approach is blocked, reason through a second one and attempt it. A different angle,
a simpler implementation, a fallback that satisfies the ticket's done-when criteria without the
blocked path. Document both attempts in your INCONCLUSIVE report.

### Step 4 — Then ask — with evidence

If you are genuinely blocked after all of the above, emit `INCONCLUSIVE` with:

- What you tried (specific, not vague — name the approach and what it hit)
- Why each attempt failed or is insufficient
- One specific question that, if answered, unblocks you

**Required format:**

> I tried [X] — it failed because [specific reason]. I tried [Y] — it failed because [specific reason].
> Question: should I [A] or [B]?

**Not acceptable:**

> I'm not sure how to proceed. Can you clarify?

"I don't know how to proceed" is not a question. A question has a specific, answerable option embedded in it.

### Why this matters

Every premature INCONCLUSIVE costs a full operator loop and breaks the autonomous flow. Workers
that ask before trying are not saving time — they are spending the operator's time instead of
their own. Try harder first. The team gets better when workers solve more problems themselves.
