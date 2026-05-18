/**
 * Flask API client for the PM storefront.
 *
 * Dev: requests go through Vite's /api proxy -> http://127.0.0.1:18080
 * Prod: requests are same-origin (Flask serves both the SPA and the API on 18080)
 *
 * All endpoints here mirror the routes defined in `pm/routes/api.py`.
 */

export class ApiError extends Error {
	status: number;
	payload: unknown;
	constructor(status: number, message: string, payload: unknown = null) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
		this.payload = payload;
	}
}

export interface ApiFetchOptions extends RequestInit {
	/** Parse response as JSON (default true). */
	json?: boolean;
}

/**
 * Low-level fetch wrapper. Throws ApiError on non-2xx responses.
 */
export async function apiFetch<T = unknown>(
	path: string,
	options: ApiFetchOptions = {}
): Promise<T> {
	const { json = true, headers, ...rest } = options;
	const url = path.startsWith('/') ? path : `/${path}`;
	const res = await fetch(url, {
		credentials: 'same-origin',
		headers: {
			Accept: 'application/json',
			...(json && rest.method && rest.method !== 'GET'
				? { 'Content-Type': 'application/json' }
				: {}),
			...headers
		},
		...rest
	});
	if (!res.ok) {
		let payload: unknown = null;
		try {
			payload = await res.json();
		} catch {
			/* ignore */
		}
		throw new ApiError(res.status, `API ${res.status} ${res.statusText} at ${url}`, payload);
	}
	if (!json) return undefined as T;
	return (await res.json()) as T;
}

/**
 * Full catalog fetch — matches /api/cards-json.
 * Heavy payload; callers should cache aggressively (Cache API + IndexedDB).
 */
export async function getCards(): Promise<unknown> {
	return apiFetch('/api/cards-json');
}

/**
 * Variant image + price lookup for a single card code.
 * Matches /api/card-variants/<code>.
 */
export async function getCardVariants(code: string): Promise<unknown> {
	return apiFetch(`/api/card-variants/${encodeURIComponent(code)}`);
}

/**
 * Single-card price lookup. Matches /api/card-price/<code>.
 */
export async function getCardPrice(code: string): Promise<unknown> {
	return apiFetch(`/api/card-price/${encodeURIComponent(code)}`);
}

// ── Phase 3/4 storefront shapes ───────────────────────────────────────

export interface SetSummary {
	set_id: string;
	set_name: string;
	card_count: number;
}

export interface CardSummary {
	code: string;
	name: string;
	type: string;
	color: string;
	cost: number | null;
	power: string;
	counter: string;
	rarity: string;
	set_id: string;
	image_path: string | null;
}

export interface CardsPage {
	cards: CardSummary[];
	total: number;
	page: number;
	limit: number;
	pages: number;
}

export interface CardVariant {
	variant_key: string;
	label: string;
	image_path: string | null;
	market_price: number | null;
}

export interface CardDetail extends CardSummary {
	effect_text: string;
	attribute: string;
	archetype: string[];
	variants: CardVariant[];
}

export interface DeckSummary {
	id: string;
	name: string;
	leader_code: string;
	leader_name: string;
	card_count: number;
	created_at: string;
	updated_at: string;
}

export interface DeckCardEntry {
	code: string;
	count: number;
	name?: string;
	type?: string;
	color?: string;
	cost?: number | null;
	power?: string;
}

export interface DeckDetail {
	id: string;
	name: string;
	leader_code: string;
	leader_name: string;
	cards: DeckCardEntry[];
	created_at: string;
	updated_at: string;
}

export interface DeckValidation {
	valid: boolean;
	errors: string[];
	warnings: string[];
	summary: {
		total_cards: number;
		leader: string;
		colors: string[];
		cost_curve: Record<string, number>;
	};
}

export interface CardsQuery {
	set?: string;
	page?: number;
	limit?: number;
	color?: string;
	type?: string;
}

export async function getSets(): Promise<{ sets: SetSummary[] }> {
	return apiFetch<{ sets: SetSummary[] }>('/api/sets');
}

export async function getCardsList(q: CardsQuery): Promise<CardsPage> {
	const params = new URLSearchParams();
	if (q.set) params.set('set', q.set);
	if (q.page) params.set('page', String(q.page));
	if (q.limit) params.set('limit', String(q.limit));
	if (q.color) params.set('color', q.color);
	if (q.type) params.set('type', q.type);
	return apiFetch<CardsPage>(`/api/cards?${params.toString()}`);
}

export async function getCardDetail(code: string): Promise<CardDetail> {
	return apiFetch<CardDetail>(`/api/cards/${encodeURIComponent(code)}`);
}

export async function getDecks(): Promise<{ decks: DeckSummary[] }> {
	return apiFetch<{ decks: DeckSummary[] }>('/api/decks');
}

export async function createDeck(body: {
	name: string;
	leader_code: string;
	cards: { code: string; count: number }[];
	id?: string;
}): Promise<DeckDetail> {
	return apiFetch<DeckDetail>('/api/decks', {
		method: 'POST',
		body: JSON.stringify(body)
	});
}

export async function getDeck(id: string): Promise<DeckDetail> {
	return apiFetch<DeckDetail>(`/api/decks/${encodeURIComponent(id)}`);
}

export async function validateDeck(id: string): Promise<DeckValidation> {
	return apiFetch<DeckValidation>(`/api/decks/${encodeURIComponent(id)}/validate`, {
		method: 'POST'
	});
}
