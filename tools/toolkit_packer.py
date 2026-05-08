"""
toolkit_packer.py -- Task-aware context injection for dispatch prompts.

Analyzes a Linear ticket's title, description, and labels, then produces a
structured context block pointing the worker at the most relevant files,
tools, and service boundaries. Designed to be called from the dispatch
flow so workers start with a "briefcase" instead of blind exploration.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict


class ToolkitContext(TypedDict):
    relevant_files: list[str]
    relevant_tools: list[str]
    service_boundaries: list[str]
    dont_touch: list[str]
    read_only: list[str]
    context_block: str


_SIGNAL_RULES: list[dict[str, Any]] = [
    {
        "name": "dispatch_listener",
        "keywords": [r"\bdispatch", r"\blistener", r"\bspawn", r"\bworktree"],
        "files": [
            "services/dispatch_listener/src/index.js",
            "services/dispatch_listener/src/spawn.js",
            "services/dispatch_listener/src/worktree.js",
            "services/dispatch_listener/src/receipt.js",
            "services/dispatch_listener/src/allowlist.js",
        ],
        "tools": ["tools/orchestrator/stall_detector.py", "tools/orchestrator/recovery_router.py"],
        "services": ["services/dispatch_listener/"],
    },
    {
        "name": "n8n_workflows",
        "keywords": [r"\bn8n\b", r"\bworkflow", r"\bwatcher\b.*workflow", r"\brouter\b"],
        "files": [],
        "tools": [],
        "services": ["docker/n8n/workflows/"],
        "extra_note": "Adopted lesson: test JS as it lives in workflow JSON (PRO-189).",
    },
    {
        "name": "miru_ai",
        "keywords": [r"\bmiru.ai\b", r"\bmiru_ai\b", r"\bollama\b", r"\bcard.?lookup"],
        "files": ["miru_ai/core/", "miru_ai/workers/"],
        "tools": ["tools/miru_ai.py"],
        "services": ["miru_ai/"],
    },
    {
        "name": "pm_storefront",
        "keywords": [r"\bstorefront\b", r"\bpm\b.*dashboard", r"\bcard.?browse"],
        "files": ["pm/storefront/", "pm/templates/"],
        "tools": [],
        "services": ["pm/"],
    },
    {
        "name": "mcp_gateway",
        "keywords": [r"\bgateway\b", r"\bmcp\b.*tool", r"\btool.?profile"],
        "files": [
            "tools/miru_mcp_gateway/server.py",
            "tools/miru_mcp_gateway/profiles.py",
            "tools/miru_mcp_gateway/config.py",
        ],
        "tools": [],
        "services": ["tools/miru_mcp_gateway/"],
    },
    {
        "name": "gatekeeper",
        "keywords": [r"\bgatekeeper\b", r"\bgovernance\b", r"\bfrontmatter\b"],
        "files": ["gatekeeper/core.py", "gatekeeper/frontmatter.py", "gatekeeper/forwarder.py"],
        "tools": [],
        "services": ["gatekeeper/"],
    },
    {
        "name": "linear_tools",
        "keywords": [r"\blinear\b.*ticket", r"\blinear\b.*api", r"\bboard.?hygiene"],
        "files": [],
        "tools": [
            "tools/sub_ticket_creator.py",
            "tools/parent_watcher.py",
            "tools/complexity_classifier.py",
        ],
        "services": [],
    },
    {
        "name": "card_catalog",
        "keywords": [r"\bcard.?catalog\b", r"\bcatalog\b.*db", r"\bingestion\b"],
        "files": ["miru_ai/ingestion/"],
        "tools": [],
        "services": ["miru_ai/ingestion/"],
        "read_only_files": ["data/card_catalog.db"],
    },
    {
        "name": "hygiene_tooling",
        "keywords": [r"\bhygiene\b", r"\blint\b", r"\bformat\b", r"\bpre.?commit"],
        "files": [".pre-commit-config.yaml", "pyproject.toml", ".github/workflows/hygiene.yml"],
        "tools": ["tools/check_worktree_clean.py"],
        "services": [],
    },
    {
        "name": "completion_system",
        "keywords": [r"\bcompletion.?marker", r"\bcompletion.?log", r"\bheartbeat"],
        "files": ["data/cc_completion_log.jsonl", "data/cc_heartbeat_log.jsonl"],
        "tools": ["tools/emit_completion.py", "tools/emit_heartbeat.py"],
        "services": [],
    },
    {
        "name": "memory_system",
        "keywords": [r"\bmemory\b.*system", r"\bmiru.?memory\b", r"\bsqlite\b.*memory"],
        "files": [],
        "tools": ["tools/miru_mcp_gateway/server.py"],
        "services": [],
    },
    {
        "name": "telegram_ops",
        "keywords": [r"\btelegram\b", r"\bcallback\b.*handler", r"\bw7\b"],
        "files": [],
        "tools": [],
        "services": ["docker/n8n/workflows/"],
    },
    {
        "name": "windows_ops",
        "keywords": [r"\bscheduled.?task", r"\bwindows\b.*service", r"\bnssm\b", r"\bwatchdog\b"],
        "files": ["windows/startup_all.ps1"],
        "tools": [],
        "services": ["windows/"],
    },
]

GLOBAL_DONT_TOUCH = [
    ".env",
    ".mcp.json",
    "data/card_catalog.db",
    "data/miru_memory.db",
    "card_catalog.db",
]

GLOBAL_READ_ONLY = [
    "data/card_catalog.db",
    "data/cc_completion_log.jsonl",
    "data/cc_heartbeat_log.jsonl",
    "data/routing_history.jsonl",
    "data/pending_callbacks.jsonl",
    "data/dispatch_dlq.jsonl",
    "data/agent_decisions.jsonl",
    "data/github_resource_ledger.jsonl",
    "data/drift_scanner_log.jsonl",
    "data/vp_ops_supervision.jsonl",
]


def _match_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(re.search(kw, text_lower) for kw in keywords)


def _dedupe_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def pack_toolkit(
    ticket_title: str,
    ticket_description: str = "",
    labels: list[str] | None = None,
    service_dirs: list[str] | None = None,
) -> ToolkitContext:
    labels = labels or []
    service_dirs = service_dirs or []
    combined_text = f"{ticket_title} {ticket_description} {' '.join(labels)}"

    matched_files: list[str] = []
    matched_tools: list[str] = []
    matched_services: list[str] = []
    matched_read_only: list[str] = []
    extra_notes: list[str] = []

    for rule in _SIGNAL_RULES:
        if _match_keywords(combined_text, rule["keywords"]):
            matched_files.extend(rule.get("files", []))
            matched_tools.extend(rule.get("tools", []))
            matched_services.extend(rule.get("services", []))
            matched_read_only.extend(rule.get("read_only_files", []))
            if rule.get("extra_note"):
                extra_notes.append(rule["extra_note"])

    for sdir in service_dirs:
        sdir_clean = sdir.rstrip("/") + "/"
        matched_services.append(sdir_clean)
        test_glob = f"tests/test_{sdir.rstrip('/').replace('/', '_')}*"
        matched_files.append(test_glob)

    matched_files = _dedupe_ordered(matched_files)
    matched_tools = _dedupe_ordered(matched_tools)
    matched_services = _dedupe_ordered(matched_services)
    matched_read_only = _dedupe_ordered(GLOBAL_READ_ONLY + matched_read_only)

    dont_touch = list(GLOBAL_DONT_TOUCH)

    context_block = _format_context_block(
        matched_files, matched_tools, matched_services, dont_touch, matched_read_only, extra_notes
    )

    return ToolkitContext(
        relevant_files=matched_files,
        relevant_tools=matched_tools,
        service_boundaries=matched_services,
        dont_touch=dont_touch,
        read_only=matched_read_only,
        context_block=context_block,
    )


def _format_context_block(
    files: list[str],
    tools: list[str],
    services: list[str],
    dont_touch: list[str],
    read_only: list[str],
    notes: list[str],
) -> str:
    sections: list[str] = ["## Toolkit Context (auto-generated)"]

    if services:
        sections.append("\n### Service boundaries (in scope)")
        for s in services:
            sections.append(f"- `{s}`")

    if files:
        sections.append("\n### Start here (relevant files)")
        for f in files:
            sections.append(f"- `{f}`")

    if tools:
        sections.append("\n### Useful tools")
        for t in tools:
            sections.append(f"- `{t}`")

    if dont_touch:
        sections.append("\n### Do NOT modify")
        for d in dont_touch:
            sections.append(f"- `{d}`")

    if read_only:
        sections.append("\n### Read-only (append-only or protected)")
        for r in read_only:
            sections.append(f"- `{r}`")

    if notes:
        sections.append("\n### Notes")
        for n in notes:
            sections.append(f"- {n}")

    return "\n".join(sections)


def main() -> None:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Pack task-aware context for a dispatch prompt.")
    parser.add_argument("title", help="Ticket title")
    parser.add_argument("--description", default="", help="Ticket description")
    parser.add_argument("--labels", default="", help="Comma-separated labels")
    parser.add_argument("--dirs", default="", help="Comma-separated service directories")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    labels = [lbl.strip() for lbl in args.labels.split(",") if lbl.strip()]
    dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]

    result = pack_toolkit(args.title, args.description, labels, dirs)

    if args.json_output:
        print(json.dumps(dict(result), indent=2))
    else:
        print(result["context_block"])


if __name__ == "__main__":
    main()
