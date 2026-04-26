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
    ],
    rules: {
      'no-unused-vars': 'warn',
      'no-undef': 'error',
      'prefer-const': 'warn',
      'no-var': 'error',
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
];
