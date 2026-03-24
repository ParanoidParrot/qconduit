"""
Worker — runs as a separate process (or asyncio background task).
Pulls jobs from Redis queue, executes them, settles budget.

Circuit breaker state per provider stored in Redis:
  cb:{provider}:failures     int
  cb:{provider}:open_until   timestamp (epoch)
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import redis.asyncio as redis

from scheduler.budget import BudgetController
from scheduler.metrics import (
    circuit_breaker_state, job_duration_seconds, jobs_completed,
    jobs_failed, jobs_throttled, queue_depth, budget_remaining_usd,
    budget_spent_usd, budget_reserved_usd,
)
from scheduler.models import JobStatus, Priority
from scheduler.providers import get_provider
from scheduler.queue import dequeue_next, get_queue_depths, update_job_status

logger = logging.getLogger("qflow.worker")

# ── Circuit breaker config ────────────────────────────────────────────────────
CB_FAILURE_THRESHOLD = 3       # trips after N consecutive failures
CB_RECOVERY_SECONDS  = 60      # how long to stay open


async def _is_circuit_open(r: redis.Redis, provider: str) -> bool:
    open_until = await r.get(f"cb:{provider}:open_until")
    if open_until and float(open_until) > time.time():
        return True
    # reset if window expired
    await r.delete(f"cb:{provider}:open_until")
    await r.set(f"cb:{provider}:failures", 0)
    return False


async def _record_failure(r: redis.Redis, provider: str):
    failures = await r.incr(f"cb:{provider}:failures")
    if failures >= CB_FAILURE_THRESHOLD:
        open_until = time.time() + CB_RECOVERY_SECONDS
        await r.set(f"cb:{provider}:open_until", open_until)
        circuit_breaker_state.labels(provider=provider).set(1)
        logger.warning(f"Circuit breaker OPEN for {provider} ({failures} failures)")


async def _record_success(r: redis.Redis, provider: str):
    await r.set(f"cb:{provider}:failures", 0)
    circuit_breaker_state.labels(provider=provider).set(0)


# ── Metric sync ───────────────────────────────────────────────────────────────

async def _sync_metrics(r: redis.Redis, budget: BudgetController):
    depths = await get_queue_depths(r)
    for p, count in depths.items():
        queue_depth.labels(priority=p).set(count)

    state = await budget.get_state()
    budget_spent_usd.set(state["spent_usd"])
    budget_remaining_usd.set(state["remaining_usd"])
    budget_reserved_usd.set(state["reserved_usd"])


# ── Main worker loop ──────────────────────────────────────────────────────────

async def run_worker(r: redis.Redis, budget: BudgetController, poll_interval: float = 0.5):
    logger.info("Worker started")

    while True:
        await _sync_metrics(r, budget)

        job = await dequeue_next(r)
        if not job:
            await asyncio.sleep(poll_interval)
            continue

        logger.info(f"Picked up job {job.job_id} | {job.provider}/{job.action} | {job.priority}")

        # ── Budget check ──────────────────────────────────────────────────────
        allowed, reason = await budget.can_proceed(job.priority.value, job.estimated_cost_usd)
        if not allowed:
            logger.warning(f"Throttled job {job.job_id}: {reason}")
            await update_job_status(r, job.job_id, JobStatus.THROTTLED, error=reason)
            jobs_throttled.labels(priority=job.priority.value).inc()
            # Re-queue LOW/MEDIUM after a backoff; drop if hard stop
            if "hard stop" not in reason:
                await asyncio.sleep(5)
                job.status = JobStatus.QUEUED   # reset before re-enqueue so _save_job doesn't stomp
                from scheduler.queue import enqueue
                await enqueue(r, job)
            continue

        # ── Circuit breaker check ─────────────────────────────────────────────
        if await _is_circuit_open(r, job.provider.value):
            logger.warning(f"Circuit open for {job.provider} — requeueing {job.job_id}")
            await update_job_status(r, job.job_id, JobStatus.THROTTLED,
                                    error=f"Circuit open for {job.provider.value}")
            await asyncio.sleep(2)
            job.status = JobStatus.QUEUED   # reset before re-enqueue
            from scheduler.queue import enqueue
            await enqueue(r, job)
            continue

        # ── Execute ───────────────────────────────────────────────────────────
        await update_job_status(r, job.job_id, JobStatus.PROCESSING)
        await budget.reserve(job.job_id, job.estimated_cost_usd)

        provider = get_provider(job.provider.value)
        if not provider:
            await update_job_status(r, job.job_id, JobStatus.FAILED,
                                    error=f"Provider {job.provider} not registered")
            await budget.settle(job.job_id, 0)
            continue

        t_start = time.time()
        try:
            result = await provider.execute(job.action.value, job.input)
            elapsed = time.time() - t_start

            if result.success:
                await update_job_status(r, job.job_id, JobStatus.COMPLETED,
                                        result=result.output,
                                        actual_cost=result.actual_cost_usd)
                await budget.settle(job.job_id, result.actual_cost_usd)
                await _record_success(r, job.provider.value)
                jobs_completed.labels(provider=job.provider.value, action=job.action.value).inc()
                job_duration_seconds.labels(provider=job.provider.value, action=job.action.value).observe(elapsed)
                logger.info(f"Completed {job.job_id} in {elapsed:.2f}s cost=${result.actual_cost_usd:.6f}")

                # Webhook callback for MEDIUM priority
                if job.priority == Priority.MEDIUM and job.webhook_url:
                    await _fire_webhook(job.webhook_url, job.job_id, result.output)

            else:
                raise Exception(result.error or "Provider returned failure")

        except Exception as exc:
            elapsed = time.time() - t_start
            logger.error(f"Job {job.job_id} failed: {exc}")
            await _record_failure(r, job.provider.value)
            await budget.settle(job.job_id, 0)

            # Retry logic (max 3 attempts)
            if job.retry_count < 3:
                job.retry_count += 1
                job.status = JobStatus.QUEUED
                from scheduler.queue import enqueue
                backoff = 2 ** job.retry_count
                logger.info(f"Retrying {job.job_id} (attempt {job.retry_count}) in {backoff}s")
                await asyncio.sleep(backoff)
                await enqueue(r, job)
            else:
                await update_job_status(r, job.job_id, JobStatus.DEAD,
                                        error=str(exc))
                jobs_failed.labels(provider=job.provider.value,
                                   action=job.action.value,
                                   reason="retries_exhausted").inc()


async def _fire_webhook(url: str, job_id: str, result):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"job_id": job_id, "result": str(result)})
    except Exception as e:
        logger.warning(f"Webhook delivery failed for {job_id}: {e}")