"""
scripts/demo_circuit_breaker.py

Demonstrates the circuit breaker tripping in real time.

What you'll see in Grafana:
  1. Jobs start processing normally
  2. After 3 failures, qflow_circuit_breaker_open{provider="mock_flaky"} flips to 1
  3. Subsequent jobs get re-queued instead of hitting the dead provider
  4. After 60s recovery window, breaker resets and jobs flow again

Usage:
  # Start the stack first
  docker-compose up -d

  # Then run this demo
  python scripts/demo_circuit_breaker.py

  # Watch Grafana at http://localhost:3000
"""

import asyncio
import logging
import os
import sys

import httpx

# Register mock providers by patching the registry before importing the app
# (This is the demo-only approach — in prod you'd add real providers)
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("qflow.cb_demo")

API_BASE = os.getenv("API_BASE_URL", "http://localhost:8000")


async def inject_mock_providers():
    """
    Tell the running API to register the mock flaky provider.
    We do this by hitting a special demo-only endpoint.
    If the endpoint isn't available, print instructions for manual setup.
    """
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.post(f"{API_BASE}/demo/register-flaky")
            if resp.status_code == 200:
                logger.info("Mock flaky provider registered via API.")
                return True
        except Exception:
            pass

    logger.warning(
        "\n" + "="*60 +
        "\nTo run this demo, temporarily patch providers/__init__.py:\n"
        "\n  from scheduler.providers.mock_providers import MockFlakyProvider"
        "\n  _REGISTRY['mock_flaky'] = MockFlakyProvider(failure_rate=0.7)"
        "\n\nThen add to priority_rules.json fallback chains:"
        "\n  'tts': ['mock_flaky', 'sarvam', ...]"
        "\n\nOr use the --inject flag: python scripts/demo_circuit_breaker.py --inject"
        "\n" + "="*60
    )
    return False


async def fire_jobs_at_flaky_provider(client: httpx.AsyncClient, count: int):
    """
    Fire jobs that will route through mock_flaky provider.
    We force the provider by passing it explicitly in the request.

    Since user-override isn't wired yet, we use the internal test endpoint.
    Falls back to standard /tasks if not available.
    """
    results = []
    for i in range(count):
        try:
            # Try internal test endpoint first (bypasses provider inference)
            resp = await client.post(
                f"{API_BASE}/demo/task",
                json={
                    "provider": "mock_flaky",
                    "action":   "tts",
                    "input":    {"text": f"Circuit breaker test job {i+1}", "language_code": "hi-IN"},
                }
            )
            if resp.status_code == 404:
                # Fall back to standard endpoint
                resp = await client.post(
                    f"{API_BASE}/tasks",
                    json={
                        "action": "tts",
                        "input":  {"text": f"Circuit breaker test job {i+1}", "language_code": "hi-IN"},
                    }
                )
            resp.raise_for_status()
            data = resp.json()
            results.append(data)
            logger.info(
                f"[{i+1:02d}/{count}] Submitted → job_id={data['job_id'][:8]}... "
                f"budget_left=${data['budget_remaining_usd']:.4f}"
            )
        except Exception as e:
            logger.error(f"[{i+1:02d}/{count}] Submit failed: {e}")

        await asyncio.sleep(0.5)  # stagger so failures are visible in Grafana timeline

    return results


async def watch_circuit_breaker(client: httpx.AsyncClient, rounds: int = 30):
    """Poll queue status and look for circuit breaker signals."""
    logger.info("\n── Watching for circuit breaker ────────────────────────────")
    logger.info("Expected sequence:")
    logger.info("  1. Jobs processing → some failing")
    logger.info("  2. After 3 failures → circuit trips (visible in Grafana)")
    logger.info("  3. Jobs re-queue with THROTTLED status")
    logger.info("  4. After 60s → circuit resets, jobs resume")
    logger.info("────────────────────────────────────────────────────────────\n")

    for i in range(rounds):
        try:
            resp = await client.get(f"{API_BASE}/queue/status")
            data = resp.json()
            q = data["queues"]
            b = data["budget"]
            logger.info(
                f"[{i+1:02d}] "
                f"queues: high={q['high']} med={q['medium']} low={q['low']}  │  "
                f"budget: {b['pct_remaining']:.1f}% remaining"
            )
        except Exception as e:
            logger.warning(f"Status check failed: {e}")

        await asyncio.sleep(2)


async def main(inject: bool, jobs: int):
    logger.info("qconduit — Circuit Breaker Demo")
    logger.info(f"API: {API_BASE}")
    logger.info(f"Grafana: http://localhost:3000\n")
    logger.info("Watch panel: 'Circuit Breakers Open' and 'Provider Latency p95'\n")

    async with httpx.AsyncClient(timeout=15) as client:
        # Health check
        try:
            resp = await client.get(f"{API_BASE}/health")
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"API not reachable: {e}")
            logger.error("Run: docker-compose up")
            return

        if inject:
            ok = await inject_mock_providers()
            if not ok:
                logger.warning("Continuing without mock provider injection...")

        logger.info(f"Firing {jobs} jobs to trigger circuit breaker...\n")
        submitted = await fire_jobs_at_flaky_provider(client, jobs)
        logger.info(f"\n{len(submitted)} jobs submitted. Monitoring...\n")

        await watch_circuit_breaker(client, rounds=30)

    logger.info(
        "\nDemo done. Key things to check in Grafana:\n"
        "  • qflow_circuit_breaker_open{provider='mock_flaky'} should have hit 1\n"
        "  • qflow_jobs_failed_total should show failures\n"
        "  • qflow_jobs_throttled_total should spike when circuit is open\n"
        "  • Provider latency p95 shows the slow/failing calls before trip"
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Circuit breaker demo")
    parser.add_argument("--inject", action="store_true",
                        help="Attempt to register mock provider via API")
    parser.add_argument("--jobs", type=int, default=15,
                        help="Number of jobs to fire")
    args = parser.parse_args()
    asyncio.run(main(args.inject, args.jobs))