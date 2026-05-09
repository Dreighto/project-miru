'use strict';

// PRO-335: scanStdoutForStatus return shape changed from `string | null` to
// `{status, category, summary}`. These tests preserve the original coverage
// (CONFIRMED_WORKING detection, null cases, whitespace tolerance) updated
// to the new return shape. Additional coverage for the new patterns
// (ESCALATE, INCONCLUSIVE, FAILED) lives in scan_stdout_status.test.js.

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { scanStdoutForStatus } = require('../src/spawn');

test('detects STATUS: CONFIRMED WORKING (space)', () => {
  const stdout = 'some output\nSTATUS: CONFIRMED WORKING\ncleanup done';
  assert.equal(scanStdoutForStatus(stdout).status, 'CONFIRMED_WORKING');
});

test('detects CONFIRMED_WORKING (underscore)', () => {
  const stdout = 'log line\nCONFIRMED_WORKING\ndone';
  assert.equal(scanStdoutForStatus(stdout).status, 'CONFIRMED_WORKING');
});

test('detects case-insensitive status marker', () => {
  const stdout = 'Status: confirmed working';
  assert.equal(scanStdoutForStatus(stdout).status, 'CONFIRMED_WORKING');
});

test('null status when no canonical marker present', () => {
  // Note: post-PRO-335 a bare `INCONCLUSIVE` token without `STATUS:` prefix
  // is no longer matched — the patterns require either `STATUS:` or
  // `CONFIRMED_WORKING`/`confirmed_working` as a standalone token.
  const stdout = 'some output\nINCONCLUSIVE\ndone';
  assert.equal(scanStdoutForStatus(stdout).status, null);
});

test('null status for empty string', () => {
  assert.equal(scanStdoutForStatus('').status, null);
});

test('null status for null input', () => {
  assert.equal(scanStdoutForStatus(null).status, null);
});

test('handles STATUS: CONFIRMED_WORKING with underscore in status line', () => {
  const stdout = 'STATUS: CONFIRMED_WORKING';
  assert.equal(scanStdoutForStatus(stdout).status, 'CONFIRMED_WORKING');
});

test('handles extra whitespace between STATUS: and CONFIRMED', () => {
  const stdout = 'STATUS:  CONFIRMED WORKING';
  assert.equal(scanStdoutForStatus(stdout).status, 'CONFIRMED_WORKING');
});

test('detects lowercase standalone confirmed_working', () => {
  const stdout = 'result: confirmed_working';
  assert.equal(scanStdoutForStatus(stdout).status, 'CONFIRMED_WORKING');
});
