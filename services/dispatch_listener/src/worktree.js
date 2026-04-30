'use strict';

const WORKTREE_SLOTS = [
  process.env.WORKTREE_SLOT_1 || 'D:\\dev\\miru-w1',
  process.env.WORKTREE_SLOT_2 || 'D:\\dev\\miru-w2',
  process.env.WORKTREE_SLOT_3 || 'D:\\dev\\miru-w3',
  process.env.WORKTREE_SLOT_4 || 'D:\\dev\\miru-w4',
  process.env.WORKTREE_SLOT_5 || 'D:\\dev\\miru-w5',
  process.env.WORKTREE_SLOT_6 || 'D:\\dev\\miru-w6',
];

// slotPath → { traceId, worker, leasedAt }
const leases = new Map();

function leaseSlot(traceId, worker) {
  for (const slot of WORKTREE_SLOTS) {
    if (!leases.has(slot)) {
      leases.set(slot, { traceId, worker, leasedAt: new Date().toISOString() });
      return slot;
    }
  }
  return null;
}

function releaseSlot(slotPath) {
  leases.delete(slotPath);
}

function getLeaseByTraceId(traceId) {
  for (const [slotPath, lease] of leases) {
    if (lease.traceId === traceId) return slotPath;
  }
  return null;
}

module.exports = { leaseSlot, releaseSlot, getLeaseByTraceId, WORKTREE_SLOTS };
