# 02 — PM Primitives

**Applies to:** PM-domain components — card tile, leader chip, cost gauge, watchlist star, Miru gem, price badge, meter variants.
**Read this when:** you need a PM-specific component; you're updating the card tile; you're adding a new PM visual.
**Skip this when:** you're using generic primitives (button, input, sheet, modal) — those are in [docs/ui_ux/04_PRIMITIVES.md](../ui_ux/04_PRIMITIVES.md).
**Length:** ~9 pages.
**Related docs:** [docs/ui_ux/04_PRIMITIVES.md](../ui_ux/04_PRIMITIVES.md), [06_DESIGN_LANGUAGE.md](06_DESIGN_LANGUAGE.md), [05_GESTURES_PM.md](05_GESTURES_PM.md).

---

## The PM primitive set

These are the workhorses — the components that appear on every PM screen.

| Primitive | Purpose |
|---|---|
| **CardTile** | Visual representation of a single TCG card in a list or grid |
| **CardImage** | The card's art, with verified-source badge and lazy load |
| **CostGauge** | The hexagonal cost indicator (OPTCG aesthetic) |
| **PowerBadge** | The power number on a Character card |
| **LeaderChip** | A pill showing a leader's color + name |
| **ColorChip** | A filter / indicator chip using leader colors semantically |
| **WatchlistStar** | Toggle star to add / remove from watchlist |
| **VariantDots** | Row of dots indicating variant count + current variant |
| **CountBadge** | `×N` badge for card count in a deck |
| **PriceBadge** | Price + source + verification time |
| **MiruGem** | Small purple gem indicating Miru-generated content |
| **MeterBar** | PM-specific meter (watchlist price, deck completion) |
| **SetBadge** | Set code chip (`OP01`, `ST02`) |
| **RarityChip** | Small rarity indicator (C / UC / R / SR / SEC) |
| **MatchupBar** | Horizontal win-rate visualization |

Every one of these has a Histoire story, states spec'd, a11y labels. Follow [docs/ui_ux/04_PRIMITIVES.md §The contract](../ui_ux/04_PRIMITIVES.md#the-contract).

---

## CardTile

The most-rendered primitive in PM. Every card grid, every deck view, every watchlist row uses this.

### Layout

```
┌────────────────────┐
│                    │
│    Card image      │
│    (aspect 5:7)    │
│                    │
│  ★     ×3     ···  │  ← overlay layer
├────────────────────┤
│  ⚡ 5   OP01-001   │  ← cost + code
│  Monkey D. Luffy   │  ← name (2-line max)
│  ● ● ●             │  ← variant dots
└────────────────────┘
```

### Props

- `card: Card` — code, name, cost, power, color, type, image, variants
- `count?: number` — shown as `×N` badge if present (deck view)
- `showWatchlist?: boolean` — star overlay in top-left
- `showVariants?: boolean` — variant dots in bottom strip
- `priceSource?: string` — shown in PriceBadge if price present
- `onTap?: (card) => void`
- `onAdd?: (card) => void` — for deck pool "add" shortcut

### Sizing

Card tile width is responsive; height adjusts for aspect ratio + strip.

| Breakpoint | Tile width | Strip height |
|---|---|---|
| Compact grid (5 col on ≥ 390px) | 72px | 36px |
| Comfortable grid (3 col) | 112px | 42px |
| Large grid / single (2 col) | 168px | 48px |
| Deck row (horizontal) | 88px | 40px |

Tile image aspect locked at 5:7 (card ratio). Always reserve `width` + `height` on the `<img>` to prevent CLS.

### States

- **Default.**
- **Pressed:** scale 0.98, 80ms.
- **Selected** (in multi-select mode, e.g. deck-builder bulk edit): 2px gold border + gold tint.
- **Watched:** star filled gold.
- **In-deck:** `×N` badge in top-right.
- **Not-in-pool (filtered out):** 40% opacity (used only on informational passes, rare).
- **Loading:** skeleton of same dimensions.
- **No image available:** card code + name in a neutral placeholder. Never a broken image.

### Interactions

- **Tap:** opens card detail sheet. Haptic `impact.light`.
- **Long-press:** opens context menu (quick add to deck, add to watchlist, share). Haptic `impact.medium` at threshold.
- **Swipe left / right:** cycles variant (see [05_GESTURES_PM.md](05_GESTURES_PM.md)). Haptic `selection` at each cycle.
- **`+` button tap (if showAdd):** adds to current deck. Haptic `impact.medium`.

### Accessibility

- `role="button"` on the tile wrapper. `aria-label` composed: "Monkey D. Luffy, red leader, cost 5, power 5000, in deck 3 times, watched."
- Variant dots: `role="tablist"` if swipe cycles between variants; `aria-selected` on the active dot.
- Watchlist star: `<button aria-pressed>` reflecting current state.
- Inside a virtualized list: `aria-posinset` and `aria-setsize`.

### Anti-patterns specific to the tile

- **Don't** nest the watchlist star's tap target inside the card's tap target without `stopPropagation`. Tapping star should not open card detail.
- **Don't** animate the variant-cycle on tap. Swipe cycles; tap opens detail. Mixing these makes the tile twitchy.
- **Don't** hide the `×N` badge when `N = 1`. Confusing — users see the count and know a card is in a deck; absence is ambiguous.

---

## CardImage

Wraps the image with a verified-source badge, lazy load, fallback, and aspect-ratio lock.

```
┌─────────────────┐
│                 │
│  [card art]     │
│                 │
│           ✓ 2h  │  ← verified badge (optional)
└─────────────────┘
```

### Props

- `src: string` — the card image URL (served via image CDN).
- `alt: string` — card name for screen readers.
- `verifiedAt?: Date | string` — if present, show the verified badge.
- `source?: 'official' | 'community' | 'unverified'` — affects badge color.
- `width: number`, `height: number` — for CLS reservation.
- `priority?: 'eager' | 'lazy'` — lazy by default; use eager above the fold.

### Verified badge

- Bottom-right corner.
- `✓` checkmark + time since verification ("2h", "1d"). Shorthand.
- Color: gold (verified against official), purple (Miru-sourced / community-verified), muted gray (unverified, heuristic only).
- 11px, 8px padding inside a rounded pill.
- Absent if `verifiedAt` is null.

### Fallback

If the image fails to load (network, 404):

- Render a neutral placeholder with the card code and name.
- Log the failure client-side; count fallbacks per session for observability.
- Never show a broken image icon, never show a spinner indefinitely.

---

## CostGauge

The hexagonal cost indicator — an OPTCG-native aesthetic. Players know the hex shape means "cost."

### Visual

- Regular hexagon (flat-top orientation, OPTCG convention).
- 20×20 default, 24×24 large, 16×16 small.
- Filled with leader color for Character/Event cards; gold for Leader cards.
- Cost number centered in white (or dark if gold-filled).
- 11–13px type, bold.

### Usage

- On CardTile: top-left of strip.
- On CardDetail: next to the name in the header.
- On deck cost curve: the x-axis labels are tiny CostGauges (cute, thematic, legible).

### Anti-patterns

- **Don't** re-skin the cost gauge as a circle or square for "consistency." The hex is the recognizable OPTCG affordance.
- **Don't** use the cost gauge for anything other than cost. No "turn counter" or "counter" reuse.

---

## PowerBadge

The power value (BP) on a Character card.

### Visual

- Small rectangle, 36×16, rounded 4px corners.
- Filled with a muted translucent color (`rgba(255,255,255,0.06)`).
- Power number in white, 11–12px, right-aligned.
- Optional `K` unit if value > 9999 ("10K"), otherwise full value ("5000").

### Usage

- CardTile strip: next to cost gauge.
- CardDetail header.
- Deck view row: compact form shows cost + power only.

---

## LeaderChip

Pill representing a leader's color + name.

### Visual

```
┌─────────────────────┐
│  ● Red Luffy        │
└─────────────────────┘
```

- Pill, 28px tall.
- 8px leader-color dot on the left.
- Leader name + color abbreviation ("Red Luffy") in 13px.
- Border: 1px rgba(leader-color, 0.32).
- Background: rgba(leader-color, 0.06).
- Text color: the leader color if contrast permits (≥4.5:1 on dark canvas), else white.

### Variants

- **Active (current leader):** +border opacity, +background opacity (stronger color).
- **Multi-color leader** (e.g. red/green): two dots side by side.
- **"Unset":** neutral stroke, "Pick leader" text.

### Anti-patterns

- **Don't** use LeaderChip as a generic tag. It's a semantic primitive — reserved for leaders.

---

## ColorChip

Filter chip for color selection. Uses leader colors semantically.

### Visual

- Pill, 32px tall.
- 6px dot in the leader color, leader-name next to it.
- Inactive: muted.
- Active: leader-color background (low opacity) + leader-color border (high opacity).

### Usage

- Cards tab filter bar (select one or more colors).
- Deck Builder pool filter.

### Interaction

- Tap: toggles. Haptic `selection`.
- Active state persists across sessions (via URL query or localStorage).

---

## WatchlistStar

Toggle star indicating "I'm watching this card."

### Visual

- 20×20 icon.
- **Unwatched:** outlined star, muted color.
- **Watched:** filled gold star.
- Tap target: 44×44 (padding around the icon).
- Transition: spring scale 0.8 → 1.15 → 1.0 on toggle, 260ms.
- Haptic `impact.medium` on toggle on.
- Haptic `selection` on toggle off.

### Where it appears

- CardTile (top-left overlay).
- CardDetail sheet (header).
- Watchlist page (already filled; tap toggles off with an undo snackbar).

### Per-variant state

When a card has multiple variants, the star's filled/unfilled state reflects *the currently displayed variant*, not the base card. Cycling variants via swipe (see [05_GESTURES_PM.md](05_GESTURES_PM.md)) updates the star live. Each variant is its own watchlist entry with its own target price. See [04_WATCHLIST_AND_METER.md §Variant differences](04_WATCHLIST_AND_METER.md).

### Copy (accessibility)

- Unwatched: `aria-label="Add Monkey D. Luffy to watchlist"`
- Watched: `aria-label="Remove Monkey D. Luffy from watchlist"`
- After toggle: an `aria-live="polite"` region announces "Added" or "Removed."

### Anti-patterns

- **Don't** use a heart or bookmark icon. Star is the PM convention.
- **Don't** animate the star on hover. Only on tap.

---

## VariantDots

Small row of dots indicating how many variants a card has and which is currently shown.

### Visual

- 4–8px dots with 4px gaps, centered in the CardTile strip or detail header.
- Active dot: gold, 6px.
- Inactive dots: 4px, muted.
- If > 6 variants: show 3 dots + "+N" compact notation.

### Interaction

- Tap a dot (desktop): jumps to that variant.
- On CardTile: not directly tappable — swipe cycles them. Dots are indicators only.
- On CardDetail: dots are tappable (they're in a roomier context).

---

## CountBadge

`×N` badge showing card count in a deck.

### Visual

- Small pill, 22px tall, 28px wide.
- Background: gold (`var(--color-miru-gold)` at 20% opacity).
- Border: gold at 40%.
- Text: gold, 11px, bold, tabular-nums.
- Position: top-right overlay on CardTile when rendered in a deck view.

### Rules

- Shown when `N ≥ 1`. See [§CardTile anti-patterns](#anti-patterns-specific-to-the-tile).
- Max display: `×4` (OPTCG 4-copy limit). If a deck is somehow over 4, show `×4+` in red — which signals a validation error.

---

## PriceBadge

Displays a price with source and freshness.

### Visual

```
┌───────────────────────┐
│ $24.99  TCGPlayer · 2h │
└───────────────────────┘
```

- Two-line or inline. Inline for compact views; two-line for detail.
- Price: gold, 14px, tabular-nums, bold.
- Source + time: muted, 11px.
- Never without both source and time. If we don't know the source, the badge doesn't render.

### States

- **Fresh** (< 2h): full opacity.
- **Aging** (2h–24h): slightly muted.
- **Stale** (> 24h): warning dot next to time. "Price may be out of date."
- **Unverified** (no source confirmation): gray, with "Unverified" text instead of source.
- **Unavailable:** "Price unavailable — no listings recorded" in muted text.

### Anti-patterns

- **Don't** display a price without source. Ever.
- **Don't** round to whole dollars without showing the cents. $24 reads as final; $24.99 reads as a price.
- **Don't** highlight price with a color other than gold (the "yours / active" color) or red (for over-target). Green for under-target is acceptable, used sparingly.

---

## MiruGem

Small purple gem icon indicating "Miru generated this" — a suggestion, insight, or piece of ambient intelligence.

### Visual

- 12–16px purple diamond/gem shape. Simple — not a sparkle, not multi-facet.
- Filled with `var(--color-miru-accent)`.
- Optional subtle pulse animation on first render (one cycle, 800ms, then static). Respects `prefers-reduced-motion`.

### Where it appears

- Next to a Miru note on Home.
- On a card detail's "Miru says" section.
- Inline in a deck suggestion row: "`[MiruGem]` Miru found 5 cards that…"

### The rule

- **MiruGem only appears next to output Miru generated.** Never decorative. See [03_MIRU_LAYER.md](03_MIRU_LAYER.md) for the broader Miru visual discipline.

---

## MeterBar

PM-specific meter with price / deck-completion semantics. See also [04_WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md).

### Visual

- Track: 6px tall, fully rounded, `rgba(255,255,255,0.06)`.
- Fill: semantic color (see below).
- Target marker: 2px vertical line, gold. Position = target as a fraction of max.
- Current marker: 6px circle. Position = current value.
- Labels: caption above ("Red Luffy · Target $22 · Now $18"), compact readout below.

### Semantic fills

| Meter kind | Fill color | Meaning |
|---|---|---|
| Price (at / under target) | Green | User's target met |
| Price (over target) | Gold | In-range but above target |
| Price (stale) | Muted | Old data, treat cautiously |
| Deck completion | Gold | 50 = full |
| Miru-confidence | Purple | AI-generated value |

### Rules

- Target and current markers are always visible; one without the other is a broken meter.
- When value crosses target (price drops to target): haptic `impact.heavy` + push notification (if enabled).
- Animation on value change: 300ms ease-out. See [docs/ui_ux/04_PRIMITIVES.md §Meter](../ui_ux/04_PRIMITIVES.md#meter).

---

## SetBadge

Small set code chip — "OP01", "ST02", "OP-PR" (promo), etc.

### Visual

- Pill, 22px tall, 4–6px horizontal padding.
- Background: `rgba(255,255,255,0.04)`.
- Border: `rgba(255,255,255,0.08)`.
- Text: muted, 11px, tabular-nums.
- Highlighted (active filter): gold background + gold border.

---

## RarityChip

Rarity indicator — C / UC / R / SR / SEC / L (Leader) / SP (Special).

### Visual

- Pill, 18px tall, 4px padding.
- Background tint varies by rarity — subtle, not gaudy:
  - **C** (Common): neutral gray.
  - **UC** (Uncommon): slight green tint.
  - **R** (Rare): slight blue tint.
  - **SR** (Super Rare): slight purple tint.
  - **SEC** (Secret Rare): subtle gold gradient. Never animated.
  - **L** (Leader): gold border.
  - **SP** (Special / Alt-art): subtle pearl tint.
- Text: 10px, bold, uppercase.

### Anti-patterns

- **Don't** use aggressive gradients or particle effects on high rarities. OPTCG players already know SEC is special. Restraint signals craft. See [00_PRINCIPLES.md §5 Class, not hype](00_PRINCIPLES.md).

> **Operator-confirmed.** Rare variants get quiet visual treatment, not casino-style animation. This is a deliberate break from TCG-app convention — SEC shimmer, alt-art sparkle, particle effects are all explicitly rejected. See [08_PM_ANTI_PATTERNS.md §C1](08_PM_ANTI_PATTERNS.md).

---

## MatchupBar

Horizontal win-rate visualization for leader-vs-leader data.

### Visual

```
Red Luffy    ┃████████░░░░░░  56%
vs Purple Doffy
(87 games · Egman · last 30d)
```

- 48% of the pill is gray (opponent's win rate); 52%+ is gold.
- Pill height 24px.
- Beneath: sample size, source, date range.
- Missing-data state: "Not enough match data yet (< 20 games)."

### Rules

- Always show sample size. A 60% win rate over 5 games is noise.
- Color transitions near 50/50 are subtle — no red/green alarm. Players are making informed decisions, not reacting.
- Link out to the source on tap (e.g. Egman tournament archive).

---

## The "is this a PM primitive?" test

Before adding a new PM component:

1. **Does it live in TCG semantics?** If it's a generic surface (button, input), it belongs in [docs/ui_ux/04_PRIMITIVES.md](../ui_ux/04_PRIMITIVES.md).
2. **Does it show up on more than one PM tab?** If yes, primitive. If only one tab, it's a view-specific component.
3. **Does it carry meaning that only OPTCG players would recognize?** (Cost hex, variant dots, ×N count, leader color dot.) Primitive.
4. **Does it compose TCG data with UI?** (CardTile, MatchupBar, PriceBadge.) Primitive.

If yes to any of 2–4, it's a PM primitive. Add a story, write a11y defaults, and link from this doc.

---

## Storage

PM primitives live at:

```
pm/storefront/src/lib/components/pm/
  CardTile.svelte
  CardImage.svelte
  CostGauge.svelte
  PowerBadge.svelte
  LeaderChip.svelte
  ColorChip.svelte
  WatchlistStar.svelte
  VariantDots.svelte
  CountBadge.svelte
  PriceBadge.svelte
  MiruGem.svelte
  MeterBar.svelte
  SetBadge.svelte
  RarityChip.svelte
  MatchupBar.svelte
```

Universal primitives (Button, Sheet, Modal, Toast) live in `pm/storefront/src/lib/components/` — same directory, one level up. The `pm/` subdirectory is for domain components.
