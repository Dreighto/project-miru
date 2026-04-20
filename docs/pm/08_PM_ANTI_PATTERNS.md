# 08 — PM Anti-Patterns: TCG-Specific Failures We Refuse

**Applies to:** Every PM surface — watchlist, meter, card detail, deck builder, Miru layer, push notifications, copy, iconography, onboarding.
**Read this when:** Designing a feature that could nudge player behavior. Writing copy that describes price movement or meta trends. Implementing anything with a notification or badge.
**Skip this when:** You need universal anti-patterns (spinners, dark patterns, accessibility failures). Use [docs/ui_ux/08_ANTI_PATTERNS.md](../ui_ux/08_ANTI_PATTERNS.md) for that.
**Length:** ~7 pages.
**Related docs:** [docs/ui_ux/08_ANTI_PATTERNS.md](../ui_ux/08_ANTI_PATTERNS.md), [docs/pm/00_PRINCIPLES.md](00_PRINCIPLES.md), [docs/pm/03_MIRU_LAYER.md](03_MIRU_LAYER.md), [docs/pm/04_WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md).

---

## Why PM needs its own anti-pattern list

The universal anti-patterns doc covers craft-level failures: tiny tap targets, exclamation-marked copy, Framer Motion everywhere, placeholder-as-label. Those apply here.

But the TCG domain has a specific failure geometry: **money, scarcity, tournament results, variants, and community sentiment** all interact in ways that can be manipulated. Most of the patterns in this doc are **domain-specific ethical traps** where even a well-intentioned implementation can slide into manipulation. If we drift, we become a price-pump casino, not a sidecar. This list exists so drift is noticed early.

The structure mirrors the universal doc: category, pattern, why it fails, what we do instead.

---

## Category A — Investment-grade hype

### A1. Framing cards as "assets" or "investments"

**Pattern:** Copy that uses financial-product language. "Your portfolio is up 12% this month." "Grow your collection's value." "Unrealized gains: +$340." Charts styled like stock tickers with green/red arrows and percentage deltas.

**Why it fails:** TCG cards are collectible objects whose prices move based on gameplay impact, reprint risk, and community sentiment. Framing them as equities invites the emotional posture of retail investing — FOMO, check-the-ticker anxiety, and the belief that there's a "right" price to predict. The category of "investment-grade card" is real in very narrow cases (sealed Alpha, graded vintage). Nothing in OPTCG's 2022-onward print run qualifies. Treating all cards this way is dishonest and harmful.

**What we do instead:** Cards have **current market value** (cited to a source and timestamped). Watchlists have **targets you set**, not projected returns. When value changes, the language is factual: *"Down 8% this week on TCGPlayer, source last verified 2h ago"* — not *"losing value."* We never aggregate a "portfolio" into a single dollar figure. We never show green/red arrows as a default visual for price changes. Color indicates *target relationship*, not "good/bad."

---

### A2. The "what's hot" feed

**Pattern:** A surface (usually a home tab or a notification) that highlights cards with rising prices, trending tournament results, or social buzz. Optimized for "see what's moving."

**Why it fails:** "Hot" is FOMO in a T-shirt. The natural behavior it rewards is impulsive buying/trading based on short-term movement. Players who already own the card feel clever; players who don't feel behind. Neither is a good state to design for. Also, "hot" tends to get louder over time — the feed has to keep escalating to stay interesting, and the app ends up screaming.

**What we do instead:** The Home tab shows *your* watchlist status, your saved decks, and Miru's observations scoped to **what you've told us you care about**. If Miru notices a card you watchlist has crossed its target, that's a signal *you asked for*. If a card you don't own and didn't watchlist is moving, it's not on your screen.

---

### A3. "Don't miss this" / "Last chance" urgency

**Pattern:** Countdown timers, flash-banner copy, "price expected to rise," "rarity-adjusted alert."

**Why it fails:** These are classic retail dark patterns. They don't describe reality; they manufacture a feeling. For a TCG player, there's almost never a real deadline — the next print, the next tournament, the next set will reshape the market regardless. Urgency copy in a TCG app is nearly always a lie.

**What we do instead:** If a genuine deadline exists (a tournament registration close date, a banlist change announcement), state it flatly with the date. Never extrapolate "expected" movements. Never use countdown timers for card prices. Never imply Miru can predict the market — the explicit rule in [03_MIRU_LAYER.md](03_MIRU_LAYER.md) is *"Miru describes, never predicts."*

---

## Category B — Price-pump and influence dynamics

### B1. Push notifications on price rises with no target set

**Pattern:** Unsolicited pushes saying *"Luffy Leader is up 20% this week — watch now?"* with a one-tap "Add to watchlist."

**Why it fails:** Apps that push rising prices to uninvolved users cause the prices to keep rising. This is a small-market pump. It doesn't matter if it's accidental — the harm is real. We would be an accelerant of volatility for the community that uses us.

**What we do instead:** Push only on **user-configured target hits** (see [04_WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md)). Never on price *rises* absent a target. Never on "trending" cards. The only unsolicited push we would consider is "a card you watchlist has no price source right now" (degraded state notice), and even that is opt-in.

---

### B2. "Buy now" affordance from price screens

**Pattern:** A "Buy on TCGPlayer" / "Buy on eBay" button on every card detail page. Click-out affiliate revenue as a business model.

**Why it fails:** This turns every price observation into a funnel. The app becomes a shopping surface even when the player opened it to check collection status. Over time, every design decision tilts toward "increase click-outs" because that's the revenue signal. This is how Collectr reviewers describe feeling funneled into purchases. We refuse this slope.

**What we do instead:** Prices are informational. Sources are cited and linkable for verification, not conversion. A "view on source" link exists in a detail drawer, not as a primary action. If we ever add affiliate links, they are clearly marked, never a default button, never influence what prices we surface.

---

### B3. Influencer-driven meta without citation

**Pattern:** Surface "top decks" or "hot cards" based on what content creators are playing/posting, without naming the creator or the post.

**Why it fails:** TCG metas are heavily influenced by creators with large audiences. Laundering that influence through an "algorithm" makes the influence invisible. Players think they're seeing a neutral meta reading when they're seeing one creator's take amplified.

**What we do instead:** If a deck signal comes from creator content, say so: *"Based on 4 featured lists from [creator], over the past 7 days."* Prefer **tournament results** (where skin is in the game) to content signals (where follower count rewards noise). If we ever show creator-driven signals, the creator is named, the source URL is linked, and the signal is clearly distinguished from tournament data.

---

## Category C — Variant chase manipulation

### C1. Visual "shininess" for SEC/SR/SP variants

**Pattern:** Animated gradients, sparkle particle effects, or gold-metallic shimmer applied to rare-variant card tiles.

**Why it fails:** It's a casino visual vocabulary. The effect is chosen for *dopamine*, not information. A player scrolling past a SEC they don't own gets a small hit of "rare thing nearby." The visual language of gambling apps is not accidental — it's designed to trigger reward-seeking. We do not borrow it.

**What we do instead:** Rarity is indicated with a **subtle tint** on the RarityChip (see [02_PM_PRIMITIVES.md](02_PM_PRIMITIVES.md)) — a single color difference, no motion, no shimmer. The information is present; the hype is not.

---

### C2. Variant completion meters and "collection %" as a default surface

**Pattern:** A progress bar on a set page showing *"73% complete — 14 cards to go!"* with the missing cards highlighted.

**Why it fails:** Completion-as-goal reframes the hobby around *owning all the things*, which is a financial and psychological commitment most players haven't agreed to. For rare variants (SEC, SP, alt-art), "completion" can cost thousands of dollars. Presenting that as a visible progress bar is manipulative by default.

**What we do instead:** Collection views (when we build them) default to *what you own*, not *what you're missing*. A "show missing" toggle exists for the player who wants that view, but it is off by default. Completion percentages are never the header of a set page. Set pages show *cards in this set*; what you own is an overlay the player can request.

---

### C3. "You're so close!" / "Just 3 more!" nudges

**Pattern:** Copy that pushes toward completing a set, collecting all variants of a leader, or owning all tournament-winning cards.

**Why it fails:** Scarcity + progress + social pressure is the exact formula of loot-box design. Applying it to a hobby that already has financial-risk surface is harmful.

**What we do instead:** Never write progress-pressure copy. State what's true. A player can ask Miru *"what's missing from my [set]?"* and get a factual answer. Miru does not volunteer that framing.

---

## Category D — Tournament result sensationalism

### D1. Single-tournament "the new meta" claims

**Pattern:** A single regional or local event wins with a surprise deck → the app headlines *"Black/Green Kaido is the new meta"* the next morning.

**Why it fails:** TCG metas shift over weeks of aggregated tournament data, not overnight. One event is one signal. Shouting from a single data point is how rumors start and how players make bad buying decisions based on noise.

**What we do instead:** Meta claims require a **sample size floor** (see [03_MIRU_LAYER.md](03_MIRU_LAYER.md)). A single tournament result is shown as *"This deck top-8'd at [event] on [date]"* — a fact, not a trend. A trend requires N tournaments and M weeks, and the denominator is shown: *"Appeared in 14 of 48 top-8s over the last 30 days."*

---

### D2. "Meta share" charts without a time window

**Pattern:** A pie chart labeled "Current meta" showing leader color percentages, with no indication of the time range or tournament count.

**Why it fails:** "Current" is a weasel word. Without the window, the chart is unfalsifiable. It could be last week or last year; the reader has no way to know. We've seen this in multiple TCG sites. It erodes trust.

**What we do instead:** Every meta chart shows **window + n**: *"Top-8 appearances, past 30 days, 48 tournaments, 384 decks."* If the window isn't specified, the chart is incomplete and doesn't ship.

---

### D3. "Winning deck" hero pages with no mulligan plan or matchup data

**Pattern:** A prominent "Tournament winner" featured deck with just the 50-card list and no explanation of how it plays, what it loses to, or what skill is required to pilot it.

**Why it fails:** Winning deck lists without context are traps. New players see the list, buy the cards, pilot it poorly, and bounce off the game. The deck list is the least useful part of a tournament winner — the *how* matters more than the *what*.

**What we do instead:** A featured deck view includes: the list, the matchup notes (if available from the primer/creator), the mulligan priorities (if available), and the sample size of the tournament run. If those context layers are not available, the deck is shown as a list with a label *"No pilot notes available."* We never pretend we have context we don't.

---

## Category E — Leader tier-list absolutism

### E1. Static tier lists with single letters (S, A, B, C)

**Pattern:** A "Leader Tier List" page with each leader graded S/A/B/C, updated weekly.

**Why it fails:** Tier lists compress nuance — matchup dependency, meta position, skill floor, and variance — into a single ordinal letter. They are aggressively shared on social media because they're legible and provocative. They are almost always wrong at the edges (a "B-tier" leader can win a regional with the right pilot) and they entrench the perception that the current meta is permanent.

**What we do instead:** Leaders have **meta data cards**, not tier letters. Each leader page (when built) shows: top-8 appearances in window, average placement, common archetypes, matchup spread (if sample size allows). All cited. No aggregate letter grade. If a player wants a one-line summary, Miru can describe: *"Appeared in 6 of 48 top-8s over 30 days — lower than average for a 3-cost red."* Description, not verdict.

---

### E2. Ban-likelihood predictions

**Pattern:** Copy or UI that predicts whether a card will be banned in the next announcement. "Ban watch: 78%."

**Why it fails:** No model has this information. It's made up. Banlists are decided by a small Bandai committee based on factors the app cannot observe. Predicting bans is fortune-telling in a data-science costume.

**What we do instead:** We track **actual ban announcements** (the official source), show when they happened, and flag cards in decks that are affected. We never predict future bans. If a card is heavily represented in top-8s, that's a fact we can describe; the *"so it will be banned"* leap is not ours to make.

---

## Category F — Social pressure and engagement

### F1. Streaks, daily check-ins, and "don't break your chain"

**Pattern:** A daily-open streak counter. A push at 9 PM: *"You haven't checked in today!"*

**Why it fails:** Streaks are a behavioral trick borrowed from habit-forming gamified apps. They manufacture guilt for not opening the app. This is the opposite of a sidecar at locals — a sidecar is waiting when you need it, not nagging when you don't.

**What we do instead:** No streaks. No daily check-ins. No "you haven't opened this in a while" pushes. The app is quiet when it's not needed. (See [docs/ui_ux/08_ANTI_PATTERNS.md](../ui_ux/08_ANTI_PATTERNS.md) category on engagement guilt for the universal version; this is the PM-specific application.)

---

### F2. Public collection valuations / leaderboards

**Pattern:** A leaderboard of highest-value collections. Profile pages that show your total collection worth to other players.

**Why it fails:** Turns the hobby into a wealth display. Invites harassment, gray-market pressure, and privacy issues. Creates a competitive axis that has nothing to do with playing the game well.

**What we do instead:** Collection values are **private by default and private by design**. Not a setting you can toggle — an affordance that doesn't exist. If we ever add any social surface, it is deck-based (archetype + decklist + optional author), never net-worth-based.

---

### F3. "X players also added this" social proof

**Pattern:** *"127 players added this card to their watchlist this week!"* as a nudge to add it.

**Why it fails:** Social proof in a scarcity market accelerates price movement. Same mechanic as the push-pump from B1, slower but steadier. Also, it's often fake or fudged in competitor apps.

**What we do instead:** We do not surface aggregate user behavior to other users as a decision prompt. If Miru's observation layer references aggregated data (e.g., *"6 of the last 50 saved decks used this card"*), the denominator is visible and the framing is descriptive, not prescriptive.

---

## Category G — Data honesty failures

### G1. Prices without source + verifiedAt

**Pattern:** A price shown as a bare number. "$12.50."

**Why it fails:** The player has no way to know if that's current, where it came from, or whether to trust it. This is Collectr's central review complaint (see [07_OPTCG_STUDY.md](07_OPTCG_STUDY.md)). It's dishonest by omission.

**What we do instead:** Every price displays a **source badge** and **verifiedAt timestamp**. If the source is stale beyond a threshold, the price is dimmed and marked *"last verified 4h ago."* If no source is available, we show *"No source"* — never a fabricated number.

---

### G2. Silent filtering with no disclosure

**Pattern:** A "Cards" page that hides banned cards, dropped variants, or low-confidence entries without telling the player.

**Why it fails:** The player expects the page to show "all cards." They can't find one they're looking for. They don't know it's filtered. They assume the data is wrong or the app is broken.

**What we do instead:** When Miru applies a filter, a thin notice bar at the top of the list says *"Filtered: X cards hidden (banned or unreleased). Show all."* Filters are always disclosed and always clearable. See [03_MIRU_LAYER.md](03_MIRU_LAYER.md) — the "ambient filtering with disclosure" pattern.

---

### G3. AI/ML claims without explainability

**Pattern:** *"Our AI recommends these cards for your deck."* Click → list of cards. No "why."

**Why it fails:** Unexplainable recommendations are either unverifiable (you have to trust them on faith) or wrong (and the player has no way to correct or dismiss the error). Miru is explicitly described as *not a black-box recommender* — every observation has a visible source and reasoning. See [03_MIRU_LAYER.md](03_MIRU_LAYER.md) for the explainability contract.

**What we do instead:** Every Miru suggestion shows its basis: *"Based on 6 top-8 Black/Red Luffy lists in the last 30 days."* If we cannot cite the basis, the suggestion does not ship.

---

## Category H — Onboarding and first-run anti-patterns

### H1. "Sign up to see anything" wall

**Pattern:** First-run gate: create an account with email and password before any content is visible.

**Why it fails:** A sidecar at locals isn't an account-first app. The value of Miru should be immediately visible, not gated behind a signup. Most TCG apps that wall aggressively have low retention because the onboarding cost is high and the perceived upfront value is low.

**What we do instead:** The app is usable without an account. Browsing cards, building a draft deck, checking a set — no login required. Accounts appear when persistence matters: *saving* a deck, setting a *watchlist*, enabling *push*. The login prompt is contextual: "Sign in to save this deck" — at the moment of need, not before.

---

### H2. Onboarding tour as a modal gauntlet

**Pattern:** 7 screens of "Welcome to [app]!" before the player can touch anything.

**Why it fails:** Forcing a player through a tour implies the app is too complex to use without instruction. That's an admission of UI failure. The player who wanted to check a price is now 90 seconds into marketing copy.

**What we do instead:** No full-screen onboarding tour. First-open drops the player onto the Home tab with useful content (empty-state copy teaches the primary action — see [01_TAB_LANDINGS.md](01_TAB_LANDINGS.md)). A one-line *"Tap any card to see details"* lives inline the first time if necessary. Learn-through-use, not learn-through-modal.

---

### H3. Permission prompts at launch

**Pattern:** Asks for push, notifications, location, camera permissions immediately on first open.

**Why it fails:** The player has no context for why we're asking, so the default answer is "deny." Once denied, re-asking is harder (OS-level friction), and we've blown our one chance.

**What we do instead:** Ask for permissions **at the moment they're needed**: push is asked when the player sets a target price; camera is asked when they tap "Scan card" (if we build scan). Location is probably never asked — if we add a "tournaments near me" feature someday, that's the ask moment. See [docs/ui_ux/05_ACCESSIBILITY.md](../ui_ux/05_ACCESSIBILITY.md) and the permission-timing patterns.

---

## The 10-question PM ethics gut-check

Before any PM feature ships, ask:

1. Does this copy or UI surface a deadline that isn't real?
2. Does it claim to predict card prices or meta shifts?
3. Does it pressure the player toward collecting more than they intended?
4. Does it surface "hot" or "trending" cards regardless of what the player watches?
5. Does it show a price, meta claim, or observation without source + verifiedAt + confidence?
6. Does it use casino/gambling visual language (sparkle, shimmer, particle effects) on rare items?
7. Does it push notifications for things the player didn't explicitly configure?
8. Does it show aggregate user behavior as a decision prompt to other users?
9. Does it rely on streaks, check-ins, or engagement guilt?
10. Would a new player, after using this for a week, report feeling **anxious** rather than **informed**?

A "yes" on any of these is a hard stop. Go back to the drawing board. [00_PRINCIPLES.md](00_PRINCIPLES.md) has the "class not hype" and "Miru describes, never predicts" principles this test operationalizes.

---

## When an anti-pattern is tempting

Some of these patterns **work** in the short-term engagement-metric sense. Streaks do increase DAU. Hot-card feeds do increase session time. Urgency copy does increase conversion on purchase-adjacent surfaces.

The reason we refuse them isn't that they're ineffective — it's that they're effective in the wrong direction. Our North Star isn't session time, DAU, or conversion. It's **trust, accept rate on Miru's observations, and low regret rate** (see [03_MIRU_LAYER.md](03_MIRU_LAYER.md)). Optimizing for engagement-guilt metrics *actively damages* the metrics we care about.

If an idea shows up in a sprint planning and it feels like any of the patterns above, name it out loud: *"This is the category D2 pattern. We rejected that."* The anti-pattern list is the shortcut — the principle behind it is the reason.

---

## What happens when we slip

If a shipped feature later reveals an anti-pattern we missed, the correction path is:

1. **Name it** in the postmortem — which category, which pattern.
2. **Remove or fix it** in the next release — don't leave it in "while we figure out the replacement."
3. **Add a new specific sub-pattern** to this doc so the next feature is warned.

This document grows. That's fine. The shape of PM-specific ethical failure is domain-specific, and the domain will evolve as OPTCG matures. The principles from [00_PRINCIPLES.md](00_PRINCIPLES.md) are the invariants; this list is the lived history.
