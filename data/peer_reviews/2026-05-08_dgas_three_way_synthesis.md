# DGAS Three-Way Synthesis — CC + GMI + PXY

```text
Research synthesis
Reviewers: Claude Code (CC), Gemini Deep Research (GMI), Perplexity Deep Research (PXY)
Date: 2026-05-08
Status: ARCHITECTURE LOCK — ready for operator merge approval to begin building
```

Three independent reviews of the DGAS framework. Strong convergence on architecture; divergence on which gap closes first.

---

## CONSENSUS — All three agree (highest confidence; build first)

### 1. The Decision Rule: when does code-enforcement become mandatory

**Synthesized rule (combines all three):**

A rule MUST be code-enforced (not prompt-only) if violation has ANY of these properties:

- **Irreversible** — force-push to main, drop table, delete unmerged work, secret leak, money movement (CC + GMI + PXY agree).
- **Undetectable by the worker itself within the same session** — completion-marker corruption, test-evidence falsification, audit-log tampering. Worker can't reliably report its own non-compliance (PXY's framing).
- **Implicit trust elevation** — changes to gateway profiles, `.miru/overlays/`, pre-commit hooks, validator scripts. These change the trust surface for future actions, not just the current task (PXY's framing).
- **State integrity violation** — operations that corrupt persistent state in ways that contaminate downstream reasoning (GMI's framing).

Otherwise prompt-enforce. Rules in prompts are requests; hooks are laws.

### 2. Localhost-bind the `full_operator` gateway default

All three reviews agree this is a critical gap. The current "no header = full_operator" pattern is fail-open. PXY gave the most concrete fix:

```python
if profile == "full_operator" and request.remote_addr != "127.0.0.1":
    raise McpError(-32003, "full_operator requires local origin")
```

GMI extends this with "Token of Presence" for future Tailscale/remote access — a short-lived token generated only when the operator is in an active local session. CC was more conservative initially ("keep as-is, document the assumption") but agrees with the harder bind.

**Verdict: do PXY's 5-line bind THIS WEEK. Add Token of Presence when remote access expands.**

### 3. Hash-chain the audit logs

All three agree append-only files alone are necessary-but-insufficient. Pre-commit excludes prevent accidental rewrite, not deliberate tampering.

- CC suggested chaining only 3 critical logs (completion, vp_ops_supervision, agent_decisions). Conservative.
- PXY argues chain ALL JSONL with a daily anchor file (`data/audit_anchors.jsonl`).
- GMI argues chain `agent_decisions.jsonl` with periodic anchoring every 10 entries.

PXY+GMI converged on more aggressive chaining than CC. SHA-256 is cheap (microseconds per write); chaining all 9 JSONL has near-zero overhead.

**Verdict: hash-chain all 9 append-only files. Add `tools/verify_audit_chain.py` (CI step). Add daily anchor file `data/audit_anchors.jsonl` (itself chained).**

### 4. Pre-commit secret scanning

All three converge: TruffleHog and/or Gitleaks as pre-commit hooks. PXY adds detect-secrets baseline for noise filtering. PXY also flags GitHub Push Protection as the server-side backstop (because workers can bypass local hooks).

**Verdict: TruffleHog + Gitleaks as pre-commit, plus GitHub Push Protection enabled (free for the repo).**

### 5. GitHub branch protection on `main`

CC + PXY explicit, GMI implicit (mentions "secure your source of truth at GitHub"). All agree it's a 2-minute config with zero ongoing cost and eliminates the force-push class entirely.

**Verdict: enable branch protection rules immediately. Disable force-push, disable branch deletion, require PR before merge, require status checks.**

### 6. Governance file registry + special merge path

PXY and GMI both propose this with different framings.

PXY: "Governance File Registry" — any PR touching a file in the registry triggers different workflow. Worker cannot self-merge. PR body must include `GOVERNANCE_CHANGE_APPROVED=true` and a "what does this change allow that wasn't allowed before?" statement. 24-hour waiting period for permission expansions.

GMI: "Multi-Stage Gating + Two-Reviewer Rule" — governance changes are Tier 3 high-risk, operator-only merge, mandatory conflict-check.

Files in the registry: `gatekeeper/`, `tools/miru_mcp_gateway/profiles.py`, `.miru/overlays/`, `.miru/reference/`, `.pre-commit-config.yaml`, any `tools/check_*.py` or `tools/validate_*.py`, `data/config/w2_profile_rules.json`.

**Verdict: build this. Implementation is a pre-commit hook + a `tools/validate_governance_change.py` that checks PR body has the required field. Operator-only merge is enforced by GitHub branch protection (CODEOWNERS for governance paths).**

### 7. Governance metrics — fault injection tests per gate

PXY and GMI both surfaced this (CC missed it). Without fault-injection tests, you can't tell working governance from theatre.

PXY: track `gate_fired_count`, `gate_blocked_count`, `gate_pass_rate`, `gate_bypass_attempts` per gate, log to `data/governance_metrics.jsonl`. Add `tests/test_governance_gates.py` with one fault injection per gate. A gate with 0 blocks and 0 fault injection tests is the red flag.

GMI: same idea framed as "Boundary Hit Rate" — measure blocked vs attempted, with a "Stall Loop" detection for over-restrictive gates.

**Verdict: build `tests/test_governance_gates.py` with one fault injection per existing gate. Make it CI-required. Add `data/governance_metrics.jsonl` (append-only, will be hash-chained per item 3).**

---

## DIVERGENCE — Where reviews differ (operator decision points)

### 8. The "what to do FIRST" disagreement

This is the most interesting divergence. Three different #1 priorities:

| Reviewer | "Do this first"                                          | Argument                                                                                                     |
| -------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **CC**   | Branch protection on main (server-side force-push block) | 2-min config, zero ongoing cost, closes force-push class entirely                                            |
| **PXY**  | Localhost-bind `full_operator` gateway                   | Single missing header grants unrestricted access to any caller — widest current blast radius per line of fix |
| **GMI**  | Hardened git-execution wrapper (client-side)             | Agents have raw git access; most common autonomous failure modes are git-related                             |

**Synthesis: these are NOT competing.** CC's branch protection (server-side) + GMI's git wrapper (client-side) cover different layers of the same gap. PXY's localhost bind closes a different gap entirely. **Do all three. Order: easiest first.**

Recommended sequence:

1. **GitHub branch protection** (Day 1, 2 minutes — UI clicks)
2. **Localhost-bind `full_operator`** (Day 1, ~5 lines in gateway code)
3. **Pre-commit secret scanner** (Day 1, ~30 min config)
4. **Git-execution wrapper** (Day 2-3, ~2 hours of code + tests)
5. Then move to Tier 2.

### 9. Audit chain scope

- CC: 3 critical logs only.
- GMI: `agent_decisions.jsonl` plus periodic anchoring.
- PXY: ALL 9 JSONL files plus daily anchor.

**Operator decision needed.** PXY's "all 9 + daily anchor" is the most defensible position (SHA-256 is cheap). CC's "3 critical only" is a defensible cost-cutter. GMI's middle position is the smallest meaningful step.

I'd lean toward PXY's approach: chain all 9. Once we're chaining anything, we may as well chain everything. The infrastructure cost is identical; the marginal effort to add 6 more files is near-zero. **My recommendation to operator: PXY's approach.**

### 10. Schema-change / DB-write enforcement

- CC: deny direct DB writes via existing MCP gateway profile deny-list.
- PXY: two-key authorization for schema changes (env var `SCHEMA_CHANGE_APPROVED=<operator_token>`).
- GMI: pre-execution wrapper for DB destructive ops + backup snapshot check.

**These are complementary, not competing.** Layer them:

1. Gateway profile deny-list blocks direct writes (CC) — first line of defense.
2. Pre-execution wrapper (GMI) catches anything that escapes the gateway.
3. Two-key env var (PXY) for schema changes specifically — most destructive class.

---

## WHAT EACH REVIEW SURFACED THAT THE OTHERS MISSED

### CC's contributions

- The "agents reviewing agents" trap — at our scale, adding more verification layers becomes bureaucracy without proportional safety gain. Don't add more verification agents unless a specific failure justifies it.
- The decision rule explicit math: "false-positive denial cost < single true-positive miss cost."
- Tier-based prioritization that explicitly defers OPA/Cedar policy engine and Vault as "overkill at our scale."

### PXY's contributions

- The "undetectable by the worker itself" axis for the decision rule — captures self-certification failures as a code-enforce trigger.
- The "implicit trust elevation" axis — captures governance-file changes as a special category.
- Fault injection tests per gate. Without these, you cannot distinguish working governance from theatre.
- Specific 5-line gateway code for the localhost bind.
- Log redaction proxy pattern — workers call `log_write(msg)` not `file.write(msg)`, with regex scrubbing before write.

### GMI's contributions

- "Token of Presence" pattern for future remote access — short-lived token bound to active OS session, rotates with session lifecycle.
- "Network Gating during build" — borrowed from Cognition/Devin VPC isolation. Workers shouldn't have unrestricted external network during code generation. Worth considering for our worktrees.
- "Migration tests" for instruction overlay changes — when an overlay changes, run a smoke test to verify it doesn't contradict existing core canon.

---

## THINGS NO REVIEW FLAGGED (gaps in our triangulation)

- **Network egress controls** — workers have unrestricted outbound HTTP. A worker could exfiltrate via API call. Real risk; none of the three explicitly addressed it. (GMI hinted at "network gating during build.")
- **Subprocess execution** — workers can `subprocess.run(["bash", "-c", "..."])`. Pre-commit hooks don't catch this. Worth thinking about whether the MCP gateway should be the only path for shell access.
- **Package install/remove** — `npm install` from arbitrary registries, `pip install` from anywhere. Supply chain attack surface. Not addressed.
- **The deferred failure classifier (item #5 from Hermes brief)** — none of the three reviews directly addressed whether DGAS subsumes this. My read: failure classifier is a separate ticket, but it benefits from the audit chain and governance metrics work.

---

## FINAL RECOMMENDED BUILD ORDER

### Tier 1 — This week (5 items, ~1 day of work total)

1. **GitHub branch protection on `main`** — disable force-push, require PR, require status checks. UI configuration.
2. **Localhost-bind `full_operator`** — 5-line gateway change. Reject when `request.remote_addr != "127.0.0.1"`.
3. **Pre-commit secret scanner** — TruffleHog + Gitleaks. Add to `.pre-commit-config.yaml`.
4. **GitHub Push Protection** — enable in repo settings. Free, server-side backstop for secret scanning.
5. **MCP gateway profile: deny direct DB writes** — extend `tools/miru_mcp_gateway/profiles.py` to add `sqlite_write_card_catalog` to the deny-list for all profiles except a hypothetical operator-only one.

### Tier 2 — Next sprint (5 items, ~1 week of work)

6. **Hash-chain all 9 JSONL audit files** — add `prev_hash` field. New helper `tools/audit_chain.py`. Add `tools/verify_audit_chain.py` to CI.
7. **Daily audit anchor** — write tail hash of each JSONL to `data/audit_anchors.jsonl` (chained itself). Cron job or git hook.
8. **Git-execution wrapper** — wrap dangerous git ops (`force-push`, `branch -D`, `reset --hard`). Reject without explicit operator approval.
9. **Governance file registry + pre-commit hook** — define registry, enforce `GOVERNANCE_CHANGE_APPROVED=true` field in PR body for governance-file changes. CODEOWNERS for operator-only merge.
10. **Fault injection tests per gate** — `tests/test_governance_gates.py`. One test per existing gate. Make CI-required.

### Tier 3 — Backlog (consider when system grows)

- OPA/Cedar policy engine (when tool-call volume justifies the operational cost).
- HashiCorp Vault for runtime secret injection (when threat model expands beyond single trusted node).
- Token of Presence for remote access (when Tailscale or remote workers expand).
- Network egress allowlist per worker profile (when supply-chain risk increases).
- Subprocess execution gating (when workers gain access to operator-side scripts).
- Migration tests for overlay changes (when overlay churn increases).

### Permanently deferred (overkill for our scale)

- Obfuscation membrane (we don't have customer IDs/tokens to strip).
- VM-per-worker isolation (worktrees are sufficient at our scale).
- Two-human approval for governance changes (we're single-operator; PXY's 24-hour waiting period is the equivalent).
- Hardware key (YubiKey) for two-key auth (single-operator threat model doesn't justify it).

---

## METRICS TO TRACK ONCE TIER 2 IS LIVE

Add to `data/governance_metrics.jsonl` (append-only, hash-chained):

- Per gate: `gate_fired_count`, `gate_blocked_count`, `gate_pass_rate`, `false_positive_count` (operator overrides).
- Per worker: tool-call count, denial count, denial rate, escalation count.
- Per ticket: pre-flight pass rate, completion-marker validity rate, VP Ops verdict distribution.
- Weekly aggregate: which gates fired 0 times (suspicious — either perfect compliance or dead code).

The "weekly 0-fire gate" alert is the canary for governance theatre.

---

## OPEN QUESTIONS FOR THE OPERATOR

Before building Tier 1, please confirm:

1. **Audit chain scope**: PXY's "all 9 logs + daily anchor" or CC's "3 critical only"? My recommendation is PXY.
2. **Tier 1 ordering**: I propose branch protection → localhost bind → secret scan → push protection → DB write deny. Acceptable?
3. **Governance file registry**: any files that should be in the registry that I haven't listed?
4. **Token of Presence vs simple localhost bind**: do this week's localhost bind, or jump straight to the more elaborate Token of Presence pattern? I'd do localhost bind now and Token of Presence as a follow-up.
5. **Network egress + subprocess gating**: surface as separate Tier 2 items, or bundle into the gateway profile work?
6. **Failure classifier (item #5 from Hermes brief)**: stays as separate ticket, or fold into Tier 2 work?

Once you've decided on those, I can start building Tier 1 immediately.
