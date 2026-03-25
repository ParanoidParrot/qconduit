"""
scripts/run_batch.py

Batch job runner — used by GitHub Action (and can be run locally).
Reads input from a JSONL file or generates synthetic jobs for testing.

Usage:
  python scripts/run_batch.py --action translation --budget 1.0
  python scripts/run_batch.py --action embedding --budget 2.0 --input-file data/items.jsonl
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qflow.batch")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


# ── Synthetic job generators (used when no input file given) ──────────────────

SYNTHETIC_INPUTS = {
    "translation": [
        {"input": "नमस्ते, आप कैसे हैं?", "metadata": {"lang": "hi"}},
        {"input": "வணக்கம், நீங்கள் எப்படி இருக்கிறீர்கள்?", "metadata": {"lang": "ta"}},
        {"input": "నమస్కారం, మీరు ఎలా ఉన్నారు?", "metadata": {"lang": "te"}},
        {"input": "ಹಲೋ, ನೀವು ಹೇಗಿದ್ದೀರಿ?", "metadata": {"lang": "kn"}},
        {"input": "ہیلو، آپ کیسے ہیں؟", "metadata": {"lang": "ur"}},
    ],
    "embedding": [
        {"input": "The quick brown fox jumps over the lazy dog"},
        {"input": "Machine learning is a subset of artificial intelligence"},
        {"input": "Redis is an in-memory data structure store"},
        {"input": "FastAPI is a modern web framework for Python"},
        {"input": "Prometheus scrapes metrics from instrumented targets"},
    ],
    "tts": [
        {"input": {"text": "मुंबई में आपका स्वागत है", "language_code": "hi-IN"}},
        {"input": {"text": "Chennai Central station", "language_code": "ta-IN"}},
        {"input": {"text": "Bengaluru city traffic update", "language_code": "kn-IN"}},
    ],
}


def load_jobs(action: str, input_file: str | None) -> list[dict]:
    if input_file:
        path = Path(input_file)
        if not path.exists():
            logger.error(f"Input file not found: {input_file}")
            sys.exit(1)
        jobs = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    jobs.append(json.loads(line))
        logger.info(f"Loaded {len(jobs)} jobs from {input_file}")
        return jobs

    jobs = SYNTHETIC_INPUTS.get(action, [])
    if not jobs:
        logger.error(f"No synthetic inputs defined for action: {action}")
        sys.exit(1)
    logger.info(f"Using {len(jobs)} synthetic jobs for action: {action}")
    return jobs


# ── Budget pre-flight check ───────────────────────────────────────────────────

async def check_budget(client: httpx.AsyncClient, max_budget: float) -> bool:
    resp = await client.get(f"{API_BASE}/queue/status")
    resp.raise_for_status()
    data = resp.json()
    remaining = data["budget"]["remaining_usd"]

    logger.info(f"Budget remaining: ${remaining:.4f} / requested batch budget: ${max_budget:.4f}")

    if remaining < max_budget:
        logger.warning(
            f"Available budget (${remaining:.4f}) is less than requested (${max_budget:.4f}). "
            "Proceeding with available budget."
        )
    if remaining <= 0:
        logger.error("No budget remaining — aborting batch run.")
        return False
    return True


# ── Job submission ────────────────────────────────────────────────────────────

async def submit_job(client: httpx.AsyncClient, action: str, payload: dict) -> dict | None:
    body = {
        "action": action,
        "input":  payload.get("input", payload),
        "metadata": payload.get("metadata", {}),
    }
    try:
        resp = await client.post(f"{API_BASE}/tasks", json=body)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to submit job: {e}")
        return None


async def poll_until_done(client: httpx.AsyncClient, job_id: str, timeout: int = 120) -> dict:
    """Poll job status until terminal state or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = await client.get(f"{API_BASE}/jobs/{job_id}")
        data = resp.json()
        status = data.get("status")

        if status in ("completed", "failed", "dead"):
            return data

        await asyncio.sleep(1)

    return {"status": "timeout", "job_id": job_id}


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(action: str, budget: float, input_file: str | None, concurrency: int):
    jobs = load_jobs(action, input_file)

    async with httpx.AsyncClient(timeout=30) as client:
        # Pre-flight budget check
        ok = await check_budget(client, budget)
        if not ok:
            sys.exit(1)

        # Submit all jobs
        logger.info(f"Submitting {len(jobs)} jobs (action={action}, concurrency={concurrency})")
        semaphore = asyncio.Semaphore(concurrency)

        async def submit_with_limit(payload):
            async with semaphore:
                return await submit_job(client, action, payload)

        submissions = await asyncio.gather(*[submit_with_limit(j) for j in jobs])
        accepted = [s for s in submissions if s]

        logger.info(f"Accepted: {len(accepted)} / {len(jobs)} jobs")
        for s in accepted:
            logger.info(
                f"  job_id={s['job_id']} priority={s['priority']} "
                f"est_cost=${s['estimated_cost_usd']:.6f} tracker={s['tracker_url']}"
            )

        # Poll for results (batch waits for completion)
        logger.info("Polling for results...")
        results = await asyncio.gather(*[
            poll_until_done(client, s["job_id"]) for s in accepted
        ])

        # Summary
        completed = sum(1 for r in results if r.get("status") == "completed")
        failed    = sum(1 for r in results if r.get("status") in ("failed", "dead"))
        total_cost = sum(r.get("actual_cost", 0) or 0 for r in results)

        logger.info("=" * 50)
        logger.info(f"Batch complete: {completed} succeeded / {failed} failed")
        logger.info(f"Total actual cost: ${total_cost:.6f}")
        logger.info("=" * 50)

        # Exit non-zero if any job failed (useful for CI)
        if failed > 0:
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="qflow batch runner")
    parser.add_argument("--action",     required=True, help="AI action type")
    parser.add_argument("--budget",     type=float, default=1.0, help="Max spend (USD)")
    parser.add_argument("--input-file", default=None, help="JSONL file of job inputs")
    parser.add_argument("--concurrency", type=int, default=5, help="Parallel submissions")
    args = parser.parse_args()

    asyncio.run(main(args.action, args.budget, args.input_file, args.concurrency))