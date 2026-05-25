from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from .base import LLMError, LLMProvider
from .anthropic_provider import AnthropicProvider
from .ollama_provider import OllamaProvider
from .vllm_provider import VLLMProvider

if TYPE_CHECKING:
    from ..nexus.spec import LLMSpec

log = logging.getLogger(__name__)

# Anthropic Haiku pricing upper-bound estimate (per 1K tokens).
# Local providers (ollama, vllm) have zero cost — budget guard is skipped.
_COST_PER_1K_INPUT = 0.00025
_COST_PER_1K_OUTPUT = 0.00125

_PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
    "vllm": VLLMProvider,
}


class LLMBudgetError(Exception):
    """Raised when a call would exceed the daily USD budget."""


class LLMRouter:
    """Budget-guarded, provider-agnostic LLM wrapper.

    Tracks estimated USD spend per UTC calendar day. For local providers
    (ollama, vllm) with budget_per_day_usd == 0.0 the budget guard is
    disabled entirely — call volume is uncapped.

    Select the provider via LLMSpec.provider:
        ``anthropic`` — Anthropic Claude API
        ``ollama``    — Ollama local model server
        ``vllm``      — vLLM / any OpenAI-compatible endpoint
    """

    def __init__(self, provider: LLMProvider, spec: "LLMSpec") -> None:
        self._provider = provider
        self._spec = spec
        self._usd_today: float = 0.0
        self._day: date = datetime.now(timezone.utc).date()

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_spec(cls, spec: "LLMSpec") -> "LLMRouter":
        """Instantiate the correct provider from an LLMSpec."""
        factory = _PROVIDER_CLASSES.get(spec.provider)
        if factory is None:
            raise LLMError(
                f"unknown LLM provider {spec.provider!r}; "
                f"available: {sorted(_PROVIDER_CLASSES)}",
                provider=spec.provider,
            )
        return cls(factory(spec), spec)

    # ── budget tracking ───────────────────────────────────────────────────────

    def _refresh_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._usd_today = 0.0
            self._day = today

    def _estimate_cost(self, prompt: str, max_tokens: int) -> float:
        prompt_tokens = len(prompt) // 4  # ~4 chars per token
        return (prompt_tokens / 1000 * _COST_PER_1K_INPUT) + (max_tokens / 1000 * _COST_PER_1K_OUTPUT)

    @property
    def _budget_enforced(self) -> bool:
        return self._spec.budget_per_day_usd > 0.0

    # ── public interface ──────────────────────────────────────────────────────

    async def complete(self, prompt: str, system: str = "", max_tokens: int | None = None) -> str:
        self._refresh_day()
        mt = max_tokens or min(self._spec.max_tokens, 1024)

        if self._budget_enforced:
            cost = self._estimate_cost(prompt + system, mt)
            if self._usd_today + cost > self._spec.budget_per_day_usd:
                raise LLMBudgetError(
                    f"daily LLM budget ${self._spec.budget_per_day_usd:.2f} would be exceeded "
                    f"(spent ${self._usd_today:.4f}, estimated ${cost:.4f})"
                )
            result = await self._provider.complete(prompt, system=system, max_tokens=mt)
            self._usd_today += cost
            log.debug("llm call cost ~$%.5f (total today $%.4f)", cost, self._usd_today)
        else:
            result = await self._provider.complete(prompt, system=system, max_tokens=mt)

        return result

    @property
    def usd_today(self) -> float:
        self._refresh_day()
        return self._usd_today

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self._spec.budget_per_day_usd - self.usd_today)
