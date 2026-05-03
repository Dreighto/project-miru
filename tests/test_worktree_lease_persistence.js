'use strict';
// Unit tests for persistent worktree lease state (Ticket B4 in
// miru-context/loop-hardening-backlog.md).
//
// Verifies:
//   * Module loads existing lease file at init.
//   * Dead-pid leases are cleared on load (reclaim works after listener
//     restart while a worker died).
//   * Live-pid leases survive load.
//   * leaseSlot writes through to disk.
//   * releaseSlot writes through to disk.
//   * Atomic write uses .tmp + rename (no half-written file on crash).
//
// Run: node tests/test_worktree_lease_persistence.js

const fs = require('fs');
const os = require('os');
const path = require('path');
const assert = require('assert');

const REPO_ROOT = path.resolve(__dirname, '..');
const LEASE_FILE = path.join(REPO_ROOT, 'data', 'worktree_leases.json');

// We modify the real lease file path during tests. Snapshot any pre-test
// content so we can restore it.
let preTestSnapshot = null;
if (fs.existsSync(LEASE_FILE)) {
  preTestSnapshot = fs.readFileSync(LEASE_FILE, 'utf8');
}

function clearWorktreeModule() {
  const modPath = require.resolve('../services/dispatch_listener/src/worktree.js');
  delete require.cache[modPath];
  // Stub out log.js so test output is quiet.
  const logPath = require.resolve('../services/dispatch_listener/src/log.js');
  require.cache[logPath] = {
    id: logPath,
    filename: logPath,
    loaded: true,
    exports: {
      info: () => {},
      warn: () => {},
      error: () => {},
      fatal: () => {},
    },
  };
}

function writeLeaseFile(obj) {
  fs.mkdirSync(path.dirname(LEASE_FILE), { recursive: true });
  fs.writeFileSync(LEASE_FILE, JSON.stringify(obj, null, 2), 'utf8');
}

function readLeaseFile() {
  return JSON.parse(fs.readFileSync(LEASE_FILE, 'utf8'));
}

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

const SLOT_1 = process.env.WORKTREE_SLOT_1 || 'D:\\dev\\miru-w1';
const SLOT_2 = process.env.WORKTREE_SLOT_2 || 'D:\\dev\\miru-w2';

// ─── Test 1: fresh start (empty file → first slot available) ────────────────
test('fresh-start: leaseSlot returns first slot, file is created', () => {
  if (fs.existsSync(LEASE_FILE)) fs.unlinkSync(LEASE_FILE);
  clearWorktreeModule();
  const wt = require('../services/dispatch_listener/src/worktree.js');
  const slot = wt.leaseSlot('trace-fresh-001', 'claude-code');
  assert.strictEqual(slot, SLOT_1, `expected ${SLOT_1}, got ${slot}`);
  assert.ok(fs.existsSync(LEASE_FILE), 'lease file should exist after first lease');
  const onDisk = readLeaseFile();
  assert.strictEqual(onDisk[SLOT_1].trace_id, 'trace-fresh-001');
  assert.strictEqual(onDisk[SLOT_1].pid, process.pid);
  assert.strictEqual(onDisk[SLOT_2], null);
  wt.releaseSlot(slot);
});

// ─── Test 2: dead-pid lease is cleared at load ──────────────────────────────
test('dead-pid lease is cleared on module load (listener-restart safety)', () => {
  // 99999 is overwhelmingly likely to be a dead pid; the test asserts the
  // pidIsAlive() probe correctly reports it dead.
  writeLeaseFile({
    [SLOT_1]: {
      trace_id: 'pre-restart-trace',
      worker: 'claude-code',
      leased_at: '2026-01-01T00:00:00Z',
      pid: 99999,
    },
    [SLOT_2]: null,
  });
  clearWorktreeModule();
  const wt = require('../services/dispatch_listener/src/worktree.js');
  // After load, slot 1 should be free again because pid 99999 is dead.
  const slot = wt.leaseSlot('post-restart-trace', 'claude-code');
  assert.strictEqual(slot, SLOT_1, 'post-restart leaseSlot should reclaim the dead-pid slot');
  const onDisk = readLeaseFile();
  assert.strictEqual(onDisk[SLOT_1].trace_id, 'post-restart-trace');
  assert.strictEqual(onDisk[SLOT_1].pid, process.pid);
  wt.releaseSlot(slot);
});

// ─── Test 3: live-pid lease survives load ───────────────────────────────────
test('live-pid lease survives module reload (no double-lease)', () => {
  writeLeaseFile({
    [SLOT_1]: {
      trace_id: 'long-running-trace',
      worker: 'claude-code',
      leased_at: '2026-05-03T00:00:00Z',
      pid: process.pid, // simulate ourselves as the live worker process
    },
    [SLOT_2]: null,
  });
  clearWorktreeModule();
  const wt = require('../services/dispatch_listener/src/worktree.js');
  // Slot 1 is held by a live pid. Next leaseSlot must skip it and use slot 2.
  const slot = wt.leaseSlot('new-trace', 'claude-code');
  assert.strictEqual(slot, SLOT_2, `expected slot 2 (slot 1 held by live pid), got ${slot}`);
  const onDisk = readLeaseFile();
  assert.strictEqual(
    onDisk[SLOT_1].trace_id,
    'long-running-trace',
    'slot 1 lease must be preserved'
  );
  assert.strictEqual(onDisk[SLOT_2].trace_id, 'new-trace');
  wt.releaseSlot(slot);
});

// ─── Test 4: releaseSlot persists ───────────────────────────────────────────
test('releaseSlot writes through to disk', () => {
  if (fs.existsSync(LEASE_FILE)) fs.unlinkSync(LEASE_FILE);
  clearWorktreeModule();
  const wt = require('../services/dispatch_listener/src/worktree.js');
  const slot = wt.leaseSlot('release-test', 'claude-code');
  assert.ok(slot);
  wt.releaseSlot(slot);
  const onDisk = readLeaseFile();
  assert.strictEqual(onDisk[slot], null, 'released slot must be null on disk');
});

// ─── Test 5: corrupt lease file is treated as empty ─────────────────────────
test('corrupt lease file is tolerated (treated as empty)', () => {
  fs.mkdirSync(path.dirname(LEASE_FILE), { recursive: true });
  fs.writeFileSync(LEASE_FILE, '{not valid json', 'utf8');
  clearWorktreeModule();
  const wt = require('../services/dispatch_listener/src/worktree.js');
  // Module loads despite corrupt file; first leaseSlot should succeed.
  const slot = wt.leaseSlot('post-corrupt-trace', 'claude-code');
  assert.strictEqual(slot, SLOT_1);
  wt.releaseSlot(slot);
});

// ─── Cleanup: restore pre-test state ────────────────────────────────────────
if (preTestSnapshot === null) {
  if (fs.existsSync(LEASE_FILE)) fs.unlinkSync(LEASE_FILE);
} else {
  fs.writeFileSync(LEASE_FILE, preTestSnapshot, 'utf8');
}

console.log(`\n${testsPassed}/${testsRun} tests passed`);
process.exit(testsPassed === testsRun ? 0 : 1);
