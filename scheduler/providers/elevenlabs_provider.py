"""
ElevenLabs provider — premium TTS.

Actions supported:
  tts  →  /v1/text-to-speech/{voice_id}

ElevenLabs is the highest-quality TTS option — use it when
audio naturalness matters more than cost.

Cost: ~$0.30 per 1000 characters (Creator plan)
      Actual cost settled from X-Character-Cost response header.

Popular voice IDs (from ElevenLabs voice library):
  "21m00Tcm4TlvDq8ikWAM"  →  Rachel   (neutral, calm)
  "AZnzlk1XvdvUeBnXmlld"  →  Domi     (strong, expressive)
  "EXAVITQu4vr4xnSDxMaL"  →  Bella    (soft, gentle)
  "ErXwobaYiN019PkySvjV"  →  Antoni   (well-rounded)
  "MF3mGyEYCl7XYWbV9V6O"  →  Elli     (emotional, young)

Set env vars:
  ELEVENLABS_API_KEY
  ELEVENLABS_DEFAULT_VOICE  (optional, default: Rachel)

Docs: https://elevenlabs.io/docs/api-reference
"""

import base64
import os
from typing import Any

import httpx

from scheduler.providers.base import BaseProvider, ProviderResult

ELEVENLABS_BASE_URL   = "https://api.elevenlabs.io"
DEFAULT_VOICE_ID      = os.getenv("ELEVENLABS_DEFAULT_VOICE", "21m00Tcm4TlvDq8ikWAM")  # Rachel
DEFAULT_MODEL_ID      = "eleven_multilingual_v2"

# Cost per character — ElevenLabs bills by character consumed
COST_PER_CHAR = 0.00030   # $0.30 per 1000 chars (Creator plan estimate)


class ElevenLabsProvider(BaseProvider):
    name = "elevenlabs"

    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=ELEVENLABS_BASE_URL,
                headers={
                    "xi-api-key":   self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=60.0,
            )
        return self._client

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="ELEVENLABS_API_KEY not set"
            )

        if action != "tts":
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"ElevenLabs only supports tts, got: {action}"
            )

        try:
            return await self._tts(input_payload)
        except httpx.HTTPStatusError as e:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"ElevenLabs HTTP {e.response.status_code}: {e.response.text}"
            )
        except httpx.TimeoutException:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="ElevenLabs API timeout"
            )

    async def _tts(self, payload: Any) -> ProviderResult:
        """
        payload: str  (text to speak — uses default voice)
             OR  {
                    "text": str,
                    "voice_id": str,        optional, overrides default voice
                    "model_id": str,        optional, default eleven_multilingual_v2
                    "stability": float,     0.0–1.0, default 0.5
                    "similarity_boost": float  0.0–1.0, default 0.75
                    "style": float,         0.0–1.0, default 0.0 (expressiveness)
                    "output_format": str    mp3_44100_128 | pcm_24000 etc, default mp3_44100_128
                 }
        """
        if isinstance(payload, str):
            text            = payload
            voice_id        = DEFAULT_VOICE_ID
            model_id        = DEFAULT_MODEL_ID
            voice_settings  = {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0}
            output_format   = "mp3_44100_128"
        else:
            text            = payload.get("text", str(payload))
            voice_id        = payload.get("voice_id", DEFAULT_VOICE_ID)
            model_id        = payload.get("model_id", DEFAULT_MODEL_ID)
            output_format   = payload.get("output_format", "mp3_44100_128")
            voice_settings  = {
                "stability":       payload.get("stability", 0.5),
                "similarity_boost": payload.get("similarity_boost", 0.75),
                "style":           payload.get("style", 0.0),
            }

        body = {
            "text":           text,
            "model_id":       model_id,
            "voice_settings": voice_settings,
        }

        # ElevenLabs returns raw audio bytes
        resp = await self._get_client().post(
            f"/v1/text-to-speech/{voice_id}",
            json=body,
            params={"output_format": output_format},
        )
        resp.raise_for_status()

        audio_b64 = base64.b64encode(resp.content).decode()

        # ElevenLabs returns character count in response header when available
        chars_used = int(resp.headers.get("x-character-count", len(text)))
        actual_cost = chars_used * COST_PER_CHAR

        return ProviderResult(
            success=True,
            output=audio_b64,
            actual_cost_usd=actual_cost,
        )