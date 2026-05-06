# Canon-Mandated vs Judgment-Driven Decisions — Segregation and Grading

**Source:** Perplexity Sonar Deep Research
**Query date:** 2026-05-03
**Query:** "In production LLM agent systems, what fraction of logged decisions are typically canon-mandated (rule-following, deterministic) versus judgment-driven (model discretion, novel inference)? Do mature teams isolate the judgment-driven subset for separate grading or quality evaluation? How do they distinguish the two categories in practice, and what grading methodologies are applied specifically to judgment-driven decisions to improve agent behavior?"

---

# Deterministic Versus Judgment-Driven Decisions in Production LLM Agent Systems

## Summary Finding

No widely published quantitative split exists. Production evidence suggests teams **architect** the split deliberately (designing where determinism applies and where model discretion is allowed) rather than measuring it retrospectively from logs. The practical pattern: deterministic rules dominate Action-tier orchestration; probabilistic inference is bounded to AI/ML-tier decision gates within carefully constrained scope.

---

## 1. The Distribution Gap — Why No Hard Numbers Exist

Most production agent deployments focus on end-to-end task completion metrics rather than decision-level categorization, making it difficult to quantify the fraction of execution pathways following deterministic logic versus model discretion. Research examining production agent evaluation practices found:

- **74%** of deployed agents rely primarily on human-in-the-loop evaluation
- **70%** rely on prompting off-the-shelf models without weight tuning
- **68%** execute at most 10 steps before triggering human intervention
- **75%** of teams forgo formal benchmarking, relying instead on A/B testing, user feedback, and production monitoring

These findings suggest teams have _implicitly_ made choices about where to locate deterministic versus probabilistic logic, but they typically have not instrumented their systems to measure the decision-level distribution with precision.

---

## 2. Architectural Segregation — How Production Teams Separate the Categories

### 2a. Phase-driven workflows (AWS production example)

AWS demonstrated that "near-deterministic behavior" could be achieved in an IT support agent by phase-driven workflows: **specific phases follow strict business logic; specific phases leverage model adaptability**. This makes the determinism/judgment split an architectural choice, not a measurement outcome.

The canonical four-tier enterprise agent structure:

- **Engagement tier** — user interaction; deterministic routing logic
- **Action tier** — execution; rule-governed orchestration, compliance checks, audit generation
- **AI/ML tier** — reasoning engine; judgment-driven inference bounded by AJDs (Agentic Job Descriptions)
- **Data tier** — evidence grounding; deterministic retrieval with structured outputs

Deterministic logic clusters in Action tier; judgment-driven decisions concentrate in AI/ML tier.

### 2b. Hybrid deterministic + probabilistic architecture

> "Deterministic governance frameworks in which probabilistic models detect patterns humans and rules wouldn't reliably see, then deterministic workflows execute the response, routing, approvals, notifications, and remediation in a consistent and auditable way."
> — Elementum.ai production deployment description

Key architectural principle: deterministic execution **unlocks** a restricted set of actions; judgment-driven reasoning operates within a broader but **auditable** scope. As organizations mature, they progressively expand what falls into the deterministic category—not by writing more rules, but by becoming more sophisticated at confining probabilistic inference to specific, bounded decision gates.

### 2c. Agentic Job Descriptions (AJDs) as jurisdictional boundaries

Research framework (arxiv.org/html/2602.19065v1): Explicit AJDs + Agentic Problem Frames (APFs) define the scope in which a worker operates.

- Decisions within AJD scope that require contextual reasoning → **judgment-driven**
- Decisions that fall outside AJD scope → must defer to a rule engine or escalate to human review → **deterministic pathway**

---

## 3. How Teams Distinguish the Two Categories in Practice

### Method 1 — Trajectory-level classification

Categorize entire execution paths, not individual decisions. Canonical path = high trace-similarity across runs, low variance in tool call sequence, predetermined branching. Judgment-driven path = adaptive tool calls tailored to specific inputs, synthesis across multiple data sources, novel edge-case resolution.

Example:

- **Canonical**: retrieve context → apply rule-based filter → generate notification → log action
- **Judgment-driven**: retrieve context → detect anomaly → synthesize multiple data sources → generate explanation → propose action

### Method 2 — Reference trace comparison

Maintain example canonical execution paths for each decision type. Compare actual logged traces against these references. Significant divergence = judgment-driven. Note: not all divergence is error; legitimate judgment-driven decisions may take alternative paths to reach correct outcomes.

### Method 3 — Decision uncertainty quantification

Low-uncertainty decisions (high confidence, predetermined logic) → likely deterministic.
High-uncertainty decisions (lower confidence, careful reasoning needed) → likely judgment-driven.
Reliability dimensions for safety-critical agents: consistency (reproducible across runs), robustness (stable under perturbations), predictability (calibrated confidence), safety (bounded failure severity). Deterministic decisions exhibit high consistency and predictability; judgment-driven decisions may show lower consistency but must maintain bounded failure severity.

### Method 4 — Rubric-based operationalization

Explicit definition of each category:

- **Deterministic**: "Follows specific branching logic with ≤3 possible outcomes, each with clear triggering conditions"
- **Judgment-driven**: "Requires synthesis of multiple information sources, involves ambiguity resolution, or generates novel synthesis not seen in training data"

Label historical traces using this rubric to create ground truth datasets for analysis and evaluator calibration.

### Method 5 — Explicit instrumentation at inference time

Add metadata to each decision at the point it is made:

```json
"decision_metadata": {
  "decision_type": "judgment_driven | canon_mandated",
  "trigger": "novel_edge_case | rule_invocation",
  "rule_id": null,
  "requires_review": false
}
```

---

## 4. Grading Methodologies — Judgment-Driven Subset

### 4a. Multi-layered evaluation pipeline (standard production approach)

For canon-mandated decisions: binary rule validation (does the rule fire correctly?).
For judgment-driven decisions: multi-layer pipeline required:

| Layer           | What it grades                                                   | Method                                   |
| --------------- | ---------------------------------------------------------------- | ---------------------------------------- |
| Component layer | Tool call correctness, format validation, policy compliance      | Code-based graders (fast, deterministic) |
| Reasoning layer | Reasoning quality, contextual appropriateness, logical coherence | LLM-as-judge with structured rubrics     |
| System layer    | End-to-end task completion, user satisfaction                    | Human review (sampled)                   |

### 4b. LLM-as-judge with structured rubrics (dominant automated approach)

Key: analytic scoring (separate score per criterion) rather than holistic (single score). This localizes _what_ failed in the reasoning, not just _whether_ it failed.

Example rubric for a judgment-driven worker decision:

1. Did the agent correctly understand the task scope? (0-3)
2. Did it retrieve / consult the appropriate context (CLAUDE.md, ticket, code)? (0-3)
3. Was the reasoning internally consistent? (0-3)
4. Did alternatives get explicitly considered before selecting an approach? (0-3)
5. Was the selected approach appropriate given constraints? (0-3)

Rubric calibration: run against human-scored ground truth set. Where judge disagrees with humans, refine instructions or escalate to human review. Track inter-rater alignment via Cohen's kappa over time.

### 4c. Pairwise comparison

Compare two different approaches the agent might have taken. Achieves higher inter-rater reliability than absolute scoring for subjective quality dimensions. Apply to judgment-driven decisions specifically — for canon-mandated decisions, the correct answer is predetermined and pairwise comparison adds no value.

### 4d. Trajectory-based evaluation (multi-step judgment chains)

For judgment-driven decisions unfolding across multiple steps, evaluate the full reasoning chain:

- Did key decision checkpoints occur in the right order?
- Did the agent verify assumptions before acting?
- Did it recognize and handle contradictions?
- Did intermediate conclusions follow logically from evidence?

Structured checklist approach rather than single-turn evaluation.

### 4e. pass@k metrics for stochastic judgment-driven decisions

For decisions that involve genuine stochasticity (same input, legitimately different valid outputs), measure the probability that at least one of k attempts succeeds rather than requiring success on the first attempt.

A pass@3 rate of 90% at a per-trial success rate of 75% tells operators the capability exists even if individual attempts show variance — this is the correct framing for judgment-driven decisions that have a range of acceptable answers.

### 4f. Decision-level assessment (vs output-level assessment)

Rather than waiting for a final outcome, examine the decision-making process itself:

- Was problem decomposition correct?
- Were appropriate tools selected for investigation?
- Was information from multiple sources synthesized coherently?
- Was reasoning consistent with available evidence?

LLM-as-judge prompt: "Given the information available to the agent at this decision point, was the reasoning sound? Did it correctly weigh competing considerations?"

### 4g. Structured exception analysis

When judgment-driven decisions result in failures, analyze: what aspect of the input triggered novel behavior, what assumptions the model made, whether those assumptions were justified. Transforms failures into learning signals. Over time reveals patterns in which judgment-driven decision types fail and why, enabling targeted improvements.

---

## 5. HITL as Implicit Judgment-Driven Grader

74% of deployed agents use human-in-the-loop as their primary evaluation method. This creates a natural categorization:

- Decisions humans routinely approve without modification = validated as correct (either correct deterministic or appropriate judgment-driven)
- Decisions that trigger human correction or override = failure signals in the category

Over time this feedback loop creates implicit training signal: decisions that humans almost always approve as-is should become deterministic; decisions humans frequently contextualize or modify are the judgment-driven cases requiring continued monitoring and improvement.

The 74% HITL reliance suggests current production systems cannot reliably separate deterministic and judgment-driven evaluation **without** human oversight — the boundaries between the two categories remain somewhat blurred even in mature deployments.

---

## 6. The Research Gap

> "Most papers attempt intermediate validation but do not directly evaluate agent reasoning or decision soundness. While nearly all systems report end-task performance (accuracy, task completion rate), only a small fraction evaluate the quality of intermediate decisions or the soundness of reasoning."

Even teams with mature evaluation infrastructure often lack systematic mechanisms to assess individual decisions at each pipeline stage beyond terminal task metrics. The implication: while teams may have intuitive understanding of their deterministic-to-judgment-driven ratio based on workflow architecture, they often lack precise quantitative data. Teams know certain decision categories are rule-governed and others are discretionary, but have not typically calculated the actual proportion.

---

## 7. Recommendations for Production Systems

1. **Explicit instrumentation**: add `decision_type` metadata at inference time — don't try to infer it from logs retrospectively.
2. **Operational definitions**: define deterministic vs judgment-driven specifically for your domain and risk profile before collecting data.
3. **Decision-level grading**: don't rely exclusively on end-to-end task metrics; apply rubric-based LLM-as-judge to judgment-driven decisions specifically.
4. **Layered evaluation pipeline**: code-based graders for objective elements → LLM-as-judge for reasoning quality → periodic human validation for calibration.
5. **Feedback loops**: use evaluation results to drive targeted improvements to prompts, tool descriptions, and reasoning patterns.
6. **Audit the categorization itself**: periodically verify the deterministic/judgment-driven boundary remains aligned with organizational priorities as systems evolve.

---

## Citations

- Production agent practices survey: https://arxiv.org/html/2512.04123v2
- AWS phase-driven IT support agent: https://builder.aws.com/content/3AGm5fKPUCbi02ZqWBhoQHUhcIO/building-production-ready-ai-agents-lessons-from-an-enterprise-it-support-assistant
- Deterministic vs probabilistic AI (Elementum): https://www.elementum.ai/blog/deterministic-vs-probabilistic-ai
- Canonical enterprise agent structure: https://agility-at-scale.com/ai/agents/canonical-structure-of-enterprise-ai-agents/
- AJD/APF framework: https://arxiv.org/html/2602.19065v1
- Trajectory evaluation: https://tianpan.co/blog/2026-02-07-evaluating-ai-agents-trajectories-not-just-outcomes
- Agent safety reliability framework: https://arxiv.org/html/2602.16666v1
- AI agent evaluation lessons (InfoQ): https://www.infoq.com/articles/evaluating-ai-agents-lessons-learned/
- Evaluating AI agents (AWS): https://aws.amazon.com/blogs/machine-learning/evaluating-ai-agents-real-world-lessons-from-building-agentic-systems-at-amazon/
- LLM evaluation rubrics: https://arxiv.org/html/2603.00077v1
- Intermediate pipeline assessment: https://arxiv.org/html/2602.22442v1
- AI agent security/breach data: https://www.digitalapplied.com/blog/ai-agent-security-2026-1-in-8-breaches-agentic-systems
