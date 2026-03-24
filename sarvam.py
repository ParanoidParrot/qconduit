"""
Sarvam AI provider — real API calls.

Models:
  TTS: bulbul:v2  (bulbul:v1 deprecated April 30 2025)
  STT: saaras:v3  (saarika being deprecated, migrate to saaras)
  Translate: mayura:v1
  Transliterate: /transliterate endpoint

Billing:
  Speech (STT/TTS) → per second of audio
  Text (translate, transliterate) → per token

Set env var: SARVAM_API_KEY
"""

import base64
import os
from typing import Any

import httpx

from scheduler.providers.base import BaseProvider, ProviderResult

SARVAM_BASE_URL = "https://api.sarvam.ai"

SUPPORTED_LANGUAGES = [
    "hi-IN", "bn-IN", "kn-IN", "ml-IN", "mr-IN",
    "od-IN", "pa-IN", "ta-IN", "te-IN", "gu-IN",
    "en-IN",
]


class SarvamProvider(BaseProvider):
    name = "sarvam"

    def __init__(self):
        self.api_key = os.getenv("SARVAM_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Lazy init — avoids issues at import time before env is loaded."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=SARVAM_BASE_URL,
                headers={"api-subscription-key": self.api_key},
                timeout=60.0,
            )
        return self._client

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="SARVAM_API_KEY not set"
            )

        handler = {
            "tts":             self._tts,
            "stt":             self._stt,
            "translation":     self._translate,
            "transliteration": self._transliterate,
        }.get(action)

        if not handler:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Sarvam does not support action: {action}"
            )

        try:
            return await handler(input_payload)
        except httpx.HTTPStatusError as e:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Sarvam HTTP {e.response.status_code}: {e.response.text}"
            )
        except httpx.TimeoutException:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="Sarvam API timeout"
            )

    # ── TTS ───────────────────────────────────────────────────────────────────

    async def _tts(self, payload: dict) -> ProviderResult:
        """
        payload: {
            "text": str,
            "language_code": str,         e.g. "hi-IN"
            "speaker": str,               optional, default "meera"
            "pace": float,                optional, default 1.0
            "pitch": float,               optional, default 0.0
            "loudness": float,            optional, default 1.0
            "speech_sample_rate": int,    optional, default 22050
            "enable_preprocessing": bool  optional, default False
        }
        """
        text = payload.get("text", "")
        body = {
            "inputs":               [text],
            "target_language_code": payload.get("language_code", "hi-IN"),
            "speaker":              payload.get("speaker", "meera"),
            "pace":                 payload.get("pace", 1.0),
            "pitch":                payload.get("pitch", 0.0),
            "loudness":             payload.get("loudness", 1.0),
            "speech_sample_rate":   payload.get("speech_sample_rate", 22050),
            "enable_preprocessing": payload.get("enable_preprocessing", False),
            "model":                "bulbul:v2",
        }

        resp = await self._get_client().post("/text-to-speech", json=body)
        resp.raise_for_status()
        data = resp.json()

        # Response: { "audios": [ "<base64_wav>" ] }
        audio_b64 = data["audios"][0]
        cost = len(text) * 0.000003

        return ProviderResult(
            success=True,
            output=audio_b64,        # b64 string — JSON-serialisable
            actual_cost_usd=cost,
        )

    # ── STT ───────────────────────────────────────────────────────────────────

    async def _stt(self, payload: dict) -> ProviderResult:
        """
        payload: {
            "audio_b64": str,           base64 encoded audio
            "audio_format": str,        "wav" | "mp3" | "ogg" etc. default "wav"
            "language_code": str,       optional — omit for auto-detect
            "duration_seconds": float   for cost calculation
        }
        """
        audio_b64 = payload.get("audio_b64", "")
        audio_bytes = base64.b64decode(audio_b64)
        audio_format = payload.get("audio_format", "wav")
        duration = payload.get("duration_seconds", 5.0)

        files = {
            "file": (f"audio.{audio_format}", audio_bytes, f"audio/{audio_format}"),
        }
        form_data = {"model": "saaras:v3"}
        if "language_code" in payload:
            form_data["language_code"] = payload["language_code"]

        resp = await self._get_client().post(
            "/speech-to-text",
            files=files,
            data=form_data,
        )
        resp.raise_for_status()
        result = resp.json()

        # Response: { "transcript": str, "language_code": str, ... }
        return ProviderResult(
            success=True,
            output={
                "transcript":    result.get("transcript", ""),
                "language_code": result.get("language_code"),
            },
            actual_cost_usd=duration * 0.000083,
            duration_seconds=duration,
        )

    # ── Translation ───────────────────────────────────────────────────────────

    async def _translate(self, payload: dict) -> ProviderResult:
        """
        payload: {
            "input": str,
            "source_language_code": str   optional — omit for auto-detect
            "target_language_code": str   e.g. "hi-IN"
        }
        """
        text = payload.get("input", "") if isinstance(payload, dict) else str(payload)
        target = payload.get("target_language_code", "hi-IN") if isinstance(payload, dict) else "hi-IN"

        body = {"input": text, "target_language_code": target, "model": "mayura:v1"}
        if isinstance(payload, dict) and "source_language_code" in payload:
            body["source_language_code"] = payload["source_language_code"]

        resp = await self._get_client().post("/translate", json=body)
        resp.raise_for_status()
        result = resp.json()

        tokens = len(text) / 4
        return ProviderResult(
            success=True,
            output={"translated_text": result.get("translated_text", "")},
            actual_cost_usd=tokens * 0.000002,
            tokens_used=int(tokens),
        )

    # ── Transliteration ───────────────────────────────────────────────────────

    async def _transliterate(self, payload: dict) -> ProviderResult:
        """
        payload: {
            "input": str,
            "source_language_code": str   e.g. "en-IN"
            "target_language_code": str   e.g. "hi-IN"
        }
        """
        text = payload.get("input", "") if isinstance(payload, dict) else str(payload)

        body = {
            "input":                text,
            "source_language_code": payload.get("source_language_code", "en-IN") if isinstance(payload, dict) else "en-IN",
            "target_language_code": payload.get("target_language_code", "hi-IN") if isinstance(payload, dict) else "hi-IN",
        }

        resp = await self._get_client().post("/transliterate", json=body)
        resp.raise_for_status()
        result = resp.json()

        return ProviderResult(
            success=True,
            output={"transliterated_text": result.get("transliterated_text", "")},
            actual_cost_usd=len(text) * 0.000001,
        )