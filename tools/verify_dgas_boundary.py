#!/usr/bin/env python3
"""verify_dgas_boundary.py — standalone DGAS audit chain boundary verifier.

LOS-10 Step 3 / part of the migration plan. Verifies cryptographic continuity
of the DGAS audit chain across the project-miru → LogueOS-Orchestrator
boundary, exactly the way the operator and auditors will run it after cutover.

DESIGN INTENT (architectural reviews, locked 2026-05-10):

- **Standalone**. Zero dependencies beyond Python stdlib. NO import from
  the orchestrator repo's modules. If someone modifies the orchestrator's
  audit_chain.py to lie, this verifier still computes the truth.
- **Auditor cannot be the culprit**. This script ships separately from the
  orchestrator repo (target: GitHub Gist, then potentially a dedicated
  audit-tools repo). A compromised orchestrator agent that tampered with
  logs would also have to compromise THIS script in a separate location
  to make the tampering pass validation.
- **Boring and deterministic**. Plain Python 3.9+ stdlib. Same answer on
  every machine. No clever data structures.

USAGE

    python verify_dgas_boundary.py \\
        --legacy-log /path/to/cc_completion_log.frozen.jsonl \\
        --manifest  /path/to/DGAS_BOUNDARY_MANIFEST.json \\
        --new-log   /path/to/cc_completion_log.jsonl

Exit codes:
    0 — chain valid across the boundary, every check passed
    1 — validation failure (specific cause printed to stderr; includes
        missing input files, malformed manifest, broken chains, signature
        failures, and any other content-level check)
    2 — usage error (missing required arg, invalid flag value, argparse
        exit)

WHAT IT VERIFIES

1. **Legacy v1 chain**: walk frozen.jsonl row-by-row, recomputing each
   row_hash from the body. Check prev_hash links forward correctly.
2. **Boundary manifest** sanity:
   - terminal_hash matches the legacy chain's final row_hash
   - row_count matches the legacy chain's chained row count
   - byte_length matches the legacy file's actual size
   - hash_algorithm == "sha256"
   - new_chain_format_version == "DGAS_V2" (CR R2: enforced)
3. **Boundary manifest signature** (optional, `--signature <path>` plus
   optional `--require-signature` to fail on absence):
   - Verify operator-held SSH key signed the manifest. Skipped if no
     `--signature` is passed and `--require-signature` is off.
   - If `--signature` IS passed but the file is missing or invalid,
     verification fails regardless of `--require-signature`.
4. **New v2 chain**: walk new-log row-by-row, recomputing each row_hash
   using the v2 formula (domain-separated, block-index-bound).
   - First v2 row's prev_hash MUST equal manifest.terminal_hash.
   - First v2 row's block_index MUST equal manifest.new_chain_starts_at.
   - Each subsequent row chains correctly.

V1 ALGORITHM (matches tools/audit_chain.py in project-miru):

    canonical(body) = json.dumps(body, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False)
    row_hash = SHA256(canonical({...payload, prev_hash: h_{i-1}}))

V2 ALGORITHM (post-boundary, per the locked plan):

    h_i = SHA256("DGASv1" || str(i) || h_{i-1} || SHA256(canonical_payload_i))

Where:
- "DGASv1" is the literal byte string for domain separation
- str(i) is the block index encoded as decimal ASCII
- h_{i-1} is the hex string of the previous row's hash
- canonical_payload_i is json.dumps with the same canonicalization as v1
- All concatenated as bytes (utf-8 for strings)

Both algorithms verifiable by this script. The v2 chain doesn't exist
pre-cutover — this verifier supports it so the same binary handles the
post-cutover audit without modification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

CHAIN_FIELD_PREV = "prev_hash"
CHAIN_FIELD_HASH = "row_hash"

DGAS_V2_DOMAIN_PREFIX = b"DGASv1"  # The literal byte prefix per locked plan.
# Note: the prefix says "DGASv1" but the CHAIN format is v2. The "v1" in the
# prefix refers to the cryptographic construction version (domain-separated +
# block-index-bound). If/when the construction needs upgrading (e.g. switch
# to BLAKE3 or change the field ordering), bump to "DGASv2" prefix and v3
# chain. This is intentional — separating "what algorithm" from "which chain
# segment" gives clean upgrade paths.


def _canonical(body: dict[str, Any]) -> bytes:
    """UTF-8 bytes of body in canonical JSON form. Matches audit_chain.py."""
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hash_v1_body(body_minus_row_hash: dict[str, Any]) -> str:
    """v1 row_hash = SHA256(canonical({...body, prev_hash}))."""
    return _sha256_hex(_canonical(body_minus_row_hash))


def _hash_v2_row(block_index: int, prev_hash: str | None, payload: dict[str, Any]) -> str:
    """v2 row_hash = SHA256("DGASv1" || i || h_{i-1} || SHA256(canonical(payload)))."""
    # First v2 row (anchored to legacy h_N) has prev_hash from manifest. There
    # is no v2 row with prev_hash=None — the boundary anchor IS the prev_hash.
    # ruff SIM108 — ternary is fine for this simple binary choice.
    prev_bytes = b"" if prev_hash is None else prev_hash.encode("ascii")
    payload_inner_hash = hashlib.sha256(_canonical(payload)).digest()
    combined = (
        DGAS_V2_DOMAIN_PREFIX + str(block_index).encode("ascii") + prev_bytes + payload_inner_hash
    )
    return _sha256_hex(combined)


def _walk_v1_chain(path: Path) -> tuple[str | None, int, int, list[str]]:
    """Walk a v1 chained JSONL file.

    Returns (terminal_row_hash, chained_row_count, byte_length, errors).

    chained_row_count counts only rows with a row_hash field. Legacy
    prefix rows (no row_hash) are tolerated at the head of the file but
    do NOT contribute to terminal_hash or the row count for boundary
    matching. This matches audit_chain.py's behavior.

    On any chain break, returns the terminal_row_hash up to the break +
    errors list. The caller decides whether to treat as fatal.
    """
    errors: list[str] = []
    if not path.exists():
        errors.append(f"legacy log not found: {path}")
        return None, 0, 0, errors

    byte_length = path.stat().st_size
    chained_count = 0
    terminal_hash: str | None = None
    expected_prev: str | None = None
    seen_chained = False

    with path.open("r", encoding="utf-8") as fh:
        for idx, raw in enumerate(fh):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                errors.append(f"line {idx}: parse error: {exc}")
                continue
            if not isinstance(obj, dict):
                errors.append(f"line {idx}: not a JSON object")
                continue

            rh = obj.get(CHAIN_FIELD_HASH)
            if not isinstance(rh, str) or not rh:
                if seen_chained:
                    errors.append(f"line {idx}: legacy row appears after chained rows began")
                    return terminal_hash, chained_count, byte_length, errors
                # Legacy prefix row — skip silently.
                continue

            seen_chained = True
            body = {k: v for k, v in obj.items() if k != CHAIN_FIELD_HASH}
            expected_hash = _hash_v1_body(body)
            if expected_hash != rh:
                errors.append(
                    f"line {idx}: row_hash mismatch (declared {rh}, computed {expected_hash})"
                )
                return terminal_hash, chained_count, byte_length, errors

            declared_prev = body.get(CHAIN_FIELD_PREV)
            if chained_count == 0:
                # First chained row anchors with prev_hash=None per audit_chain.py
                # invariant. This prevents head-row deletion.
                if declared_prev is not None:
                    errors.append(
                        f"line {idx}: first chained row must declare prev_hash=None "
                        f"(declared {declared_prev!r})"
                    )
                    return terminal_hash, chained_count, byte_length, errors
            else:
                if declared_prev != expected_prev:
                    errors.append(
                        f"line {idx}: prev_hash mismatch "
                        f"(declared {declared_prev!r}, expected {expected_prev!r})"
                    )
                    return terminal_hash, chained_count, byte_length, errors

            chained_count += 1
            terminal_hash = rh
            expected_prev = rh

    return terminal_hash, chained_count, byte_length, errors


def _walk_v2_chain(
    path: Path, anchor_prev_hash: str, expected_first_block: int
) -> tuple[str | None, int, list[str]]:
    """Walk a v2 chained JSONL file starting from a legacy boundary anchor.

    The first row in the v2 chain MUST declare:
      - prev_hash == anchor_prev_hash (from boundary manifest)
      - block_index == expected_first_block (= manifest.new_chain_starts_at)

    Each subsequent row chains via the v2 formula. CR R1 finding: previously
    the first-row block_index was seeded from whatever the log declared, so
    new_chain_starts_at was never validated. Now the verifier asserts the
    first row matches the manifest's expected starting block, which is the
    contract the boundary protocol guarantees.

    Returns (terminal_row_hash, chained_row_count, errors).
    """
    errors: list[str] = []
    if not path.exists():
        # New chain may legitimately not exist yet (pre-cutover, or no
        # post-boundary dispatches happened yet). Treat as 0-row chain
        # that anchors to anchor_prev_hash — not an error.
        return anchor_prev_hash, 0, errors

    chained_count = 0
    terminal_hash: str | None = anchor_prev_hash
    expected_prev = anchor_prev_hash
    expected_block_index: int = expected_first_block

    with path.open("r", encoding="utf-8") as fh:
        for idx, raw in enumerate(fh):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                errors.append(f"line {idx}: parse error: {exc}")
                continue
            if not isinstance(obj, dict):
                errors.append(f"line {idx}: not a JSON object")
                continue

            rh = obj.get(CHAIN_FIELD_HASH)
            if not isinstance(rh, str) or not rh:
                errors.append(f"line {idx}: v2 chain row missing row_hash")
                return terminal_hash, chained_count, errors

            declared_prev = obj.get(CHAIN_FIELD_PREV)
            declared_block = obj.get("block_index")

            if not isinstance(declared_block, int):
                errors.append(f"line {idx}: v2 row missing or non-int block_index")
                return terminal_hash, chained_count, errors

            if chained_count == 0:
                # First v2 row anchors to legacy terminal hash AND must
                # declare the block_index promised by the manifest.
                if declared_prev != anchor_prev_hash:
                    errors.append(
                        f"line {idx}: first v2 row prev_hash mismatch — "
                        f"declared {declared_prev!r}, expected boundary anchor "
                        f"{anchor_prev_hash!r}"
                    )
                    return terminal_hash, chained_count, errors
                if declared_block != expected_first_block:
                    errors.append(
                        f"line {idx}: first v2 row block_index mismatch — "
                        f"declared {declared_block}, expected manifest."
                        f"new_chain_starts_at={expected_first_block}"
                    )
                    return terminal_hash, chained_count, errors
            else:
                if declared_prev != expected_prev:
                    errors.append(
                        f"line {idx}: prev_hash mismatch "
                        f"(declared {declared_prev!r}, expected {expected_prev!r})"
                    )
                    return terminal_hash, chained_count, errors
                if declared_block != expected_block_index:
                    errors.append(
                        f"line {idx}: block_index mismatch "
                        f"(declared {declared_block}, expected {expected_block_index})"
                    )
                    return terminal_hash, chained_count, errors

            # Recompute the v2 hash and compare. Payload = body minus row_hash,
            # prev_hash, block_index (which are inputs to the formula, not part
            # of the payload).
            payload = {
                k: v
                for k, v in obj.items()
                if k not in (CHAIN_FIELD_HASH, CHAIN_FIELD_PREV, "block_index")
            }
            expected_hash = _hash_v2_row(declared_block, declared_prev, payload)
            if expected_hash != rh:
                errors.append(
                    f"line {idx}: v2 row_hash mismatch (declared {rh}, computed {expected_hash})"
                )
                return terminal_hash, chained_count, errors

            chained_count += 1
            terminal_hash = rh
            expected_prev = rh
            expected_block_index += 1

    return terminal_hash, chained_count, errors


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"manifest not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _verify_manifest_signature(manifest_path: Path, sig_path: Path | None) -> tuple[bool, str]:
    """Best-effort signature verification.

    For SSH-signed manifests: requires `ssh-keygen` on PATH and an
    `allowed_signers` file alongside the manifest. If the sig path doesn't
    exist or ssh-keygen isn't available, returns (False, reason) — the
    caller decides whether to treat as fatal based on --require-signature.
    """
    import shutil
    import subprocess

    if sig_path is None or not sig_path.exists():
        return False, f"signature file not provided or not found: {sig_path}"

    allowed_signers = manifest_path.parent / "allowed_signers"
    if not allowed_signers.exists():
        return False, f"allowed_signers file not found: {allowed_signers}"

    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        return False, "ssh-keygen not on PATH — cannot verify SSH signature"

    try:
        # ssh-keygen -Y verify -f allowed_signers -I <signer> -n file -s <sig> < <data>
        with manifest_path.open("rb") as fh:
            result = subprocess.run(
                [
                    ssh_keygen,
                    "-Y",
                    "verify",
                    "-f",
                    str(allowed_signers),
                    "-I",
                    "operator",
                    "-n",
                    "file",
                    "-s",
                    str(sig_path),
                ],
                stdin=fh,
                capture_output=True,
                text=True,
                timeout=10,
            )
        if result.returncode == 0:
            return True, "signature verified via ssh-keygen -Y verify"
        return False, f"ssh-keygen verify failed: {result.stderr.strip()}"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"ssh-keygen invocation failed: {exc}"


def verify_boundary(
    legacy_log: Path,
    manifest: Path,
    new_log: Path,
    *,
    signature_path: Path | None = None,
    require_signature: bool = False,
) -> tuple[bool, list[str]]:
    """Top-level verifier. Returns (ok, errors).

    Walks the legacy chain, validates the boundary manifest matches, then
    walks the new chain from the manifest's terminal_hash forward.
    """
    errors: list[str] = []

    # Step 1: load manifest first so we have something to validate against.
    try:
        m = _load_manifest(manifest)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        errors.append(f"manifest load failed: {exc}")
        return False, errors

    # CR R1: reject non-object manifest JSON before indexing into it. A valid
    # JSON that is not a dict (e.g. an array, a number, a string) would later
    # raise TypeError on m["..."]. Catch it cleanly here so the operator sees
    # a structured validation error, not a Python traceback.
    if not isinstance(m, dict):
        errors.append(f"manifest must be a JSON object, got {type(m).__name__}")
        return False, errors

    required_fields = [
        "legacy_log_path",
        "terminal_block",
        "terminal_hash",
        "row_count",
        "byte_length",
        "hash_algorithm",
        "new_chain_starts_at",
        "new_chain_format_version",
    ]
    missing = [f for f in required_fields if f not in m]
    if missing:
        errors.append(f"manifest missing required fields: {missing}")
        return False, errors

    if m["hash_algorithm"] != "sha256":
        errors.append(
            f"manifest hash_algorithm = {m['hash_algorithm']!r}; verifier only supports sha256"
        )
        return False, errors

    # CR R2 (PR #182): enforce new_chain_format_version. A manifest declaring
    # any other value would still pass otherwise because the verifier always
    # applies the v2 formula. Mismatch indicates either a tampered manifest
    # or a future protocol version this verifier doesn't yet support — either
    # way, fail-closed so the operator sees the discrepancy.
    if m["new_chain_format_version"] != "DGAS_V2":
        errors.append(
            f"manifest new_chain_format_version = {m['new_chain_format_version']!r}; "
            "verifier only supports 'DGAS_V2'. If this is a newer protocol, "
            "use the matching verifier."
        )
        return False, errors

    # Step 2: walk the legacy chain, compute terminal_hash + row_count.
    legacy_terminal, legacy_count, legacy_bytes, legacy_errors = _walk_v1_chain(legacy_log)
    if legacy_errors:
        errors.extend(f"[legacy] {e}" for e in legacy_errors)
        return False, errors

    # Step 3: cross-check manifest against legacy state.
    if legacy_terminal != m["terminal_hash"]:
        errors.append(
            f"manifest terminal_hash {m['terminal_hash']!r} != legacy chain "
            f"terminal {legacy_terminal!r}"
        )
        return False, errors

    if legacy_count != m["row_count"]:
        errors.append(
            f"manifest row_count {m['row_count']} != legacy chained row count {legacy_count}"
        )
        return False, errors

    if legacy_bytes != m["byte_length"]:
        errors.append(f"manifest byte_length {m['byte_length']} != legacy file size {legacy_bytes}")
        return False, errors

    # Step 4: verify manifest signature.
    # CR R1: fail when signature is provided but doesn't verify, even
    # without --require-signature. The intent of passing --signature is
    # "verify this signature"; silently accepting failure would be a
    # confusing false-success.
    #
    # Behavior matrix:
    #   --signature absent, --require-signature off  → skip (sig_ok=False,
    #     no error)
    #   --signature absent, --require-signature on   → fail
    #   --signature present, signature verifies      → pass
    #   --signature present, signature fails to verify → fail (regardless
    #     of --require-signature)
    sig_ok, sig_reason = _verify_manifest_signature(manifest, signature_path)
    sig_provided = signature_path is not None
    if not sig_ok and (require_signature or sig_provided):
        errors.append(f"signature verification failed: {sig_reason}")
        return False, errors
    # If signature is present + verified, that's the strongest trust root.
    # If absent + not required, we proceed but caller should note it in their
    # audit report.

    # Step 5: walk new chain anchored to manifest.terminal_hash AND
    # manifest.new_chain_starts_at. The terminal hash + count from the new
    # chain are computed but not used by this verifier — they exist for
    # callers that want to continue auditing forward. Underscore-prefixed
    # per ruff RUF059.
    expected_first_block = m["new_chain_starts_at"]
    if not isinstance(expected_first_block, int):
        errors.append(
            f"manifest.new_chain_starts_at must be int, got {type(expected_first_block).__name__}"
        )
        return False, errors
    _new_terminal, _new_count, new_errors = _walk_v2_chain(
        new_log, m["terminal_hash"], expected_first_block
    )
    if new_errors:
        errors.extend(f"[new] {e}" for e in new_errors)
        return False, errors

    return True, errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else "DGAS boundary verifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--legacy-log", type=Path, required=True, help="Path to frozen v1 JSONL")
    p.add_argument(
        "--manifest", type=Path, required=True, help="Path to DGAS_BOUNDARY_MANIFEST.json"
    )
    p.add_argument(
        "--new-log", type=Path, required=True, help="Path to v2 JSONL (may not exist yet)"
    )
    p.add_argument(
        "--signature",
        type=Path,
        default=None,
        help="Path to manifest.json.sig (optional; SSH-signed)",
    )
    p.add_argument(
        "--require-signature",
        action="store_true",
        help="Fail if signature is not present or doesn't verify",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print success details to stdout in addition to errors on stderr",
    )

    args = p.parse_args(argv)

    ok, errors = verify_boundary(
        args.legacy_log,
        args.manifest,
        args.new_log,
        signature_path=args.signature,
        require_signature=args.require_signature,
    )

    if not ok:
        print("DGAS boundary verification FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    if args.verbose:
        print("DGAS boundary verification PASSED")

    return 0


if __name__ == "__main__":
    sys.exit(main())
