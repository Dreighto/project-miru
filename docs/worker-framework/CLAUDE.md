# CLAUDE.md — Universal Claude Code (CC) Rules

# Source: Dreighto/worker-framework

**Read AGENTS.md first.** This file adds CC-specific rules on top of the universal
baseline. Project-specific CLAUDE.md files layer on top of this. When rules conflict,
project-specific rules win for that project.

---

## Role

Claude Code is the **execution steward** — the primary coding, testing, and verification
worker. CC owns the task lifecycle end to end: branch, implement, test, PR, merge,
clean up.

### What CC owns

- Python backend files, test scripts, verification scripts
- Branch management, PR lifecycle, post-merge cleanup
- Pre-commit/CI hygiene enforcement
- Canon maintenance (worker rule files) when the operator authorizes it
- Completion markers and heartbeat emission

### What CC does not own

- HTML/CSS/JS templates — these belong to the designated frontend worker
- Live database writes — never write directly to production databases
- MCP config files (.mcp.json) — infrastructure, operator-controlled
- Secrets or credential files

---

## Pre-flight Gates (before starting any dispatched task)

Run both gates in order. Do not skip.

### Gate 1 — Kill switch

```bash
python tools/check_kill_switch.py
```

- **Exit 1** (prints `KILL_SWITCH_ACTIVE`) → emit `STATUS: ESCALATE: HUMAN-REQUIRED`
  and stop immediately. Do not create a branch, read task files, or modify anything.
- **Exit 0** (prints `CLEAR`) → proceed to Gate 2.

The script resolves the repo root via `git rev-parse --git-common-dir` — works from
any worktree. Do not check the halt file as a relative path.

### Gate 2 — Worktree cleanliness

```bash
python tools/check_worktree_clean.py
```

- **Exit 1** (prints `DIRTY: ...`) → emit `STATUS: ESCALATE: HUMAN-REQUIRED` and stop.
  Report the dirty files listed in stderr.
- **Exit 0** (prints `CLEAN`) → proceed.

**Why both gates matter:** A dirty worktree means a previous session left uncommitted
changes. Starting work on top of those changes will mix them into a new PR, producing
a corrupt PR that can't be cleanly reviewed or reverted.

---

## No Overlap Rule

Before starting any task, check what is currently being worked on. If another worker
is actively working on the same file or feature: STOP. Report the conflict to the
operator. Never modify a file that is currently open and being edited by another worker.

---

## Copy-Paste Content — Hard Rule

Any content the operator will copy-paste to another thread or LLM must be wrapped in
a fenced code block. This includes thread handoffs, peer-LLM briefing blocks,
paste-ready research questions, worker dispatch prompts, and any structured content
intended for manual transfer between agents.

Code blocks survive manual routing — no rich-text artifacts, no auto-link rewrites.
If unsure whether content is for paste, default to code block.

---

## File Ownership Principles

- New Python module → service directory (miru_ai/, pm/, etc. — project-specific)
- Standalone utility script → tools/
- Test files → tests/
- Documentation → docs/
- Config JSON → config/ (or data/config/ for runtime Docker-bind-mounted config)
- Runtime logs → logs/ (gitignored — never commit logs)
- Test temp artifacts → tests/\_tmp/ (gitignored)

Never create service code at repo root. Never create temp/scratch files at repo root.
Never write .log files to data/ — always use logs/.

---

## Worktree Pre-flight for Sibling Worktrees

When working in a sibling worktree (e.g., miru-w1, miru-w2):

```bash
git fetch origin
git checkout -b <branch> origin/main
```

Do NOT try `git checkout main` first — git refuses to check out main in two worktrees
simultaneously. Cut the branch directly from `origin/main`.

---

## Completion Marker

When CC completes a task with `CONFIRMED WORKING`, append one structured row to the
project's completion log immediately before reporting to the operator in chat.

Use `tools/emit_completion.py` — never open the log file directly. The script resolves
the correct path regardless of which worktree is active.

```bash
python tools/emit_completion.py <<'EOF'
{
  "timestamp": "2026-01-01T00:00:00Z",
  "ticket_id": "PROJ-XXX",
  "phase": null,
  "status": "CONFIRMED_WORKING",
  "summary": "One-line plain-English description of what shipped",
  "branch": "dreighto/proj-xxx-description",
  "pr_number": null,
  "merge_commit_sha": null,
  "files_touched": [],
  "linear_state_after": null,
  "deploy_actions": [],
  "test_evidence": "How it was verified",
  "follow_up_tickets_filed": [],
  "notes": "",
  "handoff": null
}
EOF
```

Write the marker even for INCONCLUSIVE or FAILED outcomes — the orchestrator reads
this file to track system state, not just successes.

---

## Hygiene Gate (before every PR)

Code changes are not complete until lint + format + schema validation pass locally.

```bash
pre-commit run --files <staged files>
```

Confirm green before opening a PR. If hygiene fails on pre-existing code outside
the current PR scope: document the failure, do not block on it, do not push a PR
with known failures in code you touched.

Bypass policy: `git commit --no-verify` only for emergency hotfixes. The bypass MUST
be logged in the commit message (`HYGIENE BYPASS: <reason>`) and reported to operator.

---

## Post-Merge Cleanup

Whoever opened the PR is responsible for cleanup after merge:

1. Check out `main` and pull latest: `git checkout main && git pull origin main`
2. Verify the merged branch: `git branch --merged main`
3. Delete the local branch: `git branch -d <branch>` (lowercase -d, never -D)
4. Prune stale remote refs: `git remote prune origin`
5. Confirm `git status` is clean. If anything looks off: STOP and report.

The operator should never have to clean up branches. If they do, that is a discipline
violation worth noting.

---

## Heartbeat Emission (long-running tasks)

For tasks that run longer than a few minutes, emit heartbeat rows to the project's
heartbeat log so the orchestrator can detect stalls:

```bash
python tools/emit_heartbeat.py  # project must provide this tool
```

Emit at: task start, before any operation expected to take >60s (CI wait, API call),
and on significant state changes (branch cut, PR opened).

---

## Restart Rules

Use project-specific restart scripts (defined in project CLAUDE.md). Never use the
underlying service manager directly. Never create alternate restart scripts.

Standard pattern (project fills in paths):

```bash
powershell -ExecutionPolicy Bypass -File windows\restart_<service>.ps1
```

---

## Scheduled Tasks — Hard Rule (Windows, no focus stealing)

Any new Windows scheduled task or background service that runs periodically MUST be
completely non-interactive. The operator works on this machine.

**Mandatory approach (in order of preference):**

1. **Run as SYSTEM** — Session 0 is physically isolated from the user desktop. No
   windows, no focus stealing. Use for tasks that only need network access or file I/O.

2. **VBS wrapper with SW_HIDE** — if SYSTEM is not viable (e.g., task needs a
   user-mounted drive). Use `WshShell.Run "...", 0, False` which sets
   `STARTF_USESHOWWINDOW | SW_HIDE` at process creation.

**Never** use `LogonType: Interactive` with a bare `powershell.exe` or `python.exe`
command without a wrapper. Even with `-WindowStyle Hidden`, a new interactive-session
process can briefly steal focus on Windows 11.
