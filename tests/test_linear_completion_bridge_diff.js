'use strict';
// Unit tests for the hash-set diff algorithm used in wlcb002-read-and-diff
// (Linear Completion Bridge, PRO-196). Mirrors test_cc_completion_ping_diff.js
// (PRO-160) but with the bridge's independent state-key namespace
// (lc_pinged_hashes / lc_regression_alerted).
//
// This is the clean-algorithm test. The boundary-crossing test that loads the
// actual workflow JSON and evals the embedded jsCode lives in
// test_linear_completion_bridge_workflow_eval.js (per PRO-189 lesson).
//
// Run with: node tests/test_linear_completion_bridge_diff.js
const crypto = require('crypto');
const assert = require('assert');

function hashRow(line) {
  return crypto.createHash('sha1').update(line.trim()).digest('hex');
}

// Pure diff logic mirroring wlcb002-read-and-diff (n8n context stripped).
// Same algorithm as PRO-160's ccp002, with bridge-specific state keys for clarity.
function diff(rows, pinnedHashes, regressionAlerted) {
  const pinnedSet = new Set(pinnedHashes);
  const regAlerted = regressionAlerted === true;

  if (rows === null) {
    const shouldAlert = pinnedSet.size > 0 && !regAlerted;
    return {
      new_rows: [],
      current_count: 0,
      regressed: shouldAlert,
      last_count: pinnedSet.size,
      _file_missing: true,
      _state: {
        lc_pinged_hashes: [...pinnedHashes],
        lc_regression_alerted: shouldAlert || regAlerted,
      },
    };
  }

  const currentCount = rows.length;

  if (pinnedSet.size === 0 && currentCount > 0) {
    return {
      new_rows: [],
      current_count: currentCount,
      regressed: false,
      last_count: 0,
      _init: true,
      _state: {
        lc_pinged_hashes: rows.map(hashRow),
        lc_regression_alerted: false,
      },
    };
  }

  const currentHashSet = new Set(rows.map(hashRow));
  let presentCount = 0;
  for (const h of pinnedSet) {
    if (currentHashSet.has(h)) presentCount++;
  }
  const presentRatio = pinnedSet.size > 0 ? presentCount / pinnedSet.size : 1;
  const isRegressed = pinnedSet.size > 0 && presentRatio < 0.5;

  if (isRegressed) {
    if (regAlerted) {
      return {
        new_rows: [],
        current_count: currentCount,
        regressed: false,
        last_count: pinnedSet.size,
        _state: { lc_pinged_hashes: [...pinnedHashes], lc_regression_alerted: true },
      };
    }
    return {
      new_rows: [],
      current_count: currentCount,
      regressed: true,
      last_count: pinnedSet.size,
      _state: { lc_pinged_hashes: [...pinnedHashes], lc_regression_alerted: true },
    };
  }

  const newRows = rows.filter((line) => !pinnedSet.has(hashRow(line)));
  return {
    new_rows: newRows,
    current_count: currentCount,
    regressed: false,
    last_count: pinnedSet.size,
    _state: {
      lc_pinged_hashes: [...pinnedHashes, ...newRows.map(hashRow)],
      lc_regression_alerted: false,
    },
  };
}

// ---- helpers ----

const row = (n) => `{"ticket_id":"PRO-${n}","status":"CONFIRMED_WORKING","summary":"test ${n}"}`;

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

// ---- test cases ----

test('Case 1: empty state + empty file', () => {
  const result = diff([], [], false);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._init, undefined);
});

test('Case 2: empty state + 5 rows → seed all under lc_pinged_hashes, no pings', () => {
  const rows = [row(1), row(2), row(3), row(4), row(5)];
  const result = diff(rows, [], false);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result._init, true);
  assert.strictEqual(result._state.lc_pinged_hashes.length, 5);
});

test('Case 3: state=5 hashes + same 5 rows → no new, no regression', () => {
  const rows = [row(1), row(2), row(3), row(4), row(5)];
  const result = diff(rows, rows.map(hashRow), false);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result.regressed, false);
});

test('Case 4: state=5 + 5+1 rows → 1 new_row, hash stored in lc_pinged_hashes', () => {
  const rows = [row(1), row(2), row(3), row(4), row(5)];
  const allRows = [...rows, row(6)];
  const result = diff(allRows, rows.map(hashRow), false);
  assert.strictEqual(result.new_rows.length, 1);
  assert.strictEqual(result.new_rows[0], row(6));
  assert.strictEqual(result._state.lc_pinged_hashes.length, 6);
});

test('Case 5: state=5 + 1 orig + 4 new → regressed (20% present)', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const currentRows = [row(1), row(6), row(7), row(8), row(9)];
  const result = diff(currentRows, origRows.map(hashRow), false);
  assert.strictEqual(result.regressed, true);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result._state.lc_regression_alerted, true);
});

test('Case 6: state=5 + 5+4 rows → 4 new_rows, no regression', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const allRows = [...origRows, row(6), row(7), row(8), row(9)];
  const result = diff(allRows, origRows.map(hashRow), false);
  assert.strictEqual(result.new_rows.length, 4);
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._state.lc_pinged_hashes.length, 9);
});

test('Case 7: ENOENT + non-empty state → regressed under lc_regression_alerted', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const result = diff(null, origRows.map(hashRow), false);
  assert.strictEqual(result.regressed, true);
  assert.strictEqual(result._file_missing, true);
  assert.strictEqual(result._state.lc_regression_alerted, true);
});

test('Case 8: regression already alerted → suppressed', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const result = diff([row(1)], origRows.map(hashRow), true);
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._state.lc_regression_alerted, true);
});

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
