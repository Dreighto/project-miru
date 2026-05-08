# Overlay — domain-ops

```
Overlay: domain-ops
Architecture: MIRU-INSTRUCTIONS-v2
Load when: touching scheduled tasks, services, Notion (read or write), or MCP tool config.
Last reviewed: 2026-05-08
```

This overlay carries the rules for Windows operations, MCP tool usage, and
Notion read/write authority. Load it before changing scheduled tasks,
adding/removing MCP servers, or writing to Notion.

---

## Notion — Read/Write Rules

- ALL workers may READ Notion to understand the current job, active tasks, and system state
- Claude Chat is the default Notion writer for architectural decisions, new page structure, consultant packet content, and cross-session synthesis
- **Claude Code (VP Ops) has standing write authority** for the following Notion tasks — no per-task operator authorization required:
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
