# Async Agent-to-Agent Communication in Multi-Agent CLI Systems

## Executive Summary

Production multi-agent CLI systems face a hard coordination problem: when an executing worker agent hits an ambiguous specification mid-task, it cannot route through a human or a conversational UI without creating a synchronous bottleneck. The canonical solution is a **suspend-consult-resume** state machine backed by a durable shared message store. The three dominant store implementations — file-based async, Redis Pub/Sub or Streams, and SQLite WAL-mode with a shared `agent_messages` table — each occupy a distinct position in the design space, with meaningful trade-offs in durability, latency, operational overhead, and concurrency safety.

---

## The Core Problem: Mid-Task Ambiguity Without a Human Relay

When multi-agent systems were first being studied, coordination flowed through a central orchestrator: Agent A paused, reported a dependency to the user/orchestrator, the user relayed to Agent B, Agent B processed, the user notified Agent A, and only then did Agent A resume. At scale — 10 to 50+ agents — this model is untenable. The user becomes a message bus, not a decision-maker, and the parallelism the multi-agent design was supposed to unlock collapses.[^1]

The fundamental architectural requirement for production systems is that a worker in the `RUNNING` state must be able to:

1. Detect an ambiguity or dependency gap without crashing or hallucinating through it
2. Atomically emit a structured consultation request to a peer agent
3. Transition to a `SUSPENDED` state, preserving its full execution checkpoint
4. Resume deterministically once the peer's response is written to the shared channel

The SW4RM agentic protocol formalizes exactly this lifecycle, distinguishing `SUSPENDED` (preempted with state preserved for resumption) from `FAILED`, and providing explicit `suspend()` and `resume()` transitions with checkpoint hooks. Google's A2A Protocol formalizes the same idea at the network layer with five canonical task lifecycle states: `submitted`, `working`, `input-required`, `completed`, and `failed` — where `input-required` is the production-grade label for "I need a peer consult before I can proceed".[^2][^3]

---

## The Canonical Production Pattern: Suspend-Consult-Resume

### Pattern Overview

The pattern recognized across production deployments in 2025–2026 is not a direct synchronous call from Worker A to Worker B. Instead, it follows an asynchronous request-response over a durable shared channel:

1. **Worker A detects ambiguity** → serializes a structured `ClarificationRequest` message (with `task_id`, `from_agent`, `to_agent`, `context_snapshot`, and `question_payload`) to the shared channel
2. **Worker A suspends** → saves a checkpoint of its current state (file edits in progress, variables, tool call stack) to persistent storage; transitions to `SUSPENDED`
3. **Peer Agent B is notified** → via the same channel; reads the request, generates a response, writes `ClarificationResponse` back
4. **Worker A is awakened** → polls or receives a notification from the channel; loads its checkpoint; resumes from the exact point it paused
5. **Worker A applies the clarification** and continues execution

The key insight from practitioners is that "chat history is not a coordination layer." A long transcript can carry one session through one task. The moment work splits into peers operating in parallel, chat memory stops being a system and starts being a liability — scope drifts and two agents solve different versions of the same problem.[^4]

### Protocol Envelope Requirements

For a consultation to be replayable and auditable, the message envelope must carry:[^5][^6]

- **Identity**: `from_agent_id`, `to_agent_id`, capability tags
- **Correlation**: `task_id` + `parent_context_id` so the response can be routed back
- **Payload**: the ambiguous spec fragment (not just a prose question — a structured excerpt)
- **Timestamp + TTL**: to detect stale consultations and prevent deadlock
- **State reference**: pointer to the checkpoint blob so Worker A can resume correctly

Google's A2A `context handoff` mechanism encodes this: one agent passes relevant task state to another without exposing its full internal state, using the Task object's structured schema including task descriptions, context objects, constraint specifications, and result formats.[^7]

---

## Communication Channel Comparison

### File-Based Async Communication

The simplest approach: workers exchange JSON files in a shared directory, using naming conventions like `{task_id}.request.json` and `{task_id}.response.json`, potentially with `.lock` sentinel files.

**Strengths:**

- Zero infrastructure dependencies — works entirely in the local filesystem[^4]
- Naturally persistent: files survive process restarts, crashes, and reboots
- Debuggable: a human can `cat` any message at any time
- Native fit for CLI-local environments (no Docker networking required)
- Files "survive session boundaries, can be reviewed, can be updated mid-run, and don't depend on anyone remembering what paragraph three said"[^4]

**Failure modes:**

- **Race conditions**: without explicit locking, two agents writing to the same file produce silent data loss (confirmed in Claude Code's concurrent memory file bug where "last write wins")[^8]
- **Stale reads**: an agent downloads and works on an outdated version, producing incorrect output[^9]
- **Deadlocks**: agents acquiring locks on mutually dependent files can stall the entire workflow[^9]
- **No delivery guarantees**: there is no acknowledgment mechanism; a response file can be written but never consumed
- **No fan-out**: broadcasting a request to multiple potential peers requires writing N files and polling N locations
- **No ordering guarantee**: when multiple messages accumulate, there is no timestamp-based processing order beyond filesystem mtime (unreliable)

**Concurrency pattern required:** The minimum safe implementation uses `flock()` (or platform equivalent) around every read-modify-write, plus an append-only log with periodic compaction rather than in-place overwrites. Copy-on-write (read → modify private copy → atomic rename) is the recommended pattern for document-style files.[^8][^9]

**Verdict:** Appropriate for prototype systems, single-machine low-concurrency setups, or as the handoff format between sequential pipeline stages. Breaks under parallel execution pressure without careful locking discipline.

---

### Redis Pub/Sub vs. Redis Streams

Redis offers two distinct messaging primitives that are frequently confused.[^10]

#### Redis Pub/Sub

A real-time broadcast mechanism where publishers push to named channels and all current subscribers receive the message. There is no persistence: if a subscriber is offline when a message is published, the message is lost forever.[^11][^12]

| Property            | Value                          |
| ------------------- | ------------------------------ |
| Delivery guarantee  | At-most-once (fire-and-forget) |
| Message persistence | None                           |
| Missed messages     | Lost forever                   |
| Consumer groups     | Not supported                  |
| Ordering guarantee  | Per-channel (not global)       |
| Latency             | Sub-millisecond (in-memory)    |

For A2A consultation, Pub/Sub is **structurally wrong** when Worker A suspends before the peer can read. If Worker B is busy or restarting at the moment Worker A publishes its clarification request, the message is silently dropped. Worker A waits indefinitely for a response that will never arrive. This is not a recoverable failure mode.

#### Redis Streams

Redis Streams is an append-only log structure where every message is assigned a unique monotonic ID and is stored persistently until explicitly trimmed. Consumers can read from any point in the stream, reconnect after crashes without data loss, and use consumer groups for load distribution with acknowledgment (ACK) semantics.[^12][^13][^14][^10]

| Property                    | Value                           |
| --------------------------- | ------------------------------- |
| Delivery guarantee          | At-least-once (with ACK)        |
| Message persistence         | Durable, configurable retention |
| Missed messages             | Replayable from any ID          |
| Consumer groups             | Yes (load-balanced delivery)    |
| Latency overhead vs Pub/Sub | ~1–2ms[^14]                     |

Redis Streams maps cleanly onto the consult-resume pattern:

1. Worker A writes a `ClarificationRequest` to `stream:agent_messages` via `XADD`
2. Worker B reads it via `XREAD` (blocking or polling), processes, and writes `ClarificationResponse` back
3. Both ACK their consumed messages; the stream retains the full dialogue for audit

**Strengths for A2A:**

- Sub-millisecond to low-millisecond latency — appropriate for tight feedback loops
- Durable delivery: a peer agent restart does not lose the consultation request
- Consumer groups allow multiple peers to compete for incoming requests (useful when consulting a "pool" of domain experts)
- Native support for backpressure and per-stream ordering

**Operational cost:** Redis requires a running daemon (containerized or local). In a pure CLI local environment (Miru's architecture at port 18765), adding Redis adds operational surface area — a service that can crash, consume memory, and requires monitoring. For local single-machine setups, Redis is often over-engineered unless the system already relies on it for other purposes.[^15]

**Critical nuance:** Vanilla Redis Pub/Sub should not be used for agent consultation. **Redis Streams should be used instead** when Pub/Sub is part of the architecture. Many production systems correctly use both: Pub/Sub for instant alive-or-dead notification signals, and Streams as the actual durable message store.[^12]

---

### SQLite WAL Mode with `agent_messages` State Table

SQLite in WAL (Write-Ahead Logging) mode has emerged as the most widely used A2A channel in local multi-agent CLI systems, precisely because it eliminates external service dependencies while providing stronger consistency guarantees than raw files.[^16][^17][^18]

#### How WAL Mode Works for Concurrency

In WAL mode, all write operations are appended to a separate WAL file rather than overwriting the main database. This gives three key benefits:[^17][^19]

- **Readers never block writers** (and vice versa): reads observe the last committed WAL frame; writes append to the end
- **Sequential writes are faster** than the random seeks of rollback mode
- **Fewer fsync calls**: durability at checkpoints rather than per-commit

The lock hierarchy is: `SQLITE_LOCK_SHARED` (all open connections), `WAL_WRITE_LOCK` (exclusive, one active writer at a time), and `CHECKPOINTER` lock. A second writer waits until the first commits or rolls back — no concurrent writes, but high-concurrency reads are fully supported.[^20][^17]

#### Recommended `agent_messages` Schema

```sql
CREATE TABLE agent_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    from_agent  TEXT NOT NULL,
    to_agent    TEXT NOT NULL,       -- NULL = broadcast
    msg_type    TEXT NOT NULL,       -- 'clarification_request' | 'clarification_response' | 'handoff'
    payload     TEXT NOT NULL,       -- JSON blob: question, context snapshot, spec fragment
    status      TEXT NOT NULL DEFAULT 'pending',  -- 'pending' | 'ack' | 'resolved'
    created_at  INTEGER NOT NULL,    -- Unix timestamp
    ttl_seconds INTEGER,             -- NULL = no expiry; set to prevent deadlocks
    checkpoint  TEXT                 -- JSON: Worker A's state snapshot for resume
);

CREATE INDEX idx_messages_to_pending ON agent_messages (to_agent, status, created_at)
    WHERE status = 'pending';
CREATE INDEX idx_messages_task ON agent_messages (task_id);
```

Worker A suspends by `INSERT`ing a `clarification_request` row and writing its execution checkpoint to `checkpoint`. Peer Agent B polls `SELECT ... WHERE to_agent = ? AND status = 'pending'`, processes, updates `status = 'ack'`, and inserts a `clarification_response`. Worker A's polling loop detects the response, loads the checkpoint, and resumes.

#### Critical WAL PRAGMA Configuration

Production deployments require more than just setting WAL mode:[^17]

```sql
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;    -- Retry on contention, don't fail immediately
PRAGMA synchronous = NORMAL;   -- fsync at checkpoint, not per-commit (still crash-safe)
PRAGMA cache_size = -32000;    -- 32MB page cache
PRAGMA mmap_size = 134217728;  -- 128MB memory-mapped I/O
PRAGMA foreign_keys = ON;
```

Without `busy_timeout`, concurrent write contention returns `SQLITE_BUSY` immediately, which naive implementations treat as a fatal error rather than a retry signal.[^17]

#### Production Pitfalls

- **Checkpoint starvation**: long-running read transactions prevent the WAL file from being checkpointed back to the main database, causing unbounded WAL growth. Schedule periodic `PRAGMA wal_checkpoint(RESTART)` explicitly; do not rely on the passive auto-checkpoint for long-running agent processes.[^17]
- **One writer constraint**: SQLite WAL allows only one concurrent writer. For N agents all trying to write responses simultaneously, write contention under high agent counts can degrade throughput. Workaround: per-agent database files (one DB per agent), with a shared read-only message bus DB.[^17]
- **Snapshot isolation for visibility**: Connection 2 only sees Connection 1's committed writes if Connection 2 starts its read transaction _after_ Connection 1's commit completes and the WAL-index has been durably updated. This means short-polling intervals (100–500ms) with fresh transactions per poll are mandatory — do not reuse a single open read transaction across poll cycles.[^19]
- **No push notifications**: unlike Redis, SQLite has no built-in subscribe/notify. Workers must poll. For sub-second consultation latency, 100–200ms polling intervals are acceptable on local hardware; this is not a fit for latency-sensitive real-time systems.

**The A2A-in-SQLite pattern is validated by production open-source systems**: the Agentic TMUX MCP project uses "SQLite by default — no external services needed" for its `send_to_agent()` / `receive_message()` MCP tools. The opencode GitHub issue proposing DB-backed agent team coordination directly contrasts file-based JSON coordination against SQLite WAL, concluding that DB transactions give atomic team creation, Bus events work naturally via existing patterns, and WAL handles concurrent agent reads without file locking.[^18][^16]

---

## Head-to-Head Comparison

| Dimension                    | File-Based Async                         | Redis Pub/Sub                                  | Redis Streams                                   | SQLite WAL `agent_messages`                                     |
| ---------------------------- | ---------------------------------------- | ---------------------------------------------- | ----------------------------------------------- | --------------------------------------------------------------- |
| **Infrastructure**           | None                                     | Redis daemon                                   | Redis daemon                                    | None (embedded)                                                 |
| **Delivery guarantee**       | None (last-write-wins)                   | At-most-once                                   | At-least-once + ACK                             | At-least-once (polled)                                          |
| **Durability**               | Filesystem (survives restart)            | None (in-memory only)                          | Configurable (durable)                          | Durable (WAL file)                                              |
| **Message persistence**      | Until deleted                            | None                                           | Until trimmed                                   | Until `DELETE`                                                  |
| **Concurrent writer safety** | Requires explicit `flock`                | N/A                                            | N/A                                             | Single writer per WAL lock (serialized)                         |
| **Concurrent reader safety** | Risky without locking                    | N/A                                            | Consumer groups                                 | Fully supported (WAL)                                           |
| **Notification mechanism**   | Polling / inotify                        | Push (channel subscribe)                       | Blocking `XREAD`                                | Polling only                                                    |
| **Latency**                  | Filesystem (~1–5ms)                      | <1ms                                           | ~1–2ms overhead vs Pub/Sub                      | ~1–10ms (disk-based)                                            |
| **Ordering guarantee**       | None (mtime unreliable)                  | Per-channel                                    | Global by stream ID                             | AUTOINCREMENT row ID                                            |
| **Audit/query capability**   | `grep` / `jq` only                       | None                                           | Limited                                         | Full SQL (JOINs, CTEs, FTS5)                                    |
| **Operational overhead**     | Minimal                                  | Medium (Redis management)                      | Medium (Redis + stream trimming)                | Minimal (single file)                                           |
| **Best for**                 | Sequential pipeline handoffs; prototypes | Real-time alive signals (combine with Streams) | High-throughput multi-agent with external Redis | Local CLI multi-agent; embedded systems; zero-infra deployments |
| **Avoid when**               | Parallel agents write to shared files    | Consultation durability is required            | No Redis in stack; local-only requirement       | >10 agents with high-frequency concurrent writes                |

---

## Recommended Architecture for Local CLI Systems (Miru Context)

For a local multi-agent dispatcher architecture (CLI, no external services, multiple worker agents at ports like 18765/18080/19000), the **SQLite WAL `agent_messages` pattern is the production-grade choice**. It requires no additional infrastructure, provides full SQL querying for observability, supports atomic transactions for safe concurrent access, and integrates cleanly with an existing SQLite-backed state model.[^16][^18][^17]

The minimal implementation path:

1. **Add WAL `agent_messages` table** with `task_id`, `from_agent`, `to_agent`, `msg_type`, `status`, `payload` (JSON), `checkpoint` (JSON), and `ttl_seconds` columns
2. **Worker agent FSM**: detect ambiguity → `INSERT` clarification_request + checkpoint → transition to `SUSPENDED`
3. **Peer agent poll loop**: `SELECT ... WHERE to_agent = ? AND status = 'pending'` every 200ms in a background thread → process → `UPDATE status = 'ack'` + `INSERT` response
4. **Worker resume loop**: `SELECT ... WHERE task_id = ? AND msg_type = 'clarification_response' AND status = 'ack'` → load checkpoint → resume
5. **TTL watchdog**: a background job `DELETE FROM agent_messages WHERE created_at + ttl_seconds < now()` prevents stale lock-equivalent states
6. **WAL PRAGMA config**: set `busy_timeout = 5000` and `synchronous = NORMAL` to handle write contention gracefully

Upgrade to Redis Streams only when the system needs to scale beyond a single machine, requires sub-100ms consultation latency, or when Redis is already part of the stack for another purpose (e.g., caching at the PM storefront port).

---

## Emerging Standards: Google A2A Protocol

The Google Agent2Agent (A2A) Protocol, released April 9, 2025, and now governed by the Linux Foundation with 150+ organizational supporters, formalizes these patterns at an interoperability layer. Its explicit task lifecycle states (`submitted`, `working`, `input-required`, `completed`, `failed`) map directly onto the suspend-consult-resume FSM. The `input-required` state is the standardized signal for "worker needs peer consultation." A2A supports HTTP/2, gRPC, and WebSockets as transports, and a webhook registration mechanism (`tasks/pushNotification/set`) for async callbacks when clients cannot maintain persistent connections.[^21][^2][^7]

For purely local CLI systems, implementing the full A2A wire protocol is over-engineering. However, designing the `agent_messages` table schema and message envelope to be A2A-compatible (task_id → A2A Task.id, msg_type → A2A Message.role/parts, status → A2A TaskState enum) enables a clean upgrade path to cross-agent, cross-framework interoperability without a redesign.

---

## Conclusion

The canonical production pattern for mid-task peer consultation in multi-agent CLI systems is **async suspend-consult-resume** over a durable shared channel — never a synchronous blocking call, never a human relay. Among the three transport options:

- **File-based async** is viable for sequential pipelines and prototypes but breaks under parallel write pressure without careful locking discipline
- **Redis Pub/Sub** is unsuitable as a consultation channel due to zero message durability; **Redis Streams** is the right Redis primitive but adds operational overhead
- **SQLite WAL `agent_messages`** is the correct default for local, embedded, zero-infra multi-agent systems — it provides durability, SQL queryability, atomic writes, and direct compatibility with existing DB-backed state models

The message envelope must carry correlation IDs, structured payloads (not prose questions), execution checkpoints, and TTLs. Without TTLs and a deadlock watchdog, a suspended worker waiting for a peer response that was lost becomes an invisible hung process — the hardest failure mode to debug in production agent systems.

---

## References

1. [Feature: Inter-Agent Communication Channels for Direct ... - GitHub](https://github.com/openai/codex/issues/12462) - This enables agents to coordinate in real time without requiring the user to act as an intermediary....

2. [Google's A2A Protocol: How AI Agents Communicate Across ...](https://dev.to/agentsindex/googles-a2a-protocol-how-ai-agents-communicate-across-frameworks-52jj) - Google's Agent2Agent (A2A) Protocol launched April 2025 with 50+ founding partners and grew to 150+ ...

3. [State Machines - SW4RM Agentic Protocol](https://sw4rm.ai/architecture/state-machines/) - Open agentic protocol with SDKs for Python, Rust, JavaScript, and Common Lisp

4. [Parallel Coding Agents Only Work When the Handoffs Live in Files](https://dev.to/hefty_69a4c2d631c9dd70724/parallel-coding-agents-only-work-when-the-handoffs-live-in-files-5gk1) - That only works if the communication lane has rules. Who can send the message? Which sessions accept...

5. [Agent-to-Agent Communication Platform 2025: Enterprise Multi ...](https://sparkco.ai/blog/agent-to-agent-communication-how-ai-agents-talk-to-each-other-in-2026) - In 2026, our agent-to-agent communication platform powers deterministic message routing across multi...

6. [Build a Multi-Agent System & Master A2A Communication From Scratch | AI Agents Development](https://www.youtube.com/watch?v=1TLUg0al4hA) - Multi-agent systems are quickly becoming the standard architecture for production AI applications. T...

7. [Google and Anthropic Jointly Propose A2A Protocol: The HTTP of AI ...](https://callsphere.ai/blog/google-anthropic-a2a-protocol-http-of-ai-agents) - A new Agent-to-Agent (A2A) communication protocol aims to create interoperability standards for AI a...

8. [Auto memory file is not safe for concurrent agent teams #24130](https://github.com/anthropics/claude-code/issues/24130) - Concurrent memory writes should be safe — either via file-level locking, append-only writes, or a me...

9. [AI Agent Concurrent Editing: Setup Guide | Fastio](https://fast.io/resources/ai-agent-concurrent-editing/) - How to implement AI agent concurrent editing for simultaneous multi-agent file edits. File locking s...

10. [Stop confusing Redis Pub/Sub with Streams - Reddit](https://www.reddit.com/r/softwarearchitecture/comments/1nw3e1h/stop_confusing_redis_pubsub_with_streams/) - Streams act more like a durable event log . Messages are stored, can be replayed later, and multiple...

11. [How Redis Pub/Sub works and its trade-offs | Evan King posted on ...](https://www.linkedin.com/posts/evan-king-40072280_pubsub-is-a-messaging-pattern-that-shows-activity-7382067350960967681-wGP-) - Redis Pub/Sub prioritizes throughput and simplicity. There is no persistence, message history, or de...

12. [When to Use Redis Pub/Sub vs Redis Streams - OneUptime](https://oneuptime.com/blog/post/2026-03-31-redis-when-to-use-redis-pubsub-vs-redis-streams/view) - Use Redis Streams when you need durable message delivery, consumer groups for load balancing, messag...

13. [Redis Pub/Sub vs Redis Streams: A Dev-Friendly Comparison](https://dev.to/lovestaco/redis-pubsub-vs-redis-streams-a-dev-friendly-comparison-39hm) - The answer depends on your use case. For real-time notifications, go with Pub/Sub. For persistence a...

14. [Redis Streams vs Pub/Sub: A Performance Perspective - LinkedIn](https://www.linkedin.com/pulse/redis-streams-vs-pubsub-performance-perspective-ykr9c) - While Redis Streams introduces a slight latency overhead compared to Pub/Sub (typically in the 1–2 m...

15. [Redis vs SQLite for Solo Developers (2026) | SoloDevStack](https://solodevstack.com/blog/redis-vs-sqlite-solo-developers) - Performance profile. Redis is faster for single-key operations because data is in memory. Sub-millis...

16. [AI Agents Unleashed: Direct Dialogue Between Agents](https://dev.to/negaga53/ai-agents-unleashed-direct-dialogue-between-agents-3d7d) - This is a submission for the GitHub Copilot CLI Challenge What I Built Agentic TMUX MCP —...

17. [SQLite WAL Mode: Patterns and Pitfalls for AI Agent Systems - Zylos](https://zylos.ai/research/2026-02-20-sqlite-wal-mode-ai-agent-systems) - This article dissects WAL internals, catalogs the pitfalls we've encountered running SQLite as a mes...

18. [[FEATURE]: DB-backed agent team coordination (parallel ... - GitHub](https://github.com/anomalyco/opencode/issues/19215) - SQLite WAL mode handles concurrent reads well but you still need to be careful with writes. the idle...

19. [WAL Mode - SQLite Help Docs](https://sqlite.work/wal-mode/) - SQLite’s Write-Ahead Logging (WAL) mode fundamentally alters how database modifications are handled ...

20. [Using WAL mode with multiple processes - SQLite User Forum](https://sqlite.org/forum/forumpost/c4dbf6ca17) - WAL journal mode supports one writer and many readers at the same time. A second writer will have to...

21. [Understanding A2A — The protocol for agent collaboration](https://discuss.google.dev/t/understanding-a2a-the-protocol-for-agent-collaboration/189103) - The world of AI is undergoing a transformation — one where specialized agents, each crafted for narr...
