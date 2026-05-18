"""Ollama HTTP client for the shadow loop's primary + validator models.

Two NEW instances spawned per-loop, isolated from the routing Hermes (which
also runs qwen2.5:7b but on the dispatch_listener's spawn path). The
isolation is by-convention: this client uses its OWN model_id strings and
its OWN HTTP requests; Ollama itself routes by model name and is stateless
between requests.

PRO-908 PR-A.
"""

from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Raised when the Ollama server returns an error or unparseable output."""


class OllamaClient:
    def __init__(self, base_url: str, model: str, system_prompt: str, timeout_s: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.system_prompt = system_prompt
        self.timeout_s = timeout_s

    def ask_json(self, user_prompt: str) -> dict:
        """Send a user prompt; return the parsed JSON response.

        Ollama's `format=json` forces JSON-formatted output. If the model
        returns malformed JSON anyway (uncommon but possible), raise
        OllamaError so the loop can mark the row inconclusive.
        """
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
        }
        try:
            resp = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout_s)
        except requests.RequestException as exc:
            raise OllamaError(f"ollama request failed for {self.model}: {exc}") from exc

        if resp.status_code != 200:
            raise OllamaError(f"ollama {self.model} returned {resp.status_code}: {resp.text[:400]}")

        body = resp.json()
        content = body.get("message", {}).get("content", "")
        if not content:
            raise OllamaError(f"ollama {self.model} returned empty content")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaError(
                f"ollama {self.model} returned non-JSON content: {content[:400]}"
            ) from exc


PRIMARY_SYSTEM_PROMPT = """You are learning the One Piece TCG OP01 set as a knowledge model.

Your job: answer questions about specific cards as accurately as you can from what
you have learned. You DO NOT have tool access — answer only from your own learned
knowledge of the OP01 set.

When asked for a card's fields, reply ONLY with a JSON object whose keys match the
requested field names. If you genuinely don't know a field, set its value to null.
Do not make up values.
"""

VALIDATOR_SYSTEM_PROMPT = """You are the validator for a learning system on the One Piece TCG OP01 set.

You learn OP01 in parallel with a primary model. When asked a question, you answer
from your own learned knowledge. You will later be given the primary's answer to
compare against using tools (catalog DB, Bandai data, TCGPlayer pricing); for the
first pass, just answer the question accurately.

When asked for a card's fields, reply ONLY with a JSON object whose keys match the
requested field names. If you genuinely don't know a field, set its value to null.
Do not make up values.
"""


def primary(base_url: str, model: str, timeout_s: int) -> OllamaClient:
    return OllamaClient(base_url, model, PRIMARY_SYSTEM_PROMPT, timeout_s)


def validator(base_url: str, model: str, timeout_s: int) -> OllamaClient:
    return OllamaClient(base_url, model, VALIDATOR_SYSTEM_PROMPT, timeout_s)
