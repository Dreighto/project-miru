---
name: locked-design-ticket
description: Author a locked-design ticket for handoff to a sub-worker (Codex, Cursor, Gemini, or any dispatched worker). Use when the operator says "send Codex a ticket", "draft a ticket for X", "prep a handoff", "give Codex something to do", "write up the spec for the next worker", "lock the design and hand it off", or asks for a ticket that another agent will execute. Triggers include locked design, ticket for Codex, ticket for Cursor, ticket for Gemini, ticket for the worker, draft a ticket, hand off, prep a handoff, write the spec, ticket description, dispatch ticket, sub-worker ticket, the next worker, scoped ticket. Do NOT use for filing routine Linear bugs that don't involve worker handoff, for tickets the operator will execute directly, or for the operator's own todo list.
---

# locked-design-ticket

Authoring tickets for sub-workers is a specific skill that's gone wrong before — the PRO-180 retro made it an adopted lesson. The design has to live in the ticket description, not in the prompt wrapper, because the prompt is ephemeral and the ticket survives session restarts.

## Required reading before authoring

1. `.miru/overlays/adopted-lessons.md` — "Lock design in the Linear ticket description, not in the prompt wrapper" (PRO-180 retro). Your output must conform to this rule.
2. `AGENTS.md` — Operator Communication Standard. The PR title and ticket title must follow the plain-English-first format.
3. `.miru/overlays/workflow-git.md` — PR merge tier policy. The ticket should declare which tier the change is in (direct-to-main, CC-merge, operator-merge).
4. `.miru/overlays/workflow-completion.md` — completion contract. The ticket must specify what the worker emits at terminal state.
5. `.miru/reference/linear-projects.md` — the projectId table. Every Linear ticket needs a projectId.
6. The worker briefing for the target worker if one exists (e.g., for Codex, the most recent briefing posted to the operator).

## What the ticket MUST contain

These are non-negotiable. A ticket missing any of these is a discipline violation per PRO-180 — the worker session will not have enough context to execute cleanly.

1. **Title** — short, plain-English. No jargon. Example: "DGAS Tier 1: localhost-bind full_operator MCP profile". Not: "Implement RFC-2026 controlplane intent verification middleware."
2. **Why this matters** — one paragraph the operator could read out loud and understand. No code, no jargon. What gap closes? What's the cost of not doing it?
3. **Scope** — exactly which files change, what the change boundary is. If you can't list files, you haven't pre-investigated enough.
4. **Locked design** — schema, rules, ordering, transport details, error response format. Specific enough that a worker who's never seen this code can implement it without making design decisions.
5. **Don't-touch list** — files or patterns the worker MUST NOT modify. Includes anything in the governance file registry, anything that would expand the ticket's blast radius, anything that's a separate ticket's scope.
6. **Done-when criteria** — measurable, enumerated, testable. Each item is a check the worker (or you) can verify objectively. "Tests pass" is not done-when. "All 7 test cases listed in step 5 pass and the existing tests/test_phase3_denial.py still passes" is.
7. **Investigation steps** — what the worker should do if any spec gap remains. Don't fill spec gaps silently in the ticket; spell out what to do when the worker can't proceed.
8. **Completion contract** — exactly what the worker emits at terminal state. Include the operator-facing completion message format (per AGENTS.md Operator Communication Standard).
9. **Escalation rules** — what conditions trigger `STATUS: ESCALATE: <category>`. The worker must know when to stop and ask.
10. **Deferred to follow-up** — what's explicitly NOT in this ticket. Prevents scope creep and gives the worker permission to ignore tempting adjacent improvements.

## Authoring workflow

Run all four phases in order. The pre-investigation step is what separates a locked design from a wishlist.

### Phase 1 — Pre-investigate (required, no shortcuts)

Goal: every file:line reference, test pattern, and error format in the ticket must come from the actual code, not memory or guess.

1. Read the synthesis doc or feature spec the ticket implements.
2. Read the existing code at the change point. Find the function, class, line range that's the actual locus of the change.
3. Read 1–2 nearby tests. The ticket will reference them as "mirror this style." If no nearby tests exist, that's a flag — the worker may need to set up a new test pattern.
4. Identify the test fixture pattern. Examples in this repo: `_make_cfg` in `tests/test_phase3_denial.py`, contextvar manipulation patterns.
5. List every file the worker will touch. Cap at ~5 files / ~300 LOC. Over that, split into multiple tickets.
6. Identify what could break that ISN'T in scope. Each becomes a "don't-touch" entry.

If any step turns up "I don't know" or "I'd have to look at it" — go look. The ticket cannot be locked while the design has unknowns.

### Phase 2 — Draft the ticket

Use the template below. Adapt to the specific change. Do not abbreviate the don't-touch list — that's where most worker sessions go off the rails.

### Phase 3 — Self-review

Before handing the ticket to the operator (who will paste it to the worker), read it as if you were the worker:

- Could a worker who's never seen this code base section implement this from the ticket alone?
- Is every file:line reference real, or am I asking the worker to "look in the gateway code somewhere"?
- Are the done-when criteria objectively measurable?
- Is the completion contract specific enough that I'll know whether the worker actually finished?
- Does the don't-touch list cover the ways a worker could expand scope?
- If a worker needs to escalate, do they know the exact category to use?

### Phase 4 — Save and hand off

1. Save the ticket as `data/peer_reviews/<date>_<worker>_ticket_<slug>.md`. Same naming pattern as existing tickets.
2. Wrap the entire ticket body in a fenced code block (per CLAUDE.md core copy-paste rule) so the operator can paste it cleanly without rich-text artifacts.
3. Tell the operator: "Ticket saved at <path>. Hand to <worker> when ready."
4. Do NOT dispatch the worker yourself. The operator paces the dispatch.

## Ticket template (adapt, don't abbreviate)

```text
LINEAR TICKET — <Plain English Title>

Title: <under 70 chars, plain English, no jargon>
Project: <project name from .miru/reference/linear-projects.md>
Project ID: <projectId>
Type: Bug | Feature | Improvement | Chore
Priority: 0 (Urgent) | 1 (High) | 2 (Medium) | 3 (Low) | 4 (Backlog)
Tier: direct-to-main | CC-merge | operator-merge

============================================================================
WHY THIS MATTERS (Operator-facing summary)
============================================================================

<one paragraph the operator can read aloud. Plain English. No jargon. No
code blocks. What gap is being closed? What does it cost not to fix it?>

============================================================================
SCOPE
============================================================================

<exactly what changes. Files: <comma-separated list>. Approx LOC: <N>.
What's the change boundary?>

============================================================================
LOCKED DESIGN
============================================================================

<schema, interface, transport, error response format. Specific enough that
the worker doesn't make design decisions. Include code excerpts from the
existing files showing what's there today and what changes.>

============================================================================
DON'T-TOUCH LIST
============================================================================

- <file or pattern that must NOT change>
- <reason it's tempting but out of scope>
- <files in the governance file registry, if applicable>

============================================================================
DONE-WHEN
============================================================================

1. <objectively measurable criterion>
2. <test case 1>
3. <test case 2>
...
N. Pre-commit run --files <files> passes (ruff, ruff-format, prettier).

============================================================================
INVESTIGATION STEPS (if any spec gap remains)
============================================================================

If <X is unclear>:
1. <do this>
2. <or this>
3. <if still unclear, ESCALATE>

============================================================================
COMPLETION CONTRACT
============================================================================

When done:
1. Open a PR. Title: "<plain English title>".
2. PR description references this ticket and the synthesis doc.
3. Emit completion marker via tools/emit_completion.py with:
   - status: CONFIRMED_WORKING (after CI passes and smoke test succeeds)
   - test_evidence: "<passed>/<total> tests pass" (real numbers; ci_only or
     no_tests are valid prefixes if applicable)
   - files_touched: <list>
4. Tier behavior: <self-merge after CONFIRMED WORKING | request operator merge>

Operator-facing completion message format:

    What happened: <one sentence, no jargon>
    Does it work: Yes / Partially / No — <plain-English reason>
    What you need to do: <specific action, or "Nothing — it's done">

============================================================================
ESCALATION
============================================================================

STOP and emit STATUS: ESCALATE: <category> if:

- <condition that requires operator decision> -> ESCALATE: HUMAN-REQUIRED
- <change scope grew beyond the locked design> -> ESCALATE: SCOPE_EXPANSION
- <existing assumption in the synthesis is wrong> -> ESCALATE: DESIGN_CHANGE
- <conflict with hard prohibition> -> ESCALATE: SECURITY or IRREVERSIBLE_OP

============================================================================
DEFERRED TO A FOLLOW-UP TICKET (NOT THIS ONE)
============================================================================

- <thing that's tempting but out of scope>
- <thing that's a separate gap>
- <enhancement that should wait for operator decision>
```

## DO NOT do these when authoring tickets

- Do not include any narrative "why we chose this approach over alternatives." That belongs in the synthesis doc, not the ticket.
- Do not assume the worker has read the synthesis doc. Reference it explicitly with the file path.
- Do not write code in the ticket beyond minimal excerpts that show the change point.
- Do not abbreviate the don't-touch list. Workers expand scope when don't-touch is vague.
- Do not write in the first person. The ticket has no "I" — it's a contract between the operator and the worker.
- Do not include a deadline. Tickets are scoped, not timed. The operator paces.
- Do not promise the worker that the ticket is fully self-contained. Always include investigation steps for the case where it isn't.

## Escalation when authoring

Stop and tell the operator if:

- Pre-investigation reveals the synthesis is wrong about the change point. The synthesis needs an update before the ticket can be locked.
- The change is too large to be one ticket. Propose splitting and ask the operator to confirm the split.
- The locked design requires a decision the operator hasn't made (e.g., "do we use approach A or B for this?"). Get that decision before authoring.

## Reference

- Sample locked-design ticket: `data/peer_reviews/2026-05-08_codex_ticket_localhost_bind.md`
- Adopted lesson: `.miru/overlays/adopted-lessons.md`
- Operator communication: `AGENTS.md`
- Linear project IDs: `.miru/reference/linear-projects.md`

## When to NOT use this skill

- The operator will execute the work directly (no sub-worker handoff).
- The task is a routine Linear bug filing without locked design.
- The task is the operator's own todo list, not a ticket for another agent.
- The task is to update an existing ticket's status (use Linear directly).
