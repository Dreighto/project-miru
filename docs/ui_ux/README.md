# docs/ui_ux — universal frontend craft

**Applies to:** any Miru frontend surface — PM storefront, Dispatcher, Dev Review Hub, future internal tools, future user-facing apps.
**Read this when:** you're about to build or change UI that a human looks at. Anything with a screen.
**Skip this when:** backend-only tasks, one-line style tweaks, typo fixes, or work that never reaches a rendered surface.
**Length:** ~55–75 pages across 10 files.
**Related docs:** [docs/pm/](../pm/README.md) for PM-specific application of these principles.

---

## How to use this library

This is a **library, not a gate.** Every doc opens with a scope header that tells you in five seconds whether it applies to your task. Load the one or two docs that match what you're building. Don't read the whole thing.

Workers should resist the urge to load everything. The library is dense on purpose — every file earns its page count. Reading a file you don't need costs context and dilutes the signal from the file you do need.

## The index

Ordered roughly by how often each file is consulted, not alphabetically.

| # | File | When to read |
|---|------|--------------|
| 00 | [PRINCIPLES.md](00_PRINCIPLES.md) | First visit. Or when you're about to push back on one of the rules and want to know *why* it exists. |
| 01 | [MOBILE_PWA.md](01_MOBILE_PWA.md) | Anything that ships on mobile. Installation, safe areas, iOS quirks, Android fragmentation, virtual keyboards. |
| 02 | [GESTURES.md](02_GESTURES.md) | You're wiring a swipe, long-press, drag, or any non-tap interaction. Also read before *removing* a gesture. |
| 03 | [SUB_PAGE_ARCHITECTURE.md](03_SUB_PAGE_ARCHITECTURE.md) | You're adding a new screen, modal, sheet, or detail view and need to decide what kind of surface it should be. |
| 04 | [PRIMITIVES.md](04_PRIMITIVES.md) | Building a reusable component (button, input, chip, sheet). Check this first — it may already be defined. |
| 05 | [ACCESSIBILITY.md](05_ACCESSIBILITY.md) | Any UI change that touches focus, contrast, keyboard nav, screen reader behavior, or color semantics. |
| 06 | [PERFORMANCE.md](06_PERFORMANCE.md) | Card grids, images, animation, lists over ~50 items, anything that feels slow. |
| 07 | [COMPETITIVE_STUDY.md](07_COMPETITIVE_STUDY.md) | You're designing a pattern from scratch. Check here for how Linear, Stripe, Arc, Superhuman, Things, and Apple solved it. |
| 08 | [ANTI_PATTERNS.md](08_ANTI_PATTERNS.md) | Before shipping. Fast pass to confirm you haven't fallen into one of the common mistakes. |
| 09 | [TOOLING.md](09_TOOLING.md) | You want to add a library. Every addition needs justification and a rejected alternative. |

## The voice

Calm, concrete, experienced. Not hype. Every rule has a *why*. When the guide says "do X," there is evidence behind it — a spec, a real 1-star review, a device quirk, a failure mode we've actually hit. Rules without evidence are opinions; opinions don't belong in a craft library.

## When this library disagrees with code you find in the repo

Trust the library. The repo contains code written over months by many workers in many moods. Some of it predates the guide. If you find a pattern in the repo that contradicts this library, the library is the spec; the code is technical debt. Flag it (don't silently "fix" unrelated code).

## When this library disagrees with a CLAUDE.md or operator directive

Operator directives win. Always. This library is advisory for craft; CLAUDE.md rules are binding for boundaries. If they conflict, follow CLAUDE.md and flag the conflict to Claude Chat.

## How to update this library

- **Add a rule** when you have evidence (a real failure, a cited best practice, a shipped pattern that worked).
- **Remove a rule** when it no longer holds. Dated guidance is worse than no guidance.
- **Don't generalize.** The PM-specific stuff lives in `docs/pm/`. If a rule only applies to one surface, it belongs in that surface's guide.
- **Cite.** If you add a claim about "best practice," link the source.

## Cross-reference to PM

`docs/pm/` is the application of these principles to the specific PM product. Whenever a PM doc cites a universal rule, it links back here. Whenever this library references a PM-specific application, it links forward. These are two halves of the same library.
