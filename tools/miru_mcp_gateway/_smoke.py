"""Local smoke test for the Miru MCP Gateway.

Stage 1 section: filesystem deny rules (in-process, no HTTP).
Stage 2 section: import sanity + path-string deny + redact patterns +
log-allowlist refusal + GitHub allowlist helper. No outbound HTTP calls.

The deny decisions live in Python (stdio_mcp + the fs_tools monkey-patch +
fs_tools.is_denied_path_string + system_tools.APPROVED_LOG_FILES check).
The Streamable HTTP layer only serializes arguments; it does not
participate in deny logic. Any deny that fires here fires identically over
HTTPS.

Usage (from repo root):
    python tools/miru_mcp_gateway/_smoke.py

Exit code 0 on ALL PASS. Non-zero on any failure.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Make `tools/` importable so fs_tools can pull in its sibling stdio module.
_PKG_DIR = Path(__file__).resolve().parent
_TOOLS_DIR = _PKG_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

# Gateway's FS root is D:\dev\miru; make sure the stdio module sees the same.
import os

os.environ.setdefault("MIRU_FS_ALLOW_ROOT", r"D:\dev\miru")

from miru_mcp_gateway import fs_tools  # noqa: E402
from miru_mcp_gateway.fs_tools import stdio_mcp  # noqa: E402


@dataclass
class Check:
    name: str
    fn: Callable[[], Any]
    expect: str  # "ok" | "deny" | "outside-root"
    hint: str = ""


def _safe(call: Callable[[], Any]) -> tuple[bool, str, Any]:
    try:
        result = call()
    except stdio_mcp.McpError as exc:
        return False, str(exc), None
    except Exception as exc:  # noqa: BLE001
        return False, f"UNEXPECTED {type(exc).__name__}: {exc}", None
    return True, "", result


def _assess(check: Check) -> tuple[bool, str]:
    ok, err, result = _safe(check.fn)

    if check.expect == "ok":
        if ok and isinstance(result, (str, dict, list)) and result:
            return True, "returned content"
        if ok:
            return False, f"expected content, got empty: {result!r}"
        return False, f"expected success, raised: {err}"

    # Denial branches -- call MUST raise McpError.
    if ok:
        return False, f"EXPECTED DENY, but call returned: {str(result)[:120]!r}"

    if check.expect == "deny":
        if "denied by Project Miru MCP policy" in err:
            return True, "denied by policy"
        if "outside allowed root" in err:
            # Segment/traversal denies may also surface as outside-root;
            # accept either form for "deny" rows.
            return True, "outside allowed root"
        return False, f"wrong error: {err}"

    if check.expect == "outside-root":
        if "outside allowed root" in err:
            return True, "outside allowed root"
        return False, f"wrong error: {err}"

    return False, f"unknown expect value: {check.expect}"


def _build_checks() -> list[Check]:
    checks: list[Check] = []

    # --- Allowed read (sanity: a non-secret known file is reachable) ---
    checks.append(
        Check(
            name="allowed: read docs/pm/00_PRINCIPLES.md (head=5)",
            fn=lambda: fs_tools.fs_read_text_file("docs/pm/00_PRINCIPLES.md", head=5),
            expect="ok",
            hint="Harmless allowed file -- confirms the happy path isn't broken.",
        )
    )
    checks.append(
        Check(
            name="allowed: list_allowed_directories",
            fn=lambda: fs_tools.fs_list_allowed_directories(),
            expect="ok",
        )
    )

    # --- BLOCKER: filename-pattern denies ---
    checks.append(
        Check(
            name="deny: .env at repo root",
            fn=lambda: fs_tools.fs_read_text_file(".env"),
            expect="deny",
        )
    )
    checks.append(
        Check(
            name="deny: docker/n8n/.env (subpath .env)",
            fn=lambda: fs_tools.fs_read_text_file("docker/n8n/.env"),
            expect="deny",
        )
    )
    checks.append(
        Check(
            name="deny: data/card_catalog.db (DB suffix)",
            fn=lambda: fs_tools.fs_read_text_file("data/card_catalog.db"),
            expect="deny",
        )
    )

    # --- BLOCKER: key / pem patterns (conditional on file existing) ---
    root = stdio_mcp.ROOT
    key_file = root / "room.taila28611.ts.net.key"
    if key_file.exists():
        checks.append(
            Check(
                name="deny: room.taila28611.ts.net.key (*.key pattern)",
                fn=lambda: fs_tools.fs_read_text_file("room.taila28611.ts.net.key"),
                expect="deny",
            )
        )
    else:
        checks.append(
            Check(
                name="skip: no *.key at repo root -- nothing to test",
                fn=lambda: "skipped",
                expect="ok",
                hint="If you add a .key file later, re-run this smoke.",
            )
        )

    pem_candidates = list(root.glob("*.pem"))
    if pem_candidates:
        rel = pem_candidates[0].relative_to(root).as_posix()
        checks.append(
            Check(
                name=f"deny: {rel} (*.pem pattern)",
                fn=lambda rel=rel: fs_tools.fs_read_text_file(rel),
                expect="deny",
            )
        )

    # --- BLOCKER: path-segment denies ---
    checks.append(
        Check(
            name="deny: .git/config (segment .git)",
            fn=lambda: fs_tools.fs_read_text_file(".git/config"),
            expect="deny",
        )
    )
    checks.append(
        Check(
            name="deny: logs/ (segment logs) -- self-log readable would be critical",
            fn=lambda: fs_tools.fs_read_text_file(
                "logs/mcp_gateway_18766_stdout.log"
            ),
            expect="deny",
        )
    )

    # --- BLOCKER: directory listings must hide denied segments ---
    def _list_root_and_check() -> str:
        listing = fs_tools.fs_list_directory(".")
        # Each row is `[DIR] name` or `[FILE] name`. Match the name token
        # exactly so .gitignore doesn't false-positive for .git.
        leaked: list[str] = []
        for row in listing.splitlines():
            row = row.strip()
            if not row:
                continue
            # Drop the `[DIR]` / `[FILE]` prefix to get the bare entry name.
            name = row.split("]", 1)[-1].strip() if row.startswith("[") else row
            if name in fs_tools.ALL_DENIED_SEGMENTS:
                leaked.append(name)
        if leaked:
            raise stdio_mcp.McpError(
                f"LEAK: list_directory('.') exposed denied segments: {leaked}"
            )
        return listing

    checks.append(
        Check(
            name="allowed: list('.') -- must NOT leak .git or logs entries",
            fn=_list_root_and_check,
            expect="ok",
            hint="Segment filter also applies at listing time, not just at read.",
        )
    )

    def _tree_root_and_check() -> str:
        import json as _json

        tree = fs_tools.fs_directory_tree(".")
        # Walk the JSON tree; any node whose name is a denied segment is a leak.
        try:
            parsed = _json.loads(tree)
        except _json.JSONDecodeError as exc:
            raise stdio_mcp.McpError(f"tree output not JSON: {exc}") from exc

        leaked: list[str] = []

        def _walk(nodes: Any) -> None:
            if not isinstance(nodes, list):
                return
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                name = str(node.get("name", ""))
                if name in fs_tools.ALL_DENIED_SEGMENTS:
                    leaked.append(name)
                _walk(node.get("children"))

        _walk(parsed)
        if leaked:
            raise stdio_mcp.McpError(
                f"LEAK: directory_tree('.') exposed denied segments: {leaked}"
            )
        return tree

    checks.append(
        Check(
            name="allowed: tree('.') -- must NOT leak .git or logs nodes",
            fn=_tree_root_and_check,
            expect="ok",
        )
    )

    # --- BLOCKER: out-of-root paths ---
    checks.append(
        Check(
            name="outside-root: absolute C:\\Users\\Dreighto",
            fn=lambda: fs_tools.fs_read_text_file(r"C:\Users\Dreighto"),
            expect="outside-root",
        )
    )
    checks.append(
        Check(
            name="outside-root: relative ..\\..\\Windows",
            fn=lambda: fs_tools.fs_read_text_file(r"..\..\Windows"),
            expect="outside-root",
        )
    )
    checks.append(
        Check(
            name="outside-root: posix-style ../../Windows",
            fn=lambda: fs_tools.fs_read_text_file("../../Windows"),
            expect="outside-root",
        )
    )

    return checks


def _run_stage2_checks() -> tuple[int, list[tuple[str, str]]]:
    """Stage 2 invariants: imports + path deny + redact + log allowlist.

    No outbound HTTP. No FastMCP server is started; we just exercise the
    Python-level helpers each tool relies on.
    """
    from miru_mcp_gateway import (
        github_tools,
        n8n_tools,
        redact as gw_redact,
        system_tools,
    )

    passed = 0
    failed: list[tuple[str, str]] = []

    def _record(name: str, ok: bool, detail: str) -> None:
        nonlocal passed
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {name}")
        if detail:
            print(f"         -> {detail}")
        if ok:
            passed += 1
        else:
            failed.append((name, detail))

    # 1. Tool count manifests are non-empty for every category.
    for label, mod, expected in (
        ("fs_tools.TOOL_FUNCTIONS",     fs_tools,      9),
        ("system_tools.TOOL_FUNCTIONS", system_tools,  3),
        ("github_tools.TOOL_FUNCTIONS", github_tools,  7),
        ("n8n_tools.TOOL_FUNCTIONS",    n8n_tools,     5),
    ):
        actual = len(mod.TOOL_FUNCTIONS)
        _record(
            f"manifest: {label} == {expected}",
            actual == expected,
            f"got {actual}",
        )

    # 2. is_denied_path_string mirrors the fs deny patterns for remote paths.
    deny_paths = [
        ".env",
        "config/.env.production",
        "secrets/foo.pem",
        "id_rsa",
        ".git/config",
        "logs/anything.log",
        "node_modules/dep/package.json",
    ]
    for p in deny_paths:
        _record(
            f"github path deny: {p}",
            fs_tools.is_denied_path_string(p),
            "denied",
        )

    # 3. Allowed remote paths must not be wrongly denied.
    for p in ("README.md", "src/app.py", "docs/pm/00_PRINCIPLES.md"):
        _record(
            f"github path allowed: {p}",
            not fs_tools.is_denied_path_string(p),
            "not denied",
        )

    # 4. redact pattern scrubs catch known token shapes.
    samples = [
        ("ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "<REDACTED:GH_TOKEN>"),
        (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz1234567890",
            "Bearer <REDACTED:BEARER>",
        ),
        (
            "https://hook.example.com/webhook/abcdefghijklmnopqrst",
            "<REDACTED:N8N_WEBHOOK_URL>",
        ),
        (
            "https://api.telegram.org/bot12345:AAFakeFakeFakeFakeFake-Token",
            "<REDACTED:TG_BOT_URL>",
        ),
    ]
    for raw, expected_marker in samples:
        out = gw_redact.redact(raw)
        _record(
            f"redact pattern: {expected_marker}",
            expected_marker in out,
            f"output={out[:80]!r}",
        )

    # 5. system_tail_safe_log refuses path traversal / arbitrary names.
    bad_names = ["../etc/passwd", "logs/foo.log", "anything", ".env"]
    for n in bad_names:
        try:
            system_tools.system_tail_safe_log(n)
            _record(f"system_tail_safe_log refuses: {n!r}", False, "did NOT raise")
        except stdio_mcp.McpError as exc:
            _record(
                f"system_tail_safe_log refuses: {n!r}",
                "log not approved" in str(exc),
                str(exc),
            )

    # 6. system_tail_safe_log('') returns the approved-name list.
    listing = system_tools.system_tail_safe_log("")
    _record(
        "system_tail_safe_log('') lists approved names",
        '"approved_logs"' in listing,
        listing[:80],
    )

    # 7. github allowlist helper enforces patterns when set.
    saved = (github_tools._TOKEN, github_tools._ALLOWLIST)
    try:
        github_tools._TOKEN = "test-token-not-real"
        github_tools._ALLOWLIST = ("Dreighto/*",)
        try:
            github_tools._assert_repo_allowed("Dreighto", "miru")
            _record("github allowlist allows Dreighto/miru", True, "allowed")
        except stdio_mcp.McpError as exc:
            _record("github allowlist allows Dreighto/miru", False, str(exc))
        try:
            github_tools._assert_repo_allowed("torvalds", "linux")
            _record(
                "github allowlist refuses torvalds/linux", False, "did NOT raise"
            )
        except stdio_mcp.McpError as exc:
            _record(
                "github allowlist refuses torvalds/linux",
                "not in allowlist" in str(exc),
                str(exc),
            )
        # Empty allowlist allows anything.
        github_tools._ALLOWLIST = ()
        try:
            github_tools._assert_repo_allowed("torvalds", "linux")
            _record("github empty allowlist allows any", True, "allowed")
        except stdio_mcp.McpError as exc:
            _record("github empty allowlist allows any", False, str(exc))
    finally:
        github_tools._TOKEN, github_tools._ALLOWLIST = saved

    return passed, failed


def main() -> int:
    print("=" * 72)
    print("Miru MCP Gateway -- local smoke test")
    print(f"FS root: {stdio_mcp.ROOT}")
    print(f"Stage 1 fs_tools count: {len(fs_tools.TOOL_FUNCTIONS)}")
    print("=" * 72)

    checks = _build_checks()

    passed = 0
    failed: list[tuple[str, str]] = []

    print("[Stage 1] filesystem deny rules")
    for check in checks:
        ok, detail = _assess(check)
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {check.name}")
        if detail:
            print(f"         -> {detail}")
        if ok:
            passed += 1
        else:
            failed.append((check.name, detail))

    stage1_total = len(checks)

    print("")
    print("[Stage 2] manifests + path deny + redact + log allowlist + GH allowlist")
    s2_passed, s2_failed = _run_stage2_checks()
    passed += s2_passed
    failed.extend(s2_failed)

    total = stage1_total + s2_passed + len(s2_failed)
    print("-" * 72)
    print(f"PASSED: {passed} / {total}")
    if failed:
        print("FAILED:")
        for name, detail in failed:
            print(f"  - {name}")
            print(f"      {detail}")
        print("=" * 72)
        print("RESULT: FAIL -- do NOT expose the Tailscale Funnel mount.")
        return 1

    print("=" * 72)
    print("RESULT: ALL PASS -- Stage 1 + Stage 2 invariants enforced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
