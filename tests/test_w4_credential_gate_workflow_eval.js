'use strict';
// PRO-216 / PRO-189: load w4023a + w4023c jsCode from w4-dispatch-button-handler.json,
// verify parse + credential-gate behavior (mock fs + process).
//
// Run: node tests/test_w4_credential_gate_workflow_eval.js

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
  'w4-dispatch-button-handler.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));

const w4023a = wf.nodes.find((n) => n.id === 'w4023a-validate-approved-credentials');
const w4023c = wf.nodes.find((n) => n.id === 'w4023c-write-credential-dlq');
if (!w4023a || !w4023a.parameters.jsCode) {
  throw new Error('w4023a-validate-approved-credentials missing');
}
if (!w4023c || !w4023c.parameters.jsCode) {
  throw new Error('w4023c-write-credential-dlq missing');
}

const jsGate = w4023a.parameters.jsCode;
const jsDlq = w4023c.parameters.jsCode;

function execGate(itemJson, readFileResult, envExtra) {
  const mockInput = { item: { json: itemJson } };
  const mock$ = (nodeName) => {
    if (nodeName === 'w4023-build-listener-request') {
      return { item: { json: itemJson } };
    }
    throw new Error('unexpected $ node: ' + nodeName);
  };
  const mergedEnv = { ...process.env, ...envExtra };
  const proc = { env: mergedEnv };
  const mockFs = { readFileSync: () => readFileResult };
  const mockRequire = (id) => {
    if (id === 'fs') return mockFs;
    return require(id);
  };
  const fn = new Function('$input', '$', 'process', 'require', jsGate);
  return fn(mockInput, mock$, proc, mockRequire);
}

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

const baseItem = {
  trace_id: 'tr1',
  worker: 'claude-code',
  issue_identifier: 'PRO-216',
  prompt_path_host: 'data/n8n_inbox/tr1.json',
};

test('Guard: w4023a jsCode parses via vm.Script', () => {
  new vm.Script('(function($input,$,process){' + jsGate + '})');
});

test('Guard: w4023c jsCode parses via vm.Script', () => {
  new vm.Script('(function($input,$){' + jsDlq + '})');
});

test('Gate: unknown worker → credential_gate_ok false', () => {
  const cfg = JSON.stringify({
    dispatch_targets: { 'claude-code': { allowed_credential: 'MIRU_ROUTING_KEY' } },
  });
  const r = execGate({ ...baseItem, worker: 'unknown-bot' }, cfg, { MIRU_ROUTING_KEY: 'x' });
  assert.strictEqual(r.json.credential_gate_ok, false);
  assert.ok(r.json.credential_gate_reason.includes('dispatch_targets'));
});

test('Gate: claude-code + empty MIRU_ROUTING_KEY → false', () => {
  const cfg = JSON.stringify({
    dispatch_targets: { 'claude-code': { allowed_credential: 'MIRU_ROUTING_KEY' } },
  });
  const r = execGate({ ...baseItem, worker: 'claude-code' }, cfg, { MIRU_ROUTING_KEY: '' });
  assert.strictEqual(r.json.credential_gate_ok, false);
  assert.ok(r.json.credential_gate_reason.includes('MIRU_ROUTING_KEY'));
});

test('Gate: claude-code + set MIRU_ROUTING_KEY → true', () => {
  const cfg = JSON.stringify({
    dispatch_targets: { 'claude-code': { allowed_credential: 'MIRU_ROUTING_KEY' } },
  });
  const r = execGate({ ...baseItem, worker: 'claude-code' }, cfg, { MIRU_ROUTING_KEY: 'sk-test' });
  assert.strictEqual(r.json.credential_gate_ok, true);
});

test('Gate: invalid JSON config → false', () => {
  const r = execGate(baseItem, '{not-json', { MIRU_ROUTING_KEY: 'x' });
  assert.strictEqual(r.json.credential_gate_ok, false);
  assert.ok(r.json.credential_gate_reason.includes('parse'));
});

test('connections: w4023 → w4023a → w4023b → w4024', () => {
  const a = wf.connections['w4023-build-listener-request'].main[0][0].node;
  assert.strictEqual(a, 'w4023a-validate-approved-credentials');
  const b = wf.connections['w4023a-validate-approved-credentials'].main[0][0].node;
  assert.strictEqual(b, 'w4023b-credential-gate');
  const ok = wf.connections['w4023b-credential-gate'].main[0][0].node;
  assert.strictEqual(ok, 'w4024-post-listener');
  const bad = wf.connections['w4023b-credential-gate'].main[1][0].node;
  assert.strictEqual(bad, 'w4023c-write-credential-dlq');
  const tel = wf.connections['w4023c-write-credential-dlq'].main[0][0].node;
  assert.strictEqual(tel, 'w4023d-operator-credential-alert');
});

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
