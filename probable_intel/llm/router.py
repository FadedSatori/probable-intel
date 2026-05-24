from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from .anthropic_provider import AnthropicProvider, LLMError

if TYPE_CHECKING:
    from ..nexus.spec import LLMSpec

log = logging.getLogger(__name__)

# Haiku pricing upper-bound estimate (per 1K tokens)
_COST_PER_1K_INPUT = 0.00025
_COST_PER_1K_OUTPUT = 0.00125


class LLMBudgetError(Exception):
    """Raised when the daily LLM budget would be exceeded."""


class LLMRouter:
    """Budget-guarded wrapper around an LLM provider.

    Tracks estimated USD spend per calendar day UTC. Rejects calls that
    would push spending past ``budget_per_day_usd``.
    """

    def __init__(self, provider: AnthropicProvider, spec: "LLMSpec") -> None:
        self._provider = provider
        self._spec = spec
        self._usd_today: float = 0.0
        self._day: date = datetime.now(timezone.utc).date()

    def _refresh_day(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._usd_today = 0.0
            self._day = today

    def _estimate_cost(self, prompt: str, max_tokens: int) -> float:
        prompt_tokens = len(prompt) // 4  # ~4 chars per token
        return (prompt_tokens / 1000 * _COST_PER_1K_INPUT) + (max_tokens / 1000 * _COST_PER_1K_OUTPUT)

    async def complete(self, prompt: str, system: str = "", max_tokens: int | None = None) -> str:
        self._refresh_day()
        mt = max_tokens or min(self._spec.max_tokens, 1024)
        cost = self._estimate_cost(prompt + system, mt)

        if self._usd_today + cost > self._spec.budget_per_day_usd:
            raise LLMBudgetError(
                f"daily LLM budget ${self._spec.budget_per_day_usd:.2f} would be exceeded "
                f"(spent ${self._usd_today:.4f}, estimated ${cost:.4f})"
            )

        result = await self._provider.complete(prompt, system=system, max_tokens=mt)
        self._usd_today += cost
        log.debug("llm call cost ~$%.5f (total today $%.4f)", cost, self._usd_today)
        return result

    @property
    def usd_today(self) -> float:
        self._refresh_day()
        return self._usd_today

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self._spec.budget_per_day_usd - self.usd_today)

    @classmethod
    def from_spec(cls, spec: "LLMSpec") -> "LLMRouter":
        return cls(AnthropicProvider(spec), spec)
