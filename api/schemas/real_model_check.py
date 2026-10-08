"""Pydantic models for api/routers/real_model_check_routes.py."""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictBool

__all__ = [
    "RealModelCheckJob",
    "RealModelCheckResult",
    "RealModelCheckStart",
    "RealModelCheckStarted",
    "RealModelCheckState",
]


class RealModelCheckJob(BaseModel):
    status: Optional[str] = None
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None


class RealModelCheckStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


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


class RealModelCheckStarted(BaseModel):
    job_id: str
    started: bool
