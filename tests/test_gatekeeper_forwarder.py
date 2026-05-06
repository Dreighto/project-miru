"""Tests for gatekeeper.forwarder — trace ID minting, prompt file writing, HMAC signing.

Covers: mint_trace_id() format, write_prompt_file() including path-traversal guard,
_sign() HMAC computation, ForwarderError structure, ALLOWLISTED_WORKERS constants.
HTTP forward() is tested with mocked urllib to avoid needing a live listener.

PRO-305 — Gatekeeper test coverage.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gatekeeper.forwarder import (
    _RESPONSE_REASON_MAP,
    _TRACE_ID_FILENAME_RE,
    ALLOWLISTED_WORKERS,
    ForwarderError,
    _sign,
    forward,
    mint_trace_id,
    write_prompt_file,
)

# ---------------------------------------------------------------------------
# mint_trace_id()
# ---------------------------------------------------------------------------


class TestMintTraceId(unittest.TestCase):
    def test_format_matches_pattern(self):
        tid = mint_trace_id("PRO-305")
        self.assertTrue(tid.startswith("rtr-PRO-305-"))
        self.assertTrue(_TRACE_ID_FILENAME_RE.match(tid))

    def test_random_suffix_is_16_hex(self):
        tid = mint_trace_id("PRO-100")
        suffix = tid.split("-", 3)[-1]
        self.assertEqual(len(suffix), 16)
        int(suffix, 16)

    def test_uniqueness(self):
        ids = {mint_trace_id("PRO-1") for _ in range(50)}
        self.assertEqual(len(ids), 50, "50 minted trace_ids should all be unique")

    def test_length_within_bounds(self):
        tid = mint_trace_id("PRO-305")
        self.assertGreaterEqual(len(tid), 6)
        self.assertLessEqual(len(tid), 128)

    def test_safe_characters_only(self):
        tid = mint_trace_id("NAS-42")
        self.assertTrue(
            all(c.isalnum() or c in "_-" for c in tid),
            f"trace_id contains unsafe characters: {tid!r}",
        )


# ---------------------------------------------------------------------------
# _TRACE_ID_FILENAME_RE — regex coverage
# ---------------------------------------------------------------------------


class TestTraceIdRegex(unittest.TestCase):
    def test_valid_ids(self):
        for tid in [
            "rtr-PRO-305-abcdef0123456789",
            "a" * 6,
            "A" * 128,
            "rtr-NAS-1-deadbeefcafe1234",
            "simple_trace-id",
        ]:
            self.assertIsNotNone(_TRACE_ID_FILENAME_RE.match(tid), f"should match: {tid!r}")

    def test_invalid_ids(self):
        for tid in [
            "",
            "short",
            "a" * 5,
            "a" * 129,
            "has spaces here",
            "path/../traversal",
            "has/slash",
            "has.dot.here",
            "has@symbol",
        ]:
            self.assertIsNone(_TRACE_ID_FILENAME_RE.match(tid), f"should NOT match: {tid!r}")


# ---------------------------------------------------------------------------
# write_prompt_file()
# ---------------------------------------------------------------------------


class TestWritePromptFile(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="gk_test_")
        self._inbox = Path(self._tmpdir) / "data" / "n8n_inbox"
        self._patcher = patch("gatekeeper.forwarder.INBOX_DIR", self._inbox)
        self._patcher.start()

    def tearDown(self):
        self._patcher.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_writes_valid_json(self):
        tid = "rtr-PRO-305-abcdef0123456789"
        path = write_prompt_file(tid, "Hello, worker!")
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["prompt"], "Hello, worker!")

    def test_filename_matches_trace_id(self):
        tid = "rtr-PRO-100-0000111122223333"
        path = write_prompt_file(tid, "test prompt")
        self.assertEqual(path.name, f"{tid}.prompt.json")

    def test_creates_inbox_dir(self):
        self.assertFalse(self._inbox.exists())
        write_prompt_file("rtr-PRO-1-aaaaaaaaaaaaaaaa", "x")
        self.assertTrue(self._inbox.exists())

    def test_rejects_path_traversal_in_trace_id(self):
        with self.assertRaises(ValueError):
            write_prompt_file("../../etc/passwd-aaaaaa", "malicious")

    def test_rejects_short_trace_id(self):
        with self.assertRaises(ValueError):
            write_prompt_file("short", "x")

    def test_rejects_trace_id_with_slashes(self):
        with self.assertRaises(ValueError):
            write_prompt_file("rtr/PRO/305/abcdef0123456789", "x")

    def test_rejects_trace_id_with_dots(self):
        with self.assertRaises(ValueError):
            write_prompt_file("rtr.PRO.305.abcdef0123456789", "x")

    def test_unicode_prompt_text_preserved(self):
        tid = "rtr-PRO-305-abcdef0123456789"
        path = write_prompt_file(tid, "Unicode test: ☃ ❤ ✨")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("☃", data["prompt"])


# ---------------------------------------------------------------------------
# _sign() — HMAC computation
# ---------------------------------------------------------------------------


class TestSign(unittest.TestCase):
    def test_produces_hex_digest(self):
        with patch.dict(os.environ, {"W4_LISTENER_HMAC_SECRET": "test-secret"}):
            sig = _sign(b'{"test": true}')
            int(sig, 16)
            self.assertEqual(len(sig), 64)

    def test_matches_manual_hmac(self):
        secret = "my-test-key"
        body = b'{"worker":"claude-code"}'
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        with patch.dict(os.environ, {"W4_LISTENER_HMAC_SECRET": secret}):
            self.assertEqual(_sign(body), expected)

    def test_different_bodies_different_sigs(self):
        with patch.dict(os.environ, {"W4_LISTENER_HMAC_SECRET": "key"}):
            sig_a = _sign(b"body_a")
            sig_b = _sign(b"body_b")
            self.assertNotEqual(sig_a, sig_b)

    def test_missing_secret_raises_forwarder_error(self):
        env = os.environ.copy()
        env.pop("W4_LISTENER_HMAC_SECRET", None)
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ForwarderError) as ctx:
                _sign(b"test")
            self.assertEqual(ctx.exception.reason, "hmac_secret_missing")


# ---------------------------------------------------------------------------
# ForwarderError structure
# ---------------------------------------------------------------------------


class TestForwarderError(unittest.TestCase):
    def test_basic_attributes(self):
        err = ForwarderError("test_reason", "detail text", listener_status=503)
        self.assertEqual(err.reason, "test_reason")
        self.assertEqual(err.detail, "detail text")
        self.assertEqual(err.listener_status, 503)
        self.assertIsNone(err.listener_body)

    def test_str_format(self):
        err = ForwarderError("backpressure_no_slot", "all slots busy")
        self.assertIn("backpressure_no_slot", str(err))
        self.assertIn("all slots busy", str(err))

    def test_reason_only(self):
        err = ForwarderError("listener_unreachable")
        self.assertEqual(str(err), "listener_unreachable")
        self.assertEqual(err.detail, "")

    def test_is_exception(self):
        self.assertTrue(issubclass(ForwarderError, Exception))

    def test_all_fields(self):
        err = ForwarderError(
            "dispatch_failed",
            "internal error",
            listener_status=500,
            listener_body='{"error":"oops"}',
        )
        self.assertEqual(err.listener_body, '{"error":"oops"}')


# ---------------------------------------------------------------------------
# ALLOWLISTED_WORKERS
# ---------------------------------------------------------------------------


class TestAllowlistedWorkers(unittest.TestCase):
    def test_contains_claude_code(self):
        self.assertIn("claude-code", ALLOWLISTED_WORKERS)

    def test_contains_gemini(self):
        self.assertIn("gemini", ALLOWLISTED_WORKERS)

    def test_is_tuple(self):
        self.assertIsInstance(ALLOWLISTED_WORKERS, tuple)


# ---------------------------------------------------------------------------
# _RESPONSE_REASON_MAP
# ---------------------------------------------------------------------------


class TestResponseReasonMap(unittest.TestCase):
    def test_known_status_codes(self):
        self.assertEqual(_RESPONSE_REASON_MAP[400], "dispatch_failed_bad_request")
        self.assertEqual(_RESPONSE_REASON_MAP[401], "dispatch_failed_hmac_reject")
        self.assertEqual(_RESPONSE_REASON_MAP[403], "worker_not_allowlisted")
        self.assertEqual(_RESPONSE_REASON_MAP[409], "duplicate_dispatch")
        self.assertEqual(_RESPONSE_REASON_MAP[500], "dispatch_failed")
        self.assertEqual(_RESPONSE_REASON_MAP[503], "backpressure_no_slot")


# ---------------------------------------------------------------------------
# forward() — mocked HTTP
# ---------------------------------------------------------------------------


class TestForward(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="gk_fwd_")
        self._inbox = Path(self._tmpdir) / "data" / "n8n_inbox"
        self._patchers = [
            patch("gatekeeper.forwarder.INBOX_DIR", self._inbox),
            patch("gatekeeper.forwarder.REPO_ROOT", Path(self._tmpdir)),
            patch.dict(os.environ, {"W4_LISTENER_HMAC_SECRET": "test-key"}),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @patch("gatekeeper.forwarder.urllib.request.urlopen")
    def test_success_returns_parsed_json(self, mock_urlopen):
        resp_body = json.dumps(
            {"trace_id": "rtr-PRO-1-abc", "status": "spawned", "spawned_at": "2026-05-06T00:00:00Z"}
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_body
        mock_resp.status = 202
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = forward(
            trace_id="rtr-PRO-1-abcdef0123456789",
            worker="claude-code",
            prompt_text="Test prompt",
        )
        self.assertEqual(result["status"], "spawned")
        self.assertEqual(result["trace_id"], "rtr-PRO-1-abc")

    @patch("gatekeeper.forwarder.urllib.request.urlopen")
    def test_sends_hmac_header(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status":"spawned"}'
        mock_resp.status = 202
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        forward(
            trace_id="rtr-PRO-2-abcdef0123456789",
            worker="gemini",
            prompt_text="Another test",
        )

        call_args = mock_urlopen.call_args
        request = call_args[0][0]
        # urllib.request.Request lowercases custom header names internally
        self.assertIn("X-w4-hmac", request.headers)
        sig = request.headers["X-w4-hmac"]
        self.assertEqual(len(sig), 64)

    @patch("gatekeeper.forwarder.urllib.request.urlopen")
    def test_prompt_file_created(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status":"spawned"}'
        mock_resp.status = 202
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        tid = "rtr-PRO-3-abcdef0123456789"
        forward(trace_id=tid, worker="claude-code", prompt_text="Check this")

        prompt_path = self._inbox / f"{tid}.prompt.json"
        self.assertTrue(prompt_path.exists())

    @patch("gatekeeper.forwarder.LISTENER_URL", "ftp://evil.host/dispatch")
    def test_rejects_non_http_listener_url(self):
        with self.assertRaises(ForwarderError) as ctx:
            forward(
                trace_id="rtr-PRO-4-abcdef0123456789",
                worker="claude-code",
                prompt_text="test",
            )
        self.assertEqual(ctx.exception.reason, "listener_url_invalid_scheme")

    @patch("gatekeeper.forwarder.urllib.request.urlopen")
    def test_http_error_maps_to_reason(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://localhost:19100/dispatch",
            code=503,
            msg="Service Unavailable",
            hdrs={},
            fp=MagicMock(read=MagicMock(return_value=b"busy")),
        )

        with self.assertRaises(ForwarderError) as ctx:
            forward(
                trace_id="rtr-PRO-5-abcdef0123456789",
                worker="claude-code",
                prompt_text="test",
            )
        self.assertEqual(ctx.exception.reason, "backpressure_no_slot")
        self.assertEqual(ctx.exception.listener_status, 503)

    @patch("gatekeeper.forwarder.urllib.request.urlopen")
    def test_url_error_maps_to_unreachable(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        with self.assertRaises(ForwarderError) as ctx:
            forward(
                trace_id="rtr-PRO-6-abcdef0123456789",
                worker="claude-code",
                prompt_text="test",
            )
        self.assertEqual(ctx.exception.reason, "listener_unreachable")

    @patch("gatekeeper.forwarder.urllib.request.urlopen")
    def test_timeout_maps_to_timeout(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("timed out")

        with self.assertRaises(ForwarderError) as ctx:
            forward(
                trace_id="rtr-PRO-7-abcdef0123456789",
                worker="claude-code",
                prompt_text="test",
            )
        self.assertEqual(ctx.exception.reason, "listener_timeout")


if __name__ == "__main__":
    unittest.main()
