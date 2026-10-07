"""api/schemas/system.py -- System-level shapes: health, settings, diagnostics
and setup, jobs, updates, notifications, extension, ports and bug reports.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr

from api.schemas.common import TranslateEngine

__all__ = [
    "UsageRecostModelRow",
    "UsageRecostPreview",
    "UsageRecostApplyRequest",
    "UsageRecostResult",
    "MonthSpendStatus",
    "MonthCounterResetResult",
    "HealthResponse",
    "MetaResponse",
    "DependencyStatus",
    "FileCompleteness",
    "GpuStatus",
    "ModelEngineVersion",
    "RunningJob",
    "DiagnosticsOverview",
    "RemoteHealthState",
    "RemoteHealthCheck",
    "RemoteCertificateCheck",
    "RemoteDdnsCheck",
    "RemoteHealth",
    "RemoteIpCheckStatus",
    "RemoteIpCheckSetRequest",
    "RemoteIpCheckClearRequest",
    "RemoteIpCheckTestResult",
    "JobRecord",
    "JobListResponse",
    "SettingsPreferences",
    "SettingsChoices",
    "SettingsOverview",
    "SettingsUpdateRequest",
    "JobCancelResult",
    "JobDeleteResult",
    "JobsClearFinishedResult",
    "ArtifactInfo",
    "EngineKeySetRequest",
    "EngineKeyClearRequest",
    "EngineKeyResult",
    "EndpointUrlSetRequest",
    "EndpointUrlResult",
    "DiagnosticsSetupPython",
    "DiagnosticsSetupFfmpeg",
    "DiagnosticsSetupJsRuntime",
    "DiagnosticsSetupBrowser",
    "DiagnosticsSetupCuda",
    "DiagnosticsSetupFiles",
    "DiagnosticsSetupChecks",
    "DiagnosticsHfCacheEntry",
    "DiagnosticsPiperVoice",
    "DiagnosticsModelFile",
    "DiagnosticsModelCache",
    "DiagnosticsPyannoteModel",
    "DiagnosticsPyannoteReadiness",
    "DiagnosticsLogTail",
    "DiagnosticsSupportReport",
    "DiagnosticsAdminConfirm",
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
    "DiagnosticsGpuTorchSetupResult",
    "DiagnosticsResetRequest",
    "DiagnosticsResetResult",
    "ExtensionStatus",
    "ExtensionEnabledRequest",
    "ExtensionEnabledResult",
    "ExtensionTokenRequest",
    "ExtensionToken",
    "ExtensionEngineSettings",
    "ExtensionEngineRequest",
    "NotificationChannel",
    "NotificationOutcome",
    "NotificationStatus",
    "NotificationChannelSetRequest",
    "NotificationChannelClearRequest",
    "NotificationChannelResult",
    "NotificationTestResult",
    "BugReportRouteVisit",
    "BugReportConsoleEntry",
    "BugReportErrorEntry",
    "BugReportFailedRequest",
    "BugReportViewport",
    "BugReportClient",
    "BugReportSaved",
    "BugReportText",
    "BugReportDeleteConfirm",
    "BugReportListItem",
    "BugReportDeleted",
    "DiagnosticsCacheDeleteResult",
    "ShutdownResponse",
    "UpdateStatus",
    "UpdateSettingsRequest",
    "UpdateInstallRequest",
    "UpdateInstallResponse",
    "PortEntry",
    "PortsOverview",
]


class HealthResponse(BaseModel):
    status: str = Field(description="`ok` whenever the server can answer at all.")


class MetaResponse(BaseModel):
    app: str
    api_version: str
    environment: str = Field(description="`development` or `production`; empty on the "
                                         "household listener.")
    local: bool = Field(description="True when this request would pass a PC-only (local_only) "
                                    "route: the viewer is at the PC. A UI hint only; those "
                                    "routes still enforce it.")


class DependencyStatus(BaseModel):
    installed: bool
    powers: str
    tier: str


class FileCompleteness(BaseModel):
    missing_top_level: List[str]
    missing_tabs: List[str]
    all_present: bool


class GpuStatus(BaseModel):
    available: bool
    name: Optional[str] = None
    vram_used_gb: Optional[float] = None
    vram_total_gb: Optional[float] = None
    torch_cuda_version: Optional[str] = None
    message: Optional[str] = None


class ModelEngineVersion(BaseModel):
    name: str
    version: Optional[str] = None
    url: Optional[str] = None
    installed: bool
    package: Optional[str] = None
    help: Optional[str] = None


class RunningJob(BaseModel):
    job_id: str
    status: Optional[str] = None
    progress: Optional[float] = None
    message: str = ""
    error: Optional[str] = None
    description: Optional[str] = None
    gpu_touching: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None


class DiagnosticsOverview(BaseModel):
    """A read-only snapshot of Diagnostics' routine view -- no admin action (install/upgrade/delete) is exposed
    here; those are PC-only routes under /api/diagnostics. Log lines and
    job messages/errors are redacted."""
    dependencies: dict[str, DependencyStatus]
    file_completeness: FileCompleteness
    library_writable: bool
    gpu: GpuStatus
    model_engine_versions: List[ModelEngineVersion]
    running_jobs: List[RunningJob]
    recent_log_lines: List[str]


RemoteHealthState = Literal["off", "unknown", "not_configured", "ok", "warn", "critical"]


class RemoteHealthCheck(BaseModel):
    state: RemoteHealthState
    message: str


class RemoteCertificateCheck(RemoteHealthCheck):
    days_left: Optional[int] = None


class RemoteDdnsCheck(RemoteHealthCheck):
    configured: bool


class RemoteHealth(BaseModel):
    """GET /api/diagnostics/remote-health: the last scheduled check of remote
    access. States, whole days, Unix times and fixed messages only: never the
    public name, an address, a URL or a path."""
    state: Literal["off", "unknown", "ok", "warn", "critical"]
    message: str
    checked_at: Optional[float] = None
    since: Optional[float] = None
    certificate: RemoteCertificateCheck
    ddns: RemoteDdnsCheck
    listener: RemoteHealthCheck


class RemoteIpCheckStatus(BaseModel):
    """Whether the public-address check is set; never the address."""
    configured: bool


class RemoteIpCheckSetRequest(BaseModel):
    """Write-only: `value` may carry a token, so it is never echoed back and
    validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class RemoteIpCheckClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class RemoteIpCheckTestResult(BaseModel):
    """One check run now: a state and a fixed message, no address."""
    configured: bool
    state: RemoteHealthState
    message: str


class JobRecord(BaseModel):
    """One job's cross-process record (read from the
    job_records mirror) -- the last status this app
    knows about, from any process, not necessarily the current one (see
    job_records' own "no resume" limitation)."""
    job_id: str
    status: str
    progress: Optional[float] = None
    message: str = ""
    error: Optional[str] = None
    description: Optional[str] = None
    gpu_touching: bool = False
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    updated_at: float
    # A redacted, allowlisted projection of the job's result dict
    # (services/jobs_service.project_result) plus a normalised outcome
    # (ok | failed | cancelled | partial | kept_existing), so a "done" job
    # that actually failed or was cancelled does not look like a success.
    result: Optional[Dict[str, Any]] = None
    outcome: Optional[str] = None
    outcome_message: Optional[str] = None
    # Still queued/running on record, but no owner has heartbeated it for
    # 15 minutes (server clock): left behind by a process that died.
    stale: bool = False
    # Running here, but no progress update for a while (advisory; the state
    # is unchanged). Distinct from `stale`, which is about a dead owner.
    stalled: bool = False
    # The caller started this job or owns its drama (auth off and the local
    # owner: every job). Server-computed from the caller's session; true
    # only where the caller may also cancel it.
    owned_by_me: bool = False
    # The title a drama-scoped job runs on, and a fixed-vocabulary label for
    # what it does (services/jobs_service.JOB_KIND_BY_PREFIX), so clients
    # never parse job ids. Only set for jobs the caller may already see.
    drama_id: Optional[int] = None
    kind: Literal["transcribe", "translate", "align", "dub", "export", "review",
                  "import", "other"] = "other"
    # The page a job belongs to (services/jobs_service.job_page), so a job
    # with no title can still link somewhere. A page name only; None when
    # the id names no page.
    page: Optional[Literal["title", "sources", "discover", "live", "settings",
                           "diagnostics"]] = None


class JobListResponse(BaseModel):
    items: List[JobRecord]
    count: int


class SettingsPreferences(BaseModel):
    """Persisted PC-side preferences (settings parity G05, G08, G09, G13,
    G14, G15). Paths are paths only: a cookies file's contents are never
    read or returned. The four paths are returned only to the PC itself;
    any other caller gets "" there and only the *_configured booleans."""
    default_engine: str
    default_locale: str
    default_style_note: str
    scene_aware_batches: bool
    episode_summary_engine: str
    monthly_cap_usd: Optional[float] = None
    max_upload_mb: int = 20480
    ollama_num_ctx_override: int
    whisper_model_path: str
    ocr_backend: str
    ocr_prefer_paddle_vl_manga: bool
    tesseract_cmd: str
    cookies_browser: Optional[str] = None
    cookies_file: str
    lncrawl_cmd: str = ""
    whisper_model_path_configured: bool = False
    tesseract_cmd_configured: bool = False
    cookies_file_configured: bool = False
    lncrawl_cmd_configured: bool = False


class SettingsChoices(BaseModel):
    engines: List[str]
    locales: List[str]
    summary_engines: List[str]
    ocr_backends: List[str]
    cookie_browsers: List[str]


class SettingsOverview(BaseModel):
    """Non-secret settings snapshot -- engine_keys
    reports only whether a key/endpoint is configured, never its value
    (D2: keys are server-side only). endpoints carries the Ollama
    and GPT-SoVITS URLs only when they have no userinfo,
    query or fragment (settings_service.validate_endpoint_url)."""
    engine_keys: dict[str, bool]
    gpu_limit_enabled: bool
    gpu_max_parallel: int = 1
    unload_ollama_before_transcribe: bool = True
    notify_on_completion: bool
    use_gpu: bool = False
    gemini_free_tier: bool = False
    bulk_auto_resume: bool = False
    offer_provider_models: bool = False
    preferences: SettingsPreferences
    endpoints: Dict[str, Optional[str]]
    # BAIHE_MAX_UPLOAD_MB set: it wins over preferences.max_upload_mb.
    upload_max_mb_from_env: bool = False
    effective_upload_max_mb: int = 20480
    monthly_cap_env_usd: float = 0.0
    effective_monthly_cap_usd: float = 0.0
    # Full month vs what the cap counts since an active reset (UTC ISO time).
    month_spend_usd: float = 0.0
    month_spend_counted_usd: float = 0.0
    month_spend_reset_at: Optional[str] = None
    choices: SettingsChoices


class MonthSpendStatus(BaseModel):
    month_spend_usd: float
    month_spend_counted_usd: float
    month_spend_reset_at: Optional[str] = None


class MonthCounterResetResult(BaseModel):
    """POST reset or undo: month spend figures before and after. Numbers and
    a timestamp only."""
    before: MonthSpendStatus
    after: MonthSpendStatus


class SettingsUpdateRequest(BaseModel):
    """Non-secret Settings writes (preferences added for
    settings parity). Unknown fields are rejected; keys and endpoint URLs
    are never accepted here (they have their own guarded routes).
    settings_service.set_settings re-validates every value. For
    monthly_cap_usd, null clears the saved cap (the .env value applies)."""
    model_config = ConfigDict(extra="forbid")
    gpu_limit_enabled: Optional[StrictBool] = None
    gpu_max_parallel: Optional[StrictInt] = None  # clamped to 1..4
    unload_ollama_before_transcribe: Optional[StrictBool] = None
    notify_on_completion: Optional[StrictBool] = None
    use_gpu: Optional[StrictBool] = None
    gemini_free_tier: Optional[StrictBool] = None
    bulk_auto_resume: Optional[StrictBool] = None
    offer_provider_models: Optional[StrictBool] = None
    default_engine: Optional[StrictStr] = Field(None, max_length=40)
    default_locale: Optional[StrictStr] = Field(None, max_length=8)
    default_style_note: Optional[StrictStr] = Field(None, max_length=2000)
    scene_aware_batches: Optional[StrictBool] = None
    episode_summary_engine: Optional[StrictStr] = Field(None, max_length=40)
    monthly_cap_usd: Optional[Union[StrictInt, StrictFloat]] = None
    max_upload_mb: Optional[StrictInt] = None
    ollama_num_ctx_override: Optional[StrictInt] = None
    whisper_model_path: Optional[StrictStr] = Field(None, max_length=1024)
    ocr_backend: Optional[StrictStr] = Field(None, max_length=40)
    ocr_prefer_paddle_vl_manga: Optional[StrictBool] = None
    tesseract_cmd: Optional[StrictStr] = Field(None, max_length=1024)
    cookies_browser: Optional[StrictStr] = Field(None, max_length=40)
    cookies_file: Optional[StrictStr] = Field(None, max_length=1024)
    lncrawl_cmd: Optional[StrictStr] = Field(None, max_length=1024)


class JobCancelResult(BaseModel):
    job_id: str
    cancel_requested: bool
    status: str


class JobDeleteResult(BaseModel):
    job_id: str
    deleted: bool


class JobsClearFinishedResult(BaseModel):
    deleted_count: int


class ArtifactInfo(BaseModel):
    name: str
    size: int
    kind: str


class EngineKeySetRequest(BaseModel):
    """Write-only engine key. `value` is a secret:
    it is never echoed back and validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class EngineKeyClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class EngineKeyResult(BaseModel):
    engine: str
    configured: bool


class EndpointUrlSetRequest(BaseModel):
    """Ollama / GPT-SoVITS URL (settings parity G06). An
    http(s) URL with no userinfo, query or fragment."""
    model_config = ConfigDict(extra="forbid")
    url: str = Field(..., max_length=300)
    confirm: StrictBool = False


class EndpointUrlResult(BaseModel):
    name: str
    url: Optional[str] = None
    configured: bool


# ---------------------------------------------------------------------------
# API batch 1: Diagnostics gaps -- /api/diagnostics/...
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


class DiagnosticsSetupCuda(BaseModel):
    torch_installed: bool
    cuda_available: Optional[bool] = None


class DiagnosticsSetupFiles(BaseModel):
    all_present: bool
    missing_top_level: List[str]
    missing_tabs: List[str]


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


class DiagnosticsPiperVoice(BaseModel):
    voice: str
    size_bytes: int


class DiagnosticsModelFile(BaseModel):
    """One entry of a model folder outside the Hugging Face cache."""
    folder: Literal["torch", "audio_separator"]
    name: str
    size_bytes: int


class DiagnosticsModelCache(BaseModel):
    hf_cache: List[DiagnosticsHfCacheEntry]
    hf_total_bytes: int
    piper_voices: List[DiagnosticsPiperVoice]
    piper_total_bytes: int
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


class DiagnosticsAdminConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class DiagnosticsUpgradeRequest(BaseModel):
    """confirm=true, and the version the user confirmed (the last update
    check's target); 409 when that check no longer says so."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    target: Optional[StrictStr] = Field(None, max_length=64,
                                        pattern=r"^[0-9][0-9A-Za-z.+!_-]*$")


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
    # ok | old (works, below CUDA 12.8's own requirement) | too_old | unknown
    status: str
    recommended: Optional[str] = None
    minimum: Optional[str] = None


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
    variants: List[DiagnosticsTorchVariant]
    probe: Optional[DiagnosticsTorchVerify] = None


class DiagnosticsGpuTorchSetupRequest(BaseModel):
    """confirm=true; variant is one of the server's fixed variants (omitted:
    CUDA when an NVIDIA GPU answers, else CPU). No version or index is
    accepted from the client."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    variant: Optional[Literal["cu128", "cpu"]] = None


class DiagnosticsGpuTorchSetupResult(DiagnosticsInstallResult):
    variant: str
    verify: Optional[DiagnosticsTorchVerify] = None


class DiagnosticsResetRequest(BaseModel):
    """confirm=true and confirm_text "RESET" (the word the user types to confirm)."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    confirm_text: str = Field("", max_length=20)


class DiagnosticsResetResult(BaseModel):
    ok: bool
    reset_at: float


# ---------------------------------------------------------------------------
# API batch 1: browser-extension bridge control (PC only) -- /api/extension/...
# ---------------------------------------------------------------------------
class ExtensionStatus(BaseModel):
    """No port and no token, ever."""
    enabled: bool
    running: bool


class ExtensionEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class ExtensionEnabledResult(BaseModel):
    """`restart_needed`: turned off, but this process could not stop the
    endpoint, so it serves until the API restarts. Normally False."""
    enabled: bool
    running: bool
    restart_needed: bool


class ExtensionTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class ExtensionToken(BaseModel):
    token: str


class ExtensionEngineSettings(BaseModel):
    """The extension's saved translation engine (inventory G16). `ready`:
    an engine is chosen and its key is configured. Never a key value."""
    engine: Optional[str] = None
    model: Optional[str] = None
    ready: bool
    engines: List[TranslateEngine]


class ExtensionEngineRequest(BaseModel):
    """engine null = OCR only (no translation)."""
    model_config = ConfigDict(extra="forbid")
    engine: Optional[StrictStr] = None
    model: Optional[StrictStr] = None


# --- Job notifications (Discord / ntfy) ---------------------------------------
NotificationChannel = Literal["discord", "ntfy"]


NotificationOutcome = Literal["sent", "failed", "refused", "not_configured"]


class NotificationStatus(BaseModel):
    """Configured booleans only: never a webhook URL, host or topic."""
    discord_configured: bool
    ntfy_configured: bool
    ntfy_allow_local: bool


class NotificationChannelSetRequest(BaseModel):
    """Write-only channel URL. `value` is a secret: never echoed back, and
    validation errors never include it."""
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., repr=False)
    confirm: StrictBool = False


class NotificationChannelClearRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False


class NotificationChannelResult(BaseModel):
    channel: NotificationChannel
    configured: bool


class NotificationTestResult(BaseModel):
    results: Dict[NotificationChannel, NotificationOutcome]


# ---------------------------------------------------------------------------
# Report a problem (services/bug_report_service.py): the React header's
# "Report a problem" dialog. The report is sent as the multipart field
# `report` (JSON matching BugReportClient, max 256 KB) plus an optional
# `screenshot` file (PNG/JPEG, max 5 MB). Client buffers carry no request
# or response bodies, headers, cookies or line text.
# ---------------------------------------------------------------------------
class BugReportRouteVisit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: str = Field(max_length=300)
    at: Optional[str] = Field(None, max_length=40)


class BugReportConsoleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["error", "warn"]
    message: str = Field(max_length=2000)
    at: Optional[str] = Field(None, max_length=40)


class BugReportErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["error", "unhandledrejection"]
    message: str = Field(max_length=2000)
    source: Optional[str] = Field(None, max_length=500)
    at: Optional[str] = Field(None, max_length=40)


class BugReportFailedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(max_length=10)
    path: str = Field(max_length=500)
    status: int = Field(ge=0, le=999)
    code: Optional[str] = Field(None, max_length=80)
    at: Optional[str] = Field(None, max_length=40)


class BugReportViewport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=0, le=100000)
    height: int = Field(ge=0, le=100000)
    dpr: Optional[float] = Field(None, ge=0, le=20)


class BugReportClient(BaseModel):
    """What the browser sends. Lists are the capture module's ring buffers."""
    model_config = ConfigDict(extra="forbid")
    what_happened: str = Field(min_length=1, max_length=5000)
    expected: str = Field("", max_length=5000)
    include_server_log: StrictBool = True
    route: str = Field("", max_length=300)
    route_history: List[BugReportRouteVisit] = Field(default_factory=list, max_length=10)
    console: List[BugReportConsoleEntry] = Field(default_factory=list, max_length=30)
    errors: List[BugReportErrorEntry] = Field(default_factory=list, max_length=30)
    failed_requests: List[BugReportFailedRequest] = Field(default_factory=list, max_length=30)
    app_version: str = Field("", max_length=60)
    api_version: str = Field("", max_length=60)
    environment: str = Field("", max_length=60)
    build_id: str = Field("", max_length=120)
    user_agent: str = Field("", max_length=500)
    viewport: Optional[BugReportViewport] = None
    mode: Literal["pc", "lan", "remote", "unknown"] = "unknown"


class BugReportSaved(BaseModel):
    """`markdown` (for Copy report) includes the server section (commit,
    setup, log tail) only for a caller holding admin.diagnostics; it is
    always saved on the PC. `issue_markdown`, `what_happened`, `expected`
    and `title` are the server-scrubbed texts for the public GitHub issue
    link, which never carries the server section."""
    id: int
    stamp: str
    markdown: str
    issue_markdown: str
    what_happened: str
    expected: str
    title: str


class BugReportText(BaseModel):
    """One saved report's markdown (with the server section)."""
    id: int
    stamp: str
    markdown: str


class BugReportDeleteConfirm(BaseModel):
    """PC-only delete: `stamp` is the folder stamp from the list, so a stale
    list can't delete a different report."""
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    stamp: str = Field(pattern=r"^\d{8}T\d{6}Z$")


class BugReportListItem(BaseModel):
    id: int
    stamp: str
    created_at: Optional[str] = None
    summary: str
    route: Optional[str] = None
    mode: Optional[str] = None
    has_screenshot: bool
    has_server_log: bool


class BugReportDeleted(BaseModel):
    id: int
    deleted: bool


# ---------------------------------------------------------------------------
# Diagnostics parity (react-misc-parity): model-cache delete (Q14).
# ---------------------------------------------------------------------------
class DiagnosticsCacheDeleteResult(BaseModel):
    deleted: bool
    name: str


# Clean stop for the installed app (POST /api/system/shutdown).
class ShutdownResponse(BaseModel):
    status: str = Field(description="`stopping`: jobs were asked to stop and the server exits shortly.")
    cancelled_jobs: int = Field(description="How many running or queued jobs were asked to stop.")


# App updates from the public GitHub Releases (api/routers/update_routes.py).
# Names, numbers and booleans only: never a URL or a filesystem path.
class UpdateStatus(BaseModel):
    current: Optional[str] = Field(None, description="Installed version; null for a source checkout.")
    installed: bool
    latest: Optional[str] = None
    update_available: bool
    notes: str = Field("", description="Release notes as plain text, links removed, truncated.")
    installer_name: Optional[str] = None
    size: Optional[int] = None
    checked_at: Optional[float] = None
    check_error: Optional[str] = None
    release_lookup: Literal["unchecked", "found", "no_installer_release", "not_found"] = Field(
        description="`not_found`: GitHub answered 404 (repository missing, renamed or private).")
    download: Literal["idle", "downloading", "verified", "failed"]
    downloaded_bytes: int
    download_error: Optional[str] = None
    verified: bool = Field(description="The downloaded installer matched the release's SHA-256.")
    verified_version: Optional[str] = Field(None, description="The version Install would start.")
    verified_name: Optional[str] = None
    can_install: bool
    auto_check: bool
    custom_source: bool = Field(description="BAIHE_UPDATE_REPO names another repository than the default.")


class UpdateSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_check: StrictBool


class UpdateInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool


class UpdateInstallResponse(BaseModel):
    launched: bool
    installer_name: str
    version: str


# --- Diagnostics ports panel ---------------------------------------------------
class PortEntry(BaseModel):
    """One port Baihe uses: numbers, booleans and fixed text only."""
    key: Literal["api", "household", "extension", "https"]
    label: str
    port: Optional[int] = None
    active: bool
    how_to_change: str


class PortsOverview(BaseModel):
    """GET /api/diagnostics/ports (PC only)."""
    ports: List[PortEntry]


class UsageRecostModelRow(BaseModel):
    model: str
    rows: int
    stored_usd: float
    recomputed_usd: float


class UsageRecostPreview(BaseModel):
    """GET /api/settings/usage-recost: what a re-cost would change. Numbers
    only; nothing is written."""
    rows: int
    models: List[UsageRecostModelRow]
    stored_usd: float
    recomputed_usd: float
    difference_usd: float
    month_stored_usd: float
    month_recomputed_usd: float
    # Rows a re-cost already replaced, which Undo can put back.
    recosted_rows: int
    # Digest of the exact (row, new cost) set; apply refuses a different set
    # even when it has the same number of rows.
    fingerprint: str


class UsageRecostApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool = False
    # What the user saw in the preview; a different count or set now means 409.
    previewed: int = Field(ge=0)
    fingerprint: str = Field(min_length=1, max_length=64)


class UsageRecostResult(BaseModel):
    """POST apply or undo: rows written and this month's logged spend after."""
    rows: int
    month_spend_usd: float
    # Rows holding a replaced cost after this call, so a client need not accumulate.
    recosted_rows: int
    # Apply only: the previewed count the client sent and rows actually changed.
    previewed: Optional[int] = None
    changed: Optional[int] = None
