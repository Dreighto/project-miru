"""ASGI entry-gate tests for the full_operator localhost bind."""

from __future__ import annotations

import asyncio
import json
import unittest

from miru_mcp_gateway._context import current_profile
from miru_mcp_gateway.server import _ProfileExtractor


async def _empty_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _header(name: str, value: str) -> tuple[bytes, bytes]:
    return name.encode("utf-8"), value.encode("utf-8")


class _CaptureApp:
    def __init__(self) -> None:
        self.called = False
        self.profile_seen = None

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        self.profile_seen = current_profile.get()
        await send({"type": "test.app_called"})


def _run_middleware(scope):
    app = _CaptureApp()
    middleware = _ProfileExtractor(app)
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(middleware(scope, _empty_receive, send))
    return app, messages


def _http_scope(*, host: str, headers=None):
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers or [],
        "client": (host, 54321),
    }


class TestFullOperatorLocalhostBind(unittest.TestCase):
    def test_no_header_from_ipv4_localhost_proceeds_as_full_operator(self):
        app, messages = _run_middleware(_http_scope(host="127.0.0.1"))

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_no_header_from_remote_ipv4_rejected(self):
        app, messages = _run_middleware(_http_scope(host="192.168.1.50"))

        self.assertFalse(app.called)
        self.assertEqual(messages[0]["type"], "http.response.start")
        self.assertEqual(messages[0]["status"], 403)
        body = json.loads(messages[1]["body"].decode("utf-8"))
        self.assertEqual(body["error"], "full_operator_local_only")
        self.assertEqual(body["remote_addr"], "192.168.1.50")

    def test_full_operator_header_from_ipv4_localhost_proceeds(self):
        app, messages = _run_middleware(
            _http_scope(
                host="127.0.0.1",
                headers=[_header("x-miru-tool-profile", "full_operator")],
            )
        )

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_full_operator_header_from_remote_ipv4_rejected(self):
        app, messages = _run_middleware(
            _http_scope(
                host="10.0.0.5",
                headers=[_header("x-miru-tool-profile", "full_operator")],
            )
        )

        self.assertFalse(app.called)
        self.assertEqual(messages[0]["status"], 403)
        body = json.loads(messages[1]["body"].decode("utf-8"))
        self.assertEqual(body["error"], "full_operator_local_only")
        self.assertEqual(body["remote_addr"], "10.0.0.5")

    def test_drift_executor_from_remote_ipv4_proceeds(self):
        app, messages = _run_middleware(
            _http_scope(
                host="10.0.0.5",
                headers=[_header("x-miru-tool-profile", "drift_executor")],
            )
        )

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "drift_executor")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_full_operator_header_from_ipv6_localhost_proceeds(self):
        app, messages = _run_middleware(
            _http_scope(
                host="::1",
                headers=[_header("x-miru-tool-profile", "full_operator")],
            )
        )

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_full_operator_from_ipv4_mapped_ipv6_loopback_proceeds(self):
        """ASGI servers on dual-stack sockets sometimes present loopback as
        ``::ffff:127.0.0.1``. That is still local; the gate must accept it.
        Without IPv4-mapped IPv6 normalisation this would 403 a legit local
        request."""
        app, messages = _run_middleware(
            _http_scope(
                host="::ffff:127.0.0.1",
                headers=[_header("x-miru-tool-profile", "full_operator")],
            )
        )

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_full_operator_from_ipv4_mapped_remote_rejected(self):
        """Dual-stack mapped form must NOT be a backdoor: a non-loopback
        IPv4-mapped IPv6 address (e.g. ::ffff:10.0.0.5) still gets rejected."""
        app, messages = _run_middleware(
            _http_scope(
                host="::ffff:10.0.0.5",
                headers=[_header("x-miru-tool-profile", "full_operator")],
            )
        )

        self.assertFalse(app.called, "remote IPv4-mapped IPv6 must NOT bypass localhost gate")
        # 403 response sent
        statuses = [m.get("status") for m in messages if m.get("type") == "http.response.start"]
        self.assertEqual(statuses, [403])

    def test_no_header_from_tailscale_cgnat_proceeds_as_full_operator(self):
        """Tailscale CGNAT range (100.64.0.0/10) is a trusted origin: the
        gateway binds loopback only, so the only way such a peer reaches us
        is via the local Tailscale daemon proxying tunneled traffic that
        has already been authorized by the Funnel path-secret. Rejecting
        these would lock out the claude.ai web connector with no security
        gain."""
        app, messages = _run_middleware(_http_scope(host="100.81.19.49"))

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_full_operator_header_from_tailscale_cgnat_proceeds(self):
        app, messages = _run_middleware(
            _http_scope(
                host="100.64.0.1",
                headers=[_header("x-miru-tool-profile", "full_operator")],
            )
        )

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_full_operator_from_ipv4_mapped_tailscale_cgnat_proceeds(self):
        """IPv4-mapped IPv6 form of a tailnet IP also counts as trusted."""
        app, messages = _run_middleware(
            _http_scope(
                host="::ffff:100.81.19.49",
                headers=[_header("x-miru-tool-profile", "full_operator")],
            )
        )

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])

    def test_full_operator_from_just_outside_cgnat_rejected(self):
        """100.128.0.0 is one bit outside the 100.64.0.0/10 boundary and
        must NOT be treated as tailnet. Boundary regression check."""
        app, messages = _run_middleware(_http_scope(host="100.128.0.1"))

        self.assertFalse(app.called)
        statuses = [m.get("status") for m in messages if m.get("type") == "http.response.start"]
        self.assertEqual(statuses, [403])

    def test_stdio_like_non_http_scope_bypasses_localhost_check(self):
        app, messages = _run_middleware({"type": "lifespan", "client": ("10.0.0.5", 54321)})

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])


if __name__ == "__main__":
    unittest.main()
