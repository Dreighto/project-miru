-- PRO-? worker_profile memory-layer migration.
-- Idempotent: safe to re-run against data/miru_memory.db.
--
-- This migration adds editable worker capability canon beside the append-only
-- worker_perf event log. Routing reads must use current_worker_profiles.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS worker_profile (
  id                              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  created_at                      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  updated_at                      TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  worker_key                      TEXT NOT NULL,
  worker_name                     TEXT NOT NULL,
  worker_kind                     TEXT NOT NULL CHECK(worker_kind IN ('agent','cli_agent','ide_agent','backup_agent')),
  routing_status                  TEXT NOT NULL DEFAULT 'probation' CHECK(routing_status IN ('active','probation','backup','manual_only','disabled')),
  loop_integration_status         TEXT NOT NULL DEFAULT 'untested' CHECK(loop_integration_status IN ('proven','experimental','manual_only','untested','disabled')),
  dispatch_surface                TEXT NOT NULL,
  tool_call_reliability           TEXT NOT NULL DEFAULT 'unknown' CHECK(tool_call_reliability IN ('high','medium','low','none','unknown')),
  tool_call_reliability_basis     TEXT,
  reliability_tier                TEXT NOT NULL DEFAULT 'probation' CHECK(reliability_tier IN ('trusted','standard','probation','experimental','disabled')),
  cost_class                      TEXT NOT NULL DEFAULT 'unknown' CHECK(cost_class IN ('plan_included','per_token','per_request','mixed','unknown')),
  cost_notes                      TEXT,
  task_type_strengths             TEXT NOT NULL DEFAULT '[]',
  tech_strengths                  TEXT NOT NULL DEFAULT '[]',
  weaknesses                      TEXT NOT NULL DEFAULT '[]',
  anti_ticket_patterns            TEXT NOT NULL DEFAULT '[]',
  failure_mode_notes              TEXT NOT NULL DEFAULT '[]',
  preferred_handoff_format        TEXT,
  environment_compatibility       TEXT NOT NULL DEFAULT '{}',
  operator_confidence             INTEGER NOT NULL DEFAULT 50 CHECK(operator_confidence BETWEEN 0 AND 100),
  max_parallel_tasks              INTEGER NOT NULL DEFAULT 1 CHECK(max_parallel_tasks >= 1),
  operator_owned_review_required  INTEGER NOT NULL DEFAULT 1 CHECK(operator_owned_review_required IN (0,1)),
  operator_owned_review_triggers  TEXT NOT NULL DEFAULT '[]',
  last_confirmed_at               TEXT,
  profile_source                  TEXT,
  reviewed_by                     TEXT,
  routing_notes                   TEXT,
  supersedes                      TEXT REFERENCES worker_profile(id) DEFERRABLE INITIALLY DEFERRED,
  meta                            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_profile_current_worker_key
  ON worker_profile(worker_key)
  WHERE supersedes IS NULL;

CREATE INDEX IF NOT EXISTS idx_worker_profile_supersedes
  ON worker_profile(supersedes);

CREATE INDEX IF NOT EXISTS idx_worker_profile_routing_status
  ON worker_profile(routing_status, loop_integration_status, reliability_tier);

DROP VIEW IF EXISTS current_worker_profiles;

CREATE VIEW current_worker_profiles AS
SELECT
  id,
  created_at,
  updated_at,
  worker_key,
  worker_name,
  worker_kind,
  routing_status,
  loop_integration_status,
  dispatch_surface,
  tool_call_reliability,
  tool_call_reliability_basis,
  reliability_tier,
  cost_class,
  cost_notes,
  task_type_strengths,
  tech_strengths,
  weaknesses,
  anti_ticket_patterns,
  failure_mode_notes,
  preferred_handoff_format,
  environment_compatibility,
  operator_confidence,
  max_parallel_tasks,
  operator_owned_review_required,
  operator_owned_review_triggers,
  last_confirmed_at,
  profile_source,
  reviewed_by,
  routing_notes,
  meta
FROM worker_profile
WHERE supersedes IS NULL;
