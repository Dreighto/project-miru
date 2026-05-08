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

    def test_stdio_like_non_http_scope_bypasses_localhost_check(self):
        app, messages = _run_middleware({"type": "lifespan", "client": ("10.0.0.5", 54321)})

        self.assertTrue(app.called)
        self.assertEqual(app.profile_seen, "full_operator")
        self.assertEqual(messages, [{"type": "test.app_called"}])


if __name__ == "__main__":
    unittest.main()
