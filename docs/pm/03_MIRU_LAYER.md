# 03 — The Miru Layer

**Applies to:** every place Miru (the AI / assistance layer) speaks, suggests, filters, or intervenes.
**Read this when:** you're adding a Miru feature; you're adding any ambient intelligence, suggestion, or auto-fill to a surface; you're writing copy for Miru output.
**Skip this when:** the surface has no AI involvement whatsoever.
**Length:** ~8 pages.
**Related docs:** [00_PRINCIPLES.md §3 Miru works, Miru doesn't shout](00_PRINCIPLES.md), [02_PM_PRIMITIVES.md §MiruGem](02_PM_PRIMITIVES.md#mirugem), [06_DESIGN_LANGUAGE.md](06_DESIGN_LANGUAGE.md).

---

## What Miru is

Miru is the ambient-intelligence layer of Project Miru. It's the name of the assistant, the visual color accent (purple), and the way PM surfaces non-obvious observations.

Miru is:

- **A set of observations** pre-computed from tournament data, price data, decklist data, and the user's context (watchlist, decks, leaders).
- **A small visual signature** — the purple accent, the gem icon, specific typography for "Miru says" output.
- **A copy convention** — third-person, calm, short, always cited.
- **A composable layer** — Miru can appear as a note on Home, a filter pre-applied on Deck Builder, a suggestion in a card detail, or a confidence score on a price.

Miru is *not*:

- A chatbot. Miru doesn't converse.
- A mascot. No face, no avatar, no conversational interface. Miru has personality and voice, but no character to follow around.
- A decision-maker. Miru proposes; the player decides.
- A hype machine. Miru doesn't celebrate, doesn't exclaim, doesn't bait.

Miru *cares* about OPTCG — that can show in word choice. See the voice section below.

---

## Three types of Miru output

### 1. Ambient filtering ("Miru working")

The highest-respect form of Miru. Done silently.

**Examples:**
- Deck Builder pre-filters the card pool to the leader's colors. No banner. The color chips just reflect the pre-applied filter state, so a curious user can see what filters are active.
- Cards tab sorts newest-first because the user most recently searched within a recent set.
- A price on a card detail page automatically converts to the user's region's currency based on their settings.

**The rule:** Miru does the filter. The UI reflects the filter state so the work is legible. The user can change the filter. Miru never tells the user "I did this."

### 2. Observations ("Miru noticed")

Small, short, cited notes surfaced on relevant screens.

**Examples:**
- On Home: "Red Luffy builds have stabilized around 4× Monkey D. Luffy (OP01-001) in the last 7 days. You have 2 copies on your watchlist."
- On a card detail: "This card appears in 38% of top-8 Law decks in the past 30 days (42 lists, Egman)."
- On Deck Builder: "Your current deck is 6 cards below typical Red Luffy builds. Consider adding more Events."

**Format:**
- Purple gem icon (MiruGem) on the left.
- 1–2 sentence observation.
- Source + data count + timeframe in a muted caption.
- No exclamation marks, no "did you know," no urgency.

**Visibility rules:**
- Maximum one observation per screen at a time.
- Dismissible by swipe-right or tap-dismiss.
- Dismissals remembered for 24 hours for that specific observation.
- Observations with low confidence never shown (see Confidence below).

### 3. Suggestions ("Miru suggests")

Explicit proposals where the user can take action.

**Examples:**
- In Deck Builder: "Miru suggests adding these 5 cards. They appear in >40% of top-8 Red Luffy decks in the past 30 days."
- On the card detail of a card close to target price: "Miru suggests buying now — similar sellers have listed at $24–26 and sold within 12 hours."

**Format:**
- MiruGem + "Miru suggests" header.
- List of proposed actions. Each has: the object (card, price), a confidence score, a one-sentence reason, and a tap-to-act affordance.
- Every suggestion is dismissible. Dismissals inform future suggestions.
- Every suggestion shows its data basis. Never a black-box "trust us."

**Confidence levels (visible):**
- **High** (≥ 80% model confidence, > 50 data points): bold purple text.
- **Medium** (60–80%, 20–50 data points): normal purple text.
- **Low** (< 60% or < 20 data points): not shown. If Miru isn't confident, it doesn't speak.

> **Operator-confirmed placeholder — tune with real data.** The confidence threshold numbers (80/60; 50/20 data points) are placeholder values. They will be tuned once Miru runs on real data and we can see where accept rate and regret rate diverge from confidence score.

---

## The transparency contract

Every Miru output carries, visibly:

1. **Source.** "Based on 42 top-8 decklists from Egman, Nov 1–30."
2. **Freshness.** "As of Nov 30, 2026."
3. **Sample size.** "42 lists."
4. **Confidence.** High / medium. (Low never ships.)

If any of these is missing, the output doesn't render. We don't hide uncertainty.

### The "Miru doesn't know" state

When Miru lacks data to speak:

- On a card with < 10 tournament appearances: "Not enough tournament data on this card yet. Miru will have more to say once 10+ lists include it."
- On a new leader's matchup view: "Not enough match data yet (< 20 games). Miru will surface matchups when we have 20+."
- On Home for brand-new users: no Miru section at all. Miru silent until there's something to say.

This is a feature, not a fallback. Admitting ignorance is the primary way we earn trust.

---

## Miru voice

### Tone

Miru sounds like the regular at your local game store — warm, patient, knows the meta cold, never a jerk about it. Calm but not clinical; friendly but not a bro.

- **Warm, factual, concise.** Miru is the knowledgeable regular, not a hype man and not a dashboard.
- **Third person.** "Miru found 5 cards" / "Miru noticed" are common, but Miru can sometimes just say the thing — the purple gem and the labeled surface already signal it's Miru talking. Never "I" or "we."
- **No emoji in Miru copy.** The MiruGem is the visual signature; emoji would clash.
- **No capitalizations for emphasis.** No "HOT!" or "SALE!" Ever.
- **No urgency language.** No "act fast," "limited time," "don't miss." The data speaks; urgency is the user's to feel.
- **Warm word choice is welcome.** Words like *been, climbing, worth, interesting, solid, lately, showing up, keeping an eye on* read like a person pointing something out. They're not hype — they're how a regular talks.

### Sentence shape

Fact, source, (sometimes) a small observation. Two to three clauses.

**Good (warmer):**
> Red Luffy's been climbing lately — 8% to 11% meta share over the past 7 days. 42 top-8 lists. You're watching 2 copies.

**Also good (more clinical; fine when the context calls for it):**
> Red Luffy builds are converging. 94% of top-8 lists in the past 14 days include 4× Monkey D. Luffy (OP01-001). You have 2 copies on your watchlist.

**Bad:**
> 🔥 Hot trend alert! Red Luffy is dominating the meta right now and EVERY top deck is running 4× Monkey D. Luffy! You'd better grab more copies ASAP!

### Never say

These words are banned because they are marketing spam or they imply things Miru cannot honestly claim:

- "AI-powered"
- "Unleash"
- "Supercharge"
- "Revolutionary"
- "Game-changing"
- "Cutting-edge"
- "Machine learning"
- "Our algorithm"
- "Intelligent"
- "HOT" / "SURGING" / "EXPLODING" (or any all-caps emphasis)
- Exclamation points
- 🔥 / 🚀 or any emoji in Miru copy
- Predictions — *"expected to rise," "likely to be banned," "about to spike"*

The ban is on **hype and marketing language**, not on warmth. Conversational words — *been, climbing, worth, interesting, solid, lately, showing up* — are fine and wanted. If you catch yourself reaching for anything in the banned list, the sentence needs rewriting. If you catch yourself writing something that sounds like a dashboard, loosen it.

### Common openings

These are common patterns, not mandatory. Miru can often just say the thing.

- **"Miru found"** — when reporting a pattern.
- **"Miru noticed"** — for smaller observations.
- **"Miru suggests"** — for proposals requiring action.
- **"Based on [data]"** — sourcing.
- **"X% of decks"** — statistical claims.
- **"In the last [N] days"** — timeframe.

### Example rewrites

**Before:** "Great news! We've detected a trend! Red Luffy is SURGING in popularity — up 23% this week! You should totally build it!"
**After:** "Red Luffy's been climbing — meta share went from 8% to 11% over the last 7 days. 42 top-8 lists. Worth keeping an eye on if you're running Red."

**Before:** "🎯 We think you'd love these cards in your deck!"
**After:** "Miru found 5 cards that show up in more than 40% of top-8 Red Luffy lists over the past 30 days. Worth a look."

**Before:** "Oops, we don't have enough data. Come back later!"
**After:** "Not enough tournament data on this one yet. Miru will have more to say once 10+ lists include it."

**Before:** "Our AI recommends these cards for your deck."
**After:** "Miru suggests these, based on 6 top-8 Red Luffy lists in the past 30 days. Tap any for the why."

**Before:** "This card is underperforming in competitive play."
**After:** "Haven't seen this one in a top-8 list in a couple weeks. Still plenty in casual Red builds though."

---

## Miru visuals

### The color

Purple. Specifically `var(--color-miru-accent)` — `rgba(184,160,255,0.96)`.

Used on:
- MiruGem icons.
- Purple text in "Miru suggests" / "Miru found" headers.
- Purple border on cards that contain Miru-generated content.
- Purple fills on Meters that track Miru confidence.

**Never used:**
- As a background for the whole screen.
- On user-generated content (user's deck, user's watchlist — those are gold).
- As a decorative accent unrelated to Miru output.

The restraint is what makes purple mean "Miru." If everything is purple, nothing is.

### The gem

See [02_PM_PRIMITIVES.md §MiruGem](02_PM_PRIMITIVES.md#mirugem).

Simple diamond shape. No sparkle, no multi-facet, no rotation animation. One subtle pulse on first render, then static.

### Typography

Miru output uses the display font (`--font-display` — Geist) for the "Miru says" / "Miru found" label. Body text is the UI font (`--font-ui` — Inter).

This separation signals "this is output from Miru" typographically without relying only on color.

---

## Where Miru appears

### Always appears (when there's data)

- Home: one observation section, max 1 per screen, dismissible per 24h.
- Deck Builder: pre-applied leader-color filter (ambient); optional "suggested cards" section (explicit, collapsible).

### Sometimes appears

- Card detail sheet: "Miru says" section on tab 3 (a "Miru" tab alongside "Details" and "Prices").
- Leader detail page: "Miru notes" section for matchup patterns, deck convergence observations.

### Never appears

- Cards tab set browser (no observations needed for "here are the cards").
- Profile tab settings.
- Any alert, modal, or toast. Miru doesn't shout; it doesn't do ephemeral popups.

---

## Miru rendering rules

### Observations never move

If a Miru observation is rendered, it stays where it is until dismissed. It doesn't swap to a newer observation unless the user manually refreshes.

Rationale: users scroll past; they may come back; shifting the thing they meant to read is hostile.

### Dismissals are per-observation, per-user

Dismissing "Red Luffy builds are converging" dismisses *that specific observation* for 24 hours. Other Red Luffy observations still appear. Aggressive dismissal ("I don't want to see Miru anymore") is a setting in Profile.

### Never interrupt

Miru never appears as:
- A modal.
- A toast (except to confirm a Miru-triggered action, e.g. "5 cards added to deck").
- A banner that pushes content down after render (CLS violation).
- A notification unrelated to a user-configured alert.

Miru is patient. It waits for the user to look.

### Always labeled

Every Miru output has MiruGem + "Miru says" / "Miru found" / "Miru suggests" wording. The user should always know "this came from the AI layer" even if the visual signature isn't obvious to them yet.

### No progressive reveal

Miru doesn't "type" its output. No word-by-word streaming on the front-end. If we want to signal ongoing computation, use a subtle skeleton. When the output is ready, it appears all at once.

---

## The "is this Miru or is this data" test

Some observations are just data presentation, not Miru output. The distinction matters for visual treatment.

**Not Miru (just data):**
- Raw card price.
- Raw decklist from a tournament.
- A card's text and stats.
- Tournament results.

**Miru (aggregated / inferred):**
- "42 of the last 50 top-8 decks include this card."
- "This card's meta share rose from 8% to 11% over 7 days."
- "This leader has 58% win rate vs Purple Doffy in 87 games."
- "Your deck is 6 cards below typical Red Luffy builds."

Rule: if the output required computation over multiple data sources or aggregation over time, it's Miru. If it's a single datum displayed verbatim, it's data.

Data is rendered in neutral colors with source badges. Miru is rendered with purple + MiruGem.

---

## The explainability contract

Every Miru output has a "Why?" path. Tapping anywhere on a Miru observation opens a sheet showing:

- The data sources (with links).
- The sample size and timeframe.
- The computation in plain language. "Miru compared card X's appearance rate in top-8 lists to baseline rates across all recorded decks."
- The confidence score and what it means.

Users don't need to read this. The point is that it's *there*, one tap away, and the user trusts the system more because of it.

### The "Miru is wrong" path

Users can mark Miru output as unhelpful. Tap a small `⋯` on any Miru surface:

- "This isn't relevant to my deck."
- "The data seems wrong."
- "Show me less of this."

These feed back into Miru's filtering, scoped per user. A pattern flagged as unhelpful by enough users may be retired globally (with review).

---

## What Miru *doesn't* do

A list of things that look like they should be Miru features but aren't. We've considered each and left them out.

| Feature | Why we don't |
|---|---|
| A chat interface | We aren't selling conversation. Miru speaks in short structured observations, not a flowing dialogue. |
| Voice output | Low-utility for the at-locals use case (noisy environment). Adds ambient accessibility only if tied to a real user need. |
| Auto-build a deck for me | Too much agency removed. Miru suggests cards; the user decides. An "auto-draft" tool might exist someday but would be named clearly and live in its own surface. |
| Gamification ("Miru streaks," "Miru score") | [00_PRINCIPLES.md §1 Class, not hype](00_PRINCIPLES.md). We don't gamify analysis. |
| A mascot or character to follow | Miru has personality and a voice (LGS regular), but no face, no avatar, no character arc. Warmth in word choice — yes. Mood swings, favorites, running jokes — no. |
| Proactive notifications unrelated to user config | We notify on user-configured alerts (target hit). Miru doesn't ping the user to "come check its new findings." |

---

## The Miru layer's north star

The success metric for Miru is not engagement, not "interactions with Miru," not "sessions with Miru content."

It's: **does the user trust Miru enough to act on its suggestions, and is Miru right when they do?**

Measured as:
- Accept rate on Miru suggestions (tap-to-act / shown).
- Regret rate (dismiss-after-accept or reverse-action within 24h).
- Trust signal (direct: "mark as helpful / unhelpful"; indirect: do users look at the "why" sheet, and do they rate it informative in a quarterly survey).

A Miru feature that bumps engagement but tanks accept rate or spikes regret gets pulled. Engagement for its own sake is the Duolingo road.
