// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.js',
  timeout: 30000,
  use: {
    headless: true,
    viewport: { width: 375, height: 812 },
  },
  reporter: [['list']],
});
