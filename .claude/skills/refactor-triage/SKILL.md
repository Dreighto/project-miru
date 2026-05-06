---
name: refactor-triage
description: Use this skill whenever the user proposes a refactor, cleanup pass, architecture simplification, restructuring, dead-path removal, worker-map realignment, or a post-benchmark codebase adjustment in Project Miru. Triggers include refactor, restructure, cleanup, simplify, realign, collapse lanes, dead code, stale path, architectural mismatch, roster change fallout, should we refactor, cleanup PR, technical debt after benchmark, and any request to decide whether a refactor is actually warranted. Do NOT use for routine bug fixes or feature work that already has a clear scoped implementation.
---

# refactor-triage

This skill is a thin wrapper. The canonical content lives at:

**`docs/ch_operations/REFACTOR_TRIAGE_SKILL.md`** — read this file first.

It covers:

1. How to tell the difference between real architectural mismatch and cleanup temptation
2. What evidence is required before recommending a refactor
3. How to define the smallest safe refactor that realigns Miru with current truth
4. How to classify findings into now, later, or not worth doing
5. How to hand the result back to the operator as a concrete next move

## Companion doc

**`docs/ch_operations/CC_SKILLS_CHEATSHEET.md`** — use this for short dispatch wording and a paste-ready refactor prompt.

## How to use this skill

1. Read `docs/ch_operations/REFACTOR_TRIAGE_SKILL.md`.
2. Gather evidence before recommending changes.
3. Frame the result as a decision, not a vibe: no refactor, surgical refactor, or larger planned refactor.
4. If recommending work, define the smallest safe PR sequence and explicit out-of-scope list.

## When to NOT use this skill

- The task already has an approved implementation plan and just needs execution.
- The user is asking for a code audit only with no decision about cleanup or restructuring.
- The task is benchmark methodology. Use `benchmark-operator` instead.
