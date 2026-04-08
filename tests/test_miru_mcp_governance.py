from __future__ import annotations

from contextlib import closing
import json
import shutil
import sqlite3
import stat
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from miru_ai.governance import mcp_governance as mg
import tools.miru_ai_server as server


class MiruMcpGovernanceTests(unittest.TestCase):
    HARNESS_ROOT = Path(__file__).resolve().parent / "_tmp"

    def make_workspace_tempdir(self) -> Path:
        self.HARNESS_ROOT.mkdir(parents=True, exist_ok=True)
        root = self.HARNESS_ROOT / f"miru_mcp_{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        return root

    def make_catalog_context_db(self, root: Path) -> Path:
        catalog_db = root / "card_catalog.db"
        with closing(sqlite3.connect(catalog_db)) as conn:
            conn.executescript(
                """
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    canonical_code TEXT NOT NULL UNIQUE,
                    set_code TEXT NOT NULL DEFAULT '',
                    card_number TEXT NOT NULL DEFAULT '',
                    set_name TEXT NOT NULL DEFAULT '',
                    card_name TEXT NOT NULL DEFAULT '',
                    rarity TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '',
                    card_type TEXT NOT NULL DEFAULT '',
                    cost INTEGER,
                    power TEXT NOT NULL DEFAULT '',
                    counter TEXT NOT NULL DEFAULT '',
                    attribute TEXT NOT NULL DEFAULT '',
                    traits TEXT NOT NULL DEFAULT '',
                    life TEXT NOT NULL DEFAULT '',
                    block_icon TEXT NOT NULL DEFAULT '',
                    effect_text TEXT NOT NULL DEFAULT '',
                    trigger_text TEXT NOT NULL DEFAULT '',
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    base_card_id TEXT NOT NULL DEFAULT '',
                    is_variant INTEGER NOT NULL DEFAULT 0,
                    variant_category TEXT NOT NULL DEFAULT '',
                    variant_subtype TEXT NOT NULL DEFAULT '',
                    stamp_type TEXT NOT NULL DEFAULT '',
                    stamp_event_name TEXT NOT NULL DEFAULT '',
                    stamp_placement TEXT NOT NULL DEFAULT '',
                    distribution_source TEXT NOT NULL DEFAULT '',
                    distribution_event TEXT NOT NULL DEFAULT '',
                    is_serialized INTEGER NOT NULL DEFAULT 0,
                    serial_number INTEGER,
                    print_run INTEGER,
                    is_premium_variant INTEGER NOT NULL DEFAULT 0,
                    variant_meta_json TEXT NOT NULL DEFAULT '{}'
                );
                INSERT INTO cards (
                    canonical_code, set_code, card_number, set_name, card_name, rarity,
                    color, card_type, is_variant, variant_category
                ) VALUES
                    ('OP01-001', 'OP01', '001', 'Romance Dawn', 'Roronoa Zoro', 'L', 'Red', 'Leader', 0, ''),
                    ('OP01-016', 'OP01', '016', 'Romance Dawn', 'Nami', 'R', 'Red', 'Character', 0, ''),
                    ('OP01-001-P1', 'OP01', '001', 'Romance Dawn', 'Roronoa Zoro', 'L', 'Red', 'Leader', 1, 'parallel');
                """
            )
            conn.commit()
        return catalog_db

    def test_sync_catalog_snapshot_copies_canonical_catalog_into_target(self) -> None:
        root = self.make_workspace_tempdir()
        source_db = root / "card_catalog.db"
        snapshot_db = root / "card_catalog.snapshot.db"
        state_db = root / "miru_mcp_governance.db"
        mcp_config = root / ".mcp.json"

        with closing(sqlite3.connect(source_db)) as conn:
            conn.executescript(
                """
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_code TEXT NOT NULL UNIQUE
                );
                INSERT INTO cards (card_code) VALUES ('OP01-001'), ('OP01-002');
                CREATE TABLE miru_validations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_code TEXT NOT NULL
                );
                INSERT INTO miru_validations (card_code) VALUES ('OP01-001');
                """
            )
            conn.commit()

        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "sqlite-ro-snapshot": {
                            "type": "stdio",
                            "command": "cmd",
                            "args": [
                                "/c",
                                "npx.cmd",
                                "-y",
                                "@mokei/mcp-sqlite",
                                "--db",
                                str(snapshot_db),
                            ],
                            "env": {},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        report = mg.sync_catalog_snapshot(
            canonical_catalog_db_path=source_db,
            mcp_config_path=mcp_config,
            state_db_path=state_db,
        )

        self.assertEqual(report["status"], "synced")
        self.assertEqual(report["source_cards"], 2)
        self.assertEqual(report["snapshot_cards"], 2)
        self.assertTrue(snapshot_db.is_file())
        latest = mg.load_latest_catalog_sync_report(state_db_path=state_db)
        self.assertEqual(latest["status"], "synced")
        self.assertEqual(latest["snapshot_cards"], 2)

    def test_sync_catalog_snapshot_replaces_read_only_target(self) -> None:
        root = self.make_workspace_tempdir()
        source_db = root / "card_catalog.db"
        snapshot_db = root / "card_catalog.snapshot.db"
        state_db = root / "miru_mcp_governance.db"
        mcp_config = root / ".mcp.json"

        with closing(sqlite3.connect(source_db)) as conn:
            conn.executescript(
                """
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_code TEXT NOT NULL UNIQUE
                );
                INSERT INTO cards (card_code) VALUES ('OP01-001'), ('OP01-002'), ('OP01-003');
                """
            )
            conn.commit()

        with closing(sqlite3.connect(snapshot_db)) as conn:
            conn.executescript(
                """
                CREATE TABLE cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_code TEXT NOT NULL UNIQUE
                );
                INSERT INTO cards (card_code) VALUES ('OLD-001');
                """
            )
            conn.commit()
        snapshot_db.chmod(snapshot_db.stat().st_mode & ~stat.S_IWRITE)

        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "sqlite-ro-snapshot": {
                            "type": "stdio",
                            "command": "cmd",
                            "args": [
                                "/c",
                                "npx.cmd",
                                "-y",
                                "@mokei/mcp-sqlite",
                                "--db",
                                str(snapshot_db),
                            ],
                            "env": {},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        report = mg.sync_catalog_snapshot(
            canonical_catalog_db_path=source_db,
            mcp_config_path=mcp_config,
            state_db_path=state_db,
        )

        self.assertEqual(report["status"], "synced")
        self.assertEqual(report["snapshot_cards"], 3)
        self.assertEqual(mg._read_sqlite_count(snapshot_db, "cards"), 3)

    def test_run_governed_research_persists_pending_review_lead(self) -> None:
        root = self.make_workspace_tempdir()
        catalog_db = self.make_catalog_context_db(root)
        state_db = root / "miru_mcp_governance.db"
        missing_config = root / ".mcp.json"

        def fake_invoke(**kwargs):
            return {
                "server_id": kwargs["server_id"],
                "tool_name": kwargs["tool_name"],
                "arguments": dict(kwargs["arguments"]),
                "result": {
                    "results": "OP01-001 Roronoa Zoro Romance Dawn official card list discrepancy noted."
                },
                "preview": "OP01-001 Roronoa Zoro Romance Dawn official card list discrepancy noted.",
            }

        with patch.object(mg, "invoke_stdio_mcp_lane", side_effect=fake_invoke):
            result = mg.run_governed_research(
                lane_id="perplexity",
                query="OP01-001 discrepancy review",
                card_code="OP01-001",
                catalog_db_path=catalog_db,
                state_db_path=state_db,
                mcp_config_path=missing_config,
            )

        self.assertTrue(result["ok"])
        lead = result["lead"]
        self.assertEqual(lead["lane_id"], "perplexity")
        self.assertEqual(lead["card_code"], "OP01-001")
        self.assertTrue(lead["blocked_from_truth_authority"])
        self.assertTrue(lead["authority_cross_check_required"])
        self.assertEqual(lead["outcome"], "review_required")
        self.assertIn("Roronoa Zoro", result["call_result"]["arguments"]["query"])
        self.assertIn("Romance Dawn", result["call_result"]["arguments"]["query"])
        self.assertGreater(lead["governance"]["relevance_score"], 0.5)
        summary = mg.list_research_review_leads(state_db_path=state_db)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["pending"], 1)
        self.assertEqual(summary["items"][0]["lead_key"], lead["lead_key"])

    def test_build_research_request_shapes_card_and_set_specific_queries(self) -> None:
        root = self.make_workspace_tempdir()
        catalog_db = self.make_catalog_context_db(root)

        card_request = mg._build_research_request(
            server_id="perplexity",
            query="OP01-001 discrepancy review",
            card_code="OP01-001",
            max_results=2,
            catalog_db_path=catalog_db,
        )
        self.assertIn('"OP01-001"', card_request["shaped_query"])
        self.assertIn('"Roronoa Zoro"', card_request["shaped_query"])
        self.assertIn('"Romance Dawn"', card_request["shaped_query"])
        self.assertIn("official card list", card_request["shaped_query"].lower())

        set_request = mg._build_research_request(
            server_id="youtube",
            query="OP01 anomaly review",
            set_code="OP01",
            max_results=2,
            catalog_db_path=catalog_db,
        )
        self.assertIn("Romance Dawn", set_request["shaped_query"])
        self.assertIn("box opening", set_request["shaped_query"])
        self.assertIn("checklist", set_request["shaped_query"])

    def test_research_review_queue_ranks_relevant_leads_above_irrelevant_ones(self) -> None:
        root = self.make_workspace_tempdir()
        catalog_db = self.make_catalog_context_db(root)
        state_db = root / "miru_mcp_governance.db"

        previews = iter(
            [
                "Background check discrepancy review for hiring candidate and payroll.",
                "OP01-001 Roronoa Zoro Romance Dawn official card list showcase and variant discussion.",
            ]
        )

        def fake_invoke(**kwargs):
            preview = next(previews)
            return {
                "server_id": kwargs["server_id"],
                "tool_name": kwargs["tool_name"],
                "arguments": dict(kwargs["arguments"]),
                "result": {"results": preview},
                "preview": preview,
            }

        with patch.object(mg, "invoke_stdio_mcp_lane", side_effect=fake_invoke):
            low = mg.run_governed_research(
                lane_id="perplexity",
                query="OP01-001 likely discrepancy review candidate",
                card_code="OP01-001",
                catalog_db_path=catalog_db,
                state_db_path=state_db,
            )
            high = mg.run_governed_research(
                lane_id="perplexity",
                query="OP01-001 likely discrepancy review candidate",
                card_code="OP01-001",
                catalog_db_path=catalog_db,
                state_db_path=state_db,
            )

        self.assertLess(
            low["lead"]["governance"]["relevance_score"],
            high["lead"]["governance"]["relevance_score"],
        )
        summary = mg.list_research_review_leads(state_db_path=state_db)
        self.assertEqual(summary["items"][0]["lead_key"], high["lead"]["lead_key"])
        self.assertGreater(summary["items"][0]["confidence"], summary["items"][1]["confidence"])

    def test_build_mcp_governance_summary_reports_classified_lanes(self) -> None:
        root = self.make_workspace_tempdir()
        snapshot_db = root / "card_catalog.snapshot.db"
        mcp_config = root / ".mcp.json"
        mcp_config.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "sqlite-ro-snapshot": {
                            "type": "stdio",
                            "command": "cmd",
                            "args": [
                                "/c",
                                "npx.cmd",
                                "-y",
                                "@mokei/mcp-sqlite",
                                "--db",
                                str(snapshot_db),
                            ],
                            "env": {},
                        },
                        "sequential-thinking": {
                            "type": "stdio",
                            "command": "npx.cmd",
                            "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
                            "env": {},
                        },
                        "perplexity": {
                            "type": "stdio",
                            "command": "npx.cmd",
                            "args": ["@perplexity-ai/mcp-server"],
                            "env": {"PERPLEXITY_API_KEY": "test"},
                        },
                        "youtube": {
                            "type": "stdio",
                            "command": "npx.cmd",
                            "args": ["-y", "@a.ardeshir/youtube-mcp"],
                            "env": {"YOUTUBE_API_KEY": "test"},
                        },
                        "git": {
                            "type": "stdio",
                            "command": "npx.cmd",
                            "args": ["-y", "@cyanheads/git-mcp-server@latest"],
                            "env": {"GIT_BASE_DIR": str(root)},
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

        summary = mg.build_mcp_governance_summary(
            mcp_config_path=mcp_config,
            state_db_path=root / "miru_mcp_governance.db",
        )
        lanes = {item["server_id"]: item for item in summary["lanes"]}

        self.assertEqual(lanes["sqlite-ro-snapshot"]["approval_class"], "core_approved")
        self.assertEqual(lanes["perplexity"]["approval_class"], "optional_approved")
        self.assertEqual(lanes["youtube"]["approval_class"], "optional_approved")
        self.assertEqual(lanes["git"]["approval_class"], "operator_only")
        self.assertTrue(lanes["perplexity"]["blocked_from_truth_authority"])
        self.assertTrue(lanes["youtube"]["blocked_from_truth_authority"])
        self.assertEqual(
            summary["catalog_ingestion"]["sqlite_snapshot_target"],
            str(snapshot_db),
        )


class MiruMcpServerRouteTests(unittest.TestCase):
    def create_client(self):
        app = server.create_app()
        app.config["TESTING"] = True
        return app.test_client()

    def test_api_dev_mcp_status_returns_summary(self) -> None:
        client = self.create_client()
        with patch.object(
            server,
            "build_mcp_governance_summary",
            return_value={"policy_version": 1, "lanes": [{"server_id": "perplexity"}]},
        ):
            response = client.get("/api/dev/mcp/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["policy_version"], 1)
        self.assertEqual(payload["lanes"][0]["server_id"], "perplexity")

    def test_api_dev_mcp_catalog_sync_returns_report(self) -> None:
        client = self.create_client()
        with patch.object(
            server,
            "sync_catalog_snapshot",
            return_value={"status": "synced", "snapshot_cards": 2497},
        ):
            response = client.post("/api/dev/mcp/catalog-sync", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["report"]["snapshot_cards"], 2497)

    def test_api_dev_mcp_research_returns_fail_closed_error(self) -> None:
        client = self.create_client()
        with patch.object(
            server,
            "run_governed_research",
            side_effect=server.McpInvocationError("lane unavailable"),
        ):
            response = client.post(
                "/api/dev/mcp/research",
                json={"lane_id": "perplexity", "query": "OP01-001 discrepancy"},
            )

        self.assertEqual(response.status_code, 502)
        payload = response.get_json()
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["fail_closed"])
        self.assertIn("lane unavailable", payload["error"])

    def test_api_dev_mcp_research_returns_stored_lead(self) -> None:
        client = self.create_client()
        with patch.object(
            server,
            "run_governed_research",
            return_value={"ok": True, "lead": {"lead_key": "perplexity-123"}},
        ):
            response = client.post(
                "/api/dev/mcp/research",
                json={"lane_id": "perplexity", "query": "OP01-001 discrepancy"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["lead"]["lead_key"], "perplexity-123")
