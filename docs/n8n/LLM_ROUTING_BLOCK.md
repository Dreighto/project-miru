# W2 LLM Routing Block — Design & Readiness Spec

> **Status:** Pre-implementation — ready to wire after Claude Chat connector proves stable in shadow mode.
> Last updated: 2026-04-27
> Owner: Operator (Dreighto)

---

## Purpose

This document defines the LLM routing block that will replace (or shadow) the deterministic Code-node scoring in W2 (`w2007-score-workers` → `w2008-classify-risk` → `w2009-confidence-branch`). It describes the interface contract, shadow mode design, memory strategy, gate conditions, and the tools/connectors to wire before go-live.

---

## Current State (Reference)

W2 currently routes via deterministic keyword scoring:

```
Linear poll → SplitOut → w2007-score-workers (Code)
                              ↓
                         w2008-classify-risk (Code)
                              ↓
                         w2009-confidence-branch (If — 0.75 threshold)
                              ↓
                    Telegram approval → W7 callback
```

Scoring rules live in `/miru-data/config/w2_routing_rules.json`. This is the fallback baseline the LLM block must beat before promotion.

---

## LLM Block Design

### Target Architecture

```
Linear poll → SplitOut → [EXISTING deterministic score — kept as shadow baseline]
                              ↓
                    [NEW] w2-llm-route (HTTP → Claude Chat connector)
                              ↓
                    [NEW] w2-llm-compare (Code — compare LLM vs deterministic)
                              ↓
                    [NEW] w2-llm-confidence-gate (If — gate at 0.80 LLM confidence)
                         /              \
                    agree              disagree / low-conf
                       ↓                    ↓
                  use LLM decision    fallback to deterministic
                       ↓
              Telegram approval (existing W7 path unchanged)
```

### Claude Chat Connector Contract

**Input payload (sent to Claude):**

```json
{
  "task_title": "<Linear issue title>",
  "task_body": "<Linear issue description, trimmed to 500 chars>",
  "task_labels": ["<label1>", "<label2>"],
  "task_priority": "<urgent|high|medium|low>",
  "available_workers": ["claude-code", "codex", "perplexity", "operator"],
  "routing_rules_summary": "<injected from w2_routing_rules.json — key skill mappings only>"
}
```

**Expected output (Claude returns JSON):**

```json
{
  "worker": "claude-code",
  "confidence": 0.87,
  "risk": "low",
  "reasoning": "<1–2 sentence rationale>"
}
```

**Prompt template (store in `/miru-data/config/w2_llm_prompt.txt`):**

```
You are the routing brain for Project Miru, a software dispatch system.
Given a task, assign it to the best available worker and return valid JSON only.

Workers:
- claude-code: Complex code changes, multi-file refactors, architecture decisions
- codex: Targeted code edits, boilerplate generation, quick fixes
- perplexity: Research, documentation lookup, external information gathering
- operator: Approval required, ambiguous scope, sensitive changes

Return ONLY this JSON shape:
{"worker": "<worker>", "confidence": <0.0–1.0>, "risk": "<low|medium|high>", "reasoning": "<1-2 sentences>"}

Task:
Title: {{task_title}}
Body: {{task_body}}
Labels: {{task_labels}}
Priority: {{task_priority}}
```

---

## Shadow Mode (Phase 1 — Required First)

Before LLM decisions route real tasks:

1. **Wire the Claude connector call** after the deterministic score nodes.
2. **Log both decisions** (`deterministic_worker`, `llm_worker`, `llm_confidence`, `llm_reasoning`) to `data/routing_history.jsonl` — add new fields, do not break existing schema.
3. **Always use deterministic result** for actual Telegram approval send.
4. **Review divergence** after 20+ routing events. Promote only if LLM agrees with operator-corrected outcomes ≥ 80% of the time.

**New fields to add to routing_history.jsonl rows:**

```json
{
  "llm_worker": "claude-code",
  "llm_confidence": 0.87,
  "llm_risk": "low",
  "llm_reasoning": "Task involves multi-file refactor across pm/ and miru_ai/.",
  "llm_vs_deterministic": "agree",
  "llm_shadow_mode": true
}
```

---

## Memory Strategy

Claude Chat has no persistent memory by default. Until a vector store connector is wired:

- **Inject routing rules summary** into every prompt from `w2_routing_rules.json` (already exists).
- **Inject last 5 routing decisions** from `routing_history.jsonl` as context (sliding window, tail of file).
- **Inject worker capability map** as a static block in `w2_llm_prompt.txt`.

When a persistent memory tool is available (future):

- Store confirmed operator-approved routing decisions as positive examples.
- Store operator-overridden decisions as negative correction signals.
- Feed both into Claude context on each call.

---

## Confidence Gate Rules

| LLM Confidence  | LLM vs Deterministic | Action                                        |
| --------------- | -------------------- | --------------------------------------------- |
| ≥ 0.80          | Agree                | ✅ Use LLM decision                           |
| ≥ 0.80          | Disagree             | ⚠️ Flag in Telegram message, use LLM decision |
| < 0.80          | Any                  | 🔁 Fallback to deterministic                  |
| Error / timeout | Any                  | 🔁 Fallback to deterministic                  |
| Malformed JSON  | Any                  | 🔁 Fallback to deterministic                  |

**Telegram approval message** should indicate routing source:

- `[LLM ✓]` — LLM decision used, high confidence
- `[LLM ⚠]` — LLM disagrees with deterministic, operator should note
- `[Rule]` — deterministic fallback used

---

## Gate Conditions for Promotion (Shadow → Live)

Do not promote LLM routing to live until ALL are true:

- [ ] Claude Chat connector is stable (confirmed working, no timeout pattern)
- [ ] Shadow mode has run for ≥ 20 routing events
- [ ] LLM agrees with operator-confirmed outcomes ≥ 80%
- [ ] Fallback path tested: connector down → deterministic fires correctly
- [ ] Malformed JSON path tested: LLM returns garbage → deterministic fires correctly
- [ ] `w2_llm_prompt.txt` reviewed by operator and locked
- [ ] Routing history divergence log reviewed with operator

---

## Cost & Rate Control

- **Trim task body to 500 chars** before sending to Claude — prevents runaway token cost.
- **Model target:** Claude Haiku (fastest, cheapest) for routing decisions. Claude Sonnet only if Haiku confidence is consistently low (< 0.70 average over 10 calls).
- **Timeout:** 10-second hard timeout on the HTTP node. Any timeout triggers deterministic fallback immediately.
- **Rate limit:** W2 polls every 3 minutes. LLM call is one per task per poll cycle. Burst is bounded by Linear issue queue depth.

---

## Tools & Connectors Needed

| Tool                                     | Status           | Required For                       |
| ---------------------------------------- | ---------------- | ---------------------------------- |
| Claude Chat HTTP connector               | 🔲 Pending proof | Core LLM routing call              |
| `w2_llm_prompt.txt` config file          | 🔲 Create        | Prompt template storage            |
| `routing_history.jsonl` schema extension | 🔲 Pending       | Shadow mode logging                |
| Persistent memory / vector store         | 🔲 Future        | Long-term routing improvement      |
| Cost monitoring (token counter)          | 🔲 Future        | Budget safety                      |
| Claude Review feature (operator)         | ✅ Available     | Post-routing review and correction |

---

## V1 Logging Schema — Locked (PRO-200, 2026-04-29)

All rows appended to `data/routing_history.jsonl` by the LLM routing block (T1+)
**must** include the following fields in addition to the existing phase-1 fields.
This is additive — do not remove or rename existing fields.

```json
{
  "ticket_id": "<Linear ticket identifier, e.g. PRO-200>",
  "synopsis_hash": "<sha256[:16] of ticket_id — stable cross-row identifier>",
  "model_id": "<model name used, e.g. claude-haiku-4-5-20251001>",
  "model_version": "<model version string or null if unknown>",
  "timestamp": "<ISO 8601 UTC with Z suffix>",
  "confidence_raw": "<float 0.0-1.0 raw model output — before any post-processing>",
  "multi_pass_results": "<array of per-pass {worker, confidence} objects, or null if single-pass>",
  "worker_proposed": "<worker the LLM block proposed before operator review>",
  "worker_executed": "<worker that actually ran the task — null until post-dispatch>",
  "operator_disposition": "<approve | override | triage | request_revision | null>",
  "override_reason_tag": "<short slug if operator overrode, e.g. wrong_worker, scope_mismatch — else null>",
  "latency_ms": "<integer milliseconds for the LLM call — null if fallback>",
  "input_tokens": "<integer — null if not available>",
  "output_tokens": "<integer — null if not available>",
  "cost_usd": "<float — null if not calculable>",
  "prompt_package_sha": "<sha256[:16] of the prompt template used — placeholder null until T-pkg lands>",
  "w2_fallback_triggered": "<boolean — true if deterministic fallback fired instead of LLM>"
}
```

### Field rules

- `ticket_id` maps to `task_identifier` for Linear-sourced tasks; use `null` for synthetic/test rows.
- `synopsis_hash` is `sha256(ticket_id)[:16]`. Use the same algorithm as `build_corpus.py`.
- `confidence_raw` is the model's stated confidence before confidence-gate logic. Preserve it even when the gate forces a fallback.
- `operator_disposition` is populated by the W7 callback writer when the operator taps a Telegram button.
- `worker_executed` is populated by the dispatcher after the task is handed off. It may arrive in a separate row (same `trace_id`, `source: w7_callback_decided` or similar).
- `prompt_package_sha` is a placeholder (`null`) until T-pkg (prompt package management) lands.
- `w2_fallback_triggered: true` when the deterministic path fired because LLM confidence < gate threshold, timeout, or malformed JSON.

### Additive only — hard rule

Never remove, rename, or change the type of an existing field. New schema revisions are v2+, not replacements of v1. The corpus extractor and scoring script depend on stable field names.

---

## Related Files

| File                                                   | Relationship                                  |
| ------------------------------------------------------ | --------------------------------------------- |
| `docs/n8n/WORKFLOW_MAP.md`                             | W2 canonical workflow definition              |
| `docker/n8n/workflows/w2_worker_selection_router.json` | W2 live workflow JSON                         |
| `/miru-data/config/w2_routing_rules.json`              | Deterministic scoring rules (baseline)        |
| `data/routing_history.jsonl`                           | Routing decision log (extend, do not replace) |
| `data/pending_callbacks.jsonl`                         | Active approval tokens (W2 → W7)              |

---

## Hard Rules

1. **Never remove the deterministic path.** It is the permanent fallback and the truth baseline.
2. **Shadow mode first, always.** No LLM decision reaches Telegram until shadow phase is complete.
3. **Operator approves promotion.** A worker cannot self-promote the LLM block to live.
4. **W7 is unchanged.** The callback handler, HMAC validation, and Linear mutation path are not touched by this block.
5. **One LLM call per task per cycle.** No retry loops on the LLM call — timeout = fallback.
