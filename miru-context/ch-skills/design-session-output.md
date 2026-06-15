# Skill: design-session-output

## When this skill applies

After a brainstorm/architect session produces a real architectural decision,
new system component, or strategic canon entry — and the operator has confirmed
the decision is settled. This is the ONE Notion-write path you (CH) retain as
of 2026-05-17.

You are NOT the default Notion writer anymore for routine work (factual
corrections, port updates, post-ticket sync, maintenance). Those go to CC.
This skill is for brainstorm-result writes only.

## The decision rule

Before drafting a Notion write, ask yourself:

1. Did this come out of an architect-mode / brainstorm session? → continue.
2. Is it a routine update or post-ticket sync? → STOP, route to CC.
3. Is the operator already agreed on the wording? → continue.
4. Am I still inside the brainstorm session? → STOP, finish the design first.

If you're uncertain whether a write is brainstorm-result or routine, ask the
operator once: "Brainstorm output or routine update?" and act on the answer.

## Output template — Notion page or page section

When writing brainstorm-result output to Notion, use this skeleton:

```markdown
## [Decision name — plain English]

**Decided:** [YYYY-MM-DD]
**Decided by:** [Operator + CH design session]
**Source of decision:** [thread title / link / brief session summary]

### What we decided

[1-3 sentences. Plain English. No jargon. What the decision is.]

### Why

[2-4 sentences. The trade-off considered. Why this option won over
alternatives.]

### What this affects

- [System / file / surface affected — bullet list]
- [Linear projects this touches]
- [Workers that need to know]

### What changes from prior state

- Before: [what was true]
- After: [what's true now]

### Source-of-truth pointer

This decision lives in: [Notion page link or path]
Related canon files: [paths if applicable]

### Open questions / deferred

- [Anything intentionally deferred]
- [Anything that needs follow-up]
```

## When to update existing Notion pages vs create new

**Update existing pages when:**

- The decision modifies a documented architectural choice already on a page.
- The decision is a "supersedes" of a prior decision — note the supersede on the
  existing page, don't fork.

**Create a new page when:**

- The decision introduces a new system component, lane, or surface.
- The decision is broad enough that scattering it across existing pages would
  lose coherence.
- The operator explicitly asks for a new page.

When in doubt: update existing, link to it. Notion proliferates fast and stale
pages are worse than dense ones.

## Source-of-truth hierarchy (when writing into Notion)

Per the kernel canon at `D:\dev\LogueOS-Orchestrator\.logueos\reference\source-of-truth.md`:
Runtime > Audit logs > Linear > Repo (code/canon/DB) > Notion > Worker memory

> Conversation context.

Notion is a **derived mirror**, not the source. When you write a decision to
Notion, make sure the actual source (a Linear ticket, a kernel canon file, a
code change) reflects it too. If you write to Notion but not to the source,
that's drift — and drift is what we just spent weeks fixing.

## What to do after writing

1. **Write the decision to `logueos_memory.db`** via the `write_query` MCP tool.
   Schema: `decisions` table, with a `source` field pointing at the Notion page
   you just wrote and `supersedes` if applicable.
2. **Mention the write to the operator** in one line. Don't list everything you
   put on the page — link it.
3. **If the decision implies follow-up work**, file the Linear ticket(s).
   Always include `projectId`.

## Anti-patterns

- Writing routine factual updates to Notion (that's CC's lane now).
- Forking a new page when an existing page covers the topic.
- Pasting a transcript of the brainstorm session as the Notion content.
  Synthesize it. The operator already read the transcript.
- Writing the decision to Notion but forgetting to log it in `logueos_memory.db`.
- Writing the decision without checking source-of-truth alignment (Notion ≠ source).
