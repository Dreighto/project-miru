/**
 * Storefront data-source mode.
 *
 * The storefront can run in one of two modes:
 *
 *   - 'real'  → goes to Flask /api/* (backed by card_catalog.db + pm_decks.db).
 *   - 'dummy' → goes to an in-memory dummy dataset (no network calls, no DB).
 *
 * Why this exists (PRO-910): the storefront UI is full-featured already, but
 * card_catalog.db is going to be reworked. Dummy mode is the safety net so
 * the UI keeps functioning while the catalog wiring is in flux — and so
 * future storefront work can be verified independent of the data pipeline.
 *
 * Resolution order on initial load:
 *   1. URL query `?dummy=1` (or `?dummy=0`) — sets mode and persists.
 *   2. localStorage key 'miru:storefront-mode'.
 *   3. Default 'real'.
 *
 * Dummy mode is loudly visible in the UI via DummyModeBanner; we never want
 * dummy data to be mistaken for real data.
 */

import { writable, get } from 'svelte/store';
import { browser } from '$app/environment';

export type StorefrontMode = 'real' | 'dummy';

const STORAGE_KEY = 'miru:storefront-mode';

function readInitialMode(): StorefrontMode {
	if (!browser) return 'real';
	try {
		const url = new URL(window.location.href);
		const param = url.searchParams.get('dummy');
		if (param === '1' || param === 'true') {
			localStorage.setItem(STORAGE_KEY, 'dummy');
			return 'dummy';
		}
		if (param === '0' || param === 'false') {
			localStorage.setItem(STORAGE_KEY, 'real');
			return 'real';
		}
		const stored = localStorage.getItem(STORAGE_KEY);
		if (stored === 'dummy' || stored === 'real') return stored;
	} catch {
		/* ignore — storage may be disabled */
	}
	return 'real';
}

export const storefrontMode = writable<StorefrontMode>(readInitialMode());

storefrontMode.subscribe((mode) => {
	if (!browser) return;
	try {
		localStorage.setItem(STORAGE_KEY, mode);
	} catch {
		/* ignore */
	}
});

export function setStorefrontMode(mode: StorefrontMode): void {
	storefrontMode.set(mode);
}

export function getStorefrontMode(): StorefrontMode {
	return get(storefrontMode);
}

export function isDummyMode(): boolean {
	return get(storefrontMode) === 'dummy';
}
