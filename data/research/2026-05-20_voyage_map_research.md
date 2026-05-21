# Voyage Map — Research Digest (lore accuracy + design)

Pre-build research for the Voyage surface rebuild (`/voyage` on the dev page). Sources: Perplexity deep research on One Piece world geography + canon island order, and on immersive journey-map UI design. Read alongside the mockups in `docs/ui_ux/dev-page-mockups/`.

## 1. One Piece world — accurate geography (for the map)

- The world is a circular ocean planet. A continent — the **Red Line** — and a circumferential sea route — the **Grand Line** — cross it, dividing the seas into the **four Blues** (North / South / East / West).
- The **Grand Line** is the journey. Its first half is **Paradise**; its second half is the **New World**. The **Red Line** separates the two halves.
- The route: start in **East Blue** -> enter the Grand Line through **Reverse Mountain** (the only gateway) -> sail across **Paradise** -> cross the **Red Line** (via **Fish-Man Island**, which lies beneath it) -> into the **New World** -> onward toward Laugh Tale (never reached — the route is open-ended).
- Navigation is by **Log Pose** (the compass that locks onto the next island).
- Map anchors, bottom -> top: East Blue (start) - Reverse Mountain (gateway) - the Paradise stretch - the **Red Line** (a hard divider band) - the New World stretch - open fog ahead.

## 2. Canon island order (the milestones)

The Straw Hats' canonical journey, saga by saga: East Blue -> Reverse Mountain -> Whisky Peak -> Little Garden -> Drum -> **Alabasta** -> Jaya -> **Skypiea** -> Long Ring Long Land -> **Water 7** / Enies Lobby -> **Thriller Bark** -> **Sabaody** -> Amazon Lily / Impel Down / Marineford -> **Fish-Man Island** -> Punk Hazard -> **Dressrosa** -> Zou -> **Whole Cake Island** -> **Wano** -> **Egghead** -> **Elbaf** (the current arc, 2026, the Final Saga).

The dev page's **15-island milestone list** (PRO-933 backend: East Blue, Reverse Mountain, Whisky Peak, Alabasta, Skypiea, Water 7, Thriller Bark, Sabaody, Fish-Man Island, Punk Hazard, Dressrosa, Whole Cake, Wano, Egghead, Elbaf) is **canon-accurate as saga milestones** — each is a real major island, roughly one per saga. Correct resolution for a phone-width map; keep it. (A fuller ~24-stop list exists if more granularity is ever wanted.)

Islands are MILESTONES; **TCG sets are the distance sailed between them** (debrief §3.2). The map shows how far the ship has progressed along this canon route as set-verification work accumulates.

## 3. Design — making the voyage feel real (validated techniques)

Perplexity research and the v4 mockup converge on these. The mockup already embodies them; the build must implement them faithfully **and dynamically**:

- **Organic winding route** — a curved SVG path (Bézier), not a straight strip. Reads as a discovered voyage. (Refs: RPG world maps, Duolingo's guided path.)
- **Fog of war** — charted route + islands are bright/solid; the route ahead is faint dashed; future islands are dim/dashed, fading up into a "NEW WORLD" fog bank. Future = mystery; progress = earned.
- **A living beacon** — the current island is a luminous, gently-pulsing marker (glow + a slow ripple ring). The dark theme makes luminous effects sing; keep motion subtle.
- **Atmospheric depth** — layered: gradient sea, slow drifting current lines, a faint chart grid, a compass-rose decoration, the Red Line band. Nautical-chart character on a dark base.
- **Performance** — animate only `transform` / `opacity`; `will-change` sparingly; smooth on a phone.

## 4. Build implications (NOT a copy of the static mockup)

- The mockup hard-codes island pixel positions. The build must **generate the winding route and place all 15 island nodes along it programmatically**, driven by `/api/dev/voyage` (state per island: charted / current / fog).
- **Colors:** the mockup's pink/purple is placeholder. Build in the **locked Ink palette** — current-island beacon = brass `accent`; charted = `positive` green; fog = `text-faint` / dashed `border`. The sea-atmosphere blues are a Voyage-specific background (allowed — Voyage is the one surface that carries the canon framing).
- **Typography:** Geist + Geist Mono (the locked system) — not the mockup's Cinzel / IBM Plex.
- **Mobile-first + responsive:** the dev page is used on the operator's phone over Tailscale. Build fluid (works ~375–440px wide), respect safe-area insets (notch / home indicator), and verify at the operator's actual device viewport.
