# Ticket Frontmatter Dispatch Annotation Schema

**Status:** PROPOSED — Phase 1 spec, locked 2026-05-05 after PXY + GMI peer review.
**Owner:** Local Governance Gatekeeper (when shipped). Until then, reference doc only.
**Related canon:**

- Repo: `data/peer_reviews/2026-05-05_local-router-architecture_gmi.txt`
- Repo: `data/perplexity_research/miru-router-context.md`

---

## Purpose

Carry structured dispatch metadata at the **Linear ticket** level so the
Local Governance Gatekeeper can validate worker intent against the
ticket-of-record and prevent context drift between conversational dispatch
and worker execution.

GMI flagged "Context Fragmentation" — chat refines the task while the ticket
stays static — as the single biggest design risk. PXY confirmed Linear
doesn't have traditional key-value custom fields (only label groups + Asks
fields, the latter tier-gated and intake-only). Both reviewers converged on
the same practitioner pattern: **structured frontmatter in the ticket
description body, written at ticket creation by CC (Claude Code).**

The Gatekeeper reads the frontmatter as the original-intent gospel and the
`conversational_delta` (passed via the future `cc_handoff` MCP tool) as the
refinement. Contradictions trigger a Phase 2.5 Rejection Loop instead of a
silent dispatch.

---

## Format

A single HTML comment placed at the **top of the Linear ticket
description body**, containing YAML.

```yaml
<!-- dispatch:
  worker: claude-code
  scope: backend/auth
  context_files:
    - src/middleware/auth.py
    - CLAUDE.md
  expected_mode: judgment
  expected_tool_profile: standard_worker
  plan_only: false
  do_not_touch:
    - card_catalog.db
    - .mcp.json
-->
```

**Why HTML comment + YAML inside:**

- HTML comments don't render in Linear's UI — keeps the ticket description
  human-readable for the operator.
- YAML inside the comment parses cleanly with `yaml.safe_load` after
  stripping the wrapper.
- Both Linear's Markdown view and the GraphQL `description` field preserve
  comments verbatim.

**Where the parser lives:** Local Governance Gatekeeper (Phase 1 component).
A standalone Python helper at `tools/gatekeeper/frontmatter_parser.py` will
extract and validate frontmatter from any ticket description string.

---

## Schema fields

| Field                         | Type         | Required    | Notes                                                                                                                                                                            |
| ----------------------------- | ------------ | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `worker`                      | enum         | **yes**     | `claude-code` / `gemini` / `both` / `none`. The intended dispatch target at ticket-creation time. The Gatekeeper compares this against the conversational delta and label state. |
| `scope`                       | string       | **yes**     | Free-form domain hint (e.g. `backend/auth`, `frontend/storefront/cards`). Used as a coarse routing signal and a human readability anchor.                                        |
| `context_files`               | string array | recommended | Repo-relative file paths the worker should read to start. Empty array means the worker decides. Keeps the worker from going hunting and burning time on irrelevant exploration.  |
| `expected_mode`               | enum         | recommended | `routine` / `judgment` / `ambiguous` / `blocked`. The mode CC believed at ticket creation. Helps the Gatekeeper detect if the conversation pushed toward ambiguity.              |
| `expected_tool_profile`       | enum         | recommended | `drift_executor` / `standard_worker` / `reviewer` / `null`. Matches Phase 3 gateway profile names.                                                                               |
| `plan_only`                   | bool         | recommended | If true, the dispatched worker produces a plan and stops; no branches, PRs, or file modifications. Default `false`.                                                              |
| `do_not_touch`                | string array | optional    | Files or paths the worker must avoid. Hard scope boundary.                                                                                                                       |
| `parent_conversation_summary` | string       | optional    | Plain-English one-liner of the conversation that led to ticket creation. Hashed by the Gatekeeper into `parent_conversation_summary_hash` for staleness detection.               |
| `dispatch_priority`           | enum         | optional    | `urgent` / `normal` / `low`. Maps to the Gatekeeper's timeout/escalation behavior.                                                                                               |

### Closed enum reference

```yaml
worker:
  - claude-code
  - gemini
  - both
  - none

expected_mode:
  - routine # auto-eligible for drift_executor
  - judgment # default for code work
  - ambiguous # plan_only injection
  - blocked # no dispatch — depends on something

expected_tool_profile:
  - drift_executor # read-everything, write-nothing routine work
  - standard_worker # default ticket-executing dispatch
  - reviewer # peer-review pass, like drift_executor
  - null # no profile required (rare; manual override only)

dispatch_priority:
  - urgent
  - normal
  - low
```

---

## Validation rules

The Gatekeeper rejects a frontmatter as invalid (Phase 2.5 Rejection Loop)
when:

1. The HTML comment + YAML wrapper is malformed or unparseable.
2. A required field is missing (`worker`, `scope`).
3. An enum field contains a value outside the closed set above.
4. `worker: claude-code` paired with `expected_mode: ambiguous` and
   `plan_only: false` — ambiguous tasks must run plan-only.
5. `worker: none` paired with anything other than `expected_mode: blocked`.
6. `do_not_touch` lists a file outside `D:\dev\miru\` (out of scope by
   project rules).

Validation is **deterministic** — no LLM call needed. It runs as part of
the deterministic floor before the Gatekeeper invokes Llama 3.1 8B.

---

## How the Gatekeeper uses frontmatter

When CC calls the future `cc_handoff` MCP tool, the payload includes:

- `ticket_id`
- `conversational_delta` (pre-processed to highlight intent-changing
  tokens — "instead do X", "I already did Y" — per GMI 2026-05-05;
  not raw prose dump)

The Gatekeeper:

1. Pulls the ticket from Linear via MCP, extracts the frontmatter via
   `frontmatter_parser.py`.
2. Snapshots `git_local_status` against the **main repo root** (per
   `MIRU_REPO_ROOT` from PR #89, not worker worktrees — catches CC
   self-serve attempts on core branch).
3. Checks A2A bus state (`agent_messages` table): if the trace_id is
   already `claimed` or `pending`, deterministic reject.
4. Compares frontmatter `expected_*` fields against `conversational_delta`:

| Situation                                                                                                          | Gatekeeper action                                                                                                                                      |
| ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| No delta provided                                                                                                  | Frontmatter is gospel. Dispatch verbatim.                                                                                                              |
| Delta refines without contradicting (e.g. adds a context file)                                                     | Enrich the dispatch payload with the delta. Dispatch the union.                                                                                        |
| Delta materially contradicts frontmatter (e.g. delta says "use gemini" but frontmatter says `worker: claude-code`) | Phase 2.5 Rejection: `reason: ticket_drift_unresolved`. Operator must "Finalize the Delta" in Linear (update frontmatter to match) before re-dispatch. |
| Repo state shows the work is already done                                                                          | Phase 2.5 Rejection: `reason: already_completed` or `ghost_task`.                                                                                      |
| Repo state shows uncommitted changes on the main branch                                                            | Phase 2.5 Rejection: `reason: dirty_worktree`.                                                                                                         |

5. If validation passes, the Gatekeeper emits a routing decision JSON
   and signs the dispatch to the existing `dispatch_listener` on port 19100.

6. **Every Gatekeeper decision** (accept/reject/enrich) is logged as a
   `judgment_driven` entry in `agent_decisions.jsonl` (per GMI 2026-05-05
   recommendation), so shadow-mode benching is auditable against the
   actual project history.

---

## Examples

### Backend bugfix — clear scope, single worker

```yaml
<!-- dispatch:
  worker: claude-code
  scope: backend/auth
  context_files:
    - miru_ai/middleware/auth.py
    - tests/test_auth_middleware.py
  expected_mode: judgment
  expected_tool_profile: standard_worker
  plan_only: false
-->
```

### Frontend visual refactor — Gemini territory

```yaml
<!-- dispatch:
  worker: gemini
  scope: frontend/storefront/cards
  context_files:
    - pm/storefront/templates/card_detail.html
    - pm/storefront/static/css/card_detail.css
    - docs/pm/02_PM_PRIMITIVES.md
  expected_mode: judgment
  expected_tool_profile: standard_worker
  plan_only: false
-->
```

### Routine drift sweep — read-only, drift_executor profile

```yaml
<!-- dispatch:
  worker: claude-code
  scope: meta/drift
  context_files: []
  expected_mode: routine
  expected_tool_profile: drift_executor
  plan_only: false
-->
```

### Hybrid task — both workers via sub-issues

Parent ticket (no dispatch, envelope only):

```yaml
<!-- dispatch:
  worker: none
  scope: feature/leader-banner-dock
  expected_mode: blocked
-->
```

Children (separate Linear sub-issues, each dispatchable):

```yaml
<!-- dispatch:
  worker: claude-code
  scope: backend/leader-banner
  context_files:
    - miru_ai/api/leader_banner.py
  expected_mode: judgment
  expected_tool_profile: standard_worker
  plan_only: false
-->
```

```yaml
<!-- dispatch:
  worker: gemini
  scope: frontend/leader-banner-ui
  context_files:
    - pm/storefront/templates/deck_builder.html
    - docs/pm/04_WATCHLIST_AND_METER.md
  expected_mode: judgment
  expected_tool_profile: standard_worker
  plan_only: false
-->
```

### Investigation / planning task — plan_only true

```yaml
<!-- dispatch:
  worker: claude-code
  scope: investigate/dispatch_listener
  context_files:
    - services/dispatch_listener/src/spawn.js
    - logs/dispatch_listener_stdout.log
  expected_mode: ambiguous
  expected_tool_profile: reviewer
  plan_only: true
-->
```

---

## Migration / rollout

- **Phase 1 (shadow mode):** Frontmatter is parsed but not enforced.
  Gatekeeper logs decisions to `agent_decisions.jsonl`; existing dispatch
  flow continues unchanged via `dispatch_worker`.
- **Phase 2 (`cc_handoff` ships):** CC starts writing frontmatter at
  ticket creation as a new habit. Both `dispatch_worker` and `cc_handoff`
  are available; either path works.
- **Phase 3 (cutover):** `dispatch_worker` removed from CC's tool
  profile. `cc_handoff` is the only path. Tickets without frontmatter
  default to `worker: standard_worker, mode: judgment` and dispatch
  conservatively.

---

## Why frontmatter and not Linear custom fields

Linear doesn't have traditional key-value custom fields. What exists:

- **Label groups** — closest analog. Limited to enum-like values; can't
  carry arrays (e.g. `context_files`) or freeform strings.
- **Asks fields** — Business/Enterprise tier only, attached to intake
  forms not standard issues.
- **Triage routing rules** — Enterprise-only.

PXY's research confirmed the practitioner workaround: embed structured
metadata in the description body. Zero infrastructure cost, zero tier
upgrade required, full schema flexibility. The Gatekeeper does the
parsing.

---

## Hard rules

1. The frontmatter comment must be the **first content** in the ticket
   description body. Anything before it is invalid.
2. The frontmatter is **read-only** to all workers. Only CC (or the
   operator via Linear UI) updates it. Workers reading frontmatter for
   guidance is fine; workers writing or modifying it is a violation.
3. The frontmatter does NOT replace the operator's plain-English ticket
   description. The description below the comment carries the human
   context (goal, acceptance criteria, don't-touch list); the comment
   carries the machine-readable dispatch metadata.
4. If the frontmatter and the plain-English description disagree, the
   plain-English description is the source of truth for the operator's
   intent — the Gatekeeper should treat the discrepancy as a sign that
   CC has drifted from the ticket and rejects with `reason:
ticket_drift_unresolved`.

---

_Last updated: 2026-05-05 — Phase 1 spec lock after PXY + GMI peer review._
