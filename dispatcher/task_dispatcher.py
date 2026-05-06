"""
Miru Task Dispatcher — DECOMMISSIONED (PRO-234, 2026-04-30).

This module previously hosted a Flask + WebSocket service on port 19000
with a job queue, SQLite history, UI dashboard, Slack-bolt approval
bridge, file browser, and runtime control endpoints. PRO-300 (PR #93)
removed the Cursor + Codex handlers. PRO-301 (PR #94) stripped the UI
surface and the Slack-bolt subsystem. PRO-302 (this PR) extracts the
new Local Governance Gatekeeper (``dispatcher.gatekeeper``) and retires
the Frankenstein job-queue + SQLite + Anthropic-title-gen code paths
that lived here.

The new dispatch flow:

    Captain ↔ Claude Chat (interface, design, ticket writing)
                │
                ▼  via cc_handoff MCP tool (planned, Phase 2)
    dispatcher.gatekeeper  (validation core)
                │
                ▼  HMAC-signed POST via dispatcher.forwarder
    services/dispatch_listener (port 19100)  → spawns workers
                │
                ▼
    Workers: claude-code, gemini CLI

If you arrived here looking for the old job-queue API, see
``data/peer_reviews/2026-05-05_dispatcher_audit_codex.md`` for the
audit log of what was here and why it was removed.

Full deletion of this file is deferred to a follow-up cleanup ticket
(see PRO-303). Keeping the module as a deprecation stub prevents
future confusion if anything still imports it.
"""

from __future__ import annotations

# This module intentionally exposes nothing. Importing it is a no-op.
# Use the new modules under ``dispatcher`` instead:
#
#   from dispatcher.gatekeeper import gate_dispatch
#   from dispatcher.frontmatter_parser import parse, FrontmatterError
#   from dispatcher.forwarder import forward, mint_trace_id
#
# The legacy SQLite job history previously stored at
# ``dispatcher/data/jobs.db`` is archived as ``jobs.db.legacy`` in the
# same directory. See ``dispatcher/data/README.md`` for schema notes
# and access guidance. The new Gatekeeper has no SQLite dependency.

__all__: list[str] = []
