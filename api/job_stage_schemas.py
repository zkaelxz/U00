"""
api/job_stage_schemas.py -- models for the per-stage job timing
route (api/routers/job_stage_routes.py). Stage names are fixed labels from
Baihe's own code.
"""

from typing import List

from pydantic import BaseModel


class JobStage(BaseModel):
    stage: str
    started_at: float
    duration_seconds: float
    cost_usd: float


class JobStageRun(BaseModel):
    run_started_at: float
    running: bool
    total_seconds: float
    cost_usd: float
    stages: List[JobStage]


class JobStageTimings(BaseModel):
    job_id: str
    runs: List[JobStageRun]
