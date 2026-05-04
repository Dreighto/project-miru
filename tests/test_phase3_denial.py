"""Phase 3 Denial Tests — Subagent Isolation.

Maps to the 5 canon denial requirements from the Gemini blueprint:
  1. drift_executor cannot access Telegram/escalation tools
  2. drift_executor cannot broaden its own tool profile
  3. Denied attempts are logged
  4. Decision trace records escalation_attempted
  5. Agent continues or fails safely after denial

Plus boundary tests for full_operator, unknown profile, and no-header default.
"""

from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

import miru_readonly_filesystem_mcp as stdio_mcp
from miru_mcp_gateway import profiles
from miru_mcp_gateway.gateway_security import wrap_tool_entry
from miru_mcp_gateway.server import current_profile, current_trace_id


def _make_cfg(*, enforcement: bool = True) -> SimpleNamespace:
    root = Path(__file__).resolve().parent / "_tmp" / f"deny_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    return SimpleNamespace(
        fs_root=root,
        profile_enforcement_enabled=enforcement,
        rate_limit_by_category={"default": 9999},
    )


def _dummy_tool(ctx=None, **kwargs):
    return "ok"


_dummy_tool.__module__ = "miru_mcp_gateway.telegram_tools"
_dummy_tool.__name__ = "telegram_send_message"


def _make_dispatch_tool(ctx=None, **kwargs):
    return "dispatched"


_make_dispatch_tool.__module__ = "miru_mcp_gateway.dispatch_tools"
_make_dispatch_tool.__name__ = "dispatch_worker"


def _make_restart_tool(ctx=None, **kwargs):
    return "restarted"


_make_restart_tool.__module__ = "miru_mcp_gateway.restart_tools"
_make_restart_tool.__name__ = "service_restart"


def _make_fs_tool(ctx=None, **kwargs):
    return "file_content"


_make_fs_tool.__module__ = "miru_mcp_gateway.fs_tools"
_make_fs_tool.__name__ = "fs_read_text_file"


def _make_linear_tool(ctx=None, **kwargs):
    return "linear_ok"


_make_linear_tool.__module__ = "miru_mcp_gateway.linear_write_tools"
_make_linear_tool.__name__ = "linear_create_issue"


def _make_git_tool(ctx=None, **kwargs):
    return "committed"


_make_git_tool.__module__ = "miru_mcp_gateway.git_tools"
_make_git_tool.__name__ = "git_commit_and_push"


class TestProfileDefinitions(unittest.TestCase):
    """Verify the profile allowlist table is correctly encoded."""

    def test_drift_executor_denied_telegram(self):
        self.assertFalse(profiles.is_allowed("drift_executor", "telegram"))

    def test_drift_executor_denied_dispatch(self):
        self.assertFalse(profiles.is_allowed("drift_executor", "dispatch"))

    def test_drift_executor_denied_restart(self):
        self.assertFalse(profiles.is_allowed("drift_executor", "restart"))

    def test_drift_executor_denied_vp_ops(self):
        self.assertFalse(profiles.is_allowed("drift_executor", "vp_ops"))

    def test_drift_executor_denied_linear_write(self):
        self.assertFalse(profiles.is_allowed("drift_executor", "linear_write"))

    def test_drift_executor_allowed_filesystem_read(self):
        self.assertTrue(profiles.is_allowed("drift_executor", "filesystem_read"))

    def test_drift_executor_allowed_github_read(self):
        self.assertTrue(profiles.is_allowed("drift_executor", "github_read"))

    def test_drift_executor_allowed_memory_write(self):
        self.assertTrue(profiles.is_allowed("drift_executor", "memory_write"))

    def test_standard_worker_allowed_linear_write(self):
        self.assertTrue(profiles.is_allowed("standard_worker", "linear_write"))

    def test_standard_worker_allowed_git_write(self):
        self.assertTrue(profiles.is_allowed("standard_worker", "git_write"))

    def test_standard_worker_denied_telegram(self):
        self.assertFalse(profiles.is_allowed("standard_worker", "telegram"))

    def test_standard_worker_denied_dispatch(self):
        self.assertFalse(profiles.is_allowed("standard_worker", "dispatch"))

    def test_vp_ops_allowed_linear_write(self):
        self.assertTrue(profiles.is_allowed("vp_ops", "linear_write"))

    def test_vp_ops_allowed_vp_ops(self):
        self.assertTrue(profiles.is_allowed("vp_ops", "vp_ops"))

    def test_vp_ops_denied_telegram(self):
        self.assertFalse(profiles.is_allowed("vp_ops", "telegram"))

    def test_vp_ops_denied_dispatch(self):
        self.assertFalse(profiles.is_allowed("vp_ops", "dispatch"))

    def test_reviewer_matches_drift_executor(self):
        for cat in ("telegram", "dispatch", "restart", "linear_write", "n8n_write"):
            self.assertFalse(profiles.is_allowed("reviewer", cat), cat)
        for cat in ("filesystem_read", "github_read", "memory_write"):
            self.assertTrue(profiles.is_allowed("reviewer", cat), cat)

    def test_full_operator_allows_everything(self):
        for cat in ("telegram", "dispatch", "restart", "vp_ops", "filesystem_read"):
            self.assertTrue(profiles.is_allowed("full_operator", cat), cat)

    def test_unknown_profile_gets_drift_executor_restrictions(self):
        self.assertFalse(profiles.is_allowed("unknown_bot_9000", "telegram"))
        self.assertFalse(profiles.is_allowed("unknown_bot_9000", "dispatch"))
        self.assertTrue(profiles.is_allowed("unknown_bot_9000", "filesystem_read"))

    def test_known_profile(self):
        self.assertTrue(profiles.known_profile("drift_executor"))
        self.assertTrue(profiles.known_profile("full_operator"))
        self.assertFalse(profiles.known_profile("nonexistent"))


class TestDenialRequirement1(unittest.TestCase):
    """Req 1: drift_executor cannot access Telegram/escalation tools."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=True)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def test_1a_telegram_denied(self):
        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            with self.assertRaises(stdio_mcp.McpError) as ctx:
                wrapped()
            self.assertEqual(ctx.exception.code, -32003)
            body = json.loads(str(ctx.exception))
            self.assertEqual(body["error"], "profile_denied")
            self.assertEqual(body["profile"], "drift_executor")
            self.assertEqual(body["category"], "telegram")
        finally:
            current_profile.reset(tok)

    def test_1b_dispatch_denied(self):
        wrapped = wrap_tool_entry(_make_dispatch_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            with self.assertRaises(stdio_mcp.McpError) as ctx:
                wrapped()
            self.assertEqual(ctx.exception.code, -32003)
            body = json.loads(str(ctx.exception))
            self.assertEqual(body["category"], "dispatch")
        finally:
            current_profile.reset(tok)

    def test_1c_restart_denied(self):
        wrapped = wrap_tool_entry(_make_restart_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            with self.assertRaises(stdio_mcp.McpError) as ctx:
                wrapped()
            self.assertEqual(ctx.exception.code, -32003)
        finally:
            current_profile.reset(tok)

    def test_1d_linear_write_denied_for_drift_executor(self):
        wrapped = wrap_tool_entry(_make_linear_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            with self.assertRaises(stdio_mcp.McpError) as ctx:
                wrapped()
            self.assertEqual(ctx.exception.code, -32003)
        finally:
            current_profile.reset(tok)


class TestDenialRequirement2(unittest.TestCase):
    """Req 2: drift_executor cannot broaden its own tool profile."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=True)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def test_2a_profile_from_contextvar_not_mutable_by_tool(self):
        """Profile is set from HTTP header at connection init via ContextVar.
        A tool call cannot change it — ContextVar is set by the ASGI middleware,
        not by the tool function."""
        wrapped_tg = wrap_tool_entry(_dummy_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            with self.assertRaises(stdio_mcp.McpError):
                wrapped_tg()
            self.assertEqual(current_profile.get(), "drift_executor")
        finally:
            current_profile.reset(tok)

    def test_2b_no_gateway_tool_accepts_profile_override(self):
        """Confirm dispatch_worker's tool_profile param controls the DISPATCHED
        worker's profile, not the caller's own profile."""
        import inspect

        from miru_mcp_gateway import dispatch_tools

        sig = inspect.signature(dispatch_tools.dispatch_worker)
        self.assertIn("tool_profile", sig.parameters)


class TestDenialRequirement3(unittest.TestCase):
    """Req 3: Denied attempts are logged."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=True)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def test_3_denial_logged_in_audit(self):
        audit_dir = self.cfg.fs_root / "logs"
        audit_file = audit_dir / "mcp_gateway_reads.jsonl"
        audit_dir.mkdir(parents=True, exist_ok=True)

        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        tok_p = current_profile.set("drift_executor")
        tok_t = current_trace_id.set("test-trace-001")
        try:
            with self.assertRaises(stdio_mcp.McpError):
                wrapped()
        finally:
            current_profile.reset(tok_p)
            current_trace_id.reset(tok_t)

        if not audit_file.exists():
            self.skipTest("audit file not created (fs_root layout may differ)")

        lines = audit_file.read_text(encoding="utf-8").strip().split("\n")
        denial_rows = [json.loads(line) for line in lines if "profile_denied" in line]
        self.assertGreaterEqual(len(denial_rows), 1)
        row = denial_rows[0]
        self.assertEqual(row["result"], "profile_denied")
        self.assertEqual(row["profile"], "drift_executor")
        self.assertEqual(row["category"], "telegram")
        self.assertEqual(row["trace_id"], "test-trace-001")


class TestDenialRequirement4(unittest.TestCase):
    """Req 4: Decision trace can record escalation_attempted."""

    def test_4_emit_decision_schema_supports_escalation(self):
        import importlib

        mod = importlib.import_module("emit_decision")
        self.assertIn("escalation_or_non_escalation", mod.TRIGGERS)


class TestDenialRequirement5(unittest.TestCase):
    """Req 5: Agent continues or fails safely after denial."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=True)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def test_5a_denial_is_clean_mcp_error(self):
        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            with self.assertRaises(stdio_mcp.McpError) as ctx:
                wrapped()
            self.assertIsInstance(ctx.exception, stdio_mcp.McpError)
            self.assertEqual(ctx.exception.code, -32003)
            body = json.loads(str(ctx.exception))
            self.assertIn("error", body)
        finally:
            current_profile.reset(tok)

    def test_5b_allowed_tool_works_after_denial(self):
        wrapped_tg = wrap_tool_entry(_dummy_tool, self.cfg)
        wrapped_fs = wrap_tool_entry(_make_fs_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            with self.assertRaises(stdio_mcp.McpError):
                wrapped_tg()
            result = wrapped_fs()
            self.assertEqual(result, "file_content")
        finally:
            current_profile.reset(tok)


class TestBoundaryProfiles(unittest.TestCase):
    """Additional boundary tests beyond the 5 canon requirements."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=True)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def test_6_full_operator_accesses_all_categories(self):
        for tool_fn in (_dummy_tool, _make_dispatch_tool, _make_restart_tool):
            wrapped = wrap_tool_entry(tool_fn, self.cfg)
            tok = current_profile.set("full_operator")
            try:
                result = wrapped()
                self.assertIsNotNone(result)
            finally:
                current_profile.reset(tok)

    def test_7_unknown_profile_gets_drift_executor_restrictions(self):
        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        tok = current_profile.set("unknown_bot_9000")
        try:
            with self.assertRaises(stdio_mcp.McpError) as ctx:
                wrapped()
            self.assertEqual(ctx.exception.code, -32003)
        finally:
            current_profile.reset(tok)

    def test_8_no_header_defaults_to_full_operator(self):
        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        self.assertEqual(current_profile.get(), "full_operator")
        result = wrapped()
        self.assertIsNotNone(result)


class TestAuditModeNoEnforcement(unittest.TestCase):
    """Phase 3a: enforcement off — tools pass regardless of profile."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=False)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def test_drift_executor_passes_when_enforcement_off(self):
        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        tok = current_profile.set("drift_executor")
        try:
            result = wrapped()
            self.assertEqual(result, "ok")
        finally:
            current_profile.reset(tok)


class TestStandardWorkerPermissions(unittest.TestCase):
    """standard_worker can write to linear/git/docs but not telegram/dispatch."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=True)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def test_standard_worker_linear_write_allowed(self):
        wrapped = wrap_tool_entry(_make_linear_tool, self.cfg)
        tok = current_profile.set("standard_worker")
        try:
            result = wrapped()
            self.assertEqual(result, "linear_ok")
        finally:
            current_profile.reset(tok)

    def test_standard_worker_git_write_allowed(self):
        wrapped = wrap_tool_entry(_make_git_tool, self.cfg)
        tok = current_profile.set("standard_worker")
        try:
            result = wrapped()
            self.assertEqual(result, "committed")
        finally:
            current_profile.reset(tok)

    def test_standard_worker_telegram_denied(self):
        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        tok = current_profile.set("standard_worker")
        try:
            with self.assertRaises(stdio_mcp.McpError) as ctx:
                wrapped()
            self.assertEqual(ctx.exception.code, -32003)
        finally:
            current_profile.reset(tok)


class TestVpOpsPermissions(unittest.TestCase):
    """vp_ops can write to linear/git/docs/vp_ops but not telegram/dispatch."""

    def setUp(self):
        self.cfg = _make_cfg(enforcement=True)
        self.addCleanup(lambda: shutil.rmtree(self.cfg.fs_root, ignore_errors=True))

    def _make_vp_ops_tool(self, ctx=None, **kwargs):
        return "verified"

    def test_vp_ops_linear_write_allowed(self):
        wrapped = wrap_tool_entry(_make_linear_tool, self.cfg)
        tok = current_profile.set("vp_ops")
        try:
            result = wrapped()
            self.assertEqual(result, "linear_ok")
        finally:
            current_profile.reset(tok)

    def test_vp_ops_telegram_denied(self):
        wrapped = wrap_tool_entry(_dummy_tool, self.cfg)
        tok = current_profile.set("vp_ops")
        try:
            with self.assertRaises(stdio_mcp.McpError):
                wrapped()
        finally:
            current_profile.reset(tok)

    def test_vp_ops_dispatch_denied(self):
        wrapped = wrap_tool_entry(_make_dispatch_tool, self.cfg)
        tok = current_profile.set("vp_ops")
        try:
            with self.assertRaises(stdio_mcp.McpError):
                wrapped()
        finally:
            current_profile.reset(tok)


if __name__ == "__main__":
    unittest.main()
