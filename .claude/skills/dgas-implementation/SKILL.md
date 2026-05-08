---
name: dgas-implementation
description: Execute a Deterministic Governed Autonomous System (DGAS) Tier 1 or Tier 2 implementation task in Project Miru. Use when building or hardening deterministic enforcement gates — examples include MCP gateway localhost-bind, pre-commit secret scanning (TruffleHog, Gitleaks), GitHub branch protection, MCP gateway profile deny-list extensions, hash-chained audit logs, audit chain verifier, daily audit anchor, git-execution wrapper for force-push or branch -D, governance file registry, fault-injection tests for governance gates, governance metrics logging, audit log verification scripts. Triggers include DGAS, deterministic enforcement, governance gate, irreversible-op gating, gateway hardening, audit chain, hash chain, secret scanner pre-commit, branch protection, governance metrics, fault injection test, full_operator localhost bind, MCP profile deny-list, governance file registry, code-enforce, prompt-enforce, governance theatre, the synthesis doc, the DGAS plan, item from the three-way synthesis, Tier 1, Tier 2. Do NOT use for routine bug fixes, frontend or UI work, n8n workflow JSON edits, instruction-architecture changes (those go through governance file registry instead, not DGAS), or PR cleanup that does not involve a deterministic enforcement gate.
---

# dgas-implementation

This skill drives the Deterministic Governed Autonomous System (DGAS) implementation workflow for Project Miru. The architecture and priorities are locked in the three-way synthesis document. Your job when this skill fires is to execute one ticket from that synthesis cleanly.

## Required reading before any code change

Read these in order. Don't skip — every DGAS ticket sits on these:

1. `data/peer_reviews/2026-05-08_dgas_three_way_synthesis.md` — the locked architecture. Three reviewers (CC + GMI + PXY). Tier 1 / Tier 2 priorities, the decision rule, what's deferred or skipped.
2. `data/peer_reviews/2026-05-08_dgas_research_synthesis_cc.md` — the long-form synthesis with research citations.
3. The specific Tier 1/2 item being worked. The synthesis numbers items 1–10; pick the one the operator named or the one earliest in the order.
4. `CLAUDE.md` (slim core) — kill switch, worktree gate, append-only invariants, completion contract, fail-closed directive. The DGAS work sits on top of these, never around them.
5. `AGENTS.md` — Operator Communication Standard (always lead with the plain-English block), Try Harder Discipline.
6. `.miru/overlays/workflow-git.md` — PR merge tier, hygiene gate, automated PR review sequence, post-merge cleanup, WIP commit checkpoints.
7. `.miru/overlays/workflow-completion.md` — completion marker schema, terminal states, test_evidence format.

## The decision rule

A DGAS gate is the right scope if any of these are true. If none are true, this skill is the wrong tool — it's probably a prompt rule, not a code gate.

- **Irreversible** — force-push, drop table, schema mutation without rollback, secret leak, money movement, deletion of unmerged work.
- **Undetectable by the worker itself within the same session** — completion marker corruption, test evidence falsification, audit log tampering. The worker can't reliably catch its own non-compliance.
- **Implicit trust elevation** — the change modifies gateway profiles, `.miru/overlays/`, pre-commit hooks, validator scripts, or any file that governs future actions of any worker.
- **State integrity** — corrupts persistent state in ways that contaminate downstream reasoning (poisoned routing history, broken append-only chain, stale cached config).

Otherwise prompt-enforce, not code-enforce. Push back to the operator if the ticket asks for a code gate that doesn't satisfy any of these.

## Five-phase workflow

Run the phases in order. Don't skip a phase. If a phase fails, escalate per the rules at the bottom — don't paper over a failed phase to keep moving.

**Heartbeat contract**: Emit heartbeats throughout the session using `tools/emit_heartbeat.py`. Requirements:

- Emit a heartbeat at regular intervals (configurable cadence, default every major step).
- Emit a heartbeat on each phase transition (Phase 1→2, 2→3, 3→4, 4→5).
- Emit a special "stall" heartbeat when progress stalls (blocked, waiting, unclear direction).
- Include a fault-injection test that verifies heartbeat cadence and stall signals are produced and that `tools/emit_heartbeat.py` is invoked as part of the worker run.

### Phase 1 — Pre-investigate (read before write)

Goal: lock the design with real file:line references, not guesses.

1. Run pre-flight: `python tools/check_kill_switch.py` then `python tools/check_worktree_clean.py` from the worktree root. If either fails, escalate immediately.
2. Read the synthesis doc section for this ticket. Extract: what gap closes, what files are affected, what the success criterion is.
3. Search the actual code:
   - For gateway changes → read `tools/miru_mcp_gateway/server.py`, `gateway_security.py`, `profiles.py`, `_context.py`.
   - For pre-commit changes → read `.pre-commit-config.yaml`, the existing hook list.
   - For audit chain → read `tools/emit_completion.py`, `tools/emit_heartbeat.py`, the JSONL files in `data/`.
   - For git wrappers → read `.miru/overlays/workflow-git.md` for the policy this code enforces.
4. Find an existing test pattern to mirror. Most DGAS tests should look like `tests/test_phase3_denial.py` (unittest, contextvar manipulation, `_make_cfg` fixture).
5. List by file:line what will change and what tests will exercise it. Cap the list — if it's more than ~5 files or ~300 LOC, this is two tickets, not one. Stop and ask the operator to split.

### Phase 2 — Lock the design (in the ticket, not in chat)

Adopted lesson: "Lock design in the Linear ticket description, not in the prompt wrapper" (PRO-180 retro). The design has to survive worker-session restarts.

If you're authoring a ticket for Codex / Cursor / Gemini → invoke the `locked-design-ticket` skill instead. That skill handles the handoff format.

If you're implementing the work directly:

- Stage the locked design as a comment or section at the top of your scratch notes. Schema, rules, scope, don't-touch list, done-when criteria.
- Include investigation steps if any spec gap remains. Don't fill spec gaps silently.
- Confirm the don't-touch list explicitly. Files that look related but should NOT change. Examples: profile definitions in `profiles.py` are usually NOT what's changing for a gateway middleware fix; the existing test patterns shouldn't be modified just because you added new ones; the JSONL append-only invariant is sacrosanct.

### Phase 3 — Implement (one branch, one PR, no detours)

1. Cut a branch from clean `origin/main` using the project's branch-prefix convention. The current prefix is `dreighto/` (matching the GitHub account that owns the repo); confirm by running `git for-each-ref --format='%(refname:short)' refs/remotes/origin/ | head` and matching the dominant pattern. The base point MUST be `origin/main` explicitly — don't trust the current HEAD: `git fetch origin && git checkout -b dreighto/<ticket-slug> origin/main`. Do NOT `git checkout main` first if you're in a worktree.
2. Emit a heartbeat on phase transition (Phase 2→3).
3. Make the change. Match existing style. Pre-commit will reformat — let it. Don't fight ruff-format.
4. Add the tests in the same commit as the implementation. Don't ship an enforcement gate without a fault-injection test that proves the gate fires when expected and doesn't fire when not.
5. WIP commit at each major phase per `.miru/overlays/workflow-git.md` (tests written, implementation done, pre-commit running, awaiting review). Squash before opening the PR.
6. Run `python -m pre_commit run --files <files>` and confirm green before opening the PR.
7. Emit a heartbeat on phase transition (Phase 3→4).

### Phase 4 — Verify (don't ship a gate you haven't proven works)

For DGAS work, verification means more than "tests pass." It means:

1. **Fault injection ran.** A test deliberately tries to do the bad thing the gate prevents. The gate stops it. Without this test, the gate is theatre — see the synthesis doc item #7.
2. **The happy path still works.** A test confirms legitimate use is not broken. For the gateway localhost bind, that means STDIO traffic and 127.0.0.1 HTTP both still work.
3. **The validator pass.** If a related validator exists (`tools/validate_instruction_migration.py`, `vp_ops_verify_ticket`), run it. If you broke it, you broke something.
4. **Edge cases enumerated.** For each gate, list what could go wrong: missing field, malformed input, IPv6 vs IPv4, transport layer differences, race conditions. Confirm each is either handled or documented as out of scope.
5. **Heartbeat verification.** Ensure the fault-injection test verifies that heartbeats are emitted (cadence and stall signals) and that `tools/emit_heartbeat.py` is invoked during the worker run.
6. Emit a heartbeat on phase transition (Phase 4→5).

### Phase 5 — Ship (PR, review, merge, cleanup)

1. Open the PR. Title format: `DGAS Tier <N>: <one-line summary>`. Body references the synthesis item number and the locked design.
2. PR tier evaluation per `.miru/overlays/workflow-git.md`. Most DGAS tickets are CC-merge or operator-merge. If the change touches a governance file (gateway profiles, .miru/overlays/, pre-commit config), it's operator-merge by default.
3. Wait for CodeRabbit and Bugbot. Address every actionable finding. Stale findings (already fixed in earlier commits) — call them out in the PR conversation but don't re-fix. New valid findings — push a follow-up commit.
4. After merge: return to main, pull, delete the branch with verified force-delete (`gh pr list --head <branch> --state merged` then `git branch -D <branch>` only if a merged PR exists).
5. Emit the completion marker via `tools/emit_completion.py`. Requirements:
   - Always include a non-null `ticket_id` (use the actual ticket identifier, never `null` or a placeholder).
   - `test_evidence` format MUST be one of: `passed/total` (e.g., `5/5`), `ci_only:` (followed by CI run reference), or `no_tests` — never freetext narrative. This ensures downstream parsers and drift tooling can reliably validate the marker.
   - If the marker carries a `handoff` object, it MUST include all canonical fields: `next_worker`, `ticket_id`, `context`, `entry_points`, `watch_out_for`, and `blocked_on`. The `ticket_id` field within the handoff MUST be non-null.
   - The `handoff` object MUST be written as the LAST action of the session — emitting a marker before the work is fully done leaves a stale handoff that the next worker reads before the actual state stabilizes.

## Common pitfalls (don't repeat these)

- **CRLF noise on Windows.** Files appear modified after `git checkout main` because of line-ending normalization. If `git diff --stat` shows zero lines changed but git says "modified," it's CRLF. Run `git checkout -- <file>` to drop it.
- **The "no header = full_operator" assumption.** Don't break it accidentally. The operator's local STDIO session relies on this. If your change rejects STDIO traffic, you broke it.
- **Pre-commit auto-fix loops.** `ruff-format` modifies files when it runs. Re-stage after every pre-commit run.
- **Codex's missing pre_commit module.** When dispatching Codex for DGAS work, install pre_commit in its environment first. Don't assume it has the same Python env as CC.
- **The "we'll add the test later" trap.** A DGAS gate without a fault-injection test isn't a gate — it's a prompt. Refuse to ship.
- **The "while I'm here, let me also fix..." trap.** DGAS tickets are scoped. Out-of-scope findings → file a follow-up Linear ticket, not a wider PR.
- **Chasing CodeRabbit's stale findings.** If CodeRabbit re-flags something already fixed in an earlier commit, note it in the PR conversation and skip. Don't re-fix.

## DO NOT do these as part of any DGAS ticket

- Do not edit any `data/*.jsonl` file directly — append only via helper scripts.
- Do not modify `card_catalog.db` or any DB schema.
- Do not rewrite the kill switch, worktree gate, or pre-flight scripts.
- Do not add new MCP tools as part of a DGAS hardening ticket — that's a separate scope.
- Do not change the existing profile definitions in `profiles.py` to "fix" the gateway full_operator default — that's middleware, not profile-table work.
- Do not bypass pre-commit with `--no-verify`. If hygiene fails, fix it.
- Do not self-merge a governance-file change (per item #6 of the synthesis — the governance file registry rule). All `.miru/overlays/`, gateway profiles, pre-commit config, validator scripts go to operator merge.

## Escalation

Stop and emit `STATUS: ESCALATE: <category>` if any of these:

- The synthesis doc and the actual code disagree on what the gap is. Don't guess. ESCALATE: DESIGN_CHANGE.
- The change requires modifying more than 5 files or 300 LOC. ESCALATE: SCOPE_EXPANSION.
- The change needs an operator decision the synthesis flagged as deferred (Token of Presence, Vault, OPA/Cedar). ESCALATE: HUMAN-REQUIRED.
- A failure mode appears that's not in the synthesis. ESCALATE: HUMAN-REQUIRED with the specific failure documented.
- More than two review cycles produce new substantive findings. The change is more complex than scoped. ESCALATE: SCOPE_EXPANSION.

## Reference

- Three-way synthesis: `data/peer_reviews/2026-05-08_dgas_three_way_synthesis.md`
- CC's first-pass synthesis: `data/peer_reviews/2026-05-08_dgas_research_synthesis_cc.md`
- Sample locked-design ticket (localhost bind): `data/peer_reviews/2026-05-08_codex_ticket_localhost_bind.md`
- Tests to mirror: `tests/test_phase3_denial.py`, `tests/test_jsonl_append_only_invariant.py`
- Workflow rules: `.miru/overlays/workflow-git.md`, `.miru/overlays/workflow-completion.md`
- Adopted lesson on locked-design tickets: `.miru/overlays/adopted-lessons.md`

## When to NOT use this skill

- The task is a routine bug fix not related to deterministic enforcement.
- The task is frontend or UI work (use the craft guides in `docs/ui_ux/` and `docs/pm/`).
- The task is an n8n workflow JSON edit.
- The task is a typo or doc-only fix.
- The task is to author a ticket for another worker (Codex, Cursor, Gemini) — use the `locked-design-ticket` skill instead.
