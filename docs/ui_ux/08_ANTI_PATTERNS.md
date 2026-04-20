# 08 — Anti-Patterns

**Applies to:** every UI decision. A fast pass before shipping.
**Read this when:** you've built something and want to check it against the common failure modes; you're reviewing a PR; you're wondering "why does this feel off."
**Skip this when:** you're mid-build and haven't hit a decision yet. Come back before merging.
**Length:** ~8 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), every other doc in this library (anti-patterns reference them).

---

## How to use this document

Each anti-pattern follows the same shape:

- **Name** — short identifier we can refer to in PR reviews.
- **Pattern** — what the bad thing looks like.
- **Why it fails** — the mechanism by which it hurts users.
- **Evidence** — a 1-star review, WCAG failure, published spec violation, or well-studied failure mode.
- **Fix** — the correction.

Don't skim. Every anti-pattern here is something we've seen shipped — in TCG apps, in general consumer software, or in our own early prototypes.

---

## Manipulation

### Fake urgency

**Pattern:** "Only 3 left!" badges. Countdown timers on non-time-limited items. "Deal ends in 24:00:00" on evergreen content.

**Why it fails:** users learn within one use that the urgency is fake. Trust drops. The "only 3 left" trick is well-documented and widely copied because it converts in short term, and widely resented once users recognize the pattern.

**Evidence:** [Harry Brignull's deceptive.design pattern library — Urgency](https://www.deceptive.design/types/urgency). [European Data Protection Board — Dark Patterns guidelines](https://edpb.europa.eu/system/files/2023-02/edpb_03-2022_guidelines_on_deceptive_design_patterns_in_social_media_platform_interfaces_v2.0_en.pdf).

**Fix:** if there are genuinely only 3 left, say so with a neutral "3 remaining." If supply isn't limited, don't invent a number.

### Fake social proof

**Pattern:** "28 other players are watching this card right now" — when you don't know that, or when the number is always 28.

**Why it fails:** same as fake urgency. Users call it out in reviews. Once seen, cannot be unseen.

**Fix:** only display social proof when verified, and cite the source ("28 decks in the last 7 days include this card — Miru data").

### Dark patterns in cancellation / unsubscribe

**Pattern:** hiding the unsubscribe option, requiring a phone call to cancel, multi-step "are you sure" loops meant to deter.

**Why it fails:** regulatory risk (FTC's Click-to-Cancel rule), reputation, and basic respect.

**Evidence:** [FTC — "Click-to-Cancel" rule, 2024](https://www.ftc.gov/news-events/news/press-releases/2024/10/federal-trade-commission-announces-final-click-cancel-rule-making-it-easier-consumers-end-recurring). [r/assholedesign](https://www.reddit.com/r/assholedesign/) is a long-running catalog.

**Fix:** unsubscribe in one tap from anywhere a subscription appears. Confirmation is one modal max.

### Forced engagement loops

**Pattern:** streaks with punishing loss states ("you lost your 47-day streak!"). Daily push notifications unrelated to real user value. Notification badges when nothing changed.

**Why it fails:** Duolingo-style patterns work for apps people use daily. PM is weekly at best. Forced streaks create anxiety, not habit.

**Evidence:** [How Duolingo Uses Dark Patterns — Nicholas Chin](https://www.figma.com/blog/duolingo-the-dark-design-behind-a-5-5-billion-unicorn/) (summary); [Apple HIG — Notifications (guidance against "just because")](https://developer.apple.com/design/human-interface-guidelines/notifications).

**Fix:** every push notification ties to a user-set alert or a genuinely surprising event. Streaks are optional and have no loss state.

---

## Discoverability failures

### Hidden gestures as only path

**Pattern:** "swipe up on the card to watch it" — with no visible affordance.

**Why it fails:** [00_PRINCIPLES.md §3 Learn once, never forget](00_PRINCIPLES.md). Hidden gestures don't survive a multi-week gap between sessions.

**Evidence:** [Nielsen Norman Group — The Problem with Invisible UI](https://www.nngroup.com/articles/navigation-hidden/). Every app that hides primary actions in gestures gets 1-star reviews asking "how do I do X."

**Fix:** pair every gesture with a visible control. The gesture is a shortcut for power users; the control is the contract.

### Overflow menu as dumping ground

**Pattern:** the `⋯` (overflow) menu contains 14 items, three of which are primary actions.

**Why it fails:** overflow is for *rare* actions. If an action is used more than ~20% of the time, it's not overflow; it's a button.

**Fix:** move frequent actions into primary UI. Overflow has ≤ 5 items, all genuinely secondary.

### Mystery icons without labels

**Pattern:** a row of 6 icon buttons at the bottom of a card. No labels. No tooltips on mobile.

**Why it fails:** icon semantics are fuzzy even at best (see [NNG — Icons need labels](https://www.nngroup.com/articles/icon-usability/)). Abstract glyphs are guessing games.

**Fix:** icons always have text labels (below, for bottom nav) or explicit `aria-label` + tooltip on desktop. If an icon is too abstract to need a label, replace the icon.

### "Tutorial" modals on first load

**Pattern:** a 4-step overlay tutorial on first open. Users tap through without reading.

**Why it fails:** users tap through onboarding because they want to start using the app, not learn about it.

**Evidence:** [NNG — Mobile app UX: 5 rules for welcome screens](https://www.nngroup.com/articles/mobile-app-onboarding/) (common failure mode: attention drops at step 2).

**Fix:** surface the 1–2 most critical patterns in-context the first time they're relevant (e.g. first time a user lands on Deck Builder, a small contextual tip for the leader picker). Never a full-screen overlay.

---

## Responsiveness failures

### Spinner for every action

**Pattern:** tap a button; spinner shows for 500ms every time, whether the action takes 50ms or 5s.

**Why it fails:** the spinner becomes noise. Users stop looking at it to verify completion. When something actually fails, they don't notice.

**Evidence:** [NNG — Response Times: The 3 Important Limits](https://www.nngroup.com/articles/response-times-3-important-limits/). Under 1 second, no feedback needed beyond the state change; over 1 second, show progress.

**Fix:** show spinner only after 150ms of pending, and only if the operation isn't optimistic. See [04_PRIMITIVES.md §Button loading state](04_PRIMITIVES.md#loading-state).

### No optimistic UI

**Pattern:** tap "Add to watchlist," wait 400ms for the API, then the star fills.

**Why it fails:** feels laggy on good networks; broken on slow ones. INP regressions compound across every interaction that requires a round-trip.

**Fix:** flip the star immediately. Revert + show error toast on failure. The network is async; the UI is synchronous. See [06_PERFORMANCE.md §INP](06_PERFORMANCE.md#inp-the-feel-metric).

### Full-page reload after action

**Pattern:** user adds a card to deck; the whole deck-builder page re-renders and loses scroll position.

**Why it fails:** mobile users rely on scroll position to orient. Losing it after every action breaks flow.

**Fix:** update just the changed component. Svelte 5 runes + reactive state handle this naturally — don't undo it with a `goto($page.url.pathname)` refresh.

### Locked UI during save

**Pattern:** while saving a deck, the whole sheet is disabled with a spinner overlay. User can't cancel, scroll, or see what they typed.

**Why it fails:** they might realize the save was a mistake. They should be able to abort. If they can't, the UI feels hostile.

**Fix:** only disable the save button itself. The rest of the form remains readable. Show progress inline. Offer a Cancel/Abort on long saves.

---

## Mobile-specific failures

### Tiny tap targets

**Pattern:** 16×16 icons with no padding. Text links with 14px height.

**Why it fails:** fingers are bigger than cursors. 40%+ mis-tap rates on targets under 44px.

**Evidence:** [Apple HIG — Layout](https://developer.apple.com/design/human-interface-guidelines/layout) (44pt), [Material — Touch targets](https://m3.material.io/foundations/designing/structure) (48dp), [WCAG 2.5.8 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html) (24×24 floor).

**Fix:** 44×44 CSS pixel minimum, 48×48 comfort. See [04_PRIMITIVES.md](04_PRIMITIVES.md#button) and [05_ACCESSIBILITY.md](05_ACCESSIBILITY.md).

### Primary action in red zone

**Pattern:** the "Save Deck" button is in the top-right corner. Users must shift grip to tap it.

**Why it fails:** one-handed use breaks. On large phones, top-right requires thumb stretch or a grip shift.

**Evidence:** [Parachute Design — Thumb Zone UX](https://parachutedesign.ca/blog/thumb-zone-ux/). [Smashing Magazine — Designing for One-handed Use](https://www.smashingmagazine.com/2016/09/the-thumb-zone-designing-for-mobile-users/).

**Fix:** primary action bottom-center-ish in the green zone. Top-right is for passive status and close buttons.

### Keyboard covers input

**Pattern:** user taps into a text field at the bottom of the screen; iOS keyboard opens and covers it.

**Why it fails:** user can't see what they're typing. Common in dialogs, sheets, and chat-like UIs.

**Fix:** on focus, scroll input into view; use VisualViewport API to track keyboard height. See [01_MOBILE_PWA.md §Virtual keyboards](01_MOBILE_PWA.md).

### Fixed position fighting the keyboard

**Pattern:** a "Save" button is `position: fixed; bottom: 16px;` — so it sits on top of the keyboard when input is focused.

**Why it fails:** covers the keyboard's row of predictions, or floats oddly.

**Fix:** on keyboard open, change the button's positioning to `static` or move it into the keyboard's "accessory area" (via VirtualKeyboard API on supported browsers).

### Disabling pinch-zoom

**Pattern:** `<meta name="viewport" content="... user-scalable=no">`.

**Why it fails:** users who need to zoom (low-vision, translation overlay, etc.) can't. WCAG 1.4.4 failure.

**Evidence:** [WCAG 1.4.4 Resize Text](https://www.w3.org/WAI/WCAG22/Understanding/resize-text.html).

**Fix:** never disable pinch zoom globally. If a specific surface (canvas, map) needs to own pinch, scope it to that surface.

---

## Copy failures

### Exclamation marks as excitement

**Pattern:** "Deck saved!", "Card added to watchlist!", "You're all set!"

**Why it fails:** [00_PRINCIPLES.md §1 Class, not hype](00_PRINCIPLES.md). Exclamation marks read as manic. Every status message shouldn't be a celebration.

**Fix:** state facts. "Deck saved." "Added to watchlist." "Ready."

### Jargon-salad button labels

**Pattern:** "Commit," "Submit," "Process," "Execute." Generic verbs with no object.

**Why it fails:** user doesn't know what will happen.

**Fix:** verb + object. "Save deck," "Add card," "Send message."

### Empty-state copy that says nothing

**Pattern:** a "No results" screen with no explanation.

**Why it fails:** user doesn't know whether their filter is too strict, their data is missing, or the app is broken.

**Fix:** empty state explains *why* empty and what to do. "No cards match these filters. Try widening the color selection or clearing text search."

### Apologizing for errors that aren't errors

**Pattern:** "Oops! Something went wrong." on a valid empty state.

**Why it fails:** teaches users that "error" messages are noise. Real errors get ignored.

**Fix:** empty ≠ error. Use neutral copy.

### "Unleash / seamless / powerful"

**Pattern:** marketing-speak in product UI.

**Why it fails:** users can't parse it; it sets off BS-detector reflex.

**Fix:** describe what the thing is or does. "Search all cards" beats "Unleash the full power of our database."

---

## Motion failures

### Everything animates

**Pattern:** every state change has a 400ms animation. Card taps animate. Scroll snaps animate. Typing an input field animates.

**Why it fails:** the interface feels slow. Users type faster than animations; they want results, not a show.

**Fix:** animate *state changes that need communication* (modal open, page transition, toast appear). State changes that are self-evident (typing, scrolling, toggle) snap or cross-fade under 100ms.

### Long entrance, no exit

**Pattern:** modal slides in over 400ms, then just disappears on close.

**Why it fails:** asymmetric motion feels broken. The slower exit is missing.

**Fix:** entrance should be slower than exit. Entrance 250ms, exit 180ms. Users want content fast and dismissal faster. See [04_PRIMITIVES.md §Sheet physics](04_PRIMITIVES.md).

### Animated progress that outpaces reality

**Pattern:** progress bar that fills in 2s when the actual operation takes 0.5s.

**Why it fails:** users wait for the bar, not the work. Twice the perceived latency.

**Fix:** progress bars track real progress. If you don't have real progress, use an indeterminate spinner (and show only after 150ms).

### Bounce/wobble on everything

**Pattern:** modal bounces on open. Card tile wobbles on tap. Everything has spring physics.

**Why it fails:** physics-based motion is delightful for drag-release and discretely used animation. Applied everywhere, it's distracting.

**Fix:** spring physics for drag release only. Everything else uses ease-out. See [00_PRINCIPLES.md §6 Calm motion](00_PRINCIPLES.md).

### Parallax on scroll

**Pattern:** background image moves at 50% scroll speed while content moves at 100%.

**Why it fails:** vestibular stress. Some users get nauseous within seconds.

**Evidence:** [WCAG 2.3.3 Animation from Interactions](https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html).

**Fix:** no parallax. Period. If you insist, gate on `prefers-reduced-motion: no-preference` and still don't do it.

---

## Forms failures

### Auto-submitting on last field

**Pattern:** user tabs past the last field; form submits automatically.

**Why it fails:** user may want to review before submitting. Removes user agency.

**Fix:** explicit submit button. Always.

### Clearing the form on any error

**Pattern:** validation fails; form clears all fields.

**Why it fails:** user has to re-type everything. Users have actually thrown phones over this.

**Fix:** preserve input. Highlight the bad field. Allow correction in place.

### Real-time validation on first keystroke

**Pattern:** user starts typing an email; "Invalid email" appears after the first letter.

**Why it fails:** validates before the user has finished. Feels like nagging.

**Fix:** validate on blur or on submit. If you must validate live, wait until the field has content that could plausibly be complete (e.g. contains `@` for email).

### Errors that don't tell you how to fix

**Pattern:** "Invalid input." "Error." No detail.

**Why it fails:** user guesses at the cause.

**Fix:** error describes what's wrong and the fix. "Target price must be a positive number (e.g. 25.00)."

### Placeholder as label

**Pattern:** no `<label>`; placeholder says "Email address."

**Why it fails:** placeholder disappears on focus. User who looks away loses context. Screen readers may not read placeholders consistently.

**Evidence:** [WCAG 3.3.2 Labels or Instructions](https://www.w3.org/WAI/WCAG22/Understanding/labels-or-instructions.html). [NNG — Placeholder attribute is not a label](https://www.nngroup.com/articles/form-design-placeholders/).

**Fix:** always a visible `<label>`. Placeholder is a hint (optional), not a substitute.

---

## Data & transparency failures

### Price without source

**Pattern:** "$24.99" with no indication of where it came from or when.

**Why it fails:** [00_PRINCIPLES.md §4 Transparency over magic](00_PRINCIPLES.md). Users making real financial decisions need to know the source and freshness.

**Evidence:** the Collectr 1-star reviews on the App Store repeatedly cite "prices are way off" ([justuseapp.com/en/app/1603892248/collectr](https://justuseapp.com/en/app/1603892248/collectr)).

**Fix:** every price has `source` and `verifiedAt`. "TCGPlayer · 2h ago."

### Empty state that hides "why"

**Pattern:** "No data" on an empty watchlist.

**Why it fails:** user doesn't know whether the watchlist is empty because they haven't added anything, or because a sync failed.

**Fix:** empty state explains. "Your watchlist is empty. Tap the star on any card to watch it."

### Error state that swallows details

**Pattern:** "Couldn't load data" with a retry button. No detail on why.

**Why it fails:** on recurring errors, user has no clue if it's offline, auth expired, or a server bug.

**Fix:** error includes a hint. "Couldn't load — you're offline. Will retry when connection returns." Or "Couldn't load — please sign in again."

### AI suggestion without confidence

**Pattern:** Miru suggests "Add Sabo to your deck" with no reasoning shown.

**Why it fails:** user can't evaluate the suggestion. Trust is low.

**Fix:** every suggestion shows its basis. "Miru suggests Sabo: 42% of top-8 Law decks in the past 30 days include him. Tap to see tournament data."

---

## Navigation failures

### Multi-level back

**Pattern:** user taps back and lands on a page they didn't come from, because intermediate redirects are in the history stack.

**Why it fails:** predictability breaks.

**Fix:** use `history.replace` for redirects that shouldn't be in the stack. Keep `push` for intentional navigation.

### Bottom nav that disappears unexpectedly

**Pattern:** BottomNav is visible on tab roots, disappears on sub-pages, reappears in some sheets but not others.

**Why it fails:** location ambiguity.

**Fix:** consistent rule — BottomNav on tab roots only. See [03_SUB_PAGE_ARCHITECTURE.md](03_SUB_PAGE_ARCHITECTURE.md#tabs-versus-sub-pages).

### Stacked modals

**Pattern:** a modal opens a modal.

**Why it fails:** dismiss becomes ambiguous. Two backdrops. Two focus traps. Chaos.

**Fix:** close first, open next. See [03_SUB_PAGE_ARCHITECTURE.md §Navigation anti-patterns](03_SUB_PAGE_ARCHITECTURE.md#navigation-anti-patterns).

### Broken back button

**Pattern:** tapping the browser/OS back button closes the app or returns to a white page.

**Why it fails:** basic OS contract violated. Users lose trust immediately.

**Fix:** every page has a valid entry in the history stack. Test with the OS back gesture on every route.

---

## Accessibility failures

### `outline: none` without replacement

**Pattern:** global CSS removes all focus outlines for "cleaner look."

**Why it fails:** keyboard users can't see where they are.

**Fix:** replace outline with custom focus ring. Never remove without replacement. See [05_ACCESSIBILITY.md §Focus](05_ACCESSIBILITY.md#focus).

### Color as the only indicator

**Pattern:** required fields shown only by a red border.

**Why it fails:** colorblind users (~8% of men, ~0.5% of women) don't perceive the cue.

**Evidence:** [WCAG 1.4.1 Use of Color](https://www.w3.org/WAI/WCAG22/Understanding/use-of-color.html).

**Fix:** red border + asterisk + "required" text or icon. Color is a reinforcement, not the message.

### Decorative icons announced by screen reader

**Pattern:** a gold `★` next to a watchlist item; VoiceOver reads "star, gold star, star, gold star" down the list.

**Why it fails:** noise.

**Fix:** `aria-hidden="true"` on decorative icons when an adjacent text label already conveys the meaning.

### Alt text that describes the image instead of the content

**Pattern:** card image with `alt="image of a card"`.

**Why it fails:** screen reader users hear "image of a card" and learn nothing. The actual content (the card's name, power, color) is invisible to them.

**Fix:** `alt="Monkey D. Luffy, red leader, 5000 power, cost 0"`.

---

## Dev-discipline failures (that the user feels)

### CLS after load

**Pattern:** page loads, content reflows as images and fonts resolve.

**Why it fails:** user's finger on the way to a button; the button moves; they tap something else. See [06_PERFORMANCE.md §CLS](06_PERFORMANCE.md#cls-layout-shift).

**Fix:** reserve space for everything. `width`/`height` on images. `size-adjust` on fonts.

### Bundle bloat

**Pattern:** importing a full library for one helper.

**Why it fails:** every KB is a millisecond on slow connections. Compounds.

**Fix:** [06_PERFORMANCE.md §JavaScript bundle discipline](06_PERFORMANCE.md#javascript-bundle-discipline).

### Console noise

**Pattern:** 40 console warnings on a typical page load.

**Why it fails:** real errors get lost. Users who open DevTools (power users, devs) see a mess.

**Fix:** zero warnings in production. Strict mode errors in dev.

### Broken offline

**Pattern:** app fails silently when offline; no indication.

**Why it fails:** user doesn't know why nothing works. [00_PRINCIPLES.md §9 Ship the edges](00_PRINCIPLES.md).

**Fix:** offline indicator at top of screen. Queued actions list. Clear path to retry when back online.

---

## The pre-ship five-minute audit

Before you merge UI work, run through this list. If any fire, pause.

1. Any exclamation marks in labels? → probably hype.
2. Any "urgent" copy without a real deadline? → fake urgency.
3. Any tap target under 44×44? → measure.
4. Any input with only a placeholder as label? → fix.
5. Any animation > 300ms on a state change the user just made? → shorten.
6. Any modal that opens another modal? → redesign.
7. Any gesture without a visible affordance or tap alternative? → add.
8. Any spinner that shows faster than the action completes? → delay with `setTimeout`.
9. Any price or claim without a source badge? → add.
10. Any error message that doesn't tell you how to fix it? → rewrite.
11. Any color-only indicator? → add icon/text.
12. Any `outline: none`? → replace.
13. Any fixed-px font size? → convert to rem.
14. Any CLS > 0.05 on the touched flow? → reserve space.

A dozen bullets. Five minutes. Worth it every time.
