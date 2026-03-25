from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional, Any
import uuid
from datetime import datetime


class Provider(str, Enum):
    SARVAM = "sarvam"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    ELEVENLABS = "elevenlabs"
    REPLICATE = "replicate"
    OLLAMA = "ollama"           # local / free
    MOCK_FLAKY  = "mock_flaky"  # demo/testing only
    MOCK_STABLE = "mock_stable" # demo/testing only


class ActionType(str, Enum):
    # Speech
    TTS = "tts"
    STT = "stt"
    # Language
    LLM_INFERENCE = "llm_inference"
    TRANSLATION = "translation"
    TRANSLITERATION = "transliteration"
    # Vision / Media
    IMAGE_GENERATION = "image_generation"
    # Data
    EMBEDDING = "embedding"


class Priority(str, Enum):
    HIGH = "high"       # pseudo-sync: poll for result
    MEDIUM = "medium"   # async: webhook callback
    LOW = "low"         # fire-and-forget: grafana only


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    THROTTLED = "throttled"   # budget exhausted, waiting
    DEAD = "dead"             # circuit breaker killed it


# ── Inbound ──────────────────────────────────────────────────────────────────

class TaskRequest(BaseModel):
    provider: Optional[Provider] = None          # optional: scheduler can infer
    action: ActionType
    input: Any                                   # text, audio bytes (b64), etc.
    priority_override: Optional[Priority] = None # caller can override inferred priority
    webhook_url: Optional[str] = None            # required for MEDIUM priority
    metadata: dict = Field(default_factory=dict) # pass-through caller context


# ── Internal job (stored in Redis) ───────────────────────────────────────────

class Job(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: Provider
    action: ActionType
    priority: Priority
    input: Any
    webhook_url: Optional[str] = None
    metadata: dict = Field(default_factory=dict)

    status: JobStatus = JobStatus.QUEUED
    queue_position: Optional[int] = None
    estimated_cost_usd: float = 0.0
    actual_cost_usd: float = 0.0
    result: Optional[Any] = None
    error: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0


# ── Outbound ─────────────────────────────────────────────────────────────────

class TaskAccepted(BaseModel):
    job_id: str
    status: JobStatus
    priority: Priority
    priority_source: str         # "inferred" | "override" — transparency for caller
    queue_position: Optional[int]
    estimated_cost_usd: float
    budget_remaining_usd: float
    tracker_url: str             # poll endpoint or grafana link
    message: str