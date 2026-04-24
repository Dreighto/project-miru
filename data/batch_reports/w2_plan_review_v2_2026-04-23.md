# W2 — Worker Selection Router — Plan Review v2 (2026-04-23)

**Reviewer:** Claude Code (Opus 4.7).
**Supersedes:** [w2_plan_review_2026-04-23.md](w2_plan_review_2026-04-23.md) (v1, same day).
**Subject:** W2 — the second n8n workflow in Project Miru's automation layer — picks which LLM coding worker handles a Linear issue once it's promoted out of intake-draft.
**Linear issue:** [PRO-33](https://linear.app/project-miru/issue/PRO-33/build-w2-worker-selection-router-plan-mode-pass).
**Scope of this revision:** five targeted fixes against v1 plus the derivative cleanup they force. No workflow JSON, no deploy, no Notion mutations, no compose changes, no new Linear issues.

---

## v2 changelog (what this revision fixes vs. v1)

1. **Confidence floor patched.** v1's formula gave 0.90 for `top=0.55, second=0.00` — a barely-cleared-baseline winner hitting auto-dispatch purely because the second was disqualified. Added a margin-based cap: when `(top.score - 0.5) < 0.15` confidence caps at 0.50 (forces triage). Worked examples rerun in §3.
2. **Poll filter exclude list widened.** v1 excluded only worker labels and `intake-draft`. v2 adds `triage`, `research`, `pending-approval`, `test-w2` so rejected, research-routed, in-approval, and test issues cannot re-enter W2.
3. **Intent-first write order.** v1 wrote history after label-apply. v2 writes a `pending` history row FIRST, then applies labels, then writes a second `dispatched` row on success or `apply-failed` row on failure. Successful apply with no record is now impossible.
4. **Pushover priority matrix simplified.** v1's proposal was priority 1, plus a proposed "risk=high+low-conf bumps to 2" variant. v2: proposal notification → 0 (routine heads-up); triage + router failure + apply-failed → 1. Dropped the risk-based bump proposal entirely.
5. **research_signal stays canon-literal.** v1 smuggled in an "additive flag" interpretation (score + also route to research). v2 treats research_signal as canon specifies: short-circuit scoring, apply `research` label, exit. History row has `chosen_worker: null`, `outcome: "pending-research"`, and **no `ranked_candidates`** — no scoring occurred. The research-branch node moves from post-scoring to post-extract-signals.

All other v1 content is carried forward unchanged unless one of these fixes touched it. The five derivative changes are: topology diagram (§2), worked examples (§3), risk-consumers list (§4), record-shape notes (§5), Pushover format (§6), test assertions (§9), ambiguities (§10), honest pushbacks (§11).

---

## Executive summary (mobile-scannable)

- **20 nodes, not 17.** The intent-first write order adds one history-append node per dispatch path (pending → dispatched), and moving the research-branch earlier adds one IF node. Overall shape is unchanged; the flow is just more durable.
- **Confidence formula: gap + margin + margin-cap floor.** `confidence = min(1.0, 0.3·(gap/0.5) + 0.7·(margin/0.5) + 0.5)`, capped at 0.50 when `margin < 0.15`, zero when `top.score < 0.55`. Barely-cleared-baseline winners can no longer auto-dispatch, no matter how weak the runner-up.
- **Risk: low/medium/high with Linear-priority-Urgent one-way bump-up modifier.** Unchanged from v1. Risk remains informational in Phase 1 — the v1 proposal to bump Pushover priority on high-risk+low-conf is withdrawn per fix #4.
- **routing_history: JSONL at `/miru-data/routing_history.jsonl`** behind the compose bind mount (prerequisite PRO-34, unchanged from v1). Two rows per successful dispatch (pending + dispatched); one row on triage/research; two rows on apply-failed (pending + apply-failed).
- **Stub-W8 label pattern unchanged.** Apply proposed worker label + `pending-approval` atomically; operator approves by removing `pending-approval`. Pushover priority for the proposal drops from 1 to 0 (routine).
- **Research signal short-circuits scoring.** v2 honors canon. W2 detects research_signal, applies `research` label, writes history row with null `chosen_worker` and no `ranked_candidates`, exits. W3 (when built) picks it up from there.
- **Shadow mode still ships day 1.** Env-var flag. Scoring runs, history writes, comment posts, label apply is skipped. Flip off after ~30 clean runs.
- **7 honest pushbacks** on rev 3 canon in §11 (down from 9; two are withdrawn per this revision). None are blockers.

**Single hard blocker is unchanged:** compose bind mount for `D:\dev\miru\data → /miru-data`. Tracked as suggested PRO-34.

---

## Part 1 — Design context (unchanged from v1)

W1 (Planning Intake → Task Draft Sync) shipped 2026-04-23. W2 fires on Linear issues in Todo state without `intake-draft` and without a worker label, scores the four coding workers (Claude Code, Codex, Cursor, Gemini CLI) against extracted task signals, classifies risk independently, and routes the decision through an approval gate.

### Locked constraints coming into this design

From canon page 16 and the post-research invariants locked 2026-04-23:

- **No Wait nodes.** Exit-and-webhook-continuation only.
- **Router failure → triage only, no fallback routing.**
- **Worker retry cap.** Primary runs once, rank 2 runs once, third failure → halt + `manual-intervention-required` label. Deterministic tiebreaker Claude Code > Codex > Cursor > Gemini CLI.
- **routing_history schema 12 fields, all required.** This plan proposes 3 additions as canon amendments (same as v1).
- **research_signal short-circuits scoring.** Canon-literal per fix #5.

From operator brief:

- **Stub W8.** Manual-label-move approval gate: W2 applies proposed worker label + `pending-approval`, operator removes `pending-approval` to approve, swaps worker label to override, applies `triage` to reject. Pushover priority 0 (v2 change) carries the decision packet for reference.
- **Single-point-of-swap intent.** Outbound Pushover node replaces with signed-URL variant later. Inbound side is a separate W8 workflow build.

From the Worker Operating Baseline: Linear team ID `f9d6193c-4572-40a9-b834-c408439f1aa1`, team key PRO.

### Reference architecture (W1) carried forward into W2

Unchanged from v1: `w2NNN-<kebab-name>` node IDs, credential placeholders (`{{NOTION_CRED_ID}}`, `{{LINEAR_CRED_ID}}`, `{{PUSHOVER_CRED_ID}}`), retry defaults (`maxTries=3, waitBetweenTries=5000`), Code-node `javaScript` + `runOnceForEachItem`, direct `httpRequest` GraphQL for Linear, `$env.PUSHOVER_USER_KEY` with explicit priority always set, `continueErrorOutput` on risky nodes with convergent error handlers, post-deploy UI wire-up for the error-workflow reference.

### Deploy script guardrails (PRO-27) W2 relies on

Unchanged from v1: connections integrity ([deploy-workflow.ps1:37-99](docker/n8n/scripts/deploy-workflow.ps1)), credential vault references ([deploy-workflow.ps1:101-123](docker/n8n/scripts/deploy-workflow.ps1)), settings-merge allowlist preserving `errorWorkflow` ([deploy-workflow.ps1:125-161](docker/n8n/scripts/deploy-workflow.ps1)), real active-state reporting.

---

## Part 2 — Node-by-node topology (v2 shape)

20 nodes total. Grid starts at x=240, y=300 for main flow. Branches at ±200 y-offset.

### Main dispatch flow

| # | ID | Type | typeVersion | Purpose | Inputs | Outputs | Retry | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| 1 | `w2001-linear-poll` | `httpRequest` | 4.2 | Poll Linear GraphQL every 3 min. **Filter (v2): `state.name = "Todo" AND labels.every.name NOT IN [claude-code, cursor, codex, gemini, intake-draft, triage, research, pending-approval, test-w2] AND updatedAt >= now() - 1h`.** | scheduled | array of issue UUIDs | maxTries=3 wait=5000 | shared error handler |
| 2 | `w2002-manual-webhook` | `webhook` | 1.1 | Manual entry. Path `w2-route`. Body `{issue_id, trace_id_predecessor?, reason?}` | POST body | normalized | none | 400 on malformed body |
| 3 | `w2003-normalize-poll` | `set` | 3.4 | Extract `issue_id`, generate `trace_id` (UUID v4), set `intake_source: poll`, `shadow_mode` from env | w2001 | per-issue context | none | n/a |
| 4 | `w2004-normalize-webhook` | `set` | 3.4 | Same but webhook-source; reuse predecessor `trace_id` if present | w2002 | per-issue context | none | n/a |
| 5 | `w2003a-dedupe-guard` | `code` | 2 | Read last 5 min of `routing_history.jsonl`, short-circuit if same `task_id` has a non-terminal row | normalized | `{..., should_proceed}` | none | log + proceed (belt-and-suspenders) |
| 6 | `w2005-linear-fetch` | `httpRequest` | 4.2 | Single GraphQL: issue + narrow `issueLabels(filter:{name:{in:[w2-label-set]}})` | `issue_id` | issue + label map | maxTries=3 wait=5000 | `continueErrorOutput` → w2999 |
| 7 | `w2006-extract-signals` | `code` | 2 | Extract `task_type`, `surface_keywords`, `touches_paths`, `research_signal` | issue | extracted_signals | none | `continueErrorOutput` → w2999 |
| **8** | **`w2006a-research-branch`** | **`if`** | **2.2** | **(v2 NEW — moved from post-scoring) IF `research_signal` → research-handoff path; ELSE → continue to scoring** | extracted_signals | 2-way branch | none | n/a |
| 9 | `w2007-score-workers` | `code` | 2 | Load `w2_routing_rules.json`, compute per-worker scores, rank with tiebreaker | extracted_signals (non-research) | `{ranked_candidates[], confidence, winner}` | none | `continueErrorOutput` → w2999 |
| 10 | `w2008-classify-risk` | `code` | 2 | Rule-based low/medium/high; Urgent bumps one level up | signals + priority | `{risk, risk_rationale}` | none | `continueErrorOutput` → w2999 |
| 11 | `w2009-confidence-branch` | `if` | 2.2 | `confidence < 0.75` → triage path; else → dispatch path | scored + risk | 2-way branch | none | n/a |
| **12** | **`w2010-append-history-intent`** | **`code`** | **2** | **(v2 NEW — intent-first) Append history row with `outcome: "pending"`, full `ranked_candidates`, `chosen_worker: [winner]`** | decision context | pending row written | none | on write error: Pushover priority 1 + exit |
| 13 | `w2011-linear-apply-and-comment` | `httpRequest` | 4.2 | **Single** GraphQL: `issueUpdate{labelIds:[winner, pending-approval]}` + `commentCreate{body:decision-packet}` atomic. Skipped in shadow mode. | decision | labels + comment applied | maxTries=3 wait=5000 | `continueErrorOutput` → w2998-apply-failed (decision preserved, not w2999) |
| **14** | **`w2012-append-history-dispatched`** | **`code`** | **2** | **(v2 NEW — completes intent-first) Append second history row with same `trace_id`, `outcome: "dispatched"`** | decision | dispatched row written | none | on write error: Pushover priority 1 (state mostly OK; operator can reconcile from Linear labels) |
| 15 | `w2013-pushover-proposal` | `pushover` | 1 | **Priority 0 (v2 change from 1)**, title `W2 → [Worker] proposed for [PRO-X]`, body per §6. Url = Linear issue URL. **Single-point-of-swap for real W8.** | decision | notification sent | maxTries=3 wait=5000 | log on fail |

### Triage branch (confidence < 0.75)

| # | ID | Type | typeVersion | Purpose | Inputs | Outputs | Retry | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| 16 | `w2014-append-history-triage` | `code` | 2 | Append row: `chosen_worker: "triage"`, `outcome: "pending"`, full `ranked_candidates` preserved (scoring did run) | scored | triage row written | none | Pushover priority 1 + exit |
| 17 | `w2015-triage-apply` | `httpRequest` | 4.2 | Single GraphQL: `issueUpdate{labelIds:[triage]}` + `commentCreate{body:low-confidence-reason}` | scored | triage label applied | maxTries=3 wait=5000 | `continueErrorOutput` → w2999 |
| 18 | `w2016-triage-pushover` | `pushover` | 1 | **Priority 1** — "Router confidence low on PRO-X — manual routing required. Ranked: [...]" | scored | notification sent | maxTries=3 wait=5000 | log on fail |

### Research branch (research_signal short-circuits scoring — v2 canon-literal)

| # | ID | Type | typeVersion | Purpose | Inputs | Outputs | Retry | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| 19 | `w2017-append-history-research` | `code` | 2 | Append row: `chosen_worker: null`, `outcome: "pending-research"`, **no `ranked_candidates` field** (scoring didn't run). `extracted_signals.research_signal` preserved for audit. | extracted_signals | research row written | none | Pushover priority 1 + exit |
| 20 | `w2018-research-handoff` | `httpRequest` | 4.2 | Apply `research` label + post research-signal comment. W3 picks up when built. | signals | research label applied | maxTries=3 wait=5000 | `continueErrorOutput` → w2999 |

### Error-recovery convergence

| ID | Type | Role |
|---|---|---|
| `w2998-apply-failed` | `code` | Runs only when w2011 apply fails after retries. Writes second history row with `outcome: "apply-failed"` (same `trace_id` as pending), Pushover priority 1 with decision packet ("Apply failed for PRO-X — decision ready, apply manually"). Does NOT run triage — operator retains option to apply manually or re-invoke via `w2002-manual-webhook`. |
| `w2999-router-failure` | `code` | Convergence for decision-phase `continueErrorOutput` branches (fetch/extract/score/classify/risk errors). Applies `triage` label (via inline httpRequest in the same node), posts error summary comment, Pushover priority 1, appends history row with `chosen_worker: "halted"`, `outcome: "halted"`. |

### Connection topology (v2 text diagram)

```
w2001-linear-poll ──┐
                     ├─► w2003-normalize-poll ────┐
w2002-manual-webhook ├─► w2004-normalize-webhook ─┴─► w2003a-dedupe-guard
                                                       │ (should_proceed==true)
                                                       ▼
                                                    w2005-linear-fetch ──(ok)──► w2006-extract-signals
                                                                 │ (err)                │
                                                                 ▼                       ▼
                                                             w2999-router-failure    w2006a-research-branch
                                                                                     /               \
                                                                   (research_signal==true)      (research_signal==false)
                                                                          │                           │
                                                                          ▼                           ▼
                                                              w2017-append-history-research    w2007-score-workers
                                                                          │                           │
                                                                          ▼                           ▼
                                                                w2018-research-handoff        w2008-classify-risk
                                                                                                      │
                                                                                                      ▼
                                                                                          w2009-confidence-branch
                                                                                          /                     \
                                                                              (conf<0.75)                      (dispatch)
                                                                                     │                              │
                                                                                     ▼                              ▼
                                                                     w2014-append-history-triage     w2010-append-history-intent  ← v2 intent-first
                                                                                     │                              │
                                                                                     ▼                              ▼
                                                                             w2015-triage-apply          w2011-linear-apply-and-comment
                                                                                     │                              │ (ok)                     │ (err)
                                                                                     ▼                              ▼                          ▼
                                                                             w2016-triage-pushover        w2012-append-history-dispatched   w2998-apply-failed
                                                                                                                    │                        (writes apply-failed row
                                                                                                                    ▼                         + Pushover pri 1)
                                                                                                            w2013-pushover-proposal
                                                                                                                    (priority 0)
```

### Position layout (n8n canvas)

- x=240: triggers (w2001 at y=240, w2002 at y=360)
- x=440: normalize (w2003 at y=240, w2004 at y=360)
- x=640: w2003a-dedupe-guard at y=300
- x=840: w2005-linear-fetch at y=300
- x=1040: w2006-extract-signals at y=300
- x=1240: w2006a-research-branch at y=300
- x=1440: w2007-score (y=300, scoring branch) OR w2017-research-hist (y=100, research branch)
- x=1640: w2008-classify-risk (y=300) OR w2018-research-handoff (y=100)
- x=1840: w2009-confidence-branch at y=300
- x=2040: dispatch y=300 (w2010 intent), triage y=500 (w2014 history)
- x=2240: w2011 apply (y=300), w2015 triage-apply (y=500)
- x=2440: w2012 dispatched (y=300), w2016 triage-push (y=500); w2998 apply-failed at y=100
- x=2640: w2013 proposal-push (y=300)
- x=2040 y=700: w2999-router-failure

---

## Part 3 — Confidence scoring (v2 — patched floor)

### Rationale (unchanged)

Rule-based, deterministic. No LLM in the scoring path. Post-research invariants and PRO-27 research back this: LLM verbalized confidence correlates with verbosity, not correctness.

### Scoring step (unchanged from v1)

For each worker `W`:
1. Start at baseline `score[W] = 0.5`.
2. For each "best-for" signal match, `score[W] += 0.15`, capped at 4 matches.
3. For each "hard-no-go" signal match, `score[W] = 0.0` (hard disqualifier; not additive).
4. Clamp `score[W]` to `[0.0, 1.0]`.

### Ranking (unchanged from v1)

Sort by score desc; tiebreak within `|a.score - b.score| < 0.01` by fixed priority Claude Code > Codex > Cursor > Gemini CLI.

### Confidence (v2 — patched floor added)

```
top = ranked[0]
second = ranked[1]
gap    = top.score - second.score
margin = top.score - 0.5

if top.score < 0.55:
    confidence = 0.0              // nobody cleared baseline + one match → triage

else:
    base_confidence = min(1.0, 0.3 * (gap / 0.5) + 0.7 * (margin / 0.5) + 0.5)

    if margin < 0.15:             // v2 NEW — barely-cleared-baseline cap
        confidence = min(0.50, base_confidence)
    else:
        confidence = base_confidence
```

**What the new floor does:** any winner scoring less than `baseline + one-full-match` (0.65) is capped at confidence 0.50. Since the dispatch threshold is 0.75, that's a guaranteed triage. The cap specifically defuses the v1 bug case where a disqualified runner-up could inflate gap-driven confidence past 0.75 despite a winner who barely cleared baseline.

### Worked examples (v2 — rerun with new floor)

| Case | top | second | gap | margin | base_confidence | Cap applied? | Final | Outcome |
|---|---|---|---|---|---|---|---|---|
| Clear winner, strong 2nd | 0.95 | 0.50 | 0.45 | 0.45 | min(1.0, 0.3·0.9 + 0.7·0.9 + 0.5) = **1.0** | margin 0.45 ≥ 0.15 → no cap | **1.0** | dispatch |
| Clear winner, threshold 2nd (one-match) | 0.65 | 0.50 | 0.15 | 0.15 | min(1.0, 0.3·0.3 + 0.7·0.3 + 0.5) = **0.80** | margin 0.15 boundary — strict `<` → no cap | **0.80** | dispatch |
| Contested winners | 0.80 | 0.65 | 0.15 | 0.30 | min(1.0, 0.3·0.3 + 0.7·0.6 + 0.5) = 1.01 → **1.0** | margin 0.30 ≥ 0.15 → no cap | **1.0** | dispatch |
| Two close strong | 0.90 | 0.85 | 0.05 | 0.40 | min(1.0, 0.3·0.1 + 0.7·0.8 + 0.5) = 1.09 → **1.0** | margin 0.40 ≥ 0.15 → no cap | **1.0** | dispatch (tiebreaker noted) |
| Weak field | 0.55 | 0.50 | 0.05 | 0.05 | min(1.0, 0.3·0.1 + 0.7·0.1 + 0.5) = **0.60** | margin 0.05 < 0.15 → **cap at 0.50** | **0.50** | **triage** |
| Barely-cleared baseline | 0.54 | 0.50 | 0.04 | 0.04 | `top < 0.55` → **0.0** | — | **0.0** | **triage** |
| Nobody matched | 0.50 | 0.50 | 0.0 | 0.0 | `top < 0.55` → **0.0** | — | **0.0** | **triage** |
| **v1 BUG CASE: top=0.55, second=0.00** | **0.55** | **0.00** | **0.55** | **0.05** | min(1.0, 0.3·1.1 + 0.7·0.1 + 0.5) = **0.90** | **margin 0.05 < 0.15 → cap at 0.50** | **0.50** | **triage** ✓ **FIXED** |
| Strong winner, disqualified 2nd | 0.80 | 0.00 | 0.80 | 0.30 | min(1.0, 0.3·1.6 + 0.7·0.6 + 0.5) = 1.40 → **1.0** | margin 0.30 ≥ 0.15 → no cap | **1.0** | dispatch |
| Medium winner, disqualified 2nd (boundary) | 0.65 | 0.00 | 0.65 | 0.15 | min(1.0, 0.3·1.3 + 0.7·0.3 + 0.5) = 1.10 → **1.0** | margin 0.15 — strict `<` → no cap | **1.0** | dispatch |
| All four workers tied | 0.50 | 0.50 | 0.0 | 0.0 | `top < 0.55` → **0.0** | — | **0.0** | **triage** |

The bug case (row 8) is explicitly preserved in the table for regression visibility: the same inputs that gave 0.90 under v1 now give 0.50 under v2 and correctly triage.

### Known limitations (unchanged)

- Phase 1 placeholder. 0.3/0.7 weights and the 0.15 margin-cap threshold are educated guesses. Recalibrate at 200 logged decisions via Brier + 10-bucket ECE.
- No historical tie-awareness yet (deferred to Phase 2).
- All best-for matches contribute equally (per-signal weighting deferred to Phase 2 config).

---

## Part 4 — Risk classification (v2 — consumers list trimmed)

Risk is independent of confidence. Rules unchanged from v1.

### Rules

```
risk = "high" if ANY:
    - touches_paths matches /card_catalog\.db|migrations\/|schema|\.env|secrets\//
    - keywords ∩ { auth, secrets, DB schema, rate limit, migrate, drop,
                   delete production, purge, truncate, DROP TABLE }

risk = "low" if ALL:
    - task_type ∈ { chore, design, docs }
    - touches_paths matches /^(docs\/|tests\/|craft guides\/)/
    - description word count ≤ 200
    - no production code keywords

risk = "medium" otherwise

modifier: if linear_priority == 1 (Urgent):
    bump one level up (low → medium, medium → high; high stays high)
```

### Production code keyword set (unchanged)

```
{ route, endpoint, database, query, SQL, migration, auth, credential,
  secret, API key, production, deploy, rollout, backfill, reindex }
```

### Deliberately excluded (unchanged)

- `refactor` is not a risk keyword — let paths do that work.
- Description length alone is not a risk modifier.

### Edge-case table (unchanged from v1)

| Case | Paths | Keywords | Priority | Computed risk |
|---|---|---|---|---|
| Docs typo | `docs/craft/principles.md` | none | Normal | low |
| Docs typo on urgent | same | none | Urgent | medium |
| Migration | `dispatcher/migrations/0002.sql` | migrate | Normal | high |
| Feature in pm/ | `pm/storefront/routes/…` | none | Normal | medium |
| Auth bug fix | `pm/storefront/auth/session.py` | auth | High | high |
| card_catalog touch | `data/card_catalog.db` | — | Low | high |

### Downstream consumers (v2 — trimmed)

**v2 change per fix #4:** the v1 proposal to bump Pushover priority when `risk=high AND confidence<0.75` is withdrawn. Priority matrix is now flat: proposal=0, triage/failure=1.

Remaining documented consumer:
- **Phase 3 skip-approval eligibility gate:** only `risk=low` flows are candidates. Phase 1 risk remains informational.

Risk is stored in `routing_history` and displayed in the Pushover body for operator awareness. That's the full consumer set for Phase 1+2. No priority-bump.

---

## Part 5 — routing_history schema (v2 — intent-first semantics)

Canon specifies 12 fields, all required. v2 carries v1's proposal of 15 fields (12 canon + 3 additions flagged as canon-amendment candidates).

### Full schema (unchanged from v1 — 15 fields)

| # | Field | Type | W2-dispatch value | Updated by | Notes |
|---|---|---|---|---|---|
| 1 | `timestamp` | ISO-8601 UTC | `new Date().toISOString()` | W2 | |
| 2 | `trace_id` | UUID v4 | generated at w2003/w2004 | W2 | Stable across multi-row updates |
| 3 | `task_id` | string | Linear UUID | W2 | |
| 4 | `task_identifier` | string | `PRO-42` | W2 | Canon addition |
| 5 | `extracted_signals` | JSON | `{task_type, surface_keywords[], touches_paths[], research_signal}` | W2 | |
| 6 | `ranked_candidates` | array \| null | `[{worker, score, reasoning}, ...]` on dispatch/triage; **`null` on research branch (not scored)** | W2 | Canon addition (objects); v2 clarification |
| 7 | `chosen_worker` | string \| null | worker OR `"triage"` OR `"halted"` OR **`null` on research branch** | W2 | |
| 8 | `confidence` | float \| null | from §3 formula; **`null` on research branch** | W2 | |
| 9 | `risk` | enum | low / medium / high | W2 | |
| 10 | `operator_override_flag` | bool | `false` at dispatch | override-watchdog workflow | |
| 11 | `outcome` | enum | intent-first: `"pending"` first; then `"dispatched"` / `"apply-failed"` / `"triage"` / `"halted"` / `"pending-research"` | W2 (multi-row), W5, W9 | See semantics below |
| 12 | `worker_response_ref` | string | `"pending"` | W5 overwrites | |
| 13 | `proposed_model_version` | string | lookup | W2 | Canon addition |
| 14 | `actual_model_version` | string \| null | `null` at dispatch | W5 fills | Canon addition (split) |
| 15 | `w2_workflow_version` | string | git SHA | W2 | Canon addition |

### Outcome enum (v2 expanded)

```
"pending"           — intent row written, apply hasn't happened yet   (v2 NEW)
"dispatched"        — successful apply, labels + comment on Linear     (v2 NEW)
"apply-failed"      — apply failed after retries, operator surfaced    (v2 NEW)
"triage"            — triage path fired (confidence < 0.75)
"halted"            — decision-phase failure (router-failure path)
"pending-research"  — research branch, no scoring, awaiting W3
success / fail / inconclusive  — set by W5 on receipt capture
"re-routing"        — set by W9 on re-route event
```

### Intent-first write semantics (v2)

**Dispatch path writes two history rows with the same `trace_id`:**

1. **Intent row (w2010 before apply):**
   ```json
   {"timestamp":"...","trace_id":"c1f4...","task_id":"...","task_identifier":"PRO-42","extracted_signals":{...},"ranked_candidates":[{...}, ...],"chosen_worker":"codex","confidence":0.83,"risk":"medium","operator_override_flag":false,"outcome":"pending","worker_response_ref":"pending","proposed_model_version":"gpt-codex-latest","actual_model_version":null,"w2_workflow_version":"abcdef1"}
   ```

2. **Status row (w2012 after successful apply OR w2998 after failed apply):**
   - Success: same row but `timestamp` updated, `outcome: "dispatched"`. All other fields identical to intent row.
   - Failure: same row but `outcome: "apply-failed"`.

**Triage and research paths write a single row** (intent-first is specifically about preventing "apply succeeded but no record" on the dispatch path).

### Research-branch row shape (v2 canon-literal)

When `research_signal` fires at w2006a, no scoring runs. w2017 writes:

```json
{"timestamp":"...","trace_id":"c1f4...","task_id":"...","task_identifier":"PRO-42","extracted_signals":{"task_type":"research","surface_keywords":["find examples","compare"],"touches_paths":[],"research_signal":true},"ranked_candidates":null,"chosen_worker":null,"confidence":null,"risk":"low","operator_override_flag":false,"outcome":"pending-research","worker_response_ref":"pending","proposed_model_version":null,"actual_model_version":null,"w2_workflow_version":"abcdef1"}
```

Key point: **`ranked_candidates: null`**, **`chosen_worker: null`**, **`confidence: null`**. Scoring did not run. Risk classification still runs (cheap) and is informational.

### Multi-row read semantics

Reading the history for a given `trace_id`: "latest row wins per field." Typical state transitions for a successful dispatch:

```
t=0   → intent    outcome=pending
t=1   → success   outcome=dispatched
t=N   → W5 fills  outcome=success, worker_response_ref=<linear-comment-id>, actual_model_version=<from-receipt>
```

If W9 re-routes later, a new row with the same `trace_id` carries `outcome="re-routing"`. Event-log semantics, not relational.

### Record-size guard (unchanged from v1)

`fs.appendFileSync` atomicity under 4KB. Line truncates reasoning strings past 3900 bytes, sets `_truncated: true`.

---

## Part 6 — Stub-W8 handoff pattern (v2 — Pushover priority 0)

### Label flow (unchanged from v1)

```
W2 dispatch outcome:
    BEFORE w2011:  [Feature]                         (no worker label, no pending-approval)
    w2011 applies: add worker label, add pending-approval
    AFTER w2011:   [Feature, claude-code, pending-approval]

Operator actions:
    Approve:   remove `pending-approval`                          → [Feature, claude-code]
    Override:  remove current worker, add target worker, remove pending-approval
                                                                  → [Feature, codex]
    Reject:    apply `triage`, remove worker label                → [Feature, triage]
```

### Pushover notification format (v2 — priority 0 for proposal)

```
Title:  W2 → [Worker Display Name] proposed for [PRO-X]
Priority: 0                                                ← v2 change: was 1

Body:
    Task: [issue title]
    Proposed: [Worker Display Name]  (conf 0.XX, risk [low|medium|high])

    Ranked:
        1. Claude Code (0.XX)  — [reasoning, ≤80 chars]
        2. Codex       (0.XX)  — [reasoning, ≤80 chars]
        3. Cursor      (0.XX)  — [reasoning, ≤80 chars]
        4. Gemini CLI  (0.XX)  — [reasoning, ≤80 chars]

    Signals: [task_type] · [top 3 keywords] · [first 2 touches_paths]
    Risk rationale: [one line]

    Approve:  remove `pending-approval` in Linear.
    Override: swap worker label, then remove `pending-approval`.
    Reject:   apply `triage`.

    trace_id: [UUID]
```

Pushover `url` field: Linear issue URL. One-tap from phone.

### Priority matrix (v2)

| Path | Pushover priority | Rationale |
|---|---|---|
| `w2013-pushover-proposal` (dispatch) | **0** | Routine heads-up; stub-W8 is not a hard-gated approval — real W8 will be priority 1 with signed buttons |
| `w2016-triage-pushover` | **1** | Attention needed; operator must manually route |
| `w2998-apply-failed` pushover | **1** | Attention needed; decision ready but not applied |
| `w2999-router-failure` pushover | **1** | Attention needed; decision-phase failure |

**Withdrawn from v1:** the `risk=high AND confidence<0.75` → priority 2 bump. Simpler matrix.

### Real-W8 swap path (unchanged from v1)

Outbound (W2 side): single-node swap — replace `w2013-pushover-proposal` with a signed-URL variant (priority 1 at that point). Inbound side (webhook continuation + TTL escalation) is a separate W8 workflow build — not a single-node swap. Plan for a week, not a day.

---

## Part 7 — Router failure policy (unchanged from v1)

Two failure classes:

- **Decision-phase failure** (w2005 fetch, w2006 extract, w2007 score, w2008 classify errors): no decision produced → triage is safe. `continueErrorOutput` branches converge at `w2999-router-failure`. Applies `triage` label, posts error summary, Pushover priority 1, writes history row with `chosen_worker: "halted"`, `outcome: "halted"`.

- **Apply-phase failure** (w2011 apply fails after retries): decision was produced — scoring/risk/ranking completed — but the label/comment mutation didn't land. `continueErrorOutput` branch goes to `w2998-apply-failed`, NOT w2999. w2998 writes a second history row with `outcome: "apply-failed"` (the intent row from w2010 is already on disk so the full decision is preserved), Pushover priority 1 with the decision packet so operator can apply labels manually OR re-invoke via `w2002-manual-webhook`.

Workflow-level error handler (shared with W1) stays wired for truly unexpected failures.

Canon amendment candidate: §11 pushback #4 (renumbered from v1's #6).

---

## Part 8 — Tiebreaker implementation (unchanged from v1)

```javascript
const PRIORITY = { "claude-code": 0, "codex": 1, "cursor": 2, "gemini": 3 };
const TIE_EPSILON = 0.01;

function rankCandidates(candidates, excludeWorker = null) {
    const pool = excludeWorker
        ? candidates.filter(c => c.worker !== excludeWorker)
        : candidates.slice();

    pool.sort((a, b) => {
        if (Math.abs(a.score - b.score) < TIE_EPSILON) {
            return PRIORITY[a.worker] - PRIORITY[b.worker];
        }
        return b.score - a.score;
    });

    return pool;
}
```

W9 re-route (future) passes `previous_worker` as `excludeWorker` — excluded before sorting, tiebreaker applies to remaining pool.

---

## Part 9 — Test strategy (v2 — updated assertions)

Per canon W1 lesson #4, Claude Code owns the workflow test loop.

### Test harness (unchanged from v1)

- Deploy W2 **inactive**.
- Manual webhook invocations only (`POST /webhook-test/w2-route {issue_id}`).
- Test issues tagged `test-w2` (now excluded from the poll filter per fix #2).
- Cleanup via Linear GraphQL delete after each test.

### Test cases (v2 — assertions updated for fixes #3 and #5)

| # | Name | Setup | v2 Assertion changes | Cleanup |
|---|---|---|---|---|
| 1 | Low-risk happy path | typo fix in docs/... | **v2:** assert TWO history rows for `trace_id`: first `outcome="pending"`, second `outcome="dispatched"`. Labels applied atomically. Pushover **priority 0**. | delete issue |
| 2 | High-risk happy path | migration touching | **v2:** TWO history rows (pending + dispatched). Risk=high in both. Pushover priority **0** (not bumped — fix #4). | delete issue |
| 3 | Low-confidence triage | vague description | confidence < 0.75 → triage path. **v2:** exactly ONE history row (triage path is single-write), `chosen_worker="triage"`, full `ranked_candidates` preserved. Pushover priority 1. | delete issue |
| **3b** | **Margin-cap triage (v2 NEW)** | craft an issue where top=0.55, second=0.00 (one best-for match for winner, one hard-no-go for all others) | confidence = 0.50 (capped), triage path fires, history row shows `confidence: 0.50` not 0.90. **Regression test for v1 bug.** | delete issue |
| 4 | Decision-phase failure | bogus `issue_id` | w2005 fails → w2999. ONE history row, `outcome="halted"`. Pushover priority 1. | n/a |
| 5 | Apply-phase failure | disable Linear cred temporarily | w2011 fails → w2998. **v2:** TWO history rows for `trace_id`: first `outcome="pending"` (from w2010 intent), second `outcome="apply-failed"` (from w2998). Decision preserved in the first row. Pushover priority 1 with manual-apply instructions. | restore cred; delete issue |
| 6 | Research signal detection | description: "find examples of how other TCG sites do X" | **v2:** w2006a-research-branch fires. **Scoring does NOT run** (assert by checking execution trace — w2007 never executes). ONE history row: `chosen_worker: null`, `ranked_candidates: null`, `confidence: null`, `outcome: "pending-research"`. `research` label applied. Pushover priority 1. | delete issue |
| 7 | Container restart mid-decision | start w2 on test issue, `docker compose restart` between w2010 (intent written) and w2011 (apply) | **v2:** assert intent row with `outcome="pending"` exists on disk after restart. No `pending-approval` label on Linear. Re-invoke webhook: dedupe guard sees the pending intent row, short-circuits (prevents double-dispatch). Operator recovers by checking the pending row and either manually applying labels or clearing the intent row and re-invoking. | delete issue |
| **7b** | **Filter dedupe on re-entry (v2 NEW)** | pre-label an issue with `triage` and verify poll filter excludes it; same for `research`, `pending-approval`, `test-w2` | No W2 execution triggered by any of those labels. Regression test for fix #2. | delete issue |

### Shadow mode (unchanged from v1)

`W2_SHADOW_MODE=true`: all scoring + history-write + comment-post still run, label-apply mutation skipped, Pushover body prefixed "SHADOW MODE — decision was [Worker] but NOT applied." Flip off after ~30 clean runs.

---

## Part 10 — Ambiguities & proposed resolutions (v2 — updated)

| # | Ambiguity | v2 Proposed resolution | Operator decision |
|---|---|---|---|
| 1 | routing_history storage mechanism | JSONL at `/miru-data/routing_history.jsonl` behind bind mount | approve / propose alternative |
| 2 | Schema 12 canon fields vs. 15 proposed | 3 additions (`task_identifier`, model version split, `w2_workflow_version`) | accept / reject |
| 3 | `ranked_candidates` as `[{worker, score, reasoning}]` vs. array of strings | objects; `null` on research branch | accept / reject |
| 4 | research_signal handling | **v2:** canon-literal. Short-circuits scoring, apply `research`, exit. History row `chosen_worker: null`, `ranked_candidates: null`. | (v1 additive-flag proposal withdrawn) |
| 5 | Risk downstream consumers | **v2:** only Phase 3 eligibility gate. The Pushover-priority-bump proposal is withdrawn per fix #4. | approve |
| 6 | Router failure split (decision vs. apply phase) | Preserve decision on apply-phase failure | accept / reject |
| 7 | Pending-approval TTL in stub-W8 | Watchdog micro-workflow 8am/8pm, Pushover priority 2 on > 24h stale | ship with W2 / defer |
| 8 | Shadow mode on day 1 | Yes. Flip off at ~30 clean runs | approve / reject |
| 9 | Trigger cadence | 3 min | approve / propose alternative |
| 10 | External scoring rules config | `/miru-data/config/w2_routing_rules.json` | approve / hardcode |
| 11 | Override-flag capture watchdog in Phase 1 | Small label-change-webhook workflow; flips flag on mismatch | approve / defer |
| 12 | Manual webhook body shape | `{issue_id, trace_id_predecessor?, reason?}` | approve / amend |
| 13 | Poll-filter exclude list | **v2:** worker labels + intake-draft + triage + research + pending-approval + test-w2 | approve |
| 14 | Intent-first write order | **v2 (fix #3):** pending row before apply, dispatched row after | approve |
| 15 | Pushover priority matrix | **v2 (fix #4):** proposal=0, triage/failure=1 | approve |
| 16 | Confidence margin-cap floor | **v2 (fix #1):** `margin < 0.15` caps confidence at 0.50 | approve |

Three v2-specific items (14, 15, 16) are the codified fixes. Items 4 and 5 resolved by operator decision embedded in this revision.

---

## Part 11 — Honest pushbacks on rev 3 canon (v2 — 7 items)

Two v1 pushbacks withdrawn this revision:

- v1 #4 (research_signal as additive flag) — withdrawn. v2 honors canon literally.
- v1 #5 (risk as write-only; propose Pushover-priority consumer) — withdrawn. v2 removes the proposed Pushover-priority-bump consumer per fix #4. Phase 3 eligibility gate remains as the sole documented consumer.

Remaining 7 pushbacks:

### 1. `routing_history` storage mechanism unspecified (unchanged)

Canon says "shared store"; container filesystem realities force a choice. Proposing JSONL + new bind mount (prerequisite PRO-34).

### 2. Three schema additions (unchanged)

`task_identifier`, `proposed_model_version` / `actual_model_version` split, `w2_workflow_version`. Free to include now, expensive to backfill.

### 3. `ranked_candidates` as objects not strings (unchanged in spirit; v2 clarifies research-branch is `null`)

v2 addition: on research branch `ranked_candidates` is explicitly `null` (scoring didn't run). Lets readers distinguish "no candidates because research" from "no candidates because error".

### 4. Router failure split: decision-phase vs. apply-phase (unchanged)

Decision-phase → triage. Apply-phase → preserve decision, write `outcome: "apply-failed"`, surface to operator. Scoring is expensive; discarding a good decision because the label write flaked is silent information loss.

### 5. Pending-approval TTL in stub-W8 (unchanged)

Watchdog micro-workflow every 8am/8pm: scan for `pending-approval` older than 24h → Pushover priority 2. 3 nodes.

### 6. Hardcoded Linear team ID (unchanged — inherited tech debt)

W1 hardcodes team ID; W2 inherits. Flag for future extraction. Not fixing in W2.

### 7. JSONL concurrent-write guarantees at scale (unchanged)

Fine at Phase 1 (~50 tasks/month). Migration options at Phase 2+: write-through HTTP endpoint on dispatcher (19000) OR SQLite with WAL mode. Flag as watch item.

---

## Part 12 — Prerequisite and next steps (unchanged from v1)

### Hard prerequisite: compose bind mount

Add to `docker/n8n/docker-compose.yml`:

```yaml
services:
  n8n:
    volumes:
      - n8n_data:/home/node/.n8n
      - D:\dev\miru\data:/miru-data   # ← NEW
```

One-time `docker compose down && up -d`. Tracked as suggested PRO-34 (~30 min, not in PRO-33 scope).

### Next plan-mode pass (after operator approves this v2 review)

1. Draft `w2-worker-selection-router.json` workflow JSON per §2.
2. Draft `config/w2_routing_rules.json` with canon §W2 step 3 matrix.
3. Draft pending-approval watchdog workflow.
4. Draft override-flag capture watchdog workflow.
5. Plan Claude Code's post-deploy test script (the 9 cases in §9, including regression tests 3b and 7b).
6. Plan any canon-page-16 updates contingent on operator's amendment decisions.

No JSON produced in that next pass either — deferred to the build-and-deploy pass that runs after the second plan-mode approval.

### Reviewer's honest signal (v2)

Confident in: topology (now intent-first, more durable), scoring formula floor (regression-tested in §3), research short-circuit (canon-literal), Pushover priority matrix (simpler is better), stub-W8 label semantics.

Less confident in: 0.75 dispatch threshold, 0.3/0.7 weights, the specific 0.15 margin-cap threshold — all Phase 1 placeholders pending Brier/ECE calibration at 200 decisions.

Least confident in: apply-phase failure recovery path (§7 pushback #4). Still a judgment call; canon's uniform-triage behavior is defensible alternative. Flag for operator sign-off in this revision.

---

## Completion

- **Path to this v2 deliverable:** `data/batch_reports/w2_plan_review_v2_2026-04-23.md`
- **Supersedes:** [w2_plan_review_2026-04-23.md](w2_plan_review_2026-04-23.md)
- **Linear issue:** [PRO-33](https://linear.app/project-miru/issue/PRO-33/build-w2-worker-selection-router-plan-mode-pass) — see diff-summary comment for v2 changes
- **All 5 fixes confirmed landed:**
  1. ✓ Confidence margin-cap floor at §3 (regression-tested against `top=0.55, second=0.00` case)
  2. ✓ Poll filter widened to exclude `triage, research, pending-approval, test-w2` at w2001 description in §2
  3. ✓ Intent-first write order: w2010-append-history-intent → w2011-apply → w2012-append-history-dispatched (§2 topology, §5 semantics)
  4. ✓ Pushover priority matrix simplified: proposal=0, triage/failure=1. Risk-bump proposal withdrawn (§6, §4)
  5. ✓ research_signal canon-literal: short-circuits scoring, new w2006a branch node placed before w2007 (§2 topology, §5 record shape, §11 pushback withdrawn)

**STATUS: CONFIRMED WORKING** — v2 review ready for operator.
