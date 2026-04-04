from __future__ import annotations

import unittest

import miru_ai.server as canonical_server
import miru_ai.workers.learning_engine as canonical_learning_engine
import shared.env as canonical_env
import tools.miru_ai_server as compat_server
import tools.miru_env as compat_env
import tools.miru_learning_engine as compat_learning_engine


class MiruAiBoundaryTests(unittest.TestCase):
    def test_tools_wrappers_resolve_to_canonical_modules(self) -> None:
        self.assertIs(compat_server, canonical_server)
        self.assertIs(compat_learning_engine, canonical_learning_engine)
        self.assertIs(compat_env, canonical_env)

    def test_canonical_server_uses_miru_ai_asset_paths(self) -> None:
        self.assertIn("\\miru_ai\\static\\miru_ai.css", str(canonical_server.CSS_PATH))
        self.assertIn("\\miru_ai\\templates\\miru_ai.html", str(canonical_server.TEMPLATE_PATH))
        self.assertIn("\\miru_ai\\core\\ai.py", str(canonical_server.SCRIPT_PATH))


if __name__ == "__main__":
    unittest.main()
