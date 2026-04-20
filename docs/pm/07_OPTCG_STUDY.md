# 07 — OPTCG App Landscape: What to Steal, What to Reject

**Applies to:** Every PM feature decision that could be described as "like [competitor]."
**Read this when:** Considering a feature that already exists in a TCG app or OPTCG-specific tool. Before writing a spec that borrows from another app.
**Skip this when:** You need general craft inspiration outside the TCG domain (use [docs/ui_ux/07_COMPETITIVE_STUDY.md](../ui_ux/07_COMPETITIVE_STUDY.md) — Linear, Stripe, Arc, etc.).
**Length:** ~8 pages.
**Related docs:** [docs/ui_ux/07_COMPETITIVE_STUDY.md](../ui_ux/07_COMPETITIVE_STUDY.md), [docs/pm/00_PRINCIPLES.md](00_PRINCIPLES.md), [docs/pm/08_PM_ANTI_PATTERNS.md](08_PM_ANTI_PATTERNS.md).

---

## Why this document is separate from the universal competitive study

The craft guide in `docs/ui_ux/07_COMPETITIVE_STUDY.md` studies Linear, Stripe, Arc, and other non-TCG apps for motion, layout, typography, and restraint. That's craft inspiration — the shape of quality.

This document studies the TCG-specific landscape: apps already solving adjacent problems, apps players already have installed, apps we will be compared to. Every feature we ship will be mentally benchmarked against one of these. Understanding them in detail prevents us from either copying their mistakes or accidentally reinventing what they already do well.

The rule: **steal craft from outside the domain, steal domain decisions (carefully) from inside it.** Never the reverse.

---

## The landscape at a glance

| App | Category | Platform | Our stance |
|---|---|---|---|
| **Collectr** | Collection tracker + price tool | iOS/Android native | Mostly reject (price accuracy + dark patterns) |
| **Manabox** | Scan-based collection tracker | iOS/Android native | Steal scan flow, reject gesture design |
| **Moxfield** | Deck builder (MTG primarily, OPTCG secondary) | Desktop web | Steal deck UX concepts, reject mobile experience |
| **OPTCGSim** | Play simulator | Web (tabletop sim) | Ignore — different job entirely |
| **Egman Events** | Tournament report archive | Web | Use as data source, never compete with |
| **MOOgiwara** | Community deck-sharing / meta | Web | Study for social signals, reject social model |
| **Bandai OP.TCG Companion App** | Official companion | iOS/Android native | Study the gaps (which are many) |
| **DBS / Pokemon Collectr-equivalents** | Adjacent TCG apps | Mixed | Inherit lessons where platform-generic |

A quick principle up front: most of these apps are built by small teams or solo developers, and we respect the effort. Criticism here is structural, not personal. The goal is to understand **why** certain patterns fail our bar so we don't repeat them — not to denigrate the work that exists.

---

## Collectr

**URL:** [collectr.com](https://collectr.com) · App Store: [apps.apple.com/us/app/collectr-tcg-collector-app/id1603892248](https://apps.apple.com/us/app/collectr-tcg-collector-app/id1603892248)
**What it is:** Collection tracker, price tracker, and marketplace-adjacent tool for Pokemon, OPTCG, MTG, and other TCGs. Cross-platform native app with a large install base.
**Why it matters:** Most common point of comparison. Players will say "why not just use Collectr?" The answer needs to be concrete.

### What to steal

- **Multi-TCG pivot UI.** The game-switch at the top is clear and fast. We are single-game for now, but if we ever add Dragon Ball Super or MTG we should copy the switch pattern (not a submenu, not a settings screen — a visible top-level control).
- **Quick-add from scanner.** The scanner-into-collection flow is quick. Card detected → confirm set + variant → add. No form. No modal. We mirror this spirit in the add flow even without a scanner in v1.
- **Binder-style visual view.** A 3×3 or 4×3 grid view of owned cards as a visual shelf. This is satisfying and we should offer it as an alternative layout on the Collection page (when we build one).

### What to reject

- **Price accuracy and sourcing.** App Store reviews repeatedly flag prices as stale or wrong. A widely-circulated review thread (paraphrased from multiple 1-star reviews on the App Store link above and on [justuseapp.com/en/app/1603892248/collectr-pokemon-tcg-collector](https://justuseapp.com/en/app/1603892248/collectr-pokemon-tcg-collector)) describes a Japanese Base Arcanine variant priced around $25 in the app but closer to $3 on TCGPlayer. This is the exact failure mode we built the **source + verifiedAt + confidence** contract to prevent. Never show a number without telling the player where it came from and when it was last checked.
- **Upsell friction.** Reviewers describe paywalls around core features (CSV export, deeper history) appearing at confusing moments. Our stance: free tier is real and usable; any paid tier is clearly scoped and announced, never surprise-paywalled mid-flow.
- **Overstated scope claims.** Marketing copy across the Collectr surface claims comprehensive coverage that App Store reviews regularly contest. We avoid phrases like "every card" or "always accurate" in our own copy — we say what we have and mark what we don't.
- **Push notification volume.** Several reviews mention frequent non-actionable notifications. Our push rule (user-configured targets only, off by default, 1/card/24h, 5/user/day — see [04_WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md)) is specifically designed to avoid becoming this.

### The compare-to quote

When a player asks "why not Collectr?" the honest answer: Collectr is a broad multi-TCG tracker. Miru is a focused OPTCG sidecar that tells you where its numbers come from and when it doesn't know. Different jobs. Pick the one that fits what you actually need.

---

## Manabox

**URL:** [manabox.app](https://manabox.app) · App Store: [apps.apple.com/us/app/manabox-mtg-scanner/id1464951011](https://apps.apple.com/us/app/manabox-mtg-scanner/id1464951011)
**What it is:** MTG-first scanner + collection app. Not OPTCG-native, but widely used by OPTCG players who also play MTG. Strong recognition accuracy.
**Why it matters:** Sets the scan-speed bar. Any scanner we ever build will be compared to Manabox.

### What to steal

- **Scan-throughput mindset.** The app treats scanning as the hot path — camera opens fast, recognition is optimistic, the UI commits to a guess and lets the user correct. Correct frame-to-result latency.
- **Bulk-scan mode.** A stream of cards can be scanned in a row without returning to a menu between each. This is the only tolerable flow for a box-break or a trade-in session.
- **Set disambiguation UX.** When the same illustration exists in multiple sets, Manabox surfaces both candidates quickly with clear distinguishing marks (set symbol, number). We should copy this exactly when we build OPTCG scan.

### What to reject

- **Inconsistent gesture language.** Multiple reviews on r/mtgfinance and r/edh mention unexpected gesture behaviors — swipes that sometimes delete, sometimes edit, depending on context. This violates our **"one gesture, one job globally"** rule from [docs/ui_ux/02_GESTURES.md](../ui_ux/02_GESTURES.md). We pick one meaning for swipe and keep it.
- **Cluttered detail view.** The card detail page packs price history, variant list, set info, edition details, and sales links into one long scroll. Our detail view is focused: what this card is, what it costs, where that came from. Everything else is a deliberate sub-page.
- **Inconsistent dark mode.** Dark mode is present but some screens have light-mode artifacts. We are dark-only in v1, so this isn't a risk for us — but noted: partial theming is worse than no theming.

---

## Moxfield

**URL:** [moxfield.com](https://moxfield.com)
**What it is:** Web-based deck builder, mostly MTG-focused, with an OPTCG section. Desktop-first. Strong deck-sharing, comment threads, format awareness.
**Why it matters:** The gold standard for web deck-building UX. Any deck builder we ship will be compared to Moxfield on the desktop side.

### What to steal

- **Format awareness.** Decks are built within a format (Standard/Commander/etc. in MTG; for us it's block legality + banlist). Invalid cards are flagged inline, not at save. We already do this in principle — Moxfield is the reference for how clearly it's communicated.
- **"Primer" concept.** Deck creators can write a long-form explanation of the deck's plan, mulligan priorities, and matchups. This is a first-class surface, not a comment. If we ever add deck-sharing, this is the model: the deck and the primer are two tabs of the same object, not two documents.
- **Version history.** Moxfield tracks deck iterations with diffs. If we add this, the diff view is the right UI (not a timeline of saves).

### What to reject

- **Desktop-first assumptions leaking into mobile.** The mobile web version of Moxfield is the desktop view shrunk. Card rows are too dense, tap targets are tight, and the builder UI does not adapt to thumb reach. We are mobile-first. When we reference Moxfield, we reference the *concepts*, not the *rendering*.
- **Cross-referenced pricing without provenance.** Prices shown in Moxfield aggregate from multiple vendors without a clear per-card source display. We always show source and freshness per number.
- **Not a PWA.** Moxfield is a web app but not installable, no offline mode, no home-screen presence. If our deck builder matches Moxfield's functional depth but lives on the home screen and works on a plane, we win the niche.

---

## OPTCGSim

**URL:** [optcgsim.com](https://optcgsim.com) (and various community forks)
**What it is:** A web-based play simulator for OPTCG — tabletop simulation, drag cards, declare attacks, track life.
**Why it matters:** We will be asked "does Miru include a simulator?" The answer is no.

### Our stance

Ignore the feature set. Different job. Simulators are play surfaces; Miru is a pre-play/post-play sidecar. Combining them would violate the **one app, one job** principle from [docs/ui_ux/00_PRINCIPLES.md](../ui_ux/00_PRINCIPLES.md).

**What we can link to:** Direct links to OPTCGSim from specific deck views ("Open this deck in OPTCGSim" — external link) is a reasonable integration if the protocol is available. We don't simulate; we hand off.

---

## Egman Events

**URL:** [egmanevents.com](https://egmanevents.com)
**What it is:** Tournament report archive. A community-maintained collection of OPTCG tournament results: top cuts, winning lists, regional breakdowns.
**Why it matters:** This is the *source* for a huge fraction of meta intelligence. Top 8 at a 500-player regional is a signal. Aggregated across N tournaments over M weeks, it becomes Miru's observation layer.

### Our stance

Egman is upstream. We cite. We link. We never reframe their data as our insight.

- When Miru says *"Black/Yellow Luffy appeared in 8 of 32 top-8s over the last 30 days,"* that sentence is a count from Egman's archive, and the source badge credits Egman.
- We never scrape and rebrand. Any usage respects rate limits and their terms of service. Where possible, prefer official API or exports.
- If Egman is unavailable (downtime), Miru's tournament-based observations should degrade gracefully: the badge reads *"Based on tournaments reported through [date]"* and we stop showing "fresh" meta claims.

### What NOT to do

- Do not try to be Egman. Do not build a tournament-upload flow in Miru. Do not claim "our tournament data" when it isn't ours.
- Do not cache aggressively enough that we show stale data without marking it stale.

---

## MOOgiwara

**URL:** [moogiwara.com](https://moogiwara.com)
**What it is:** Community OPTCG deck-sharing site with meta tracking, deck archetypes, and commentary.
**Why it matters:** The closest thing to a "social" layer the OPTCG community has online. It's not Twitter; it's a structured list of decks with context.

### What to steal

- **Archetype tagging.** Decks are grouped by archetype (e.g., "Black/Yellow Luffy," "Red Zoro") rather than by individual author. This is the correct primary axis: **archetype > author** in the competitive space. If we ever surface meta archetypes in the Leaders tab, this is the model.
- **Meta context per deck.** Each deck surface shows where it finished, what event, when. Meaningful context, cited.

### What to reject

- **Social-first design.** MOOgiwara tilts toward community dynamics (likes, follows, trending) in places where Miru intentionally won't go. Our North Star isn't engagement. We do not ship follows, likes, feeds, or streaks. See [docs/pm/03_MIRU_LAYER.md](03_MIRU_LAYER.md) for the explicit metrics contract (accept rate, regret rate, trust — not session time).
- **Desktop-first layout.** MOOgiwara is a full-desktop site; mobile is a responsive fallback. Our surface is mobile-first and one-handed. Different center of gravity.

---

## Bandai OP.TCG Companion App (official)

**App Store:** [apps.apple.com/us/app/one-piece-card-game-official/id6450388763](https://apps.apple.com/us/app/one-piece-card-game-official/id6450388763) (and the Android equivalent on Play Store)
**What it is:** The official app from Bandai. Deck registration for events, card database, rules reference, tournament support.
**Why it matters:** It's the "default." Every tournament player has it installed because event registration often routes through it.

### What to study

- **Deck registration format.** Competitive events require a deck list format that matches the official app's export. We should export in a format that imports cleanly. This is non-negotiable compatibility.
- **Rules reference.** The official rules are the source of truth for legality and interaction questions. We do not maintain our own rules document — we link to the official one.
- **Tournament calendar.** The official tournament calendar is the source. We can surface upcoming events near the player (if they consent to location), but we link out; we do not fork the calendar.

### What to reject

- **Gaps, not flaws — Miru fills the gaps.** The official app covers registration and rules well. It does not do: price watching, cross-tournament meta synthesis, variant tracking, or sidecar-at-locals mode. That gap is Miru's whole reason to exist.
- **Never try to replace registration.** Do not build our own event registration. Link to the official flow.
- **Never reprint the rules.** Quote snippets with citation when necessary; do not host the rulebook.

---

## DBS, Pokemon, and other adjacent TCG apps — generalized lessons

We're not direct competitors to Pokemon TCG Pocket, Pokellector, DBS companion apps, or MTG's Arena/Manabox/Moxfield ecosystem. But OPTCG players often play these games too, and they bring expectations with them. A few generalized lessons:

- **Pokemon TCG Pocket** (Bandai-adjacent in product posture, not directly comparable) has shown that a clean, focused card-surface experience with smooth motion is a massive draw. The animation restraint inside TCG Pocket is worth studying — though its gacha-driven acquisition loop is explicitly rejected.
- **Cardsphere** (MTG trade-matching) demonstrates that **want-list → have-list** matching is a *different feature* than watching prices. If we ever add trade posting, it is a separate surface, not grafted onto watchlist.
- **TCGPlayer's own mobile app** is cluttered and transactional — useful as a reference for "this is the marketplace UX we are not." We watch prices; we do not sell.

---

## The per-competitor "steal/reject" summary

| Competitor | Steal | Reject |
|---|---|---|
| Collectr | Multi-TCG switcher pattern, quick scan-add, binder view | Price opacity, paywall friction, push volume |
| Manabox | Scan throughput, set disambiguation | Inconsistent gestures, cluttered detail |
| Moxfield | Format-awareness, primer concept, version diffs | Desktop-first mobile, no PWA, opaque pricing |
| OPTCGSim | Nothing directly — different job | Everything (we are not a simulator) |
| Egman | Tournament data model, citation | Never rebrand their data |
| MOOgiwara | Archetype-first meta grouping | Social dynamics, engagement optimization |
| Bandai OP.TCG | Official deck format, rules link, tournament calendar | Nothing to reject; we live in the gaps |

---

## Things no OPTCG app does well (yet) — Miru's opening

Across every app studied, these patterns are consistently weak or missing. This is the shape of our opening.

1. **Transparency about where prices came from.** Every app shows prices; almost none show source + verifiedAt + confidence per number.
2. **"I don't know" as a first-class state.** No competitor says *"Miru doesn't have data on this."* Silence or a made-up number is the default. We ship the negative state.
3. **Ambient filtering with disclosed rules.** Every app either shows you everything (firehose) or filters invisibly (mystery). None show *"This page is filtered. Here's how. Clear it."*
4. **One-handed, bad-wifi, 15-second burst mode.** Most apps assume attention. Miru assumes distraction.
5. **Restraint in social pressure.** Every competitor trends toward feeds, streaks, or urgency. None have chosen craft+calm as the brand.
6. **Watchlist that respects price oscillation.** Most target-price implementations either fire on every cross or mute completely. The "first-hit, then cooldown, then approaching opt-in" pattern (see [04_WATCHLIST_AND_METER.md](04_WATCHLIST_AND_METER.md)) is rare.
7. **Meta claims tied to sample size.** "Popular in tournaments" is everywhere. *"8 of 32 top-8s over 30 days, n=32"* is almost nowhere. We ship the denominator.

Every one of these gaps is a principle from [00_PRINCIPLES.md](00_PRINCIPLES.md) made concrete. The opening isn't a feature list — it's a posture the market hasn't taken.

---

## How to use this document when designing a feature

1. **Name the feature in one sentence.** ("Add a target-price editor to the watchlist.")
2. **Find the nearest competitor implementation.** ("Collectr has one.")
3. **Read their App Store reviews and relevant subreddit threads.** (r/onepiecetcg, r/tcgpro, r/mtgfinance for adjacent apps.)
4. **Write the steal list and the reject list** for that competitor's version of the feature.
5. **Cross-check against [00_PRINCIPLES.md](00_PRINCIPLES.md)** — does any "steal" item violate our principles? If yes, drop it.
6. **Cross-check against [08_PM_ANTI_PATTERNS.md](08_PM_ANTI_PATTERNS.md)** — does any "steal" item appear in the rejected list? If yes, stop.
7. **Write the Miru version** of the feature with citation back to what inspired it and what we chose not to carry over.

If the feature appears in no competitor, that's interesting. Ask why. Possibilities:
- It's genuinely a gap in the market (good — this is the opening).
- Nobody needs it (pause and reconsider).
- Someone tried and failed for reasons we should discover (search harder).

---

## Research hygiene

- **Cite the review, not your memory of it.** App Store review text shifts over time. If we reference a 1-star review, capture the quote and link to the app page at minimum. Prefer archived sources when the review is load-bearing for a decision.
- **Reddit/Discord quotes are evidence but not gospel.** A single user complaint is a signal to investigate, not a verdict. Look for repeated patterns across threads.
- **Dates matter.** An app that was cluttered in 2023 may have shipped a redesign. Always check recent reviews (last 6 months) and recent screenshots before citing a critique.
- **Respect TOS.** Do not scrape. Do not rehost images. Do not republish their data without attribution. When in doubt, link out.

---

## The honest conclusion

Most OPTCG tooling is built by hobbyists or small teams on top of engines built for broader TCG categories. The craft bar across the domain is low because the domain is young and the volume is small. That's our opening and also our obligation: **the floor for Miru is "competitors would not ship this," and the ceiling is "a player switches from a general-purpose tool to a focused one because the focus is worth the switch."**

Nobody owes us their install. We earn each one by being calmly, obviously better at a specific job.
