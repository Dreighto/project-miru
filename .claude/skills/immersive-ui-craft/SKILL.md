---
name: immersive-ui-craft
description: Use this skill when building or refreshing a UI surface that should feel finished, atmospheric, and characterful rather than like a flat scaffold — adding depth and elevation, motion language, and immersive treatment (maps, journey/progress views, "make it feel like X" surfaces). Triggers include design refresh, feels half-done, feels like scaffold, flat UI, add depth, elevation layers, atmospheric, immersive, parallax, motion design, make it feel like a voyage/journey/place, characterful UI, art direction, polish pass, hero surface. Complements operator-console-ui (density) and frontend-systems-engineer (engineering) — this skill is the visual/atmospheric craft layer. Do NOT use it to justify decorative motion or ornament on a plain utility surface where density and speed are the whole job.

References: data/research/2026-05-21_dev_page_design_research.md (full research) and docs/ui_ux/ (00–09 craft guides).
---

# immersive-ui-craft

This skill is self-contained. It is the craft layer that turns a "functional scaffold" into a surface that feels designed.

## Purpose

A surface reads as half-finished for diagnosable reasons — not vague taste. This skill names those reasons and the moves that fix them: depth, motion, focal points, atmosphere. Apply it whenever a surface "feels off" or needs to feel like a place, not a form.

## The two failure modes

1. **Flatness.** One background, one or two surface tones, no elevation. Everything sits on the same plane → nothing has weight → "scaffold." The fix is an elevation system, not more borders.
2. **Inertness.** Static dots, neutral connectors, no focal point, no sense of being somewhere. The fix is a living focal point + atmosphere + a narrative frame (past/present/future made visible).

## Elevation & depth — the first lever

- Build on an **elevation ladder**, not a flat background: base → default surface → raised (cards) → overlay (popovers/sheets). Each step is lighter, and may shift hue subtly. Depth comes from this ladder + 1px tokenized borders + restrained shadow — never from heavy drop-shadows.
- A raised element must actually sit on a lighter surface than what's behind it. If a card and its page are the same tone, it is not a card.
- Restrained accent: ~60% neutral surface / ~30% secondary / ~10% accent. The accent is spent on meaning (the current thing, the primary action), never on generic fills (a CPU bar is not accent-worthy).

## Motion language

Three registers, nothing else:

- **Idle / ambient** — slow, looping, subtle (a few px, multi-second, eased). Signals "alive," never asks for attention.
- **Transition** — 120–250ms on state changes; communicates what changed.
- **Event** — rare, brief (<1.5s), for genuine moments (a milestone, a completion).

Rules: animate `transform` and `opacity` only; `will-change` only on the few elements that truly animate; drive JS animation with `requestAnimationFrame`. Always honour `prefers-reduced-motion` — static is the default, motion is added under `@media (prefers-reduced-motion: no-preference)`, and JS checks `matchMedia` and jumps to end-state.

## Focal point

Every surface has one thing the eye should land on first. Give it contrast — in size, warmth, or motion. On a journey surface that is the **vessel** (warm, idle-bob, glow); on a console it is the most important status or the primary action. A focal point is a character, not a dot — a static dot has no agency.

## Atmospheric craft (map / journey / immersive surfaces)

- **Narrative frame: past / present / future made visible.** Past = a drawn wake / completed-and-lit nodes. Present = the living focal point. Future = haze, faint silhouettes, a distant goal.
- **Parallax depth** — layers move at different rates on scroll (distant slow, near fast) via `translate3d`. Even 2–3 layers transform a flat plane into a world.
- **Distance fog** — a gradient denser toward the unknown; near the focal point it is clear. Builds anticipation and manages density.
- **A horizon goal** — a distant, always-faintly-visible objective gives a surface direction and meaning.
- **Chapters** — segment a long surface into themed regions with shifting tone; humans read journeys as chapters.
- **Distinct landmarks** — repeated identical shapes feel like a chart; give each node its own silhouette.

## Polish checklist — "scaffold" vs "finished"

- Elevation ladder applied (nothing floats on its own tone).
- Loading = skeletons, not spinners; empty = an intentional state, not bare text.
- Long text never towers — clamp (~3 lines) + "Show full"; give long fields their own width.
- Data is monospace and aligned; labels have deliberate tracking; weights are intentional.
- Activity/event lists are timelines (icon + color + relative time + hierarchy), not flat text.
- Meters carry threshold zones, not a single flat fill.
- Tap targets ≥44px; `:active`/press feedback exists (touch has no `:hover`).
- Motion respects `prefers-reduced-motion`; nothing is decorative-only.

## Implementation patterns

- SVG for vector route/landmark/vessel geometry — `getPointAtLength()` for path-following + tangent rotation, `stroke-dasharray` for draw-on (a wake).
- Canvas for continuous texture (water, noise) — only when needed.
- DOM + CSS for chrome, labels, sky, weather.
- Streaming data: a SvelteKit loader returns un-awaited promises; the shell renders instantly and sections fill via `{#await}`. A blocking `await Promise.all(...)` stalls every navigation behind the slowest call.

## Tokens

Use the project's design tokens (`miru_ai/hub_ui/src/app.css` — the evolved "Ink" system: an elevation ladder, depth-tinted darks, a restrained brass accent, a Voyage-only sea accent, Geist + Geist Mono + a Voyage display face). Never hardcode hex — extend the tokens if something is missing.

## When to ask instead of guess

- Whether a surface should be immersive/atmospheric at all, or is a pure-utility surface where this skill does not apply.
- How far a visual identity may change (token values vs a new direction).
- The intended "feeling" of an immersive surface, if not already specified.

## When NOT to use this skill

- A pure-density utility surface where speed is the whole job — use `operator-console-ui`.
- Marketing/storefront surfaces.
- As a license for decorative motion or ornament that serves nothing.

## Anti-patterns

- Adding shadows and borders to fake depth instead of a real elevation ladder.
- Motion for its own sake; animating layout properties; `will-change` everywhere.
- Spending the accent color on generic fills until it means nothing.
- A "hero" treatment on every surface — atmosphere is for the surfaces that earn it.
- Declaring a surface done from a screenshot without navigating, tapping, and measuring it.
