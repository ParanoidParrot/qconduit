"""
scripts/demo.py

Demo script — fires a burst of jobs across priorities and actions.
Designed to make the Grafana dashboard light up:
  - Queue depth rising then draining
  - Budget meter ticking down
  - Throttling kicking in as budget depletes
  - All three priority lanes active simultaneously

Usage:
  python scripts/demo.py                    # default burst
  python scripts/demo.py --jobs 30          # larger burst
  python scripts/demo.py --tight-budget     # set $0.10 budget to trigger throttling fast
"""

import argparse
import asyncio
import json
import logging
import os
import random

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qflow.demo")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")

# ── Demo job templates ────────────────────────────────────────────────────────
# Mix of actions to exercise all priority lanes:
#   HIGH   → stt, tts
#   MEDIUM → llm_inference, translation
#   LOW    → embedding, image_generation

JOB_TEMPLATES = [
    # HIGH priority
    {"action": "tts",  "input": {"text": "मुंबई सेंट्रल स्टेशन", "language_code": "hi-IN"},
     "label": "TTS [HIGH]"},
    {"action": "stt",  "input": {"audio_b64": "base64==", "duration_seconds": 8, "language_code": "ta-IN"},
     "label": "STT [HIGH]"},
    {"action": "tts",  "input": {"text": "Bengaluru traffic update", "language_code": "kn-IN"},
     "label": "TTS [HIGH]"},

    # MEDIUM priority
    {"action": "translation",  "input": "Hello, how are you today?",
     "label": "Translation [MEDIUM]"},
    {"action": "llm_inference", "input": "Summarise the key benefits of async task queues",
     "label": "LLM [MEDIUM]"},
    {"action": "translation",  "input": "The weather in Chennai is hot and humid",
     "label": "Translation [MEDIUM]"},

    # LOW priority
    {"action": "embedding",        "input": "Distributed systems require careful fault tolerance design",
     "label": "Embedding [LOW]"},
    {"action": "image_generation", "input": {"prompt": "A futuristic Indian city skyline at dusk"},
     "label": "ImageGen [LOW]"},
    {"action": "embedding",        "input": "Redis sorted sets are perfect for leaderboards",
     "label": "Embedding [LOW]"},
]


async def fire_jobs(client: httpx.AsyncClient, n: int):
    """Submit n jobs by cycling through templates."""
    submitted = []

    for i in range(n):
        template = JOB_TEMPLATES[i % len(JOB_TEMPLATES)]
        body = {
            "action":   template["action"],
            "input":    template["input"],
            "metadata": {"demo": True, "seq": i},
        }

        try:
            resp = await client.post(f"{API_BASE}/tasks", json=body)
            resp.raise_for_status()
            data = resp.json()
            submitted.append(data)
            logger.info(
                f"[{i+1:02d}/{n}] {template['label']:<22} "
                f"job_id={data['job_id'][:8]}... "
                f"pos={data['queue_position']} "
                f"budget_left=${data['budget_remaining_usd']:.4f}"
            )
        except httpx.HTTPStatusError as e:
            logger.warning(f"[{i+1:02d}/{n}] Rejected: {e.response.text}")
        except Exception as e:
            logger.error(f"[{i+1:02d}/{n}] Error: {e}")

        # Small stagger so queue depth is visible in Grafana
        await asyncio.sleep(0.2)

    return submitted


async def watch_queue(client: httpx.AsyncClient, interval: float = 2.0, rounds: int = 15):
    """Print queue + budget state every interval seconds."""
    logger.info("\n── Queue monitor (Ctrl+C to stop) ──────────────────────────")
    for _ in range(rounds):
        try:
            resp = await client.get(f"{API_BASE}/queue/status")
            data = resp.json()
            q = data["queues"]
            b = data["budget"]
            logger.info(
                f"  Queues  high={q['high']} medium={q['medium']} low={q['low']}  │  "
                f"Budget  spent=${b['spent_usd']:.4f}  "
                f"reserved=${b['reserved_usd']:.4f}  "
                f"remaining=${b['remaining_usd']:.4f} ({b['pct_remaining']:.1f}%)"
            )
        except Exception as e:
            logger.warning(f"Status check failed: {e}")
        await asyncio.sleep(interval)


async def main(n_jobs: int, tight_budget: bool):
    if tight_budget:
        logger.info("Setting tight budget ($0.10) to trigger throttling quickly...")
        # This env var would need to be set before the API starts;
        # for the demo we just warn the user.
        logger.warning(
            "To use tight budget mode: set TOTAL_BUDGET_USD=0.10 in .env and restart.\n"
            "  echo 'TOTAL_BUDGET_USD=0.10' >> .env && docker-compose restart qflow-api"
        )

    logger.info(f"\nqflow demo — firing {n_jobs} jobs")
    logger.info(f"API: {API_BASE}")
    logger.info(f"Grafana: http://localhost:3000  (admin/admin)\n")

    async with httpx.AsyncClient(timeout=15) as client:
        # Health check first
        try:
            resp = await client.get(f"{API_BASE}/health")
            resp.raise_for_status()
            logger.info(f"API healthy: {resp.json()}\n")
        except Exception as e:
            logger.error(f"API not reachable at {API_BASE}: {e}")
            logger.error("Start the stack first:  docker-compose up")
            return

        # Fire jobs
        submitted = await fire_jobs(client, n_jobs)
        logger.info(f"\nSubmitted {len(submitted)}/{n_jobs} jobs. "
                    f"Open Grafana to watch them drain → http://localhost:3000\n")

        # Watch the queue drain
        await watch_queue(client, interval=2.0, rounds=20)

    logger.info("Demo complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="qflow demo load tester")
    parser.add_argument("--jobs",         type=int,  default=18,    help="Number of jobs to fire")
    parser.add_argument("--tight-budget", action="store_true",       help="Trigger throttling")
    args = parser.parse_args()

    asyncio.run(main(args.jobs, args.tight_budget))