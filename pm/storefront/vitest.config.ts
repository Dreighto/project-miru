import { defineConfig } from 'vitest/config';
import { sveltekit } from '@sveltejs/kit/vite';

// Vitest config split from vite.config.ts so the dev server config (proxy
// to Flask :18080) stays focused on serving. Unit tests run in jsdom and
// do not need the proxy. SvelteKit plugin still needed so `$lib/...` and
// `$app/...` aliases resolve in test files.
export default defineConfig({
	plugins: [sveltekit()],
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.{js,ts}'],
		// Vitest 4 + JSDOM: assert that PointerEvent is defined; if not we add
		// a minimal polyfill in the setup file (see swipe.test.ts harness).
		globals: false,
	},
});
