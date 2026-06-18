"""
Meta AI provider — Llama models via Meta's Llama API.

Actions supported:
  llm_inference  →  /v1/chat/completions  (OpenAI-compatible endpoint)

Models:
  Llama-4-Scout-17B-16E-Instruct  — fast, efficient MoE model
  Llama-4-Maverick-17B-128E-Instruct — more capable MoE model
  Llama-3.3-70B-Instruct          — strong all-round model
  Llama-3.2-3B-Instruct           — lightweight, very fast

Cost per token (USD):
  Llama-4-Scout:    input $0.17/M, output $0.17/M
  Llama-4-Maverick: input $0.27/M, output $0.85/M
  Llama-3.3-70B:    input $0.59/M, output $0.79/M
  Llama-3.2-3B:     input $0.06/M, output $0.06/M

Note: Meta Llama API uses an OpenAI-compatible interface.
      For local inference use the Ollama provider instead.

Set env var: META_API_KEY
Docs: https://llama.developer.meta.com/docs
"""

import os
from typing import Any

import httpx

from scheduler.providers.base import BaseProvider, ProviderResult

META_BASE_URL = "https://api.llama.com"
DEFAULT_MODEL = "Llama-4-Scout-17B-16E-Instruct"

_MODEL_COSTS = {
    "Llama-4-Scout-17B-16E-Instruct":    {"input": 0.17e-6, "output": 0.17e-6},
    "Llama-4-Maverick-17B-128E-Instruct": {"input": 0.27e-6, "output": 0.85e-6},
    "Llama-3.3-70B-Instruct":             {"input": 0.59e-6, "output": 0.79e-6},
    "Llama-3.2-11B-Vision-Instruct":      {"input": 0.16e-6, "output": 0.16e-6},
    "Llama-3.2-3B-Instruct":              {"input": 0.06e-6, "output": 0.06e-6},
    "Llama-3.1-8B-Instruct":              {"input": 0.10e-6, "output": 0.10e-6},
}


class MetaProvider(BaseProvider):
    name = "meta"

    def __init__(self):
        self.api_key = os.getenv("META_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=META_BASE_URL,
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
                error="META_API_KEY not set"
            )

        if action != "llm_inference":
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Meta provider only supports llm_inference, got: {action}"
            )

        try:
            return await self._llm(input_payload)
        except httpx.HTTPStatusError as e:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Meta HTTP {e.response.status_code}: {e.response.text}"
            )
        except httpx.TimeoutException:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="Meta API timeout"
            )

    async def _llm(self, payload: Any) -> ProviderResult:
        """
        payload: str  (simple prompt)
             OR  {
                    "prompt": str,
                    "messages": [...],    OpenAI-style messages array
                    "system": str,        optional system prompt
                    "model": str,         optional, default Llama-4-Scout-17B-16E-Instruct
                    "max_tokens": int,    optional, default 1024
                    "temperature": float  optional
                 }
        """
        if isinstance(payload, str):
            messages    = [{"role": "user", "content": payload}]
            model       = DEFAULT_MODEL
            max_tokens  = 1024
            temperature = None
        else:
            if "messages" in payload:
                messages = payload["messages"]
            else:
                prompt   = payload.get("prompt", str(payload))
                messages = [{"role": "user", "content": prompt}]

            if "system" in payload:
                messages = [{"role": "system", "content": payload["system"]}] + messages

            model       = payload.get("model", DEFAULT_MODEL)
            max_tokens  = payload.get("max_tokens", 1024)
            temperature = payload.get("temperature")

        body: dict = {
            "model":      model,
            "messages":   messages,
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            body["temperature"] = temperature

        # Meta Llama API is OpenAI-compatible
        resp = await self._get_client().post("/v1/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        content       = data["choices"][0]["message"]["content"]
        usage         = data.get("usage", {})
        input_tokens  = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        costs         = _MODEL_COSTS.get(model, _MODEL_COSTS[DEFAULT_MODEL])
        actual_cost   = (input_tokens  * costs["input"] +
                         output_tokens * costs["output"])

        return ProviderResult(
            success=True,
            output=content,
            actual_cost_usd=actual_cost,
            tokens_used=input_tokens + output_tokens,
        )