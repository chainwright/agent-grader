from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TaskIn(BaseModel):
    slug: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9-]*$")
    prompt: str = Field(min_length=1)
    rubric: list[dict[str, Any]] = Field(min_length=1)


class TaskOut(BaseModel):
    id: int
    slug: str
    prompt: str
    rubric: list[dict[str, Any]]
    created_at: datetime

    model_config = {"from_attributes": True}


class SubmissionIn(BaseModel):
    agent: str = Field(min_length=1, max_length=120)
    output: str
    latency_ms: Optional[float] = Field(default=None, ge=0)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class CheckOut(BaseModel):
    kind: str
    passed: bool
    detail: str
    weight: float


class SubmissionOut(BaseModel):
    id: int
    task_id: int
    agent: str
    passed: bool
    score: float
    checks: list[CheckOut]
    latency_ms: Optional[float]
    created_at: datetime
    duplicate_of: Optional[int] = None

    model_config = {"from_attributes": True}


class FailureBreakdown(BaseModel):
    kind: str
    failures: int


class StatsOut(BaseModel):
    task_id: int
    slug: str
    submissions: int
    passed: int
    pass_rate: float
    mean_score: float
    p95_latency_ms: Optional[float]
    failures_by_check: list[FailureBreakdown]
