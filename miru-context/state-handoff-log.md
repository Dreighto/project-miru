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

# Miru thread handoff — 2026-05-03 (PM session, Claude Code closeout after Phase 1 ship)

## What we were working on

Two tracks in one CC session: (1) closed all six autonomy-gap tickets from the morning's CH-paused thread (PRO-284..289); (2) shipped Phase 1 of the four-phase Autonomy Overhaul (PRO-290 — A2A bus with claim ownership). All seven shipped, all merged, all VP Ops verified.

## What got done

- **PRO-289 (Urgent, behavioral)** — Drift correction is autonomous. Canon strengthened across `claude-operating-model.md`, `guardrails.md`, `CLAUDE_CHAT.md`, `canon-and-drift.md`. PR #79 → `7e779ef`.
- **PRO-286 (High, structural)** — Handoff freshness rule (Option B). PR #80 → `c0d75a5`. Project Memory `agenda` row seeded.
- **PRO-285 (High, structural)** — `tools/emit_completion.py` auto-fills `ticket_id` from `MIRU_TRACE_ID`; drift scanner now surfaces orphan markers as third drift category. 25 tests. PR #81 → `b65b09a`.
- **PRO-288 (Medium, doc)** — Manual-backfill expectation documented in dsw003 jsCode + agenda. PR #84 → `83fc848`.
- **PRO-287 (Medium, tooling)** — `git_local_status` read-only gateway tool. 14 tests. Operator-merged PR #83 → `c1a68f4`.
- **PRO-284 (Urgent, structural)** — `git_pull_main` gateway tool (Option A). 6 tests. Operator-merged PR #82 → `3768e97`.
- **PRO-290 — Phase 1 A2A Bus.** New `agent_messages` table in `miru_memory.db`, `tools/agent_bus.py` client, 24-test regression suite. WAL + busy_timeout=5000 enforced. PR #85 → `2a0901e`. VP Ops verified, zero flags.
- **Notion canon synced.** 01 Now updated with Autonomy Overhaul Phase 1 status. Work Log gained `2026-05-03 (PM)` anchor (distinct from morning's loop-hardening anchor).
- **New canon rule:** "Copy-paste content for manual routing — Hard Rule." Any content the operator will paste to CH/GPT/GMI/PXY/Cursor MUST be in a fenced code block. Added to `CLAUDE.md` + `miru-context/operator-profile.md`.

## What's still open

**Autonomy Overhaul Phases 2–4 — LOCKED until explicitly approved:**

- **Phase 2 (Judgment Trail)** — `tools/emit_decision.py` + `data/agent_decisions.jsonl` (would be 8th append-only file) + 9-trigger canon rule. Schema in master brief includes `identity / classification / authority_surface / decision_summary / calibration / contextual_surface / outcome / grading_state`. Calibration corpus only grades `judgment_driven` decisions, not `canon_mandated` ones.
- **Phase 3 (Subagent Isolation)** — `X-Miru-Tool-Profile` header on dispatch + gateway-side per-connection tool gating. Profiles: `drift_executor` (`linear_*`, `git_*`, `fs_*` — NO `telegram_*`), `vp_ops`, `reviewer`, `full_operator`. Includes Phase 3 Denial Test gate before W2 can auto-assign.
- **Phase 4 (Ingress Classifier)** — W2 extension to pre-classify tasks as `routine | judgment | ambiguous | blocked` before CH wakes up. Imposes `tool_profile` rather than asking CH to self-classify.

**Other open items (non-blocking):**

- Pre-existing test failure in `tests/test_miru_mcp_gateway_git_write.py::test_resolve_allowed_paths_rejects_worker_rule_file_other_than_claude` — predates today; CURSOR.md is in `_ALLOWED_EXACT` but the test asserts rejection. Flagged in PR #82 description; not filed as a separate ticket.

## Decisions made

- **Switch sessions per phase.** Phase 2 starts in a NEW CC session. Reasoning: Phase 1 is the bus that enables clean fresh-context handoffs; PRO-289's long-session permission-bias failure mode argues for resets at phase boundaries; phases are independent in code dependencies. Within a phase, stay in one session.
- **Copy-paste content goes in code blocks** — hard rule for all workers. Operator runs a manual multi-LLM routing workflow.
- **Phase 1 single-field `expires_at` is state-dependent.** pending → message TTL; claimed → claim TTL (overwritten); requeued → fresh window. Documented in `agent_bus.py` and the migration .sql.
- **Phase 1 does NOT modify `memory_tools.py`.** Existing `sqlite3.connect(timeout=5)` already maps to `busy_timeout=5000` at the Python driver level; WAL is sticky on the .db file.
- **DDL via gateway memory MCP `write_query` is BLOCKED** — confirmed during Phase 1. Future schema changes go through `tools/migrations/` + direct sqlite3 application.

## What the next thread should do first

1. Read this handoff. Read `CLAUDE_CHAT.md` (if CH) or `CLAUDE.md` (if CC) and confirm the new "Copy-paste content — Hard Rule" + the existing "Drift correction is autonomous" rule are both internalized.
2. Query Project Memory: `stack_state`, recent `decisions` (last ~10), active `agenda` items where priority ≤ 2.
3. Tail `data/cc_completion_log.jsonl` for the last few terminal markers.
4. Check Linear for any new tickets in Backlog/Todo since session close (most recent: PRO-290).
5. **Wait for operator direction on Phase 2.** Phases 2/3/4 are explicitly locked.

## What NOT to do

- Do NOT begin Phase 2, 3, or 4 without explicit operator approval.
- Do NOT modify `tools/agent_bus.py` or `tools/migrations/m005_agent_messages.sql` unless fixing a Phase 1 bug.
- Do NOT modify decision logging (no `agent_decisions.jsonl` exists yet — that's Phase 2).
- Do NOT modify tool profiles or W2 classifier behavior (Phase 3 / Phase 4).
- Do NOT change canon authority rules.
- Do NOT generate paste-ready content for the operator without wrapping in a code block (new hard rule).

## Loop health

- All 5 services healthy (mcp_gateway 18766, pm 18080, miru_ai 18765, dispatch_listener 19100, n8n 15678).
- Local `main` at `0942ec9` (PRO-290 marker push). Working tree clean.
- 7 append-only data files healthy. 86 markers in `cc_completion_log.jsonl`.
- `agent_messages` table live but empty (Phase 1 just shipped, no agents using it yet).
- VP Ops verification working — used today on PRO-290 closeout, returned VERIFIED with zero flags.

## Key files / surfaces touched

- **Linear:** PRO-284..289 → Done; PRO-290 created + Done; closeout comments on all seven.
- **GitHub:** PRs #79, #80, #81, #82, #83, #84, #85 all merged. Local main `e656830...0942ec9`.
- **Notion:** 01 Now + Work Log both updated.
- **Project Memory:** 2 new active priority-2 agenda rows.
- **Repo new files:** `tools/migrations/m005_agent_messages.sql`, `tools/agent_bus.py`, `tests/test_agent_bus.py`, `tests/test_emit_completion_ticket_id_autofill.py`, `tests/test_drift_scanner_orphan_markers.py`, `tests/test_git_local_status.py`, `tests/test_git_pull_main.py`, `archive/design_docs/deck_builder_before.html`.
- **Repo edits:** `CLAUDE.md`, `CLAUDE_CHAT.md`, `miru-context/canon-and-drift.md`, `miru-context/claude-operating-model.md`, `miru-context/guardrails.md`, `miru-context/operator-profile.md`, `miru-context/state-handoff-log.md` (this file), `tools/emit_completion.py`, `tools/miru_mcp_gateway/git_tools.py`, `docker/n8n/workflows/w-drift-scanner.json`, `.pre-commit-config.yaml`.
- **Schema:** `data/miru_memory.db` gained `agent_messages` table + WAL mode.
- **CC auto-memory:** new entry `feedback_copy_paste_code_blocks.md`.
