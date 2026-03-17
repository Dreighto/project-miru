from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.miru_pushover import send_pushover_notification


class FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class MiruPushoverTests(unittest.TestCase):
    def test_send_pushover_notification_reports_success(self) -> None:
        env = {
            "PUSHOVER_USER_KEY": "user-key",
            "PUSHOVER_APP_TOKEN": "app-token",
            "PUSHOVER_ENABLED": "true",
            "PUSHOVER_DEFAULT_PRIORITY": "0",
        }
        with patch(
            "tools.miru_pushover.urlopen",
            return_value=FakeResponse(200, json.dumps({"status": 1, "request": "abc123"})),
        ) as mocked_urlopen:
            result = send_pushover_notification(
                title="Miru AI Test",
                message="Hello from Miru.",
                environ=env,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["response_json"]["status"], 1)
        mocked_urlopen.assert_called_once()

    def test_send_pushover_notification_reports_http_failure_body(self) -> None:
        env = {
            "PUSHOVER_USER_KEY": "user-key",
            "PUSHOVER_APP_TOKEN": "bad-token",
            "PUSHOVER_ENABLED": "true",
        }
        error_stream = io.BytesIO(b'{"status":0,"errors":["application token is invalid"]}')
        error = HTTPError(
            url="https://api.pushover.net/1/messages.json",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=error_stream,
        )
        with patch("tools.miru_pushover.urlopen", side_effect=error):
            result = send_pushover_notification(
                title="Miru AI Test",
                message="Hello from Miru.",
                environ=env,
            )
        error_stream.close()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status_code"], 401)
        self.assertIn("HTTPError", result["error"])
        self.assertEqual(result["response_json"]["status"], 0)


if __name__ == "__main__":
    unittest.main()
