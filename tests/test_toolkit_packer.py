"""Tests for tools/toolkit_packer.py"""

from __future__ import annotations

from tools.toolkit_packer import (
    GLOBAL_DONT_TOUCH,
    GLOBAL_READ_ONLY,
    _match_keywords,
    pack_toolkit,
)


class TestMatchKeywords:
    def test_matches_basic_keyword(self):
        assert _match_keywords("fix the dispatch listener", [r"\bdispatch"])

    def test_no_match(self):
        assert not _match_keywords("update the readme", [r"\bdispatch"])

    def test_case_insensitive(self):
        assert _match_keywords("Fix N8N workflow", [r"\bn8n\b"])

    def test_regex_boundary(self):
        assert not _match_keywords("undispatchable", [r"\bdispatch\b"])
        assert _match_keywords("dispatch is broken", [r"\bdispatch\b"])


class TestPackToolkitSignals:
    def test_dispatch_keyword_maps_to_listener(self):
        ctx = pack_toolkit("Fix dispatch listener spawn bug")
        assert any("dispatch_listener" in f for f in ctx["relevant_files"])
        assert "services/dispatch_listener/" in ctx["service_boundaries"]

    def test_n8n_keyword_maps_to_workflows(self):
        ctx = pack_toolkit("Update the n8n workflow for routing")
        assert "docker/n8n/workflows/" in ctx["service_boundaries"]

    def test_miru_ai_keyword(self):
        ctx = pack_toolkit("Fix miru_ai card lookup timeout")
        assert "miru_ai/" in ctx["service_boundaries"]

    def test_gateway_keyword(self):
        ctx = pack_toolkit("Add new MCP tool to gateway")
        assert "tools/miru_mcp_gateway/" in ctx["service_boundaries"]

    def test_gatekeeper_keyword(self):
        ctx = pack_toolkit("Update gatekeeper frontmatter parser")
        assert "gatekeeper/" in ctx["service_boundaries"]

    def test_linear_keyword(self):
        ctx = pack_toolkit("Linear ticket board hygiene cleanup")
        assert any(
            "sub_ticket_creator" in t or "parent_watcher" in t for t in ctx["relevant_tools"]
        )

    def test_card_catalog_keyword(self):
        ctx = pack_toolkit("Fix card catalog ingestion pipeline")
        assert "data/card_catalog.db" in ctx["read_only"]

    def test_hygiene_keyword(self):
        ctx = pack_toolkit("Fix pre-commit lint failures")
        assert any("pre-commit" in f for f in ctx["relevant_files"])

    def test_completion_keyword(self):
        ctx = pack_toolkit("Fix completion marker emission")
        assert any("emit_completion" in t for t in ctx["relevant_tools"])

    def test_windows_keyword(self):
        ctx = pack_toolkit("Fix scheduled task focus stealing")
        assert "windows/" in ctx["service_boundaries"]

    def test_storefront_keyword(self):
        ctx = pack_toolkit("Update PM storefront card grid")
        assert "pm/" in ctx["service_boundaries"]

    def test_memory_system_keyword(self):
        ctx = pack_toolkit("Investigate sqlite memory system regression")
        assert any("miru_mcp_gateway/server.py" in t for t in ctx["relevant_tools"])

    def test_telegram_keyword(self):
        ctx = pack_toolkit("Fix telegram callback handler timeout")
        assert "docker/n8n/workflows/" in ctx["service_boundaries"]

    def test_no_signals_returns_minimal(self):
        ctx = pack_toolkit("Generic improvement task")
        assert ctx["relevant_files"] == []
        assert ctx["relevant_tools"] == []
        assert ctx["service_boundaries"] == []


class TestPackToolkitServiceDirs:
    def test_service_dirs_added_to_boundaries(self):
        ctx = pack_toolkit("Some task", service_dirs=["miru_ai/"])
        assert "miru_ai/" in ctx["service_boundaries"]

    def test_service_dirs_generate_test_glob(self):
        ctx = pack_toolkit("Some task", service_dirs=["miru_ai/"])
        assert any("test_miru_ai" in f for f in ctx["relevant_files"])

    def test_multiple_service_dirs(self):
        ctx = pack_toolkit("Some task", service_dirs=["miru_ai/", "tools/"])
        assert "miru_ai/" in ctx["service_boundaries"]
        assert "tools/" in ctx["service_boundaries"]


class TestPackToolkitLabels:
    def test_labels_contribute_to_matching(self):
        ctx = pack_toolkit("Fix something", labels=["n8n"])
        assert "docker/n8n/workflows/" in ctx["service_boundaries"]


class TestDontTouchAndReadOnly:
    def test_global_dont_touch_always_present(self):
        ctx = pack_toolkit("Any task")
        for item in GLOBAL_DONT_TOUCH:
            assert item in ctx["dont_touch"]

    def test_env_file_in_dont_touch(self):
        ctx = pack_toolkit("Any task")
        assert ".env" in ctx["dont_touch"]

    def test_global_read_only_always_present(self):
        ctx = pack_toolkit("Any task")
        for item in GLOBAL_READ_ONLY:
            assert item in ctx["read_only"]

    def test_no_secrets_in_context_block(self):
        ctx = pack_toolkit("Fix the dispatch listener with API key handling")
        block = ctx["context_block"]
        assert "LINEAR_API_KEY" not in block
        assert "ANTHROPIC_API_KEY" not in block
        assert "password" not in block.lower()


class TestContextBlock:
    def test_context_block_is_string(self):
        ctx = pack_toolkit("Fix dispatch listener")
        assert isinstance(ctx["context_block"], str)

    def test_context_block_has_header(self):
        ctx = pack_toolkit("Fix dispatch listener")
        assert "## Toolkit Context" in ctx["context_block"]

    def test_context_block_includes_matched_files(self):
        ctx = pack_toolkit("Fix dispatch listener spawn")
        assert "spawn.js" in ctx["context_block"]

    def test_context_block_includes_dont_touch(self):
        ctx = pack_toolkit("Any task")
        assert ".env" in ctx["context_block"]

    def test_empty_sections_omitted(self):
        ctx = pack_toolkit("Generic task with no matches")
        assert "### Start here" not in ctx["context_block"]
        assert "### Useful tools" not in ctx["context_block"]

    def test_extra_notes_included(self):
        ctx = pack_toolkit("Fix the n8n workflow JSON")
        assert "PRO-189" in ctx["context_block"]


class TestDeduplication:
    def test_no_duplicate_files(self):
        ctx = pack_toolkit(
            "Fix dispatch listener and spawn worktree issue",
            ticket_description="dispatch spawn worktree",
        )
        assert len(ctx["relevant_files"]) == len(set(ctx["relevant_files"]))

    def test_no_duplicate_services(self):
        ctx = pack_toolkit("Fix dispatch", service_dirs=["services/dispatch_listener/"])
        assert len(ctx["service_boundaries"]) == len(set(ctx["service_boundaries"]))
