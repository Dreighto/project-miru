# Refactor Triage Skill — Claude Code

Use this when Claude Code is asked whether Miru should refactor after new findings, roster changes, or architecture drift.

## Purpose

This skill exists to distinguish a justified refactor from post-discovery cleanup energy. The goal is not to produce more work. The goal is to identify whether the codebase still matches current truth.

## Entry Gate

Use this skill when the question is any variation of:

- Should we refactor now?
- Does the architecture still match what we learned?
- What dead paths or stale assumptions should be removed?
- Is this mismatch large enough to justify a cleanup PR?

Do not use it for ordinary bug-fixing or a ticket that already has a locked implementation plan.

## Evidence Standard

Before recommending a refactor, gather concrete mismatch evidence:

1. What assumption changed?
2. Where is the old assumption still encoded?
3. What complexity, risk, or confusion does that create now?
4. What is the smallest change that would realign the system?

If the answer is vague, the refactor case is weak.

## Decision Categories

Classify the result as one of:

- `No refactor needed`
  The system is awkward but still aligned enough; leave it alone.

- `Surgical refactor warranted`
  A small, explicit cleanup will remove stale assumptions or reduce real risk.

- `Planned refactor warranted`
  The mismatch is real, but the safe answer is a sequenced effort rather than an opportunistic cleanup.

## What a Good Recommendation Looks Like

A good recommendation names:

- the changed truth
- the stale surfaces
- the risk of leaving them in place
- the exact smallest PR or PR sequence to fix it
- the out-of-scope list

Example shape:

- `Changed truth: production roster is now Claude backend + Gemini frontend; Cursor is not in production dispatch.`
- `Stale surfaces: worker maps, allowlists, fallback docs, dead dispatch branches.`
- `Why it matters: the codebase still encodes workers that no longer own production lanes, which increases routing ambiguity and maintenance drag.`
- `Recommendation: surgical refactor in one PR to update roster config, strip dead dispatch paths, and refresh canon references.`

## What a Weak Recommendation Looks Like

- `We should probably clean things up.`
- `The code feels messy now.`
- `It would be nice to simplify things.`

Those are not refactor justifications. They are moods.

## Refactor Output Contract

The recommendation should end with:

1. `Verdict`
2. `Why now`
3. `Smallest safe scope`
4. `Explicit out-of-scope`
5. `Whether the work should become a ticket`

## Anti-Patterns

- Bundling three architectural decisions into one cleanup pass
- Using a refactor as cover for new feature work
- Changing scope mid-flight because the code is nearby
- Calling for a refactor without naming the stale assumptions
- Mistaking personal preference for architectural need
