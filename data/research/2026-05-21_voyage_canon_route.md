# Voyage Map — Grand Line Canon Route Research

**Date:** 2026-05-21
**Author:** CC (Claude Code)
**Purpose:** Chart the dev-page Voyage surface's route to match One Piece manga
canon — the real twists and turns, not a generic swirl.
**Method:** Perplexity deep-research pass (~81 KB report, ~50 citations, One Piece
Fandom canon) synthesised here into an actionable chart-layout spec.
**Builds on:** the Atlas display model (each voyage leg = one chart-image page).

---

## 1. The canon route — structure

**The cross.** Two planet-spanning great circles cross like a cross: the **Red
Line** (a continuous ring of continent) and the **Grand Line** (an ocean route,
perpendicular to it). They intersect at exactly **two antipodal points**. The
cross divides the sea into the **four Blues**.

**Calm Belts.** Two windless, Sea-King-infested bands flank the Grand Line north
and south — impassable walls. The route is trapped in the band between them.

**Two halves.** The Grand Line is **Paradise** (first half), then the **New
World** (second half). It is **strictly one-way** — once entered, it cannot be
retraced.

**Entry — Reverse Mountain & Twin Capes.** Four sea currents (one per Blue) flow
_uphill_ to a summit, merge, and plunge down a single canal into Paradise — a
one-way gate. At the base sits **Twin Capes**, the first harbour, where the Log
Pose rules are first explained.

**Paradise (single-needle Log Pose) — a linear chain.** Straw Hat order:
Reverse Mountain -> Whisky Peak -> Little Garden -> Drum Island -> Alabasta ->
Jaya -> **Skypiea** (a vertical excursion, see §2) -> Long Ring Long Land ->
Water 7 / Enies Lobby -> Thriller Bark (in the Florian Triangle fog) ->
**Sabaody Archipelago** (end of Paradise; all seven Paradise routes converge here).

**The crossing — UNDER the Red Line.** The Red Line is impassable for pirates
over the top (that way runs through Mariejois). Instead: from Sabaody, coat the
ship and **dive ~10,000 m** to **Fish-Man Island**, through a tunnel passing
_beneath_ the Red Line, then rise into the New World. Draw it as a **vertical
underpass — never a jump.**

**The New World (three-needle Log Pose) — a branching network**, not a chain;
each island offers up to three successors. Order: Fish-Man Island -> Punk Hazard
-> Dressrosa -> Zou -> Whole Cake / Wano (a branch that splits and re-converges)
-> Egghead -> Elbaf -> onward.

**The end that isn't.** **Lodestar** is the _last_ island any Log Pose can
reach. **Laugh Tale** lies beyond it — **off the magnetic grid entirely**,
locatable only by triangulating the four Road Poneglyphs. No Log Pose reaches it.
=> **This is canon's own "no finish line"** — exactly the open-endedness we want.

---

## 2. Why the route winds — the rule that kills the swirl

The route **cannot** be a straight line or a clean spiral:

- **Every island has its own overpowering magnetic field.** A compass is useless
  — there is no "north" inside the Grand Line. Fields from multiple islands
  overlap and tug unpredictably.
- **The Log Pose is a one-way magnetic chain.** It records an island's field
  over a "set time" (minutes to a **full year** — Little Garden = 1 yr, Whisky
  Peak < 1 day), then points only to the _next_ island. It cannot point back.
- **The route winds north/south within the band** — islands protrude toward the
  Calm Belts or toward the Red Line, so the track weaves rather than running
  straight down the centre.
- **Weather curves every leg.** Neighbouring islands have fixed clashing climates
  (summer/winter/spring islands); their air masses collide and storm the sea
  between them. Ships take **curved paths to skirt the storms** — inter-island
  legs are gentle curves and zigzags, never straight.
- **The route is 3-D.** Two vertical excursions: **Skypiea** (the route blasts
  ~7,000 m _up_ into sky-island clouds above Jaya via the Knock-Up Stream, loops,
  drops back) and **Fish-Man Island** (~10,000 m _down_ under the Red Line).
- **Deviations add spurs and branches.** Eternal Poses skip the chain (the crew
  skipped Little Garden's 1-yr wait); Vivre Cards point to a _person_ (used to
  reach the moving island Zou); distress calls / alliances drive detours (Punk
  Hazard). Enies Lobby is a spur off the main chain.

**Net character:** a forced, zigzagging progression within a constrained band —
winding north/south, curving around storms, looping above and below the sea,
branching and re-converging. **Not a line. Not a spiral.**

---

## 3. Chart-layout spec — translating canon to the Atlas

1. **Winding band, not a swirl.** The route is a continuous line threading the
   islands in order, weaving side to side. **Vary every turn** — some legs swing
   wide, some are short and tight; avoid a uniform sine wave. (The current
   generated serpentine _is_ a uniform sine — replace it with a hand-shaped,
   varied path per chapter.)
2. **One-way wake/ahead.** Solid glowing wake behind the ship; dashed, fading
   route ahead. (Already in place.)
3. **The Red Line** is a real landmass band across the chart between Paradise and
   the New World. The route **plunges under it** at Fish-Man Island.
4. **Skypiea = an upward loop** — off the main line above Jaya, branching up to
   Skypiea and rejoining at Long Ring Long Land.
5. **Fish-Man Island = a downward plunge** beneath the Red Line band.
6. **New World branches** — the route may show an offshoot (the Zou -> Whole Cake
   / Wano split-and-reconverge); optional if it crowds the page.
7. **Spurs/detours** — Enies Lobby hangs off the chain as a spur; Thriller Bark
   sits in a patch of fog (the Florian Triangle).
8. **Laugh Tale stays unreachable** — a far glimmer beyond the last island, never
   joined by the solid route, hinted only by faint triangulation lines. This is
   the "no finish line."

---

## 4. Proposed Atlas chapters

Canon gives three natural regions — they become the Atlas chapters:

| Chapter | Region        | Islands                                                                                                                                             | Route character                                                                                               |
| ------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| I       | East Blue     | Foosha, Shells Town, Orange Town, Syrup Village, Baratie, Cocoyasi, Loguetown (7)                                                                   | The calm home sea before the Grand Line — a looser, gentler scatter. Ends at Loguetown -> Reverse Mountain.   |
| II      | Paradise      | Reverse Mountain, Whisky Peak, Little Garden, Drum, Alabasta, Jaya, Skypiea, Long Ring Long Land, Water 7, Enies Lobby, Thriller Bark, Sabaody (12) | Enter at Reverse Mountain; a tight, twisting chain; the Skypiea up-loop; ends at Sabaody.                     |
| III     | The New World | Fish-Man Island, Punk Hazard, Dressrosa, Zou, Whole Cake, Wano, Egghead, Elbaf (8)                                                                  | Dive under the Red Line at Fish-Man Island; a wilder, branching route; Laugh Tale unreachable on the horizon. |

**Note:** Paradise has 12 islands — likely too many for one comfortable chart
page. Recommend **4 pages**, splitting Paradise at the Skypiea excursion:
I East Blue / II Paradise — First Log (Reverse Mountain -> Skypiea) /
III Paradise — Second Log (Long Ring Long Land -> Sabaody) / IV New World.

**Voyage data note:** the backend currently exposes **15 milestones**; the art
library is **27**. At build time we decide whether the map renders the 15
milestones or the fuller canon set — the layout above supports either.

---

## 5. Build checklist (when assets are in)

- Replace the uniform sine serpentine with a **hand-authored, varied winding
  path** per chapter.
- Render the **Red Line** as a landmass band; the route dives under it at
  Fish-Man Island.
- Add the **Skypiea upward loop** and **Fish-Man downward plunge**.
- Keep **Laugh Tale** as an unreachable far glimmer — no solid route to it.
- Place islands in canon order; vary leg lengths so no two turns feel the same.
