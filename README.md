# qflow — Smart AI Task Scheduler

A lightweight, provider-agnostic async scheduler for AI workloads.
Sits between your app and any AI API — queues heavy calls, enforces budget limits,
and gives you a live Grafana view of every job from submission to completion.

---

## Why qflow?

AI APIs are slow, expensive, and unpredictable. Calling them synchronously from a
web handler will eventually time out, drain your wallet, or both. qflow solves this:

- **Async by default** — your app gets a `job_id` instantly, never blocks on AI
- **Priority-aware** — real-time voice (TTS/STT) jumps the queue over batch jobs
- **Budget throttling** — spend limits enforced at the scheduler level, not per-call
- **Circuit breaker** — flaky providers get isolated before they cascade failures
- **Observable** — Prometheus + Grafana out of the box, zero dashboard setup

---

## Quickstart

```bash
# 1. Clone and configure
git clone https://github.com/yourname/qflow
cd qflow
cp .env.example .env
# edit .env — add your API keys and set TOTAL_BUDGET_USD

# 2. Start the stack
docker-compose up

# 3. Run the demo (fires 18 jobs across all priority lanes)
python scripts/demo.py

# 4. Watch the dashboard
open http://localhost:3000   # Grafana (admin/admin)
```

---

## How it works

```
Your App
   │
   ▼  POST /tasks  { action, input }
┌─────────────────────────────────────────────────────┐
│  qflow API (FastAPI)                                │
│  • Infers priority from action type                 │
│  • Estimates cost from price_map.json               │
│  • Returns 202 + job_id immediately                 │
└─────────────────┬───────────────────────────────────┘
                  │ enqueue
                  ▼
         Redis Priority Queues
         ┌──────────┐  ┌────────────┐  ┌───────┐
         │  HIGH    │  │  MEDIUM    │  │  LOW  │
         │ tts/stt  │  │ llm/trans  │  │ embed │
         └──────────┘  └────────────┘  └───────┘
                  │ dequeue (highest first)
                  ▼
┌─────────────────────────────────────────────────────┐
│  Worker                                             │
│  • Budget check → throttle if limit approaching     │
│  • Circuit breaker → skip unhealthy providers       │
│  • Execute → provider API call                      │
│  • Settle budget (reserved → spent)                 │
│  • Webhook callback (MEDIUM priority)               │
└─────────────────────────────────────────────────────┘
                  │ metrics
                  ▼
         Prometheus → Grafana
```

---

## Priority lanes

| Priority | Actions             | Caller gets                          |
|----------|---------------------|--------------------------------------|
| HIGH     | `tts`, `stt`        | Poll `GET /jobs/{id}` for result     |
| MEDIUM   | `llm_inference`, `translation`, `transliteration` | Webhook callback when done |
| LOW      | `embedding`, `image_generation` | Grafana link, fire-and-forget |

---

## API

### Submit a task
```
POST /tasks
{
  "action": "tts",
  "input": { "text": "नमस्ते", "language_code": "hi-IN" },
  "webhook_url": null
}
```
Response `202`:
```json
{
  "job_id": "abc-123",
  "status": "queued",
  "priority": "high",
  "queue_position": 2,
  "estimated_cost_usd": 0.000021,
  "budget_remaining_usd": 4.823,
  "tracker_url": "http://localhost:8000/jobs/abc-123",
  "message": "Queued as HIGH priority. Budget remaining: $4.8230"
}
```

### Poll job status
```
GET /jobs/{job_id}
```

### Queue + budget health
```
GET /queue/status
```

---

## Budget throttling

Configured in `scheduler/config/priority_rules.json`:

| Budget remaining | Effect                          |
|------------------|---------------------------------|
| < 20%            | LOW priority jobs throttled     |
| < 5%             | MEDIUM priority jobs throttled  |
| < 1%             | Hard stop — all jobs blocked    |

Throttled jobs are re-queued with backoff (not dropped).

---

## Circuit breaker

Each provider gets its own breaker:
- **Trips** after 3 consecutive failures
- **Stays open** for 60 seconds
- **Auto-resets** after recovery window

Visible in Grafana as `qflow_circuit_breaker_open{provider="sarvam"}`.

---

## Adding a provider

1. Create `scheduler/providers/your_provider.py` implementing `BaseProvider`
2. Register it in `scheduler/providers/__init__.py`
3. Add pricing to `scheduler/config/price_map.json`
4. Add to fallback chain in `scheduler/config/priority_rules.json`

---

## Supported providers (current)

| Provider    | Actions                              |
|-------------|--------------------------------------|
| Sarvam AI   | tts, stt, translation, transliteration |
| OpenAI      | llm_inference, embedding, image_generation, stt, tts |
| Anthropic   | llm_inference                        |
| ElevenLabs  | tts                                  |
| Replicate   | image_generation                     |
| Ollama      | llm_inference, embedding (local/free)|

---

## GitHub Action (batch mode)

Runs nightly or on-demand. Submits batch jobs, waits for completion,
exits non-zero if any job fails — CI-friendly.

```yaml
# Trigger manually from GitHub Actions UI:
# action: translation
# budget_usd: 1.0
```

See `.github/workflows/batch.yml`.

---

## Project structure

```
qflow/
├── scheduler/
│   ├── main.py          # FastAPI app, routes, startup
│   ├── worker.py        # Background job runner, circuit breaker
│   ├── models.py        # Pydantic models (Job, TaskRequest, etc.)
│   ├── queue.py         # Redis queue operations
│   ├── budget.py        # Token-bucket cost controller
│   ├── router.py        # Priority + provider inference
│   ├── metrics.py       # Prometheus metric definitions
│   ├── providers/
│   │   ├── base.py      # Abstract BaseProvider
│   │   ├── sarvam.py    # Sarvam AI
│   │   └── __init__.py  # Provider registry
│   └── config/
│       ├── price_map.json       # Cost per unit per provider/action
│       └── priority_rules.json  # Priority inference + throttle thresholds
├── scripts/
│   ├── demo.py          # Load tester / demo script
│   └── run_batch.py     # Batch runner (used by GitHub Action)
├── prometheus/
│   └── prometheus.yml
├── grafana/
│   ├── dashboards/qflow.json
│   └── provisioning/
├── .github/workflows/batch.yml
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```