'use strict';

// Tests for PRO-319: worktree lease PID tracking fix.
// Run with: node tests/test_worktree_lease_pid.js

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const os = require('os');

// Set env vars BEFORE require — the module reads them at load time.
const TMP_SLOT = fs.mkdtempSync(path.join(os.tmpdir(), 'miru-wt-test-'));
process.env.WORKTREE_SLOT_1 = TMP_SLOT;
// Short TTL so stale-null-pid test doesn't require a 30-min wait.
process.env.WORKTREE_STALE_NULL_PID_MS = '1000';

const REPO_ROOT = path.resolve(__dirname, '..');
const LEASE_FILE = path.join(REPO_ROOT, 'data', 'worktree_leases.json');

const {
  leaseSlot,
  updateLeasePid,
  _loadFromDisk,
  _leases,
  WORKTREE_SLOTS,
  STALE_NULL_PID_MS,
} = require('../services/dispatch_listener/src/worktree');

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

// ---- Test 1: leaseSlot() stores pid: null, not process.pid ----

test('leaseSlot() sets pid to null, not process.pid', () => {
  _leases.clear();
  const slot = leaseSlot('trace-aaa', 'claude');
  assert.strictEqual(slot, TMP_SLOT, 'should return the first available slot');
  const entry = _leases.get(slot);
  assert.ok(entry, 'lease entry must exist in the in-memory map');
  assert.strictEqual(entry.pid, null, 'pid must be null at lease time — worker not spawned yet');
  assert.notStrictEqual(
    entry.pid,
    process.pid,
    'pid must not be the listener PID (PRO-319 regression guard)'
  );
  assert.strictEqual(entry.trace_id, 'trace-aaa');
  _leases.clear();
});

// ---- Test 2: updateLeasePid() sets actual child PID and flushes to disk ----

test('updateLeasePid() writes child pid to in-memory lease and flushes', () => {
  _leases.clear();
  const slot = leaseSlot('trace-bbb', 'claude');
  assert.strictEqual(_leases.get(slot).pid, null, 'pid must be null before update');

  const fakePid = 88888;
  updateLeasePid(slot, fakePid);

  assert.strictEqual(_leases.get(slot).pid, fakePid, 'in-memory pid should be updated');

  // Verify the flush: the JSON on disk must also have the updated pid.
  const written = JSON.parse(fs.readFileSync(LEASE_FILE, 'utf8'));
  assert.strictEqual(
    written[slot].pid,
    fakePid,
    'flushed JSON must contain the child pid, not null'
  );
  _leases.clear();
});

// ---- Test 3: _loadFromDisk() clears stale pid:null leases older than TTL ----

test('_loadFromDisk() clears stale pid:null leases and keeps recent ones', () => {
  _leases.clear();

  const staleTs = new Date(Date.now() - (STALE_NULL_PID_MS + 5000)).toISOString();
  const recentTs = new Date().toISOString();

  const obj = {};
  for (const s of WORKTREE_SLOTS) obj[s] = null;
  // Slot 0 (TMP_SLOT): stale null-pid → must be cleared.
  obj[WORKTREE_SLOTS[0]] = {
    trace_id: 'stale-trace',
    worker: 'claude',
    leased_at: staleTs,
    pid: null,
  };
  // Slot 1: recent null-pid → must be kept (spawn may still be in progress).
  if (WORKTREE_SLOTS[1]) {
    obj[WORKTREE_SLOTS[1]] = {
      trace_id: 'recent-trace',
      worker: 'claude',
      leased_at: recentTs,
      pid: null,
    };
  }

  fs.mkdirSync(path.dirname(LEASE_FILE), { recursive: true });
  const savedExists = fs.existsSync(LEASE_FILE);
  const saved = savedExists ? fs.readFileSync(LEASE_FILE) : null;
  fs.writeFileSync(LEASE_FILE, JSON.stringify(obj), 'utf8');

  try {
    _leases.clear();
    _loadFromDisk();

    assert.strictEqual(
      _leases.has(WORKTREE_SLOTS[0]),
      false,
      'stale null-pid lease (older than TTL) should be cleared on disk load'
    );

    if (WORKTREE_SLOTS[1]) {
      assert.strictEqual(
        _leases.has(WORKTREE_SLOTS[1]),
        true,
        'recent null-pid lease should survive disk load (spawn may still be in progress)'
      );
      assert.strictEqual(
        _leases.get(WORKTREE_SLOTS[1]).pid,
        null,
        'recent lease pid should still be null after load'
      );
    }
  } finally {
    if (savedExists) {
      fs.writeFileSync(LEASE_FILE, saved);
    } else {
      try {
        fs.unlinkSync(LEASE_FILE);
      } catch (_e) {
        // best effort
      }
    }
    _leases.clear();
    try {
      fs.rmSync(TMP_SLOT, { recursive: true, force: true });
    } catch (_e) {
      // best effort
    }
  }
});

// ---- Summary ----

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
