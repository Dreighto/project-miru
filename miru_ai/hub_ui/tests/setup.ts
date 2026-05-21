// Vitest setup — runs before every unit-test suite.
import '@testing-library/jest-dom/vitest';
import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';

// jsdom does not implement the Web Animations API. Svelte transitions
// (e.g. transition:slide) call element.animate(); provide a minimal mock so
// component tests can mount transitioning elements without throwing. The mock
// resolves immediately so intro/outro transitions complete in tests.
if (!('animate' in Element.prototype)) {
	(Element.prototype as unknown as Record<string, unknown>).animate = function () {
		let finishCb: (() => void) | null = null;
		return {
			currentTime: 0,
			startTime: 0,
			playState: 'finished',
			pending: false,
			effect: null,
			finished: Promise.resolve(),
			oncancel: null,
			get onfinish() {
				return finishCb;
			},
			set onfinish(fn: (() => void) | null) {
				finishCb = fn;
				if (fn) queueMicrotask(fn);
			},
			play() {},
			pause() {},
			cancel() {},
			finish() {},
			reverse() {},
			updatePlaybackRate() {},
			addEventListener() {},
			removeEventListener() {}
		};
	};
}

afterEach(() => {
	cleanup();
});
