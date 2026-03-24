"""
qflow — Smart AI Task Scheduler
FastAPI entry point.
"""

import asyncio
import os
import logging

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

from scheduler.budget import BudgetController, estimate_cost
from scheduler.models import Job, JobStatus, TaskAccepted, TaskRequest
from scheduler.queue import enqueue, get_job, get_queue_depths
from scheduler.router import build_job_params
from scheduler.worker import run_worker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("qflow.api")

# ── Config ────────────────────────────────────────────────────────────────────

REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379")
TOTAL_BUDGET_USD  = float(os.getenv("TOTAL_BUDGET_USD", "5.0"))
GRAFANA_URL       = os.getenv("GRAFANA_URL", "http://localhost:3000/d/qflow")
API_BASE_URL      = os.getenv("API_BASE_URL", "http://localhost:8000")

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="qflow — Smart AI Task Scheduler",
    description="Provider-agnostic async scheduler with cost-aware throttling",
    version="0.1.0",
)

r: redis.Redis = None
budget: BudgetController = None


@app.on_event("startup")
async def startup():
    global r, budget
    r = redis.from_url(REDIS_URL, decode_responses=True)
    budget = BudgetController(r, TOTAL_BUDGET_USD)
    await budget.initialize()

    # Start worker as background task
    asyncio.create_task(run_worker(r, budget))
    logger.info(f"qflow started | budget=${TOTAL_BUDGET_USD} | redis={REDIS_URL}")


@app.on_event("shutdown")
async def shutdown():
    await r.aclose()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/tasks", response_model=TaskAccepted, status_code=202)
async def submit_task(request: TaskRequest):
    """
    Submit an AI task. Scheduler infers priority and provider.
    Returns job_id immediately — never blocks on AI execution.
    """
    priority, provider = build_job_params(request)
    estimated_cost = estimate_cost(provider, request.action, request.input)

    job = Job(
        provider=provider,
        action=request.action,
        priority=priority,
        input=request.input,
        webhook_url=request.webhook_url,
        metadata=request.metadata,
        estimated_cost_usd=estimated_cost,
    )

    position = await enqueue(r, job)
    budget_state = await budget.get_state()

    # tracker_url: poll endpoint for HIGH/MEDIUM, grafana for LOW
    from scheduler.models import Priority as P
    if priority == P.LOW:
        tracker_url = f"{GRAFANA_URL}?var-job_id={job.job_id}"
    else:
        tracker_url = f"{API_BASE_URL}/jobs/{job.job_id}"

    return TaskAccepted(
        job_id=job.job_id,
        status=JobStatus.QUEUED,
        priority=priority,
        queue_position=position,
        estimated_cost_usd=estimated_cost,
        budget_remaining_usd=budget_state["remaining_usd"],
        tracker_url=tracker_url,
        message=f"Queued as {priority.upper()} priority. "
                f"Budget remaining: ${budget_state['remaining_usd']:.4f}",
    )


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Poll job status (used for HIGH / MEDIUM priority tasks)."""
    job = await get_job(r, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id":          job.job_id,
        "status":          job.status,
        "priority":        job.priority,
        "provider":        job.provider,
        "action":          job.action,
        "estimated_cost":  job.estimated_cost_usd,
        "actual_cost":     job.actual_cost_usd,
        "result":          job.result if job.status == JobStatus.COMPLETED else None,
        "error":           job.error,
        "created_at":      job.created_at,
        "completed_at":    job.completed_at,
        "retry_count":     job.retry_count,
    }


@app.get("/queue/status")
async def queue_status():
    """Queue depths + budget state. Useful for ops / health checks."""
    depths = await get_queue_depths(r)
    budget_state = await budget.get_state()
    return {"queues": depths, "budget": budget_state}


@app.get("/health")
async def health():
    try:
        await r.ping()
        return {"status": "ok", "redis": "connected"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unreachable: {e}")


@app.get("/metrics")
async def metrics():
    """Prometheus scrape endpoint."""
    from scheduler.metrics import REGISTRY
    data = generate_latest(REGISTRY)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)