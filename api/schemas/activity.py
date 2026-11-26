"""api/schemas/activity.py -- models for the activity log and daily GPU usage routes (api/routers/activity_routes.py)."""

from typing import List, Optional

from pydantic import BaseModel

__all__ = ["ActivityItem", "ActivityList", "GpuDay", "GpuDaily"]


class ActivityItem(BaseModel):
    id: int
    label: str
    kind: str
    outcome: str       # done | error | cancelled
    started_at: float
    ended_at: float
    seconds: float
    gpu: bool
    mine: bool


class ActivityList(BaseModel):
    items: List[ActivityItem]
    next_before_id: Optional[int] = None


class GpuDay(BaseModel):
    day: str
    gpu_seconds: float
    jobs: int


class GpuDaily(BaseModel):
    days: List[GpuDay]
    total_gpu_seconds: float
