"""Trust policy for the gateway ``full_operator`` profile."""

from __future__ import annotations

import ipaddress
import json

from miru_mcp_gateway import redact as gw_redact

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
TAILSCALE_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")

__all__ = [
    "LOCAL_HOSTS",
    "TAILSCALE_CGNAT_V4",
    "has_tailscale_funnel_marker",
    "is_trusted_origin",
    "remote_addr",
    "send_full_operator_local_only",
    "validate_loopback_bind",
]


def remote_addr(scope) -> str | None:
    client = scope.get("client")
    if not client:
        return None
    if not isinstance(client, list | tuple) or len(client) < 1:
        return None
    host = client[0]
    if not isinstance(host, str):
        return None
    return host


def validate_loopback_bind(host: str) -> None:
    """Fail fast if the gateway is configured to bind a non-loopback interface.

    The trusted-origin checks in ``is_trusted_origin`` (specifically the
    ``Tailscale-Funnel-Request`` header branch) depend on the loopback bind
    invariant: if the gateway is reachable from a non-Tailscale, non-localhost
    peer, an attacker can spoof the Funnel header and self-elevate to
    ``full_operator``.

    Accepted: 127.0.0.1, ::1, "localhost". Rejected: 0.0.0.0, any routable
    interface address, or anything that fails to parse.
    """
    if host in LOCAL_HOSTS:
        return
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        raise SystemExit(
            f"FATAL: gateway host {host!r} does not parse as an IP address. "
            f"The gateway must bind a loopback interface "
            f"(127.0.0.1, ::1, or 'localhost') because the "
            f"Tailscale-Funnel-Request header trust assumes loopback-only "
            f"reachability. Set MIRU_MCP_GATEWAY_HOST=127.0.0.1 in .env."
        ) from None
    if not addr.is_loopback:
        raise SystemExit(
            f"FATAL: gateway host {host!r} is not a loopback address. "
            f"The Tailscale-Funnel-Request header trust check in "
            f"is_trusted_origin assumes the gateway is unreachable from "
            f"any non-localhost, non-Tailscale peer. Binding to a routable "
            f"interface would let any reachable client spoof the header "
            f"and self-elevate to full_operator. "
            f"Set MIRU_MCP_GATEWAY_HOST=127.0.0.1 in .env."
        )


def has_tailscale_funnel_marker(scope) -> bool:
    """Return True if the request carries Tailscale Funnel's identifying header.

    Tailscale Funnel injects ``Tailscale-Funnel-Request: ?1`` (Structured Field
    boolean-true) on every request it forwards from the public internet.
    Tailscale also strips any client-supplied ``Tailscale-*`` headers before
    forwarding, so an external caller cannot spoof this marker.

    Combined with the gateway's loopback-only bind (enforced at startup by
    ``validate_loopback_bind``), presence of this header is a reliable signal
    that the request was authorized by the Tailscale Funnel path-secret at
    Tailscale's edge.

    Strict ``?1`` match per Tailscale's Structured Field spec; any other value,
    including empty or arbitrary strings, is rejected.
    """
    headers = scope.get("headers") or []
    for name, value in headers:
        lower_name = name.lower() if isinstance(name, bytes) else name.encode().lower()
        if lower_name == b"tailscale-funnel-request":
            decoded = value.decode("utf-8") if isinstance(value, bytes) else value
            return decoded.strip() == "?1"
    return False


def is_trusted_origin(scope) -> bool:
    """Return True iff the request is a trusted-peer origin for full_operator.

    "Trusted" means one of:
        * Literal strings 127.0.0.1, ::1, "localhost".
        * IPv4 loopback range (parsed via ipaddress).
        * IPv4-mapped IPv6 loopback (e.g. ``::ffff:127.0.0.1``).
        * Tailscale CGNAT range 100.64.0.0/10 (and its IPv4-mapped form).
          Catches direct tailnet-member requests where the proxy preserves the
          tailnet peer IP.
        * Carries the ``Tailscale-Funnel-Request`` header. Tailscale Funnel
          injects this on every Funnel-forwarded request and strips incoming
          client copies, so its presence is a reliable signal that the request
          transited the Funnel (which means the URL-path secret was validated
          at Tailscale's edge). Without this branch the gate would reject
          claude.ai (and any other public Funnel client) because Tailscale
          Funnel preserves the original public client IP through to the
          upstream, so the peer address shows as a public IP, not a CGNAT one.

    The gateway binds 127.0.0.1 only, so an external caller cannot reach us at
    all without traversing either localhost or Tailscale; both paths are
    covered above.

    Anything else is treated as remote; fail closed.
    """
    if has_tailscale_funnel_marker(scope):
        return True

    host = remote_addr(scope)
    if host is None:
        return False
    if host in LOCAL_HOSTS:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        mapped = addr.ipv4_mapped
        if mapped.is_loopback:
            return True
        return mapped in TAILSCALE_CGNAT_V4
    return isinstance(addr, ipaddress.IPv4Address) and addr in TAILSCALE_CGNAT_V4


async def send_full_operator_local_only(scope, send) -> None:
    remote = remote_addr(scope)
    if remote is not None:
        remote = gw_redact.redact(remote)
    payload = {
        "error": "full_operator_local_only",
        "message": (
            "full_operator requires a trusted origin "
            "(loopback, tailnet 100.64.0.0/10, or Tailscale-Funnel-Request header)"
        ),
        "remote_addr": remote,
    }
    body = json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
