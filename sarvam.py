"""
Sarvam AI provider.
Docs: https://docs.sarvam.ai

Set env var: SARVAM_API_KEY
"""

import os
import asyncio
import httpx
from typing import Any

from scheduler.providers.base import BaseProvider, ProviderResult


SARVAM_BASE_URL = "https://api.sarvam.ai"


class SarvamProvider(BaseProvider):
    name = "sarvam"

    def __init__(self):
        self.api_key = os.getenv("SARVAM_API_KEY", "")
        self.client = httpx.AsyncClient(
            base_url=SARVAM_BASE_URL,
            headers={"API-Subscription-Key": self.api_key},
            timeout=60.0,
        )

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        handler = {
            "tts":             self._tts,
            "stt":             self._stt,
            "translation":     self._translate,
            "transliteration": self._transliterate,
        }.get(action)

        if not handler:
            return ProviderResult(success=False, output=None, actual_cost_usd=0,
                                  error=f"Sarvam does not support action: {action}")
        return await handler(input_payload)

    async def _tts(self, payload: dict) -> ProviderResult:
        """
        payload: { "text": str, "language_code": str, "speaker": str }
        """
        # TODO: wire up actual Sarvam TTS endpoint
        # POST /text-to-speech
        await asyncio.sleep(0.1)   # placeholder
        chars = len(payload.get("text", ""))
        cost = chars * 0.000003
        return ProviderResult(success=True, output=b"<audio_bytes>",
                              actual_cost_usd=cost)

    async def _stt(self, payload: dict) -> ProviderResult:
        """
        payload: { "audio_b64": str, "language_code": str, "duration_seconds": float }
        """
        # TODO: POST /speech-to-text
        await asyncio.sleep(0.1)
        duration = payload.get("duration_seconds", 5)
        cost = duration * 0.000083
        return ProviderResult(success=True, output="<transcript>",
                              actual_cost_usd=cost, duration_seconds=duration)

    async def _translate(self, payload: dict) -> ProviderResult:
        # TODO: POST /translate
        await asyncio.sleep(0.1)
        chars = len(payload.get("input", ""))
        return ProviderResult(success=True, output="<translated>",
                              actual_cost_usd=chars * 0.000002)

    async def _transliterate(self, payload: dict) -> ProviderResult:
        # TODO: POST /transliterate
        await asyncio.sleep(0.1)
        chars = len(payload.get("input", ""))
        return ProviderResult(success=True, output="<transliterated>",
                              actual_cost_usd=chars * 0.000001)