"""api/schemas/diagnostics_setup.py -- Diagnostics shapes: setup checks, model cache, package
installs, GPU torch and reset.
"""

from typing import Annotated, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, StringConstraints

from api.schemas.common import ConfirmRequest, UpgradeRequest

__all__ = [
    "DiagnosticsSetupPython",
    "DiagnosticsSetupFfmpeg",
    "DiagnosticsSetupJsRuntime",
    "DiagnosticsSetupBrowser",
    "DiagnosticsSetupCuda",
    "DiagnosticsSetupFiles",
    "DiagnosticsSetupChecks",
    "DiagnosticsHfCacheEntry",
    "DiagnosticsModelFile",
    "DiagnosticsModelCache",
    "DiagnosticsPyannoteModel",
    "DiagnosticsPyannoteReadiness",
    "DiagnosticsLogTail",
    "DiagnosticsSupportReport",
    "DiagnosticsAdminConfirm",
    "DiagnosticsUpgradePlanRequest",
    "DiagnosticsUpgradeRequest",
    "DiagnosticsInstallResult",
    "DiagnosticsPackageInfo",
    "DiagnosticsInstallTask",
    "DiagnosticsInstallPresets",
    "DiagnosticsPackageUpdate",
    "DiagnosticsPackageUpdates",
    "DiagnosticsGpuTorchNvidia",
    "DiagnosticsTorchPackage",
    "DiagnosticsTorchVariant",
    "DiagnosticsTorchVerify",
    "DiagnosticsGpuTorchStatus",
    "DiagnosticsGpuTorchSetupRequest",
    "DiagnosticsDependencyInstallRequest",
    "DiagnosticsResetRequest",
    "DiagnosticsResetResult",
    "DiagnosticsCacheDeleteResult",
]


# ---------------------------------------------------------------------------
# Diagnostics gaps -- /api/diagnostics/...
# ---------------------------------------------------------------------------
class DiagnosticsSetupPython(BaseModel):
    version: Optional[str] = None
    ok: bool


class DiagnosticsSetupFfmpeg(BaseModel):
    found: bool
    version: Optional[str] = None
    libass: Optional[bool] = Field(default=None, description=(
        "Built with libass (burned-in subtitles); null when ffmpeg is missing or unknown."))


class DiagnosticsSetupJsRuntime(BaseModel):
    found: bool
    name: Optional[str] = None


class DiagnosticsSetupBrowser(BaseModel):
    found: bool
    name: Optional[str] = None
    package: Optional[bool] = None


class DiagnosticsSetupCuda(BaseModel):
    torch_installed: bool
    cuda_available: Optional[bool] = None


class DiagnosticsSetupFiles(BaseModel):
    all_present: bool
    missing_top_level: List[str]


class DiagnosticsSetupChecks(BaseModel):
    """Found/version/name only; never a path."""
    python: DiagnosticsSetupPython
    ffmpeg: DiagnosticsSetupFfmpeg
    js_runtime: DiagnosticsSetupJsRuntime
    browser: DiagnosticsSetupBrowser
    cuda: DiagnosticsSetupCuda
    files: DiagnosticsSetupFiles
    library_writable: bool
    warnings: List[str] = []


class DiagnosticsHfCacheEntry(BaseModel):
    repo_id: str
    repo_type: str
    revision: str
    size_bytes: int


class DiagnosticsModelFile(BaseModel):
    """One entry of a model folder outside the Hugging Face cache."""
    folder: Literal["torch", "audio_separator"]
    name: str
    size_bytes: int


class DiagnosticsModelCache(BaseModel):
    hf_cache: List[DiagnosticsHfCacheEntry]
    hf_total_bytes: int
    model_files: List[DiagnosticsModelFile]
    model_files_total_bytes: int


class DiagnosticsPyannoteModel(BaseModel):
    model: str
    accessible: bool


class DiagnosticsPyannoteReadiness(BaseModel):
    """Booleans only; the token is never returned."""
    pyannote_installed: bool
    hf_token_configured: bool
    models: Optional[List[DiagnosticsPyannoteModel]] = None
    ready: bool


class DiagnosticsLogTail(BaseModel):
    lines: List[str]


class DiagnosticsSupportReport(BaseModel):
    report: str


class DiagnosticsAdminConfirm(ConfirmRequest):
    pass


class DiagnosticsUpgradePlanRequest(UpgradeRequest):
    """The version the user is about to confirm (the last update check's
    target); 409 when that check no longer says so."""


class DiagnosticsUpgradeRequest(DiagnosticsUpgradePlanRequest):
    """confirm=true and the target, plus accept_risk/accepted_risks as for
    DiagnosticsDependencyInstallRequest: the update is re-planned here."""
    confirm: StrictBool = False
    accept_risk: StrictBool = False
    accepted_risks: List[Annotated[StrictStr, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=20)


class DiagnosticsInstallResult(BaseModel):
    package: str
    ok: bool
    output_tail: List[str]
    # A plain-English next step for a known failure (pip's cache unwritable).
    hint: Optional[str] = None


class DiagnosticsPackageInfo(BaseModel):
    name: str
    dist: str
    installed: bool
    # From installed metadata (the real dist, or a known alternate like
    # opencv-python-headless); None when not installed or unreadable.
    installed_version: Optional[str] = None
    min_version: Optional[str] = None     # the app's minimum (requirements files' >=)
    below_min: bool = False
    installable: bool
    powers: str
    approx_mb: Optional[int] = None
    pulls_torch: bool
    source_url: Optional[str] = None
    not_offered_reason: Optional[str] = None
    warning: Optional[str] = None


class DiagnosticsInstallTask(BaseModel):
    id: str
    group: str
    label: str
    help: str
    packages: List[str]
    # package -> "required" | "recommended" | "optional" for this task
    roles: Dict[str, str] = {}
    installed_count: int
    required_missing: List[str] = []
    to_install: List[str]           # missing required + recommended (Install for this task)
    optional_missing: List[str] = []
    approx_mb: int


class DiagnosticsInstallPresets(BaseModel):
    """Install presets by task, plus per-package pip name, approx. size,
    PyPI link and install caveats (GET /api/diagnostics/install-presets)."""
    tasks: List[DiagnosticsInstallTask]
    packages: Dict[str, DiagnosticsPackageInfo]
    # Installed packages below the app's minimum that "Update these" upgrades.
    outdated: List[str] = []


class DiagnosticsPackageUpdate(BaseModel):
    name: str
    dist: str
    installed_version: Optional[str] = None
    # update | up_to_date | held_back | managed | unknown
    status: str
    latest: Optional[str] = None
    target: Optional[str] = None      # the version Upgrade installs (status "update")
    reason: Optional[str] = None      # what holds a newer release back


class DiagnosticsPackageUpdates(BaseModel):
    """POST /api/diagnostics/package-updates/check: asks PyPI (fixed URL per
    static dist name) only when called; cached in the server process."""
    checked_at: float
    packages: Dict[str, DiagnosticsPackageUpdate]


class DiagnosticsGpuTorchNvidia(BaseModel):
    found: bool
    gpu_name: Optional[str] = None
    driver_version: Optional[str] = None
    # ok | old (works, below the recommended build's own CUDA requirement) | too_old | unknown
    status: str
    recommended: Optional[str] = None
    minimum: Optional[str] = None
    cuda: Optional[str] = None       # the CUDA version `recommended` is for, e.g. "12.6"


class DiagnosticsTorchPackage(BaseModel):
    name: str
    version: Optional[str] = None
    build: Optional[str] = None      # "cuda", "cpu", or None (no build tag / not installed)


class DiagnosticsTorchVariant(BaseModel):
    variant: str
    label: str
    index_url: str
    versions: Dict[str, str]
    needs_nvidia: bool


class DiagnosticsTorchVerify(BaseModel):
    torch: Optional[str] = None
    torchvision: Optional[str] = None
    torchaudio: Optional[str] = None
    cuda_build: Optional[str] = None
    cuda_available: Optional[bool] = None
    device: Optional[str] = None
    error: Optional[str] = None


class DiagnosticsGpuTorchStatus(BaseModel):
    """GET /api/diagnostics/gpu-torch: NVIDIA GPU/driver, the installed
    torch family, mismatches and the recommended matched triple. `probe`
    only from POST /api/diagnostics/gpu-torch/check (imports torch in a
    fresh Python)."""
    nvidia: DiagnosticsGpuTorchNvidia
    installed: List[DiagnosticsTorchPackage]
    problems: List[str]
    # missing | mismatched | cpu_on_gpu | recommended | different
    state: str
    python_supported: bool
    recommended: DiagnosticsTorchVariant
    # Why that variant, in plain words, when it isn't the default; else None.
    recommended_reason: Optional[str] = None
    variants: List[DiagnosticsTorchVariant]
    probe: Optional[DiagnosticsTorchVerify] = None


class DiagnosticsGpuTorchSetupRequest(BaseModel):
    """confirm=true; variant is one of the server's fixed variants (omitted:
    CUDA when an NVIDIA GPU answers, else CPU). No version or index is
    accepted from the client."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    variant: Optional[Literal["cu128", "cu126", "cpu"]] = None


class DiagnosticsDependencyInstallRequest(BaseModel):
    """confirm=true; accept_risk=true with accepted_risks (the plan's
    needs_confirm sentences the owner was shown) when the install plan
    downgrades a package Baihe needs or could not be previewed. 409 without
    them, or when the server's plan has a risk the list lacks."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    accept_risk: StrictBool = False
    accepted_risks: List[Annotated[StrictStr, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=20)


class DiagnosticsResetRequest(BaseModel):
    """confirm=true and confirm_text "RESET" (the word the user types to confirm)."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=20)


class DiagnosticsResetResult(BaseModel):
    ok: bool
    reset_at: float


# ---------------------------------------------------------------------------
# Diagnostics: model-cache delete.
# ---------------------------------------------------------------------------
class DiagnosticsCacheDeleteResult(BaseModel):
    deleted: bool
    name: str
