"""
Provider registry — add new providers here.
Worker imports this, never imports providers directly.
"""

from scheduler.providers.base import BaseProvider
from scheduler.providers.sarvam import SarvamProvider
# from scheduler.providers.openai import OpenAIProvider       # TODO
# from scheduler.providers.anthropic import AnthropicProvider # TODO
# from scheduler.providers.elevenlabs import ElevenLabsProvider # TODO
# from scheduler.providers.ollama import OllamaProvider       # TODO


_REGISTRY: dict[str, BaseProvider] = {
    "sarvam":    SarvamProvider(),
    # "openai":    OpenAIProvider(),
    # "anthropic": AnthropicProvider(),
    # "elevenlabs": ElevenLabsProvider(),
    # "ollama":    OllamaProvider(),
}


def get_provider(name: str) -> BaseProvider | None:
    return _REGISTRY.get(name)


def list_providers() -> list[str]:
    return list(_REGISTRY.keys())