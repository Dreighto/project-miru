// Playwright config — E2E browser tests against the SvelteKit dev server.
// Single browser (chromium) to keep local runs fast; CI can fan out later.
import { defineConfig, devices } from '@playwright/test';

const PORT = 18768;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
	testDir: './tests/e2e',
	timeout: 30_000,
	expect: { timeout: 5_000 },
	fullyParallel: false,
	forbidOnly: !!process.env.CI,
	retries: process.env.CI ? 1 : 0,
	reporter: process.env.CI ? 'github' : 'list',
	use: {
		baseURL: BASE_URL,
		trace: 'retain-on-failure',
		screenshot: 'only-on-failure',
		video: 'retain-on-failure'
	},
	projects: [
		{
			name: 'chromium',
			use: { ...devices['Desktop Chrome'] }
		}
	],
	webServer: {
		command: 'npm run dev',
		url: BASE_URL,
		reuseExistingServer: !process.env.CI,
		timeout: 60_000,
		stdout: 'pipe',
		stderr: 'pipe'
	}
});
