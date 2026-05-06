"""Unit tests for the cc_handoff MCP tool (gatekeeper_tools.py).

Tests cover:
  1. Input validation (ticket_id, prompt, lengths).
  2. Payload construction — correct keys passed to gate_dispatch.
  3. Error mapping — GatekeeperError -> McpError.
  4. Decision passthrough — gate_dispatch result returned as JSON.
  5. Registration gating — disabled when dispatch is not enabled.

Gate_dispatch is mocked: the gatekeeper core module has its own test
suite (test_gatekeeper_core.py, 57 tests). These tests verify the MCP
tool wrapper, not the gatekeeper internals.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TOOLS_DIR = _REPO_ROOT / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import miru_readonly_filesystem_mcp as stdio_mcp  # noqa: E402
from miru_mcp_gateway import gatekeeper_tools  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_decision(**overrides: Any) -> dict[str, Any]:
    """Build a minimal valid gate_dispatch decision dict."""
    base: dict[str, Any] = {
        "schema_version": "2",
        "trace_id": "rtr-PRO-999-abcdef1234567890",
        "ticket_id": "PRO-999",
        "validation": {"is_legitimate_build": True},
        "decision": {
            "worker": "claude-code",
            "mode": "judgment",
            "tool_profile": "standard_worker",
            "confidence": 0.92,
        },
        "execution": {
            "model": "claude-sonnet-4-20250514",
            "thinking_level": "extended",
            "timeout_seconds": 600,
            "plan_only": False,
        },
        "rejection": None,
        "context_snapshot": {},
        "flags": ["latency_ms:450", "model:qwen2.5:7b"],
    }
    base.update(overrides)
    return base


def _fake_rejection(**overrides: Any) -> dict[str, Any]:
    """Build a Phase 2.5 Rejection decision dict."""
    base: dict[str, Any] = {
        "schema_version": "2",
        "trace_id": "rtr-PRO-999-abcdef1234567890",
        "ticket_id": "PRO-999",
        "validation": {"is_legitimate_build": False},
        "decision": {"worker": "none", "mode": "blocked"},
        "rejection": {
            "reason": "dirty_worktree",
            "explanation": "main repo has uncommitted tracked changes",
            "suggested_correction": "Commit or stash changes.",
        },
        "flags": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Input validation
# ---------------------------------------------------------------------------


class TestInputValidation(unittest.TestCase):
    """cc_handoff rejects bad inputs before calling gate_dispatch."""

    def test_empty_ticket_id_raises(self) -> None:
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            gatekeeper_tools.cc_handoff(ticket_id="", prompt="do the thing")
        self.assertIn("ticket_id", str(ctx.exception))

    def test_none_ticket_id_raises(self) -> None:
        with self.assertRaises(stdio_mcp.McpError):
            gatekeeper_tools.cc_handoff(ticket_id=None, prompt="do the thing")  # type: ignore[arg-type]

    def test_whitespace_ticket_id_raises(self) -> None:
        with self.assertRaises(stdio_mcp.McpError):
            gatekeeper_tools.cc_handoff(ticket_id="   ", prompt="do the thing")

    def test_empty_prompt_raises(self) -> None:
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            gatekeeper_tools.cc_handoff(ticket_id="PRO-999", prompt="")
        self.assertIn("prompt", str(ctx.exception))

    def test_none_prompt_raises(self) -> None:
        with self.assertRaises(stdio_mcp.McpError):
            gatekeeper_tools.cc_handoff(ticket_id="PRO-999", prompt=None)  # type: ignore[arg-type]

    def test_whitespace_prompt_raises(self) -> None:
        with self.assertRaises(stdio_mcp.McpError):
            gatekeeper_tools.cc_handoff(ticket_id="PRO-999", prompt="   ")

    def test_prompt_too_long_raises(self) -> None:
        long_prompt = "x" * (gatekeeper_tools._MAX_PROMPT_CHARS + 1)
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            gatekeeper_tools.cc_handoff(ticket_id="PRO-999", prompt=long_prompt)
        self.assertIn("too long", str(ctx.exception))

    def test_description_too_long_raises(self) -> None:
        long_desc = "d" * (gatekeeper_tools._MAX_DESCRIPTION_CHARS + 1)
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            gatekeeper_tools.cc_handoff(
                ticket_id="PRO-999",
                prompt="do the thing",
                ticket_description=long_desc,
            )
        self.assertIn("ticket_description", str(ctx.exception))

    def test_integer_ticket_id_raises(self) -> None:
        with self.assertRaises(stdio_mcp.McpError):
            gatekeeper_tools.cc_handoff(ticket_id=999, prompt="do the thing")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 2. Payload construction
# ---------------------------------------------------------------------------


class TestPayloadConstruction(unittest.TestCase):
    """Verify that cc_handoff builds the correct payload for gate_dispatch."""

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_minimal_payload(self, mock_gd: mock.MagicMock) -> None:
        mock_gd.return_value = _fake_decision()
        gatekeeper_tools.cc_handoff(ticket_id="PRO-100", prompt="fix the bug")

        mock_gd.assert_called_once()
        payload = mock_gd.call_args[0][0]
        self.assertEqual(payload["ticket_id"], "PRO-100")
        self.assertEqual(payload["prompt"], "fix the bug")
        self.assertTrue(payload["shadow_mode"])
        self.assertNotIn("ticket_description", payload)
        self.assertNotIn("conversational_delta", payload)
        self.assertNotIn("gatekeeper_model", payload)

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_full_payload(self, mock_gd: mock.MagicMock) -> None:
        mock_gd.return_value = _fake_decision()
        gatekeeper_tools.cc_handoff(
            ticket_id="  PRO-200  ",
            prompt="add pagination",
            ticket_description="<!-- dispatch:\n  worker: claude-code\n-->",
            conversational_delta="Also handle edge case X",
            shadow_mode=False,
            gatekeeper_model="llama3.1:8b",
        )

        payload = mock_gd.call_args[0][0]
        self.assertEqual(payload["ticket_id"], "PRO-200")  # stripped
        self.assertEqual(payload["prompt"], "add pagination")
        self.assertFalse(payload["shadow_mode"])
        self.assertIn("dispatch", payload["ticket_description"])
        self.assertEqual(payload["conversational_delta"], "Also handle edge case X")
        self.assertEqual(payload["gatekeeper_model"], "llama3.1:8b")

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_shadow_mode_defaults_true(self, mock_gd: mock.MagicMock) -> None:
        mock_gd.return_value = _fake_decision()
        gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")
        payload = mock_gd.call_args[0][0]
        self.assertTrue(payload["shadow_mode"])

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_none_optional_fields_excluded(self, mock_gd: mock.MagicMock) -> None:
        mock_gd.return_value = _fake_decision()
        gatekeeper_tools.cc_handoff(
            ticket_id="PRO-1",
            prompt="test",
            ticket_description=None,
            conversational_delta=None,
            gatekeeper_model=None,
        )
        payload = mock_gd.call_args[0][0]
        self.assertNotIn("ticket_description", payload)
        self.assertNotIn("conversational_delta", payload)
        self.assertNotIn("gatekeeper_model", payload)


# ---------------------------------------------------------------------------
# 3. Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping(unittest.TestCase):
    """GatekeeperError is converted to McpError with correct details."""

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_gatekeeper_error_becomes_mcp_error(self, mock_gd: mock.MagicMock) -> None:
        from gatekeeper.core import GatekeeperError

        mock_gd.side_effect = GatekeeperError("ollama_unreachable", "Connection refused")
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")
        msg = str(ctx.exception)
        self.assertIn("ollama_unreachable", msg)
        self.assertIn("Connection refused", msg)

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_gatekeeper_error_no_detail(self, mock_gd: mock.MagicMock) -> None:
        from gatekeeper.core import GatekeeperError

        mock_gd.side_effect = GatekeeperError("payload_missing_ticket_id")
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")
        msg = str(ctx.exception)
        self.assertIn("payload_missing_ticket_id", msg)

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_mcp_error_code_is_minus_32000(self, mock_gd: mock.MagicMock) -> None:
        from gatekeeper.core import GatekeeperError

        mock_gd.side_effect = GatekeeperError("ollama_timeout", "timed out after 180s")
        with self.assertRaises(stdio_mcp.McpError) as ctx:
            gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")
        self.assertEqual(ctx.exception.code, -32000)


# ---------------------------------------------------------------------------
# 4. Decision passthrough
# ---------------------------------------------------------------------------


class TestDecisionPassthrough(unittest.TestCase):
    """gate_dispatch result is returned as formatted JSON."""

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_acceptance_returned_as_json(self, mock_gd: mock.MagicMock) -> None:
        expected = _fake_decision()
        mock_gd.return_value = expected
        result = gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")

        parsed = json.loads(result)
        self.assertEqual(parsed["schema_version"], "2")
        self.assertEqual(parsed["decision"]["worker"], "claude-code")
        self.assertIsNone(parsed["rejection"])

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_rejection_returned_as_json(self, mock_gd: mock.MagicMock) -> None:
        expected = _fake_rejection()
        mock_gd.return_value = expected
        result = gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")

        parsed = json.loads(result)
        self.assertEqual(parsed["decision"]["worker"], "none")
        self.assertEqual(parsed["rejection"]["reason"], "dirty_worktree")

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_json_is_indented(self, mock_gd: mock.MagicMock) -> None:
        mock_gd.return_value = _fake_decision()
        result = gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")
        self.assertIn("\n", result)  # indented JSON has newlines

    @mock.patch("gatekeeper.core.gate_dispatch")
    def test_all_decision_keys_preserved(self, mock_gd: mock.MagicMock) -> None:
        decision = _fake_decision(
            flags=["latency_ms:200", "model:qwen2.5:7b", "shadow_mode:no_forward"]
        )
        mock_gd.return_value = decision
        result = gatekeeper_tools.cc_handoff(ticket_id="PRO-1", prompt="test")

        parsed = json.loads(result)
        self.assertEqual(parsed["flags"], decision["flags"])
        self.assertIn("execution", parsed)
        self.assertIn("context_snapshot", parsed)


# ---------------------------------------------------------------------------
# 5. Registration gating
# ---------------------------------------------------------------------------


class TestRegistration(unittest.TestCase):
    """Register function gates on dispatch_enabled + HMAC secret."""

    def _make_cfg(self, dispatch_enabled: bool = False, hmac_secret: str | None = None) -> Any:
        cfg = mock.MagicMock()
        cfg.dispatch_enabled = dispatch_enabled
        cfg.dispatch_hmac_secret = hmac_secret
        cfg.disabled_categories = {}
        return cfg

    def test_disabled_when_dispatch_not_enabled(self) -> None:
        cfg = self._make_cfg(dispatch_enabled=False, hmac_secret="secret")
        mcp = mock.MagicMock()
        count = gatekeeper_tools.register(mcp, cfg)
        self.assertEqual(count, 0)
        self.assertIn("gatekeeper", cfg.disabled_categories)
        mcp.tool.assert_not_called()

    def test_disabled_when_no_hmac_secret(self) -> None:
        cfg = self._make_cfg(dispatch_enabled=False, hmac_secret=None)
        mcp = mock.MagicMock()
        count = gatekeeper_tools.register(mcp, cfg)
        self.assertEqual(count, 0)
        self.assertIn("gatekeeper", cfg.disabled_categories)

    @mock.patch("miru_mcp_gateway.gateway_security.wrap_tool_entry")
    def test_enabled_registers_one_tool(self, mock_wrap: mock.MagicMock) -> None:
        mock_wrap.return_value = lambda: None
        cfg = self._make_cfg(dispatch_enabled=True, hmac_secret="secret123")
        mcp = mock.MagicMock()
        count = gatekeeper_tools.register(mcp, cfg)
        self.assertEqual(count, 1)
        mcp.tool.assert_called_once()
        self.assertNotIn("gatekeeper", cfg.disabled_categories)

    def test_tool_functions_tuple_contains_cc_handoff(self) -> None:
        self.assertIn(gatekeeper_tools.cc_handoff, gatekeeper_tools.TOOL_FUNCTIONS)
        self.assertEqual(len(gatekeeper_tools.TOOL_FUNCTIONS), 1)


# ---------------------------------------------------------------------------
# 6. Constants
# ---------------------------------------------------------------------------


class TestConstants(unittest.TestCase):
    def test_max_prompt_chars_is_positive(self) -> None:
        self.assertGreater(gatekeeper_tools._MAX_PROMPT_CHARS, 0)

    def test_max_description_chars_is_positive(self) -> None:
        self.assertGreater(gatekeeper_tools._MAX_DESCRIPTION_CHARS, 0)

    def test_max_prompt_matches_dispatch_tools(self) -> None:
        from miru_mcp_gateway import dispatch_tools

        self.assertEqual(
            gatekeeper_tools._MAX_PROMPT_CHARS,
            dispatch_tools._MAX_PROMPT_CHARS,
        )


if __name__ == "__main__":
    unittest.main()
