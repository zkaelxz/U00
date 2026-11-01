"""
api/diagnostics_install_schemas.py -- Pydantic models for
api/routers/diagnostics_installs_routes.py (package install and GPU PyTorch
setup, Deno install, "Test first" for an update).
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from api.schemas import (ConfirmRequest, DiagnosticsInstallResult, DiagnosticsTorchVerify, JobStarted, JobState,
                         UpgradeRequest)


class DiagnosticsJobState(JobState):
    pass


class DiagnosticsJobStarted(JobStarted):
    pass


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


class DiagnosticsDenoInstallRequest(ConfirmRequest):
    pass


class DiagnosticsUpgradeCheckRequest(UpgradeRequest, ConfirmRequest):
    """confirm=true and the version the last update check offered."""


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
    unavailable_reason: Optional[str] = Field(
        None, description="Set when this copy has no test suite to run; hide Test first.")
    job_id: str
    job: Optional[DiagnosticsJobState] = None


class DiagnosticsDependencyInstallResult(DiagnosticsInstallResult):
    """Like the old synchronous result, plus what the GPU PyTorch setup
    adds (variant, the CUDA check) and whether Cancel ended it."""
    cancelled: bool = False
    variant: Optional[str] = None
    verify: Optional[DiagnosticsTorchVerify] = None


class DiagnosticsDependencyInstallState(BaseModel):
    """The latest package install / GPU PyTorch setup job. Output lines are
    redacted; no paths or keys."""
    kind: Optional[Literal["package", "upgrade", "upgrade_outdated", "gpu_torch", "cuda_runtime"]] = None
    package: Optional[str] = None
    result: Optional[DiagnosticsDependencyInstallResult] = None
    job_id: str
    job: Optional[DiagnosticsJobState] = None


class DiagnosticsCudaRuntimeStatus(BaseModel):
    missing: bool = Field(description="An NVIDIA GPU is present but no cuBLAS 12 / cuDNN 9 is "
                                      "where Whisper can load it.")
    show: bool = Field(description="Offer the row: missing, or an install of it has run.")
    install: Optional[DiagnosticsDependencyInstallState] = None
    job: Optional[DiagnosticsJobState] = None
