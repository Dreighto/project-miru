'use strict';
// Boundary-crossing tests for w-linear-completion-bridge.json (PRO-196).
// Loads the workflow JSON from disk, extracts jsCode from each Code node,
// and evals via new Function / vm.Script — same boundary n8n crosses at runtime.
//
// Per PRO-189 adopted lesson: tests MUST load from disk and eval the exact
// bytes that n8n will execute, not a clean extracted copy.
//
// Tests cover:
//   - wlcb002-read-and-diff: SyntaxError guard + $getWorkflowStaticData mutation + algorithm
//   - wlcb005-parse-row: SyntaxError guard + all status types + skip conditions
//   - wlcb008-resolve-ids: SyntaxError guard + UUID extraction + missing issue
//
// Run with: node tests/test_linear_completion_bridge_workflow_eval.js

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
  'w-linear-completion-bridge.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));

function extractCode(nodeId) {
  const node = wf.nodes.find((n) => n.id === nodeId);
  if (!node) throw new Error(nodeId + ' node not found in workflow JSON');
  const code = node.parameters && node.parameters.jsCode;
  if (!code) throw new Error('jsCode not found in ' + nodeId);
  return code;
}

const wlcb002Code = extractCode('wlcb002-read-and-diff');
const wlcb005Code = extractCode('wlcb005-parse-row');
const wlcb008Code = extractCode('wlcb008-resolve-ids');

// ---- harnesses ----

const TMP_DIR = path.join(__dirname, '_tmp');
if (!fs.existsSync(TMP_DIR)) fs.mkdirSync(TMP_DIR, { recursive: true });

// Run wlcb002 jsCode in a sandboxed function.
// fileContent: string (written to a temp file) or null (simulates ENOENT).
// initState: object (initial static-data values, mutated in place).
function runWLCB002(fileContent, initState) {
  const state = Object.assign({}, initState);

  const tmpFile = path.join(
    TMP_DIR,
    'wlcb002_test_' + Date.now() + '_' + Math.random().toString(36).slice(2) + '.jsonl'
  );
  const targetPath =
    fileContent === null ? '/nonexistent/path/wlcb002_enoent_sentinel_' + Date.now() : tmpFile;

  if (fileContent !== null) {
    fs.writeFileSync(tmpFile, fileContent, 'utf8');
  }

  // Patch hardcoded JSONL path using JSON.stringify for safe Windows path escaping.
  const patchedCode = wlcb002Code.replace(
    "'/miru-data/cc_completion_log.jsonl'",
    JSON.stringify(targetPath)
  );

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

// Run wlcb005 jsCode with a single row string as input.
// `require` must be passed explicitly — n8n's Code node injects it implicitly
// at runtime, but `new Function` runs in the global scope where it isn't.
function runWLCB005(rowStr) {
  const fn = new Function('$input', '$env', 'require', wlcb005Code);
  const mockInput = { item: { json: { new_rows: rowStr } } };
  const mockEnv = {};
  return fn(mockInput, mockEnv, require);
}

// Run wlcb008 jsCode with mock inputs.
function runWLCB008(priorJson, inputJson) {
  const fn = new Function('$input', '$', wlcb008Code);
  const mockInput = { item: { json: inputJson } };
  const mockSelector = (nodeName) => {
    if (nodeName === 'wlcb005-parse-row') {
      return { item: { json: priorJson } };
    }
    throw new Error('unexpected node selector: ' + nodeName);
  };
  return fn(mockInput, mockSelector);
}

function hashRow(line) {
  return crypto.createHash('sha1').update(line.trim()).digest('hex');
}

const mkRow = (n) =>
  `{"ticket_id":"PRO-${n}","status":"CONFIRMED_WORKING","summary":"test ${n}","timestamp":"2026-04-29T00:00:00Z"}`;

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

// ============================================================
// wlcb002 tests
// ============================================================

test('wlcb002 Guard B: jsCode parses without SyntaxError via vm.Script', () => {
  new vm.Script('(function($getWorkflowStaticData,require){' + wlcb002Code + '})');
});

test('wlcb002 Guard A: $getWorkflowStaticData call mutates state (not a string)', () => {
  const rows = [mkRow(1), mkRow(2), mkRow(3)];
  const { state } = runWLCB002(rows.join('\n') + '\n', {});
  assert.ok(
    Array.isArray(state.lc_pinged_hashes),
    'lc_pinged_hashes must be an array — Bug A would leave it absent'
  );
  assert.strictEqual(state.lc_pinged_hashes.length, 3);
});

test('wlcb002 Case 1: empty state + empty file → empty new_rows', () => {
  const { json } = runWLCB002('', {});
  assert.deepStrictEqual(json.new_rows, []);
  assert.strictEqual(json.regressed, false);
  assert.strictEqual(json._init, undefined);
});

test('wlcb002 Case 2: empty state + 5 rows → seed all, no pings', () => {
  const rows = [1, 2, 3, 4, 5].map(mkRow);
  const { json, state } = runWLCB002(rows.join('\n') + '\n', {});
  assert.deepStrictEqual(json.new_rows, []);
  assert.strictEqual(json._init, true);
  assert.strictEqual(json.regressed, false);
  assert.strictEqual(state.lc_pinged_hashes.length, 5);
});

test('wlcb002 Case 3: state has 5 + same 5 rows → no new rows', () => {
  const rows = [1, 2, 3, 4, 5].map(mkRow);
  const { json } = runWLCB002(rows.join('\n') + '\n', {
    lc_pinged_hashes: rows.map(hashRow),
  });
  assert.deepStrictEqual(json.new_rows, []);
  assert.strictEqual(json.regressed, false);
});

test('wlcb002 Case 4: state has 5 + 1 new row → 1 new_row', () => {
  const origRows = [1, 2, 3, 4, 5].map(mkRow);
  const allRows = [...origRows, mkRow(6)];
  const { json, state } = runWLCB002(allRows.join('\n') + '\n', {
    lc_pinged_hashes: origRows.map(hashRow),
  });
  assert.strictEqual(json.new_rows.length, 1);
  assert.strictEqual(json.new_rows[0], mkRow(6));
  assert.strictEqual(state.lc_pinged_hashes.length, 6);
});

test('wlcb002 Case 5: state uses lc_pinged_hashes (not cc_completion_pinged_hashes)', () => {
  const rows = [1, 2].map(mkRow);
  const { state } = runWLCB002(rows.join('\n') + '\n', {});
  assert.ok(Array.isArray(state.lc_pinged_hashes), 'must use lc_pinged_hashes key');
  assert.strictEqual(state.cc_completion_pinged_hashes, undefined, 'must NOT use ccp key');
});

test('wlcb002 Case 6: ENOENT + non-empty state → regressed=true', () => {
  const rows = [1, 2, 3].map(mkRow);
  const { json } = runWLCB002(null, { lc_pinged_hashes: rows.map(hashRow) });
  assert.strictEqual(json.regressed, true);
  assert.strictEqual(json._file_missing, true);
});

// ============================================================
// wlcb005 tests
// ============================================================

test('wlcb005 Guard: jsCode parses without SyntaxError via vm.Script', () => {
  new vm.Script('(function($input,$env,require){' + wlcb005Code + '})');
});

test('wlcb005 Case 1: CONFIRMED_WORKING → _skip=false, comment has icon', () => {
  const row = JSON.stringify({
    ticket_id: 'PRO-196',
    status: 'CONFIRMED_WORKING',
    summary: 'all good',
    test_evidence: '11/11 tests pass',
    branch: 'dreighto/pro-196-test',
    pr_number: 42,
    files_touched: ['foo.py'],
    notes: '',
  });
  const result = runWLCB005(row);
  assert.strictEqual(result.json._skip, false);
  assert.strictEqual(result.json.ticket_id, 'PRO-196');
  assert.strictEqual(result.json.status, 'CONFIRMED_WORKING');
  assert.ok(result.json.comment_body.includes('CONFIRMED_WORKING'));
  assert.ok(result.json.comment_body.includes('all good'));
  assert.ok(result.json.comment_body.includes('11/11 tests pass'));
  assert.ok(result.json.comment_body.includes('dreighto/pro-196-test'));
  assert.ok(result.json.comment_body.includes('#42'));
});

test('wlcb005 Case 2: INCONCLUSIVE → _skip=false', () => {
  const row = JSON.stringify({ ticket_id: 'PRO-100', status: 'INCONCLUSIVE', summary: 'unclear' });
  const result = runWLCB005(row);
  assert.strictEqual(result.json._skip, false);
  assert.strictEqual(result.json.status, 'INCONCLUSIVE');
  assert.ok(result.json.comment_body.includes('INCONCLUSIVE'));
});

test('wlcb005 Case 3: FAILED → _skip=false', () => {
  const row = JSON.stringify({ ticket_id: 'PRO-101', status: 'FAILED', summary: 'broke' });
  const result = runWLCB005(row);
  assert.strictEqual(result.json._skip, false);
  assert.strictEqual(result.json.status, 'FAILED');
  assert.ok(result.json.comment_body.includes('FAILED'));
});

test('wlcb005 Case 4: non-PRO ticket_id → _skip=true', () => {
  const row = JSON.stringify({ ticket_id: 'BOOTSTRAP', status: 'CONFIRMED_WORKING', summary: 'x' });
  const result = runWLCB005(row);
  assert.strictEqual(result.json._skip, true);
  assert.ok(result.json._skip_reason.includes('BOOTSTRAP'));
});

test('wlcb005 Case 5: malformed JSON → _skip=true', () => {
  const result = runWLCB005('not-json{{{');
  assert.strictEqual(result.json._skip, true);
  assert.ok(result.json._skip_reason.includes('invalid JSON'));
});

test('wlcb005 Case 6: unknown status → _skip=true', () => {
  const row = JSON.stringify({ ticket_id: 'PRO-99', status: 'PARTIAL', summary: 'x' });
  const result = runWLCB005(row);
  assert.strictEqual(result.json._skip, true);
  assert.ok(result.json._skip_reason.includes('unknown status'));
});

test('wlcb005 Case 7: files_touched included in comment (max 20)', () => {
  const files = Array.from({ length: 25 }, (_, i) => `file_${i}.py`);
  const row = JSON.stringify({
    ticket_id: 'PRO-200',
    status: 'CONFIRMED_WORKING',
    summary: 'many files',
    files_touched: files,
  });
  const result = runWLCB005(row);
  assert.strictEqual(result.json._skip, false);
  // Should include first 20 files but not the 21st+
  assert.ok(result.json.comment_body.includes('file_19.py'));
  assert.ok(!result.json.comment_body.includes('file_20.py'));
});

test('wlcb005 Case 8: comment body includes <!-- bridge:hash:... --> marker', () => {
  const rowStr = JSON.stringify({
    ticket_id: 'PRO-196',
    status: 'CONFIRMED_WORKING',
    summary: 'dedup marker test',
  });
  const result = runWLCB005(rowStr);
  assert.strictEqual(result.json._skip, false);
  const expectedHash = hashRow(rowStr);
  assert.ok(
    result.json.comment_body.includes('<!-- bridge:hash:' + expectedHash + ' -->'),
    'comment must include hash marker for future dedup'
  );
  assert.strictEqual(result.json._row_hash, expectedHash, '_row_hash field must be set');
});

test('wlcb005 Case 9: _row_hash carried through even on skip rows', () => {
  const rowStr = JSON.stringify({ ticket_id: 'BOOTSTRAP', status: 'CONFIRMED_WORKING' });
  const result = runWLCB005(rowStr);
  assert.strictEqual(result.json._skip, true);
  assert.strictEqual(result.json._row_hash, hashRow(rowStr));
});

// ============================================================
// wlcb008 tests
// ============================================================

test('wlcb008 Guard: jsCode parses without SyntaxError via vm.Script', () => {
  new vm.Script('(function($input,$){' + wlcb008Code + '})');
});

const mockPrior005 = {
  _skip: false,
  ticket_id: 'PRO-196',
  status: 'CONFIRMED_WORKING',
  comment_body: '✅ **CC CONFIRMED_WORKING**\n\nall done',
};

test('wlcb008 Case 1: issue found + In Review state → all IDs resolved', () => {
  const resp = {
    data: {
      issues: {
        nodes: [
          {
            id: 'issue-uuid-abc',
            identifier: 'PRO-196',
            title: 'PRO-196 title',
            state: { id: 'state-in-progress', name: 'In Progress' },
          },
        ],
      },
      team: {
        states: {
          nodes: [
            { id: 'state-todo', name: 'Todo', type: 'unstarted' },
            { id: 'state-in-progress', name: 'In Progress', type: 'started' },
            { id: 'state-in-review', name: 'In Review', type: 'started' },
            { id: 'state-done', name: 'Done', type: 'completed' },
          ],
        },
      },
    },
  };
  const result = runWLCB008(mockPrior005, resp);
  assert.strictEqual(result.json._linear_skip, false);
  assert.strictEqual(result.json._issue_id, 'issue-uuid-abc');
  assert.strictEqual(result.json._in_review_state_id, 'state-in-review');
  assert.strictEqual(result.json._issue_title, 'PRO-196 title');
  // prior fields carried through
  assert.strictEqual(result.json.ticket_id, 'PRO-196');
  assert.strictEqual(result.json.comment_body, '✅ **CC CONFIRMED_WORKING**\n\nall done');
});

test('wlcb008 Case 2: issue not found → _linear_skip=true', () => {
  const resp = {
    data: {
      issues: { nodes: [] },
      team: { states: { nodes: [{ id: 'sid', name: 'In Review', type: 'started' }] } },
    },
  };
  const result = runWLCB008(mockPrior005, resp);
  assert.strictEqual(result.json._linear_skip, true);
  assert.ok(result.json._linear_err.includes('PRO-196'));
});

test('wlcb008 Case 3: In Review state absent → _in_review_state_id=null', () => {
  const resp = {
    data: {
      issues: {
        nodes: [
          {
            id: 'issue-uuid-abc',
            identifier: 'PRO-196',
            title: 't',
            state: { id: 'sid', name: 'In Progress' },
          },
        ],
      },
      team: { states: { nodes: [{ id: 'sid', name: 'In Progress', type: 'started' }] } },
    },
  };
  const result = runWLCB008(mockPrior005, resp);
  assert.strictEqual(result.json._linear_skip, false);
  assert.strictEqual(result.json._in_review_state_id, null);
  assert.strictEqual(result.json._issue_id, 'issue-uuid-abc');
});

test('wlcb008 Case 4: null/missing data → _linear_skip=true', () => {
  const result = runWLCB008(mockPrior005, {});
  assert.strictEqual(result.json._linear_skip, true);
});

// ============================================================
// workflow-level structure tests
// ============================================================

test('Bridge workflow has error workflow wired to W1 error handler', () => {
  assert.strictEqual(wf.settings.errorWorkflow, 'l5wzFuWnJ2zSoMM2');
});

test('Bridge workflow wlcb006-skip-branch output 0 → wlcb007 (not skipped path)', () => {
  const conn = wf.connections['wlcb006-skip-branch'];
  assert.ok(conn, 'wlcb006 must have connections');
  assert.strictEqual(
    conn.main[0][0].node,
    'wlcb007-lookup-issue',
    'output 0 = not-skipped → lookup'
  );
  assert.strictEqual(conn.main[1][0].node, 'wlcb-noop-skipped', 'output 1 = skipped → noop');
});

test('Bridge mutation nodes have continueOnFail=true', () => {
  const n009 = wf.nodes.find((n) => n.id === 'wlcb009-post-comment');
  const n010 = wf.nodes.find((n) => n.id === 'wlcb010-move-in-review');
  assert.ok(n009 && n009.continueOnFail, 'wlcb009 must have continueOnFail=true');
  assert.ok(n010 && n010.continueOnFail, 'wlcb010 must have continueOnFail=true');
});

test('wlcb010 jsonBody gates state move on status === CONFIRMED_WORKING', () => {
  // Structural test: wlcb010 is a no-op (sends __typename) when status is not
  // CONFIRMED_WORKING. The expression lives in jsonBody, not jsCode, so we
  // check the workflow JSON literal contains the gating clause.
  const n010 = wf.nodes.find((n) => n.id === 'wlcb010-move-in-review');
  const body = n010.parameters.jsonBody;
  assert.ok(
    body.includes("status !== 'CONFIRMED_WORKING'"),
    'wlcb010 must skip state move when status is not CONFIRMED_WORKING'
  );
});

// ---- summary ----

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
