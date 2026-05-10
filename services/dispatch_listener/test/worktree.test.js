'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const {
  leaseSlot,
  releaseSlot,
  getLeaseByTraceId,
  listKnownRepos,
  WORKTREE_SLOTS,
  WORKTREE_POOLS,
  DEFAULT_TARGET_REPO,
  _leases,
} = require('../src/worktree');

// Helper: clear the in-memory lease map between tests so prior test state
// can't poison subsequent tests. Required because the module loads leases
// from disk on import; tests share that singleton state.
function _clearAllLeases() {
  for (const slot of WORKTREE_SLOTS) _leases.delete(slot);
}

// ----------------------------------------------------------------------------
// Default pool (project-miru) — backward compat with pre-2026-05-09 callers
// ----------------------------------------------------------------------------

test('leaseSlot returns a valid slot path (default pool)', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-a', 'claude-code');
  assert.ok(WORKTREE_SLOTS.includes(slot), `expected a slot from WORKTREE_SLOTS, got ${slot}`);
  assert.ok(
    WORKTREE_POOLS[DEFAULT_TARGET_REPO].includes(slot),
    'default-pool lease should land in the default pool'
  );
  releaseSlot(slot);
});

test('two concurrent leases return different slots (default pool)', () => {
  _clearAllLeases();
  const slot1 = leaseSlot('trace-b1', 'claude-code');
  const slot2 = leaseSlot('trace-b2', 'claude-code');
  assert.ok(slot1, 'first lease should succeed');
  assert.ok(slot2, 'second lease should succeed');
  assert.notEqual(slot1, slot2, 'slots should be different');
  releaseSlot(slot1);
  releaseSlot(slot2);
});

test('lease beyond default-pool capacity returns null', () => {
  _clearAllLeases();
  const defaultPoolSize = WORKTREE_POOLS[DEFAULT_TARGET_REPO].length;
  const leased = [];
  for (let i = 0; i < defaultPoolSize; i++) {
    leased.push(leaseSlot(`trace-c${i}`, 'claude-code'));
  }
  assert.ok(
    leased.every(Boolean),
    `all ${defaultPoolSize} default-pool slots should lease successfully`
  );
  const overflow = leaseSlot('trace-overflow', 'claude-code');
  assert.strictEqual(overflow, null, 'overflow lease in default pool should return null');
  for (const slot of leased) releaseSlot(slot);
});

test('releaseSlot frees a slot so a subsequent lease succeeds', () => {
  _clearAllLeases();
  const slot1 = leaseSlot('trace-d1', 'claude-code');
  const slot2 = leaseSlot('trace-d2', 'claude-code');
  releaseSlot(slot1);
  const slot3 = leaseSlot('trace-d3', 'claude-code');
  assert.ok(slot3, 'lease after release should succeed');
  releaseSlot(slot2);
  releaseSlot(slot3);
});

test('getLeaseByTraceId returns the leased slot path', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-e', 'claude-code');
  const found = getLeaseByTraceId('trace-e');
  assert.strictEqual(found, slot);
  releaseSlot(slot);
});

test('getLeaseByTraceId returns null for unknown trace', () => {
  _clearAllLeases();
  const result = getLeaseByTraceId('no-such-trace-xyz');
  assert.strictEqual(result, null);
});

test('getLeaseByTraceId returns null after slot is released', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-f', 'claude-code');
  releaseSlot(slot);
  const result = getLeaseByTraceId('trace-f');
  assert.strictEqual(result, null);
});

// ----------------------------------------------------------------------------
// Multi-repo pool support (added 2026-05-09 for LOS team)
// ----------------------------------------------------------------------------

test('leaseSlot with explicit project-miru target_repo lands in default pool', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-g', 'claude-code', 'project-miru');
  assert.ok(slot, 'explicit project-miru lease should succeed');
  assert.ok(
    WORKTREE_POOLS['project-miru'].includes(slot),
    'explicit project-miru lease should land in project-miru pool'
  );
  releaseSlot(slot);
});

test('leaseSlot with LogueOS-Console target_repo lands in LogueOS-Console pool', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-h', 'gemini', 'LogueOS-Console');
  assert.ok(slot, 'LogueOS-Console lease should succeed');
  assert.ok(
    WORKTREE_POOLS['LogueOS-Console'].includes(slot),
    'LogueOS-Console lease should land in LogueOS-Console pool'
  );
  assert.ok(
    !WORKTREE_POOLS['project-miru'].includes(slot),
    'LogueOS-Console lease should NOT land in project-miru pool'
  );
  releaseSlot(slot);
});

test('leaseSlot with unknown target_repo returns null', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-i', 'claude-code', 'NoSuchRepo');
  assert.strictEqual(slot, null, 'unknown target_repo should return null');
});

test('default-pool exhaustion does not block LogueOS-Console pool', () => {
  // Filling the project-miru pool should NOT prevent leasing from LogueOS-Console.
  // This is the core multi-repo isolation property.
  _clearAllLeases();
  const miruLeases = [];
  const miruPoolSize = WORKTREE_POOLS['project-miru'].length;
  for (let i = 0; i < miruPoolSize; i++) {
    miruLeases.push(leaseSlot(`trace-fill-${i}`, 'claude-code', 'project-miru'));
  }
  assert.ok(miruLeases.every(Boolean), 'all project-miru slots should lease');

  const losSlot = leaseSlot('trace-los-isolated', 'gemini', 'LogueOS-Console');
  assert.ok(losSlot, 'LogueOS-Console lease should still succeed when project-miru pool is full');

  for (const slot of miruLeases) releaseSlot(slot);
  releaseSlot(losSlot);
});

test('LogueOS-Console exhaustion does not block default pool', () => {
  // Inverse: filling LogueOS-Console pool should NOT prevent project-miru leases.
  _clearAllLeases();
  const losLeases = [];
  const losPoolSize = WORKTREE_POOLS['LogueOS-Console'].length;
  for (let i = 0; i < losPoolSize; i++) {
    losLeases.push(leaseSlot(`trace-los-fill-${i}`, 'gemini', 'LogueOS-Console'));
  }
  assert.ok(losLeases.every(Boolean), 'all LogueOS-Console slots should lease');

  const miruSlot = leaseSlot('trace-miru-isolated', 'claude-code', 'project-miru');
  assert.ok(miruSlot, 'project-miru lease should still succeed when LogueOS-Console pool is full');

  for (const slot of losLeases) releaseSlot(slot);
  releaseSlot(miruSlot);
});

test('listKnownRepos returns all configured pool keys', () => {
  const repos = listKnownRepos();
  assert.ok(repos.includes('project-miru'), 'should include project-miru');
  assert.ok(repos.includes('LogueOS-Console'), 'should include LogueOS-Console');
});

test('lease entry persists target_repo for downstream observability', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-j', 'gemini', 'LogueOS-Console');
  const entry = _leases.get(slot);
  assert.strictEqual(entry.target_repo, 'LogueOS-Console');
  releaseSlot(slot);
});

test('lease entry defaults target_repo to project-miru when omitted', () => {
  _clearAllLeases();
  const slot = leaseSlot('trace-k', 'claude-code');
  const entry = _leases.get(slot);
  assert.strictEqual(entry.target_repo, 'project-miru');
  releaseSlot(slot);
});
