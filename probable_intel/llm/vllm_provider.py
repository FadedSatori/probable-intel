from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import LLMError, LLMProvider

if TYPE_CHECKING:
    from ..nexus.spec import LLMSpec

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://localhost:8000"


class VLLMProvider(LLMProvider):
    """vLLM provider via its OpenAI-compatible REST API.

    Works with any OpenAI-compatible server: vLLM, LM Studio, llama.cpp server,
    Groq, Together AI, Fireworks, Anyscale, etc.

    Example NEXUS config::

        llm:
          provider: "vllm"
          model: "mistralai/Mistral-7B-Instruct-v0.3"
          base_url: "http://localhost:8000"
          api_key_env: "VLLM_API_KEY"   # optional; leave empty for no auth
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
            raise LLMError(f"httpx not installed: {e}", provider="vllm") from e

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self._spec.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        headers = {"Content-Type": "application/json"}
        if self._spec.api_key_env:
            from ..hub.secrets import SecretManager
            key = SecretManager().get(self._spec.api_key_env, default="")
            if key:
                headers["Authorization"] = f"Bearer {key}"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json=body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            raise LLMError(str(e), provider="vllm") from e
