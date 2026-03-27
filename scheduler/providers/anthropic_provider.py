"""
Anthropic provider — Claude models via the Messages API.

Actions supported:
  llm_inference  →  /v1/messages  (claude-3-5-haiku-20241022 default)

Cost per token (USD):
  claude-3-5-haiku:  input $0.80/M,  output $4.00/M
  claude-3-5-sonnet: input $3.00/M,  output $15.00/M
  claude-opus-4:     input $15.00/M, output $75.00/M

Set env var: ANTHROPIC_API_KEY
"""

import os
from typing import Any

import httpx

from scheduler.providers.base import BaseProvider, ProviderResult

ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION  = "2023-06-01"

# Cost per token in USD — update as Anthropic revises pricing
_MODEL_COSTS = {
    "claude-haiku-4-5-20251001":    {"input": 0.80e-6,  "output": 4.00e-6},
    "claude-3-5-haiku-20241022":    {"input": 0.80e-6,  "output": 4.00e-6},
    "claude-sonnet-4-5":            {"input": 3.00e-6,  "output": 15.00e-6},
    "claude-3-5-sonnet-20241022":   {"input": 3.00e-6,  "output": 15.00e-6},
    "claude-opus-4":                {"input": 15.00e-6, "output": 75.00e-6},
}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self):
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=ANTHROPIC_BASE_URL,
                headers={
                    "x-api-key":         self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type":      "application/json",
                },
                timeout=120.0,
            )
        return self._client

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        if not self.api_key:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="ANTHROPIC_API_KEY not set"
            )

        if action != "llm_inference":
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Anthropic provider only supports llm_inference, got: {action}"
            )

        try:
            return await self._llm(input_payload)
        except httpx.HTTPStatusError as e:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Anthropic HTTP {e.response.status_code}: {e.response.text}"
            )
        except httpx.TimeoutException:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="Anthropic API timeout"
            )

    async def _llm(self, payload: Any) -> ProviderResult:
        """
        payload: str  (simple prompt — becomes a user message)
             OR  {
                    "prompt": str,           simple user message
                    "messages": [...],       full Messages API format (overrides prompt)
                    "system": str,           optional system prompt
                    "model": str,            optional, default claude-haiku-4-5-20251001
                    "max_tokens": int,       optional, default 1024
                    "temperature": float,    optional, 0.0–1.0
                 }
        """
        if isinstance(payload, str):
            messages   = [{"role": "user", "content": payload}]
            model      = DEFAULT_MODEL
            max_tokens = 1024
            system     = None
            temperature = None
        else:
            # Full messages array takes priority over prompt shorthand
            if "messages" in payload:
                messages = payload["messages"]
            else:
                prompt   = payload.get("prompt", str(payload))
                messages = [{"role": "user", "content": prompt}]

            model       = payload.get("model", DEFAULT_MODEL)
            max_tokens  = payload.get("max_tokens", 1024)
            system      = payload.get("system")
            temperature = payload.get("temperature")

        body: dict = {
            "model":      model,
            "messages":   messages,
            "max_tokens": max_tokens,
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature

        resp = await self._get_client().post("/v1/messages", json=body)
        resp.raise_for_status()
        data = resp.json()

        # Extract text from content blocks
        content_blocks = data.get("content", [])
        text = " ".join(
            block["text"] for block in content_blocks
            if block.get("type") == "text"
        )

        # Settle cost from actual usage
        usage         = data.get("usage", {})
        input_tokens  = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        costs         = _MODEL_COSTS.get(model, _MODEL_COSTS[DEFAULT_MODEL])
        actual_cost   = (input_tokens  * costs["input"] +
                         output_tokens * costs["output"])

        return ProviderResult(
            success=True,
            output=text,
            actual_cost_usd=actual_cost,
            tokens_used=input_tokens + output_tokens,
        )