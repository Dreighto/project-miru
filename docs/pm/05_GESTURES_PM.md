# 05 — PM Gestures

**Applies to:** gestures specific to PM surfaces, particularly the swipe-for-variants pattern. Extends the universal gesture vocabulary.
**Read this when:** you're wiring a gesture inside a PM surface; you want to know why swipe-for-variants is locked in; you're proposing a new PM-specific gesture.
**Skip this when:** the gesture is universal (swipe-to-dismiss sheet, pull-to-refresh). Those live in [docs/ui_ux/02_GESTURES.md](../ui_ux/02_GESTURES.md).
**Length:** ~6 pages.
**Related docs:** [docs/ui_ux/02_GESTURES.md](../ui_ux/02_GESTURES.md) (the universal rules this doc builds on), [02_PM_PRIMITIVES.md](02_PM_PRIMITIVES.md) for CardTile.

---

## The PM gesture additions

These are the PM-specific gestures that extend the [universal gesture table](../ui_ux/02_GESTURES.md#the-miru-gesture-table).

| Gesture | PM-specific meaning | Surface |
|---|---|---|
| Swipe left/right on CardTile | Cycle variant (prev/next printing) | CardTile everywhere it appears |
| Long-press on CardTile | Open card context sheet (add, watch, share) | CardTile everywhere |
| Long-press on pool card (Deck Builder) | Start drag-to-deck | Deck Builder pool |
| Drag from pool to deck | Add card to deck at target position | Deck Builder |
| Drag from deck to pool / gutter | Remove card from deck | Deck Builder |
| Long-press on a watchlist row | Enter multi-select mode | Watchlist page |
| Swipe up on a deck row (Saved decks) | Duplicate deck | Saved decks list |
| Pinch on card detail image | Zoom image | Card detail sheet, images tab |

All other gestures inherit from the universal table. When in doubt, universal wins.

---

## The canonical: swipe for variants

This is the PM-defining gesture. It's the single interaction that most strongly signals "PM is built for TCG players who know variants matter."

### The meaning

On a CardTile, swiping left or right cycles through the available variants of that card. Left = previous variant; right = next variant.

Variants in OPTCG include:
- **Regular** (C / UC / R / SR / SEC) — the base printing.
- **Alternate art (AA, Alt-Art)** — usually Super Rare or higher.
- **Parallel / Foil** — rare treatments.
- **Manga rare** — rare art-specific treatment.
- **Box topper / Promo** — distributed differently, same card code.

A single card can have 2–6 variants. They all share a code but differ in artwork, rarity treatment, and price.

### Why this gesture

- **Variants are the reason TCG players open the app.** "Which variant of this card?" is a frequent question at an LGS.
- **Tap opens detail.** Tap is reserved for "tell me about this card."
- **Swipe is cheap.** One-handed, one-thumbed, no navigation required.
- **Cycling is inherent.** Variants form a ring; swipe is the natural gesture for ring navigation.

### Physics

- **Direction lock.** Horizontal gesture locks to horizontal at 30° cone. See [docs/ui_ux/02_GESTURES.md §Direction lock](../ui_ux/02_GESTURES.md#direction-lock).
- **Threshold.** 40% of tile width to commit, or velocity > 0.3 px/ms. Below threshold → snap back.
- **Visual feedback.** During drag, the current variant image slides off while the next variant slides in. Edges fade.
- **Haptic.** On commit (threshold crossed), `selection` haptic. Never during-drag.
- **Momentum.** No momentum scrolling — one swipe = one variant cycle. Rapid repeated swipes work but each is a discrete commit.

### Visual affordance

- **Variant dots** (see [02_PM_PRIMITIVES.md §VariantDots](02_PM_PRIMITIVES.md#variantdots)) at the bottom of the tile show how many variants exist and which is current.
- **On first-use hint.** The very first time a user encounters a CardTile with 2+ variants, a one-time animation: the dots subtly pulse, and a small "swipe to cycle" ghost affordance fades in and out once. Never repeated for that user.
- **On desktop / trackpad:** horizontal two-finger scroll on the tile cycles variants, matching the touch gesture.

### What swipe does *not* do on a CardTile

- Does not add to deck. (That's long-press + drag, or `+` button.)
- Does not add to watchlist. (That's the WatchlistStar.)
- Does not scroll the grid. (The grid scrolls vertically; swipe on a tile locks to horizontal.)
- Does not open detail. (Tap does.)

### The horizontal scroller problem

The one tricky case: if a CardTile lives inside a horizontally-scrolling row (e.g. "Your recent decks" on Home), the swipe gesture conflicts with the row's scroll.

**Resolution:** in a horizontally-scrolling context, the tile drops the swipe-for-variants behavior. Variant cycling is not available from a horizontal scroller. Users who want to cycle variants tap into the card's detail sheet and cycle there (tappable dots).

This is a deliberate constraint — a single gesture has a single global meaning, and the cost is that some contexts lose the gesture. Beats having the gesture mean different things in different places.

---

## Long-press on CardTile

Long-press reveals a context sheet with secondary actions. Think "right-click menu."

### Physics

- **500ms hold.** See [docs/ui_ux/02_GESTURES.md §Long-press](../ui_ux/02_GESTURES.md#long-press).
- **Haptic `impact.medium`** at the hold threshold. Signals "press has registered."
- **Movement cancels.** > 10px of drift before 500ms = scroll or drag, not long-press.
- **Sheet opens** from the bottom. Half-height, autofit content.

### Menu items (context-dependent)

On a pool card in Deck Builder:
- Add to deck (quick)
- Add 2, 3, 4 (stepper)
- Add to watchlist
- View details
- Copy card code
- Share

On a deck card in Deck Builder:
- Increase / decrease count
- Remove from deck
- View details
- Move to sideboard (future)

On a watchlist card:
- View details
- Edit target
- Remove from watchlist
- Add to deck

The list is tailored to the context. Menu items never change position within a context (learn-once principle).

### Keyboard alternative

Every long-press action has a keyboard path. On a CardTile, the focus-within state + Space or Enter opens the context sheet. Shift+F10 also triggers it (standard context-menu key). Right-click works on desktop.

---

## Drag-to-deck (Deck Builder)

Drag cards from the pool into the deck. The power-user pattern.

### When it fires

- User long-presses (500ms) on a pool card → drag starts.
- A translucent ghost of the card follows the finger.
- Valid drop zones highlight with a 2px accent outline.

### Drop zones

- The "Deck" tab strip (top of screen) — dropping here adds 1× to the deck.
- Within the deck view, dropping between cards inserts at that position.
- Dragging to the edges of the scroll container auto-scrolls.

### Release behavior

- Released on valid zone → commit. Haptic `impact.medium`. Card's row animates in.
- Released on invalid zone → ghost animates back to origin over 200ms. No haptic.
- Released on the "Deck" tab strip specifically → instant add without scroll. User doesn't need to scroll to the deck view.

### Accessibility fallback

- Every drag-drop has a `+` button alternative on the CardTile in Pool mode.
- VoiceOver reads the CardTile's add action as "double-tap to add to deck."
- WCAG 2.5.7 compliance.

### Sideboard drag (future)

When sideboards are added, a third drop zone appears. Until then, the pattern is pool → deck only.

---

## Long-press on watchlist row

Enters multi-select mode.

- Haptic `impact.medium` on hold threshold.
- The row's leading icon switches to a checkbox.
- Subsequent taps on watchlist rows toggle selection.
- A sticky footer appears: "X selected · [Remove] [Set target] [Move]."
- Exit: tap the "Done" button in the header, or clear all selections.

This is modal behavior — users enter multi-select intentionally and exit cleanly. We don't default to multi-select; single actions are one tap.

---

## Swipe up on a deck row (duplicate)

In the "Saved Decks" list, swipe up on a row to duplicate it.

- Physics: 30% of row height + release.
- Haptic: `impact.medium` on commit.
- New deck appears immediately in the list, named "[Original] (copy)."
- Undo snackbar: 4s.

Rationale: duplicating is the primary operation for "I want to try a variant of this deck." Common enough to warrant a gesture.

### Why up, not left/right

Left/right on a list row is already "reveal action" (swipe-left = remove, swipe-right = pin). Up is free on a horizontal list.

---

## Pinch on card detail image

The one pinch gesture in PM. Scoped to the card detail sheet's image tab.

- Pinch out: zoom in, up to 4× scale.
- Pinch in: zoom out, clamped to 1×.
- Pan: when zoomed > 1×, drag pans within the image.
- Double-tap (zoomed in): reset to 1×.
- Double-tap (at 1×): zoom to 2×.

See [docs/ui_ux/02_GESTURES.md §Pinch-zoom on card images](../ui_ux/02_GESTURES.md#pinch-zoom-on-card-images) for implementation notes using the Visual Viewport API.

---

## Why these specific gestures

Every gesture added to PM has to pay for itself. Here's why each one is on the list.

| Gesture | Alternative (if we didn't have it) | Cost of alternative |
|---|---|---|
| Swipe for variants | Tap into detail, tap dots | 2 taps instead of 1 gesture — multiplied by every variant check, which is the core use case |
| Long-press for context menu | Hunt for an overflow `⋯` button | 3 taps (tile to open, `⋯` to open menu, item tap) |
| Drag to deck | Tap `+` button | `+` is already there as the tap path; drag is the power-user shortcut |
| Long-press for multi-select | Dedicated "Edit" toolbar | Extra screen real estate, always visible |
| Swipe-up to duplicate deck | Detail page + "Duplicate" menu item | 3 taps and screen change |
| Pinch for card zoom | Modal image viewer | Extra surface, breaks flow |

In each case, the gesture saves multiple taps in a frequent operation. Gestures that save one tap in a rare operation don't earn their way in.

---

## Gesture conflicts inside PM

Known conflicts and resolutions:

### Swipe on CardTile inside a horizontal scroller

Resolved by *not* supporting swipe on tiles in horizontal scrollers (see above).

### Long-press in a sheet that contains CardTiles

Long-pressing a pool card inside the Deck Builder opens a context menu. Long-pressing the *sheet* elsewhere dismisses it (if drag gesture fires). The discriminator: long-press on a CardTile is on the tile's interactive region; long-press on the sheet body outside a tile is interpreted as a drag attempt.

### Pull-down inside a sheet vs pull-to-refresh

Sheets dismiss on pull-down. Underlying pages pull-to-refresh. A sheet covers the page; users can't reach the page's pull-to-refresh while a sheet is open. Not a conflict, but worth naming.

### Edge-swipe-back vs horizontal swipe on a tile

iOS and Android edge-swipe back is reserved by the OS. Tile swipes are inset 20px from screen edges. Tiles near the edge of the screen deliberately don't register swipe if the gesture starts within 20px of the edge.

---

## The gesture discoverability strategy

Gestures are invisible. PM's strategy:

1. **Visible affordances.** Variant dots tell users "there's more."
2. **First-use hints.** One-shot animations on first encounter. Never repeated.
3. **Explain in Profile.** Profile > About > Gestures lists every PM gesture with a short demo. Users rarely look, but it's there for the curious.
4. **Redundant taps.** Every gesture has a tap path. No user is locked out of any feature.

We don't rely on in-app tutorials. The first-use hint + the tap alternative + the gesture table in Profile covers everyone.

---

## Gesture rules we inherit from universal

From [docs/ui_ux/02_GESTURES.md](../ui_ux/02_GESTURES.md):

- **One gesture, one job, globally.** Swipe on a CardTile always means "cycle variant," everywhere. Never "delete," never "open," never "watch."
- **No double-tap as app action.** Reserved for OS (zoom).
- **Edge-swipe is OS territory.** Don't intercept.
- **Haptic vocabulary.** Selection / impact-light / impact-medium / impact-heavy / notification-success / notification-warning / notification-error. Consistent across PM.
- **Keyboard alternatives.** Every gesture has a tap + keyboard path.
- **Accessibility.** WCAG 2.5.7 dragging alternative for every drag.

When PM adds a gesture, we check that universal rules still hold.

---

## Proposing a new PM gesture

Before adding a gesture:

1. **Does it save ≥ 2 taps in a frequent operation?** If not, skip.
2. **Does it conflict with an existing gesture?** Run the conflict table.
3. **Does it have a visible affordance or first-use hint?** If not, add one or don't add the gesture.
4. **Does it have a tap / keyboard alternative?** WCAG 2.5.7 requires this.
5. **Would a user who hasn't opened PM in 3 months still remember it?** If it needs a tutorial to remember, it's the wrong gesture.

If all five check out, propose the gesture. Document it in this doc and the universal table.
