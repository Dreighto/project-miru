"""
Ollama local handler.

Runs a prompt against a locally-served Ollama model via its HTTP API.
Model selection is effort-driven: Quick/Standard/Deep map to different
Ollama models configured via OLLAMA_EFFORT_MODELS (env) or the defaults
below. Requires Ollama to be running at OLLAMA_BASE_URL.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

log = logging.getLogger("miru.dispatcher.handler.ollama")

# Default effort → Ollama model mapping. Override via environment if needed.
_DEFAULT_EFFORT_MODELS: dict[str, str] = {
    "Quick":    "gemma3:latest",
    "Standard": "qwen3.5:latest",
    "Deep":     "gemma4:e4b",
}


def handler(job) -> None:
    """Local executor: runs prompt against an Ollama model via HTTP API."""
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    effort_models = _DEFAULT_EFFORT_MODELS

    model_name = effort_models.get(job.effort)
    if not model_name:
        job.status = "failed"
        job.output = f"[ollama] No model mapped for effort '{job.effort}'"
        return

    # Preflight: verify the requested model is available locally.
    try:
        tags_req = urllib.request.Request(f"{base_url}/api/tags")
        with urllib.request.urlopen(tags_req, timeout=10) as resp:
            tags_data = json.loads(resp.read())
        available = {m.get("name", "") for m in tags_data.get("models", [])}
        if model_name not in available:
            job.status = "failed"
            job.output = (
                f"[ollama] Model '{model_name}' not found locally.\n"
                f"Available: {', '.join(sorted(available)) or '(none)'}\n"
                f"Run: ollama pull {model_name}"
            )
            return
    except urllib.error.URLError as exc:
        job.status = "failed"
        job.output = f"[ollama] Cannot reach Ollama at {base_url}: {exc.reason}"
        return
    except Exception as exc:
        job.status = "failed"
        job.output = f"[ollama] Preflight check failed: {exc}"
        return

    if job.cancel_event.is_set():
        job.status = "cancelled"
        job.output = "[cancelled before Ollama request]"
        return

    # Execute: POST /api/chat (non-streaming).
    timeout_map = {"Quick": 60, "Standard": 120, "Deep": 300}
    timeout = timeout_map.get(job.effort, 120)

    payload = json.dumps({
        "model": model_name,
        "messages": [{"role": "user", "content": job.prompt}],
        "stream": False,
    }).encode("utf-8")

    log.info("Ollama job %s: model=%s timeout=%ds", job.id, model_name, timeout)

    try:
        chat_req = urllib.request.Request(
            f"{base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(chat_req, timeout=timeout) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        job.status = "failed"
        job.output = f"[ollama] HTTP {exc.code}: {body}"
        return
    except urllib.error.URLError as exc:
        job.status = "failed"
        job.output = f"[ollama] Connection error: {exc.reason}"
        return
    except (TimeoutError, OSError) as exc:
        job.status = "failed"
        job.output = f"[ollama] Request timed out after {timeout}s: {exc}"
        return
    except Exception as exc:
        job.status = "failed"
        job.output = f"[ollama] Unexpected error: {exc}"
        log.exception("Unexpected error in ollama handler for job %s", job.id)
        return

    # Parse response.
    content = result.get("message", {}).get("content", "").strip()
    if not content:
        job.status = "failed"
        job.output = "[ollama] Empty response from model"
        return

    job.output = content
    job.status = "done"
    job.estimated_cost = 0.0

    if result.get("prompt_eval_count") is not None:
        job.input_tokens = result["prompt_eval_count"]
    if result.get("eval_count") is not None:
        job.output_tokens = result["eval_count"]

    total_ns = result.get("total_duration", 0)
    if total_ns:
        log.info(
            "Job %s done via Ollama (%s): %.1fs, %s in / %s out tokens",
            job.id, model_name, total_ns / 1e9,
            job.input_tokens or "?", job.output_tokens or "?",
        )
