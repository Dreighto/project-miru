# 02 — Gestures

**Applies to:** every touch interaction in every Miru surface. Tap, swipe, long-press, drag, pinch, pull-to-refresh, edge-swipe.
**Read this when:** you're wiring a new non-tap interaction, you're removing a gesture that already exists, or the user reports a gesture conflict ("the back swipe kept undoing my scroll").
**Skip this when:** the surface is tap-only and nothing scrolls horizontally.
**Length:** ~9 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [01_MOBILE_PWA.md](01_MOBILE_PWA.md), [05_ACCESSIBILITY.md](05_ACCESSIBILITY.md), [docs/pm/05_GESTURES_PM.md](../pm/05_GESTURES_PM.md).

---

## The core rule

**One gesture, one job, globally.**

If swipe-left-on-card-tile means "cycle variant" on the Cards tab, it does not mean "delete" on the Watchlist and "mark read" in notifications. Pick a global meaning, honor it everywhere, or don't use the gesture.

**Why:** gestures are invisible affordances. Users can't see them. The only way they learn a gesture is by accident or by being told once. If the same gesture does different things in different places, they never form muscle memory — they just stop using gestures and fall back to taps. This is the single most common failure mode in TCG apps ([r/TCGPlayer thread on Manabox gestures](https://www.reddit.com/r/tcgpro/), where users complain that swipe means add-one-copy in some views and mark-as-picked in others).

**How to apply:** keep a canonical gesture table (below). Before adding a new gesture, check whether the intended action already has a tap path. If so, the gesture is nice-to-have — and a duplicate doesn't justify breaking consistency.

---

## The Miru gesture table

This is the ground truth. If you want to add a row, propose in `docs/pm/05_GESTURES_PM.md` and link evidence.

| Gesture | Global meaning | Where it works |
|---|---|---|
| Tap | Primary action of the element | Universal |
| Long-press (≥500ms) | Reveal context (sheet, menu) | Universal, except card tiles where it starts drag — see below |
| Swipe left on list row | Reveal destructive / secondary action (iOS Mail pattern) | Any list row with a secondary action |
| Swipe right on list row | Reveal primary affirmative action | Any list row with a primary action |
| Swipe up / down on sheet | Dismiss / expand sheet | Every sheet |
| Swipe left / right on card tile | Cycle variant (prev/next printing) | Card tiles only, PM-specific — see [docs/pm/05_GESTURES_PM.md](../pm/05_GESTURES_PM.md) |
| Pinch | Zoom image | Only on card detail images |
| Pull down at top of scroll | Refresh | Only where a refresh is meaningful |
| Edge-swipe from left | Back (system) | Do not intercept. Ever. |
| Two-finger horizontal swipe | (Reserved for iOS back) | Do not intercept |
| Double-tap | (Reserved — do not use) | Do not use as app-level affordance |

### Why "do not intercept" matters for edge-swipe

iOS uses an edge-swipe-from-left for back navigation. If you put a horizontal swipe at the left edge of a scroll container, users will hit the edge gesture instead. On Android, the edge gesture is back-navigation too (since Android 10's gesture nav). Intercepting it leaks a pattern the OS owns — users who swipe right from the edge to go back will end up doing whatever app-level thing you wired there, which feels like the OS is broken.

The fix: keep horizontal gestures **inset by at least 20px** from the left and right edges. On a 390px-wide iPhone, that's a 350px gesture zone — plenty. This also matches [Apple HIG Gestures](https://developer.apple.com/design/human-interface-guidelines/gestures).

### Why "do not use double-tap"

Double-tap is globally owned by iOS as a zoom gesture on text and images in Safari. It is also used by VoiceOver and TalkBack as the "activate" action. Adding an app-level double-tap creates:

1. A zoom flash before the action fires (bad feel).
2. A conflict with screen readers (accessibility failure).
3. Discoverability problems — nobody guesses double-tap in 2026 outside Apple's own built-in apps.

If you catch yourself wanting double-tap, you want a different gesture or a button. Source: [Apple HIG Gestures — Standard Gestures](https://developer.apple.com/design/human-interface-guidelines/gestures).

---

## Haptic vocabulary

Haptics are the *confirmation channel* for gestures. Visual feedback is the primary signal; haptics make silent, eyes-off interactions feel correct. The iOS taxonomy (also the one we use globally, because Android has no fine-grained standard):

| Pattern | Meaning | When |
|---|---|---|
| `selection` | A value changed | Stepper +/−, chip toggle, variant cycled |
| `impact.light` | Small affirmative | Card tile tapped to open detail |
| `impact.medium` | Action committed | Watchlist added, deck card added |
| `impact.heavy` | Significant action | Deck saved, watch price hit |
| `notification.success` | Backend confirmed | Deck validate passed |
| `notification.warning` | Something may not be right | Deck validate warning (wrong leader colors) |
| `notification.error` | Action failed | Save error |

**Constraint: iOS Safari does not support `navigator.vibrate`.** We documented this in [01_MOBILE_PWA.md](01_MOBILE_PWA.md). On iOS, the only way to trigger a haptic from a PWA is the iOS 18+ checkbox-switch workaround (off-screen `<input type="checkbox" switch>` that we programmatically click). On Android, `navigator.vibrate([10])` works. Source: [MDN — Navigator.vibrate()](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/vibrate), [GitHub — browser-compat-data#29166](https://github.com/mdn/browser-compat-data/issues/29166).

**Rule:** wrap haptics in a `haptic(kind)` util that degrades gracefully. Never let a missing haptic break a visual animation — they are additive.

**Rule:** haptics respect `prefers-reduced-motion`. If reduced motion is on, skip the haptic too (the user is signaling they want a quieter interface). Source: [MDN — prefers-reduced-motion](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion).

---

## Direction lock

When a horizontal gesture starts (e.g. card-tile swipe), the vertical scroll must *stop* responding. When a vertical scroll starts, the horizontal gesture must *stop* responding. Without this, the user gets a jittery "which way am I going" feel, and on slow devices the axis flips mid-gesture.

**Implementation:**

1. On `pointerdown`, record `x0, y0`.
2. On first `pointermove` where `|dx| > 4px OR |dy| > 4px`, compute `angle = atan2(|dy|, |dx|)`.
3. If `angle < 30°` → axis is horizontal. Set `touch-action: pan-y` → `touch-action: none` on the element, call `setPointerCapture`, consume all subsequent moves as horizontal.
4. If `angle > 60°` → axis is vertical. Release capture, let scroll handle it.
5. Between 30° and 60° → ambiguous. Default to vertical (scrolling is the dominant motion on mobile — if in doubt, don't block scroll).

The 30°/60° cones come from practical testing against diagonal drags. A single midline (45°) flips too easily and creates the "did I mean to swipe or scroll?" feel. [Pointer Events Level 3 spec](https://www.w3.org/TR/pointerevents3/) and [Carlos Rojas — Pointer Events Guide](https://web.dev/articles/pointer-events) both recommend a similar cone approach.

**Use `setPointerCapture`** so that even if the finger leaves the element, subsequent `pointermove` and `pointerup` still fire on the original target. Without capture, a fast swipe that drifts off the tile leaves the gesture "hanging" — the release handler never fires, the card stays half-slid. Source: [MDN — Element.setPointerCapture()](https://developer.mozilla.org/en-US/docs/Web/API/Element/setPointerCapture).

---

## `touch-action` as the contract

`touch-action` is a CSS property that tells the browser which panning/zooming gestures it should handle natively. It is the single most important CSS property for gesture code. Wrong value = laggy gesture, double-scrolling, lost events.

| Value | Means | Use where |
|---|---|---|
| `auto` | Browser does everything (pan, pinch-zoom, double-tap-zoom) | Default page scroll |
| `manipulation` | Browser pans and pinch-zooms but **not** double-tap-zoom. Removes the 300ms tap delay. | Every tappable element (buttons, chips, card tiles) |
| `pan-y` | Browser vertical-scrolls only; horizontal is yours | Horizontal-swipe containers that live inside a vertical scroll |
| `pan-x` | Browser horizontal-scrolls only; vertical is yours | Rare — horizontal list with vertical gestures inside |
| `none` | Browser does nothing; you handle every pointer event | Canvases, custom drag-drop targets, image pinch-zoom |

**Default every interactive element to `touch-action: manipulation`.** This kills the 300ms tap delay globally. Source: [Chrome Developers — 300ms tap delay gone away](https://developer.chrome.com/blog/300ms-tap-delay-gone-away/), [MDN — touch-action](https://developer.mozilla.org/en-US/docs/Web/CSS/touch-action).

**Only use `touch-action: none` when you will call preventDefault on every move.** Setting `none` and then not handling pointer events yourself creates a dead zone where nothing scrolls. Catch this in review.

**Inside a sheet, set `touch-action: pan-y` on the scrollable body and `touch-action: none` on the drag handle.** Without this, dragging the handle also scrolls the content underneath — which feels broken every time.

---

## Swipe-to-reveal vs swipe-to-commit

Two patterns, two different physics. Get them confused and the UI feels either twitchy or unresponsive.

### Swipe-to-reveal (iOS Mail pattern)

User drags left, action buttons reveal underneath. User releases:
- If dragged less than **40%** of row width → snap back.
- If dragged 40–80% → stay in revealed state, user taps the button.
- If dragged more than **80%** → auto-commit the primary action.

This is the pattern you want 95% of the time. It lets cautious users see what they're about to do, and lets power users blast through.

### Swipe-to-commit (Tinder-style)

User drags and releases past threshold. Action fires immediately; no intermediate state.

This is only appropriate when:
1. The action is frequently repeated and must feel fast.
2. There is an undo (snackbar) within 5 seconds.
3. Hitting the wrong card accidentally costs nothing permanent.

For PM, the only place we currently use swipe-to-commit is the Watchlist price-alert dismiss — which has a 4s undo snackbar.

### The velocity heuristic

For both patterns, use **velocity** as a tiebreaker. If release velocity > 0.3 px/ms in the swipe direction, count it as past-threshold even if the finger didn't travel far. This matches iOS Mail and Gmail behavior — users with strong muscle memory flick without dragging fully. Measured from iOS Mail source observation and [Material 3 — Swipe to dismiss](https://m3.material.io/components/lists/specs#1e4a0c18-c7ab-4e8c-be06-2e68d1b29d89).

---

## Long-press

Long-press is the **context reveal** gesture. It is *not* a shortcut for a tap action.

| Rule | Reason |
|---|---|
| Hold duration: **500ms** | Shorter feels accidental (iOS Safari selects text at ~300ms). Longer feels broken. |
| Haptic fires at hold threshold | Signals "the press has registered" even if user hasn't released |
| Movement cancels | If finger moves >10px before 500ms, it's a drag or a scroll, not a long-press |
| Works on mouse (contextmenu) too | Desktop users can right-click and get the same sheet |
| Never the *only* way to reach an action | Long-press must be a shortcut to something a button or swipe also reaches |

**The 500ms number:** Android's default long-press is 500ms ([Android SDK `ViewConfiguration.getLongPressTimeout()`](https://developer.android.com/reference/android/view/ViewConfiguration#getLongPressTimeout()), ≈500ms on most devices). iOS's default is ~500ms too. Matching the OS means the user's muscle memory works immediately.

**The haptic rule:** fire `impact.medium` when the 500ms hold threshold hits, not on release. This tells the user "okay, this counted as a long-press" before they commit. Without this, users release early because they're not sure if they've pressed long enough, and the action doesn't fire. We saw this repeatedly in usability testing on Dispatcher's long-press-to-rerun menu.

---

## Drag

For PM, drag is currently scoped to: drag card from pool into deck list, drag card out of deck list to remove. Do not introduce drag elsewhere without an explicit spec.

### Drag physics

- **Hold + move.** 500ms hold to start drag, with haptic at start. Without hold, the drag trigger conflicts with scroll.
- **Ghost follows finger.** A translucent clone of the source element follows the pointer. Use `position: fixed`, `pointer-events: none`, `opacity: 0.8`, `transform: translate(x, y)`.
- **Drop zone highlights.** Every valid drop zone gets a 2px accent outline when the drag enters.
- **Release on invalid zone → animate back.** Never leave the ghost stranded; always resolve.
- **Auto-scroll at edges.** If the ghost is within 60px of the top or bottom of the scroll container, scroll that direction at 3px/frame.

### Accessibility fallback

Every drag-drop action must have a tap-based alternative. In PM, "add card to deck" works via drag *and* via a + button on the tile. Drag is a shortcut for users who prefer it; the + button is the contract.

Rationale: drag requires fine motor control and two-handed use for some tremor users; iOS VoiceOver cannot replay arbitrary drag. [WCAG 2.5.7 Dragging Movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html) requires a non-drag alternative for every draggable action.

---

## Pull-to-refresh

Only add pull-to-refresh where:
1. The data on the screen is expected to change within a single user session.
2. A refresh is not already happening on interval or on focus.
3. There is actually something to refresh — not a static detail page.

For PM: use it on Home (feed), Watchlist (prices), Cards list (set updates). Not on Deck Builder (local state), Card Detail (immutable), Profile (static).

### Pull-to-refresh physics

- User pulls down at `scrollTop === 0`.
- At **60px** pull, haptic `selection`. Spinner appears.
- At **100px** pull, haptic `impact.medium`. Release at this point or past fires the refresh.
- On release before 100px, spring back.
- On release past 100px, hold at 80px while the request is in-flight, then release.

These numbers match [Material 3 — Pull to refresh](https://m3.material.io/components/pull-to-refresh) defaults (40dp trigger with elastic resistance).

### Browser's native pull-to-refresh

iOS Safari and Chrome Android both have *their own* pull-to-refresh that refreshes the whole page. We do not want that — it nukes client state. Disable it on the `<body>` or scroll container with:

```css
body { overscroll-behavior-y: contain; }
```

This is already set in `pm/storefront/src/app.css`. Do not remove. Source: [MDN — overscroll-behavior](https://developer.mozilla.org/en-US/docs/Web/CSS/overscroll-behavior), [Chrome Developers — overscroll-behavior](https://developer.chrome.com/blog/overscroll-behavior).

---

## Pinch-zoom on card images

Card detail images (in the bottom sheet) get pinch-zoom. Nothing else in the app does.

**Why only detail images:** pinch anywhere else (on a card tile, on a grid) fights iOS double-tap-zoom and usually ends with the user accidentally zooming the whole page. Restricting pinch to one well-marked context keeps it safe.

**Implementation notes:**

- Use the [Visual Viewport API](https://developer.mozilla.org/en-US/docs/Web/API/Visual_Viewport_API) to read scale, not manual two-finger pointer math. The API handles iOS's rubber-band and trackpad pinch too.
- Clamp scale to 1.0–4.0. Above 4.0, image pixelates; below 1.0, user is fighting the browser.
- When scale > 1.0, consume all pan events yourself (`touch-action: none` on the image). When scale === 1.0, let the sheet handle scroll.
- Double-tap on a zoomed image resets scale to 1.0 (the one acceptable use of double-tap in the app — it matches iOS Photos).

---

## Anti-patterns (with evidence)

### Swipe as the only way

Pattern: "Swipe the card to add it to your deck." No tap path.
Why it fails: users who don't discover the swipe can't add cards. iOS VoiceOver users can't perform the swipe at all.
Evidence: [WCAG 2.5.7 Dragging Movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html) forbids this.
Fix: always have a + button.

### Horizontal swipe inside a horizontal scroller

Pattern: a horizontally-scrolling row of card tiles, and each tile also responds to left-swipe to cycle variants.
Why it fails: the outer scroll and the inner swipe compete. Users end up scrolling when they meant to cycle, or vice versa, and the tile's momentum feels wrong.
Fix: pick one. If the row is horizontally scrollable, tiles only respond to tap. If tiles cycle variants, the row doesn't scroll horizontally — it wraps to a grid.

### Long-press for a primary action

Pattern: "Long-press to add to watchlist."
Why it fails: long-press is invisible. Users don't guess it. iPhone users who accidentally long-press in Safari get the system share menu, not the app action.
Evidence: [Nielsen Norman Group — Long-Press Gestures](https://www.nngroup.com/articles/touch-target-size/) documents low discoverability of long-press.
Fix: long-press reveals secondary actions. Primary action is a button.

### Swipe-to-delete without undo

Pattern: swipe-left deletes a card from the watchlist. No snackbar, no undo.
Why it fails: finger slips. Everyone has deleted the wrong email in Gmail once. If you don't give an undo, the user loses work.
Evidence: [Gmail's 5-second undo](https://support.google.com/mail/answer/2819488) is the reference. Every 1-star review of a TCG app complains about "lost my watchlist" at least once.
Fix: 4–5s undo snackbar on every destructive swipe.

### Pinch on list/grid pages

Pattern: pinch to zoom the card grid.
Why it fails: iOS reserves pinch for page zoom in Safari. Your gesture will sometimes work, sometimes zoom the whole page depending on where the user's fingers land relative to a selectable element.
Fix: pinch is scoped to the detail image. Use a density toggle (compact/comfy) for the grid.

### Gestures requiring both hands

Pattern: two-finger swipe to reorder, three-finger tap for deck menu.
Why it fails: PM is designed one-handed (see [00_PRINCIPLES.md §2](00_PRINCIPLES.md)). Two-finger gestures break the contract.
Fix: design so every gesture is single-finger.

### Gestures with no visual affordance or tutorial

Pattern: "You can swipe up on the card tile to watch it. We don't tell anyone."
Why it fails: nobody discovers it. The feature might as well not exist.
Fix: either add a visible affordance (a ghosted arrow on first use), or move the action to a visible control, or accept that this gesture is power-user-only and lives alongside the primary tap path.

---

## Testing gestures

You cannot test gestures via unit tests. Unit tests verify that handlers exist; they don't verify that the gesture *feels* correct.

### The three-device test

Before shipping a gesture, test on:

1. **An iPhone on iOS Safari** (not simulator — simulator pointer events differ). The SE3 is the worst-case for thumb reach; iPhone 16 is the worst-case for height.
2. **An Android on Chrome** — specifically a Samsung with One UI, because Samsung Internet behaves differently (and a chunk of One Piece players are on Galaxy devices per [Samsung DevCon 2024](https://developer.samsung.com/internet)).
3. **A desktop with a trackpad** — gesture events fire on trackpads via pointer events, and many users are on desktop too.

### The "one-handed after a drink" test

The final check: can you do the gesture with one hand, on a phone, at a crowded LGS, with a drink in the other hand, while half-watching someone's turn? If the answer is no, it's not a gesture — it's a desktop interaction port.

### The thumb-drag map

Map every gesture onto the [thumb reach zones from 00_PRINCIPLES.md](00_PRINCIPLES.md#thumb-zones). Green zone gestures should be the ones users do the most (variant cycle, add to deck). Red zone gestures should be rare (destructive, close-app).

---

## What to do when a gesture conflicts with the platform

If the OS reserves a gesture (edge-swipe-back, pull-down-for-notifications, three-finger-swipe on iPad, two-finger-scroll on iPad), **we lose.** Do not attempt to override.

If the OS gesture's behavior conflicts with something we want to build, we redesign. We don't argue with iOS. Source: [Apple HIG — Gestures](https://developer.apple.com/design/human-interface-guidelines/gestures), and every forum thread about "my app broke when iOS changed the back gesture" ([example on r/iOSProgramming](https://www.reddit.com/r/iOSProgramming/)).
