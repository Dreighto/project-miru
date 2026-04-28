'use strict';
// Unit tests for the hash-set diff algorithm used in ccp002-read-and-diff.
// Run with: node tests/test_cc_completion_ping_diff.js
const crypto = require('crypto');
const assert = require('assert');

function hashRow(line) {
  return crypto.createHash('sha1').update(line.trim()).digest('hex');
}

// Pure diff logic mirroring ccp002-read-and-diff (n8n context stripped).
// rows: string[] of non-empty lines, or null to simulate ENOENT.
// pinnedHashes: string[] from static data (cc_completion_pinged_hashes).
// regressionAlerted: boolean from static data (cc_completion_regression_alerted).
// Returns { new_rows, current_count, regressed, last_count, _init?, _file_missing?,
//           _state: { pinged_hashes, regression_alerted } }
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
        pinged_hashes: [...pinnedHashes],
        regression_alerted: shouldAlert || regAlerted,
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
        pinged_hashes: rows.map(hashRow),
        regression_alerted: false,
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
        _state: { pinged_hashes: [...pinnedHashes], regression_alerted: true },
      };
    }
    return {
      new_rows: [],
      current_count: currentCount,
      regressed: true,
      last_count: pinnedSet.size,
      _state: { pinged_hashes: [...pinnedHashes], regression_alerted: true },
    };
  }

  const newRows = rows.filter((line) => !pinnedSet.has(hashRow(line)));
  return {
    new_rows: newRows,
    current_count: currentCount,
    regressed: false,
    last_count: pinnedSet.size,
    _state: {
      pinged_hashes: [...pinnedHashes, ...newRows.map(hashRow)],
      regression_alerted: false,
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

// Case 1: Empty state + empty file → empty new_rows, no _init flag.
test('Case 1: empty state + empty file', () => {
  const result = diff([], [], false);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._init, undefined);
  assert.strictEqual(result.current_count, 0);
  assert.strictEqual(result.last_count, 0);
});

// Case 2: Empty state + 5 rows → empty new_rows, all 5 hashes seeded (_init=true).
test('Case 2: empty state + 5 rows → seed all, no pings', () => {
  const rows = [row(1), row(2), row(3), row(4), row(5)];
  const result = diff(rows, [], false);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result._init, true);
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result.current_count, 5);
  assert.strictEqual(result._state.pinged_hashes.length, 5);
  const seededSet = new Set(result._state.pinged_hashes);
  for (const r of rows) {
    assert.ok(seededSet.has(hashRow(r)), `missing hash for: ${r}`);
  }
});

// Case 3: State has 5 hashes + same 5 rows → empty new_rows, no regression.
test('Case 3: state=5 hashes + same 5 rows → no new, no regression', () => {
  const rows = [row(1), row(2), row(3), row(4), row(5)];
  const hashes = rows.map(hashRow);
  const result = diff(rows, hashes, false);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._state.pinged_hashes.length, 5);
  assert.strictEqual(result._state.regression_alerted, false);
});

// Case 4: State has 5 hashes + same 5 rows + 1 new row → 1 new_row, hash added.
test('Case 4: state=5 hashes + 5+1 rows → 1 new_row, hash stored', () => {
  const rows = [row(1), row(2), row(3), row(4), row(5)];
  const hashes = rows.map(hashRow);
  const allRows = [...rows, row(6)];
  const result = diff(allRows, hashes, false);
  assert.strictEqual(result.new_rows.length, 1);
  assert.strictEqual(result.new_rows[0], row(6));
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._state.pinged_hashes.length, 6);
  assert.ok(result._state.pinged_hashes.includes(hashRow(row(6))));
});

// Case 5: State has 5 hashes + only 1 original + 4 new rows → regressed=true (20% present).
test('Case 5: state=5 hashes + 1 orig + 4 new → regressed (20% present)', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const hashes = origRows.map(hashRow);
  const currentRows = [row(1), row(6), row(7), row(8), row(9)];
  const result = diff(currentRows, hashes, false);
  assert.strictEqual(result.regressed, true);
  assert.deepStrictEqual(result.new_rows, []);
  assert.strictEqual(result._state.regression_alerted, true);
  assert.strictEqual(result.last_count, 5);
});

// Case 6: State has 5 hashes + 5 same rows + 4 new rows → 4 new_rows, no regression.
test('Case 6: state=5 hashes + 5+4 rows → 4 new_rows, no regression', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const hashes = origRows.map(hashRow);
  const allRows = [...origRows, row(6), row(7), row(8), row(9)];
  const result = diff(allRows, hashes, false);
  assert.strictEqual(result.new_rows.length, 4);
  assert.deepStrictEqual(result.new_rows, [row(6), row(7), row(8), row(9)]);
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._state.pinged_hashes.length, 9);
  assert.strictEqual(result._state.regression_alerted, false);
});

// Case 7: File missing when state has hashes → regressed=true.
test('Case 7: ENOENT + non-empty state → regressed', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const hashes = origRows.map(hashRow);
  const result = diff(null, hashes, false);
  assert.strictEqual(result.regressed, true);
  assert.strictEqual(result._file_missing, true);
  assert.strictEqual(result._state.regression_alerted, true);
  assert.strictEqual(result.last_count, 5);
  assert.deepStrictEqual(result.new_rows, []);
});

// Bonus: regression alert suppressed on second poll when already alerted.
test('Bonus: regression already alerted → regressed=false (suppressed)', () => {
  const origRows = [row(1), row(2), row(3), row(4), row(5)];
  const hashes = origRows.map(hashRow);
  const currentRows = [row(1)]; // only 20% present, still in regression
  const result = diff(currentRows, hashes, true); // regressionAlerted=true
  assert.strictEqual(result.regressed, false);
  assert.strictEqual(result._state.regression_alerted, true);
});

// Summary
console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
