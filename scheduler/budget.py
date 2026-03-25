"""
Budget controller — token bucket based on $ spend.

Redis keys:
  budget:total          float   configured max spend (USD)
  budget:spent          float   cumulative spend so far
  budget:reserved       float   cost of in-flight jobs (not yet settled)
"""

import json
from pathlib import Path
from typing import Tuple
import redis.asyncio as redis

from scheduler.models import ActionType, Provider

# ── Load config ───────────────────────────────────────────────────────────────

_PRICE_MAP_PATH = Path(__file__).parent / "config" / "price_map.json"
_RULES_PATH = Path(__file__).parent / "config" / "priority_rules.json"

with open(_PRICE_MAP_PATH) as f:
    PRICE_MAP = json.load(f)["providers"]

with open(_RULES_PATH) as f:
    _rules = json.load(f)
    THROTTLE_RULES = _rules["budget_throttle_thresholds"]


# ── Cost estimation ───────────────────────────────────────────────────────────

def estimate_cost(provider: Provider, action: ActionType, input_payload: any) -> float:
    """
    Rough pre-flight cost estimate before the job runs.
    Actual cost is settled from API response usage fields.
    """
    try:
        spec = PRICE_MAP[provider.value][action.value]
    except KeyError:
        return 0.0

    unit = spec["unit"]
    rate = spec["cost_per_unit"]

    if unit == "char":
        size = len(str(input_payload))
    elif unit == "token":
        size = len(str(input_payload)) / 4   # rough tiktoken approximation
    elif unit == "second":
        # input_payload expected to carry {"duration_seconds": N}
        size = input_payload.get("duration_seconds", 30) if isinstance(input_payload, dict) else 30
    elif unit == "image":
        size = 1
    else:
        size = 1

    return round(size * rate, 6)


# ── Budget state (Redis-backed) ───────────────────────────────────────────────

class BudgetController:
    def __init__(self, r: redis.Redis, total_budget_usd: float):
        self.r = r
        self.total = total_budget_usd

    async def initialize(self):
        """Seed budget keys if not already set."""
        exists = await self.r.exists("budget:total")
        if not exists:
            await self.r.set("budget:total", self.total)
            await self.r.set("budget:spent", 0.0)
            await self.r.set("budget:reserved", 0.0)

    async def get_state(self) -> dict:
        total    = float(await self.r.get("budget:total") or self.total)
        spent    = float(await self.r.get("budget:spent") or 0)
        reserved = float(await self.r.get("budget:reserved") or 0)
        used     = spent + reserved
        remaining = max(0.0, total - used)
        pct_remaining = (remaining / total * 100) if total > 0 else 0

        return {
            "total_usd":      round(total, 4),
            "spent_usd":      round(spent, 4),
            "reserved_usd":   round(reserved, 4),
            "remaining_usd":  round(remaining, 4),
            "pct_remaining":  round(pct_remaining, 2),
        }

    async def can_proceed(self, priority: str, estimated_cost: float) -> Tuple[bool, str]:
        """
        Returns (allowed, reason).
        Checks budget thresholds from priority_rules.json.
        """
        state = await self.get_state()
        pct = state["pct_remaining"]

        if pct <= THROTTLE_RULES["hard_stop_at_pct"]:
            return False, f"Budget critically low ({pct:.1f}% remaining) — hard stop"

        if priority == "low" and pct <= THROTTLE_RULES["throttle_low_priority_at_pct"]:
            return False, f"Budget at {pct:.1f}% — throttling LOW priority tasks"

        if priority == "medium" and pct <= THROTTLE_RULES["throttle_medium_priority_at_pct"]:
            return False, f"Budget at {pct:.1f}% — throttling MEDIUM priority tasks"

        if estimated_cost > state["remaining_usd"]:
            return False, "Estimated cost exceeds remaining budget"

        return True, "ok"

    async def reserve(self, job_id: str, amount: float):
        """Hold estimated cost while job is in-flight."""
        await self.r.incrbyfloat("budget:reserved", amount)
        await self.r.set(f"budget:reserved:{job_id}", amount)

    async def settle(self, job_id: str, actual_cost: float):
        """Release reservation; add actual cost to spent."""
        reserved = float(await self.r.get(f"budget:reserved:{job_id}") or 0)
        await self.r.incrbyfloat("budget:reserved", -reserved)
        await self.r.incrbyfloat("budget:spent", actual_cost)
        await self.r.delete(f"budget:reserved:{job_id}")