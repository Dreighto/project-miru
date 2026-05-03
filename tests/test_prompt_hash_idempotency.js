'use strict';
// Unit tests for findInFlightByPromptHash (Ticket B3 in
// miru-context/loop-hardening-backlog.md).
//
// Verifies:
//   * Returns null when the inbox is empty.
//   * Returns null when no receipt matches the prompt_hash.
//   * Returns the existing trace_id when a 'spawned' receipt with matching
//     prompt_hash is within the time window.
//   * Returns null when the matching receipt is older than the window.
//   * Returns null when the matching receipt has terminated (status != 'spawned').
//
// Run: node tests/test_prompt_hash_idempotency.js

const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');

const {
  findInFlightByPromptHash,
  writePlaceholderReceipt,
} = require('../services/dispatch_listener/src/receipt');

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

function makeInbox() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'miru-prompthash-'));
}

function rmInbox(p) {
  try {
    fs.rmSync(p, { recursive: true, force: true });
  } catch (_e) {
    // ignore
  }
}

function writeReceipt(inboxDir, traceId, fields) {
  const target = path.join(inboxDir, `${traceId}.result.json`);
  const body = {
    schema_version: 'v1',
    trace_id: traceId,
    worker: fields.worker || 'claude-code',
    status: fields.status || 'spawned',
    summary: '',
    files_touched: [],
    exit_code: fields.exit_code != null ? fields.exit_code : null,
    stderr_tail: '',
    started_at: fields.started_at,
    completed_at: fields.completed_at != null ? fields.completed_at : null,
    prompt_hash: fields.prompt_hash || null,
  };
  fs.writeFileSync(target, JSON.stringify(body, null, 2), 'utf8');
}

// ─── Test 1: empty inbox ────────────────────────────────────────────────────
test('empty inbox returns null', () => {
  const dir = makeInbox();
  try {
    const result = findInFlightByPromptHash(dir, 'abc123', 600);
    assert.strictEqual(result, null);
  } finally {
    rmInbox(dir);
  }
});

// ─── Test 2: no matching prompt_hash ────────────────────────────────────────
test('non-matching hash returns null', () => {
  const dir = makeInbox();
  try {
    writeReceipt(dir, 'trace-001', {
      prompt_hash: 'abc123',
      started_at: new Date().toISOString(),
    });
    const result = findInFlightByPromptHash(dir, 'different', 600);
    assert.strictEqual(result, null);
  } finally {
    rmInbox(dir);
  }
});

// ─── Test 3: in-flight matching hash within window ─────────────────────────
test('matching in-flight hash within window returns trace_id', () => {
  const dir = makeInbox();
  try {
    writeReceipt(dir, 'trace-original', {
      prompt_hash: 'aaaaaaaaaaaaaaaa',
      started_at: new Date().toISOString(),
    });
    const result = findInFlightByPromptHash(dir, 'aaaaaaaaaaaaaaaa', 600);
    assert.strictEqual(result, 'trace-original');
  } finally {
    rmInbox(dir);
  }
});

// ─── Test 4: matching hash but outside window ──────────────────────────────
test('matching hash outside window returns null', () => {
  const dir = makeInbox();
  try {
    const oldTs = new Date(Date.now() - 700 * 1000).toISOString();
    writeReceipt(dir, 'trace-old', {
      prompt_hash: 'bbbbbbbbbbbbbbbb',
      started_at: oldTs,
    });
    const result = findInFlightByPromptHash(dir, 'bbbbbbbbbbbbbbbb', 600);
    assert.strictEqual(result, null);
  } finally {
    rmInbox(dir);
  }
});

// ─── Test 5: terminated receipt (status != 'spawned') ─────────────────────
test('matching hash but terminated status returns null', () => {
  const dir = makeInbox();
  try {
    writeReceipt(dir, 'trace-done', {
      prompt_hash: 'cccccccccccccccc',
      status: 'CONFIRMED_WORKING',
      started_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      exit_code: 0,
    });
    const result = findInFlightByPromptHash(dir, 'cccccccccccccccc', 600);
    assert.strictEqual(result, null);
  } finally {
    rmInbox(dir);
  }
});

// ─── Test 6: writePlaceholderReceipt with promptHash field ────────────────
test('writePlaceholderReceipt persists promptHash in receipt', () => {
  const dir = makeInbox();
  try {
    writePlaceholderReceipt({
      inboxDir: dir,
      traceId: 'trace-with-hash',
      worker: 'claude-code',
      startedAt: new Date().toISOString(),
      promptHash: 'dddddddddddddddd',
    });
    const onDisk = JSON.parse(
      fs.readFileSync(path.join(dir, 'trace-with-hash.result.json'), 'utf8')
    );
    assert.strictEqual(onDisk.prompt_hash, 'dddddddddddddddd');
    // Round-trip via findInFlightByPromptHash.
    const result = findInFlightByPromptHash(dir, 'dddddddddddddddd', 600);
    assert.strictEqual(result, 'trace-with-hash');
  } finally {
    rmInbox(dir);
  }
});

// ─── Test 7: missing prompt_hash field on legacy receipt ───────────────────
test('legacy receipt without prompt_hash field is ignored', () => {
  const dir = makeInbox();
  try {
    // Simulate a pre-B3 receipt that has no prompt_hash field at all.
    fs.writeFileSync(
      path.join(dir, 'legacy-trace.result.json'),
      JSON.stringify({
        schema_version: 'v1',
        trace_id: 'legacy-trace',
        worker: 'claude-code',
        status: 'spawned',
        started_at: new Date().toISOString(),
      }),
      'utf8'
    );
    const result = findInFlightByPromptHash(dir, 'eeeeeeeeeeeeeeee', 600);
    assert.strictEqual(result, null, 'legacy receipt without hash must not match anything');
  } finally {
    rmInbox(dir);
  }
});

console.log(`\n${testsPassed}/${testsRun} tests passed`);
process.exit(testsPassed === testsRun ? 0 : 1);
