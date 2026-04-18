# 07 — Competitive Study

**Applies to:** designing a pattern from scratch, or deciding whether to copy a convention.
**Read this when:** you're about to invent something new and want to check whether a better-resourced team already solved it; you're evaluating whether a convention is actually good or just common.
**Skip this when:** the pattern is already in this library.
**Length:** ~8 pages.
**Related docs:** [00_PRINCIPLES.md](00_PRINCIPLES.md), [docs/pm/07_OPTCG_STUDY.md](../pm/07_OPTCG_STUDY.md) for TCG-specific competitors.

---

## Who we study and why

The best consumer-grade software teams operate publicly. Their design choices leave traces — blog posts, conference talks, screen recordings, reverse-engineered CSS. We can learn from what they do *and* what they publicly say about why.

We study:

- **Linear** — keyboard-first productivity, speed, dense minimalism.
- **Stripe** — forms, data, elegance at scale.
- **Arc (The Browser Company)** — opinionated mobile, sidebar-first desktop, motion.
- **Superhuman** — shortcuts, triage speed, calm density.
- **Things 3 (Cultured Code)** — gesture language, one-handed, offline-first.
- **Apple** (Mail, Reminders, Photos) — native iOS patterns we can echo.
- **Notion** — block composition, responsive density.
- **Spotify** — list virtualization, offline, audio as background.

We **don't** study:

- **Meta products** (Instagram, Facebook) — engagement-maximizing patterns we explicitly reject.
- **TikTok, Snapchat** — same.
- **Slack** — built for always-on use, not bursts.
- **Duolingo** — streaks and manipulation are the anti-Miru.

Knowing who to ignore is as important as knowing who to learn from.

---

## Linear

Linear is the reference for "fast, keyboard-driven, opinionated." Their team writes openly about their design choices. [linear.app/method](https://linear.app/method) is their design system + methodology doc.

### What we steal

- **Command palette (`Cmd+K`).** Every action reachable via keyboard, searchable. [Linear — Keyboard shortcuts](https://linear.app/changelog). The cheatsheet comes up via `?`.
- **Single accent color with strong neutrals.** Linear uses blue/purple as their single accent on a near-black canvas. We use gold + purple, applying the same single-accent discipline per semantic role.
- **Dense lists with breathing room.** 32–40px row height, but 16px gap between sections. Dense is not cramped.
- **Inline editing.** Click a field, type, Enter commits. No modal for a single-field change.
- **No hover states on mobile.** Linear's mobile app drops hover entirely. Pressed states do the work.
- **Transition subtlety.** 180–220ms ease-out. Nothing bounces. Nothing spins.
- **Status badges, not status icons.** A "Done" badge is clearer than a green checkmark — text is unambiguous.

### What we reject

- **Issue-tracker language.** Linear is for engineers tracking work. PM is for card players collecting. "Cycle" doesn't belong.
- **Complete reliance on keyboard.** Linear is desktop-first, phone-second. PM inverts that. We keep the keyboard paths but the mobile tap path is always equivalent.
- **Sidebar-heavy IA.** Linear's left-sidebar navigation is for power users who live in the app all day. PM needs thumb-reach, bottom-tab nav.

### Source

- [Linear — Method](https://linear.app/method)
- [Linear's engineering blog](https://linear.app/blog)
- [Karri Saarinen on Twitter — design principles](https://twitter.com/karrisaarinen) (Linear's co-founder)

---

## Stripe

Stripe sets the reference for forms, dashboards, and "technical content at scale that doesn't feel soulless." Their design system docs were public for a while at [stripe.com/design](https://stripe.com/newsroom/news/redesigning-stripe-design-system) (retired, but still referenced).

### What we steal

- **Form field rhythm.** Label above input, 8px gap. Input 44px high. Helper text below, 12px. This is their baseline and it holds up. Copy it.
- **Inline validation.** Fields validate on blur, not on every keystroke. Error text slides in, doesn't pop. Error persists until fixed; doesn't clear on focus.
- **Receipts and confirmations.** Stripe's "payment succeeded" screen is a template: green check, amount, details, "Back to dashboard" primary action. Clean, confident, not celebratory. We use the same template for deck-saved, variant-flip-committed, etc.
- **Numbers are first-class.** Tabular figures (`font-variant-numeric: tabular-nums`) on every price, count, and ID. Without this, "125" and "243" have different widths — fine for prose, ugly in a table.
- **Documentation tone.** [stripe.com/docs](https://stripe.com/docs) — direct, no fluff, examples first. Our in-app help copy follows the same rule.

### What we reject

- **Dashboard density on mobile.** Stripe desktop is glorious; Stripe mobile is… fine. Don't force desktop density onto a phone.
- **Marketing-grade animations.** Stripe marketing pages use gradients and particle effects. Stripe product doesn't. Don't confuse the two.

### Source

- [Stripe — Design](https://stripe.com/newsroom/news/redesigning-stripe-design-system)
- [Stripe Press — publications](https://press.stripe.com/)
- [Benjamin De Cock's work at Stripe](https://twitter.com/bdc) — motion design
- [Stripe Dashboard](https://dashboard.stripe.com/) — the live product, worth exploring

---

## Arc (The Browser Company)

Arc's mobile browser ("Arc Search") ships a mobile UX most browsers don't attempt: command-bar-first, gesture-rich, delightful in small details.

### What we steal

- **Floating command bar.** Arc's bottom bar is a mini command center — search, tabs, shortcuts, AI. Not a tab bar, not a nav bar, a *bar of capabilities*. We don't fully copy this, but the discipline of "one clearly-named thing per button, no overflow" applies.
- **Haptic vocabulary.** Arc Search uses haptics aggressively but consistently. Light tap on hover, medium on commit, heavy on "browse for me" completion. Clear taxonomy — see our [02_GESTURES.md §Haptics](02_GESTURES.md#haptic-vocabulary).
- **"Pinch to get a summary" gesture.** A well-learned iOS gesture repurposed for content summary. We use pinch only for image zoom, but the idea that an existing OS gesture can get a new app-level meaning — when it's a rare, power-user action — is worth remembering.
- **Transparency around AI.** When Arc's AI does something, it shows what it did in a card with "I searched X and Y, found Z." Miru's "Miru working" principle is the same — show the work [00_PRINCIPLES.md §5](00_PRINCIPLES.md).

### What we reject

- **Opinionated browser chrome.** Arc reshapes what a browser is. We're not that — we're an app with conventional shapes.
- **Hidden-until-hovered everything.** Arc's desktop hides tabs, sidebar, URL until you move your mouse. On mobile, this kind of hidden-UI is tutorial-dependent and fails [00_PRINCIPLES.md §3 Learn once, never forget](00_PRINCIPLES.md).

### Source

- [The Browser Company blog](https://thebrowser.company/learning/)
- [Josh Miller's posts](https://twitter.com/joshm)
- Direct use of Arc and Arc Search on iOS — the product is the study

---

## Superhuman

Superhuman is the reference for "keyboard shortcuts as primary UX" and "triage at speed." It's also an instructive lesson in *who Superhuman is for* — you cannot blindly copy a tool built for extreme-power-users.

### What we steal

- **Every action has a shortcut.** `?` shows the cheatsheet. Actions are verbs, one letter per verb when possible (`E` to archive, `R` to reply).
- **The first-run onboarding.** Superhuman famously does a paid onboarding call. The web-product version is less extreme but the pattern is: *teach the handful of critical shortcuts on day 1, then get out of the way*. We don't do a paid call, but our onboarding surfaces 3–4 critical gestures on first use, with a "Got it" and no reminders after.
- **Calm density.** Superhuman packs a lot into one screen but breathes. Generous type, generous leading, dense content. Good reference for the PM Home feed.
- **Undo everywhere.** Cmd+Z undoes almost any recent action. Every destructive action is undoable. See [02_GESTURES.md §Swipe-to-commit](02_GESTURES.md#swipe-to-reveal-vs-swipe-to-commit) on our 5s undo rule.

### What we reject

- **Shortcut-first IA.** Superhuman's UI assumes you know the shortcuts. PM can't — most users won't learn shortcuts for a TCG app. We make everything reachable via tap first, shortcut second.
- **Tribal features.** Features that only work for their target segment (finance executives, YC founders). We design for a wider demographic.

### Source

- [Superhuman blog](https://blog.superhuman.com/)
- Rahul Vohra's public talks on building Superhuman (search "Rahul Vohra Superhuman product market fit")

---

## Things 3 (Cultured Code)

Things 3 is the reference for gesture language on iOS, one-handed use, and respectful simplicity. It's been consistently on iOS best-of lists since 2017.

### What we steal

- **Magic plus button.** Drag the + button to the position in the list where you want the new item. Contextual creation without a modal. We adapt this for "add card to specific deck position" where drag physics already exist.
- **Slide-in side menu with swipe from left edge.** But — see [02_GESTURES.md](02_GESTURES.md) — we keep clear of the edge because the OS owns it. Things works because Cultured Code chose to accept occasional OS-gesture conflict; we don't.
- **Slide-to-reveal on list rows.** Short slide → single action; longer slide → menu of actions. The pattern is what iOS Mail eventually shipped too.
- **Haptic precision.** Things uses haptics on every gesture completion, every deadline-hit, every drag-drop. It feels *responsive* rather than chatty because the haptic is always coupled to something the user did.
- **Offline-first.** Every action works offline; sync happens in the background. Key for the "at the LGS, spotty wifi" case.

### What we reject

- **Desktop/Mac parity obsession.** Things has a Mac app and maintains full parity. We're web + PWA. No desktop app, no pretending the web is a Mac app.
- **Paid-only model.** Things is "buy once" and no free tier. Not applicable to our go-to-market.

### Source

- Things' [Wonderful Day Out](https://culturedcode.com/things/blog/) design journal
- Ken Case / Omni Group posts on gesture design (parallel universe; similar era)
- Personal use of Things 3 on iPhone — the product is the study

---

## Apple (Mail, Reminders, Photos)

Apple's own apps are the reference for "what does iOS feel like." Matching their conventions earns immediate familiarity; departing from them costs onboarding time.

### What we steal

- **Large title that shrinks on scroll.** Navigation bar collapses from 32pt to 17pt as the user scrolls. Standard UIKit behavior. We replicate via CSS scroll-driven animations or a JS scroll handler with `rAF`. See [Apple HIG — Navigation Bars](https://developer.apple.com/design/human-interface-guidelines/navigation-bars).
- **Sheet grabber.** Small handle at top of sheet (4px × 36px, rounded). Appears on every iOS sheet since iOS 15. Universal affordance for "drag me."
- **Pull-down to search.** On list views, pulling down past the top reveals a search field. Users who don't use search never see it; users who do, know exactly where to look. Alternative to a persistent search bar eating 56px of screen. See [Apple HIG — Search Fields](https://developer.apple.com/design/human-interface-guidelines/searching).
- **Rounded rect everything.** 14px radius for cards, 10px for chips, 8px for small controls. iOS-native feel.
- **Red destructive text, not red button.** Delete is red text on plain background, not a red button. Subtle and less alarming.
- **SF Symbols alternative.** We use lucide-svelte because SF Symbols aren't licensed for web. Pick a consistent stroke and treat the library like a font. See [04_PRIMITIVES.md §IconButton](04_PRIMITIVES.md#icon-button).

### What we reject

- **iPad split-view patterns in a phone-first app.** iPad-specific affordances don't belong on phone screens.
- **iOS-exclusive behaviors.** Dynamic Island integration, Live Activities — we can plan for them (via web push + app store wrapper later), but they're not the baseline.

### Source

- [Apple Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines/) — required reading
- WWDC sessions from 2020 onward on iOS design (available at developer.apple.com/videos)
- Direct use on a personal iPhone

---

## Notion

Notion is the reference for block composition — "everything is a block, blocks compose, rearrange easily." It's also the reference for cross-platform design drift: their iOS, web, and macOS are *similar* but have deliberate platform-native divergences.

### What we steal

- **Slash-command menu.** Type `/` to open an inline command menu for inserting content. We adapt this for power-user deck-builder commands (`/leader`, `/4×`).
- **Drag handles on hover.** On desktop, each block reveals a `::` drag handle on hover. On mobile, drag handles are always visible (hover doesn't exist). We apply the same pattern — on desktop, reveal drag handles; on mobile, they're baked in.
- **Page-as-a-primitive.** A page contains blocks; a block can be a page. The model is lean and generative. For PM, a deck is a page of card-blocks; a card is a block with multiple views (detail, variants, prices).
- **Inline toggles.** A block can collapse/expand inline. No modal, no new page. Good for card-detail expand on desktop.

### What we reject

- **Slow loads on mobile.** Notion mobile is slower than it should be and users notice. Performance budgets matter.
- **Every page is infinitely hierarchical.** Good for docs; bad for product surfaces that need predictable IA.

### Source

- [Notion's site](https://www.notion.so/) — the product is the study
- [Notion's design blog](https://www.notion.com/blog/topic/design)
- Ryo Lu's posts on Notion's design system

---

## Spotify

Spotify is the reference for "long lists on mobile, with many interactions per row, and it still feels fast." Their list virtualization and scroll behavior are reference-quality.

### What we steal

- **Now-playing bar that collapses/expands.** Mini-player at bottom of screen; tap expands to full player. The pattern we use for "active deck in build mode."
- **Context menus via overflow `⋯` icon.** Every row has a `⋯` that opens an action sheet. Clearer than swipe-to-reveal for users who don't know swipe gestures.
- **Offline-first for saved content.** Downloaded songs are marked, play without network. Same pattern for "saved decks" — they work offline, downloaded card images included.
- **Skeletons for everything loading.** Not spinners. Spinners don't convey shape; skeletons do.

### What we reject

- **Auto-play / recommendation-driven UI.** Spotify's core loop is "sit back and let algorithms pick." PM's core loop is "the user has a thing they want to check." Don't push.
- **Dark-only design.** Spotify is dark-only. We're dark-by-design but support OS light mode as a respect setting (eventually).

### Source

- [Spotify Design](https://spotify.design/) blog
- Direct use on iOS — the product is the study

---

## What these companies share

Common threads, worth naming explicitly:

1. **Every interaction has a time budget.** Linear aims for 16ms; Superhuman aims for 100ms end-to-end; Stripe for < 300ms form submit.
2. **Keyboard is a first-class citizen, not a fallback.** Even mobile-first apps (Things) support external keyboards elegantly.
3. **Motion communicates, doesn't entertain.** No bounce without reason; no spin without load.
4. **Dense but breathing.** Information-rich screens with clear visual rhythm beat empty-airy-sparse.
5. **Opinionated and conservative.** Pick a convention; honor it in 100 places. Don't reinvent the checkbox.
6. **Transparent AI/automation.** Where AI exists (Arc, Notion AI, Superhuman AI), it shows its work and keeps the human in charge.
7. **Offline is not "graceful degradation."** Offline is a feature.

---

## What to do when competitors disagree

They will disagree often:

- Linear uses blur backgrounds aggressively; Superhuman doesn't.
- Things puts actions in swipes; Spotify puts actions in `⋯` menus.
- Arc puts AI prominently; Things has no AI.

**Tiebreaker:** which pattern fits [00_PRINCIPLES.md](00_PRINCIPLES.md)? The principles are the spine; competitive patterns are tactical choices that serve the spine.

When in doubt, pick the more conservative pattern. A user who learned the conservative pattern at Stripe or Apple will transfer the muscle memory. A user learning a novel pattern must learn it twice — once on the first app, once on yours.

---

## What to do when a competitor ships something you want to copy

1. **Find the underlying principle.** Don't just copy the shape.
2. **Check if it conflicts with our principles.** "Dark-pattern notification" at a famous competitor is still a dark pattern.
3. **Check if it generalizes beyond that competitor's product.** An engagement-loop pattern from a social app rarely ports to a tool.
4. **Prototype small, test, iterate.** Our users aren't their users. Our app isn't their app.
5. **Write down what you learned** — even if you don't ship the pattern, document the study so the next worker doesn't repeat it.

---

## Where the TCG-specific study lives

One Piece / TCG-app competitors (Collectr, Manabox, Moxfield, OPTCGSim, Egman, OP.TCG iOS app) are in [docs/pm/07_OPTCG_STUDY.md](../pm/07_OPTCG_STUDY.md). That doc carries user quotes from App Store reviews, Reddit threads, and Discord screenshots.

The division: *this* doc is about craft (how Linear handles forms); *that* doc is about domain (how Collectr prices cards).
