from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.miru_env import (
    build_pushover_status_message,
    inspect_pushover_env,
    load_project_env,
)


class MiruEnvTests(unittest.TestCase):
    def test_load_project_env_reads_local_dotenv_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "PUSHOVER_USER_KEY=test-user",
                        "PUSHOVER_APP_TOKEN=test-token",
                        "PUSHOVER_ENABLED=true",
                        "PUSHOVER_DEFAULT_PRIORITY=0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            target_env: dict[str, str] = {}

            load_result = load_project_env(env_path=env_path, environ=target_env)
            pushover = inspect_pushover_env(environ=target_env)

            self.assertTrue(load_result["exists"])
            self.assertIn("PUSHOVER_USER_KEY", load_result["available_keys"])
            self.assertEqual(target_env["PUSHOVER_APP_TOKEN"], "test-token")
            self.assertTrue(pushover["enabled"])
            self.assertTrue(pushover["configured"])

    def test_build_pushover_status_message_warns_when_enabled_keys_missing(self) -> None:
        target_env = {
            "PUSHOVER_ENABLED": "true",
        }

        message = build_pushover_status_message(
            env_load={
                "exists": True,
                "env_path": "C:/repo/.env",
            },
            pushover=inspect_pushover_env(environ=target_env),
        )

        self.assertIn("missing required keys", message)
        self.assertIn("PUSHOVER_USER_KEY", message)
        self.assertIn("PUSHOVER_APP_TOKEN", message)

    def test_load_project_env_does_not_override_existing_process_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "PUSHOVER_USER_KEY=from-file\nPUSHOVER_APP_TOKEN=from-file\n",
                encoding="utf-8",
            )
            target_env = {
                "PUSHOVER_USER_KEY": "from-process",
            }

            load_project_env(env_path=env_path, environ=target_env, override=False)

            self.assertEqual(target_env["PUSHOVER_USER_KEY"], "from-process")
            self.assertEqual(target_env["PUSHOVER_APP_TOKEN"], "from-file")


if __name__ == "__main__":
    unittest.main()
