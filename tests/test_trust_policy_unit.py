"""Unit tests for the gateway full_operator trust policy."""

from __future__ import annotations

import asyncio
import json
import unittest

from miru_mcp_gateway.trust_policy import (
    has_tailscale_funnel_marker,
    is_trusted_origin,
    remote_addr,
    send_full_operator_local_only,
    validate_loopback_bind,
)


def _scope(*, host="127.0.0.1", headers=None):
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers or [],
        "client": (host, 54321),
    }


def _header(name, value):
    return name, value


class TestRemoteAddr(unittest.TestCase):
    def test_extracts_host_from_tuple_client(self):
        self.assertEqual(remote_addr({"client": ("127.0.0.1", 1234)}), "127.0.0.1")

    def test_extracts_host_from_list_client(self):
        self.assertEqual(remote_addr({"client": ["::1", 1234]}), "::1")

    def test_missing_client_returns_none(self):
        self.assertIsNone(remote_addr({}))

    def test_empty_client_returns_none(self):
        self.assertIsNone(remote_addr({"client": []}))

    def test_non_sequence_client_returns_none(self):
        self.assertIsNone(remote_addr({"client": "127.0.0.1"}))

    def test_non_string_host_returns_none(self):
        self.assertIsNone(remote_addr({"client": [1234, 5678]}))

    def test_ipv4_mapped_ipv6_is_returned_unchanged(self):
        self.assertEqual(remote_addr({"client": ("::ffff:127.0.0.1", 1234)}), "::ffff:127.0.0.1")


class TestTailscaleFunnelMarker(unittest.TestCase):
    def test_accepts_byte_header_with_structured_true(self):
        self.assertTrue(
            has_tailscale_funnel_marker({"headers": [(b"tailscale-funnel-request", b"?1")]})
        )

    def test_accepts_string_header_with_structured_true(self):
        self.assertTrue(
            has_tailscale_funnel_marker({"headers": [("tailscale-funnel-request", "?1")]})
        )

    def test_header_name_is_case_insensitive(self):
        self.assertTrue(
            has_tailscale_funnel_marker({"headers": [(b"Tailscale-Funnel-Request", b"?1")]})
        )

    def test_empty_value_rejected(self):
        self.assertFalse(
            has_tailscale_funnel_marker({"headers": [(b"tailscale-funnel-request", b"")]})
        )

    def test_arbitrary_truthy_values_rejected(self):
        for value in ("true", "1", "yes", "?0", "TRUE"):
            with self.subTest(value=value):
                self.assertFalse(
                    has_tailscale_funnel_marker(
                        {"headers": [(b"tailscale-funnel-request", value.encode("utf-8"))]}
                    )
                )

    def test_missing_header_rejected(self):
        self.assertFalse(has_tailscale_funnel_marker({"headers": []}))


class TestTrustedOrigin(unittest.TestCase):
    def test_ipv4_localhost_trusted(self):
        self.assertTrue(is_trusted_origin(_scope(host="127.0.0.1")))

    def test_remote_ipv4_rejected(self):
        self.assertFalse(is_trusted_origin(_scope(host="192.168.1.50")))

    def test_ipv6_localhost_trusted(self):
        self.assertTrue(is_trusted_origin(_scope(host="::1")))

    def test_ipv4_mapped_ipv6_loopback_trusted(self):
        self.assertTrue(is_trusted_origin(_scope(host="::ffff:127.0.0.1")))

    def test_ipv4_mapped_remote_rejected(self):
        self.assertFalse(is_trusted_origin(_scope(host="::ffff:10.0.0.5")))

    def test_tailscale_cgnat_trusted(self):
        self.assertTrue(is_trusted_origin(_scope(host="100.81.19.49")))

    def test_tailscale_cgnat_boundary_trusted(self):
        self.assertTrue(is_trusted_origin(_scope(host="100.64.0.1")))

    def test_ipv4_mapped_tailscale_cgnat_trusted(self):
        self.assertTrue(is_trusted_origin(_scope(host="::ffff:100.81.19.49")))

    def test_just_outside_cgnat_rejected(self):
        self.assertFalse(is_trusted_origin(_scope(host="100.128.0.1")))

    def test_public_ip_with_funnel_marker_trusted(self):
        self.assertTrue(
            is_trusted_origin(
                _scope(
                    host="160.79.106.37",
                    headers=[_header(b"tailscale-funnel-request", b"?1")],
                )
            )
        )

    def test_public_ip_without_funnel_marker_rejected(self):
        self.assertFalse(is_trusted_origin(_scope(host="160.79.106.37")))

    def test_funnel_marker_empty_value_rejected(self):
        self.assertFalse(
            is_trusted_origin(
                _scope(
                    host="160.79.106.37",
                    headers=[_header(b"tailscale-funnel-request", b"")],
                )
            )
        )

    def test_funnel_marker_arbitrary_values_rejected(self):
        for value in ("true", "1", "yes", "?0", "TRUE"):
            with self.subTest(value=value):
                self.assertFalse(
                    is_trusted_origin(
                        _scope(
                            host="160.79.106.37",
                            headers=[_header(b"tailscale-funnel-request", value.encode("utf-8"))],
                        )
                    )
                )

    def test_missing_client_rejected(self):
        self.assertFalse(is_trusted_origin({"type": "http", "headers": []}))

    def test_unparseable_host_rejected(self):
        self.assertFalse(is_trusted_origin(_scope(host="not-an-ip-and-not-localhost")))


class TestValidateLoopbackBind(unittest.TestCase):
    def test_loopback_string_127_accepted(self):
        validate_loopback_bind("127.0.0.1")

    def test_loopback_string_ipv6_accepted(self):
        validate_loopback_bind("::1")

    def test_loopback_string_localhost_accepted(self):
        validate_loopback_bind("localhost")

    def test_loopback_in_127_block_accepted(self):
        validate_loopback_bind("127.0.0.5")

    def test_wildcard_bind_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            validate_loopback_bind("0.0.0.0")
        self.assertIn("not a loopback", str(ctx.exception))

    def test_routable_lan_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            validate_loopback_bind("192.168.1.10")
        self.assertIn("not a loopback", str(ctx.exception))

    def test_routable_public_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            validate_loopback_bind("8.8.8.8")
        self.assertIn("not a loopback", str(ctx.exception))

    def test_garbage_value_rejected_with_clear_error(self):
        with self.assertRaises(SystemExit) as ctx:
            validate_loopback_bind("not-an-ip-and-not-localhost")
        self.assertIn("does not parse", str(ctx.exception))


class TestSendFullOperatorLocalOnly(unittest.TestCase):
    def test_sends_compatible_error_shape(self):
        messages = []

        async def send(message):
            messages.append(message)

        asyncio.run(send_full_operator_local_only(_scope(host="10.0.0.5"), send))

        self.assertEqual(messages[0]["type"], "http.response.start")
        self.assertEqual(messages[0]["status"], 403)
        body = json.loads(messages[1]["body"].decode("utf-8"))
        self.assertEqual(body["error"], "full_operator_local_only")
        self.assertIn("trusted origin", body["message"])
        self.assertEqual(body["remote_addr"], "10.0.0.5")


if __name__ == "__main__":
    unittest.main()
