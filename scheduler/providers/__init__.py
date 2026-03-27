"""
Provider registry — single place to add/remove providers.
Worker talks to get_provider(), never imports providers directly.
"""

from scheduler.providers.base import BaseProvider
from scheduler.providers.sarvam import SarvamProvider
from scheduler.providers.openai_provider import OpenAIProvider
from scheduler.providers.anthropic_provider import AnthropicProvider
from scheduler.providers.elevenlabs_provider import ElevenLabsProvider
from scheduler.providers.ollama_provider import OllamaProvider


_REGISTRY: dict[str, BaseProvider] = {
    "sarvam":     SarvamProvider(),
    "openai":     OpenAIProvider(),
    "anthropic":  AnthropicProvider(),
    "elevenlabs": ElevenLabsProvider(),
    "ollama":     OllamaProvider(),
}


def get_provider(name: str) -> BaseProvider | None:
    return _REGISTRY.get(name)


def list_providers() -> list[str]:
    return list(_REGISTRY.keys())