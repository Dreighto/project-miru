# Canon and Drift — Source Order, Drift Detection, and State Preservation

## Governing Rule

Already-approved work carries forward across threads unless new evidence changes the plan.

A new thread is not a new decision. If the operator approved a direction — a bug fix, a refactor, a routing pattern, a design choice — that approval stands until the operator changes it or new information makes it wrong. Claude does not re-ask about already-agreed work just because the conversation restarted.

Claude only asks for approval when something is genuinely new, risky, irreversible, or unclear. Routine continuation of approved work is not any of those things.

---

## Source-of-Truth Hierarchy

When sources disagree, this is the authority order. Higher wins:

1. **Operator's live instruction** — what the operator says right now, in this conversation
2. **Notion canon pages** — architecture, decisions, design, reference material. This is where "why we decided X" lives.
3. **Repo documentation** — PROJECT_MIRU_INSTRUCTIONS.md, AGENTS.md, WORKFLOW_MAP.md, startup files in miru-context/. These are the operating rules.
4. **Linear tickets** — execution state, task descriptions, status. This is "what's being worked on."
5. **Project Memory (miru_memory.db)** — decisions log, agenda, routing history, stack state. This is the memory of what happened.

**Key distinction:** Notion and Linear are sources of truth. Project Memory is a log of changes and decisions. If memory says one thing and Notion says another, Notion wins — then memory gets updated to match.

---

## Drift Detection

Drift is when different surfaces disagree about what's true. Claude should watch for these patterns:

### Common Drift Patterns

| Pattern                  | Example                                                                          | How to catch it                                                     |
| ------------------------ | -------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Shipped but not updated  | PRO-208 merged, but 01 Now still describes the old behavior                      | After any ticket moves to Done, check if Notion reflects the change |
| Decided but not recorded | Operator approved Haiku for the router, but no decisions row exists              | After any architecture call, check Project Memory for the decision  |
| Stale ticket description | PRO-202 description still has the old mega-scope after decomposition             | After re-speccing a ticket, re-read it to confirm the update landed |
| Memory vs reality        | stack_state says "PRO-202 stuck" but PRO-202 was re-specced and moved to Backlog | At thread start, cross-check stack_state against Linear             |
| Repo doc vs Notion       | WORKFLOW_MAP.md says W2 has 35 nodes but Notion says 38                          | After any workflow change, check both surfaces                      |
| Thread reset drift       | New thread re-asks about a direction the operator already approved last thread   | Check Project Memory decisions and the handoff log before asking    |

### When to Check

- **At thread close** (mandatory): scan all four surfaces against the thread's work before drafting the handoff. This is the canon hygiene rule.
- **After a ticket ships**: verify Notion and memory reflect what actually happened, not what was planned.
- **After a decision**: verify it's logged in Project Memory with the right supersedes chain.
- **At thread start**: cross-check stack_state and recent decisions against Linear and Notion. Don't re-ask the operator about things that are already settled — just verify they're still accurate and keep moving.
- **When something feels wrong**: if Claude's context doesn't match what the tools return, that's drift. Investigate before proceeding.

### How to Fix Drift

For routine corrections (typo, stale status, simple update): fix it directly. No need to ask.

For judgment calls (two sources say different things and it's not clear which is right): surface both versions to the operator, explain which one Claude thinks is correct and why, and wait.

---

## State Preservation Rules

### What Must Be Preserved Across Threads

- Active decisions (stored in Project Memory decisions table)
- Current stack state (stored in Project Memory stack_state table)
- Active agenda items (stored in Project Memory agenda table)
- Operator direction phrases that were given as standing instructions ("you're driving," approved patterns)
- The current working direction — what we're building, what's next, what's already approved

### What Can Be Compacted

- Completed routing decisions older than 7 days (keep the decision, drop the detail)
- Resolved agenda items (mark done, don't delete)
- Shipped ticket details (Linear and Notion hold the history — memory doesn't need to duplicate)

### What Gets Re-Read (Not Stored)

- Full Notion page content (read at thread start, not cached in memory)
- Linear ticket details (queried live, not stored)
- Repo file contents (read via MCP when needed)

---

## Context Compaction

When a thread's context grows large, or when writing a handoff summary, Claude compacts information using this structure.

### When to Compact

- At thread close, when drafting the handoff
- When a topic has been fully resolved and the details are no longer needed for active decisions
- When Project Memory has multiple rows about the same topic that can be consolidated

### Compaction Template

When summarizing a resolved topic or decision chain, use this format:

```
**Intent:** [What we were trying to do — one sentence]
**Decisions:** [What we decided — bullet list, max 3 items]
**Current direction:** [What's approved and should continue — one sentence]
**Next steps:** [What follows from this — max 2 items, or "none, this is closed"]
**Key files:** [Repo files, Notion pages, or Linear tickets that hold the full detail]
**Pointers:** [Project Memory row IDs, if relevant, so the next thread can look up the full record]
```

### Compaction Rules

- Never delete detail — compress it. The full record should always be recoverable from the pointers.
- Don't compact active work. Only compact things that are resolved or deferred.
- Don't compact decisions. Decisions are append-only in Project Memory and should never be summarized away.
- **Keep the current direction intact.** When compacting, always preserve what was approved and what should continue. The next thread should be able to read a compacted summary and keep moving without re-asking the operator. If the summary loses the direction, the next thread resets — and that's the failure mode we're preventing.
- When in doubt, keep more context rather than less. It's cheaper to skip past extra context than to lose something important.

---

## Memory Naming Convention

Two memory systems exist. Disambiguate clearly:

- **Personal Memory** = Anthropic's memory system. Lives in Claude's context every conversation. Holds preferences, identity, cross-project context.
- **Project Memory** = miru_memory.db on ROOM. Queryable via Miru MCP. Holds Miru-specific decisions, agenda, routing history, stack state.

Disambiguation:

- "memory" alone → ambiguous, ask which one
- "project memory" / "server memory" / "miru memory" / "the db" → Project Memory
- "your memory" / "personal memory" → Personal Memory
- Mid-task "log this" → defaults to Project Memory
