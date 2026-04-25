// PRO-107 — pre-PR hygiene ESLint config (flat config, ESLint 9+).
// Conservative defaults only. Catches mechanical issues, not style preferences.
// File-rule tightening should ship as separate follow-up tickets.
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
];
