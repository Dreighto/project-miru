# Decision Log Data Schemas — Field Definitions and JSON Structures

**Source:** Perplexity Sonar Deep Research
**Query date:** 2026-05-03
**Query:** "What is the exact data schema that mature LLM/agent teams use to capture agent decisions in a decision log or judgment trail? What fields are recorded — e.g., confidence scores, discarded alternatives (counterfactual actions), external world state at decision time, prompt inputs, tool call outputs, intermediate reasoning traces, uncertainty estimates? Provide specific published schemas, JSON structures, or field-level definitions from research papers, engineering blogs, or production systems."

---

# Data Schemas for LLM Agent Decision Logging: Specification, Field Definitions, and Production Implementations

The landscape reveals converging standards around three primary frameworks: the **Decision Trace Schema (DES)** for governance and compliance, **AgentTrace** for multi-surface observability combining cognitive and operational telemetry, and emerging **OpenTelemetry** standards that normalize LLM-specific metadata across production systems.

---

## 1. Decision Trace Schema (DES) — Governance-Focused Reference Architecture

Published via formal academic publication (arxiv.org/abs/2604.09296), DES addresses the "Fragmented Trace Problem" — regulatory frameworks prescribe what information must be logged without specifying the data model. DES bridges four infrastructure layers: ML inference, rule/policy evaluation, cross-system coupling, and governance metadata.

### Ten required root-level fields, organized into six field groups:

| Group                    | Fields                                                                            | Governance purpose                                  |
| ------------------------ | --------------------------------------------------------------------------------- | --------------------------------------------------- |
| **Identity & lifecycle** | Decision UUID, event timestamp, version                                           | Unique identification, temporal ordering            |
| **Inputs**               | Structured data at decision time, model config, external context snapshot         | What information was available                      |
| **Process**              | Reasoning paths evaluated, intermediate conclusions, **rejected alternatives**    | How the decision was reached; counterfactual record |
| **Output**               | Final selection, **confidence/probability score**, alternative probabilities      | What was decided and how certain                    |
| **Rationale**            | Explainability artifacts: policy citations, rule activations, feature importances | Why this decision                                   |
| **Governance metadata**  | Actor identity (human/system), authorization chain, classification tags           | Accountability and audit                            |

### Three evidence tiers (tiered logging strategy):

| Tier            | When used                                | What is captured                                                      |
| --------------- | ---------------------------------------- | --------------------------------------------------------------------- |
| **Lightweight** | Routine, low-risk decisions              | Minimal required fields only                                          |
| **Sampled**     | Statistical sample across all decisions  | Full reasoning + tool outputs                                         |
| **Full**        | High-stakes decisions, compliance audits | Every detail including rejected alternatives and world state snapshot |

DES has been validated against 25+ existing decision logging formats and is the only specification that simultaneously covers all four infrastructure layers.

---

## 2. AgentTrace Unified Logging Envelope

Source: arxiv.org/html/2602.10133v1

All three surfaces (cognitive, operational, contextual) emit logs conforming to a shared envelope:

```json
{
  "id": "<uuid>",
  "surface": "cognitive | operational | contextual",
  "trace_id": "<parent-trace-uuid>",
  "span_id": "<this-span-uuid>",
  "ts": "2026-05-03T14:22:01.847Z",
  "agent_name": "miru-worker-1",
  "level": "INFO | WARN | ERROR",
  "event": {
    /* surface-specific body below */
  }
}
```

### Cognitive surface payload (LLM reasoning):

```json
{
  "model": "claude-sonnet-4-6",
  "prompt_tokens": 1842,
  "completion_tokens": 347,
  "total_tokens": 2189,
  "prompt_summary": "Task: implement retry logic in stall_detector.py...",
  "thinking_segments": [
    "I need to check if the file exists first...",
    "The existing retry logic uses exponential backoff..."
  ],
  "plan": "1. Read existing code. 2. Add retry wrapper. 3. Update tests.",
  "reflections": ["The original code doesn't handle transient failures."],
  "confidence_raw": 0.87,
  "alternatives_considered": [
    { "option": "Use tenacity library", "rejected_because": "adds external dependency" },
    { "option": "Custom loop", "selected": true }
  ]
}
```

### Operational surface payload (method calls):

```json
{
  "method": "run_pre_commit",
  "status": "success | error",
  "duration_ms": 2341,
  "arg_summary": "staged_files=['stall_detector.py', 'tests/test_stall.py']",
  "result_summary": "All hooks passed",
  "error": null
}
```

### Contextual surface payload (external interactions):

```json
{
  "operation": "http_get | db_query | cache_read | vector_search | file_read",
  "source": "github_api | postgres | redis | pinecone | filesystem",
  "query_summary": "GET /repos/Dreighto/project-miru/contents/...",
  "response_summary": "200 OK, 1 file returned",
  "row_count": 1,
  "latency_ms": 187,
  "status": "success | error | timeout"
}
```

---

## 3. OpenTelemetry LLM Semantic Conventions

Source: opentelemetry.io/blog/2024/llm-observability, arize-ai.github.io/openinference/spec/semantic_conventions.html

Standard attribute names for LLM spans:

```
openinference.span.kind     = "LLM" | "EMBEDDING" | "RETRIEVER" | "TOOL" | "AGENT"
llm.system                  = "openai" | "anthropic" | "cohere"
llm.model_name              = "gpt-4o" | "claude-sonnet-4-6" | ...
llm.request.temperature     = 0.7
llm.request.top_p           = 0.9
llm.request.max_tokens      = 2048
llm.usage.prompt_tokens     = 1842
llm.usage.completion_tokens = 347
llm.usage.total_tokens      = 2189
llm.prompt                  = "<full prompt text>"   # stored as event, not attribute
llm.response                = "<full response text>" # stored as event, not attribute
```

Span kinds define the trace hierarchy:

- **AGENT span** — outermost, contains the full agent execution
- **LLM spans** — individual model calls (nested under AGENT)
- **TOOL spans** — tool/API invocations (nested under LLM or AGENT)
- **RETRIEVER spans** — vector DB / RAG retrieval steps
- **EMBEDDING spans** — vector generation steps

---

## 4. Confidence Score Mechanisms

### 4a. Raw probability (softmax)

Most common baseline. Logged as a float 0-1 representing the model's token-level probability for the selected output. Limitations: softmax normalization loses evidence-strength signal.

### 4b. Alternative candidate scores (margin)

DES mandates logging not just the winner but **the full distribution of top-k alternatives**. A decision between 0.92 and 0.08 is qualitatively different from 0.51 and 0.49. The margin (winner probability − runner-up probability) is the key operational metric.

```json
"confidence": {
  "selected_option": "use_web_search",
  "selected_probability": 0.73,
  "alternatives": [
    { "option": "use_database_search", "probability": 0.21 },
    { "option": "ask_clarification", "probability": 0.06 }
  ],
  "margin": 0.52,
  "entropy": 0.94
}
```

### 4c. Logits-induced token uncertainty (LogTokU)

Source: arxiv.org/html/2502.00290v3

Real-time uncertainty without multiple sampling. Raw logits contain evidence-strength information that is destroyed during softmax normalization. Analyzing logits enables detection of cases where the model has weak evidence for even the top-probability selection. Decomposes uncertainty into:

- **Aleatoric** — inherent randomness in the decision space (irreducible)
- **Epistemic** — gaps in the model's knowledge (reducible via more training/RAG)

```json
"uncertainty": {
  "method": "LogTokU",
  "aleatoric": 0.12,
  "epistemic": 0.31,
  "total": 0.43,
  "confidence_calibrated": 0.69
}
```

### 4d. DiverseAgentEntropy (multi-agent consistency)

Source: amazon.science paper

Rather than self-consistency on a single query, estimate confidence by querying multiple diverse variations of the same question and measuring agreement across variations. More reliable than single-query uncertainty when models are confidently wrong.

---

## 5. Counterfactual / Discarded Alternatives

DES mandates recording the **process** field group capturing: which reasoning paths were evaluated, what intermediate conclusions were reached, and crucially, **what reasoning was rejected and why**.

Practical implementation approaches:

**Approach A — Inline capture (at inference time):**

```json
"alternatives_considered": [
  {
    "option": "Break PR into two separate PRs",
    "evaluation": "Would create merge complexity for dependent files",
    "rejected_because": "file overlap means sequential dependency",
    "probability_if_selected": 0.21
  },
  {
    "option": "Single bundled PR",
    "selected": true,
    "evaluation": "Simpler review, atomic merge",
    "probability": 0.73
  }
]
```

**Approach B — Counterfactual generation (offline):**
Source: arxiv.org/html/2601.20090v1

Uses structural causal models to reconstruct what would have happened under different decisions. Applied post-hoc to sampled decisions. Computationally expensive; typically applied to high-risk or anomalous decisions rather than all decisions.

**Approach C — Self-play sampling:**
For stochastic decisions, run the same decision prompt multiple times (pass@k) and record the distribution of outcomes across runs as a proxy for the counterfactual space.

---

## 6. External World State at Decision Time

The contextual surface in AgentTrace captures the state snapshot of external systems at the moment each decision was made. Key fields:

```json
"world_state_snapshot": {
  "timestamp": "2026-05-03T14:22:01.847Z",
  "tool_outputs": [
    {
      "tool": "git_status",
      "output": "On branch main, clean working tree",
      "latency_ms": 43
    },
    {
      "tool": "linear_get_issue",
      "output": { "id": "PRO-290", "state": "In Progress", "priority": "high" },
      "latency_ms": 187
    }
  ],
  "retrieved_documents": [
    {
      "source": "CLAUDE.md",
      "relevance_score": 0.94,
      "excerpt": "CC may self-merge PRs that fall in the low-risk column..."
    }
  ],
  "env_vars_present": ["MIRU_ROUTING_KEY", "ANTHROPIC_API_KEY"],
  "branch": "main",
  "clean": true
}
```

---

## 7. Adobe DecisioningEvents Schema (XDM)

Source: experienceleague.adobe.com/en/docs/journey-optimizer

Production-scale implementation. Four required fields per decision event:

| Field         | Description                                              |
| ------------- | -------------------------------------------------------- |
| `fallback`    | Name + ID of fallback offer if no personalized selection |
| `placement`   | Name, ID, channel of the delivery placement              |
| `selections`  | Name + ID of the selected offer                          |
| `activity`    | Name + ID of the decision activity executed              |
| `identityMap` | Profile for whom decision was made                       |
| `timestamp`   | Exact moment of delivery (ISO 8601)                      |

---

## 8. Complete Decision Event — Synthesized Example

Based on DES + AgentTrace + OpenTelemetry conventions:

```json
{
  "decision_id": "d-8f2a3b91-cc2e-4f1a-9d7c-0a1b2c3d4e5f",
  "trace_id": "t-pro290-session-001",
  "span_id": "s-plan-001",
  "ts": "2026-05-03T14:22:01.847Z",
  "agent_id": "claude-code-1",
  "ticket_id": "PRO-290",
  "decision_type": "judgment_driven",
  "phase": "planning",

  "inputs": {
    "user_intent": "Add retry logic to stall_detector.py",
    "context_files_read": ["tools/orchestrator/stall_detector.py", "CLAUDE.md"],
    "world_state": {
      "branch": "main",
      "git_clean": true,
      "linear_ticket_state": "In Progress"
    }
  },

  "process": {
    "reasoning_trace": "The current stall_detector reads cc_heartbeat_log.jsonl and compares timestamps. If the file is missing or empty, it raises FileNotFoundError. In production, log files can be temporarily unavailable during backup rotation. I need to add retry logic. Options considered: (1) tenacity library — clean API but adds external dependency not approved in CLAUDE.md; (2) custom retry loop — more code but zero new deps; (3) ignore the error — unacceptable, would silently miss stalls.",
    "alternatives_considered": [
      {
        "option": "Use tenacity library",
        "rejected_because": "external dependency, not in approved list"
      },
      { "option": "Silent failure", "rejected_because": "would miss stall events silently" },
      {
        "option": "Custom retry loop",
        "selected": true,
        "rationale": "zero deps, handles transient FS errors"
      }
    ]
  },

  "output": {
    "decision": "Implement custom retry loop with 3 attempts and 2s backoff",
    "confidence": {
      "selected_probability": 0.84,
      "alternatives": [
        { "option": "Use tenacity", "probability": 0.12 },
        { "option": "Silent failure", "probability": 0.04 }
      ],
      "margin": 0.72
    }
  },

  "rationale": {
    "rules_applied": ["CLAUDE.md: No new external dependencies without operator approval"],
    "policy_citations": ["CLAUDE.md#file-placement", "CLAUDE.md#completion-contract"],
    "explanation": "Custom retry preferred over tenacity to avoid introducing unapproved dependency."
  },

  "governance": {
    "worker_id": "claude-code-1",
    "authorized_by": "operator (PRO-290 ticket dispatch)",
    "review_required": false,
    "tags": ["retry-logic", "resilience", "stall-detection"]
  }
}
```

---

## 9. Audit Trail Fields (Compliance-focused)

Source: mintmcp.com/blog/build-audit-trails-ai-coding-agents

The six essential elements for compliance-grade audit trails:

| Element            | Field in log                                    |
| ------------------ | ----------------------------------------------- |
| **Input**          | Prompt or instruction that triggered the action |
| **Output**         | What the agent generated or modified            |
| **Data accessed**  | Files, databases, APIs touched                  |
| **Model identity** | Which AI model and version performed the action |
| **User identity**  | Who authorized or initiated the action          |
| **Timestamp**      | Precise timing for correlation and sequencing   |

---

## 10. Reasoning Trace and Chain-of-Thought Capture

Modern APIs expose reasoning traces via:

- `<thinking>` XML blocks (Anthropic extended thinking)
- `reasoning_content` field (OpenAI o-series)
- Structured JSON fields with `plan`, `reflection`, `steps` sub-fields

Schema for capturing these:

```json
"reasoning": {
  "format": "thinking_blocks | cot | structured_json",
  "raw": "<thinking>...</thinking>",
  "extracted_steps": [
    { "step": 1, "content": "Read the existing implementation" },
    { "step": 2, "content": "Identify failure modes" },
    { "step": 3, "content": "Select implementation approach" }
  ],
  "plan": "Implement retry with backoff in stall_detector.py",
  "self_critique": "The 3-attempt limit may be too low for slow disks under backup load."
}
```

---

## Citations

- DES paper: https://arxiv.org/abs/2604.09296
- AgentTrace: https://arxiv.org/html/2602.10133v1
- OpenTelemetry LLM observability: https://opentelemetry.io/blog/2024/llm-observability/
- OpenInference conventions: https://arize-ai.github.io/openinference/spec/semantic_conventions.html
- Datadog span kinds: https://docs.datadoghq.com/llm_observability/terms/
- Traceloop spans guide: https://www.traceloop.com/blog/understanding-traces-and-spans-in-llm-applications
- LogTokU uncertainty: https://arxiv.org/html/2502.00290v3
- DiverseAgentEntropy: https://www.amazon.science/publications/rethinking-llm-uncertainty-a-multi-agent-approach
- Counterfactual causal models: https://arxiv.org/html/2601.20090v1
- Adobe XDM: https://experienceleague.adobe.com/en/docs/journey-optimizer/using/decisioning/offer-decisioning/create-reports/get-started-events
- Audit trail essentials: https://www.mintmcp.com/blog/build-audit-trails-ai-coding-agents
