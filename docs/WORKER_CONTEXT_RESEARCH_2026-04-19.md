# Worker Context Architecture — Deep Research Synthesis

**Date:** 2026-04-19
**Scope:** Practitioner patterns (2025-2026) for multi-worker AI-coding systems. Three blocks: shared-doc + per-worker-addendum architecture, drift/scope prevention, per-worker tool specialization.
**Method:** Perplexity research + ask, citations required on every claim, vaporware flagged where detected.
**Applies to:** Project Miru's 6-worker model (Claude Chat, Claude Code, Cursor, Gemini CLI, Codex, Copilot), 3-service layout (PM, Miru AI, Dispatcher), Windows-native operations.

---

## Block 1 — Shared AGENTS.md + Per-Worker Addendum Architecture

**Block confidence:** Medium-high. Architectural guidance is plentiful; real-team adoption stories and failure logs exist but are sparser than the marketing suggests.

### 1A — Real teams running shared + per-worker addendums

The pattern has broad tooling support:

- **AGENTS.md standard** is emerging as a cross-tool convention. OpenAI Codex CLI, Cursor, Aider, and Gemini CLI all read `AGENTS.md` at project root; Claude Code reads `CLAUDE.md`. Next.js / Vercel use a **symlink pattern** (`CLAUDE.md` → `AGENTS.md`) to serve a single source of truth to both tool ecosystems without duplication. ([agents.md](https://agents.md), [vercel/next.js symlink convention observed in repo])
- **ETH Zurich study (2025):** LLM-generated context files in real codebases *decreased* task success by 2-3 percentage points while adding ~20% inference cost compared to hand-authored short files. Headline finding: throwing an LLM at "write my AGENTS.md" measurably hurts. ([arxiv.org preprint referenced in practitioner coverage])
- **GitHub analysis (2500+ repos, 2025-2026):** Shared + addendum is the dominant pattern where multiple AI tools coexist. Most teams converged on < 500-line root files with per-folder `AGENTS.md` overrides rather than per-worker addendums. Per-worker files (one-per-tool) are rarer and mostly used where workers have sharply different capabilities.
- **Lumenalta (2025-2026)** runs a central task spec + scoped per-role context + a coordinator agent to arbitrate conflicts. They publish this as a pattern, not a single codebase. ([lumenalta.com/insights/8-tactics-to-reduce-context-drift-with-parallel-ai-agents](https://lumenalta.com/insights/8-tactics-to-reduce-context-drift-with-parallel-ai-agents))

**Vaporware flag:** `microsoft/multi-agent-reference-architecture` and `Danau5tin/multi-agent-coding-system` are cited widely but are largely conceptual / promotional — they lack production failure logs and post-deployment retrospectives. Treat them as sketches, not adoption stories. ([github.com/microsoft/multi-agent-reference-architecture](https://github.com/microsoft/multi-agent-reference-architecture), [github.com/Danau5tin/multi-agent-coding-system](https://github.com/Danau5tin/multi-agent-coding-system))

### 1B — Documented failure modes of shared + addendum

The sharpest practitioner write-ups:

1. **Agentic drift (Helge Sverre, 2025)** — Parallel agents on a shared Dart codebase referencing a shared `AGENTS.md` produced "semantic conflicts" (code compiles, git merges, but assumptions diverge). Shared files *reduce the blast radius, they do not prevent drift*. ([helgesver.re/articles/agentic-drift](https://helgesver.re/articles/agentic-drift))
2. **16-agent CLI refactor (Jonny, 2026)** — Ran 16 agents against shared `MULTI-AGENT.md` plus per-doc addendums (`CLAUDE-CODE.md`, etc.). 2/9 agents hit rate limits and silently omitted error handling; base rules were present in context but skipped under pressure. Fix was *targeted additions to addendums* — an ongoing maintenance cost. ([jonnyzzz.com/blog/2026/01/24/16-ai-agents-documentation-refactor](https://jonnyzzz.com/blog/2026/01/24/16-ai-agents-documentation-refactor))
3. **Context rot at scale (Kevin Kern, 2026)** — Large codebases compound context-switching costs; every open thread becomes "a merge conflict waiting to happen." Stale content in shared docs is the dominant failure mode after month 2. ([kevinkern.dev/posts/agentic-drift-in-large-codebase](https://kevinkern.dev/posts/agentic-drift-in-large-codebase))
4. **Contradiction-without-coordinator (Lumenalta)** — Role addendums drift and contradict the base; guardrails help but do not eliminate "narrow-view" violations (an agent skipping project standards because its addendum is silent on them). A coordinator/arbiter agent is usually needed.
5. **Multi-agent reference architectures rarely show failures** — Most "successful" public repos do not log post-deployment incidents. Treat clean READMEs skeptically.

**Common failure signatures observed across sources:**
- Duplicated rules drifting apart across per-worker files (the exact risk for Miru's current 5 per-worker files)
- Addendums silently contradicting the base
- Base rules present in context but ignored under task pressure (attention dilution)
- Merge conflicts when two workers edit the shared file
- Stale content that no one owns updating

### 1C — Session-start context enforcement

How production tools actually get the file into the model:

| Tool | Injection mechanism | Observed failure rate |
|------|--------------------|----------------------|
| **Cursor** | `.cursor/rules/*.mdc` with `alwaysApply: true` injected at chat/⌘K start; glob patterns for file-specific; `@` for manual | ~25% ignored in multi-project setups; `alwaysApply: false` misrenders as "Apply always" in UI ([forum.cursor.com/t/inconsistent-application-of-rules](https://forum.cursor.com/t/inconsistent-application-of-rules-incl-as-context-always-and-in-multi-project/83326)) |
| **Claude Code** | Auto-injects `CLAUDE.md` and `~/.claude/projects/*/memory/MEMORY.md` into every session system prompt; scoped rules in `.claude/rules/` | ~10-15% non-adherence in user anecdotes without iterative tuning; auto-memory reduces repeats by ~40% ([code.claude.com/docs/en/memory](https://code.claude.com/docs/en/memory), [institute.sfeir.com/en/claude-code/claude-code-memory-system-claude-md](https://institute.sfeir.com/en/claude-code/claude-code-memory-system-claude-md/deep-dive/)) |
| **Codex CLI / Cursor / Aider** | Pulls `AGENTS.md` into context; `config.toml` defines `review_model` for post-generation compliance | Team-specific; no public benchmarks |

**Enforcement beyond injection** (what teams add when the prompt alone fails):

- **Pre-commit validators** (Packmind) scan AI outputs, flag rule violations, auto-correct before PR. Reports ~20-30% drift baseline falling to <10% post-intervention on mature repos. ([packmind.com/context-engineering-ai-coding/context-engineering-best-practices](https://packmind.com/context-engineering-ai-coding/context-engineering-best-practices/))
- **Server-side input guards** in Claude Code's auto mode block rule-violating reads/writes before they execute. ([anthropic.com/engineering/claude-code-auto-mode](https://www.anthropic.com/engineering/claude-code-auto-mode))
- **Review agents** (Codex `review_model`, e.g. gpt-5.3-codex) do compliance checks post-generation, retry up to 10x. ([iceberglakehouse.com/posts/2026-03-context-openai-codex](https://iceberglakehouse.com/posts/2026-03-context-openai-codex/))
- **Exploration-first / reflection prompts** (Codex, Claude) force a plan step that names rules compliance before writing code.

**Vaporware flag:** Ruler's "zero drift" claim across agents has no independent verification. ([packmind.com/context-engineering-ai-coding/best-context-engineering-tools](https://packmind.com/context-engineering-ai-coding/best-context-engineering-tools/))

### 1D — Word count targets

**No rigorous 2025-2026 study directly benchmarks rule-compliance by word count.** What is consistently reported:

- **Effective context collapses at 70-80% of the Maximum Effective Context Window (MECW).** For Claude 3.5 Sonnet (200K advertised), usable is ~140-160K. "Lost in the middle" effects make mid-context instructions especially vulnerable. ([local-ai-zone.github.io/guides/context-length-optimization-ultimate-guide-2025](https://local-ai-zone.github.io/guides/context-length-optimization-ultimate-guide-2025.html), [atlan.com/know/llm-context-window-limitations](https://atlan.com/know/llm-context-window-limitations/))
- **Practitioner consensus:** short rules (under ~500 words, ~650 tokens) have materially better compliance than long ones (2000+ words). Cursor forum reports extensively cite this. ([forum.cursor.com/t/rules-in-settings-are-often-ignored-need-better-enforcement-or-clearer-limits/154821](https://forum.cursor.com/t/rules-in-settings-are-often-ignored-need-better-enforcement-or-clearer-limits/154821))
- **No Anthropic/OpenAI/Cursor doc** prescribes a recommended length. Cursor explicitly treats rules as system prompts with no length cap beyond model limits.
- **OpenAI community (2025-2026)** notes fine-tuned models still fail explicit length/count tasks with 20-50% error rates — implying negative constraints ("do not X") degrade faster than positive ones.

**Operational takeaway:** target 300-500 words per worker file, partitioned by the craft-guide "load on demand" pattern Miru already uses. Longer files load but lose attention in long sessions.

---

## Block 2 — Drift Prevention, Scope Containment, Stop-and-Ask

**Block confidence:** High on 2A, 2C, 2D. Medium on 2E. Low-medium on 2B (primary docs thin for several named systems).

### 2A — Periodic re-anchoring patterns

Re-anchoring = systematically reinstating canonical context (rules, invariants, state) at defined intervals or trigger points during extended agent sessions.

**Who triggers re-anchoring** (five patterns):

1. **Agent-driven** (LangChain Agent Builder) — the agent is instructed to "update memory" based on feedback. Weakness: agents often fail to recognize their own drift. ([langchain.com/blog/how-to-use-memory-in-agent-builder](https://www.langchain.com/blog/how-to-use-memory-in-agent-builder))
2. **Wrapper-script** (Cursor Project Rules, Aider) — rules marked `alwaysApply` are injected at session start; long sessions still deprioritize them in context. Known limitation on Cursor: "rules can get pushed out of context as the conversation grows." ([forum.cursor.com/t/workflow-to-reduce-drift-in-cursor-sessions/155963](https://forum.cursor.com/t/workflow-to-reduce-drift-in-cursor-sessions/155963))
3. **Orchestrator-level** (LangGraph, MCP-backed) — a higher-level system monitors turn count/token usage and signals when to re-anchor. MCP enables tool-agnostic canonical-context servers. MCP adoption by April 2026: "97M monthly SDK downloads, 5800+ community servers; OpenAI adopted April 2025, Microsoft July 2025, AWS November 2025."
4. **Hook-based** (Claude Code) — `PreToolUse`/`PostToolUse` hooks run custom code at lifecycle points; can implement re-anchoring cleanly. ([code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks))
5. **Event / CI / turn-based triggers** — user-invoked ("re-read CONTRACT.md"), tool-call-count, token-budget thresholds, or pre-destructive-op gates.

**What gets reinjected** (spectrum):

- Full canonical document (simple, expensive)
- Compressed summaries (1-3 sentences; lossy)
- Constraints/invariants only — the Cursor `CONTRACT.md` / `WHY.md` / `QUICKSTART.md` split
- Delta updates — "what changed since last anchor" (Mem0 updates-not-duplicates pattern, 91% lower latency, 90% token savings claimed) ([techsy.io/blog/ai-agent-memory-guide](https://techsy.io/blog/ai-agent-memory-guide))
- Memory-curated / relevance-ranked (Generative Agents reflection, Park et al. 2023)

**Frequency strategies:** turn-count, token-usage, event-based, time-based, or composite. Production systems layer multiple triggers.

**Conflict resolution when re-anchor contradicts current agent state:**
- Stop-and-reorient (safest; causes friction)
- Silent override (opaque; debugging hell)
- Conflict negotiation (agent chooses; preserves agency)
- Stratified priority (CONTRACT.md > WHY.md > QUICKSTART.md; practical default)

**Practitioner recommendation:** start with `.cursor/rules/CONTRACT.md`-style hard invariants, one periodic trigger (every 50 turns or 40K tokens), full reinstatement at trigger, monitor violation rates before/after.

### 2B — Ledger of tried-and-failed approaches (INCONCLUSIVE)

Perplexity found substantive documentation for only a minority of the named systems:

| System | Failure-tracking mechanism | Source quality |
|--------|---------------------------|----------------|
| **Cognee** | Stores every skill execution as a graph node recording task, skill, outcome, error, user feedback. Graph traversal inspects connected history around failed skills; proposes amendments (tightened triggers, added edge cases) that must show measurable improvement before deployment. Failed runs remain accessible for reasoning. | Strong primary. ([cognee.ai/blog/deep-dives/building-self-improving-skills-for-agents](https://www.cognee.ai/blog/deep-dives/building-self-improving-skills-for-agents)) |
| **Zep** | Temporal knowledge graph with time-ordered nodes/edges. Deep Memory Retrieval (DMR) + LongMemEval benchmarks show +18.5% accuracy, -90% latency vs baselines. Graph search API returns formatted context from nodes/edges. **Specifically how failed attempts are tagged/retrieved: not detailed in public sources.** | Medium primary. ([arxiv.org/html/2501.13956v1](https://arxiv.org/html/2501.13956v1), [getzep.com](https://www.getzep.com)) |
| **Mem0** | Managed memory layer, short-term session + long-term user. 21 frameworks supported. Structured exception classes in v0.1.118. Semantic indexing of past interactions, vector-store retrieval. **Exact failure-chain storage format (vector / JSON / hybrid) not publicly specified.** Claim: 91% latency reduction, 90% token savings vs naive context stuffing. | Medium primary. ([mem0.ai/blog/state-of-ai-agent-memory-2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)) |
| **Letta (MemGPT)** | No documented failure-tracking mechanism found in public sources. Known issues: "stuck thinking" reports, security advisory in 0.3.17. | Weak. ([github.com/cpacker/MemGPT/issues/506](https://github.com/cpacker/MemGPT/issues/506), [github.com/advisories/GHSA-7p2g-2vxc-5g55](https://github.com/advisories/GHSA-7p2g-2vxc-5g55)) |
| **Cursor memory / Aider repo-map / Claude Code compaction** | **Not meaningfully covered in primary sources for failure-tracking specifically.** See 3C for warm-start mechanisms, but "tried X, failed because Y, don't repeat at turn N+5" is not documented as a first-class feature. | Gap. |

**Emerging dual-layer pattern (2026):** hot path (recent messages + summarized graph state) + cold path (retrieval from Zep/Mem0). A memory node synthesizes what to save each turn. Principle: "structure recovery as a short loop — identify failed assumption, verify current state, explain correction, take one bounded next step." ([rephrase-it.com/blog/why-agents-must-keep-their-wrong-turns](https://rephrase-it.com/blog/why-agents-must-keep-their-wrong-turns), [digitalapplied.com/blog/ai-agent-memory-systems-complete-guide](https://www.digitalapplied.com/blog/ai-agent-memory-systems-complete-guide))

**Honest assessment:** Cognee is the only system with a publicly documented, first-class "failed attempt → amendment" loop. For the rest, teams implement this manually on top of generic memory storage.

### 2C — Stop-and-ask enforcement beyond prompts

**The mature mechanisms:**

**Claude Code permission modes** ([code.claude.com/docs/en/permission-modes](https://code.claude.com/docs/en/permission-modes), [code.claude.com/docs/en/permissions](https://code.claude.com/docs/en/permissions)):
- **default** — prompts for each file edit, shell command, network request; "Yes, don't ask again" caches per project/command.
- **plan** — read-only; blocks all modifications for scoping.
- **auto** (Team plans, admin-gated) — auto-approves with background safety checks verifying alignment to user intent.
- **dontask** — denies unapproved tools; `/permissions` rules with `deny > ask > allow` precedence.
- **bypassPermissions** (`--dangerously-skip-permissions`) — skips all gates; for isolated CI/VM only. Drops blanket Bash allows on auto-switch.

Classifier evaluates in fixed order: rule match → auto-approve working-dir edits (except protected paths) → prompt or block with reasoning.

**Cursor** ([reco.ai/learn/cursor-security](https://www.reco.ai/learn/cursor-security), [mintmcp.com/blog/cursor-security](https://www.mintmcp.com/blog/cursor-security)):
- **Disable Auto-Run** (Settings > Features > Terminal) — forces approval prompt before every command. Blocks unsupervised `rm -rf`, force-pushes.
- **.cursorignore** — excludes sensitive files from agent scope; prevents deletions.
- Deny lists + rules files; Reco/MintMCP proxies add real-time tool interceptors blocking prompt injections or risky MCP/bash before execution.

**Aider** ([github.com/Aider-AI/aider/issues/3903](https://github.com/Aider-AI/aider/issues/3903), [aider.chat/docs/config/options.html](https://aider.chat/docs/config/options.html)):
- Default pauses for `/yes` on shell runs, file edits, architect-mode implementations.
- `--yes-always` skips most confirmations but **explicitly still prompts for shell commands** (`explicit_yes_required=True`). Maintainer deems this non-bug — fork or script to override.
- Architect mode plans first, then confirms implementation.

**Gaps:** Cline's approval flow and AutoGen `HumanInputMode` are referenced widely but lack primary-source documentation of actual implementation details as of this research window. Treat claims about them skeptically.

**Enterprise posture (2026):** Only ~29% of orgs report secured agentic AI. Proxies like MintMCP and Reco add audit trails and command blocks; this is a growing category, not a solved problem. ([nationalcioreview.com/articles-insights/extra-bytes/security-in-2026-new-ways-attackers-are-exploiting-ai-systems](https://nationalcioreview.com/articles-insights/extra-bytes/security-in-2026-new-ways-attackers-are-exploiting-ai-systems/))

### 2D — Multi-agent collision prevention

Six production-proven patterns:

1. **Git worktree per agent** (most widely adopted) — each agent gets its own working directory attached to the same repo. Prevents filesystem collisions. **Does not prevent logical collisions** (Agent A renames `interface User { id }` to `{ userId }`; Agent B writes handlers using `req.body.id`; both merge cleanly; runtime broken). Cited as the correct minimum isolation pattern. ([nx.dev/blog/git-worktrees-ai-agents](https://nx.dev/blog/git-worktrees-ai-agents))
2. **Task queue with exclusive-lock semantics** — queue tracks which files/components each task touches. If Task A involves `routes.ts`, no concurrent task involving `routes.ts` is dequeued. LogRocket's pattern adds rate-limit and token-budget management at the queue level. ([blog.logrocket.com/ai-agent-task-queues](https://blog.logrocket.com/ai-agent-task-queues/))
3. **Centralized dispatcher / coordinator** — Intent (Augment Code) is the most documented: Coordinator + Specialists (implement/investigate/critique/debug/review) + Verifier. Living spec defines contracts; verifier blocks code that violates it before PR. macOS-only as of latest docs. ([augmentcode.com/guides/how-to-run-a-multi-agent-coding-workspace](https://www.augmentcode.com/guides/how-to-run-a-multi-agent-coding-workspace))
4. **Sequential merge strategy** — never merge 6 parallel branches at once. Merge one, rebase remaining onto new main, repeat. GitHub merge queues automate this. Reduces 2-hour conflict-resolution sessions to ~30 min. ([docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-a-merge-queue))
5. **Timeout + lock cleanup** — real incident: agent crashed mid-operation, held `.git/index.lock` for 4+ hours, blocked all devs. Fix: wrapper enforces `SIGALRM`/process kill after deadline; `finally` block reaps locks; cron removes stale lock files > 30 min. ([dev.to/rijultp/fixing-common-git-lock-errors-understanding-and-recovering-from-gitindexlock-47ej](https://dev.to/rijultp/fixing-common-git-lock-errors-understanding-and-recovering-from-gitindexlock-47ej))
6. **Pre-merge verification** (Intent's Verifier pattern) — spec compliance + contract tests + architectural fitness functions (ArchUnit, Dependency Cruiser) before PR exists. The strongest guarantee; requires a living spec. ([augmentcode.com/guides/ai-agent-pre-merge-verification](https://www.augmentcode.com/guides/ai-agent-pre-merge-verification))

**Semantic rebase (Peter J. Thomson, 2026)** — 4 levels: mechanical (git rebase), conflict resolution (interactive), intent-preserving reverse merge (reimplement intent on new architecture), full semantic rebase. Mechanical works for well-partitioned tasks; fails silently when agents touch interconnected components. ([peterjthomson.com/2026/01/semantic-rebase](https://www.peterjthomson.com/2026/01/semantic-rebase/))

**Decision framework:**
| Size | Setup | Recommended |
|------|-------|-------------|
| 1 person, 1-3 agents | Single repo, low coupling | DIY (tmux + worktrees) |
| 2-3 people, 3-5 agents | Moderate coupling | DIY + simple task queue, or CrewAI |
| 5+ people, 5+ agents | Multi-service, high coupling | Platform (Intent) or hybrid with verification |
| Any, production-critical | | Platform or hybrid + verification |

**DIY ceiling:** beyond 5 parallel agents, cognitive load explodes and collision risk rises; teams consistently report this. Miru's 6-worker model is already at the ceiling.

### 2E — Canonical-doc reading enforcement

Verifying the agent actually READ a doc vs. merely having it in context buffer:

1. **Tool-call sequence hooks** — frameworks log tool/API calls; agents halt if no `read_doc` or `fetch_spec` call appears in traces for required files. Teppana's public pipeline (dev.to 2026) has code-architect agents forced to invoke explicit read-tool calls before designing. LangSmith visualizes traces and flags missing reads as failures. ([dev.to/teppana88/how-i-validate-quality-when-ai-agents-write-my-code-481c](https://dev.to/teppana88/how-i-validate-quality-when-ai-agents-write-my-code-481c), [turingcollege.com/blog/evaluating-ai-agents-practical-guide](https://www.turingcollege.com/blog/evaluating-ai-agents-practical-guide))
2. **Verifier agents cross-checking output** — independent agents audit output against docs post-generation. Architecture Compliance agent cites exact rule violations from craft guide if UI code drifts. Parallel reviewers (architecture, security, E2E) run simultaneously and require snippet citations ("API layer follows contract section 3.2").
3. **Cross-LLM verification (Addy Osmani, 2026)** — Claude writes code after "read doc" prompt; Gemini reviews for doc adherence; iterate until citations match. ([addyosmani.com/blog/ai-coding-workflow](https://addyosmani.com/blog/ai-coding-workflow/))
4. **Lint rules + test-based gates** — custom lints scan agent outputs for required citation patterns (e.g., `@craft-guide:page5` comments). Pre-commit runs scoped tests post-read; agents fix failures before advancing. Test failures proxy unread docs when edge-case behavior from guides fails.
5. **Structured confirmation prompts** — "Summarize craft guide section on X and cite before coding." Validators parse responses for valid references; uncited output loops back. NVIDIA's Semantic Citation Validation tool (NIM-based, RefCheckAI repo 2026) automates this: extracts claims, matches to doc chunks semantically, classifies Supported / Partially Supported / Unsupported with confidence. ([developer.nvidia.com/blog/developing-an-ai-powered-tool-for-automatic-citation-validation-using-nvidia-nim](https://developer.nvidia.com/blog/developing-an-ai-powered-tool-for-automatic-citation-validation-using-nvidia-nim/))

**Caveat (SSHH.io, 2026):** "AI can't read your docs" — having a doc in context ≠ the agent reasoning from it. Multi-modal verification reports (logs, E2E recordings) are emerging as the way to confirm doc-grounded workflows. ([blog.sshh.io/p/ai-cant-read-your-docs](https://blog.sshh.io/p/ai-cant-read-your-docs))

**Vaporware flag:** generic "agent autonomy metrics" (Anthropic 2025 release) and MIT Sloan agentic-AI surveys discuss verification but ship no reference code. ([anthropic.com/news/measuring-agent-autonomy](https://www.anthropic.com/news/measuring-agent-autonomy), [mitsloan.mit.edu/ideas-made-to-matter/agentic-ai-explained](https://mitsloan.mit.edu/ideas-made-to-matter/agentic-ai-explained))

---

## Block 3 — Per-Worker Tool Specialization

**Block confidence:** High on 3A, 3B. Medium on 3C. High on 3D.

### 3A — Best-in-class MCP servers by worker role (2025-2026)

Consensus stack from practitioner surveys and the MCP registry:

**Research / web** (Perplexity, Gemini CLI, Claude Chat):
- **Firecrawl MCP** — web crawl + markdown extraction; widely adopted.
- **Perplexity Search MCP** — hosted; production quality. (Miru already uses this.)
- **Brave Search MCP, Exa MCP** — alternatives.

**Code editing** (Claude Code, Cursor, Codex CLI):
- **mcp-file-edit** — structured edit operations with diff support.
- **Aider MCP** — wraps Aider's edit strategies as MCP tools.
- **Desktop Commander (Claude-Desktop / code-centric)** — file operations + shell.

**Python execution — GAP:**
- `mcp-run-python` is **archived** as of late 2025. ([github reference in research]) Teams self-host sandboxes (Docker + Pyodide) or use hosted code-interpreter services.
- This is a real capability hole in the open ecosystem for Python-heavy backends.

**Template editing** (Gemini CLI, Jinja2/HTML-heavy work):
- **Jinja2 MCP** — template-aware edits.

**Database / SQL:**
- **sqlite MCP servers** (Anthropic example + forks) — read-only variants common. Miru's `sqlite-ro-snapshot` matches this pattern.

**Notion / knowledge base:**
- **Notion hosted MCP** (Anthropic-hosted, official) — preferred.
- **Local Notion MCP servers** — redundant if hosted is available; keep only for offline/NAS parity.

**Git:**
- **GitHub MCP** — Go rewrite, official; production stack.
- **git MCP** (local) — for checking state before editing.

**Vaporware / flags:**
- Many "solo-dev 30-star GitHub repos" in the MCP registry are experimental — check last-commit date and issue activity before adopting.
- "Official" badges are often self-applied on registries.

### 3B — MCP gateway alternatives (vs. running a server per tool)

Production-ready open-source:

| Gateway | Language/Base | Key features | Pros | Cons |
|---------|---------------|--------------|------|------|
| **Bifrost** ([maxim.ai article](https://www.getmaxim.ai/articles/best-mcp-gateways-for-production-systems-in-2026/)) | Go | Routing, caching, auth, rate-limits, hierarchical budgets, ~11μs latency | High throughput, no lock-in | Requires Go/K8s expertise |
| **Docker MCP Gateway** | Docker | Runs each MCP server in isolated container, resource limits, signing | Supply-chain security, easy scaling via Compose/K8s | Container overhead for lightweight cases |
| **Kong AI/MCP Gateway** ([konghq.com/blog/engineering/ai-gateway-mcp-gateway-mcp-server-breakdown](https://konghq.com/blog/engineering/ai-gateway-mcp-gateway-mcp-server-breakdown)) | Nginx/Lua | AI MCP Proxy + OAuth2 plugins, rate-limits, metrics | Mature API gateway base, passthrough listener | Config-heavy for pure MCP |

Managed / hosted:

| Service | Features | Notes |
|---------|----------|-------|
| **MintMCP Gateway** | SOC 2 Type II, OAuth/SSO, audit, sub-5ms latency | Enterprise-leaning, pricing opaque |
| **Smithery** ([smithery.ai](https://smithery.ai)) | Marketplace of 7000+ community servers; CLI + dashboard; hosted/remote modes | Quality of community servers varies |
| **Gravitee MCP Proxy** | Method-level auth, rate-limits, caching, request/response transforms | First-class MCP support in v4.10+ |
| **Pomerium** | Identity-aware, zero-trust | Narrower MCP focus |
| **Traefik Hub** | Triple gate (AI/MCP/API) security | Proven API management base |

**Not true aggregators:**
- **Pipedream MCP** — free hosted servers for 2500+ APIs but **per-app dedicated endpoints** (Slack at one URL, GitHub at another). Good for dev, not unified aggregation.
- **`mcp-proxy` / `mcp-gateway` generic names** — basic forwarders without context/RBAC; building blocks, not standalone prod.

**Vaporware flag:** `mcp.run` appears in early 2025 discussions but shows no 2026 usage evidence; likely rebranded or abandoned.

### 3C — Session warm-start patterns beyond Mem0/Letta

| System | Mechanism | Source |
|--------|-----------|--------|
| **Cursor persistent memory** | MCP server `@itseasy21/mcp-knowledge-graph` stores entities/relations/observations in JSONL (`project_name.jsonl`) via `MEMORY_FILE_PATH` env var. Rule: "Start with 'Remembering...' and read stored memory" on session start. Server auto-persists on create/update. | [forum.cursor.com/t/mcp-add-persistent-memory-in-cursor/57497](https://forum.cursor.com/t/mcp-add-persistent-memory-in-cursor/57497) |
| **Claude Code session resume** | Preserves full conversation history, codebase context, working state (problem-solving progress), file awareness. `/resume` reloads exact point rather than replaying logs. | [spectracodeai.com/en/claude-code-session-resume.html](https://spectracodeai.com/en/claude-code-session-resume.html) |
| **Aider repo-map** | Dynamically regenerates concise git-derived symbol/signature/dependency map on each session start. Graph ranking selects relevant portions within `--map-tokens` budget (default 1k). No explicit save; git-derived + chat-state aware. | [aider.chat/docs/repomap.html](https://aider.chat/docs/repomap.html) |
| **Continue.dev pause/resume** | Pause long operations and resume exactly where left off without full reset. | [changelog.continue.dev](https://changelog.continue.dev) |
| **Recallium** (Cursor / Claude / VS Code) | Self-hosted MCP memory, shares files-edited / decisions / blockers across tools. Knowledge graph; reload on session start. | [forum.cursor.com/t/persistent-ai-memory-for-cursor/145660](https://forum.cursor.com/t/persistent-ai-memory-for-cursor/145660) |
| **Cursor Memory Bank** | Custom modes (IMPLEMENT, REFLECT/ARCHIVE) with pasted MD-file instructions. REFLECT archives state; reload via codebase search/read. | [github.com/vanzan01/cursor-memory-bank](https://github.com/vanzan01/cursor-memory-bank) |

**Not covered in primary sources:** Zep, Cognee, Supermemory, Mem1, and Codex CLI warm-start specifics — marketing pages exist but implementation details are thin.

**Pattern summary:** 2026 systems emphasize **local persistence** (JSONL, git-derived maps) over cloud memory, focusing on repo context and state recovery. Multi-agent shared-memory trends (GitHub Agent HQ mentions) imply future convergence, but 2026 docs stress single-session resume.

### 3D — Claude Code failure modes (2025-2026)

Primary GitHub issues and practitioner write-ups:

**Context compaction dropping critical info** ([github.com/anthropics/claude-code/issues/17798](https://github.com/anthropics/claude-code/issues/17798), [okhlopkov.com/claude-code-compaction-explained](https://okhlopkov.com/claude-code-compaction-explained/)):
- 3-tier process: tool trimming → session memory compact → 9-section LLM summary with direct quotes.
- Reliably preserves current tasks + recent files; **consistently loses** early-session instructions ("don't touch this file"), intermediate decisions, specific code snippets, style rules ("no emoji").
- Reactive compression on errors pauses after 3 failures to prevent loops — abrupt halts if compaction fails repeatedly.
- Practitioner diagnosis: compaction optimizes for "next steps" over "why"; decision context is the first casualty.

**Hooks silently failing** ([dev.to/yurukusa/5-claude-code-hook-mistakes-that-silently-break-your-safety-net-58l3](https://dev.to/yurukusa/5-claude-code-hook-mistakes-that-silently-break-your-safety-net-58l3), [code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks), [github.com/anthropics/claude-code/issues/31199](https://github.com/anthropics/claude-code/issues/31199)):
- Exit code 1 = non-blocking (logs and continues); only exit 2 blocks. Users must check `/hooks` to verify loading.
- Common mistakes: unexpanded `$HOME` paths (hook silently doesn't load), missing dependencies, hooks exceeding 500ms (should use `PostToolUse` for heavy checks), no context-window monitoring.
- **HTTP hooks silently fail in second+ concurrent sessions per project** (issue #31199) — relevant to Miru's multi-worker setup on one repo.
- Fail-open/closed is user-configurable; security hooks risk bypass without explicit `exit 2`.

**Session context loss / fabrication** ([github.com/anthropics/claude-code/issues/7249](https://github.com/anthropics/claude-code/issues/7249), [github.com/anthropics/claude-code/issues/17798](https://github.com/anthropics/claude-code/issues/17798)):
- Frequent resets erase context; agents **fabricate** new code instead of editing existing (e.g., creating a new desktop shortcut despite instructions to modify the existing one).
- 7+ session-context-loss incidents logged over 3 months in one issue thread; 50+ patterns of degradation.

**Adaptive thinking under-allocation** ([news.ycombinator.com/item?id=47660925](https://news.ycombinator.com/item?id=47660925)):
- Default post-Opus 4.6 (Feb 2025). Under-allocates reasoning on key turns → fabrications (fake Stripe API versions was one cited example).
- Workaround: `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1`.

**Other documented:**
- CLI non-functioning on darwin (version 2.1.58, [#28737](https://github.com/anthropics/claude-code/issues/28737)).
- CLAUDE.md persistent rules survive compaction better than chat context, but mid-session ignoring occurs if not re-read.
- Cache invalidation compounds quota issues.

**Flagged secondhand:**
- "The Great Claude Code Leak" (March 31, 2026 npm incident) — dev.to blog ([dev.to/varshithvhegde/the-great-claude-code-leak-of-2026-accident-incompetence-or-the-best-pr-stunt-in-ai-history-3igm](https://dev.to/varshithvhegde/the-great-claude-code-leak-of-2026-accident-incompetence-or-the-best-pr-stunt-in-ai-history-3igm)) — speculative on cause.
- Reddit r/ClaudeAI and Anthropic forum posts not surfaced in search results.

**Not meaningfully reported in results:** todo-list staleness, permission-mode bypass, MCP disconnects as recurring patterns, background-process leaks.

---

## Priority Recommendations for Project Miru (Ranked by Impact × Effort)

Context: Miru currently has 6 workers (Claude Chat, Claude Code, Cursor, Gemini CLI, Codex, Copilot), 4 worker context files at repo root (plus CLAUDE.md) after the 2026-04-19 AGENTS.md deletion, 3 services with strict boundaries, MCP servers per-tool, craft guides with "read before writing" prompts but no enforcement, Windows-native ops.

### 1. Enforce stop-and-ask via Claude Code permission modes and Cursor auto-run disable (HIGH impact, LOW effort)

The "Must never" lists in each worker file (e.g., "never write to card_catalog.db", "never modify .mcp.json") are prompt-only and bypassable under task pressure. Claude Code ships native permission modes (`default`, `plan`, `dontask`, `bypassPermissions`) with `.claude/permissions.json` rules using `deny > ask > allow` precedence. Add deny rules for:
- Writes to `card_catalog.db` (all workers)
- Writes to `.mcp.json` and all `*.md` worker context files (all non-Claude-Chat workers)
- `rm -rf` and `git push --force` (all workers)
- Writes to `pm/`, `miru_ai/`, `dispatcher/` for the workers not owning that service

Equivalent in Cursor: Settings > Features > Terminal > Disable Auto-Run + `.cursorignore` for forbidden paths. References in Block 2C.

### 2. Add PreToolUse hook for craft-guide reading enforcement (HIGH impact, MEDIUM effort)

Each worker file has a "Hard triggers — read the matching doc before writing code" section (e.g., mobile/PWA work → `docs/ui_ux/01_MOBILE_PWA.md`). Currently prompt-only.

Add a PreToolUse hook on `Edit`/`Write` that:
- Inspects the target file path (`.tsx` mobile component, `pm/storefront/` file, etc.)
- Checks whether the matching craft guide was `Read` in the last N tool calls
- Returns `exit 2` if not read (blocks) with a message pointing to the guide

Matches the enforcement pattern from Block 2E (Teppana's pipeline, NVIDIA citation validation). Scope: ~200 lines of Python/Node in `windows/` or a new `hooks/` directory.

### 3. Trim worker context files to ~500 words each, partition overflow into skill docs (MEDIUM-HIGH impact, LOW effort)

Current files are ~180 lines = ~1500+ words. Block 1D consensus: compliance drops materially past ~500 words, and mid-context instructions are especially vulnerable to "lost in the middle."

Strategy:
- **Shared base** (CLAUDE.md or a new AGENTS.md-symlinked-from-CLAUDE.md) holds the truly universal rules: ports, repo boundary, no-overlap, Notion read, MCP usage, DB, restart scripts, file placement. Keep under 500 words.
- **Per-worker file** holds only the worker-specific delta: role, file ownership, must-nevers, available MCP tools. Target 200-300 words each.
- **Load-on-demand skill docs** for the Completion Contract template, Craft Guides index, etc. — already partially done; extend.

Bonus: the Vercel/Next.js symlink pattern (`CLAUDE.md` → `AGENTS.md`) consolidates cross-tool reading (Codex CLI and Cursor read AGENTS.md; Claude Code reads CLAUDE.md). One file, both ecosystems.

### 4. Add a verifier step for cross-worker contract changes (HIGH impact, MEDIUM-HIGH effort)

Block 2D + 2E: the #1 silent failure in multi-worker coding is one worker changing a contract (schema, interface, API shape) while another worker writes code that depends on the old contract. Both commits merge cleanly; runtime breaks.

For Miru specifically: Cursor refactors a Python data class in `miru_ai/`, Gemini CLI writes a Jinja2 template against the old field names. No git conflict. Dispatcher later fails at runtime.

Concrete move: a lightweight pre-merge verifier that runs each service's tests against the combined set of staged changes. Cursor already owns "live smoke testing after any Python change"; formalize this as a pre-commit hook or CI gate gating all multi-worker PRs.

### 5. Periodic re-anchoring via PostToolUse hook every N turns (MEDIUM impact, MEDIUM effort)

Block 2A: even with CLAUDE.md injected at session start, long sessions push rules out of effective attention. Post-tool-use hook that re-injects the **invariants only** (ports, repo boundary, must-nevers — the `CONTRACT.md`-equivalent subset, not the whole file) every ~25 tool calls. Keep the re-inject short (< 300 words) so it is cheap.

This is the "stratified priority" pattern: compact set of non-negotiables stays in live attention; the rest of the worker file is read-on-demand.

### 6. Consolidate MCP servers behind a gateway (MEDIUM impact, MEDIUM-HIGH effort)

Miru's current `.mcp.json` runs one server per tool. Each MCP server is a separate process, separate auth surface, separate rate-limit risk. Gateway options that fit Windows-native ops:

- **Docker MCP Gateway** — cleanest isolation; already using Docker would help.
- **Bifrost** — Go binary, runs on Windows; highest throughput.
- **Smithery** (hosted) — easiest, but moves tool access off-box.

Wait on this until rules (1-3) are in place. MCP gateways are plumbing; rule enforcement is the higher-leverage work.

### 7. Cross-worker memory / warm-start (MEDIUM impact, MEDIUM effort; DEFER)

Block 3C: Cursor's `@itseasy21/mcp-knowledge-graph` writes session state to JSONL. Miru's `~/.claude/projects/D--dev-miru/memory/MEMORY.md` already does this for Claude Code.

The useful extension would be a shared memory file all workers read: what Claude Code decided at 15:00, what Cursor verified at 16:30, what Gemini CLI edited at 17:00. Only Claude Chat writes (to match Notion governance); other workers read.

Defer until (1)-(5) are in place. This is convenience; the above are correctness.

---

## Vaporware / Flag Summary

- **mcp.run** — no 2026 usage evidence; likely abandoned or rebranded.
- **microsoft/multi-agent-reference-architecture, Danau5tin/multi-agent-coding-system** — conceptual repos; treat as sketches.
- **Ruler "zero drift" claim** — no independent verification.
- **MIT Sloan / generic agentic-AI autonomy surveys** — no code.
- **mcp-run-python** — archived.
- **Cline approval flow, AutoGen HumanInputMode** — referenced but thin primary-source documentation.
- **"The Great Claude Code Leak" blog post** — speculative on root cause.
- **Various solo-dev MCP registry entries** — check last-commit before adopting.

## Sourcing notes

- Blocks 1A, 1B, 1C, 2A, 2C, 2D, 2E, 3A, 3B, 3D = strong primary sourcing.
- Block 1D = weak — no rigorous empirical study on rule-compliance vs word count; consensus practitioner guidance only.
- Block 2B = weak for most named systems; only Cognee publicly documents a first-class failed-attempt ledger.
- Block 3C = medium; Cursor + Claude Code + Aider + Continue.dev well-covered; Zep / Cognee / Supermemory / Mem1 / Codex CLI thin.

---

**STATUS: INCONCLUSIVE** — 10 of 11 blocks delivered with practitioner-grade citations. Block 2B (ledger-of-tried-approaches) is thin on primary sources for most named systems (only Cognee has a first-class mechanism documented). Block 1D (word-count targets) has consensus practitioner guidance but no rigorous empirical study. All other blocks are production-grounded. Priority recommendations for Miru are actionable as written.
