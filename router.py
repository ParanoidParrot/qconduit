"""
Router — infers priority and best provider for a task.
No user input needed; scheduler decides based on priority_rules.json.
"""

import json
from pathlib import Path

from scheduler.models import ActionType, Priority, Provider, TaskRequest

_RULES_PATH = Path(__file__).parent / "config" / "priority_rules.json"

with open(_RULES_PATH) as f:
    _rules = json.load(f)

_PRIORITY_MAP: dict[str, Priority] = {}
for p, spec in _rules["priority_rules"].items():
    for action in spec["actions"]:
        _PRIORITY_MAP[action] = Priority(p)

_FALLBACK_CHAIN: dict[str, list[str]] = _rules["provider_fallback_chain"]


def infer_priority(action: ActionType) -> Priority:
    """Map action type → priority. Defaults to LOW if unknown."""
    return _PRIORITY_MAP.get(action.value, Priority.LOW)


def infer_provider(action: ActionType, requested_provider: Provider | None) -> Provider:
    """
    If caller specified a provider, respect it.
    Otherwise pick first in the fallback chain for this action.
    (Later: check circuit breaker state to skip unhealthy providers.)
    """
    if requested_provider is not None:
        return requested_provider

    chain = _FALLBACK_CHAIN.get(action.value, [])
    if not chain:
        raise ValueError(f"No provider configured for action: {action.value}")

    return Provider(chain[0])


def build_job_params(request: TaskRequest) -> tuple[Priority, Provider]:
    """Single entry point: returns (priority, provider) for a request."""
    priority = infer_priority(request.action)
    provider = infer_provider(request.action, request.provider)
    return priority, provider