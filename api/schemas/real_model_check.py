"""Pydantic models for api/routers/real_model_check_routes.py."""

from typing import List, Literal, Optional

from pydantic import BaseModel

from api.schemas.common import ConfirmRequest, JobStarted, JobState

__all__ = [
    "RealModelCheckJob",
    "RealModelCheckResult",
    "RealModelCheckStart",
    "RealModelCheckStarted",
    "RealModelCheckState",
]


class RealModelCheckJob(JobState):
    pass


class RealModelCheckStart(ConfirmRequest):
    pass


class RealModelCheckResult(BaseModel):
    id: Literal["asr", "ocr", "translate"]
    label: str
    status: Literal["pass", "fail", "skipped", "could_not_check"]
    reason: str


class RealModelCheckState(BaseModel):
    job_id: str
    job: Optional[RealModelCheckJob] = None
    checks: List[RealModelCheckResult]
    finished: bool


class RealModelCheckStarted(JobStarted):
    pass
