# DGAS Research — CC's Synthesis

```text
Research: Deterministic Governed Autonomous System (DGAS)
Author: Claude Code (CC)
Date: 2026-05-08
Sources: 2 Perplexity Deep Research passes (implementation patterns + small-scale governance)
Companion reviews: GMI Deep Research, Perplexity (operator-relayed)
```

This is my synthesis. GMI and PXY may converge or diverge — we'll triangulate.

---

## The Big Tension

Both research passes agree on the architecture, but **disagree on aggressiveness**:

- **Implementation research** advocates a full Sovereign Agentic Loop (SAL) with policy engines (OPA/Cedar), Vault-backed JIT secret materialization, hash-chained audit trails, two-key authorization, and 12-15ms latency overhead per tool call.
- **Small-scale research** warns: "scaling down enterprise patterns" doesn't work. Solo operators face fundamentally different constraints — operator fatigue, single-point-of-failure approval, coordination overhead exceeding parallelism benefit. Successful patterns are git worktrees + JSONL audit + Telegram approvals + ship-first.

**The synthesis: pick the SAL components that translate to our scale, reject the ones that don't.**

The implementation research gives us the "what good looks like at scale." The small-scale research tells us the inflection points where each component starts paying for itself. We're a 1-operator, 1-node, 2-active-agents system. Most enterprise patterns are overkill for us right now — but the _threat models_ they protect against still apply.

---

## What We Already Have (mapped to research vocabulary)

| Research term                | What we have                                    | Status                                                                     |
| ---------------------------- | ----------------------------------------------- | -------------------------------------------------------------------------- |
| Control plane (SAL)          | MCP gateway with profile enforcement            | **Partial** — profile-based deny, but no per-call policy evaluation        |
| Obfuscation membrane         | None                                            | **Missing** — workers see real IDs, paths, tokens                          |
| Evidence chain               | 9 append-only JSONL files                       | **Partial** — append-only but not hash-chained                             |
| Pre-execution policy         | Ingress classifier (w2008a)                     | **Partial** — assigns profile, doesn't validate per-call                   |
| Irreversible op gating       | None programmatic                               | **Missing** — force-push, schema changes, secrets all rule-based-only      |
| JIT secret materialization   | None                                            | **Missing** — workers read .env files                                      |
| Pre-commit secret scanning   | None                                            | **Missing** — only formatting/lint hooks                                   |
| Branch protection            | Unknown — need to verify                        | **Likely missing** — no force-push block on main                           |
| Two-key authorization        | Telegram approval flow (operator-tap)           | **Partial** — exists for dispatch, not for irreversible ops                |
| Tamper-evident audit         | Append-only files + pre-commit excludes         | **Partial** — accidental tamper-resistant, not deliberate-tamper-resistant |
| Worker isolation             | Git worktrees per worker slot                   | **Good** — already implemented                                             |
| Verification agent           | VP Ops (Claude Code via vp_ops_verify_ticket)   | **Good** — 6 deterministic checks per ticket                               |
| Stall detection              | tools/orchestrator/stall_detector.py            | **Good** — heartbeat staleness + recovery routing                          |
| Specialized failure recovery | recovery_router.py per Augment 48-mode taxonomy | **Good** — provisional, awaiting validation                                |

We're stronger than most solo operators (research called out the "Council of 15" and similar setups that have far less infrastructure). We're weaker than enterprise SAL setups (no policy engine, no obfuscation, no hash chaining).

---

## The Decision Rule for "Code-Enforce vs Prompt-Enforce"

From the research, distilled:

**Code-enforce when ALL of:**

1. Violation causes irreversible damage (data loss, secret leak, service outage, money movement)
2. The check can be expressed deterministically (you can write a regex, a SQL query, a syscall filter)
3. The cost of false-positive denial is lower than the cost of one true-positive miss

**Prompt-enforce when:**

1. The decision requires judgment (which worker is best for this ticket)
2. The rule is heuristic, not categorical
3. False-negative rate is tolerable (the worst case is "operator review")

**Code-enforce concrete examples that map to our gaps:**

- Force-push to main → GitHub branch protection rule (one-time setup, zero ongoing cost)
- Direct write to card_catalog.db → MCP gateway tool ban (we already have profile enforcement; just deny `sqlite_write` for any non-vp_ops profile)
- Secret in commit → pre-commit hook (gitleaks or trufflehog)
- Schema change without approval → pre-commit hook on migration files + branch protection requiring review

**The one principle that ties this together** (from implementation research, paraphrased):

> "AI provides intelligence; deterministic code provides governance. Rules in prompts are requests. Hooks in code are laws."

If we believe that, then any hard-rule we have today that could be code-enforced but isn't is a **discipline gamble**. We're betting the LLM follows the rule. Sometimes it doesn't. The cost of that bet matters more for irreversible actions than for routine ones.

---

## What I'd Build First (priority order, accounting for our scale)

**Tier 1 — Catches biggest gaps for least effort:**

1. **GitHub branch protection rule on `main`** — Block force-push for everyone except a designated bypass token (which only the operator holds). One-time setup, immediately closes the "force-push to main" gap. **No ongoing cost.**

2. **Pre-commit secret scanner** — Add `gitleaks` or `trufflehog` to the existing pre-commit stack. Catches the "secret in commit" failure mode the research called out as a top-3 issue at small scale. **<2 sec per commit.**

3. **MCP gateway tool ban for direct DB writes** — We already have profile-based deny lists. Add `sqlite_write_card_catalog` (or whatever the actual MCP tool name is) to the deny list for ALL profiles except a hypothetical `db_admin` profile that only the operator's direct session can claim. **Zero new infra.**

**Tier 2 — Hardens what we have:**

4. **Hash-chained completion log** — Upgrade `data/cc_completion_log.jsonl` so each entry includes the SHA of the previous entry. Detects deliberate tampering, not just accidental. Implementation research had a 50-line Python class. Other 8 append-only files can stay as-is until we have a reason to chain them. **One-time migration, ~2 hours.**

5. **Operator allowlist for irreversible-ops Telegram button** — Right now, the operator approves dispatches via Telegram tap. Add a second class of approval: "irreversible op confirmation" that requires explicit text reply (not just a button) so an accidental tap can't authorize a destructive action. **Extends w2008a-style flow.**

6. **Pre-execution wrapper for irreversible git ops** — A small Python script that workers MUST call instead of `git` directly for `force-push`, `branch -D`, `reset --hard`. The wrapper checks: is this action in the operator-confirmed irreversible-ops queue? If not, deny. **Zero new infra; just a wrapper script.**

**Tier 3 — Probably overkill for us right now, mark as "consider later":**

7. **Full OPA/Cedar policy engine** — Adds 12-15ms per tool call, requires running OPA as a service, requires writing Rego/Cedar policies and testing them. The research is clear this matters at scale. We have ~50 tool calls per dispatched task. We don't have the volume to justify the operational overhead yet. **Revisit when we have 5+ active agents or external API surface.**

8. **HashiCorp Vault for JIT secret materialization** — Workers read .env today. Vault would replace .env with runtime-fetched secrets. Real benefit but real complexity (Vault server, MCP integration, token rotation). For a single-operator on a trusted box, .env is fine if we add (a) pre-commit scanning to prevent leaks (Tier 1 #2) and (b) `.gitignore` discipline (already have). **Revisit when our threat model changes — e.g., remote workers or shared infrastructure.**

9. **Obfuscation membrane** — Strips IDs, tokens, identifiers from worker context before LLM inference. Useful at scale where workers see thousands of customer IDs. For us, workers see PRO-XXX ticket IDs and our own infra paths. Low value right now. **Skip.**

10. **Litestream replication of SQLite audit DB** — Implementation research suggests this. We don't even have a SQLite audit DB; we have JSONL. Migration would be its own project. **Revisit only if JSONL becomes painful.**

---

## Specific Architectural Decisions to Make Before Building

### 1. Where does the Control Plane live?

Two valid options for our scale:

**Option A (current):** MCP gateway IS the control plane. Profile enforcement happens at the gateway layer. Per-call policy is added as more profile rules. Lightweight, no new component.

**Option B:** Add a separate "admissibility wrapper" between the worker and the MCP gateway. A small Python service that intercepts MCP calls, runs deterministic checks (matching tool name + args against allowlist/denylist), then forwards to the gateway.

Implementation research advocates B (true separation of concerns). I think A is right for us — adding B doubles operational surface. Our profiles already do most of what B would do. The improvement to make A more capable: extend profile definitions from "allowed tool list" to "allowed tool list + arg validators."

### 2. Audit chain: every log or just the critical ones?

Research argues for hash-chaining everything. For us:

**Critical (chain these):**

- `data/cc_completion_log.jsonl` — work outcomes, used by VP Ops verification
- `data/vp_ops_supervision.jsonl` — verification verdicts
- `data/agent_decisions.jsonl` — Phase 2 Judgment Trail (calibration corpus)

**Not critical (leave as plain append-only):**

- `data/cc_heartbeat_log.jsonl` — operational telemetry, low tampering risk
- `data/routing_history.jsonl` — high volume, tampering survives only as bad statistics
- `data/pending_callbacks.jsonl`, `data/dispatch_dlq.jsonl`, `data/drift_scanner_log.jsonl`, `data/github_resource_ledger.jsonl` — operational

That's 3 chained, 6 plain. Half the work, all the protection where it matters.

### 3. Operator's trusted local session — keep or harden?

Currently: `no header = full_operator`. Research flagged this as fail-open.

My take: **keep it for now, with a documented caveat.** The operator's local session running on the ROOM node IS trusted by the threat model (physical access required). What we should add: gateway audit log writes a clear `principal: operator-local-session` flag for every call where no header was sent. That way if the threat model changes (e.g., we expose any remote port), we have visibility into what was happening under the trusted-session assumption.

Hardening to "verify caller identity (e.g., localhost-only check)" is real protection but is operator-decision territory because it could break workflows we don't fully understand yet.

### 4. The "agents reviewing agents" trap

Small-scale research warned: at our scale, mutual verification can become bureaucracy. We have VP Ops (CC verifying other workers via 6 deterministic checks). That's the right amount. Adding more verification layers (security agent, compliance agent, etc.) would slow things down without proportional safety gain. **Don't add more verification agents unless a specific failure justifies one.**

---

## Things I'm Watching For in the GMI/PXY Reviews

When you get GMI/PXY back, here's what to look for:

1. **Does either of them push for an external policy engine (OPA/Cedar) for our scale?** If yes, ask "what's the smallest deployable form?" Both research passes suggested it but the small-scale one specifically said it's overkill at <5 agents.

2. **Does either flag a deterministic gap I missed?** I covered force-push, schema changes, secrets, audit chaining, Telegram approval text-reply. Possible gaps: package install/uninstall (`npm install`, `pip install` from arbitrary registries), arbitrary subprocess execution (`bash -c "..."`), file deletion patterns, network egress.

3. **Does either propose a different sequencing?** I went Tier 1 (branch protection + secret scan + DB ban), Tier 2 (hash chain + irreversible-ops wrapper), Tier 3 (defer). If GMI/PXY argue for a different order, we should hear why.

4. **Does either propose new metrics?** I didn't call out a "governance pass rate" metric. The implementation research mentions it indirectly (audit log completeness, policy denial rate). Worth tracking.

5. **Does either flag the failure classifier (deferred item #5 from the Hermes brief)?** Both research passes touched failure taxonomies but neither directly mapped to our specific deferred ticket. The DGAS work might subsume the failure classifier — or it might be a separate gap.

---

## TL;DR for the Operator

**What's solid about our current system:** Pre-flight gates, append-only data invariant, profile-based MCP enforcement, VP Ops verification, stall detection. We're well-instrumented compared to most solo AI operations.

**What's exposed today:** Force-push to main isn't blocked. Schema changes aren't gated. Secrets in commits aren't scanned. Audit logs are append-only but not tamper-evident.

**Lowest-effort, highest-impact additions (week 1):**

1. GitHub branch protection rule on main (force-push deny except for operator token)
2. Pre-commit secret scanner (gitleaks or trufflehog)
3. MCP gateway deny-list addition for direct DB writes

**Probably overkill for us right now:** Full OPA/Cedar policy engine, HashiCorp Vault, obfuscation membrane, full hash-chained audit across all logs.

**Ready for your call.** Once GMI/PXY are back, we triangulate. Then we build.
