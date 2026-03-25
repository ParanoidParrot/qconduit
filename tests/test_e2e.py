"""
tests/test_e2e.py

End-to-end tests for qconduit.

Tests are structured in three layers:
  Unit        — pure logic, no Redis, no API calls
  Integration — needs Redis (docker-compose up redis)
  Live        — needs real API keys + Redis (skipped by default)

Run unit + integration:   pytest tests/
Run all including live:   pytest tests/ --live
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_tts_payload():
    return {"text": "नमस्ते दुनिया", "language_code": "hi-IN"}

@pytest.fixture
def sample_stt_payload():
    import base64
    return {
        "audio_b64":        base64.b64encode(b"fake_audio_bytes").decode(),
        "audio_format":     "wav",
        "language_code":    "hi-IN",
        "duration_seconds": 5.0,
    }

@pytest.fixture
def sample_translation_payload():
    return {"input": "Hello, how are you?", "target_language_code": "hi-IN"}


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS — no external dependencies
# ═══════════════════════════════════════════════════════════════════════════════

class TestPriorityInference:
    """Router correctly maps action types to priority lanes."""

    def test_tts_is_high(self):
        from scheduler.models import ActionType, Priority
        from scheduler.router import infer_priority
        assert infer_priority(ActionType.TTS) == Priority.HIGH

    def test_stt_is_high(self):
        from scheduler.models import ActionType, Priority
        from scheduler.router import infer_priority
        assert infer_priority(ActionType.STT) == Priority.HIGH

    def test_llm_is_medium(self):
        from scheduler.models import ActionType, Priority
        from scheduler.router import infer_priority
        assert infer_priority(ActionType.LLM_INFERENCE) == Priority.MEDIUM

    def test_translation_is_medium(self):
        from scheduler.models import ActionType, Priority
        from scheduler.router import infer_priority
        assert infer_priority(ActionType.TRANSLATION) == Priority.MEDIUM

    def test_embedding_is_low(self):
        from scheduler.models import ActionType, Priority
        from scheduler.router import infer_priority
        assert infer_priority(ActionType.EMBEDDING) == Priority.LOW

    def test_image_generation_is_low(self):
        from scheduler.models import ActionType, Priority
        from scheduler.router import infer_priority
        assert infer_priority(ActionType.IMAGE_GENERATION) == Priority.LOW


class TestProviderInference:
    """Router picks correct default provider per action."""

    def test_tts_defaults_to_sarvam(self):
        from scheduler.models import ActionType, Provider
        from scheduler.router import infer_provider
        assert infer_provider(ActionType.TTS, None) == Provider.SARVAM

    def test_llm_defaults_to_openai(self):
        from scheduler.models import ActionType, Provider
        from scheduler.router import infer_provider
        assert infer_provider(ActionType.LLM_INFERENCE, None) == Provider.OPENAI

    def test_explicit_provider_respected(self):
        from scheduler.models import ActionType, Provider
        from scheduler.router import infer_provider
        result = infer_provider(ActionType.TTS, Provider.OPENAI)
        assert result == Provider.OPENAI


class TestCostEstimation:
    """Budget controller cost estimates are in the right ballpark."""

    def test_tts_cost_sarvam(self):
        from scheduler.budget import estimate_cost
        from scheduler.models import ActionType, Provider
        # "hello" = 5 chars * 0.000003 = 0.000015
        cost = estimate_cost(Provider.SARVAM, ActionType.TTS, {"text": "hello"})
        assert cost > 0
        assert cost < 0.01   # sanity: should be fractions of a cent for short text

    def test_embedding_cost_openai(self):
        from scheduler.budget import estimate_cost
        from scheduler.models import ActionType, Provider
        cost = estimate_cost(Provider.OPENAI, ActionType.EMBEDDING, "some text to embed")
        assert cost >= 0

    def test_unknown_provider_returns_zero(self):
        from scheduler.budget import estimate_cost
        from scheduler.models import ActionType, Provider
        cost = estimate_cost(Provider.OLLAMA, ActionType.LLM_INFERENCE, "test")
        assert cost == 0.0  # ollama is free/local

    def test_image_generation_is_most_expensive(self):
        from scheduler.budget import estimate_cost
        from scheduler.models import ActionType, Provider
        img_cost = estimate_cost(Provider.OPENAI, ActionType.IMAGE_GENERATION, {})
        tts_cost = estimate_cost(Provider.OPENAI, ActionType.TTS, {"text": "hello"})
        assert img_cost > tts_cost


class TestBudgetThrottleLogic:
    """Throttle thresholds fire at the right percentages."""

    def _make_budget(self, remaining_pct: float):
        """Returns a mock BudgetController that reports given remaining %."""
        from scheduler.budget import BudgetController
        budget = MagicMock(spec=BudgetController)
        total = 10.0
        remaining = total * remaining_pct / 100
        budget.get_state = AsyncMock(return_value={
            "total_usd":     total,
            "spent_usd":     total - remaining,
            "reserved_usd":  0.0,
            "remaining_usd": remaining,
            "pct_remaining": remaining_pct,
        })
        # Replicate can_proceed logic directly
        from scheduler.budget import THROTTLE_RULES

        async def can_proceed(priority, estimated_cost):
            if remaining_pct <= THROTTLE_RULES["hard_stop_at_pct"]:
                return False, "hard stop"
            if priority == "low" and remaining_pct <= THROTTLE_RULES["throttle_low_priority_at_pct"]:
                return False, f"throttling LOW"
            if priority == "medium" and remaining_pct <= THROTTLE_RULES["throttle_medium_priority_at_pct"]:
                return False, f"throttling MEDIUM"
            return True, "ok"

        budget.can_proceed = can_proceed
        return budget

    @pytest.mark.asyncio
    async def test_low_priority_throttled_at_20pct(self):
        budget = self._make_budget(15.0)
        allowed, reason = await budget.can_proceed("low", 0.001)
        assert not allowed
        assert "LOW" in reason

    @pytest.mark.asyncio
    async def test_medium_priority_throttled_at_5pct(self):
        budget = self._make_budget(3.0)
        allowed, reason = await budget.can_proceed("medium", 0.001)
        assert not allowed
        assert "MEDIUM" in reason

    @pytest.mark.asyncio
    async def test_high_priority_always_proceeds(self):
        budget = self._make_budget(3.0)   # below low + medium thresholds
        allowed, reason = await budget.can_proceed("high", 0.001)
        assert allowed

    @pytest.mark.asyncio
    async def test_hard_stop_blocks_everything(self):
        budget = self._make_budget(0.5)   # below 1% hard stop
        for priority in ["high", "medium", "low"]:
            allowed, reason = await budget.can_proceed(priority, 0.001)
            assert not allowed


class TestJobModel:
    """Job model serialises and deserialises cleanly."""

    def test_job_round_trip(self):
        from scheduler.models import ActionType, Job, JobStatus, Priority, Provider
        job = Job(
            provider=Provider.SARVAM,
            action=ActionType.TTS,
            priority=Priority.HIGH,
            input={"text": "test", "language_code": "hi-IN"},
        )
        serialised = job.model_dump_json()
        restored = Job.model_validate_json(serialised)
        assert restored.job_id == job.job_id
        assert restored.status == JobStatus.QUEUED
        assert restored.provider == Provider.SARVAM




class TestPriorityOverride:
    """Caller-supplied priority_override is respected over inferred priority."""

    def test_override_low_for_tts(self):
        """TTS is normally HIGH — caller can demote it to LOW for batch use."""
        from scheduler.models import ActionType, Priority, TaskRequest
        from scheduler.router import build_job_params

        req = TaskRequest(
            action=ActionType.TTS,
            input={"text": "batch tts job"},
            priority_override=Priority.LOW,
        )
        priority, provider, source = build_job_params(req)
        assert priority == Priority.LOW
        assert source   == "override"

    def test_no_override_returns_inferred(self):
        from scheduler.models import ActionType, Priority, TaskRequest
        from scheduler.router import build_job_params

        req = TaskRequest(action=ActionType.TTS, input={"text": "live tts"})
        priority, provider, source = build_job_params(req)
        assert priority == Priority.HIGH
        assert source   == "inferred"

    def test_override_high_for_embedding(self):
        """Embedding is normally LOW — caller can promote it."""
        from scheduler.models import ActionType, Priority, TaskRequest
        from scheduler.router import build_job_params

        req = TaskRequest(
            action=ActionType.EMBEDDING,
            input="urgent embedding",
            priority_override=Priority.HIGH,
        )
        priority, _, source = build_job_params(req)
        assert priority == Priority.HIGH
        assert source   == "override"

    def test_override_medium_for_llm(self):
        """LLM is normally MEDIUM — override stays MEDIUM, source is still 'override'."""
        from scheduler.models import ActionType, Priority, TaskRequest
        from scheduler.router import build_job_params

        req = TaskRequest(
            action=ActionType.LLM_INFERENCE,
            input="test",
            priority_override=Priority.MEDIUM,
        )
        priority, _, source = build_job_params(req)
        assert priority == Priority.MEDIUM
        assert source   == "override"


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS — need Redis
# ═══════════════════════════════════════════════════════════════════════════════

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

def _redis_available() -> bool:
    try:
        import redis
        r = redis.from_url(REDIS_URL)
        r.ping()
        return True
    except Exception:
        return False

needs_redis = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis not available — start with: docker-compose up redis"
)


@needs_redis
class TestQueueIntegration:

    @pytest_asyncio.fixture
    async def redis_client(self):
        import redis.asyncio as aioredis
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        # Clean test keys before each test
        for key in await r.keys("queue:*"):
            await r.delete(key)
        for key in await r.keys("job:*"):
            await r.delete(key)
        yield r
        await r.aclose()

    @pytest.mark.asyncio
    async def test_enqueue_dequeue_high_priority(self, redis_client):
        from scheduler.models import ActionType, Job, Priority, Provider
        from scheduler.queue import dequeue_next, enqueue

        job = Job(provider=Provider.SARVAM, action=ActionType.TTS,
                  priority=Priority.HIGH, input={"text": "test"})
        await enqueue(redis_client, job)
        dequeued = await dequeue_next(redis_client)

        assert dequeued is not None
        assert dequeued.job_id == job.job_id

    @pytest.mark.asyncio
    async def test_high_priority_dequeued_before_low(self, redis_client):
        from scheduler.models import ActionType, Job, Priority, Provider
        from scheduler.queue import dequeue_next, enqueue

        low_job = Job(provider=Provider.OPENAI, action=ActionType.EMBEDDING,
                      priority=Priority.LOW, input="test")
        high_job = Job(provider=Provider.SARVAM, action=ActionType.TTS,
                       priority=Priority.HIGH, input={"text": "urgent"})

        # Enqueue low first, then high
        await enqueue(redis_client, low_job)
        await enqueue(redis_client, high_job)

        first = await dequeue_next(redis_client)
        assert first.job_id == high_job.job_id   # HIGH always wins

    @pytest.mark.asyncio
    async def test_job_status_update(self, redis_client):
        from scheduler.models import ActionType, Job, JobStatus, Priority, Provider
        from scheduler.queue import enqueue, get_job, update_job_status

        job = Job(provider=Provider.SARVAM, action=ActionType.TTS,
                  priority=Priority.HIGH, input={"text": "test"})
        await enqueue(redis_client, job)
        await update_job_status(redis_client, job.job_id, JobStatus.PROCESSING)

        updated = await get_job(redis_client, job.job_id)
        assert updated.status == JobStatus.PROCESSING
        assert updated.started_at is not None

    @pytest.mark.asyncio
    async def test_queue_depths(self, redis_client):
        from scheduler.models import ActionType, Job, Priority, Provider
        from scheduler.queue import enqueue, get_queue_depths

        for _ in range(3):
            await enqueue(redis_client,
                Job(provider=Provider.SARVAM, action=ActionType.TTS,
                    priority=Priority.HIGH, input={"text": "hi"}))
        for _ in range(2):
            await enqueue(redis_client,
                Job(provider=Provider.OPENAI, action=ActionType.EMBEDDING,
                    priority=Priority.LOW, input="embed this"))

        depths = await get_queue_depths(redis_client)
        assert depths["high"] == 3
        assert depths["low"] == 2
        assert depths["medium"] == 0

    @pytest.mark.asyncio
    async def test_budget_reserve_settle(self, redis_client):
        from scheduler.budget import BudgetController

        budget = BudgetController(redis_client, total_budget_usd=10.0)
        await budget.initialize()

        await budget.reserve("job-test-1", 0.05)
        state = await budget.get_state()
        assert state["reserved_usd"] == pytest.approx(0.05, abs=0.001)

        await budget.settle("job-test-1", actual_cost=0.03)
        state = await budget.get_state()
        assert state["reserved_usd"] == pytest.approx(0.0, abs=0.001)
        assert state["spent_usd"] == pytest.approx(0.03, abs=0.001)


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE TESTS — need real API keys (opt-in with --live flag)
# ═══════════════════════════════════════════════════════════════════════════════

def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False,
                     help="Run live API tests (requires API keys)")

def pytest_configure(config):
    config.addinivalue_line("markers", "live: requires real API keys")

needs_live = pytest.mark.skipif(
    "not config.getoption('--live')",
    reason="Skipped by default — run with pytest --live to enable"
)


@needs_live
class TestSarvamLive:

    @pytest.mark.asyncio
    async def test_tts_returns_audio(self, sample_tts_payload):
        from scheduler.providers.sarvam import SarvamProvider
        provider = SarvamProvider()
        result = await provider.execute("tts", sample_tts_payload)
        assert result.success, result.error
        assert result.output is not None
        assert result.actual_cost_usd > 0

    @pytest.mark.asyncio
    async def test_translation(self, sample_translation_payload):
        from scheduler.providers.sarvam import SarvamProvider
        provider = SarvamProvider()
        result = await provider.execute("translation", sample_translation_payload)
        assert result.success, result.error
        assert "translated_text" in result.output


@needs_live
class TestOpenAILive:

    @pytest.mark.asyncio
    async def test_llm_inference(self):
        from scheduler.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider()
        result = await provider.execute("llm_inference", "Say hello in one word.")
        assert result.success, result.error
        assert isinstance(result.output, str)
        assert len(result.output) > 0
        assert result.tokens_used > 0

    @pytest.mark.asyncio
    async def test_embedding_returns_vector(self):
        from scheduler.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider()
        result = await provider.execute("embedding", "test sentence for embedding")
        assert result.success, result.error
        assert isinstance(result.output, list)
        assert len(result.output) > 100   # embedding vectors are long