from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..nexus.spec import LLMSpec

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when the LLM provider fails."""
    def __init__(self, message: str, provider: str = "anthropic") -> None:
        super().__init__(message)
        self.provider = provider


class AnthropicProvider:
    """Thin async wrapper around the Anthropic Messages API."""

    def __init__(self, spec: "LLMSpec") -> None:
        self._spec = spec
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                from ..hub.secrets import SecretManager
                api_key = SecretManager().get(self._spec.api_key_env, default="")
                self._client = anthropic.AsyncAnthropic(api_key=api_key or None)
            except ImportError as e:
                raise LLMError(f"anthropic package not installed: {e}")
        return self._client

    async def complete(self, prompt: str, system: str = "", max_tokens: int = 1024) -> str:
        client = self._get_client()
        try:
            messages = [{"role": "user", "content": prompt}]
            kwargs = {
                "model": self._spec.model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            response = await client.messages.create(**kwargs)
            return response.content[0].text
        except Exception as e:
            raise LLMError(str(e), provider="anthropic") from e
