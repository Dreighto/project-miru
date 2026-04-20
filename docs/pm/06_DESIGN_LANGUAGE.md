# 06 — Design Language

**Applies to:** every visual decision in PM — color, type, spacing, shadow, radius, motion, iconography.
**Read this when:** you're picking a color, specifying a type size, deciding a radius, tweaking a shadow, or adding a new visual pattern.
**Skip this when:** you're implementing against existing tokens without modifying them.
**Length:** ~8 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [02_PM_PRIMITIVES.md](02_PM_PRIMITIVES.md), [docs/ui_ux/05_ACCESSIBILITY.md](../ui_ux/05_ACCESSIBILITY.md).

---

## The Forge aesthetic

PM's visual language is "the forge at night": dark canvas, warm gold as the primary accent, cool purple as the Miru accent, leader colors as semantic functional color. The overall feel is precise, warm, serious. Not playful, not corporate, not retro.

Inspiration: the moment before a hand of cards is dealt in a dark-lit game shop; the inside of a jeweler's case; the warm metal tone of a well-used tool.

**Forge aesthetic rules:**

1. Dark canvas always.
2. Gold is "yours" — what the user owns, watches, saves, actives.
3. Purple is "Miru" — what the AI layer produced.
4. Leader colors are semantic only — never decorative.
5. Restraint over drama. No gradients unless a gradient *means* something. No glows unless the glow maps to a state.

---

## Color tokens

All colors are defined in `pm/storefront/src/app.css` under the `@theme` block. Reference them by token name; don't hardcode hex.

### Core surface

```
--color-miru-bg:              #08060f;
--color-miru-surface:         rgba(10, 14, 22, 0.88);
--color-miru-stroke:          rgba(255, 255, 255, 0.10);
--color-miru-stroke-brand:    rgba(201, 176, 255, 0.18);
--color-miru-stroke-gold:     rgba(244, 208, 120, 0.18);
```

- **bg** — the deepest canvas. Everything sits on this.
- **surface** — slightly-raised UI surfaces. Cards, sheets, sticky headers.
- **stroke** — default borders and dividers. Neutral, low-emphasis.
- **stroke-brand** — borders on surfaces that contain Miru output.
- **stroke-gold** — borders on surfaces that are "yours" (watchlist, saved decks).

### Accents

```
--color-miru-accent:  rgba(184, 160, 255, 0.96);  /* Miru purple */
--color-miru-gold:    rgba(244, 208, 120, 0.96);  /* yours, active */
```

One for "Miru" (the AI layer), one for "yours" (user ownership). No third accent. If you need a third, prove it's not a variation of one of these two.

### Text

```
--color-miru-text:     rgba(237, 243, 255, 0.88);  /* default text */
--color-miru-muted:    rgba(255, 255, 255, 0.55);  /* secondary */
--color-miru-muted-2:  rgba(255, 255, 255, 0.30);  /* tertiary */
```

Three tiers:

- **text** — body, headings, primary copy. Contrast ≥ 15:1 on bg.
- **muted** — captions, secondary info, helper text. Contrast ≥ 7:1.
- **muted-2** — tertiary, rare use. Contrast ≥ 4.5:1 (meets WCAG AA minimum — don't push it lower).

### Leader colors

```
--color-leader-red:     #c93a3a;
--color-leader-green:   #2d9d5f;
--color-leader-blue:    #3a7bc8;
--color-leader-purple:  #8b5cf6;
--color-leader-yellow:  #d4a012;
--color-leader-black:   #4b5563;
```

**These are semantic.** Leader red = red leader, red color symbol in OPTCG. Do not use them as decorative accents for non-leader-related UI. Do not use them for success/error semantics (those are separate).

### Functional / semantic

These are not in the theme block but are used consistently:

- **Success / at-target:** `#3fc98f` (green, distinct from leader green — slightly brighter, for UI state not card semantics).
- **Warning:** `#ffb75c` (warm orange, distinct from gold).
- **Error / destructive:** `#ff6b6b` (lighter than leader red, for UI state).
- **Info (rarely used):** `#5ca8e8`.

### Rules

1. **No new colors.** Propose additions via a PR that references this doc.
2. **Leader color in non-leader context = reject.** Use a theme color.
3. **Gradients only where they mean something.** E.g. a meter bar's fill can gradient from purple to gold when representing "Miru suggests → you decide." Otherwise flat.
4. **Opacity-based tints.** To create a subtle surface tint, use the accent color at low opacity, not a new hex.

---

## Typography

### Font families

```
--font-display:  'Geist', system-ui, sans-serif;
--font-ui:       'Inter', system-ui, -apple-system, sans-serif;
```

- **Display (Geist):** titles, hero numbers, "Miru says" labels.
- **UI (Inter):** body, labels, captions, everything else.

Both are self-hosted (see [docs/ui_ux/06_PERFORMANCE.md §Fonts](../ui_ux/06_PERFORMANCE.md#fonts)). Both are variable fonts — one file, all weights.

### Type scale

Mobile-first. All sizes in `rem` (relative to root 16px) so Dynamic Type scales.

| Role | Size | Weight | Line-height | Use |
|---|---|---|---|---|
| hero | 1.875rem / 30px | 600 | 1.1 | Large hero numbers, card-detail name |
| title | 1.375rem / 22px | 600 | 1.2 | Tab landing titles, card name on tile |
| subtitle | 1.125rem / 18px | 500 | 1.3 | Section headers within a page |
| body-lg | 1rem / 16px | 500 | 1.5 | Primary body — **minimum for inputs on iOS** |
| body | 0.875rem / 14px | 400 | 1.5 | Default body text |
| caption | 0.75rem / 12px | 500 | 1.4 | Labels, metadata, captions |
| micro | 0.6875rem / 11px | 500 | 1.3 | Price badges, tiny chips, tag pills |

### Numeric typography

- **`font-variant-numeric: tabular-nums`** on every price, count, stat, or any number in a list. Prevents jitter as values change.
- **`font-feature-settings: "tnum", "zero"`** for tabular + slashed-zero where type disambiguation matters.

### Character sets

- Latin + ASCII for all UI text.
- **Japanese** for card names in `lang="ja"` contexts (some OPTCG cards have Japanese names on Japanese-set cards). Geist doesn't cover CJK; we use `Noto Sans JP` variable as a fallback (loaded only on detail pages that render Japanese).

### Rules

- **No `px` for text.** Use `rem` so Dynamic Type scales.
- **Line-height unitless.** `line-height: 1.5`, not `line-height: 24px`.
- **No text shadows.** Dark-mode on dark canvas — shadows are noise.
- **No letter-spacing on body.** Leave it at 0. Slight negative tracking on hero-size is fine (`-0.01em` at sizes > 24px).

---

## Spacing scale

Spacing in PM follows a 4px base. Every value is a multiple of 4.

| Token | Value | Use |
|---|---|---|
| `--space-1` | 4px | Between adjacent inline elements (dot + name) |
| `--space-2` | 8px | Between label and input; between chips |
| `--space-3` | 12px | Small padding (chips, badges) |
| `--space-4` | 16px | Default padding, section margins |
| `--space-5` | 24px | Between major sections |
| `--space-6` | 32px | Between feature sections |
| `--space-8` | 48px | Between pages/landings |
| `--space-10` | 64px | Reserved for very large layouts |

### Rules

- **Content padding inside a card:** 16px.
- **Content padding on a page:** 14px horizontal (from `app.css body`), generous vertical for readability.
- **Gap between cards in a grid:** 8px default, 12px for larger tiles.
- **No arbitrary pixel values.** If you reach for `padding: 13px`, align to the scale.

---

## Radii

```
--radius-thumb:     6px;   /* very small — keyboard key, micro chip */
--radius-card-sm:   8px;   /* small card, chip */
--radius-button:    10px;  /* default button */
--radius-pill:      12px;  /* pill shape baseline (full radius for small heights) */
--radius-card:      14px;  /* card surface */
--radius-hero:      18px;  /* hero panel, large surface */
```

### Rules

- **Buttons:** 10px.
- **Cards:** 14px.
- **Chips (pills):** `9999px` (fully rounded).
- **Sheets:** top corners 20px, bottom flat. (No radius on bottom — safe area interacts with home indicator.)
- **Inputs:** 10px, same as buttons.
- **Images (card thumbnails):** 8px (card-sm). Card art usually has its own border inherent to the card image.

Consistency: a rounded rect at 14px next to a rounded rect at 10px looks sloppy. Use the right token for the role.

---

## Shadow and elevation

PM is low-elevation. Most surfaces sit flat on the canvas with a stroke to distinguish.

### When to use shadow

Only on elevated surfaces:

- Sticky footer: `box-shadow: 0 -8px 24px -16px rgba(0,0,0,0.6)`.
- Sticky header on scroll: same, flipped vertically.
- Modal / sheet: `box-shadow: 0 -16px 48px -24px rgba(0,0,0,0.8)`.
- Floating drag ghost: `box-shadow: 0 24px 48px -16px rgba(0,0,0,0.7)`.

### When not

- Card tiles: no shadow. Rely on stroke + slight bg delta.
- Sections: no shadow.
- Stickers / badges: no shadow.
- Hover state: a 1px lift + brightness, no shadow shift.

Rationale: on dark canvas, subtle shadows get lost in the background — they don't earn the paint cost. Strokes do the work. Reserve shadow for actual elevation.

### Glow vs shadow

For "active" or "highlighted" states, we use a soft glow (a low-opacity border-radius-matched blur) instead of a hard shadow:

```css
.active {
  box-shadow: 0 0 0 2px rgba(244, 208, 120, 0.16);
  /* Plus the normal border */
}
```

Glow is a "ring" — it maps to focus / active / selected. Shadow is elevation. They don't mix.

---

## Motion

[docs/ui_ux/00_PRINCIPLES.md §6 Calm motion](../ui_ux/00_PRINCIPLES.md) governs. PM specifics:

### Durations

| Role | Duration |
|---|---|
| Tap feedback (press in/out) | 80ms |
| Chip toggle | 120ms |
| Modal / sheet entrance | 250–280ms |
| Modal / sheet exit | 180–220ms |
| Page push/pop | 280ms / 250ms |
| Meter value change | 300ms |
| Variant cycle on tile | 200ms |
| WatchlistStar toggle | 260ms (with spring) |

### Easing

- Default: `cubic-bezier(0.22, 1, 0.36, 1)` — iOS standard ease-out.
- On-state-change: ease-out.
- On-dismiss: same ease-out, shorter duration.
- Spring (rare): drag release, WatchlistStar toggle. Custom spring via CSS `transition-timing-function: spring(...)` once browsers support it, or JS-based spring for now.

### Reduced motion

Already wired in `pm/storefront/src/app.css` via:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
}
```

Every animation respects it automatically. Don't override per-component.

### What never animates

- Text color changes (unless going to/from disabled state).
- Borders on every state change (only on active / focus).
- Scroll positions from state changes other than user-initiated.
- Layout shifts. Layout is stable; motion is pixel-level.

---

## Iconography

### Library

[lucide-svelte](https://lucide.dev/) — see [docs/ui_ux/09_TOOLING.md §Icons](../ui_ux/09_TOOLING.md).

### Sizes

- **Inline with body text:** match font size (16px icon with 16px body text).
- **On tile overlays (star, count badge):** 20px.
- **On buttons:** 14px (sm), 16px (md), 20px (lg).
- **BottomNav:** 24px.
- **Large feature icons:** 32–40px.

### Stroke weight

Lucide's default is 2px. We keep it. Don't mix stroke weights — icons with 1.5px stroke next to 2px look inconsistent.

### Custom icons

For PM-domain icons we can't get from lucide (cost hex, Miru gem, leader color symbol), they're SVG files in `pm/storefront/src/lib/icons/`. Same stroke weight, same visual density.

### Color

- Default: inherit from text color.
- Active: gold.
- Miru: purple.
- Leader: leader color (only in leader-context).

### Never

- Icons in a color that doesn't match a semantic. (No "this icon looks better red.")
- Icons without meaning. (No decorative flourishes.)
- Multiple icons on the same control. (Pick one.)

---

## Imagery

### Card images

- Aspect 5:7 locked.
- 14px radius on containers; card art has its own inherent border.
- Always served via CDN with AVIF → WebP → JPEG fallback.
- Always lazy below the fold.
- Always with explicit `width` / `height` for CLS.

### User avatars

- 32/40/56px circular.
- Fallback: single letter of display name on a gold-tinted background.
- No gradient or pattern fallback — just a clean letter.

### Decorative imagery

None. PM doesn't use hero illustrations, stock photography, or decorative flourishes. The design is spare on purpose. Card images and typography carry the visual weight.

---

## Surface hierarchy

PM uses two levels of surface beyond the canvas:

1. **Canvas** — `--color-miru-bg`. The page background. Covered by a subtle radial-gradient at the top (already in `app.css`) to give the app a slight glow.
2. **Surface** — `--color-miru-surface`. Slightly raised: cards, sheets, sticky headers.
3. **Elevated surface** — sheets open over surfaces get a darker edge via `box-shadow` to separate.

No third level. If something needs more elevation, rethink the IA — it probably wants to be a sheet or a page, not a nested surface.

### Backdrop blur

`backdrop-filter: blur(16px) saturate(140%)` on:

- Sheet backdrops (the darkened region behind).
- Sticky footers on pages with scrollable content.
- Sticky headers when scrolled past the top.

Respect performance budget: one blur per screen. See [docs/ui_ux/06_PERFORMANCE.md §Backdrop-filter](../ui_ux/06_PERFORMANCE.md#safari-specific-performance-notes).

---

## Dark-mode only (for now)

PM ships dark-only. Not a light theme, not an auto-mode switch. The operator directive is explicit.

Rationale: the forge aesthetic reads as dark. A light-mode re-skin would be a substantially different visual language, not a palette swap.

If we ever add a light mode, it goes in this doc with its own token set — we don't retrofit existing dark tokens into light.

### OS light preference

We honor `prefers-color-scheme: light` only to the extent of not forcing scrollbars, caret colors, or selection colors to dark values on users who've set light preference at the OS. We don't flip our UI to light.

---

## Visual consistency pre-ship checklist

Before merging any PM UI change:

1. **All colors come from tokens?** No hex literals in component code.
2. **All sizes in rem?** No `px` for text.
3. **All radii from the scale?** 8 / 10 / 12 / 14 / 18.
4. **All spacing multiples of 4?** Use the scale.
5. **Shadows only on elevated surfaces?** Not on cards, not on chips.
6. **Leader colors only in leader context?**
7. **Purple only where Miru output appears?**
8. **Gold only where user ownership shows?**
9. **Icons consistent with lucide stroke weight (2px)?**
10. **Animations ≤ 300ms? Easing from the standard set?**

If any answer is "I'm not sure," check back against this doc.
