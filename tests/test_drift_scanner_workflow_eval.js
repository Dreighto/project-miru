'use strict';
// Boundary-crossing test for dsw003-classify-drift (Ticket B7).
// PRO-189 lesson: load the JS as it lives in the workflow JSON, eval it,
// then exercise the algorithm — NOT a clean extracted copy. This catches
// SyntaxError class bugs and any deploy-time mangling.
//
// Run: node tests/test_drift_scanner_workflow_eval.js

const fs = require('fs');
const path = require('path');
const assert = require('assert');

const WORKFLOW = path.join(__dirname, '..', 'docker', 'n8n', 'workflows', 'w-drift-scanner.json');

let testsRun = 0;
let testsPassed = 0;
function test(name, fn) {
  testsRun += 1;
  try {
    fn();
    testsPassed += 1;
    console.log(`  PASS  ${name}`);
  } catch (err) {
    console.error(`  FAIL  ${name}`);
    console.error(`         ${err.message}`);
    if (err.stack) console.error(err.stack.split('\n').slice(1, 4).join('\n'));
  }
}

// Load the workflow JSON.
const wf = JSON.parse(fs.readFileSync(WORKFLOW, 'utf8'));

function getNodeJsCode(nodeId) {
  const node = wf.nodes.find((n) => n.id === nodeId);
  if (!node) throw new Error(`node ${nodeId} not found in workflow`);
  return node.parameters.jsCode;
}

// Wrap a Code node's source so we can call it as a function with an injected
// $input / $env / require context. n8n exposes require to Code nodes (it's
// how stlw002, ccp002, dsw003 etc. read JSONL files via fs). For the
// boundary test we pass node's real require in.
function buildCodeRunner(jsCode, _mode) {
  return new Function('require', '$input', '$env', '$getWorkflowStaticData', `${jsCode}`);
}

// ─── Test 1: dsw003 jsCode parses (no SyntaxError) ──────────────────────────
test('dsw003-classify-drift jsCode parses without SyntaxError', () => {
  const code = getNodeJsCode('dsw003-classify-drift');
  // Just constructing the Function will throw on syntax error.
  buildCodeRunner(code, 'runOnceForAllItems');
  assert.ok(code.length > 100, 'code should be substantial');
});

// ─── Test 2: dsw004 jsCode parses ───────────────────────────────────────────
test('dsw004-append-log jsCode parses without SyntaxError', () => {
  buildCodeRunner(getNodeJsCode('dsw004-append-log'), 'runOnceForEachItem');
});

// ─── Test 3: dsw006 jsCode parses ───────────────────────────────────────────
test('dsw006-build-message jsCode parses without SyntaxError', () => {
  buildCodeRunner(getNodeJsCode('dsw006-build-message'), 'runOnceForEachItem');
});

// ─── Test 4: dsw003 classification logic — drift cases produce correct counts
test('dsw003 classification: missing_marker + stale_linear logic', () => {
  // Build a fake $input that simulates the Linear GraphQL response.
  const linearResponse = {
    data: {
      team: {
        issues: {
          nodes: [
            {
              identifier: 'PRO-100',
              title: 'Done with no marker',
              state: { name: 'Done', type: 'completed' },
              updatedAt: '2026-05-01T00:00:00Z',
            },
            {
              identifier: 'PRO-101',
              title: 'In Review with marker',
              state: { name: 'In Review', type: 'started' },
              updatedAt: '2026-05-01T00:00:00Z',
            },
            {
              identifier: 'PRO-102',
              title: 'In Progress with stale marker',
              state: { name: 'In Progress', type: 'started' },
              updatedAt: '2026-05-01T00:00:00Z',
            },
            {
              identifier: 'PRO-103',
              title: 'Todo no marker (clean)',
              state: { name: 'Todo', type: 'unstarted' },
              updatedAt: '2026-05-01T00:00:00Z',
            },
            {
              identifier: 'PRO-104',
              title: 'Done with marker (clean)',
              state: { name: 'Done', type: 'completed' },
              updatedAt: '2026-05-01T00:00:00Z',
            },
          ],
        },
      },
    },
  };
  const $input = { first: () => ({ json: linearResponse }) };

  // Stub out fs.readFileSync for cc_completion_log.jsonl. We'll temporarily
  // override fs.existsSync + readFileSync just for this test.
  const fsModule = require('fs');
  const origExists = fsModule.existsSync;
  const origRead = fsModule.readFileSync;
  fsModule.existsSync = (p) => (p === '/miru-data/cc_completion_log.jsonl' ? true : origExists(p));
  fsModule.readFileSync = (p, enc) => {
    if (p === '/miru-data/cc_completion_log.jsonl') {
      return [
        JSON.stringify({ ticket_id: 'PRO-101', status: 'CONFIRMED_WORKING' }),
        JSON.stringify({ ticket_id: 'PRO-102', status: 'CONFIRMED_WORKING' }),
        JSON.stringify({ ticket_id: 'PRO-104', status: 'CONFIRMED_WORKING' }),
        '',
      ].join('\n');
    }
    return origRead(p, enc);
  };

  try {
    const code = getNodeJsCode('dsw003-classify-drift');
    const fn = buildCodeRunner(code, 'runOnceForAllItems');
    const result = fn(require, $input, {}, () => ({}));
    const out = Array.isArray(result) ? result[0].json : result.json;

    assert.strictEqual(out.scanned, 5);
    assert.strictEqual(out.drift_count, 2, `expected 2 drift rows, got ${out.drift_count}`);
    assert.strictEqual(out.missing_marker.length, 1, 'PRO-100 should be MISSING_MARKER');
    assert.strictEqual(out.missing_marker[0].ticket_id, 'PRO-100');
    assert.strictEqual(out.stale_linear.length, 1, 'PRO-102 should be STALE_LINEAR');
    assert.strictEqual(out.stale_linear[0].ticket_id, 'PRO-102');
  } finally {
    fsModule.existsSync = origExists;
    fsModule.readFileSync = origRead;
  }
});

// ─── Test 5: dsw003 with clean state (no drift) ────────────────────────────
test('dsw003 classification: clean state produces drift_count=0', () => {
  const linearResponse = {
    data: {
      team: {
        issues: {
          nodes: [
            {
              identifier: 'PRO-200',
              title: 'Todo',
              state: { name: 'Todo', type: 'unstarted' },
              updatedAt: '2026-05-01T00:00:00Z',
            },
            {
              identifier: 'PRO-201',
              title: 'Done with marker',
              state: { name: 'Done', type: 'completed' },
              updatedAt: '2026-05-01T00:00:00Z',
            },
          ],
        },
      },
    },
  };
  const $input = { first: () => ({ json: linearResponse }) };

  const fsModule = require('fs');
  const origExists = fsModule.existsSync;
  const origRead = fsModule.readFileSync;
  fsModule.existsSync = (p) => (p === '/miru-data/cc_completion_log.jsonl' ? true : origExists(p));
  fsModule.readFileSync = (p, enc) => {
    if (p === '/miru-data/cc_completion_log.jsonl') {
      return JSON.stringify({ ticket_id: 'PRO-201', status: 'CONFIRMED_WORKING' });
    }
    return origRead(p, enc);
  };

  try {
    const code = getNodeJsCode('dsw003-classify-drift');
    const fn = buildCodeRunner(code, 'runOnceForAllItems');
    const result = fn(require, $input, {}, () => ({}));
    const out = Array.isArray(result) ? result[0].json : result.json;
    assert.strictEqual(out.drift_count, 0);
    assert.strictEqual(out.scanned, 2);
    assert.strictEqual(out.missing_marker.length, 0);
    assert.strictEqual(out.stale_linear.length, 0);
  } finally {
    fsModule.existsSync = origExists;
    fsModule.readFileSync = origRead;
  }
});

// ─── Test 6: workflow JSON is structurally valid ───────────────────────────
test('workflow JSON has valid name/nodes/connections + connection integrity', () => {
  assert.ok(wf.name, 'name required');
  assert.ok(Array.isArray(wf.nodes) && wf.nodes.length > 0, 'nodes array required');
  assert.ok(typeof wf.connections === 'object', 'connections object required');
  const nodeNames = new Set(wf.nodes.map((n) => n.name));
  for (const [src, conn] of Object.entries(wf.connections)) {
    assert.ok(nodeNames.has(src), `connection source '${src}' must be a node`);
    for (const branch of conn.main || []) {
      for (const target of branch || []) {
        assert.ok(nodeNames.has(target.node), `connection target '${target.node}' must be a node`);
      }
    }
  }
});

console.log(`\n${testsPassed}/${testsRun} tests passed`);
process.exit(testsPassed === testsRun ? 0 : 1);
