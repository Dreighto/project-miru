from __future__ import annotations

from contextlib import closing
import os
import sqlite3
import subprocess
import tempfile
import textwrap
import unittest
import uuid
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import tools.miru_ai_server as server
import tools.miru_learning_engine as learning_engine
from tools.miru_ai_onepiece import initialize_fallback_catalog_db, inspect_fallback_catalog_db


class MiruAiServerTests(unittest.TestCase):
    HARNESS_ROOT = Path(__file__).resolve().parent / "_tmp"

    def create_client(self):
        app = server.create_app()
        app.config["TESTING"] = True
        return app.test_client()

    def write_codex_harness(self, path: Path) -> Path:
        repo_root = Path(__file__).resolve().parent.parent
        harness_path = Path(path)
        harness_path.write_text(
            textwrap.dedent(
                f"""
                import os
                import sys
                from pathlib import Path

                sys.path.insert(0, {str(repo_root)!r})
                os.environ["OPENAI_API_KEY"] = "test-key"

                import tools.miru_ai as miru_ai

                miru_ai.make_request = lambda api_key, model, timeout, payload: {{
                    "output_text": "Objective\\nFix promo matching for OP09 alt-art cards.\\n\\nAssumptions\\n- OP09 alt-art references can be ambiguous.\\n\\nImplementation outline\\n- Normalize promo and alt markers.\\n- Keep official-cardlist data authoritative.\\n\\nVerification\\n- Check OP09 alt and promo lookups.\\n"
                }}

                if __name__ == "__main__":
                    miru_ai.main()
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )
        return harness_path

    def test_homepage_is_minimal_product_landing_page(self) -> None:
        client = self.create_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Miru AI", html)
        self.assertIn("A One Piece Card Intelligence System", html)
        self.assertIn('/static/icons/miru-fruit.png', html)
        self.assertIn("answers from verified card knowledge, not unsourced guesses", html)
        self.assertIn("Ask Miru", html)
        self.assertIn('data-run-url="/api/run"', html)
        self.assertIn('action="/api/run"', html)
        self.assertIn('runUrl: "/api/run"', html)
        self.assertNotIn('/api/run/"', html)
        self.assertNotIn('"api/run"', html)
        self.assertIn("View Dossiers", html)
        self.assertIn("Knowledge gaps", html)
        self.assertNotIn('id="miruForm"', html)
        self.assertNotIn("Plan a Feature", html)
        self.assertNotIn("Debug a Bug", html)
        self.assertNotIn("Review a File", html)
        self.assertNotIn("Draft a Codex Prompt", html)

    def test_ask_page_renders_question_ui_and_copy_controls(self) -> None:
        client = self.create_client()
        response = client.get("/ask")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('data-run-url="/api/run"', html)
        self.assertIn('action="/api/run"', html)
        self.assertIn('runUrl: "/api/run"', html)
        self.assertNotIn('/api/run/"', html)
        self.assertNotIn('"api/run"', html)
        self.assertIn('id="miruForm"', html)
        self.assertIn("Paste Question", html)
        self.assertIn("Copy Result", html)
        self.assertIn("Select Result", html)
        self.assertIn("Card Lookup", html)
        self.assertIn("What is OP09-001?", html)
        self.assertIn("What facts are still missing for P-088?", html)

    def test_secondary_pages_render_reorganized_content(self) -> None:
        client = self.create_client()
        expectations = {
            "/dossiers": ["Structured card knowledge", "Resolve the exact card first"],
            "/gaps": ["What Miru still needs to learn", "Keep unknowns visible"],
            "/training": ["How Miru is growing", "Training progress", "Verified dossiers vs catalog", "Miru Intelligence Progress", "Mini voyage track", "/static/icons/miru_voyage/"],
            "/status": ["Miru sidecar status", "Knowledge routes"],
            "/dev": ["Miru Dev Monitor", "What Miru is doing right now", "Training progress", "Verified dossiers vs catalog", "Learning engine", "Queue and sidecar throughput", "Why canonical card values were accepted", "Machine load right now", "System Health", "Pushover", "Local time updates in your browser.", "Voyage map", "Captain's log", "voyageMapCanvas--dev", "/static/icons/miru_voyage/"],
        }
        for path, checks in expectations.items():
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            html = response.get_data(as_text=True)
            for expected in checks:
                self.assertIn(expected, html)

    def test_brand_assets_use_real_miru_logo_asset(self) -> None:
        client = self.create_client()
        response = client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn('/static/icons/miru-fruit.png', html)
        self.assertNotIn("logoGlyph", html)

    def test_api_training_status_reports_progress_metrics(self) -> None:
        knowledge_path = Path(__file__).resolve().parent.parent / "data" / "miru_ai_onepiece_knowledge.json"
        self.HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
        catalog_db = self.HARNESS_ROOT / f"card_catalog_{uuid.uuid4().hex}.db"
        dossiers_db = self.HARNESS_ROOT / f"miru_dossiers_{uuid.uuid4().hex}.db"
        try:
            initialize_fallback_catalog_db(db_path=catalog_db, cache_path=knowledge_path)

            with closing(sqlite3.connect(dossiers_db)) as conn:
                conn.executescript(
                    """
                    CREATE TABLE cards (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        canonical_code TEXT NOT NULL UNIQUE,
                        overall_state TEXT
                    );
                    INSERT INTO cards (canonical_code, overall_state) VALUES
                        ('OP01-001', 'verified'),
                        ('OP01-002', 'draft'),
                        ('OP01-003', 'verified');
                    """
                )

            with patch.object(server, "FALLBACK_CATALOG_DB_PATH", catalog_db), patch.object(server, "DOSSIER_DB_PATH", dossiers_db):
                client = self.create_client()
                response = client.get("/api/training-status")
        finally:
            catalog_db.unlink(missing_ok=True)
            dossiers_db.unlink(missing_ok=True)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertGreater(payload["total_cards"], 0)
        self.assertEqual(payload["dossiers_created"], 3)
        self.assertEqual(payload["verified_dossiers"], 2)
        self.assertEqual(payload["remaining_gaps"], payload["total_cards"] - 2)
        self.assertEqual(payload["progress_percent"], payload["verified_coverage_percent"])
        self.assertEqual(payload["catalog_coverage_percent"], 100.0)
        self.assertEqual(payload["training_stage"], "verification_expanding")
        self.assertIn("intelligence_progress", payload)
        self.assertEqual(payload["intelligence_progress"]["current_stage"]["label"], "Card Understanding")
        self.assertEqual(payload["intelligence_progress"]["current_stage"]["voyage_arc"], "Alabasta")
        self.assertIn("voyage", payload)
        self.assertIn("current_island", payload["voyage"])
        self.assertIn("next_boss", payload["voyage"])
        self.assertIn("recent_log", payload["voyage"])
        self.assertIn("route_nodes", payload["voyage"])
        self.assertIn("route_polyline", payload["voyage"])
        self.assertIn("ship_position", payload["voyage"])
        self.assertTrue(str(payload["voyage"]["current_island"]["sprite_url"]).endswith(".png"))

    def test_api_health_reports_routes(self) -> None:
        client = self.create_client()
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["app_name"], "Miru AI")
        self.assertIn("/ask", payload["pages"])
        self.assertIn("/training", payload["pages"])
        self.assertIn("/dev", payload["pages"])
        self.assertIn("runtime_dependencies", payload)
        self.assertIn("runtime_issues", payload)
        self.assertIn("fallback_catalog", payload)
        self.assertTrue(payload["fallback_catalog"]["exists"])
        self.assertTrue(payload["fallback_catalog"]["openable"])
        self.assertGreater(payload["fallback_catalog"]["cards"], 0)

    def test_training_and_dev_pages_render_even_if_voyage_data_is_missing(self) -> None:
        app = server.create_app()
        app.config["TESTING"] = True
        with app.test_request_context("/training"):
            base_status = server.build_training_status()
            base_status = dict(base_status)
            base_status.pop("voyage", None)

        with patch.object(server, "build_training_status", return_value=base_status):
            client = app.test_client()
            training_response = client.get("/training")
            dev_response = client.get("/dev")

        self.assertEqual(training_response.status_code, 200)
        self.assertEqual(dev_response.status_code, 200)
        self.assertIn("Miru Intelligence Progress", training_response.get_data(as_text=True))
        self.assertIn("Voyage map", dev_response.get_data(as_text=True))

    def test_api_dev_status_reports_monitor_payload(self) -> None:
        self.HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
        queue_db = self.HARNESS_ROOT / f"miru_learning_queue_{uuid.uuid4().hex}.db"
        status_db = self.HARNESS_ROOT / f"miru_learning_log_{uuid.uuid4().hex}.db"
        dossier_db = self.HARNESS_ROOT / f"miru_learning_dossiers_{uuid.uuid4().hex}.db"
        engine = learning_engine.MiruLearningEngine(
            queue_db_path=queue_db,
            status_db_path=status_db,
            dossier_db_path=dossier_db,
            catalog_db_path=server.FALLBACK_CATALOG_DB_PATH,
            knowledge_cache_path=server.KNOWLEDGE_CACHE_PATH,
            sleep_seconds=0.1,
        )
        try:
            engine.ensure_datastores()
            engine.enqueue_task(
                card_code="OP01-001",
                task_type="verify_official_fields",
                source_id="official-cardlist",
                task_payload={"snapshot_path": "tests/fixtures/miru_official_cardlist_sample.json"},
            )
            engine.process_one()
            with patch.object(server, "LEARNING_QUEUE_DB_PATH", queue_db), patch.object(server, "LEARNING_STATUS_DB_PATH", status_db), patch.object(server, "LEARNING_DOSSIER_DB_PATH", dossier_db):
                client = self.create_client()
                response = client.get("/api/dev-status")
        finally:
            queue_db.unlink(missing_ok=True)
            status_db.unlink(missing_ok=True)
            dossier_db.unlink(missing_ok=True)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("activity", payload)
        self.assertIn("training", payload)
        self.assertIn("voyage", payload)
        self.assertIn("learning_metrics", payload)
        self.assertIn("resource_metrics", payload)
        self.assertIn("issues", payload)
        self.assertIn("links", payload)
        self.assertIn("learning_engine", payload)
        self.assertIn("image_coverage_by_set", payload)
        self.assertIn("validation_audit", payload)
        self.assertIn("validation_audit_url_base", payload)
        self.assertIn("pushover", payload)
        self.assertIn("project_miru", payload["links"])
        self.assertTrue(any(item["key"] == "cpu" for item in payload["resource_metrics"]))
        self.assertIn("miru_ai", payload["issues"])
        self.assertIn("project_miru", payload["issues"])
        self.assertIn("current_island", payload["voyage"])
        self.assertIn("next_island", payload["voyage"])
        self.assertIn("next_boss", payload["voyage"])
        self.assertIn("route_nodes", payload["voyage"])
        self.assertIn("recent_log", payload["voyage"])
        self.assertIn("route_polyline", payload["voyage"])
        self.assertIn("ship_position", payload["voyage"])
        self.assertIn("assets", payload["voyage"])
        self.assertEqual(payload["training"]["progress_percent"], payload["voyage"]["progress_percent"])
        self.assertEqual(payload["learning_engine"]["processed_count"], 1)
        self.assertEqual(payload["learning_engine"]["success_count"], 1)
        self.assertEqual(payload["learning_engine"]["source_success_count"], 1)
        self.assertEqual(payload["learning_engine"]["dossier_count"], 1)
        self.assertEqual(payload["learning_engine"]["last_source_id"], "official-cardlist")
        self.assertIn("images_tracked", payload["learning_engine"])
        self.assertIn("images_verified", payload["learning_engine"])
        self.assertIn("images_missing", payload["learning_engine"])
        self.assertIn("image_success_count", payload["learning_engine"])
        self.assertIn("image_error_count", payload["learning_engine"])
        self.assertGreaterEqual(payload["learning_engine"]["images_tracked"], 0)
        self.assertGreaterEqual(payload["learning_engine"]["images_verified"], 0)
        self.assertIn("recently_validated", payload["validation_audit"])
        self.assertIn("env_path", payload["pushover"])
        self.assertIn("server_script_path", payload["pushover"])
        self.assertIn("project_root", payload["pushover"])
        self.assertIn("test_endpoint", payload["pushover"])
        self.assertIsInstance(payload["image_coverage_by_set"], list)
        if payload["image_coverage_by_set"]:
            sample = payload["image_coverage_by_set"][0]
            self.assertIn("set_code", sample)
            self.assertIn("total_cards", sample)
            self.assertIn("images_tracked", sample)
            self.assertIn("images_verified", sample)
            self.assertIn("images_missing", sample)
            self.assertIn("coverage_percent", sample)
            self.assertIn("milestone_stage", sample)
            self.assertIn("milestone_label", sample)
        self.assertIn(payload["activity"]["key"], {"setting_sail", "gathering_crew"})

    def test_api_dev_card_validation_returns_audit_payload(self) -> None:
        self.HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
        queue_db = self.HARNESS_ROOT / f"miru_learning_queue_{uuid.uuid4().hex}.db"
        status_db = self.HARNESS_ROOT / f"miru_learning_log_{uuid.uuid4().hex}.db"
        dossier_db = self.HARNESS_ROOT / f"miru_learning_dossiers_{uuid.uuid4().hex}.db"
        catalog_db = self.HARNESS_ROOT / f"card_catalog_{uuid.uuid4().hex}.db"
        engine = learning_engine.MiruLearningEngine(
            queue_db_path=queue_db,
            status_db_path=status_db,
            dossier_db_path=dossier_db,
            project_db_path=catalog_db,
            catalog_db_path=catalog_db,
            knowledge_cache_path=server.KNOWLEDGE_CACHE_PATH,
            sleep_seconds=0.1,
        )
        try:
            engine.ensure_datastores()
            engine.enqueue_task(
                card_code="OP01-001",
                task_type="verify_official_fields",
                source_id="official-cardlist",
                task_payload={"snapshot_path": "tests/fixtures/miru_official_cardlist_sample.json"},
            )
            engine.process_one()
            with patch.object(server, "FALLBACK_CATALOG_DB_PATH", catalog_db):
                client = self.create_client()
                response = client.get("/api/dev/card-validation/OP01-001")
        finally:
            queue_db.unlink(missing_ok=True)
            status_db.unlink(missing_ok=True)
            dossier_db.unlink(missing_ok=True)
            catalog_db.unlink(missing_ok=True)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["card_code"], "OP01-001")
        self.assertIn("audit", payload)
        self.assertEqual(payload["audit"]["winning_source"]["source_id"], "official-cardlist")
        self.assertIn("confidence_reason", payload["audit"])
        self.assertIn("conflict_summary", payload["audit"])

    def test_api_dev_test_pushover_uses_sender_and_returns_result(self) -> None:
        client = self.create_client()
        with patch.object(
            server,
            "send_pushover_notification",
            return_value={
                "ok": True,
                "enabled": True,
                "configured": True,
                "missing_required_keys": [],
                "endpoint": "https://api.pushover.net/1/messages.json",
                "status_code": 200,
                "response_json": {"status": 1, "request": "abc123"},
                "response_text": "{\"status\":1}",
                "error": "",
            },
        ) as mocked_send:
            response = client.post(
                "/api/dev/test-pushover",
                json={"title": "Unit Test", "message": "Miru test ping."},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["send_result"]["status_code"], 200)
        self.assertEqual(payload["send_result"]["response_json"]["status"], 1)
        mocked_send.assert_called_once()

    def test_legacy_dev_routes_resolve_without_server_error(self) -> None:
        client = self.create_client()

        response_status = client.get("/api/dev/status")
        response_usage = client.get("/api/dev/usage")
        response_validation = client.get("/api/dev/validation_insights")
        response_root_status = client.get("/dev-status")

        self.assertEqual(response_status.status_code, 200)
        self.assertEqual(response_usage.status_code, 200)
        self.assertEqual(response_validation.status_code, 200)
        self.assertEqual(response_root_status.status_code, 200)
        self.assertIn("updated_at", response_status.get_json())
        self.assertTrue(response_usage.get_json()["ok"])
        self.assertTrue(response_validation.get_json()["ok"])

    def test_missing_route_returns_404_instead_of_server_fallback(self) -> None:
        client = self.create_client()

        api_response = client.get("/api/does-not-exist")
        page_response = client.get("/favicon.ico")

        self.assertEqual(api_response.status_code, 404)
        self.assertTrue(api_response.get_json()["error"].startswith("404 Not Found"))
        self.assertEqual(page_response.status_code, 404)

    def test_dev_launcher_script_exists_and_prints_expected_urls(self) -> None:
        script_path = Path(__file__).resolve().parent.parent / "run_miru_dev.ps1"
        self.assertTrue(script_path.is_file())
        content = script_path.read_text(encoding="utf-8")
        self.assertIn("Miru Dev Launcher", content)
        self.assertIn("Dev Monitor URL", content)
        self.assertIn("LAN Dev Monitor URL", content)
        self.assertIn(r"python tools\miru_ai_server.py --host $BindHost --port $Port", content)

    def test_initialize_fallback_catalog_db_populates_local_snapshot(self) -> None:
        knowledge_path = Path(__file__).resolve().parent.parent / "data" / "miru_ai_onepiece_knowledge.json"
        self.HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
        db_path = self.HARNESS_ROOT / f"card_catalog_test_{uuid.uuid4().hex}.db"
        try:
            status = initialize_fallback_catalog_db(db_path=db_path, cache_path=knowledge_path)
            inspected = inspect_fallback_catalog_db(db_path)
        finally:
            db_path.unlink(missing_ok=True)

        self.assertTrue(status["exists"])
        self.assertTrue(status["openable"])
        self.assertTrue(status["usable"])
        self.assertGreater(status["cards"], 0)
        self.assertGreater(status["variants"], 0)
        self.assertGreater(status["sets"], 0)
        self.assertTrue(inspected["openable"])

    def test_api_run_codex_prompt_returns_codex_structure(self) -> None:
        self.HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
        harness_path = self.HARNESS_ROOT / f"miru_ai_codex_harness_{uuid.uuid4().hex}.py"
        try:
            harness_path = self.write_codex_harness(harness_path)
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                with patch.object(server, "SCRIPT_PATH", harness_path):
                    client = self.create_client()
                    response = client.post(
                        "/api/run",
                        json={
                            "mode": "codex prompt",
                            "request_text": "write a codex prompt to improve promo matching for OP09 alt-art cards",
                            "file_path": "",
                        },
                    )
        finally:
            harness_path.unlink(missing_ok=True)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "codex prompt")
        self.assertEqual(payload["cli_mode"], "codex-prompt")
        self.assertIn("codex-prompt", payload["command"])
        self.assertIn("Codex implementation prompt", payload["output"])

    def test_api_validation_returns_meaningful_error(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
            client = self.create_client()
            response = client.post(
                "/api/run",
                data={
                    "mode": "review",
                    "request_text": "",
                    "file_path": "",
                },
            )

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertIn("Review needs a readable file path.", payload["error"])

    def test_api_run_card_knowledge_returns_structured_understanding(self) -> None:
        client = self.create_client()
        response = client.post(
            "/api/run",
            json={
                "mode": "card knowledge",
                "request_text": "Explain card OP09-001",
                "file_path": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cli_mode"], "knowledge")
        self.assertIn("OPTCG understanding", payload["output"])
        self.assertIn("OP09-001", payload["output"])

    def test_api_run_trailing_slash_alias_returns_same_payload_shape(self) -> None:
        client = self.create_client()
        response = client.post(
            "/api/run/",
            json={
                "mode": "card knowledge",
                "request_text": "Explain card OP09-001",
                "file_path": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cli_mode"], "knowledge")
        self.assertIn("OP09-001", payload["output"])

    def test_api_run_stale_codex_mode_downgrades_card_metadata_question(self) -> None:
        client = self.create_client()
        response = client.post(
            "/api/run",
            json={
                "mode": "codex prompt",
                "request_text": "who drew OP04-061",
                "file_path": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["requested_mode"], "codex prompt")
        self.assertEqual(payload["mode"], "card lookup")
        self.assertEqual(payload["cli_mode"], "knowledge")
        self.assertIn("Artist credit:", payload["output"])
        self.assertNotIn("Codex implementation prompt", payload["output"])

    def test_run_miru_ai_invokes_codex_prompt_subprocess(self) -> None:
        with patch("tools.miru_ai_server.subprocess.run") as run_mock:
            run_mock.return_value = CompletedProcess(
                args=["python", "tools/miru_ai.py", "codex-prompt", "request"],
                returncode=0,
                stdout="OP Miru Codex prompt\n====================\nCodex implementation prompt\n\nGoal\nTest\n",
                stderr="",
            )
            ok, output = server.run_miru_ai("codex prompt", "request", "")

        self.assertTrue(ok)
        called_command = run_mock.call_args.args[0]
        self.assertEqual(called_command[2], "codex-prompt")
        self.assertIn("Codex implementation prompt", output)

    def test_js_copy_and_paste_hooks_have_working_fallbacks(self) -> None:
        js_path = (Path(__file__).resolve().parent.parent / "tools" / "static" / "miru_ai.js").as_posix()
        node_script = textwrap.dedent(
            f"""
            const fs = require('fs');
            const vm = require('vm');
            const code = fs.readFileSync('{js_path}', 'utf8');
            global.window = {{ matchMedia: () => ({{ matches: false }}) }};
            global.navigator = {{}};
            global.setTimeout = (fn) => 1;
            global.clearTimeout = () => {{}};
            global.document = {{
                getElementById: () => null,
                createElement: () => ({{
                    value: '',
                    style: {{}},
                    setAttribute: () => {{}},
                    focus: () => {{}},
                    select: function () {{ this.selected = true; }},
                    setSelectionRange: () => {{}},
                    remove: () => {{}},
                }}),
                body: {{ appendChild: () => {{}} }},
                execCommand: (command) => command === 'copy',
            }};
            vm.runInThisContext(code);
            const hooks = window.MIRU_AI_TEST_HOOKS;
            const target = {{
                value: '',
                focus: () => {{}},
                select: function () {{ this.selected = true; }},
                setSelectionRange: () => {{}},
            }};
            const messages = [];
            (async () => {{
                const copyMode = await hooks.copyTextWithFallback({{
                    text: 'Verified result',
                    clipboard: {{ writeText: async () => {{ throw new Error('blocked'); }} }},
                    documentRef: document,
                    target,
                    setFeedback: (message, tone) => messages.push(`copy:${{message}}:${{tone}}`),
                    successText: 'Copied ok',
                    fallbackText: 'manual copy',
                }});
                const pasteTarget = {{ value: '', focus: () => {{}} }};
                const pasteMode = await hooks.pasteTextWithFallback({{
                    clipboard: {{ readText: async () => 'OP01-001' }},
                    target: pasteTarget,
                    setFeedback: (message, tone) => messages.push(`paste:${{message}}:${{tone}}`),
                    successText: 'Pasted ok',
                }});
                console.log(copyMode);
                console.log(pasteMode);
                console.log(pasteTarget.value);
                console.log(messages.join('|'));
            }})().catch((error) => {{
                console.error(error);
                process.exit(1);
            }});
            """
        )
        result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("legacy", result.stdout)
        self.assertIn("clipboard", result.stdout)
        self.assertIn("OP01-001", result.stdout)
        self.assertIn("Copied ok:success", result.stdout)
        self.assertIn("Pasted ok:success", result.stdout)

    def test_js_ask_submit_uses_canonical_api_route_even_with_stale_config(self) -> None:
        js_path = (Path(__file__).resolve().parent.parent / "tools" / "static" / "miru_ai.js").as_posix()
        node_script = textwrap.dedent(
            f"""
            const fs = require('fs');
            const vm = require('vm');
            const code = fs.readFileSync('{js_path}', 'utf8');

            function createElement(overrides = {{}}) {{
                return Object.assign({{
                    value: '',
                    textContent: '',
                    innerHTML: '',
                    dataset: {{}},
                    disabled: false,
                    checked: false,
                    placeholder: '',
                    className: '',
                    classList: {{ add: () => {{}}, remove: () => {{}}, toggle: () => {{}} }},
                    style: {{}},
                    addEventListener(type, handler) {{
                        this._listeners = this._listeners || {{}};
                        this._listeners[type] = handler;
                    }},
                    dispatch(type, event) {{
                        if (this._listeners && this._listeners[type]) {{
                            return this._listeners[type](event);
                        }}
                    }},
                    querySelectorAll(selector) {{
                        if (selector === 'input[name="mode"]') {{
                            return this._modeInputs || [];
                        }}
                        return [];
                    }},
                    focus: () => {{}},
                    blur: () => {{}},
                    select() {{ this.selected = true; }},
                    setSelectionRange: () => {{}},
                    scrollIntoView: () => {{}},
                    matches: () => false,
                    closest: () => null,
                }}, overrides);
            }}

            const mainContent = createElement({{ dataset: {{ pageKey: 'ask', runDisabled: 'false', runUrl: '/api/run/' }} }});
            const form = createElement();
            const requestText = createElement({{ value: 'What is OP09-001?' }});
            const requestHelp = createElement();
            const requestExample = createElement();
            const modeHint = createElement();
            const runButton = createElement();
            const clearButton = createElement();
            const pasteButton = createElement();
            const loadingCard = createElement();
            const resultCard = createElement();
            const resultMeta = createElement();
            const resultHint = createElement();
            const resultReadable = createElement();
            const manualCopyBlock = createElement();
            const resultOutput = createElement();
            const copyButton = createElement();
            const selectButton = createElement();
            const copyFeedback = createElement();
            const errorCard = createElement();
            const errorMeta = createElement();
            const errorOutput = createElement();
            const modeInput = createElement({{ value: 'card lookup', checked: true }});
            form._modeInputs = [modeInput];

            const elements = {{
                miruMainContent: mainContent,
                miruForm: form,
                requestText,
                requestHelp,
                requestExample,
                modeHint,
                runButton,
                clearButton,
                pasteButton,
                loadingCard,
                resultCard,
                resultMeta,
                resultHint,
                resultReadable,
                manualCopyBlock,
                resultOutput,
                copyButton,
                selectButton,
                copyFeedback,
                errorCard,
                errorMeta,
                errorOutput,
            }};

            const fetchCalls = [];
            global.fetch = async (url) => {{
                fetchCalls.push(url);
                return {{
                    ok: true,
                    json: async () => ({{
                        ok: true,
                        mode: 'card lookup',
                        output: 'OP09-001',
                        command_summary: 'python miru_ai.py knowledge'
                    }})
                }};
            }};

            global.window = {{
                MIRU_AI_CONFIG: {{
                    pageKey: 'ask',
                    modeConfigs: [{{ key: 'card lookup', label: 'Card Lookup', request_help: '', request_placeholder: '', request_example: '', result_hint: '' }}],
                    defaultMode: 'card lookup',
                    runUrl: 'api/run/',
                    runDisabled: false,
                }},
                matchMedia: () => ({{ matches: false }}),
                addEventListener: () => {{}},
                scrollTo: () => {{}},
                history: {{ pushState: () => {{}}, replaceState: () => {{}} }},
                location: {{ origin: 'http://localhost:8765', href: 'http://localhost:8765/ask' }},
                setInterval: () => 1,
                clearInterval: () => {{}},
                setTimeout: (fn) => 1,
                clearTimeout: () => {{}},
            }};
            global.navigator = {{}};
            global.setTimeout = window.setTimeout;
            global.clearTimeout = window.clearTimeout;
            global.document = {{
                body: createElement(),
                title: 'Miru AI',
                querySelector: () => null,
                querySelectorAll: (selector) => selector === '.presetButton' ? [] : [],
                addEventListener: () => {{}},
                getElementById: (id) => elements[id] || null,
                createElement: () => createElement(),
                execCommand: () => true,
            }};
            global.DOMParser = class {{
                parseFromString() {{
                    return {{
                        getElementById: () => null,
                        querySelector: () => null,
                        body: {{ className: '' }},
                        title: 'Miru AI',
                    }};
                }}
            }};

            vm.runInThisContext(code);
            Promise.resolve(form.dispatch('submit', {{ preventDefault() {{}} }}))
                .then(() => {{
                    console.log(fetchCalls.join('|'));
                }})
                .catch((error) => {{
                    console.error(error);
                    process.exit(1);
                }});
            """
        )
        result = subprocess.run(["node", "-e", node_script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/api/run", result.stdout)
        self.assertNotIn("/api/run/", result.stdout)
        self.assertNotIn("api/run", result.stdout.replace("/api/run", ""))


if __name__ == "__main__":
    unittest.main()
