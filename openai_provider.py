"""
OpenAI provider.

Actions supported:
  llm_inference    → /chat/completions  (gpt-4o-mini default)
  embedding        → /embeddings        (text-embedding-3-small)
  tts              → /audio/speech      (tts-1)
  stt              → /audio/transcriptions (whisper-1)
  image_generation → /images/generations (dall-e-3)

Set env var: OPENAI_API_KEY
"""

import base64
import os
from typing import Any

import httpx

from scheduler.providers.base import BaseProvider, ProviderResult

OPENAI_BASE_URL = "https://api.openai.com/v1"

# Cost per unit (USD) — kept in sync with price_map.json
_COSTS = {
    "llm_input_token":   0.000000150,   # gpt-4o-mini input
    "llm_output_token":  0.000000600,   # gpt-4o-mini output
    "embedding_token":   0.0000000200,  # text-embedding-3-small
    "tts_char":          0.000015,      # tts-1
    "stt_second":        0.0001,        # whisper-1
    "image_standard":    0.04,          # dall-e-3 standard 1024x1024
    "image_hd":          0.08,          # dall-e-3 HD
}


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=OPENAI_BASE_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type":  "application/json",
                },
                timeout=120.0,
            )
        return self._client

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="OPENAI_API_KEY not set"
            )

        handler = {
            "llm_inference":    self._llm,
            "embedding":        self._embedding,
            "tts":              self._tts,
            "stt":              self._stt,
            "image_generation": self._image,
        }.get(action)

        if not handler:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"OpenAI provider does not support action: {action}"
            )

        try:
            return await handler(input_payload)
        except httpx.HTTPStatusError as e:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"OpenAI HTTP {e.response.status_code}: {e.response.text}"
            )
        except httpx.TimeoutException:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="OpenAI API timeout"
            )

    # ── LLM inference ─────────────────────────────────────────────────────────

    async def _llm(self, payload: Any) -> ProviderResult:
        """
        payload: str  (simple prompt)
             OR  {
                    "messages": [...],     OpenAI chat format
                    "model": str,          optional, default gpt-4o-mini
                    "max_tokens": int,     optional, default 1024
                    "system": str          optional system prompt
                 }
        """
        if isinstance(payload, str):
            messages = [{"role": "user", "content": payload}]
            model = "gpt-4o-mini"
            max_tokens = 1024
        else:
            messages = payload.get("messages", [{"role": "user", "content": str(payload)}])
            model = payload.get("model", "gpt-4o-mini")
            max_tokens = payload.get("max_tokens", 1024)
            if "system" in payload:
                messages = [{"role": "system", "content": payload["system"]}] + messages

        body = {"model": model, "messages": messages, "max_tokens": max_tokens}

        resp = await self._get_client().post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        usage = data.get("usage", {})
        input_tokens  = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = (input_tokens * _COSTS["llm_input_token"] +
                output_tokens * _COSTS["llm_output_token"])

        content = data["choices"][0]["message"]["content"]

        return ProviderResult(
            success=True,
            output=content,
            actual_cost_usd=cost,
            tokens_used=input_tokens + output_tokens,
        )

    # ── Embeddings ────────────────────────────────────────────────────────────

    async def _embedding(self, payload: Any) -> ProviderResult:
        """
        payload: str  (text to embed)
             OR  { "input": str, "model": str }
        """
        text  = payload if isinstance(payload, str) else payload.get("input", str(payload))
        model = "text-embedding-3-small" if isinstance(payload, str) else payload.get("model", "text-embedding-3-small")

        body = {"input": text, "model": model}

        resp = await self._get_client().post("/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()

        tokens = data.get("usage", {}).get("total_tokens", len(text) // 4)
        cost   = tokens * _COSTS["embedding_token"]

        return ProviderResult(
            success=True,
            output=data["data"][0]["embedding"],
            actual_cost_usd=cost,
            tokens_used=tokens,
        )

    # ── TTS ───────────────────────────────────────────────────────────────────

    async def _tts(self, payload: Any) -> ProviderResult:
        """
        payload: str  (text to speak)
             OR  {
                    "text": str,
                    "voice": str,     alloy|echo|fable|onyx|nova|shimmer  default nova
                    "model": str,     tts-1 | tts-1-hd  default tts-1
                    "format": str,    mp3|opus|aac|flac  default mp3
                 }
        """
        text   = payload if isinstance(payload, str) else payload.get("text", str(payload))
        voice  = "nova" if isinstance(payload, str) else payload.get("voice", "nova")
        model  = "tts-1" if isinstance(payload, str) else payload.get("model", "tts-1")
        fmt    = "mp3"  if isinstance(payload, str) else payload.get("format", "mp3")

        body = {"model": model, "input": text, "voice": voice, "response_format": fmt}

        # TTS returns raw audio bytes
        client = self._get_client()
        req = client.build_request("POST", "/audio/speech", json=body)
        req.headers["Content-Type"] = "application/json"
        resp = await client.send(req)
        resp.raise_for_status()

        audio_b64 = base64.b64encode(resp.content).decode()
        cost = len(text) * _COSTS["tts_char"]

        return ProviderResult(
            success=True,
            output=audio_b64,
            actual_cost_usd=cost,
        )

    # ── STT ───────────────────────────────────────────────────────────────────

    async def _stt(self, payload: dict) -> ProviderResult:
        """
        payload: {
            "audio_b64": str,          base64 encoded audio
            "audio_format": str,       mp3|mp4|wav|webm etc. default mp3
            "language": str,           optional ISO-639-1 e.g. "hi"
            "duration_seconds": float  for cost calculation
        }
        """
        audio_b64  = payload.get("audio_b64", "")
        audio_bytes = base64.b64decode(audio_b64)
        fmt        = payload.get("audio_format", "mp3")
        duration   = payload.get("duration_seconds", 5.0)

        files = {"file": (f"audio.{fmt}", audio_bytes, f"audio/{fmt}")}
        form  = {"model": "whisper-1"}
        if "language" in payload:
            form["language"] = payload["language"]

        # STT uses multipart — need a client without Content-Type: application/json
        async with httpx.AsyncClient(
            base_url=OPENAI_BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=120.0,
        ) as client:
            resp = await client.post("/audio/transcriptions", files=files, data=form)
            resp.raise_for_status()

        result = resp.json()

        return ProviderResult(
            success=True,
            output={"transcript": result.get("text", "")},
            actual_cost_usd=duration * _COSTS["stt_second"],
            duration_seconds=duration,
        )

    # ── Image generation ──────────────────────────────────────────────────────

    async def _image(self, payload: Any) -> ProviderResult:
        """
        payload: str  (prompt)
             OR  {
                    "prompt": str,
                    "model": str,         dall-e-3 | dall-e-2  default dall-e-3
                    "size": str,          1024x1024 | 1792x1024 | 1024x1792
                    "quality": str,       standard | hd  default standard
                    "n": int              default 1
                 }
        """
        prompt  = payload if isinstance(payload, str) else payload.get("prompt", str(payload))
        model   = "dall-e-3" if isinstance(payload, str) else payload.get("model", "dall-e-3")
        size    = "1024x1024" if isinstance(payload, str) else payload.get("size", "1024x1024")
        quality = "standard" if isinstance(payload, str) else payload.get("quality", "standard")
        n       = 1 if isinstance(payload, str) else payload.get("n", 1)

        body = {"model": model, "prompt": prompt, "n": n, "size": size, "quality": quality}

        resp = await self._get_client().post("/images/generations", json=body)
        resp.raise_for_status()
        data = resp.json()

        cost_per = _COSTS["image_hd"] if quality == "hd" else _COSTS["image_standard"]
        cost = cost_per * n

        # Return URLs (revised_prompt + url per image)
        images = [{"url": img["url"], "revised_prompt": img.get("revised_prompt")}
                  for img in data["data"]]

        return ProviderResult(
            success=True,
            output=images,
            actual_cost_usd=cost,
        )