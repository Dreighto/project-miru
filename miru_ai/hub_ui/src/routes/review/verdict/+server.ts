// BFF proxy for the Review surface's verdict buttons (PRO-929).
//
// The browser cannot POST to Flask directly, so this server route validates the
// request and forwards it to Flask's POST /api/shadow-review/verdict, passing
// Flask's own status and body straight back so real errors stay visible.
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';
import { parseVerdictBody } from './validation';

// Flask base-URL resolution mirrors $lib/server/flask.ts. Resolved here rather
// than reusing fetchFlask() because the verdict POST needs Flask's real status
// code and error body surfaced — the GET helper collapses every non-2xx into a
// generic Error and discards the response body.
const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export const POST: RequestHandler = async ({ request }) => {
	let raw: unknown;
	try {
		raw = await request.json();
	} catch {
		return json({ error: 'Request body must be valid JSON.' }, { status: 400 });
	}

	const parsed = parseVerdictBody(raw);
	if (!parsed.ok) {
		return json({ error: parsed.error }, { status: 400 });
	}

	let resp: Response;
	try {
		resp = await fetch(`${flaskBaseUrl()}/api/shadow-review/verdict`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({
				...parsed.value,
				// submit_verdict() rejects an empty sources_checked for
				// correct/wrong verdicts; stamp the surface so the override log
				// records where the verdict came from.
				sources_checked: ['dev-page-review']
			}),
			signal: AbortSignal.timeout(10_000)
		});
	} catch {
		// Network failure / timeout — Flask unreachable.
		return json(
			{ error: 'The review service is unreachable. Try again in a moment.' },
			{ status: 503 }
		);
	}

	// Pass Flask's response through verbatim. On success Flask returns
	// { ok, new_approval_state, event_logged }; on bad input { error } + 400.
	let payload: unknown;
	try {
		payload = await resp.json();
	} catch {
		payload = { error: `Review service returned a non-JSON response (HTTP ${resp.status}).` };
	}
	return json(payload, { status: resp.status });
};
