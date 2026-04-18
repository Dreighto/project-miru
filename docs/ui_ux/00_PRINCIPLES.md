# 00 — Principles

**Applies to:** every UI decision across every Miru surface.
**Read this when:** you're starting a new surface, reviewing a spec, or deciding between two approaches and need a tiebreaker.
**Skip this when:** you're implementing something already spec'd — just reference the specific doc for the rule.
**Length:** ~6 pages.
**Related docs:** every other file in this library starts from here.

---

## The nine principles

These aren't aesthetic preferences. Each one is a decision rule you can apply when two paths look equally valid.

### 1. Class, not hype.

No red dots without a real reason. No fake urgency. No "only 3 left!" pressure. No slot-machine animations on non-gambling mechanics. No "unleash," "seamless," "effortless," "powerful." No shouty copy. No exclamation marks in UI labels.

**Why:** the user is an adult who picked up our app to do a job. Treating them like a dopamine target is how we become Duolingo streaks. It works in the short term and rots the product in the long term.

**How to apply:** if a label, notification, or animation exists to *manipulate* behavior rather than *inform* it, delete it. Replace emotional language with factual language. "New!" → nothing, or a date stamp if the date matters. "Buy now!" → "Add to cart." "You're on fire! 🔥" → "3 sessions this week."

### 2. One-handed, 15-second-burst, bad-wifi default.

Design for the phone in one hand, at a table, under fluorescent lights, on 4G. Not for your MacBook Pro with hardwired ethernet and a second monitor.

**Why:** this is where the PM user actually opens the app — at an LGS between turns, on the train, standing in line. If it doesn't work in that context, it doesn't work.

**How to apply:** thumb reach matters (see [§thumb zones](#thumb-zones) below). Primary CTAs go bottom-center. Key data loads first. Never gate a critical action behind a network round-trip that doesn't have an optimistic UI path.

### 3. Learn once, never forget.

Gestures do one job globally. Labels stay put. Affordances stay visible. A user who returns after three months should feel fluent again in ten seconds.

**Why:** the alternative is progressive onboarding, hidden features, and "did you know?" modals. Those work for apps people use daily. Our apps get opened weekly or less. A feature the user has to *relearn* is a feature they stop using.

**How to apply:** no progressive unlocking. No hidden features that require a tutorial. If a gesture only works in one place, it's the wrong affordance. If a label changes based on state, the change must be trivially obvious.

### 4. Transparency over magic.

Every datapoint shows its source. Every AI suggestion shows its confidence. Every empty state says *why* it's empty. When we don't know something, we say so.

**Why:** magic is a demo feature. Transparency is a trust feature. The user is making real decisions (which cards to watch, which decks to build, which prices to believe). If the app lies by omission, the user will find out and stop trusting everything we say.

**How to apply:** source badges on every price ("TCGPlayer · verified 2h ago"). "Last verified" stamps on every data block. "Miru doesn't have enough confirmed data on this yet" when true. No fake loading spinners to make a result feel harder-won than it is.

### 5. Miru working, not Miru talking.

The app does work the user didn't ask for but is glad it did. It doesn't *tell* the user it did the work.

**Why:** pre-filtering the card pool to the leader's colors is Miru working. A toast that says "Pool filtered to leader colors!" is Miru talking. The work is the value; the announcement is noise.

**How to apply:** anticipatory UI should be invisible until the user looks for it. When a user lands on Deck Builder with a leader picked, the pool is already filtered — no banner, no explanation. If the user wants to see *why*, the filter chip shows the active state. That's the affordance.

### 6. Calm motion.

Motion exists to communicate state change, not to entertain. Entrance 200–300ms fade+translate-y-8, ease-out. Exit 150ms, shorter than entrance. No bounce unless the thing is literally bouncing (drag release). No spring-out flourishes on mount.

**Why:** calm motion respects attention. Playful motion begs for attention. The user opened PM to check a price; nobody asked for a stage show.

**How to apply:** animate layout change, not static content. Haptics over visuals for single-finger feedback. If a component animates on every state change, cut half of those animations.

### 7. Forge aesthetic — gold on dark, purple as Miru.

Dark canvas (#08060f), two semantic accents: gold (`#f4d078` / `rgba(244,208,120,0.96)`) for *yours* — active state, watched, trending, success; purple (`#c9b0ff` / `rgba(184,160,255,0.96)`) for *Miru* — insight, suggestion, ambient intelligence. Leader colors are semantic only (red/green/blue/purple/yellow/black), never decorative.

**Why:** a two-accent system forces discipline. Once every color has a meaning, adding a third color has to justify itself. That's how design systems stay legible as they grow.

**How to apply:** if you're reaching for a third accent, first ask whether one of the two existing accents expresses the same thing. If you're using a leader color outside a leader context, stop.

### 8. Verify, don't assume.

Every ingested datapoint is verified or labeled unverified. Every automated suggestion has a confidence rating. Every prediction has an audit trail.

**Why:** the competitive differentiator of PM isn't "we have more cards" — it's "we know what we know and we tell you." This is a principle that shows up in copy, UI, and backend. The UI enforces it by never showing a price, count, or claim without a source.

**How to apply:** every price component accepts `source` and `verifiedAt` props. Every meta claim links to evidence. Every deck suggestion marks itself as "verified tournament data" or "community-reported."

### 9. Ship the edges.

Loading states, empty states, error states, offline states, zero-results, and permission-denied states are part of the feature, not polish to add later. A feature without these isn't a feature — it's a demo.

**Why:** the happy path is <10% of real use. The other 90% is someone on 4G with battery saver on whose scan didn't work. If we only designed for the happy path, we'd ship the same buggy app every other TCG tool ships.

**How to apply:** every new component gets: loading, error, empty, success, and at-least-one offline fallback. These are not optional polish. They are the feature.

---

## Thumb zones

Baseline device: 6.3–6.7" phone held in one hand. Green zone (easy thumb reach): **bottom 40% of the screen, center-weighted**. Yellow zone (stretch): middle 40%. Red zone (contortion): top 20% and opposite-corner edges.

- **Primary action: green zone.** Bottom-center. Always reachable.
- **Navigation: green zone.** Bottom tab bar. Never top.
- **Titles and status: red zone is fine.** Users read these, they don't tap them.
- **Secondary controls (filters, search): yellow zone.** Reachable with a small grip shift.
- **Destructive actions: not in green zone.** Delete, remove, unwatch — put these one step deeper (swipe-to-reveal, or inside a sheet) to prevent muscle-memory disasters.

Source: [Parachute Design — Thumb Zone UX](https://parachutedesign.ca/blog/thumb-zone-ux/), applied to our baseline 6.1–6.7" iPhone / Android devices.

---

## Evidence, not taste

When two patterns look equally defensible, the tiebreaker is evidence. In order of precedence:

1. **A real user complaint** from a 1-star review or a Reddit thread in our domain (TCG apps). Cite the URL.
2. **A published spec** (WCAG, Apple HIG, Material Design, W3C). Cite the section.
3. **A measured metric** (INP, LCP, bundle size). Cite the budget.
4. **A competitive benchmark** from Linear, Stripe, Arc, or another reference system. Cite the screenshot or talk.
5. **Operator directive.** Cite the CLAUDE.md rule.
6. **Your taste.** Only if 1–5 are silent. And then note that's what you did.

The reason this ordering matters: *we are not special.* The mobile web is mature enough that every common problem has a documented answer somewhere. Taste is what you use when the evidence runs out, not what you lead with.

---

## The five-second test

Before shipping any UI change, five-second test:

1. **What is this screen's one job?** If you can't name it in one sentence, the screen is confused.
2. **Where is the primary action?** If it's not bottom-center-ish on mobile, justify.
3. **What happens on 4G with empty cache?** If you can't answer, you haven't shipped the edges.
4. **What breaks if I'm left-handed, have Dynamic Type on, or use VoiceOver?** If "nothing happens" is the honest answer, good. If anything hangs, you haven't shipped.
5. **Would I use this?** Not "would I be proud of this." "Would I open this at a card shop to check a price."

If you fail one of these, loop back. Not later. Now.
