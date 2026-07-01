// BFF for kicking off image_fetcher.fetch_all_missing as a background job.
// POST starts the job, returns { job_id, status: "running" }. Pair this with
// GET /api/dev/fetch-missing-images/[job_id] for status polling.
import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export const POST: RequestHandler = async () => {
	let resp: Response;
	try {
		resp = await fetch(`${flaskBaseUrl()}/api/dev/fetch-missing-images`, {
			method: 'POST',
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
