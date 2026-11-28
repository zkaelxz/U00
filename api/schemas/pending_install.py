"""Pydantic models for api/routers/pending_install_routes.py."""

from typing import Annotated, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool

__all__ = [
    "PendingInstallChange",
    "PendingInstallOutcome",
    "PendingInstallPlan",
    "PendingInstallPlanRequest",
    "PendingInstallQueueRequest",
    "PendingInstallQueued",
    "PendingInstallRestart",
    "PendingInstallRestartRequest",
    "PendingInstallRestartStarted",
    "PendingInstallStatus",
]

_KEY = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"     # the shape pending_install accepts


class PendingInstallPlanRequest(BaseModel):
    """Registry package keys only; the server derives everything else."""
    model_config = ConfigDict(extra="forbid")
    packages: List[Annotated[str, Field(pattern=_KEY)]] = Field(min_length=1, max_length=30)


class PendingInstallQueueRequest(PendingInstallPlanRequest):
    confirm: StrictBool = False
    accept_risk: StrictBool = False


class PendingInstallChange(BaseModel):
    name: str
    from_version: Optional[str] = None
    to_version: str
    kind: str = Field(description="install, upgrade, downgrade or change (a version pip can't order)")


class PendingInstallPlan(BaseModel):
    packages: List[str]
    available: bool = Field(description="False when pip could not preview the change.")
    mode: str = Field(description="`now` installs immediately; `restart` waits for the next start.")
    changes: List[PendingInstallChange]
    summary: List[str]
    loaded: List[str]
    blocked: List[str]
    needs_confirm: List[str]
    note: Optional[str] = None


class PendingInstallQueued(BaseModel):
    queued: bool
    install_now: bool
    plan: PendingInstallPlan


class PendingInstallOutcome(BaseModel):
    status: str = Field(description="ok, failed, timed_out, refused, running or interrupted.")
    packages: List[str]
    message: str
    tail: List[str]
    restored: List[str]
    restore_failed: List[str]
    finished: Optional[int] = None


class PendingInstallRestart(BaseModel):
    available: bool = Field(description="Baihe can restart itself (a start.bat copy on Windows).")
    reason: Optional[str] = Field(None, description="Plain words why it can't.")
    running_jobs: List[str] = Field(description="Jobs a restart would stop.")
    other_jobs: bool = Field(description="Another Baihe process has a job; a restart waits for it.")
    automatic: bool = Field(False, description="Baihe restarts itself to run the queued install once "
                            "no job or Live session is running.")
    service: bool = Field(False, description="The background service runs this copy: closing "
                          "its window doesn't restart it.")


class PendingInstallRestartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    stop_jobs: StrictBool = False


class PendingInstallRestartStarted(BaseModel):
    restarting: bool
    stopped_jobs: List[str]


class PendingInstallLastRestart(BaseModel):
    outcome: str = Field(description="ok, install_failed, not_healthy, start_failed or stop_failed.")
    downtime_seconds: Optional[int] = None
    at: Optional[int] = None
    packages: Optional[int] = None
    restored: Optional[int] = None
    restore_failed: Optional[int] = None
    install_status: Optional[str] = None


class PendingInstallStatus(BaseModel):
    packages: List[str]
    created: Optional[int] = None
    before: Dict[str, str]
    problem: Optional[str] = Field(None, description="Plain words when the queued file was refused.")
    result: Optional[PendingInstallOutcome] = None
    applying: bool
    restart: PendingInstallRestart
    last_restart: Optional[PendingInstallLastRestart] = Field(
        None, description="The service helper's last automatic restart, until it is dismissed.")
