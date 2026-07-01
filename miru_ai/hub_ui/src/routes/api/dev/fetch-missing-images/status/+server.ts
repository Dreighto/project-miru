// BFF for polling the singleton image-fetcher's in-memory state.
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export const GET: RequestHandler = async () => {
	let resp: Response;
	try {
		resp = await fetch(`${flaskBaseUrl()}/api/dev/fetch-missing-images/status`, {
			signal: AbortSignal.timeout(10_000)
		});
	} catch {
		return json({ error: 'Image-fetcher endpoint unreachable.' }, { status: 503 });
	}
	let payload: unknown;
	try {
		payload = await resp.json();
	} catch {
		payload = { error: `Image-fetcher returned a non-JSON response (HTTP ${resp.status}).` };
	}
	return json(payload, { status: resp.status });
};
