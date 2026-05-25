from __future__ import annotations

from abc import ABC, abstractmethod


class LLMError(Exception):
    """Raised when an LLM provider call fails."""
    def __init__(self, message: str, provider: str = "unknown") -> None:
        super().__init__(message)
        self.provider = provider


class LLMProvider(ABC):
    """Abstract base for all LLM provider implementations."""

    @abstractmethod
    async def complete(self, prompt: str, system: str = "", max_tokens: int = 1024) -> str:
        """Send a completion request and return the response text."""
