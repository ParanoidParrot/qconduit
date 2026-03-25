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
from scheduler.metrics import jobs_total

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
    jobs_total.labels(provider=provider.value, action=request.action.value, priority=priority.value).inc()
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


# ── Demo-only endpoints (circuit breaker testing) ─────────────────────────────

@app.post("/demo/register-flaky")
async def register_flaky_provider(failure_rate: float = 0.7):
    """Registers MockFlaky provider at runtime — demo/testing only."""
    from scheduler.providers import _REGISTRY
    from scheduler.providers.mock_providers import MockFlakyProvider, MockStableProvider
    _REGISTRY["mock_flaky"]  = MockFlakyProvider(failure_rate=failure_rate)
    _REGISTRY["mock_stable"] = MockStableProvider()
    return {
        "registered": ["mock_flaky", "mock_stable"],
        "failure_rate": failure_rate,
        "message": f"MockFlaky registered with {failure_rate*100:.0f}% failure rate",
    }


@app.post("/demo/task", response_model=TaskAccepted, status_code=202)
async def submit_demo_task(provider: str, action: str, input: dict | str | None = None):
    """Submit a task with explicit provider — demo/testing only. Bypasses provider inference."""
    from scheduler.models import ActionType, Provider
    from scheduler.budget import estimate_cost
    from scheduler.router import infer_priority

    try:
        _provider = Provider(provider)
        _action   = ActionType(action)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    priority       = infer_priority(_action)
    estimated_cost = estimate_cost(_provider, _action, input or {})

    job = Job(
        provider=_provider, action=_action, priority=priority,
        input=input or {}, estimated_cost_usd=estimated_cost,
    )
    position     = await enqueue(r, job)
    budget_state = await budget.get_state()

    from scheduler.models import Priority as P
    tracker_url = (
        f"{GRAFANA_URL}?var-job_id={job.job_id}" if priority == P.LOW
        else f"{API_BASE_URL}/jobs/{job.job_id}"
    )

    return TaskAccepted(
        job_id=job.job_id, status=JobStatus.QUEUED, priority=priority,
        queue_position=position, estimated_cost_usd=estimated_cost,
        budget_remaining_usd=budget_state["remaining_usd"],
        tracker_url=tracker_url,
        message=f"Demo task queued via {provider}/{action}",
    )