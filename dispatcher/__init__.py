"""Dispatcher package — DECOMMISSIONED (PRO-234, 2026-04-30).

The legacy port-19000 service is removed. The Local Governance Gatekeeper
that briefly lived here (PRO-302, PR #95) has been relocated to the
top-level ``gatekeeper`` package (PRO-306).

New code should import from ``gatekeeper``, not ``dispatcher``::

    from gatekeeper.core import gate_dispatch, GatekeeperError
    from gatekeeper.frontmatter_parser import parse, FrontmatterError
    from gatekeeper.forwarder import forward, mint_trace_id

Legacy artifacts remaining in this package:

- ``task_dispatcher.py`` — deprecation stub (PRO-303 tracks full deletion)
- ``data/`` — archived job history (gitignored, read-only reference)
"""
