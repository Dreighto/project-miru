# 01 — Tab Landings

**Applies to:** the five top-level tabs in PM — Home, Cards, Deck Builder, Leaders, Profile. Each tab's root surface.
**Read this when:** designing or refactoring a tab root; deciding what belongs on a tab landing vs a sub-page; adding a new top-level capability.
**Skip this when:** you're inside a specific sub-page (then the relevant feature doc is your reference).
**Length:** ~9 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [docs/ui_ux/03_SUB_PAGE_ARCHITECTURE.md](../ui_ux/03_SUB_PAGE_ARCHITECTURE.md), [02_PM_PRIMITIVES.md](02_PM_PRIMITIVES.md).

---

## The five tabs — one job each

The BottomNav is locked at five tabs. Adding a sixth means removing something; we don't grow the nav.

| # | Tab | One-sentence job |
|---|------|------------------|
| 1 | **Home** | What changed since I last checked? |
| 2 | **Cards** | Find a specific card or browse a set. |
| 3 | **Deck Builder** | Build, edit, or review a deck. |
| 4 | **Leaders** | Study a leader — stats, matchups, signature cards. |
| 5 | **Profile** | Me — watchlist, settings, collection. |

If you can't answer "what's this tab's job" in one sentence, the tab is in trouble. Usually it's a sub-page that was promoted too far.

### Why these five and not others

We considered — and rejected:

- **A dedicated "Search" tab.** Search is a capability, not a destination. It belongs in the header of Cards and Home.
- **A dedicated "Meta" tab.** Meta data belongs in Leaders (for a specific leader's meta share) and Home (for what's moving).
- **A dedicated "Prices" tab.** Prices are attributes of cards. They appear in the card detail sheet, on the watchlist (in Profile), and inline on tiles.
- **A dedicated "Collection" tab.** Collection is part of Profile until we have evidence it needs its own tab.
- **A dedicated "Social" tab.** No social layer yet. When there's a feed (e.g. deck sharing), it's a section in Home.

The test for promoting something to a tab: the user needs it in < 2 taps from cold-open, more than once a week. If no, it's a sub-page or a section, not a tab.

---

## Tab 1 — Home (`/`)

### Job

What changed since I last checked?

### Sections (top to bottom)

1. **Header.** "Miru" wordmark on the left; search icon on the right. No title — Home is home.
2. **Active meters.** 0–3 cards I've flagged with target prices. Each row shows the card, target, current, and a meter bar. Tap → card detail sheet. See [04_WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md).
3. **Today's movement.** 3–5 cards that moved in price or meta share since I last opened the app. Each row shows card, change direction (↑ / ↓), percent, and source. Tap → card detail sheet.
4. **Miru note.** 0–1 ambient observation from Miru (e.g. "Red Luffy builds have stabilized around 4× Monkey D. Luffy (OP01-001) in the last 7 days — you have 2 copies in your watchlist"). See [03_MIRU_LAYER.md](03_MIRU_LAYER.md). Hidden if Miru has nothing confident to say.
5. **Saved decks (quick access).** Last 3 touched decks, horizontal scroll. Tap → deck detail page.
6. **Watchlist snapshot.** The user's watchlist as a compact grid — 6 cards max, "See all" link to the watchlist page.

### What's *not* on Home

- A feed of every card in the game.
- "Trending cards globally" — this is noise unless the user cares. Instead, movement is framed in terms of the user's leaders and watchlist.
- Tips, tutorials, onboarding.
- Promotional content.

### Empty state

If the user has no watchlist and no decks:

- "Welcome. Watch a card to start tracking it."
- A "Watch a card" CTA that leads to Cards tab.
- After that, Home populates itself.

No full-screen onboarding, no forced tour.

### First-open state

On *very* first open:

- Home shows a single card: "Miru will surface what changes in your watched cards and decks here. Start by adding a card to your watchlist."
- Tap → go to Cards tab.

Silent after that. Home is functional, not motivational.

---

## Tab 2 — Cards (`/cards`)

### Job

Find a specific card, or browse a set.

### Sections (top to bottom)

1. **Header.** "Cards" title, search icon on right. Pull-down to reveal search field (Apple pattern, see [docs/ui_ux/07_COMPETITIVE_STUDY.md §Apple](../ui_ux/07_COMPETITIVE_STUDY.md#apple-mail-reminders-photos)).
2. **Set browser.** Grid of sets (OP01, OP02, ST01, ST02, etc.), newest first. Tap → open that set's cards.
3. **Alternatively, if sets view is overkill:** a compact toolbar with "All sets | Main | Starter | Promo" chips, a "Format" chip (Standard / East / Egman / Other), and a set count.

### Sub-routes

- `/cards?set=OP01` — open to a specific set's grid.
- `/cards/:code` — open directly to a card (deep link). Presents the card detail page (desktop) or opens the app to a sheet (mobile). See [docs/ui_ux/03_SUB_PAGE_ARCHITECTURE.md §URL shape](../ui_ux/03_SUB_PAGE_ARCHITECTURE.md).

### Cards grid behavior

- Virtual scroll via virtua if set > 120 cards (most sets hit this).
- Card tiles are PM's CardTile primitive. See [02_PM_PRIMITIVES.md](02_PM_PRIMITIVES.md).
- Tap tile → bottom sheet with card detail.
- Swipe tile left/right → cycle variants. See [05_GESTURES_PM.md](05_GESTURES_PM.md).
- Filter chips above the grid: color, cost, type, rarity, feature tag. Multiple select.
- Active filter count visible. "Color · 2 · Cost · 1" inline with a "Clear" button if any active.

### Empty / no-results state

- "No cards match these filters. Try widening color or clearing text search."
- "Clear filters" CTA.

### Offline state

- Show cached cards. A small header banner: "You're offline. Prices last verified [time]."
- Search works on cached data.
- Pull-to-refresh does nothing when offline.

---

## Tab 3 — Deck Builder (`/deck-builder`)

### Job

Build, edit, or review a deck.

This tab has the longest PM sessions — typical builds run 5–20 minutes. It's still one-handed, still mobile-first, but the frequency-of-use is lower than Home.

### Sections (top to bottom)

1. **Header.** "Deck Builder" or the deck's name if loaded. On the right: "Save" if unsaved changes, or a "..." overflow for save/load/export/delete.
2. **Leader bar.** The chosen leader at the top — avatar, name, color badge, life/counter. Tap → "Change leader" sheet.
3. **Deck state summary.** "42 / 50" card count, color validity, average cost. Small meters — see [04_WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md) for the Meter primitive.
4. **Pool / Deck toggle.** The screen splits: "Pool" (cards you can add) and "Deck" (cards currently in the deck). Tab between the two. On wide screens (tablet+), they sit side-by-side.
5. **Pool view.** Pre-filtered by leader colors. Card tiles with a `+` button (or long-press-drag) to add. Variant swipe works here.
6. **Deck view.** Grouped by type (Character / Event / Stage), with count badges (`×4`). Stepper on each row to adjust count. Swipe-left removes.
7. **Cost curve.** Small inline bar chart showing card count per cost. One tile of chrome, not a full section.
8. **Sticky footer (deck-view only).** "Validate" + "Save" buttons.

### Sub-routes

- `/deck-builder` — current working deck.
- `/deck-builder/:deckId` — a specific saved deck loaded for editing.
- `/deck-builder/new?leader=OP01-001` — start a new deck pre-seeded with a leader.

### No-leader state

- If no leader picked yet, the whole screen is a single prompt: "Pick a leader to start."
- A "Pick leader" CTA opens the leader-pick sheet.
- No pool visible until leader is chosen.

### Unsaved-changes affordance

- If the user has edited and not saved, the "Save" button pulses subtly (never strobe — `prefers-reduced-motion` turns it off). Gold tint. Present but not loud.
- On tab navigation away, if unsaved, show a confirm sheet: "Save changes to this deck?" with "Save," "Discard," "Cancel."

### Validation errors

- Inline banner at the top of the deck view. Red border, list of issues:
  - "Deck is 42/50 cards."
  - "3 copies of Monkey D. Luffy — 4-copy limit exceeded" (if it were, which it wouldn't be here).
  - "Card X is off-color (green leader requires green/yellow)."
- Tap a banner item → scrolls to and highlights the offending row.

### Offline state

- Drafts are saved locally (IndexedDB). Deck Builder works fully offline.
- Save-to-cloud queues when online; the user sees a "Will sync when online" badge on the deck tile.

---

## Tab 4 — Leaders (`/leaders`)

### Job

Study a leader — stats, matchups, signature cards, current meta share.

### Sections (top to bottom)

1. **Header.** "Leaders" title, filter chip row (color, format).
2. **Leaders grid.** All leaders, newest first (or by meta share, configurable). Each tile: leader image, name, color badge, current meta share %, trend arrow.
3. **Empty state if filter clears all:** "No leaders match these filters."

### Leader detail — sub-page (`/leaders/:code`)

Tapping a leader opens a *page*, not a sheet. Reason: enough content to be a destination, and users want to deep-link / share.

Leader detail contents:

1. **Hero.** Large leader image + color badge + meta share with 7d / 30d trend.
2. **Matchups.** Win rate against top 5 other leaders (from tournament data). Cite source URL.
3. **Typical deck shape.** Cost curve + key cards in ≥50% of top-8 lists.
4. **Signature cards.** 6–12 cards most uniquely tied to this leader (appears in leader decks at >2× baseline rate).
5. **Format notes.** Banned / restricted status per format.
6. **"Build this" CTA.** Opens Deck Builder with this leader pre-seeded.

---

## Tab 5 — Profile (`/profile`)

### Job

Me — watchlist, settings, saved decks, collection (when available), account.

### Sections (top to bottom)

1. **Header.** User's display name + avatar. Tap → edit-profile sheet.
2. **Watchlist preview.** First 6 cards, "See all" link.
3. **Saved decks.** List of user's decks (3 most recent, "See all" link).
4. **Collection.** (Future feature.) Count of cards owned, by set, by color.
5. **Settings sections.** Connected accounts, data export, notifications, appearance, privacy. Each → sub-page or sheet (single-setting edits are sheets; multi-setting areas are pages).
6. **Account.** Sign out (alert confirm), delete account (alert confirm, two-step).

### Sub-routes

- `/watchlist` — full watchlist. Sortable, filterable. Meters for items with targets.
- `/decks` — full deck library. Sortable by last-edited, by leader, by format.
- `/profile/settings/*` — settings pages.

### Sign-out / delete-account copy

- Sign out: "Sign out? You'll need to sign in again to sync. [Cancel] [Sign out]"
- Delete account: "Delete account?" followed by "All your decks, watchlist, and settings will be permanently removed. This can't be undone. [Cancel] [Delete]." Second modal confirmation.

---

## The BottomNav

Always present on tab roots, always hidden on sub-pages (see [docs/ui_ux/03_SUB_PAGE_ARCHITECTURE.md](../ui_ux/03_SUB_PAGE_ARCHITECTURE.md)).

- 5 slots, equal width.
- Each slot: icon (24px) + label (11px), centered.
- Active tab: gold icon + gold label + 2px gold top border on the slot.
- Inactive: muted icon and label.
- Tap: haptic `selection`, instant swap (no transition).
- Long-press a tab: show recent sub-pages in a mini menu (iOS-style). E.g. long-press Deck Builder → recent 3 decks.

Height: 56px + `env(safe-area-inset-bottom)`.

Source for long-press on tab → recent: Arc Search and Safari both support a version of this. It's a power-user shortcut, not a required path.

---

## Tab state persistence

Each tab remembers:

- Scroll position.
- Filters applied.
- Sub-route last visited (if user navigates between tabs and back).

When the user swipes from one tab to another, no tab state is lost. Reselecting a tab by tapping its BottomNav item from anywhere in that tab returns to the tab's root (standard iOS pattern — tapping the active tab pops to root).

---

## Rules about what doesn't go on a tab landing

### No heavy promotions

No "upgrade to pro" banners on Home. If pro exists, it's a single section in Profile, or an occasional contextual prompt (e.g. "This feature requires Pro — [learn more]" when the user taps a locked feature).

### No ads

No third-party ads. If we ever do sponsored content (e.g. a featured LGS), it's clearly labeled, never above-the-fold, and never animates.

### No notifications in-app

Tapping a push notification should deep-link to the relevant surface, not dump the user on Home with a "notification center" sidebar. A notifications list lives in Profile if needed; most users won't need it.

### No "what's new" inline

App-update highlights go in Profile > About > What's new. Not on Home.

---

## The cold-open test

For each tab, simulate: user opens PM to that tab. What do they see in the first 800ms?

- **Home:** 1 meter at the top. Skeleton while data loads.
- **Cards:** set grid (cached). No skeleton for the grid itself — it's cached.
- **Deck Builder:** the current deck, or no-leader prompt.
- **Leaders:** leader grid (cached).
- **Profile:** watchlist preview + saved-decks preview (cached).

Cached-first means no tab ever flashes empty on cold-open on a returning user. The second-open experience is the one most users get; optimize for it.

For brand-new users, the first-open empty states above apply.

---

## The "wrong tab" check

If you're about to add a feature and can't cleanly assign it to one of the five tabs, it probably doesn't belong in PM — or it belongs as a sub-page.

Examples we've considered and placed:

| Feature idea | Belongs to |
|---|---|
| Buy a card | Card detail sheet (`View prices` → external) — not a tab |
| Scan a card | Cards tab header action (if built); not a standalone tab |
| Tournament browser | Leaders detail, or future "Meta" sub-page in Home |
| Price history chart | Card detail sheet's "Prices" tab |
| Deck tester | Deck Builder's Validate action; not a standalone tab |
| Settings | Profile tab's sub-pages |
| Shared deck social feed | Home section (when built) |

When a new feature proposal can't find a tab home, the right move is usually "that's a future feature, not now." Don't shoehorn — the tab bar is the IA contract.
