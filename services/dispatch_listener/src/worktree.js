'use strict';

const fs = require('fs');
const path = require('path');

const log = require('./log');

// Leases older than this with pid: null are stale — the listener crashed or
// the spawn never completed. Configurable so tests can inject a short TTL.
const STALE_NULL_PID_MS = Number(process.env.WORKTREE_STALE_NULL_PID_MS) || 30 * 60 * 1000;

// Multi-repo worktree pool (added 2026-05-09 for LOS team). Each entry maps a
// `target_repo` string to its ordered list of worktree slot paths. Backward
// compat: callers that don't pass target_repo land on 'project-miru' which uses
// the same WORKTREE_SLOT_1..6 env vars and default paths as before.
//
// To add a new repo's pool:
//   1. Add an entry below: `'<repo>': [ process.env.<REPO>_WORKTREE_SLOT_1 || '<path>', ... ]`
//   2. Init the worktree(s) on disk parked on `_parking_<repo>-w<N>` from main
//   3. Restart the dispatch_listener
//
// New pools should always default to at least one slot. Single-slot pools are
// fine for low-volume repos; expand as load demands.
const DEFAULT_TARGET_REPO = 'project-miru';

const WORKTREE_POOLS = {
  'project-miru': [
    process.env.WORKTREE_SLOT_1 || 'D:\\dev\\miru-w1',
    process.env.WORKTREE_SLOT_2 || 'D:\\dev\\miru-w2',
    process.env.WORKTREE_SLOT_3 || 'D:\\dev\\miru-w3',
    process.env.WORKTREE_SLOT_4 || 'D:\\dev\\miru-w4',
    process.env.WORKTREE_SLOT_5 || 'D:\\dev\\miru-w5',
    process.env.WORKTREE_SLOT_6 || 'D:\\dev\\miru-w6',
  ],
  'LogueOS-Console': [process.env.LOGUEOS_CONSOLE_WORKTREE_SLOT_1 || 'D:\\dev\\LogueOS-Console-w1'],
};

// Flat list of every slot across every pool. Used for lease persistence
// (load/flush) and for the legacy WORKTREE_SLOTS export so callers that just
// want "any slot for housekeeping" still work.
const WORKTREE_SLOTS = Object.values(WORKTREE_POOLS).flat();

// Reverse index: slot path → target_repo. Lets us recover the repo identity
// from a lease entry that pre-dates target_repo persistence (legacy format).
const SLOT_TO_REPO = {};
for (const [repo, slots] of Object.entries(WORKTREE_POOLS)) {
  for (const slot of slots) {
    SLOT_TO_REPO[slot] = repo;
  }
}

// Persistent state file: PR for ticket B4. Without this, listener restart
// loses every lease and a new dispatch can re-claim a slot still in use by
// a pre-restart worker.
//
// Format: { [slotPath]: null | { trace_id, worker, target_repo, leased_at, pid } }
//
// `target_repo` was added 2026-05-09. Pre-existing entries without it are
// treated as project-miru (the only repo before the change).
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
  const now = Date.now();
  for (const slot of WORKTREE_SLOTS) {
    const entry = parsed[slot];
    if (!entry) continue;

    // Backfill target_repo for pre-2026-05-09 lease entries.
    if (!entry.target_repo) {
      entry.target_repo = SLOT_TO_REPO[slot] || DEFAULT_TARGET_REPO;
    }

    if (entry.pid === null) {
      // Lease was written before the spawn completed. Keep it if recent — the
      // process may still be starting. Clear it if older than STALE_NULL_PID_MS.
      const leasedAt = entry.leased_at ? new Date(entry.leased_at).getTime() : 0;
      if (now - leasedAt > STALE_NULL_PID_MS) {
        cleared += 1;
        log.info('worktree_lease_cleared_stale_null_pid', {
          slot,
          trace_id: entry.trace_id || null,
          target_repo: entry.target_repo,
          leased_at: entry.leased_at || null,
        });
      } else {
        leases.set(slot, entry);
      }
    } else if (pidIsAlive(entry.pid)) {
      leases.set(slot, entry);
    } else {
      cleared += 1;
      log.info('worktree_lease_cleared_dead_pid', {
        slot,
        trace_id: entry.trace_id || null,
        target_repo: entry.target_repo,
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

function getPoolSlots(targetRepo) {
  const slots = WORKTREE_POOLS[targetRepo];
  if (!slots) return null;
  return slots;
}

// leaseSlot picks the next free slot from the pool for the given target_repo.
// Returns the absolute path of the leased slot, or null if all slots in that
// repo's pool are occupied. `targetRepo` defaults to 'project-miru' for
// backward compatibility with pre-2026-05-09 callers.
function leaseSlot(traceId, worker, targetRepo) {
  const repo = targetRepo || DEFAULT_TARGET_REPO;
  const pool = getPoolSlots(repo);
  if (!pool) {
    log.warn('worktree_lease_unknown_target_repo', {
      trace_id: traceId,
      worker,
      target_repo: repo,
      known_repos: Object.keys(WORKTREE_POOLS),
    });
    return null;
  }
  for (const slot of pool) {
    const existing = leases.get(slot);
    if (!existing) {
      leases.set(slot, {
        trace_id: traceId,
        worker,
        target_repo: repo,
        leased_at: new Date().toISOString(),
        pid: null,
      });
      _flushToDisk();
      return slot;
    }
    // Reclaim if: (a) pid is set but the process is dead, or
    // (b) pid is null and the lease is older than STALE_NULL_PID_MS
    //     (spawn crashed before updateLeasePid could fire).
    const reclaimable =
      (existing.pid && !pidIsAlive(existing.pid)) ||
      (existing.pid === null &&
        Date.now() - new Date(existing.leased_at || 0).getTime() > STALE_NULL_PID_MS);

    if (reclaimable) {
      log.info('worktree_lease_reclaimed', {
        slot,
        prev_trace_id: existing.trace_id,
        prev_pid: existing.pid,
        prev_target_repo: existing.target_repo || DEFAULT_TARGET_REPO,
        reason: existing.pid ? 'dead_pid' : 'stale_null_pid',
      });
      leases.set(slot, {
        trace_id: traceId,
        worker,
        target_repo: repo,
        leased_at: new Date().toISOString(),
        pid: null,
      });
      _flushToDisk();
      return slot;
    }
  }
  return null;
}

// Update the in-memory lease and flush to disk once the child PID is known.
// Called immediately after spawnWorker returns so pidIsAlive checks use the
// worker's PID, not the listener's.
function updateLeasePid(slotPath, pid) {
  const entry = leases.get(slotPath);
  if (!entry) {
    log.warn('worktree_update_pid_no_lease', { slot: slotPath, pid });
    return;
  }
  entry.pid = pid;
  _flushToDisk();
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

// Returns the list of repo names (pool keys) currently configured.
// Used by the HTTP handler to validate target_repo on incoming dispatches.
function listKnownRepos() {
  return Object.keys(WORKTREE_POOLS);
}

// Load any persisted leases on module init. Listener startup happens before
// any dispatch is accepted, so this is the right place to clear dead-pid
// entries and rebuild the in-memory cache.
_loadFromDisk();

module.exports = {
  leaseSlot,
  updateLeasePid,
  releaseSlot,
  getLeaseByTraceId,
  listKnownRepos,
  WORKTREE_SLOTS,
  WORKTREE_POOLS,
  DEFAULT_TARGET_REPO,
  STALE_NULL_PID_MS,
  // Exposed for tests only:
  _loadFromDisk,
  _flushToDisk,
  _leases: leases,
};
