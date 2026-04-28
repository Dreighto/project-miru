# worker_profile schema

`worker_profile` is editable canon for worker routing. It sits beside
`worker_perf`, which remains the append-only outcome log. Routing reads must use
`current_worker_profiles`, not the raw table, so retired profile rows cannot be
selected by accident.

Copilot is intentionally absent. It is an inline helper, not a routable worker.

## Migration

Apply the idempotent migration in:

```text
tools/migrations/m004_worker_profile.sql
```

The same DDL is included in `tools/miru_memory_import.py` so `--bootstrap`
creates the seventh memory table on fresh databases.

## CREATE TABLE

```sql
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
```

## CREATE VIEW

```sql
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
```

## Example INSERT: claude-code

This is a reviewed seed example, not part of the migration. It is idempotent so
an operator can paste it safely after approving the contents.

```sql
INSERT INTO worker_profile (
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
)
SELECT
  'claude-code',
  'Claude Code',
  'cli_agent',
  'active',
  'proven',
  'dispatcher/handlers/claude.py -> Claude Code CLI --print --dangerously-skip-permissions',
  'high',
  'Rated for the local Claude Code CLI dispatch path, not Claude Chat web UI. Miru has already shipped multiple execution tickets through this worker.',
  'trusted',
  'plan_included',
  'Routine use is covered by the current operator plan; do not use per-ticket cost as a penalty unless the plan changes.',
  '["default code executor","multi-file implementation","SQLite migration work","test and verification loops","Windows repo operations","MCP gateway changes after fresh-session verification"]',
  '["Python","SQLite","PowerShell","Node subprocess wrappers","MCP gateway code","Linear-driven task execution"]',
  '["slower than inline helpers by design","can overrun simple UI-only polish","needs fresh-session verification for MCP surface changes","should not be rewarded for speed when thorough execution is the desired tradeoff"]',
  '["pure CSS or visual-only iteration with no backend risk","single-function inline autocomplete work","tasks requiring live IDE interaction as the main value"]',
  '["may report local success before a separate Claude Chat session can see a new MCP tool","headless CLI can hide interactive approval needs unless the bridge catches the prompt","large scope can produce broad edits that need tight review"]',
  '{"prompt":"Linear ticket with goal, acceptance criteria, files in scope, constraints, and verification command.","return":"Summary, files changed, tests run, risks, and any operator decision needed."}',
  '{"windows":"proven with cmd-wrapped claude.cmd and Git Bash path support","docker":"can edit Docker and n8n files from host; not containerized","tailscale":"compatible; no special dependency"}',
  90,
  1,
  1,
  '[".env or secret handling",".mcp.json or MCP config","n8n workflow JSON","security boundaries","new database migrations","new files until operator relaxes the gate"]',
  strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
  'operator+claude_chat+codex_schema_pass',
  'operator',
  'Default fallback for code execution while other workers remain less proven through the dispatch loop.',
  '{"copilot_policy":"excluded from worker_profile because it is not routable","profile_version":"example-v1"}'
WHERE NOT EXISTS (
  SELECT 1 FROM current_worker_profiles WHERE worker_key = 'claude-code'
);
```

## JSON parse failure handling

The JSON-shaped columns are stored as `TEXT` for SQLite simplicity and later
Postgres migration. Claude Chat or any router that reads the view should parse:

- `task_type_strengths`
- `tech_strengths`
- `weaknesses`
- `anti_ticket_patterns`
- `failure_mode_notes`
- `preferred_handoff_format`
- `environment_compatibility`
- `operator_owned_review_triggers`
- `meta`

If parsing fails, routing must not crash. Treat that field as empty, add the
column name and parse error to the routing rationale, downrank the profile for
that decision, and write the parse failure into `routing_decisions.meta` if a
decision is logged. The operator should then repair the profile row through a
reviewed canon flip.

## Canon Flip Discipline

Rows with `supersedes IS NULL` are current. To replace a profile, create the
replacement row with a known id and retire the old row by setting the old row's
`supersedes` to the replacement id in the same transaction. The partial unique
index allows only one current row per `worker_key`.

```sql
BEGIN;

UPDATE worker_profile
SET
  supersedes = :replacement_id,
  updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
WHERE id = :old_id;

INSERT INTO worker_profile (
  id,
  worker_key,
  worker_name,
  worker_kind,
  dispatch_surface
)
VALUES (
  :replacement_id,
  :worker_key,
  :worker_name,
  :worker_kind,
  :dispatch_surface
);

COMMIT;
```

## Codex Tool-Call Reliability Decision

Codex is rated only for the local API-backed CLI path:
`dispatcher/handlers/codex.py -> codex exec --full-auto` with `OPENAI_API_KEY`.
Do not mix this with ChatGPT web UI behavior. Initial profile rows should use
`tool_call_reliability='medium'`, `loop_integration_status='experimental'`, and
`reliability_tier='probation'` until Miru has enough successful dispatch-loop
outcomes to promote it.

## Field Guide

| Field                            | Stores                                                                     | Written by                            | Read by                            |
| -------------------------------- | -------------------------------------------------------------------------- | ------------------------------------- | ---------------------------------- |
| `id`                             | UUID-like text primary key.                                                | SQLite default or operator migration. | Audits, canon flips.               |
| `created_at`                     | Row creation timestamp.                                                    | SQLite default.                       | Audits, staleness checks.          |
| `updated_at`                     | Last manual edit or retirement timestamp.                                  | Operator or migration.                | Audits.                            |
| `worker_key`                     | Stable slug, such as `claude-code` or `codex`.                             | Operator.                             | Router, logs.                      |
| `worker_name`                    | Human label.                                                               | Operator.                             | Approval prompts.                  |
| `worker_kind`                    | Broad class: CLI, IDE, generic agent, backup.                              | Operator.                             | Router pre-filter.                 |
| `routing_status`                 | Whether the worker is active, backup, probation, manual-only, or disabled. | Operator.                             | Router pre-filter.                 |
| `loop_integration_status`        | How proven the worker is in Miru dispatch.                                 | Operator after reviews.               | Router and approval rationale.     |
| `dispatch_surface`               | Exact invocation path being rated.                                         | Operator or implementer.              | Router and worker dispatcher.      |
| `tool_call_reliability`          | Coarse trust rating for tool/MCP contract following on the named surface.  | Operator after review.                | Router scoring.                    |
| `tool_call_reliability_basis`    | Why that rating is assigned.                                               | Operator or reviewer.                 | Approval rationale.                |
| `reliability_tier`               | Overall operational trust.                                                 | Operator.                             | Router scoring and fallback logic. |
| `cost_class`                     | Cost shape: plan-included, per-token, per-request, mixed, unknown.         | Operator.                             | Router tiebreakers.                |
| `cost_notes`                     | Human cost caveats.                                                        | Operator.                             | Approval rationale.                |
| `task_type_strengths`            | JSON array of task categories the worker should get.                       | Operator.                             | Router scoring.                    |
| `tech_strengths`                 | JSON array of technologies or surfaces.                                    | Operator.                             | Router scoring.                    |
| `weaknesses`                     | JSON array of known limits.                                                | Operator.                             | Router scoring.                    |
| `anti_ticket_patterns`           | JSON array of cheap rejection patterns.                                    | Operator.                             | Router pre-filter.                 |
| `failure_mode_notes`             | JSON array describing how the worker fails.                                | Operator after reviews.               | Router, approval rationale.        |
| `preferred_handoff_format`       | JSON object or text describing best prompt and return shape.               | Operator.                             | Claude Chat prompt builder.        |
| `environment_compatibility`      | JSON object for Windows, Docker, Tailscale, and local constraints.         | Operator or implementer.              | Router pre-filter and dispatcher.  |
| `operator_confidence`            | 0-100 subjective confidence, separate from measured perf.                  | Operator.                             | Router scoring and tiebreakers.    |
| `max_parallel_tasks`             | Current concurrency limit. Defaults to 1 until parallel dispatch is real.  | Operator.                             | Future dispatcher.                 |
| `operator_owned_review_required` | Boolean gate for operator-owned review before merge/apply.                 | Operator.                             | Router, approval, merge policy.    |
| `operator_owned_review_triggers` | JSON array of patterns that force review even for capable workers.         | Operator.                             | Router, approval, merge policy.    |
| `last_confirmed_at`              | Last time the profile was reviewed as true.                                | Operator.                             | Staleness checks.                  |
| `profile_source`                 | Source of the profile contents.                                            | Operator or migration.                | Audits.                            |
| `reviewed_by`                    | Reviewer identity, usually `operator`.                                     | Operator.                             | Audits.                            |
| `routing_notes`                  | Short free-text guidance.                                                  | Operator.                             | Approval rationale.                |
| `supersedes`                     | Replacement row id when this row is retired; `NULL` means current.         | Operator during canon flip.           | `current_worker_profiles`.         |
| `meta`                           | JSON object for low-frequency extras.                                      | Operator.                             | Audits and future tools.           |
