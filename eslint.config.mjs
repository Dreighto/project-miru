// PRO-107 — pre-PR hygiene ESLint config (flat config, ESLint 9+).
// Conservative defaults only. Catches mechanical issues, not style preferences.
// File-rule tightening should ship as separate follow-up tickets.
const NODE_COMMONJS_GLOBALS = {
  require: 'readonly',
  module: 'readonly',
  exports: 'readonly',
  __dirname: 'readonly',
  __filename: 'readonly',
  process: 'readonly',
  Buffer: 'readonly',
  console: 'readonly',
  setTimeout: 'readonly',
  clearTimeout: 'readonly',
  setInterval: 'readonly',
  clearInterval: 'readonly',
  setImmediate: 'readonly',
  clearImmediate: 'readonly',
  globalThis: 'readonly',
  URL: 'readonly',
  URLSearchParams: 'readonly',
};

export default [
  {
    files: ['**/*.{js,jsx,ts,tsx}'],
    ignores: [
      'docker/n8n/workflows/**',
      'node_modules/**',
      '**/dist/**',
      '**/_build/**',
      'archive/**',
      'data/**',
      'logs/**',
      'tests/_tmp/**',
      // Vite build output served by Flask — these are minified bundles,
      // not source. Linting the compiled bundle yields ~1900 false-positive
      // errors (no-undef on `document`/`fetch`, eqeqeq on minified `==`, etc.).
      'miru_ai/static/**',
      'pm/storefront/build/**',
    ],
    rules: {
      // Existing (tightened 2026-05-09 per CH linter-tightening proposal).
      // caughtErrorsIgnorePattern: '^_' honors the existing convention where
      // `_e` / `_err` indicates "intentionally swallowed error" (used in
      // spawn.js cleanup paths and elsewhere).
      'no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', caughtErrors: 'all', caughtErrorsIgnorePattern: '^_' },
      ],
      'no-undef': 'error',
      'prefer-const': 'warn',
      'no-var': 'error',

      // Error handling — added 2026-05-09 to catch the class of bugs CodeRabbit
      // assertive flagged on PR #152 (silent catches, missing exception types,
      // useless catch blocks, async without await).
      'no-throw-literal': 'error',
      'no-useless-catch': 'error',
      'no-empty': ['error', { allowEmptyCatch: false }],
      'require-await': 'error',
      'no-return-await': 'error',

      // Logic errors — contract violations + footguns that linters can catch
      // before code review.
      'consistent-return': 'error',
      'no-promise-executor-return': 'error',
      'no-unreachable': 'error',
      'no-fallthrough': 'error',
      eqeqeq: ['error', 'always'],
      'no-self-compare': 'error',
      'no-constant-condition': 'error',
    },
  },
  // PRO-83 — Node CommonJS service. Scoped so the rest of the repo isn't affected.
  {
    files: ['services/dispatch_listener/**/*.js'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: NODE_COMMONJS_GLOBALS,
    },
  },
  // Node CommonJS test scripts (tests/w2, tests/w8, tests/test_*.js, etc.)
  {
    files: ['tests/**/*.js'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: NODE_COMMONJS_GLOBALS,
    },
  },
  // PRO-309 — UI verification harness + Playwright config. Mixed Node/browser context:
  // page.evaluate() callbacks run in-browser, so document/window are legitimate.
  {
    files: ['tools/ui_verifier/**/*.js', 'playwright.config.js'],
    languageOptions: {
      sourceType: 'commonjs',
      globals: {
        ...NODE_COMMONJS_GLOBALS,
        document: 'readonly',
        window: 'readonly',
      },
    },
  },
];
