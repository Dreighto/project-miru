"""
Miru Task Dispatcher — sidecar utility on port 19000.

Scope: windows/ folder only. Does not touch PM (18080), Miru AI (18765),
or any canonical runtime. In-memory job queue + SQLite history.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import re as _re_noise
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, stream_with_context

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# dispatcher/task_dispatcher.py → parent chain:
#   parent        = dispatcher/
#   parent.parent = repo root
DISPATCHER_ROOT = Path(__file__).resolve().parent
REPO_ROOT = DISPATCHER_ROOT.parent

DISPATCHER_TEMPLATE_DIR = DISPATCHER_ROOT / "templates"
DISPATCHER_STATIC_DIR = DISPATCHER_ROOT / "static"
ENV_PATH = REPO_ROOT / ".env"

# ---------------------------------------------------------------------------
# Logging + .env
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("miru.dispatcher")

if load_dotenv is None:
    log.warning(
        "python-dotenv not installed; .env at %s will NOT be auto-loaded",
        ENV_PATH,
    )
elif not ENV_PATH.exists():
    log.warning("no .env found at %s; relying on process environment", ENV_PATH)
else:
    load_dotenv(ENV_PATH)
    log.info("loaded environment from %s", ENV_PATH)

# ---------------------------------------------------------------------------
# Handler import (after .env load so os.environ is populated)
# ---------------------------------------------------------------------------
# Python adds the script directory (dispatcher/) to sys.path when
# run directly, so "handlers" resolves as a sibling package.

try:
    from .handlers import get_handler, resolve_executor_mode
except ImportError:  # pragma: no cover - direct script execution path
    from handlers import get_handler, resolve_executor_mode

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DISPATCHER_BASE_URL = os.environ.get("DISPATCHER_BASE_URL", "").strip()

VALID_MODELS = {"Ollama", "Claude", "Gemini", "Simulation"}
VALID_EFFORTS = {"Quick", "Standard", "Deep"}
JOB_LIMIT = 50
_NOISE_LINE_RE = _re_noise.compile(
    r"\[WARN\]\s+Skipping unreadable directory",
    _re_noise.IGNORECASE,
)

# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

DB_PATH = DISPATCHER_ROOT / "data" / "jobs.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS job_history (
    job_id         TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    finished_at    TEXT,
    prompt         TEXT,
    model          TEXT,
    effort         TEXT,
    handler_name   TEXT,
    executor_mode  TEXT,
    status         TEXT,
    result_text    TEXT,
    error_message  TEXT,
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    estimated_cost REAL,
    run_duration_ms REAL,
    title          TEXT
)
"""

_MIGRATE_TITLE = "ALTER TABLE job_history ADD COLUMN title TEXT"


def _init_db() -> None:
    """Create the job_history table if it does not exist. Migrate if needed."""
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(_CREATE_TABLE)
            # Migrate: add title column if missing (Fix 4)
            try:  # noqa: SIM105
                conn.execute(_MIGRATE_TITLE)
            except sqlite3.OperationalError:
                pass  # column already exists
            conn.commit()
        log.info("Job history DB ready at %s", DB_PATH)
    except Exception as exc:
        log.warning("Could not init job history DB: %s", exc)


def _db_upsert_job(job: Job) -> None:
    """Insert or update a job record. Non-fatal on failure."""
    dur = None
    if job.finished_at and job.created_at:
        try:  # noqa: SIM105
            dur = (
                datetime.fromisoformat(job.finished_at) - datetime.fromisoformat(job.created_at)
            ).total_seconds() * 1000
        except Exception:
            pass
    err_msg = ""
    if job.status == "failed":
        err_msg = job.output[:2000]
    result_text = job.output[:50000] if job.output else ""
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.execute(
                """INSERT INTO job_history
                   (job_id,created_at,finished_at,prompt,model,effort,
                    handler_name,executor_mode,status,result_text,
                    error_message,input_tokens,output_tokens,
                    estimated_cost,run_duration_ms,title)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET
                    finished_at=excluded.finished_at,
                    handler_name=excluded.handler_name,
                    executor_mode=excluded.executor_mode,
                    status=excluded.status,
                    result_text=excluded.result_text,
                    error_message=excluded.error_message,
                    input_tokens=excluded.input_tokens,
                    output_tokens=excluded.output_tokens,
                    estimated_cost=excluded.estimated_cost,
                    run_duration_ms=excluded.run_duration_ms,
                    title=excluded.title
                """,
                (
                    job.id,
                    job.created_at,
                    job.finished_at,
                    job.prompt,
                    job.model,
                    job.effort,
                    job.handler_name,
                    job.executor_mode,
                    job.status,
                    result_text,
                    err_msg,
                    job.input_tokens,
                    job.output_tokens,
                    job.estimated_cost,
                    dur,
                    job.title,
                ),
            )
            conn.commit()
    except Exception as exc:
        log.warning("DB upsert failed for job %s: %s", job.id, exc)


def _db_get_job(job_id: str) -> dict[str, Any] | None:
    """Fetch a single job from the history DB. Returns dict or None."""
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM job_history WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return dict(row)
    except Exception:
        return None


def _db_query_history(
    limit: int = 20, status: str = "", executor: str = ""
) -> list[dict[str, Any]]:
    """Query job history with optional filters."""
    try:
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if executor:
            clauses.append("executor_mode=?")
            params.append(executor)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM job_history{where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("DB history query failed: %s", exc)
        return []


def _db_cumulative_stats() -> dict[str, Any]:
    """Aggregate stats across all persisted jobs."""
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) as success,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail,
                    SUM(CASE WHEN executor_mode='local' THEN 1 ELSE 0 END) as local_ct,
                    SUM(CASE WHEN executor_mode='real' THEN 1 ELSE 0 END) as real_ct,
                    COALESCE(SUM(input_tokens),0) as total_in,
                    COALESCE(SUM(output_tokens),0) as total_out
                FROM job_history
            """).fetchone()
        return dict(r) if r else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Job store
# ---------------------------------------------------------------------------


class Job:
    __slots__ = (
        "cancel_event",
        "created_at",
        "effort",
        "estimated_cost",
        "executor_mode",
        "finished_at",
        "future",
        "handler_name",
        "id",
        "input_tokens",
        "model",
        "output",
        "output_lines",
        "output_tokens",
        "proc",
        "prompt",
        "status",
        "title",
    )

    def __init__(self, prompt: str, model: str, effort: str) -> None:
        self.id: str = str(uuid.uuid4())
        self.status: str = "pending"
        self.prompt: str = prompt
        self.model: str = model
        self.effort: str = effort
        self.created_at: str = datetime.now(UTC).isoformat()
        self.finished_at: str | None = None
        self.output: str = ""
        self.executor_mode: str = "simulated"
        self.handler_name: str = ""
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.estimated_cost: float | None = None
        self.cancel_event: threading.Event = threading.Event()
        self.future: Future | None = None
        self.output_lines: list[str] = []
        self.title: str | None = None
        self.proc = None  # subprocess.Popen ref — set by handlers for stdin injection

    @property
    def output_preview(self) -> str:
        return self.output[:200]

    @property
    def approval_pending(self) -> bool:
        return self.status == "waiting_approval"

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "approval_pending": self.approval_pending,
            "model": self.model,
            "effort": self.effort,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "output_preview": self.output_preview,
            "executor_mode": self.executor_mode,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": self.estimated_cost,
            "title": self.title,
            "prompt": (self.prompt or "")[:120],
        }

    def to_detail(self) -> dict[str, Any]:
        data = self.to_summary()
        data["prompt"] = self.prompt
        data["output"] = self.output
        data["handler_name"] = self.handler_name
        return data


jobs_lock = threading.Lock()
jobs: dict[str, Job] = {}
jobs_order: list[str] = []  # newest first

executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="miru-job")


def _graceful_shutdown():
    log.info("Shutting down executor (cancel pending, wait for running)...")
    executor.shutdown(wait=True, cancel_futures=True)
    log.info("Executor shut down cleanly.")


atexit.register(_graceful_shutdown)


def _store_job(job: Job) -> None:
    with jobs_lock:
        jobs[job.id] = job
        jobs_order.insert(0, job.id)
        if len(jobs_order) > JOB_LIMIT * 2:
            for stale_id in jobs_order[JOB_LIMIT * 2 :]:
                candidate = jobs.get(stale_id)
                if candidate and candidate.status in {"done", "failed", "cancelled"}:
                    jobs.pop(stale_id, None)
            jobs_order[:] = [jid for jid in jobs_order if jid in jobs]


def _listed_jobs() -> list[Job]:
    with jobs_lock:
        return [jobs[jid] for jid in jobs_order[:JOB_LIMIT] if jid in jobs]


# ---------------------------------------------------------------------------
# Auto-generated job titles via Claude API
# ---------------------------------------------------------------------------


def _title_fallback(prompt: str) -> str:
    """Word-boundary truncation: up to 6 words or 50 chars, whichever comes first."""
    words = (prompt or "").split()
    result = ""
    for word in words:
        candidate = (result + " " + word).strip() if result else word
        if len(candidate) > 50 or len(candidate.split()) > 6:
            break
        result = candidate
    return result.strip() or "Untitled"


def _generate_title_background(job: Job) -> None:
    """Background thread: call Claude Haiku to generate a 4-6 word job title."""
    raw = ""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if api_key:
        prompt_snippet = (job.prompt or "")[:200]
        user_msg = (
            "Generate a 4-6 word title for this task. "
            "Reply with only the title, no quotes, no punctuation at the end:\n\n" + prompt_snippet
        )
        payload = json.dumps(
            {
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 20,
                "messages": [{"role": "user", "content": user_msg}],
            }
        ).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                raw = data["content"][0]["text"].strip()
        except Exception as exc:
            log.warning("Claude title gen error for job %s: %s", job.id[:8], exc)
            raw = ""

        # Fix 1: strip Gemini/CLI noise lines before sanitizing (defensive, in case
        # the raw text ever passes through a CLI path in future).
        if raw:
            clean_lines = [
                ln
                for ln in raw.splitlines()
                if not _NOISE_LINE_RE.search(ln) and "MCP issues detected" not in ln
            ]
            raw = " ".join(ln.strip() for ln in clean_lines if ln.strip())
    else:
        log.info("No ANTHROPIC_API_KEY — using fallback title for job %s", job.id[:8])

    # Sanitize: strip quotes, newlines, JSON artifacts, limit length
    title = raw.replace('"', "").replace("'", "").replace("\n", " ").strip()
    if title.startswith("{") or title.startswith("["):
        title = ""
    if len(title) > 60:
        title = title[:57] + "..."

    # Fix 3: word-boundary fallback instead of hard char truncation
    if not title:
        title = _title_fallback(job.prompt or "")

    job.title = title
    _db_upsert_job(job)
    log.info("Title generated for job %s: %s", job.id[:8], job.title)


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------


def run_job(job: Job) -> None:
    """Resolve handler by model and manage lifecycle."""
    job.status = "running"
    handler = get_handler(job.model)
    job.handler_name = handler.__name__
    job.executor_mode = resolve_executor_mode(handler)
    try:
        handler(job)
        if job.status == "running":
            job.status = "done"
    except Exception as exc:
        job.status = "failed"
        job.output = f"Error: {exc}"
        log.exception("Job %s failed", job.id)
    finally:
        if job.finished_at is None:
            job.finished_at = datetime.now(UTC).isoformat()

    _db_upsert_job(job)

    if job.status in ("done", "failed"):
        # Fix 4: Auto-generate title in background daemon thread
        t = threading.Thread(target=_generate_title_background, args=(job,), daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=None)


@app.errorhandler(404)
def _not_found(_e):
    return jsonify({"error": "not found"}), 404


@app.post("/api/jobs")
def api_create_job():
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"error": "invalid JSON body"}), 400

    prompt = str(payload.get("prompt", "")).strip()
    model = str(payload.get("model", "")).strip()
    effort = str(payload.get("effort", "")).strip()

    if not prompt:
        return jsonify({"error": "prompt is required"}), 400
    if model not in VALID_MODELS:
        return jsonify({"error": f"model must be one of {sorted(VALID_MODELS)}"}), 400
    if effort not in VALID_EFFORTS:
        return jsonify({"error": f"effort must be one of {sorted(VALID_EFFORTS)}"}), 400

    job = Job(prompt=prompt, model=model, effort=effort)
    handler = get_handler(model)
    job.handler_name = handler.__name__
    job.executor_mode = resolve_executor_mode(handler)
    _store_job(job)
    _db_upsert_job(job)
    job.future = executor.submit(run_job, job)
    return jsonify(job.to_detail()), 201


@app.get("/api/jobs")
def api_list_jobs():
    return jsonify({"jobs": [j.to_summary() for j in _listed_jobs()]})


@app.get("/api/jobs/<job_id>")
def api_get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is not None:
        return jsonify(job.to_detail())
    row = _db_get_job(job_id)
    if row is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(
        {
            "id": row["job_id"],
            "status": row["status"] or "unknown",
            "model": row["model"] or "",
            "effort": row["effort"] or "",
            "created_at": row["created_at"],
            "finished_at": row["finished_at"],
            "output_preview": (row["result_text"] or "")[:200],
            "executor_mode": row["executor_mode"] or "",
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "estimated_cost": row["estimated_cost"],
            "prompt": row["prompt"] or "",
            "output": row["result_text"] or "",
            "handler_name": row["handler_name"] or "",
            "title": row.get("title") or None,
        }
    )


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404

    if job.status == "pending":
        cancelled = False
        if job.future is not None:
            cancelled = job.future.cancel()
        if cancelled:
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC).isoformat()
            job.output = "[cancelled before start]"
            return jsonify({"status": job.status, "id": job.id})
        job.status = "cancel_requested"
        job.cancel_event.set()
        return jsonify({"status": job.status, "id": job.id})

    if job.status == "running":
        job.status = "cancel_requested"
        job.cancel_event.set()
        return jsonify({"status": job.status, "id": job.id})

    return jsonify({"status": job.status, "id": job.id, "note": "terminal state"}), 200


@app.post("/api/jobs/cancel-all")
def api_cancel_all_jobs():
    """Cancel every pending or running job in one shot."""
    cancellable = {"pending", "running", "cancel_requested", "waiting_approval"}
    cancelled_ids: list[str] = []
    with jobs_lock:
        snapshot = list(jobs.values())
    for job in snapshot:
        if job.status not in cancellable:
            continue
        if job.status == "pending":
            if job.future is not None:
                job.future.cancel()
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC).isoformat()
            job.output = "[cancelled before start]"
        else:
            job.status = "cancel_requested"
            job.cancel_event.set()
        cancelled_ids.append(job.id)
    log.info("cancel-all: cancelled %d jobs", len(cancelled_ids))
    return jsonify({"cancelled": cancelled_ids, "count": len(cancelled_ids)})


@app.post("/api/jobs/<job_id>/stdin")
def api_job_stdin(job_id: str):
    """Send text input to a running job's subprocess stdin."""
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    if job.status not in ("running", "waiting_approval"):
        return jsonify({"error": "job not accepting input", "status": job.status}), 400
    if job.proc is None or job.proc.stdin is None:
        return jsonify({"error": "job has no stdin pipe"}), 400
    try:
        payload = request.get_json(force=True, silent=True) or {}
        text = str(payload.get("text", "")).strip()
        if not text:
            return jsonify({"error": "text required"}), 400
        job.proc.stdin.write(text + "\n")
        job.proc.stdin.flush()
        job.output_lines.append(f"[STDIN] Sent: {text}")
        log.info("Stdin sent to job %s: %s", job.id[:8], text)
        return jsonify({"ok": True, "sent": text})
    except Exception as exc:
        log.warning("Stdin write failed for job %s: %s", job.id, exc)
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Live log stream (Feature 3: SSE)
# ---------------------------------------------------------------------------


@app.get("/api/jobs/<job_id>/stream")
def api_job_stream(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found or no longer in memory"}), 404

    def generate():
        idx = 0
        while True:
            # Replay any lines we haven't sent yet
            lines = job.output_lines
            while idx < len(lines):
                line_text = lines[idx]
                # Try to parse as JSON for Claude Code structured output
                payload = None
                try:
                    parsed = json.loads(line_text)
                    payload = json.dumps(parsed)
                except (json.JSONDecodeError, TypeError):
                    payload = json.dumps({"type": "raw", "text": line_text})
                yield f"event: log\ndata: {payload}\n\n"
                idx += 1

            # Check if job is terminal
            if job.status in ("done", "failed", "cancelled"):
                yield "event: done\ndata: {}\n\n"
                return

            # Heartbeat to keep iOS connection alive
            yield "event: heartbeat\ndata: ping\n\n"
            time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _init_db()
    log.info(
        "Starting Miru Task Dispatcher on 0.0.0.0:19000 - "
        "no auth layer; intended for private Tailscale / trusted network only"
    )
    app.run(host="0.0.0.0", port=19000, debug=False, use_reloader=False, threaded=True)
