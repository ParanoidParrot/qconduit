"""
Base provider interface. Every provider implements execute().
Return shape is always ProviderResult — keeps worker logic clean.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ProviderResult:
    success: bool
    output: Any              # TTS → bytes, LLM → str, etc.
    actual_cost_usd: float
    tokens_used: Optional[int] = None
    duration_seconds: Optional[float] = None
    error: Optional[str] = None


class BaseProvider(ABC):
    name: str

    @abstractmethod
    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        ...