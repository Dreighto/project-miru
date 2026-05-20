// Pure request-body validation for the Review verdict endpoint (PRO-929).
// Kept separate from +server.ts so it can be unit-tested without mocking
// $env/dynamic/private or fetch.

export const VALID_VERDICTS = ['correct', 'wrong', 'defer'] as const;
export type Verdict = (typeof VALID_VERDICTS)[number];

export interface VerdictRequest {
	canonical_code: string;
	print_id: string;
	contributing_model: string;
	verdict: Verdict;
}

export type ParseResult = { ok: true; value: VerdictRequest } | { ok: false; error: string };

/**
 * Validate a raw verdict request body. On success returns the cleaned request;
 * on failure returns a human-readable error string (surfaced to the browser as
 * an HTTP 400). The verdict vocabulary mirrors `submit_verdict()` in
 * `miru_ai/shadow_review.py` — correct | wrong | defer.
 */
export function parseVerdictBody(raw: unknown): ParseResult {
	if (typeof raw !== 'object' || raw === null) {
		return { ok: false, error: 'Request body must be a JSON object.' };
	}
	const body = raw as Record<string, unknown>;
	const canonical_code = typeof body.canonical_code === 'string' ? body.canonical_code.trim() : '';
	const print_id = typeof body.print_id === 'string' ? body.print_id.trim() : '';
	const contributing_model =
		typeof body.contributing_model === 'string' ? body.contributing_model.trim() : '';
	const verdict = typeof body.verdict === 'string' ? body.verdict.trim() : '';

	if (!canonical_code || !print_id || !contributing_model || !verdict) {
		return {
			ok: false,
			error: 'canonical_code, print_id, contributing_model and verdict are all required.'
		};
	}
	if (!(VALID_VERDICTS as readonly string[]).includes(verdict)) {
		return { ok: false, error: `verdict must be one of: ${VALID_VERDICTS.join(', ')}.` };
	}
	return {
		ok: true,
		value: { canonical_code, print_id, contributing_model, verdict: verdict as Verdict }
	};
}
