'use strict';
// Boundary-crossing test for w2007-score-workers (PRO-258 / PRO-189 pattern).
// Loads docker/n8n/workflows/w2_worker_selection_router.json from disk, extracts
// the jsCode string from the w2007-score-workers node, and evals it in a
// controlled harness — the same boundary n8n crosses when it runs the workflow.
//
// Catches:
//   Guard B: SyntaxError from malformed JS in the JSON string
//   Scoring: file-extension-based routing signals must differentiate workers
//
// Run with: node tests/test_w2_scoring_workflow_eval.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

// ---- load workflow ----

const WF_PATH = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'w2_worker_selection_router.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));
const jsCode = wf.nodes.find((n) => n.id === 'w2007-score-workers')?.parameters?.jsCode;
if (!jsCode) throw new Error('w2007-score-workers node or jsCode not found in workflow JSON');

// ---- harness ----

const CONFIG_PATH_IN_CODE = "'/miru-data/config/w2_routing_rules.json'";
const LOCAL_CONFIG_PATH = path.join(__dirname, '..', 'data', 'config', 'w2_routing_rules.json');

function runW2007(inputJson) {
  // Patch the hardcoded Docker config path to the local dev path.
  const patchedCode = jsCode.replace(CONFIG_PATH_IN_CODE, JSON.stringify(LOCAL_CONFIG_PATH));

  const $input = { item: { json: inputJson } };
  const fn = new Function('$input', 'require', patchedCode);
  return fn($input, require);
}

// Build a minimal extracted_signals object (mirrors w2006-extract-signals output).
function makeInput({
  touchesPaths = [],
  surfaceKeywords = [],
  taskType = 'unknown',
  title = '',
  description = '',
} = {}) {
  return {
    issue_id: 'TEST-001',
    issue_identifier: 'TEST-001',
    trace_id: 'test-trace-id',
    shadow_mode: false,
    intake_source: 'test',
    issue_title: title,
    issue_description: description,
    issue_priority: 0,
    issue_labels: [],
    issue_existing_label_ids: [],
    labels_map: {},
    extracted_signals: {
      task_type: taskType,
      surface_keywords: surfaceKeywords,
      touches_paths: touchesPaths,
      research_signal: false,
    },
  };
}

// ---- test runner ----

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log(`  PASS  ${name}`);
    passed++;
  } catch (e) {
    console.log(`  FAIL  ${name}: ${e.message}`);
    failed++;
  }
}

// ---- Guard tests (PRO-189 boundary) ----

test('Guard B: jsCode parses without SyntaxError via vm.Script', () => {
  new vm.Script('(function($input,require){' + jsCode + '})');
});

test('Guard: patched code parses without SyntaxError', () => {
  const patchedCode = jsCode.replace(CONFIG_PATH_IN_CODE, JSON.stringify(LOCAL_CONFIG_PATH));
  new vm.Script('(function($input,require){' + patchedCode + '})');
});

// ---- Fixture 1: .py ticket → claude-code (acceptance criterion 1) ----

test('Fixture 1: touches_paths with .py routes to claude-code with confidence > 0', () => {
  const result = runW2007(
    makeInput({
      touchesPaths: ['miru_ai/workers/router.py', 'tests/test_router.py'],
      title: 'Fix routing bug',
      description: 'The scoring logic in miru_ai/workers/router.py always returns 0.5 baseline.',
    })
  );
  const { chosen_worker, confidence, ranked_candidates } = result.json;
  assert.strictEqual(
    chosen_worker,
    'claude-code',
    `Expected claude-code, got ${chosen_worker}. ranked: ${JSON.stringify(ranked_candidates)}`
  );
  assert.ok(confidence > 0, `Expected confidence > 0, got ${confidence}`);
});

// ---- Fixture 2: .html/.css ticket → cursor (acceptance criterion 2) ----

test('Fixture 2: touches_paths with .html/.css routes to cursor with confidence > 0', () => {
  const result = runW2007(
    makeInput({
      touchesPaths: ['pm/templates/card.html', 'pm/static/main.css'],
      title: 'Fix card tile styling',
      description:
        'The card tile in pm/templates/card.html has broken styles in pm/static/main.css.',
    })
  );
  const { chosen_worker, confidence, ranked_candidates } = result.json;
  assert.strictEqual(
    chosen_worker,
    'cursor',
    `Expected cursor, got ${chosen_worker}. ranked: ${JSON.stringify(ranked_candidates)}`
  );
  assert.ok(confidence > 0, `Expected confidence > 0, got ${confidence}`);
});

// ---- Fixture 3: .js template ticket → cursor (acceptance criterion 2) ----

test('Fixture 3: touches_paths with .js routes to cursor with confidence > 0', () => {
  const result = runW2007(
    makeInput({
      touchesPaths: ['pm/static/app.js'],
      title: 'Fix frontend interaction',
      description: 'The swipe gesture in pm/static/app.js does not fire on mobile.',
    })
  );
  const { chosen_worker, confidence, ranked_candidates } = result.json;
  assert.strictEqual(
    chosen_worker,
    'cursor',
    `Expected cursor, got ${chosen_worker}. ranked: ${JSON.stringify(ranked_candidates)}`
  );
  assert.ok(confidence > 0, `Expected confidence > 0, got ${confidence}`);
});

// ---- Fixture 4: audit keyword → gemini (validates existing rules still work) ----

test('Fixture 4: surface_keywords with "audit" routes to gemini with confidence > 0', () => {
  const result = runW2007(
    makeInput({
      surfaceKeywords: ['audit'],
      title: 'Audit the logging',
      description: 'We need a full audit of logging across the codebase.',
    })
  );
  const { chosen_worker, confidence, ranked_candidates } = result.json;
  assert.strictEqual(
    chosen_worker,
    'gemini',
    `Expected gemini, got ${chosen_worker}. ranked: ${JSON.stringify(ranked_candidates)}`
  );
  assert.ok(confidence > 0, `Expected confidence > 0, got ${confidence}`);
});

// ---- Fixture 5: no signals → triage (baseline still works) ----

test('Fixture 5: empty signals produce confidence = 0 (triage path)', () => {
  const result = runW2007(
    makeInput({
      touchesPaths: [],
      surfaceKeywords: [],
      title: 'Unknown task',
      description: 'Do something.',
    })
  );
  const { confidence } = result.json;
  assert.strictEqual(confidence, 0, `Expected confidence 0 for empty signals, got ${confidence}`);
});

// ---- summary ----

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
