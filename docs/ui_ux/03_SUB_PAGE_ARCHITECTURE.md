# 03 — Sub-page Architecture

**Applies to:** any time you're adding a new screen, modal, sheet, drawer, detail view, or secondary surface. Anywhere the user goes "into" or "beneath" the main tab.
**Read this when:** you're about to decide "should this be a page or a sheet?"; you're mapping navigation for a new feature; you're cleaning up a surface that grew a pile of ad-hoc modals.
**Skip this when:** you're editing inside an existing surface that's already the right shape.
**Length:** ~8 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [02_GESTURES.md](02_GESTURES.md), [04_PRIMITIVES.md](04_PRIMITIVES.md).

---

## The core question

Every secondary surface answers one of three questions:

1. **"What is this?"** → Peek. Modal or inline expand. Temporary, returns to the prior context.
2. **"Do this action."** → Sheet. Interactive, may include form fields, returns to prior context.
3. **"Go here."** → Page. Full navigation, URL-addressable, back is an undo.

Pick the wrong shape and the surface fights the user: a sheet that's really a page can't be bookmarked; a page that's really a peek loses context every time.

---

## The decision rule

| You want to... | Use |
|---|---|
| Show a quick preview of an item the user just tapped | **Modal** (inline detail, returns on tap-outside) |
| Let the user take an action without leaving their place | **Sheet** (bottom sheet with input) |
| Move the user to a different primary workspace | **Page** (full route, URL changes, back is a real navigation) |
| Confirm a destructive action | **Alert** (system-style modal, two buttons, dismissable) |
| Display a sequence of steps | **Page** (each step is addressable) OR **Full-screen sheet** (if no deep-link needed) |
| Show ephemeral status (success/error) | **Toast / snackbar** (auto-dismiss) |
| Reveal secondary controls on a list row | **Swipe-to-reveal** (inline), not a sheet |
| Let the user pick one of 3–7 options | **Action sheet** (native-feel list) |
| Let the user pick from >7 options or filter | **Full-height sheet** or **Page** depending on context |

**Defaults, not laws.** A sheet can be a peek if the design demands it. A page can be ephemeral if it deep-links. But if you're reaching for an exception, you should be able to name the reason in one sentence.

---

## Modals

A modal is a **focus-capturing overlay** that sits above the current context. It darkens or blurs the rest of the screen, and tapping outside dismisses it. It's for "I need to read something" or "I need one decision."

### When to use a modal

- Confirming a destructive action ("Delete this deck?")
- Displaying a single piece of information that's tied to something the user just tapped
- System-style alerts from the browser (permission prompts, etc.)

### When NOT to use a modal

- Anything the user might want to scroll through. Modals are short. If scrolling is needed, use a sheet.
- Anything with inputs. Modals on mobile get eaten by the keyboard and become un-dismissible. Use a sheet.
- Anything the user might want to go back to. Modals aren't in the history stack.

### Modal physics

- **Backdrop:** 60% black overlay with a 12px backdrop-filter blur where GPU allows.
- **Entrance:** fade backdrop over 200ms; scale modal from 0.96 → 1.00 and fade 0 → 1 over 250ms; combined `ease-out`.
- **Exit:** fade both out over 180ms, no scale.
- **Tap-outside-to-dismiss:** yes, always.
- **Escape key:** dismisses.
- **Trap focus inside** the modal while open ([W3C — Dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)).
- **`aria-modal="true"`** and `role="dialog"` or `role="alertdialog"` for destructive.
- **Return focus** to the element that opened the modal on close.

### Modal height

Keep modals small: **max 60% of viewport height** on mobile, so the user can always see context behind them. Any taller and it's a sheet in disguise — use a sheet.

### A modal is not a page

Modals do not change the URL. If the user could meaningfully share a link to this content, it should be a page. If the user navigates away and comes back, the modal should not reappear — it's ephemeral.

### `<dialog>` element

Prefer the native [`<dialog>` element](https://developer.mozilla.org/en-US/docs/Web/HTML/Element/dialog) over a custom overlay. It gives you free `showModal()`, `close()`, focus trap, Escape handling, top-layer rendering (no z-index fights), and `::backdrop` styling. Supported in Safari 15.4+, Chrome 37+, Firefox 98+. No reason to hand-roll this anymore.

---

## Sheets

A sheet is a surface that slides in from an edge. On mobile, almost always the bottom. On tablet/desktop, often the right side. It's for "do an action without losing my place."

Sheets are the **workhorse of Miru**. Most secondary surfaces are sheets because they support inputs, scrolling, scrolling gestures, and the mental model of "I'll poke at this and come back."

### When to use a sheet

- Editing a value (add to watchlist, set target price, pick leader colors)
- Confirming a multi-part action (deck save with name + privacy)
- Showing detail that the user will interact with (card detail with buy/watch buttons)
- Any secondary surface on mobile that needs scrolling or inputs

### Sheet variants

| Variant | Height | Example |
|---|---|---|
| **Action sheet** | Auto (content height, max 50% viewport) | Pick a variant, pick a format |
| **Half sheet** | 50% viewport, drag up for full | Card detail with tabs |
| **Full sheet** | 92% viewport, ~8% backdrop visible | Deck save form with many fields |
| **Side sheet** | 360px right-anchored (tablet/desktop only) | Card detail on iPad |

### Sheet physics

- **Entrance:** slide from bottom (or side) over 280ms, `ease-out`.
- **Exit:** slide back over 220ms.
- **Drag to dismiss:** the top 48px of the sheet is a drag handle. Drag down >40% of height + release, or velocity > 0.5 px/ms down, dismisses.
- **Drag to expand:** a half-sheet has a drag handle that expands to full on drag up past 20%.
- **Backdrop:** fade in to 40% black over 280ms, fade out over 220ms.
- **Spring-back on partial drag:** < 40% of dismiss distance → spring back over 180ms.
- **Rounded top corners:** 20px radius. On iOS safe-area devices, add `padding-bottom: env(safe-area-inset-bottom)` to the sheet body.
- **Content `touch-action: pan-y`;** drag handle `touch-action: none` to prevent conflicts (see [02_GESTURES.md](02_GESTURES.md)).

### Scroll inside a sheet

The sheet's body scrolls when sheet is at full height. When partially collapsed, drag-gesture wins over scroll. The switch-over:

- If `scrollTop > 0` → gesture on the body scrolls content, not sheet.
- If `scrollTop === 0` and gesture direction is down → dismiss gesture.
- If `scrollTop === 0` and gesture direction is up → expand gesture (half → full).

This "rubber-band at top" is what iOS native sheets do. Source: [Apple HIG — Sheets](https://developer.apple.com/design/human-interface-guidelines/sheets).

### Sheet accessibility

- Sheet opens: focus moves to the sheet's first interactive element (or the close button if none).
- Close via: tap backdrop, drag-to-dismiss, Escape key, explicit close button in top-right.
- `role="dialog"`, `aria-modal="true"`, `aria-labelledby` pointing at sheet title.
- Return focus to opener on close.

### Sheet URL behavior (important)

**Sheets should push a URL hash or a shallow route when opened.** E.g. opening the card detail sheet from `/cards` moves the URL to `/cards#SOP-001`. This makes:

- Browser back = close sheet.
- Refresh = sheet stays open (user is less likely to lose their place).
- Share link = deep-links to the same sheet open.

SvelteKit supports this via `goto('#hash', { noScroll: true, replaceState: false })`. The pattern also matches Gmail, Twitter, Instagram, and Arc. Source: [Instagram's URL pattern — their modal-on-profile hack](https://web.dev/articles/url-patterns).

---

## Pages

A page is a primary surface. It has its own URL, its own back button, its own scroll position. It's a **destination**, not an overlay.

### When to use a page

- The user has navigated to a different workspace (Cards → specific card's history → specific variant's price chart)
- The surface is deep-linkable (you'd expect to share it or bookmark it)
- The surface has substantial content that belongs in the browser history
- The user might want to open this in a new tab

### When NOT to use a page

- Quick confirmations (use modal)
- Inline edits (use sheet)
- Short-lived previews (use modal or sheet)

### Page structure

Every PM page has the same skeleton:

```
┌─────────────────────────────┐
│ Header (sticky)             │ ← title, back button (if not tab root), actions
├─────────────────────────────┤
│                             │
│ Content (scroll region)     │
│                             │
├─────────────────────────────┤
│ Sticky footer (optional)    │ ← primary CTA if applicable
├─────────────────────────────┤
│ BottomNav (tabs only)       │ ← only visible on tab roots
└─────────────────────────────┘
```

Sub-pages under a tab *replace* the BottomNav area with the sticky footer (or nothing). The user is "inside" and needs to come back out. The bottom tab bar reappearing mid-navigation creates confusion about "where am I."

### Page transitions

- **Push (forward):** slide from right over 280ms `ease-out`.
- **Pop (back):** slide to right over 250ms `ease-out`.
- **Tab switch:** no transition, instant swap (with scroll restoration).

Page transitions are subtle — they signal history direction without drama. Do not animate the header separately; treat the whole page as one layer.

SvelteKit uses [view transitions API](https://developer.chrome.com/docs/web-platform/view-transitions/) where supported (Chrome 111+, Safari 18+). Fallback to no transition on older browsers — transitions are a nice-to-have, not a contract.

### Tabs versus sub-pages

A **tab** is a top-level workspace (Home, Cards, Deck Builder, Leaders, Profile). The user moves between tabs via the BottomNav.

A **sub-page** is a screen you navigate into from a tab. It has a back button, not a tab indicator.

**Rule:** tabs never have a back button. Sub-pages always do. If your "tab" needs a back button, it's a sub-page that got promoted too far.

### URL shape

Every page in PM follows `/tab[/subpath]` shape:

- `/cards` — Cards tab root
- `/cards/OP01-001` — Card detail page (this is where the bottom sheet is an acceptable alternative — the sheet is the mobile form, the page is the desktop form)
- `/deck-builder` — Deck Builder tab root
- `/deck-builder/:deckId` — Specific deck
- `/leaders/:leaderCode` — Leader detail

Sub-routes are fine. Deep sub-routes (`/cards/OP01-001/variants/SPECIAL/history`) are a code smell — that's three pages of nesting. Flatten or move to sheets.

---

## Other surface types

### Toasts / snackbars

Ephemeral status strip at the bottom of the screen. Auto-dismisses after 4s (5s if there's an undo button).

**When to use:** after an action committed ("Added to watchlist", "Deck saved"). After an auto-dismiss destructive ("Card removed. Undo?").
**When NOT to use:** for errors that require user decision (use modal). For anything critical the user might miss (use sheet).
**Position:** above BottomNav on tab roots; above sticky footer on sub-pages; at bottom of safe area everywhere.

Accessibility: `role="status"` for affirmative, `role="alert"` for warnings/errors. Do not steal focus. Source: [W3C — ARIA live regions](https://www.w3.org/TR/wai-aria-1.2/#live_region_roles).

### Action sheets

Native-feel list of 3–7 options that appears from the bottom, with a Cancel option at the bottom.

**When to use:** one-of-N choice. "Pick a variant," "pick a sort order," "pick a format."
**When NOT to use:** more than 7 options (goes to a full sheet). Anything other than a single pick (goes to a full sheet).

iOS and Android native action sheets differ visually; we pick iOS-style (rounded rectangle, blurred backdrop, Cancel separated below). This is what most TCG apps do and it reads correctly on both platforms.

### Alerts

System-style modal with a short title, optional body, and 2 buttons (rarely 3). "Are you sure?" territory.

**Default title wording:** a clear question or statement, not a verb. "Delete this deck?" not "Delete."
**Default buttons:** Cancel (left, plain text) + destructive action (right, red). On iOS, destructive is always red text on plain background. On Android, it can be filled red button. We use iOS style everywhere for consistency.
**Avoid** "OK" — use the specific verb. "Delete," "Save," "Discard."

### Popovers

A small overlay anchored to an element, showing a tooltip-like floating menu. Used only on desktop/tablet breakpoints for PM; on mobile these become action sheets because anchored popovers get clipped by the viewport.

---

## Mapping PM's tabs to surfaces

This is the authoritative surface inventory for PM. If you're adding to one of these tabs, check you're using the right shape.

### Home tab (`/`)

- Root: feed + meters + watchlist summary.
- Card tap → **sheet** (half, card detail with tabs).
- "See all" on watchlist → **page** (`/watchlist`).
- Notification tap → **page** (`/notifications`).
- Meter tap → **sheet** (target config).

### Cards tab (`/cards`)

- Root: sets grid.
- Set tap → **in-place swap** to cards grid (sub-route `/cards?set=OP01`).
- Card tap → **sheet** (half, expandable to full).
- Within the sheet, tab between details / variants / prices is an **inline tab**, not a new page.
- "View full history" → **page** (`/cards/:code/history`).

### Deck Builder tab (`/deck-builder`)

- Root: current deck working state (no-leader prompt if none picked).
- Leader pick → **sheet** (leader grid, full height).
- Card tap (in pool) → **modal** (small peek, 3s dismiss if not interacted) or long-press for a **sheet** with full detail.
- Deck save → **sheet** (name + description + privacy form).
- Deck validation errors → **inline banner** at top, not a modal.
- "Load deck" → **sheet** (list of saved decks).
- "Share deck" → **action sheet** (share targets: copy link, system share, export).

### Leaders tab (`/leaders`)

- Root: leader grid.
- Leader tap → **page** (`/leaders/:code` — leader has enough content to be destination-worthy: matchups, meta share, signature cards).
- Within leader page: signature card tap → **sheet** (card detail).

### Profile tab (`/profile`)

- Root: user settings sections.
- Section tap → **page** for substantive sections (Connected accounts, Data export), **sheet** for single-setting edits (Display name, Theme).
- Sign out → **alert** (destructive confirm).

---

## Navigation anti-patterns

### Sheet-inside-sheet

Opening a sheet from within a sheet. User can't tell where they are; dismiss becomes ambiguous.
**Fix:** if an action inside a sheet needs another surface, close the current sheet first and open the new one, or put the secondary action inline (expandable section).

### Modal-on-modal

Same problem as sheet-in-sheet, worse because modals don't have a drag handle to orient by.
**Fix:** close first, open next. Serialize the decisions.

### Page that should be a sheet

Pattern: "Edit name" is a full route that navigates away from the list. User now loses scroll position.
**Fix:** bottom sheet with a text input and Save. URL can still change to `/?edit=name` if deep-linking matters.

### Sheet that should be a page

Pattern: card detail opens as a sheet, but the sheet has 4 tabs of content and the user spends 3 minutes in it.
**Fix:** in PM we keep the sheet, but the sheet pushes URL `/cards#CARD-001`. A full-page route at `/cards/CARD-001` exists too and wins on desktop where a sheet would be weird.

### Infinite sub-routing

`/cards/SOP-001/variants/ALT/prices/seller-3/history/2025-11`. Each level is a page. Back button takes 6 presses to escape.
**Fix:** nest at most 2 levels. Anything deeper becomes a sheet on top of a page, or a sub-tab within a page.

### BottomNav visible inside a sheet

Pattern: sheet opens and you can still see the tab bar.
**Fix:** sheets are above everything except system UI. BottomNav is `z-index: 40`; sheet backdrop is `z-index: 60`; sheet content is `z-index: 61`. The sheet occludes the BottomNav when at full height.

### Pull-to-refresh inside a sheet

Pattern: sheet body scrolls, and at top, a pull gesture fires a refresh on the *underlying page*.
**Fix:** disable pull-to-refresh inside sheets. `overscroll-behavior: contain` on the sheet body.

---

## Checklist before shipping a new surface

1. Did you pick the right shape (modal vs sheet vs page)? Use the decision rule above.
2. Does back navigation (hardware back, gesture back, header back) do the right thing?
3. Does the surface push a URL (or explicitly decide not to, with a reason)?
4. Does the surface trap focus (if modal/sheet) and restore focus on close?
5. Does the surface respect safe areas (`env(safe-area-inset-*)`)?
6. Does the surface dismiss cleanly on backdrop tap, Escape, and drag-down?
7. Does the surface handle the keyboard (for inputs) — see [01_MOBILE_PWA.md](01_MOBILE_PWA.md) on VisualViewport?
8. Does the surface render over the BottomNav, not beside it?
9. Is the exit animation shorter than the entrance?
10. Does the transition cancel if the user triggers a second navigation mid-animation?

If you can't check all ten, the surface isn't shipped — it's a demo.
