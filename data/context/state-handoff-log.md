# Project Miru — State Handoff Log

> **Rolling thread-to-thread handoff for Project Miru.** Overwritten at each
> CH thread close — current-state only, NOT an append log. The next thread
> reads this on boot so work continues instead of restarting.
>
> **Canonical location:** `D:\dev\miru\data\context\state-handoff-log.md`
> — in the Miru repo. No per-project state lives kernel-side.

---

## Last updated

2026-05-19 — CH dev-page design session (Ink + Geist locked).

## Next thread purpose

**The dev-page design gate is CLOSED.** Next thread either kicks off the
unblocked UI tickets with CC, or moves to whatever the operator wants to do
next. No design work is pending.

## What is DONE — do not reopen

The Miru AI dev page (SvelteKit on **18768**; Flask API on **18765**) is
FUNCTIONALLY and VISUALLY design-complete. Logged in the `decisions` table
of Project Memory (`miru_memory.db`):

- **Three surfaces.** Glance (four-question status + Tailscale-bound
  service controls), Voyage (corrected island-route model), Review (QA
  verification flow).
- **QA verification flow.** Five stages; Stage 4 = three doors
  (A fix / B approve / C system-fault); Defer = a queue-level skip, NOT a
  door; five-rung confidence score separate from `promotion_status`.
- **Three hardening decisions.** Model-collusion fix (Bandai-trace
  agreement, not just answer-agreement). Door B override marker
  (append-only event with snapshot hash). Derived-card attenuation
  (`derived_from` field; effective score capped by lowest parent).
- **Voyage model.** Islands = real OP islands in canon order, as
  MILESTONES (not sets). Sets = the distance sailed. Route open-ended.
  Egghead is NOT the end (manga ongoing). Ship sits near the start.
- **Schema.** Three additions to `miru_learning_pool.db` scoped as ONE
  coordinated migration. DB exists on disk (1.5 MB, 348 rows).
- **Design system — NEW 2026-05-19.** Primary palette **Ink** (black +
  brass, single-accent, dark-only). Typography **Geist + Geist Mono**.
  Authoritative record: `decisions` domain
  `miru-ai-dev-page-design-system-2026-05-19`. Section 8 of the debrief
  has the full token list. LogueOS Console inherit was considered and
  rejected (Console serves a different purpose). Rosinante was considered
  seriously through three variants and a head-to-head — retained as a
  PLANNED theme-switcher alternate (follow-up ticket post UI tickets 4–6,
  NOT v1).

## What's unblocked

All six suggested tickets from debrief section 7 are now actionable:

- **Tickets 1–3** (backend: investigate `miru_learning_pool.db`, schema
  migration, QA-flow backend) were already independent of the design gate.
- **Tickets 4–6** (UI: Review wiring, Glance service controls, Voyage
  rebuild) are NOW unblocked. They build against the Ink + Geist system
  in debrief section 8.
- **Follow-up ticket** (Rosinante theme switcher) is planned post 4–6,
  not v1.

None of these tickets are filed in Linear yet. Operator has not authorized
filing — CH should ask before creating the Linear tickets.

## State of the CC handoff

CC read the debrief in the prior session. The debrief was updated this
session (sections 0, 7, 8) to record the design-system lock. CC re-reads
the debrief before ticketing.

## Open housekeeping (for CC, not the next CH thread)

- `THE_ONE_PIECE.md` boot sequence + project instructions: confirm they now
  point the handoff at `data/context/state-handoff-log.md` and not the dead
  kernel path. (CC was correcting this — verify it landed.)
- `data/peer_reviews/state-handoff-log.md` — old temporary handoff file;
  superseded by this one. Safe to remove.
- `data/peer_reviews/_ch_write_probe.md` leftover tombstone — remove.
- Confirm the 18765-vs-18768 port canon corrections landed across the repo.

## Reference mockups from the design session

All three are session-scoped (`/mnt/user-data/outputs/`, will NOT persist):

- `miru-devpage-type-palette-mockup.html` — initial 3 palettes × 3 type
  pairings on a Glance reference screen.
- `miru-rosinante-variants-mockup.html` — three Rosinante variants
  (Minion Night / Corazon / Feather) on the same screen.
- `miru-rosinante-vs-ink-mockup.html` — head-to-head Rosinante vs Ink
  with five type pairings switchable across both. The basis for the
  final lock.

If durable reference is wanted, CC can rebuild any of these as Storybook
fixtures during the UI ticket work.

## Next action

Operator's call: file the Linear tickets (1–6 + the switcher follow-up) and
brief CC off `miru-dev-2.0-debrief.md`, or move to a different thread of
work. CH waits for explicit go-ahead before filing tickets.
