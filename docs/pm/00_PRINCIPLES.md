# 00 — PM Principles

**Applies to:** every PM surface decision. These are the PM-specific principles that sit atop [docs/ui_ux/00_PRINCIPLES.md](../ui_ux/00_PRINCIPLES.md).
**Read this when:** first PM visit; you're debating a tradeoff that the universal principles don't resolve; you're writing copy or UI that references Miru or the player.
**Skip this when:** the decision is obvious from the universal principles.
**Length:** ~6 pages.
**Related docs:** [docs/ui_ux/00_PRINCIPLES.md](../ui_ux/00_PRINCIPLES.md), every other PM doc.

---

## The mental model: sidecar at a locals table

The canonical PM moment: a player is at their local game store. They're mid-match, or between rounds, or waiting for a teammate. They pull their phone out for **15 to 60 seconds**. They need one thing:

- "What's this card worth right now?"
- "Do I have this variant already?"
- "What does this leader's matchup look like?"
- "Which variant of Luffy is cheapest?"
- "Is this spike in price real, or a bot?"

They put the phone back in their pocket. They go back to the game.

PM exists for that moment. Every design decision defers to it. Features that require longer attention spans belong on a different surface (desktop web, detailed exports) or on a dedicated deep-session tab (Deck Builder is the longest PM session).

### What this means operationally

- **Bottom-tab nav with 5 tabs.** One hand. Clear jobs per tab.
- **One primary action per screen.** If the user opens the screen and doesn't know what it's for, the screen failed.
- **Cold-open is useful.** If the user opened PM, the very first thing they see is either what they last cared about (watchlist meters, trending prices) or a clear path to what they want.
- **No feed scroll obligation.** Home surfaces what the user has asked for — meters, watchlist movements, Miru suggestions tied to their decks. Not an infinite feed.
- **Fast.** 4G, old iPhone SE3, one-handed. Works. Doesn't feel like it's thinking.

### The anti-moment

The user opens PM, sees a tutorial modal, taps through, sees a promotion, taps through, sees a login prompt, taps through, is on the home screen, and the thing they wanted to check is three taps away. That's 20 seconds lost. They close the app. They won't open it at the next LGS visit.

Every tutorial, promotion, or gate between cold-open and core value *costs retention*. We don't add them.

---

## Six PM-specific principles

These layer on top of the nine universal principles. Order matters — they're listed in rough priority.

### 1. 15–60 second bursts, one-handed, bad wifi.

[docs/ui_ux/00_PRINCIPLES.md §2](../ui_ux/00_PRINCIPLES.md) says design for one hand, 15-second bursts, bad wifi. In PM this is *the* constraint, not one of many.

**How it shows up:**
- Primary actions are thumb-reachable. See [docs/ui_ux/05_ACCESSIBILITY.md §Tap targets](../ui_ux/05_ACCESSIBILITY.md).
- Card images load fast — AVIF, right size, lazy below fold. See [docs/ui_ux/06_PERFORMANCE.md](../ui_ux/06_PERFORMANCE.md).
- State persists locally — closing and reopening the app puts the user back where they were.
- Deck drafts, watchlist changes, and favorites save to localStorage/IndexedDB immediately, sync later.

### 2. Learn once, never forget.

We use PM patterns designed so that a player who returns after a month still remembers what tap, swipe, and long-press mean.

**How it shows up:**
- The PM gesture table ([docs/pm/05_GESTURES_PM.md](05_GESTURES_PM.md)) is small and canonical. We never add a PM-wide gesture that exists only in one tab.
- Tab labels don't change based on state. "Deck Builder" is always "Deck Builder."
- Icons are paired with text labels on BottomNav.
- Hidden features are for power users and always have a tap path.

### 3. Miru works, Miru doesn't shout.

The AI layer (we call "Miru" — the product voice is the assistant's voice) does work in the background and surfaces results only when the user looks. It doesn't announce itself.

**How it shows up:**
- Pre-filters. When a user lands in Deck Builder with a leader picked, the card pool is already filtered to the leader's colors. No banner says "we filtered this for you."
- Suggestions, never decisions. Miru says "consider these 5 cards" with a confidence score. It never says "I added these 5 cards."
- Explainable every time. Tap any Miru output and see why — which decks, which tournaments, which sources.
- No purple chrome everywhere. Purple is reserved for Miru output. If every screen is purple, the accent means nothing.

More in [03_MIRU_LAYER.md](03_MIRU_LAYER.md).

### 4. Transparency over authority.

PM aggregates price data, meta data, tournament data. We are never the authority — we're a lens on sources that the user trusts (or verifies).

**How it shows up:**
- Every price has `source` and `verifiedAt`. "$24.99 · TCGPlayer · 2h ago." See [02_PM_PRIMITIVES.md §Price components](02_PM_PRIMITIVES.md).
- Every "top deck" has a link to where it came from (Egman tournament report, /r/OnePieceTCG thread).
- Every Miru suggestion shows confidence and basis.
- When we don't know, we say so. "Miru doesn't have enough data on this card yet."
- No "our predictions" language. "Based on 42 top-8 decklists in the last 14 days, this card is in X% of them" — the data says it, not Miru.

### 5. Class, not hype.

The OPTCG world has enough hype. Price pumps, FOMO threads, "investment grade" nonsense. PM doesn't play.

**How it shows up:**
- Neutral copy. "Price changed." Not "Price exploded!" or "🔥 HOT CARD 🔥."
- No rarity theater. Secret Rare, Alt Art, etc. get a quiet chip, not a gold-gilded animation.
- No countdown timers on evergreen items.
- No "3 people viewing this now" fake social proof.
- Push notifications: rare, earned, off-by-default on first install.

### 6. Owned by the player.

PM stores the player's data, which is their watchlist, deck drafts, and collection (when we get to it). The player owns this — we hold it.

**How it shows up:**
- Every datum exportable (JSON/CSV).
- Account deletion is one tap + one confirm. Purges everything.
- No "downgrade friction" on paid tiers (when applicable) — if the user cancels, they keep what they built.
- Sync is a feature, not a lock-in. Local-first; cloud is a convenience.

---

## The PM pronouns

**"You"** — the player. Always the subject.

**"Miru"** (見る — Japanese "to see, to watch, to look") — the engine that runs PM. Miru watches data, meta, prices, releases. Miru also has a voice, but it isn't the narrator of the whole app. PM's infrastructure copy — buttons, errors, card labels, stat readouts — is direct and plain. Miru only speaks through specific surfaces: observations, suggestions, the companion feed, tiered disclosure on leaders. Refer to Miru as an *it*, not a *she* or *he* or *they-person*. Miru has personality but no face, no mascot, no conversational interface. When Miru speaks, Miru sounds like **the regular at your LGS** — someone who's been playing since OP01, knows the meta cold, is warm with newbies, excited about OPTCG but calm about it. Never a hype machine, never a bro, never a jerk about it.

**"We"** — the team behind PM. Used sparingly; mostly in legal/settings contexts. The product voice is Miru speaking, not we-the-company.

### The three tiers of copy

Every PM surface sits in one of three tiers. The tier determines the voice.

**1. PM infrastructure copy** — direct, factual, no character. Buttons, errors, price labels, stat readouts, form fields.
- *"Red Luffy · $18 · TCGPlayer · 2h ago"*
- *"Deck saved."*
- *"Price unavailable — no listings recorded."*

**2. Miru speaking (when invited)** — LGS-regular tone, warm but calm. Appears only with the MiruGem + labeled surfaces.
- *"Red Luffy's been climbing lately — 8% to 11% meta share over the past week. 42 top-8 lists."*
- *"Worth keeping an eye on. Showing up in more Red builds than it was a month ago."*
- *"Not enough tournament data on this card yet. Miru will have more to say once 10+ lists include it."*

**3. Never** — hype copy, explicitly forbidden. Appears nowhere, in any tier.
- *"🔥 HOT CARD ALERT 🔥 Red Luffy is SURGING!"*
- *"Don't miss out! Act fast!"*
- *"Our AI-powered algorithm recommends…"*
- *"Congratulations! You've built 3 decks this week! Keep it up!"*

The test: if you read Miru's copy out loud and it sounds like either a robot OR a carnival barker, it's wrong. It should sound like the person who knows the meta cold and would never be a jerk about it.

---

## The one-hand test

For every PM surface:

1. Hold the phone in your non-dominant hand. (Left, if you're right-handed.)
2. Do what the surface asks. Can your thumb reach the primary action? The back button? The tab you need to go to next?
3. If any answer is no, the layout fails — redesign.

The test catches:
- Titles in the top-right (fine — users don't tap titles).
- Primary actions in the top-right (fail — thumb doesn't reach).
- Nav bar at the top (fail — user has to shift grip).
- Dense checkbox lists with 30px rows (fail — mis-tap rate is huge).

We do this before every ship. It catches 80% of mobile problems before QA.

---

## The at-locals test

Once per quarter, we do a walk-through simulating the actual context:

1. Stand at a crowded card shop or café.
2. Phone in one hand, a drink or cards in the other.
3. Open PM cold.
4. Do one task ("check if I have a copy of Monkey D. Luffy (OP01-001) in my watchlist").
5. Close the app.
6. Time it. Note friction points.

Target: < 12 seconds for a first-order task (check price, toggle watchlist, open a saved deck). If longer, there's a design issue.

---

## The 60-second test

After shipping any PM change that touches core IA:

1. Open the app.
2. Attempt the flow you just changed.
3. Time yourself.
4. Do it three times. Average.

If the average is > 60 seconds for a one-task flow, the task has too many steps, the surface is wrong, or the user needs context we didn't provide. Fix before we call it shipped.

---

## How PM handles disagreement

If two team members disagree on a PM design decision, the tiebreakers in order:

1. **The nine universal principles** ([docs/ui_ux/00_PRINCIPLES.md](../ui_ux/00_PRINCIPLES.md)).
2. **These six PM principles** (the one that's most at stake wins).
3. **A 1-star review from the domain.** An actual user complaint beats an internal intuition.
4. **A competitive benchmark** — Linear, Apple, etc., per the evidence hierarchy.
5. **Operator directive** (CLAUDE.md).
6. **Taste.** Only if 1–5 are silent.

If after all that there's still disagreement, escalate to Claude Chat (the Lead Architect, see `CLAUDE.md`).

---

## The PM craft bar

Before shipping any PM surface, five questions:

1. **Does it work in 15 seconds, one-handed, on 4G?**
2. **Can a player who last used the app two months ago reach the primary action without re-learning?**
3. **If Miru is involved, does the player see the work without the announcement?**
4. **Does every data point show its source?**
5. **Would a thoughtful OPTCG player share a screenshot of this screen and feel it represents them well?**

If any answer is no, loop back. The bar isn't "ship before Friday" — it's "respect the player's time and intelligence."
