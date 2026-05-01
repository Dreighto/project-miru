# Canon Contract

How knowledge enters and lives in the Miru system. This document governs the flow of
information from raw execution (Linear) into durable system knowledge (Notion). It does
not cover how existing canon stays aligned with reality — that is canon-and-drift.md.

Last updated: 2026-05-01

---

## 1. The Two-Tier Knowledge Model

**Linear = Raw Execution Memory.**
Linear holds the trail of what was tried, decided, built, and shipped. Every ticket,
comment, and state transition is part of the execution record. This is working memory —
detailed, temporary in importance, authoritative for its own scope.

**Notion = Distilled System Canon.**
Notion holds the conclusions that outlive the work that produced them. Architecture
decisions, reusable patterns, hard-won lessons, rules that govern future work. This is
long-term memory — compressed, permanent, authoritative across tasks.

**The relationship between them:**
Linear is always written first. Notion is written when something in Linear has been
validated and is worth carrying forward. Moving knowledge from Linear to Notion is
called _promotion_. It is a judgment call, not an automatic step.

---

## 2. Promotion Test

Before promoting anything from Linear to Notion, apply this test:

> **"Will this help future workers make better decisions across more than one task?"**

If yes: promote.
If no: leave it in Linear. The execution trail is already there.

### Promotion examples

| Finding                                                                                      | Promote? | Why                                                                                          |
| -------------------------------------------------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------- |
| PM Dashboard health endpoint is `/__pm_health`, not `/health`                                | Yes      | Every future worker touching PM needs this; `/health` returns 200 silently via SPA catch-all |
| The `ruff-format` pre-commit hook modified `health_check.py` on first run                    | No       | One-time fix; not reusable guidance                                                          |
| Ollama freeform text scanning is fragile — use `format: json` with `should_escalate` boolean | Yes      | Changes how all future Ollama integrations should be designed                                |
| PRO-209 retried the dispatch three times before succeeding                                   | No       | Operational noise; no rule generalizes from this                                             |
| n8n owns the Telegram webhook; `getUpdates` from any other code returns HTTP 409             | Yes      | Architectural constraint that affects any future Telegram integration                        |
| Worker took 8 minutes on a task expected to take 2                                           | No       | Not actionable without root cause                                                            |
| Append-only JSONL files must use `open(path, "a")` — never read-modify-write                 | Yes      | Enforced contract; every future file-writing task needs this                                 |

### Anti-noise list — do NOT promote these

- One-off bugs that were fixed and won't recur (e.g. a typo, a missing import).
- Temporary failures that resolved without a systemic fix (e.g. service restarted itself).
- Unverified hypotheses (e.g. "I think the issue might be X" — promote after confirmation).
- Single-task implementation details that aren't reusable patterns.
- Raw logs, stack traces, or error output — these belong in Linear comments, not Notion.

---

## 3. Retroactive Promotion Authority

Claude Chat may promote findings from **closed** Linear tickets into Notion without
reopening the ticket. Closed tickets are a valid source. The value of a lesson does
not expire when a ticket closes.

### Requirements for retroactive promotion

All four must be true before promoting from a closed ticket:

1. **Reusable** — the finding applies to future work, not just the closed ticket's context.
2. **Confirmed** — the finding was validated (not a hypothesis or a theory that wasn't tested).
3. **Distilled** — the promotion is a synthesized rule or pattern, not a copy-paste of the ticket's text.
4. **Not already captured** — a search of existing Notion canon finds no adequate existing entry.

If requirement 4 fails (canon already exists), update the existing entry instead of
creating a duplicate. See Section 5 (Deduplication).

### Attribution

Every promoted entry must reference its originating ticket or PR briefly. This preserves
the reasoning chain for anyone who needs to trace a rule back to its source.

Format: `Source: PRO-### (YYYY-MM-DD)` or `Source: PR #NN (YYYY-MM-DD)`.

Do NOT copy raw ticket content. The Notion entry is a distilled rule, not a ticket mirror.

---

## 4. Canon Lifecycle

Canon is not static. Every entry has a lifecycle state:

| State          | Meaning                                                                                           |
| -------------- | ------------------------------------------------------------------------------------------------- |
| **Active**     | In use. Workers should follow this rule or pattern.                                               |
| **Deprecated** | Superseded by a newer entry. The old entry stays for traceability but is no longer authoritative. |
| **Merged**     | Absorbed into another entry. The merged entry points to the surviving one.                        |
| **Replaced**   | The rule was wrong or outdated. The replacement entry explains why.                               |

Rules:

- Never silently delete a canon entry. Deprecate or replace it instead, with a note explaining why.
- When replacing, cross-reference both entries: the old entry says "Replaced by X", the new entry says "Replaces Y".
- Deprecated entries do not get updated — they are frozen at the point of deprecation.

---

## 5. Deduplication Before Promotion

Before creating a new Notion entry, search for an existing one. This prevents canon
from fragmenting into near-duplicate entries that drift out of sync.

### Deduplication flow

1. **Search first.** Run a Notion search for the topic, the service name, or the pattern name.
2. **Update existing.** If a suitable entry exists, update it — add the new finding, expand the scope, correct outdated content.
3. **Only create new** if no suitable home exists. "Suitable" means the new finding fits the existing entry's scope without distorting it.
4. **If scope is genuinely different** — the new finding is a distinct concept, not just an addition — create a new entry with a clear title and cross-reference the related entry.

If two entries cover overlapping territory: merge them. Pick one as the surviving
entry, move content into it, and mark the other as merged with a pointer.

---

## 6. Startup Read Sync

When new canon is created, the session-start read list in CLAUDE_CHAT.md must be
evaluated. Not every new document needs to be added — only documents that Claude Chat
actively needs at the start of every session to make good routing and dispatch decisions.

**Criteria for session-start inclusion:**

- Claude Chat would make worse decisions on routine tasks without it, OR
- The document changes Claude Chat's default behavior in a material way.

**Not session-start material** (reference-on-demand instead):

- Documents that apply only in specific scenarios (e.g. retry-backoff.md, kill-switch.md)
- Documents that workers need but Claude Chat doesn't consult routinely

When adding a new session-start read: update CLAUDE_CHAT.md immediately in the same
commit as the new document. Do not leave CLAUDE_CHAT.md out of sync.

---

## 7. When These Rules Conflict with CLAUDE.md or Operator Directives

CLAUDE.md and explicit operator instructions win. Always.
Flag the conflict rather than silently overriding — but follow the explicit instruction.

This document fills the gap when CLAUDE.md does not address a specific knowledge-flow
situation.
