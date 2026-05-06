"""Gatekeeper-to-listener forwarder.

After the Gatekeeper validates a dispatch, this module signs the payload
with HMAC-SHA256 and POSTs it to the existing ``dispatch_listener`` on
port 19100. The listener handles worktree leasing, worker spawning, and
completion-marker writes — the Gatekeeper is a thin validation layer in
front of it, not a re-implementation.

API contract verified 2026-05-05 against
``services/dispatch_listener/src/index.js`` (commit pre-PR-94).

Endpoint: ``POST http://127.0.0.1:19100/dispatch``
Auth: ``X-W4-HMAC`` header, hex SHA-256 HMAC of the raw body.
Secret: ``W4_LISTENER_HMAC_SECRET`` env var (loaded from ``.env``).
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

log = logging.getLogger("miru.gatekeeper.forwarder")

LISTENER_URL = os.environ.get("MIRU_DISPATCH_LISTENER_URL", "http://127.0.0.1:19100/dispatch")
LISTENER_TIMEOUT_S = 30
HMAC_SECRET_ENV = "W4_LISTENER_HMAC_SECRET"

# Honor MIRU_REPO_ROOT so the prompt file lands in the same tree the
# Gatekeeper and dispatch_listener expect (per PR #89 — gateway and
# downstream consumers all read MIRU_REPO_ROOT). Falls back to the
# package-relative parent so the module is still importable in a stock
# checkout without the env var set.
REPO_ROOT = Path(os.environ.get("MIRU_REPO_ROOT") or Path(__file__).resolve().parents[1])
INBOX_DIR = REPO_ROOT / "data" / "n8n_inbox"


class ForwarderError(Exception):
    """Forwarder-level error mapped from listener response codes.

    ``reason`` matches the rejection vocabulary the Gatekeeper surfaces
    to CH (e.g. ``backpressure_no_slot`` for 503, ``dispatch_failed``
    for 500). ``listener_status`` is the raw HTTP code if available.
    """

    def __init__(
        self,
        reason: str,
        detail: str = "",
        listener_status: int | None = None,
        listener_body: str | None = None,
    ):
        self.reason = reason
        self.detail = detail
        self.listener_status = listener_status
        self.listener_body = listener_body
        super().__init__(f"{reason}: {detail}" if detail else reason)


def _load_secret() -> bytes:
    secret = os.environ.get(HMAC_SECRET_ENV)
    if not secret:
        raise ForwarderError(
            "hmac_secret_missing",
            f"environment variable {HMAC_SECRET_ENV} is not set",
        )
    return secret.encode("utf-8")


def _sign(body: bytes) -> str:
    return hmac.new(_load_secret(), body, hashlib.sha256).hexdigest()


_TRACE_ID_FILENAME_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{6,128}$")


def mint_trace_id(ticket_id: str) -> str:
    """Generate a trace_id matching the listener's ``/^[a-zA-Z0-9_-]{6,128}$/``.

    Format: ``rtr-<TICKET>-<16-hex-rand>``. 16 hex chars = 64 bits of
    entropy, plenty for collision avoidance at our dispatch volume.
    """
    rand = secrets.token_hex(8)
    return f"rtr-{ticket_id}-{rand}"


def write_prompt_file(trace_id: str, prompt_text: str) -> Path:
    """Write the prompt JSON the listener will read.

    The listener expects a JSON file with a ``prompt`` field. Path is
    relative to repo root; we use absolute path on disk but pass relative
    in the dispatch payload (listener resolves both).

    ``trace_id`` is interpolated into the filename, so we enforce a
    strict allowlist (the same regex the listener uses:
    ``/^[A-Za-z0-9_-]{6,128}$/``) before writing. Reject path-traversal
    attempts via ValueError.
    """
    if not _TRACE_ID_FILENAME_RE.match(trace_id):
        raise ValueError(
            f"trace_id violates filename safety regex /^[A-Za-z0-9_-]{{6,128}}$/: " f"{trace_id!r}"
        )
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    abs_path = INBOX_DIR / f"{trace_id}.prompt.json"
    if abs_path.parent.resolve() != INBOX_DIR.resolve():
        raise ValueError(f"resolved prompt path escapes INBOX_DIR: {abs_path}")
    payload = {"prompt": prompt_text}
    abs_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return abs_path


_RESPONSE_REASON_MAP: dict[int, str] = {
    400: "dispatch_failed_bad_request",
    401: "dispatch_failed_hmac_reject",
    403: "worker_not_allowlisted",
    409: "duplicate_dispatch",
    500: "dispatch_failed",
    503: "backpressure_no_slot",
}


def forward(
    *,
    trace_id: str,
    worker: str,
    prompt_text: str,
    timeout_seconds: int = 600,
    model: str | None = None,
    thinking_level: str | None = None,
    tool_profile: str = "standard_worker",
    use_api_key: bool = False,
) -> dict[str, Any]:
    """Sign and POST a dispatch to the listener.

    Returns the listener's 202 response body on success
    (``{"trace_id": ..., "status": "spawned", "spawned_at": ...}``).
    Raises :class:`ForwarderError` on any non-2xx response.
    """
    prompt_file = write_prompt_file(trace_id, prompt_text)
    rel_path = prompt_file.relative_to(REPO_ROOT).as_posix()

    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "worker": worker,
        "prompt_path": rel_path,
        "timeout_seconds": int(timeout_seconds),
        "use_api_key": bool(use_api_key),
        "tool_profile": tool_profile,
    }
    if model:
        payload["model"] = model
    if thinking_level:
        payload["thinking_level"] = thinking_level

    body = json.dumps(payload).encode("utf-8")
    signature = _sign(body)

    req = urllib.request.Request(
        LISTENER_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-W4-HMAC": signature,
        },
    )

    started_at = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=LISTENER_TIMEOUT_S) as resp:
            response_body = resp.read().decode("utf-8")
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0
            log.info(
                "listener_accepted trace_id=%s status=%s latency_ms=%.0f",
                trace_id,
                resp.status,
                elapsed_ms,
            )
            try:
                return json.loads(response_body)
            except json.JSONDecodeError as e:
                raise ForwarderError(
                    "listener_response_not_json",
                    str(e),
                    listener_status=resp.status,
                    listener_body=response_body[:500],
                ) from e
    except urllib.error.HTTPError as e:
        body_text = ""
        with contextlib.suppress(Exception):
            body_text = e.read().decode("utf-8", errors="replace")
        reason = _RESPONSE_REASON_MAP.get(e.code, "dispatch_failed_unknown")
        log.warning(
            "listener_rejected trace_id=%s status=%s body=%s",
            trace_id,
            e.code,
            body_text[:300],
        )
        raise ForwarderError(
            reason,
            body_text[:300],
            listener_status=e.code,
            listener_body=body_text[:500],
        ) from e
    except urllib.error.URLError as e:
        raise ForwarderError(
            "listener_unreachable",
            str(e.reason),
        ) from e
    except (TimeoutError, OSError) as e:
        raise ForwarderError(
            "listener_timeout",
            str(e),
        ) from e


_AllowlistedWorker = Literal["claude-code", "gemini"]
ALLOWLISTED_WORKERS: tuple[_AllowlistedWorker, ...] = ("claude-code", "gemini")
