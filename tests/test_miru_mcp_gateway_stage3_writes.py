from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import miru_readonly_filesystem_mcp as stdio_mcp
from miru_mcp_gateway import audit as gw_audit
from miru_mcp_gateway import n8n_write_tools as nw


class MiruMcpGatewayStage3WritesTests(unittest.TestCase):
    HARNESS = Path(__file__).resolve().parent / "_tmp"

    def setUp(self) -> None:
        self.HARNESS.mkdir(parents=True, exist_ok=True)
        self.root = self.HARNESS / f"gw_{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_append_jsonl_rotates_when_file_over_threshold(self) -> None:
        log = self.root / "writes.jsonl"
        log.write_text('{"x":1}\n', encoding="utf-8")
        orig_stat = Path.stat

        def stat_patch(self: Path, *args, **kwargs):
            if self == log:
                return SimpleNamespace(st_size=gw_audit._ROTATE_BYTES + 1)
            return orig_stat(self, *args, **kwargs)

        with patch.object(Path, "stat", stat_patch):
            gw_audit.append_jsonl(log, {"after": "rotate"})

        self.assertTrue(log.with_name(f"{log.name}.1").exists())
        self.assertTrue(log.exists())
        tail = log.read_text(encoding="utf-8").strip()
        self.assertIn("after", tail)

    def test_bulk_delete_refuses_over_cap_without_deleting(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_request(
            method: str,
            path: str,
            *,
            params=None,
            json_body=None,
        ):
            calls.append((method, path))
            if method == "GET" and path == "/api/v1/executions":
                ids = [{"id": str(i)} for i in range(102)]
                return {"data": ids}
            raise AssertionError(f"unexpected {method} {path}")

        cfg = SimpleNamespace(
            n8n_api_key="k",
            n8n_base_url="http://n.example",
            n8n_write_workflow_allowlist=(),
            fs_root=self.root,
        )
        nw._CFG = cfg
        nw._API_KEY = "k"
        nw._BASE_URL = "http://n.example"
        nw._WORKFLOW_ALLOWLIST = frozenset()
        with patch.object(nw, "_n8n_request", side_effect=fake_request):
            out = nw.n8n_bulk_delete_executions({"workflow_id": "wf1"})
        payload = json.loads(out)
        self.assertFalse(payload.get("ok"))
        self.assertEqual(payload.get("would_delete_count"), 102)
        self.assertEqual([c[0] for c in calls], ["GET"])

    def test_create_workflow_appends_pending_intent(self) -> None:
        data_dir = self.root / "data"
        pending = data_dir / "mcp_gateway_pending_writes.jsonl"
        cfg = SimpleNamespace(
            n8n_api_key="k",
            n8n_base_url="http://n.example",
            fs_root=self.root,
            data_dir=data_dir,
            mcp_gateway_pending_writes_path=pending,
            n8n_write_approval_notify_url=None,
            n8n_write_workflow_allowlist=(),
        )
        nw._CFG = cfg
        with patch.object(gw_audit, "notify_approval_webhook"):
            out = nw.n8n_create_workflow({"name": "t", "nodes": [], "connections": {}})
        payload = json.loads(out)
        self.assertEqual(payload.get("status"), "pending_approval")
        rid = payload["approval"]["request_id"]
        lines = pending.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        row = json.loads(lines[0])
        self.assertEqual(row["kind"], "intent")
        self.assertEqual(row["request_id"], rid)
        self.assertEqual(row["operation"], "create_workflow")

    def test_trigger_webhook_audits_shared_request_transport_errors(self) -> None:
        cfg = SimpleNamespace(
            n8n_api_key="k",
            n8n_base_url="http://n.example",
            n8n_write_workflow_allowlist=(),
            fs_root=self.root,
        )
        nw._CFG = cfg
        nw._API_KEY = "k"
        nw._BASE_URL = "http://n.example"

        err = nw.requests.exceptions.ConnectionError("boom")
        with (
            patch.object(nw.requests, "request", side_effect=err) as req,
            self.assertRaises(stdio_mcp.McpError),
        ):
            nw.n8n_trigger_webhook("test", {"x": 1})

        req.assert_called_once()
        self.assertEqual(req.call_args.args[:2], ("POST", "http://n.example/webhook/test"))
        self.assertEqual(req.call_args.kwargs["json"], {"x": 1})
        writes_log = self.root / "logs" / "mcp_gateway_writes.jsonl"
        row = json.loads(writes_log.read_text(encoding="utf-8").strip())
        self.assertEqual(row["tool"], "n8n_trigger_webhook")
        self.assertEqual(row["target_id"], "/webhook/test")
        self.assertEqual(row["result"], "failure")
        self.assertIn("n8n_write: transport error on /webhook/test", row["error"])
        self.assertIn("boom", row["error"])


if __name__ == "__main__":
    unittest.main()
