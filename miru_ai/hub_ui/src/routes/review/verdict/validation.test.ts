import { describe, it, expect } from 'vitest';
import { parseVerdictBody } from './validation';

const valid = {
	canonical_code: 'OP01-001',
	print_id: 'OP01-001',
	contributing_model: 'qwen2.5:7b',
	verdict: 'correct'
};

describe('parseVerdictBody', () => {
	it('accepts a well-formed correct verdict', () => {
		const r = parseVerdictBody(valid);
		expect(r.ok).toBe(true);
		if (r.ok) expect(r.value.verdict).toBe('correct');
	});

	it('accepts wrong and defer verdicts', () => {
		expect(parseVerdictBody({ ...valid, verdict: 'wrong' }).ok).toBe(true);
		expect(parseVerdictBody({ ...valid, verdict: 'defer' }).ok).toBe(true);
	});

	it('trims surrounding whitespace from fields', () => {
		const r = parseVerdictBody({ ...valid, canonical_code: '  OP01-001  ' });
		expect(r.ok).toBe(true);
		if (r.ok) expect(r.value.canonical_code).toBe('OP01-001');
	});

	it('rejects a non-object body', () => {
		expect(parseVerdictBody(null).ok).toBe(false);
		expect(parseVerdictBody('nope').ok).toBe(false);
		expect(parseVerdictBody(42).ok).toBe(false);
	});

	it('rejects a missing required field', () => {
		const r = parseVerdictBody({ ...valid, print_id: '' });
		expect(r.ok).toBe(false);
		if (!r.ok) expect(r.error).toMatch(/required/);
	});

	it('rejects an unknown verdict', () => {
		const r = parseVerdictBody({ ...valid, verdict: 'maybe' });
		expect(r.ok).toBe(false);
		if (!r.ok) expect(r.error).toMatch(/verdict must be one of/);
	});
});
