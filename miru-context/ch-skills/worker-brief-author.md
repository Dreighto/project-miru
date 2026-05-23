# Skill: worker-brief-author

## When this skill applies

**Keyword trigger (operator types one of these):**

- **`worker brief`** ← canonical phrase
- `dispatch brief`
- `lock the brief`
- `brief CC` / `brief GMI` (worker-specific variant)

When the operator says any of those phrases, load this skill and write a single authoritative brief using the structure below. The brief is the artifact the operator will paste into the worker prompt wrapper (or relay verbally) when dispatching. Do NOT enter this mode without one of those keywords — for unsolicited briefs, brainstorm with the operator first and let them invoke this skill explicitly.

(Operator can change the canonical keyword by editing the bold line above. CH fuzzy-matches it on every new thread.)

## What this skill is for

CH is about to hand a worker (CC, GMI, dispatched or interactive) a task where the design is "settled but easy to miss-implement if the worker re-derives scope from existing code." Common cases:

- Architectural framing the worker doesn't yet have
- A multi-PR feature where the wrong default would be expensive to unwind
- Anything where the worker's natural assumption from reading existing code would be WRONG

The SvelteKit dev-page rebuild brief (2026-05-18) is the canonical example: CC's prior audit assumed porting six existing React surfaces; the actual settled design was three new surfaces with the old scope dead.

NOT for: routine ticket descriptions where the design is straightforward (just use `locked-design-ticket` on the CC side). NOT for: critiquing or reframing a prior worker analysis (that's `design-correction-brief`).

## How to behave in brief-author mode

**The brief replaces, not supplements, the worker's existing context.** If you skip this and just say "here's the new design, layer it on top," the worker will half-merge old framing with new framing and produce something incoherent. Be explicit about what's dead.

## The brief structure (use this exact skeleton)

```
WORKER: <Claude Code | Gemini CLI | Cursor>
TASK: <one-line task statement, no fluff>
TARGET REPO: <repo name + local path>

== WHAT THIS IS ==
<2-4 sentences. The PURPOSE. What this surface is FOR. If correcting a
prior framing, the second sentence must explicitly negate the wrong
framing: "This is NOT the old <X>. That scope is dead. Do not port it,
do not reference its structure, do not re-derive scope from it."
End with one sentence on how this fits into the larger system.>

== SETTLED — build to exactly this ==
1. <Numbered, definitive decisions. Each item is a constraint the worker
   may not relitigate. Cover: framework choice with version pins, file
   layout, data path, naming, state management, anything else where the
   worker would otherwise have to guess and might guess wrong.>
2. <…>

== SCOPE OF THIS TICKET — <one-line scope> ==
- <Bulleted concrete deliverables>
- <Each one is a thing the worker will produce>

== EXPLICITLY OUT OF SCOPE — DO NOT BUILD ==
- <The thing the worker would naturally over-deliver. Name it.>
- <The adjacent feature that's part of the larger plan but not this ticket.>
- <Anything the worker would touch by default that they shouldn't.>

== COMPLETION ==
Report: <one-line acceptance criteria>. CONFIRMED WORKING / INCONCLUSIVE / FAILED with evidence.

== NOTE ==
<Sanity-check sentence. Common one: "Your earlier <X> ran on the OLD
<Y> framing. Treat this brief as the correction. Re-verify against it."
This sentence is the safety net that catches a worker who skims.>
```

## Why each section is non-optional

| Section | What dies if you skip it |
|---|---|
| `WHAT THIS IS` (with explicit negation) | Worker re-derives scope from existing code and ports the wrong thing |
| `SETTLED — build to exactly this` | Worker treats decisions as proposals and second-guesses pins/framework |
| `SCOPE OF THIS TICKET` | Worker over-builds (PRO-922 first attempt: built mock infrastructure that wasn't asked for) |
| `EXPLICITLY OUT OF SCOPE — DO NOT BUILD` | The single most load-bearing section. Worker scope creep is the #1 cause of 600s timeouts. |
| `COMPLETION` | Worker doesn't know what "done" means and either over-delivers or stops early |
| `NOTE` (re-verify against this brief) | Worker carries a wrong prior assumption through to execution |

## When to write the brief vs let the ticket carry the design

- **Write the brief** when the design is materially different from what reading the existing code would suggest. (Today's dev page rebuild fits.)
- **Skip the brief, just write the ticket** when the design is a natural extension of existing code and the worker reading the codebase would arrive at the right place.

Per the existing `locked-design-ticket` discipline (PRO-180 retro): the design always lives in the Linear ticket description. The BRIEF is a different artifact — it's the framing wrapper around the ticket that operator hands to the worker conversationally. When dispatching, the ticket gets the locked design; the brief gets pasted into the prompt wrapper or operator-relayed verbally.

## Anti-patterns to avoid

- **Don't bury the negation.** "This is the new design" leaves the old framing alive. "This is NOT the old <X>. That scope is dead." kills it.
- **Don't soften "do not build".** "Maybe defer X" → worker builds X anyway. "DO NOT build X" → worker doesn't.
- **Don't number the wrong list.** Settled decisions get numbered (they're an ordered set of constraints). Scope items can be bulleted. Out-of-scope items can be bulleted. This visual hierarchy matters.
- **Don't omit version pins** when you're saying "match existing infra." The worker will pick latest by default and you'll regret it.
- **Don't write a brief longer than 60 lines.** If you can't fit the framing in 60 lines, the design isn't actually settled yet — finish brainstorming first.

## Canonical example

`data/peer_reviews/2026-05-18_dev_page_audit_ch_brainstorm.md` was the CC handoff TO CH for context. The brief CH wrote BACK to launch the build is recorded in PRO-918's original ticket description (overwritten later by the re-scope, but the worker received it). The brief was ~45 lines and produced a clean dispatch chain that needed zero design re-asks across 6 worker dispatches.

Compare to: a brief that says "implement the SvelteKit version of the dev page" with no negation, no version pins, no out-of-scope list → worker reads the existing 12,622-line server.py + React code, ports the six React surfaces to Svelte, and you've shipped the wrong thing.

## Related skills

- `brainstorm-protocol` — use this BEFORE writing the brief. The brief is the artifact that comes OUT of a settled brainstorm.
- `design-session-output` — for capturing the brainstorm itself.

The flow: **brainstorm-protocol → design-session-output → worker-brief-author → ticket file + dispatch.**
