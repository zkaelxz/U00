"""Schemas for Diagnostics > "Install ffmpeg" (api/routers/diagnostics_ffmpeg_routes.py). Booleans, sizes in MB and status text only: no folder path or URL."""

from typing import Optional

from pydantic import BaseModel, Field

from api.schemas.common import ConfirmRequest, JobStarted, JobState

__all__ = [
    "FfmpegInstallJob",
    "FfmpegInstallResult",
    "FfmpegInstallStatus",
    "FfmpegInstallStarted",
    "FfmpegInstallRequest",
]


class FfmpegInstallJob(JobState):
    pass


class FfmpegInstallResult(BaseModel):
    ok: bool
    message: str


class FfmpegInstallStatus(BaseModel):
    found: bool = Field(description="An ffmpeg the app can run, from the PC or its own folder.")
    libass: Optional[bool] = Field(None, description="Whether it can burn subtitles in; null if unknown.")
    app_ffmpeg_installed: bool
    version: str = Field(description="The build this app downloads.")
    download_mb: int
    free_mb: int
    required_mb: int
    refusal: Optional[str] = Field(None, description="Why the install can't start now, or null.")
    job_id: str
    job: Optional[FfmpegInstallJob] = None
    last_result: Optional[FfmpegInstallResult] = None


class FfmpegInstallStarted(JobStarted):
    pass


class FfmpegInstallRequest(ConfirmRequest):
    pass
