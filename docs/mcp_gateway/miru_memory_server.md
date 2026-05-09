# `miru_memory` MCP server config (PRO-156)

Memory-layer SQLite server entry for `.mcp.json`. The actual `.mcp.json` is gitignored
per the worktree-private convention — this document captures the canonical shape so
the operator can propagate it to `D:\dev\miru-config\.mcp.json` and any worker
worktree symlinks pick it up.

## Entry to add to `.mcp.json` under `mcpServers`

```jsonc
"miru_memory": {
  "type": "stdio",
  "command": "powershell.exe",
  "args": [
    "-NoLogo",
    "-NoProfile",
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    "D:\\dev\\miru\\tools\\launch_miru_memory_mcp.ps1"
  ],
  "env": {}
}
```

## Notes

- **Package:** `mcp-server-sqlite` (Anthropic-published Python reference server, installed
  via `uvx`). The PRO-156 ticket originally referenced `@modelcontextprotocol/server-sqlite`
  on npm, which returns 404 — the Python `mcp-server-sqlite` is the correct package and
  matches the same shape as the existing `fetch` server entry (`uvx mcp-server-fetch`).
- **Launcher wrapper (`tools/launch_miru_memory_mcp.ps1`).** Earlier versions of this
  entry ran `uvx` directly. After repeated cold-boot failures (PATH propagation lag and
  orphan SQLite WAL/SHM sidecars left by dirty shutdowns), the entry now invokes a
  PowerShell launcher that resolves `uvx.exe` via `Get-Command` and checkpoints any
  orphan WAL/SHM files before launching the server. Same pattern as `notion`, `youtube`,
  and `magic-ui` entries.
- **`--readonly` flag is intentionally omitted.** Write access is the default; the
  flag would disable writes only if explicitly set. PRO-156 acceptance requires write
  access (smoke tests #3 and #5 are INSERT and DELETE).
- **DB path is absolute Windows path.** This config is for the host-side Claude Code
  session; the gateway is not Docker-containerized in this project.
- **Tools exposed by the server:** `list_tables`, `read_query`, `write_query`,
  `create_table`, `describe_table`, `append_insight`. These are the surface PRO-156's
  smoke tests exercise.

## Smoke test verification (run end-to-end during PRO-156 build)

All 5 tests pass through this server config:

1. `list_tables` → 6 tables returned: `routing_decisions`, `agenda`, `decisions`,
   `worker_perf`, `stack_state`, `peer_review`.
2. `read_query SELECT COUNT(*) FROM routing_decisions` → 142 rows (matches Step Zero
   JSONL import: 120 routing_history + 22 cc_completion_log).
3. `write_query INSERT INTO stack_state (key, value) VALUES ('memory_layer_smoke_test', 'ok')`
   → `affected_rows: 1`.
4. `read_query SELECT * FROM stack_state WHERE key = 'memory_layer_smoke_test'` →
   row returned with auto-populated `updated_at`.
5. `write_query DELETE FROM stack_state WHERE key = 'memory_layer_smoke_test'` →
   `affected_rows: 1`; cleanup confirmed.

## Reproducibility

If a worker checks out this repo fresh:

1. Run `python tools/miru_memory_import.py --bootstrap` to create `data/miru_memory.db`
   with the canonical schema (embedded in the import script's `SCHEMA_SQL` constant),
   WAL mode enabled, and the legacy JSONL sources imported into `routing_decisions`.
   The script is idempotent — safe to re-run; `IF NOT EXISTS` guards on every table
   prevent schema collisions.
2. Add the `miru_memory` entry above to the worker's `.mcp.json` (or rely on the
   canonical at `D:\dev\miru-config\.mcp.json` if symlinked).
3. Restart the Claude Code session so it picks up the new MCP server.

## Hard rule reconciliation

CLAUDE.md says "Claude Code must never modify .mcp.json or any MCP config files."
PRO-156 explicitly authorized this modification per the per-task authorization pattern
(operator directive overrides durable rule for the scope of the ticket). This is
documented in the PR description and the completion marker `notes` field.
