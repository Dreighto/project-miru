"""Regression test for PRO-285: drift scanner workflow surfaces orphan
completion markers (null ticket_id but ticket inferable from branch / summary).

Per the PRO-189 lesson (locked 2026-04-28): the test loads the jsCode from
the workflow JSON file on disk and evaluates it in Node, NOT a clean copy of
the algorithm. This catches:

1. Syntax errors introduced by hand-edits to the JSON-encoded JS string.
2. Deploy-pipeline mangling between source and runtime.
3. Behavioral regressions in the actual deployed code path.

The dsw003-classify-drift node reads `cc_completion_log.jsonl` and a Linear
issues response, then emits one of three drift categories per ticket:
missing_marker, stale_linear, or orphan_markers.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / "docker" / "n8n" / "workflows" / "w-drift-scanner.json"

NODE = shutil.which("node")


@unittest.skipUnless(NODE is not None, "Node.js required to evaluate workflow jsCode")
class DriftScannerOrphanMarkerTests(unittest.TestCase):
    """Behavioural tests that exercise the dsw003-classify-drift jsCode AS IT
    LIVES IN THE WORKFLOW JSON FILE."""

    @classmethod
    def setUpClass(cls) -> None:
        with WORKFLOW_PATH.open(encoding="utf-8") as fh:
            workflow = json.load(fh)
        cls.dsw003_jscode = None
        for node in workflow.get("nodes", []):
            if node.get("id") == "dsw003-classify-drift":
                cls.dsw003_jscode = node["parameters"]["jsCode"]
                break
        if cls.dsw003_jscode is None:
            raise RuntimeError("dsw003-classify-drift node missing from workflow JSON")

    def _run_dsw003(self, marker_lines, linear_response):
        """Execute dsw003 jsCode with controlled inputs. Returns the parsed
        result object (the json field of the single returned item)."""
        with tempfile.TemporaryDirectory() as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            cp_path = tmpdir / "cc_completion_log.jsonl"
            cp_path.write_text(
                "\n".join(marker_lines) + ("\n" if marker_lines else ""),
                encoding="utf-8",
            )

            wrapper_js = (
                "const fs = require('fs');\n"
                "const _origRead = fs.readFileSync;\n"
                "const _origExists = fs.existsSync;\n"
                f"const REAL_CP = {json.dumps(str(cp_path))};\n"
                "fs.readFileSync = function(p, enc) {\n"
                "  if (p === '/miru-data/cc_completion_log.jsonl') return _origRead(REAL_CP, enc);\n"
                "  return _origRead(p, enc);\n"
                "};\n"
                "fs.existsSync = function(p) {\n"
                "  if (p === '/miru-data/cc_completion_log.jsonl') return _origExists(REAL_CP);\n"
                "  return _origExists(p);\n"
                "};\n"
                f"const $input = {{ first: () => ({{ json: {json.dumps(linear_response)} }}) }};\n"
                "const result = (function() {\n"
                f"{self.dsw003_jscode}\n"
                "})();\n"
                "process.stdout.write(JSON.stringify(result[0].json));\n"
            )
            wrapper_path = tmpdir / "test_wrapper.js"
            wrapper_path.write_text(wrapper_js, encoding="utf-8")

            proc = subprocess.run(
                [NODE, str(wrapper_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=(
                    f"node exited {proc.returncode}; "
                    f"stderr={proc.stderr!r}; stdout={proc.stdout!r}"
                ),
            )
            return json.loads(proc.stdout.strip())

    def test_jscode_parses_without_syntax_error(self) -> None:
        """Smoke test (PRO-189 lesson): the jsCode must evaluate. Catches deploy-
        time mangling and hand-edit syntax bugs at the JSON↔runtime boundary."""
        result = self._run_dsw003(
            marker_lines=[],
            linear_response={"data": {"team": {"issues": {"nodes": []}}}},
        )
        self.assertIn("missing_marker", result)
        self.assertIn("stale_linear", result)
        self.assertIn("orphan_markers", result)
        self.assertEqual(result["scanned"], 0)
        self.assertEqual(result["drift_count"], 0)

    def test_orphan_marker_with_pro_in_branch(self) -> None:
        """Marker has null ticket_id but branch encodes PRO-276; Linear has
        PRO-276 in In Progress → classified as orphan, not missing/stale."""
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "Fix W7 HMAC replay window",
            "branch": "dreighto/pro-276-w7-callback-fix",
            "pr_number": 74,
        }
        linear_response = {
            "data": {
                "team": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "uuid-1",
                                "identifier": "PRO-276",
                                "title": "W7 callback bug",
                                "updatedAt": "2026-05-03T00:00:00Z",
                                "state": {
                                    "id": "s1",
                                    "name": "In Progress",
                                    "type": "started",
                                },
                            }
                        ]
                    }
                }
            }
        }
        result = self._run_dsw003(
            marker_lines=[json.dumps(marker)], linear_response=linear_response
        )
        self.assertEqual(len(result["orphan_markers"]), 1)
        orphan = result["orphan_markers"][0]
        self.assertEqual(orphan["inferred_ticket_id"], "PRO-276")
        self.assertEqual(orphan["linear_state"], "In Progress")
        self.assertEqual(orphan["pr_number"], 74)
        self.assertEqual(result["missing_marker"], [])
        self.assertEqual(result["stale_linear"], [])
        self.assertEqual(result["drift_count"], 1)

    def test_orphan_marker_inferred_from_summary(self) -> None:
        """Branch and pr_number absent but summary references PRO-X → still
        classified."""
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "Loop hardening: PRO-275 retro logged",
            "branch": None,
            "pr_number": None,
        }
        linear_response = {
            "data": {
                "team": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "u1",
                                "identifier": "PRO-275",
                                "title": "Retro",
                                "updatedAt": "2026-05-03T00:00:00Z",
                                "state": {
                                    "id": "s1",
                                    "name": "Done",
                                    "type": "completed",
                                },
                            }
                        ]
                    }
                }
            }
        }
        result = self._run_dsw003(
            marker_lines=[json.dumps(marker)], linear_response=linear_response
        )
        self.assertEqual(len(result["orphan_markers"]), 1)
        self.assertEqual(result["orphan_markers"][0]["inferred_ticket_id"], "PRO-275")

    def test_orphan_inference_for_unknown_ticket_dropped(self) -> None:
        """Inferred ticket id that doesn't appear in Linear must NOT be flagged.
        Avoids false positives when summary text mentions an unrelated ticket."""
        marker = {
            "timestamp": "2026-05-02T23:48:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "Reference to PRO-9999 (does not exist in Linear)",
            "branch": None,
            "pr_number": None,
        }
        linear_response = {"data": {"team": {"issues": {"nodes": []}}}}
        result = self._run_dsw003(
            marker_lines=[json.dumps(marker)], linear_response=linear_response
        )
        self.assertEqual(result["orphan_markers"], [])
        self.assertEqual(result["drift_count"], 0)

    def test_existing_marker_with_ticket_id_not_treated_as_orphan(self) -> None:
        """Marker with valid ticket_id goes into the marked set; not orphan."""
        marker = {
            "timestamp": "2026-05-03T17:09:31Z",
            "ticket_id": "PRO-289",
            "status": "CONFIRMED_WORKING",
            "summary": "Strengthened drift correction autonomy",
            "branch": "dreighto/pro-289-canon-strengthening",
            "pr_number": 79,
        }
        linear_response = {
            "data": {
                "team": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "u1",
                                "identifier": "PRO-289",
                                "title": "Drift autonomy",
                                "updatedAt": "2026-05-03T00:00:00Z",
                                "state": {
                                    "id": "s1",
                                    "name": "Done",
                                    "type": "completed",
                                },
                            }
                        ]
                    }
                }
            }
        }
        result = self._run_dsw003(
            marker_lines=[json.dumps(marker)], linear_response=linear_response
        )
        # No drift: marker exists with the right ticket_id, Linear says Done.
        self.assertEqual(result["drift_count"], 0)
        self.assertEqual(result["orphan_markers"], [])
        self.assertEqual(result["missing_marker"], [])

    def test_missing_marker_drift_still_works(self) -> None:
        """Linear says Done but no marker → flagged as missing_marker (existing
        behaviour preserved)."""
        linear_response = {
            "data": {
                "team": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "u1",
                                "identifier": "PRO-300",
                                "title": "Some closed ticket",
                                "updatedAt": "2026-05-03T00:00:00Z",
                                "state": {
                                    "id": "s1",
                                    "name": "Done",
                                    "type": "completed",
                                },
                            }
                        ]
                    }
                }
            }
        }
        result = self._run_dsw003(marker_lines=[], linear_response=linear_response)
        self.assertEqual(len(result["missing_marker"]), 1)
        self.assertEqual(result["missing_marker"][0]["ticket_id"], "PRO-300")

    def test_stale_linear_drift_still_works(self) -> None:
        """Marker exists but Linear says Todo → flagged as stale_linear
        (existing behaviour preserved)."""
        marker = {
            "timestamp": "2026-05-03T00:00:00Z",
            "ticket_id": "PRO-401",
            "status": "CONFIRMED_WORKING",
            "summary": "Fix something",
        }
        linear_response = {
            "data": {
                "team": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "u1",
                                "identifier": "PRO-401",
                                "title": "Something",
                                "updatedAt": "2026-05-03T00:00:00Z",
                                "state": {
                                    "id": "s1",
                                    "name": "Todo",
                                    "type": "unstarted",
                                },
                            }
                        ]
                    }
                }
            }
        }
        result = self._run_dsw003(
            marker_lines=[json.dumps(marker)], linear_response=linear_response
        )
        self.assertEqual(len(result["stale_linear"]), 1)
        self.assertEqual(result["stale_linear"][0]["ticket_id"], "PRO-401")

    def test_orphan_takes_priority_over_missing(self) -> None:
        """If an orphan marker can be inferred for a ticket Linear says is Done,
        it surfaces as orphan (fix: link the marker), not missing (fix: write
        a new marker). Same ticket cannot appear in both categories."""
        marker = {
            "timestamp": "2026-05-02T23:00:00Z",
            "ticket_id": None,
            "status": "CONFIRMED_WORKING",
            "summary": "Shipped PRO-500 fix",
            "branch": "dreighto/pro-500-fix",
            "pr_number": 88,
        }
        linear_response = {
            "data": {
                "team": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "u1",
                                "identifier": "PRO-500",
                                "title": "Some fix",
                                "updatedAt": "2026-05-03T00:00:00Z",
                                "state": {
                                    "id": "s1",
                                    "name": "Done",
                                    "type": "completed",
                                },
                            }
                        ]
                    }
                }
            }
        }
        result = self._run_dsw003(
            marker_lines=[json.dumps(marker)], linear_response=linear_response
        )
        self.assertEqual(len(result["orphan_markers"]), 1)
        self.assertEqual(result["missing_marker"], [])
        self.assertEqual(result["drift_count"], 1)


if __name__ == "__main__":
    unittest.main()
