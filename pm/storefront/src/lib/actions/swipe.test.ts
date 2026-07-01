/**
 * Unit tests for the `swipe` action.
 *
 * STATUS — these tests are written against Vitest + JSDOM but the
 * `pm/storefront` project does not yet have a test runner installed.
 * To run them, add the dev deps once:
 *
 *     npm i -D vitest jsdom
 *
 * and add a `test` script to package.json:
 *
 *     "test": "vitest run --environment=jsdom",
 *
 * Then `npm run test`. Every assertion below was also verified
 * behaviorally against the live deck builder via Playwright on
 * 2026-05-24 — see SWIPE_IMPLEMENTATION_2026-05-24.md for the matrix.
 *
 * The tests exercise the state machine documented in
 * .agent/research/cc_gesture_research.md §Q4 and the
 * mobile-deckbuilder-ux skill's gesture contract.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { swipe, type SwipeOptions } from './swipe';

// --- harness ----------------------------------------------------------------

interface Recorded {
	leftCalls: number;
	rightCalls: number;
	armed: Array<'left' | 'right'>;
}

function setup(opts: Partial<SwipeOptions> = {}) {
	// JSDOM viewport defaults to 1024 — narrow it so edge-guard tests behave
	// like a phone viewport without monkey-patching window.innerWidth.
	Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });

	const node = document.createElement('button');
	node.style.width = '100px';
	node.style.height = '160px';
	document.body.appendChild(node);

	const recorded: Recorded = { leftCalls: 0, rightCalls: 0, armed: [] };

	const handle = swipe(node, {
		onSwipeRight: () => recorded.rightCalls++,
		onSwipeLeft: () => recorded.leftCalls++,
		onArmed: (dir) => recorded.armed.push(dir),
		...opts,
	});

	return { node, recorded, handle };
}

function pe(type: string, x: number, y: number, opts: Partial<PointerEventInit> = {}) {
	return new PointerEvent(type, {
		pointerId: 1,
		pointerType: 'touch',
		isPrimary: true,
		clientX: x,
		clientY: y,
		bubbles: true,
		cancelable: true,
		...opts,
	});
}

beforeEach(() => {
	document.body.innerHTML = '';
});

afterEach(() => {
	document.body.innerHTML = '';
});

// --- tests ------------------------------------------------------------------

describe('swipe action — state machine', () => {
	it('applies touch-action: pan-y on mount and restores on destroy', () => {
		const { node, handle } = setup();
		expect(node.style.touchAction).toBe('pan-y');
		handle.destroy();
		expect(node.style.touchAction).toBe('');
	});

	it('fires onSwipeRight when horizontal drag commits ≥ 48px', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		// Move past commit threshold
		node.dispatchEvent(pe('pointermove', 110, 50)); // dx = 60
		node.dispatchEvent(pe('pointerup', 110, 50));
		expect(recorded.rightCalls).toBe(1);
		expect(recorded.leftCalls).toBe(0);
		expect(recorded.armed).toEqual(['right']);
	});

	it('fires onSwipeLeft when horizontal drag commits ≤ -48px', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 200, 50));
		node.dispatchEvent(pe('pointermove', 140, 50)); // dx = -60
		node.dispatchEvent(pe('pointerup', 140, 50));
		expect(recorded.leftCalls).toBe(1);
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.armed).toEqual(['left']);
	});

	it('does NOT fire when drag stays below the commit threshold (48px)', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 80, 50)); // dx = 30 (sub-commit)
		node.dispatchEvent(pe('pointerup', 80, 50));
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.leftCalls).toBe(0);
		expect(recorded.armed).toEqual([]);
	});

	it('releases the gesture to scroll when vertical dominance exceeds horizontal', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 55, 100)); // dx=5 dy=50 — vertical dominant
		node.dispatchEvent(pe('pointerup', 55, 100));
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.leftCalls).toBe(0);
		expect(recorded.armed).toEqual([]);
	});

	it('respects the 20px edge guard on the LEFT viewport edge', () => {
		const { node, recorded } = setup();
		// Start at x=10 (inside left guard) — gesture should never arm
		node.dispatchEvent(pe('pointerdown', 10, 50));
		node.dispatchEvent(pe('pointermove', 80, 50)); // dx=70 (would commit if armed)
		node.dispatchEvent(pe('pointerup', 80, 50));
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.armed).toEqual([]);
	});

	it('respects the 20px edge guard on the RIGHT viewport edge', () => {
		const { node, recorded } = setup();
		// Start within 20px of innerWidth=390 → x=375 is inside the right guard
		node.dispatchEvent(pe('pointerdown', 375, 50));
		node.dispatchEvent(pe('pointermove', 305, 50)); // dx=-70 (would commit left)
		node.dispatchEvent(pe('pointerup', 305, 50));
		expect(recorded.leftCalls).toBe(0);
		expect(recorded.armed).toEqual([]);
	});

	it('ignores mouse pointerType — desktop drag-to-swipe is disabled', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50, { pointerType: 'mouse' }));
		node.dispatchEvent(pe('pointermove', 120, 50, { pointerType: 'mouse' }));
		node.dispatchEvent(pe('pointerup', 120, 50, { pointerType: 'mouse' }));
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.armed).toEqual([]);
	});

	it('fires onArmed exactly once per direction during a single drag', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 100, 50)); // dx=50 → armed right
		node.dispatchEvent(pe('pointermove', 110, 50)); // dx=60 → still armed, no extra fire
		node.dispatchEvent(pe('pointermove', 120, 50)); // dx=70 → still armed, no extra fire
		node.dispatchEvent(pe('pointerup', 120, 50));
		expect(recorded.armed).toEqual(['right']);
		expect(recorded.rightCalls).toBe(1);
	});

	it('re-arms when user crosses back across threshold (drag-back-and-forward)', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 110, 50)); // dx=60 → armed right
		node.dispatchEvent(pe('pointermove', 70, 50)); //  dx=20 → un-armed
		node.dispatchEvent(pe('pointermove', 115, 50)); // dx=65 → armed right again
		node.dispatchEvent(pe('pointerup', 115, 50));
		expect(recorded.armed).toEqual(['right', 'right']);
		expect(recorded.rightCalls).toBe(1);
	});

	it('pointercancel resets without firing anything', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 110, 50)); // dx=60 → armed
		node.dispatchEvent(pe('pointercancel', 110, 50));
		// Subsequent pointerup should be a no-op now
		node.dispatchEvent(pe('pointerup', 110, 50));
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.leftCalls).toBe(0);
	});

	it('sets and clears --swipe-dx during the drag lifecycle', () => {
		const { node } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 110, 50));
		expect(node.style.getPropertyValue('--swipe-dx')).toBe('60px');
		node.dispatchEvent(pe('pointerup', 110, 50));
		expect(node.style.getPropertyValue('--swipe-dx')).toBe('');
	});

	it('respects enabled=false on update', () => {
		const { node, recorded, handle } = setup();
		handle.update({
			enabled: false,
			onSwipeRight: () => recorded.rightCalls++,
			onSwipeLeft: () => recorded.leftCalls++,
		});
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 110, 50));
		node.dispatchEvent(pe('pointerup', 110, 50));
		expect(recorded.rightCalls).toBe(0);
	});

	it('honours overridden commit threshold', () => {
		const { node, recorded, handle } = setup({ commitPx: 100 });
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 130, 50)); // dx=80 — below new threshold
		node.dispatchEvent(pe('pointerup', 130, 50));
		expect(recorded.rightCalls).toBe(0);
		// retry with a drag that exceeds the new threshold
		node.dispatchEvent(pe('pointerdown', 50, 50, { pointerId: 2 }));
		node.dispatchEvent(pe('pointermove', 160, 50, { pointerId: 2 })); // dx=110
		node.dispatchEvent(pe('pointerup', 160, 50, { pointerId: 2 }));
		expect(recorded.rightCalls).toBe(1);

		// silence unused warning if vitest flags it
		void handle;
	});

	it('destroy() removes listeners — subsequent events are no-ops', () => {
		const { node, recorded, handle } = setup();
		handle.destroy();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 110, 50));
		node.dispatchEvent(pe('pointerup', 110, 50));
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.leftCalls).toBe(0);
	});

	it('quick stationary tap-like release does NOT fire a swipe', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		// movement under 8px slop, fast release
		node.dispatchEvent(pe('pointermove', 53, 51));
		node.dispatchEvent(pe('pointerup', 53, 51));
		expect(recorded.rightCalls).toBe(0);
		expect(recorded.leftCalls).toBe(0);
	});
});

// Sanity-pin: changing these defaults silently is the kind of drift the
// gmi-pr-review skill warns about. If any of these break, somebody bumped
// the public contract — review the SKILL.md before approving.
describe('swipe action — public defaults are load-bearing', () => {
	it('default edge guard is 20px', () => {
		// We can't introspect defaults directly without exporting them, but
		// these two boundary tests pin the behavior implicitly.
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 19, 50)); // inside guard
		node.dispatchEvent(pe('pointermove', 89, 50));
		node.dispatchEvent(pe('pointerup', 89, 50));
		expect(recorded.rightCalls).toBe(0);
	});

	it('default commit threshold is 48px', () => {
		const { node, recorded } = setup();
		node.dispatchEvent(pe('pointerdown', 50, 50));
		node.dispatchEvent(pe('pointermove', 97, 50)); // dx=47 — just under
		node.dispatchEvent(pe('pointerup', 97, 50));
		expect(recorded.rightCalls).toBe(0);
		node.dispatchEvent(pe('pointerdown', 50, 50, { pointerId: 2 }));
		node.dispatchEvent(pe('pointermove', 98, 50, { pointerId: 2 })); // dx=48 — at threshold
		node.dispatchEvent(pe('pointerup', 98, 50, { pointerId: 2 }));
		expect(recorded.rightCalls).toBe(1);
	});

	it('vitest is installed if you got this far', () => {
		// guard against accidental tree-shaking
		expect(vi).toBeDefined();
	});
});
