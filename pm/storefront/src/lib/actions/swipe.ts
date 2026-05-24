/**
 * `use:swipe` — Svelte 5 action for horizontal swipe-left / swipe-right
 * gestures on touch surfaces. Tap-and-keyboard remain canonical paths;
 * this action only adds an additive affordance for touch users.
 *
 * Architecture: hand-rolled pointer-event state machine. Locked decisions
 * from the 2026-05-23 gesture research (see
 * `.agent/research/SESSION_REPORT_2026-05-23.md` and
 * `.agent/skills/mobile-deckbuilder-ux/SKILL.md`):
 *
 *   - touch-action: pan-y applied to the node so vertical page scroll
 *     stays UA-handled while horizontal is reserved for this action.
 *   - 20 px edge guard on left + right viewport edges so iOS Safari's
 *     edge-swipe-back stays uninterrupted.
 *   - 8 px direction-lock slop. Vertical-dominant motion releases the
 *     gesture so the page can scroll.
 *   - 48 px horizontal commit threshold. Below that = snap back, no fire.
 *   - Single haptic at threshold-cross (the "armed latch" pattern, per
 *     AGY's recommendation). Caller-supplied `onArmed` hook receives the
 *     direction so it can vibrate / flash a visual indicator.
 *   - Mouse pointerType is intentionally ignored — desktop drag-to-swipe
 *     is awkward; tap-and-click are the canonical desktop paths.
 *   - pointercancel (interruption, multi-touch, scroll intercept) resets
 *     to idle without firing anything.
 *
 * Usage:
 *
 *   import { swipe } from '$lib/actions/swipe';
 *   <button
 *     use:swipe={{
 *       onSwipeRight: () => addCard(c),
 *       onSwipeLeft: () => removeCard(c),
 *       onArmed: (dir) => haptic(8),
 *     }}
 *   >...</button>
 *
 * The action also drives a CSS variable `--swipe-dx` on the node during
 * the gesture so the consumer can render a translateX(var(--swipe-dx))
 * effect for the snap-back visual.
 */

export interface SwipeOptions {
	onSwipeLeft?: () => void;
	onSwipeRight?: () => void;
	/** Optional. Fires when the swipe distance first crosses the commit threshold. */
	onArmed?: (direction: 'left' | 'right') => void;
	/** Set false to disable the action without unmounting it. Default: true. */
	enabled?: boolean;
	/** Override the edge guard distance from the viewport's left + right edges. */
	edgeGuardPx?: number;
	/** Override the direction-lock slop (movement before axis is decided). */
	slopPx?: number;
	/** Override the commit threshold (movement at which the swipe fires on release). */
	commitPx?: number;
	/** Override the axis-dominance multiplier. */
	dominanceFactor?: number;
}

interface ActiveSwipe {
	pointerId: number;
	startX: number;
	startY: number;
	startTime: number;
	armedDir: 'left' | 'right' | null;
	axis: 'pending' | 'horizontal' | 'vertical';
}

const DEFAULTS = {
	edgeGuardPx: 20,
	slopPx: 8,
	commitPx: 48,
	dominanceFactor: 1.2,
	tapMaxMoveSquared: 64, // 8px² — used to suppress accidental swipe firing on a quick stationary release
} as const;

export function swipe(node: HTMLElement, initialOptions: SwipeOptions = {}) {
	let options: SwipeOptions = { enabled: true, ...initialOptions };
	let active: ActiveSwipe | null = null;

	// Apply touch-action so the browser knows we own horizontal. Vertical scroll
	// stays UA-handled. The node owner can layer additional styles — we only
	// touch this property and the swipe-dx CSS variable during gestures.
	const previousTouchAction = node.style.touchAction;
	node.style.touchAction = 'pan-y';

	function isEnabled(): boolean {
		return options.enabled !== false;
	}

	function inEdgeGuard(clientX: number): boolean {
		const guard = options.edgeGuardPx ?? DEFAULTS.edgeGuardPx;
		const w = typeof window !== 'undefined' ? window.innerWidth : 0;
		return clientX < guard || clientX > w - guard;
	}

	function reset(): void {
		if (active) {
			// Defensive: hasPointerCapture is not in JSDOM, and even in real browsers
			// calling releasePointerCapture on a node that doesn't currently hold
			// the capture throws InvalidStateError. Optional-chain + try/catch covers
			// both the "API not present" (tests) and "no capture held" (browser) cases.
			try {
				node.releasePointerCapture?.(active.pointerId);
			} catch {
				/* either no capture held, or the API isn't implemented in this env */
			}
		}
		active = null;
		node.style.removeProperty('--swipe-dx');
		node.dataset.swipeArmed = '';
	}

	function onPointerDown(event: PointerEvent): void {
		if (!isEnabled()) return;
		// Skip mouse — desktop drag-to-swipe is awkward; tap stays canonical there.
		if (event.pointerType === 'mouse') return;
		// Multi-touch interruption: bail on second pointer.
		if (active !== null) {
			reset();
			return;
		}
		if (inEdgeGuard(event.clientX)) return; // let UA own the system gesture

		active = {
			pointerId: event.pointerId,
			startX: event.clientX,
			startY: event.clientY,
			startTime: performance.now(),
			armedDir: null,
			axis: 'pending',
		};
		// pointer capture keeps the move + up events flowing even if the finger
		// drifts off the original node (which it will, that's the whole point).
		try {
			node.setPointerCapture(event.pointerId);
		} catch {
			/* capture can throw in some test envs; safe to continue */
		}
	}

	function onPointerMove(event: PointerEvent): void {
		if (!active || event.pointerId !== active.pointerId) return;
		const dx = event.clientX - active.startX;
		const dy = event.clientY - active.startY;
		const slop = options.slopPx ?? DEFAULTS.slopPx;
		const dominance = options.dominanceFactor ?? DEFAULTS.dominanceFactor;
		const commit = options.commitPx ?? DEFAULTS.commitPx;

		// Axis hasn't been decided yet — decide once we exceed the slop.
		if (active.axis === 'pending') {
			const absDx = Math.abs(dx);
			const absDy = Math.abs(dy);
			if (absDx < slop && absDy < slop) return; // still inside the slop disk
			if (absDx > absDy * dominance) {
				active.axis = 'horizontal';
			} else {
				active.axis = 'vertical';
				// Vertical-dominant — release the gesture so the page can scroll.
				reset();
				return;
			}
		}

		if (active.axis !== 'horizontal') return;

		// We own this gesture. Prevent the UA from scrolling the page horizontally
		// (it normally wouldn't because we set touch-action: pan-y, but some
		// browsers still emit horizontal momentum without preventDefault).
		// pointermove listeners are non-passive by default; this is allowed.
		if (event.cancelable) event.preventDefault();

		node.style.setProperty('--swipe-dx', `${dx}px`);

		// Threshold-cross detection — fire onArmed exactly once per direction.
		const direction: 'left' | 'right' | null =
			dx >= commit ? 'right' : dx <= -commit ? 'left' : null;
		if (direction && active.armedDir !== direction) {
			active.armedDir = direction;
			node.dataset.swipeArmed = direction;
			options.onArmed?.(direction);
		} else if (!direction && active.armedDir !== null) {
			// User dragged back under the threshold — clear armed state, no fire.
			active.armedDir = null;
			node.dataset.swipeArmed = '';
		}
	}

	function onPointerUp(event: PointerEvent): void {
		if (!active || event.pointerId !== active.pointerId) return;
		const armed = active.armedDir;
		const dx = event.clientX - active.startX;
		const dy = event.clientY - active.startY;
		const tapMaxSq = DEFAULTS.tapMaxMoveSquared;
		const wasTap = active.axis === 'pending' && dx * dx + dy * dy <= tapMaxSq;

		reset();

		// Tap: let the click handler do its thing (the underlying element's onclick
		// fires from the synthetic click event, NOT from us). We only need to NOT
		// have preventDefault'd. Nothing to do here — the click event will follow.
		if (wasTap) return;

		// Swipe: commit if we crossed the threshold; otherwise the snap-back is
		// already visible because --swipe-dx was removed above.
		if (armed === 'right') {
			options.onSwipeRight?.();
		} else if (armed === 'left') {
			options.onSwipeLeft?.();
		}
	}

	function onPointerCancel(event: PointerEvent): void {
		if (!active || event.pointerId !== active.pointerId) return;
		reset();
	}

	node.addEventListener('pointerdown', onPointerDown);
	// passive: false on pointermove so preventDefault is allowed.
	node.addEventListener('pointermove', onPointerMove, { passive: false });
	node.addEventListener('pointerup', onPointerUp);
	node.addEventListener('pointercancel', onPointerCancel);

	return {
		update(newOptions: SwipeOptions = {}) {
			options = { enabled: true, ...newOptions };
			if (!isEnabled()) reset();
		},
		destroy() {
			reset();
			node.removeEventListener('pointerdown', onPointerDown);
			node.removeEventListener('pointermove', onPointerMove);
			node.removeEventListener('pointerup', onPointerUp);
			node.removeEventListener('pointercancel', onPointerCancel);
			// Restore the original touch-action value (probably '' / inherit).
			node.style.touchAction = previousTouchAction;
		},
	};
}
