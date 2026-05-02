'use strict';
// PRO-251 / PRO-189: load w7-report-format jsCode from w7-telegram-callback-handler.json,
// verify parse + formatting behavior.
//
// Run: node tests/test_w7_report_command_workflow_eval.js

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
  'w7-telegram-callback-handler.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));

const formatNode = wf.nodes.find((n) => n.id === 'w7-report-format');
if (!formatNode || !formatNode.parameters || !formatNode.parameters.jsCode) {
  throw new Error('w7-report-format node missing or has no jsCode');
}
const jsFormat = formatNode.parameters.jsCode;

function execFormat(reportJson) {
  const mockInput = { item: { json: reportJson } };
  const fn = new Function('$input', jsFormat);
  return fn(mockInput);
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

// Guard: jsCode parses
test('Guard: w7-report-format jsCode parses via vm.Script', () => {
  new vm.Script('(function($input){' + jsFormat + '})');
});

// Basic shape: always returns { json: { text: string } }
test('Returns { json: { text: string } } for minimal input', () => {
  const r = execFormat({
    generated_at: '2026-05-01T00:00:00Z',
    dlq_count: 0,
    last_completions: [],
    last_heartbeats: [],
  });
  assert.ok(r && r.json && typeof r.json.text === 'string', 'text must be a string');
});

// DLQ count appears in output
test('DLQ count appears in output', () => {
  const r = execFormat({
    generated_at: '2026-05-01T00:00:00Z',
    dlq_count: 3,
    last_completions: [],
    last_heartbeats: [],
  });
  assert.ok(r.json.text.includes('3'), 'dlq_count=3 should appear in text');
});

// Completion entries appear
test('Completion entries rendered in output', () => {
  const r = execFormat({
    generated_at: '2026-05-01T00:00:00Z',
    dlq_count: 0,
    last_completions: [
      { ticket_id: 'PRO-251', status: 'CONFIRMED_WORKING', summary: 'ops report endpoint shipped' },
    ],
    last_heartbeats: [],
  });
  assert.ok(r.json.text.includes('PRO-251'), 'ticket_id should appear');
  assert.ok(r.json.text.includes('CONFIRMED_WORKING'), 'status should appear');
});

// Heartbeat entries appear
test('Heartbeat entries rendered in output', () => {
  const r = execFormat({
    generated_at: '2026-05-01T00:00:00Z',
    dlq_count: 0,
    last_completions: [],
    last_heartbeats: [
      {
        worker_id: 'miru-w2',
        ticket_id: 'PRO-251',
        step: 'writing_tests',
        ts: '2026-05-01T12:00:00Z',
      },
    ],
  });
  assert.ok(r.json.text.includes('miru-w2'), 'worker_id should appear');
  assert.ok(r.json.text.includes('PRO-251'), 'ticket_id should appear');
});

// Null/missing fields don't throw
test('Null fields handled gracefully', () => {
  const r = execFormat({
    generated_at: null,
    dlq_count: null,
    last_completions: null,
    last_heartbeats: null,
  });
  assert.ok(r && r.json && typeof r.json.text === 'string');
});

// Verify w7-type-branch and new /report connections in the workflow
test('connections: w7001 -> w7-type-branch (not w7002-ack-callback directly)', () => {
  const targets = wf.connections['w7001-telegram-trigger'].main[0].map((c) => c.node);
  assert.ok(targets.includes('w7-type-branch'), 'w7001 must connect to w7-type-branch');
  assert.ok(!targets.includes('w7002-ack-callback'), 'w7001 must NOT connect directly to w7002');
});

test('connections: w7-type-branch true -> w7002-ack-callback', () => {
  const trueTargets = wf.connections['w7-type-branch'].main[0].map((c) => c.node);
  assert.ok(
    trueTargets.includes('w7002-ack-callback'),
    'true branch must go to w7002-ack-callback'
  );
});

test('connections: w7-type-branch false -> w7-report-if', () => {
  const falseTargets = wf.connections['w7-type-branch'].main[1].map((c) => c.node);
  assert.ok(falseTargets.includes('w7-report-if'), 'false branch must go to w7-report-if');
});

test('connections: w7-report-if -> w7-report-fetch -> w7-report-format -> w7-report-send', () => {
  const fetch = wf.connections['w7-report-if'].main[0][0].node;
  assert.strictEqual(fetch, 'w7-report-fetch');
  const fmt = wf.connections['w7-report-fetch'].main[0][0].node;
  assert.strictEqual(fmt, 'w7-report-format');
  const send = wf.connections['w7-report-format'].main[0][0].node;
  assert.strictEqual(send, 'w7-report-send');
});

test('w7001-telegram-trigger updates includes both callback_query and message', () => {
  const trigger = wf.nodes.find((n) => n.id === 'w7001-telegram-trigger');
  const updates = trigger.parameters.updates;
  assert.ok(updates.includes('callback_query'), 'must include callback_query');
  assert.ok(updates.includes('message'), 'must include message');
});

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
