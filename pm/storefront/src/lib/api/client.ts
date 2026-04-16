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
			...(json && rest.method && rest.method !== 'GET' ? { 'Content-Type': 'application/json' } : {}),
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
