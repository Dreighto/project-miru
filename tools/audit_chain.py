"""Hash-chained append-only JSONL helpers for data/ audit logs.

DGAS Tier 2 #6: every entry in the canonical append-only data/*.jsonl files
includes a SHA-256 fingerprint of the previous entry. Tampering with any
older row breaks the chain at every subsequent row, so deliberate after-
the-fact edits are detectable. Pre-commit hooks already protect against
accidental rewrites of these files; this module adds tamper-evidence on
top.

Design notes:
    * Mirrors the algorithm used by ``tools/miru_mcp_gateway/audit.py`` so
      gateway logs and data/ logs verify identically. Consolidating the two
      implementations is a follow-up; for now they are independent so
      changes to one do not require operator-merge review of the other.
    * Tolerates legacy prefix rows that pre-date chaining. The first row
      in the file with a ``row_hash`` field starts a fresh chain link with
      ``prev_hash = null``. Subsequent rows must chain to it.
    * Canonicalisation (sort_keys=True, no whitespace) is required so the
      same body produces the same hash on every machine.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHAIN_FIELD_PREV = "prev_hash"
CHAIN_FIELD_HASH = "row_hash"

# v2 chain (post LOS-10 boundary cutover): domain-separated, block-index-
# bound. Formula: h_i = SHA256("DGASv1" || str(i) || h_{i-1} || SHA256(canonical_payload_i))
# The "DGASv1" prefix is the cryptographic construction version, NOT the
# chain segment version. See tools/verify_dgas_boundary.py for the
# authoritative description of the construction. Keep these two files in
# lockstep — any change here must mirror to the verifier.
CHAIN_FIELD_BLOCK_INDEX = "block_index"
DGAS_V2_DOMAIN_PREFIX = b"DGASv1"


@dataclass
class ChainResult:
    """Outcome of verifying a single JSONL file.

    Attributes:
        path: file checked
        total_rows: lines that parsed as JSON objects (including legacy)
        chained_rows: rows that carry both prev_hash and row_hash
        legacy_prefix_rows: pre-chain rows at the head of the file
        ok: True iff every chained row hash + prev_hash check passes
        broken_at_line: 0-indexed line of the first failure, or None
        error: human-readable description of the first failure, or None
    """

    path: Path
    total_rows: int = 0
    chained_rows: int = 0
    legacy_prefix_rows: int = 0
    ok: bool = True
    broken_at_line: int | None = None
    error: str | None = None
    parse_errors: list[tuple[int, str]] = field(default_factory=list)


def _canonical(body: dict[str, Any]) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_body(body: dict[str, Any]) -> str:
    """Return the SHA-256 hex digest of ``body`` in canonical form."""
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def _scan_tail_for_last_chained(data: bytes) -> tuple[bool, str | None]:
    """Walk a byte buffer (assumed to contain whole lines) backwards.

    Returns ``(found, value)``:
        * ``(True, hash_str)`` if the last parseable JSON object in the
          buffer carries a row_hash.
        * ``(True, None)`` if the last parseable JSON object is a legacy
          row (no row_hash) — caller starts a fresh chain link.
        * ``(False, None)`` if no parseable JSON object was found in the
          buffer at all (caller should re-read with a larger window).
    """
    text = data.decode("utf-8", errors="replace")
    for raw in reversed(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            # Parseable but not a JSON object (array, scalar) — skip; do
            # not treat it as a chain decision.
            continue
        rh = obj.get(CHAIN_FIELD_HASH)
        if isinstance(rh, str) and rh:
            return True, rh
        return True, None
    return False, None


def _read_last_chained(path: Path) -> str | None:
    """Return the row_hash of the last chained row in ``path``, or None.

    Reads a 64 KiB tail window first because that's enough for any
    realistic JSONL row. If a single tail row exceeds 64 KiB (rare but
    possible for verbose audit entries), falls back to reading the whole
    file rather than returning None — a silent None would cause
    ``append_chained`` to start a new chain link incorrectly and break
    every subsequent verification.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        size = path.stat().st_size
        chunk = min(size, 65536)
        with path.open("rb") as fh:
            fh.seek(size - chunk)
            data = fh.read()
    except OSError:
        return None

    found, value = _scan_tail_for_last_chained(data)
    if found:
        return value

    # Tail window did not contain a complete parseable JSON object. Fall
    # back to reading the whole file. Skip if we already read the whole
    # thing on the first pass (chunk == size).
    if chunk >= size:
        return None
    try:
        with path.open("rb") as fh:
            data = fh.read()
    except OSError:
        return None
    _, value = _scan_tail_for_last_chained(data)
    return value


def append_chained(path: Path, row: dict[str, Any], *, fsync: bool = False) -> str:
    """Append ``row`` to ``path`` with prev_hash + row_hash fields.

    ``row`` must not contain ``prev_hash`` or ``row_hash`` keys; they are
    added here. The previous row's hash is read from the file tail; if the
    file is empty or the tail is a legacy row, prev_hash is set to None and
    a new chain link starts.

    Args:
        path: target JSONL file (created if absent).
        row: caller-supplied object to chain.
        fsync: when True, ``os.fsync`` the file descriptor after the write
            so the row is durable across power loss. Use for ledgers that
            track external side effects (e.g. github_resource_ledger.jsonl)
            where losing the row would orphan a real-world resource. Default
            False — most audit logs accept page-cache durability.

    Returns the row_hash that was written, so callers can chain follow-up
    work in the same session if needed.
    """
    import os as _os

    body = {k: v for k, v in row.items() if k not in (CHAIN_FIELD_PREV, CHAIN_FIELD_HASH)}
    body[CHAIN_FIELD_PREV] = _read_last_chained(path)
    row_hash = _hash_body(body)
    final = {**body, CHAIN_FIELD_HASH: row_hash}
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(final, separators=(",", ":"), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        if fsync:
            fh.flush()
            _os.fsync(fh.fileno())
    return row_hash


def _hash_v2_row(block_index: int, prev_hash: str | None, payload: dict[str, Any]) -> str:
    """Compute the v2 row hash for a payload at block_index.

    Mirrors tools/verify_dgas_boundary.py::_hash_v2_row. The two MUST stay
    byte-for-byte identical — the standalone verifier is the auditor's
    source of truth, and divergence here means our writes won't verify.

    Formula: SHA256("DGASv1" || str(i) || h_{i-1} || SHA256(canonical_payload_i))

    - prev_hash is the v1 chain's terminal hash (from the boundary manifest)
      for the first v2 row, then the previous v2 row's row_hash thereafter.
    - The first v2 row has prev_hash set to the manifest's terminal_hash
      (never None — the boundary anchor IS the prev_hash).
    """
    prev_bytes = b"" if prev_hash is None else prev_hash.encode("ascii")
    payload_inner_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).digest()
    combined = (
        DGAS_V2_DOMAIN_PREFIX + str(block_index).encode("ascii") + prev_bytes + payload_inner_hash
    )
    return hashlib.sha256(combined).hexdigest()


def _scan_tail_for_last_v2(data: bytes) -> tuple[bool, str | None, int | None]:
    """Walk a byte buffer (assumed to contain whole lines) backwards for v2.

    Returns ``(found, rh, bi)``:
        * ``(True, hash_str, block_index)`` if the last parseable JSON
          object in the buffer carries both row_hash and block_index.
        * ``(True, None, None)`` if the last parseable JSON object is
          missing one of them (corrupt v2 row; caller refuses to append).
        * ``(False, None, None)`` if no parseable JSON object was found
          in the buffer at all (caller retries with a larger window).
    """
    text = data.decode("utf-8", errors="replace")
    for raw in reversed(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        rh = obj.get(CHAIN_FIELD_HASH)
        bi = obj.get(CHAIN_FIELD_BLOCK_INDEX)
        if isinstance(rh, str) and rh and isinstance(bi, int):
            return True, rh, bi
        return True, None, None
    return False, None, None


def _read_last_v2_state(path: Path) -> tuple[str | None, int | None]:
    """Return (last_row_hash, last_block_index) for a v2-chained file.

    Returns (None, None) for an empty/missing file OR a corrupt last row.
    Walks from the tail like _read_last_chained, but extracts both the
    row_hash AND the block_index so the next append can increment correctly.

    Reads a 64 KiB tail window first; if that doesn't contain a complete
    parseable JSON object (e.g. a single v2 row exceeds 64 KiB — rare but
    possible for verbose audit entries with large payloads), falls back
    to reading the whole file. CR R3 (PR #182 combined): mirrors the
    same fallback that _read_last_chained has for v1. Without this, a
    >64 KiB last row would incorrectly return (None, None), causing the
    next append to demand anchor parameters for what should be a normal
    subsequent row.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None, None
    try:
        size = path.stat().st_size
        chunk = min(size, 65536)
        with path.open("rb") as fh:
            fh.seek(size - chunk)
            data = fh.read()
    except OSError:
        return None, None

    found, rh, bi = _scan_tail_for_last_v2(data)
    if found:
        return rh, bi

    # Tail window did not contain a complete parseable JSON object. Fall
    # back to reading the whole file. Skip if we already read the whole
    # thing on the first pass (chunk == size).
    if chunk >= size:
        return None, None
    try:
        with path.open("rb") as fh:
            data = fh.read()
    except OSError:
        return None, None
    _, rh, bi = _scan_tail_for_last_v2(data)
    return rh, bi


def append_v2_chained(
    path: Path,
    row: dict[str, Any],
    *,
    anchor_prev_hash: str | None = None,
    anchor_block_index: int | None = None,
    fsync: bool = False,
) -> tuple[str, int]:
    """Append ``row`` to ``path`` using the v2 chain formula.

    Post-LOS-10-boundary-cutover writer. For the FIRST row in a v2 file,
    the caller MUST provide ``anchor_prev_hash`` (= boundary manifest's
    terminal_hash) and ``anchor_block_index`` (= manifest.new_chain_starts_at).
    For subsequent rows, the function reads the previous row's hash + index
    from the file tail.

    ``row`` must not contain prev_hash, row_hash, or block_index keys; they
    are added here. The payload is canonicalized identically to v1.

    Returns (row_hash, block_index) of the row written.

    Raises:
        ValueError: if the file exists with rows but tail parse fails, or
            if the file is empty and no anchor is provided.
    """
    import os as _os

    body = {
        k: v
        for k, v in row.items()
        if k not in (CHAIN_FIELD_PREV, CHAIN_FIELD_HASH, CHAIN_FIELD_BLOCK_INDEX)
    }

    last_hash, last_block_index = _read_last_v2_state(path)
    if last_hash is None:
        # First row in this v2 file. The caller must supply the boundary anchor.
        if anchor_prev_hash is None or anchor_block_index is None:
            raise ValueError(
                "append_v2_chained: file is empty/missing — anchor_prev_hash and "
                "anchor_block_index are required for the first v2 row."
            )
        prev_hash: str = anchor_prev_hash
        block_index: int = anchor_block_index
    else:
        prev_hash = last_hash
        block_index = last_block_index + 1

    row_hash = _hash_v2_row(block_index, prev_hash, body)
    final = {
        **body,
        CHAIN_FIELD_PREV: prev_hash,
        CHAIN_FIELD_BLOCK_INDEX: block_index,
        CHAIN_FIELD_HASH: row_hash,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(final, separators=(",", ":"), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
        if fsync:
            fh.flush()
            _os.fsync(fh.fileno())
    return row_hash, block_index


def validate_chain(path: Path) -> ChainResult:
    """Verify every chained row in ``path``.

    Walks the file once. Tolerates legacy prefix rows (no row_hash). For
    every row that carries row_hash, checks: (a) the body hashes to the
    declared row_hash, and (b) prev_hash matches the previous chained
    row's row_hash (or is None for the first chained row in the file).
    """
    result = ChainResult(path=path)
    if not path.exists():
        result.ok = False
        result.error = f"file not found: {path}"
        return result

    prev_hash: str | None = None
    first_chained = True

    with path.open("r", encoding="utf-8") as fh:
        for idx, raw in enumerate(fh):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                result.parse_errors.append((idx, str(exc)))
                continue
            if not isinstance(obj, dict):
                result.parse_errors.append((idx, "parsed JSON value is not an object"))
                continue
            result.total_rows += 1

            rh = obj.get(CHAIN_FIELD_HASH)
            if not isinstance(rh, str) or not rh:
                # Legacy row. Allowed only as a prefix at the head of the file.
                if result.chained_rows == 0:
                    result.legacy_prefix_rows += 1
                    continue
                result.ok = False
                result.broken_at_line = idx
                result.error = f"line {idx}: legacy row appears after chained rows began"
                return result

            body = {k: v for k, v in obj.items() if k != CHAIN_FIELD_HASH}
            expected_hash = _hash_body(body)
            if expected_hash != rh:
                result.ok = False
                result.broken_at_line = idx
                result.error = (
                    f"line {idx}: row_hash mismatch (declared {rh}, computed {expected_hash})"
                )
                return result

            declared_prev = body.get(CHAIN_FIELD_PREV)
            if first_chained:
                # The first chained row in any data/*.jsonl file MUST anchor
                # to prev_hash=None. Allowing any value here would let an
                # attacker delete the head row without breaking the chain.
                # Our 9 data/*.jsonl files do not rotate (rotation is a
                # gateway-audit concern), so the simple anchor invariant
                # applies uniformly.
                if declared_prev is not None:
                    result.ok = False
                    result.broken_at_line = idx
                    result.error = (
                        f"line {idx}: first chained row must declare prev_hash=None "
                        f"(declared {declared_prev!r}). Likely the head row was deleted."
                    )
                    return result
                first_chained = False
            else:
                if declared_prev != prev_hash:
                    result.ok = False
                    result.broken_at_line = idx
                    result.error = (
                        f"line {idx}: prev_hash mismatch "
                        f"(declared {declared_prev!r}, expected {prev_hash!r})"
                    )
                    return result

            result.chained_rows += 1
            prev_hash = rh

    return result
