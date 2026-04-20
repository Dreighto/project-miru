# 05 — Accessibility

**Applies to:** every UI change. Accessibility is a default, not a polish pass.
**Read this when:** you're building a new component, touching focus management, working with forms, adjusting color contrast, or adding anything a screen reader might encounter.
**Skip this when:** never. At least skim before shipping any UI.
**Length:** ~8 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [04_PRIMITIVES.md](04_PRIMITIVES.md), [01_MOBILE_PWA.md](01_MOBILE_PWA.md).

---

## The target: WCAG 2.2 Level AA

Miru commits to **WCAG 2.2 AA**. Not AAA (too restrictive in places — e.g. AAA contrast 7:1 conflicts with the forge aesthetic). AA is the industry baseline and the compliance target for most enterprise customers.

Source: [W3C — WCAG 2.2 at a Glance](https://www.w3.org/WAI/standards-guidelines/wcag/glance/).

AA, not as a checklist to pass before ship, but as a property of the components we build. Every primitive in [04_PRIMITIVES.md](04_PRIMITIVES.md) is designed AA by default. Views composed from primitives should be AA by composition.

---

## The evidence for caring

This isn't a compliance-theater item. The data:

- **CDC (2022):** 1 in 4 US adults has a disability, of which cognitive (13.9%) and mobility (12.1%) are the most common; vision (4.8%) and hearing (5.9%) are meaningful. [CDC — Disability Impacts All of Us](https://www.cdc.gov/ncbddd/disabilityandhealth/infographic-disability-impacts-all.html).
- **WebAIM Million (2024):** 95.9% of home pages have at least one WCAG failure. The median page has 56 detected errors. [WebAIM — The WebAIM Million](https://webaim.org/projects/million/).
- **Dynamic Type usage on iOS:** ~35% of iPhone users have Dynamic Type turned on, some at sizes far above default. If your layout breaks at 200% type, you've lost a third of your iPhone users. [WWDC 2022 — Design with iOS pickers](https://developer.apple.com/videos/play/wwdc2022/10074/).
- **Temporary disability** is common too: a broken arm, eyes dilated from an exam, a crowded noisy environment. Accessibility features benefit the 100%, not the 26%.

We build for everyone because it's correct *and* because it's cheap to do upfront and expensive to retrofit.

---

## Color contrast

WCAG AA requires:

- **4.5:1** for text under 18pt (or 14pt bold).
- **3:1** for large text (18pt+ or 14pt bold).
- **3:1** for UI components and graphical objects (icons, chart elements, focus rings).

### Our palette, checked

Our dark canvas is `#08060f`. Tested against this background:

| Foreground | Hex / rgba | Contrast | Use |
|---|---|---|---|
| Default text | `rgba(255,255,255,0.96)` ≈ `#F5F5F5` | ~19:1 | Body, headings |
| Muted text | `rgba(255,255,255,0.64)` ≈ `#A3A3A3` | ~7.5:1 | Captions, secondary |
| Dim text | `rgba(255,255,255,0.48)` ≈ `#7A7A7A` | ~4.5:1 | Minimum viable; use sparingly |
| Miru gold | `#f4d078` | ~11:1 | Active, watched, success |
| Miru purple | `#c9b0ff` | ~9.2:1 | Insight, suggestion |
| Leader red | `#ff6b6b` | ~6.8:1 | Red leaders, destructive |
| Leader blue | `#6fa7ff` | ~7.1:1 | Blue leaders |
| Leader green | `#76d794` | ~8.3:1 | Green leaders |

All text passes AA. Do not introduce text colors below 4.5:1 on the canvas.

Tested with [WebAIM Contrast Checker](https://webaim.org/resources/contrastchecker/). If you add a new color, run it through that tool and add to this table.

### What to do about the `0.48` dim text

It meets 4.5:1 but barely. Use only for truly tertiary captions. Do not put dim text on a non-canvas background (e.g. on a card surface that's lighter than the canvas) without re-checking.

### Color is never the only signal

WCAG SC 1.4.1 ([Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html)) requires any information conveyed by color to also be conveyed by something else — text, icon, pattern, position.

**Rule:** if you set a red border on an error input, also include a `!` icon or error text. If you tint a leader card red, also show the leader color name in a label.

---

## Tap targets

**44×44 CSS pixels minimum** for anything tappable on mobile. ([Apple HIG — Layout](https://developer.apple.com/design/human-interface-guidelines/layout)) Android uses 48dp, which is ≈48 CSS pixels. Use 44 as the floor, 48 as the comfort target, 52 as the sticky-footer primary.

WCAG 2.2 SC 2.5.8 ([Target Size — Minimum](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)) requires 24×24 minimum. We exceed that — our floor is 44 because we're mobile-first.

### Spacing between tap targets

Adjacent tap targets need **at least 8px of space** between them. Without spacing, users with tremor or dexterity issues (and all users on a jostled commute) hit the wrong target. [Material 3 — Touch targets](https://m3.material.io/foundations/designing/structure#5e0d2b13-d4d4-4f55-9e21-83a9e80c8fa2).

If a target is smaller than 44×44 visually (e.g. a 20×20 icon button), give it 44×44 of hit area via padding. The visible icon is small; the invisible click zone is large.

---

## Focus

### Focus ring

Every interactive element shows a **visible focus ring** when focused via keyboard. Our ring: 2px solid `rgba(184,160,255,0.96)` (Miru purple), 2px offset, 4px radius.

**Do not remove focus rings.** `outline: none` without replacement is a WCAG failure. If the default browser ring conflicts with the design, *replace* it with a custom ring — don't delete it.

Use `:focus-visible` (not `:focus`) so the ring shows only on keyboard navigation, not on mouse click. Source: [MDN — :focus-visible](https://developer.mozilla.org/en-US/docs/Web/CSS/:focus-visible), [web.dev — :focus-visible](https://web.dev/articles/focus-visible).

### Focus order

Tab order must follow visual order. If a modal opens, focus moves into it. If a modal closes, focus returns to the opener.

For complex pages (grids, canvases), set explicit `tabindex` only when the default DOM order doesn't match reading order. Avoid `tabindex` > 0 (creates unnavigable chaos). Use `tabindex="0"` to make a non-interactive element focusable, `tabindex="-1"` to make it programmatically focusable but not in tab order.

Source: [WCAG SC 2.4.3 Focus Order](https://www.w3.org/WAI/WCAG22/Understanding/focus-order.html).

### Focus trap in modals / sheets

When a modal or sheet is open, Tab / Shift+Tab must cycle within it, not escape to the page underneath.

Implementation: when the surface opens, find all focusable descendants (`a, button, input, select, textarea, [tabindex]:not([tabindex="-1"])`), capture Tab and Shift+Tab to loop. On Escape, close. On close, restore focus to the element that opened the surface.

Use the native `<dialog>` with `showModal()` — it does this for free. See [03_SUB_PAGE_ARCHITECTURE.md](03_SUB_PAGE_ARCHITECTURE.md#-element).

### Skip links

At the top of every page, an invisible-until-focused "Skip to main content" link. First Tab focus reveals it. Saves keyboard users from tabbing through the nav bar on every page.

```html
<a href="#main" class="skip-link">Skip to main content</a>
```

Source: [WebAIM — Skip Navigation](https://webaim.org/techniques/skipnav/).

---

## Screen readers

The four major screen readers:

- **VoiceOver** (iOS, macOS) — ~70% of iOS users who use a screen reader.
- **TalkBack** (Android) — Android default.
- **NVDA** (Windows) — open-source, ~65% of Windows screen reader users per [WebAIM 2024 Survey](https://webaim.org/projects/screenreadersurvey10/).
- **JAWS** (Windows) — commercial, ~60% of Windows screen reader users (overlap).

Test on at least VoiceOver (macOS + iOS) and NVDA. These two cover ~90% of the user base and catch ~95% of issues.

### The test routine

1. Open Settings > Accessibility > VoiceOver on iOS, or VoiceOver on macOS Safari (VO+F5).
2. Swipe right to read each element.
3. Double-tap to activate.
4. Rotor (VO+U): can you navigate by Heading, Landmark, Link, Button?
5. Form mode: on an input, does label + current value get announced?

If any step fails, that's a bug — fix before shipping.

### Common screen-reader bugs and fixes

| Bug | Fix |
|---|---|
| Icon button reads "Button" with no meaning | Add `aria-label="Close"` or similar |
| Toast announcement doesn't fire | Wrap in `<div role="status" aria-live="polite">` for affirmative, `role="alert"` for errors |
| Custom checkbox reads as nothing | Use native `<input type="checkbox">` or add `role="checkbox"` + `aria-checked` + keyboard handling |
| Modal close button reachable via Tab but not focused on open | Move focus to close button (or first interactive element) when modal opens |
| Tab key escapes modal | Implement focus trap (see above) |
| Dynamic content change (e.g. filter applied) goes unannounced | Add `aria-live="polite"` to the container of the result count |
| Images missing alt | `alt=""` for decorative, `alt="Luffy, red leader, 5000 power"` for meaningful |
| Decorative icons double-read | Add `aria-hidden="true"` on icons when an adjacent text label exists |

### `aria-live` usage

- `aria-live="polite"` — announce when user is idle (status messages, filter counts).
- `aria-live="assertive"` — interrupt immediately (errors, critical warnings).
- `role="status"` — implies `aria-live="polite"`.
- `role="alert"` — implies `aria-live="assertive"`.

Use polite 95% of the time. Assertive is for "the form failed to submit" and "your session expired." Overusing assertive creates a hostile reading experience. Source: [W3C — Live Regions](https://www.w3.org/TR/wai-aria-1.2/#live_region_roles).

### Landmarks

Every page has:

- `<header role="banner">` — the top header bar.
- `<nav>` — the BottomNav and any header nav.
- `<main id="main">` — the main content. One per page.
- `<footer role="contentinfo">` — if present.

Screen reader users navigate by landmark. Missing `<main>` = they have no way to skip the header on every page.

---

## Forms

### Every input has a label

Visible. Associated via `for`/`id` or wrapping. No placeholder-as-label.

### Errors are described

```html
<label for="target-price">Target price</label>
<input id="target-price" aria-describedby="target-price-help target-price-error" aria-invalid="true">
<span id="target-price-help">USD. Miru will notify when the market hits this.</span>
<span id="target-price-error" role="alert">Target price must be positive.</span>
```

`aria-invalid="true"` tells assistive tech the field failed validation. The error message, referenced via `aria-describedby`, reads after the label.

### Autocomplete

See [04_PRIMITIVES.md §Input](04_PRIMITIVES.md#input). Use the [semantic autocomplete tokens](https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/autocomplete) (`email`, `username`, `new-password`, `one-time-code`, `street-address`, etc.). Password managers and autofill rely on these.

### Keyboard submission

Enter in a single input submits the form. Enter in a multi-line textarea inserts a newline (not submit). Cmd/Ctrl+Enter in a textarea submits.

---

## Keyboard navigation

Every interactive element must be keyboard-reachable. Every action must be keyboard-performable.

### The keyboard-only test

Unplug your mouse. Try to:

1. Navigate to each tab via Tab / Shift+Tab.
2. Activate each tab via Enter or Space.
3. Open a card detail (Enter on a card tile).
4. Close it (Escape).
5. Fill a form and submit.
6. Reveal a swipe action on a list row (keyboard alternative: context menu key or long-Enter with modifier; or shift+F10; or right-click on trackpad).

If any step doesn't work, that's a bug. The most common failure: "we shipped a swipe gesture, forgot to add a keyboard path."

### Keyboard shortcuts

Discoverable, documented, non-conflicting. Use `?` to open a shortcut cheatsheet (Stripe, Linear, GitHub all do this). Do not override browser shortcuts (`Cmd+K` is debatable — it's a de facto command-palette shortcut).

---

## Dynamic Type / text scaling

iOS users can set Dynamic Type up to ~130% default (accessibility sizes up to ~200%). Android has a similar font size setting.

### Use `rem` / `em` and relative units

Set `font-size` in `rem` (relative to root `<html>` font size, which respects user preferences). Set line-height in unitless numbers (scales with font size).

Do **not** use `px` for anything text-adjacent. `px` ignores user preferences and the entire interface stops scaling.

### Test at 200%

Chrome DevTools > Rendering > Emulate CSS > `font-size: 32px` on `<html>` (double default). Your layout should still work:

- No horizontal scrolling except within tables/preformatted content ([WCAG SC 1.4.10 Reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)).
- No text clipped.
- Buttons grow with the text; they don't stay fixed and let text overflow.
- List rows grow taller when they have multi-line text.

The common sin: hard-coded heights. A button with `height: 44px` and `font-size: 14px` looks fine at default; at 200% the text is 28px and overflows the button. Fix: `min-height: 44px` and let text push the height up.

### Honor `prefers-reduced-motion`

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

Add this once to the root stylesheet. Every animation and transition automatically snaps. Source: [web.dev — prefers-reduced-motion](https://web.dev/articles/prefers-reduced-motion).

### Honor `prefers-contrast: more`

Increase contrast of borders, focus rings, and muted text when this media query matches.

```css
@media (prefers-contrast: more) {
  :root {
    --color-border: rgba(255,255,255,0.32);
    --color-muted: rgba(255,255,255,0.80);
  }
}
```

---

## The "inputs stuck behind keyboard" bug

On iOS, when an input is focused, the virtual keyboard covers part of the screen. If the input is in the bottom half, the user can't see what they're typing.

**Fix:** on focus, scroll the input into view:

```javascript
inputElement.addEventListener('focus', () => {
  setTimeout(() => {
    inputElement.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, 300); // wait for keyboard animation
});
```

Better: use the [VisualViewport API](https://developer.mozilla.org/en-US/docs/Web/API/Visual_Viewport_API) to detect keyboard height and adjust layout. See [01_MOBILE_PWA.md](01_MOBILE_PWA.md#virtual-keyboards).

---

## Language

Every page declares its language: `<html lang="en">`. If a specific section is in another language, use `lang="ja"` (for One Piece card names in Japanese, as they sometimes appear). Screen readers switch pronunciation rules based on `lang`.

---

## Motion and vestibular concerns

Some users get nausea from parallax, zoom, and auto-playing video. ([WCAG SC 2.3.3 Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html))

- No parallax. Period.
- No auto-playing video. User taps to play.
- Pinch-zoom on card images: fine, it's intentional.
- Slide transitions between pages: fine, they're brief and predictable.
- Anything that *rotates* or *spins* on a loop: disable under `prefers-reduced-motion`.

---

## Semantic HTML first

The biggest a11y win is using the right element. `<button>` not `<div onClick>`. `<a>` not `<span onClick>`. `<h1>`–`<h6>` for headings, in order. `<ul>` / `<ol>` for lists. `<label>` for inputs.

Semantic HTML gives you:
- Keyboard handling (`<button>` handles Space and Enter; `<a>` handles Enter).
- Screen reader announcements ("button," "link," "heading level 2").
- Focus management.
- Right-click context menus.
- OS features (iOS `3D Touch` on links, right-click "Inspect," etc.).

Every time you reach for `<div>` with custom behavior, ask: is there a native element that does this? Usually yes.

Source: [MDN — HTML Elements reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Element), [Adrian Roselli's blog](https://adrianroselli.com/) (deep archive of native-vs-custom debates).

---

## The accessibility check at PR time

Before merging any UI PR, run:

1. **Axe DevTools** ([Chrome extension](https://chrome.google.com/webstore/detail/axe-devtools-web-accessib/lhdoppojpmngadmnindnejefpokejbdd)) — one-click scan. Should report zero violations.
2. **Lighthouse accessibility audit** — 100 score is aspirational, 95+ is the floor.
3. **Keyboard-only walk** — unplug the mouse.
4. **VoiceOver walk on a device** — actual device, not simulator.
5. **Scaled-text walk** — browser at 200% zoom.

If you can't do all five, don't ship — queue the review.

### Automated tools find ~30% of issues

[Deque Systems](https://www.deque.com/) and WebAIM both report that automated tools catch around 30% of WCAG issues. The other 70% require human judgment — "does this label make sense when read aloud?"

Don't trust a green Axe score as proof of accessibility. Trust the walk-throughs.

---

## Accessibility is a craft, not a compliance bar

The difference between an app that "passes accessibility" and one that's *good* with accessibility tools is the difference between checked-box and considered. Good defaults:

- Hit targets generous, not minimum.
- Focus states distinct and delightful, not just visible.
- Copy honest and specific ("3 unread" not "notifications available").
- Motion optional, haptics optional, sound optional — user decides.
- Errors recoverable, not dead ends.

The test: would you prefer using this app with assistive technology over the competition? If yes, ship. If no, there's work to do. Source: reading 1-star reviews of TCG apps that specifically call out inaccessibility — [justuseapp.com/en/app/1603892248/collectr](https://justuseapp.com/en/app/1603892248/collectr) collects many of these.
