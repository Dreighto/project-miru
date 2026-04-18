# 04 — Watchlist and Meter

**Applies to:** the watchlist feature — adding / removing cards, target prices, meters, and push notifications. This is the core retention loop of PM.
**Read this when:** you're changing watchlist behavior, adding new meter kinds, designing notification copy, touching the target-price editor, or reviewing anything related to "alerts."
**Skip this when:** the task doesn't touch watchlist / meters / notifications.
**Length:** ~7 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [02_PM_PRIMITIVES.md §WatchlistStar, MeterBar](02_PM_PRIMITIVES.md), [docs/ui_ux/05_ACCESSIBILITY.md](../ui_ux/05_ACCESSIBILITY.md).

---

## The core loop

The watchlist + meter pattern is PM's retention loop. It's the one place we intentionally pull the user back — and we've designed it so that the pull is always earned (user-configured), always useful (specific data the user asked for), and never manipulative.

The loop:

1. User taps **WatchlistStar** on a card. Card enters their watchlist.
2. Optionally, user sets a **target price** for the card.
3. PM watches. When a price hits target, PM sends a **push notification** (if enabled): "Red Luffy dropped to $18. That's at your target."
4. User taps notification, lands on the card's detail with seller links. Or they ignore.
5. Next session, the meter on Home shows whether the target is still met, approaching, or drifted.

The value the user gets is: they don't have to poll prices. PM polls for them. That's the promise.

---

## The watchlist itself

### What a watchlist entry is

A watchlist entry is:

- A card (by code + variant).
- An optional target price.
- Optional notification preferences (email, push, or neither).
- An optional note ("for my Red Luffy deck").
- Added timestamp, last-hit timestamp (last time the current price moved by > 5%).

### What it isn't

- A wishlist for gifts. Users might call it that; the name is "Watchlist."
- A collection tracker. That's a separate future feature.
- A shopping cart. No purchase flow in PM.
- A feed or social object. Watchlists are private.

### Limits

- 200 cards per watchlist for free tier (if free tier exists at launch).
- 1,000 cards for any paid tier.
- Rationale: watching 1,000+ cards is a use case we don't serve well. Past that scale, users want filters and saved queries, not flat lists.

Rejection copy when limit hit: "Your watchlist is at 200 cards. Remove some to add new ones, or upgrade for larger watchlists."

> **Operator decision pending.** The free/paid tier structure is not yet decided. The 200/1,000 limits and the "upgrade" copy above are reasonable placeholders; they will be revisited once the tier model is settled. Don't remove the limits when implementing — build against them as defaults, and parameterize so the numbers and the upgrade copy can change without a rewrite.

---

## Target price editor

When a user taps to set a target price on a watchlist card, a sheet opens.

### Layout

```
┌─────────────────────────────────────┐
│        Set target price             │
│   Monkey D. Luffy · Current $24.99  │
│                                     │
│   ┌──────────────────────────┐      │
│   │  $ 22.00                 │      │
│   └──────────────────────────┘      │
│                                     │
│   [−]  Step by $1  [+]              │
│                                     │
│   Notify via: [Push] [Email]  ☐     │
│                                     │
│   [Save]                            │
└─────────────────────────────────────┘
```

### Input

- Numeric text input with `inputmode="decimal"` and a `$` prefix.
- Stepper buttons for coarse adjustment ($1 default, configurable to $5 / $0.10 in Profile).
- Current price displayed as reference.
- "Below current" hint (from Miru, with MiruGem): *"$22's about 12% below current. Miru's seen 8 sales at or below this in the last 30 days."* Non-urgent, informational.

### Validation

- Must be > 0.
- Show a warning (non-blocking) if target is unreasonable:
  - *"That's well below what we're seeing — want to double-check?"*
  - *"That's well above market — want to double-check?"*
- No hard caps; users can set whatever they want.

### Notification setup

- Push: uses web push (requires permission). See [01_MOBILE_PWA.md §Install flows](../ui_ux/01_MOBILE_PWA.md#install-flows) and below.
- Email: optional; requires confirmed email on account.
- Neither: the user wants a meter on Home but no push. Perfectly valid — PM respects it.

### Deleting a target

"Remove target" button — removes the target, keeps the card on watchlist. Separate from "Remove from watchlist."

---

## The meter

A meter is the visual summary of "where is this price now vs where the user wants it?"

### Anatomy

```
┌─────────────────────────────────────┐
│ ⭐ Monkey D. Luffy   $22 target       │
│                                     │
│   ───────●──────┃──────────────     │
│   $15         $22         $35       │
│                                     │
│ Now $18 · TCGPlayer · 2h ago        │
└─────────────────────────────────────┘
```

- Track runs from a minimum (usually 60% of target) to a maximum (usually 160% of target).
- Target marker (vertical line, gold) at the user's target value.
- Current marker (circle) at current price.
- Color of the circle reflects state:
  - **Green** — current ≤ target (target hit).
  - **Gold** — current is above target, within 20%.
  - **Muted** — current is far above target (> 20% over).
  - **Red** — current crosses far below target (> 20% below). Rare; signals possible data anomaly or major price movement.

### What the meter tells you at a glance

- Where the current price sits relative to target.
- Whether the target is "close to hit," "comfortably hit," or "way off."

### When the target is hit

- Circle turns green.
- On the first render of the meter after target is hit, a subtle gold underline pulses once (200ms). Once. Never again for the same crossing.
- Haptic `impact.heavy` if the user is looking at the meter when it crosses (rare but elegant — ties the moment of crossing to a physical signal).
- Push notification fires (if enabled). See below.

### When the target is missed

- Meter just shows current vs target. No warning, no urgency. The user sees the data; they decide if they care.

### Dormant meters

If the user hasn't interacted with a meter for 60+ days, it's demoted to a compact row without the bar. "Red Luffy · $18 · 2h · [View]" — saves screen real estate. User can re-promote by tapping.

---

## Push notifications

### The principles

1. **Every push the user sees is tied to a configuration they made.** No surprise pushes.
2. **Off by default.** Users opt into push when they set a target. Never at install time.
3. **Rate-limited.** Max 1 push per card per 24h (unless user explicitly sets "alert me every time"). Max 5 pushes per user per day across all cards.
4. **Dismissible globally.** Profile > Notifications > Pause all (for 24h, 7d, or indefinitely).
5. **Never for engagement.** No "haven't seen you in a while," no "check out this new card," no "daily digest."

### The target-hit push

**Copy template:**
> **[Card name] hit your target**
> Now $X · target was $Y · [source]

Example:
> **Monkey D. Luffy hit your target**
> Now $18 · target was $22 · TCGPlayer

- Tap: opens PM to the card detail sheet.
- Dismiss: clears the notification, no state change.
- No emoji. No exclamation. No "act fast" copy.

### The "approaching target" push (optional, off by default)

If the user enables it, a second push fires when price is within 10% of target (warning shot):

> **[Card name] close to your target**
> Now $X · target $Y · [source]

This is opt-in per card. Most users won't want it; some (active traders) will.

### The silent watchdog

Regardless of push preferences, PM records all price movements on the user's watchlist. The meter reflects the current state on the next session. This means the watchlist is still useful for users who turn off all push.

---

## Permission and friction

### When to ask for push permission

Only when the user explicitly tries to set a target price with push enabled. Never at install; never at signup.

**Request UI** (before the native permission sheet):
> **Enable push?**
> PM will notify you when a card on your watchlist hits your target price.
> [Not now] [Enable]

- Tapping "Enable" triggers the native permission sheet.
- Tapping "Not now" sets the preference to "ask again in 7 days."
- Permission denials remembered; we don't re-ask within 30 days.

### Why not nag

iOS and Android both degrade re-ask experience: if the user denies, browsers/OSes show a permanent "no" state until the user resets settings. Asking too soon burns the permission for life. Source: [web.dev — Permissions UX](https://web.dev/articles/permissions-ux).

### Unsubscribe anywhere

Any notification has:

- A path to "turn this off for this card" (via the push action, on supported platforms).
- Mention in Profile > Notifications.

One tap to turn off all alerts for a card. Two taps to turn off all PM push.

---

## The watchlist page (`/watchlist`)

The full watchlist is a sub-page under Profile.

### Layout

- Header: "Watchlist" title. Right-side: sort (By added / By target / By current) and filter (has target / no target / currently hit).
- List of watchlist rows.
- Pull-down search by card name.

### Row layout

Each row is a list row with:

- CardImage (32×44 thumbnail).
- Card name + set + variant indicator.
- Target price (if set) + meter (compact version).
- Current price + source + last-verified.
- Swipe-left: remove. Swipe-right: edit target.

### Sort defaults

Default sort: most-recently-hit targets first, then by how close to target. Meaning: the top of the watchlist is always "what might matter right now."

### Filters

- **All** (default).
- **Hit now** — target met currently.
- **Approaching** — within 10% of target.
- **No target** — watchlist items without a price goal.

### Bulk edit

Long-press a row → enters multi-select mode. Select multiple → bulk actions (remove, set target, duplicate target). Implements the checkbox pattern at 32×32 hit area.

### Empty state

"Your watchlist is empty. Watch a card to start tracking it."
CTA to the Cards tab.

---

## Edge cases

### Price source goes offline

If TCGPlayer is our price source and its API fails for 4+ hours:

- Stop sending target-hit notifications until source is restored. Avoid "false hit" pushes off stale data.
- Meter rows show a warning caption: "Prices haven't updated in X hours."
- Target-hit meters don't flip green until fresh data confirms.

Rationale: a notification is a promise. We don't keep it with bad data.

### Price crosses target multiple times in a day

If a price oscillates around target (it happens on volatile cards):

- First crossing: push fires.
- Subsequent crossings within 24h: no push. Meter reflects state.
- Rationale: user asked to know when it hit. Repeated pushes train them to ignore.

### Variant differences

Target prices are per-variant. Watching "Monkey D. Luffy OP01-001 (Special)" is a different item from watching "Monkey D. Luffy OP01-001 (Alt Art)." The UI distinguishes with small variant indicators next to the card code.

**WatchlistStar interaction with swipe-for-variants.** On a CardTile that supports variant-cycling (swipe left/right — see [05_GESTURES_PM.md](05_GESTURES_PM.md)), the WatchlistStar's filled/unfilled state reflects *the currently displayed variant*, not the base card. Cycle to a variant you haven't watchlisted and the star appears unfilled; tapping it adds that specific variant as its own watchlist entry. This matches the rule above: each variant is a distinct item with its own target price, its own meter, and its own push subscription.

### Currency

Prices stored in the source's native currency (usually USD for TCGPlayer). Target price set in the user's local currency (configured in Profile). Comparison uses the latest FX rate, which is refreshed daily. Source attribution notes both currency and rate date.

### Price below zero / errors in data

- Card at "$0.00" from a source typically means no listings. Show "No listings recorded." Not a hit.
- Negative / impossible values: filter out silently; log the bad data for engineering.

---

## The Apple Watch / iOS Live Activity ethics

At some point, PM might ship:

- A live activity (iPhone) showing a target-hit meter as the price approaches.
- An Apple Watch complication showing the number of "close to target" cards.

These are non-standard extensions. Rules when we build them:

1. **Opt-in per card.** User chooses which cards show on Watch / in live activity.
2. **No red / urgent styling.** Even at-target, the visual is neutral.
3. **No streaks or counts of "decisions made."** Not "47-day price-watcher streak."
4. **Clear off switch.** One tap to dismiss from Watch.

We don't build Activity Ring-style psychological drivers. Rings work for health metrics where closing them is self-improvement. They don't belong in a TCG tool.

---

## The ethics table

| Pattern | Pros | Cons | Verdict |
|---|---|---|---|
| Streaks ("X days in a row watching prices") | Engagement | Manipulation; [00_PRINCIPLES.md §1](00_PRINCIPLES.md) | **Reject** |
| "You haven't checked in 5 days" push | Brings user back | No user benefit; nagging | **Reject** |
| Summary push ("3 cards moved today") | Informational | Noise if user doesn't care | **Defer** — only if user opts in |
| Anniversary push ("card has been on watchlist 1y") | Nostalgia | No utility | **Reject** |
| Target-hit push | User-configured, informational | Limited to rate | **Ship** |
| "Approaching target" push | Power feature | Could be noisy | **Ship**, opt-in per card |
| "Price changed > X%" push | Could be useful for volatile cards | Volatility varies a lot per card | **Ship**, opt-in per card, X user-configurable |
| In-app notifications list | Familiar pattern | Bloat if underused | **Defer** — until push + meter alone prove insufficient |

---

## Copy for the meter states

For screen readers and for any state labels:

- Meter at target: "Target hit. Current $18, target $22."
- Meter approaching: "Close to target. Current $23, target $22."
- Meter over by a lot: "$35, target $22. $13 above."
- Meter with no target: "Watching. Current $18. Set a target to get alerts."
- Meter with stale data: "Current $18 as of 2h ago. Data may be delayed."

Every state announces the actual numbers so screen-reader users get the same information as visual users.

---

## Testing the loop

Before shipping a change to watchlist/meter/notifications:

1. **Add to watchlist, tap-tap → card is watched.** Round-trip < 500ms.
2. **Set a target, price was within range → verify push fires.** Time to fire: < 5 minutes after the price check that confirms hit (depending on polling cadence).
3. **Change target → meter updates live on Home.**
4. **Deny push permission → target-hit still updates meter, no push.**
5. **Remove from watchlist → meter on Home disappears.** (Don't leave orphaned meters.)

If any step fails, the loop is broken — and the loop is the core retention mechanism.

---

## Why this matters

The watchlist + meter is the one feature in PM that reliably pulls users back without being manipulative. The rest is utility — players open PM when they need something. The watchlist is the only feature where *we* reach out to *them* because we have news they asked for.

Get it right, and it's the reason a player has PM installed. Get it wrong, and the app becomes another source of notification fatigue they mute within a week. The difference is discipline: only notify on what the user asked for, source every price, respect their time.
