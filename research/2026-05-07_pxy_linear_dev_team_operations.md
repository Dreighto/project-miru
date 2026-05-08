# How Dev Teams Operate + Linear Business Plan Deep Dive

**Source:** Perplexity Deep Research, 2026-05-07
**Context:** Operator wants to understand how real dev teams work and how to get full value from Linear Business ($18/mo)

---

## Part 1: How Solid Dev Teams Operate

### Cycles (Sprints)

- 2-week cycles are the sweet spot — enough time to build, short enough to stay responsive
- At cycle start: commit to a realistic body of work with clear success criteria
- At cycle end: retro — what shipped, what blocked, what to improve
- Unfinished work carries forward automatically — no penalty, no artificial pressure

### Ticket Lifecycle

Standard flow: **Backlog → Todo → In Progress → In Review → Done**

- Each status transition should mean something real (branch created, PR opened, merged)
- Automate transitions where possible (PR merge → issue Done)
- Optional: add Blocked status for external dependencies

### Backlog Grooming

- 15-30 min/week per developer, mid-cycle (NOT during planning)
- Progressive elaboration: rough items at bottom, detailed items at top
- Keep backlog lean — close stale items, merge duplicates, archive "maybe someday"
- Maintain 1-2 cycles of "ready" work as a buffer
- Bring data: effort history, usage analytics, customer signals

### Prioritization

- Use explicit framework: customer impact × business impact × risk reduction × effort
- Link high-priority items to the context that justified them (customer tickets, analytics, strategy)
- Consistent framework prevents thrashing from whoever shouts loudest

### Sprint vs Kanban (Hybrid)

- Sprints for planned feature work (predictable, committed)
- Kanban for reactive work (bugs, support, incidents)
- Typical split: 60% planned / 40% reactive capacity

### Code Review

- Small, focused PRs — large PRs get shallow reviews
- Clear review standards: coding conventions, test coverage, potential bugs, better alternatives
- Automate where possible: CI runs tests, linters catch style issues

### Release Management

- CI/CD automates build → test → stage → deploy
- Every release has rollback capability
- Incremental releases (small, frequent) over big-bang releases
- Release notes document what changed

### WIP Limits

- Limit In Progress to 1 issue per person at a time
- If blocked, can start another, but limit prevents shallow progress on too many things
- This is one of the highest-leverage practices for shipping speed

---

## Part 2: Linear Business Plan — What You're Paying For

### Business Plan Features (vs Basic/Free)

| Feature                       | Free | Basic ($10) | Business ($18) |
| ----------------------------- | ---- | ----------- | -------------- |
| Issues                        | 250  | Unlimited   | Unlimited      |
| Teams                         | 2    | 5           | Unlimited      |
| Triage Intelligence (AI)      | No   | No          | Yes            |
| Linear Agent automations      | No   | No          | Yes (beta)     |
| Linear Insights (analytics)   | No   | No          | Yes            |
| Linear Asks (request capture) | No   | No          | Yes            |
| SLAs                          | No   | No          | Yes            |
| Private teams + guests        | No   | No          | Yes            |

### Features Worth Using NOW

**1. Triage Intelligence**
AI analyzes every new issue against your history. Suggests assignee, labels, and surfaces duplicates/related issues. Reduces manual triage work.

**2. Linear Agent (beta)**
Natural-language automations: "When a security issue arrives, assign to the security team, set priority Critical, add monitoring links." Handles complex routing without code.

**3. Linear Insights**
Real-time dashboards: issue count, cycle time, triage time, lead time, issue age. Drill down on any metric. Spot bottlenecks.

**4. SLAs**
Auto-apply deadlines based on priority. Visual indicator (fire icon) transitions from gray → yellow → orange → red as deadline approaches. Notifications 24h before breach.

**5. GitHub Integration**

- Auto-link PRs to issues via branch name or PR description
- Auto-move issues to In Progress when branch is created
- Auto-move to Done when PR merges
- Set up in workspace settings → team workflow settings

**6. MCP Server**
Linear has an official MCP server. AI agents (Claude, Cursor) can:

- Query issues, projects, cycles
- Create/update issues
- Add comments
- Change statuses
  All programmatically through the MCP protocol with OAuth or API key auth.

**7. Linear Asks**
Capture requests from Slack. Anyone in the Slack workspace can submit structured requests → goes to Triage queue.

**8. Webhooks**
HMAC-SHA256 signed. Trigger external actions on issue changes. E.g., notify Telegram on high-priority issue creation.

### Workflow Automations to Set Up

- PR merge → issue moves to Done
- Branch created from issue → issue moves to In Progress
- Issue created with "security" label → auto-assign, set priority Critical
- Issue in "In Progress" with no update for 7 days → flag for review
- High-priority issue created → Telegram notification

### Custom Views to Create

| View                   | What it shows                                         | Question it answers                         |
| ---------------------- | ----------------------------------------------------- | ------------------------------------------- |
| Current Cycle Progress | Active cycle, grouped by status, sorted by priority   | What did we commit to? Where is it?         |
| Blocked Work           | Issues with Blocked status or unresolved dependencies | What's stuck?                               |
| Production Issues      | Critical/High priority, current + recent cycles       | What's on fire?                             |
| Backlog Grooming       | Top of backlog, missing acceptance criteria           | What needs clarification before next cycle? |

### Common Mistakes to Avoid

1. **Filing system syndrome** — create issues then never update them. Fix: automate status transitions via GitHub integration
2. **Unlimited backlog** — hundreds of stale items. Fix: archive items >1 year old, move speculative items to "Future Ideas" project
3. **Skipping grooming** — clarify requirements during planning under pressure. Fix: 30 min/week grooming, non-negotiable
4. **No WIP limits** — too many things in progress. Fix: 1 active issue per person
5. **Poor GitHub integration** — PRs not linked to issues. Fix: branch name includes issue ID (e.g., `PRO-123-fix-auth`)

### API Access for AI Agents

- GraphQL API with @linear/sdk (TypeScript)
- MCP server for direct AI agent integration
- Webhook subscriptions for event-driven automation
- All operations: query, create, update, comment, status change
