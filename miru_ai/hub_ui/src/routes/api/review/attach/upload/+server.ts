// BFF proxy for multipart file uploads → Flask /api/shadow-review/image/upload.
// Kept separate from the JSON-bodied /api/review/attach because multipart needs
// the FormData passed through untouched (not re-serialized as JSON).
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export const POST: RequestHandler = async ({ request }) => {
	let form: FormData;
	try {
		form = await request.formData();
	} catch {
		return json({ error: 'expected multipart/form-data' }, { status: 400 });
	}

	let resp: Response;
	try {
		resp = await fetch(`${flaskBaseUrl()}/api/shadow-review/image/upload`, {
			method: 'POST',
			body: form,
			signal: AbortSignal.timeout(60_000)
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
