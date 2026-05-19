// Single source of truth for server-side Flask calls (used by Tickets C/D/E BFFs).
// Reads MIRU_FLASK_BASE_URL from env; defaults to http://127.0.0.1:18765.
import { env } from '$env/dynamic/private';

const BASE_URL = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export async function fetchFlask<T = unknown>(path: string, init?: RequestInit): Promise<T> {
	const url = `${BASE_URL()}${path}`;
	const res = await fetch(url, { ...init, signal: AbortSignal.timeout(10_000) });
	if (!res.ok) {
		throw new Error(`Flask ${path} → HTTP ${res.status}`);
	}
	return res.json() as Promise<T>;
}
