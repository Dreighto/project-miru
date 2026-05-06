"""Local Governance Gatekeeper — Miru dispatch-validation core.

The ``dispatcher`` package has been REPURPOSED. The legacy port-19000
service it used to host is decommissioned (PRO-234, 2026-04-30). The
package now hosts the Local Governance Gatekeeper: a thin validation
layer in front of the existing ``dispatch_listener`` (port 19100).
The Gatekeeper closes Claude Chat's self-serve loophole at the
structural level by gating all conversational dispatches through
cross-context validation.

Distinction:
- The legacy ``dispatcher.task_dispatcher`` MODULE is a deprecation stub
  with no functional code (full deletion deferred to PRO-303).
- The ``dispatcher`` PACKAGE is the canonical home for the Gatekeeper
  modules listed below. New code SHOULD import from this package.

Public surface
--------------

  from dispatcher.gatekeeper import gate_dispatch
      Top-level entry point. Takes a cc_handoff payload, runs the
      deterministic floor + LLM cross-context validation, and returns
      either a routing decision (forwarded to the listener) or a
      Phase 2.5 Rejection (kicked back to CH).

  from dispatcher.frontmatter_parser import parse, FrontmatterError
      Extracts the HTML-comment YAML dispatch annotation from a Linear
      ticket description. Validates against the closed-enum schema in
      docs/dispatch/ticket_frontmatter_schema.md.

  from dispatcher.forwarder import forward, mint_trace_id
      HMAC-signed POST to the dispatch_listener on port 19100. Maps
      listener response codes to Gatekeeper rejection vocabulary.

Architecture & ground truth
---------------------------

- Locked design: Notion ``358c5d34-0141-817c-8dda-e2f91a50a9c5``
  ("Dispatcher (Resurrected) — Local Router Architecture")
- Frontmatter schema: ``docs/dispatch/ticket_frontmatter_schema.md``
- Output schema (canonical): ``tools/gatekeeper/routing_schema.gbnf``
  (also mirrored as a JSON Schema in
  ``dispatcher.gatekeeper.ROUTING_JSON_SCHEMA`` for the Ollama
  ``format`` field — this Ollama build silently ignores
  ``options.grammar``; smoke test 2026-05-06 confirmed)
- Bench harness: ``tools/gatekeeper/bench.py``
- Audit log: every Gatekeeper accept / reject / enrich is appended to
  ``data/agent_decisions.jsonl`` as a ``judgment_driven`` entry
  (per GMI 2026-05-05; auditable shadow-mode bench)

Legacy / decommissioned
-----------------------

The legacy job-queue + UI dashboard + Slack-bolt approval bridge that
ran on port 19000 (PRO-234) is removed. ``dispatcher/task_dispatcher.py``
is now a deprecation stub. Old job history is archived at
``dispatcher/data/jobs.db.legacy``. Full deletion of these legacy
artifacts is deferred to a follow-up cleanup ticket (PRO-303).
"""
