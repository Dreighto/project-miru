# Canon Sweep — 2026-07-12

Triggered by operator directive mid-session (governance-registry review surfaced one stale
line; operator flagged that the underlying SOP shift — CH no longer default canon owner —
likely means broader drift, and asked for a deeper pass).

## Sources checked

- **Runtime:** `systemctl list-units` (LogueOS/Sully/dispatch/companion units), `systemctl --failed` (none), `ss -tlnp` live ports, worktree pool listing. All active units healthy, no failed units.
- **Linear:** direct GraphQL API (`reference_linear_direct_api_bypass`). Team ticket counts: Sully 50 open, LogueOS 18 open (all Triage), Project Miru 10 open, NASDOOM 2 open. LogueOS Console project: 0 open (consistent with "superseded" tag already in operator memory).
- **File canon:** 3 parallel audits — LogueOS-Orchestrator kernel canon, project-miru canon, LogueOS-Console canon. ~20 files read across the three.
- **DB memory:** `data/logueos_memory.db` — 35 tables (schema differs from what the canon-sweep skill doc assumes — `provisional_lessons`/`lessons`/`observations`, not `worker_profile`/`stack_state`/`decisions`/`lessons_tier_0/1`). 4 Tier-1 lessons, all current, none contradicting today's findings. 160 provisional (Tier-0) lessons, most recent 2026-07-11.

## Critical drift (13 findings — one systemic root cause)

All of these stem from the same fact: **CH (Claude Chat) is no longer the default canon owner or session driver.** The operator now drives sessions solely through CC/dispatched agents. Canon across two repos still describes CH as either the active default owner or a dormant role awaiting reactivation — both are now wrong; this is a permanent reassignment, not a toggle.

**LogueOS-Orchestrator kernel:**

- [ ] `AGENTS.md` (~L204): "CH owns by default: CLAUDE.md, AGENTS.md, GEMINI.md, `CLAUDE_CHAT.md`, and all worker prompts." → CC owns by default; CH role is historical.
- [ ] `CLAUDE.md` (~L237): near-duplicate of the above.
- [ ] `CLAUDE.md` (~L243) "Worker Roster Snapshot" lists CH first as "lead architect and canon owner" — contradicts `services/dispatch_listener/src/allowlist.js` (`AIDER_WORKERS`/`ALLOWLIST_DEF`), which has **no `ch` key at all**. CH was never dispatch-wired in code; the canon claim was always aspirational, now also organizationally wrong.
- [ ] `AGENTS.md` (~L186-188) roster line "CH, CC, CDX, GMI, AGY, DPSK, CUR" + "CH... Notion read AND write (default writer)... Prefer `cc_handoff` for worker dispatches" → drop CH or relabel as retired.
- [ ] `.logueos/context/team-charter.md:84,137` — "CH owns canon by default," "lead architect, canon owner."
- [ ] `.logueos/reference/source-of-truth.md:96,142-152` — frames CH's absence as "offline" pending return, cites `data/peer_reviews/2026-05-09_ch_role_brief.md` as the reactivation trigger. Reframe as permanent reassignment to CC.
- [ ] `roadmap.md:67-68,158,163,167,181` — Phase 2/3 gated on "CH being back online," CH return listed as a planned milestone with role handback.
- [ ] `.logueos/context/guardrails.md:102` — softer version of the same claim ("CH retains write authority for brainstorm-result synthesis... not the default routing target for routine writes" implies a standing special lane that no longer applies).
- [ ] `.logueos/context/ch-tool-operations.md` — **whole file**, a capability index keyed entirely to CH ("CH freely...", "CH must check before..."). Premise is stale in full, not line-level. Candidate for archive (see Demotion below).

**project-miru:**

- [ ] `CLAUDE.md:153` — "Claude Chat (CH) — Lead Architect role (currently offline...). When active: architecture decisions, planning, worker prompt authoring, Notion writes." → reframe as historical/inactive, not conditional.
- [ ] `AGENTS.md:29` — "When CH is active: CH owns CLAUDE.md, AGENTS.md, and all worker rule files by default; CC may edit when operator explicitly authorizes it." → CC owns by default, full stop.
- [ ] `docs/dispatch/ticket_frontmatter_schema.md:26,135,279-285,310-321` — assumes CH authors ticket frontmatter and owns the `cc_handoff` dispatch path. Needs a full pass, not a line fix (design doc's actor assumption is wrong throughout).
- [ ] `docs/workforce_overlays/miru/miru-overlay-cc.md:1,3,31` — titled "Miru overlay — Claude Chat / Claude Code," frames itself as CC/CH joint canon. Retitle to CC-primary.

## Stale references (7 findings)

- [ ] project-miru `docs/ch_operations/CH_PLAYBOOK.md:4` → points at `miru-context/ch-tool-operations.md`, **file does not exist**.
- [ ] project-miru `docs/workforce_overlays/miru/miru-overlay-cc.md:32-33` → points at `miru-context/team-charter.md` and `miru-context/job-stewardship.md`, **both missing**.
- [ ] project-miru `CLAUDE_CHAT.md` (8 occurrences, e.g. L56, L330) and `PROJECT_MIRU_INSTRUCTIONS.md` (8 occurrences, e.g. L21, L62) — still `D:\dev\miru` / `D:\dev\LogueOS-Orchestrator\...` Windows paths. `CLAUDE.md` in the same repo already migrated to `~/dev/...` (updated 2026-06-22); these two files were last touched 2026-05-19 and never got the pass.
- [ ] LogueOS-Console `GEMINI.md:58-59` — restart procedure references port **18080** (dead — nothing listens there) and a `pm2 restart all` / "windows startup scripts" flow. Live service is `logueos-console.service` on **18767** (confirmed via `systemctl is-active` + `curl` 200), systemd-managed, no pm2 installed. Procedure predates the 2026-05-25 Linux migration.
- [ ] LogueOS-Console `GEMINI.md:12-13`, `README.md:32-33,43-45` — `D:\dev\LogueOS-Console`, `D:\dev\LogueOS-Orchestrator\...` Windows paths; targets exist under `/home/dreighto/dev/...`, only the path syntax is wrong.
- [ ] `.logueos/reference/governance-file-registry.md`, cited by the `pr-governance-template` skill as the authoritative governed-path list — **never existed** in this repo's git history. Already corrected in operator memory this session (`reference_governance_gate_required_section_header.md`); flagging here so the kernel-side doc gap is on record too, since nothing in the repo points at the _actual_ source of truth (`.logueos/governance.json`) except the script itself.
- [ ] The `canon-sweep` skill's own Pass-4 DB table list (`worker_profile`, `stack_state`, `decisions`, `lessons_tier_0`, `lessons_tier_1`) doesn't match the live schema (`provisional_lessons`, `lessons`, `observations`, plus 30+ operational tables unrelated to tactical memory). Low priority, but the skill will mislead the next sweep if not corrected.

## Promotion candidates (0 findings)

None surfaced this round. Tier-1 `lessons` (4 rows) all look current and already correctly promoted; no strong provisional-lesson candidate stood out in a spot check. Note as a separate observation, not a finding: `provisional_lessons` has 160 rows against only 4 promoted — a real synthesis backlog exists, but assessing which ones deserve promotion needs a dedicated pass, not a byproduct of this sweep.

## Demotion candidates (3 findings)

- [ ] `.logueos/context/ch-tool-operations.md` (kernel) — archive; premise (CH as active tool operator) no longer holds.
- [ ] project-miru `docs/ch_operations/CH_PLAYBOOK.md` — archive or re-home the still-useful dispatch/ops patterns under a CC-operations doc; the CH-as-orchestrator framing throughout is stale.
- [ ] Version stamps: kernel `CLAUDE.md` ("Effective: 2026-05-11") and `AGENTS.md` ("Effective: 2026-05-12") haven't been bumped despite commits as recent as today (`746c3fdb`, `081b55e1`). Not urgent on its own, but worth bumping in the same pass as the CH-ownership fix since that fix touches both files anyway.

## Not stale (confirmed clean)

- Console repo has no dead file-path references, no prod-vs-dev :18769 claim anywhere in its canon (that number isn't sourced from this repo).
- project-miru: ticket ID format consistent everywhere sampled; no stray "Miru"-branding leakage into kernel-facing sections; `miru-context/miru-service-catalog.md`, `miru-protected-constraints.md`, `miru-vocab.md`, kernel `worker-roster.md`, `data/context/state-handoff-log.md` all exist and check out.
- Kernel: no dead file paths, no live Windows-path claims, no port/service contradictions beyond what's listed above; `.miru/` path references in `roadmap.md` are correctly framed as historical.
- DB memory (Tier 0/1): fresh, no contradictions with today's findings.

## Next sweep due: 2026-07-15 (3-day cadence)
