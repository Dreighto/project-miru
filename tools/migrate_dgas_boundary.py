#!/usr/bin/env python3
"""migrate_dgas_boundary.py — DGAS audit chain boundary writer (LOS-10 Step 4).

Companion to tools/verify_dgas_boundary.py (reader). This script is invoked
ONCE at the LOS-10 cutover moment to produce the cryptographic anchor
between the project-miru v1 chain and the LogueOS-Orchestrator v2 chain.

WHAT THIS SCRIPT DOES (writer side):

1. **Freeze the legacy log**: copy data/cc_completion_log.jsonl to a
   timestamped, immutable path under data/dgas_boundary/. The source
   file remains in place; consumers running between cutover prep and
   the actual switch see no disruption.
2. **Walk the v1 chain** to compute terminal_hash, row_count, byte_length.
   If the chain doesn't validate (tamper, head-row deletion, mid-chain
   break), refuses to proceed.
3. **Build the boundary manifest**:
       {
         "legacy_log_path": "data/dgas_boundary/cc_completion_log.frozen-<ts>.jsonl",
         "terminal_block": <int>,         # 1-indexed count of chained rows
         "terminal_hash": "<sha256 hex>",
         "row_count": <int>,
         "byte_length": <int>,
         "hash_algorithm": "sha256",
         "new_chain_starts_at": <terminal_block + 1>,
         "new_chain_format_version": "DGAS_V2",
         "created_at_utc": "<ISO 8601>",
         "canon_snapshot_id_at_cutover": "<sha256 hex from gateway>",
         "writer_script": "tools/migrate_dgas_boundary.py",
         "writer_version": "1"
       }
4. **Sign the manifest** (optional, --signing-key): produce
   DGAS_BOUNDARY_MANIFEST.json.sig via ssh-keygen -Y sign, plus the
   `allowed_signers` file. SSH-signed because the operator already
   has SSH keys; no new key infrastructure required.
5. **Create a signed git tag** (optional, --git-tag): annotated, GPG-
   or SSH-signed tag pinned to HEAD. Records the boundary in git
   history.
6. **Initialize the v2 chain file**: creates an empty
   data/cc_completion_log.v2.jsonl (or whatever path the operator
   specifies). The next dispatch worker after cutover writes the
   first v2 row via append_v2_chained, anchored to the manifest's
   terminal_hash + new_chain_starts_at.

USAGE (dry run first, ALWAYS):

    python tools/migrate_dgas_boundary.py \\
        --canon-snapshot-id <hex64> \\
        --output-dir data/dgas_boundary \\
        --dry-run --verbose

Then for the real cutover:

    python tools/migrate_dgas_boundary.py \\
        --canon-snapshot-id <hex64> \\
        --output-dir data/dgas_boundary \\
        --signing-key ~/.ssh/id_ed25519 \\
        --git-tag dgas-cutover-v1 \\
        --verbose

Exit codes:
    0 — boundary produced + verified
    1 — chain failed to validate, file I/O error, or signing failed
    2 — usage error (missing arg, invalid value)

POST-RUN VERIFICATION (always do this):

    python tools/verify_dgas_boundary.py \\
        --legacy-log <output-dir>/cc_completion_log.frozen-<ts>.jsonl \\
        --manifest   <output-dir>/DGAS_BOUNDARY_MANIFEST.json \\
        --new-log    <output-dir>/cc_completion_log.v2.jsonl \\
        --signature  <output-dir>/DGAS_BOUNDARY_MANIFEST.json.sig \\
        --require-signature \\
        --verbose

DESIGN NOTES:

- Idempotency: re-running with --output-dir pointing at a previous run
  REFUSES to overwrite unless --force is passed. The boundary should be
  a one-time event; accidental overwrites would destroy the audit anchor.
- Atomicity: each output file is written to a .tmp sibling first then
  renamed. A crash mid-write leaves no partial manifest claiming to be valid.
- Determinism: same legacy log + same canon_snapshot_id → byte-identical
  manifest (modulo the created_at_utc timestamp). The timestamp can be
  pinned via --created-at-utc for reproducibility checks.
- This script is in project-miru. After cutover, it's run from a clean
  checkout of project-miru at the cutover commit; the output is committed
  to LogueOS-Orchestrator as the bootstrap anchor.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Reuse the v1 chain reader from the project's existing audit_chain module
# so the writer can NEVER drift from the live chain semantics. The verifier
# is independent on purpose (separate trust root); the writer can share code
# with the project because if the writer is compromised, the verifier still
# catches it.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_chain import _hash_body

CHAIN_FIELD_PREV = "prev_hash"
CHAIN_FIELD_HASH = "row_hash"

WRITER_VERSION = "1"
NEW_CHAIN_FORMAT_VERSION = "DGAS_V2"
HASH_ALGORITHM = "sha256"

# Canonical filenames inside the output directory. Keep stable — the
# verifier docs reference them by name in operator runbooks.
MANIFEST_NAME = "DGAS_BOUNDARY_MANIFEST.json"
SIGNATURE_NAME = "DGAS_BOUNDARY_MANIFEST.json.sig"
ALLOWED_SIGNERS_NAME = "allowed_signers"
V2_LOG_NAME = "cc_completion_log.v2.jsonl"


@dataclass
class ChainSummary:
    """Result of walking the v1 chain. terminal_hash is None for empty logs."""

    terminal_hash: str | None
    row_count: int
    byte_length: int
    legacy_prefix_rows: int


def _walk_v1_chain_strict(path: Path) -> ChainSummary:
    """Walk the v1 chain. Raises ValueError on any break or tamper.

    Stricter than the audit_chain.validate_chain: refuses to proceed if
    ANY chained row fails verification, head-row anchor is wrong, or a
    legacy row appears mid-chain. The cutover must not happen on a
    broken chain.
    """
    if not path.exists():
        raise ValueError(f"legacy log not found: {path}")
    byte_length = path.stat().st_size

    prev_hash: str | None = None
    row_count = 0
    legacy_prefix_rows = 0
    seen_chained = False
    terminal_hash: str | None = None

    with path.open("r", encoding="utf-8") as fh:
        for idx, raw in enumerate(fh):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"line {idx}: parse error: {exc}") from exc
            if not isinstance(obj, dict):
                # ValueError is the function's single error vocabulary (bad
                # input value, not a type error per ruff TRY004's suggestion).
                raise ValueError(f"line {idx}: parsed JSON is not an object")  # noqa: TRY004

            rh = obj.get(CHAIN_FIELD_HASH)
            if not isinstance(rh, str) or not rh:
                if seen_chained:
                    raise ValueError(
                        f"line {idx}: legacy row (no row_hash) appears after chained rows began"
                    )
                legacy_prefix_rows += 1
                continue

            seen_chained = True
            body = {k: v for k, v in obj.items() if k != CHAIN_FIELD_HASH}
            expected_hash = _hash_body(body)
            if expected_hash != rh:
                raise ValueError(
                    f"line {idx}: row_hash mismatch (declared {rh}, computed {expected_hash})"
                )

            declared_prev = body.get(CHAIN_FIELD_PREV)
            if row_count == 0:
                if declared_prev is not None:
                    raise ValueError(
                        f"line {idx}: first chained row must declare prev_hash=None "
                        f"(declared {declared_prev!r})"
                    )
            else:
                if declared_prev != prev_hash:
                    raise ValueError(
                        f"line {idx}: prev_hash mismatch "
                        f"(declared {declared_prev!r}, expected {prev_hash!r})"
                    )

            row_count += 1
            prev_hash = rh
            terminal_hash = rh

    return ChainSummary(
        terminal_hash=terminal_hash,
        row_count=row_count,
        byte_length=byte_length,
        legacy_prefix_rows=legacy_prefix_rows,
    )


def _validate_canon_snapshot_id(snap_id: str) -> None:
    """canon_snapshot_id must be lowercase hex SHA-256 (64 chars). Matches
    the format check in services/dispatch_listener/src/canon_probe.js."""
    if not re.fullmatch(r"[0-9a-f]{64}", snap_id):
        raise ValueError(
            f"canon_snapshot_id must be 64-char lowercase hex SHA-256 — got "
            f"{snap_id!r} ({len(snap_id)} chars)"
        )


_ISO_UTC_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$")


def _parse_iso_utc(value: str) -> _dt.datetime:
    """Parse a strict ISO 8601 UTC timestamp (YYYY-MM-DDTHH:MM:SSZ).

    CR R4 CRITICAL: the resulting datetime is what gets re-rendered into
    the frozen-log filename. Strict regex match guarantees no `/`, no
    `..`, no whitespace, no non-ASCII — so the value can be safely
    interpolated into a filename without risk of escaping output_dir.

    Raises ValueError on any deviation from the canonical shape, on any
    out-of-range field (e.g. month 13), or on whitespace. The regex
    itself rejects path separators; datetime() then catches semantic
    out-of-range cases (Feb 30, etc.).
    """
    if not isinstance(value, str):
        # ValueError is the function's single error vocabulary; callers
        # catch ValueError, not TypeError. Matches the rest of this module.
        raise ValueError(  # noqa: TRY004
            f"created_at_utc must be a string in ISO 8601 UTC form "
            f"(YYYY-MM-DDTHH:MM:SSZ), got {type(value).__name__}"
        )
    m = _ISO_UTC_RE.match(value)
    if not m:
        raise ValueError(
            f"created_at_utc must match YYYY-MM-DDTHH:MM:SSZ exactly — got "
            f"{value!r}. (Used to derive the frozen-log filename; loose "
            "parsing would let path separators through.)"
        )
    yyyy, mm, dd, hh, mi, ss = (int(g) for g in m.groups())
    try:
        return _dt.datetime(yyyy, mm, dd, hh, mi, ss, tzinfo=_dt.UTC)
    except ValueError as exc:
        raise ValueError(
            f"created_at_utc has out-of-range component(s): {value!r} — {exc}"
        ) from exc


def _fsync_dir(dir_path: Path) -> None:
    """Best-effort directory fsync so a prior rename inside it is durable.

    POSIX-only behavior: NTFS journals rename metadata inherently, so
    os.fsync on a Windows directory handle raises PermissionError or
    similar — that's fine to swallow. POSIX filesystems (ext4, xfs)
    require this fsync for the rename to survive a power loss.

    Centralized here so both _atomic_write_bytes and _atomic_copy
    have identical crash semantics (CR R4: previously only _atomic_copy
    fsynced the directory; _atomic_write_bytes did not).
    """
    try:
        dir_fd = os.open(str(dir_path), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, PermissionError):
        pass


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write to path.tmp then rename. Crash-safe on any POSIX or NTFS.

    CR R4 (PR #182 combined): added parent-directory fsync after rename.
    Without it, on POSIX the rename metadata isn't durable until the
    containing directory is fsynced — power loss could lose the manifest,
    signature, allowed_signers, or the initialized v2 log even though the
    data file itself was fsynced.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    # On Windows, os.replace allows replacing an existing file atomically;
    # on POSIX, os.rename is atomic on same filesystem.
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _atomic_copy(src: Path, dst: Path) -> int:
    """Copy src to dst atomically. Returns byte_length of dst.

    Crash semantics: fsync data file before rename, then fsync parent
    directory after rename so both file content + directory metadata
    are durable. Identical pattern to _atomic_write_bytes (via _fsync_dir).
    """
    if not src.exists():
        raise ValueError(f"source file does not exist: {src}")
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, tmp)
    # fsync the data before the rename. Use os.open directly with O_RDWR
    # so the descriptor supports fsync on both POSIX and Windows. A
    # Python read-only file object's fileno() raises EBADF on os.fsync.
    fd = os.open(str(tmp), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, dst)
    _fsync_dir(dst.parent)
    return dst.stat().st_size


def _build_manifest(
    *,
    legacy_log_rel: str,
    summary: ChainSummary,
    canon_snapshot_id: str,
    created_at_utc: str,
) -> dict[str, Any]:
    """Construct the manifest dict. terminal_block = row_count (1-indexed
    last chained row). new_chain_starts_at = row_count + 1."""
    return {
        # Required fields (verified by tools/verify_dgas_boundary.py)
        "legacy_log_path": legacy_log_rel,
        "terminal_block": summary.row_count,
        "terminal_hash": summary.terminal_hash,
        "row_count": summary.row_count,
        "byte_length": summary.byte_length,
        "hash_algorithm": HASH_ALGORITHM,
        "new_chain_starts_at": summary.row_count + 1,
        "new_chain_format_version": NEW_CHAIN_FORMAT_VERSION,
        # Additional provenance (not required by verifier; useful for audit)
        "created_at_utc": created_at_utc,
        "canon_snapshot_id_at_cutover": canon_snapshot_id,
        "writer_script": "tools/migrate_dgas_boundary.py",
        "writer_version": WRITER_VERSION,
        "legacy_prefix_rows": summary.legacy_prefix_rows,
    }


def _canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Manifest is canonicalized identically to chain rows so the signed
    bytes are deterministic. sort_keys=True, no whitespace."""
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _ssh_sign_manifest(
    manifest_path: Path, signing_key: Path, signers_id: str = "operator"
) -> tuple[Path, Path]:
    """SSH-sign the manifest. Produces .sig and writes allowed_signers.

    Uses `ssh-keygen -Y sign -f <key> -n file <manifest>` which writes
    `<manifest>.sig`. Then derives the public key with `ssh-keygen -y`
    and writes an `allowed_signers` line so the verifier can validate.

    Raises ValueError on any ssh-keygen failure with stderr included.
    """
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise ValueError(
            "ssh-keygen not found on PATH — cannot sign manifest. Install OpenSSH or "
            "pass --no-sign (signature optional but operators should verify integrity "
            "out-of-band if skipped)."
        )
    if not signing_key.exists():
        raise ValueError(f"signing key not found: {signing_key}")

    # Step A: sign
    try:
        result = subprocess.run(
            [
                ssh_keygen,
                "-Y",
                "sign",
                "-f",
                str(signing_key),
                "-n",
                "file",
                str(manifest_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError(f"ssh-keygen -Y sign invocation failed: {exc}") from exc
    if result.returncode != 0:
        raise ValueError(
            f"ssh-keygen -Y sign failed (rc={result.returncode}): {result.stderr.strip()}"
        )

    sig_path = manifest_path.with_suffix(manifest_path.suffix + ".sig")
    if not sig_path.exists():
        raise ValueError(f"ssh-keygen reported success but signature file not created: {sig_path}")

    # Step B: derive public key for allowed_signers
    try:
        pub = subprocess.run(
            [ssh_keygen, "-y", "-f", str(signing_key)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise ValueError(f"ssh-keygen -y invocation failed: {exc}") from exc
    if pub.returncode != 0:
        raise ValueError(f"ssh-keygen -y failed (rc={pub.returncode}): {pub.stderr.strip()}")

    allowed_signers_path = manifest_path.parent / ALLOWED_SIGNERS_NAME
    allowed_line = f"{signers_id} {pub.stdout.strip()}\n"
    _atomic_write_bytes(allowed_signers_path, allowed_line.encode("utf-8"))

    return sig_path, allowed_signers_path


def _create_signed_git_tag(tag_name: str, message: str, repo_dir: Path) -> str:
    """Create an annotated signed git tag at HEAD. Returns the tag's commit SHA.

    Uses `git tag -s` which uses the configured signing key (GPG by default,
    SSH if user.signingkey is set to an SSH key + gpg.format=ssh).
    Caller is responsible for ensuring the repo is in a clean state.
    """
    git = shutil.which("git")
    if not git:
        raise ValueError("git not found on PATH")

    # Check tag doesn't already exist
    existing = subprocess.run(
        [git, "tag", "-l", tag_name],
        capture_output=True,
        text=True,
        cwd=repo_dir,
        timeout=10,
    )
    if existing.stdout.strip():
        raise ValueError(
            f"git tag {tag_name!r} already exists. Refusing to overwrite — "
            f"the boundary tag is one-shot. Delete it manually with "
            f"`git tag -d {tag_name}` if you genuinely want to redo the cutover."
        )

    result = subprocess.run(
        [git, "tag", "-s", tag_name, "-m", message],
        capture_output=True,
        text=True,
        cwd=repo_dir,
        timeout=30,
    )
    if result.returncode != 0:
        raise ValueError(
            f"git tag -s failed (rc={result.returncode}): {result.stderr.strip()}\n"
            f"Ensure user.signingkey + gpg.format=ssh (or GPG key) are configured."
        )

    sha = subprocess.run(
        [git, "rev-list", "-n", "1", tag_name],
        capture_output=True,
        text=True,
        cwd=repo_dir,
        timeout=10,
    )
    if sha.returncode != 0:
        raise ValueError(f"git rev-list failed: {sha.stderr.strip()}")
    return sha.stdout.strip()


def migrate(
    *,
    legacy_log: Path,
    output_dir: Path,
    canon_snapshot_id: str,
    signing_key: Path | None = None,
    git_tag: str | None = None,
    git_repo_dir: Path | None = None,
    created_at_utc: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute the boundary migration. Returns a result dict describing
    every artifact produced (or that would be produced under --dry-run).

    Raises ValueError on validation failures, file I/O errors, or any
    refusal-to-proceed condition. Caller (main) translates these to exit 1.
    """
    # Step 0: validate inputs
    _validate_canon_snapshot_id(canon_snapshot_id)
    legacy_log = legacy_log.resolve()
    output_dir = output_dir.resolve()
    # CR R4 CRITICAL (PR #182 combined): validate created_at_utc as a real
    # ISO 8601 UTC timestamp and re-derive it from the parsed datetime,
    # rather than string-splicing the raw CLI argument into the output
    # filename. An attacker (or careless operator) supplying a value like
    # "2026-01-01T00:00:00Z/../escape" could otherwise produce a frozen_name
    # containing path separators and escape output_dir.
    if created_at_utc is None:
        created_at_utc = _dt.datetime.now(tz=_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        parsed_ts = _parse_iso_utc(created_at_utc)
        # Canonicalize back to the strict ISO 8601 Z form. This both
        # normalizes the value (drops microseconds, enforces 'Z' suffix)
        # AND ensures the result has no path separators.
        created_at_utc = parsed_ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Step 1: walk and validate the v1 chain
    if verbose:
        print(f"[migrate] walking v1 chain: {legacy_log}", file=sys.stderr)
    summary = _walk_v1_chain_strict(legacy_log)
    if summary.terminal_hash is None:
        raise ValueError(
            f"legacy log has no chained rows ({summary.legacy_prefix_rows} legacy-prefix rows). "
            "Cannot establish a boundary anchor without at least one chained row."
        )
    if verbose:
        print(
            f"[migrate] v1 chain valid — row_count={summary.row_count}, "
            f"byte_length={summary.byte_length}, terminal={summary.terminal_hash[:12]}…",
            file=sys.stderr,
        )

    # Step 2: build paths for output artifacts
    ts_safe = created_at_utc.replace(":", "").replace("-", "").replace("Z", "Z")
    frozen_name = f"cc_completion_log.frozen-{ts_safe}.jsonl"
    frozen_path = output_dir / frozen_name
    manifest_path = output_dir / MANIFEST_NAME
    v2_log_path = output_dir / V2_LOG_NAME

    # Signature artifacts are written conditionally (only when --signing-key
    # is passed). If a previous run left them behind and this run is unsigned,
    # the output_dir would look signed but the .sig wouldn't match the new
    # manifest. --require-signature would then fail late at verification time,
    # which is a confusing way to find out the directory state is inconsistent.
    # CR R4 minor finding: track them alongside the other artifacts so --force
    # clears them too, AND refuse-without-force catches them.
    signature_path = manifest_path.with_suffix(manifest_path.suffix + ".sig")
    allowed_signers_path = output_dir / ALLOWED_SIGNERS_NAME

    # Artifacts that count toward "directory is already populated":
    #   - frozen_path / manifest_path / v2_log_path: always written
    #   - signature_path / allowed_signers_path: written only when signing
    # On an UNSIGNED rerun, leaving stale sig artifacts behind is dangerous
    # (looks signed but isn't). So we include them in the overwrite check
    # regardless of whether THIS run is signing. --force clears all of them.
    artifact_paths = [frozen_path, manifest_path, v2_log_path]
    if signing_key is None:
        # Unsigned run — treat any pre-existing sig artifacts as stale.
        artifact_paths.extend([signature_path, allowed_signers_path])

    # Refuse to overwrite existing artifacts unless --force
    if not force:
        for p in artifact_paths:
            if p.exists():
                hint = "Pass --force to override (only use if you really mean to redo the cutover)."
                if signing_key is None and p in (signature_path, allowed_signers_path):
                    hint = (
                        "This run is UNSIGNED but a previous signed run left this file behind. "
                        "Leaving it would make the directory look signed while the .sig no longer "
                        "matches the new manifest. Pass --force to clear stale signature "
                        "artifacts, or pass --signing-key to re-sign."
                    )
                raise ValueError(f"refusing to overwrite existing artifact: {p}. {hint}")

    # With --force on an unsigned rerun, proactively REMOVE stale sig artifacts
    # so they can't make the directory look signed when it isn't.
    if force and signing_key is None:
        for p in (signature_path, allowed_signers_path):
            if p.exists():
                try:
                    p.unlink()
                    if verbose:
                        print(
                            f"[migrate] --force unsigned rerun: removed stale {p}",
                            file=sys.stderr,
                        )
                except OSError as exc:
                    raise ValueError(
                        f"failed to remove stale signature artifact {p}: {exc}"
                    ) from exc

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "canon_snapshot_id": canon_snapshot_id,
        "created_at_utc": created_at_utc,
        "legacy_log_input": str(legacy_log),
        "frozen_log_output": str(frozen_path),
        "manifest_output": str(manifest_path),
        "v2_log_output": str(v2_log_path),
        "v1_summary": {
            "row_count": summary.row_count,
            "byte_length": summary.byte_length,
            "terminal_hash": summary.terminal_hash,
            "legacy_prefix_rows": summary.legacy_prefix_rows,
        },
        "signature_output": None,
        "allowed_signers_output": None,
        "git_tag": None,
        "git_tag_commit": None,
    }

    if dry_run:
        if verbose:
            print(
                "[migrate] dry-run: not writing any files. Manifest preview:",
                file=sys.stderr,
            )
            # Build (but don't write) the manifest so the operator can review.
            legacy_rel_for_manifest = frozen_name  # post-cutover, relative to output_dir
            preview_manifest = _build_manifest(
                legacy_log_rel=legacy_rel_for_manifest,
                summary=summary,
                canon_snapshot_id=canon_snapshot_id,
                created_at_utc=created_at_utc,
            )
            print(json.dumps(preview_manifest, indent=2, sort_keys=True), file=sys.stderr)
        return result

    # Step 3: freeze the legacy log
    if verbose:
        print(f"[migrate] freezing legacy log → {frozen_path}", file=sys.stderr)
    frozen_size = _atomic_copy(legacy_log, frozen_path)
    if frozen_size != summary.byte_length:
        # Defensive: the legacy log changed between walk + copy. Refuse to
        # produce a manifest that doesn't match the frozen content.
        raise ValueError(
            f"legacy log changed during freeze: walk saw {summary.byte_length} bytes, "
            f"frozen file has {frozen_size}. Aborting; re-run when writers are quiesced."
        )

    # Step 4: re-walk the FROZEN log to compute terminal_hash from the
    # actual on-disk snapshot. If a race slipped past _atomic_copy's
    # post-condition, this will catch it.
    if verbose:
        print(f"[migrate] re-walking frozen log to confirm: {frozen_path}", file=sys.stderr)
    frozen_summary = _walk_v1_chain_strict(frozen_path)
    if frozen_summary.terminal_hash != summary.terminal_hash:
        raise ValueError(
            f"frozen log terminal_hash {frozen_summary.terminal_hash!r} != live walk "
            f"terminal {summary.terminal_hash!r}. Race or corruption — aborting."
        )

    # Step 5: build + write manifest
    manifest = _build_manifest(
        legacy_log_rel=frozen_name,
        summary=frozen_summary,
        canon_snapshot_id=canon_snapshot_id,
        created_at_utc=created_at_utc,
    )
    manifest_bytes = _canonical_manifest_bytes(manifest)
    if verbose:
        print(
            f"[migrate] writing manifest ({len(manifest_bytes)} bytes) → {manifest_path}",
            file=sys.stderr,
        )
    _atomic_write_bytes(manifest_path, manifest_bytes)

    # Step 6: initialize empty v2 log (writers will append the first row
    # via append_v2_chained anchored to manifest.terminal_hash)
    if verbose:
        print(f"[migrate] initializing empty v2 log → {v2_log_path}", file=sys.stderr)
    _atomic_write_bytes(v2_log_path, b"")

    # Step 7: optional SSH signature
    if signing_key is not None:
        if verbose:
            print(f"[migrate] signing manifest with {signing_key}", file=sys.stderr)
        sig_path, signers_path = _ssh_sign_manifest(manifest_path, signing_key)
        result["signature_output"] = str(sig_path)
        result["allowed_signers_output"] = str(signers_path)

    # Step 8: optional signed git tag
    if git_tag:
        repo_dir = git_repo_dir or Path.cwd()
        tag_message = (
            f"DGAS audit chain boundary at row {summary.row_count}\n\n"
            f"terminal_hash: {summary.terminal_hash}\n"
            f"canon_snapshot_id: {canon_snapshot_id}\n"
            f"created_at_utc: {created_at_utc}\n"
            f"manifest: {manifest_path.relative_to(repo_dir) if manifest_path.is_relative_to(repo_dir) else manifest_path}\n"
        )
        if verbose:
            print(f"[migrate] creating signed git tag {git_tag!r}", file=sys.stderr)
        tag_sha = _create_signed_git_tag(git_tag, tag_message, repo_dir)
        result["git_tag"] = git_tag
        result["git_tag_commit"] = tag_sha

    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=(__doc__ or "DGAS boundary writer").splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--legacy-log",
        type=Path,
        default=Path("data/cc_completion_log.jsonl"),
        help="Path to the live v1 JSONL (default: data/cc_completion_log.jsonl)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/dgas_boundary"),
        help="Directory for frozen log + manifest + signature (default: data/dgas_boundary)",
    )
    p.add_argument(
        "--canon-snapshot-id",
        required=True,
        help="64-char lowercase hex SHA-256 canon snapshot id from the gateway probe",
    )
    p.add_argument(
        "--signing-key",
        type=Path,
        default=None,
        help="Path to SSH private key for ssh-keygen -Y sign (optional)",
    )
    p.add_argument(
        "--git-tag",
        default=None,
        help="Name for the annotated signed git tag (e.g. dgas-cutover-v1). Omit to skip tagging.",
    )
    p.add_argument(
        "--git-repo-dir",
        type=Path,
        default=None,
        help="Working directory for git operations (default: current directory)",
    )
    p.add_argument(
        "--created-at-utc",
        default=None,
        help="Pin the manifest timestamp (ISO 8601 Z). Default: current UTC.",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing artifacts in --output-dir (DANGEROUS — only for redo)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Walk the chain, validate, print the manifest that would be written; no file I/O",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print progress to stderr",
    )

    args = p.parse_args(argv)

    try:
        result = migrate(
            legacy_log=args.legacy_log,
            output_dir=args.output_dir,
            canon_snapshot_id=args.canon_snapshot_id,
            signing_key=args.signing_key,
            git_tag=args.git_tag,
            git_repo_dir=args.git_repo_dir,
            created_at_utc=args.created_at_utc,
            force=args.force,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
    except ValueError as exc:
        print(f"[migrate_dgas_boundary] FAILED: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # CR R4 major: docstring + CLI help promise "file I/O failures → exit 1",
        # but migrate() can raise OSError from _atomic_copy / _atomic_write_bytes
        # / fsync / dir creation on permissions or disk failures. Without this
        # handler, those escape as a Python traceback and break automation
        # around the cutover gate (operators scripting against the exit code
        # would see an unexpected non-1, non-0 exit). Surface as a single
        # stderr line + exit 1 to match the documented contract.
        print(f"[migrate_dgas_boundary] FAILED (I/O): {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
