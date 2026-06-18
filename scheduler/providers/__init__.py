"""
Provider registry — single place to add/remove providers.
Worker talks to get_provider(), never imports providers directly.
"""

from scheduler.providers.base import BaseProvider
from scheduler.providers.anthropic_provider import AnthropicProvider
from scheduler.providers.elevenlabs_provider import ElevenLabsProvider
from scheduler.providers.gemini_provider import GeminiProvider
from scheduler.providers.meta_provider import MetaProvider
from scheduler.providers.ollama_provider import OllamaProvider
from scheduler.providers.openai_provider import OpenAIProvider
from scheduler.providers.sarvam import SarvamProvider


_REGISTRY: dict[str, BaseProvider] = {
    "anthropic":  AnthropicProvider(),
    "elevenlabs": ElevenLabsProvider(),
    "gemini": GeminiProvider(),
    "meta": MetaProvider(),
    "ollama": OllamaProvider(),
    "openai": OpenAIProvider(),
    "sarvam": SarvamProvider(),
}


def get_provider(name: str) -> BaseProvider | None:
    return _REGISTRY.get(name)


def list_providers() -> list[str]:
    return list(_REGISTRY.keys())