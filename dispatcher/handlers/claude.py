"""
Claude Code CLI handler.

Invokes the Claude Code CLI (installed via npm as `claude.cmd`) in
non-interactive --print mode with --dangerously-skip-permissions for
headless operation.  Effort is passed as an annotated prefix in the
prompt because the CLI has no native --effort flag.

Approval bridge: if the CLI emits a line matching known approval
patterns, the bridge posts to Slack and waits for the operator's reply.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("miru.dispatcher.handler.claude")

# Claude Code CLI — installed via `npm install -g @anthropic-ai/claude-code`
_CLAUDE_CLI = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"

APPROVAL_PATTERNS = [
    r"\(y/n\)",
    r"\[y/N\]",
    r"1\.\s+Allow",
    r"Allow once",
    r"Proceed\?",
    r"Do you want to",
    r"May I",
    r"Approve",
]
_APPROVAL_RE = re.compile("|".join(APPROVAL_PATTERNS), re.IGNORECASE)


def handler(job) -> None:
    """Real handler: invokes the Claude Code CLI via subprocess.

    On Windows, .cmd files require ``cmd /c`` as an interpreter in headless
    subprocesses (CREATE_NO_WINDOW).  Calling the .cmd directly via Popen
    produces no output and hangs silently until timeout.
    """
    import shutil

    # Effort → max-turns hint injected as a comment prefix.
    effort_hint = {"Quick": "brief", "Standard": "standard", "Deep": "thorough"}
    annotated_prompt = (
        f"[effort:{effort_hint.get(job.effort, 'standard')}] {job.prompt}"
    )

    timeout_seconds = {"Quick": 120, "Standard": 300, "Deep": 600}
    timeout = timeout_seconds.get(job.effort, 300)

    # Build cmd — primary path: APPDATA npm .cmd wrapped through cmd /c
    if _CLAUDE_CLI.exists():
        cmd = [
            "cmd", "/c", str(_CLAUDE_CLI),
            "--print",
            "--dangerously-skip-permissions",
            annotated_prompt,
        ]
    else:
        # Fallback: search PATH for any claude binary
        which_claude = shutil.which("claude")
        if which_claude:
            # Use cmd /c for .cmd files, direct exec for native binaries
            if which_claude.lower().endswith(".cmd"):
                cmd = [
                    "cmd", "/c", which_claude,
                    "--print",
                    "--dangerously-skip-permissions",
                    annotated_prompt,
                ]
            else:
                cmd = [
                    which_claude,
                    "--print",
                    "--dangerously-skip-permissions",
                    annotated_prompt,
                ]
            log.info("Claude CLI not at APPDATA path; using PATH: %s", which_claude)
        else:
            job.status = "failed"
            job.output = (
                f"[claude_cli] CLI not found at {_CLAUDE_CLI} or on PATH.\n"
                "Install with: npm install -g @anthropic-ai/claude-code"
            )
            log.error("Claude CLI not found at %s or on PATH", _CLAUDE_CLI)
            return

    log.info("Launching Claude CLI for job %s (timeout=%ds)", job.id, timeout)

    # Build env: inherit process env + ensure Git Bash is discoverable.
    cli_env = os.environ.copy()
    if "CLAUDE_CODE_GIT_BASH_PATH" not in cli_env:
        _git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if _git_bash.exists():
            cli_env["CLAUDE_CODE_GIT_BASH_PATH"] = str(_git_bash)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=cli_env,
        )

        # Store proc on job for stdin injection endpoint access.
        job.proc = proc

        # Close stdin immediately.  Claude CLI (Node.js) detects an open stdin
        # pipe and waits for more input — never producing output.  Sending EOF
        # tells it no interactive input is coming and it proceeds to run.
        # Approval-bridge writes handle BrokenPipeError via the write_exc handler.
        try:
            proc.stdin.close()
        except Exception:
            pass

        # Stream stdout lines into job.output_lines for SSE streaming.
        collected = []
        poll_interval = 0.5
        elapsed = 0.0

        # Non-blocking read via thread
        _stdout_lines: list[str] = []
        _read_done = False

        def _reader():
            nonlocal _read_done
            for raw_line in proc.stdout:
                _stdout_lines.append(raw_line.replace('\r', ''))
                # Check for approval prompt
                stripped = raw_line.strip()
                if _APPROVAL_RE.search(stripped):
                    try:
                        from task_dispatcher import ApprovalBridge
                        bridge = ApprovalBridge(timeout_seconds=600)
                        reply = bridge.ask(job, stripped)
                        if reply == "review":
                            reply = "n"
                        if reply and proc.stdin:
                            try:
                                proc.stdin.write(reply + "\n")
                                proc.stdin.flush()
                            except Exception as write_exc:
                                log.warning("Failed to write approval to stdin: %s", write_exc)
                    except Exception as bridge_exc:
                        log.warning("ApprovalBridge error: %s", bridge_exc)
            _read_done = True

        rt = threading.Thread(target=_reader, daemon=True)
        rt.start()

        while elapsed < timeout:
            if job.cancel_event.is_set():
                proc.kill()
                job.status = "cancelled"
                job.output = "[cancelled by operator]\n" + "".join(collected)
                log.info("Job %s cancelled mid-run; Claude CLI killed", job.id)
                return

            # Drain buffered lines
            while _stdout_lines:
                line = _stdout_lines.pop(0)
                collected.append(line)
                if hasattr(job, "output_lines"):
                    job.output_lines.append(line.rstrip("\n"))

            ret = proc.poll()
            if ret is not None:
                break
            time.sleep(poll_interval)
            elapsed += poll_interval
        else:
            proc.kill()
            job.status = "failed"
            job.output = f"[timeout after {timeout}s]\n" + "".join(collected)
            log.warning("Job %s timed out after %ds", job.id, timeout)
            return

        # Drain remaining lines after process exits
        rt.join(timeout=5)
        while _stdout_lines:
            line = _stdout_lines.pop(0)
            collected.append(line)
            if hasattr(job, "output_lines"):
                job.output_lines.append(line.rstrip("\n"))

        stderr_text = proc.stderr.read() or ""
        if proc.returncode == 0:
            job.output = "".join(collected).strip() or "[claude_cli] (no output)"
            job.status = "done"
            log.info("Job %s completed successfully (rc=0)", job.id)
        else:
            job.status = "failed"
            job.output = (
                f"[claude_cli] exit code {proc.returncode}\n"
                f"stdout: {''.join(collected).strip()}\n"
                f"stderr: {stderr_text.strip()}"
            )
            log.warning(
                "Job %s failed: rc=%d stderr=%s",
                job.id, proc.returncode, stderr_text[:200],
            )

    except FileNotFoundError:
        job.status = "failed"
        job.output = (
            f"[claude_cli] CLI executable not found. "
            f"Expected: {_CLAUDE_CLI} (or claude on PATH)"
        )
        log.error("Claude CLI FileNotFoundError for job %s", job.id)
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.output = f"[claude_cli] unexpected error: {exc}"
        log.exception("Unexpected error in claude handler for job %s", job.id)
