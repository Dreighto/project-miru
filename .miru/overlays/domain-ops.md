# Overlay — domain-ops

```text
Overlay: domain-ops
Architecture: MIRU-INSTRUCTIONS-v2
Load when: touching scheduled tasks, services, Notion (read or write), or MCP tool config.
Last reviewed: 2026-05-09
```

This overlay carries the rules for Windows operations, MCP tool usage, and
Notion read/write authority. Load it before changing scheduled tasks,
adding/removing MCP servers, or writing to Notion.

---

## Notion — Read/Write Rules

- ALL workers may READ Notion to understand the current job, active tasks, and system state
- **Claude Code (VP Ops) is the acting default Notion writer while CH is offline** (2026-05-07 onward). When CH returns, default-writer authority returns to CH for architectural decisions, new page structure, consultant packet content, and cross-session synthesis. CC retains standing write authority for the maintenance categories below regardless.
- **Claude Code standing write authority** — no per-task operator authorization required:
  - Post-ticket canon updates after verifying completed work (factual corrections, tool lists, port/service status)
  - Worker Operating Baseline syncs when CLAUDE.md or AGENTS.md changes
  - Work Log anchor entries after a sprint
  - Reference/spec pages (e.g. ROOM hardware spec, schema references)
  - Any Notion update where CC already holds the full context from a just-verified ticket
- All other workers may write to Notion only when the operator explicitly authorizes a specific task
- Use Notion reads to avoid overlapping with in-progress work

---

## MCP Tool Usage Rules

- Use MCP tools when they genuinely help the task
- Always use sequential-thinking MCP for complex multi-step tasks before executing — think first
- Always use sqlite-ro-snapshot MCP to read card data before writing any intelligence pipeline code
- Use perplexity MCP for research tasks only
- Use notion MCP to read current job state
- Use git MCP to check what files are currently changed before starting work
- Never use a tool just because it is available — only use it if it helps this specific task
- Never write to the database through any MCP tool
- `git_commit_and_push` (PRO-187) is for Claude Chat / orchestrator-scoped commits only. It may commit allowlisted canon/docs/skills files after hygiene, but must not be used for worker code changes, workflow JSON, DB files, append-only JSONL files, force-push, branch creation, rebase, reset, merge, cherry-pick, amend, or `--no-verify`.

---

## Scheduled Tasks — Hard Rule (no focus stealing)

Any new Windows scheduled task or background service that runs periodically or at startup **MUST be completely non-interactive**. The operator works on this machine and any window that appears or steals focus is unacceptable.

**Mandatory approach (in order of preference):**

1. **Run as SYSTEM** (`/RU "SYSTEM" /RP ""` via schtasks, or `LogonType: ServiceAccount` in Task Scheduler). Session 0 is physically isolated from the user desktop — no windows, no focus stealing possible. Use this for any task that only needs network access, file I/O, or Python scripts. Use the `data/config/python_path.txt` mechanism for Python tasks (written by the FIX_TASK_SESSIONS_RUN_AS_ADMIN.bat setup script).

2. **VBS wrapper with SW_HIDE** if SYSTEM is not viable (e.g. task needs a user-mounted drive like G:\, or a WinGet/NVM tool installed in user profile). Use `WshShell.Run "...", 0, False` — this properly sets `STARTF_USESHOWWINDOW | SW_HIDE` at process creation. VBS wrappers live in `windows/tasks/run_<name>.vbs`.

**Never do these:**

- Never create a task with `LogonType: Interactive` and a bare `powershell.exe` or `python.exe` command without a wrapper — even with `-WindowStyle Hidden`, a new process in the interactive session can briefly steal focus on Windows 11.
- Never use `Win32_Process.Create` (WMI) to launch hidden processes — it does not reliably suppress the window.
- Never register a new task in `startup_all.ps1` with `LogonType: Interactive` without adding it to the FIX_TASK_SESSIONS_RUN_AS_ADMIN.bat setup script.

**Exception documentation:** If a task must stay Interactive (e.g. needs user-mounted Google Drive), document the exception inline in the script with a comment explaining why SYSTEM cannot be used.

Set 2026-05-05 by operator. Root cause: periodic tasks (MiruServiceWatchdog 2 min, MiruStallRecovery 3 min, MiruSentinel 20 min, MiruN8nWatchdog 15 min) ran Interactive and repeatedly stole focus while operator was typing.

### Second axis — interactive session required when workers restart the process (added 2026-05-09)

The "no focus stealing" rule above optimizes for SYSTEM (Session 0). That's still correct for **non-interactive periodic services** — watchdogs, sentinels, scheduled audits.

**But** if a non-elevated worker shell (CC, Cursor, normal PowerShell) needs to **kill or restart** the process autonomously, the spawned process MUST land in the operator's interactive Windows session (Session 1+), not Session 0. Cross-session same-user termination requires `SeDebugPrivilege` (admin), which non-elevated workers don't have — even SYSTEM-launched processes the worker user technically owns are unkillable from a worker shell when in Session 0.

**Decision rule when registering a new task:**

1. Will a non-elevated worker need to kill or restart this? → **Session 1+ mandatory.** Use a `shell:startup` shortcut (fires at logon, in interactive session) OR an AtLogOn-triggered task with `LogonType=Interactive` and a wrapper-based hidden-window pattern (no focus steal). Verify post-launch with `(Get-Process -Id <pid>).SessionId -ne 0`.
2. Pure background service / daemon / periodic check, never killed by workers? → SYSTEM (Session 0) is fine and preferred.

The `dispatch_listener` (port 19100) is the canonical Session-1+ case. Watchdogs and sentinels are the canonical SYSTEM case. See `.miru/reference/restart-procedures.md` for the dispatch_listener boot-path caveat and PRO-336 for the permanent fix.

### Third axis -- MCP server startup windows (added 2026-05-10)

MCP servers defined in `.mcp.json` are spawned by Claude Code as child processes at session start. Claude Code applies `windowsHide` / `CREATE_NO_WINDOW` for `node.exe` children but NOT for `cmd.exe` or bare `powershell.exe` -- so the choice of `command` in `.mcp.json` directly controls whether a console window flashes on startup.

**Rules:**

1. **Never use `npx.cmd` or `cmd /c npx.cmd` as the MCP server command.** `.cmd` files run inside `cmd.exe`; Claude Code spawns that `cmd.exe` without `CREATE_NO_WINDOW`, causing a visible console flash. Pre-install the npm package globally (`npm install -g <package>`) and set `"command": "node"` with the absolute path to the package's main script. Global node_modules root: `C:\Users\Dreighto\AppData\Roaming\npm\node_modules`.

2. **Never use `"command": "powershell.exe"` without `-WindowStyle Hidden` and `-NonInteractive`.** Always include those flags before `-ExecutionPolicy Bypass`.

3. **`@latest` tags in npx are banned.** They trigger a network version check on every session start, which spawns additional processes. Pre-installed packages are pinned at install time; run `tools/update_mcp_global_packages.ps1` periodically to pull updates.

4. **Docker-based entries are exempt** -- Docker handles console allocation internally; the `docker` command is already handled cleanly by Claude Code.

**Maintenance:** Run `tools/update_mcp_global_packages.ps1` to update all pre-installed MCP packages to their latest versions. No `.mcp.json` edits needed -- the package paths are stable across minor/patch version bumps.

**Current pre-installed MCP packages (as of 2026-05-10):**

```
C:\Users\Dreighto\AppData\Roaming\npm\node_modules\
  @perplexity-ai\mcp-server\dist\index.js
  @modelcontextprotocol\server-sequential-thinking\dist\index.js
  @playwright\mcp\cli.js
  @cyanheads\git-mcp-server\dist\index.js
  @mokei\mcp-sqlite\lib\serve.js
  @notionhq\notion-mcp-server\bin\cli.mjs
  @21st-dev\magic\dist\index.js
  @a.ardeshir\youtube-mcp\index.js
  shadcn\dist\index.js
```
