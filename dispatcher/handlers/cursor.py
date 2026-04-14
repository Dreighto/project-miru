"""
Cursor headless handler via the bundled Claude Agent SDK CLI.

Cursor 3.0.16 ships the Claude Code CLI v2.1.70 at:
    extensions/cursor-agent/dist/claude-agent-sdk/cli.js
This CLI supports ``-p/--print`` for non-interactive output and
``--output-format json`` for structured parsing.

Invocation: ``node cli.js --print --output-format json "<prompt>"``

Environment notes (Windows):
  - ``CLAUDECODE`` must be *unset* or the CLI refuses to start
    (nested-session guard).
  - ``CLAUDE_CODE_GIT_BASH_PATH`` must point to Git Bash or the CLI
    errors out on Windows.

Failure surfacing:
  - Non-zero exit code → job fails with exit code + stderr.
  - Zero exit + empty stdout → job fails as EXPERIMENTAL.
  - JSON parse failure → falls back to raw stdout.
  - Timeout → job killed and marked failed.

Tested against: Cursor 3.0.16 / Claude Code SDK 2.1.70 / Node 24.x.
Cursor remains EXPERIMENTAL — promotion requires explicit operator OK.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger("miru.dispatcher.handler.cursor")

# Repo root — three levels up: handlers/ → dispatcher/ → repo/
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_CURSOR_APP_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor"
_CURSOR_CLI = _CURSOR_APP_DIR / "resources" / "app" / "bin" / "cursor.cmd"
_CURSOR_AGENT_CLI = (
    _CURSOR_APP_DIR / "resources" / "app" / "extensions"
    / "cursor-agent" / "dist" / "claude-agent-sdk" / "cli.js"
)


def handler(job) -> None:
    """Headless Cursor handler via the bundled Agent SDK CLI."""
    # --- Resolve the CLI binary -----------------------------------------------
    if not _CURSOR_AGENT_CLI.exists():
        if _CURSOR_CLI.exists():
            job.status = "failed"
            job.output = (
                "[cursor_cli] Cursor is installed but the bundled Agent SDK "
                f"CLI was not found at:\n  {_CURSOR_AGENT_CLI}\n\n"
                "This Cursor version may not include the headless CLI. "
                "Executor remains EXPERIMENTAL / SCAFFOLDED."
            )
        else:
            job.status = "failed"
            job.output = f"[cursor_cli] Cursor not found at {_CURSOR_APP_DIR}"
        log.error("Cursor Agent CLI not found at %s", _CURSOR_AGENT_CLI)
        return

    # --- Build command --------------------------------------------------------
    timeout_seconds = {"Quick": 120, "Standard": 300, "Deep": 600}
    timeout = timeout_seconds.get(job.effort, 300)

    effort_flag = {"Quick": "low", "Standard": "medium", "Deep": "high"}
    effort_val = effort_flag.get(job.effort, "medium")

    cmd = [
        "node", str(_CURSOR_AGENT_CLI),
        "--print",
        "--output-format", "json",
        "--effort", effort_val,
        job.prompt,
    ]
    log.info(
        "Launching Cursor headless CLI for job %s (timeout=%ds, effort=%s)",
        job.id, timeout, effort_val,
    )

    # --- Build environment ---------------------------------------------------
    cli_env = os.environ.copy()
    cli_env.pop("CLAUDECODE", None)  # strip nested-session guard
    if "CLAUDE_CODE_GIT_BASH_PATH" not in cli_env:
        _git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if _git_bash.exists():
            cli_env["CLAUDE_CODE_GIT_BASH_PATH"] = str(_git_bash)

    # --- Launch and poll -----------------------------------------------------
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(_REPO_ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=cli_env,
        )

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
            proc.kill()
            job.status = "failed"
            job.output = f"[timeout after {timeout}s]\n" + (proc.stdout.read() or "")
            log.warning("Job %s timed out after %ds", job.id, timeout)
            return

        stdout, stderr = proc.communicate()

        # --- Process exit code -----------------------------------------------
        if proc.returncode != 0:
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
            return

        # --- Parse JSON output -----------------------------------------------
        raw = stdout.strip()
        if not raw:
            job.status = "failed"
            stderr_info = stderr.strip()
            job.output = (
                "[cursor_cli] Cursor CLI exited 0 but produced no output.\n"
                "Executor is EXPERIMENTAL / SCAFFOLDED."
            )
            if stderr_info:
                job.output += f"\n\nstderr:\n{stderr_info}"
            log.warning("Job %s: empty stdout. stderr=%s", job.id, stderr_info[:200])
            return

        result_text = raw  # fallback: use raw output
        cost_usd = None
        duration_ms = None
        model_used = None
        try:
            envelope = json.loads(raw)
            if isinstance(envelope, dict):
                is_error = envelope.get("is_error", False)
                result_text = envelope.get("result", raw)
                cost_usd = envelope.get("total_cost_usd")
                duration_ms = envelope.get("duration_ms")
                mu = envelope.get("modelUsage")
                if isinstance(mu, dict) and mu:
                    model_used = next(iter(mu))
                if is_error:
                    job.status = "failed"
                    job.output = f"[cursor_cli] Agent returned error:\n{result_text}"
                    if stderr.strip():
                        job.output += f"\n\nstderr:\n{stderr.strip()}"
                    log.warning("Job %s: agent error: %s", job.id, result_text[:200])
                    return
        except (json.JSONDecodeError, KeyError, TypeError):
            log.debug("Job %s: non-JSON output, using raw stdout", job.id)

        # --- Success ---------------------------------------------------------
        job.output = result_text
        job.status = "done"

        meta_parts = []
        if model_used:
            meta_parts.append(f"model={model_used}")
        if duration_ms is not None:
            meta_parts.append(f"duration={duration_ms}ms")
        if cost_usd is not None:
            meta_parts.append(f"cost=${cost_usd:.4f}")
        if meta_parts:
            job.output += f"\n\n--- cursor metadata: {', '.join(meta_parts)} ---"

        log.info(
            "Job %s completed via Cursor headless CLI (rc=0, model=%s, cost=%s)",
            job.id, model_used, cost_usd,
        )

    except FileNotFoundError:
        job.status = "failed"
        job.output = (
            f"[cursor_cli] Could not launch Cursor CLI.\n"
            f"  CLI path: {_CURSOR_AGENT_CLI}\n"
            f"  Ensure 'node' is on PATH and Cursor is installed."
        )
        log.error("Cursor CLI FileNotFoundError for job %s", job.id)
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.output = f"[cursor_cli] unexpected error: {exc}"
        log.exception("Unexpected error in cursor handler for job %s", job.id)
