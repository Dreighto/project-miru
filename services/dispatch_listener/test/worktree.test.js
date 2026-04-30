'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { leaseSlot, releaseSlot, getLeaseByTraceId, WORKTREE_SLOTS } = require('../src/worktree');

test('leaseSlot returns a valid slot path', () => {
  const slot = leaseSlot('trace-a', 'claude-code');
  assert.ok(WORKTREE_SLOTS.includes(slot), `expected a slot from WORKTREE_SLOTS, got ${slot}`);
  releaseSlot(slot);
});

test('two concurrent leases return different slots', () => {
  const slot1 = leaseSlot('trace-b1', 'claude-code');
  const slot2 = leaseSlot('trace-b2', 'claude-code');
  assert.ok(slot1, 'first lease should succeed');
  assert.ok(slot2, 'second lease should succeed');
  assert.notEqual(slot1, slot2, 'slots should be different');
  releaseSlot(slot1);
  releaseSlot(slot2);
});

test('lease beyond capacity returns null', () => {
  const leased = [];
  for (let i = 0; i < WORKTREE_SLOTS.length; i++) {
    leased.push(leaseSlot(`trace-c${i}`, 'claude-code'));
  }
  assert.ok(leased.every(Boolean), 'all slots should lease successfully');
  const overflow = leaseSlot('trace-overflow', 'claude-code');
  assert.strictEqual(overflow, null);
  for (const slot of leased) releaseSlot(slot);
});

test('releaseSlot frees a slot so a subsequent lease succeeds', () => {
  const slot1 = leaseSlot('trace-d1', 'claude-code');
  const slot2 = leaseSlot('trace-d2', 'claude-code');
  releaseSlot(slot1);
  const slot3 = leaseSlot('trace-d3', 'claude-code');
  assert.ok(slot3, 'lease after release should succeed');
  releaseSlot(slot2);
  releaseSlot(slot3);
});

test('getLeaseByTraceId returns the leased slot path', () => {
  const slot = leaseSlot('trace-e', 'claude-code');
  const found = getLeaseByTraceId('trace-e');
  assert.strictEqual(found, slot);
  releaseSlot(slot);
});

test('getLeaseByTraceId returns null for unknown trace', () => {
  const result = getLeaseByTraceId('no-such-trace-xyz');
  assert.strictEqual(result, null);
});

test('getLeaseByTraceId returns null after slot is released', () => {
  const slot = leaseSlot('trace-f', 'claude-code');
  releaseSlot(slot);
  const result = getLeaseByTraceId('trace-f');
  assert.strictEqual(result, null);
});
