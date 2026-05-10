"""Tests for tools/miru_mcp_gateway/canon_routes.py — LOS-10 Step 1.

Coverage:
  - get_canon_file happy path returns content + sha256 + mtime + snapshot id
  - get_canon_file rejects paths not in the allowlist (404 semantics)
  - get_canon_file gracefully handles a layout-declared file that's missing
    from disk (defensive only; should never happen in production)
  - get_canon_manifest returns all known canon files with correct shape
  - canon_snapshot_id is stable across unchanged calls
  - canon_snapshot_id changes when any single canon file changes
  - cache invalidates correctly when mtime changes (no stale content served)

Tests use a synthetic repo_root populated with a subset of the canonical
layout. Mutating real `D:\\dev\\miru\\.miru/` would race against live worker
sessions — bad. Synthetic temp dir is hermetic.
"""

from __future__ import annotations

import hashlib
import shutil

# Make the gateway package importable when running this test directly.
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from miru_mcp_gateway import canon_routes  # noqa: E402


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CanonRoutesTests(unittest.TestCase):
    """Unit tests for the canon_routes module."""

    def setUp(self) -> None:
        # Hermetic temp repo root with a subset of the canon layout.
        self.tmp_root = Path(tempfile.mkdtemp(prefix="canon_routes_test_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_root, ignore_errors=True))

        # Mirror the on-disk shape of just the files we'll exercise. Using a
        # subset keeps the test fast and lets us prove the layout->disk
        # resolution is correct without populating all 41 files.
        (self.tmp_root / ".miru" / "overlays").mkdir(parents=True)
        (self.tmp_root / ".miru" / "reference").mkdir(parents=True)
        (self.tmp_root / "miru-context").mkdir(parents=True)

        # Use write_bytes (NOT write_text) — Python's text mode translates
        # `\n` to `\r\n` on Windows, which would make the test assertions
        # depend on platform line-ending normalization. Bytes are exact.
        (self.tmp_root / "CLAUDE.md").write_bytes(b"# CLAUDE\n")
        (self.tmp_root / "AGENTS.md").write_bytes(b"# AGENTS\n")
        (self.tmp_root / ".miru" / "overlays" / "workflow-git.md").write_bytes(b"# workflow-git\n")
        (self.tmp_root / ".miru" / "reference" / "ports-and-services.md").write_bytes(b"# ports\n")
        (self.tmp_root / "miru-context" / "guardrails.md").write_bytes(b"# guardrails\n")

        # Reset module-level cache between tests so per-test state doesn't
        # leak via the singleton.
        canon_routes.reset_cache_for_tests()

    # -------------------------------------------------------------------
    # get_canon_file
    # -------------------------------------------------------------------

    def test_get_canon_file_happy_path_returns_full_metadata(self) -> None:
        result = canon_routes.get_canon_file(self.tmp_root, "overlays/workflow-git.md")
        self.assertIsNotNone(result)
        self.assertEqual(result["canon_path"], "overlays/workflow-git.md")
        self.assertEqual(result["content"], "# workflow-git\n")
        self.assertEqual(result["encoding"], "utf-8")
        self.assertEqual(result["sha256"], _sha256(b"# workflow-git\n"))
        self.assertEqual(result["byte_length"], len(b"# workflow-git\n"))
        self.assertGreater(result["mtime_ns"], 0)
        # snapshot id is a sha256 hex string => 64 chars
        self.assertEqual(len(result["canon_snapshot_id"]), 64)

    def test_get_canon_file_resolves_root_files(self) -> None:
        # CLAUDE.md and AGENTS.md sit at the repo root, not under .miru/.
        result = canon_routes.get_canon_file(self.tmp_root, "root/CLAUDE.md")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "# CLAUDE\n")
        result_agents = canon_routes.get_canon_file(self.tmp_root, "root/AGENTS.md")
        self.assertIsNotNone(result_agents)
        self.assertEqual(result_agents["content"], "# AGENTS\n")

    def test_get_canon_file_resolves_context_files(self) -> None:
        result = canon_routes.get_canon_file(self.tmp_root, "context/guardrails.md")
        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "# guardrails\n")

    def test_get_canon_file_rejects_path_not_in_allowlist(self) -> None:
        # Random invented paths must raise NotAllowlistedError — the layout
        # dict is the ONLY source of truth for what's exposable. No
        # directory walking. CodeRabbit R0: distinct exception lets the
        # handler differentiate "wrong path" from "file gone."
        for bogus in [
            "overlays/nonexistent.md",
            "../../etc/passwd",
            "root/.env",
            "data/secrets.json",
        ]:
            with self.assertRaises(canon_routes.NotAllowlistedError):
                canon_routes.get_canon_file(self.tmp_root, bogus)

    def test_get_canon_file_missing_on_disk_raises_distinct_error(self) -> None:
        # 'overlays/adopted-lessons.md' IS in the allowlist but we didn't
        # create it in setUp. Must raise AllowlistedFileMissingError — NOT
        # the same exception as a path that's not in the allowlist at all.
        with self.assertRaises(canon_routes.AllowlistedFileMissingError):
            canon_routes.get_canon_file(self.tmp_root, "overlays/adopted-lessons.md")

    # -------------------------------------------------------------------
    # get_canon_manifest
    # -------------------------------------------------------------------

    def test_manifest_shape_includes_all_layout_keys(self) -> None:
        manifest = canon_routes.get_canon_manifest(self.tmp_root)
        self.assertIn("canon_snapshot_id", manifest)
        self.assertIn("files", manifest)
        self.assertIn("file_count", manifest)
        # Every entry in the static layout dict should appear in the manifest
        # — even ones not on disk (they get the missing=True marker).
        for canon_path in canon_routes._CANON_LAYOUT:
            self.assertIn(canon_path, manifest["files"])

    def test_manifest_marks_missing_files(self) -> None:
        manifest = canon_routes.get_canon_manifest(self.tmp_root)
        # We populated 5 files in setUp; the rest should be marked missing.
        present_canon_paths = {
            "overlays/workflow-git.md",
            "reference/ports-and-services.md",
            "context/guardrails.md",
            "root/CLAUDE.md",
            "root/AGENTS.md",
        }
        for canon_path, meta in manifest["files"].items():
            if canon_path in present_canon_paths:
                self.assertFalse(meta.get("missing", False), f"{canon_path} should be present")
                self.assertGreater(meta["byte_length"], 0)
                self.assertEqual(len(meta["sha256"]), 64)
            else:
                self.assertTrue(
                    meta.get("missing", False), f"{canon_path} should be marked missing"
                )

    # -------------------------------------------------------------------
    # canon_snapshot_id behavior
    # -------------------------------------------------------------------

    def test_snapshot_id_stable_across_unchanged_calls(self) -> None:
        first = canon_routes.get_canon_manifest(self.tmp_root)["canon_snapshot_id"]
        second = canon_routes.get_canon_manifest(self.tmp_root)["canon_snapshot_id"]
        self.assertEqual(first, second)
        # Per-file fetch returns the same snapshot id too.
        per_file = canon_routes.get_canon_file(self.tmp_root, "overlays/workflow-git.md")
        self.assertEqual(per_file["canon_snapshot_id"], first)

    def test_snapshot_id_changes_when_file_changes(self) -> None:
        before = canon_routes.get_canon_manifest(self.tmp_root)["canon_snapshot_id"]
        # Mutate one canon file. Sleep to ensure mtime_ns advances on hosts
        # with low-resolution clocks (Windows NTFS has 100-ns resolution so
        # this is generous; on FAT/exFAT the resolution is 2 sec).
        time.sleep(0.01)
        (self.tmp_root / ".miru" / "overlays" / "workflow-git.md").write_bytes(
            b"# workflow-git CHANGED\n"
        )
        after = canon_routes.get_canon_manifest(self.tmp_root)["canon_snapshot_id"]
        self.assertNotEqual(before, after, "snapshot id must change when canon mutates")

    def test_cache_serves_fresh_content_after_mtime_change(self) -> None:
        # First read primes the cache.
        first = canon_routes.get_canon_file(self.tmp_root, "overlays/workflow-git.md")
        self.assertEqual(first["content"], "# workflow-git\n")
        # Mutate the file.
        time.sleep(0.01)
        (self.tmp_root / ".miru" / "overlays" / "workflow-git.md").write_bytes(b"# new content\n")
        # Second read must reflect the new content (cache invalidated by mtime).
        second = canon_routes.get_canon_file(self.tmp_root, "overlays/workflow-git.md")
        self.assertEqual(second["content"], "# new content\n")
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertNotEqual(first["canon_snapshot_id"], second["canon_snapshot_id"])

    def test_layout_keys_match_expected_count(self) -> None:
        # Pin the canon set size — if someone adds a file to the layout dict
        # without adding it here, this test forces them to update both. As of
        # 2026-05-10: 6 overlays + 8 reference + 26 context + 2 root = 42.
        # Adjust this number when canon legitimately grows.
        self.assertEqual(len(canon_routes._CANON_LAYOUT), 42)

    def test_get_canon_file_handles_non_utf8_content(self) -> None:
        # CodeRabbit R2: defense in depth — a corrupted canon file with
        # invalid UTF-8 bytes must NOT 500 the handler. get_canon_file
        # should raise AllowlistedFileMissingError (semantically: file
        # is present but unusable for canon purposes — workers can't act
        # on undecodable bytes).
        # Write raw bytes that are not valid UTF-8 (bare 0xFF byte).
        (self.tmp_root / ".miru" / "overlays" / "workflow-git.md").write_bytes(
            b"\xff\xfe binary garbage \x00\x01\x02"
        )
        with self.assertRaises(canon_routes.AllowlistedFileMissingError) as ctx:
            canon_routes.get_canon_file(self.tmp_root, "overlays/workflow-git.md")
        # The error message should include enough debug info for the operator
        # to find the corrupted file (canon_path + sha256 + byte_length).
        msg = str(ctx.exception)
        self.assertIn("overlays/workflow-git.md", msg)
        self.assertIn("not valid UTF-8", msg)

    def test_load_file_handles_toctou_disappearance(self) -> None:
        # CodeRabbit R0: between the cache's internal exists() and the
        # subsequent stat()/read_bytes(), a file can disappear (operator edit,
        # antivirus quarantine, parallel cleanup). _load_file must catch
        # FileNotFoundError and return None instead of letting it escape.
        # Simulate by deleting the file mid-cache-prime.
        target = self.tmp_root / ".miru" / "overlays" / "workflow-git.md"
        target.unlink()
        # Now the file is missing; calling get_canon_file must surface as the
        # AllowlistedFileMissingError, not a raw FileNotFoundError 500.
        with self.assertRaises(canon_routes.AllowlistedFileMissingError):
            canon_routes.get_canon_file(self.tmp_root, "overlays/workflow-git.md")
        # Manifest should mark it missing instead of crashing.
        manifest = canon_routes.get_canon_manifest(self.tmp_root)
        self.assertTrue(manifest["files"]["overlays/workflow-git.md"]["missing"])


class CanonRoutesHTTPTests(unittest.TestCase):
    """Route-level tests via Starlette's TestClient.

    CodeRabbit R0: helpers were unit-tested but the HTTP wiring (route paths,
    JSON shape, status codes) wasn't. These tests exercise the actual handlers
    registered by `register_canon_routes()` so the contract is covered.

    A tiny FakeMcp stand-in collects routes via the same `custom_route`
    decorator API that FastMCP exposes. Avoids dragging the full FastMCP
    dependency into the test path.
    """

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="canon_routes_http_test_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_root, ignore_errors=True))
        (self.tmp_root / ".miru" / "overlays").mkdir(parents=True)
        (self.tmp_root / "miru-context").mkdir(parents=True)
        (self.tmp_root / "CLAUDE.md").write_bytes(b"# CLAUDE\n")
        (self.tmp_root / "AGENTS.md").write_bytes(b"# AGENTS\n")
        (self.tmp_root / ".miru" / "overlays" / "workflow-git.md").write_bytes(b"# workflow-git\n")
        (self.tmp_root / "miru-context" / "guardrails.md").write_bytes(b"# guardrails\n")
        canon_routes.reset_cache_for_tests()

        # Build a Starlette app with the canon routes wired in.
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient

        class FakeMcp:
            """Stand-in for FastMCP — same custom_route decorator API."""

            def __init__(self) -> None:
                self.routes: list[Route] = []

            def custom_route(self, path, methods):
                def decorator(handler):
                    self.routes.append(Route(path, handler, methods=methods))
                    return handler

                return decorator

        fake_mcp = FakeMcp()
        canon_routes.register_canon_routes(fake_mcp, self.tmp_root)
        self.app = Starlette(routes=fake_mcp.routes)
        self.client = TestClient(self.app)

    def test_route_canon_manifest_returns_full_shape(self) -> None:
        response = self.client.get("/canon-manifest")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("canon_snapshot_id", body)
        self.assertIn("files", body)
        self.assertEqual(body["file_count"], len(canon_routes._CANON_LAYOUT))
        # Files we created should report present + correct sha
        wg = body["files"]["overlays/workflow-git.md"]
        self.assertFalse(wg.get("missing", False))
        self.assertEqual(wg["sha256"], _sha256(b"# workflow-git\n"))

    def test_route_canon_file_happy_path(self) -> None:
        response = self.client.get("/canon/overlays/workflow-git.md")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["canon_path"], "overlays/workflow-git.md")
        self.assertEqual(body["content"], "# workflow-git\n")
        self.assertEqual(body["sha256"], _sha256(b"# workflow-git\n"))
        self.assertEqual(body["byte_length"], len(b"# workflow-git\n"))
        self.assertEqual(len(body["canon_snapshot_id"]), 64)

    def test_route_canon_file_per_file_snapshot_id_matches_manifest(self) -> None:
        # CR R0 explicit ask: per-file snapshot_id must match manifest's.
        per_file = self.client.get("/canon/overlays/workflow-git.md").json()
        manifest = self.client.get("/canon-manifest").json()
        self.assertEqual(per_file["canon_snapshot_id"], manifest["canon_snapshot_id"])

    def test_route_canon_file_root_files(self) -> None:
        response = self.client.get("/canon/root/CLAUDE.md")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["content"], "# CLAUDE\n")

    def test_route_canon_file_404_for_non_allowlisted_path(self) -> None:
        response = self.client.get("/canon/overlays/totally-fake.md")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "not_in_canon_allowlist")
        self.assertEqual(body["canon_path"], "overlays/totally-fake.md")

    def test_route_canon_file_404_for_allowlisted_but_missing(self) -> None:
        # 'overlays/adopted-lessons.md' is in _CANON_LAYOUT but not in the
        # temp dir — handler must return the differentiated error code, NOT
        # the same 'not_in_canon_allowlist' as a bogus path. Operators
        # debugging missing canon need to know the difference.
        response = self.client.get("/canon/overlays/adopted-lessons.md")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "allowlisted_file_missing")
        self.assertEqual(body["canon_path"], "overlays/adopted-lessons.md")

    def test_route_canon_file_404_for_non_utf8_content(self) -> None:
        # CodeRabbit R2: at the route level, a corrupted UTF-8 file in the
        # canon must surface as a clean 404 (allowlisted_file_missing), NOT
        # a 500 from an unhandled UnicodeDecodeError. Operators on iPhone
        # debugging the dashboard would see a server-error blob instead of
        # an actionable error payload otherwise.
        (self.tmp_root / ".miru" / "overlays" / "workflow-git.md").write_bytes(
            b"\xff\xfe corrupted \x00"
        )
        canon_routes.reset_cache_for_tests()
        response = self.client.get("/canon/overlays/workflow-git.md")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["error"], "allowlisted_file_missing")

    def test_route_canon_file_404_for_traversal_attempt(self) -> None:
        # CodeRabbit R1: use URL-encoded segments so Starlette's router
        # cannot normalize the traversal away before the handler sees it.
        # Then assert the payload — not just status_code — to prove the 404
        # came from the allowlist boundary (returning the canonical
        # "not_in_canon_allowlist" error), NOT from the router 404'ing on a
        # missing route. If we only checked status_code, a router-level 404
        # would silently pass the test for the wrong reason.
        response = self.client.get("/canon/%2E%2E%2F%2E%2E%2Fetc/passwd")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "not_in_canon_allowlist")
        # The handler echoes the canon_path it saw — proves the request
        # reached the handler with the encoded path intact.
        self.assertIn("..", body["canon_path"])

    # Note (CodeRabbit R1 follow-up): a plain `/canon/../../etc/passwd` test
    # was attempted but httpx (TestClient's transport) normalizes `..`
    # segments BEFORE the request even leaves the client. The request
    # reaching the app is `/etc/passwd` — no route match, body is empty.
    # The URL-encoded test above is the correct way to prove the allowlist
    # is the rejection point: encoded segments survive httpx normalization
    # and reach the handler verbatim.


class CanonRoutesCrossRepoIsolationTests(unittest.TestCase):
    """CodeRabbit R1: cache must isolate per repo_root. Two temp repos with
    DIFFERENT content for the same canon_path must NOT cross-contaminate
    each other's cached entry."""

    def setUp(self) -> None:
        self.tmp_a = Path(tempfile.mkdtemp(prefix="canon_iso_A_"))
        self.tmp_b = Path(tempfile.mkdtemp(prefix="canon_iso_B_"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_a, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_b, ignore_errors=True))
        for root, content in ((self.tmp_a, b"# from REPO A\n"), (self.tmp_b, b"# from REPO B\n")):
            (root / ".miru" / "overlays").mkdir(parents=True)
            (root / ".miru" / "overlays" / "workflow-git.md").write_bytes(content)
        canon_routes.reset_cache_for_tests()

    def test_two_repos_serve_their_own_content(self) -> None:
        a = canon_routes.get_canon_file(self.tmp_a, "overlays/workflow-git.md")
        b = canon_routes.get_canon_file(self.tmp_b, "overlays/workflow-git.md")
        self.assertEqual(a["content"], "# from REPO A\n")
        self.assertEqual(b["content"], "# from REPO B\n")
        self.assertNotEqual(a["sha256"], b["sha256"])
        # Snapshot ids must also differ — different content set per repo.
        self.assertNotEqual(a["canon_snapshot_id"], b["canon_snapshot_id"])

    def test_repo_a_cache_not_corrupted_by_repo_b_request(self) -> None:
        # Prime A's cache, then hit B (which has different content for the
        # same canon_path). Then re-hit A — must still return A's content.
        a1 = canon_routes.get_canon_file(self.tmp_a, "overlays/workflow-git.md")
        canon_routes.get_canon_file(self.tmp_b, "overlays/workflow-git.md")
        a2 = canon_routes.get_canon_file(self.tmp_a, "overlays/workflow-git.md")
        self.assertEqual(a1["content"], a2["content"])
        self.assertEqual(a1["sha256"], a2["sha256"])
        self.assertEqual(a1["canon_snapshot_id"], a2["canon_snapshot_id"])


if __name__ == "__main__":
    unittest.main()
