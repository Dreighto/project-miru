"""Runtime configuration for the Miru MCP Gateway.

Reads environment variables and fails fast if anything required is missing or
weak. There is no "accidentally open" code path: a missing or short URL secret
prevents the server from starting.

Path-stripped mode (Stage 1, current):
    The Tailscale Funnel mount at /mcp/<SECRET> strips the prefix before
    forwarding to 127.0.0.1:18766. The gateway therefore serves:
        /mcp      Streamable HTTP MCP endpoint
        /health   JSON health probe
    Neither path embeds the secret -- the Funnel layer IS the secret check.
    Loopback binding is what keeps these routes unreachable from anywhere
    except the local Tailscale process.

Stage 2 additions:
    Optional tool categories (github, n8n) gated by env presence. Each
    category disables itself cleanly if its env vars are missing -- the
    gateway still starts, only MIRU_MCP_URL_SECRET is fatal.

Secret validation remains in place because a missing MIRU_MCP_URL_SECRET
still signals an incomplete operator setup (e.g. Funnel mount registered
without the secret path). Fail closed.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18766
DEFAULT_ROOT = Path(r"D:\dev\miru")
MIN_SECRET_HEX_LEN = 32

# Internal (loopback) paths. Tailscale strips its mount prefix, so these are
# what the gateway's own router must match.
INTERNAL_MCP_PATH = "/mcp"
INTERNAL_HEALTH_PATH = "/health"

# Stage 2 defaults
DEFAULT_N8N_BASE_URL = "http://localhost:15678"


@dataclass
class GatewayConfig:
    host: str
    port: int
    url_secret: str
    fs_root: Path
    mcp_path: str
    health_path: str

    # Stage 2: per-category settings. None/empty means category will disable
    # itself when register() runs.
    github_token: str | None = None
    github_allowlist: tuple[str, ...] = ()
    n8n_api_key: str | None = None
    n8n_base_url: str = DEFAULT_N8N_BASE_URL

    # Populated by each category's register() if it refuses to start. The
    # banner reads this to print "github : DISABLED -- <reason>" lines.
    disabled_categories: dict[str, str] = field(default_factory=dict)

    @property
    def public_prefix(self) -> str:
        """The external URL prefix (set up on the Tailscale side, not served here)."""
        return f"/mcp/{self.url_secret}"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"FATAL: {name} is not set. The gateway refuses to start without it. "
            f"Add {name}=<value> to D:\\dev\\miru\\.env and restart."
        )
    return value


def _validate_secret(secret: str) -> str:
    cleaned = secret.strip()
    if len(cleaned) < MIN_SECRET_HEX_LEN:
        raise SystemExit(
            f"FATAL: MIRU_MCP_URL_SECRET is shorter than {MIN_SECRET_HEX_LEN} chars "
            f"(got {len(cleaned)}). Generate with: "
            f"python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    if any(c not in "0123456789abcdefABCDEF" for c in cleaned):
        raise SystemExit(
            "FATAL: MIRU_MCP_URL_SECRET must be hex characters only "
            "(0-9, a-f). Re-generate with secrets.token_hex(32)."
        )
    return cleaned.lower()


def _load_github_settings() -> tuple[str | None, tuple[str, ...]]:
    """Read GitHub token + allowlist. Read-scope only: refuse to use a
    write-scope token even if the operator set GITHUB_TOKEN_WRITE.

    Returns (token_or_None, allowlist_patterns).
    """
    read_token = os.environ.get("GITHUB_TOKEN_READ", "").strip() or None

    raw_allow = os.environ.get("MIRU_GITHUB_REPO_ALLOWLIST", "").strip()
    allowlist: tuple[str, ...] = ()
    if raw_allow:
        allowlist = tuple(
            piece.strip() for piece in raw_allow.split(",") if piece.strip()
        )

    return read_token, allowlist


def _load_n8n_settings() -> tuple[str | None, str]:
    api_key = os.environ.get("N8N_API_KEY", "").strip() or None
    base_url = (
        os.environ.get("MIRU_N8N_BASE_URL", "").strip() or DEFAULT_N8N_BASE_URL
    )
    # Strip trailing slash so we can `${base}/api/v1/...` cleanly.
    base_url = base_url.rstrip("/")
    return api_key, base_url


def load() -> GatewayConfig:
    secret = _validate_secret(_require_env("MIRU_MCP_URL_SECRET"))

    raw_root = os.environ.get("MIRU_FS_ALLOW_ROOT", str(DEFAULT_ROOT))
    fs_root = Path(raw_root).resolve()
    if not fs_root.exists():
        raise SystemExit(f"FATAL: MIRU_FS_ALLOW_ROOT does not exist: {fs_root}")

    raw_port = os.environ.get("MIRU_MCP_GATEWAY_PORT", str(DEFAULT_PORT))
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise SystemExit(f"FATAL: MIRU_MCP_GATEWAY_PORT not an int: {raw_port}") from exc

    host = os.environ.get("MIRU_MCP_GATEWAY_HOST", DEFAULT_HOST)
    if host not in ("127.0.0.1", "localhost"):
        print(
            f"WARNING: gateway host={host} is not loopback. "
            f"This widens the attack surface beyond the Tailscale Funnel.",
            file=sys.stderr,
        )

    github_token, github_allowlist = _load_github_settings()
    n8n_api_key, n8n_base_url = _load_n8n_settings()

    return GatewayConfig(
        host=host,
        port=port,
        url_secret=secret,
        fs_root=fs_root,
        mcp_path=INTERNAL_MCP_PATH,
        health_path=INTERNAL_HEALTH_PATH,
        github_token=github_token,
        github_allowlist=github_allowlist,
        n8n_api_key=n8n_api_key,
        n8n_base_url=n8n_base_url,
    )
