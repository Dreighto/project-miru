"""Perplexity AI search tools (PRO-225).

Gated by PERPLEXITY_API_KEY presence. Wraps the Perplexity Chat Completions
API (api.perplexity.ai) using the sonar model for web-grounded answers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_TOOLS_DIR = Path(__file__).resolve().parent.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402

from miru_mcp_gateway import redact as _redact  # noqa: E402

try:
    import requests  # type: ignore
except ImportError:
    requests = None  # type: ignore

_API_BASE = "https://api.perplexity.ai"
_HTTP_TIMEOUT_S = 30
_DEFAULT_MODEL = "sonar"
_MAX_TOKENS_CAP = 4096

_API_KEY: str | None = None


def perplexity_search(query: str, max_tokens: int = 1024, ctx: Any = None) -> str:
    """Web-grounded search via Perplexity AI (sonar model).

    Returns a JSON object with ``answer``, ``citations``, and ``model``.
    ``max_tokens`` is capped at 4096.
    """
    if requests is None:
        raise stdio_mcp.McpError("perplexity: 'requests' library not installed", -32000)
    if not _API_KEY:
        raise stdio_mcp.McpError("perplexity: PERPLEXITY_API_KEY not configured", -32000)
    q = (query or "").strip()
    if not q:
        raise stdio_mcp.McpError("perplexity: query must not be empty", -32602)
    tok = max(64, min(int(max_tokens), _MAX_TOKENS_CAP))

    payload = {
        "model": _DEFAULT_MODEL,
        "messages": [{"role": "user", "content": q}],
        "max_tokens": tok,
    }
    headers = {
        "Authorization": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        resp = requests.post(
            f"{_API_BASE}/chat/completions",
            json=payload,
            headers=headers,
            timeout=_HTTP_TIMEOUT_S,
        )
    except requests.exceptions.Timeout as exc:
        raise stdio_mcp.McpError(f"perplexity: timeout after {_HTTP_TIMEOUT_S}s", -32000) from exc
    except requests.exceptions.RequestException as exc:
        raise stdio_mcp.McpError(
            f"perplexity: transport error: {_redact.redact(str(exc))}", -32000
        ) from exc

    if resp.status_code == 401:
        raise stdio_mcp.McpError(
            "perplexity: 401 Unauthorized -- PERPLEXITY_API_KEY may be invalid", -32000
        )
    if not (200 <= resp.status_code < 300):
        body = _redact.redact(resp.text[:400])
        raise stdio_mcp.McpError(f"perplexity: HTTP {resp.status_code}: {body}", -32000)

    try:
        data = resp.json()
    except ValueError as exc:
        raise stdio_mcp.McpError("perplexity: non-JSON response", -32000) from exc

    choices = data.get("choices") or []
    answer = ""
    if choices:
        answer = (choices[0].get("message") or {}).get("content", "")

    citations = data.get("citations") or []
    model_used = data.get("model", _DEFAULT_MODEL)

    out = {
        "answer": _redact.redact(answer),
        "citations": citations,
        "model": model_used,
    }
    return json.dumps(out, indent=2)


TOOL_FUNCTIONS = (perplexity_search,)


def register(mcp, cfg) -> int:
    """Register perplexity_* tools iff PERPLEXITY_API_KEY is present."""
    global _API_KEY

    if not getattr(cfg, "perplexity_enabled", False) or not getattr(
        cfg, "perplexity_api_key", None
    ):
        cfg.disabled_categories["perplexity"] = "PERPLEXITY_API_KEY missing"
        return 0
    if requests is None:
        cfg.disabled_categories["perplexity"] = "'requests' library not installed"
        return 0

    _API_KEY = cfg.perplexity_api_key

    from miru_mcp_gateway.gateway_security import wrap_tool_entry

    for func in TOOL_FUNCTIONS:
        mcp.tool(wrap_tool_entry(func, cfg))
    return len(TOOL_FUNCTIONS)
