'use strict';

// Tests for the worker_terminal closed-cause taxonomy (PRO-330).
// Covers computeTerminalCause (pure) and the TERMINAL_CAUSES constant.

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { computeTerminalCause, TERMINAL_CAUSES } = require('../src/spawn');

// --- TERMINAL_CAUSES constant ---

test('TERMINAL_CAUSES is a non-empty array', () => {
  assert.ok(Array.isArray(TERMINAL_CAUSES));
  assert.ok(TERMINAL_CAUSES.length > 0);
});

test('TERMINAL_CAUSES contains exactly the four expected causes', () => {
  const expected = new Set(['spawn_error', 'timeout', 'exit_clean', 'exit_nonzero']);
  assert.equal(TERMINAL_CAUSES.length, expected.size, 'no extras in TERMINAL_CAUSES');
  for (const cause of TERMINAL_CAUSES) {
    assert.ok(expected.has(cause), `unexpected cause: ${cause}`);
  }
});

test('TERMINAL_CAUSES has no duplicates', () => {
  const unique = new Set(TERMINAL_CAUSES);
  assert.equal(unique.size, TERMINAL_CAUSES.length);
});

// --- computeTerminalCause ---

test('returns timeout when timedOut is true regardless of exitCode', () => {
  assert.equal(computeTerminalCause(true, 0), 'timeout');
  assert.equal(computeTerminalCause(true, 1), 'timeout');
  assert.equal(computeTerminalCause(true, -1), 'timeout');
  assert.equal(computeTerminalCause(true, null), 'timeout');
});

test('returns exit_clean when not timed out and exitCode is 0', () => {
  assert.equal(computeTerminalCause(false, 0), 'exit_clean');
});

test('returns exit_nonzero when not timed out and exitCode is nonzero', () => {
  assert.equal(computeTerminalCause(false, 1), 'exit_nonzero');
  assert.equal(computeTerminalCause(false, 2), 'exit_nonzero');
  assert.equal(computeTerminalCause(false, 127), 'exit_nonzero');
  assert.equal(computeTerminalCause(false, -1), 'exit_nonzero');
});

test('all values returned by computeTerminalCause are in TERMINAL_CAUSES', () => {
  const causeSet = new Set(TERMINAL_CAUSES);
  const cases = [
    [true, 0],
    [true, 1],
    [false, 0],
    [false, 1],
    [false, -1],
  ];
  for (const [timedOut, exitCode] of cases) {
    const cause = computeTerminalCause(timedOut, exitCode);
    assert.ok(causeSet.has(cause), `cause ${cause} not in TERMINAL_CAUSES`);
  }
  // spawn_error is emitted directly by the error handler; verify it's in the set
  assert.ok(causeSet.has('spawn_error'), 'spawn_error must be in TERMINAL_CAUSES');
});
