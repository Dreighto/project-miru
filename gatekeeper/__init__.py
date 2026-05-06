"""Local Governance Gatekeeper — dispatch validation package.

Validates dispatch proposals before they reach the dispatch_listener
(port 19100).  Sits between Claude Chat and the listener, running a
deterministic floor (no LLM call) followed by cross-context LLM
validation via a local Ollama model.

Public surface::

    from gatekeeper.core import gate_dispatch, GatekeeperError
    from gatekeeper.core import GOVERNANCE_PREAMBLE, ROUTING_JSON_SCHEMA
    from gatekeeper.frontmatter_parser import parse, FrontmatterError
    from gatekeeper.forwarder import forward, mint_trace_id

Architecture & ground truth
---------------------------

- Locked design: Notion 358c5d34-0141-817c-8dda-e2f91a50a9c5
- Frontmatter schema: docs/dispatch/ticket_frontmatter_schema.md
- Output schema: tools/gatekeeper/routing_schema.gbnf
- Bench harness: tools/gatekeeper/bench.py
- Audit log: data/agent_decisions.jsonl (judgment_driven entries)

History: originally extracted into dispatcher/ in PRO-302 (PR #95).
Relocated to this top-level package in PRO-306.
"""

from gatekeeper.core import (  # noqa: F401
    GOVERNANCE_PREAMBLE,
    ROUTING_JSON_SCHEMA,
    GatekeeperError,
    gate_dispatch,
)
from gatekeeper.forwarder import (  # noqa: F401
    ForwarderError,
    forward,
    mint_trace_id,
)
from gatekeeper.frontmatter_parser import (  # noqa: F401
    FrontmatterError,
    parse,
)
