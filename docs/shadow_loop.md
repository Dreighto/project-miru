# Shadow loop — OP01 evaluation learning system

**Status:** PR-A skeleton landed; PR-B (real verifier + scoring) and PR-C (verifier-of-verifier guards) in flight.

**Parent ticket:** PRO-908 — see Linear for the full design.

## What this is

A closed-local 2.5-stage learning loop that builds the intelligence layer for the
storefront. Two NEW Ollama models learn OP01 in parallel; a deterministic sanity
post-check guards hard fields. Output lands in `data/miru_learning_pool.db`
(PR-907 schema). Operator reviews escalations via the PRO-909 dev page.

This service is NOT autonomous and does NOT touch live storefront or Miru AI
traffic. It writes only to `miru_learning_pool.db`. Promotion from the pool
into `card_catalog.db` is a separate operator-gated mechanism (later ticket).

## Architecture (2.5-stage)

| Stage                  | Component                           | Role                                                                                                                                    |
| ---------------------- | ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1. Primary             | NEW Qwen 2.5 7B Ollama spawn        | Answers card questions from learned knowledge only. No tools. The "naive learner" that gets graded.                                     |
| 2. Validator           | NEW Mistral Small 3 7B Ollama spawn | Answers the same question independently with tool access (catalog + Bandai + TCGPlayer). Compares to primary, emits per-field outcomes. |
| 2.5. Sanity post-check | Small Python check                  | Re-verifies validator's hard-field claims against catalog directly. Catches validator hallucinating around its own tools.               |

**HARD constraint:** the routing Hermes (`qwen2.5:7b` at `dispatch_listener`
spawn) is UNTOUCHED. The shadow loop runs separate Ollama model instances
identified by their own client objects in `services/shadow_loop/ollama_client.py`.

## File layout

```
services/shadow_loop/
  __init__.py
  config.py              # env-var loading + defaults
  ollama_client.py       # primary + validator HTTP clients
  priority_queue.py      # in-memory FIFO (PR-C extends to true priority)
  question_template.py   # the structured question both models receive
  db_writer.py           # writes learned_cards rows
  dummy_verifier.py      # PR-A scaffold; PR-B replaces with real
  loop_runner.py         # main tick loop
  launch.py              # entry point

tests/services/shadow_loop/
  test_priority_queue.py
  test_dummy_verifier.py
  test_db_writer.py
  test_question_template.py
  test_loop_runner.py    # smoke test with fake clients
```

PR-C will add (per PRO-911):

```
services/shadow_loop/
  bootstrap_test.py
  sentinel.py
  override_metric.py
  stale_requeue.py
data/shadow_loop/
  bootstrap_fixtures.json
  sentinels.json
data/shadow_loop_verifier_overrides.jsonl  (append-only, written by PRO-909)
```

## Launching

### Prerequisites

- Ollama running on `http://127.0.0.1:11434`.
- Models pulled:
  ```bash
  ollama pull qwen2.5:7b          # already present per routing Hermes
  ollama pull mistral-small3:7b   # ~4-5 GB one-time pull
  ```
- `data/miru_learning_pool.db` exists (run `python tools/create_miru_learning_pool.py` if missing).

### Real mode

```bash
python -m services.shadow_loop.launch
```

Logs go to `data/shadow_loop.log` (rotating, 5 MB × 5 files) and stdout.

### Smoke mode (CI / no models pulled)

```bash
SHADOW_LOOP_MODE=smoke python -m services.shadow_loop.launch
```

Uses a canned `SmokeClient` that returns empty JSON for every question. The
loop still exercises the queue, DB writer, and dummy verifier — useful for
verifying the plumbing without depending on Ollama.

## Configuration

All knobs are env vars with documented defaults in `config.py`:

| Variable                        | Default                      | Purpose                        |
| ------------------------------- | ---------------------------- | ------------------------------ |
| `SHADOW_LOOP_OLLAMA_URL`        | `http://127.0.0.1:11434`     | Ollama HTTP endpoint           |
| `SHADOW_LOOP_PRIMARY_MODEL`     | `qwen2.5:7b`                 | Primary learner model          |
| `SHADOW_LOOP_VALIDATOR_MODEL`   | `mistral-small3:7b`          | Validator model (used in PR-B) |
| `SHADOW_LOOP_REQUEST_TIMEOUT_S` | `180`                        | Per-model request timeout      |
| `SHADOW_LOOP_TICK_SECONDS`      | `60`                         | Sleep between ticks            |
| `SHADOW_LOOP_MODE`              | `real`                       | Set `smoke` to bypass Ollama   |
| `SHADOW_LOOP_CATALOG_DB`        | `data/card_catalog.db`       | Catalog read source            |
| `SHADOW_LOOP_POOL_DB`           | `data/miru_learning_pool.db` | Learning pool write target     |
| `SHADOW_LOOP_LOG_PATH`          | `data/shadow_loop.log`       | Rotating log file              |
| `SHADOW_LOOP_SET_SCOPE`         | `OP01-`                      | Card-code prefix to learn      |

## Tick flow (PR-A)

1. If queue is empty → seed from `card_catalog` (every card under `SHADOW_LOOP_SET_SCOPE`).
2. Pop one `(canonical_code, print_id)` from the queue.
3. Fetch the catalog row for context.
4. Send the structured question (see `question_template.py`) to the primary.
5. Score the primary's answer through the verifier (dummy in PR-A; real in PR-B).
6. Write a row to `learned_cards` with the primary's claims, verifier outcomes, and metadata.
7. Sleep `SHADOW_LOOP_TICK_SECONDS`.

PR-C hooks (sentinel checks, override-rate halt, stale-row re-queue) are
imported via try/except in `loop_runner.py` so PR-A runs cleanly without
PR-C present.

## Restart procedure

(To be added to `.logueos/reference/restart-procedures.md` in a follow-up
orchestrator PR.)

Manual:

```powershell
Get-NetTCPConnection -LocalPort 11434 -ErrorAction SilentlyContinue
# Ollama is shared — don't restart it from the shadow loop.
# To restart the shadow loop service:
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*services.shadow_loop.launch*" } | Stop-Process -Force
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-Command','cd D:\dev\miru; python -m services.shadow_loop.launch'
```

## What this is NOT

- Not autonomous. It writes only to `miru_learning_pool.db`; promotion into
  `card_catalog.db` is operator-gated via PRO-909.
- Not touching live storefront (18080) or Miru AI (18765) traffic.
- Not sharing state with the routing Hermes — separate Ollama model instances,
  separate clients, separate process.
