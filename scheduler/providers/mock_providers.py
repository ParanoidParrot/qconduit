"""
MockFlaky provider — for circuit breaker demo only.

Simulates an unreliable AI provider that fails at a configurable rate.
Use this to watch the circuit breaker trip in Grafana.

NOT registered by default — enable it in providers/__init__.py
or import directly in the demo script.
"""

import asyncio
import random
from typing import Any

from scheduler.providers.base import BaseProvider, ProviderResult


class MockFlakyProvider(BaseProvider):
    """
    Simulates a flaky provider.

    failure_rate: 0.0 = never fails, 1.0 = always fails
    latency_range: (min_seconds, max_seconds) per call
    """
    name = "mock_flaky"

    def __init__(self, failure_rate: float = 0.7, latency_range: tuple = (0.5, 3.0)):
        self.failure_rate = failure_rate
        self.latency_range = latency_range
        self._call_count = 0

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        self._call_count += 1

        # Simulate variable latency
        latency = random.uniform(*self.latency_range)
        await asyncio.sleep(latency)

        # Randomly fail based on failure_rate
        if random.random() < self.failure_rate:
            error_types = [
                "Connection timeout after 3s",
                "HTTP 503 Service Unavailable",
                "HTTP 429 Too Many Requests",
                "HTTP 500 Internal Server Error",
            ]
            return ProviderResult(
                success=False,
                output=None,
                actual_cost_usd=0,
                error=random.choice(error_types),
            )

        # Success path — return mock output matching action type
        output = {
            "tts":             "bW9ja19hdWRpb19ieXRlcw==",  # base64 "mock_audio_bytes"
            "stt":             {"transcript": "mock transcription result"},
            "llm_inference":   "This is a mock LLM response.",
            "translation":     {"translated_text": "यह एक नकली अनुवाद है।"},
            "transliteration": {"transliterated_text": "yah ek nakalee anuvaad hai"},
            "embedding":       [0.1] * 128,
            "image_generation": [{"url": "https://example.com/mock_image.png"}],
        }.get(action, f"mock output for {action}")

        return ProviderResult(
            success=True,
            output=output,
            actual_cost_usd=0.0001,
        )


class MockStableProvider(BaseProvider):
    """
    Always succeeds instantly. Useful as a fallback in circuit breaker tests
    to show that the scheduler routes away from the flaky provider.
    """
    name = "mock_stable"

    async def execute(self, action: str, input_payload: Any) -> ProviderResult:
        await asyncio.sleep(0.1)
        return ProviderResult(
            success=True,
            output=f"stable mock output for {action}",
            actual_cost_usd=0.00005,
        )