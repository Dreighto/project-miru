# Agent Decision Logs & Judgment Trails — Research Synthesis

**Query date:** 2026-05-03
**Tool:** Perplexity Sonar Deep Research (4 parallel queries)
**Topic:** How production LLM engineering teams build decision logs and judgment trails to refine autonomous agent quality over time

---

## What this research covers

Four parallel deep-research queries were run to comprehensively answer the full question. Each is a standalone document in this folder:

| File                                                                                   | Sub-question                                                                                                        |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| [01-architecture-and-system-design.md](./01-architecture-and-system-design.md)         | How are decision log systems architected end-to-end? What do mature teams capture beyond success/fail?              |
| [02-data-schemas-and-field-definitions.md](./02-data-schemas-and-field-definitions.md) | Exact data schema: field-level definitions, JSON structures, confidence scores, discarded alternatives, world state |
| [03-deterministic-vs-judgment-driven.md](./03-deterministic-vs-judgment-driven.md)     | Canon-mandated vs judgment-driven ratio; segregation; specialized grading for the judgment-driven subset            |
| [04-rlhf-and-self-improvement.md](./04-rlhf-and-self-improvement.md)                   | How decision log corpora feed offline RLHF / self-grading loops; metrics to detect reward hacking                   |

---

## Key findings at a glance

### 1. Decision log architecture

- Modern systems use a **three-surface model**: cognitive (LLM reasoning traces), operational (method calls + timing), contextual (external tool/API/DB interactions).
- Industry frameworks: **AgentTrace** (academic), **LangSmith**, **Datadog LLM Observability**, **Galileo Agentic Evaluations**, **Confident AI**.
- Traces are emitted via OpenTelemetry + JSONL dual-path. Structured schemas validated at emit time.
- Enrichment pipeline: automated evaluators (LLM-as-judge) score sampled traces; human annotators label via annotation queues.
- Improvement loop: collect → enrich → identify patterns → make targeted fix → validate → deploy → repeat.

### 2. Data schema

- **Decision Trace Schema (DES)** — the most governance-complete published spec. Ten required root fields across six groups: identity, inputs, process (including discarded alternatives), output+confidence, rationale, governance metadata. Supports three evidence tiers (lightweight/sampled/full) matched to decision risk.
- **AgentTrace unified envelope**: `{uuid, surface, trace_id, span_id, ts, event_body}`. Cognitive payload adds reasoning chains, thinking-block extracts, token counts. Contextual payload adds data source, query structure, row counts.
- **OpenTelemetry LLM conventions**: `llm.vendor`, `llm.model_name`, `llm.request.temperature`, `llm.usage.prompt_tokens`, `llm.usage.completion_tokens`, full prompt + response as events.
- Confidence captured as: raw softmax probability of selected decision + **top-k alternative scores** (margin matters). Logits-based uncertainty (LogTokU) enables real-time uncertainty without multi-sampling.
- Counterfactual / discarded-alternatives field: DES mandates recording which alternatives were evaluated and why rejected. Contrastive methods sample multiple reasoning paths offline.

### 3. Canon-mandated vs judgment-driven

- No widely published quantitative split exists. Production evidence suggests teams **architect** the split deliberately rather than measure it retrospectively: deterministic rules dominate Action-tier orchestration; probabilistic inference is bounded to AI/ML-tier decision gates.
- AWS production example: "near-deterministic behavior" achieved by phase-driven workflows that confine model discretion to specific steps.
- Teams distinguish the categories via: trajectory classification (canonical vs adaptive path), reference trace comparison, decision uncertainty thresholds, and explicit instrumentation metadata at inference time.
- **74% of deployed agents use human-in-the-loop as primary evaluation** — judgment-driven subset is implicitly graded through human approval/override signals rather than a formal automated pipeline in most orgs.
- Grading methodologies for judgment-driven subset: multi-dimensional LLM-as-judge with structured rubrics (analytic, not holistic); pairwise comparison; trajectory-level assessment (not just final output); pass@k for stochastic decisions; structured exception analysis.

### 4. RLHF / self-improvement loops

- **Standard pipeline**: trajectory collection → preference labeling → reward model training → PPO policy update → evaluation → deploy.
- Key production example: **Agent-in-the-Loop (AITL)** at US customer support — 4 annotation types embedded into live ops. Result: +11.7% retrieval recall, +8.4% generation helpfulness, reducing retraining from months to weeks.
- **Reward hacking detection**: monitor gap between proxy reward and ground-truth task metrics; contrastive trajectory analysis (63% detection rate in context vs 45% isolated); trajectory monitors for behavioral anomalies; behavioral diversity metrics.
- **Constitutional AI (RLAIF)**: model self-critiques against an explicit principle set; eliminates human annotation bottleneck but risks alignment faking and bias reinforcement. Best used as a pre-filter with human QC.
- **Search Self-Play (SSP)**: model generates its own training tasks, verifiable reward (correct citation or not). No human annotation. Significant benchmark gains.
- **KL regularization** is mandatory to prevent runaway optimization. Reward must be bounded and show rapid initial growth then convergence — sustained acceleration signals reward hacking.
- Separate reflection from execution architecturally. Self-improvement loops must never touch live production pipelines.

---

## Implications for Project Miru

The research directly maps to Miru's agent architecture decisions:

1. **CLAUDE.md judgment trails already exist in embryonic form** — `cc_heartbeat_log.jsonl` + `cc_completion_log.jsonl` + `vp_ops_supervision.jsonl` are the operational surface. The missing layer is the **cognitive surface** (reasoning trace capture) and structured grading against rubrics.

2. **Canon-mandated vs judgment-driven split**: Miru's existing `CLAUDE.md` rules map exactly to the "canon-mandated" category. Any decision the worker makes that isn't explicitly covered by CLAUDE.md or a Linear ticket spec is judgment-driven. The judgment-driven subset is currently graded only via VP Ops verification — no rubric exists.

3. **RLHF is premature at Miru's scale**, but the data collection infrastructure (append-only JSONL logs, structured completion markers, VP Ops verification) is the correct foundation. When enough judgment-driven decision events accumulate, preference pairs can be derived from vp_ops verifications that flag problems.

4. **Immediate actionable gap**: Miru has no schema for capturing _discarded alternatives_ or _decision rationale_ in the completion log. The `notes` field is the closest proxy but unstructured. Adding an optional `decision_rationale` or `alternatives_considered` field to the completion log schema would begin closing this gap.
