-- PRO-311: Cross-worker dependency bus.
-- Idempotent: safe to re-run against data/miru_memory.db.
--
-- Adds the task_dependencies table for inter-ticket dependency tracking.
-- Workers register dependencies before starting work and check upstream
-- status. Upstream workers mark themselves ready (with optional artifact
-- payload) so downstream workers know when to proceed.
--
-- Lifecycle: pending -> ready | failed | cancelled
--   * pending = upstream has not completed yet
--   * ready   = upstream completed and (optionally) published an artifact
--   * failed  = upstream failed; downstream should not proceed
--   * cancelled = dependency removed (e.g. scope change)

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS task_dependencies (
  id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  ticket_id       TEXT NOT NULL,
  depends_on      TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending', 'ready', 'failed', 'cancelled')),
  artifact_json   TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  resolved_at     TEXT,
  trace_id        TEXT,
  notes           TEXT,
  UNIQUE(ticket_id, depends_on)
);

-- Hot path: worker checks all deps for its ticket.
CREATE INDEX IF NOT EXISTS idx_task_deps_ticket
  ON task_dependencies(ticket_id, status);

-- Hot path: upstream marks itself ready — find all rows depending on it.
CREATE INDEX IF NOT EXISTS idx_task_deps_depends_on
  ON task_dependencies(depends_on)
  WHERE status = 'pending';

-- Trace correlation.
CREATE INDEX IF NOT EXISTS idx_task_deps_trace
  ON task_dependencies(trace_id)
  WHERE trace_id IS NOT NULL;
