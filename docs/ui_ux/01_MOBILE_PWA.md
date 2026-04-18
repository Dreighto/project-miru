# 01 — Mobile + PWA reality

**Applies to:** any change that ships on mobile browsers or installed PWAs. Safe areas, viewport math, keyboard handling, install flows, iOS Safari bugs, Android fragmentation.
**Read this when:** you're touching layout at the viewport edge, fixed-positioned elements, scroll containers, inputs that trigger the keyboard, or install affordances. Also read before claiming "it works on mobile" — because it probably doesn't.
**Skip this when:** desktop-only admin surfaces, backend code, design tokens without layout impact.
**Length:** ~14 pages.
**Related docs:** [02_GESTURES.md](02_GESTURES.md), [05_ACCESSIBILITY.md](05_ACCESSIBILITY.md), [06_PERFORMANCE.md](06_PERFORMANCE.md).

---

## The baseline device

The 2026 baseline is **not** a Pixel 9 Pro, not an iPhone 17 Pro, and not your development machine.

- **Primary iOS:** iPhone 13 / iPhone SE (3rd gen) on iOS 17. Still in wide circulation. Still shipping with the notch and the 4.7" SE form factor.
- **Primary Android:** Samsung Galaxy A54 / A55 on Android 14 with Samsung Internet or Chrome. Mid-range Snapdragon 7-series or Exynos 1380. 6GB RAM. 60Hz display.
- **Network:** 4G LTE, not 5G. Often degraded in LGS basements and convention center wifi.

If a layout only feels good on a ProMotion 120Hz display with 16GB RAM on 1Gbps wifi, it isn't shipped.

---

## The viewport unit rules

### `100vh` is broken. Use `100dvh`.

Historical bug: `100vh` on iOS Safari calculates against the *largest possible viewport* with all toolbars hidden. When the address bar is visible (which is most of the time), `100vh` elements overflow below the fold.

Current fix: `100dvh` (dynamic viewport height) updates as browser chrome expands and contracts. Universal support in iOS Safari 15.4+, Chrome 108+, Firefox 101+. ([CSS values spec, `env()`](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Values/env); [explainer on dvh/svh/lvh](https://savvy.co.il/en/blog/css/css-dynamic-viewport-height-dvh/))

```css
/* Default rule: use dvh with a vh fallback */
.hero {
  height: 100vh;   /* fallback for anything under Safari 15.4 */
  height: 100dvh;  /* real answer */
}
```

### When to use which unit

- `100dvh` — full-screen layouts that adapt as chrome shows/hides. **Default choice.**
- `100svh` — smallest viewport with all toolbars visible. Use when you want content locked to the most-constrained state (rare).
- `100lvh` — largest viewport, all toolbars hidden. Causes overflow when chrome is visible. **Don't use.**
- `100vh` — legacy alias. Broken on iOS. **Don't use** except as a fallback alongside `100dvh`.

### VisualViewport for precise keyboard-aware layouts

`dvh` handles chrome, not keyboard. When an input focuses and the virtual keyboard appears, you often need to anchor a button above the keyboard. For that, use the VisualViewport API. ([MDN](https://developer.mozilla.org/en-US/docs/Web/API/VisualViewport))

```js
const viewport = window.visualViewport;
function setVH() {
  document.documentElement.style.setProperty(
    '--vvh', `${viewport.height}px`
  );
}
viewport.addEventListener('resize', setVH);
viewport.addEventListener('scroll', setVH);
setVH();
```

```css
.keyboard-anchored-bar {
  position: fixed;
  bottom: 0;
  height: calc(var(--vvh, 100dvh) * 0 + 56px); /* anchor above kb */
}
```

### The iOS 26 fixed-element offset bug

iOS 26 (shipped September 2025) introduced a regression where `position: fixed` and `position: sticky` elements render ~20px higher than computed after the user taps the address bar and dismisses the keyboard. Fixed in iOS 26.1 beta but still broken in 26.0.x at time of writing. Workarounds are ugly — we've avoided them by not relying on `position: fixed` for critical overlays on iOS 26 users. If we hit the bug, toggle `document.body.style.backgroundColor` on modal open to force Safari to recompute toolbar geometry. ([Apple Discussions thread](https://discussions.apple.com/thread/256138682); [Mastodon issue tracker](https://github.com/mastodon/mastodon/issues/36144))

**Rule:** prefer `position: sticky` with a flex parent over `position: fixed` for anything that *could* be expressed inside a scroll container. Sticky + flex survives the iOS 26 bug; `fixed` doesn't.

---

## Safe areas

Safe areas are the inset distances from the viewport edge where content is guaranteed to be visible, not obscured by notches, Dynamic Island, home indicator, or gesture bar.

### The four env() values

```css
/* top: notch + Dynamic Island */
padding-top: env(safe-area-inset-top);

/* bottom: home indicator on notchless phones, gesture bar on Android */
padding-bottom: env(safe-area-inset-bottom);

/* left/right: only nonzero in landscape */
padding-left:  env(safe-area-inset-left);
padding-right: env(safe-area-inset-right);
```

### iPhone 16 safe-area insets (reference values)

From Useyourloaf's measured values ([iPhone 16 screen sizes](https://useyourloaf.com/blog/iphone-16-screen-sizes/), [iPhone 15 reference](https://useyourloaf.com/blog/iphone-15-screen-sizes/)):

| Device | top (portrait) | bottom (portrait) |
|---|---|---|
| iPhone 16 / 16 Plus | 59px | 34px |
| iPhone 16 Pro / Pro Max | 62px | 34px |
| iPhone 15 / 15 Plus | 59px | 34px |
| iPhone SE 3rd gen | 20px | 0px |

Landscape shifts insets to left/right. A 6.7" iPhone lands at `left: 59px; right: 59px; bottom: 21px` in landscape.

### The `max()` trick for bottom bars

When a bottom bar has its own padding *and* needs to respect the home indicator, combine them with `max()` so we never shrink below the baseline:

```css
.bottom-nav {
  padding-bottom: max(8px, env(safe-area-inset-bottom));
}
```

This reads: "at least 8px of breathing room, more if the device demands it." PM's `app.css` already does this with `calc(var(--bottom-nav-height) + env(safe-area-inset-bottom, 0px))`.

### Required viewport meta tag

Every page gets:

```html
<meta name="viewport"
      content="width=device-width, initial-scale=1, viewport-fit=cover">
```

Without `viewport-fit=cover`, content doesn't extend under the notch and you lose visual continuity. PM's layout already sets this implicitly via SvelteKit defaults — verify it in your `<svelte:head>`.

### Deprecated manifest values

`apple-mobile-web-app-status-bar-style` content values: `default` (white bar, dark text), `black` (black bar, white text), `black-translucent` (content extends under the bar). Use `black-translucent` for PM to extend the dark canvas all the way up.

---

## iOS Safari quirks

### 1. The 300ms tap delay is gone.

It's gone when `<meta name=viewport content="width=device-width">` is present. Which it always is in our templates. Don't ship `FastClick` or any other legacy tap-delay workaround. ([Chrome developers, 2014](https://developer.chrome.com/blog/300ms-tap-delay-gone-away))

### 2. Double-tap-zoom on buttons — use `touch-action: manipulation`.

Even though the 300ms delay is gone, double-tap-to-zoom can still mis-trigger on custom buttons built with `<div>` + `onclick`. The fix is twofold:

```html
<!-- always prefer semantic HTML -->
<button type="button">...</button>
```
```css
button, [role="button"] {
  touch-action: manipulation;
}
```

`manipulation` allows panning and pinch-zoom but disables double-tap-to-zoom for that element. ([MDN touch-action](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/Properties/touch-action))

### 3. Input font-size < 16px triggers auto-zoom.

iOS Safari zooms the viewport when a focused input has `font-size < 16px`. It does not zoom back out when you blur. Rule: **all `<input>` and `<textarea>` elements use `font-size: 16px` minimum.** Scale down with `transform: scale()` or reduce padding if you need visual density; never drop the font-size below 16.

### 4. Rubber-band scroll breaks `position: fixed`.

Overscroll bounce at the top/bottom of the document can briefly reposition fixed elements during the rubber-band animation. Fix with `overscroll-behavior: contain` on the scrolling container. PM's body already sets `overscroll-behavior-y: contain`.

For inner scroll containers (modals, carousels, bottom sheets):

```css
.bottom-sheet-content {
  overscroll-behavior: contain;
  overflow-y: auto;
}
```

### 5. Momentum scroll + virtualized lists.

Safari's momentum scroll runs on the compositor thread. JS-driven scroll position updates during momentum (e.g., virtualization libraries calling `element.scrollTop = X`) can misalign. The react-window workaround pattern: temporarily toggle `-webkit-overflow-scrolling: touch` off → update scroll → toggle back on after 50ms. ([react-window issue #122](https://github.com/bvaughn/react-window/issues/122))

When using `@tanstack/svelte-virtual` for PM's card grid, test on real iOS hardware. If you see jitter during fast-scroll-then-filter, this is probably why.

### 6. Haptics on iOS Safari — mostly not supported.

- `navigator.vibrate()` — **not supported on iOS Safari** as of iOS 18. It exists on Android (Chrome, Samsung Internet, Firefox). ([browser-compat-data issue #29166](https://github.com/mdn/browser-compat-data/issues/29166); [Progressier PWA capabilities — Vibration](https://progressier.com/pwa-capabilities/vibration-api))
- iOS 18+ introduced *non-standard* haptics on `<input type="checkbox" switch>` elements. Triggering `click()` on the associated label emits the system selection haptic. This is a toehold; abuse of it is fragile. ([ionic-framework issue #29942](https://github.com/ionic-team/ionic-framework/issues/29942); [tijnjh/ios-haptics shim library](https://github.com/tijnjh/ios-haptics))

**Rule:** feature-detect and degrade gracefully.

```js
function haptic(style = 'selection') {
  if ('vibrate' in navigator) {
    // Android path
    const pattern = { light: 5, medium: 10, heavy: 20, selection: 5 }[style];
    navigator.vibrate(pattern);
    return;
  }
  // iOS path — emit via the switch-input hack if already mounted, else no-op
  const shim = document.querySelector('[data-haptic-shim]');
  if (shim) {
    const label = shim.querySelector('label');
    label?.click();
  }
}
```

Never block a UX on haptics. They are confirmation, never the only signal.

### 7. Standalone mode detection.

```js
const isStandalone =
  window.navigator.standalone === true ||                // iOS legacy API
  window.matchMedia('(display-mode: standalone)').matches; // everyone else
```

Use for: hiding an "install to home screen" banner once installed; adjusting spacing when the status bar is no longer visible; enabling standalone-only features (badging API on iOS 16.4+).

### 8. iOS 17.4 + EU = no standalone PWA.

In the EU, under Digital Markets Act compliance, Apple removed standalone PWA support on iOS 17.4+. Home-screen icons open in Safari tabs. No Web Push. No badging. ([MagicBell — PWA iOS limitations guide](https://www.magicbell.com/blog/pwa-ios-limitations-safari-support-complete-guide))

**Rule:** assume PM users may be in browser tab mode on iOS. Don't gate critical flows behind standalone-only APIs without a fallback.

---

## Android quirks

### 1. Browser diversity.

Chrome, Samsung Internet, Firefox, and on some OEM skins a custom browser. All Chromium-based except Firefox. Test the big three. Samsung Internet has more share than Firefox on global Android — don't skip it.

### 2. Pull-to-refresh.

Default behavior on Android browsers: dragging down at the top of the document fires a refresh. Almost always unwanted for an app shell. Disable on the body:

```css
body { overscroll-behavior-y: contain; }
```

`contain` stops the refresh gesture and scroll chaining to the body from nested scroll containers. Keeps the visual overscroll glow. Use `none` to kill the glow too. ([Chrome developers — overscroll-behavior](https://developer.chrome.com/blog/overscroll-behavior))

### 3. Edge-to-edge on Chrome 135+.

Chrome 135 (early 2025) extended the viewport behind the gesture navigation bar on Android, similar to iOS's viewport-fit=cover. Our bottom nav already honors `env(safe-area-inset-bottom)` which handles this correctly. No code change needed — but *test* it, because a nav bar that sits under the gesture hint is a thumb-hostility disaster.

### 4. Virtual keyboard behavior is inconsistent.

Android has two modes: `adjustResize` (shrinks viewport, content stays above keyboard) and `adjustPan` (keyboard overlays, content doesn't move). Which one you get depends on the Chrome version, the WebAPK install path, and OEM skin. Consequence: you cannot rely on `100vh` or even `100dvh` updating correctly on all Android devices when the keyboard opens.

**Fix with VirtualKeyboard API when available:**

```js
if ('virtualKeyboard' in navigator) {
  navigator.virtualKeyboard.overlaysContent = true;
  navigator.virtualKeyboard.addEventListener('geometrychange', e => {
    const kbHeight = e.target.boundingRect.height;
    document.documentElement.style.setProperty(
      '--kb-height', `${kbHeight}px`
    );
  });
}
```

```css
.keyboard-anchored {
  bottom: var(--kb-height, 0px);
}
```

([MDN VirtualKeyboard API](https://developer.mozilla.org/en-US/docs/Web/API/VirtualKeyboard_API))

Feature detect, and for browsers that don't support it, fall back to `env(safe-area-inset-bottom)` and live with the imperfect behavior.

### 5. Samsung Internet POST share-target bug.

PWA manifests with `share_target.method: "POST"` install but silently fail on Samsung Internet. Google Play Protect also flags those WebAPKs as potentially unsafe, requiring manual override to install. Workaround: use `method: "GET"`. ([Modern Web Weekly #69](https://modernwebweekly.substack.com/p/modern-web-weekly-69))

PM doesn't currently implement share_target. Note for the future.

---

## Touch, Pointer, Click

### Use Pointer Events. Not Touch Events.

Pointer Events unify mouse, touch, and stylus under one API. Wide support since 2018. ([W3C Pointer Events spec](https://www.w3.org/TR/pointerevents/); [Complete Guide to Pointer Events, Rojas 2025](https://stories.carlosrojas.dev/2025/10/13/the-complete-guide-to-pointer-events/))

```js
el.addEventListener('pointerdown', e => { el.setPointerCapture(e.pointerId); });
el.addEventListener('pointermove', e => { /* no need to check active */ });
el.addEventListener('pointerup',   e => { el.releasePointerCapture(e.pointerId); });
```

`setPointerCapture` is the good-parts. The element keeps receiving `pointermove` even if the finger leaves the element — essential for drag gestures (swipe-to-cycle-variants is our canonical example, see [02_GESTURES.md](02_GESTURES.md)).

### Ghost clicks.

When a user scrolls then lifts their finger, a stale `click` event can fire on whatever element was under the finger at lift. Prevent by checking distance between pointerdown and pointerup:

```js
const MOVE_THRESHOLD = 8; // px
let startX = 0, startY = 0;
el.addEventListener('pointerdown', e => { startX = e.clientX; startY = e.clientY; });
el.addEventListener('pointerup', e => {
  const dx = Math.abs(e.clientX - startX);
  const dy = Math.abs(e.clientY - startY);
  if (dx > MOVE_THRESHOLD || dy > MOVE_THRESHOLD) {
    e.preventDefault(); // don't fire click
  }
});
```

### `touch-action` beats `preventDefault`.

Declaring `touch-action` in CSS lets the browser compositor decide which gestures to keep scroll-performant. Calling `preventDefault()` in a JS listener forces the compositor to wait for JS, causing scroll jank. Prefer CSS.

| Gesture intent | CSS |
|---|---|
| Default browser handling | `touch-action: auto` |
| Prevent double-tap-zoom on buttons | `touch-action: manipulation` |
| Horizontal-only custom gesture (swipe tile, allow vertical page scroll) | `touch-action: pan-y` |
| Vertical-only custom gesture (e.g. long-press drag) | `touch-action: pan-x` |
| Full control in JS (canvas, map, gesture-heavy zone) | `touch-action: none` |

**Rule:** for PM's swipe-to-cycle-variants, the tile gets `touch-action: pan-y`. That tells the browser: "the vertical direction is yours (page scroll); the horizontal direction is mine (swipe gesture)." No `preventDefault` needed; no jank.

---

## Install flows

### Android: `beforeinstallprompt`.

```js
let deferredPrompt = null;

window.addEventListener('beforeinstallprompt', e => {
  e.preventDefault();
  deferredPrompt = e;
  // don't show UI yet — wait for a natural moment
});

// later, on a user-meaningful moment (after save-deck, for example):
async function showInstallPrompt() {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  const { outcome } = await deferredPrompt.userChoice;
  deferredPrompt = null;
  return outcome === 'accepted';
}
```

([MDN BeforeInstallPromptEvent](https://developer.mozilla.org/en-US/docs/Web/API/Window/beforeinstallprompt_event); [web.dev PWA install prompt guide](https://web.dev/learn/pwa/installation-prompt))

### iOS: there is no prompt.

iOS Safari doesn't fire `beforeinstallprompt`. The only install path is Share sheet → "Add to Home Screen." We can show an instructional banner *only* when:

- The user is on iOS,
- Not already in standalone mode,
- Has visited ≥ 2 sessions (basic engagement threshold),
- Hasn't dismissed the banner in the past 30 days.

Static instructions — icon arrow to the Share button, then two steps: "Scroll down to Add to Home Screen" → "Tap Add."

### Install prompt timing rules

From [PWA Book ch. 4](https://pwa-book.awwwards.com/chapter-4) and field data:

- **Never on first load.** Acceptance rates are brutal.
- **Never mid-flow.** Don't interrupt deck-building to ask for install.
- **After a win moment.** User saved their first deck. Or hit their first watchlist target. Or opened PM for the third time.
- **Respect dismissal.** At least 30 days before re-asking. Save dismissal time to `localStorage`.

```js
const DISMISS_KEY = 'pm:install-dismissed-at';
const REPROMPT_MS = 30 * 24 * 60 * 60 * 1000;

function canShowInstallPrompt() {
  const dismissedAt = Number(localStorage.getItem(DISMISS_KEY) || 0);
  return Date.now() - dismissedAt > REPROMPT_MS;
}
```

---

## Thumb reach

We already laid down thumb zones in [00_PRINCIPLES.md §thumb zones](00_PRINCIPLES.md#thumb-zones). The Android / iOS realities:

- **Apple Reachability** (tap home indicator, screen shifts down): still exists on iPhone 15+ but most users don't know about it. Don't design around it being available.
- **Samsung One-Handed Mode**: opt-in, reduces the whole screen. Same caveat.
- **~10% of users are left-handed**, so anything that's unreachable with the right thumb is also unreachable for half of lefties holding the phone in the other hand.

**Rules:**
- Primary action: bottom-center. Survives both handedness cases.
- No action element in the top 20% of the viewport that matters to a reading flow.
- Gestures must work from the bottom half of a tile — don't force the user to reach to the top of a card to swipe it.

Source: [UX Matters — how users really hold mobile devices](https://www.uxmatters.com/mt/archives/2013/02/how-do-users-really-hold-mobile-devices.php) (still the most-cited study, and still accurate). [Smashing Magazine — design for one-hand usage](https://www.smashingmagazine.com/2020/02/design-mobile-apps-one-hand-usage/).

---

## Touch target sizing

- **Minimum 44×44pt (Apple HIG).** Enforced by the 44px floor on `BottomNav.svelte`.
- **Minimum 48×48dp (Material / Google).** Same target in CSS pixels.
- **Spacing ≥ 8px between adjacent interactive targets.** Prevents mis-taps.
- **Icon-only buttons: 48px padded box around a 24px icon.**

If a component doesn't meet these, it isn't shippable on mobile. The one exception is dense grids of non-critical affordances (variant thumbnails in the detail sheet — users tap the card, not the thumbnail) where the *tap target* is the whole row, even though the thumbnail is smaller.

---

## Pre-flight: the mobile readiness checklist

Before a PR lands any mobile-visible change, confirm:

- [ ] `viewport-fit=cover` present in `<head>`.
- [ ] `env(safe-area-inset-*)` applied to all fixed-position elements touching an edge.
- [ ] `overscroll-behavior-y: contain` on `body` (or the equivalent inherited).
- [ ] `touch-action` set on any element that handles custom gestures.
- [ ] Inputs are `font-size: 16px` or larger (prevent iOS zoom).
- [ ] Primary action is reachable with a thumb on a 6.7" phone.
- [ ] Tested in dark mode (PM is dark-first; do *not* ship a light-mode regression).
- [ ] Tested with mobile CPU throttling 4× slowdown and Slow 4G in DevTools.
- [ ] Tested on real iOS (at least Safari 17) — simulator doesn't cover the fixed-element bug.
- [ ] Tested on real Android (Samsung Internet, not just Chrome).
- [ ] Run Lighthouse mobile — PWA / Perf / A11y scores haven't regressed.

If you skip this list, you will ship a bug to one of these environments. Pick your lane.
