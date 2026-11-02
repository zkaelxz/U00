"""Schemas for Diagnostics' voice engines that install into a venv of their own
(api/routers/diagnostics_voice_engine_routes.py). Booleans, sizes in MB and status
text only: no folder path."""

from typing import List, Optional

from pydantic import BaseModel, Field

from api.schemas.common import ConfirmRequest, JobStarted, JobState

__all__ = [
    "VoiceEngineInstallJob",
    "VoiceEngineInstallResult",
    "VoiceEngineInstallRow",
    "VoiceEngineInstallStatus",
    "VoiceEngineInstallStarted",
    "VoiceEngineInstallRequest",
    "VoiceEngineRemoveStarted",
    "VoiceEngineRemoveRequest",
]


class VoiceEngineInstallJob(JobState):
    pass


class VoiceEngineInstallResult(BaseModel):
    ok: bool
    message: str
    output_tail: List[str] = Field(description="A few redacted lines of pip's output.")


class VoiceEngineInstallRow(BaseModel):
    engine: str
    label: str
    installed: bool
    folder_present: bool = Field(description="Its folder exists, ready or not (a damaged install).")
    required_mb: int
    refusal: Optional[str] = Field(None, description="Why the install can't start now, or null.")
    job_id: str
    job: Optional[VoiceEngineInstallJob] = None
    last_result: Optional[VoiceEngineInstallResult] = None


class VoiceEngineInstallStatus(BaseModel):
    free_mb: int
    engines: List[VoiceEngineInstallRow]


class VoiceEngineInstallStarted(JobStarted):
    pass


class VoiceEngineInstallRequest(ConfirmRequest):
    pass


class VoiceEngineRemoveStarted(JobStarted):
    pass


class VoiceEngineRemoveRequest(ConfirmRequest):
    pass
