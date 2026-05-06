'use strict';
// Boundary-crossing test for PRO-304 codex deprecation cleanup.
//
// PRO-189 hard rule: every change to a jsCode field in a workflow JSON must
// be accompanied by a test that loads the JSON from disk, extracts the
// jsCode string, and evals it via `new vm.Script(...)` to confirm it parses
// without SyntaxError — i.e., test the JS as it lives in the workflow JSON,
// not a clean extracted copy.
//
// w2007-score-workers already has its own boundary test in
// test_w2_scoring_workflow_eval.js. This test covers the seven OTHER nodes
// PRO-304 modified in w2_worker_selection_router.json:
//
//   - w2010-append-history-intent
//   - w2012-append-history-dispatched
//   - w2012a-mint-callback-token
//   - w2014-append-history-triage
//   - w2998a-apply-failed-code
//   - w2003f-detect-manual-dispatch
//   - w2-emit-mint-dispatch
//
// Run with: node tests/test_w2_codex_deprecation_eval.js

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const WF_PATH = path.join(
  __dirname,
  '..',
  'docker',
  'n8n',
  'workflows',
  'w2_worker_selection_router.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));

const PRO304_TOUCHED_NODES = [
  'w2010-append-history-intent',
  'w2012-append-history-dispatched',
  'w2012a-mint-callback-token',
  'w2014-append-history-triage',
  'w2998a-apply-failed-code',
  'w2003f-detect-manual-dispatch',
  'w2-emit-mint-dispatch',
];

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

// ---- Guard A: every touched node still exists with jsCode ----

for (const nodeId of PRO304_TOUCHED_NODES) {
  test(`Node ${nodeId} exists with non-empty jsCode`, () => {
    const node = wf.nodes.find((n) => n.id === nodeId);
    assert.ok(node, `node ${nodeId} not found in workflow JSON`);
    const js = node.parameters && node.parameters.jsCode;
    assert.ok(typeof js === 'string' && js.length > 0, `node ${nodeId} has no jsCode`);
  });
}

// ---- Guard B: each touched node's jsCode parses without SyntaxError ----
// Per PRO-189 — we wrap in a function expression to avoid top-level
// return/await issues in standalone vm.Script eval, the same pattern
// test_w2_scoring_workflow_eval.js uses for w2007.

for (const nodeId of PRO304_TOUCHED_NODES) {
  test(`Node ${nodeId} jsCode parses without SyntaxError`, () => {
    const node = wf.nodes.find((n) => n.id === nodeId);
    const js = node.parameters.jsCode;
    // Wrapper injects only what n8n provides as named args. The jsCode
    // is responsible for its own `require('crypto')`-style imports.
    new vm.Script(
      '(function($input,require,$env,$now,$workflow,$getWorkflowStaticData){' + js + '})'
    );
  });
}

// ---- Guard C: no 'codex' references remain in any touched node's jsCode ----
// This is the regression guard for PRO-304 — if anyone accidentally
// reintroduces a codex entry to one of these maps/arrays, this test fails.

for (const nodeId of PRO304_TOUCHED_NODES) {
  test(`Node ${nodeId} jsCode does not reference codex (PRO-304 invariant)`, () => {
    const node = wf.nodes.find((n) => n.id === nodeId);
    const js = node.parameters.jsCode;
    assert.ok(!/codex/i.test(js), `node ${nodeId} jsCode still references codex`);
  });
}

// ---- Guard D: workflow-wide codex absence (catches drift in non-jsCode params too) ----

test('No codex references anywhere in W2 router workflow JSON (PRO-304 invariant)', () => {
  const blob = JSON.stringify(wf);
  assert.ok(!/codex/i.test(blob), 'W2 router workflow JSON still contains codex references');
});

// ---- summary ----

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
