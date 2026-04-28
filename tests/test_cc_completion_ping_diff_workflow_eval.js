'use strict';
// Boundary-crossing test for ccp002-read-and-diff (PRO-189).
// Loads docker/n8n/workflows/w-cc-completion-ping.json from disk, extracts
// the jsCode string from the ccp002-read-and-diff node, and evals it in a
// controlled harness — the same boundary n8n crosses when it runs the workflow.
//
// This catches bugs that live in the JSON-to-eval path:
//   Bug A (PRO-189): $getWorkflowStaticData call mangled to ('global')
//   Bug B (PRO-189): literal LF inside content.split('...') → SyntaxError
//
// PRO-160's clean-algorithm tests (test_cc_completion_ping_diff.js) remain
// alongside — they test the algorithm logic in isolation. This file tests that
// the same algorithm, as it actually lives in the workflow JSON, survives the
// eval boundary without corruption.
//
// Run with: node tests/test_cc_completion_ping_diff_workflow_eval.js

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const vm = require('vm');
const assert = require('assert');

// ---- load workflow ----

const WF_PATH = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'w-cc-completion-ping.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));
const jsCode = wf.nodes.find((n) => n.id === 'ccp002-read-and-diff')?.parameters?.jsCode;
if (!jsCode) throw new Error('ccp002-read-and-diff node or jsCode not found in workflow JSON');

// ---- harness ----

const TMP_DIR = path.join(__dirname, '_tmp');
if (!fs.existsSync(TMP_DIR)) fs.mkdirSync(TMP_DIR, { recursive: true });

// Run the ccp002 jsCode in a sandboxed function.
// fileContent: string (written to a temp file) or null (simulates ENOENT).
// initState: object (initial static-data values, mutated in place).
// Returns { json: <first item's json>, state: <the mutated state object> }.
function runCCP002(fileContent, initState) {
  const state = Object.assign({}, initState);

  const tmpFile = path.join(
    TMP_DIR,
    'ccp002_test_' + Date.now() + '_' + Math.random().toString(36).slice(2) + '.jsonl'
  );
  const targetPath =
    fileContent === null ? '/nonexistent/path/pro189_enoent_sentinel_' + Date.now() : tmpFile;

  if (fileContent !== null) {
    fs.writeFileSync(tmpFile, fileContent, 'utf8');
  }

  // Patch the hardcoded JSONL path so the code reads our temp file.
  // JSON.stringify produces a valid JS string literal (with backslashes escaped),
  // which avoids Windows path characters like \t being misread as escape sequences
  // by the JS engine when new Function parses the patched source.
  const patchedCode = jsCode.replace(
    "'/miru-data/cc_completion_log.jsonl'",
    JSON.stringify(targetPath)
  );

  // Wrap in a function to allow top-level `return` statements (n8n does this).
  // Inject $getWorkflowStaticData and require as named parameters.
  let output;
  try {
    const fn = new Function('$getWorkflowStaticData', 'require', patchedCode);
    output = fn(() => state, require);
  } finally {
    if (fileContent !== null && fs.existsSync(tmpFile)) {
      fs.unlinkSync(tmpFile);
    }
  }

  return { json: output[0].json, state };
}

function hashRow(line) {
  return crypto.createHash('sha1').update(line.trim()).digest('hex');
}

const mkRow = (n) => `{"ticket_id":"PRO-${n}","status":"CONFIRMED_WORKING","summary":"test ${n}"}`;

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

// ---- Bug-specific guard tests (catch PRO-189 regressions) ----

// Guard 1: jsCode must parse without SyntaxError (catches Bug B).
test('Guard B: jsCode parses without SyntaxError via vm.Script', () => {
  // Wrapping in a function allows top-level return (same as n8n's eval wrapper).
  new vm.Script('(function($getWorkflowStaticData,require){' + jsCode + '})');
});

// Guard 2: $getWorkflowStaticData must be called and the return value mutated
// (catches Bug A — if staticData is a plain string, mutations silently fail and
// pinged_hashes is never written to state).
test('Guard A: $getWorkflowStaticData call mutates state (not a string)', () => {
  const rows = [mkRow(1), mkRow(2), mkRow(3)];
  const { state } = runCCP002(rows.join('\n') + '\n', {});
  // On first run with empty state, the seeder must write pinged_hashes to state.
  assert.ok(
    Array.isArray(state.cc_completion_pinged_hashes),
    'cc_completion_pinged_hashes must be an array — Bug A would leave it absent'
  );
  assert.strictEqual(state.cc_completion_pinged_hashes.length, 3);
});

// ---- Algorithm correctness tests (same 8 cases as PRO-160, but via eval) ----

// Case 1: Empty state + empty file → empty new_rows, no _init flag.
test('Case 1: empty state + empty file → empty new_rows', () => {
  const { json } = runCCP002('', {});
  assert.deepStrictEqual(json.new_rows, []);
  assert.strictEqual(json.regressed, false);
  assert.strictEqual(json._init, undefined);
});

// Case 2: Empty state + 5 rows → seed silently, no pings (_init=true).
test('Case 2: empty state + 5 rows → seed all, no pings', () => {
  const rows = [1, 2, 3, 4, 5].map(mkRow);
  const initState = {};
  const { json, state } = runCCP002(rows.join('\n') + '\n', initState);
  assert.deepStrictEqual(json.new_rows, []);
  assert.strictEqual(json._init, true);
  assert.strictEqual(json.regressed, false);
  assert.strictEqual(state.cc_completion_pinged_hashes.length, 5);
  const seeded = new Set(state.cc_completion_pinged_hashes);
  for (const r of rows) assert.ok(seeded.has(hashRow(r)));
});

// Case 3: State has 5 hashes + same 5 rows → no new rows, no regression.
test('Case 3: state=5 hashes + same 5 rows → no new', () => {
  const rows = [1, 2, 3, 4, 5].map(mkRow);
  const { json, state } = runCCP002(rows.join('\n') + '\n', {
    cc_completion_pinged_hashes: rows.map(hashRow),
  });
  assert.deepStrictEqual(json.new_rows, []);
  assert.strictEqual(json.regressed, false);
  assert.strictEqual(state.cc_completion_pinged_hashes.length, 5);
});

// Case 4: State has 5 hashes + 5 rows + 1 new row → 1 new_row, hash stored.
test('Case 4: state=5 hashes + 5+1 rows → 1 new_row', () => {
  const origRows = [1, 2, 3, 4, 5].map(mkRow);
  const allRows = [...origRows, mkRow(6)];
  const { json, state } = runCCP002(allRows.join('\n') + '\n', {
    cc_completion_pinged_hashes: origRows.map(hashRow),
  });
  assert.strictEqual(json.new_rows.length, 1);
  assert.strictEqual(json.new_rows[0], mkRow(6));
  assert.strictEqual(json.regressed, false);
  assert.strictEqual(state.cc_completion_pinged_hashes.length, 6);
});

// Case 5: State has 5 hashes + only 1 original + 4 new rows → regressed (20% present).
test('Case 5: state=5 hashes + 1 orig + 4 new → regressed', () => {
  const origRows = [1, 2, 3, 4, 5].map(mkRow);
  const currentRows = [mkRow(1), mkRow(6), mkRow(7), mkRow(8), mkRow(9)];
  const { json } = runCCP002(currentRows.join('\n') + '\n', {
    cc_completion_pinged_hashes: origRows.map(hashRow),
    cc_completion_regression_alerted: false,
  });
  assert.strictEqual(json.regressed, true);
  assert.deepStrictEqual(json.new_rows, []);
});

// Case 6: State has 5 hashes + 5 same rows + 4 new rows → 4 new_rows, no regression.
test('Case 6: state=5 hashes + 5+4 rows → 4 new_rows', () => {
  const origRows = [1, 2, 3, 4, 5].map(mkRow);
  const allRows = [...origRows, mkRow(6), mkRow(7), mkRow(8), mkRow(9)];
  const { json, state } = runCCP002(allRows.join('\n') + '\n', {
    cc_completion_pinged_hashes: origRows.map(hashRow),
  });
  assert.strictEqual(json.new_rows.length, 4);
  assert.strictEqual(json.regressed, false);
  assert.strictEqual(state.cc_completion_pinged_hashes.length, 9);
});

// Case 7: File missing when state has hashes → regressed=true.
test('Case 7: ENOENT + non-empty state → regressed', () => {
  const origRows = [1, 2, 3, 4, 5].map(mkRow);
  const { json } = runCCP002(null, {
    cc_completion_pinged_hashes: origRows.map(hashRow),
  });
  assert.strictEqual(json.regressed, true);
  assert.strictEqual(json._file_missing, true);
  assert.deepStrictEqual(json.new_rows, []);
});

// Case 8 (bonus): Regression already alerted → regressed=false (suppressed).
test('Case 8: regression alerted already → suppressed', () => {
  const origRows = [1, 2, 3, 4, 5].map(mkRow);
  const currentRows = [mkRow(1)]; // only 20% present
  const { json } = runCCP002(currentRows.join('\n') + '\n', {
    cc_completion_pinged_hashes: origRows.map(hashRow),
    cc_completion_regression_alerted: true,
  });
  assert.strictEqual(json.regressed, false);
});

// ---- summary ----

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
