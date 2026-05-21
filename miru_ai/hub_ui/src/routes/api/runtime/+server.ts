// BFF proxy for Glance service controls (PRO-932).
//
// Accepts POST { service: 'miru-ai' | 'pm-storefront' | 'learner', action: 'start' | 'stop' | 'restart' }
// and forwards to the appropriate Flask /api/runtime/<action>/<target> endpoint.
// Flask handles access-guard (loopback + Tailscale); requests arrive from SvelteKit on loopback.
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const FLASK_BASE = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

const SERVICE_MAP: Record<string, string> = {
	'miru-ai': '18765',
	'pm-storefront': '18080',
	learner: 'learner'
};

const VALID_ACTIONS = new Set(['start', 'stop', 'restart']);

export const POST: RequestHandler = async ({ request }) => {
	let raw: unknown;
	try {
		raw = await request.json();
	} catch {
		return json({ error: 'Request body must be valid JSON.' }, { status: 400 });
	}

	if (typeof raw !== 'object' || raw === null) {
		return json({ error: 'Expected a JSON object.' }, { status: 400 });
	}

	const body = raw as Record<string, unknown>;
	const service = typeof body.service === 'string' ? body.service.trim() : '';
	const action = typeof body.action === 'string' ? body.action.trim() : '';

	const target = SERVICE_MAP[service];
	if (!target) {
		return json({ error: `Unknown service "${service}".` }, { status: 400 });
	}
	if (!VALID_ACTIONS.has(action)) {
		return json({ error: `Unknown action "${action}".` }, { status: 400 });
	}

	let resp: Response;
	try {
		resp = await fetch(`${FLASK_BASE()}/api/runtime/${action}/${target}`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			signal: AbortSignal.timeout(120_000)
		});
	} catch {
		return json(
			{ error: 'Flask service control endpoint is unreachable.' },
			{ status: 503 }
		);
	}

	let payload: unknown;
	try {
		payload = await resp.json();
	} catch {
		payload = { error: `Flask returned a non-JSON response (HTTP ${resp.status}).` };
	}
	return json(payload, { status: resp.status });
};
