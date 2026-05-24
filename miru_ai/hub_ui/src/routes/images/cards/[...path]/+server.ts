// Passthrough proxy for card image bytes. The dev page (:18768) doesn't have
// the OPTCG_Images folder on disk — Flask (:18765) serves it. Sending images
// through the SvelteKit BFF means <img src="/images/cards/..."> works the same
// whether the page is loaded locally, over the tailnet, or through Funnel.
import { error } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types';

const flaskBaseUrl = () => (env.MIRU_FLASK_BASE_URL ?? 'http://127.0.0.1:18765').replace(/\/$/, '');

export const GET: RequestHandler = async ({ params, setHeaders }) => {
	const path = params.path ?? '';
	if (!path || path.split('/').some((seg) => seg === '..')) {
		throw error(400, 'invalid path');
	}
	const upstream = `${flaskBaseUrl()}/images/cards/${path
		.split('/')
		.map((seg) => encodeURIComponent(seg))
		.join('/')}`;
	let resp: Response;
	try {
		resp = await fetch(upstream, { signal: AbortSignal.timeout(15_000) });
	} catch {
		throw error(503, 'image service unreachable');
	}
	if (!resp.ok) {
		throw error(resp.status, `upstream ${resp.status}`);
	}
	const contentType = resp.headers.get('content-type') ?? 'application/octet-stream';
	const cacheControl = resp.headers.get('cache-control') ?? 'public, max-age=86400';
	setHeaders({ 'content-type': contentType, 'cache-control': cacheControl });
	return new Response(resp.body, { status: 200 });
};
