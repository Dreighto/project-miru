"""Canon file HTTP routes for the MCP gateway.

LOS-10 Step 1: expose worker behavior canon (`.miru/overlays/`,
`.miru/reference/`, `miru-context/`, `CLAUDE.md`, `AGENTS.md`) over HTTP so
that dispatched workers can fetch their behavior rules from the gateway
instead of from the worktree filesystem.

Why this exists: the canon must move out of the target-repo worktrees so the
LogueOS-Orchestrator extraction (LOS-10) can detach the dispatch system from
`project-miru`. Filesystem-based canon (symlinks/junctions) was rejected on
Windows due to privilege requirements + worktree-cleanup file-lock failures.

This module is observation-only at this stage: workers do NOT yet fetch from
these endpoints. Step 2 of LOS-10 wires the worker-side fetch with the
fail-closed semantics (refuse to spawn if gateway unreachable).

Endpoints registered:

    GET /canon/<canon_path>     Single file + metadata + canon_snapshot_id.
                                <canon_path> is rooted at one of:
                                  overlays/<name>     -> .miru/overlays/<name>
                                  reference/<name>    -> .miru/reference/<name>
                                  context/<name>      -> miru-context/<name>
                                  root/CLAUDE.md      -> CLAUDE.md
                                  root/AGENTS.md      -> AGENTS.md
                                Files outside this allowlist are rejected with
                                404 (no leakage of which paths exist beyond
                                the allowlist).

    GET /canon-manifest         Full manifest of every canon file with its
                                sha256 + mtime_ns + byte_length, plus the
                                canon_snapshot_id. Workers call this once at
                                spawn to record reproducibility metadata.

The `canon_snapshot_id` is `SHA256(sorted("<canon_path>:<sha256>" lines))`
across all canon files. It changes when any single canon file changes.
Workers record it per task spawn so a later auditor can ask "exactly which
canon was in force when worker W3 acted on ticket T?" and get a
deterministic answer.

Caching: in-memory mtime-based cache keyed by absolute path. The cache is
invalidated lazily — every request restats every canon file (cheap on
Windows NTFS for ~40 small files; ~1-2 ms per snapshot recompute) and only
re-reads file content when its mtime_ns changes. This means a canon edit
takes effect on the very next request, no gateway restart required. If the
canon set ever grows large enough that this becomes hot, switch to a watched
notify-based cache; for now, simple-and-correct wins.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any

# Map of <canon_path> -> on-disk relative path under the repo root.
# Order doesn't matter for security (the resolution is exact-match), but
# keeping it grouped for readability matches the canon's own folder layout.
_CANON_LAYOUT: dict[str, str] = {
    # `.miru/overlays/` — task-type overlays
    "overlays/adopted-lessons.md": ".miru/overlays/adopted-lessons.md",
    "overlays/domain-ops.md": ".miru/overlays/domain-ops.md",
    "overlays/domain-ui.md": ".miru/overlays/domain-ui.md",
    "overlays/workflow-completion.md": ".miru/overlays/workflow-completion.md",
    "overlays/workflow-dispatch.md": ".miru/overlays/workflow-dispatch.md",
    "overlays/workflow-git.md": ".miru/overlays/workflow-git.md",
    # `.miru/reference/` — on-demand factual references
    "reference/database-rules.md": ".miru/reference/database-rules.md",
    "reference/file-placement.md": ".miru/reference/file-placement.md",
    "reference/linear-projects.md": ".miru/reference/linear-projects.md",
    "reference/multi-repo-onboarding.md": ".miru/reference/multi-repo-onboarding.md",
    "reference/ports-and-services.md": ".miru/reference/ports-and-services.md",
    "reference/restart-procedures.md": ".miru/reference/restart-procedures.md",
    "reference/roadmap.md": ".miru/reference/roadmap.md",
    "reference/source-of-truth.md": ".miru/reference/source-of-truth.md",
    # `miru-context/` — universal worker boot rules
    "context/THE_ONE_PIECE.md": "miru-context/THE_ONE_PIECE.md",
    "context/budget-governance.md": "miru-context/budget-governance.md",
    "context/canon-and-drift.md": "miru-context/canon-and-drift.md",
    "context/canon-contract.md": "miru-context/canon-contract.md",
    "context/ch-tool-operations.md": "miru-context/ch-tool-operations.md",
    "context/claude-operating-model.md": "miru-context/claude-operating-model.md",
    "context/concurrency-policy.md": "miru-context/concurrency-policy.md",
    "context/coordination-contract.md": "miru-context/coordination-contract.md",
    "context/guardrails.md": "miru-context/guardrails.md",
    "context/job-stewardship.md": "miru-context/job-stewardship.md",
    "context/kill-switch.md": "miru-context/kill-switch.md",
    "context/linear-triage-framework.md": "miru-context/linear-triage-framework.md",
    "context/loop-hardening-backlog.md": "miru-context/loop-hardening-backlog.md",
    "context/miru-protected-constraints.md": "miru-context/miru-protected-constraints.md",
    "context/miru-service-catalog.md": "miru-context/miru-service-catalog.md",
    "context/miru-vocab.md": "miru-context/miru-vocab.md",
    "context/operating-model.md": "miru-context/operating-model.md",
    "context/operator-profile.md": "miru-context/operator-profile.md",
    "context/operator-translation.md": "miru-context/operator-translation.md",
    "context/performance-scorecard.md": "miru-context/performance-scorecard.md",
    "context/retry-backoff.md": "miru-context/retry-backoff.md",
    "context/source-of-truth.md": "miru-context/source-of-truth.md",
    "context/state-handoff-log.md": "miru-context/state-handoff-log.md",
    "context/team-charter.md": "miru-context/team-charter.md",
    "context/worker-decision-layer.md": "miru-context/worker-decision-layer.md",
    "context/worker-roster.md": "miru-context/worker-roster.md",
    # Repo root — top-level worker rules
    "root/CLAUDE.md": "CLAUDE.md",
    "root/AGENTS.md": "AGENTS.md",
}

# Cache: (repo_root_str, canon_path) -> {mtime_ns, sha256, byte_length, content_bytes}
# Snapshot cache: repo_root_str -> {"id": ..., "fingerprint": ...}
#
# CodeRabbit R1: cache keys MUST include repo_root because get_canon_file()
# accepts a repo_root parameter and could be called with different roots
# (e.g., test temp dirs vs production repo). Without per-repo scoping, a test
# that loaded canon from /tmp/repo_A would corrupt the production cache.
# In production we only have one repo_root, but the defense matters for
# tests + future flexibility.
_CACHE_LOCK = threading.Lock()
_FILE_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_SNAPSHOT_CACHE: dict[str, dict[str, str]] = {}


def _repo_key(repo_root: Path) -> str:
    """Stable string key for a repo_root, used in cache keys."""
    return str(repo_root.resolve())


class CanonAccessError(Exception):
    """Base for canon resolution failures. Lets the HTTP handler return
    differentiated 404 payloads — operators debugging a missing canon entry
    need to know whether they typed a wrong path or whether the file
    actually disappeared."""


class NotAllowlistedError(CanonAccessError):
    """The requested canon_path is not in the static allowlist."""


class AllowlistedFileMissingError(CanonAccessError):
    """The canon_path is allowlisted but the underlying file has gone
    missing on disk (or vanished mid-read via TOCTOU race)."""


def _hash_bytes(data: bytes) -> str:
    """sha256 hex of arbitrary bytes."""
    return hashlib.sha256(data).hexdigest()


def _load_file(repo_key: str, canon_path: str, abs_path: Path) -> dict[str, Any] | None:
    """Load file content + compute metadata. Caller holds _CACHE_LOCK.

    Returns None if the file disappeared between an earlier exists() check
    and the stat()/read_bytes() inside this function. CodeRabbit R0: that
    TOCTOU window is real on a busy host (file cleanup, antivirus quarantine,
    operator editing) and an unguarded FileNotFoundError would 500 the
    request instead of cleanly 404-ing.

    CodeRabbit R1: repo_key is now part of the cache key so cross-repo
    collisions are impossible.
    """
    cache_key = (repo_key, canon_path)
    try:
        stat = abs_path.stat()
    except FileNotFoundError:
        # Drop any stale cached entry so a recreate-then-fetch sequence
        # doesn't keep serving the deleted file's old content.
        _FILE_CACHE.pop(cache_key, None)
        return None
    mtime_ns = stat.st_mtime_ns
    cached = _FILE_CACHE.get(cache_key)
    if cached and cached["mtime_ns"] == mtime_ns:
        return cached
    try:
        content_bytes = abs_path.read_bytes()
    except FileNotFoundError:
        _FILE_CACHE.pop(cache_key, None)
        return None
    entry = {
        "mtime_ns": mtime_ns,
        "byte_length": len(content_bytes),
        "sha256": _hash_bytes(content_bytes),
        "content_bytes": content_bytes,
    }
    _FILE_CACHE[cache_key] = entry
    return entry


def _refresh_snapshot(repo_root: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    """Recompute the canon snapshot id and per-file metadata.

    Returns (snapshot_id, per_file_metadata). Per-file metadata DOES NOT
    include `content_bytes` — that's cached separately and only sent when the
    per-file endpoint is called. Manifest endpoint sends only metadata so a
    worker can record everything in a single round-trip without paying the
    bandwidth of pulling all 42 files at spawn.

    Side effect: every canon file's entry is loaded into _FILE_CACHE under
    the (repo_key, canon_path) key. This is what get_canon_file relies on
    for its single-pass race-free read (CodeRabbit R1).

    Caller holds _CACHE_LOCK.
    """
    repo_key = _repo_key(repo_root)
    per_file_meta: dict[str, dict[str, Any]] = {}
    fingerprint_parts: list[str] = []  # also drives a quick "did anything change" check
    for canon_path, rel_path in _CANON_LAYOUT.items():
        abs_path = repo_root / rel_path
        # _load_file is itself TOCTOU-safe: returns None if the file is
        # missing or vanishes mid-read. We don't need an exists() pre-check.
        entry = _load_file(repo_key, canon_path, abs_path)
        if entry is None:
            # Canon file declared in layout but missing on disk. Should never
            # happen in production; defensive — surface via 'missing' marker.
            per_file_meta[canon_path] = {
                "mtime_ns": 0,
                "byte_length": 0,
                "sha256": "",
                "missing": True,
            }
            fingerprint_parts.append(f"{canon_path}:MISSING")
            continue
        per_file_meta[canon_path] = {
            "mtime_ns": entry["mtime_ns"],
            "byte_length": entry["byte_length"],
            "sha256": entry["sha256"],
        }
        fingerprint_parts.append(f"{canon_path}:{entry['sha256']}")

    fingerprint = "\n".join(sorted(fingerprint_parts))
    repo_snapshot = _SNAPSHOT_CACHE.setdefault(repo_key, {"id": "", "fingerprint": ""})
    if fingerprint == repo_snapshot["fingerprint"]:
        return repo_snapshot["id"], per_file_meta

    snapshot_id = _hash_bytes(fingerprint.encode("utf-8"))
    repo_snapshot["id"] = snapshot_id
    repo_snapshot["fingerprint"] = fingerprint
    return snapshot_id, per_file_meta


def get_canon_file(repo_root: Path, canon_path: str) -> dict[str, Any]:
    """Return the file content + metadata for one canon path.

    Raises:
        NotAllowlistedError: canon_path is not in the static allowlist.
        AllowlistedFileMissingError: canon_path is allowlisted but the
            underlying file is missing on disk (declared-but-not-present, or
            disappeared via TOCTOU race during read).

    The returned dict shape matches the JSON the HTTP handler will return.

    CodeRabbit R0: previously returned None for both error conditions, which
    forced the HTTP handler to use the same 404 payload for "you typed a
    wrong path" and "the canon file is gone" — operationally distinct
    failures. Distinct exceptions let the handler differentiate.

    CodeRabbit R1: previously read the file via _load_file then called
    _refresh_snapshot which re-read the same file. Between those two reads
    (in-process lock prevents in-process races but NOT OS-level mtime
    changes), the returned `entry.sha256` and `canon_snapshot_id` could be
    derived from different bytes-on-disk. Now: refresh first (loads all
    canon files in a single pass, populates _FILE_CACHE), then read the
    cached entry. Single source of truth.
    """
    if canon_path not in _CANON_LAYOUT:
        raise NotAllowlistedError(canon_path)
    repo_key = _repo_key(repo_root)
    cache_key = (repo_key, canon_path)
    with _CACHE_LOCK:
        snapshot_id, per_file_meta = _refresh_snapshot(repo_root)
        if per_file_meta[canon_path].get("missing"):
            raise AllowlistedFileMissingError(canon_path)
        entry = _FILE_CACHE.get(cache_key)
        if entry is None:
            # Defensive — _refresh_snapshot just populated this key. If we
            # got here something raced very tightly (cache eviction or
            # concurrent reset). Treat as missing.
            raise AllowlistedFileMissingError(canon_path)
    # CodeRabbit R2: guard the UTF-8 decode. Canon files SHOULD always be
    # UTF-8 .md text — we control the layout and the inputs. But defense in
    # depth: if a file gets corrupted (binary content, mangled BOM, partial
    # write during edit), an unguarded decode raises UnicodeDecodeError and
    # the handler returns 500 instead of a clean error. Reuse
    # AllowlistedFileMissingError per CR's guidance — semantically the file
    # IS present but unusable for canon purposes (workers can't act on
    # undecodable bytes), so the operator-facing semantics are the same as
    # "the file is gone."
    try:
        content = entry["content_bytes"].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AllowlistedFileMissingError(
            f"{canon_path}: file present but not valid UTF-8 "
            f"(snapshot_id={snapshot_id}, sha256={entry['sha256']}, "
            f"byte_length={entry['byte_length']}): {exc}"
        ) from exc
    return {
        "canon_path": canon_path,
        "content": content,
        "encoding": "utf-8",
        "sha256": entry["sha256"],
        "byte_length": entry["byte_length"],
        "mtime_ns": entry["mtime_ns"],
        "canon_snapshot_id": snapshot_id,
    }


def get_canon_manifest(repo_root: Path) -> dict[str, Any]:
    """Return the full canon manifest. Workers call this once at spawn to
    record reproducibility metadata."""
    with _CACHE_LOCK:
        snapshot_id, per_file_meta = _refresh_snapshot(repo_root)
    return {
        "canon_snapshot_id": snapshot_id,
        "files": per_file_meta,
        "file_count": len(per_file_meta),
    }


def reset_cache_for_tests() -> None:
    """Test-only helper: clear the in-process cache. Production code never
    calls this."""
    with _CACHE_LOCK:
        _FILE_CACHE.clear()
        _SNAPSHOT_CACHE.clear()


def register_canon_routes(mcp, repo_root: Path) -> None:
    """Attach GET /canon/<canon_path> and GET /canon-manifest to the MCP
    server's underlying Starlette app.

    Mirrors the registration pattern in `_register_health_route` in
    server.py. The Funnel layer is the secret check; 127.0.0.1 binding is
    what keeps these routes private.

    CodeRabbit R3: handlers offload the blocking disk I/O + hashing work to
    a threadpool via starlette.concurrency.run_in_threadpool. Without this,
    multiple concurrent canon requests would serialize through the async
    event loop (the work is sync + holds _CACHE_LOCK), starving every other
    HTTP route on the gateway during a canon read.
    """
    try:
        from starlette.concurrency import run_in_threadpool
        from starlette.responses import JSONResponse
    except ImportError as exc:  # pragma: no cover -- dependency invariant
        raise SystemExit(
            "FATAL: starlette is required (installed as a fastmcp dependency). "
            f"Original error: {exc}"
        ) from exc

    if not hasattr(mcp, "custom_route"):  # pragma: no cover -- dependency invariant
        raise SystemExit(
            "FATAL: this FastMCP version does not expose `custom_route` for "
            "attaching the /canon endpoints. Pin to fastmcp>=2.5,<3."
        )

    async def canon_file(request):
        # Starlette path params land in request.path_params. The route is
        # registered as `/canon/{canon_path:path}` so subdirectories (e.g.
        # `overlays/workflow-git.md`) come through intact.
        canon_path = request.path_params.get("canon_path", "")
        try:
            result = await run_in_threadpool(get_canon_file, repo_root, canon_path)
        except NotAllowlistedError:
            return JSONResponse(
                {"ok": False, "error": "not_in_canon_allowlist", "canon_path": canon_path},
                status_code=404,
            )
        except AllowlistedFileMissingError as exc:
            # Distinguishable from "wrong path" — operator should investigate
            # why a file declared in _CANON_LAYOUT is no longer on disk OR
            # is present but not valid UTF-8. CodeRabbit R3: log the
            # exception so the diagnostic info that get_canon_file built
            # into the message (snapshot_id, sha256, byte_length for the
            # corrupted-UTF-8 case) lands in dispatch_listener_stdout.log
            # via the gateway's own log stream — otherwise the operator
            # only sees a bare 404 with no clue what failed. Using print
            # to stdout matches the rest of this gateway's logging
            # convention (no central logger module — see server.py banner).
            print(
                f"[canon_routes] WARN allowlisted_file_missing: "
                f"canon_path={canon_path} detail={exc}",
                flush=True,
            )
            return JSONResponse(
                {"ok": False, "error": "allowlisted_file_missing", "canon_path": canon_path},
                status_code=404,
            )
        return JSONResponse({"ok": True, **result})

    async def canon_manifest(_request):
        manifest = await run_in_threadpool(get_canon_manifest, repo_root)
        return JSONResponse({"ok": True, **manifest})

    mcp.custom_route("/canon/{canon_path:path}", methods=["GET"])(canon_file)
    mcp.custom_route("/canon-manifest", methods=["GET"])(canon_manifest)


__all__ = [
    "AllowlistedFileMissingError",
    "CanonAccessError",
    "NotAllowlistedError",
    "get_canon_file",
    "get_canon_manifest",
    "register_canon_routes",
    "reset_cache_for_tests",
]
