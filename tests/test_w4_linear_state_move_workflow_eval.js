'use strict';
// Boundary-crossing tests for w4041-resolve-in-progress (PRO-196).
// Loads docker/n8n/workflows/w4-dispatch-button-handler.json from disk,
// extracts the jsCode string from the w4041-resolve-in-progress node, and
// evals it in a controlled harness — the same boundary n8n crosses at runtime.
//
// Per PRO-189 adopted lesson: tests MUST load from disk and eval the exact
// bytes that n8n will execute, not a clean extracted copy.
//
// Run with: node tests/test_w4_linear_state_move_workflow_eval.js

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
  'w4-dispatch-button-handler.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));

const w4041Node = wf.nodes.find((n) => n.id === 'w4041-resolve-in-progress');
if (!w4041Node) throw new Error('w4041-resolve-in-progress node not found in workflow JSON');
const jsCode = w4041Node.parameters.jsCode;
if (!jsCode) throw new Error('jsCode not found in w4041-resolve-in-progress node');

// Verify w4040 and w4042 also exist in the workflow
const w4040Node = wf.nodes.find((n) => n.id === 'w4040-fetch-team-states');
if (!w4040Node) throw new Error('w4040-fetch-team-states node not found in workflow JSON');
const w4042Node = wf.nodes.find((n) => n.id === 'w4042-move-in-progress');
if (!w4042Node) throw new Error('w4042-move-in-progress node not found in workflow JSON');

// ---- harness ----

// Run w4041 jsCode with mock inputs.
// priorJson: object (simulates $('w4027-update-pending-dispatched').item.json)
// inputJson: object (simulates $input.item.json, i.e. the w4040 response)
function runW4041(priorJson, inputJson) {
  // n8n injects $input and named-node selectors as function parameters.
  const fn = new Function('$input', '$', patchCode(jsCode));
  const mockInput = { item: { json: inputJson } };
  const mockSelector = (nodeName) => {
    if (nodeName === 'w4027-update-pending-dispatched') {
      return { item: { json: priorJson } };
    }
    throw new Error('unexpected node selector: ' + nodeName);
  };
  return fn(mockInput, mockSelector);
}

function patchCode(code) {
  // Wrap in function to allow top-level return (same as n8n's eval wrapper).
  return code;
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

// ---- Node existence tests ----

test('w4040 node present in workflow', () => {
  assert.ok(w4040Node, 'w4040-fetch-team-states must exist');
  assert.strictEqual(w4040Node.type, 'n8n-nodes-base.httpRequest');
  assert.ok(w4040Node.continueOnFail, 'w4040 must have continueOnFail=true');
});

test('w4042 node present in workflow with continueOnFail', () => {
  assert.ok(w4042Node, 'w4042-move-in-progress must exist');
  assert.strictEqual(w4042Node.type, 'n8n-nodes-base.httpRequest');
  assert.ok(w4042Node.continueOnFail, 'w4042 must have continueOnFail=true');
});

test('w4027 wired to w4040 in connections', () => {
  const conn = wf.connections['w4027-update-pending-dispatched'];
  assert.ok(conn, 'w4027 must have outbound connection');
  const targets = conn.main[0].map((e) => e.node);
  assert.ok(targets.includes('w4040-fetch-team-states'), 'w4027 must connect to w4040');
});

test('connection chain w4040 → w4041 → w4042 present', () => {
  const c40 = wf.connections['w4040-fetch-team-states'];
  assert.ok(c40 && c40.main[0][0].node === 'w4041-resolve-in-progress');
  const c41 = wf.connections['w4041-resolve-in-progress'];
  assert.ok(c41 && c41.main[0][0].node === 'w4042-move-in-progress');
});

// ---- Bug guard: jsCode syntax check ----

test('Guard: w4041 jsCode parses without SyntaxError via vm.Script', () => {
  new vm.Script('(function($input,$){' + jsCode + '})');
});

// ---- Algorithm tests ----

const mockPrior = {
  token: 'tok-abc',
  issue_id: 'issue-uuid-123',
  issue_identifier: 'PRO-196',
  worker: 'claude-code',
  _dispatched_persisted: true,
};

test('Case 1: started state found → _state_move_skip=false, _in_progress_state_id set', () => {
  const resp = {
    data: {
      team: {
        states: {
          nodes: [
            { id: 'state-unstarted', name: 'Todo', type: 'unstarted' },
            { id: 'state-started-1', name: 'In Progress', type: 'started' },
            { id: 'state-completed', name: 'Done', type: 'completed' },
          ],
        },
      },
    },
  };
  const result = runW4041(mockPrior, resp);
  assert.strictEqual(result.json._state_move_skip, false);
  assert.strictEqual(result.json._in_progress_state_id, 'state-started-1');
  assert.strictEqual(result.json.issue_id, 'issue-uuid-123', 'prior fields carried through');
});

test('Case 2: no started state → _state_move_skip=true', () => {
  const resp = {
    data: {
      team: {
        states: {
          nodes: [
            { id: 'state-unstarted', name: 'Todo', type: 'unstarted' },
            { id: 'state-completed', name: 'Done', type: 'completed' },
          ],
        },
      },
    },
  };
  const result = runW4041(mockPrior, resp);
  assert.strictEqual(result.json._state_move_skip, true);
  assert.ok(result.json._state_move_err, 'error message must be set');
});

test('Case 3: empty states array → _state_move_skip=true', () => {
  const resp = { data: { team: { states: { nodes: [] } } } };
  const result = runW4041(mockPrior, resp);
  assert.strictEqual(result.json._state_move_skip, true);
});

test('Case 4: null/missing data → _state_move_skip=true', () => {
  const result = runW4041(mockPrior, {});
  assert.strictEqual(result.json._state_move_skip, true);
});

test('Case 5: prior fields are carried through on success', () => {
  const resp = {
    data: { team: { states: { nodes: [{ id: 'sid', name: 'In Progress', type: 'started' }] } } },
  };
  const result = runW4041(mockPrior, resp);
  assert.strictEqual(result.json.token, 'tok-abc');
  assert.strictEqual(result.json.worker, 'claude-code');
  assert.strictEqual(result.json.issue_identifier, 'PRO-196');
});

test('Case 6: matches by name=In Progress even when In Review comes first', () => {
  // Regression guard: original code matched by type==='started' which would
  // return whichever started-state appeared first. Real Linear team has both
  // In Progress and In Review as type='started' — must disambiguate by name.
  const resp = {
    data: {
      team: {
        states: {
          nodes: [
            { id: 'sid-review', name: 'In Review', type: 'started' },
            { id: 'sid-progress', name: 'In Progress', type: 'started' },
          ],
        },
      },
    },
  };
  const result = runW4041(mockPrior, resp);
  assert.strictEqual(result.json._state_move_skip, false);
  assert.strictEqual(
    result.json._in_progress_state_id,
    'sid-progress',
    'must match In Progress by name, not first started state'
  );
});

test('Case 7: only In Review (started) present → skip (no In Progress)', () => {
  const resp = {
    data: {
      team: {
        states: {
          nodes: [{ id: 'sid-review', name: 'In Review', type: 'started' }],
        },
      },
    },
  };
  const result = runW4041(mockPrior, resp);
  assert.strictEqual(result.json._state_move_skip, true);
  assert.ok(result.json._state_move_err, 'must report missing In Progress state');
});

// ---- summary ----

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
