'use strict';

const fs = require('fs');
const path = require('path');

const log = require('./log');

const WORKTREE_SLOTS = [
  process.env.WORKTREE_SLOT_1 || 'D:\\dev\\miru-w1',
  process.env.WORKTREE_SLOT_2 || 'D:\\dev\\miru-w2',
  process.env.WORKTREE_SLOT_3 || 'D:\\dev\\miru-w3',
  process.env.WORKTREE_SLOT_4 || 'D:\\dev\\miru-w4',
  process.env.WORKTREE_SLOT_5 || 'D:\\dev\\miru-w5',
  process.env.WORKTREE_SLOT_6 || 'D:\\dev\\miru-w6',
];

// Persistent state file: PR for ticket B4. Without this, listener restart
// loses every lease and a new dispatch can re-claim a slot still in use by
// a pre-restart worker.
//
// Format: { [slotPath]: null | { trace_id, worker, leased_at, pid } }
//
// The in-memory `leases` Map below is a write-through cache. Every mutation
// flushes to disk; every read at startup loads from disk and clears
// dead-pid entries.
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');
const LEASE_FILE = path.join(REPO_ROOT, 'data', 'worktree_leases.json');
const LEASE_TMP = LEASE_FILE + '.tmp';

const leases = new Map();

function pidIsAlive(pid) {
  if (!pid || typeof pid !== 'number') return false;
  try {
    // signal 0 doesn't kill — it just throws if the process is gone.
    process.kill(pid, 0);
    return true;
  } catch (_e) {
    return false;
  }
}

function _loadFromDisk() {
  let parsed = {};
  try {
    if (fs.existsSync(LEASE_FILE)) {
      parsed = JSON.parse(fs.readFileSync(LEASE_FILE, 'utf8')) || {};
    }
  } catch (err) {
    log.warn('worktree_lease_parse_failed', { error: err.message });
    parsed = {};
  }
  let cleared = 0;
  for (const slot of WORKTREE_SLOTS) {
    const entry = parsed[slot];
    if (entry && entry.pid && pidIsAlive(entry.pid)) {
      leases.set(slot, entry);
    } else if (entry) {
      cleared += 1;
      log.info('worktree_lease_cleared_dead_pid', {
        slot,
        trace_id: entry.trace_id || null,
        pid: entry.pid || null,
      });
    }
  }
  log.info('worktree_leases_loaded', {
    file: LEASE_FILE,
    active: leases.size,
    cleared_dead: cleared,
  });
}

function _flushToDisk() {
  const obj = {};
  for (const slot of WORKTREE_SLOTS) {
    obj[slot] = leases.get(slot) || null;
  }
  try {
    fs.mkdirSync(path.dirname(LEASE_FILE), { recursive: true });
    fs.writeFileSync(LEASE_TMP, JSON.stringify(obj, null, 2), 'utf8');
    fs.renameSync(LEASE_TMP, LEASE_FILE);
  } catch (err) {
    log.error('worktree_lease_write_failed', { error: err.message });
    // Don't throw — disk failure shouldn't take the listener down. The
    // in-memory lease map still works for the lifetime of this process.
  }
}

function leaseSlot(traceId, worker) {
  for (const slot of WORKTREE_SLOTS) {
    const existing = leases.get(slot);
    if (!existing) {
      leases.set(slot, {
        trace_id: traceId,
        worker,
        leased_at: new Date().toISOString(),
        pid: process.pid,
      });
      _flushToDisk();
      return slot;
    }
    // Defensive: if the cache somehow holds an entry whose pid is gone,
    // reclaim. This shouldn't happen because spawnWorker calls releaseSlot
    // on every exit/error, but listener crashes between those points are
    // exactly the failure mode this ticket addresses.
    if (existing.pid && !pidIsAlive(existing.pid)) {
      log.info('worktree_lease_reclaimed_dead', {
        slot,
        prev_trace_id: existing.trace_id,
        prev_pid: existing.pid,
      });
      leases.set(slot, {
        trace_id: traceId,
        worker,
        leased_at: new Date().toISOString(),
        pid: process.pid,
      });
      _flushToDisk();
      return slot;
    }
  }
  return null;
}

function releaseSlot(slotPath) {
  if (leases.delete(slotPath)) {
    _flushToDisk();
  }
}

function getLeaseByTraceId(traceId) {
  for (const [slotPath, lease] of leases) {
    if (lease.trace_id === traceId) return slotPath;
  }
  return null;
}

// Load any persisted leases on module init. Listener startup happens before
// any dispatch is accepted, so this is the right place to clear dead-pid
// entries and rebuild the in-memory cache.
_loadFromDisk();

module.exports = {
  leaseSlot,
  releaseSlot,
  getLeaseByTraceId,
  WORKTREE_SLOTS,
  // Exposed for tests only:
  _loadFromDisk,
  _flushToDisk,
  _leases: leases,
};
