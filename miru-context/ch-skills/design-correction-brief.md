# Skill: design-correction-brief

## When this skill applies

**Keyword trigger (operator types one of these):**

- **`design correction`** ← canonical phrase
- `correction brief`
- `reframe this`

When the operator says any of those phrases, load this skill and use the structure below to write a single authoritative document for the operator to relay to CC/GMI. Do NOT enter this mode without one of those keywords — for unsolicited reframings, brainstorm with the operator first and let them invoke this skill explicitly.

(Operator can change the canonical keyword by editing the bold line above. CH fuzzy-matches it on every new thread.)

## What this skill is for

CH has received a prior analysis from CC or GMI (audit, design proposal, scope inventory, refactor plan, etc.) that is **correct in data but wrong in framing**. The worker's facts are accurate; their conclusions or migration map are built on a premise CH has since rejected — either because of a brainstorm that landed elsewhere, or because the worker was working from outdated context.

Canonical example (2026-05-18): CC produced a thorough dev-page audit assuming the next move was porting the existing six React surfaces. The audit data was accurate (route list, template inventory, dead-code finds, bundle layout — all correct). But the framing was wrong: the settled design was three new surfaces (Glance/Voyage/Review) with the old scope dead, not a six-surface port. CH had to hand back a correction without discarding the audit's load-bearing inventory.

NOT for: critiquing a worker's execution (that's review). NOT for: rejecting an analysis wholesale (just say it's wrong, no correction-brief needed). This skill is specifically for the surgical case where you keep some of the work and reframe the rest.

## How to behave in correction-brief mode

The trap to avoid: **handing back "your audit + my corrections" as a layered document.** The worker will half-merge the old framing with the new framing and produce something incoherent. The first execution attempt will carry the wrong assumption through.

Instead: hand back a **single authoritative document** that explicitly states what's preserved, what's dead, and what the corrected position is. The worker should not have to reconcile.

## The brief structure (use this exact skeleton)

```
== WHAT YOU GOT RIGHT ==
<2-4 sentences naming the specific findings/data/inventory that survive
this correction. Be concrete — "the orphan template list", "the route
enumeration", "the diff between flask.ts implementations". The worker
should be able to point at their prior artifact and know exactly what
they can keep using. NOT generic praise.>

== WHAT WAS WRONG IN FRAMING ==
<1-3 sentences naming the premise that's been rejected. Be blunt.
"You assumed X. X is dead. Here's why: <one-line reason>." If the
worker's framing came from reading existing code, name that too —
"the code suggests X, but the design we settled on is Y; do not
re-derive scope from existing code."

The premise-killing sentence is load-bearing. Without it, the worker
will silently fold the old framing back in.>

== THE CORRECTED POSITION ==
<Numbered, definitive. Same shape as a worker-brief-author "Settled —
build to exactly this" block, but framed as a correction:
1. <constraint>
2. <constraint>

Workers may not relitigate these. Pin versions, name files, be explicit.>

== HOW TO INTEGRATE YOUR PRIOR WORK ==
<2-4 bullets telling the worker exactly what to do with their prior
artifact:
- "Keep using <X inventory> as your 'what's dead and needs to die' list."
- "Discard <Y> — it was built on the wrong premise."
- "Re-frame <Z> against the corrected position before acting on it."

This section is what makes a correction-brief different from a
worker-brief-author brief. The worker has SUNK COST in prior analysis;
you're telling them how to harvest it.>

== NEXT MOVE ==
<One line. What the worker does first with this correction. Usually:
"Re-read this brief before acting on the prior audit", or "Apply the
corrected position to <specific ticket scope>", or "Stand down on
<prior plan>; we'll re-scope from here.">
```

## Why each section is non-optional

| Section                                          | What dies if you skip it                                                                             |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| `WHAT YOU GOT RIGHT`                             | Worker thinks the whole analysis was wasted and starts over from scratch, losing real findings       |
| `WHAT WAS WRONG IN FRAMING` (with explicit kill) | Worker silently merges old framing with new and ships the wrong thing                                |
| `THE CORRECTED POSITION`                         | Worker doesn't know what's settled vs. still under discussion and re-asks                            |
| `HOW TO INTEGRATE YOUR PRIOR WORK`               | Worker either over-discards (loses good inventory) or over-preserves (carries wrong premise forward) |
| `NEXT MOVE`                                      | Worker reads the correction and then does the wrong first action                                     |

## Anti-patterns

- **Don't critique the worker.** "You misunderstood" / "your scope was off" — that's review tone. The correction-brief is about the WORK, not the WORKER. Use passive voice for framing errors when possible: "the premise here was X; we've moved past X."
- **Don't bury the kill.** "We've moved on to a different framing" is too soft. "X is dead. Do not port it, do not reference its structure, do not re-derive scope from it." kills it.
- **Don't write a correction that requires the worker to re-read their prior artifact + this brief side-by-side.** This brief should stand alone as the authoritative position. The worker SHOULD re-read their prior artifact, but only to harvest the bits you flagged as keepers.
- **Don't include corrections that aren't actually correcting.** If the worker's framing is fine and you're just adding new constraints, that's `worker-brief-author`, not this skill.
- **Don't write a correction-brief longer than 80 lines.** If you need more space, the prior analysis probably should be discarded wholesale — just say so.

## Relationship to worker-brief-author

`worker-brief-author` = the brief you write when handing OFF a fresh task. No prior worker artifact to reconcile.

`design-correction-brief` = the brief you write when reframing a worker's prior artifact. They have sunk cost; you're triaging it.

After a correction-brief lands and the worker is re-oriented, the NEXT step is usually a `worker-brief-author` brief (or a ticket) that takes the corrected position and turns it into a dispatchable task. The two skills compose: correction → re-orientation → fresh brief → dispatch.

## Canonical reference

The 2026-05-18 dev-page rebuild correction is the working example. The brief CH wrote ("WORKER: Claude Code / TASK: Scaffold the Miru AI dev-page rebuild...") was actually a hybrid — it functioned as BOTH a correction-brief (killing the six-surface framing from CC's audit) AND a worker-brief-author brief (locking the SvelteKit three-surface scope for dispatch). Cleanly separating the two would have been slightly clearer, but the hybrid worked because the kill sentence ("This is NOT the old six-surface React operator-console. That scope is dead.") was unambiguous.

For future corrections, prefer the cleaner split: correction-brief first to re-orient, then a separate worker-brief-author brief to dispatch from the corrected position. This makes the dispatch ticket easier to re-read later without the correction context cluttering it.

## Related skills

- `brainstorm-protocol` — the brainstorm that produces the corrected position usually precedes a correction-brief.
- `worker-brief-author` — the natural follow-up after the worker is re-oriented.
- `design-session-output` — for capturing what changed during the brainstorm that made the prior framing wrong (provides the "why X is dead" content for the correction-brief).

Flow: **brainstorm-protocol → design-session-output → design-correction-brief (if worker had prior analysis) → worker-brief-author → ticket + dispatch.**
