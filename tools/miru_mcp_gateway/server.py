"""Miru MCP Gateway -- FastMCP server exposing read-only tools to Claude.ai
(web) over Streamable HTTP via Tailscale Funnel.

Stage 1: filesystem tools (fs_*).
Stage 2: + system status (system_*), GitHub read-only (github_*), n8n
         read-only (n8n_*). Each Stage 2 category gates itself on env
         presence and disables cleanly if its env vars are missing.

Run as a script:
    python D:\\dev\\miru\\tools\\miru_mcp_gateway\\server.py

Required environment (loaded by start_mcp_gateway.ps1 from .env):
    MIRU_MCP_URL_SECRET    64-hex secret used by the Tailscale Funnel path mount
    MIRU_FS_ALLOW_ROOT     defaults to D:\\dev\\miru
    MIRU_MCP_GATEWAY_PORT  defaults to 18766
    MIRU_MCP_GATEWAY_HOST  defaults to 127.0.0.1 (loopback only)

Optional Stage 2 environment:
    GITHUB_TOKEN_READ              read-only PAT (enables github_* tools)
    MIRU_GITHUB_REPO_ALLOWLIST     comma-separated owner/repo or owner/* patterns
    N8N_API_KEY                    n8n REST key (enables n8n_* tools)
    MIRU_N8N_BASE_URL              defaults to http://localhost:15678

Path-stripped mode:
    Tailscale Funnel at /mcp/<SECRET> strips the prefix and forwards to the
    gateway. Internally the gateway serves only:
        /mcp      Streamable HTTP MCP endpoint
        /health   JSON health probe
    The secret is enforced at the Tailscale edge; the gateway binds loopback
    (127.0.0.1) so neither path is reachable from the tailnet, the LAN, or the
    internet. Losing the loopback invariant would lose the only auth boundary.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

# Make the package importable when run directly as a script.
_PKG_DIR = Path(__file__).resolve().parent
_TOOLS_DIR = _PKG_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from miru_mcp_gateway import config as gw_config
from miru_mcp_gateway import (
    docs_write_tools,
    fs_tools,
    github_tools,
    n8n_tools,
    n8n_write_tools,
    system_tools,
)
from miru_mcp_gateway import redact as gw_redact

SERVER_NAME = "miru-fs-gateway"
SERVER_VERSION = "0.3.0"

# Order matters: filesystem first (Stage 1, always on), then system status,
# then external categories. Each module owns its own enable check via
# register(mcp, cfg) -> int.
CATEGORIES: tuple[tuple[str, Any], ...] = (
    ("filesystem", fs_tools),
    ("system", system_tools),
    ("github", github_tools),
    ("n8n", n8n_tools),
    ("n8n_write", n8n_write_tools),
    ("docs_write", docs_write_tools),
)


def _build_server(cfg: gw_config.GatewayConfig):
    """Construct a FastMCP server with all fs_* tools registered.

    Imported lazily so that `python server.py --help`-style invocations don't
    error out before config validation has run.
    """
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit(
            "FATAL: fastmcp is not installed. Install with:\n"
            '    pip install --user "fastmcp>=2.5,<3"\n'
            f"Original error: {exc}"
        ) from exc

    mcp = FastMCP(SERVER_NAME)

    counts: dict[str, int] = {}
    for name, mod in CATEGORIES:
        try:
            counts[name] = mod.register(mcp, cfg)
        except Exception as exc:
            cfg.disabled_categories[name] = f"register() raised: {exc!r}"
            counts[name] = 0

    _register_health_route(mcp, cfg)

    return mcp, counts


class _RootAlias:
    """Tiny ASGI wrapper that aliases bare-root paths onto the MCP endpoint.

    Some MCP clients (Claude.ai's custom-connector UI in particular) probe the
    URL the operator typed without appending the FastMCP `/mcp` suffix. Without
    this alias, a connector URL of `.../mcp/<SECRET>` (no trailing `/mcp`)
    would 404 at the gateway router because Tailscale strips its prefix and
    the gateway only sees `/`.

    Behavior:
      * `/` and `''` are rewritten to `/mcp` so the same FastMCP handler runs.
      * Every other path (including `/health`, `/mcp`, `/mcp/`) is passed through.
      * Lifespan / websocket scopes are passed through untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path", "")
            if path in ("", "/"):
                scope = {**scope, "path": "/mcp", "raw_path": b"/mcp"}
        await self.app(scope, receive, send)


def _print_category_summary(cfg: gw_config.GatewayConfig, counts: dict[str, int]) -> None:
    """Banner footer: one line per category, total at the end."""
    total = 0
    for name, _mod in CATEGORIES:
        c = counts.get(name, 0)
        if c > 0:
            extra = ""
            if name == "github" and cfg.github_allowlist:
                extra = f"  (allowlist: {', '.join(cfg.github_allowlist)})"
            elif name == "github":
                extra = "  (allowlist: any repo the token can see)"
            elif name == "n8n":
                extra = f"  (base: {cfg.n8n_base_url})"
            elif name == "n8n_write":
                if cfg.n8n_write_workflow_allowlist:
                    extra = f"  (allowlist: {', '.join(cfg.n8n_write_workflow_allowlist)})"
                else:
                    extra = "  (allowlist: any workflow id)"
                if cfg.n8n_write_approval_notify_url:
                    extra += "  (approval notify URL set)"
            elif name == "docs_write":
                extra = f"  ({len(cfg.docs_write_path_allowlist)} path glob(s))"
            print(f"  {name:<11} : {c} tools{extra}", flush=True)
            total += c
        else:
            reason = cfg.disabled_categories.get(name, "disabled")
            print(f"  {name:<11} : DISABLED -- {reason}", flush=True)
    print(f"  {'total':<11} : {total} tools", flush=True)


def _register_health_route(mcp, cfg: gw_config.GatewayConfig) -> None:
    """Attach a GET /health route to the underlying Starlette app.

    Path-stripped mode: Tailscale's path mount drops /mcp/<SECRET> before
    forwarding, so the internal route must be bare /health. The Funnel layer
    is the secret check; 127.0.0.1 binding is what keeps this route private.
    """
    try:
        from starlette.responses import JSONResponse
    except ImportError as exc:
        raise SystemExit(
            "FATAL: starlette is required (installed as a fastmcp dependency). "
            f"Original error: {exc}"
        ) from exc

    payload = {
        "ok": True,
        "version": SERVER_VERSION,
        "name": SERVER_NAME,
    }

    async def health(_request):
        return JSONResponse(payload)

    # Register at the secret-prefixed path. If the FastMCP API rename has
    # happened in a newer version, surface a clear error rather than silently
    # serving health at a different URL.
    if not hasattr(mcp, "custom_route"):
        raise SystemExit(
            "FATAL: this FastMCP version does not expose `custom_route` for "
            "attaching the /health endpoint. Pin to fastmcp>=2.5,<3."
        )

    mcp.custom_route(cfg.health_path, methods=["GET"])(health)


def main() -> int:
    cfg = gw_config.load()

    # Make sure fs_tools' resolver is rooted at the configured directory.
    # The stdio module reads MIRU_FS_ALLOW_ROOT at import time; if it differs
    # from the gateway's view, fail loudly.
    if fs_tools.stdio_mcp.ROOT.resolve() != cfg.fs_root.resolve():
        raise SystemExit(
            "FATAL: filesystem root mismatch.\n"
            f"  stdio MCP module ROOT = {fs_tools.stdio_mcp.ROOT}\n"
            f"  gateway config root   = {cfg.fs_root}\n"
            "Set MIRU_FS_ALLOW_ROOT before launching the gateway."
        )

    # Eagerly load redactor: snapshot env values now so any token rotation
    # done after startup requires a gateway restart (documented behavior).
    gw_redact.reload_substring_set()

    print(
        f"[{SERVER_NAME}] starting v{SERVER_VERSION}\n"
        f"  host         : {cfg.host}\n"
        f"  port         : {cfg.port}\n"
        f"  fs_root      : {cfg.fs_root}\n"
        f"  internal mcp : http://{cfg.host}:{cfg.port}{cfg.mcp_path}\n"
        f"  internal hlth: http://{cfg.host}:{cfg.port}{cfg.health_path}\n"
        f"  public via   : Tailscale Funnel + /mcp/<SECRET> (strips prefix)",
        flush=True,
    )

    mcp, counts = _build_server(cfg)
    _print_category_summary(cfg, counts)

    # FastMCP 2.x: build the Starlette app explicitly so we can wrap it in a
    # tiny ASGI middleware that aliases bare-root paths onto /mcp. This lets
    # Claude.ai's connector accept either `.../mcp/<SECRET>` (no suffix) or
    # `.../mcp/<SECRET>/mcp` (canonical) -- both end up running the same MCP
    # handler. Anything else (e.g. /random) still 404s through Starlette.
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "FATAL: uvicorn is required (installed as a fastmcp dependency). "
            f"Original error: {exc}"
        ) from exc

    inner_app = mcp.http_app(path=cfg.mcp_path, transport="streamable-http")
    wrapped_app = _RootAlias(inner_app)

    try:
        uvicorn.run(
            wrapped_app,
            host=cfg.host,
            port=cfg.port,
            log_level="info",
        )
    except KeyboardInterrupt:
        print(f"[{SERVER_NAME}] shutdown requested", flush=True)
        return 0
    except Exception:
        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
