'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { scanStdoutForStatus } = require('../src/spawn');

test('detects STATUS: CONFIRMED WORKING (space)', () => {
  const stdout = 'some output\nSTATUS: CONFIRMED WORKING\ncleanup done';
  assert.equal(scanStdoutForStatus(stdout), 'CONFIRMED_WORKING');
});

test('detects CONFIRMED_WORKING (underscore)', () => {
  const stdout = 'log line\nCONFIRMED_WORKING\ndone';
  assert.equal(scanStdoutForStatus(stdout), 'CONFIRMED_WORKING');
});

test('detects case-insensitive status marker', () => {
  const stdout = 'Status: confirmed working';
  assert.equal(scanStdoutForStatus(stdout), 'CONFIRMED_WORKING');
});

test('returns null when no marker present', () => {
  const stdout = 'some output\nINCONCLUSIVE\ndone';
  assert.equal(scanStdoutForStatus(stdout), null);
});

test('returns null for empty string', () => {
  assert.equal(scanStdoutForStatus(''), null);
});

test('returns null for null input', () => {
  assert.equal(scanStdoutForStatus(null), null);
});

test('handles STATUS: CONFIRMED_WORKING with underscore in status line', () => {
  const stdout = 'STATUS: CONFIRMED_WORKING';
  assert.equal(scanStdoutForStatus(stdout), 'CONFIRMED_WORKING');
});

test('handles extra whitespace between STATUS: and CONFIRMED', () => {
  const stdout = 'STATUS:  CONFIRMED WORKING';
  assert.equal(scanStdoutForStatus(stdout), 'CONFIRMED_WORKING');
});
