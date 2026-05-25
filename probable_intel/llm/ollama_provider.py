from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import LLMError, LLMProvider

if TYPE_CHECKING:
    from ..nexus.spec import LLMSpec

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaProvider(LLMProvider):
    """Ollama local model provider via the Ollama REST API.

    Requires Ollama running locally (https://ollama.ai).
    No API key needed. Configure base_url if Ollama runs on a non-default address.

    Example NEXUS config::

        llm:
          provider: "ollama"
          model: "llama3.2"
          base_url: "http://localhost:11434"
          max_tokens: 4096
          budget_per_day_usd: 0.0
    """

    def __init__(self, spec: "LLMSpec") -> None:
        self._spec = spec
        self._base_url = (spec.base_url or _DEFAULT_BASE_URL).rstrip("/")

    async def complete(self, prompt: str, system: str = "", max_tokens: int = 1024) -> str:
        try:
            import httpx
        except ImportError as e:
            raise LLMError(f"httpx not installed: {e}", provider="ollama") from e

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self._spec.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(f"{self._base_url}/api/chat", json=body)
                resp.raise_for_status()
                data = resp.json()
                return data["message"]["content"]
        except Exception as e:
            raise LLMError(str(e), provider="ollama") from e
