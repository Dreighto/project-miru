"""Phase 3 — Subagent Isolation: tool profile definitions.

Each profile maps to a frozenset of allowed gateway categories (deny-all
default).  Unknown profile strings fall back to ``drift_executor`` restrictions
(most conservative).  Absent header (operator's direct session) defaults to
``full_operator`` — backward-compatible.
"""

from __future__ import annotations

_READ_SURFACE: frozenset[str] = frozenset(
    {
        "filesystem_read",
        "system_logs",
        "github_read",
        "n8n_read",
        "audit_read",
        "worker_read",
        "memory_write",
        "perplexity",
        "aggregator",
    }
)

_PROFILE_ALLOWLISTS: dict[str, frozenset[str] | None] = {
    "drift_executor": _READ_SURFACE,
    "reviewer": _READ_SURFACE,
    "standard_worker": _READ_SURFACE
    | frozenset({"linear_write", "n8n_write", "docs_write", "git_write"}),
    "vp_ops": _READ_SURFACE | frozenset({"linear_write", "docs_write", "git_write", "vp_ops"}),
    "full_operator": None,
}

_DEFAULT_ALLOWLIST: frozenset[str] = _PROFILE_ALLOWLISTS["drift_executor"]


def is_allowed(profile: str, category: str) -> bool:
    allowlist = _PROFILE_ALLOWLISTS.get(profile)
    if allowlist is None and profile in _PROFILE_ALLOWLISTS:
        return True
    effective = allowlist if allowlist is not None else _DEFAULT_ALLOWLIST
    return category in effective


def known_profile(name: str) -> bool:
    return name in _PROFILE_ALLOWLISTS
