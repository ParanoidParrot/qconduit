"""
Queue layer — three Redis lists, one per priority.

  queue:high    → LIFO-ish, small, near real-time
  queue:medium  → FIFO, webhook-driven
  queue:low     → FIFO, batch / fire-and-forget

Job state stored as hash at:  job:{job_id}
"""

import json
import redis.asyncio as redis
from datetime import datetime, timezone

from scheduler.models import Job, JobStatus, Priority


QUEUE_KEYS = {
    Priority.HIGH:   "queue:high",
    Priority.MEDIUM: "queue:medium",
    Priority.LOW:    "queue:low",
}

JOB_KEY = lambda job_id: f"job:{job_id}"
JOB_TTL_SECONDS = 60 * 60 * 24   # keep job state for 24h


# ── Write ─────────────────────────────────────────────────────────────────────

async def enqueue(r: redis.Redis, job: Job) -> int:
    """Push job onto the correct priority queue. Returns queue position."""
    payload = job.model_dump_json()
    queue_key = QUEUE_KEYS[job.priority]

    # HIGH priority → LPUSH (front of list, picked up first)
    # MEDIUM / LOW  → RPUSH (back of list, FIFO)
    if job.priority == Priority.HIGH:
        await r.lpush(queue_key, payload)
    else:
        await r.rpush(queue_key, payload)

    # Persist job state hash separately for fast status lookups
    await _save_job(r, job)

    position = await r.llen(queue_key)
    return position


async def _save_job(r: redis.Redis, job: Job):
    await r.set(JOB_KEY(job.job_id), job.model_dump_json(), ex=JOB_TTL_SECONDS)


# ── Read ──────────────────────────────────────────────────────────────────────

async def dequeue_next(r: redis.Redis) -> Job | None:
    """
    Pop from highest-priority non-empty queue.
    Returns None if all queues empty.
    """
    for priority in [Priority.HIGH, Priority.MEDIUM, Priority.LOW]:
        raw = await r.lpop(QUEUE_KEYS[priority])
        if raw:
            return Job.model_validate_json(raw)
    return None


async def get_job(r: redis.Redis, job_id: str) -> Job | None:
    raw = await r.get(JOB_KEY(job_id))
    if not raw:
        return None
    return Job.model_validate_json(raw)


async def get_queue_depths(r: redis.Redis) -> dict:
    return {
        "high":   await r.llen("queue:high"),
        "medium": await r.llen("queue:medium"),
        "low":    await r.llen("queue:low"),
    }


# ── Update ────────────────────────────────────────────────────────────────────

async def update_job_status(
    r: redis.Redis,
    job_id: str,
    status: JobStatus,
    result=None,
    error: str = None,
    actual_cost: float = None,
):
    job = await get_job(r, job_id)
    if not job:
        return

    job.status = status

    if status == JobStatus.PROCESSING:
        job.started_at = datetime.now(timezone.utc)

    if status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.DEAD):
        job.completed_at = datetime.now(timezone.utc)

    if result is not None:
        job.result = result
    if error is not None:
        job.error = error
    if actual_cost is not None:
        job.actual_cost_usd = actual_cost

    await _save_job(r, job)