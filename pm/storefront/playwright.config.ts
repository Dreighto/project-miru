import { defineConfig, devices } from '@playwright/test';

// Assumes PM Storefront Flask is already running at :18080 (the project's
// canonical dev port — see windows/start_op_miru_worktree.ps1). No webServer
// block — we don't want playwright to manage the Flask process.
const BASE = process.env.PM_STOREFRONT_URL ?? 'http://127.0.0.1:18080';

export default defineConfig({
	testDir: './tests/e2e',
	timeout: 30_000,
	expect: { timeout: 5_000 },
	fullyParallel: false, // serial — Flask is single-process + shares state
	retries: 0,
	reporter: [['list']],
	use: {
		baseURL: BASE,
		headless: true,
		// iPhone 14 baseline so the swipe spec exercises the actual mobile path
		viewport: { width: 390, height: 844 },
		hasTouch: true,
		isMobile: true,
		trace: 'retain-on-failure',
	},
	projects: [
		{
			// Chromium-engine mobile (Pixel 7). Catches the cross-browser core of
			// the gesture state machine + render layer.
			name: 'chromium-mobile',
			use: { ...devices['Pixel 7'] },
		},
		{
			// WebKit-engine mobile (iPhone 14). Catches Safari-specific behaviors:
			// the long-press image-context-menu (suppressed via the iOS CSS rules
			// in deck-builder/+page.svelte), -webkit-touch-callout handling, etc.
			name: 'webkit-mobile',
			use: { ...devices['iPhone 14'] },
		},
	],
});
