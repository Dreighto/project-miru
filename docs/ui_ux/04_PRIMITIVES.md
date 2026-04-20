# 04 — Primitives

**Applies to:** every reusable UI component in Miru surfaces. Buttons, inputs, chips, sheets, meters, steppers, list rows, card tiles.
**Read this when:** you're about to build a new component and want to check if one already exists; you're reviewing a PR that introduces a new one-off; you're standardizing a rough draft component into the library.
**Skip this when:** you're composing from existing primitives without modifying them.
**Length:** ~10 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [05_ACCESSIBILITY.md](05_ACCESSIBILITY.md), [docs/pm/02_PM_PRIMITIVES.md](../pm/02_PM_PRIMITIVES.md).

---

## The contract

A primitive is a component that:

1. Has **no business logic** — pure presentation + pure interaction.
2. Is **composable** — you pass children / slots, not behavior.
3. Has **one job** — a button is a button; a button-with-icon-that-also-handles-loading is three primitives composed.
4. Has **states spec'd upfront** — default, hover, pressed, disabled, loading, error.
5. Has **a11y defaults built in** — focus ring, role, aria-* where applicable.
6. Has **a Histoire / Storybook story** for every state.

If it doesn't meet all six, it's not a primitive — it's a view. Views compose primitives. Primitives are the vocabulary.

---

## The index

This library's primitives, in rough order of how often you'll reach for them. PM-specific variants (card tile, hex gauge, Miru gem, leader chip) live in [docs/pm/02_PM_PRIMITIVES.md](../pm/02_PM_PRIMITIVES.md) — the universal versions here are the skeletons.

| Primitive | Purpose |
|---|---|
| Button | Primary/secondary/ghost/destructive tap target |
| Icon button | Tap target that is only an icon (44×44 min) |
| Input | Text entry (single-line, multi-line) |
| Chip | Toggleable filter / small indicator |
| Stepper | Discrete numeric +/− control |
| Sheet | Bottom sheet (see [03_SUB_PAGE_ARCHITECTURE.md](03_SUB_PAGE_ARCHITECTURE.md)) |
| Modal | Focus-trapping overlay (see same) |
| Sticky footer | Bottom-anchored action bar |
| Toast | Ephemeral status strip |
| Meter | Progress bar with semantic color |
| List row | Generic swipeable list row |
| Card (surface) | Bounded content container |
| Tabs | Inline tab control |
| Avatar | User / leader image container |
| Segmented control | Compact 2–4 option picker |
| Skeleton | Loading placeholder shape |

---

## Button

The workhorse. If you're building *any* tappable thing that isn't an icon-only control, it's a Button.

### Variants

| Variant | Visual | Use |
|---|---|---|
| `primary` | Gold fill (`--color-miru-gold`), dark text | The *one* main action per screen. Bottom-center sticky footer. |
| `secondary` | Transparent, gold-border, gold text | Secondary actions. "Share," "Cancel." |
| `ghost` | No background, gold text | Tertiary, in-sheet links, footers |
| `destructive` | Red text (`#ff6d6d`), no fill | Delete, remove, unwatch. Always with confirmation. |
| `miru` | Purple fill (`--color-miru-accent`), dark text | Miru-triggered actions. "Explain," "Suggest." |

**One primary per screen.** If you have two primaries, you don't — one is secondary. This is a discipline problem, not a styling problem.

### Sizes

| Size | Height | Padding | Font |
|---|---|---|---|
| `sm` | 36px | 12px | 14px |
| `md` | 44px | 16px | 15px |
| `lg` | 52px | 20px | 16px |

`md` is default. `lg` is for sticky-footer primaries where it must feel thumb-worthy. `sm` is for inline controls.

**44px is the minimum for tap targets.** See [05_ACCESSIBILITY.md](05_ACCESSIBILITY.md) and [Apple HIG — Layout](https://developer.apple.com/design/human-interface-guidelines/layout).

### States

- **Default:** base style.
- **Hover (desktop only):** slight lift (brightness +4%). No transform — the hover is a readability cue, not a dance.
- **Pressed:** scale to 0.98, opacity 0.9, `transition: transform 80ms`. Haptic `impact.light`.
- **Focus (keyboard):** 2px ring, 2px offset, color `--color-miru-accent` (purple).
- **Disabled:** opacity 0.4, no pointer events, cursor not-allowed on desktop.
- **Loading:** shows an inline spinner at 14px, replacing the label (or next to it if label is critical for context). Button is disabled during loading.

### Loading state

Loading replaces the label with a spinner. **Do not** leave the label visible and float a spinner over it — users can't read through the spinner and the layout shifts. Keep the button's width fixed (use `min-width: <measured>` or a `width: 100%`).

Duration to show the spinner: show after **150ms** of pending. If the action completes faster, don't show the spinner at all. This avoids the "spinner flash" that happens on fast networks. Use `setTimeout(show, 150)` and clear on resolve. Source: [Nielsen Norman Group — Response Times](https://www.nngroup.com/articles/response-times-3-important-limits/).

### Icon + label buttons

Icon on the left, 8px gap, label. Icon size matches label size (14px label → 14px icon, 16px label → 16px icon). Never both icons (gets visually noisy) and never icon-only with a primary variant (use Icon Button instead).

### Destructive confirmation

Destructive buttons never execute on first tap. They either:

1. Open a confirm alert ("Delete this deck? [Cancel] [Delete]"), or
2. Trigger an action that is undoable via toast ("Deck removed. Undo.").

Never both. Pick the right one for the blast radius.

---

## Icon Button

A Button that is only an icon. It has a 44×44 minimum hit area even if the icon is 20×20 (extra space is transparent padding). It has an `aria-label` for screen readers.

**Rule:** every Icon Button has an `aria-label`. No exceptions. If the icon's meaning isn't obvious (e.g. an abstract Miru glyph), also include a tooltip on desktop.

**Icon library:** [lucide-svelte](https://lucide.dev/). Consistent stroke width, Svelte-native, tree-shakeable. See [09_TOOLING.md](09_TOOLING.md). Don't mix icon libraries.

---

## Input

Text entry. Supports single-line (`<input>`) and multi-line (`<textarea>`) via a prop.

### Visual

- 44px min height (`md`), 52px for `lg`.
- 12px horizontal padding, 10px vertical.
- Rounded 10px (matches the medium radius token).
- Border: 1px solid rgba(255,255,255,0.08) default, rgba(244,208,120,0.64) focus.
- Background: rgba(255,255,255,0.03).
- Placeholder: `color-mix(in oklch, var(--color-miru-text) 40%, transparent)`.

### States

- **Default / focus / error / disabled** — same pattern as Button.
- **Error:** border `#ff6d6d`, helper text `#ff6d6d`, `aria-invalid="true"`.
- **Helper text:** 12px, below input, 4px gap. Used for hints and errors.

### iOS-specific rules

- **Font size ≥ 16px** on mobile. Otherwise iOS Safari auto-zooms on focus. See [01_MOBILE_PWA.md](01_MOBILE_PWA.md).
- **`autocomplete` attribute set.** Never `autocomplete="off"` on login-adjacent inputs (breaks password managers) unless there's a specific reason. Use the [semantic autocomplete tokens](https://developer.mozilla.org/en-US/docs/Web/HTML/Attributes/autocomplete) like `email`, `new-password`, `one-time-code`.
- **`inputmode` where appropriate.** For numeric input, `inputmode="numeric"` shows a number pad without requiring `type="number"` (which has its own quirks). For price, `inputmode="decimal"`. For email, `inputmode="email"`. Source: [MDN — inputmode](https://developer.mozilla.org/en-US/docs/Web/HTML/Global_attributes/inputmode).

### Label

Always a `<label>`. Visually above the input, left-aligned. Never use placeholder as label — placeholder disappears on focus, breaking short-term memory users and failing WCAG 3.3.2 ([W3C — Labels or Instructions](https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions.html)).

### Clear button

On single-line text inputs with content, show a small "×" icon inside the right edge to clear. 28×28 tap target, fades in when the value is non-empty. Source: iOS / Android both do this.

---

## Chip

Small toggleable filter or indicator. Think "filter chips at the top of a list" or "tags on a card."

### Visual

- Pill shape (fully rounded).
- 28px height (small), 32px (medium), 36px (large).
- 12–16px horizontal padding.
- Default: `bg: rgba(255,255,255,0.04); color: muted; border: 1px rgba(255,255,255,0.08)`.
- Active: `bg: rgba(244,208,120,0.16); color: gold; border: 1px rgba(244,208,120,0.48)` for "yours" state; purple equivalent for Miru state.
- Leader-color chip: border and dot in leader color, background `rgba(255,255,255,0.04)`. Leader colors are semantic — see [00_PRINCIPLES.md §7](00_PRINCIPLES.md).

### Tap behavior

Chips toggle on tap. Haptic `selection` on state change. No pressed-state "dent" — the color change is the feedback.

### Chip groups

Chips in a filter group are usually multi-select. Label the group above with a small caption ("Filter by color") and render the active count inline ("Color · 2"). Source: [Stripe's filter chip pattern](https://stripe.com/docs/api) — the pattern is industry-standard.

### Anti-patterns

- **Don't use chips as tabs.** Tabs have a single-select; chips are often multi-select. Mixing breaks the mental model.
- **Don't let chips wrap across 3+ rows.** If you have that many filters, use a sheet or a collapse control.
- **Don't nest chips.** A chip inside a chip is ugly and ambiguous.

---

## Stepper

Numeric +/− control. Used for quantity (card count in deck), target price, etc.

### Visual

Three parts in a row: `−` button, center readout, `+` button. Each button 36×36 (or 44×44 for primary-surface steppers). The center shows the current value, optionally with a unit ("4×", "$25.00").

### Interaction

- Tap `−` or `+`: decrement/increment, haptic `selection`.
- Tap-and-hold: repeat every 80ms after a 500ms hold. Haptic at each tick (light).
- Long-press on center readout: opens an input sheet to set value directly.
- Wraparound: no (clamp at min/max).
- Disabled state: when at min, `−` is disabled (opacity 0.3, no haptic). Same for max and `+`.

### Keyboard

- Arrow up / down: increment / decrement.
- Tab moves between `−`, center (if editable), `+`.
- Page up / down: step by 5 (or configured stride).

### Accessibility

- Role: `spinbutton` with `aria-valuenow`, `aria-valuemin`, `aria-valuemax`.
- Label the stepper (`aria-labelledby`) pointing at the context — e.g. "Copies of Monkey D. Luffy."
- Announce value changes via `aria-valuenow` update (screen readers read it).

Source: [W3C APG — Spinbutton](https://www.w3.org/WAI/ARIA/apg/patterns/spinbutton/).

---

## Sheet / Modal / Toast

See [03_SUB_PAGE_ARCHITECTURE.md](03_SUB_PAGE_ARCHITECTURE.md) for shape and behavior. The primitive versions expose:

- `<Sheet bind:open height="half|full|auto" dismissable={true}>`
- `<Modal bind:open {destructive}>`
- `<Toast message="..." action={{ label, onClick }} duration={4000}>`

All three return focus to the opener on close. All three are lazily rendered (not in the DOM when closed).

---

## Sticky footer

A bottom-anchored container for the primary action on sub-pages and in sheets.

### Rules

- **One primary action, optional secondary.** Primary on the right (thumb side — users are 90% right-handed per [Pew Research](https://www.pewresearch.org/science/2013/02/05/just-how-left-handed-are-you/)). If two equal-weight actions, put destructive on left.
- **`padding-bottom: max(env(safe-area-inset-bottom), 12px)`** so it clears the home indicator.
- **Elevated visually** — 1px top border, semi-transparent gold glow, or a soft blur behind (if backdrop-filter is available). The blur + border pattern is what makes iOS-style action bars feel grounded without being heavy.
- **Hidden on scroll down, reappears on scroll up.** 240ms slide. Prevents the footer from eating valuable thumb space when the user is clearly reading.
- **Matches BottomNav height discipline** — don't stack footer + BottomNav on top of each other. On sub-pages, BottomNav is hidden (see [03_SUB_PAGE_ARCHITECTURE.md](03_SUB_PAGE_ARCHITECTURE.md#page-structure)).

### Anti-patterns

- More than two buttons in a sticky footer. Third action goes in an overflow menu or elsewhere in the page.
- Sticky footer that scrolls with content. That's a regular footer.
- Sticky footer that covers a portion of the last list row. Give the scroll region enough `padding-bottom` to account for footer height + 12px.

---

## Meter

A horizontal bar that shows a value relative to a range. Used for: deck completion %, price distance from target, etc.

### Visual

- Background track: 6px tall, `rgba(255,255,255,0.06)`, fully rounded.
- Fill: semantic color — gold for "yours" progress, purple for Miru-suggested, green for "healthy" price, red for "over," white for neutral.
- Animates on value change: 300ms ease-out.
- Labels optional: small 12px label on the left, value on the right.

### Semantic meters

The meter's *color* must carry meaning consistent with the color system:

- **Gold:** user-owned progress (deck completion, watchlist coverage).
- **Purple:** Miru-generated signal (confidence, relevance).
- **Green:** target met (price at or under target).
- **Red:** over limit, over budget, violation.
- **White / muted:** neutral meta (progress of an unrelated metric).

**Don't use meter color decoratively.** If you want a decorative colored bar, use a progress primitive without the semantic guarantees. Meters carry meaning.

### Accessibility

- Role: `progressbar` with `aria-valuenow`, `aria-valuemin`, `aria-valuemax`, `aria-label`.
- Critical values (target hit, over limit) announce via a polite `aria-live` region in the adjacent label.

Source: [W3C APG — Meter pattern](https://www.w3.org/WAI/ARIA/apg/patterns/meter/).

---

## List row

A generic row for lists: avatar/icon on left, title + subtitle in middle, right-side meta (time, value, chevron).

### Structure

```
┌───────────────────────────────────────────┐
│ [icon]  Title                     meta  › │
│         Subtitle · secondary              │
└───────────────────────────────────────────┘
```

- Min height: 60px (two lines) or 44px (one line). Must meet tap target even if only 44px.
- Horizontal padding: 16px.
- Vertical rhythm: 8px between title and subtitle.
- Divider: 1px bottom border, `rgba(255,255,255,0.04)`, except on last row.

### Swipe-to-reveal

See [02_GESTURES.md](02_GESTURES.md#swipe-to-reveal-vs-swipe-to-commit) for physics. Left-side reveal: affirmative action (pin, watch). Right-side reveal: destructive (unwatch, remove).

Revealed actions are 72px wide each, max 2 per side. More than 2 = sheet.

### States

- **Default:** base.
- **Pressed:** 0.96 scale-down + opacity 0.85, 80ms.
- **Selected (multi-select mode):** left-side checkmark appears; background shifts to `rgba(244,208,120,0.08)`.
- **Swipe-revealed:** row offsets left/right, action buttons slide in.

---

## Card (surface)

Generic bounded content container. **Not** the PM card-tile (which represents a TCG card — see [docs/pm/02_PM_PRIMITIVES.md](../pm/02_PM_PRIMITIVES.md)). This is a content surface.

### Visual

- `bg: rgba(255,255,255,0.03)`.
- Border: 1px solid `rgba(255,255,255,0.06)`.
- Radius: 14px.
- Padding: 16px default.
- Hover (desktop): border `rgba(255,255,255,0.12)`.

### Subtypes

- **Standalone card:** in a list of peers.
- **Embedded card:** inside another surface, usually borderless.
- **Interactive card:** whole card is a tap target. Pressed state scales to 0.99.

---

## Tabs (inline)

A segmented tab control that switches between views within a page/sheet. **Not** the BottomNav, which is navigation between tabs — see [03_SUB_PAGE_ARCHITECTURE.md](03_SUB_PAGE_ARCHITECTURE.md#tabs-versus-sub-pages).

### Visual

- Horizontal row of 2–5 tabs.
- Active: gold text, 2px underline (or pill background, pick one and stay consistent within a surface).
- Inactive: muted text.
- Swipe between tabs is **optional** and must not fight vertical scroll — usually not worth it. Tap-only is the safer default.

### Accessibility

- `role="tablist"` on container, `role="tab"` on each, `aria-selected`, `aria-controls` pointing at the panel.
- Panel has `role="tabpanel"`.
- Keyboard: left/right arrow moves selection. Home/End go to first/last.

Source: [W3C APG — Tabs pattern](https://www.w3.org/WAI/ARIA/apg/patterns/tabs/).

---

## Avatar

Round image container with optional fallback letter.

- Sizes: 24/32/40/56px.
- Fallback: single letter of name, gold-tinted background.
- Status dot (optional): colored dot at bottom-right, 25% diameter.

---

## Segmented control

Compact 2–4 option picker. iOS-style: rounded pill container, active segment has a lifted inner pill.

- Used when the user picks one of N and N is small.
- **Use a segmented control over tabs when** the choices are values (sort order, format), not sections of content.
- **Use tabs over a segmented control when** each choice opens a different view/panel.

---

## Skeleton

Loading placeholder shape.

### Visual

- Rounded rectangle (matches target component's radius).
- Shimmer animation: left-to-right gradient sweep, 1.4s loop, `ease-in-out`.
- Color: `rgba(255,255,255,0.04)` base, `rgba(255,255,255,0.08)` shimmer peak.

### Rules

- **Show skeleton when loading > 150ms.** Faster than that, just render when ready — skeleton flash is worse than instant render. Use a debounce (`setTimeout(showSkeleton, 150)`).
- **Shape matches reality.** If a card will render as 80px tall, the skeleton is 80px. Layout shift on render is the sin ([CLS metric](https://web.dev/articles/cls)).
- **Don't nest skeletons.** One skeleton per major content block. Five skeletons in a row for five list rows is fine; a skeleton for the list row AND a skeleton for its avatar AND a skeleton for its title is noise.
- **`prefers-reduced-motion: reduce`** disables the shimmer — show a static muted block.

---

## State vocabulary

Every primitive honors this vocabulary consistently. If a new component introduces a state name not on this list, pause — is it really a new state, or is it a combination of existing ones?

| State | Applies to | Visual signal |
|---|---|---|
| default | All | Base style |
| hover | Tappable, desktop only | Brightness +4% |
| pressed | Tappable | Scale 0.98, opacity 0.9 |
| focus | Tappable, keyboard-navigable | 2px purple ring, 2px offset |
| disabled | Interactive | Opacity 0.4 |
| loading | Interactive (buttons, inputs) | Spinner replaces label |
| error | Inputs, form controls | Red border, red helper text |
| selected | Toggleable (chips, list rows in multi-select) | Gold fill / border |
| active | Meter-bearing (meters, progress) | Semantic color fill |

---

## The "should this be a primitive?" test

Before adding a new primitive:

1. **Does it appear in more than one place, or likely will?** If one-off, compose inline.
2. **Does it have >3 states or >2 variants?** If not, a CSS class may be enough.
3. **Does it need a11y props beyond generic ones?** That's a sign it needs a component wrapper.
4. **Does it compose with other primitives, or does it wrap them?** If it wraps, build it *from* primitives — don't bake a Button into a custom component, pass a Button in as a slot.

If you answer yes to 1 + (2 or 3), it's a primitive. Add it to this library. Write a Histoire story. Don't forget the dark / reduced-motion / RTL cases.

---

## Primitives we explicitly do *not* build

Sometimes the right call is to use a native element without wrapping it.

- **Checkbox / radio:** `<input type="checkbox">` with CSS. No component. Native keyboard/a11y is already correct.
- **Select:** native `<select>` on mobile; the native iOS/Android picker is far better than any we could build. Wrap only when you need custom multi-select or search within options.
- **Date picker:** native `<input type="date">` first. Only a custom picker for ranges and unusual formats.
- **File picker:** native `<input type="file">`. Style the button, don't reinvent the input.

Reinventing natives loses: iOS AutoFill, Android scribble, accessibility tree, security prompts, keyboard layouts. Unless you have a specific reason — and you can name it — don't.

Source: [web.dev — Forms best practices](https://web.dev/learn/forms), [Adrian Roselli — Under-engineered form controls](https://adrianroselli.com/2019/09/under-engineered-custom-radio-buttons-and-checkboxen.html).

---

## Library location

Primitives live at:

```
pm/storefront/src/lib/components/
  Button.svelte
  IconButton.svelte
  Input.svelte
  Chip.svelte
  Stepper.svelte
  Sheet.svelte
  Modal.svelte
  Toast.svelte
  StickyFooter.svelte
  Meter.svelte
  ListRow.svelte
  Card.svelte
  Tabs.svelte
  Avatar.svelte
  SegmentedControl.svelte
  Skeleton.svelte
```

PM-domain components (CardTile, HexGauge, WatchlistStar, LeaderChip, MiruGem) live alongside but are domain primitives — see [docs/pm/02_PM_PRIMITIVES.md](../pm/02_PM_PRIMITIVES.md).

If a primitive outgrows a single file (>300 lines), split into `Button/` with `Button.svelte`, `Button.stories.svelte`, maybe sub-parts. Don't split prematurely — most primitives fit in one file.
