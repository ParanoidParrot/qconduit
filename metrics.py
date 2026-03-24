"""
Prometheus metrics — imported by both API and worker.
Grafana reads these via /metrics endpoint.
"""

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry

REGISTRY = CollectorRegistry()

# ── Queue ─────────────────────────────────────────────────────────────────────

queue_depth = Gauge(
    "qflow_queue_depth",
    "Current number of jobs in queue",
    ["priority"],
    registry=REGISTRY,
)

# ── Jobs ──────────────────────────────────────────────────────────────────────

jobs_total = Counter(
    "qflow_jobs_total",
    "Total jobs submitted",
    ["provider", "action", "priority"],
    registry=REGISTRY,
)

jobs_completed = Counter(
    "qflow_jobs_completed_total",
    "Jobs completed successfully",
    ["provider", "action"],
    registry=REGISTRY,
)

jobs_failed = Counter(
    "qflow_jobs_failed_total",
    "Jobs that failed (including retries exhausted)",
    ["provider", "action", "reason"],
    registry=REGISTRY,
)

jobs_throttled = Counter(
    "qflow_jobs_throttled_total",
    "Jobs blocked by budget throttling",
    ["priority"],
    registry=REGISTRY,
)

# ── Latency ───────────────────────────────────────────────────────────────────

job_duration_seconds = Histogram(
    "qflow_job_duration_seconds",
    "Time from worker pickup to completion",
    ["provider", "action"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
    registry=REGISTRY,
)

# ── Budget ────────────────────────────────────────────────────────────────────

budget_spent_usd = Gauge(
    "qflow_budget_spent_usd",
    "Cumulative USD spent on AI calls",
    registry=REGISTRY,
)

budget_remaining_usd = Gauge(
    "qflow_budget_remaining_usd",
    "USD remaining in budget bucket",
    registry=REGISTRY,
)

budget_reserved_usd = Gauge(
    "qflow_budget_reserved_usd",
    "USD reserved for in-flight jobs",
    registry=REGISTRY,
)

# ── Circuit breaker ───────────────────────────────────────────────────────────

circuit_breaker_state = Gauge(
    "qflow_circuit_breaker_open",
    "1 = circuit open (provider blocked), 0 = closed",
    ["provider"],
    registry=REGISTRY,
)