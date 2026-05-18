/**
 * DUMMY card dataset for PRO-910 storefront scaffolding.
 *
 * This is FAKE DATA. Every card is labelled "[DMY]" and lives in the
 * synthetic set "DUMMY01". Image paths are null on purpose — the UI
 * renders coloured tiles with the card code, which makes it obvious
 * at a glance that nothing here is a real Bandai card.
 *
 * Shape mirrors the real Flask API responses (see _real-client.ts) so
 * the UI cannot tell which client it's talking to.
 *
 * The dataset is intentionally small but deck-builder-complete:
 *   - 6 leaders spanning all 6 base OP-TCG colours
 *   - ~50 characters across cost/power/counter ranges
 *   - 6 events
 *   - 2 stages
 *   - 1 variant card to exercise the variant tab
 *
 * For each leader colour there are enough characters/events to actually
 * build a 50-card deck (mostly via the 4-copy ceiling), so the full
 * loop can be exercised without leaving dummy mode.
 *
 * When the storefront is gutted/reworked, this dataset lets the UI
 * keep functioning. When the real catalog is rebuilt, dummy mode is
 * still available as a regression check.
 */

import type {
	CardDetail,
	CardSummary,
	CardVariant,
	DeckDetail,
	DeckSummary,
	DeckValidation,
	SetSummary
} from '../api/_real-client';

export const DUMMY_SET_ID = 'DUMMY01';
export const DUMMY_SET_NAME = '[DUMMY] Test Cards — Pre-Real-Data Scaffold';

interface DummyCardSeed extends CardSummary {
	effect_text: string;
	attribute: string;
	archetype: string[];
}

function ld(
	num: number,
	color: string,
	name: string,
	life: string,
	power: string,
	effect: string,
	archetype: string[]
): DummyCardSeed {
	const code = `${DUMMY_SET_ID}-${String(num).padStart(3, '0')}`;
	return {
		code,
		name: `[DMY] ${name}`,
		type: 'Leader',
		color,
		cost: null,
		power,
		counter: '-',
		rarity: 'L',
		set_id: DUMMY_SET_ID,
		image_path: null,
		effect_text: effect,
		attribute: life ? `Life ${life}` : '',
		archetype
	};
}

function ch(
	num: number,
	color: string,
	name: string,
	cost: number,
	power: string,
	counter: string,
	attribute: string,
	effect: string,
	rarity: string = 'C',
	archetype: string[] = []
): DummyCardSeed {
	const code = `${DUMMY_SET_ID}-${String(num).padStart(3, '0')}`;
	return {
		code,
		name: `[DMY] ${name}`,
		type: 'Character',
		color,
		cost,
		power,
		counter,
		rarity,
		set_id: DUMMY_SET_ID,
		image_path: null,
		effect_text: effect,
		attribute,
		archetype
	};
}

function ev(
	num: number,
	color: string,
	name: string,
	cost: number,
	counter: string,
	effect: string,
	rarity: string = 'UC'
): DummyCardSeed {
	const code = `${DUMMY_SET_ID}-${String(num).padStart(3, '0')}`;
	return {
		code,
		name: `[DMY] ${name}`,
		type: 'Event',
		color,
		cost,
		power: '-',
		counter,
		rarity,
		set_id: DUMMY_SET_ID,
		image_path: null,
		effect_text: effect,
		attribute: '',
		archetype: []
	};
}

function st(
	num: number,
	color: string,
	name: string,
	cost: number,
	effect: string,
	rarity: string = 'R'
): DummyCardSeed {
	const code = `${DUMMY_SET_ID}-${String(num).padStart(3, '0')}`;
	return {
		code,
		name: `[DMY] ${name}`,
		type: 'Stage',
		color,
		cost,
		power: '-',
		counter: '-',
		rarity,
		set_id: DUMMY_SET_ID,
		image_path: null,
		effect_text: effect,
		attribute: '',
		archetype: []
	};
}

// 6 leaders — paired dual-colour so any chosen leader's pool is wide enough
// to build a 50-card deck (mirrors how most real OP-TCG leaders work).
const LEADERS: DummyCardSeed[] = [
	ld(1, 'Red/Green', 'Crimson Captain', '5', '5000', 'On Play: Draw 1 card.', ['Pirate']),
	ld(2, 'Green/Yellow', 'Verdant Tactician', '4', '5000', 'Activate: Rest 1 card to draw 1.', ['Marine']),
	ld(3, 'Blue/Purple', 'Azure Strategist', '4', '5000', 'Once Per Turn: Look at top 3 of deck.', ['Scholar']),
	ld(4, 'Purple/Black', 'Violet Sovereign', '5', '6000', "Don't Activate: +1 DON cost.", ['Royal']),
	ld(5, 'Black/Red', 'Obsidian Warden', '5', '5000', 'On Block: -1000 to attacker.', ['Sentinel']),
	ld(6, 'Yellow/Blue', 'Solar Oracle', '4', '5000', 'Activate Main: Reveal top card of Life.', ['Diviner'])
];

// Characters — ~10 per colour for deck-builder space.
const CHARACTERS: DummyCardSeed[] = [
	// Red
	ch(10, 'Red', 'Cannon Crew', 1, '2000', '1000', 'Strike', 'Rush.', 'C', ['Pirate']),
	ch(11, 'Red', 'Powder Specialist', 2, '3000', '1000', 'Special', 'On Play: +1000 power to a Pirate.', 'C', ['Pirate']),
	ch(12, 'Red', 'Boarding Party', 3, '5000', '2000', 'Strike', 'Double Attack.', 'UC', ['Pirate']),
	ch(13, 'Red', 'Quartermaster', 4, '6000', '1000', 'Strike', 'On Play: KO an opponent Character with 3000 or less.', 'UC', ['Pirate']),
	ch(14, 'Red', 'First Mate', 5, '7000', '1000', 'Strike', 'When Attacking: +2000 power.', 'R', ['Pirate']),
	ch(15, 'Red', 'Rival Captain', 6, '8000', '-', 'Strike', 'Banish.', 'R', ['Pirate']),
	ch(16, 'Red', 'Cannon Recruit', 1, '1000', '2000', 'Strike', '', 'C', ['Pirate']),
	ch(17, 'Red', 'Pyromancer', 3, '4000', '1000', 'Special', 'On KO: Draw 1.', 'C', ['Pirate']),
	ch(18, 'Red', 'Burning Lookout', 2, '3000', '2000', 'Strike', '', 'C', ['Pirate']),

	// Green
	ch(20, 'Green', 'Marine Recruit', 1, '2000', '1000', 'Strike', '', 'C', ['Marine']),
	ch(21, 'Green', 'Marine Lieutenant', 2, '3000', '2000', 'Slash', '', 'C', ['Marine']),
	ch(22, 'Green', 'Marine Commodore', 3, '4000', '1000', 'Slash', 'Rest 1 of opponent Cost-2 or less Characters on Play.', 'UC', ['Marine']),
	ch(23, 'Green', 'Marine Captain', 4, '5000', '1000', 'Slash', '', 'UC', ['Marine']),
	ch(24, 'Green', 'Vice Admiral', 5, '6000', '1000', 'Slash', 'Counter +1000.', 'R', ['Marine']),
	ch(25, 'Green', 'Marine HQ Reinforcement', 6, '7000', '-', 'Slash', 'Banish.', 'R', ['Marine']),
	ch(26, 'Green', 'Field Drill Sergeant', 2, '2000', '2000', 'Strike', '', 'C', ['Marine']),
	ch(27, 'Green', 'Marine Scout', 1, '1000', '2000', 'Wisdom', '', 'C', ['Marine']),
	ch(28, 'Green', 'Coastal Patrol', 3, '5000', '1000', 'Slash', '', 'C', ['Marine']),

	// Blue
	ch(30, 'Blue', 'Apprentice Scholar', 1, '1000', '2000', 'Wisdom', 'On Play: Look at top of deck.', 'C', ['Scholar']),
	ch(31, 'Blue', 'Tome Keeper', 2, '3000', '1000', 'Wisdom', '', 'C', ['Scholar']),
	ch(32, 'Blue', 'Scribe of Tides', 3, '4000', '1000', 'Wisdom', 'On Play: Draw 1, then discard 1.', 'UC', ['Scholar']),
	ch(33, 'Blue', 'Cartographer', 4, '5000', '1000', 'Wisdom', '', 'UC', ['Scholar']),
	ch(34, 'Blue', 'Arcanist', 5, '6000', '1000', 'Wisdom', 'On Play: Bounce an opponent Cost-3 or less Character.', 'R', ['Scholar']),
	ch(35, 'Blue', 'Archmage', 7, '8000', '-', 'Wisdom', 'On Play: Bounce 2 opponent Characters.', 'SR', ['Scholar']),
	ch(36, 'Blue', 'Junior Researcher', 1, '2000', '1000', 'Wisdom', '', 'C', ['Scholar']),
	ch(37, 'Blue', 'Library Guard', 2, '2000', '2000', 'Slash', '', 'C', ['Scholar']),
	ch(38, 'Blue', 'Sea Diviner', 3, '4000', '1000', 'Wisdom', '', 'C', ['Scholar']),

	// Purple
	ch(40, 'Purple', 'Court Page', 1, '2000', '1000', 'Strike', '', 'C', ['Royal']),
	ch(41, 'Purple', 'Royal Guard', 2, '3000', '2000', 'Slash', '', 'C', ['Royal']),
	ch(42, 'Purple', 'Court Mage', 3, '4000', '1000', 'Special', 'On Play: Add 1 DON to your Cost area.', 'UC', ['Royal']),
	ch(43, 'Purple', 'Spymaster', 4, '5000', '1000', 'Special', '', 'UC', ['Royal']),
	ch(44, 'Purple', 'Sovereign-Knight', 5, '6000', '1000', 'Slash', '', 'R', ['Royal']),
	ch(45, 'Purple', 'Crown Heir', 6, '8000', '-', 'Slash', 'On Play: Place 2 DON cards as active.', 'SR', ['Royal']),
	ch(46, 'Purple', 'Royal Herald', 1, '1000', '2000', 'Special', '', 'C', ['Royal']),
	ch(47, 'Purple', 'Court Steward', 2, '2000', '2000', 'Wisdom', '', 'C', ['Royal']),

	// Black
	ch(50, 'Black', 'Sentinel Recruit', 1, '2000', '1000', 'Slash', '', 'C', ['Sentinel']),
	ch(51, 'Black', 'Watcher of the Gate', 2, '3000', '2000', 'Strike', '', 'C', ['Sentinel']),
	ch(52, 'Black', 'Iron Inspector', 3, '4000', '1000', 'Slash', 'On Play: -1000 cost to an opponent Cost-3 or less Character until end of turn.', 'UC', ['Sentinel']),
	ch(53, 'Black', 'Black-Cloak Hunter', 4, '5000', '1000', 'Slash', '', 'UC', ['Sentinel']),
	ch(54, 'Black', 'Grand Inquisitor', 5, '6000', '1000', 'Special', '', 'R', ['Sentinel']),
	ch(55, 'Black', 'Warden of the Spire', 7, '8000', '-', 'Slash', 'On Play: Banish 1 of opponent Cost-4 or less Characters.', 'SR', ['Sentinel']),
	ch(56, 'Black', 'Gate Keeper', 1, '1000', '2000', 'Slash', '', 'C', ['Sentinel']),
	ch(57, 'Black', 'Vault Sentry', 2, '2000', '2000', 'Strike', '', 'C', ['Sentinel']),

	// Yellow
	ch(60, 'Yellow', 'Oracle Apprentice', 1, '2000', '1000', 'Special', '', 'C', ['Diviner']),
	ch(61, 'Yellow', 'Sun-Touched Acolyte', 2, '3000', '1000', 'Special', '', 'C', ['Diviner']),
	ch(62, 'Yellow', 'Diviner of Fate', 3, '4000', '1000', 'Special', 'Trigger: Add this card to your hand.', 'UC', ['Diviner']),
	ch(63, 'Yellow', 'Light-Bringer', 4, '5000', '1000', 'Ranged', '', 'UC', ['Diviner']),
	ch(64, 'Yellow', 'Solar Sage', 5, '6000', '1000', 'Special', '', 'R', ['Diviner']),
	ch(65, 'Yellow', 'Avatar of the Sun', 7, '8000', '-', 'Ranged', 'On Play: Add 1 card from Life to hand.', 'SR', ['Diviner']),
	ch(66, 'Yellow', 'Acolyte of Dawn', 1, '1000', '2000', 'Special', '', 'C', ['Diviner']),
	ch(67, 'Yellow', 'Templar Initiate', 2, '2000', '2000', 'Slash', '', 'C', ['Diviner'])
];

const EVENTS: DummyCardSeed[] = [
	ev(80, 'Red', 'Broadside Volley', 1, '2000', 'Counter: +2000 power to one of your Characters during this battle.'),
	ev(81, 'Green', 'Tactical Retreat', 2, '1000', 'Main: Return one of your Cost-3 or less Characters to its owners hand.'),
	ev(82, 'Blue', 'Foresight', 1, '1000', 'Main: Look at top 3 cards of your deck.'),
	ev(83, 'Purple', 'Royal Decree', 3, '-', 'Main: Add 1 DON to your DON deck.', 'R'),
	ev(84, 'Black', 'Cell Block', 2, '1000', 'Main: Rest 1 of opponent Cost-4 or less Characters.'),
	ev(85, 'Yellow', 'Solar Flare', 2, '2000', 'Counter: -3000 to an attacker during this battle.')
];

const STAGES: DummyCardSeed[] = [
	st(90, 'Red', 'Pirate Cove', 1, 'Activate: Add 1 DON from your DON deck as rested.'),
	st(91, 'Blue', 'Hidden Library', 2, 'Activate: Look at the top card of your deck; you may place it on bottom.')
];

// Build the variant for one card (exercises the variant tab in the UI).
const VARIANTS_BY_CODE: Record<string, CardVariant[]> = {
	[`${DUMMY_SET_ID}-014`]: [
		{ variant_key: 'base', label: 'Base', image_path: null, market_price: 0.5 },
		{ variant_key: 'alt', label: 'Alt-art (DMY)', image_path: null, market_price: 12.75 }
	]
};

export const DUMMY_CARDS: DummyCardSeed[] = [...LEADERS, ...CHARACTERS, ...EVENTS, ...STAGES];

export const DUMMY_SETS: SetSummary[] = [
	{ set_id: DUMMY_SET_ID, set_name: DUMMY_SET_NAME, card_count: DUMMY_CARDS.length }
];

export function dummyCardSummary(seed: DummyCardSeed): CardSummary {
	const { code, name, type, color, cost, power, counter, rarity, set_id, image_path } = seed;
	return { code, name, type, color, cost, power, counter, rarity, set_id, image_path };
}

export function dummyCardDetail(seed: DummyCardSeed): CardDetail {
	const variants =
		VARIANTS_BY_CODE[seed.code] ??
		[
			{
				variant_key: 'base',
				label: 'Base',
				image_path: null,
				market_price: seed.rarity === 'L' || seed.rarity === 'SR' ? 5.0 : 0.25
			}
		];
	return { ...dummyCardSummary(seed), effect_text: seed.effect_text, attribute: seed.attribute, archetype: seed.archetype, variants };
}

export function findDummyCard(code: string): DummyCardSeed | null {
	return DUMMY_CARDS.find((c) => c.code === code) ?? null;
}
