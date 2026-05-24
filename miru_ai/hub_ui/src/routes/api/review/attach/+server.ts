// BFF proxy for image-attach actions (PRO-9xx Review-page Attach modal).
// Three sub-actions, picked by the JSON body's `source` field:
//   - source="local" → forwards { canonical_code, print_id, rel_path } to
//     Flask /api/shadow-review/image/attach
//   - source="url"   → forwards { canonical_code, print_id, url } to
//     Flask /api/shadow-review/image/from-url
// Uploads use a separate /api/review/attach/upload endpoint that handles
// multipart bodies.
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

interface AttachBody {
	canonical_code?: string;
	print_id?: string;
	source?: 'local' | 'url';
	rel_path?: string;
	url?: string;
}

export const POST: RequestHandler = async ({ request }) => {
	const body = (await request.json().catch(() => ({}))) as AttachBody;
	const source = body.source;
	let upstreamPath: string;
	let upstreamBody: Record<string, unknown>;
	if (source === 'local') {
		upstreamPath = '/api/shadow-review/image/attach';
		upstreamBody = {
			canonical_code: body.canonical_code,
			print_id: body.print_id,
			rel_path: body.rel_path
		};
	} else if (source === 'url') {
		upstreamPath = '/api/shadow-review/image/from-url';
		upstreamBody = {
			canonical_code: body.canonical_code,
			print_id: body.print_id,
			url: body.url
		};
	} else {
		return json({ error: "source must be 'local' or 'url'" }, { status: 400 });
	}

	let resp: Response;
	try {
		resp = await fetch(`${flaskBaseUrl()}${upstreamPath}`, {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify(upstreamBody),
			signal: AbortSignal.timeout(30_000)
		});
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
