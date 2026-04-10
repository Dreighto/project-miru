"""
Miru Task Dispatcher â€” sidecar utility on port 19000.

Scope: windows/ folder only. Does not touch PM (18080), Miru AI (18765),
or any canonical runtime. In-memory job queue, optional Pushover notifications,
mobile-first single-column UI.
"""

from __future__ import annotations

import atexit
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None  # type: ignore

import os
import subprocess

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


# ---------------------------------------------------------------------------
# Configuration and logging
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DISPATCHER_ROOT = Path(__file__).resolve().parent / "dispatcher"
DISPATCHER_TEMPLATE_DIR = DISPATCHER_ROOT / "templates"
DISPATCHER_STATIC_DIR = DISPATCHER_ROOT / "static"
ENV_PATH = REPO_ROOT / ".env"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("miru.dispatcher")

# .env auto-load: repo-root .env is picked up if python-dotenv is installed.
# Missing dotenv or missing .env are both non-fatal - dispatcher still runs,
# just without Pushover unless the keys are already in the process environment.
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

VALID_MODELS = {"Claude Code", "Cursor", "ChatGPT", "Codex"}
VALID_EFFORTS = {"Quick", "Standard", "Deep"}
JOB_LIMIT = 50


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
        # Trim history beyond the hard cap. Keep in-flight jobs regardless.
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
    except Exception as exc:  # noqa: BLE001 â€” silent by design
        log.warning("Pushover send failed: %s", exc)


# ---------------------------------------------------------------------------
# Job execution â€” handler-based dispatcher
# ---------------------------------------------------------------------------


# Claude Code CLI â€” installed via npm on Windows
_CLAUDE_CLI = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"

# Cursor CLI â€” installed via Cursor app on Windows
_CURSOR_CLI = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor" / "resources" / "app" / "bin" / "cursor.cmd"


def _handler_simulation(job: Job) -> None:
    """Simulated worker for v1. Sleeps 3s with cancel checks, echoes prompt."""
    total_sleep = 3.0
    step = 0.25
    elapsed = 0.0
    while elapsed < total_sleep:
        if job.cancel_event.is_set():
            job.status = "cancelled"
            job.output = f"[cancelled mid-run]\nPrompt: {job.prompt}"
            return
        time.sleep(step)
        elapsed += step
    job.output = (
        f"[simulated {job.model} / {job.effort}]\n"
        f"Prompt echoed:\n{job.prompt}"
    )
    job.status = "done"


def _handler_claude_cli(job: Job) -> None:
    """Real handler: invokes Claude Code CLI via subprocess."""
    if not _CLAUDE_CLI.exists():
        job.status = "failed"
        job.output = f"[claude_cli] CLI not found at {_CLAUDE_CLI}"
        log.error("Claude CLI not found at %s", _CLAUDE_CLI)
        return

    # Effort â†’ max-turns hint passed as a comment prefix in the prompt.
    # Claude Code CLI does not have a native --effort flag; we annotate
    # the prompt so the model can calibrate scope.
    effort_hint = {"Quick": "brief", "Standard": "standard", "Deep": "thorough"}
    annotated_prompt = (
        f"[effort:{effort_hint.get(job.effort, 'standard')}] {job.prompt}"
    )

    timeout_seconds = {"Quick": 120, "Standard": 300, "Deep": 600}
    timeout = timeout_seconds.get(job.effort, 300)

    cmd = [str(_CLAUDE_CLI), "--print", annotated_prompt]
    log.info("Launching Claude CLI for job %s (timeout=%ds)", job.id, timeout)

    # Build env: inherit process env + ensure Git Bash is discoverable
    cli_env = os.environ.copy()
    if "CLAUDE_CODE_GIT_BASH_PATH" not in cli_env:
        _git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if _git_bash.exists():
            cli_env["CLAUDE_CODE_GIT_BASH_PATH"] = str(_git_bash)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=cli_env,
        )

        # Poll for cancel while waiting for the process to finish.
        poll_interval = 0.5
        elapsed = 0.0
        while elapsed < timeout:
            if job.cancel_event.is_set():
                proc.kill()
                job.status = "cancelled"
                job.output = "[cancelled by operator]\n" + (proc.stdout.read() or "")
                log.info("Job %s cancelled mid-run; CLI killed", job.id)
                return
            ret = proc.poll()
            if ret is not None:
                break
            time.sleep(poll_interval)
            elapsed += poll_interval
        else:
            # Timed out
            proc.kill()
            job.status = "failed"
            job.output = f"[timeout after {timeout}s]\n" + (proc.stdout.read() or "")
            log.warning("Job %s timed out after %ds", job.id, timeout)
            return

        stdout, stderr = proc.communicate()
        if proc.returncode == 0:
            job.output = stdout.strip() or "[claude_cli] (no output)"
            job.status = "done"
            log.info("Job %s completed successfully (rc=0)", job.id)
        else:
            job.status = "failed"
            job.output = (
                f"[claude_cli] exit code {proc.returncode}\n"
                f"stdout: {stdout.strip()}\n"
                f"stderr: {stderr.strip()}"
            )
            log.warning(
                "Job %s failed: rc=%d stderr=%s",
                job.id, proc.returncode, stderr[:200],
            )

    except FileNotFoundError:
        job.status = "failed"
        job.output = f"[claude_cli] CLI executable not found: {_CLAUDE_CLI}"
        log.error("Claude CLI FileNotFoundError for job %s", job.id)
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.output = f"[claude_cli] unexpected error: {exc}"
        log.exception("Unexpected error in _handler_claude_cli for job %s", job.id)


def _handler_cursor_cli(job: Job) -> None:
    """Real handler: invokes Cursor CLI agent via subprocess."""
    if not _CURSOR_CLI.exists():
        job.status = "failed"
        job.output = f"[cursor_cli] CLI not found at {_CURSOR_CLI}"
        log.error("Cursor CLI not found at %s", _CURSOR_CLI)
        return

    timeout_seconds = {"Quick": 120, "Standard": 300, "Deep": 600}
    timeout = timeout_seconds.get(job.effort, 300)

    # Cursor 3.x 'agent' subcommand is interactive-only (no --print).
    # We invoke it here so the handler is wired and ready. Once Cursor
    # ships a headless flag, update the cmd list below.
    cmd = [str(_CURSOR_CLI), "agent", job.prompt]
    log.info("Launching Cursor CLI for job %s (timeout=%ds)", job.id, timeout)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        # Poll for cancel while waiting for the process to finish.
        poll_interval = 0.5
        elapsed = 0.0
        while elapsed < timeout:
            if job.cancel_event.is_set():
                proc.kill()
                job.status = "cancelled"
                job.output = "[cancelled by operator]\n" + (proc.stdout.read() or "")
                log.info("Job %s cancelled mid-run; Cursor CLI killed", job.id)
                return
            ret = proc.poll()
            if ret is not None:
                break
            time.sleep(poll_interval)
            elapsed += poll_interval
        else:
            # Timed out
            proc.kill()
            job.status = "failed"
            job.output = f"[timeout after {timeout}s]\n" + (proc.stdout.read() or "")
            log.warning("Job %s timed out after %ds", job.id, timeout)
            return

        stdout, stderr = proc.communicate()
        if proc.returncode == 0:
            output = stdout.strip()
            if output:
                job.output = output
                job.status = "done"
                log.info("Job %s completed successfully (rc=0)", job.id)
            else:
                # Cursor agent exited 0 but produced no stdout â€” headless mode
                # not yet supported in this Cursor version.
                job.status = "failed"
                job.output = (
                    "[cursor_cli] CLI exited 0 but produced no output.\n"
                    "Cursor agent currently requires an interactive terminal.\n"
                    "This handler will work once Cursor ships a headless --print flag."
                )
                log.warning("Job %s: Cursor agent produced no stdout (headless not supported)", job.id)
        else:
            job.status = "failed"
            job.output = (
                f"[cursor_cli] exit code {proc.returncode}\n"
                f"stdout: {stdout.strip()}\n"
                f"stderr: {stderr.strip()}"
            )
            log.warning(
                "Job %s failed: rc=%d stderr=%s",
                job.id, proc.returncode, stderr[:200],
            )

    except FileNotFoundError:
        job.status = "failed"
        job.output = f"[cursor_cli] CLI executable not found: {_CURSOR_CLI}"
        log.error("Cursor CLI FileNotFoundError for job %s", job.id)
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.output = f"[cursor_cli] unexpected error: {exc}"
        log.exception("Unexpected error in _handler_cursor_cli for job %s", job.id)


# Map model names to handler functions.
_HANDLERS: dict[str, Any] = {
    "Claude Code": _handler_claude_cli,   # real â€” Phase A
    "Cursor":      _handler_cursor_cli,   # real â€” Phase B
    "ChatGPT":     _handler_simulation,   # deferred
    "Codex":       _handler_simulation,   # deferred
}


def _get_handler(model: str):
    return _HANDLERS.get(model, _handler_simulation)


def run_job(job: Job) -> None:
    """Dispatcher: resolves handler by model, manages lifecycle + notifications."""
    job.status = "running"
    handler = _get_handler(job.model)
    job.handler_name = handler.__name__
    job.executor_mode = "simulated" if handler is _handler_simulation else "real"
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

    if job.status in ("done", "failed"):
        job_url = f"{DISPATCHER_BASE_URL}/jobs/{job.id}" if DISPATCHER_BASE_URL else ""
        send_pushover(
            title=f"Miru job {job.id[:8]} {job.status.upper()}",
            message=job.output_preview,
            url=job_url,
        )


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(
    __name__,
    template_folder=str(DISPATCHER_TEMPLATE_DIR),
    static_folder=str(DISPATCHER_STATIC_DIR),
    static_url_path="/static",
)


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
    handler = _get_handler(model)
    job.handler_name = handler.__name__
    job.executor_mode = "simulated" if handler is _handler_simulation else "real"
    _store_job(job)
    job.future = executor.submit(run_job, job)
    return jsonify(job.to_detail()), 201


@app.get("/api/jobs")
def api_list_jobs():
    return jsonify({"jobs": [j.to_summary() for j in _listed_jobs()]})


@app.get("/api/jobs/<job_id>")
def api_get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job.to_detail())


@app.post("/api/jobs/<job_id>/cancel")
def api_cancel_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({"error": "job not found"}), 404

    if job.status == "pending":
        # Attempt to cancel via executor. If it fails (already picked up), fall through.
        cancelled = False
        if job.future is not None:
            cancelled = job.future.cancel()
        if cancelled:
            job.status = "cancelled"
            job.finished_at = datetime.now(timezone.utc).isoformat()
            job.output = "[cancelled before start]"
            return jsonify({"status": job.status, "id": job.id})
        # else: slipped into running
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
    by_model: dict[str, int] = {}
    by_executor_mode: dict[str, int] = {"simulated": 0, "real": 0}
    success = fail = 0
    durations: list[float] = []
    total_input_tokens = 0
    total_output_tokens = 0
    total_estimated_cost = 0.0
    has_token_data = False
    for j in all_jobs:
        by_model[j.model] = by_model.get(j.model, 0) + 1
        if j.executor_mode in by_executor_mode:
            by_executor_mode[j.executor_mode] += 1
        if j.status == "done":
            success += 1
        elif j.status == "failed":
            fail += 1
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
    return jsonify({
        "total_jobs": len(all_jobs),
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
    })


# ---------------------------------------------------------------------------
# HTML job detail
# ---------------------------------------------------------------------------


@app.get("/jobs/<job_id>")
def html_job_detail(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return Response(
            "<html><body style='background:#0d0d0f;color:#f0edf8;font-family:sans-serif;padding:40px'>"
            "<h1>Job not found</h1><a href='/' style='color:#c9a84c'>&larr; Dashboard</a></body></html>",
            status=404,
            mimetype="text/html",
        )
    duration = ""
    if job.finished_at and job.created_at:
        try:
            dt = datetime.fromisoformat(job.finished_at) - datetime.fromisoformat(job.created_at)
            duration = f"{dt.total_seconds():.1f}s"
        except Exception:
            pass
    usage_parts: list[str] = []
    if job.input_tokens is not None:
        usage_parts.append(f"Input: {job.input_tokens:,} tokens")
    if job.output_tokens is not None:
        usage_parts.append(f"Output: {job.output_tokens:,} tokens")
    if job.estimated_cost is not None:
        usage_parts.append(f"Est. cost: ${job.estimated_cost:.4f}")
    usage_block = ""
    if usage_parts:
        usage_block = (
            '<div class="field"><div class="field-label">Usage</div>'
            '<div class="field-value">' + " &middot; ".join(usage_parts) + "</div></div>"
        )
    return render_template(
        "job_detail.html",
        job_id=job.id,
        job_id_short=job.id[:8],
        status=job.status,
        model=job.model,
        effort=job.effort,
        created_at=job.created_at or "-",
        finished_at=job.finished_at or "(still running)",
        duration=duration or "-",
        executor_mode=job.executor_mode,
        handler_name=job.handler_name or "-",
        usage_block=usage_block,
        prompt=job.prompt,
        output=job.output or "(no output yet)",
    )


# ---------------------------------------------------------------------------
# Operator surface
# ---------------------------------------------------------------------------


@app.get("/admin/dispatcher/logs")
def admin_logs():
    log_path = REPO_ROOT / "data" / "startup-logs" / "dispatcher_19000_stderr.log"
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
# UI
# ---------------------------------------------------------------------------


@app.get("/")
def index():
    return render_template("dispatcher.html")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info(
        "Starting Miru Task Dispatcher on 0.0.0.0:19000 (pushover=%s) - "
        "no auth layer; intended for private Tailscale / trusted network only",
        PUSHOVER_ENABLED,
    )
    # use_reloader disabled so the ThreadPoolExecutor is not duplicated
    app.run(host="0.0.0.0", port=19000, debug=False, use_reloader=False)

