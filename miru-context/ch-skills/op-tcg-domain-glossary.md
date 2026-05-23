# Skill: op-tcg-domain-glossary

## When this skill applies

Any conversation, design, or implementation touching One Piece Card Game (OP TCG) cards, sets, rarities, mechanics, or storefront/catalog content. Load this when you need to talk about cards without re-deriving the terminology.

## Set codes

OP TCG releases come in numbered booster sets, starter decks, promo sets, and event-exclusive sets. The set code is the prefix on every card number.

**Booster sets (OP01–OPnn):**

- `OP01` — Romance Dawn (the base set, 121 cards)
- `OP02` — Paramount War
- `OP03` — Pillars of Strength
- `OP04` — Kingdoms of Intrigue
- `OP05` — Awakening of the New Era
- `OP06` — Wings of the Captain
- `OP07` — 500 Years in the Future
- `OP08` — Two Legends
- `OP09` — Emperors in the New World
- `OP10` — Royal Bloodlines
- `OP11` — A Fist of Divine Speed
- `OP12` — Legacy of the Master
- `OP13` — (release pending — confirm against Bandai before stating as fact)
- `OP14` and `OP15` likewise — verify before asserting

**Starter decks (ST01–STnn):**

Each ST set is a leader-focused preconstructed deck. `ST01` Straw Hat Crew, `ST02` Worst Generation, `ST03` The Seven Warlords, etc. Card numbers like `ST01-007`.

**Promo / event sets:**

- `PRB01`, `PRB02` — Premium Booster sets (reprints + alt arts)
- `EB01` — Extra Booster (variant collection)
- Other promos: tournament promos, judge promos, manga-included promos.

When uncertain about a set code's release status or contents, verify against the Bandai cardlist (`https://en.onepiece-cardgame.com/cardlist/`) before stating as fact. Don't assert specific card counts or release dates from memory.

## Card types

Every card has exactly one type:

| Type | What it is | Notes |
|------|-----------|-------|
| **Leader** | The deck's central card. One per deck, starts in play. | Has a Life value and a color identity. |
| **Character** | Cards you play onto the field to attack/defend. | Has Cost, Power, optional Counter value. |
| **Event** | One-shot effect cards. | Triggered, then discarded. |
| **Stage** | Persistent field cards (one at a time). | Provide ongoing effects. |

## Card attributes (on Characters and some events)

Visible icons on the card:

- **Strike** (sword icon) — melee attacker
- **Ranged** (bow/gun icon) — long-range
- **Special** (lightning icon) — supernatural / Devil Fruit-style
- **Wisdom** (book icon) — knowledge-based

Multiple attributes possible on a single card.

## Card values

- **Cost** (top-left) — what you pay in DON!! cards to play it.
- **Power** (bottom or center, often in red) — attack/defense strength, usually in thousands (e.g., 5000, 7000).
- **Counter** (top-right corner, blue +N) — bonus power when used as counter from hand.
- **Life** (Leaders only, in heart icon) — how many life cards the deck starts with.

## Colors

Every Leader has one or two colors. Other cards have one color. The five colors:

- **Red** — aggressive, low-cost, swarm
- **Green** — rest/active manipulation, tempo
- **Blue** — bounce/return, control
- **Purple** — DON!! manipulation, ramp
- **Black** — cost reduction, removal
- **Yellow** — life manipulation, late-game

(Some sets introduce dual-color leaders enabling decks that mix two colors.)

## Rarities

Visible on the card and in the print_id suffix. From most common to rarest:

- **C** — Common
- **UC** — Uncommon
- **R** — Rare
- **SR** — Super Rare
- **SEC** — Secret Rare (typically alt-art or full-art treatment)
- **L** — Leader rarity (Leader-specific)
- **P** — Promo (not in the regular pull pool)

Variant treatments (separate from base rarity):

- **alt-art** — alternative artwork printing (often SR/SEC parallels)
- **manga-art** — artwork sourced from the manga, often included as promos
- **R1 / R2** — reprint variants in PRB / Premium sets; visually similar to base but with different print_id suffix (`_r1`, `_r2`)
- **Parallel / Foil** — extra-glossy variants of a base card

In the catalog DB, `print_id` carries the suffix that distinguishes printings: `OP01-001` (base) vs `OP01-001_p3` (3rd print) vs `OP01-001_r1` (PRB reprint) vs `OP01-001::st10 alt` (legacy synthetic ID for ST10 alt-art).

## Print conventions and Bandai's source-of-truth

Bandai publishes the authoritative card list at `https://en.onepiece-cardgame.com/cardlist/`. The freewords search returns each card's printings grouped under the card number. Each printing has:

- A set code (which release it's in)
- A rarity
- An image URL pointing at the Bandai-hosted PNG

The `card_variants` table in `card_catalog.db` should mirror this: one row per printing. The format-of-record is `print_id = "OP01-NNN" or "OP01-NNN_pN"` matching Bandai's URL convention.

**Legacy `::`-style print_ids** (e.g., `OP01-016::st10 alt`) are pre-Bandai-format entries — duplicates of `_pN` rows the canonical catalog already has. Deduping these is OP01 Pass C work.

## Mechanics shorthand

These come up in deck discussions and card-effect text:

- **DON!! cards** — the resource cards each player has (10 total), spent to play characters/events.
- **Life cards** — face-down cards under the Leader that absorb damage; when reduced to 0, next damage = loss.
- **Active / Rested** — characters tap (Rest) to attack; refresh (Active) at start of turn.
- **K.O.** — destroyed (sent to discard).
- **Trash** — discard pile.
- **Hand** — cards in hand.
- **Field** — the play area.
- **On Play** / **Once Per Turn** / **When Attacking** — common trigger conditions on card effects.

## How CC uses this skill

When implementing DB work, storefront tiles, image-asset pipelines, or any code touching card data: load this glossary so you don't mis-name a field or invent a rarity tier. The `print_id` distinction (canonical `_pN` vs legacy `::` vs phantom `_r1`) drives the OP01 Pass C dedup logic.

## How CH uses this skill

When in brainstorm/architect mode about catalog design, storefront UX, or AI training corpus structure: use the precise terms. The operator works in OP TCG terminology daily; mis-naming a card type or rarity surfaces as friction.

## What this skill is NOT

- Not a complete card database — for actual card data, query `card_catalog.db`.
- Not the source of truth on set release dates / counts — verify against Bandai.
- Not a deck-building guide. Mechanics shorthand here is just enough to talk about cards, not enough to deck-build.
