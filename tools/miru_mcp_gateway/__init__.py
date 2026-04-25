"""Miru MCP Gateway -- remote read-only MCP server for Claude.ai (web).

Stage 1: filesystem-only.
Stage 2: + system status, GitHub read-only, n8n read-only. Each Stage 2
category gates itself on env presence and disables cleanly if missing.

Exposed via Tailscale Funnel under a URL-secret prefix. See README.md for
setup, rotation, and rollback.
"""

__version__ = "0.2.0"
