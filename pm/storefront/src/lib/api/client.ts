/**
 * Storefront API facade — dispatches to real or dummy client based on mode.
 *
 * Existing UI code (cards/deck-builder/leaders/profile pages) imports from
 * '$lib/api/client' and is unchanged. This module re-exports the type
 * surface from _real-client.ts and exports each function with a runtime
 * check on the storefront mode store (see ./mode).
 *
 * Mode 'real'  → _real-client.ts (Flask /api/* backed by card_catalog.db).
 * Mode 'dummy' → _dummy-client.ts (in-memory fake data).
 *
 * The two impls share identical signatures, so the UI cannot tell which
 * one it's talking to. Dummy mode is visibly announced via the
 * DummyModeBanner so it cannot be mistaken for real data.
 */

import { getStorefrontMode } from './mode';
import * as real from './_real-client';
import * as dummy from './_dummy-client';

// Re-export types — the dummy client uses these same types.
export type {
	ApiFetchOptions,
	SetSummary,
	CardSummary,
	CardsPage,
	CardVariant,
	CardDetail,
	DeckSummary,
	DeckCardEntry,
	DeckDetail,
	DeckValidation,
	CardsQuery
} from './_real-client';
export { ApiError, apiFetch } from './_real-client';

function pick<K extends keyof typeof real>(name: K): (typeof real)[K] {
	return getStorefrontMode() === 'dummy'
		? (dummy[name as keyof typeof dummy] as unknown as (typeof real)[K])
		: real[name];
}

export const getCards = ((): Promise<unknown> => pick('getCards')()) as typeof real.getCards;
export const getCardVariants = ((code: string) =>
	pick('getCardVariants')(code)) as typeof real.getCardVariants;
export const getCardPrice = ((code: string) =>
	pick('getCardPrice')(code)) as typeof real.getCardPrice;
export const getSets = (() => pick('getSets')()) as typeof real.getSets;
export const getCardsList = ((q) => pick('getCardsList')(q)) as typeof real.getCardsList;
export const getCardDetail = ((code: string) =>
	pick('getCardDetail')(code)) as typeof real.getCardDetail;
export const getDecks = (() => pick('getDecks')()) as typeof real.getDecks;
export const createDeck = ((body) => pick('createDeck')(body)) as typeof real.createDeck;
export const getDeck = ((id: string) => pick('getDeck')(id)) as typeof real.getDeck;
export const validateDeck = ((id: string) => pick('validateDeck')(id)) as typeof real.validateDeck;
