/**
 * Dummy storefront client — in-memory implementation of the Flask API.
 *
 * Same function signatures as _real-client.ts, but data comes from the
 * fake dataset in $lib/data/dummy-cards. Decks live in a session-local
 * Map (lost on reload — intentional; dummy mode is for verifying the UI
 * works, not persisting state).
 *
 * Why this exists (PRO-910): keeps the storefront UI exercisable while
 * card_catalog.db is being reworked.
 */

import {
	DUMMY_CARDS,
	DUMMY_SETS,
	dummyCardDetail,
	dummyCardSummary,
	findDummyCard
} from '../data/dummy-cards';
import type {
	CardDetail,
	CardSummary,
	CardsPage,
	CardsQuery,
	DeckDetail,
	DeckSummary,
	DeckValidation,
	SetSummary
} from './_real-client';

function splitColors(color: string): string[] {
	return (color || '')
		.split('/')
		.map((c) => c.trim())
		.filter(Boolean);
}

export async function getCards(): Promise<unknown> {
	return { cards: DUMMY_CARDS.map(dummyCardSummary) };
}

export async function getCardVariants(code: string): Promise<unknown> {
	const seed = findDummyCard(code);
	if (!seed) return { code, variants: [] };
	const detail = dummyCardDetail(seed);
	return { code, variants: detail.variants };
}

export async function getCardPrice(code: string): Promise<unknown> {
	const seed = findDummyCard(code);
	if (!seed) return { found: false, code };
	const detail = dummyCardDetail(seed);
	const base = detail.variants.find((v) => v.variant_key === 'base');
	return {
		found: true,
		code,
		market: base?.market_price ?? null,
		low: base?.market_price ?? null,
		name: seed.name,
		rarity: seed.rarity,
		alt_art_market: null
	};
}

export async function getSets(): Promise<{ sets: SetSummary[] }> {
	return { sets: DUMMY_SETS };
}

export async function getCardsList(q: CardsQuery): Promise<CardsPage> {
	let rows = DUMMY_CARDS.slice();
	if (q.set) rows = rows.filter((c) => c.set_id === q.set);
	if (q.color) rows = rows.filter((c) => splitColors(c.color).includes(q.color!));
	if (q.type) rows = rows.filter((c) => c.type === q.type);

	const limit = q.limit ?? 40;
	const page = q.page ?? 1;
	const total = rows.length;
	const pages = total === 0 ? 0 : Math.ceil(total / limit);
	const start = (page - 1) * limit;
	const cards = rows.slice(start, start + limit).map(dummyCardSummary);
	return { cards, total, page, limit, pages };
}

export async function getCardDetail(code: string): Promise<CardDetail> {
	const seed = findDummyCard(code);
	if (!seed) throw new Error(`Dummy card not found: ${code}`);
	return dummyCardDetail(seed);
}

// ── In-memory deck store ────────────────────────────────────────────
type StoredDeck = {
	id: string;
	name: string;
	leader_code: string;
	cards: { code: string; count: number }[];
	created_at: string;
	updated_at: string;
};

const DECKS = new Map<string, StoredDeck>();

function newId(): string {
	return 'dmy-deck-' + Math.random().toString(36).slice(2, 10);
}

function nowIso(): string {
	return new Date().toISOString();
}

function leaderNameFor(code: string): string {
	const ldr = findDummyCard(code);
	return ldr ? ldr.name : code;
}

function hydrateDeck(stored: StoredDeck): DeckDetail {
	return {
		id: stored.id,
		name: stored.name,
		leader_code: stored.leader_code,
		leader_name: leaderNameFor(stored.leader_code),
		cards: stored.cards.map((entry) => {
			const seed = findDummyCard(entry.code);
			return {
				code: entry.code,
				count: entry.count,
				name: seed?.name,
				type: seed?.type,
				color: seed?.color,
				cost: seed?.cost,
				power: seed?.power
			};
		}),
		created_at: stored.created_at,
		updated_at: stored.updated_at
	};
}

export async function getDecks(): Promise<{ decks: DeckSummary[] }> {
	const decks: DeckSummary[] = [...DECKS.values()].map((d) => ({
		id: d.id,
		name: d.name,
		leader_code: d.leader_code,
		leader_name: leaderNameFor(d.leader_code),
		card_count: d.cards.reduce((s, e) => s + e.count, 0),
		created_at: d.created_at,
		updated_at: d.updated_at
	}));
	return { decks };
}

export async function createDeck(body: {
	name: string;
	leader_code: string;
	cards: { code: string; count: number }[];
	id?: string;
}): Promise<DeckDetail> {
	const leader = findDummyCard(body.leader_code);
	if (!leader || leader.type !== 'Leader') {
		throw new Error(`Leader not found: ${body.leader_code}`);
	}
	const id = body.id ?? newId();
	const existing = DECKS.get(id);
	const stored: StoredDeck = {
		id,
		name: body.name.trim() || `${leader.name} Deck`,
		leader_code: body.leader_code,
		cards: body.cards.filter((e) => e.count > 0),
		created_at: existing?.created_at ?? nowIso(),
		updated_at: nowIso()
	};
	DECKS.set(id, stored);
	return hydrateDeck(stored);
}

export async function getDeck(id: string): Promise<DeckDetail> {
	const stored = DECKS.get(id);
	if (!stored) throw new Error(`Deck not found: ${id}`);
	return hydrateDeck(stored);
}

export async function validateDeck(id: string): Promise<DeckValidation> {
	const stored = DECKS.get(id);
	if (!stored) throw new Error(`Deck not found: ${id}`);
	const leader = findDummyCard(stored.leader_code);
	const leaderColors = leader ? splitColors(leader.color) : [];

	const errors: string[] = [];
	const warnings: string[] = [];

	const totalCards = stored.cards.reduce((s, e) => s + e.count, 0);
	if (totalCards !== 50) {
		errors.push(`Deck has ${totalCards} cards (must be exactly 50).`);
	}

	const counts: Record<string, number> = {};
	for (const entry of stored.cards) counts[entry.code] = (counts[entry.code] ?? 0) + entry.count;
	for (const [code, count] of Object.entries(counts)) {
		if (count > 4) errors.push(`${code}: ${count} copies (max 4).`);
	}

	for (const entry of stored.cards) {
		const seed = findDummyCard(entry.code);
		if (!seed) {
			errors.push(`${entry.code}: card not found.`);
			continue;
		}
		if (seed.type === 'Leader') {
			errors.push(`${entry.code}: leaders cannot be added to the main deck.`);
			continue;
		}
		if (leaderColors.length > 0) {
			const cardColors = splitColors(seed.color);
			const matchesLeader = cardColors.some((cc) => leaderColors.includes(cc));
			if (!matchesLeader) {
				errors.push(`${entry.code}: color (${seed.color}) does not match leader (${leader?.color}).`);
			}
		}
	}

	const costCurve: Record<string, number> = {};
	const colors = new Set<string>();
	for (const entry of stored.cards) {
		const seed = findDummyCard(entry.code);
		if (!seed) continue;
		for (const c of splitColors(seed.color)) colors.add(c);
		const cost = seed.cost;
		if (cost === null || cost === undefined) continue;
		const key = cost >= 7 ? '7+' : String(cost);
		costCurve[key] = (costCurve[key] ?? 0) + entry.count;
	}

	if (totalCards < 50) warnings.push(`Add ${50 - totalCards} more cards to reach 50.`);

	return {
		valid: errors.length === 0,
		errors,
		warnings,
		summary: {
			total_cards: totalCards,
			leader: stored.leader_code,
			colors: [...colors],
			cost_curve: costCurve
		}
	};
}
