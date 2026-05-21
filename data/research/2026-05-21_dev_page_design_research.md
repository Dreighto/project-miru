# Dev Page — Design Research Digest

Research backing the dev-page design refresh (Glance / Voyage / Review on `hub_ui`, port 18768). Two deep passes: immersive journey/voyage UI, and dark operator-console craft. This is the durable source the `immersive-ui-craft` skill and dispatched workers cite.

---

## Part 1 — Turning a chart into a voyage

**The diagnosis.** A dotted line connecting island dots reads as a _chart_ because it treats the path as a neutral connector between discrete data points. A voyage treats the path as an **expressive, narrative line** and surrounds it with place, atmosphere, and a living focal point. You cannot hide the linear structure — you make it _feel_ like a journey through the techniques below.

### Wake / Vessel / Horizon = past / present / future

The single most useful reframe. Past milestones = the **wake** (a solid, glowing trail drawn behind you). Present = the **vessel** (the living focal point). Future = the **horizon** (hazy island silhouettes, a faint route ahead, a distant goal).

- **The wake** — a separate path sharing the route geometry, revealed via `stroke-dasharray` / `stroke-dashoffset` to current progress. It is _visible history_ — it can glow brighter on segments that went well.
- **The route ahead** — lower opacity, thinner, dashed — "charted but not yet sailed." Fades into fog near the top.
- Completed islands gain small signs of life (a glow, a lit window); unvisited stay silhouetted. (Zelda BotW towers: dormant → glowing.)

### The vessel as a living focal point

A static dot has no agency. A ship does. The vessel must be unmistakably "you are here":

- **Asymmetric** — a clear bow/stern so direction reads; **rotates to the route tangent** (compute from `getPointAtLength` neighbours).
- **Idle motion** — a gentle bob (a few px, ~4s loop, eased), a soft glow/outer-stroke so it stays visible over any background.
- **Warm against cool** — a warm vessel (brass/gold) on a cool sea reads instantly as the focal point.
- It is an **interaction hub** — tapping it surfaces voyage stats / the log.
- Three motion states only: idle (ambient bob), moving (glide along a segment on milestone-complete, exaggerated wake, ease-out), celebratory (rare, <1–2s).

### Atmospheric depth

- **Parallax layers** — at minimum: distant sky+horizon (slow), midground sea+islands (full speed), foreground wisps (slightly faster). Offset = camera displacement × a per-layer factor. Composite with `transform: translate3d(...)`, never top/left.
- **Distance fog** — a gradient/overlay, near-transparent by the vessel, denser and desaturated toward the horizon and the unknown. `backdrop-filter: blur()` gives a frosted fog over dark sea (use sparingly — GPU cost).
- **Horizon line** — the sea/sky border at the top; distant islands near it are smaller, washed-out, lower-contrast. A subtle pseudo-3D recession.
- **Atmospheric perspective** — distant elements lose contrast and saturation.

### Fog-of-war & discovery

Future islands you must show structurally — but render them as **faint silhouettes through haze**, detail resolving as the vessel nears. Partial revelation builds anticipation. Each island should be a **distinct landmark silhouette**, not a repeated circle (Monument Valley: every node a unique structure).

### Chapters / arcs

Humans grasp long journeys as chapters. Cluster islands into themed regions separated by gate-features; shift the sky/sea tone across them. (Genshin regions; Alto's Odyssey biome/weather drift; Duolingo's section background changes.) For Miru: **Paradise** vs the **New World**, gated by the **Red Line** — this is canon and free.

### The horizon goal

A distant, always-faintly-visible objective at the far edge (Journey's mountain) — a constant orienting symbol of the overarching destination. It grows as you approach.

### Reboarding (session continuity)

On open, settle the camera onto the vessel; optionally pull back briefly to show the wake, then ease in. A one-line textual cue ("The Log Pose points to …") reorients. Duolingo keeps the current spot always visible + a floating "return to current" affordance.

### Named references

- **Journey** — one dominant motif; a horizon mountain as a permanent goal anchor.
- **Monument Valley** — every node a distinct architectural landmark.
- **Alto's Odyssey** — parallax depth; biome/weather/time drift signals progress without screens.
- **Duolingo path** — a guided winding path of nodes; current position always centered; floating return arrow.
- **Candy Crush maps** — "narrative breadcrumbs": small per-node story beats accumulating into an arc.
- **Zelda BotW** — map reveal; dormant→active state changes on landmarks.

---

## Part 2 — Dark operator-console craft

### What makes a dark console feel finished (vs scaffold)

- **A real elevation ladder.** Not one flat background — a stack of surfaces that lighten (and may subtly shift hue) as they rise: base → default surface → raised (cards) → overlay (popovers). This is the single biggest "scaffold → product" lever. (Atlassian/Material elevation.)
- **Restrained accent.** Roughly 60% neutral surface / 30% secondary / 10% accent. The accent earns its place — it is not spent on generic fills.
- **Disciplined density** — comfortable padding (12–16px), a _compact_ mode is a deliberate token, never just "cramped."
- **Semantic icon + color + text together** — never color alone.
- **Skeletons over spinners** — perceived as faster; a finished feel.
- **Motion restraint** — 120–250ms state transitions; nothing decorative.

### Activity / event feed

Not a flat text list. Group by time (sticky range headers); a **semantic icon + color per event kind**; relative timestamps for recent ("2 min ago"), absolute for old; typographic hierarchy (the subject bold, the detail dim); aggregate repeats ("×14"); a left-border/tint accent for the noteworthy. A timeline rail (dot + connector) reads as a _log_, not a list.

### Resource / usage meters

The value is the hero — large, **monospace**. The bar carries **threshold zones** (calm / caution / critical) — a meter that's just a brass fill conveys nothing. A tiny trend sparkline answers "is it stable?" Metric name in dim text; never truncate it. Mono for all numerics so columns align.

### Long text in a dense panel

Never let a paragraph tower. Clamp to ~3 lines with `-webkit-line-clamp` (+ `display:-webkit-box; -webkit-box-orient:vertical; overflow:hidden`) and a **"Show full" expand** escape hatch. Give long text its own full-width row — do not bury it in a narrow table column. For a comparison of values, stack per-field on mobile rather than force a horizontal-scroll table.

### Color & typography

- **Dark UI** — never pure black, never pure white text (halation). Layered dark tones; text around `#ece8df`-class warm off-white on the deepest surface. Pair every color signal with a shape/icon.
- **Type** — a humanist UI sans + a mono for data (ports, IDs, timestamps, counts). A stable modular scale; weights ≤600 for body on dark (700+ looks harsh); deliberate tracking on small uppercase labels. `-webkit-font-smoothing: antialiased`.

---

## Part 3 — Implementation patterns

- **SVG** for the route, islands, vessel — vector precision, hit-testing, `getTotalLength()` / `getPointAtLength()` for path-following + tangent rotation, `stroke-dasharray` for the wake draw-on.
- **Canvas** for continuous water/noise texture (fast-follow, not the core pass).
- **DOM + CSS** for sky/weather/labels.
- **Animation** — `requestAnimationFrame` (synced to refresh); compositor-only properties (`transform`, `opacity`); `will-change` only on the few genuinely-animated elements (the vessel, wake, parallax layers) — overuse hurts.
- **`prefers-reduced-motion`** — default styles are static; motion added under `@media (prefers-reduced-motion: no-preference)`; JS animation checks `matchMedia` and jumps to end-state.
- **Mobile** — `width=device-width` viewport; safe-area insets; ≥44px tap targets; test 393px and 430–440px.
- **SvelteKit loaders** — return un-awaited promises to **stream**; the page renders its shell instantly and fills sections with `{#await}`. Blocking `await Promise.all(...)` in a loader stalls every client-side navigation behind the slowest call.
