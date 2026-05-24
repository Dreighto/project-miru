// BFF proxy: list local OPTCG_Images files that match a canonical_code.
// Used by the Review-page Attach modal's "Local files" tab.
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export const GET: RequestHandler = async ({ params }) => {
	const { canonical_code } = params;
	let resp: Response;
	try {
		resp = await fetch(
			`${flaskBaseUrl()}/api/shadow-review/local-images/${encodeURIComponent(canonical_code)}`,
			{ signal: AbortSignal.timeout(10_000) }
		);
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
