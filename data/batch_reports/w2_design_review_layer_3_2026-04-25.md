# W2 Design Review — Layer 3 (dispatch loop)

**Date:** 2026-04-25
**Linear ticket:** [PRO-79](https://linear.app/project-miru/issue/PRO-79)
**Layers 1+2:** see [`w2_design_review_layers_1_2_2026-04-25.md`](./w2_design_review_layers_1_2_2026-04-25.md) (Cursor)
**Scope:** the autonomous-handoff gap — once W2 routes and W7 captures approval, what triggers the worker.

## 1. What we currently have

End-to-end loop today (verified live 2026-04-25 against PRO-77): W1 drafts → W2 scores → W2 mints HMAC token + Telegram intent → W7 receives callback, validates, applies/strips Linear labels, edits the Telegram message, appends `outcome:"callback-decided"` to `routing_history.jsonl` ([w7-telegram-callback-handler.json:207](../../docker/n8n/workflows/w7-telegram-callback-handler.json#L207)). After W7's mutation, **the loop ends.** The Linear issue carries the worker label and a confirmation comment, but no process picks it up — operator has to RDP into ROOM, open the matching IDE/CLI, and paste the prompt by hand. Canon already specifies W4 ("Approved Plan → Worker Dispatch") with two modes — Mode A manual paste (Phase 1 default), Mode B file-drop + signed Execute button — but **W4 is unbuilt**: no `data/n8n_inbox/` directory, no listener, no Execute-button workflow ([n8n canon §W4](https://www.notion.so/34bc5d340141810a88adeb38c3e9fbc6)). Hard rule from canon: "W4 NEVER auto-executes in any mode or phase." The operator click is a non-negotiable.

## 2. What the research surfaced

### Headless invocation maturity (late 2025 / early 2026)

All four workers now ship first-class non-interactive modes, contrary to the assumption baked into Phase 1 Mode A:

- **Claude Code**: `claude -p "prompt"` / stdin, with `--bare` (no hooks/memory), `--max-turns`, `--allowedTools`, `--max-budget-usd`, `--output-format json` ([SFEIR cheatsheet](https://institute.sfeir.com/en/claude-code/claude-code-headless-mode-and-ci-cd/cheatsheet/), [code.claude.com/docs/headless](https://code.claude.com/docs/en/headless))
- **Gemini CLI**: `gemini -p`, `--approval-mode auto_edit`, `--output-format json`, exit codes 0/1 ([google-gemini.github.io/gemini-cli/docs/cli/headless](https://google-gemini.github.io/gemini-cli/docs/cli/headless.html))
- **Codex**: `codex exec "prompt"` non-interactive by default ([augmentcode.com Codex CLI guide](https://www.augmentcode.com/learn/openai-codex-cli-terminal-agent))
- **Cursor `cursor-agent`**: `-p` headless, but MCP-approval surface incomplete; users work around with `--approve-mcps --force` ([forum.cursor.com 143045](https://forum.cursor.com/t/cursor-cli-mcp-the-non-interactive-mode-cannot-be-used/143045), [docs.praison.ai cursor-cli](https://docs.praison.ai/docs/code/cursor-cli)). Canon already flags Cursor as Linear-comment-only for Mode B.

### Trigger patterns (concrete trade-offs)

| Pattern | Latency | Reliability | Visibility | Setup cost |
|---|---|---|---|---|
| **A. Polling worker** (daemon polls Linear every N min for label+state match) | 1–5 min ([hatchworks.com](https://hatchworks.com/blog/ai-agents/orchestrating-ai-agents/)) | Daemon offline → missed polls; supervisor (NSSM/PM2) restart needed | Logs only; no real-time intercept | Medium (long-running service) |
| **B. Webhook + host listener** (n8n → `http://host.docker.internal:PORT` → Express/FastAPI → spawn CLI) | 2–15 s | HTTP retries built in; daemon as Windows service | Excellent — daemon logs + n8n exec trace ([n8n community](https://community.n8n.io/t/how-to-self-host-n8n-using-docker/178172)) | Low — ~20 LOC listener |
| **C. File-drop + watcher** (n8n writes JSON to `data/n8n_inbox/`; chokidar/watchdog picks up) | <10 s | Watchers miss bursts on crash/restart; needs `.lock` discipline ([community.n8n.io 54893](https://community.n8n.io/t/how-to-route-local-file-trigger-node-in-locally-hosted-n8n-in-docker/54893)) | Good — file timestamps; queue browseable | Medium — volume mount + watcher |
| **D. Telegram "Dispatch" button** (W7 emits second inline keyboard after Approve; operator tap → webhook → CLI) | 2–15 s + operator click | Same as B once tapped; preserves explicit consent | Highest — full chat history, abort by ignoring | Low–Medium — extends W7 + same listener as B |
| **E. Queue-backed (Redis/SQLite/NATS)** | Sub-second after enqueue | Very high (durable) | Queue metrics | High — extra service for single-operator scale ([n8n queue mode](https://docs.n8n.io/hosting/scaling/queue-mode/)) |

The **n8n Docker community converges on B** for host-CLI invocation (`host.docker.internal:PORT` is the standard reachability primitive); file-drop is common for triggers but less so for dispatch ([n8n docker docs](https://docs.n8n.io/hosting/installation/docker/), [localxpose.io n8n](https://localxpose.io/blog/expose-n8n-to-internet)). Polling shows up in self-hosted Aider/Continue/Tabby setups but is the heaviest pattern — a side effect of those tools predating reliable webhook surfaces ([dev.to self-hosted-ai-code-generation](https://dev.to/techstuff/self-hosted-ai-code-generation-the-complete-guide-to-building-your-private-ai-coding-assistant-4ncj)).

## 3. Recommendation — Hybrid D + B + C

**Pick Telegram-gated Dispatch (D) layered on a host webhook listener (B), with a file-drop sidecar (C) for audit and replay.** Concretely:

1. **Extend W7 with a second-stage Telegram message.** When the Approve action lands and the worker is one of {claude-code, codex, gemini} (i.e. CLI-invocable), W7 emits a follow-up message with one inline button: `🚀 Dispatch <worker>`. Cursor stays on the existing flow (Linear-comment only — already canon).
2. **Build W4 as the Dispatch-button handler.** Tap → Telegram → W4 webhook trigger → fetch Linear issue body + research findings + canon refs → assemble prompt JSON per the Worker Prompt Requirements schema → write to `data/n8n_inbox/<trace_id>.json` (audit/replay artifact, mirrors canon Mode B inbox spec) → POST to `http://host.docker.internal:19100/dispatch` → small Node listener on ROOM spawns `claude -p < prompt.json` (or `gemini -p`, or `codex exec`) with `--allowedTools` / `--approval-mode auto_edit` set per risk level → captures stdout/stderr → posts back to Linear as a comment with the receipt.
3. **Why this combination.**
   - **D preserves the operator-in-the-loop posture (canon-locked).** Approve = "this is the right worker." Dispatch = "go now." Two clicks, two distinct decisions, both auditable in Telegram chat history. Eliminates the RDP-and-paste step without giving up the consent gate.
   - **B is the lightest reliable trigger.** Webhook + 20-LOC listener is the n8n-Docker community default; far less moving than a polling daemon, far more recoverable than file-watcher races. Listener runs as a Windows service via NSSM (already in repo for restarts).
   - **C as sidecar, not primary**: the prompt JSON written to `data/n8n_inbox/` is the canonical audit artifact and replay key — if the listener crashes mid-spawn, the operator (or a future `w_dispatch_recovery` workflow) can replay from disk. Aligns with canon's Mode B file-drop spec without requiring a watcher process.
   - **Headless-CLI maturity** is no longer a blocker for any worker except Cursor — the Phase-1 assumption that "everything is interactive paste" is out of date.
4. **Out of scope on purpose**: polling worker (heavyweight, no operator click), pure file-drop (no consent gate, MOA spec already lays C out for Phase 2 anyway), queue-backed (single-operator, low-volume — overkill).

This is essentially **codifying canon's Mode B with a concrete mechanism** (Telegram → webhook listener → headless CLI), not inventing something new. Roadmap fit: matches the Phase 2 entry criterion that Mode B becomes "opt-in per workflow" — operator promotes the dispatch flow worker-by-worker after watching it run.

## 4. Open questions / unknowns

1. **Listener auth & blast radius.** `host.docker.internal:19100` is reachable only from the n8n container, but accidental exposure (Tailscale Funnel mistake, port published to `0.0.0.0`) would allow remote command execution. Need: shared-secret HMAC on POST body, listener bound to `127.0.0.1` only, hard allowlist of CLI binaries it can spawn. Verify before first run.
2. **Working-directory & repo state on dispatch.** Does the listener `cd D:/dev/miru` and run on the current branch, or should W4 ensure a clean checkout / dedicated worktree per dispatch? Operator-in-the-loop posture argues for dispatching into the operator's live tree (matches today's manual flow); concurrency argues for worktree isolation. Lean live tree at Phase 1, revisit when concurrency becomes real.
3. **Cursor's place in Mode B.** Canon says "Linear-comment delivery only." Does that mean no Dispatch button at all, or a degenerate Dispatch button that just opens a deep link to the Linear issue in the operator's browser? Pick one and document.
4. **Receipt-back path.** Listener captures stdout — but Claude Code in `-p` mode emits the final message only; tool calls and intermediate state need `--output-format json`. What's the canonical receipt shape that W5 expects? Worth defining the schema before W4/W5 ship.
5. **Long-running workers.** A `claude -p` call on a multi-file refactor can run minutes. Listener should spawn detached + return 202, with W4 polling for completion via a result file written by the listener's wrapper. Don't hold the HTTP request open.

## 5. Cross-layer concerns

- **Idempotency keys (Cursor's Layer-2 must-fix)** apply directly to W4 dispatch. The Telegram Dispatch callback re-uses the existing W7 HMAC token (12 hex chars, already replay-protected via `w7004-hmac-validate`). The listener should treat that token as the dispatch idempotency key — refuse to re-spawn if a `data/n8n_inbox/<trace_id>.json` already has an associated `<trace_id>.result.json`. This piggybacks on the existing replay defense rather than introducing a new key store.
- **DLQ schema (Layer-2 worth-adopting)** has a clean fit at the listener boundary: any spawn failure (binary missing, non-zero exit, timeout) writes a parked-payload row to `data/dispatch_dlq.jsonl` with `{trace_id, worker, prompt_path, exit_code, stderr_tail, timestamp}` plus a Telegram alert. That's the dispatch-loop's first DLQ surface; the Cursor-recommended retention (30 days) and replay context fit it directly.
- **Schema version on append-only files (Layer-2 worth-adopting).** The proposed listener spawns a new artifact stream (`<trace_id>.json`, `<trace_id>.result.json`, `dispatch_dlq.jsonl`). These should ship with `schema_version: "v1"` from day one — cheaper than retrofitting, and Layers 1+2 already flagged this as the lesson learned from `pending_callbacks.jsonl` (PRO-77 GC needed because the file shipped without versioning OR rotation).
- **Phase-2 calibration interaction.** Once W2 calibration improves enough to drop the W8 approval gate at confidence ≥ 0.90 (canon Phase 2), the Telegram Dispatch button becomes the *only* operator gate between W2 scoring and worker execution. Before that promotion lands, W4's Dispatch button must be running cleanly for ≥30 dispatches. Treat Layer 3 as a hard prerequisite for canon's Phase 2 entry, not a parallel track.

---

## Completion contract

**STATUS: CONFIRMED WORKING**

- **Report file:** `data/batch_reports/w2_design_review_layer_3_2026-04-25.md` (this file)
- **Linear ticket:** [PRO-79 — W2 design review research pass — Layer 3 (dispatch loop)](https://linear.app/project-miru/issue/PRO-79)
- **Summary:** Layer 3 maps the autonomous-handoff gap: today's loop ends at W7's label mutation, leaving a manual paste step. Research shows headless one-shot CLIs are mature for Claude Code, Gemini, and Codex (Cursor partial); n8n-Docker community converges on `host.docker.internal:PORT` webhook + small host listener for CLI dispatch. Recommended pattern is **Telegram-gated Dispatch (D) → host webhook listener (B) → headless CLI**, with file-drop (C) kept as audit/replay sidecar. This codifies canon's Mode B with a concrete mechanism, preserves the locked operator-in-the-loop posture (two distinct clicks: Approve, then Dispatch), and is a hard prerequisite for canon's Phase 2 entry. Five open questions on the recommendation are unresolved (listener auth, working-dir model, Cursor's Mode-B shape, receipt schema, long-running spawn handling) and called out for operator decision before W4 implementation begins.
