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


def _read_last_chained(path: Path) -> str | None:
    """Return the row_hash of the last chained row in ``path``, or None.

    Only the last few KB are read so this stays cheap on large logs. If the
    tail of the file is a legacy row (no ``row_hash``) the function returns
    None, which signals to ``append_chained`` that a new chain link should
    start with ``prev_hash = null``.
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
    text = data.decode("utf-8", errors="replace")
    for raw in reversed(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        rh = obj.get(CHAIN_FIELD_HASH)
        if isinstance(rh, str) and rh:
            return rh
        # Last parseable row is a legacy row — chain head starts here.
        return None
    return None


def append_chained(path: Path, row: dict[str, Any]) -> str:
    """Append ``row`` to ``path`` with prev_hash + row_hash fields.

    ``row`` must not contain ``prev_hash`` or ``row_hash`` keys; they are
    added here. The previous row's hash is read from the file tail; if the
    file is empty or the tail is a legacy row, prev_hash is set to None and
    a new chain link starts.

    Returns the row_hash that was written, so callers can chain follow-up
    work in the same session if needed.
    """
    body = {k: v for k, v in row.items() if k not in (CHAIN_FIELD_PREV, CHAIN_FIELD_HASH)}
    body[CHAIN_FIELD_PREV] = _read_last_chained(path)
    row_hash = _hash_body(body)
    final = {**body, CHAIN_FIELD_HASH: row_hash}
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(final, separators=(",", ":"), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return row_hash


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
                # First chained row may declare prev_hash = None (fresh chain
                # after legacy prefix) or any value (continuing across log
                # rotation). We don't enforce a predecessor for the first.
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
