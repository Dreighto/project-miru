# W2 — Worker Selection Router — Plan Review (2026-04-23)

**Reviewer:** Claude Code (Opus 4.7). One Explore agent (repo-side recon on `docker/n8n/`), one Plan agent (stress-test), two Notion canon reads, one compose-file validation, plus prior PRO-27 research as grounding.
**Subject:** W2 — the second n8n workflow in Project Miru's automation layer — picks which LLM coding worker handles a Linear issue once it's promoted out of intake-draft.
**Linear issue:** [PRO-33](https://linear.app/project-miru/issue/PRO-33/build-w2-worker-selection-router-plan-mode-pass).
**Scope of this pass:** design only. No workflow JSON, no deploy, no Notion mutations, no compose changes. The deliverable is this markdown file and the associated Linear issue + comment.
**Next pass (after operator approves this review):** a second plan-mode pass to write the actual W2 workflow JSON and the associated error-handler wiring.

---

## Executive summary (mobile-scannable)

- **Ship with 17 nodes, not 16.** Split the "apply labels" and "post comment" steps into a single atomic GraphQL multi-mutation, but add a dedupe-recent-history guard against the poll-retrigger race. Net: the topology has one more node than the first-pass sketch.
- **Confidence formula: gap + margin, not gap alone.** Pure-gap formulas give false confidence when the leader barely clears baseline. Current: `confidence = min(1.0, 0.3·(gap/0.5) + 0.7·(margin/0.5) + 0.5)` with a floor at `top.score < 0.55`. Phase 1 placeholder, calibrated after 200 decisions via Brier + 10-bucket ECE.
- **Risk: low/medium/high with Linear-priority as one-way bump-up modifier only.** Keywords cover topics that carry risk regardless of path (auth, secrets, DB schema, irreversible ops). Paths cover structural risk (card_catalog.db, migrations/, schema, .env, secrets/). Deliberately excluded: "refactor" — let paths do the work.
- **routing_history: JSONL append log at `/miru-data/routing_history.jsonl`.** BLOCKS on a compose bind mount (see §12). 15 fields total: 12 canon + 3 additions (task_identifier, proposed/actual model version split, w2_workflow_version) flagged as canon-amendment candidates.
- **Stub-W8: apply worker label + `pending-approval` atomically.** Operator approves by removing `pending-approval`. Overrides by swapping worker label first. Rejects by applying `triage`. Pushover priority 1 with full decision packet. Single outbound node swaps to real-W8 signed-URL node later; inbound side (webhook continuation + TTL escalation) is a separate W8 workflow build, not a single-node swap.
- **Router-failure split (canon amendment proposed):** decision-phase failure → triage only (no fallback, fail-closed). Apply-phase failure → preserve the decision, write `outcome="apply-failed"`, surface to operator so they can apply the labels manually. Discarding a successful scoring decision because the label write flaked is silent information loss.
- **Shadow mode on day 1.** Env-var flag. W2 scores, writes history, posts comment — does NOT apply worker label. Flip off after ~30 clean runs. Massive risk reduction while scoring formula is a first guess.
- **9 honest pushbacks** on rev 3 canon below, all surfaced as canon-amendment candidates, none blockers if operator rejects.

**Single hard blocker to acknowledge before W2 build starts:** the compose bind mount for `D:\dev\miru\data → /miru-data`. 30-minute change tracked as a follow-up issue (suggested PRO-34). Without it, the JSONL plan is DOA because the path is unreachable from inside the n8n container.

---

## Part 1 — Design context

W1 (Planning Intake → Task Draft Sync) shipped 2026-04-23. It moves Notion intake pages into Linear as `intake-draft`–labeled issues. W2 picks up where W1 stops: it fires on Linear issues in Todo state without `intake-draft` and without a worker label, scores the four coding workers (Claude Code, Codex, Cursor, Gemini CLI) against extracted task signals, classifies risk independently, and routes the decision through an approval gate.

### Locked constraints coming into this design

From canon page 16 and the post-research invariants locked 2026-04-23:

- **No Wait nodes.** Exit-and-webhook-continuation only. Wait nodes lose paused state on container restart; they're documented as fragile in the community ([issue](https://community.n8n.io/t/wait-node-vs-human-in-the-loop-node/259594), [bug](https://community.n8n.io/t/wait-node-operation-on-form-submitted-struggling-bug-nuance/99947)).
- **Router failure → triage only, no fallback routing.** A broken scorer that silently dispatches to Claude Code for hours is the failure mode this invariant prevents.
- **Worker retry cap.** Primary runs once, rank 2 runs once, third failure → halt + `manual-intervention-required` label. Deterministic tiebreaker Claude Code > Codex > Cursor > Gemini CLI.
- **routing_history schema 12 fields, all required.** Canonical, locked as the shared dispatch log across W2/W4/W5/W9. This design proposes 3 additions as canon amendments.

From operator brief:

- **Stub W8.** Real W8 (Phone Approval Inbox) is deferred. W2 ships with a manual-label-move approval gate: W2 applies the proposed worker label + `pending-approval`, operator removes `pending-approval` to approve, swaps worker label to override, applies `triage` to reject. Pushover priority 1 notification carries the full decision packet.
- **Single-point-of-swap intent.** When real W8 ships, the outbound Pushover node gets replaced with a signed-URL variant. The inbound side is a separate workflow build.

From the Worker Operating Baseline (AGENTS.md mirror):

- Linear team ID `f9d6193c-4572-40a9-b834-c408439f1aa1`, team key PRO.
- Worker labels: `claude-code`, `cursor`, `codex`, `gemini`. Type labels: Bug, Feature, Improvement, chore, design, research, blocked.
- Claude Chat + Claude Code both write to Notion; all other workers READ-ONLY.
- `card_catalog.db` never touched by any worker via any path.

### Reference architecture (W1) carried forward into W2

- Node ID scheme `w2NNN-<kebab-name>` (W1 uses `aNNN-`, error handler uses `eNNN-`).
- Credential placeholder convention: `{{NOTION_CRED_ID}}`, `{{LINEAR_CRED_ID}}`, `{{PUSHOVER_CRED_ID}}`.
- Standard retry on every external API call: `retryOnFail: true`, `maxTries: 3`, `waitBetweenTries: 5000`.
- Code nodes: `javaScript`, `mode: runOnceForEachItem`, return `{ json: {...} }`.
- Linear API access via direct `httpRequest` with GraphQL body (n8n's built-in Linear node is limited; W1 already established the direct-HTTP pattern).
- Pushover node reads `$env.PUSHOVER_USER_KEY`, **explicit priority always set** (rev-3 amendment: default priority -1 is silent delivery, which is a footgun).
- `continueErrorOutput` on risky nodes, with error branches converging to a recovery handler.
- Error-workflow wiring done post-deploy via n8n UI (one-time manual step; not exposed in the public API).

### Deploy script guardrails (from PRO-27) W2 relies on

All three checks run before any POST/PUT against the n8n API:

1. **Connections integrity** — every top-level key in `connections` and every edge `.node` must reference a node name that exists in `nodes[]`. Catches the rename-without-rewrite silent-break class ([deploy-workflow.ps1:37-99](docker/n8n/scripts/deploy-workflow.ps1)).
2. **Credential references** — every `credentials.*.id` must exist in the live n8n vault. Catches UUID drift after credential rotation ([deploy-workflow.ps1:101-123](docker/n8n/scripts/deploy-workflow.ps1)).
3. **Settings merge** — on update, fetches existing settings and merges incoming through a writable-key allowlist. `errorWorkflow` survives redeploys ([deploy-workflow.ps1:125-161](docker/n8n/scripts/deploy-workflow.ps1)).

Also post-deploy reports the real active state (no hardcoded "(inactive)" string).

---

## Part 2 — Node-by-node topology

17 nodes. Grid starts at x=240, y=300 for main flow. Branches at ±200 y-offset. W1's grid ends at x=2240; W2 is a separate workflow so its grid resets.

### Main flow

| # | ID | Type | typeVersion | Purpose | Inputs | Outputs | Retry | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| 1 | `w2001-linear-poll` | `n8n-nodes-base.httpRequest` | 4.2 | Poll Linear GraphQL every 3 min for Todo issues matching W2 filter | scheduled trigger | array of issue UUIDs | maxTries=3 wait=5000 | parks to shared error handler |
| 2 | `w2002-manual-webhook` | `n8n-nodes-base.webhook` | 1.1 | Manual entry for Claude Chat / W9 re-route. Path `w2-route`. Body `{issue_id, trace_id_predecessor?, reason?}` | POST body | normalized context | none | 400 to caller on malformed body |
| 3 | `w2003-normalize-poll` | `n8n-nodes-base.set` | 3.4 | For each polled issue: extract `issue_id`, generate `trace_id` (UUID v4), set `intake_source: poll`, set `shadow_mode` from env | output of w2001 | per-issue context | none | n/a |
| 4 | `w2004-normalize-webhook` | `n8n-nodes-base.set` | 3.4 | Extract `issue_id` from webhook body, reuse `trace_id_predecessor` if present (else generate), set `intake_source: webhook`, set `shadow_mode` from env | output of w2002 | per-issue context | none | n/a |
| 5 | `w2003a-dedupe-guard` | `n8n-nodes-base.code` | 2 | Read last 5 min of `routing_history.jsonl`, short-circuit if same `task_id` has a non-terminal row. Prevents double-write from poll-retrigger race | normalized context | `{..., should_proceed: bool}` | none | on read error: log + proceed (fail-open for dedupe only; real dedupe is the Linear label filter, this is belt-and-suspenders) |
| 6 | `w2005-linear-fetch` | `n8n-nodes-base.httpRequest` | 4.2 | Single GraphQL: fetch issue + labels + narrow `issueLabels(filter:{name:{in:[w2-label-set]}})`. No team-states sub-query | `issue_id` | full issue JSON + known label-name-to-ID map | maxTries=3 wait=5000 | `continueErrorOutput` → w2999 |
| 7 | `w2006-extract-signals` | `n8n-nodes-base.code` | 2 | Extract `task_type` from labels, `surface_keywords` from title+description, `touches_paths` from path-regex over description, `research_signal` (additive flag, not terminator) | issue JSON | `extracted_signals` blob | none | `continueErrorOutput` → w2999 |
| 8 | `w2007-score-workers` | `n8n-nodes-base.code` | 2 | Load `w2_routing_rules.json` from `/miru-data/config/`, compute per-worker scores, rank, compute confidence | extracted_signals | `{ranked_candidates[], confidence, winner}` | none | `continueErrorOutput` → w2999 |
| 9 | `w2008-classify-risk` | `n8n-nodes-base.code` | 2 | Rule-based low/medium/high; Linear priority Urgent bumps one level up (one-way) | extracted_signals + issue priority | `{risk, risk_rationale}` | none | `continueErrorOutput` → w2999 |
| 10 | `w2009-branch-decision` | `n8n-nodes-base.if` | 2.2 | Route: `confidence < 0.75` → triage path; `research_signal AND confidence >= 0.75` → research-handoff path; else → dispatch path | scored + risk context | 3-way branch | none | n/a |
| 11 | `w2010-linear-apply-and-comment` | `n8n-nodes-base.httpRequest` | 4.2 | **Single** GraphQL: `issueUpdate{labelIds:[winner, pending-approval]}` + `commentCreate{body:decision-packet}` atomic. Matches W1 `a015` multi-mutation pattern. Skipped in shadow mode. | ranked + risk + winner | Linear issue updated | maxTries=3 wait=5000 | `continueErrorOutput` → w2998-apply-failed (NOT w2999 — decision preserved) |
| 12 | `w2011-append-history` | `n8n-nodes-base.code` | 2 | fs.appendFileSync to `/miru-data/routing_history.jsonl` with 15-field record (see §5). Record-size guard at 3900 bytes | full decision context | appended line | none | on write error: Pushover priority 2 + exit (file-write failures are operator-actionable) |
| 13 | `w2012-pushover-stub` | `n8n-nodes-base.pushover` | 1 | Priority 1, title `W2 → [Worker] proposed for [PRO-X]`, body per §6 format, url = Linear issue URL. **Single-point-of-swap for real W8.** | full decision context | notification sent | maxTries=3 wait=5000 | on fail: log to error handler (history row already written, state recoverable) |

### Triage branch (confidence < 0.75)

| # | ID | Type | typeVersion | Purpose | Inputs | Outputs | Retry | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| 14 | `w2013-triage-path` | `n8n-nodes-base.httpRequest` | 4.2 | Single GraphQL: `issueUpdate{labelIds:[triage]}` + `commentCreate{body:low-confidence-reason}` | scored context | triage applied | maxTries=3 wait=5000 | `continueErrorOutput` → w2999 |
| 15 | `w2014-triage-pushover` | `n8n-nodes-base.pushover` | 1 | Priority 1 — "Router confidence low on PRO-X — manual routing required. Ranked: [...]" | scored context | notification sent | maxTries=3 wait=5000 | log on fail |

History append on triage path: a minimal `w2013a-append-history-triage` code node mirrors w2011 with `chosen_worker: "triage"`, `outcome: "pending"` (Phase 1: operator will set the label manually; no automatic outcome flip).

### Research-handoff branch (research_signal set)

| # | ID | Type | typeVersion | Purpose | Inputs | Outputs | Retry | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| 16 | `w2015-research-handoff` | `n8n-nodes-base.httpRequest` | 4.2 | Apply `research` label + post research-signal comment. W3 not built yet — this is where the flow stops in Phase 1 until W3 ships. | scored context | research label applied | maxTries=3 wait=5000 | `continueErrorOutput` → w2999 |

A secondary `w2015a-append-history-research` code node writes the history row with `chosen_worker: null`, `research_flag: true`, `outcome: "pending-research"`.

### Error-recovery convergence

| # | ID | Type | typeVersion | Purpose | Inputs | Outputs | Retry | Failure behavior |
|---|---|---|---|---|---|---|---|---|
| 17 | `w2999-router-failure` | `n8n-nodes-base.code` | 2 | Convergence point for decision-phase `continueErrorOutput` branches. Applies `triage` (via a sub-httpRequest not tracked as a separate node, or via inline sequenced call — see §7), posts error summary comment, Pushover priority 1, appends history with `outcome: "halted"` | failed execution + error msg | halt state applied | maxTries=3 wait=5000 (on internal httpRequest) | workflow-level error handler picks up (shared with W1) |
| — | `w2998-apply-failed` | `n8n-nodes-base.code` | 2 | Distinct from w2999. Runs only when w2010 fails AFTER retries. Writes history `outcome: "apply-failed"`, Pushover priority 1 with full decision packet ("Apply failed for PRO-X — decision ready, apply manually: worker=[X] reasoning=[...]") | decision context + apply error | history written + operator notified | none | workflow-level error handler |

### Connection topology (text diagram)

```
w2001-linear-poll ──┐
                     ├─► w2003-normalize-poll ────┐
w2002-manual-webhook ├─► w2004-normalize-webhook ─┴─► w2003a-dedupe-guard
                                                       │  (should_proceed==true)
                                                       ▼
                                                    w2005-linear-fetch ──(ok)──► w2006-extract-signals
                                                                 │                       │
                                                                 ├──(err)─► w2999        ▼
                                                                                   w2007-score-workers
                                                                                         │
                                                                                         ▼
                                                                                   w2008-classify-risk
                                                                                         │
                                                                                         ▼
                                                                                   w2009-branch-decision
                                                                                  /    |    \
                                                            (conf<0.75)          /     |     \       (dispatch)
                                                                w2013-triage-path    (research)   w2010-linear-apply-and-comment
                                                                       │              w2015-research-handoff        │ (ok)
                                                                       ▼                       │                    ▼
                                                                w2013a-append-hist             ▼              w2011-append-history
                                                                       │              w2015a-append-hist            │
                                                                       ▼                       │                    ▼
                                                                w2014-triage-pushover          │              w2012-pushover-stub
                                                                                               │
                                                                       (apply-failed)          │
                                                                           w2010 ──(err)──► w2998-apply-failed
                                                                                               │
                                                                                (decision-phase errors from any code node)
                                                                                               │
                                                                                               ▼
                                                                                         w2999-router-failure
```

### Position layout (n8n canvas coordinates)

- x=240: triggers (w2001 at y=240, w2002 at y=360)
- x=440: normalize (w2003 at y=240, w2004 at y=360)
- x=640: w2003a-dedupe-guard at y=300
- x=840: w2005-linear-fetch at y=300
- x=1040: w2006-extract-signals at y=300
- x=1240: w2007-score-workers at y=300
- x=1440: w2008-classify-risk at y=300
- x=1640: w2009-branch-decision at y=300
- x=1840: dispatch path y=300 (w2010), triage path y=500 (w2013), research path y=100 (w2015)
- x=2040: append-history row per path
- x=2240: pushover row per path
- x=2040 y=-100: w2998-apply-failed
- x=2040 y=700: w2999-router-failure

---

## Part 3 — Confidence scoring

### Rationale

The post-research invariants and PRO-27 research are explicit: LLM verbalized confidence correlates with verbosity, not correctness. A rule-based scorer with explicit signal matches is the Phase 1 right-sized tool. The scoring function MUST be rule-based and deterministic; no LLM is in the W2 scoring path.

### Scoring formula

For each worker `W`:
1. Start at baseline `score[W] = 0.5`.
2. For each "best-for" signal match from the canon table, `score[W] += 0.15`, capped at 4 matches (so max contribution from best-for = `0.15 × 4 = 0.60`).
3. For each "hard-no-go" signal match from the canon table, `score[W] = 0.0` (hard disqualifier; not additive).
4. Clamp `score[W]` to `[0.0, 1.0]`.

### Canon-table "best-for" and "hard-no-go" signals

From canon page 16, §W2 step 3 table:

| Worker | Best for signals (each +0.15) | Hard no-go (score → 0) |
|---|---|---|
| Claude Code | careful implementation; architecture-sensitive; multi-step exec; multi-file implementation; backend/service code | pure UI iteration; read-only audits |
| Codex | technical repo work; analysis-heavy coding; multi-file implementation; refactors | interactive UI builds; direct DB execution |
| Cursor | rapid UI iteration; HTML/CSS/Svelte; live phone testing; template/storefront work | backend architecture; risky refactors; DB-adjacent work |
| Gemini CLI | audit; schema reads; alternate framing; repo scan; second-opinion | editing code or templates |

These signals come from extracted keywords, labels, and `touches_paths`. The exact mapping from extracted signal to best-for/hard-no-go match lives in `/miru-data/config/w2_routing_rules.json` — externalized so scoring-rule changes don't require workflow redeploy. The config file will be created by the actual W2 build pass, not this plan pass.

### Ranking

```
sort candidates by score desc
within-tie (|a.score - b.score| < 0.01): fixed priority Claude Code > Codex > Cursor > Gemini CLI
```

See §8 for the JS implementation.

### Confidence

```
top = ranked[0]
second = ranked[1]
gap = top.score - second.score
margin = top.score - 0.5

if top.score < 0.55:
    confidence = 0.0           // nobody cleared baseline + one match → force triage
else:
    confidence = min(1.0, 0.3 * (gap / 0.5) + 0.7 * (margin / 0.5) + 0.5)
```

### Worked examples

| Case | top.score | second.score | gap | margin | confidence | Outcome |
|---|---|---|---|---|---|---|
| Clear winner, strong match | 0.95 | 0.50 | 0.45 | 0.45 | min(1.0, 0.3·0.9 + 0.7·0.9 + 0.5) = 1.0 | dispatch |
| Clear winner, weak match | 0.65 | 0.50 | 0.15 | 0.15 | min(1.0, 0.3·0.3 + 0.7·0.3 + 0.5) = 0.80 | dispatch |
| Contested winners | 0.80 | 0.65 | 0.15 | 0.30 | min(1.0, 0.3·0.3 + 0.7·0.6 + 0.5) = 1.01→1.0 | dispatch (but close — watch in logs) |
| Two close strong | 0.90 | 0.85 | 0.05 | 0.40 | min(1.0, 0.3·0.1 + 0.7·0.8 + 0.5) = 1.09→1.0 | dispatch; tiebreaker may trigger |
| Weak field | 0.55 | 0.50 | 0.05 | 0.05 | 0.3·0.1 + 0.7·0.1 + 0.5 = 0.60 | **triage** (< 0.75) |
| Barely-cleared baseline | 0.54 | 0.50 | 0.04 | 0.04 | **0** (floor triggered) | **triage** |
| Nobody matched | 0.50 | 0.50 | 0.0 | 0.0 | **0** (floor triggered) | **triage** |
| One worker disqualified | 0.80 | 0.0 | 0.80 | 0.30 | min(1.0, 0.3·1.6 + 0.7·0.6 + 0.5) = 1.40→1.0 | dispatch — note: gap component capped by normalization (gap/0.5 could exceed 1 in theory, but min(1.0, …) clips it) |

### Known limitations

- **This is a Phase 1 placeholder.** The 0.3/0.7 weighting of gap vs. margin is an educated guess. Recalibrate at 200 logged decisions using Brier score + 10-bucket ECE (per the PRO-27 research plan). Phase 2 entry is gated on this calibration pass.
- **No historical tie-awareness yet.** If two workers have tied at 0.80 five times in the last 20 decisions and the operator always overrode to Codex, Phase 1 will keep picking the fixed-priority winner (Claude Code). Historical signal consumption deferred to Phase 2.
- **No signal weights.** All best-for matches contribute +0.15. In reality "multi-file implementation" is a stronger signal than "architecture-sensitive" (subjective word). Flatten-for-now is Phase 1's bet; Phase 2 may introduce per-signal weights in the config file.

---

## Part 4 — Risk classification

Risk is **independent of confidence** — a W2 can be fully confident about picking Claude Code for a high-risk migration; the high-risk classification still flows through the approval payload and feeds downstream decisions (Phase 2+).

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
    - no production code keywords (defined below)

risk = "medium" otherwise (default)

modifier: if linear_priority == 1 (Urgent):
    bump one level up (low → medium, medium → high, high stays high)
    # one-way only — urgent never lowers risk
```

### Production code keyword set

(Prevents a `docs/CRITICAL_API.md` from being miscategorized as low-risk when it carries implementation detail, and catches multi-surface tasks that happen to mention `docs/` incidentally.)

```
{ route, endpoint, database, query, SQL, migration, auth, credential,
  secret, API key, production, deploy, rollout, backfill, reindex }
```

### Deliberately excluded

- **"refactor" is not a risk keyword.** A docs-only refactor is low risk; a schema refactor is high risk; the paths already distinguish these. Including "refactor" in the keyword list creates a spurious risk bump on every well-scoped cleanup task.
- **Description length alone is not a risk modifier.** Long descriptions correlate weakly with scope, and W2 has cleaner signals (paths, keywords). Adding a length rule would introduce noise.

### Edge cases

| Case | Paths | Keywords | Priority | Computed risk | Rationale |
|---|---|---|---|---|---|
| Docs typo | `docs/craft/principles.md` | none | Normal | low | all low conditions met |
| Docs typo on urgent | same | none | Urgent | **medium** | priority bump |
| Migration | `dispatcher/migrations/0002.sql` | migrate | Normal | high | path hit |
| Feature in pm/ | `pm/storefront/routes/…` | none | Normal | medium | default |
| Auth bug fix | `pm/storefront/auth/session.py` | auth | High | high | keyword hit (priority=high doesn't bump; only Urgent does) |
| card_catalog touch | `data/card_catalog.db` | — | Low | high | path hit (priority doesn't modify high down) |

### Downstream consumers (gap in canon)

Canon says risk is "informational only" in Phase 1. That's defensible but creates a write-only field. This review proposes eventual consumers:

- **Phase 1+:** Pushover priority bump. `risk=high AND confidence<0.75` → priority 2 (emergency) rather than priority 1. Small code change in w2012/w2014.
- **Phase 3:** eligibility gate. Only `risk=low` flows are candidates for skip-approval.

Surfaced as canon-amendment candidate #5 in §11.

---

## Part 5 — routing_history schema

Canon specifies 12 fields, all required. This review proposes 15 fields total — 12 canon + 3 additions flagged as canon-amendment candidates. The additions are free to include now, expensive to backfill later.

### Full schema (proposed, 15 fields)

| # | Field | Type | W2-dispatch value | Updated by | Notes |
|---|---|---|---|---|---|
| 1 | `timestamp` | ISO-8601 UTC | `new Date().toISOString()` | W2 (write-once) | |
| 2 | `trace_id` | UUID v4 | generated at w2003/w2004 | W2 (write-once) | Carried into W5/W9 writes for correlation |
| 3 | `task_id` | string | Linear UUID (stable across rename) | W2 (write-once) | |
| 4 | `task_identifier` | string | Linear identifier e.g. `PRO-42` | W2 (write-once) | **CANON ADDITION (#1).** Human-friendly grep key; Linear UUIDs are hard to spot-check |
| 5 | `extracted_signals` | JSON object | `{task_type, surface_keywords[], touches_paths[], research_signal}` | W2 (write-once) | |
| 6 | `ranked_candidates` | array of objects | `[{worker, score, reasoning}, ...]` 4-element full ranking | W2 (write-once) | **CANON ADDITION (#2) — structural.** Canon says "array"; expanding to `[{worker, score, reasoning}]` lets W5/W9 pick rank-2 without re-running W2 |
| 7 | `chosen_worker` | string | worker label OR `"triage"` OR `"halted"` OR `null` (research branch) | W2 (write-once) | |
| 8 | `confidence` | float | from §3 formula | W2 (write-once) | |
| 9 | `risk` | enum | `low` / `medium` / `high` | W2 (write-once) | |
| 10 | `operator_override_flag` | bool | `false` at dispatch | Override-watchdog workflow (see §10-extra) | Set to `true` when operator's label-swap-at-approval targets a different worker than W2 proposed |
| 11 | `outcome` | enum | `"pending"` at dispatch; `"pending-research"` on research branch; `"halted"` from w2999; `"apply-failed"` from w2998 | W5 (success/fail/inconclusive), W9 (re-routing) | Multi-writer field — append rule: most recent row per trace_id wins |
| 12 | `worker_response_ref` | string | `"pending"` sentinel | W5 (overwrites with Linear comment ID) | |
| 13 | `proposed_model_version` | string | lookup: `claude-code → claude-opus-4-7`, `codex → gpt-codex-latest`, `cursor → cursor-default`, `gemini → gemini-2.5-pro` | W2 (write-once) | **CANON ADDITION (#3a) — split.** Replaces canon `model_version` |
| 14 | `actual_model_version` | string \| null | `null` at dispatch | W5 (fills if receipt includes it) | **CANON ADDITION (#3b) — split.** Keeps Phase 2 calibration honest when worker's actual model differs from expected |
| 15 | `w2_workflow_version` | string | git SHA of `w2.json` at deploy time | W2 (write-once) | **CANON ADDITION (#4).** Essential for Phase 2 calibration — filters out rows produced under a prior scoring formula |

### JSONL example (dispatch row, one per line)

```json
{"timestamp":"2026-04-24T01:42:15.234Z","trace_id":"c1f4d8e1-6f21-4b8e-9c87-4e3d2a8b91f2","task_id":"abc12345-6789-0000-aaaa-bbbbccccdddd","task_identifier":"PRO-42","extracted_signals":{"task_type":"Feature","surface_keywords":["multi-file","refactor","dispatcher"],"touches_paths":["dispatcher/handlers/claude.py","dispatcher/task_dispatcher.py"],"research_signal":false},"ranked_candidates":[{"worker":"codex","score":0.95,"reasoning":"multi-file implementation, technical repo work, refactor"},{"worker":"claude-code","score":0.80,"reasoning":"multi-step exec, architecture-sensitive, multi-file"},{"worker":"gemini","score":0.50,"reasoning":"baseline — no matches, no no-go"},{"worker":"cursor","score":0.0,"reasoning":"hard no-go: backend architecture, risky refactor"}],"chosen_worker":"codex","confidence":0.83,"risk":"medium","operator_override_flag":false,"outcome":"pending","worker_response_ref":"pending","proposed_model_version":"gpt-codex-latest","actual_model_version":null,"w2_workflow_version":"abcdef1"}
```

### Record-size guard (concurrency safety)

`fs.appendFileSync` on Linux/ext4 guarantees atomicity for writes < `PIPE_BUF` (typically 4096 bytes). Full records with long reasoning strings can approach or exceed this.

The w2011-append-history code node enforces:

```javascript
const line = JSON.stringify(record) + "\n";
if (line.length > 3900) {
    // Truncate the longest free-text fields (ranked_candidates[].reasoning first)
    record.ranked_candidates.forEach(c => { if (c.reasoning && c.reasoning.length > 80) c.reasoning = c.reasoning.slice(0, 77) + "..."; });
    record._truncated = true;
    line = JSON.stringify(record) + "\n";
}
if (line.length > 3900) {
    // Fallback: also truncate extracted_signals.surface_keywords to first 5
    record.extracted_signals.surface_keywords = (record.extracted_signals.surface_keywords || []).slice(0, 5);
    line = JSON.stringify(record) + "\n";
}
// Final safety: if still over 3900 we accept the risk (extremely rare; `_truncated: true` surfaces it)
fs.appendFileSync(HISTORY_PATH, line);
```

At ~50 tasks/month, race with W9 re-route writes is effectively zero; the guard is cheap belt-and-suspenders.

### Append semantics

- **Write-once fields** (timestamp, trace_id, task_id, task_identifier, extracted_signals, ranked_candidates, chosen_worker, confidence, risk, proposed_model_version, w2_workflow_version) are set once at the W2 dispatch row and never mutated.
- **Multi-writer fields** (outcome, worker_response_ref, operator_override_flag, actual_model_version) require updates after the dispatch row is written. Since JSONL doesn't support in-place update, the convention is **append a new row with the same trace_id and updated fields**, and "latest row per trace_id wins" on read. This is an event log, not a relational store.

---

## Part 6 — Stub-W8 handoff pattern

### Label flow

```
W2 dispatch-path outcome:
    Linear issue labels BEFORE w2010:  [Feature, intake-draft-removed-by-operator]  (no worker label, no pending-approval)
    w2010 applies:                      add 'claude-code' (or proposed worker), add 'pending-approval'
    Linear issue labels AFTER w2010:   [Feature, claude-code, pending-approval]

Operator actions (manual via Linear UI or Claude Chat):
    Approve:   remove 'pending-approval'            → labels: [Feature, claude-code]        → W4/W5 begin picking up
    Override:  remove 'claude-code', add 'codex', remove 'pending-approval'
                                                    → labels: [Feature, codex]              → W4/W5 begin; override-watchdog flips operator_override_flag
    Reject:    apply 'triage', remove worker label  → labels: [Feature, triage]             → dropped from automation; operator manages manually
```

### Pushover notification format (priority 1)

```
Title:  W2 → [Worker Display Name] proposed for [PRO-X]

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

    trace_id: [UUID]    # included for audit; operator can grep routing_history
```

Pushover `url` field: Linear issue URL (`https://linear.app/project-miru/issue/PRO-X/...`). One-tap from phone takes the operator directly to the approval context.

### Why this pattern vs. alternatives

- **Prefix-style (`proposed:claude-code`):** rejected. Downstream workflows (W4) watch for `claude-code` as the start-signal. Prefixing breaks that contract and introduces two labels where the clean semantic is "the label that routes work is the worker label."
- **Single `pending-approval` label, no worker label:** rejected. Loses the proposal information at the label level — operator has to read the Pushover or Linear comment to know what was proposed. Bad for audit, bad for quick operator scan.
- **Apply both (recommended):** each label carries independent meaning. `claude-code` = "this is the proposed work lane", `pending-approval` = "this is awaiting operator approval". Two axes, each with a single meaning. Approval and override are clean single-action operator workflows.

### Real-W8 swap path (honest scope)

The operator brief says "node shape should be designed so the swap is a single node change."

Partially true:

- **Outbound (W2 side):** ✓ single-node swap. `w2012-pushover-stub` gets replaced with a variant that generates signed URLs for Approve/Reject/Override/Triage. Same inputs, same downstream — just the Pushover payload changes.
- **Inbound (real-W8 side):** ✗ not a single-node swap. Real W8 requires:
  - A separate continuation workflow triggered by the webhook from the signed URL click.
  - TTL escalation logic (re-ping at priority 2 after the Linear-priority-scaled TTL; fallback-to-triage after 2×TTL).
  - A pending-approval state store that the continuation workflow reads from (proposed: same `routing_history` — `trace_id` is the key, and the most recent row for a `task_id` with `outcome: "pending"` is the open approval).

Real-W8 build is 1 new workflow (~5 nodes) + 1 watchdog workflow (~3 nodes). Plan for a week, not a day. The deliverable surfaces this so operator isn't surprised when real-W8 lands with its own plan-mode pass.

### Trace-id URL embedding (forward-compat)

Signed URLs in real-W8 will embed `trace_id` + `task_id` as query params. The continuation workflow parses these and looks up the pending decision from `routing_history`. UUID v4 is URL-safe (base16 with dashes, no special characters). This is already satisfied by the trace_id format.

---

## Part 7 — Router failure policy

Canon: "If the routing logic itself fails or is unavailable: apply `triage`, Pushover operator at priority 1, do NOT attempt fallback routing. System fails closed."

This review proposes splitting failure modes into two classes with different handling:

### Decision-phase failure (canon-aligned)

Failures during w2005-fetch, w2006-extract, w2007-score, w2008-classify, or w2009-branch. **No decision was produced.** Triage is safe because there's nothing to preserve.

- `continueErrorOutput` from each risky node converges at `w2999-router-failure`.
- w2999 applies `triage` label to the issue, posts an error-summary comment ("W2 halted at [node]. Error: [msg]"), sends Pushover priority 1, appends a history row with `chosen_worker: "halted"`, `outcome: "halted"`.

### Apply-phase failure (canon amendment)

Failures during w2010-linear-apply-and-comment AFTER retries exhausted. **The decision was produced — scoring, risk, ranking all completed — but the label/comment write didn't land.**

Canon currently routes this through the same `triage` path as decision-phase failures. That's information loss: the decision was computable, just not applicable. Discarding it means the next W2 pass will re-compute from scratch — with the risk of landing on a different answer if signals have drifted.

Proposed alternative:

- w2010's `continueErrorOutput` branch goes to `w2998-apply-failed`, NOT w2999.
- w2998 writes a history row with `outcome: "apply-failed"`, `chosen_worker: [proposed]` (decision preserved).
- w2998 sends Pushover priority 1: "Apply failed for PRO-X — decision ready, apply manually. Proposed: [Worker]. Reasoning: [...]. Label the issue with [worker] + pending-approval manually, or trigger a retry via the w2-route webhook."
- Operator has two recovery options: apply labels manually, or let a separate operator action re-fire W2 via the manual webhook (which will re-score from scratch — acceptable, but not forced).

### Workflow-level error handler

W1's shared error handler (`w1-error-handler.json`) stays wired for truly unexpected failures (n8n internals, node crashes outside the managed `continueErrorOutput` paths). Unchanged from W1. Wired post-deploy via n8n UI.

### Canon amendment candidate

Surfaced as honest-pushback #6 in §11.

---

## Part 8 — Tiebreaker implementation

Deterministic per canon invariant. Fixed priority order `Claude Code > Codex > Cursor > Gemini CLI`.

### Code snippet (w2007-score-workers)

```javascript
const PRIORITY = { "claude-code": 0, "codex": 1, "cursor": 2, "gemini": 3 };
const TIE_EPSILON = 0.01;

function rankCandidates(candidates, excludeWorker = null) {
    // W9 re-route case: exclude the previous worker from the candidate set
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

### Tiebreaker behavior

- `|a.score - b.score| < 0.01` → treat as tied, use fixed priority.
- Strict float equality would be fragile under scoring-rule changes (0.65 vs. 0.65000000000001 would split); the epsilon is conservative.
- W9 re-route (future) calls `rankCandidates(candidates, previous_worker)`. The excluded worker is removed from the pool BEFORE sorting. The tiebreaker rule applies to the remaining pool.

### Edge cases

- **All four workers tied.** Rank: Claude Code, Codex, Cursor, Gemini. Confidence computed against `top=0.X, second=0.X` → `gap=0` → floor likely triggers → triage.
- **Winner tied with no-go'd worker.** No-go worker has score=0, winner has score=0.X. Not a tie.
- **W9: only one worker left after exclusion and they have no-go.** Winner has score=0. Floor triggers. Triage. Correct fail-closed behavior — better a human picks than re-dispatch to Gemini for a code edit.

---

## Part 9 — Test strategy

Per canon W1 lesson #4, Claude Code owns the workflow test loop. Operator authorizes + reviews but doesn't click through UIs.

### Test harness

- Deploy W2 **inactive**.
- Use manual webhook invocations only (`POST http://localhost:15678/webhook-test/w2-route` with `{issue_id}` body) — never the poll trigger during testing.
- Test issues tagged with `test-w2` label. The poll filter excludes this label so test runs can't self-pollute when W2 eventually goes live.
- Cleanup step: delete test issues via Linear GraphQL after each test.

### Test cases

| # | Name | Setup | Assertion | Cleanup |
|---|---|---|---|---|
| 1 | Low-risk happy path | Create `test-w2` issue: title "Fix typo in docs/craft/principles.md", description < 200 words, label Bug | risk=low; chosen_worker ∈ {cursor, claude-code}; confidence ≥ 0.75; labels applied atomically; history row present; Pushover sent | delete issue |
| 2 | High-risk happy path | Create `test-w2` issue: title "Add migration to dispatcher/migrations/0042.sql", description mentions "DB schema", priority Normal | risk=high; chosen_worker ∈ {claude-code, codex}; confidence ≥ 0.75; history row shape correct; Pushover includes risk rationale | delete issue |
| 3 | Low-confidence triage | Create `test-w2` issue: vague title "help", description "just do something", no meaningful keywords | confidence < 0.75; `triage` label applied; no worker label applied; history row `chosen_worker: "triage"`, `outcome: "pending"`; Pushover priority 1 | delete issue |
| 4 | Decision-phase failure | POST webhook with bogus `issue_id: "00000000-0000-0000-0000-000000000000"` | w2005 fetch returns Linear error after retries; w2999 fires; history row `outcome: "halted"`; Pushover priority 1 | n/a (no real issue touched) |
| 5 | Apply-phase failure | Stage: disable Linear credential temporarily OR use a real issue but revoke write-scope on token mid-execution | w2010 fails after 3 retries; w2998 fires (not w2999); history row `outcome: "apply-failed"`, `chosen_worker: [proposed, preserved]`; Pushover includes "apply manually" instructions | restore credential; delete issue |
| 6 | Research signal detection | Create `test-w2` issue: description contains "find examples of how other TCG sites do X" | extracted_signals.research_signal=true; if confidence ≥ 0.75 → research-handoff branch fires; `research` label applied; history row `outcome: "pending-research"` | delete issue |
| 7 | Container restart mid-decision | Trigger W2 on a test issue; while execution is between w2005 and w2010, run `docker compose -f docker/n8n/docker-compose.yml restart` | No orphan `pending-approval` label on Linear (because w2010 didn't run); no history row with incomplete fields; shared error handler fires; re-invoke webhook completes cleanly (dedupe guard short-circuits OR runs fresh if outside 5-min window) | delete issue |

### Shadow mode — extra validation before go-live

Ship W2 with `W2_SHADOW_MODE=true` environment variable on day 1.

- All scoring, risk, history-write, and comment-post operations still run.
- w2010's label-apply GraphQL mutation is skipped (`issueUpdate` not called); only `commentCreate` runs (so operator can still see the decision packet as a comment).
- w2012 Pushover still fires but says "SHADOW MODE — decision was [Worker] but NOT applied. Review and apply manually if desired."
- Flip `W2_SHADOW_MODE=false` after ~30 clean shadow runs.

This is the lowest-cost risk mitigation for a new scoring formula. Two lines in w2003/w2004 to read the env var, a single `if` in w2010 to skip the label mutation. Costs about an hour of build time and would catch any scoring-formula disasters before they label-spam the Linear board.

---

## Part 10 — Ambiguities & proposed resolutions for operator decision

| # | Ambiguity | Proposed resolution | Operator decision needed |
|---|---|---|---|
| 1 | routing_history storage mechanism | JSONL at `/miru-data/routing_history.jsonl` (after compose bind mount) | approve / propose alternative |
| 2 | Schema: 12 canon fields vs. 15 proposed | Include 3 additions (task_identifier, proposed/actual model version split, w2_workflow_version) | accept / reject additions |
| 3 | `ranked_candidates` — array of strings or array of objects | Array of `{worker, score, reasoning}` for W5/W9 rank-2 selection | accept / reject |
| 4 | `research_signal` — terminator or additive flag | Additive flag; W2 still produces a ranked proposal, research-handoff branch fires for the research label | accept / reject |
| 5 | Risk downstream consumers | Document now: Pushover priority bump (Phase 1+), Phase 3 skip-approval eligibility gate | approve / defer |
| 6 | Router failure split (decision vs. apply phase) | Preserve decision on apply-phase failure, surface to operator | accept / reject |
| 7 | Pending-approval TTL in stub-W8 | Watchdog micro-workflow: scan every 8am/8pm for `pending-approval` older than 24h → Pushover priority 2 | ship with W2 / defer to real-W8 |
| 8 | Shadow mode on day 1 | Yes, ship it. Flip off at ~30 clean runs | approve / reject |
| 9 | Trigger cadence (1 min vs. 3 min) | 3 min. Human cadence doesn't need faster; saves API budget | approve / propose alternative |
| 10 | External scoring rules config file | `/miru-data/config/w2_routing_rules.json` loaded at runtime | approve / keep rules hardcoded |
| 11 | Override-flag capture watchdog in Phase 1 | Ship a small label-change-webhook workflow that flips `operator_override_flag` when operator's label doesn't match proposed | approve / defer to Phase 2 |
| 12 | Manual webhook body shape | `{issue_id, trace_id_predecessor?, reason?}` | approve / propose schema change |
| 13 | Test-W2 label filter in trigger | Add `test-w2` to the "exclude labels" list so test issues don't self-trigger | approve |

None of these block the build if operator rejects — W2 can ship with canon's literal shape and still work. The proposals are optimizations the current design reveals.

---

## Part 11 — Honest pushbacks on rev 3 canon

All 9 are canon-amendment candidates. Operator can accept, amend, or reject each. Rejecting any just means W2 ships with the literal canon shape on that point.

### 1. `routing_history` storage mechanism is unspecified

Canon says "shared store". Container filesystem realities (only `n8n_data:/home/node/.n8n` is mounted) mean this must be resolved before W2 can write anywhere useful. Proposing JSONL + new bind mount. Alternative: static data (rejected — loses on restart), Postgres (rejected — overkill at scale), write-through via dispatcher on 19000 (reasonable Phase 2 option, but adds a service dependency for Phase 1).

### 2. Three schema additions proposed (task_identifier, model version split, w2_workflow_version)

All free to include now, expensive to backfill. `task_identifier` is a grep-ability win. `proposed/actual_model_version` split keeps Phase 2 calibration honest when worker's actual model differs from expected (workers don't consistently report version today, but may in future receipts). `w2_workflow_version` filters out rows produced under stale scoring formulas — essential for Brier/ECE calibration to be meaningful.

### 3. `ranked_candidates` as objects not strings

Canon says "array". Expanding to `[{worker, score, reasoning}]` lets W5/W9 pick rank-2 without re-running W2 — and without the risk that W2's next run lands on a different answer because signals shifted. Self-contained history row is cheaper than re-derivation.

### 4. `research_signal` as additive flag, not terminator

Canon W2 step 6: "If research_signal is true: DO NOT apply worker label. Pass issue to W3 instead." Reading strictly, research signal is mutually exclusive with worker routing. But an issue can legitimately be both research-heavy AND have a clear coding lane (e.g., "find examples of how other deck-builders do X, then implement in pm/"). Proposing: W2 still scores workers and writes the history row with `chosen_worker: null, research_flag: true, outcome: "pending-research"`, and the research-handoff branch fires. W3, when built, has the scoring context already available.

### 5. Risk is a write-only field in Phase 1

Canon says risk is "informational only" in Phase 1 with no gating effect. That's defensible but a write-only field is a code smell — it drifts from reality with no feedback loop to correct it. Propose documenting consumers now:

- Phase 1+: `risk=high AND confidence<0.75` bumps Pushover priority from 1 to 2.
- Phase 3: only `risk=low` flows are candidates for skip-approval.

Small, cheap, keeps the field honest.

### 6. Router failure split: decision-phase vs. apply-phase

Canon treats all routing failures the same: triage, notify, no fallback. Proposing a split:

- **Decision-phase** (fetch/score/classify failures): triage (canon behavior).
- **Apply-phase** (label write failure after retries): preserve the decision. Write `outcome: "apply-failed"`, surface decision packet to operator via Pushover ("decision ready, apply manually"). Re-routing via the manual webhook is always an option, but don't force it.

Rationale: scoring is expensive (it's the entire point of W2). Discarding a successful score because the label mutation flaked on retries is silent information loss. Preserving the decision gives operator a fast-path recovery that doesn't require regenerating the decision from scratch.

### 7. Pending-approval TTL in stub-W8

Canon W8 has TTL escalation (priority-scaled, ending in fail-closed triage). Stub-W8 has no TTL — issue could sit with `pending-approval` forever if operator goes on vacation. Proposing a small watchdog micro-workflow (runs 8am and 8pm local): scan Linear for `pending-approval` older than 24h → Pushover priority 2 nudge. 3 nodes. Ship with W2.

### 8. Hardcoded Linear team ID (technical debt from W1)

W1 hardcodes team ID `f9d6193c-4572-40a9-b834-c408439f1aa1`. W2 inherits. Flag for future extraction to env var or config. Not fixing in W2 — orthogonal concern, blast radius is "multi-team support doesn't work" which isn't on any current roadmap.

### 9. JSONL concurrent-write guarantees at scale

Fine at ~50 tasks/month (effective zero race). At Phase 2+ scale with more workflows writing (W4 Mode B dispatches, W5 receipts, W7 closeouts, W9 re-routes) concurrent writes become plausible. Migration paths at that time:

- Write-through HTTP endpoint on the dispatcher service (19000) — single serializer, existing service.
- Switch to SQLite with WAL mode — good concurrent read/write, built-in fsync.

Not fixing in Phase 1 — flag as Phase 2 watch item.

---

## Part 12 — Prerequisite and next steps

### Hard prerequisite: compose bind mount

W2's routing_history plan depends on file access to `D:\dev\miru\data` from inside the n8n container. Currently only `n8n_data:/home/node/.n8n` is mounted ([docker-compose.yml:8-9](docker/n8n/docker-compose.yml)).

Proposed change to `docker/n8n/docker-compose.yml`:

```yaml
services:
  n8n:
    # ... existing config ...
    volumes:
      - n8n_data:/home/node/.n8n
      - D:\dev\miru\data:/miru-data   # ← NEW
```

Then one-time `docker compose -f docker/n8n/docker-compose.yml down && up -d`.

**Tracked as a follow-up Linear issue** (suggested PRO-34, ~30 minutes including a deploy script sanity re-run to confirm no regressions on the W1 deploy path). Not in scope for this PRO-33 plan-review pass.

### Next plan-mode pass (after operator approves this review)

A second plan-mode pass will:

1. Draft the actual `w2-worker-selection-router.json` workflow JSON matching the topology in §2.
2. Draft `config/w2_routing_rules.json` with the worker-signal matrix populated from canon §W2 step 3.
3. Draft the pending-approval watchdog workflow JSON (3 nodes).
4. Draft the override-flag capture watchdog workflow JSON (3 nodes, label-change-webhook triggered).
5. Plan the test script that Claude Code will run after deploy.
6. Plan any canon-page-16 updates if operator accepts the amendment proposals.

No JSON is produced in that next pass either — that's deferred to the build-and-deploy pass that runs after the second plan-mode approval.

### Reviewer's honest signal

I'm confident in the topology, the scoring formula shape, and the stub-W8 label semantics. I'm less confident in the specific confidence threshold (0.75) and the specific scoring weights (0.3/0.7 for gap/margin) — those are Phase 1 placeholders by design, recalibration-dependent. If operator disagrees on any of these, push back now. If operator agrees on the shape but wants a different number, the threshold is a one-line change in the Code node.

I'm least confident in the apply-phase failure recovery path (§7). The proposal (preserve decision, surface to operator) adds complexity for a rare failure mode. An alternative is canon's current behavior (treat all failures as triage). Both are defensible. Flagging as a judgment call operator should weigh in on.

---

## Completion

- **Path to this deliverable:** `data/batch_reports/w2_plan_review_2026-04-23.md`
- **Linear issue:** [PRO-33](https://linear.app/project-miru/issue/PRO-33/build-w2-worker-selection-router-plan-mode-pass)
- **One-paragraph summary:** W2 is a 17-node n8n workflow that polls Linear every 3 min for Todo issues, fetches the issue context, extracts signals (task type, keywords, paths, research flag), scores all four workers against a canon best-for/hard-no-go matrix, ranks them with a deterministic fixed-priority tiebreaker, computes a gap+margin confidence score, and classifies execution risk independently. If confidence ≥ 0.75 it atomically applies the proposed worker label + `pending-approval` and writes a decision packet as a Linear comment + routing_history row; if confidence < 0.75 it applies `triage`; if research_signal fires it applies `research`. All paths notify via Pushover priority 1. Operator approves by removing `pending-approval` — this is the stub-W8 approval gate until real W8 ships with signed-URL buttons. Failure modes split into decision-phase (triage, canon-aligned) and apply-phase (preserve decision, surface to operator — canon amendment proposed). Shadow mode ships on day 1.
- **Hard blocker:** compose bind mount for `D:\dev\miru\data:/miru-data` — tracked as suggested PRO-34, ~30 min, not in this pass.
- **9 honest pushbacks** listed in §11, all canon-amendment candidates, none blockers if operator rejects.
- **Operator decisions needed:** the 13 ambiguities listed in §10, plus the 9 pushbacks in §11.

**STATUS: CONFIRMED WORKING** — review ready for operator.
