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

    # Stage 3 / 3.5 write surfaces (PRO-122 / PRO-123)
    n8n_write_enabled: bool = False
    n8n_write_workflow_allowlist: tuple[str, ...] = ()
    n8n_write_approval_notify_url: str | None = None
    docs_write_enabled: bool = False
    docs_write_path_allowlist: tuple[str, ...] = ()

    # PRO-131 / PRO-132 / PRO-134 / PRO-135 / PRO-136 feature gates
    github_read_enabled: bool = False
    n8n_read_enabled: bool = False
    system_logs_enabled: bool = False
    aggregator_enabled: bool = False
    audit_read_enabled: bool = False
    worker_status_enabled: bool = False
    linear_api_key: str | None = None
    linear_team_id: str | None = None
    n8n_container_name: str = "miru-n8n"
    workers_config_raw: str = ""
    workers_yaml_path: Path | None = None
    worker_path_allow_prefixes: tuple[str, ...] = ()
    n8n_execution_data_skip_approval: bool = False

    # PRO-163: miru_memory SQLite tools
    memory_enabled: bool = False
    memory_db_path: Path | None = None

    # PRO-226: Linear write tools
    linear_write_enabled: bool = False

    # PRO-227: Telegram direct send
    telegram_bot_token: str | None = None
    telegram_default_chat_id: str | None = None

    # PRO-187: orchestrator-scoped git commit/push tool
    git_write_enabled: bool = False

    # PRO-225: Perplexity search
    perplexity_api_key: str | None = None
    perplexity_enabled: bool = False

    # PRO-225: service restart tools
    restart_tools_enabled: bool = False

    # PRO-235: dispatch_worker — trigger CC workers via dispatch listener
    dispatch_enabled: bool = False
    dispatch_hmac_secret: str | None = None
    dispatch_listener_url: str = "http://127.0.0.1:19100"

    # PRO-137 rate limits (calls per 60s sliding window, per category)
    rate_limit_by_category: dict[str, int] = field(default_factory=dict)

    # Populated by each category's register() if it refuses to start. The
    # banner reads this to print "github : DISABLED -- <reason>" lines.
    disabled_categories: dict[str, str] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return self.fs_root / "data"

    @property
    def mcp_gateway_pending_writes_path(self) -> Path:
        return self.data_dir / "mcp_gateway_pending_writes.jsonl"

    @property
    def mcp_gateway_execution_cache_dir(self) -> Path:
        """PRO-132: W7 or operator drops approved includeData JSON here."""
        return self.data_dir / "mcp_gateway_execution_cache"

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
            f'python -c "import secrets; print(secrets.token_hex(32))"'
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
        allowlist = tuple(piece.strip() for piece in raw_allow.split(",") if piece.strip())

    return read_token, allowlist


def _load_n8n_settings() -> tuple[str | None, str]:
    api_key = os.environ.get("N8N_API_KEY", "").strip() or None
    base_url = os.environ.get("MIRU_N8N_BASE_URL", "").strip() or DEFAULT_N8N_BASE_URL
    # Strip trailing slash so we can `${base}/api/v1/...` cleanly.
    base_url = base_url.rstrip("/")
    return api_key, base_url


def _truthy_env(name: str) -> bool:
    v = os.environ.get(name, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _comma_tuple(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _load_rate_limits() -> dict[str, int]:
    """PRO-137: per-category calls per minute (sliding window)."""
    keys = (
        ("linear_read", "MIRU_RATE_LIMIT_LINEAR_READ"),
        ("linear_write", "MIRU_RATE_LIMIT_LINEAR_WRITE"),
        ("github_read", "MIRU_RATE_LIMIT_GITHUB_READ"),
        ("n8n_read", "MIRU_RATE_LIMIT_N8N_READ"),
        ("n8n_write", "MIRU_RATE_LIMIT_N8N_WRITE"),
        ("docs_write", "MIRU_RATE_LIMIT_DOCS_WRITE"),
        ("filesystem_read", "MIRU_RATE_LIMIT_FILESYSTEM_READ"),
        ("system_logs", "MIRU_RATE_LIMIT_SYSTEM_LOGS"),
        ("aggregator", "MIRU_RATE_LIMIT_AGGREGATOR"),
        ("audit_read", "MIRU_RATE_LIMIT_AUDIT_READ"),
        ("worker_read", "MIRU_RATE_LIMIT_WORKER_READ"),
        ("memory_write", "MIRU_RATE_LIMIT_MEMORY_WRITE"),
        ("git_write", "MIRU_RATE_LIMIT_GIT_WRITE"),
        ("perplexity", "MIRU_RATE_LIMIT_PERPLEXITY"),
        ("restart", "MIRU_RATE_LIMIT_RESTART"),
        ("telegram", "MIRU_RATE_LIMIT_TELEGRAM"),
        ("dispatch", "MIRU_RATE_LIMIT_DISPATCH"),
        ("default", "MIRU_RATE_LIMIT_DEFAULT"),
    )
    defaults: dict[str, int] = {
        "linear_read": 60,
        "linear_write": 30,
        "github_read": 60,
        "n8n_read": 60,
        "n8n_write": 10,
        "docs_write": 20,
        "filesystem_read": 120,
        "system_logs": 60,
        "aggregator": 30,
        "audit_read": 60,
        "worker_read": 30,
        "memory_write": 60,
        "git_write": 10,
        "perplexity": 20,
        "restart": 5,
        "telegram": 20,
        "dispatch": 5,
        "default": 30,
    }
    out = dict(defaults)
    for cat, env_name in keys:
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        try:
            out[cat] = max(1, int(raw))
        except ValueError:
            continue
    return out


def _workers_yaml_path(fs_root: Path) -> Path | None:
    raw = os.environ.get("MIRU_WORKERS_YAML", "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = (fs_root / p).resolve()
    return p


def _default_docs_write_globs() -> tuple[str, ...]:
    """PRO-123/PRO-225 default positive allowlist (repo-relative posix globs)."""
    return (
        "*.md",
        "docs/**/*.md",
        "docs/**/*.txt",
        "miru-context/*.md",
        "miru-context/**/*.md",
        "tools/**/*.md",
        "services/**/*.md",
        "pm/**/*.md",
        "miru_ai/**/*.md",
        "dispatcher/**/*.md",
        "docker/**/*.md",
        "windows/**/*.md",
        "skills/**/*.md",
        ".cursor/rules/*.md",
        ".claude/**/*.md",
        "data/config/*",
    )


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

    n8n_write_enabled = _truthy_env("MIRU_N8N_WRITE_ENABLED")
    n8n_write_workflow_allowlist = _comma_tuple("MIRU_N8N_WRITE_WORKFLOW_ALLOWLIST")
    n8n_write_approval_notify_url = (
        os.environ.get("MIRU_N8N_WRITE_APPROVAL_NOTIFY_URL", "").strip() or None
    )

    docs_write_enabled = _truthy_env("MIRU_DOCS_WRITE_ENABLED")
    raw_docs_allow = _comma_tuple("MIRU_DOCS_WRITE_PATH_ALLOWLIST")
    docs_write_path_allowlist = raw_docs_allow if raw_docs_allow else _default_docs_write_globs()

    github_read_enabled = _truthy_env("MIRU_GITHUB_READ_ENABLED")
    n8n_read_enabled = _truthy_env("MIRU_N8N_READ_ENABLED")
    system_logs_enabled = _truthy_env("MIRU_SYSTEM_LOGS_ENABLED")
    aggregator_enabled = _truthy_env("MIRU_AGGREGATOR_ENABLED")
    audit_read_enabled = _truthy_env("MIRU_AUDIT_READ_ENABLED")
    worker_status_enabled = _truthy_env("MIRU_WORKER_STATUS_ENABLED")
    linear_api_key = os.environ.get("LINEAR_API_KEY", "").strip() or None
    linear_team_id = os.environ.get("MIRU_LINEAR_TEAM_ID", "").strip() or None
    n8n_container_name = os.environ.get("MIRU_N8N_CONTAINER_NAME", "").strip() or "miru-n8n"
    workers_config_raw = os.environ.get("MIRU_WORKERS_CONFIG", "").strip()
    workers_yaml_path = _workers_yaml_path(fs_root)
    worker_path_allow_prefixes = _comma_tuple("MIRU_WORKER_PATH_ALLOWLIST")
    n8n_execution_data_skip_approval = _truthy_env("MIRU_N8N_EXECUTION_DATA_SKIP_APPROVAL")
    rate_limit_by_category = _load_rate_limits()

    memory_enabled = _truthy_env("MIRU_MEMORY_ENABLED")
    raw_mem_db = os.environ.get("MIRU_MEMORY_DB_PATH", "").strip()
    memory_db_path: Path | None = None
    if raw_mem_db:
        p = Path(raw_mem_db)
        memory_db_path = p if p.is_absolute() else (fs_root / p).resolve()
    elif memory_enabled:
        memory_db_path = fs_root / "data" / "miru_memory.db"

    linear_write_enabled = _truthy_env("MIRU_LINEAR_WRITE_ENABLED")
    telegram_bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
    telegram_default_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None
    git_write_enabled = _truthy_env("MIRU_GIT_WRITE_ENABLED")

    perplexity_api_key = os.environ.get("PERPLEXITY_API_KEY", "").strip() or None
    perplexity_enabled = bool(perplexity_api_key)
    restart_tools_enabled = _truthy_env("MIRU_RESTART_TOOLS_ENABLED")

    dispatch_hmac_secret = os.environ.get("W4_LISTENER_HMAC_SECRET", "").strip() or None
    dispatch_listener_url = (
        os.environ.get("MIRU_DISPATCH_LISTENER_URL", "http://127.0.0.1:19100").strip().rstrip("/")
    )
    dispatch_enabled = _truthy_env("MIRU_DISPATCH_ENABLED") and bool(dispatch_hmac_secret)

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
        n8n_write_enabled=n8n_write_enabled,
        n8n_write_workflow_allowlist=n8n_write_workflow_allowlist,
        n8n_write_approval_notify_url=n8n_write_approval_notify_url,
        docs_write_enabled=docs_write_enabled,
        docs_write_path_allowlist=docs_write_path_allowlist,
        github_read_enabled=github_read_enabled,
        n8n_read_enabled=n8n_read_enabled,
        system_logs_enabled=system_logs_enabled,
        aggregator_enabled=aggregator_enabled,
        audit_read_enabled=audit_read_enabled,
        worker_status_enabled=worker_status_enabled,
        linear_api_key=linear_api_key,
        linear_team_id=linear_team_id,
        n8n_container_name=n8n_container_name,
        workers_config_raw=workers_config_raw,
        workers_yaml_path=workers_yaml_path,
        worker_path_allow_prefixes=worker_path_allow_prefixes,
        n8n_execution_data_skip_approval=n8n_execution_data_skip_approval,
        rate_limit_by_category=rate_limit_by_category,
        memory_enabled=memory_enabled,
        memory_db_path=memory_db_path,
        linear_write_enabled=linear_write_enabled,
        telegram_bot_token=telegram_bot_token,
        telegram_default_chat_id=telegram_default_chat_id,
        git_write_enabled=git_write_enabled,
        perplexity_api_key=perplexity_api_key,
        perplexity_enabled=perplexity_enabled,
        restart_tools_enabled=restart_tools_enabled,
        dispatch_enabled=dispatch_enabled,
        dispatch_hmac_secret=dispatch_hmac_secret,
        dispatch_listener_url=dispatch_listener_url,
    )
