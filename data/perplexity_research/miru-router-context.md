# Miru Orchestrator / Router — Local Model Context Block

> **Purpose:** Reference document for planning and implementation of the Ollama-based local LLM router for the Miru autonomous workflow system. This is findings and recommendations only — not a directive.

---

## What This Model Does

The orchestrator is a **stateless routing function**, not a coder or planner. Each call receives:

1. A stable governance preamble (rules, boundaries, constraints)
2. A routing schema definition (closed-enum JSON structure)
3. A bounded list of ticket summaries from Linear (with labels and keywords)

It emits a single compact JSON routing decision per ticket. It does not hold conversation history between calls. It does not call external services. It does not modify its own behavior.

---

## Hardware Context

| Component         | Spec                                                          |
| ----------------- | ------------------------------------------------------------- |
| Box               | GMKtec NucBox K12                                             |
| CPU               | Ryzen 7 8745H (8745H-class)                                   |
| iGPU              | Radeon 780M (RDNA3)                                           |
| RAM               | 32 GB DDR5                                                    |
| Inference backend | Ollama (Vulkan staged — requires UMA → 8 GB in BIOS + reboot) |
| Access            | Docker + Tailnet, MCP bridge via ASGI gateway (port 18766)    |

---

## RAM Budget (32 GB Total)

| Allocation                                                                         | Range        |
| ---------------------------------------------------------------------------------- | ------------ |
| OS + system + Docker engine + Tailnet                                              | 4–6 GB       |
| MCP servers, dispatcher Flask app, logging/monitoring                              | 4–6 GB       |
| 6–7 CLI worker agents (CC, Gemini, extras) — process buffers only, not LLM weights | 6–8 GB       |
| **Orchestrator model (weights + context/cache)**                                   | **10–14 GB** |

> Workers (Claude Code, Gemini CLI) are cloud-backed — their 0.8–1.2 GB per-process estimate covers only local process + buffers, not weights.

> This is why models above ~8B parameters are not recommended here. 13B+ would consume the worker headroom under peak concurrency.

---

## Shortlisted Ollama Models

### 1. `llama3.1:8b-instruct` (or 3.2 / 3.3 8B Instruct) — Primary Candidate

**Fit:** Very good general reasoning and planning; strong performance on structured outputs and function schema understanding; good community documentation around tool calling and JSON-only output modes.

**Resource plan on this box:**

- Model weights: ~8–10 GB
- Context/cache overhead: ~4–6 GB
- **Total target budget: 12–14 GB**

**Trade-offs:**

- Slightly higher latency than smaller models — still acceptable for a routing brain at this call volume (5–15/day)
- Needs explicit instructions for terse, JSON-only output; system message should enforce schema and prohibit explanatory prose

**Notes on variants:**

- `llama3.2:8b-instruct` and `llama3.3:8b-instruct` (when available in Ollama) are additive improvements on reasoning and tool call adherence over 3.1; prefer the latest stable 8B instruct variant available
- For near-100% closed-enum JSON adherence without constrained decoding, no 7–8B model achieves this reliably on its own — pair with a grammar enforcer (see section below)

---

### 2. `mistral:7b-instruct` — Secondary / Lower-Latency Option

**Fit:** More memory-efficient and faster on short prompts; good at concise, structured responses; well-suited when extra RAM headroom is needed for peak worker concurrency.

**Resource plan on this box:**

- Model weights: ~7–9 GB
- Context/cache overhead: ~3–5 GB
- **Total target budget: 10–12 GB**

**Trade-offs:**

- Slightly weaker long-chain reasoning compared to Llama 3.x
- Adequate for ticket routing when prompts are tightly structured and decisions are narrow
- JSON adherence via raw generation is also imperfect at 7B; grammar enforcement applies here too

---

### 3. `phi3-medium` (nearest Phi-3 medium instruct variant in Ollama) — Lightweight / Triage Option

**Fit:** Very efficient; good for small decision tasks; suitable for triage-style routing when tickets are already well-labeled and routing is mostly mechanical.

**Resource plan on this box:**

- Model weights: ~5–7 GB
- Context/cache overhead: ~3–4 GB
- **Total target budget: 8–10 GB**

**Trade-offs:**

- Not ideal for complex multi-ticket global planning
- Best used as a triage model or secondary fallback, not as the sole routing brain
- Good candidate if the system later introduces a two-stage pattern: phi3 handles obvious/routine tickets, llama3.x handles judgment/ambiguous ones

---

### 4. `qwen2.5:7b-instruct` — Experimental / Conditional

**Fit:** Competitive on tool calling and structured outputs in evaluations; good general reasoning.

**Resource plan on this box:**

- Similar to `mistral:7b-instruct`
- **Total target budget: 10–12 GB**

**Trade-offs:**

- Behavior has been noted as more "creative" — may require stricter sampling params (`temperature: 0`, `top_p: 1`) to keep routing deterministic
- Needs stricter governance prompting to prevent over-calling tools or drifting from schema
- Use only if Llama 3.x / Mistral are ruled out; validate against routing history before deploying

---

## Constrained Decoding — Recommended Pairing

For a strict JSON router, pairing the model with constrained decoding (grammar enforcement) is the **practitioner standard**, not an over-optimization.

**Why:**

- Drives syntactic JSON validity close to 100%
- The grammar does not meaningfully interfere with the model's ability to choose correct enum values — it constrains structure, not reasoning
- Simplifies downstream parsing; removes the need for try/except JSON repair logic in the dispatcher

**Options:**

- `lm-format-enforcer` — integrates with Hugging Face and vLLM; well-documented for JSON schema enforcement
- `Outlines` — library for structured text generation with grammar + regex + JSON schema modes
- `llama.cpp` GBNF grammars — native to llama.cpp / Ollama's underlying runtime; can be passed directly via Ollama's API as a grammar parameter

**Division of labor:**

> Model handles **semantics** (which worker, which mode, which tool profile).
> Grammar enforces **structure** (valid JSON, correct field names, only allowed enum values).

---

## Routing Call Design

Each call should be:

- **Stateless** — no conversation history carried between calls
- **Context window:** 8k–16k tokens is sufficient; prompt fits comfortably at 1–2 KB
- **Sampling:** `temperature: 0` (or near-zero) for deterministic routing; `top_p: 1`
- **Output:** JSON only, no preamble, no explanation prose outside the `rationale` and `validation.rationale` fields

Target schema (sketch):

```json
{
  "trace_id": "rtr-PRO-XXX-<rand>",
  "ticket_id": "PRO-XXX",
  "decision": {
    "worker": "claude-code | gemini | both | none",
    "mode": "routine | judgment | ambiguous | blocked",
    "tool_profile": "drift_executor | standard_worker | reviewer | null",
    "confidence": "high | medium | low"
  },
  "validation": {
    "is_legitimate_build": true,
    "rationale": "<1-2 sentences>"
  },
  "execution": {
    "model": "...",
    "thinking_level": "...",
    "timeout_seconds": 1200,
    "plan_only": false
  },
  "flags": ["keyword:audit", "task_type:Feature"],
  "rationale": "<1-2 sentences appended to routing_history.jsonl>"
}
```

---

## Latency Expectations (Ryzen 7 8745H + 780M, Vulkan, Q4_K_M)

- **Warm calls** (model already loaded): ~3–10 seconds for 400–800 total tokens
- **Cold start** (first call after model load): ~5–15 seconds
- Comfortably inside the sub-30s latency budget; typically sub-10s once warm
- UMA set to 8 GB in BIOS allows iGPU to hold more of the model weights in VRAM and improves throughput

---

## Evaluation Against routing_history.jsonl

When scoring candidate models against historical operator-approved decisions, use at least three metrics:

1. **JSON validity rate** — after grammar enforcement, should approach 100%; measures only syntactic correctness
2. **Exact match accuracy** — per-field match on `worker`, `mode`, `tool_profile`, `confidence`
3. **Cost-weighted alignment score** — encodes operational penalty for each mismatch type:
   - Low cost: `judgment` ↔ `ambiguous` swap
   - Medium cost: `routine` ↔ `judgment` swap
   - High cost: anything → `blocked` wrong; `blocked` → anything wrong

A model that errs on the side of `ambiguous` or `standard_worker` over a more aggressive choice is generally preferred in a governed system — it's safer to be conservative and let the operator override.

---

## Architecture Note — Single Model vs Two-Stage Classifier

For this system's current scale (5–15 routing calls/day), a **single 7–8B orchestrator** with:

- A strong governance preamble
- Constrained decoding
- The existing Judgment Trail validator

...outperforms a fine-tuned classifier + judgment model split on ops simplicity, maintainability, and sufficiency for the workload. A two-stage split is beneficial at higher volumes (hundreds+ calls/day) or multi-tenant deployments — not warranted here.

The existing W2 ingress classifier already handles coarse triage upstream. The local router's scope is narrower: validate legitimate build vs CH self-serve attempt, then assign `worker` + finalize `mode/tool_profile` under governance constraints.

---

_Last updated: 2026-05-05 — Miru Autonomy Overhaul Phases 1–4 shipped (A2A Bus, Judgment Trail, Subagent Isolation, Ingress Classifier). Dispatcher port 19000 targeted for resurrection as local router host._
