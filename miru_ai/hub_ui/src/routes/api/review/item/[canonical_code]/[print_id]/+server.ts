// BFF proxy for the Review surface's evidence panel (PRO-935).
// Forwards GET /api/shadow-review/item/<code>/<print_id>?contributing_model=<m>
// to Flask and passes the response through verbatim so the client gets the
// full field_outcomes / bandai_url / tcgplayer_url payload.
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export const GET: RequestHandler = async ({ params, url }) => {
	const contributing_model = url.searchParams.get('contributing_model') ?? '';
	const { canonical_code, print_id } = params;

	let resp: Response;
	try {
		const flaskUrl =
			`${flaskBaseUrl()}/api/shadow-review/item/${encodeURIComponent(canonical_code)}` +
			`/${encodeURIComponent(print_id)}?contributing_model=${encodeURIComponent(contributing_model)}`;
		resp = await fetch(flaskUrl, { signal: AbortSignal.timeout(10_000) });
	} catch {
		return json({ error: 'Review service unreachable.' }, { status: 503 });
	}

	let payload: unknown;
	try {
		payload = await resp.json();
	} catch {
		payload = { error: `Review service returned a non-JSON response (HTTP ${resp.status}).` };
	}
	return json(payload, { status: resp.status });
};
