"""
Google Gemini provider — via Google AI Studio REST API.

Actions supported:
  llm_inference    →  /v1beta/models/{model}:generateContent
  embedding        →  /v1beta/models/text-embedding-004:embedContent
  image_generation →  /v1beta/models/imagen-3.0-generate-002:predict

Models:
  gemini-2.0-flash       — fast, cheap, good for most tasks
  gemini-2.0-flash-lite  — cheapest option
  gemini-2.5-pro         — most capable (higher cost)

Cost per token (USD):
  gemini-2.0-flash:      input $0.10/M,  output $0.40/M  (<=128k ctx)
  gemini-2.0-flash-lite: input $0.075/M, output $0.30/M
  gemini-2.5-pro:        input $1.25/M,  output $10.00/M (<=200k ctx)
  imagen-3.0:            $0.03 per image

Set env var: GOOGLE_API_KEY
Docs: https://ai.google.dev/api
"""

import os
from typing import Any

import httpx

from scheduler.providers.base import BaseProvider, ProviderResult

GOOGLE_BASE_URL  = "https://generativelanguage.googleapis.com"
DEFAULT_MODEL    = "gemini-2.0-flash"
EMBED_MODEL      = "text-embedding-004"
IMAGE_MODEL      = "imagen-3.0-generate-002"

_MODEL_COSTS = {
    "gemini-2.0-flash":       {"input": 0.10e-6,  "output": 0.40e-6},
    "gemini-2.0-flash-lite":  {"input": 0.075e-6, "output": 0.30e-6},
    "gemini-2.5-pro":         {"input": 1.25e-6,  "output": 10.00e-6},
    "gemini-1.5-flash":       {"input": 0.075e-6, "output": 0.30e-6},
    "gemini-1.5-pro":         {"input": 1.25e-6,  "output": 5.00e-6},
}

IMAGE_COST_PER_IMAGE = 0.03


class GeminiProvider(BaseProvider):
    name = "gemini"

    def __init__(self):
        self.api_key = os.getenv("GOOGLE_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=GOOGLE_BASE_URL,
                timeout=120.0,
            )
        return self._client

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="GOOGLE_API_KEY not set"
            )

        handler = {
            "llm_inference":    self._llm,
            "embedding":        self._embedding,
            "image_generation": self._image,
        }.get(action)

        if not handler:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Gemini does not support action: {action}"
            )

        try:
            return await handler(input_payload)
        except httpx.HTTPStatusError as e:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Gemini HTTP {e.response.status_code}: {e.response.text}"
            )
        except httpx.TimeoutException:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="Gemini API timeout"
            )

    # ── LLM inference ─────────────────────────────────────────────────────────

    async def _llm(self, payload: Any) -> ProviderResult:
        """
        payload: str  (simple prompt)
             OR  {
                    "prompt": str,
                    "model": str,           optional, default gemini-2.0-flash
                    "system": str,          optional system instruction
                    "max_tokens": int,      optional, default 1024
                    "temperature": float,   optional
                    "messages": [...],      optional multi-turn history
                                            [{"role": "user"|"model", "content": str}]
                 }
        """
        if isinstance(payload, str):
            contents   = [{"role": "user", "parts": [{"text": payload}]}]
            model      = DEFAULT_MODEL
            max_tokens = 1024
            system     = None
            temperature = None
        else:
            model      = payload.get("model", DEFAULT_MODEL)
            max_tokens = payload.get("max_tokens", 1024)
            system     = payload.get("system")
            temperature = payload.get("temperature")

            if "messages" in payload:
                # Convert OpenAI-style messages to Gemini format
                contents = [
                    {
                        "role":  "model" if m["role"] == "assistant" else m["role"],
                        "parts": [{"text": m["content"]}],
                    }
                    for m in payload["messages"]
                    if m["role"] != "system"   # system handled separately
                ]
            else:
                prompt   = payload.get("prompt", str(payload))
                contents = [{"role": "user", "parts": [{"text": prompt}]}]

        body: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
            },
        }
        if temperature is not None:
            body["generationConfig"]["temperature"] = temperature
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        resp = await self._get_client().post(
            f"/v1beta/models/{model}:generateContent",
            params={"key": self.api_key},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract text from first candidate
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )

        # Settle cost from usageMetadata
        usage         = data.get("usageMetadata", {})
        input_tokens  = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)
        costs         = _MODEL_COSTS.get(model, _MODEL_COSTS[DEFAULT_MODEL])
        actual_cost   = (input_tokens  * costs["input"] +
                         output_tokens * costs["output"])

        return ProviderResult(
            success=True,
            output=text,
            actual_cost_usd=actual_cost,
            tokens_used=input_tokens + output_tokens,
        )

    # ── Embeddings ────────────────────────────────────────────────────────────

    async def _embedding(self, payload: Any) -> ProviderResult:
        """
        payload: str  (text to embed)
             OR  { "input": str, "model": str, "task_type": str }

        task_type options:
          RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, SEMANTIC_SIMILARITY,
          CLASSIFICATION, CLUSTERING  (default: RETRIEVAL_DOCUMENT)
        """
        text      = payload if isinstance(payload, str) else payload.get("input", str(payload))
        model     = EMBED_MODEL if isinstance(payload, str) else payload.get("model", EMBED_MODEL)
        task_type = "RETRIEVAL_DOCUMENT" if isinstance(payload, str) else payload.get("task_type", "RETRIEVAL_DOCUMENT")

        body = {
            "model":   f"models/{model}",
            "content": {"parts": [{"text": text}]},
            "taskType": task_type,
        }

        resp = await self._get_client().post(
            f"/v1beta/models/{model}:embedContent",
            params={"key": self.api_key},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        embedding = data.get("embedding", {}).get("values", [])

        # Gemini embeddings billed per 1k chars, roughly
        cost = (len(text) / 1000) * 0.00001

        return ProviderResult(
            success=True,
            output=embedding,
            actual_cost_usd=cost,
            tokens_used=len(text) // 4,
        )

    # ── Image generation ──────────────────────────────────────────────────────

    async def _image(self, payload: Any) -> ProviderResult:
        """
        payload: str  (prompt)
             OR  {
                    "prompt": str,
                    "n": int,              number of images, default 1
                    "aspect_ratio": str,   "1:1"|"16:9"|"9:16"|"4:3", default "1:1"
                 }
        """
        prompt       = payload if isinstance(payload, str) else payload.get("prompt", str(payload))
        n            = 1 if isinstance(payload, str) else payload.get("n", 1)
        aspect_ratio = "1:1" if isinstance(payload, str) else payload.get("aspect_ratio", "1:1")

        body = {
            "instances":  [{"prompt": prompt}],
            "parameters": {
                "sampleCount":  n,
                "aspectRatio":  aspect_ratio,
            },
        }

        resp = await self._get_client().post(
            f"/v1beta/models/{IMAGE_MODEL}:predict",
            params={"key": self.api_key},
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        # Response: { "predictions": [{ "bytesBase64Encoded": str, "mimeType": str }] }
        images = [
            {"b64": pred.get("bytesBase64Encoded"), "mime": pred.get("mimeType", "image/png")}
            for pred in data.get("predictions", [])
        ]

        return ProviderResult(
            success=True,
            output=images,
            actual_cost_usd=IMAGE_COST_PER_IMAGE * n,
        )