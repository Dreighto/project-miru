# State Handoff Log

Claude Chat writes the latest handoff directly to this file at every thread close. The next
thread reads it at startup to restore context without asking the operator to recap.

**How it works:**

- At thread close: Claude Chat runs thread-close hygiene (memory sync, Notion drift check, Linear
  cleanup), then overwrites the "Latest Handoff" section below with the current handoff.
- At thread start: read this file. If a handoff exists, start from it. No copy-paste needed.
- Only the latest handoff is kept here. History goes in Project Memory (agenda table) if needed.

This file is not the place to solve autonomy or continuity — that's handled by the operating
model and Project Memory. This is just the bridge that tells the next thread where to start.

---

## Template

```markdown
# Miru thread handoff — [DATE + TIME CONTEXT]

## What we were working on

[1-2 sentences. Plain English. What was the main focus?]

## What got done

[Bullet list. Each item = one ticket or decision that shipped/closed. Include PRO-### numbers.]

## What's still open

[Bullet list. Each item = one ticket or task in progress or blocked. Include Linear state and why it's stuck.]

## Decisions made

[Bullet list. Key decisions from this thread. Already logged in Project Memory — this is just a quick reference.]

## What the next thread should do first

[1-3 concrete actions. Specific, not vague. "Promote PRO-191 to Todo" not "continue the smoke test work."]

## What NOT to do

[Tickets that shouldn't be promoted, conversations that are paused, known traps.]

## Loop health (if relevant)

[Quick status. Only include if the loop was exercised or changed this thread.]

## Key files touched

[Repo files, Notion pages, or Linear tickets created or significantly changed.]
```

---

## Rules

- **Keep it short.** The whole handoff should fit on one phone screen, maybe two.
- **Use plain English.** The operator reads these too.
- **No redundancy with Project Memory.** Reference decision IDs, don't repeat full rationales.
- **Don't pack everything.** The next thread has tools — it can look things up. The handoff just says where to start.
- **Write at thread close.** Canon hygiene checks happen first, then the handoff is drafted.

---

## Where It Lives

The latest handoff is written directly to the "Latest Handoff" section below by Claude Chat
at thread close. The next thread reads this file at startup — no copy-paste required.

If the operator wants a handoff archived, Claude logs a compact version to Project Memory's
agenda table with the handoff content in a notes-style field.

---

## Latest Handoff

# Miru thread handoff — 2026-05-02 (CC session, autonomy wiring sprint)

## What we were working on

Wiring up the full autonomous loop: Claude Chat model/effort dispatch, PR review and merge,
branch deletion, PR policy enforcement, and all supporting MD files for autonomy readiness.

## What got done

- **PRO-265 (direct-to-main, de546ad)** — Per-dispatch model and thinking-level params wired
  end-to-end: `dispatch_tools.py` → `index.js` → `spawn.js`. Claude-code workers get
  `--model <id>` and `--extended-thinking` injected at spawn time when Claude Chat passes them.
- **PRO-266 (direct-to-main, de546ad)** — `github_merge_pr` and `github_delete_branch` gateway
  tools added. Claude Chat can now squash-merge CC-eligible PRs and delete branches directly
  without operator relay. Protected-branch guard on delete (main/master/develop).
- **CLAUDE.md** — mandatory pre-commit PR policy decision gate added. Workers must evaluate
  direct-to-main vs CC-merge vs operator-merge before every commit.
- **CLAUDE_CHAT.md** — canon change escalation added as first trigger; full PR review/merge loop
  documented; dispatch protocol expanded with model/thinking selection (step 3) and PR loop
  trigger (step 11). PRO-265 and PRO-266 referenced.
- **miru-context/worker-roster.md** — model/thinking-level selection table added.
- **miru-context/source-of-truth.md** — budget state row updated to reflect file is live.
- **miru-context/operating-model.md** — canon change escalation trigger added.
- **PROJECT_MIRU_INSTRUCTIONS.md** — full cleanup: startup reads expanded to 11 files,
  inline PRO bug tickets removed, stale references removed, Copilot/Windsurf marked inactive.

## What's still open

- **Dispatch listener restart** — spawn.js changes are live in code but the running listener
  (port 19100) needs a restart to pick them up. Run `windows\restart_dispatch_listener.ps1`
  or equivalent.
- **MCP gateway restart** — github_merge_pr and github_delete_branch won't appear as tools
  until the gateway (port 18766) is restarted.
- **PRO-265 flag name** — `--extended-thinking` is the implemented flag for claude-code.
  Verify this is the actual Claude CLI flag name before first live use; adjust in spawn.js if not.
- **MiruN8nWatchdog task** — still needs `register_watchdog_task.ps1` from elevated shell.
- **MiruOpsDigest task** — still needs `register_ops_digest_task.ps1` from elevated shell.
- **PRO-261** — stall detector fires on Done tickets. Normal priority, Backlog.
- **PRO-259** — pending_callbacks stale awaiting entries. Low risk, Backlog.
- **PRO-154** — W1 504 retry-with-backoff. Todo, no blocker.
- **`services/dispatch_listener/src/allowlist.js`** — has a tracked change (M in git status from previous session). Verify with `git diff` — confirm intentional or discard before next commit.

## Decisions made

- Model/thinking selection is Claude Chat's responsibility at dispatch time; workers run at
  default unless override included in dispatch payload. PRO-265 is the wiring; fallback is
  including model guidance in prompt text (already documented in CLAUDE_CHAT.md).
- `--extended-thinking` is the flag used for claude-code; gemini/codex ignores the field for now.
- `github_merge_pr` defaults to `squash` merge method, matching the CLAUDE.md policy.
- `github_delete_branch` refuses to delete main/master/develop as a safety guard.
- Canon changes (CLAUDE.md, CLAUDE_CHAT.md, worker rule files, structural Notion docs) always
  require operator approval before executing — added as first escalation trigger.

## What the next thread should do first

1. Restart dispatch listener (port 19100) and MCP gateway (port 18766) to pick up new code.
2. Verify `--extended-thinking` is the correct Claude CLI flag — run `claude --help` or check docs.
3. Check Linear queue and dispatch PRO-154 (W1 504 retry) — next real code ticket.

## What NOT to do

- Do not commit `config/claude_chat.mcp.json` (sensitive, intentionally untracked)
- Do not dispatch to Cursor via W4 (permanent manual-only)
- Do not clear `data/dispatch_dlq.jsonl` (append-only; historical entries expected)
- Do not self-merge operator-merge-column PRs (new files, schema, infrastructure)

## Loop health

All changes committed direct-to-main (de546ad). Working tree clean except untracked files.
Dispatch listener and MCP gateway need restarts to pick up new code.
Budget: Cursor ~85% left, Codex ~60% left (unverified — check cursor.com/settings).

## Key files touched

- `tools/miru_mcp_gateway/dispatch_tools.py` — model/thinking_level params added
- `services/dispatch_listener/src/index.js` — model/thinking_level extraction + validation
- `services/dispatch_listener/src/spawn.js` — extra flags injection for claude-code
- `tools/miru_mcp_gateway/github_tools.py` — merge_pr + delete_branch tools added
- `CLAUDE.md` — mandatory PR policy decision gate added
- `CLAUDE_CHAT.md` — canon escalation, PR loop, model/thinking selection
- `miru-context/worker-roster.md` — model assignment table
- `PROJECT_MIRU_INSTRUCTIONS.md` — full cleanup
- `data/cc_completion_log.jsonl` — PRO-265 and PRO-266 completion markers appended
