"""Schemas for Diagnostics > "Install browser support"
(api/routers/diagnostics_browser_routes.py). Booleans, sizes in MB and
status text only: no folder path, URL or secret."""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = [
    "BrowserInstallJob",
    "BrowserInstallResult",
    "BrowserInstallStatus",
    "BrowserInstallStarted",
    "BrowserInstallRequest",
]


class BrowserInstallJob(BaseModel):
    status: Optional[str] = None
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None


class BrowserInstallResult(BaseModel):
    ok: bool
    message: str
    output_tail: List[str] = Field(description="A few redacted lines of the installer's output.")


class BrowserInstallStatus(BaseModel):
    playwright_installed: bool
    app_browser_installed: bool
    system_browser_found: bool = Field(description="Chrome, Edge or Chromium found, or a custom browser set.")
    free_mb: int
    required_mb: int
    refusal: Optional[str] = Field(None, description="Why the install can't start now, or null.")
    job_id: str
    job: Optional[BrowserInstallJob] = None
    last_result: Optional[BrowserInstallResult] = None


class BrowserInstallStarted(BaseModel):
    job_id: str
    started: bool


class BrowserInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
