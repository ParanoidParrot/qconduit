"""
Ollama provider — local inference, completely free.

Ollama runs models locally via a REST API on localhost.
No API key needed. Cost is always $0.00.

Actions supported:
  llm_inference  →  /api/chat      (default model: llama3.2)
  embedding      →  /api/embeddings (default model: nomic-embed-text)

Prerequisites:
  brew install ollama          # macOS
  ollama serve                 # starts the local server on :11434
  ollama pull llama3.2         # pull a model before use
  ollama pull nomic-embed-text # pull embedding model

Set env var: OLLAMA_BASE_URL (default: http://localhost:11434)

Docs: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

import os
from typing import Any

import httpx

from scheduler.providers.base import BaseProvider, ProviderResult

OLLAMA_BASE_URL  = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_LLM      = os.getenv("OLLAMA_LLM_MODEL", "llama3.2")
DEFAULT_EMBED    = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=180.0,   # local models can be slow on first run
            )
        return self._client

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        handler = {
            "llm_inference": self._llm,
            "embedding":     self._embedding,
        }.get(action)

        if not handler:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Ollama does not support action: {action}"
            )

        try:
            return await handler(input_payload)
        except httpx.ConnectError:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Is it running? Try: ollama serve"
                )
            )
        except httpx.TimeoutException:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error="Ollama timed out — model may still be loading, retry shortly"
            )
        except httpx.HTTPStatusError as e:
            return ProviderResult(
                success=False, output=None, actual_cost_usd=0,
                error=f"Ollama HTTP {e.response.status_code}: {e.response.text}"
            )

    # ── LLM inference ─────────────────────────────────────────────────────────

    async def _llm(self, payload: Any) -> ProviderResult:
        """
        payload: str  (simple prompt)
             OR  {
                    "prompt": str,        simple user message
                    "messages": [...],    OpenAI-style messages array (preferred)
                    "system": str,        optional system prompt
                    "model": str,         optional, overrides OLLAMA_LLM_MODEL
                    "temperature": float  optional
                 }
        """
        if isinstance(payload, str):
            messages = [{"role": "user", "content": payload}]
            model    = DEFAULT_LLM
            options  = {}
        else:
            if "messages" in payload:
                messages = payload["messages"]
            else:
                prompt   = payload.get("prompt", str(payload))
                messages = [{"role": "user", "content": prompt}]

            model = payload.get("model", DEFAULT_LLM)
            options = {}
            if "temperature" in payload:
                options["temperature"] = payload["temperature"]

            if "system" in payload:
                # Prepend system message in OpenAI-compatible format
                messages = [{"role": "system", "content": payload["system"]}] + messages

        body: dict = {
            "model":    model,
            "messages": messages,
            "stream":   False,   # we want a single JSON response
        }
        if options:
            body["options"] = options

        resp = await self._get_client().post("/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()

        # Response: { "message": { "role": "assistant", "content": str }, "eval_count": int, ... }
        content      = data.get("message", {}).get("content", "")
        total_tokens = data.get("eval_count", 0) + data.get("prompt_eval_count", 0)

        return ProviderResult(
            success=True,
            output=content,
            actual_cost_usd=0.0,   # local — always free
            tokens_used=total_tokens,
        )

    # ── Embeddings ────────────────────────────────────────────────────────────

    async def _embedding(self, payload: Any) -> ProviderResult:
        """
        payload: str  (text to embed)
             OR  { "input": str, "model": str }
        """
        text  = payload if isinstance(payload, str) else payload.get("input", str(payload))
        model = DEFAULT_EMBED if isinstance(payload, str) else payload.get("model", DEFAULT_EMBED)

        body = {"model": model, "prompt": text}

        resp = await self._get_client().post("/api/embeddings", json=body)
        resp.raise_for_status()
        data = resp.json()

        # Response: { "embedding": [float, ...] }
        embedding = data.get("embedding", [])

        return ProviderResult(
            success=True,
            output=embedding,
            actual_cost_usd=0.0,
            tokens_used=len(text) // 4,  # Ollama doesn't report token count for embeddings
        )