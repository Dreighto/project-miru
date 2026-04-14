"""
Miru Task Dispatcher — sidecar utility on port 19000.

Scope: windows/ folder only. Does not touch PM (18080), Miru AI (18765),
or any canonical runtime. In-memory job queue + SQLite history, optional
Pushover notifications, operator dashboard UI.
"""

from __future__ import annotations

import atexit
import io
import json
import logging
import mimetypes
import os
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore

from flask_sock import Sock


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
        "python-dotenv not installed; .env at %s will NOT be auto-loaded "
        "(Pushover will be disabled unless PUSHOVER_* keys are already in the process env)",
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

from handlers import get_handler, resolve_executor_mode  # noqa: E402

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PUSHOVER_USER_KEY = os.environ.get("PUSHOVER_USER_KEY", "").strip()
PUSHOVER_API_TOKEN = os.environ.get("PUSHOVER_API_TOKEN", "").strip()
PUSHOVER_ENABLED = bool(PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN and requests is not None)

if not PUSHOVER_ENABLED:
    _missing = []
    if not PUSHOVER_USER_KEY:
        _missing.append("PUSHOVER_USER_KEY")
    if not PUSHOVER_API_TOKEN:
        _missing.append("PUSHOVER_API_TOKEN")
    if requests is None:
        _missing.append("requests module")
    log.warning(
        "Pushover disabled - notifications will be skipped (missing: %s)",
        ", ".join(_missing),
    )

DISPATCHER_BASE_URL = os.environ.get("DISPATCHER_BASE_URL", "").strip()

VALID_MODELS = {"Ollama", "Claude", "Cursor", "Gemini"}
VALID_EFFORTS = {"Quick", "Standard", "Deep"}
JOB_LIMIT = 50

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
            try:
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
        try:
            dur = (
                datetime.fromisoformat(job.finished_at)
                - datetime.fromisoformat(job.created_at)
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
                    job.id, job.created_at, job.finished_at, job.prompt,
                    job.model, job.effort, job.handler_name, job.executor_mode,
                    job.status, result_text, err_msg,
                    job.input_tokens, job.output_tokens, job.estimated_cost, dur,
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
            row = conn.execute(
                "SELECT * FROM job_history WHERE job_id=?", (job_id,)
            ).fetchone()
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
        "id",
        "status",
        "prompt",
        "model",
        "effort",
        "created_at",
        "finished_at",
        "output",
        "executor_mode",
        "handler_name",
        "input_tokens",
        "output_tokens",
        "estimated_cost",
        "cancel_event",
        "future",
        "output_lines",
        "title",
    )

    def __init__(self, prompt: str, model: str, effort: str) -> None:
        self.id: str = str(uuid.uuid4())
        self.status: str = "pending"
        self.prompt: str = prompt
        self.model: str = model
        self.effort: str = effort
        self.created_at: str = datetime.now(timezone.utc).isoformat()
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

    @property
    def output_preview(self) -> str:
        return self.output[:200]

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
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
            for stale_id in jobs_order[JOB_LIMIT * 2:]:
                candidate = jobs.get(stale_id)
                if candidate and candidate.status in {"done", "failed", "cancelled"}:
                    jobs.pop(stale_id, None)
            jobs_order[:] = [jid for jid in jobs_order if jid in jobs]


def _listed_jobs() -> list[Job]:
    with jobs_lock:
        return [jobs[jid] for jid in jobs_order[:JOB_LIMIT] if jid in jobs]


# ---------------------------------------------------------------------------
# Pushover
# ---------------------------------------------------------------------------


def send_pushover(title: str, message: str, url: str = "") -> None:
    if not PUSHOVER_ENABLED:
        return
    try:
        data: dict[str, str] = {
            "token": PUSHOVER_API_TOKEN,
            "user": PUSHOVER_USER_KEY,
            "title": title[:250],
            "message": message[:1024],
        }
        if url:
            data["url"] = url[:512]
            data["url_title"] = "View job"
        resp = requests.post(
            "https://api.pushover.net/1/messages.json",
            data=data,
            timeout=8,
        )
        if resp.status_code != 200:
            log.warning("Pushover non-200: %s", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        log.warning("Pushover send failed: %s", exc)


# ---------------------------------------------------------------------------
# Auto-generated job titles via Gemini CLI (Fix 4)
# ---------------------------------------------------------------------------


def _generate_title_background(job: Job) -> None:
    """Background thread: ask Gemini CLI for a 4-6 word job title."""
    import shutil

    cli = shutil.which("gemini")
    if cli is None:
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidate = os.path.join(appdata, "npm", "gemini.cmd")
            if os.path.isfile(candidate):
                cli = candidate
    if cli is None:
        job.title = (job.prompt or "")[:50].strip() or "Untitled"
        _db_upsert_job(job)
        log.info("Title fallback (no CLI) for job %s: %s", job.id[:8], job.title)
        return

    prompt_snippet = (job.prompt or "")[:200].replace('"', "'")
    gen_prompt = (
        "Generate a 4-6 word title for this task. "
        "Reply with ONLY the title, no punctuation, no quotes, no explanation: "
        + prompt_snippet
    )
    cli_env = os.environ.copy()
    cli_env["NO_COLOR"] = "1"
    cli_env["TERM"] = "dumb"

    try:
        # Use Popen + manual timeout so we can kill the entire process tree
        # on Windows (subprocess.run timeout leaves orphan node.exe children).
        _cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [cli, "-p", gen_prompt],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_REPO_ROOT),
            creationflags=_cflags,
            env=cli_env,
        )
        try:
            stdout, _stderr = proc.communicate(timeout=30)
            raw = stdout.strip() if proc.returncode == 0 else ""
        except subprocess.TimeoutExpired:
            log.warning("Title gen timeout for job %s — killing process tree", job.id[:8])
            # Kill entire process tree (node.exe children) on Windows
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    timeout=5, creationflags=_cflags,
                )
            except Exception:
                proc.kill()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            raw = ""
    except Exception as exc:
        log.warning("Title gen error for job %s: %s", job.id[:8], exc)
        raw = ""

    # Sanitize: strip quotes, newlines, JSON artifacts, limit length
    title = raw.replace('"', "").replace("'", "").replace("\n", " ").strip()
    # Remove any JSON-like wrapping
    if title.startswith("{") or title.startswith("["):
        title = ""
    # Truncate to reasonable length
    if len(title) > 60:
        title = title[:57] + "..."
    # Fallback
    if not title:
        title = (job.prompt or "")[:50].strip() or "Untitled"

    job.title = title
    _db_upsert_job(job)
    log.info("Title generated for job %s: %s", job.id[:8], job.title)


# ---------------------------------------------------------------------------
# Job execution
# ---------------------------------------------------------------------------


def run_job(job: Job) -> None:
    """Resolve handler by model, manage lifecycle + notifications."""
    job.status = "running"
    handler = get_handler(job.model)
    job.handler_name = handler.__name__
    job.executor_mode = resolve_executor_mode(handler)
    try:
        handler(job)
        if job.status == "running":
            job.status = "done"
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.output = f"Error: {exc}"
        log.exception("Job %s failed", job.id)
    finally:
        if job.finished_at is None:
            job.finished_at = datetime.now(timezone.utc).isoformat()

    _db_upsert_job(job)

    if job.status in ("done", "failed"):
        job_url = f"{DISPATCHER_BASE_URL}/jobs/{job.id}" if DISPATCHER_BASE_URL else ""
        send_pushover(
            title=f"Miru job {job.id[:8]} {job.status.upper()}",
            message=job.output_preview,
            url=job_url,
        )
        # Fix 4: Auto-generate title in background daemon thread
        t = threading.Thread(
            target=_generate_title_background, args=(job,), daemon=True
        )
        t.start()


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(DISPATCHER_TEMPLATE_DIR),
    static_folder=str(DISPATCHER_STATIC_DIR),
    static_url_path="/static",
)
sock = Sock(app)

# ---------------------------------------------------------------------------
# AssemblyAI Universal Streaming config
# ---------------------------------------------------------------------------

ASSEMBLYAI_API_KEY = os.environ.get("ASSEMBLY_AI_API_KEY", "").strip()


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
    return jsonify({
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
    })


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
            job.finished_at = datetime.now(timezone.utc).isoformat()
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


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@app.get("/api/stats")
def api_stats():
    all_jobs = _listed_jobs()
    by_model: dict[str, dict[str, int]] = {}
    by_executor_mode: dict[str, int] = {"simulated": 0, "real": 0, "local": 0}
    success = fail = running = pending = 0
    durations: list[float] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_estimated_cost = 0.0
    has_token_data = False
    for j in all_jobs:
        bm = by_model.setdefault(j.model, {"total": 0, "success": 0, "fail": 0})
        bm["total"] += 1
        if j.executor_mode in by_executor_mode:
            by_executor_mode[j.executor_mode] += 1
        if j.status == "done":
            success += 1
            bm["success"] += 1
        elif j.status == "failed":
            fail += 1
            bm["fail"] += 1
        elif j.status == "running":
            running += 1
        elif j.status == "pending":
            pending += 1
        if j.input_tokens is not None:
            total_input_tokens += j.input_tokens
            has_token_data = True
        if j.output_tokens is not None:
            total_output_tokens += j.output_tokens
            has_token_data = True
        if j.estimated_cost is not None:
            total_estimated_cost += j.estimated_cost
        if j.finished_at and j.created_at:
            try:
                dt = (
                    datetime.fromisoformat(j.finished_at)
                    - datetime.fromisoformat(j.created_at)
                ).total_seconds() * 1000
                durations.append(dt)
            except Exception:
                pass
    avg_ms = round(sum(durations) / len(durations), 1) if durations else None
    p95_ms = None
    if durations:
        durations.sort()
        p95_ms = round(durations[min(int(len(durations) * 0.95), len(durations) - 1)], 1)
    cum = _db_cumulative_stats()
    return jsonify({
        "total_jobs": len(all_jobs),
        "running_count": running,
        "pending_count": pending,
        "by_model": by_model,
        "by_executor_mode": by_executor_mode,
        "success_count": success,
        "fail_count": fail,
        "avg_duration_ms": avg_ms,
        "p95_duration_ms": p95_ms,
        "tokens": {
            "total_input": total_input_tokens,
            "total_output": total_output_tokens,
            "total_estimated_cost_usd": round(total_estimated_cost, 6),
        } if has_token_data else None,
        "cumulative": {
            "total_jobs": cum.get("total", 0),
            "success": cum.get("success", 0),
            "fail": cum.get("fail", 0),
            "total_input_tokens": cum.get("total_in", 0),
            "total_output_tokens": cum.get("total_out", 0),
        },
    })


# ---------------------------------------------------------------------------
# Job history API
# ---------------------------------------------------------------------------


@app.get("/api/history")
def api_history():
    limit = request.args.get("limit", "20", type=str)
    try:
        limit_int = min(int(limit), 100)
    except ValueError:
        limit_int = 20
    status = request.args.get("status", "").strip()
    executor_filter = request.args.get("executor", "").strip()
    rows = _db_query_history(limit=limit_int, status=status, executor=executor_filter)
    return jsonify({"jobs": rows})


# ---------------------------------------------------------------------------
# File browser API
# ---------------------------------------------------------------------------

_FILE_ROOT = REPO_ROOT

# External pinned directories (outside repo) that the file browser may access
_EXTERNAL_PINS: dict[str, Path] = {
    "__screenshots__": Path(r"C:\temp\playwright-shots"),
}


def _safe_resolve(rel_path: str) -> Path | None:
    """Resolve a relative path under _FILE_ROOT or an allowed external pin.
    Returns None if it escapes all allowed roots."""
    # Check external pin prefixes first  (e.g. "__screenshots__/foo.png")
    for prefix, ext_root in _EXTERNAL_PINS.items():
        if rel_path == prefix or rel_path.startswith(prefix + "/") or rel_path.startswith(prefix + "\\"):
            sub = rel_path[len(prefix):].lstrip("/\\")
            target = (ext_root / sub).resolve() if sub else ext_root.resolve()
            if not str(target).startswith(str(ext_root.resolve())):
                return None
            return target
    try:
        target = (_FILE_ROOT / rel_path).resolve()
        if not str(target).startswith(str(_FILE_ROOT.resolve())):
            return None
        return target
    except Exception:
        return None


_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".css", ".html", ".htm", ".json",
    ".md", ".txt", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env",
    ".sh", ".bash", ".ps1", ".cmd", ".bat", ".sql", ".csv", ".xml",
    ".gitignore", ".dockerignore", ".editorconfig", ".prettierrc",
    ".eslintrc", ".lock", ".log", ".conf", ".rs", ".go", ".java",
    ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".swift", ".kt",
}


def _is_text_file(p: Path) -> bool:
    if p.suffix.lower() in _TEXT_EXTENSIONS:
        return True
    if p.suffix == "" and p.name.startswith("."):
        return True
    mt, _ = mimetypes.guess_type(str(p))
    return mt is not None and mt.startswith("text/")


def _child_rel_path(child: Path, rel: str) -> str:
    """Build a browser-safe relative path for a child entry."""
    # For external pins, prefix with the virtual mount name
    for prefix, ext_root in _EXTERNAL_PINS.items():
        try:
            child_rel = child.relative_to(ext_root.resolve())
            return (prefix + "/" + str(child_rel)).replace("\\", "/")
        except ValueError:
            continue
    return str(child.relative_to(_FILE_ROOT)).replace("\\", "/")


@app.get("/api/files")
def api_files():
    rel = request.args.get("path", "").strip().lstrip("/\\")
    target = _safe_resolve(rel)
    if target is None or not target.exists():
        return jsonify({"error": "path not found"}), 404
    if not target.is_dir():
        return jsonify({"error": "not a directory"}), 400
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith(".git") and child.name != ".gitignore":
                continue
            if child.name in ("__pycache__", "node_modules"):
                continue
            try:
                st = child.stat()
                entries.append({
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size": st.st_size if child.is_file() else None,
                    "mtime": st.st_mtime,
                    "path": _child_rel_path(child, rel),
                })
            except OSError:
                continue
    except PermissionError:
        return jsonify({"error": "permission denied"}), 403
    return jsonify({"path": rel or ".", "entries": entries})


@app.get("/api/file")
def api_file():
    rel = request.args.get("path", "").strip().lstrip("/\\")
    target = _safe_resolve(rel)
    if target is None or not target.exists():
        return jsonify({"error": "file not found"}), 404
    if not target.is_file():
        return jsonify({"error": "not a file"}), 400
    if target.stat().st_size > 2 * 1024 * 1024:
        return jsonify({"error": "file too large (>2MB)", "size": target.stat().st_size}), 413
    is_text = _is_text_file(target)
    if not is_text:
        return jsonify({
            "path": rel, "is_text": False,
            "size": target.stat().st_size, "content": None,
        })
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return jsonify({"error": f"read failed: {exc}"}), 500
    return jsonify({"path": rel, "is_text": True, "size": len(content), "content": content})


@app.post("/api/files/download-zip")
def api_download_zip():
    """Accept a JSON list of relative paths, return a ZIP."""
    payload = request.get_json(force=True, silent=True) or {}
    paths = payload.get("paths", [])
    if not paths or not isinstance(paths, list):
        return jsonify(error="paths list required"), 400
    if len(paths) > 50:
        return jsonify(error="too many files (max 50)"), 400

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in paths:
            rel = str(rel).lstrip("/")
            target = _safe_resolve(rel)
            if target is None or not target.is_file():
                continue
            if target.stat().st_size > 50 * 1024 * 1024:
                continue
            zf.write(target, arcname=target.name)

    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name="dispatch-export-" + datetime.now().strftime("%Y-%m-%d") + ".zip",
    )


# ---------------------------------------------------------------------------
# Runtime health + restart
# ---------------------------------------------------------------------------

_health_state: dict[str, dict[str, Any]] = {
    "pm": {"status": "unknown", "last_checked": None, "detail": None},
    "miru_ai": {"status": "unknown", "last_checked": None, "detail": None},
}
_health_lock = threading.Lock()


def _check_http(url: str, timeout: int = 5) -> bool:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def _poll_until_up(service: str, url: str, max_wait: int = 30):
    """Background thread: poll every 2 s until service returns HTTP 200."""
    port = "18080" if service == "pm" else "18765"
    label = "PM" if service == "pm" else "Miru AI"
    time.sleep(5)  # grace period — let restart script kill the old process
    start = time.time()
    remaining = max(max_wait - 5, 10)
    while time.time() - start < remaining:
        if _check_http(url):
            with _health_lock:
                _health_state[service] = {
                    "status": "up",
                    "last_checked": datetime.now(timezone.utc).isoformat(),
                    "detail": f"{label} restarted and healthy on :{port}",
                }
            log.info("%s is UP after restart", service)
            return
        time.sleep(2)
    with _health_lock:
        _health_state[service] = {
            "status": "down",
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "detail": f"Restart timed out — {label} still unreachable on :{port} after {max_wait}s",
        }
    log.warning("%s did not come up within %ds", service, max_wait)


@sock.route("/api/voice/stream")
def api_voice_stream(ws):
    """Browser WebSocket → AssemblyAI Universal Streaming proxy (u3-rt-pro)."""
    from urllib.parse import urlencode as _urlencode
    import websockets.sync.client as _wss_client

    print("[Voice] Client connected", flush=True)  # LOG 1

    if not ASSEMBLYAI_API_KEY:
        try:
            ws.send(json.dumps({"error": "AssemblyAI API key not configured"}))
        except Exception:
            pass
        return

    keyterms = [
        "Flask", "Python", "Tailscale", "camelCase", "dispatcher", "worktree",
        "phase2", "Claude", "Gemini", "Codex", "Cursor", "Ollama",
        "18765", "19000", "18080", "git branch", "SSE", "WebSocket", "subprocess",
    ]
    params = [
        ("sample_rate", "16000"),
        ("encoding", "pcm_s16le"),
        ("speech_model", "u3-rt-pro"),
    ] + [("word_boost", t) for t in keyterms]
    aai_url = f"wss://streaming.assemblyai.com/v3/ws?{_urlencode(params)}"

    try:
        aai_ws = _wss_client.connect(
            aai_url,
            additional_headers={"Authorization": ASSEMBLYAI_API_KEY},
        )
    except Exception as exc:
        log.error("AssemblyAI connect failed: %s", exc)
        print("[Voice] Exception: AAI connect failed:", str(exc), flush=True)  # LOG 6
        try:
            ws.send(json.dumps({"error": f"Could not connect to transcription service: {exc}"}))
        except Exception:
            pass
        return

    stop_evt = threading.Event()
    ws_send_lock = threading.Lock()  # protect flask-sock ws.send() from relay thread

    def _relay_from_aai():
        """Forward AssemblyAI transcript messages to the browser WebSocket."""
        try:
            while not stop_evt.is_set():
                try:
                    raw = aai_ws.recv(timeout=1)
                except TimeoutError:
                    continue
                except Exception as e:
                    print("[Voice] Exception: AAI recv failed:", str(e), flush=True)  # LOG 6
                    break
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                msg_type = msg.get("type", "")
                transcript_preview = (msg.get("transcript") or msg.get("text") or "")[:50]
                print("[Voice] AAI message type:", msg_type, "transcript:", transcript_preview, flush=True)  # LOG 3
                if msg_type == "Begin":
                    print("[Voice] AssemblyAI connected, Begin received:", raw[:100], flush=True)  # LOG 2
                elif msg_type == "Turn":
                    # AAI v3 Universal Streaming uses Turn messages (not PartialTranscript/FinalTranscript)
                    # transcript field contains cumulative text; end_of_turn=True means utterance is complete
                    text = (msg.get("transcript") or "").strip()
                    is_final = bool(msg.get("end_of_turn", False))
                    if text:
                        try:
                            json_payload = json.dumps({"text": text, "is_final": is_final})
                            print("[Voice] Sending to browser:", json_payload[:100], flush=True)  # LOG 4
                            with ws_send_lock:
                                ws.send(json_payload)
                        except Exception as e:
                            print("[Voice] Exception: browser send failed:", str(e), flush=True)  # LOG 6
                            break
                elif msg_type == "PartialTranscript":
                    # v2 fallback — kept for compatibility
                    text = (msg.get("text") or "").strip()
                    if text:
                        try:
                            json_payload = json.dumps({"text": text, "is_final": False})
                            print("[Voice] Sending to browser:", json_payload[:100], flush=True)  # LOG 4
                            with ws_send_lock:
                                ws.send(json_payload)
                        except Exception as e:
                            print("[Voice] Exception: browser send failed:", str(e), flush=True)  # LOG 6
                            break
                elif msg_type == "FinalTranscript":
                    # v2 fallback — kept for compatibility
                    text = (msg.get("text") or "").strip()
                    try:
                        json_payload = json.dumps({"text": text, "is_final": True})
                        print("[Voice] Sending to browser:", json_payload[:100], flush=True)  # LOG 4
                        with ws_send_lock:
                            ws.send(json_payload)
                    except Exception as e:
                        print("[Voice] Exception: browser send failed:", str(e), flush=True)  # LOG 6
                        break
        except Exception as e:
            print("[Voice] Exception: relay thread outer:", str(e), flush=True)  # LOG 6

    relay_thread = threading.Thread(target=_relay_from_aai, daemon=True)
    relay_thread.start()

    _chunk_count = 0  # LOG 5: counter for first-3 audio chunk logging
    try:
        while True:
            try:
                data = ws.receive()
            except Exception as e:
                print("[Voice] Exception: browser receive failed:", str(e), flush=True)  # LOG 6
                break
            if data is None:
                break
            if _chunk_count < 3:  # LOG 5
                print("[Voice] Received audio chunk, size:", len(data), flush=True)
                _chunk_count += 1
            try:
                aai_ws.send(data)
            except Exception as exc:
                log.warning("AssemblyAI send failed: %s", exc)
                print("[Voice] Exception: AAI send failed:", str(exc), flush=True)  # LOG 6
                break
    finally:
        stop_evt.set()
        try:
            aai_ws.send(json.dumps({"type": "Terminate"}))
        except Exception:
            pass
        try:
            aai_ws.close()
        except Exception:
            pass
        relay_thread.join(timeout=3)
        log.info("Voice stream session closed")


@app.get("/api/health")
def api_health():
    now = datetime.now(timezone.utc).isoformat()
    with _health_lock:
        pm_restarting = _health_state["pm"].get("status") == "restarting"
        miru_restarting = _health_state["miru_ai"].get("status") == "restarting"

    pm_up = None if pm_restarting else _check_http("http://127.0.0.1:18080/", timeout=3)
    miru_up = None if miru_restarting else _check_http("http://127.0.0.1:18765/api/health", timeout=3)

    with _health_lock:
        if pm_up is not None:
            _health_state["pm"] = {
                "status": "up" if pm_up else "down",
                "last_checked": now,
                "detail": None if pm_up else _health_state["pm"].get("detail"),
            }
        if miru_up is not None:
            _health_state["miru_ai"] = {
                "status": "up" if miru_up else "down",
                "last_checked": now,
                "detail": None if miru_up else _health_state["miru_ai"].get("detail"),
            }
        return jsonify({
            "dispatcher": {"status": "up"},
            "pm": dict(_health_state["pm"]),
            "miru_ai": dict(_health_state["miru_ai"]),
        })


_RESTART_SCRIPTS = {
    "pm": REPO_ROOT / "windows" / "restart_pm.ps1",
    "miru_ai": REPO_ROOT / "windows" / "restart_miru_ai.ps1",
}
_HEALTH_URLS = {
    "pm": "http://127.0.0.1:18080/",
    "miru_ai": "http://127.0.0.1:18765/api/health",
}


@app.post("/api/restart/<service>")
def api_restart_service(service: str):
    if service not in _RESTART_SCRIPTS:
        return jsonify({"status": "error", "detail": f"unknown service: {service}"}), 400
    script = _RESTART_SCRIPTS[service]
    if not script.exists():
        return jsonify({
            "status": "error",
            "detail": f"restart script not found: {script.name}",
        }), 404
    port = "18080" if service == "pm" else "18765"
    label = "PM" if service == "pm" else "Miru AI"
    with _health_lock:
        _health_state[service] = {
            "status": "restarting",
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "detail": None,
        }
    try:
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(script),
            ],
            cwd=str(REPO_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as exc:
        detail = f"Failed to launch restart script for {label}: {exc}"
        with _health_lock:
            _health_state[service] = {
                "status": "down",
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "detail": detail,
            }
        return jsonify({"status": "error", "detail": detail}), 500
    url = _HEALTH_URLS[service]
    threading.Thread(target=_poll_until_up, args=(service, url), daemon=True).start()
    log.info("Restart initiated for %s via %s; polling %s", service, script.name, url)
    return jsonify({
        "status": "restarting",
        "detail": f"Restart command sent for {label}; verifying on :{port}...",
    }), 202


# ---------------------------------------------------------------------------
# HTML job detail
# ---------------------------------------------------------------------------


@app.get("/jobs/<job_id>")
def html_job_detail(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    row = None
    if job is None:
        row = _db_get_job(job_id)
    if job is None and row is None:
        return Response(
            "<html><body style='background:#0d0d0f;color:#f0edf8;font-family:sans-serif;padding:40px'>"
            "<h1>Job not found</h1><a href='/' style='color:#c9a84c'>&larr; Dashboard</a></body></html>",
            status=404,
            mimetype="text/html",
        )
    if job is not None:
        d = {
            "id": job.id, "status": job.status, "model": job.model,
            "effort": job.effort, "created_at": job.created_at,
            "finished_at": job.finished_at, "executor_mode": job.executor_mode,
            "handler_name": job.handler_name, "input_tokens": job.input_tokens,
            "output_tokens": job.output_tokens, "estimated_cost": job.estimated_cost,
            "prompt": job.prompt, "output": job.output,
        }
    else:
        d = {
            "id": row["job_id"], "status": row["status"] or "unknown",
            "model": row["model"] or "", "effort": row["effort"] or "",
            "created_at": row["created_at"], "finished_at": row["finished_at"],
            "executor_mode": row["executor_mode"] or "",
            "handler_name": row["handler_name"] or "",
            "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
            "estimated_cost": row["estimated_cost"],
            "prompt": row["prompt"] or "", "output": row["result_text"] or "",
        }
    duration = ""
    if d["finished_at"] and d["created_at"]:
        try:
            dt = datetime.fromisoformat(d["finished_at"]) - datetime.fromisoformat(d["created_at"])
            duration = f"{dt.total_seconds():.1f}s"
        except Exception:
            pass
    usage_parts: list[str] = []
    if d["input_tokens"] is not None:
        usage_parts.append(f"Input: {d['input_tokens']:,} tokens")
    if d["output_tokens"] is not None:
        usage_parts.append(f"Output: {d['output_tokens']:,} tokens")
    if d["estimated_cost"] is not None:
        usage_parts.append(f"Est. cost: ${d['estimated_cost']:.4f}")
    usage_block = ""
    if usage_parts:
        usage_block = (
            '<div class="field"><div class="field-label">Usage</div>'
            '<div class="field-value">' + " &middot; ".join(usage_parts) + "</div></div>"
        )
    return render_template(
        "job_detail.html",
        job_id=d["id"],
        job_id_short=d["id"][:8],
        status=d["status"],
        model=d["model"],
        effort=d["effort"],
        created_at=d["created_at"] or "-",
        finished_at=d["finished_at"] or "(still running)",
        duration=duration or "-",
        executor_mode=d["executor_mode"],
        handler_name=d["handler_name"] or "-",
        usage_block=usage_block,
        prompt=d["prompt"],
        output=d["output"] or "(no output yet)",
    )


# ---------------------------------------------------------------------------
# Operator surface
# ---------------------------------------------------------------------------


@app.get("/admin/dispatcher/logs")
def admin_logs():
    log_path = REPO_ROOT / "logs" / "dispatcher_19000_stderr.log"
    if not log_path.exists():
        return jsonify({"lines": [], "note": "log file not found"})
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return jsonify({"lines": lines[-100:]})
    except Exception as e:
        return jsonify({"lines": [], "error": str(e)})


@app.post("/admin/dispatcher/restart")
def admin_restart():
    launcher = REPO_ROOT / "windows" / "start_dispatcher.ps1"
    if not launcher.exists():
        return jsonify({"error": "launcher not found"}), 500
    try:
        subprocess.Popen(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", str(launcher), "-Force",
            ],
            cwd=str(REPO_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "restarting", "note": "dispatcher will restart in ~2s"})


# ---------------------------------------------------------------------------
# Git context (Feature 1: Context Bar)
# ---------------------------------------------------------------------------

_REPO_ROOT = r"D:\dev\tcg-watcher-worktree"


@app.get("/api/git/context")
def api_git_context():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = result.stdout.strip() or "unknown"
    except Exception:
        branch = "unknown"
    repo_name = os.path.basename(_REPO_ROOT)
    return jsonify({"branch": branch, "repo": repo_name, "path": _REPO_ROOT})


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
# UI
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    return render_template("dispatcher.html", cache_bust=int(time.time()))


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _init_db()
    log.info(
        "Starting Miru Task Dispatcher on 0.0.0.0:19000 (pushover=%s) - "
        "no auth layer; intended for private Tailscale / trusted network only",
        PUSHOVER_ENABLED,
    )
    app.run(host="0.0.0.0", port=19000, debug=False, use_reloader=False,
            threaded=True)
