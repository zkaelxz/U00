"""
api/diagnostics_install_schemas.py -- Pydantic models for
api/routers/diagnostics_installs_routes.py (Deno install, "Test first" for
an update).
"""

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr


class DiagnosticsJobState(BaseModel):
    status: Optional[str] = None
    progress: float = 0.0
    message: str = ""
    error: Optional[str] = None


class DiagnosticsJobStarted(BaseModel):
    job_id: str
    started: bool


class DiagnosticsDenoResult(BaseModel):
    ok: bool
    on_path: bool
    needs_restart: bool
    message: str
    output_tail: List[str]


class DiagnosticsDenoStatus(BaseModel):
    runtime_found: bool
    runtime_name: Optional[str] = None
    deno_on_path: bool
    deno_installed: bool
    can_install: bool
    install_method: str = Field(description="`winget` (Windows) or `download` (official release zip).")
    job_id: str
    job: Optional[DiagnosticsJobState] = None
    last_result: Optional[DiagnosticsDenoResult] = None


class DiagnosticsDenoInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class DiagnosticsUpgradeCheckRequest(BaseModel):
    """confirm=true and the version the last update check offered."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    target: Optional[StrictStr] = Field(None, max_length=64,
                                        pattern=r"^[0-9][0-9A-Za-z.+!_-]*$")


class DiagnosticsUpgradeCheckResult(BaseModel):
    ok: bool
    verdict: Optional[str] = None
    reason: Optional[str] = None
    version: Optional[str] = None
    new_failures: Optional[List[str]] = None
    preexisting_failures: Optional[List[str]] = None
    conflicts: Optional[List[str]] = None


class DiagnosticsUpgradeCheckState(BaseModel):
    package: Optional[str] = None
    target: Optional[str] = None
    output_tail: List[str]
    result: Optional[DiagnosticsUpgradeCheckResult] = None
    job_id: str
    job: Optional[DiagnosticsJobState] = None
