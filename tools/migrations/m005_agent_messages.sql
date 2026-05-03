-- PRO-290 Phase 1: A2A Bus migration.
-- Idempotent: safe to re-run against data/miru_memory.db.
--
-- Adds the agent_messages table for asynchronous agent-to-agent (A2A)
-- communication with explicit claim ownership semantics. Required for
-- Suspend-Consult-Resume cycles in later phases of the autonomy overhaul.
--
-- Design notes:
--   * status enforced via CHECK constraint. The seven values cover the
--     full lifecycle: pending -> claimed -> {responded | expired | failed}
--     plus operator-driven cancelled/superseded transitions.
--   * expires_at semantics are STATE-DEPENDENT:
--       - status=pending : timestamp at which the message itself expires
--                          if never claimed (sweeper marks 'expired').
--       - status=claimed : timestamp at which the CLAIM expires if the
--                          claimer doesn't respond. Sweeper requeues
--                          (status -> pending, with fresh window) if
--                          attempt_count < retry_limit, else 'failed'.
--       - other states   : informational only; sweeper does not act.
--   * Partial indexes optimise the two hot-path queries: (a) find next
--     pending message addressed to me, (b) find expired claims to sweep.
--   * response_to_id supports threading: a 'consult_response' carries
--     response_to_id pointing at the original 'consult_request'.
--   * journal_mode=WAL is sticky on the .db file; setting it here is a
--     one-time effective change. Subsequent migrations can rely on it.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS agent_messages (
  id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  trace_id        TEXT,
  ticket_id       TEXT,
  from_agent      TEXT NOT NULL,
  to_agent        TEXT NOT NULL,
  message_type    TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','claimed','responded','expired','cancelled','superseded','failed')),
  payload_json    TEXT NOT NULL,
  priority        INTEGER NOT NULL DEFAULT 5 CHECK(priority BETWEEN 0 AND 10),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  expires_at      TEXT,
  response_to_id  TEXT REFERENCES agent_messages(id) DEFERRABLE INITIALLY DEFERRED,
  claimed_by      TEXT,
  claim_token     TEXT,
  claimed_at      TEXT,
  responded_at    TEXT,
  attempt_count   INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
  last_error      TEXT
);

-- Hot path 1: find next pending message addressed to a given agent,
-- highest priority first then oldest first.
CREATE INDEX IF NOT EXISTS idx_agent_messages_pending
  ON agent_messages(to_agent, priority DESC, created_at)
  WHERE status = 'pending';

-- Hot path 2: sweeper finds claimed messages whose claim has expired.
CREATE INDEX IF NOT EXISTS idx_agent_messages_claimed_expiry
  ON agent_messages(expires_at)
  WHERE status = 'claimed';

-- Hot path 3: sweeper finds pending messages whose message TTL expired.
CREATE INDEX IF NOT EXISTS idx_agent_messages_pending_expiry
  ON agent_messages(expires_at)
  WHERE status = 'pending' AND expires_at IS NOT NULL;

-- Trace correlation across dispatch boundary.
CREATE INDEX IF NOT EXISTS idx_agent_messages_trace
  ON agent_messages(trace_id)
  WHERE trace_id IS NOT NULL;

-- Ticket correlation.
CREATE INDEX IF NOT EXISTS idx_agent_messages_ticket
  ON agent_messages(ticket_id)
  WHERE ticket_id IS NOT NULL;

-- Response threading lookup.
CREATE INDEX IF NOT EXISTS idx_agent_messages_response_to
  ON agent_messages(response_to_id)
  WHERE response_to_id IS NOT NULL;
