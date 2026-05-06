# dispatcher/data/ — legacy job history

This directory previously held the SQLite job-history database for the
decommissioned dispatcher service on port 19000. The new Local Governance
Gatekeeper (`dispatcher.gatekeeper`, shipped in PRO-302 / PR #95) has no
SQLite dependency — all dispatch state lives in `services/dispatch_listener/`
(in-memory + per-trace receipts) and the canonical append-only JSONL files
under `data/`.

## Files

| File                                         | Status            | Notes                                     |
| -------------------------------------------- | ----------------- | ----------------------------------------- |
| `jobs.db` (or `jobs.db.legacy` after rename) | Read-only archive | Old dispatcher job history. Schema below. |
| `.gitkeep`                                   | Tracked           | Preserves the directory in git.           |

`jobs.db` is gitignored (`.gitignore` line 83). The file is local-only —
the canonical record is the operator's machine, not the repo.

## Schema (inferred from pre-PRO-302 git history of task_dispatcher.py)

The `job_history` table held one row per dispatched job:

- `job_id` (TEXT PRIMARY KEY) — UUID4
- `created_at` (TEXT) — ISO 8601 UTC
- `finished_at` (TEXT or NULL)
- `prompt` (TEXT)
- `model` (TEXT) — Ollama / Claude / Gemini / Cursor / Codex / Simulation
- `effort` (TEXT) — Quick / Standard / Deep
- `handler_name` (TEXT)
- `executor_mode` (TEXT) — local / real / simulated
- `status` (TEXT) — pending / running / done / failed / cancelled / cancel_requested
- `result_text` (TEXT)
- `error_message` (TEXT or NULL)
- `input_tokens` (INTEGER or NULL)
- `output_tokens` (INTEGER or NULL)
- `estimated_cost` (REAL or NULL)
- `run_duration_ms` (INTEGER or NULL)
- `title` (TEXT or NULL) — generated via the Anthropic title-gen path
  PRO-303 flagged as a security concern; that code path is removed in
  PRO-302.

## Why archived

Per CLAUDE.md merge policy ("operator merges changes" + "do not silently
delete user data"), the old database is preserved rather than dropped.
If the operator wants to inspect past dispatch history (e.g. for the
shadow-mode bench corpus), the file is here. The `dispatcher.gatekeeper`
module does NOT read this database — it's purely for reference.

## Recommended rename

For clarity, rename `jobs.db` to `jobs.db.legacy` once any local process
holding the file handle releases it. PRO-302 attempted the rename
during PR authoring but the OS reported the file was busy
(Windows file locking). The rename is cosmetic — the file is
gitignored either way.

Command:

    mv dispatcher/data/jobs.db dispatcher/data/jobs.db.legacy

If a process is holding the file, it's likely a stale Python interpreter
or a Windows file indexer; killing the process or rebooting releases it.

## Don't

- Don't write new code that reads or writes this database. The new
  Gatekeeper has no SQLite dependency.
- Don't try to migrate the schema into a new system. The data is dispatch
  history that's no longer load-bearing for any production flow.
- Don't commit this file to git. It stays gitignored.
