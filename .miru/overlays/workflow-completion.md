# Overlay — workflow-completion

```
Overlay: workflow-completion
Architecture: MIRU-INSTRUCTIONS-v2
Load when: reaching a terminal task state, emitting heartbeats, or signalling a stall.
Last reviewed: 2026-05-08
```

This overlay carries the rules for finishing tasks: completion marker schema,
heartbeat emission during long tasks, and stall classification when a task
cannot proceed normally.

The terminal-state mandate (CONFIRMED_WORKING / INCONCLUSIVE / FAILED) lives
in the slim `CLAUDE.md` core. This overlay tells you what to do with the
terminal state once you have one.

---

## Completion-marker convention (locked 2026-04-25)

When CC completes a task with `CONFIRMED WORKING` status, CC MUST append one structured row to `data/cc_completion_log.jsonl` immediately before reporting completion to the operator in chat.

This is how Claude Chat verifies completion without the operator manually relaying CC's chat report. The file is append-only — never edit, never truncate.

### Schema (one JSON object per line, no array wrapping)

- `timestamp` (ISO 8601 string, UTC) — when the task completed.
- `ticket_id` (string) — Linear ticket identifier (e.g. "PRO-80"). Use null if no ticket.
- `phase` (string or null) — sub-phase label if relevant (e.g. "A").
- `status` (enum) — `CONFIRMED_WORKING` | `INCONCLUSIVE` | `FAILED`.
- `summary` (string) — one-line plain-English description of what shipped.
- `branch` (string or null) — git branch name if applicable.
- `pr_number` (int or null) — GitHub PR number if applicable.
- `merge_commit_sha` (string or null) — merge commit SHA if merged.
- `files_touched` (array of strings) — repo-relative paths edited or created.
- `linear_state_after` (string or null) — final Linear ticket state (e.g. "In Review", "Done").
- `deploy_actions` (array of strings) — short descriptions of any deploys, redeploys, or service restarts ("w7 redeployed via deploy-workflow.ps1, active state preserved").
- `test_evidence` (string) — structured test result. **Format rules (enforced — Hermes and VP Ops parse this field):**
  - If tests were run, write `passed/total` where `total` is ALL applicable tests for the ticket's scope (not just the ones the worker chose to run), optionally followed by brief context. Examples: `"34/34 tests pass"`, `"7/8 (1 flaky, see notes)"`. The machine-parseable ratio must appear first; the regex `(\d+)\s*/\s*(\d+)` extracts it.
  - If only CI/lint checks apply (no unit or integration tests), write `"ci_only: pre-commit green, hygiene CI pass"`.
  - If no tests apply (behavioral rule, doc-only, config change), write `"no_tests"`.
  - Never write freetext narrative without a leading `passed/total`, `ci_only:`, or `no_tests` prefix. The field must be machine-parseable.
- `follow_up_tickets_filed` (array of strings) — Linear ticket IDs filed during this work for out-of-scope items.
- `notes` (string) — anything Claude Chat needs to know that doesn't fit above. Empty string if none.
- `handoff` (object or null) — structured brief for the next worker when a continuation is expected. Null if no handoff needed. Schema:
  - `next_worker` (string) — which worker picks this up (e.g. "cursor", "codex", "claude-code").
  - `ticket_id` (string) — the Linear ticket the next worker is working against.
  - `context` (string) — plain English paragraph: what was built, what contract it establishes, what the next worker needs to know to start.
  - `entry_points` (array of strings) — file:line references that are the best starting points (e.g. `"pm/templates/card_detail.html:42"`).
  - `watch_out_for` (array of strings) — specific gotchas, edge cases, or constraints the next worker should know before touching anything.
  - `blocked_on` (string or null) — null, or a ticket ID if the next worker can't start until it resolves.

### When to write

Write the row at the moment CC would otherwise produce a `CONFIRMED WORKING` chat report. The chat report still happens (operator visibility is still useful), but the marker is the structured truth Claude Chat reads.

For `INCONCLUSIVE` or `FAILED` outcomes: write the row too, with status set accordingly. `notes` field should explain what blocked or broke. This gives Claude Chat visibility into stalled work.

### When NOT to write

- Mid-task progress updates. The marker is for terminal task state only.
- Sub-task milestones inside a multi-phase ticket. Wait for the phase to land.
- Diagnostic-only or read-only work that produces no commit, no merge, no deploy. (CC can still chat-report, just no marker needed.)

### How to write — use the script, not a raw file open

Always write the marker via `tools/emit_completion.py`. This script resolves the
correct path regardless of which worktree the worker is running in (miru-w1, miru-w2, etc.):

```bash
python tools/emit_completion.py <<'EOF'
{"timestamp":"...","ticket_id":"PRO-XXX", ...}
EOF
```

Or from Python:

```python
import json, subprocess
marker = {"timestamp": "...", "ticket_id": "PRO-XXX", ...}
subprocess.run(["python", "tools/emit_completion.py"],
               input=json.dumps(marker), text=True, check=True)
```

**Never open `data/cc_completion_log.jsonl` directly with a relative path** — from a worktree
that resolves to the wrong directory and the orchestrator will never see the entry.

### Rules

- Append only. Never read-modify-write the file. Never sort it. Never deduplicate it.
- One JSON object per line. No trailing commas, no array wrapping.
- ISO 8601 UTC timestamps with `Z` suffix.
- If a field is genuinely unknown or not applicable, use `null` (not empty string, not omitted).
- `tools/emit_completion.py` handles serialisation — pass a dict from Python or a JSON string from shell.

### Verification by Claude Chat

Claude Chat reads this file via Filesystem MCP when the operator says "task done" or asks for completion verification. Claude Chat then cross-checks the marker against GitHub PR state, Linear ticket state, file changes, and (for n8n workflows) deploy state. Discrepancies between the marker and ground truth get flagged for operator review.

---

## Heartbeat emission (PROVISIONAL — promote after first validated stall-recovery use)

Workers emit a heartbeat row to `data/cc_heartbeat_log.jsonl` during long-running tasks so the orchestrator can detect stalls without operator intervention. The file is append-only (gitignored) — same hard rules as the other five append-only files. Use `tools/emit_heartbeat.py` to write rows; do not hand-roll the append logic per-task.

**Schema (one JSON object per line):**

```jsonl
{
  "ts": "2026-04-28T08:12:00Z",
  "worker_id": "claude-code-1",
  "ticket_id": "PRO-XXX",
  "status": "IN_PROGRESS",
  "step": "running_pre_commit",
  "branch": "dreighto/pro-xxx-...",
  "last_file_written": "tests/test_x.py",
  "stall_signal": null,
  "outputs": []
}
```

Field definitions:

- `ts` (ISO 8601 UTC with `Z`) — heartbeat emit time.
- `worker_id` (string) — stable per-worker identifier (e.g. `claude-code-1`).
- `ticket_id` (string) — Linear ticket the worker is on.
- `status` (enum) — `IN_PROGRESS` only. Terminal states go in `cc_completion_log.jsonl`.
- `step` (string) — short label of current phase (e.g. `pre_flight`, `writing_tests`, `running_pre_commit`, `opening_pr`, `awaiting_bugbot`, `post_merge_cleanup`).
- `branch` (string or null) — current git branch.
- `last_file_written` (string or null) — most recently written/staged file.
- `stall_signal` (string or null) — populated when the worker detects a likely stall (e.g. `"awaiting_external: bugbot"`, `"deny_rule_hit: <rule>"`, `"ambiguous_spec_question_pending"`). Null otherwise.
- `outputs` (array of strings) — artifact paths produced so far. Used by dependent tickets.

**Emit cadence:** at the start of each major phase, before any operation expected to take >60 s (CI wait, Bugbot wait), and on significant state changes (branch cut, PR opened).

**Stall detection (orchestrator side):** if `now − max(heartbeat.ts for ticket_id) > 5 minutes` AND no terminal marker exists in `cc_completion_log.jsonl`, the worker is considered `STALLED`. Threshold is tunable; 5 min is the starting point. Source: PRO-180 (research-sourced, 2026-04-28).

---

## Stall classification (PROVISIONAL — promote to adopted after first validated use)

Terminal states (above) cover task completion. Workers also signal stall conditions during a task using the four classes below. Sourced from Augment Code's published multi-agent failure taxonomy (PRO-178); flagged provisional until a real stall-recovery event in this project validates the schema.

| Class                     | Worker emits                                                                                                                                                                                                                                                                                                                 | Orchestrator response                                                                                                                             |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Transient**             | Heartbeat lapse past TTL (PRO-180) with no error and no terminal state. No worker emit required — orchestrator infers from heartbeat staleness.                                                                                                                                                                              | Auto-unstick: branch hygiene, rebase-on-main, missing env key the orchestrator controls, ambiguous spec covered by locked design.                 |
| **Ambiguous spec**        | `STATUS: INCONCLUSIVE` plus one specific question. Worker MUST have completed the Try Harder Discipline (AGENTS.md) first — canon check, repo search, alternative attempt. Question format: "I tried X (failed because Z). I tried Y (failed because Z). Should I do A or B?" Not acceptable: "I'm not sure how to proceed." | Orchestrator checks the locked design (Linear ticket description). If covered → answer via Linear comment. If not covered → escalate to operator. |
| **Dependency starvation** | `STATUS: BLOCKED_ON: <ticket_id>` (e.g. `BLOCKED_ON: PRO-180`). Worker stops, does not retry.                                                                                                                                                                                                                                | Orchestrator reroutes, resequences, or marks task as waiting. Not a stall — expected behavior in parallel-worker setups.                          |
| **Human-required**        | `STATUS: ESCALATE: <category>` where category is one of `SECURITY`, `SCOPE_EXPANSION`, `DESIGN_CHANGE`, `IRREVERSIBLE_OP`, `REPEATED_FAILURE`.                                                                                                                                                                               | Orchestrator writes Linear comment, pings operator via Telegram, parks task.                                                                      |

Rules:

- Existing terminal states (CONFIRMED_WORKING / INCONCLUSIVE / FAILED) are unchanged. The new states (BLOCKED_ON, ESCALATE) are non-terminal stall signals — task continues once the block clears or operator decides.
- For `INCONCLUSIVE` with an ambiguous-spec question: the question must be answerable in one Linear comment. If the worker needs more than one back-and-forth, escalate instead.
- For `ESCALATE`: the category determines orchestrator behavior. `SECURITY` and `IRREVERSIBLE_OP` always go to operator immediately. `SCOPE_EXPANSION` may be filed as a follow-up Linear ticket and the in-scope work continued. `DESIGN_CHANGE` always goes to operator. `REPEATED_FAILURE` (same worker stalling on same task >2 times) always goes to operator.
- `routing_decisions.outcome` enum (success / failure / partial / deferred / legacy) is sufficient — these stall signals are mid-task states, not terminal outcomes, so the existing outcome enum doesn't need expansion.

Promotion criteria: first validated stall-recovery event in this project (orchestrator correctly classifies a real worker stall, takes the matching action, and the recovery succeeds) → promote section to "adopted" via the Lesson Promotion Discipline (Notion canon, 2026-04-28).
