'use strict';

// PRO-335: tests for scanStdoutForStatus pattern recognition + diagnostic
// block capture. Pre-PRO-335 the function only recognized CONFIRMED_WORKING
// and returned a bare string; everything else collapsed to empty INCONCLUSIVE
// with no summary.

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { scanStdoutForStatus } = require('../src/spawn');

test('CONFIRMED_WORKING with explicit STATUS: prefix', () => {
  const out = 'Some output\nSTATUS: CONFIRMED WORKING\nDone.';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'CONFIRMED_WORKING');
  assert.equal(r.category, null);
  assert.match(r.summary, /CONFIRMED WORKING/);
  assert.match(r.summary, /Done\./);
});

test('CONFIRMED_WORKING with underscore form', () => {
  const out = 'STATUS: CONFIRMED_WORKING\n5/5 tests pass';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'CONFIRMED_WORKING');
  assert.match(r.summary, /5\/5 tests pass/);
});

test('CONFIRMED_WORKING bare token (no STATUS prefix) — fallback', () => {
  const out = 'Worker says everything CONFIRMED_WORKING and ready';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'CONFIRMED_WORKING');
});

test('ESCALATE: HUMAN-REQUIRED with diagnostic block', () => {
  const out =
    'Pre-flight gate 2 failed.\n\n**STATUS: ESCALATE: HUMAN-REQUIRED**\n\n' +
    'Dirty files on branch foo:\n- file1\n- file2\n\nTo unblock: git stash';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'INCONCLUSIVE'); // ESCALATE maps to INCONCLUSIVE
  assert.equal(r.category, 'HUMAN-REQUIRED');
  assert.match(r.summary, /Dirty files on branch foo/);
  assert.match(r.summary, /To unblock: git stash/);
});

test('ESCALATE: SCOPE_EXPANSION captures category', () => {
  const out = 'STATUS: ESCALATE: SCOPE_EXPANSION\nThe ticket has grown beyond scope.';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'INCONCLUSIVE');
  assert.equal(r.category, 'SCOPE_EXPANSION');
  assert.match(r.summary, /grown beyond scope/);
});

test('ESCALATE: DESIGN_CHANGE captures category', () => {
  const out = 'STATUS: ESCALATE: DESIGN_CHANGE\nThe spec assumption is wrong.';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'INCONCLUSIVE');
  assert.equal(r.category, 'DESIGN_CHANGE');
});

test('explicit STATUS: INCONCLUSIVE with summary', () => {
  const out =
    'Tried three rounds of fixes; CodeRabbit still finds new issues.\n\n' +
    'STATUS: INCONCLUSIVE\n\nRemaining: 2 minor nit findings about test naming.';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'INCONCLUSIVE');
  assert.equal(r.category, null);
  assert.match(r.summary, /Remaining: 2 minor nit/);
});

test('explicit STATUS: FAILED with reason', () => {
  const out = 'STATUS: FAILED\n\nReason: tests are red — 3/12 fail after change.';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'FAILED');
  assert.match(r.summary, /tests are red/);
});

test('multiple status lines — first match wins', () => {
  // CONFIRMED comes first → wins. (priority order in pattern list.)
  const out = 'STATUS: CONFIRMED WORKING\n... but also STATUS: INCONCLUSIVE later';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'CONFIRMED_WORKING');
});

test('empty stdout returns null status + empty summary', () => {
  const r = scanStdoutForStatus('');
  assert.equal(r.status, null);
  assert.equal(r.category, null);
  assert.equal(r.summary, '');
});

test('null stdout returns null status', () => {
  const r = scanStdoutForStatus(null);
  assert.equal(r.status, null);
});

test('stdout with no status line returns null', () => {
  const r = scanStdoutForStatus('Just some normal log lines\nNothing canonical here.');
  assert.equal(r.status, null);
  assert.equal(r.summary, '');
});

test('summary is capped at 4096 chars + truncated marker', () => {
  // Build a long stdout: status line + lots of diagnostic
  const status = 'STATUS: ESCALATE: HUMAN-REQUIRED\n';
  const longBody = 'x'.repeat(10000);
  const r = scanStdoutForStatus(status + longBody);
  assert.equal(r.status, 'INCONCLUSIVE');
  assert.ok(
    r.summary.length <= 4096 + '\n... [truncated]'.length,
    `summary length ${r.summary.length} exceeds cap`
  );
  assert.match(r.summary, /\[truncated\]/);
});

test('case-insensitive STATUS prefix', () => {
  const r = scanStdoutForStatus('status: confirmed working');
  assert.equal(r.status, 'CONFIRMED_WORKING');
});

test('STATUS line with extra whitespace', () => {
  const out = 'STATUS:    ESCALATE:    HUMAN-REQUIRED\nSome detail.';
  const r = scanStdoutForStatus(out);
  assert.equal(r.status, 'INCONCLUSIVE');
  assert.equal(r.category, 'HUMAN-REQUIRED');
});
