# Miru Card Insight Upgrade / Overwrite Policy

This document defines when Miru may replace an existing card insight with a new one, and when it must preserve the existing text.

The goal is simple: insights should get better over time, never worse.

---

## 1. Quality Tiers

Every insight is classified into one of four quality tiers, from lowest to highest:

| Tier | Rank | Definition |
|---|---|---|
| **generic** | 0 | Formulaic template text with no strategic value. Restates obvious facts or uses filler phrasing. Does not explain why the card matters. |
| **contextual** | 1 | Adds some context (set, color, type, price) but does not explain strategic role or archetype relevance. |
| **strategic** | 2 | Explains the card's role, archetype relevance, package membership, core/flex status, or meta position. |
| **evidenced** | 3 | Strategic content backed by strong evidence — high confidence score (≥0.70) derived from meaningful sample sizes. |

### Classification rules

An insight is classified as **generic** if it matches known template patterns (e.g. "in Miru's verified card layer," "currently filed as a," "synergy-first rather than standalone tech," "generic filler slot") and does not contain strategic content signals.

An insight is classified as **strategic** or **evidenced** if it contains multiple strategic content signals — words like "core," "flex," "shell," "package," "variant," "archetype," "build," "meta," "staple," "inclusion," "consistently," "every," "most." The distinction between strategic and evidenced is confidence: ≥0.70 confidence elevates strategic to evidenced.

Everything else is **contextual** — it has some value but doesn't explain strategic relevance.

---

## 2. Overwrite Rules

When a new insight candidate exists for a card+type slot that already has a stored insight:

| Condition | Action |
|---|---|
| New tier **higher** than existing tier | **REPLACE** — the new insight is materially better |
| New tier **equal** to existing tier, and new confidence > existing confidence + 0.05 | **REPLACE** — meaningfully higher evidence quality |
| New tier **equal** to existing tier, confidence within 0.05 | **PRESERVE** existing — avoid unnecessary churn |
| New tier **lower** than existing tier | **PRESERVE** existing — never downgrade |
| Existing slot has no insight, new candidate exists | **INSERT** — new data, no conflict |
| Existing slot has an insight, no new candidate | **PRESERVE** existing — do not delete working insights |

### Key principles

- **Never downgrade.** A stronger insight is never replaced by a weaker one, regardless of recency.
- **No blind deletion.** The sync process does not delete existing insights before evaluating replacements. Insights without new candidates are preserved.
- **Confidence matters within tiers.** When two insights are at the same quality tier, the one with meaningfully higher confidence wins. Small confidence differences (≤0.05) are not worth churning over.
- **Recency does not trump quality.** A newer generic insight does not replace an older strategic one.

---

## 3. What Counts as Weak / Generic Insight

An insight is considered weak or generic if it exhibits any of these patterns:

- **Formulaic template text** — mechanically assembled from card fields without strategic reasoning (e.g., "This card lines up best with Strike, Slash shells in Miru's verified card layer")
- **Vague usefulness claims** — says the card "works best" or "is synergy-first" without explaining why or where
- **Restating obvious card facts** — repeats information visible in the card's UI fields (cost, color, type) without adding context
- **No archetype or meta context** — does not reference where the card appears in actual builds
- **Filler phrasing** — uses padding language like "in Miru's verified dossier" or "in Miru's verified card layer" as the core of the insight rather than as attribution
- **No evidence basis** — confidence is low and the text does not acknowledge the uncertainty

---

## 4. What Counts as a Meaningful Upgrade

A replacement insight is considered stronger if it adds one or more of:

- **Explains why the card matters** — not just what it is, but why a player would include it
- **Explains where it appears** — names specific archetypes, leaders, or build patterns
- **Names the card's role** — core, flex, variant-specific, tech slot
- **Explains strategic relevance** — package membership, shared shell role, meta position
- **Adds a gameplay tip** — concrete play-pattern advice tied to evidence
- **Adds meta context** — how the card fits into the competitive landscape
- **Adds relevant lore** — One Piece story connection that genuinely contextualizes the card's role (not decoration)
- **Cites higher confidence** — backed by more decks, more archetypes, or verified data

---

## 5. Confidence Interaction

- **Low-confidence insight (tier: generic or contextual, confidence < 0.55)** does not replace medium or strong existing insight.
- **Medium-confidence insight (strategic, confidence 0.55–0.69)** can replace generic text but not evidenced text.
- **Strong-confidence insight (evidenced, confidence ≥ 0.70)** can replace anything at or below its tier.
- When the pipeline has no new candidate for a slot, the existing insight is preserved — even if it is generic. Something is better than nothing.

---

## 6. Output Quality Rules

All Miru insights — whether new, preserved, or upgraded — must follow the voice and quality standards defined in `docs/miru_behavior_spec.md`:

- Useful: explains something the player cannot trivially see
- Concise: 2–4 sentences for primary insight
- Optional: never blocks the user or clutters the UI
- Not filler: every sentence carries signal
- Written in Miru's voice: first person, calm, modest, transparent

---

## 7. Known Generic Patterns

The following text patterns are markers of generic/template insights. Insights containing these (without compensating strategic content) are classified as tier `generic`:

```
"in Miru's verified card layer"
"in Miru's verified dossier"
"currently filed as a"
"currently anchored to"
"synergy-first rather than standalone tech"
"generic filler slot"
"Miru treats it as"
"Miru's verified text suggests"
```

This list should grow as new template patterns are identified.

---

## 8. Implementation

The upgrade policy is enforced in `tools/miru_project_sync.py`:

- **`classify_insight_quality(text, confidence)`** — returns a quality tier string
- **`should_replace_insight(existing_tier, existing_confidence, new_tier, new_confidence)`** — returns True/False
- **`sync_miru_card_insights()`** — modified to load existing insights first, compare before writing, and preserve stronger existing text

The `miru_card_insights` table gains one additive column: `quality_tier TEXT NOT NULL DEFAULT ''`.

---

## Appendix: Decision Examples

### Preserve existing

Existing (strategic, confidence 0.72):
> "I see Roronoa Zoro in almost every Red Luffy build — he's core shell that holds the archetype together."

New candidate (generic, confidence 0.65):
> "Roronoa Zoro lines up best with Strike, Slash shells in Miru's verified card layer."

**Decision: PRESERVE.** Existing is strategic (rank 2) with higher confidence. New is generic (rank 0). Never downgrade.

### Replace existing

Existing (generic, confidence 0.58):
> "Radical Beam!! is currently filed as a Red Event piece, so Miru treats it as synergy-first rather than standalone tech."

New candidate (strategic, confidence 0.68):
> "I see Radical Beam in every Red Luffy build I've tracked. It's one of the non-negotiable cards in the shared shell — a 2-cost event that protects your board investment during critical early turns."

**Decision: REPLACE.** New is strategic (rank 2), higher confidence, and explains the card's role. Existing is generic (rank 0) template text.

### Reject new insight

Existing (evidenced, confidence 0.78):
> "From the decks I've tracked, Marco is a core 5-cost character in nearly every Red Luffy and Whitebeard build. He bridges the midgame and is rarely cut."

New candidate (contextual, confidence 0.52):
> "Marco is tracked in PARAMOUNT WAR [OP02] with Strike affiliations in Miru's verified dossier."

**Decision: PRESERVE.** Existing is evidenced (rank 3), high confidence. New is contextual (rank 1), low confidence. The existing insight is categorically better.
