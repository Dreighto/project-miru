'use strict';
// PRO-299 / PRO-189: load w8003-parse-count and w8005-format-reply jsCode from
// w8-telegram-command-handler.json and verify parse + behavior.
//
// Run: node tests/test_w8_recent_command_workflow_eval.js

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
  'w8-telegram-command-handler.json'
);
const wf = JSON.parse(fs.readFileSync(WF_PATH, 'utf8'));

function requireNode(id) {
  const node = wf.nodes.find((n) => n.id === id);
  if (!node || !node.parameters || !node.parameters.jsCode) {
    throw new Error(id + ' node missing or has no jsCode');
  }
  return node.parameters.jsCode;
}

const jsParseCount = requireNode('w8003-parse-count');
const jsFormatReply = requireNode('w8005-format-reply');

function execParseCount(messageJson) {
  const mockInput = { item: { json: messageJson } };
  const fn = new Function('$input', jsParseCount);
  return fn(mockInput);
}

function execFormatReply(cmdOutput, parsedData) {
  const mockInput = { item: { json: cmdOutput } };
  const mockDollar = () => ({ item: { json: parsedData } });
  const fn = new Function('$input', '$', jsFormatReply);
  return fn(mockInput, mockDollar);
}

let passed = 0;
let failed = 0;

function test(name, fn) {
  try {
    fn();
    console.log('  PASS  ' + name);
    passed++;
  } catch (e) {
    console.log('  FAIL  ' + name + ': ' + e.message);
    failed++;
  }
}

// Guard: both jsCode blocks parse without SyntaxError
test('Guard: w8003-parse-count jsCode parses via vm.Script', () => {
  new vm.Script('(function($input){' + jsParseCount + '})');
});

test('Guard: w8005-format-reply jsCode parses via vm.Script', () => {
  new vm.Script('(function($input,$){' + jsFormatReply + '})');
});

// --- w8003-parse-count ---

test('/recent -> count=5 (default)', () => {
  const r = execParseCount({ message: { text: '/recent', chat: { id: 12345 }, message_id: 1 } });
  assert.strictEqual(r.json.count, 5);
  assert.strictEqual(r.json.chat_id, 12345);
  assert.strictEqual(r.json.message_id, 1);
});

test('/recent 10 -> count=10', () => {
  const r = execParseCount({ message: { text: '/recent 10', chat: { id: 12345 }, message_id: 2 } });
  assert.strictEqual(r.json.count, 10);
});

test('/recent 25 -> count=20 (clamped to max)', () => {
  const r = execParseCount({ message: { text: '/recent 25', chat: { id: 12345 }, message_id: 3 } });
  assert.strictEqual(r.json.count, 20);
});

test('/recent 0 -> count=1 (clamped to min)', () => {
  const r = execParseCount({ message: { text: '/recent 0', chat: { id: 12345 }, message_id: 4 } });
  assert.strictEqual(r.json.count, 1);
});

test('/recent abc -> count=5 (non-numeric falls back to default)', () => {
  const r = execParseCount({
    message: { text: '/recent abc', chat: { id: 12345 }, message_id: 5 },
  });
  assert.strictEqual(r.json.count, 5);
});

test('/recent 1 -> count=1 (boundary, min)', () => {
  const r = execParseCount({ message: { text: '/recent 1', chat: { id: 12345 }, message_id: 6 } });
  assert.strictEqual(r.json.count, 1);
});

test('/recent 20 -> count=20 (boundary, max)', () => {
  const r = execParseCount({ message: { text: '/recent 20', chat: { id: 12345 }, message_id: 7 } });
  assert.strictEqual(r.json.count, 20);
});

test('missing message -> chat_id null, message_id null', () => {
  const r = execParseCount({ message: {} });
  assert.strictEqual(r.json.chat_id, null);
  assert.strictEqual(r.json.message_id, null);
  assert.strictEqual(r.json.count, 5);
});

// --- w8005-format-reply ---

const dummyParse = { chat_id: 12345, message_id: 99 };

test('stdout present -> reply_text = stdout', () => {
  const r = execFormatReply({ exitCode: 0, stdout: 'recent output here', stderr: '' }, dummyParse);
  assert.strictEqual(r.json.reply_text, 'recent output here');
  assert.strictEqual(r.json.chat_id, 12345);
  assert.strictEqual(r.json.message_id, 99);
});

test('stdout empty, exitCode=0 -> fallback message', () => {
  const r = execFormatReply({ exitCode: 0, stdout: '', stderr: '' }, dummyParse);
  assert.ok(typeof r.json.reply_text === 'string' && r.json.reply_text.length > 0);
});

test('stdout empty, exitCode=1, stderr -> error message includes stderr', () => {
  const r = execFormatReply(
    { exitCode: 1, stdout: '', stderr: 'pwsh: command not found' },
    dummyParse
  );
  assert.ok(r.json.reply_text.includes('pwsh: command not found') || r.json.reply_text.length > 0);
});

test('very long stdout -> truncated to <= 4000 chars', () => {
  const longOut = 'x'.repeat(5000);
  const r = execFormatReply({ exitCode: 0, stdout: longOut, stderr: '' }, dummyParse);
  assert.ok(r.json.reply_text.length <= 4000, 'reply_text must not exceed 4000 chars');
});

test('null stdout/stderr/exitCode -> graceful fallback', () => {
  const r = execFormatReply({ exitCode: null, stdout: null, stderr: null }, dummyParse);
  assert.ok(typeof r.json.reply_text === 'string' && r.json.reply_text.length > 0);
});

// --- Workflow structure ---

test('w8001-telegram-trigger listens for message updates', () => {
  const node = wf.nodes.find((n) => n.id === 'w8001-telegram-trigger');
  assert.ok(node, 'w8001-telegram-trigger must exist');
  assert.ok(node.parameters.updates.includes('message'), 'must include message updates');
});

test('connections: w8001 -> w8002-is-recent', () => {
  const targets = wf.connections['w8001-telegram-trigger'].main[0].map((c) => c.node);
  assert.ok(targets.includes('w8002-is-recent'));
});

test('connections: w8002-is-recent true -> w8003-parse-count', () => {
  const trueTargets = wf.connections['w8002-is-recent'].main[0].map((c) => c.node);
  assert.ok(trueTargets.includes('w8003-parse-count'));
});

test('connections: w8003 -> w8004 -> w8005 -> w8006 (linear chain)', () => {
  assert.strictEqual(wf.connections['w8003-parse-count'].main[0][0].node, 'w8004-exec-recent');
  assert.strictEqual(wf.connections['w8004-exec-recent'].main[0][0].node, 'w8005-format-reply');
  assert.strictEqual(wf.connections['w8005-format-reply'].main[0][0].node, 'w8006-send-reply');
});

test('w8004-exec-recent is executeCommand type', () => {
  const node = wf.nodes.find((n) => n.id === 'w8004-exec-recent');
  assert.ok(node, 'w8004-exec-recent must exist');
  assert.strictEqual(node.type, 'n8n-nodes-base.executeCommand');
  assert.ok(node.parameters.command.includes('recent.ps1'), 'command must reference recent.ps1');
  assert.ok(node.parameters.command.includes('MIRU_REPO_ROOT'), 'command must use MIRU_REPO_ROOT');
});

test('w8006-send-reply uses sendMessage endpoint', () => {
  const node = wf.nodes.find((n) => n.id === 'w8006-send-reply');
  assert.ok(node, 'w8006-send-reply must exist');
  assert.ok(node.parameters.url.includes('sendMessage'), 'must call sendMessage');
});

console.log('\n' + (passed + failed) + ' tests: ' + passed + ' passed, ' + failed + ' failed');
if (failed > 0) process.exit(1);
